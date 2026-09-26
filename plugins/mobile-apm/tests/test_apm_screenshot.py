#!/usr/bin/env python3
"""apm_screenshot.py 的离线测试；不连接真实 iOS 设备。"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apm_screenshot as screenshot


def png_bytes(width=1170, height=2532):
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class TestScreenshot(unittest.TestCase):
    def test_png头部能验证尺寸(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "shot.png"
            path.write_bytes(png_bytes())
            self.assertEqual(
                screenshot.inspect_image(path),
                {"format": "png", "bytes": len(png_bytes()), "width": 1170, "height": 2532},
            )

    def test_模拟器不能冒充真机(self):
        payload = {
            "result": {
                "devices": [
                    {
                        "identifier": "sim-1",
                        "hardwareProperties": {"udid": "sim-udid", "reality": "simulator"},
                        "deviceProperties": {},
                        "connectionProperties": {},
                    }
                ]
            }
        }
        with mock.patch.object(screenshot, "_read_device_list", return_value=payload), self.assertRaises(
            screenshot.ScreenshotError
        ):
            screenshot.resolve_physical_device("sim-udid")

    def test_auto优先使用DVT并验证输出(self):
        device = {"udid": "udid"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.png"
            calls = []

            def fake_run(argv, timeout):
                calls.append(list(argv))
                Path(argv[-1]).write_bytes(png_bytes(640, 480))

            def fake_optional(name, override):
                return f"/fake/{name}"

            with mock.patch.object(screenshot, "run_command", side_effect=fake_run), mock.patch.object(
                screenshot, "_optional_binary", side_effect=fake_optional
            ):
                result = screenshot.capture(device, output)

            self.assertEqual(result["backend"], "pymobiledevice3-native")
            self.assertEqual(result["width"], 640)
            self.assertEqual(len(calls), 1)
            self.assertIn("--native", calls[0])

    def test_DVT失败回退到idevicescreenshot(self):
        device = {"udid": "udid"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.png"
            calls = []

            def fake_run(argv, timeout):
                calls.append(list(argv))
                if "pymobiledevice3" in argv[0]:
                    raise screenshot.ScreenshotError("DVT unavailable")
                Path(argv[-1]).write_bytes(png_bytes(320, 200))

            def fake_optional(name, override):
                return f"/fake/{name}"

            with mock.patch.object(screenshot, "run_command", side_effect=fake_run), mock.patch.object(
                screenshot, "_optional_binary", side_effect=fake_optional
            ):
                result = screenshot.capture(device, output)

            self.assertEqual(result["backend"], "idevicescreenshot")
            self.assertEqual([item["status"] for item in result["attempts"]], ["failed", "failed", "ok"])

    def test_全部后端失败时保留原因(self):
        device = {"udid": "udid"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.png"
            with mock.patch.object(
                screenshot, "run_command", side_effect=screenshot.ScreenshotError("no service")
            ), mock.patch.object(
                screenshot, "_optional_binary", return_value="/fake/tool"
            ), self.assertRaises(screenshot.CaptureFailure) as raised:
                screenshot.capture(device, output)
            self.assertEqual(len(raised.exception.attempts), 3)
            self.assertTrue(all("no service" in item["error"] for item in raised.exception.attempts))
            self.assertFalse(output.exists())

    def test_隔离安装路径可由环境变量提供(self):
        path = "/tmp/venv/bin/pymobiledevice3"
        with mock.patch.dict(os.environ, {"APM_PYMOBILEDEVICE3_BIN": path}), mock.patch.object(
            screenshot.shutil, "which", return_value=None
        ):
            self.assertEqual(screenshot._optional_binary("pymobiledevice3", None), path)
            self.assertIsNone(screenshot._optional_binary("idevicescreenshot", None))

    def test_默认拒绝覆盖已有文件(self):
        device = {"udid": "udid"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.png"
            output.write_bytes(png_bytes())
            with self.assertRaises(screenshot.ScreenshotError):
                screenshot.capture(device, output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
