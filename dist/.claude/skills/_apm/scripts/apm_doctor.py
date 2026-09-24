#!/usr/bin/env python3
"""APM Doctor — 检查 iOS / React Native / Android / HarmonyOS 的 APM 工具链就绪度。

Agent 在执行任何性能/崩溃任务前应当先跑这个脚本，确认"哪些能力现在真的能做"，
而不是假设工具存在后失败。

用法:
  python3 apm_doctor.py                  # 人类可读报告
  python3 apm_doctor.py --json           # 机器可读（供 Agent 解析）
  python3 apm_doctor.py --platform ios   # 只检查某平台: ios|rn|android|harmony
  python3 apm_doctor.py --strict         # 有 required 缺失时以退出码 1 结束
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

TIMEOUT = 8


@dataclass
class Tool:
    key: str
    cmd: str
    purpose: str
    platforms: list[str]
    required: bool
    install: str
    version_args: list[str] = field(default_factory=lambda: ["--version"])
    # 某些工具 --version 会挂起或返回非 0，用这个覆盖
    version_ok_codes: tuple[int, ...] = (0,)


SPEC: list[Tool] = [
    # ---------- 基础 ----------
    Tool("python3", "python3", "数据平面脚本运行时（本插件所有 scripts 依赖）", ["all"], True,
         "系统自带 / brew install python@3.13"),
    Tool("jq", "jq", "解析 trace / profile JSON", ["all"], False,
         "brew install jq"),
    Tool("git", "git", "版本管理与基线提交", ["all"], True, "xcode-select --install"),

    # ---------- Agent 宿主 ----------
    Tool("claude", "claude", "Claude Code CLI（skill/plugin 宿主）", ["all"], True,
         "npm i -g @anthropic-ai/claude-code"),
    Tool("mobilebuildmcp", "mobilebuildmcp", "iOS 构建/模拟器/UI自动化/LLDB 的统一入口", ["ios"], True,
         "npm i -g mobilebuildmcp@latest 或 brew tap getsentry/xcodebuildmcp && brew install mobilebuildmcp",
         ["--version"]),

    # ---------- iOS 工具链 ----------
    Tool("xcodebuild", "xcodebuild", "构建 iOS 工程", ["ios"], True, "App Store 安装 Xcode"),
    Tool("xcrun", "xcrun", "调用 Xcode 工具链的入口", ["ios"], True, "随 Xcode"),
    Tool("simctl", "xcrun", "模拟器管理 (xcrun simctl)", ["ios"], True, "随 Xcode"),
    Tool("xctrace", "xcrun", "Instruments 命令行：录制 Time Profiler / App Launch / Allocations",
         ["ios"], True, "随 Xcode（xcrun xctrace）", ["xctrace", "version"]),
    Tool("instruments", "xcrun", "传统 Instruments CLI（老版本兼容）", ["ios"], False,
         "随 Xcode（xcrun instruments）", ["instruments", "-s", "templates"]),
    Tool("dsymutil", "dsymutil", "生成/校验 dSYM，崩溃符号化第一步", ["ios"], True, "随 Xcode"),
    Tool("atos", "atos", "地址 → 符号，崩溃栈符号化核心", ["ios"], True, "随 Xcode"),
    Tool("symbolicatecrash", "symbolicatecrash", "符号化 .crash 文件", ["ios"], False,
         "find /Applications/Xcode.app -name symbolicatecrash"),
    Tool("xcresulttool", "xcrun", "解析 .xcresult（测试结果/覆盖率/性能指标）", ["ios"], True,
         "随 Xcode（xcrun xcresulttool）", ["xcresulttool", "version"]),
    Tool("xccov", "xcrun", "代码覆盖率", ["ios"], False, "随 Xcode（xcrun xccov）", ["xccov", "version"]),
    Tool("plutil", "plutil", "读写 Info.plist / MetricKit payload", ["ios"], True, "随 macOS"),
    Tool("codesign", "codesign", "签名检查（真机安装失败排查）", ["ios"], False, "随 macOS"),
    Tool("idevicesyslog", "idevicesyslog", "真机 syslog（libimobiledevice）", ["ios"], False,
         "brew install libimobiledevice"),

    # ---------- React Native ----------
    Tool("node", "node", "RN / Metro / 各类 JS 工具链运行时", ["rn"], True, "brew install node 或 nvm"),
    Tool("npm", "npm", "包管理", ["rn"], True, "随 node"),
    Tool("watchman", "watchman", "Metro 文件监听（损坏会导致 RN 热更新失效）", ["rn"], True,
         "brew install watchman"),
    Tool("npx", "npx", "运行 react-native / metro CLI", ["rn"], True, "随 node"),
    Tool("source-map", "node", "source-map 包：RN/Hermes 堆栈还原", ["rn"], False,
         "npm i -g source-map"),
    Tool("hermesc", "hermesc", "Hermes 编译器，把栈地址还原成 JS 行号", ["rn"], False,
         "随 react-native（node_modules/hermes-engine）"),
    Tool("react-native", "npx", "RN CLI（start/bundle/run）", ["rn"], False,
         "npx react-native --version"),

    # ---------- Android ----------
    Tool("adb", "adb", "Android 调试桥：装包/日志/性能采集", ["android"], True,
         "brew install --cask android-platform-tools"),
    Tool("java", "java", "Gradle / AGP 运行时", ["android"], True, "brew install openjdk"),
    Tool("gradle", "gradle", "Android 构建", ["android"], False, "随工程 gradlew 或 brew install gradle"),
    Tool("emulator", "emulator", "Android 模拟器", ["android"], False, "Android Studio SDK Manager"),
    Tool("bundletool", "bundletool", "AAB 分析 / 安装测试", ["android"], False,
         "brew install bundletool"),

    # ---------- HarmonyOS ----------
    Tool("hvigorw", "hvigorw", "鸿蒙构建（等价于 gradle）", ["harmony"], True,
         "DevEco Studio → SDK 中配置，并把 ohpm/hvigorw 加入 PATH"),
    Tool("ohpm", "ohpm", "鸿蒙包管理", ["harmony"], True, "DevEco Studio 自带"),
    Tool("hdc", "hdc", "鸿蒙设备调试桥（等价于 adb）", ["harmony"], True, "DevEco Studio 自带"),
    Tool("hidumper", "hdc", "鸿蒙性能/内存/栈信息（hdc shell hidumper）", ["harmony"], False,
         "随 HarmonyOS SDK"),

    # ---------- 自动化测试 ----------
    Tool("maestro", "maestro", "跨平台 UI 自动化（YAML 流程，最易被 Agent 生成）", ["ios", "rn", "android"], False,
         "curl -Ls https://get.maestro.mobile.dev | bash"),
    Tool("appium", "appium", "跨平台 UI 自动化（WebDriver 协议）", ["rn", "android"], False,
         "npm i -g appium"),
    Tool("idb", "idb", "Facebook iOS 调试桥（比 simctl 更强的自动化）", ["ios"], False,
         "pip3 install fb-idb && brew tap facebook/fb && brew install idb-companion"),
    Tool("detox", "detox", "RN 端到端测试（灰盒，最适合 RN）", ["rn"], False,
         "npm i -D detox"),

    # ---------- 数据上报 / 可观测后端 ----------
    Tool("sentry-cli", "sentry-cli", "Sentry 符号化文件上传 + release 管理", ["all"], False,
         "brew install getsentry/tools/sentry-cli"),
    Tool("firebase", "firebase", "Firebase CLI（Crashlytics / Perf / MCP）", ["all"], False,
         "npm i -g firebase-tools"),
    Tool("otel-cli", "otel-cli", "OpenTelemetry 本地打点验证", ["all"], False,
         "brew install equinix-labs/otel-cli/otel-cli"),
]

CAPABILITIES = {
    "启动耗时检测": ["mobilebuildmcp", "xctrace", "xcresulttool", "python3"],
    "页面渲染/卡顿检测": ["mobilebuildmcp", "xctrace", "python3"],
    "白屏检测": ["mobilebuildmcp", "python3"],
    "内存检测": ["xctrace", "mobilebuildmcp", "python3"],
    "崩溃符号化与根因": ["atos", "dsymutil", "mobilebuildmcp", "python3"],
    "UI 自动化回归": ["mobilebuildmcp"],
    "RN 专项（JS/堆栈/Hermes）": ["node", "watchman", "python3"],
    "Android 专项": ["adb", "java"],
    "鸿蒙专项": ["hvigorw", "ohpm", "hdc"],
}


def detect(tool: Tool) -> dict:
    """检测单个工具是否可用并取版本。"""
    real = tool.cmd
    path = shutil.which(real)
    # xcrun 包装的工具：用 xcrun 探测
    is_xcrun_wrapper = tool.version_args and tool.version_args[0] == tool.key and tool.cmd == "xcrun"

    if path is None and not is_xcrun_wrapper:
        return {"key": tool.key, "status": "missing", "version": None,
                "path": None, "purpose": tool.purpose,
                "platforms": tool.platforms, "required": tool.required, "install": tool.install}

    version = None
    try:
        if tool.cmd == "xcrun":
            argv = ["xcrun", *tool.version_args]
        else:
            argv = [tool.cmd, *tool.version_args]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        out = (r.stdout or r.stderr or "").strip()
        if out:
            version = out.splitlines()[0][:120]
    except Exception:
        version = None

    return {"key": tool.key, "status": "ok" if path or is_xcrun_wrapper else "missing",
            "version": version, "path": path, "purpose": tool.purpose,
            "platforms": tool.platforms, "required": tool.required, "install": tool.install}


def main() -> int:
    ap = argparse.ArgumentParser(description="APM 工具链自检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--platform", choices=["ios", "rn", "android", "harmony", "all"], default="all")
    ap.add_argument("--strict", action="store_true", help="required 缺失则退出码 1")
    args = ap.parse_args()

    results = [detect(t) for t in SPEC]
    if args.platform != "all":
        results = [r for r in results
                   if "all" in r["platforms"] or args.platform in r["platforms"]]

    # 能力就绪度
    by_key = {r["key"]: r for r in results}
    caps = {}
    for cap, need in CAPABILITIES.items():
        missing = [n for n in need if by_key.get(n, {}).get("status") != "ok"]
        # 未纳入本次过滤的工具不计入缺失
        missing = [m for m in missing if m in by_key]
        caps[cap] = {"ready": not missing, "missing": missing,
                     "note": "可执行" if not missing else f"缺少 {'/'.join(missing)}"}

    env_info = {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
    }

    payload = {"env": env_info, "tools": results, "capabilities": caps,
               "missing_required": [r["key"] for r in results
                                    if r["required"] and r["status"] != "ok"]}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("=" * 74)
    print(f"  APM Doctor  |  {env_info['os']} {env_info['arch']}  |  python {env_info['python']}")
    print("=" * 74)

    groups = [
        ("基础", ["python3", "jq", "git"]),
        ("Agent 宿主", ["claude", "mobilebuildmcp"]),
        ("iOS 工具链", ["xcodebuild", "xcrun", "simctl", "xctrace", "instruments", "dsymutil",
                        "atos", "symbolicatecrash", "xcresulttool", "xccov", "plutil",
                        "codesign", "idevicesyslog"]),
        ("React Native", ["node", "npm", "watchman", "npx", "source-map", "hermesc", "react-native"]),
        ("Android", ["adb", "java", "gradle", "emulator", "bundletool"]),
        ("HarmonyOS", ["hvigorw", "ohpm", "hdc", "hidumper"]),
        ("自动化测试", ["maestro", "appium", "idb", "detox"]),
        ("上报/符号化后端", ["sentry-cli", "firebase", "otel-cli"]),
    ]

    for title, keys in groups:
        rows = [by_key[k] for k in keys if k in by_key]
        if not rows:
            continue
        print(f"\n▎{title}")
        for r in rows:
            mark = "✅" if r["status"] == "ok" else ("❌" if r["required"] else "➖")
            req = "必需" if r["required"] else "可选"
            ver = f"  {r['version']}" if r["version"] else ""
            print(f"  {mark} {r['key']:<18} [{req}]{ver}")
            if r["status"] != "ok":
                print(f"     └─ 用途: {r['purpose']}")
                print(f"     └─ 安装: {r['install']}")

    print("\n" + "=" * 74)
    print("  能力就绪度（决定 Agent 现在能自主做什么）")
    print("=" * 74)
    for cap, info in caps.items():
        mark = "✅ 就绪  " if info["ready"] else "⚠️  受限  "
        print(f"  {mark} {cap:<26} {info['note']}")

    if payload["missing_required"]:
        print(f"\n⛔ 缺失的必需工具: {', '.join(payload['missing_required'])}")
        print("   请先安装；这些是自主闭环的硬前提。")
        return 1 if args.strict else 0
    print("\n✅ 所有必需工具就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
