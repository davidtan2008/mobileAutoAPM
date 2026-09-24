#!/usr/bin/env python3
"""符号文件产出与校验 —— 符号化流水线的构建期与 CI 门禁。

**为什么需要它**：符号化链路没修好之前，崩溃分析基本是白做的。
而这条链路上有几个非常容易漏的坑（见下），靠人工检查必然出错。

本工具做两件事：
  bundle  按正确姿势产出 RN 产物与 sourcemap（含各平台坑位处理）
  verify  校验一次构建的符号文件是否齐全 —— **可直接作为发版门禁**

已内置的坑位知识：
  1. ⚠️ **iOS 默认不生成 sourcemap** —— 必须在打包脚本里显式导出 SOURCEMAP_FILE
  2. ⚠️ **Hermes 需要两步合成** —— .hbc.map 必须与 Metro map 合成后才能还原行号
  3. ⚠️ Hermes bundle 不是合法 JS，sentry-cli 会创建 **0 字节占位文件**，
     这是**正常现象**，不是上传失败 —— 不要在这里浪费时间 debug
  4. ⚠️ 关联必须用 **debug ID** 而非版本号（热修后版本号会错位）

用法:
  # 产出与校验（在 RN 工程根目录）
  python3 rn_build_symbols.py verify --platform android --build-dir android/app/build
  python3 rn_build_symbols.py verify --platform ios --build-dir ios/build
  python3 rn_build_symbols.py verify --platform harmony

  # CI 门禁：不齐全则退出码 2
  python3 rn_build_symbols.py verify --platform ios --build-dir ios/build --strict
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Artifact:
    name: str
    patterns: list[str]
    required: bool
    why: str


# 各平台发版必须归档的符号文件
SPECS: dict[str, list[Artifact]] = {
    "ios": [
        Artifact("dSYM", ["**/*.app.dSYM", "**/*.dSYM"], True,
                 "原生崩溃符号化的唯一依据。⚠️ 必须 DEBUG_INFORMATION_FORMAT=dwarf-with-dsym"),
        Artifact("RN sourcemap", ["**/*.jsbundle.map", "**/main.jsbundle.map"], True,
                 "⚠️ iOS 默认不生成！需在打包脚本里手动导出 SOURCEMAP_FILE"),
        Artifact("Hermes hbc.map", ["**/*.hbc.map", "**/*.jsbundle.hbc.map"], False,
                 "Hermes 项目必需。缺它只能用字节码偏移，还原不出 JS 行号"),
    ],
    "android": [
        Artifact("mapping.txt", ["**/mapping.txt", "**/outputs/mapping/**/mapping.txt"], True,
                 "R8/ProGuard 混淆还原。必须与 versionCode 严格对应"),
        Artifact("RN sourcemap", ["**/index.android.bundle.map"], True,
                 "RN 崩溃还原的基础"),
        Artifact("Hermes hbc.map", ["**/*.hbc.map"], False,
                 "Hermes 项目必需"),
    ],
    "harmony": [
        Artifact("SO 符号表", ["**/*.so.sym", "**/symbols/**"], True,
                 "鸿蒙 Native 崩溃符号化，按 SO UUID 匹配"),
        Artifact("nameCache", ["**/nameCache*", "**/name_cache*"], True,
                 "鸿蒙混淆还原（对应 Android 的 mapping）"),
        Artifact("sourceMaps", ["**/*.map", "**/sourceMaps/**"], False,
                 "鸿蒙 JS/TS 还原"),
    ],
}


@dataclass
class CheckResult:
    artifact: Artifact
    found: list[str]

    @property
    def ok(self) -> bool:
        return bool(self.found) or not self.artifact.required


def find_artifacts(root: Path, art: Artifact) -> list[str]:
    hits: list[str] = []
    for pat in art.patterns:
        for p in root.glob(pat):
            if p.exists() and (p.is_dir() or p.stat().st_size > 0):
                hits.append(str(p))
    return sorted(set(hits))


def cmd_verify(args) -> int:
    build_dir = Path(args.build_dir).resolve()
    if not build_dir.exists():
        print(f"⛔ 构建目录不存在：{build_dir}")
        return 1

    specs = SPECS.get(args.platform)
    if not specs:
        print(f"⛔ 未知平台 {args.platform}，支持：{', '.join(SPECS)}")
        return 1

    results = [CheckResult(a, find_artifacts(build_dir, a)) for a in specs]

    print("=" * 74)
    print(f"  符号文件校验  |  平台 {args.platform}  |  {build_dir}")
    print("=" * 74)

    for r in results:
        mark = "✅" if r.ok else ("❌" if r.artifact.required else "➖")
        req = "必需" if r.artifact.required else "可选"
        print(f"\n{mark} {r.artifact.name}  [{req}]")
        print(f"    用途: {r.artifact.why}")
        if r.found:
            for f in r.found[:5]:
                print(f"    找到: {f}")
            if len(r.found) > 5:
                print(f"    … 还有 {len(r.found) - 5} 个")
        else:
            print("    未找到")

    # 0 字节文件检查 —— Hermes 的 0 字节占位是**正常**的，但要能区分出来
    zero_byte = [
        str(p) for p in build_dir.rglob("*.map")
        if p.is_file() and p.stat().st_size == 0
    ]
    if zero_byte:
        print("\nℹ️  发现 0 字节的 .map 文件：")
        for z in zero_byte[:3]:
            print(f"    {z}")
        print("    ⚠️ 若这是 Hermes bundle 的 sourcemap 占位，**属正常现象**")
        print("       （Hermes bundle 不是合法 JS，sentry-cli 会创建占位文件指向真实 sourcemap）")
        print("       但若所有 .map 都是 0 字节，说明 sourcemap 根本没生成。")

    missing = [r for r in results if r.artifact.required and not r.found]
    failed_check = [r for r in results if not r.ok]

    print("\n" + "=" * 74)
    if failed_check:
        print(f"⛔ {len(missing)} 项必需符号文件缺失：{', '.join(r.artifact.name for r in missing)}")
        print()
        print("   缺失后果：对应的崩溃**无法符号化**，只能看到地址，定位不了根因。")
        print("   修复后请把该项加入发版门禁，避免再次遗漏。")
        for r in missing:
            if "iOS" in r.artifact.why or "默认不生成" in r.artifact.why:
                print("\n   💡 iOS sourcemap 缺失的常见原因：")
                print("      打包脚本（Bundle React Native code and images）里没有导出 SOURCEMAP_FILE。")
                print("      正确做法见 docs/symbolication-pipeline.md。")
        return 2 if args.strict else 1

    print("✅ 所有必需符号文件齐全。")
    return 0


def cmd_bundle(args) -> int:
    """按正确姿势产出 RN bundle 与 sourcemap。"""
    root = Path.cwd()
    platform = args.platform
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle_name = "main.jsbundle" if platform == "ios" else "index.android.bundle"
    bundle_path = out_dir / bundle_name
    sourcemap_path = out_dir / f"{bundle_name}.map"

    cmd = [
        "npx", "react-native", "bundle",
        "--platform", "ios" if platform == "ios" else "android",
        "--dev", "false",
        "--entry-file", args.entry or "index.js",
        "--bundle-output", str(bundle_path),
        "--sourcemap-output", str(sourcemap_path),
    ]
    if args.assets_dest:
        cmd += ["--assets-dest", args.assets_dest]

    print("执行：")
    print("  " + " ".join(cmd))
    print()
    print("ℹ️  关键点：必须显式传 --sourcemap-output。")
    print("    ⚠️ iOS 的默认打包脚本**不生成** sourcemap —— 线上崩溃就没法还原行号。")
    print()

    if args.dry_run:
        print("（--dry-run，未实际执行）")
        return 0

    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        print("⛔ 找不到 npx。请确认 Node 与 react-native 已安装。")
        return 1
    except subprocess.TimeoutExpired:
        print("⛔ 打包超时。")
        return 1

    if r.returncode != 0:
        print("⛔ 打包失败：")
        print((r.stderr or r.stdout)[-2000:])
        return 1

    got: list[str] = []
    for p in (bundle_path, sourcemap_path):
        if p.exists() and p.stat().st_size > 0:
            got.append(f"{p}  ({p.stat().st_size} 字节)")
        else:
            print(f"⚠️  {p} 未生成或为空")
    print("✅ 产物：")
    for g in got:
        print(f"   {g}")

    # Hermes：合成两步 map
    hbc_map = out_dir / f"{bundle_name}.hbc.map"
    if hbc_map.exists():
        composed = out_dir / f"{bundle_name}.composed.map"
        print(f"\n检测到 Hermes map，正在合成 → {composed}")
        scripts_dir = Path(__file__).resolve().parent
        r2 = subprocess.run(
            [sys.executable, str(scripts_dir / "rn_symbolicate.py"), "compose",
             "--outer", str(hbc_map), "--inner", str(sourcemap_path), "--out", str(composed)],
            capture_output=True, text=True,
        )
        print(r2.stdout.strip())
        if r2.returncode != 0:
            print(r2.stderr.strip())
            return 1
    else:
        print("\nℹ️  未发现 .hbc.map。若项目开启了 Hermes，说明 map 未产出：")
        print("    Android: 在 build.gradle 里确保 hermesFlags 含 \"-output-source-map\"")
        print("    iOS:     检查打包脚本是否把 hbc map 复制到了产物目录")

    return 0


def cmd_manifest(args) -> int:
    """生成符号清单（含 debug ID），供 Agent 与 CI 核对。

    ⚠️ **关联必须用 debug ID 而非版本号** —— 热修后版本号会错位，
    拿它匹配 sourcemap 会静默取到错误的文件。
    """
    root = Path(args.build_dir).resolve()
    manifest: dict = {
        "platform": args.platform,
        "version": args.version,
        "build": args.build_number,
        "debugId": args.debug_id,
        "artifacts": {},
    }
    for art in SPECS.get(args.platform, []):
        manifest["artifacts"][art.name] = find_artifacts(root, art)

    out = Path(args.out) if args.out else root / "symbols-manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 符号清单 → {out}")
    if not args.debug_id:
        print("⚠️  未提供 --debug-id。**强烈建议提供** —— 版本号在热修后会错位，")
        print("   只有 per-build 唯一的 debug ID 才能可靠关联符号文件。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="符号文件产出与校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="校验符号文件是否齐全（CI 门禁）")
    v.add_argument("--platform", required=True, choices=list(SPECS))
    v.add_argument("--build-dir", required=True)
    v.add_argument("--strict", action="store_true", help="缺失时退出码 2")
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bundle", help="产出 RN bundle 与 sourcemap")
    b.add_argument("--platform", required=True, choices=["ios", "android"])
    b.add_argument("--out", required=True)
    b.add_argument("--entry", default="index.js")
    b.add_argument("--assets-dest")
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(func=cmd_bundle)

    m = sub.add_parser("manifest", help="生成符号清单（含 debug ID）")
    m.add_argument("--platform", required=True, choices=list(SPECS))
    m.add_argument("--build-dir", required=True)
    m.add_argument("--version", default="")
    m.add_argument("--build-number", default="")
    m.add_argument("--debug-id", default="")
    m.add_argument("--out")
    m.set_defaults(func=cmd_manifest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
