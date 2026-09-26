#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""APM 标准测量模板（当前 profile：iOS 原生冷启动）。

这个脚本解决 T1 暴露的缺口：采集命令不能靠 Agent 每次临场手拼，更不能把
UDID、bundle id、构建路径硬编码进工程本地脚本。

设计约束：

* 只用 Python 标准库，macOS 自带即可运行；
* 物理真机硬校验，模拟器不能冒充真机；
* 每次样本保留 total、canonical pre-main、完整 stages 与原始日志；
* 缺数据写明确失败状态，绝不写 0；
* 不按 pre-main 自动分层——T1 已证明那只是不稳定相关；
* 采集完成后强制调用 apm_diagnose.py；测量不可信时退出码为 2。

退出码：
- 0：样本完整且方差诊断达到可用门槛；
- 1：配置、设备、App 或采集本身失败；
- 2：采到数据但样本不完整/测量不可信，应先修测量。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import apm_diagnose

TOOL_VERSION = "1.1.0"
PROFILE = "ios-native-startup"
PROFILE_VERSION = 1
DEFAULT_SAMPLES = 5
DEFAULT_INTERVAL_SEC = 5.0
DEFAULT_TIMEOUT_SEC = 20.0
DEFAULT_WARMUP_SEC = 6.0
DEFAULT_WARMUP_LAUNCHES = 1
DEFAULT_MIN_EFFECT_MS = 30.0
MAX_LOG_LINES = 2000
MAX_LOG_LINE_CHARS = 4096
LOG_READY_SEC = 0.3
POST_FRAME_SEC = 0.4
CLOSURE_TOLERANCE_MS = 5.0

FIRST_FRAME_RE = re.compile(
    r"(?:IOSAPM\s+launch\s+total|App\s+launch\s+first-frame|first[- ]frame)"
    r"\s*:\s*(\d+(?:\.\d+)?)\s*ms",
    re.IGNORECASE,
)
EARLY_PREMAIN_RE = re.compile(
    r"EARLY\s+pre-main\s*:\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE
)
SDK_PREMAIN_RE = re.compile(
    r"IOSAPM\s+pre-main\s*:\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE
)
STAGE_LINE_RE = re.compile(
    r"(?:(?:IOSAPM\s+stages|Launch\s+stages)\s*\(since/\+delta\s+ms\)\s*:\s*)?(.+)",
    re.IGNORECASE,
)
STAGE_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=(\d+(?:\.\d+)?)/\+(\d+(?:\.\d+)?)")


