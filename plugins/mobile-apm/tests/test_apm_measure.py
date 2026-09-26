#!/usr/bin/env python3
"""apm_measure.py 的离线与假设备测试；不连接真实 iOS 设备。"""

import argparse
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apm_measure as measure


class TestLogParser(unittest.TestCase):
    def test_ios_apm日志能保留首段盲区和闭合关系(self):
        lines = [
            "IOSAPM pre-main: 11 ms since process start",
            "IOSAPM pre-main: 12 ms",
            "IOSAPM launch total: 270 ms",
            "IOSAPM stages (since/+delta ms): "
            "processStart=22/+0 appInit=23/+1 appReady=30/+7 firstFrame=270/+240",
        ]
        parsed = measure.parse_log_lines(lines)
        self.assertTrue(parsed["valid"], parsed["errors"])
        self.assertEqual(parsed["firstFrameMs"], 270)
        self.assertEqual(parsed["premainMs"], 11)
        self.assertEqual(parsed["premainSource"], "iosapm_first_log")
        self.assertEqual(parsed["premainCandidatesMs"], [11, 12])
        self.assertEqual(parsed["accountedTimeMs"], 270)
        self.assertEqual(parsed["unattributedResidualMs"], 0)

    def test_T1阶段格式不依赖第三段实现(self):
        lines = [
            "EARLY pre-main: 28 ms since process start",
            "App launch first-frame: 311ms since process start",
            "Launch stages (since/+delta ms): "
            "processStart=35/+35 appInit=38/+2 firstFrame=311/+274",
        ]
        parsed = measure.parse_log_lines(lines)
        self.assertTrue(parsed["valid"], parsed["errors"])
        self.assertEqual(parsed["premainSource"], "early_marker")
        self.assertEqual(parsed["firstFrameMs"], 311)
        self.assertEqual(parsed["unattributedResidualMs"], 0)

    def test_整数阶段舍入残差在有界容差内保留(self):
        lines = [
            "EARLY pre-main: 12 ms since process start",
            "App launch first-frame: 276ms since process start",
            "Launch stages (since/+delta ms): "
            "processStart=16/+16 appInit=17/+0 environmentReady=23/+6 appBody=156/+132 "
            "rootBody=175/+18 homeBody=183/+7 rootAppear=225/+41 storeReady=225/+0 "
            "viewModelsReady=225/+0 homeAppear=225/+0 homeTaskStart=225/+0 "
            "homeTaskEnd=225/+0 firstFrame=276/+50",
        ]
        parsed = measure.parse_log_lines(lines)
        self.assertTrue(parsed["valid"], parsed["errors"])
        self.assertEqual(parsed["unattributedResidualMs"], 6)
        self.assertEqual(parsed["closureToleranceMs"], 15)
        self.assertTrue(any("整数舍入残差" in warning for warning in parsed["warnings"]))

    def test_缺premain不能用0伪装(self):
        lines = [
            "IOSAPM launch total: 100 ms",
            "IOSAPM stages (since/+delta ms): processStart=10/+0 firstFrame=100/+90",
        ]
        parsed = measure.parse_log_lines(lines)
        self.assertFalse(parsed["valid"])
        self.assertIsNone(parsed["premainMs"])
        self.assertEqual(parsed["premainSource"], "unavailable")
        self.assertIn("pre-main 不可用", "；".join(parsed["errors"]))

    def test_total与阶段不闭合会拒绝样本(self):
        lines = [
            "IOSAPM pre-main: 10 ms",
            "IOSAPM launch total: 100 ms",
            "IOSAPM stages (since/+delta ms): processStart=10/+0 firstFrame=90/+80",
        ]
        parsed = measure.parse_log_lines(lines)
        self.assertFalse(parsed["valid"])
        self.assertTrue(any("不闭合" in error for error in parsed["errors"]))


