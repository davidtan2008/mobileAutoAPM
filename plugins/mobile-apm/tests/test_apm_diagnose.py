#!/usr/bin/env python3
"""apm_diagnose.py 回归测试。

T1 数值只作为已归档失败演练的固定输入，用来防止诊断规则漂移；
它们不是本测试重新测量出的性能结果。
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apm_diagnose as diagnose


class TestMultimodalDetection(unittest.TestCase):
    def test_T1_基线被判为疑似多峰而非挑快样本(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = write_t1_baseline(Path(temporary) / "baseline")
            report = diagnose.build_report(
                [run], "startup.cold.first_frame", 5, 50
            )
            summary = report["runs"][0]["focusSummary"]
            self.assertEqual(summary["status"], "unusable_multimodal")
            self.assertEqual(len(summary["suspectedClusters"]), 2)
            self.assertEqual(report["verdict"]["exitCode"], 2)
            self.assertTrue(report["effectAssessment"][0]["effectBelowNoise"])

    def test_连续单峰数据不会误报多峰(self):
        values = [98, 99, 100, 101, 102, 100, 99, 101]
        clusters = diagnose.suspicious_clusters(values)
        self.assertEqual(len(clusters), 1)

    def test_三个明显簇都能被识别(self):
        values = [10, 11, 12, 50, 51, 52, 90, 91, 92]
        clusters = diagnose.suspicious_clusters(values)
        self.assertEqual(len(clusters), 3)


class TestLegacyRunLoading(unittest.TestCase):
    def test_T1_旧格式能补出分段CV(self):
        with tempfile.TemporaryDirectory() as temporary:
            loaded = diagnose.load_run(write_t1_baseline(Path(temporary) / "baseline"))
            stages = {metric["name"]: metric for metric in loaded["metrics"]}
            self.assertIn("startup.stage.firstFrame", stages)
            self.assertIn("startup.stage.appBody", stages)
            self.assertEqual(stages["startup.stage.firstFrame"]["samples"], [57, 46, 54, 57, 53])

    def test_没有metrics_json时只从raw构造且不伪造分段(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "raw").mkdir()
            (run / "raw" / "samples.txt").write_text("100\n101\n99\n", encoding="utf-8")
            loaded = diagnose.load_run(run)
            self.assertEqual([metric["name"] for metric in loaded["metrics"]], ["startup.cold.first_frame"])
            self.assertTrue(any("仅从 raw" in warning for warning in loaded["warnings"]))


class TestCrossRunDiagnosis(unittest.TestCase):
    def test_premain_跨运行方向相反时禁止自动分层(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            common = {
                "commit": "same-commit",
                "device": "physical-device",
                "build": "Release",
                "method": "terminate-launch",
            }
            write_metrics(
                first,
                [311, 450, 312, 314, 431],
                [28, 16, 27, 15, 12],
                common,
            )
            write_metrics(
                second,
                [332, 496, 273, 476, 283],
                [12, 29, 13, 29, 13],
                common,
            )
            report = diagnose.build_report(
                [first, second], "startup.cold.first_frame", 5, None
            )
            premain = next(
                item
                for item in report["crossRunCorrelations"]
                if item["candidate"] == "startup.premain"
            )
            self.assertEqual(premain["stability"], "direction_inconsistent")
            self.assertTrue(any("premain" in reason for reason in report["verdict"]["reasons"]))

    def test_跨运行中位数漂移会阻断基线判定(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            context = {
                "commit": "same-commit",
                "device": "physical",
                "build": "Release",
                "method": "fixed",
            }
            write_metrics(first, [100, 101, 99, 100, 102], [10, 11, 10, 10, 11], context)
            write_metrics(second, [200, 201, 199, 200, 202], [10, 11, 10, 10, 11], context)
            report = diagnose.build_report(
                [first, second], "startup.cold.first_frame", 5, None
            )
            self.assertEqual(report["crossRunFocus"]["status"], "shift_detected")
            self.assertEqual(report["verdict"]["exitCode"], 2)
            self.assertTrue(any("跨 run 焦点中位数漂移" in reason for reason in report["verdict"]["reasons"]))

    def test_跨运行设备不一致会阻断可用判定(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            write_metrics(first, [100, 101, 99, 100, 102], None, {"device": "A", "build": "Release", "method": "m"})
            write_metrics(second, [100, 101, 99, 100, 102], None, {"device": "B", "build": "Release", "method": "m"})
            report = diagnose.build_report([first, second], "startup.cold.first_frame", 5, None)
            self.assertFalse(report["verdict"]["usable"])
            self.assertTrue(any("device" in warning for warning in report["contextWarnings"]))


class TestStageDerivation(unittest.TestCase):
    # ── 阶段派生与 stage 正则 ──────────────────────────────
    # 起因：derive_from_observations 只认 dict 形态的 stages，
    # 而 apm_measure 写出的 observations[].stages 是 **list** 形态
    # （[{"name","sinceMs","deltaMs"}, ...]）。两者对不上 → 这条派生路径
    # 对新模板数据永远是死代码。而它恰恰是「stage 名跨样本不一致、
    # measure 因此不导出 stage 指标」时的兜底，不能失效。
    def test_列表形态的stages能派生出分段指标(self):
        observations = [
            {"stages": [
                {"name": "processStart", "sinceMs": 0, "deltaMs": 0},
                {"name": "dyld", "sinceMs": 120.5, "deltaMs": 120.5},
            ]},
            {"stages": [
                {"name": "processStart", "sinceMs": 0, "deltaMs": 0},
                {"name": "dyld", "sinceMs": 115.0, "deltaMs": 115.0},
            ]},
        ]
        derived, warnings = diagnose.derive_from_observations(observations, [])
        names = {metric["name"] for metric in derived}
        self.assertIn("startup.stage.dyld", names)
        dyld = next(m for m in derived if m["name"] == "startup.stage.dyld")
        self.assertEqual(dyld["samples"], [120.5, 115.0])
        self.assertTrue(warnings)

    def test_字典形态的stages仍然能派生(self):
        # 向后兼容：早期手写 JSON 是字典形态，不能因为修了 list 就把老的弄坏
        observations = [
            {"stages": {"dyld": 10.0}},
            {"stages": {"dyld": 12.0}},
        ]
        derived, _ = diagnose.derive_from_observations(observations, [])
        self.assertEqual([m["name"] for m in derived], ["startup.stage.dyld"])

    def test_已导出stage指标时不重复派生(self):
        observations = [{"stages": [{"name": "dyld", "sinceMs": 1, "deltaMs": 1}]}] * 3
        metrics = [{"name": "startup.stage.dyld", "unit": "ms",
                    "direction": "lower_is_better", "samples": [1.0, 2.0, 3.0]}]
        derived, warnings = diagnose.derive_from_observations(observations, metrics)
        self.assertEqual(derived, [])
        self.assertEqual(warnings, [])

    def test_阶段正则接受小数(self):
        # 必须与 apm_measure.STAGE_RE 一致。measure 侧允许小数，
        # 这里若只认整数，含小数 stage 的日志在诊断侧就解析不到。
        line = "APM stages: processStart=0/+0, dyld=120.5/+120.5, firstFrame=250.75/+130.25"
        parsed = diagnose.STAGE_RE.findall(line)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[1], ("dyld", "120.5", "120.5"))


class TestCLI(unittest.TestCase):
    def test_稳定指标JSON模式返回0(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_metrics(run, [100, 101, 99, 100, 102], None, {"device": "A", "build": "Release", "method": "m"})
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = diagnose.main([str(run), "--metric", "startup.cold.first_frame", "--format", "json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["verdict"]["usable"])

    def test_数据问题返回1(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = diagnose.main(["/definitely/not/a/run", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertIn("找不到 run 路径", stderr.getvalue())


def write_t1_baseline(path):
    """把 T1 已归档数值做成仓库内可复现 fixture，不依赖外部工程路径。"""
    write_metrics(
        path,
        [311, 450, 312, 314, 431],
        [28, 16, 27, 15, 12],
        {
            "device": "iPhone 13 / iOS 26.7 / physical",
            "build": "Release",
            "method": "terminate + launch; idevicesyslog",
        },
    )
    raw = path / "raw"
    raw.mkdir()
    (raw / "samples.txt").write_text("311\n450\n312\n314\n431\n", encoding="utf-8")
    (raw / "premain.txt").write_text("28\n16\n27\n15\n12\n", encoding="utf-8")
    (raw / "stages.txt").write_text(
        "processStart=35/+35 appInit=38/+2 environmentReady=47/+8 appBody=183/+136 rootBody=212/+28 homeBody=219/+7 rootAppear=253/+34 storeReady=253/+0 viewModelsReady=253/+0 homeAppear=253/+0 homeTaskStart=253/+0 homeTaskEnd=254/+0 firstFrame=311/+57\n"
        "processStart=20/+20 appInit=22/+2 environmentReady=28/+6 appBody=315/+287 rootBody=349/+33 homeBody=362/+13 rootAppear=402/+40 storeReady=402/+0 viewModelsReady=403/+0 homeAppear=403/+0 homeTaskStart=403/+0 homeTaskEnd=403/+0 firstFrame=450/+46\n"
        "processStart=33/+33 appInit=37/+3 environmentReady=46/+9 appBody=193/+146 rootBody=216/+22 homeBody=222/+6 rootAppear=257/+34 storeReady=257/+0 viewModelsReady=257/+0 homeAppear=257/+0 homeTaskStart=257/+0 homeTaskEnd=257/+0 firstFrame=312/+54\n"
        "processStart=20/+20 appInit=22/+2 environmentReady=28/+6 appBody=193/+164 rootBody=215/+22 homeBody=222/+7 rootAppear=256/+33 storeReady=256/+0 viewModelsReady=256/+0 homeAppear=256/+0 homeTaskStart=256/+0 homeTaskEnd=257/+0 firstFrame=314/+57\n"
        "processStart=16/+16 appInit=17/+1 environmentReady=23/+6 appBody=278/+255 rootBody=321/+43 homeBody=335/+13 rootAppear=377/+41 storeReady=377/+0 viewModelsReady=377/+0 homeAppear=377/+0 homeTaskStart=377/+0 homeTaskEnd=377/+0 firstFrame=431/+53\n",
        encoding="utf-8",
    )
    return path


def write_metrics(path, samples, premain, context):
    path.mkdir(parents=True, exist_ok=True)
    metrics = [
        {
            "name": "startup.cold.first_frame",
            "unit": "ms",
            "direction": "lower_is_better",
            "samples": samples,
            "minEffect": 30,
        }
    ]
    if premain is not None:
        metrics.append(
            {
                "name": "startup.premain",
                "unit": "ms",
                "direction": "lower_is_better",
                "samples": premain,
                "minEffect": 5,
            }
        )
    (path / "metrics.json").write_text(
        json.dumps({"context": context, "metrics": metrics}, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
