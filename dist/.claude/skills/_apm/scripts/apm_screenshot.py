#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""iOS 物理真机截图 —— 真实设备优先，能力不可用时明确报告。

为什么不直接把 `idevicescreenshot` 当成标准：
部分新系统版本上旧 screenshotr 服务会返回 `Invalid service`。本脚本按
以下顺序尝试后端：

1. `pymobiledevice3 developer dvt screenshot`（可选依赖，走 DVT/CoreDevice）；
2. 同工具的 usbmux 连接模式；
3. `idevicescreenshot`（系统已安装时作为回退）。

`pymobiledevice3` 是**可选能力**，不是本项目的运行时依赖。未安装时脚本会
继续尝试回退后端；全部不可用时返回 `unavailable`，绝不把模拟器截图冒充真机。

退出码：
- 0：物理真机截图成功；
- 1：设备、参数或所有截图后端不可用。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

TOOL_VERSION = "1.0.0"


class ScreenshotError(Exception):
    """截图无法继续。"""


class CaptureFailure(ScreenshotError):
    """所有后端都失败；保留每个后端的失败原因。"""

    def __init__(self, message: str, attempts: Sequence[Dict[str, Any]]):
        super().__init__(message)
        self.attempts = list(attempts)


def command_text(argv: Sequence[str]) -> str:
    return shlex.join(str(item) for item in argv)