class MeasurementError(Exception):
    """测量无法继续。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_number(value: float) -> Any:
    number = float(value)
    if not math.isfinite(number):
        raise MeasurementError("解析到非有限数字")
    return int(number) if number.is_integer() else number


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def command_text(argv: Sequence[str]) -> str:
    return shlex.join(str(item) for item in argv)


def git_context(project_root: Path) -> Dict[str, Any]:
    def git(*args: str) -> Optional[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=normal")
    return {
        "commit": commit,
        "gitDirty": None if status is None else bool(status),
    }


def measurement_signature(context: Dict[str, Any]) -> str:
    canonical = {
        "profile": context["profile"],
        "profileVersion": context["profileVersion"],
        "toolVersion": context["toolVersion"],
        "deviceUdid": context["deviceInfo"]["udid"],
        "deviceOsVersion": context["deviceInfo"].get("osVersion"),
        "deviceOsBuild": context["deviceInfo"].get("osBuild"),
        "platform": context["deviceInfo"].get("platform"),
        "packageId": context["packageId"],
        "buildType": context["build"],
        "startDefinition": context["startDefinition"],
        "endDefinition": context["endDefinition"],
        "launchType": context["launchType"],
        "measurementMethod": context["measurementMethod"],
        "sampleCount": context["samplePlan"]["requested"],
        "intervalSec": context["samplePlan"]["intervalSec"],
        "warmupLaunches": context["samplePlan"].get("warmupLaunches"),
        "deviceReadyTimeoutSec": context["samplePlan"].get("deviceReadyTimeoutSec"),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_app(app_path: Path, package_id: str) -> Path:
    app_path = app_path.expanduser().resolve()
    if not app_path.is_dir() or app_path.suffix != ".app":
        raise MeasurementError(f"App 构建产物不存在或不是 .app：{app_path}")
    info_path = app_path / "Info.plist"
    if not info_path.is_file():
        raise MeasurementError(f"App 缺少 Info.plist：{info_path}")
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (plistlib.InvalidFileException, OSError) as exc:
        raise MeasurementError(f"无法读取 App Info.plist：{exc}") from exc
    actual = info.get("CFBundleIdentifier")
    if actual != package_id:
        raise MeasurementError(
            f"bundle id 不一致：参数={package_id}，构建产物={actual or '不可用'}"
        )
    return app_path


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
        raise MeasurementError(f"命令不存在：{argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MeasurementError(f"命令超时（{timeout:.1f}s）：{command_text(argv)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "无错误输出").strip()
        raise MeasurementError(
            f"命令失败（exit {completed.returncode}）：{command_text(argv)}\n{detail}"
        )
    return completed


def _read_device_list() -> Dict[str, Any]:
    handle, raw_path = tempfile.mkstemp(prefix="apm-devicectl-", suffix=".json")
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
        raise MeasurementError(f"无法解析 devicectl 设备列表：{exc}") from exc
    finally:
        json_path.unlink(missing_ok=True)


def _read_device_details(identifier: str) -> Dict[str, Any]:
    """用 info details 主动建立/确认 CoreDevice tunnel。"""
    handle, raw_path = tempfile.mkstemp(prefix="apm-devicectl-details-", suffix=".json")
    os.close(handle)
    json_path = Path(raw_path)
    try:
        run_command(
            [
                "xcrun",
                "devicectl",
                "device",
                "info",
                "details",
                "--device",
                identifier,
                "--timeout",
                "15",
                "--json-output",
                str(json_path),
                "--quiet",
            ],
            timeout=25,
        )
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise MeasurementError("devicectl info details 没有返回设备详情")
        return result
    except (json.JSONDecodeError, OSError) as exc:
        raise MeasurementError(f"无法解析 devicectl 设备详情：{exc}") from exc
    finally:
        json_path.unlink(missing_ok=True)


def _match_device(payload: Dict[str, Any], identifier: str):
    matches = []
    for device in payload.get("result", {}).get("devices", []):
        hardware = device.get("hardwareProperties", {})
        connection = device.get("connectionProperties", {})
        candidates = {
            device.get("identifier"),
            hardware.get("udid"),
            hardware.get("serialNumber"),
            hardware.get("productType"),
            device.get("deviceProperties", {}).get("name"),
        }
        if identifier in {value for value in candidates if value}:
            matches.append((device, hardware, connection))
    if not matches:
        raise MeasurementError(f"devicectl 找不到设备：{identifier}")
    if len(matches) > 1:
        raise MeasurementError(f"设备标识不唯一：{identifier}")
    return matches[0]


def resolve_physical_device(identifier: str, ready_timeout: float = 30.0) -> Dict[str, Any]:
    """解析物理设备，并等待 CoreDevice tunnel 真正 connected。

    `mobilebuildmcp` 可以在 wired/paired 但 tunnel 尚未恢复时安装 App；这不等于
    性能测量环境已经稳定。测量 profile 必须等待 connected，避免把连接切换成本
    算进第一次启动。
    """
    started = time.monotonic()
    deadline = started + max(0.0, ready_timeout)
    last_state = "unknown"
    last_error = None
    device, hardware, connection = _match_device(_read_device_list(), identifier)
    while True:
        state = connection.get("tunnelState")
        if state not in {"available", "connected"}:
            try:
                details = _read_device_details(identifier)
                device = details
                hardware = details.get("hardwareProperties", {})
                connection = details.get("connectionProperties", {})
                state = connection.get("tunnelState")
            except MeasurementError as exc:
                last_error = str(exc)

        reality = hardware.get("reality")
        if reality != "physical":
            raise MeasurementError(
                f"ios-native-startup 只接受物理真机，当前设备类型={reality or 'unknown'}"
            )
        udid = hardware.get("udid")
        if not isinstance(udid, str) or not udid:
            raise MeasurementError("devicectl 未返回 idevicesyslog 所需的物理 UDID")

        pairing_state = connection.get("pairingState")
        transport = connection.get("transportType")
        last_state = state or "unknown"
        if state in {"available", "connected"}:
            return {
                "udid": udid,
                "coreDeviceIdentifier": device.get("identifier"),
                "model": hardware.get("marketingName"),
                "productType": hardware.get("productType"),
                "platform": hardware.get("platform"),
                "reality": reality,
                "osVersion": device.get("deviceProperties", {}).get("osVersionNumber"),
                "osBuild": device.get("deviceProperties", {}).get("osBuildUpdate"),
                "state": state,
                "pairingState": pairing_state,
                "transportType": transport,
                "connectionMode": "wired-paired" if transport == "wired" else "tunnel",
                "readyWaitSec": max(0.0, time.monotonic() - started),
            }

        if time.monotonic() >= deadline:
            detail = f"；最后错误={last_error}" if last_error else ""
            raise MeasurementError(
                f"设备已发现但 CoreDevice tunnel 未恢复：{hardware.get('marketingName', identifier)}"
                f"（state={last_state}, pairing={pairing_state or 'unknown'}, "
                f"transport={transport or 'unknown'}）{detail}；未开始性能采样"
            )
        time.sleep(1)
        try:
            device, hardware, connection = _match_device(_read_device_list(), identifier)
        except MeasurementError as exc:
            last_error = str(exc)


def parse_stage_line(line: str) -> List[Tuple[str, float, float]]:
    match = STAGE_LINE_RE.search(line)
    if not match:
        return []
    tokens = STAGE_RE.findall(match.group(1))
    names = {token[0] for token in tokens}
    if not tokens or (len(tokens) < 2 and "firstFrame" not in names):
        return []
    return [(name, float(since), float(delta)) for name, since, delta in tokens]


def unique_number(values: Sequence[float]) -> Optional[float]:
    unique = {float(value) for value in values}
    if len(unique) != 1:
        return None
    return next(iter(unique))


def parse_log_lines(lines: Sequence[str]) -> Dict[str, Any]:
    first_values: List[float] = []
    early_premain: List[float] = []
    sdk_premain: List[float] = []
    stage_lines: List[List[Tuple[str, float, float]]] = []
    for raw in lines:
        line = raw[:MAX_LOG_LINE_CHARS]
        first_values.extend(float(match) for match in FIRST_FRAME_RE.findall(line))
        early_premain.extend(float(match) for match in EARLY_PREMAIN_RE.findall(line))
        sdk_premain.extend(float(match) for match in SDK_PREMAIN_RE.findall(line))
        stages = parse_stage_line(line)
        if stages:
            stage_lines.append(stages)

    errors: List[str] = []
    warnings: List[str] = []
    first_frame = unique_number(first_values)
    if first_values and first_frame is None:
        errors.append("同次日志出现多个不同 first-frame 值")

    distinct_stage_lines = {
        tuple((name, since, delta) for name, since, delta in line) for line in stage_lines
    }
    if len(distinct_stage_lines) > 1:
        errors.append("同次日志出现多份不同 stages，无法无歧义配对")
    stage_sequence = stage_lines[0] if stage_lines else []
    stages = [
        {"name": name, "sinceMs": clean_number(since), "deltaMs": clean_number(delta)}
        for name, since, delta in stage_sequence
    ]

    if first_frame is None and stages:
        stage_first = [item for item in stages if item["name"] == "firstFrame"]
        if len(stage_first) == 1:
            first_frame = float(stage_first[0]["sinceMs"])
            warnings.append("first-frame 总结日志缺失，数值取自同次 stages.firstFrame.sinceMs")
    if first_frame is None:
        errors.append("未解析到 first-frame 终点")

    accounted_time: Optional[float] = None
    residual: Optional[float] = None
    closure_tolerance = CLOSURE_TOLERANCE_MS
    if not stages:
        errors.append("未解析到完整 stages")
    else:
        names = [item["name"] for item in stages]
        if names[0] != "processStart":
            errors.append(f"stages 第一段不是 processStart：{names[0]}")
        if names[-1] != "firstFrame":
            errors.append(f"stages 终点不是 firstFrame：{names[-1]}")
        if len(names) != len(set(names)):
            errors.append("stages 含重复阶段名")
        since_values = [float(item["sinceMs"]) for item in stages]
        if any(right < left for left, right in zip(since_values, since_values[1:])):
            errors.append("stages 的 sinceMs 非单调")
        if any(float(item["deltaMs"]) < 0 for item in stages):
            errors.append("stages 含负 delta")
        stage_first = [item for item in stages if item["name"] == "firstFrame"]
        if first_frame is not None and stage_first:
            stage_total = float(stage_first[0]["sinceMs"])
            if abs(stage_total - first_frame) > CLOSURE_TOLERANCE_MS:
                errors.append(
                    f"first-frame 总结与 stages 不一致：{first_frame:.0f} vs {stage_total:.0f}ms"
                )
        if first_frame is not None:
            # 每个 delta 都来自 Int(...) 的毫秒截断；N 个阶段最多累积约 Nms 的
            # 舍入误差。用有界、可解释的动态容差，不把真实缺口放过去。
            closure_tolerance = max(CLOSURE_TOLERANCE_MS, len(stages) + 2.0)
            # 第一段 delta 在不同 SDK 实现里可能是 0，也可能已包含 process→mark；
            # 统一用「第一段 since + 后续 delta」验证闭合，不猜实现细节。
            accounted_time = float(stages[0]["sinceMs"]) + sum(
                float(item["deltaMs"]) for item in stages[1:]
            )
            residual = first_frame - accounted_time
            if abs(residual) > closure_tolerance:
                errors.append(
                    f"阶段时间不闭合：已归因 {accounted_time:.0f}ms / total {first_frame:.0f}ms"
                )
            elif abs(residual) >= 1.0:
                warnings.append(
                    f"阶段整数舍入残差 {residual:.0f}ms，在 {closure_tolerance:.0f}ms 理论上限内"
                )

    early_value = unique_number(early_premain)
    if early_premain and early_value is None:
        errors.append("同次日志出现多个不同 EARLY pre-main 值")
    sdk_unique = list(dict.fromkeys(sdk_premain))
    if early_value is not None:
        premain = early_value
        premain_source = "early_marker"
        if sdk_unique and abs(sdk_unique[0] - early_value) > 1.0:
            warnings.append("EARLY 与 IOSAPM pre-main 候选不同；canonical 采用显式 EARLY 值")
    elif sdk_unique:
        # 当前 ios-apm 的 C 构造与 LaunchTracker.report 使用同一前缀；syslog 保序，
        # canonical 取本次启动最先出现的值，并保留全部候选，绝不拿 activate 时刻冒充 C 时刻。
        premain = sdk_premain[0]
        premain_source = "iosapm_first_log"
        if len(sdk_unique) > 1:
            warnings.append("IOSAPM pre-main 同名前缀出现多个值；canonical 采用首个日志值并保留候选")
    else:
        premain = None
        premain_source = "unavailable"
        errors.append("pre-main 不可用；不会用 0 代替")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "firstFrameMs": clean_number(first_frame) if first_frame is not None else None,
        "premainMs": clean_number(premain) if premain is not None else None,
        "premainSource": premain_source,
        "premainCandidatesMs": [clean_number(value) for value in early_premain + sdk_premain],
        "stages": stages,
        "accountedTimeMs": clean_number(accounted_time) if accounted_time is not None else None,
        "unattributedResidualMs": clean_number(residual) if residual is not None else None,
        "closureToleranceMs": clean_number(closure_tolerance) if stages else None,
    }


class LogCollector:
    """有界读取 idevicesyslog；只保留与启动口径有关的有界日志。"""

    def __init__(self, udid: str, log_match: Optional[str] = None):
        self.udid = udid
        self.log_match = log_match
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.first_frame_event = threading.Event()
        self._lock = threading.Lock()
        self._lines: List[str] = []
        self._dropped = 0

    def start(self) -> None:
        argv = ["idevicesyslog", "--no-colors", "-u", self.udid]
        if self.log_match:
            argv.extend(["--match", self.log_match])
        try:
            self.process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise MeasurementError("命令不存在：idevicesyslog") from exc
        self.thread = threading.Thread(target=self._read, name="apm-ios-syslog", daemon=True)
        self.thread.start()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for raw in self.process.stdout:
            line = raw.rstrip("\n")
            has_frame = bool(FIRST_FRAME_RE.search(line))
            has_stages = bool(parse_stage_line(line))
            has_premain = bool(EARLY_PREMAIN_RE.search(line) or SDK_PREMAIN_RE.search(line))
            if not (has_frame or has_stages or has_premain):
                continue
            with self._lock:
                if len(self._lines) < MAX_LOG_LINES:
                    self._lines.append(line[:MAX_LOG_LINE_CHARS])
                else:
                    self._dropped += 1
            if has_frame or has_stages:
                self.first_frame_event.set()

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._dropped = 0
        self.first_frame_event.clear()

    def wait_for_frame(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.first_frame_event.wait(0.1):
                return True
            if self.process is not None and self.process.poll() is not None:
                return False
        return False

    def snapshot(self) -> Tuple[List[str], int]:
        with self._lock:
            return list(self._lines), self._dropped

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.thread is not None:
            self.thread.join(timeout=2)
        if process.stdout is not None:
            process.stdout.close()


def tool_preflight() -> Dict[str, str]:
    paths: Dict[str, str] = {}
    for executable in ("xcrun", "idevicesyslog"):
        path = shutil.which(executable)
        if not path:
            raise MeasurementError(f"缺少必需工具：{executable}")
        paths[executable] = path
    return paths


def build_context(
    args: argparse.Namespace,
    app_path: Path,
    device: Dict[str, Any],
    project_root: Path,
    started_at: str,
) -> Dict[str, Any]:
    device_label = " / ".join(
        item
        for item in (
            device.get("model"),
            device.get("osVersion"),
            "physical",
            device.get("udid"),
        )
        if item
    )
    context: Dict[str, Any] = {
        "schemaVersion": 1,
        "profile": PROFILE,
        "profileVersion": PROFILE_VERSION,
        "toolVersion": TOOL_VERSION,
        "device": device_label,
        "deviceInfo": device,
        "build": args.build_type,
        "buildInfo": {
            "type": args.build_type,
            "appPath": str(app_path),
            "packageId": args.package_id,
            "debugId": None,
        },
        "packageId": args.package_id,
        "projectRoot": str(project_root),
        "command": command_text(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        ),
        "measurementMethod": "devicectl install + terminate-existing launch + bounded idevicesyslog",
        "startDefinition": "process creation time from sysctl(KERN_PROC_PID)",
        "endDefinition": "firstFrame mark one runloop after onAppear (near CA::Transaction::commit)",
        "launchType": "cold",
        "samplePlan": {
            "requested": args.samples,
            "intervalSec": args.interval_sec,
            "timeoutSec": args.timeout_sec,
            "warmupSec": args.warmup_sec,
            "warmupLaunches": args.warmup_launches,
            "deviceReadyTimeoutSec": args.device_ready_timeout,
        },
        "premainPolicy": "record paired values; never auto-stratify or post-hoc correct",
        "stageClosureToleranceMs": CLOSURE_TOLERANCE_MS,
        "stageClosureToleranceRule": "max(5ms, stageCount + 2ms; integer log truncation)",
        "rawDataPath": "raw/",
        "startedAt": started_at,
        "finishedAt": None,
    }
    context.update(git_context(project_root))
    context["measurementSignature"] = measurement_signature(context)
    return context


def default_output(project_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return project_root / ".apm" / "runs" / f"{timestamp}-ios-startup"


def validate_args(args: argparse.Namespace) -> None:
    if args.samples < 1 or args.samples > 100:
        raise MeasurementError("--samples 必须在 1–100")
    if args.interval_sec < DEFAULT_INTERVAL_SEC:
        raise MeasurementError(f"--interval-sec 不得小于 {DEFAULT_INTERVAL_SEC:g}s")
    if args.timeout_sec <= 0 or args.warmup_sec < 0:
        raise MeasurementError("--timeout-sec 必须 > 0，--warmup-sec 必须 >= 0")
    if args.warmup_launches < 1 or args.warmup_launches > 20:
        raise MeasurementError("--warmup-launches 必须在 1–20")
    if args.device_ready_timeout < 0:
        raise MeasurementError("--device-ready-timeout 必须 >= 0")
    if not args.build_type.strip():
        raise MeasurementError("--build-type 不能为空")
    if not args.package_id.strip() or not args.device.strip():
        raise MeasurementError("--device 与 --package-id 不能为空")


def metrics_from_observations(
    observations: Sequence[Dict[str, Any]], min_effect_ms: float
) -> Tuple[List[Dict[str, Any]], List[str]]:
    valid = [item for item in observations if item.get("status") == "valid"]
    metrics: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def add(name: str, samples: Sequence[float], unit: str = "ms", min_effect: Any = None) -> None:
        if not samples:
            return
        metrics.append(
            {
                "name": name,
                "unit": unit,
                "direction": "lower_is_better",
                "samples": [clean_number(value) for value in samples],
                "minEffect": min_effect,
            }
        )

    add(
        "startup.cold.first_frame",
        [float(item["firstFrameMs"]) for item in valid],
        min_effect=min_effect_ms,
    )
    add(
        "startup.premain",
        [float(item["premainMs"]) for item in valid],
        min_effect=min(5.0, min_effect_ms),
    )
    first_stages = [item["stages"][0] for item in valid if item.get("stages")]
    add(
        "startup.process_to_first_mark",
        [float(item["sinceMs"]) for item in first_stages],
    )

    stage_names = [
        stage["name"]
        for item in valid
        for stage in item.get("stages", [])
    ]
    unique_stage_names = sorted(set(stage_names))
    consistent = all(
        {stage["name"] for stage in item.get("stages", [])} == set(unique_stage_names)
        for item in valid
    )
    if valid and not consistent:
        warnings.append("不同样本的 stage 覆盖不一致；为避免错配，本次不导出 stage 指标")
    elif consistent:
        for stage_name in unique_stage_names:
            samples = [
                float(stage["deltaMs"])
                for item in valid
                for stage in item["stages"]
                if stage["name"] == stage_name
            ]
            add(f"startup.stage.{stage_name}", samples)
    return metrics, warnings


def write_legacy_raw(output: Path, observations: Sequence[Dict[str, Any]]) -> None:
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    valid = [item for item in observations if item.get("status") == "valid"]
    (raw_dir / "samples.txt").write_text(
        "".join(f"{item['firstFrameMs']}\n" for item in valid), encoding="utf-8"
    )
    (raw_dir / "premain.txt").write_text(
        "".join(f"{item['premainMs']}\n" for item in valid), encoding="utf-8"
    )
    (raw_dir / "paired.txt").write_text(
        "".join(f"{item['premainMs']} {item['firstFrameMs']}\n" for item in valid),
        encoding="utf-8",
    )
    (raw_dir / "stages.txt").write_text(
        "".join(
            " ".join(
                f"{stage['name']}={stage['sinceMs']}/+{stage['deltaMs']}"
                for stage in item["stages"]
            )
            + "\n"
            for item in valid
        ),
        encoding="utf-8",
    )


def execute_measurement(
    args: argparse.Namespace, run_diagnosis: bool = True
) -> int:
    validate_args(args)
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise MeasurementError(f"项目根目录不存在：{project_root}")
    app_path = verify_app(Path(args.build_path), args.package_id)
    tool_preflight()

    if args.dry_run:
        plan = {
            "profile": PROFILE,
            "device": args.device,
            "packageId": args.package_id,
            "buildType": args.build_type,
            "appPath": str(app_path),
            "projectRoot": str(project_root),
            "output": str(Path(args.output).expanduser().resolve() if args.output else default_output(project_root)),
            "samples": args.samples,
            "intervalSec": args.interval_sec,
            "warmupLaunches": args.warmup_launches,
            "physicalDeviceWillBeResolved": True,
            "willInstallApp": True,
            "willRunWarmup": True,
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    device = resolve_physical_device(args.device, args.device_ready_timeout)
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else default_output(project_root)
    )
    if output.exists():
        raise MeasurementError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    (output / "raw").mkdir()

    started_at = utc_now()
    context = build_context(args, app_path, device, project_root, started_at)
    write_json(output / "status.json", {"state": "running", "startedAt": started_at})
    write_json(
        output / "meta.json",
        {
            "schemaVersion": 1,
            "profile": PROFILE,
            "context": context,
            "installCommand": [
                "xcrun", "devicectl", "device", "install", "app",
                "--device", device["udid"], "--timeout", "120", "--quiet", str(app_path),
            ],
            "launchCommand": [
                "xcrun", "devicectl", "device", "process", "launch",
                "--device", device["udid"], "--terminate-existing",
                "--timeout", "30", "--quiet", args.package_id,
            ],
        },
    )

    install_argv = [
        "xcrun", "devicectl", "device", "install", "app",
        "--device", device["udid"], "--timeout", "120", "--quiet", str(app_path),
    ]
    launch_argv = [
        "xcrun", "devicectl", "device", "process", "launch",
        "--device", device["udid"], "--terminate-existing",
        "--timeout", "30", "--quiet", args.package_id,
    ]

    try:
        print(f"▎安装构建产物：{app_path}")
        run_command(install_argv, timeout=130)
        print(f"▎预热 {args.warmup_launches} 次（不计入样本）")
        for _warmup_index in range(args.warmup_launches):
            run_command(launch_argv, timeout=35)
            if args.warmup_sec:
                time.sleep(args.warmup_sec)

        observations: List[Dict[str, Any]] = []
        for index in range(1, args.samples + 1):
            sample_started = utc_now()
            collector = LogCollector(device["udid"], args.log_match)
            collector.start()
            time.sleep(LOG_READY_SEC)
            collector.clear()
            try:
                run_command(launch_argv, timeout=35)
                observed = collector.wait_for_frame(args.timeout_sec)
                if observed and POST_FRAME_SEC:
                    time.sleep(POST_FRAME_SEC)
            finally:
                collector.stop()
            lines, dropped = collector.snapshot()
            raw_name = f"sample-{index:02d}.log"
            (output / "raw" / raw_name).write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
            parsed = parse_log_lines(lines)
            status = "valid" if observed and parsed["valid"] else "invalid"
            errors = list(parsed["errors"])
            if not observed:
                errors.append(f"{args.timeout_sec:.1f}s 内未观察到 first-frame 日志")
            if dropped:
                errors.append(f"日志超过有界缓冲 {MAX_LOG_LINES} 行，丢弃 {dropped} 行")
            observation = {
                "index": index,
                "startedAt": sample_started,
                "status": status if not errors else "invalid",
                "firstFrameMs": parsed["firstFrameMs"],
                "premainMs": parsed["premainMs"],
                "premainSource": parsed["premainSource"],
                "premainCandidatesMs": parsed["premainCandidatesMs"],
                "stages": parsed["stages"],
                "accountedTimeMs": parsed["accountedTimeMs"],
                "unattributedResidualMs": parsed["unattributedResidualMs"],
                "closureToleranceMs": parsed["closureToleranceMs"],
                "rawLog": f"raw/{raw_name}",
                "errors": errors,
                "warnings": parsed["warnings"],
            }
            observations.append(observation)
            if observation["status"] == "valid":
                print(
                    f"  #{index} first-frame={observation['firstFrameMs']}ms "
                    f"pre-main={observation['premainMs']}ms ✅"
                )
            else:
                print(f"  #{index} 失败：{'；'.join(errors)}")
            if index < args.samples:
                time.sleep(args.interval_sec)
    except Exception as exc:
        write_json(
            output / "status.json",
            {
                "state": "failed",
                "startedAt": started_at,
                "finishedAt": utc_now(),
                "error": str(exc),
            },
        )
        raise

    metrics, metric_warnings = metrics_from_observations(observations, args.min_effect_ms)
    context["finishedAt"] = utc_now()
    context["validSampleCount"] = sum(item["status"] == "valid" for item in observations)
    context["invalidSampleCount"] = sum(item["status"] != "valid" for item in observations)
    if metric_warnings:
        context["warnings"] = metric_warnings
    payload = {"context": context, "metrics": metrics, "observations": observations}
    write_json(output / "metrics.json", payload)
    write_json(output / "meta.json", {**json.loads((output / "meta.json").read_text(encoding="utf-8")), "finishedContext": context})
    write_legacy_raw(output, observations)

    valid_count = context["validSampleCount"]
    diagnosis_error = None
    if valid_count == 0:
        diagnosis_code = 1
        diagnosis_state = "unavailable"
    elif run_diagnosis:
        try:
            report = apm_diagnose.build_report(
                [output], "startup.cold.first_frame", apm_diagnose.DEFAULT_MIN_SAMPLES, None
            )
            write_json(output / "diagnosis.json", report)
            apm_diagnose.print_text(report)
            diagnosis_code = int(report["verdict"]["exitCode"])
            diagnosis_state = "usable" if diagnosis_code == 0 else "measurement_unreliable"
        except apm_diagnose.DiagnoseError as exc:
            diagnosis_error = str(exc)
            write_json(
                output / "diagnosis.json",
                {
                    "schemaVersion": 1,
                    "tool": "apm_diagnose",
                    "status": "unavailable",
                    "error": diagnosis_error,
                },
            )
            print(f"⛔ 方差诊断失败：{diagnosis_error}", file=sys.stderr)
            diagnosis_code = 1
            diagnosis_state = "unavailable"
    else:
        diagnosis_code = 0
        diagnosis_state = "skipped_internal_test"

    if valid_count == 0 or diagnosis_error:
        state = "failed"
        exit_code = 1
    elif valid_count < args.samples or diagnosis_code == 2:
        state = "measurement_unreliable"
        exit_code = 2
    else:
        state = "complete"
        exit_code = 0
    write_json(
        output / "status.json",
        {
            "state": state,
            "startedAt": started_at,
            "finishedAt": context["finishedAt"],
            "requestedSamples": args.samples,
            "validSamples": valid_count,
            "invalidSamples": context["invalidSampleCount"],
            "diagnosis": diagnosis_state,
            "diagnosisError": diagnosis_error,
            "exitCode": exit_code,
        },
    )
    print(f"\n→ {output / 'metrics.json'}")
    return exit_code


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须 >= 1")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是有限正数")
    return number


def nonnegative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("必须是有限非负数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="APM 参数化标准测量模板")
    parser.add_argument("--profile", choices=(PROFILE,), default=PROFILE)
    parser.add_argument(
        "--device",
        required=True,
        help=(
            "物理设备标识。三种写法都接受："
            "CoreDevice identifier（如 D7F8B1F0-...）、"
            "UDID（如 00008110-...）、"
            "或设备名（如 '菀墨'）。"
            "内部一律解析成 CoreDevice + UDID 后使用；"
            "模拟器会被硬校验拒绝。"
        ),
    )
    parser.add_argument("--package-id", required=True, help="iOS bundle identifier")
    parser.add_argument("--build-type", required=True, help="例如 Release；不可留空")
    parser.add_argument("--build-path", required=True, help="编译出的 .app 路径")
    parser.add_argument("--project-root", default=".", help="用于 commit 与默认输出路径")
    parser.add_argument("--output", help="run 目录；默认 .apm/runs/<timestamp>-ios-startup")
    parser.add_argument("--samples", type=positive_int, default=DEFAULT_SAMPLES)
    parser.add_argument("--interval-sec", type=positive_float, default=DEFAULT_INTERVAL_SEC)
    parser.add_argument("--timeout-sec", type=positive_float, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--device-ready-timeout", type=nonnegative_float, default=30.0,
                        help="等待 CoreDevice tunnel connected 的秒数")
    parser.add_argument("--warmup-sec", type=nonnegative_float, default=DEFAULT_WARMUP_SEC)
    parser.add_argument("--warmup-launches", type=positive_int, default=DEFAULT_WARMUP_LAUNCHES,
                        help="安装后不计入样本的预热启动次数")
    parser.add_argument("--min-effect-ms", type=nonnegative_float, default=DEFAULT_MIN_EFFECT_MS)
    parser.add_argument("--log-match", help="传给 idevicesyslog 的可选字面量过滤；默认不过滤")
    parser.add_argument("--dry-run", action="store_true", help="只验证本地产物与计划，不连接设备")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return execute_measurement(args, run_diagnosis=True)
    except MeasurementError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⛔ 测量被用户中断", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