class TestAppValidation(unittest.TestCase):
    def test_bundle_id不一致直接失败(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Demo.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.demo"}))
            with self.assertRaises(measure.MeasurementError):
                measure.verify_app(app, "com.example.other")

    def test_dry_run只验证本地产物和参数(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = make_app(root)
            args = make_args(root, app, output=root / "run")
            args.dry_run = True
            with mock.patch.object(measure, "tool_preflight", return_value={}):
                self.assertEqual(measure.execute_measurement(args), 0)
            self.assertFalse((root / "run").exists())


class TestDeviceResolution(unittest.TestCase):
    def test物理设备可用时返回UDID(self):
        payload = {
            "result": {
                "devices": [
                    {
                        "identifier": "core-1",
                        "hardwareProperties": {
                            "udid": "00008110-000805902684801E",
                            "marketingName": "iPhone 13",
                            "productType": "iPhone14,5",
                            "platform": "iOS",
                            "reality": "physical",
                        },
                        "deviceProperties": {"osVersionNumber": "26.7", "osBuildUpdate": "23H24"},
                        "connectionProperties": {
                            "tunnelState": "disconnected",
                            "pairingState": "paired",
                            "transportType": "wired",
                        },
                    }
                ]
            }
        }

        calls = {"count": 0}

        def fake_run(argv, timeout):
            calls["count"] += 1
            if calls["count"] > 1:
                payload["result"]["devices"][0]["connectionProperties"]["tunnelState"] = "connected"
            output = Path(argv[argv.index("--json-output") + 1])
            if "info" in argv and "details" in argv:
                output.write_text(
                    json.dumps({"result": payload["result"]["devices"][0]}), encoding="utf-8"
                )
            else:
                output.write_text(json.dumps(payload), encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch.object(measure, "run_command", side_effect=fake_run), mock.patch.object(measure.time, "sleep"):
            device = measure.resolve_physical_device("00008110-000805902684801E", ready_timeout=2)
        self.assertEqual(device["udid"], "00008110-000805902684801E")
        self.assertEqual(device["reality"], "physical")

    def test模拟器或不可用设备不能冒充真机(self):
        payload = {
            "result": {
                "devices": [
                    {
                        "identifier": "sim-1",
                        "hardwareProperties": {"udid": "sim-udid", "reality": "simulator"},
                        "deviceProperties": {},
                        "connectionProperties": {"tunnelState": "available"},
                    }
                ]
            }
        }

        def fake_run(argv, timeout):
            output = Path(argv[argv.index("--json-output") + 1])
            output.write_text(json.dumps(payload), encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch.object(measure, "run_command", side_effect=fake_run), self.assertRaises(measure.MeasurementError):
            measure.resolve_physical_device("sim-udid")


class FakeCollector:
    lines: ClassVar[list[str]] = [
        "IOSAPM pre-main: 11 ms since process start",
        "IOSAPM launch total: 270 ms",
        "IOSAPM stages (since/+delta ms): "
        "processStart=22/+0 appInit=23/+1 firstFrame=270/+247",
    ]

    def __init__(self, udid, log_match=None):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def clear(self):
        pass

    def wait_for_frame(self, timeout):
        return True

    def stop(self):
        self.stopped = True

    def snapshot(self):
        return list(self.lines), 0


class TestExecution(unittest.TestCase):
    def test完整样本写出逐次数据_阶段指标和状态(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = make_app(root)
            output = root / "run"
            args = make_args(root, app, output=output, samples=2)
            args.warmup_launches = 2
            device = {
                "udid": "00008110-000805902684801E",
                "model": "iPhone 13",
                "productType": "iPhone14,5",
                "platform": "iOS",
                "reality": "physical",
                "osVersion": "26.7",
                "osBuild": "23H24",
                "state": "available",
            }
            run_command = mock.Mock(return_value=mock.Mock(returncode=0))
            with mock.patch.object(measure, "tool_preflight", return_value={}), mock.patch.object(
                measure, "resolve_physical_device", return_value=device
            ), mock.patch.object(measure, "run_command", run_command), mock.patch.object(
                measure, "LogCollector", FakeCollector
            ), mock.patch.object(measure, "git_context", return_value={"commit": "abc123", "gitDirty": False}), mock.patch.object(
                measure.time, "sleep"
            ):
                code = measure.execute_measurement(args, run_diagnosis=False)

            self.assertEqual(code, 0)
            payload = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["context"]["validSampleCount"], 2)
            self.assertEqual(len(payload["observations"]), 2)
            names = {metric["name"] for metric in payload["metrics"]}
            self.assertIn("startup.cold.first_frame", names)
            self.assertIn("startup.process_to_first_mark", names)
            self.assertIn("startup.stage.appInit", names)
            self.assertEqual(payload["metrics"][0]["samples"], [270, 270])
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "complete")
            self.assertTrue((output / "raw" / "sample-01.log").exists())
            self.assertTrue((output / "raw" / "paired.txt").exists())
            launch_calls = [call.args[0] for call in run_command.call_args_list if "process" in call.args[0]]
            self.assertEqual(len(launch_calls), 4)  # 2 warmup + 2 measured launches

    def test_部分样本不会被伪装成完整run(self):
        class MissingCollector(FakeCollector):
            lines: ClassVar[list[str]] = ["IOSAPM launch total: 270 ms"]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = make_app(root)
            output = root / "run"
            args = make_args(root, app, output=output, samples=2)
            device = {
                "udid": "udid",
                "model": "iPhone",
                "productType": "iPhone14,5",
                "platform": "iOS",
                "reality": "physical",
                "osVersion": "26.7",
                "osBuild": "23H24",
                "state": "available",
            }
            with mock.patch.object(measure, "tool_preflight", return_value={}), mock.patch.object(
                measure, "resolve_physical_device", return_value=device
            ), mock.patch.object(measure, "run_command", return_value=mock.Mock(returncode=0)), mock.patch.object(
                measure, "LogCollector", MissingCollector
            ), mock.patch.object(measure, "git_context", return_value={"commit": "abc", "gitDirty": False}), mock.patch.object(
                measure.time, "sleep"
            ):
                code = measure.execute_measurement(args, run_diagnosis=False)
            self.assertEqual(code, 1)
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["validSamples"], 0)


def make_app(root):
    app = root / "Demo.app"
    app.mkdir()
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.demo"}))
    return app


def make_args(root, app, output=None, samples=5):
    return argparse.Namespace(
        profile="ios-native-startup",
        device="udid",
        package_id="com.example.demo",
        build_type="Release",
        build_path=str(app),
        project_root=str(root),
        output=str(output) if output else None,
        samples=samples,
        interval_sec=5.0,
        timeout_sec=1.0,
        device_ready_timeout=0.0,
        warmup_sec=0.0,
        warmup_launches=1,
        min_effect_ms=30.0,
        log_match=None,
        dry_run=False,
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
