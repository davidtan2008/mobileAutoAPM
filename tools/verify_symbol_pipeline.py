#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""RN 符号化流水线端到端自检（真实工具链，不用合成 fixture）。

## 为什么需要它

`rn_symbolicate.py` / `rn_build_symbols.py` 的单元测试用的是**简写**堆栈
（`anonymous@1:999`），它们全绿；但真实 Hermes 运行时输出是

    at anonymous (address at /abs/path/app.hbc:1:49386)

路径带空格与冒号，早先的解析走不通 → **20/20 帧全部还原失败**，
而所有测试依然通过。这类缺陷只有跑真实工具链才暴露。

本脚本用真实 RN 工程执行完整链路：

    react-native bundle → hermesc → compose → 真实堆栈 → 符号化

任何一环对不上就**非零退出**，可直接用作 CI 门禁。

## 用法

```bash
python3 tools/verify_symbol_pipeline.py --rn-app /path/to/HelloRN
```

前置：目标目录需已 `npm install`（有 node_modules）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins" / "mobile-apm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rn_symbolicate  # noqa: E402


class StageError(Exception):
    """流水线某一环失败。"""


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 1800) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise StageError(f"命令失败（exit {r.returncode}）：{' '.join(cmd[:6])} …\n{tail}")
    return r.stdout


def find_hermes(rn_app: Path) -> Path:
    base = rn_app / "node_modules" / "react-native" / "sdks" / "hermesc"
    for sub in ("osx-bin", "linux64-bin", "win64-bin"):
        p = base / sub
        if p.is_dir():
            return p
    raise StageError(f"找不到 hermesc：{base}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RN 符号化流水线端到端自检")
    ap.add_argument("--rn-app", required=True, help="已 npm install 的 RN 工程目录")
    ap.add_argument("--keep", action="store_true", help="保留临时产物")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rn_app = Path(args.rn_app).resolve()
    if not (rn_app / "node_modules").is_dir():
        print(f"⛔ 缺少 node_modules：{rn_app}", file=sys.stderr)
        return 1
    entry = rn_app / "index.js"
    if not entry.exists():
        print(f"⛔ 缺少入口 index.js：{entry}", file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp(prefix="rn-symbol-"))
    steps: List[dict] = []

    def record(name: str, detail: str) -> None:
        steps.append({"step": name, "detail": detail})
        print(f"  ✅ {name} —— {detail}")

    try:
        # 1) 真实 RN 打包（必须显式产出 sourcemap）
        packager = work / "packager"
        packager.mkdir(parents=True, exist_ok=True)
        run(
            [
                sys.executable, str(SCRIPTS / "rn_build_symbols.py"),
                "bundle", "--platform", "ios", "--out", str(packager),
            ],
            cwd=rn_app,
        )
        bundle = packager / "main.jsbundle"
        metro_map = packager / "main.jsbundle.map"
        if not bundle.exists() or not metro_map.exists():
            raise StageError("打包未产出 main.jsbundle / .map")
        record("1. react-native bundle", f"bundle={bundle.stat().st_size}B map={metro_map.stat().st_size}B")

        # 2) 真实 hermesc 编译成 HBC（严格照 RN 的 react-native-xcode.sh：
        #    -output-source-map 不带值，Hermes 按 -out 推导 map 名）
        hermes_bin = find_hermes(rn_app)
        hermesc = hermes_bin / "hermesc"
        hbc_dir = work / "hbc"
        hbc_dir.mkdir(parents=True, exist_ok=True)
        hbc_out = hbc_dir / "main.jsbundle"
        run([
            str(hermesc), "-emit-binary", "-max-diagnostic-width=80",
            "-O", "-output-source-map", "-out", str(hbc_out), str(bundle),
        ])
        hbc_map = hbc_dir / "main.jsbundle.map"
        if not hbc_map.exists():
            raise StageError("hermesc 未产出 hbc map")
        record("2. hermesc → HBC", f"hbc={hbc_out.stat().st_size}B hbc.map={hbc_map.stat().st_size}B")

        # 3) 两步合成
        composed = work / "composed.map"
        run([
            sys.executable, str(SCRIPTS / "rn_symbolicate.py"), "compose",
            "--outer", str(hbc_map), "--inner", str(metro_map), "--out", str(composed),
        ])
        sm = rn_symbolicate.SourceMap.from_file(str(composed))
        record("3. compose 两张 map", f"映射 {sm.mapping_count} 条 / 源文件 {len(sm.sources)} 个")

        # 4) 真实 Hermes 堆栈：用 hermes CLI 执行 HBC，拿它真实抛出的栈
        hermes = hermes_bin / "hermes"
        app_hbc = hbc_dir / "app.hbc"  # 必须 .hbc 后缀，否则 hermes 会当源码解析
        shutil.copyfile(hbc_out, app_hbc)
        r = subprocess.run(
            [str(hermes), str(app_hbc)], capture_output=True, text=True, timeout=300
        )
        real_stack = (r.stdout or "") + (r.stderr or "")
        (work / "real-stack.txt").write_text(real_stack, encoding="utf-8")
        stack = rn_symbolicate.parse_stack(real_stack)
        offsets = [f.hermes_offset for f in stack.frames if f.hermes_offset]
        if not offsets:
            raise StageError(
                "真实堆栈里没有解析出任何字节码偏移 —— 解析器又退化了吗？\n"
                + real_stack[:600]
            )
        record("4. 真实 Hermes 堆栈", f"{len(stack.frames)} 帧，其中 {len(offsets)} 帧带字节码偏移")

        # 5) 符号化（核心断言：必须全部还原）
        sym = rn_symbolicate.Symbolicator(sm)
        js_frames = [f for f in stack.frames if not f.is_native]
        results = [sym.symbolicate_frame(f) for f in js_frames]
        total = len(results)
        resolved = [x for x in results if x.position is not None]
        rate = len(resolved) / total if total else 0.0
        record("5. 符号化", f"{len(resolved)}/{total} 帧还原（{rate:.0%}）")
        if rate < 0.95:
            sample = [x.original.raw.strip() for x in results if x.position is None][:3]
            raise StageError(
                f"还原率仅 {rate:.0%}，低于 95% 门槛。\n未还原样例：\n  " + "\n  ".join(sample)
            )
        for x in resolved[:3]:
            print(f"      {x.original.function or '<anon>'} → {x.position}")

        summary = {
            "status": "ok",
            "rnApp": str(rn_app),
            "frames": total,
            "resolved": len(resolved),
            "rate": round(rate, 4),
            "steps": steps,
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\n✅ 符号化流水线端到端自检通过（真实 RN 工具链 + 真实 Hermes 堆栈）")
        return 0

    except StageError as exc:
        print(f"\n⛔ {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"（产物保留在 {work}）")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
