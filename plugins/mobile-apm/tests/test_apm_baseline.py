#!/usr/bin/env python3
"""apm_baseline.py 的契约与质量门禁测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apm_baseline.py"


class BaselineCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_健康劣化返回2且JSON模式不吞退出码(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_payload(root / "baseline.json", [100, 101, 99, 100, 102], "base")
            write_payload(root / "run.json", [120, 121, 119, 120, 122], "run")
            text = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"), "--run", str(root / "run.json")
            )
            self.assertEqual(text.returncode, 2, text.stdout + text.stderr)
            machine = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"),
                "--run", str(root / "run.json"), "--json"
            )
            self.assertEqual(machine.returncode, 2)
            self.assertEqual(json.loads(machine.stdout)["results"][0]["verdict"], "regressed")

    def test_高方差不会包装成确认劣化(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_payload(root / "baseline.json", [100, 101, 99, 100, 102], "base")
            write_payload(root / "run.json", [100, 300, 110, 290, 105], "run")
            result = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"), "--run", str(root / "run.json")
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("测量不可信", result.stdout)
            self.assertIn("疑似多簇", result.stdout)

    def test_口径缺失或不一致返回数据错误(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_payload(root / "baseline.json", [100, 101, 99, 100, 102], "base", device="A")
            write_payload(root / "run.json", [100, 101, 99, 100, 102], "run", device="B")
            result = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"), "--run", str(root / "run.json")
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("device", result.stdout)

    def test_record_require_healthy拒绝高方差输入(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "run.json"
            write_payload(source, [100, 300, 110, 290, 105], "run")
            result = self.run_cli(
                "record", "--in", str(source), "--out", str(root / "baseline.json"), "--require-healthy"
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root / "baseline.json").exists())

    def test_允许语义上有意义的负值指标(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "render.json"
            source.write_text(
                json.dumps(
                    {
                        "context": {"device": "iPhone", "build": "Release"},
                        "metrics": [
                            {
                                "name": "render.frameOverrunMs",
                                "unit": "ms",
                                "direction": "lower_is_better",
                                "samples": [-2, 0, 1],
                                "minEffect": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli("record", "--in", str(source), "--out", str(root / "baseline.json"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "baseline.json").exists())

    def test_非法样本不被写入基线(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "run.json"
            source.write_text(
                json.dumps(
                    {
                        "context": {"device": "A", "build": "Release"},
                        "metrics": [{"name": "x", "unit": "ms", "samples": [float("nan")]}],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli("record", "--in", str(source), "--out", str(root / "baseline.json"))
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root / "baseline.json").exists())

    def test_辅助阶段质量失败不否定健康焦点指标(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "context": {"device": "iPhone", "build": "Release", "measurementMethod": "fixed"},
                "metrics": [
                    {
                        "name": "startup.cold.first_frame",
                        "unit": "ms",
                        "direction": "lower_is_better",
                        "samples": [100, 101, 99, 100, 102],
                    },
                    {
                        "name": "startup.stage.homeTaskStart",
                        "unit": "ms",
                        "direction": "lower_is_better",
                        "samples": [0, 0, 0, 0, 0],
                    },
                ],
            }
            (root / "baseline.json").write_text(json.dumps(payload), encoding="utf-8")
            (root / "run.json").write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"),
                "--run", str(root / "run.json"), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["data_error"])
            stage = next(item for item in report["results"] if item["name"] == "startup.stage.homeTaskStart")
            self.assertEqual(stage["verdict"], "measurement-unreliable")

    def test_跨run漂移时回归只保留为暂定值(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = {
                "commit": "same-commit",
                "device": "iPhone",
                "build": "Release",
                "measurementMethod": "fixed",
            }
            first = {
                "context": context,
                "metrics": [{
                    "name": "startup.cold.first_frame", "unit": "ms",
                    "direction": "lower_is_better", "samples": [100, 101, 99, 100, 102],
                    "minEffect": 30,
                }],
            }
            second = json.loads(json.dumps(first))
            second["metrics"][0]["samples"] = [200, 201, 199, 200, 202]
            (root / "baseline.json").write_text(json.dumps(first), encoding="utf-8")
            (root / "run.json").write_text(json.dumps(second), encoding="utf-8")
            result = self.run_cli(
                "compare", "--baseline", str(root / "baseline.json"),
                "--run", str(root / "run.json"), "--json"
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            focus = next(item for item in payload["results"] if item["name"] == "startup.cold.first_frame")
            self.assertEqual(focus["verdict"], "measurement-unreliable")
            self.assertEqual(focus["provisionalVerdict"], "regressed")

    def test_diagnose子命令输出机器可读结果(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "run.json"
            write_payload(source, [100, 101, 99, 100, 102], "run")
            result = self.run_cli("diagnose", "--input", str(source), "--format", "json")
            self.assertEqual(result.returncode, 0)
            self.assertTrue(json.loads(result.stdout)["verdict"]["usable"])


def write_payload(path, samples, commit, device="iPhone 13"):
    path.write_text(
        json.dumps(
            {
                "context": {
                    "commit": commit,
                    "device": device,
                    "build": "Release",
                    "measurementMethod": "fixed-test-command",
                },
                "metrics": [
                    {
                        "name": "startup.cold.first_frame",
                        "unit": "ms",
                        "direction": "lower_is_better",
                        "samples": samples,
                        "minEffect": 5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