def run_command(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            [str(item) for item in argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScreenshotError(f"命令不存在：{argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScreenshotError(f"命令超时（{timeout:.1f}s）：{command_text(argv)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "无错误输出").strip()
        raise ScreenshotError(
            f"命令失败（exit {completed.returncode}）：{command_text(argv)}\n{detail}"
        )
    return completed


def _read_device_list() -> Dict[str, Any]:
    """用 devicectl 的结构化输出确认设备确实是 physical。"""
    handle, raw_path = tempfile.mkstemp(prefix="apm-devicectl-screenshot-", suffix=".json")
    os.close(handle)
    json_path = Path(raw_path)
    try:
        run_command(
            [
                "xcrun",
                "devicectl",
                "list",
                "devices",
                "--timeout",
                "15",
                "--json-output",
                str(json_path),
                "--quiet",
            ],
            timeout=25,
        )
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ScreenshotError(f"无法解析 devicectl 设备列表：{exc}") from exc
    finally:
        json_path.unlink(missing_ok=True)


def resolve_physical_device(identifier: str) -> Dict[str, Any]:
    payload = _read_device_list()
    matches = []
    for device in payload.get("result", {}).get("devices", []):
        hardware = device.get("hardwareProperties", {})
        connection = device.get("connectionProperties", {})
        properties = device.get("deviceProperties", {})
        candidates = {
            device.get("identifier"),
            hardware.get("udid"),
            hardware.get("serialNumber"),
            hardware.get("productType"),
            properties.get("name"),
        }
        if identifier in {value for value in candidates if value}:
            matches.append((device, hardware, connection, properties))
    if not matches:
        raise ScreenshotError(f"devicectl 找不到设备：{identifier}")
    if len(matches) > 1:
        raise ScreenshotError(f"设备标识不唯一：{identifier}")

    device, hardware, connection, properties = matches[0]
    reality = hardware.get("reality")
    if reality != "physical":
        raise ScreenshotError(
            f"iOS 真机截图只接受 physical device，当前设备类型={reality or 'unknown'}"
        )
    udid = hardware.get("udid")
    if not isinstance(udid, str) or not udid:
        raise ScreenshotError("devicectl 未返回截图后端所需的物理 UDID")
    return {
        "udid": udid,
        "coreDeviceIdentifier": device.get("identifier"),
        "model": hardware.get("marketingName"),
        "productType": hardware.get("productType"),
        "platform": hardware.get("platform"),
        "osVersion": properties.get("osVersionNumber"),
        "osBuild": properties.get("osBuildUpdate"),
        "state": connection.get("tunnelState"),
        "pairingState": connection.get("pairingState"),
        "transportType": connection.get("transportType"),
    }


def inspect_image(path: Path) -> Dict[str, Any]:
    """确认后端真的写出了非空图片；PNG 同时返回尺寸。"""
    if not path.is_file():
        raise ScreenshotError(f"后端没有生成截图文件：{path}")
    size = path.stat().st_size
    if size <= 0:
        raise ScreenshotError(f"后端生成了空截图文件：{path}")
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
        return {"format": "png", "bytes": size, "width": width, "height": height}
    if header[:2] == b"\xff\xd8":
        return {"format": "jpeg", "bytes": size, "width": None, "height": None}
    if header[:4] in {b"II*\x00", b"MM\x00*"}:
        return {"format": "tiff", "bytes": size, "width": None, "height": None}
    raise ScreenshotError(f"截图文件不是受支持的图片格式：{path}")


def _attempt(
    name: str,
    argv: Sequence[str],
    output: Path,
    timeout: float,
) -> Dict[str, Any]:
    output.unlink(missing_ok=True)
    try:
        run_command(argv, timeout=timeout)
        image = inspect_image(output)
        return {"backend": name, "status": "ok", "command": command_text(argv), **image}
    except ScreenshotError as exc:
        output.unlink(missing_ok=True)
        return {"backend": name, "status": "failed", "command": command_text(argv), "error": str(exc)}


def _optional_binary(name: str, override: Optional[str]) -> Optional[str]:
    configured = os.environ.get("APM_PYMOBILEDEVICE3_BIN") if name == "pymobiledevice3" else None
    return override or configured or shutil.which(name)


def capture(
    device: Dict[str, Any],
    output: Path,
    backend: str = "auto",
    timeout: float = 60.0,
    force: bool = False,
    pymobiledevice3_bin: Optional[str] = None,
) -> Dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists() and not force:
        raise ScreenshotError(f"输出文件已存在，拒绝覆盖：{output}（需要覆盖请加 --force）")
    output.parent.mkdir(parents=True, exist_ok=True)

    pymobiledevice3 = _optional_binary("pymobiledevice3", pymobiledevice3_bin)
    attempts: List[Dict[str, Any]] = []
    ordered: List[str]
    if backend == "pymobiledevice3":
        ordered = ["pymobiledevice3-native", "pymobiledevice3-usbmux"]
    elif backend == "idevicescreenshot":
        ordered = ["idevicescreenshot"]
    else:
        ordered = ["pymobiledevice3-native", "pymobiledevice3-usbmux", "idevicescreenshot"]

    for name in ordered:
        if name.startswith("pymobiledevice3"):
            if not pymobiledevice3:
                attempts.append(
                    {
                        "backend": name,
                        "status": "unavailable",
                        "error": "未找到 pymobiledevice3；可选安装：python3 -m pip install pymobiledevice3",
                    }
                )
                continue
            if name.endswith("native"):
                argv = [pymobiledevice3, "developer", "dvt", "screenshot", "--native", "--udid", device["udid"], str(output)]
            else:
                argv = [pymobiledevice3, "developer", "dvt", "screenshot", "--udid", device["udid"], str(output)]
        else:
            idevicescreenshot = _optional_binary("idevicescreenshot", None)
            if not idevicescreenshot:
                attempts.append(
                    {
                        "backend": name,
                        "status": "unavailable",
                        "error": "未找到 idevicescreenshot；可安装 libimobiledevice 作为回退",
                    }
                )
                continue
            argv = [idevicescreenshot, "-u", device["udid"], str(output)]
        result = _attempt(name, argv, output, timeout)
        attempts.append(result)
        if result["status"] == "ok":
            return {
                "status": "ok",
                "tool": "apm_screenshot",
                "toolVersion": TOOL_VERSION,
                "backend": name,
                "device": device,
                "output": str(output),
                "attempts": attempts,
                **result,
            }

    raise CaptureFailure("所有 iOS 真机截图后端均不可用", attempts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="iOS 物理真机截图（pymobiledevice3 DVT 优先）")
    parser.add_argument("--device", required=True, help="物理设备 UDID、名称或 devicectl identifier")
    parser.add_argument("--output", required=True, help="输出 PNG/TIFF/JPEG 路径")
    parser.add_argument(
        "--backend",
        choices=("auto", "pymobiledevice3", "idevicescreenshot"),
        default="auto",
        help="截图后端；默认按 DVT → idevicescreenshot 顺序回退",
    )
    parser.add_argument("--timeout-sec", type=float, default=60.0, help="每个后端的超时秒数")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的输出文件")
    parser.add_argument(
        "--pymobiledevice3-bin",
        help="pymobiledevice3 可执行文件路径；用于未加入 PATH 的隔离安装",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not math.isfinite(args.timeout_sec) or args.timeout_sec <= 0:
        print("⛔ --timeout-sec 必须 > 0", file=sys.stderr)
        return 1
    try:
        device = resolve_physical_device(args.device)
        result = capture(
            device,
            Path(args.output),
            backend=args.backend,
            timeout=args.timeout_sec,
            force=args.force,
            pymobiledevice3_bin=args.pymobiledevice3_bin,
        )
    except CaptureFailure as exc:
        payload = {
            "status": "unavailable",
            "tool": "apm_screenshot",
            "toolVersion": TOOL_VERSION,
            "device": locals().get("device"),
            "attempts": exc.attempts,
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"⛔ {exc}", file=sys.stderr)
            for attempt in exc.attempts:
                print(f"  · {attempt['backend']}: {attempt['status']} — {attempt.get('error', '')}", file=sys.stderr)
        return 1
    except ScreenshotError as exc:
        if args.json:
            print(json.dumps({"status": "unavailable", "tool": "apm_screenshot", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"⛔ {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 真机截图成功：{result['output']}")
        print(f"   backend={result['backend']} device={result['device']['udid']} format={result['format']}")
        if result.get("width"):
            print(f"   size={result['width']}x{result['height']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
