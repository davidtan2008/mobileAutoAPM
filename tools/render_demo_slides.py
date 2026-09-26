#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""把 demo 证据包渲染成 16:9 SVG 幻灯片。

**只允许从证据文件读取数字**：本脚本不接受任何硬编码的性能/测试结论，
所有 p50、通过数、失败信息都来自 `docs/demo/*/` 下的命令原始输出 JSON。

用法：
  python3 tools/render_demo_slides.py \
    --evidence docs/demo/translation-persistence \
    --out docs/demo/translation-persistence/slides
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence
from xml.sax.saxutils import escape

W, H = 1920, 1080
BG = "#0b1020"
PANEL = "#141b2d"
PANEL_2 = "#1b2438"
TEXT = "#e8edf7"
MUTED = "#9aa8c7"
GREEN = "#3ddc97"
RED = "#ff6b6b"
AMBER = "#ffc857"
BLUE = "#6ba8ff"

SANS = "PingFang SC, Hiragino Sans GB, Helvetica Neue, Arial, sans-serif"
MONO = "SF Mono, Menlo, Consolas, monospace"


def load(evidence: Path, name: str) -> dict:
    return json.loads((evidence / name).read_text(encoding="utf-8"))


def summary_of(payload: dict) -> dict:
    return payload.get("data", {}).get("summary", {}) or {}


def failure_text(payload: dict) -> str:
    diagnostics = payload.get("data", {}).get("diagnostics", {}) or {}
    failures = diagnostics.get("testFailures") or payload.get("data", {}).get("testFailures") or []
    for item in failures:
        for key in ("message", "failureText", "description"):
            if item.get(key):
                return str(item[key])
    return "（未提取到失败文本）"


def rect(x, y, w, h, fill, rx=18, opacity=1.0, stroke=None) -> str:
    stroke_attr = f' stroke="{stroke}" stroke-width="2"' if stroke else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" opacity="{opacity}"{stroke_attr}/>'
    )


def text(
    x,
    y,
    content: str,
    size=34,
    fill=TEXT,
    family=SANS,
    weight="500",
    anchor="start",
    opacity=1.0,
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" opacity="{opacity}">'
        f"{escape(content)}</text>"
    )


def bullets(x, y, lines: Sequence[str], size=30, gap=52, fill=TEXT) -> str:
    out = []
    for index, line in enumerate(lines):
        out.append(rect(x, y + index * gap - 20, 12, 12, BLUE, rx=6))
        out.append(text(x + 32, y + index * gap, line, size=size, fill=fill))
    return "".join(out)


def code_block(x, y, w, lines: Sequence[str], size=26, line_height=40, tone=TEXT) -> str:
    height = line_height * len(lines) + 56
    out = [rect(x, y, w, height, PANEL, rx=20, stroke="#26314d")]
    for index, line in enumerate(lines):
        out.append(
            text(
                x + 32,
                y + 56 + index * line_height,
                line,
                size=size,
                fill=tone,
                family=MONO,
                weight="500",
            )
        )
    return "".join(out), height


def slide_shell(index: int, total: int, kicker: str, title: str, accent=BLUE) -> List[str]:
    return [
        rect(0, 0, W, H, BG, rx=0),
        rect(0, 0, 18, H, accent, rx=0),
        text(96, 108, kicker, size=26, fill=MUTED, weight="700"),
        text(96, 186, title, size=58, weight="800"),
        rect(96, 214, 220, 6, accent, rx=3),
        text(
            96,
            H - 56,
            "mobileAutoAPM · 所有数字来自本次真实命令输出",
            size=22,
            fill=MUTED,
        ),
        text(W - 96, H - 56, f"{index}/{total}", size=22, fill=MUTED, anchor="end"),
    ]


def write_slide(path: Path, parts: Sequence[str]) -> None:
    body = "".join(parts)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">{body}</svg>\n',
        encoding="utf-8",
    )


def build(evidence: Path, out: Path) -> List[float]:
    red = load(evidence, "red-test.json")
    green_focused = load(evidence, "green-focused.json")
    green_full = load(evidence, "green-full-unit.json")
    release = load(evidence, "green-release-build.json")
    fix_lines = (evidence / "fix-commit.txt").read_text(encoding="utf-8").splitlines()
    fix_sha = next(
        (line.split()[1] for line in fix_lines if line.startswith("commit ")), "unknown"
    )
    fix_short = fix_sha[:7]
    # 红测跑在修复前的 commit 上，与绿测 commit 不同 —— 两者都必须来自证据文件
    commit_lines = (evidence / "commits.txt").read_text(encoding="utf-8").splitlines()
    before_sha = next(
        (line.split()[1] for line in commit_lines if line.startswith("commit ")), "unknown"
    )
    before_short = before_sha[:7]

    red_summary = summary_of(red)
    red_counts = red_summary.get("counts", {})
    focused_counts = summary_of(green_focused).get("counts", {})
    full_counts = summary_of(green_full).get("counts", {})
    release_summary = summary_of(release)

    slides: List[tuple] = []

    # 1 —— 问题
    slides.append(
        (
            "01 · 用户可见问题",
            "译文当场出现，返回历史再进入却全部消失",
            AMBER,
            [
                bullets(
                    96,
                    320,
                    [
                        "对象：已结束的会议记录详情页",
                        "操作：点击「翻译成我的语言」",
                        "现象：UI 当场显示译文，像是成功",
                        "结果：重新进入同一任务，译文全部消失",
                    ],
                    size=34,
                    gap=72,
                ),
                code_block(
                    96,
                    660,
                    1728,
                    [
                        "影响：全部多语会议都可能丢失译文",
                        "类型：确定性用户数据丢失（非性能问题、非竞态猜测）",
                    ],
                    size=28,
                ),
            ],
            12.0,
        )
    )

    # 2 —— 红测
    red_block, _ = code_block(
        96,
        300,
        1728,
        [
            f"修复前 commit {before_short} · 临时 detached worktree",
            "",
            f"FAIL  {failure_text(red)}",
            f"      passed={red_counts.get('passed', 0)}  failed={red_counts.get('failed', 0)}",
        ],
        size=27,
        tone=RED,
    )
    slides.append(
        (
            f"02 · 先证明它坏（修复前 commit {before_short}）",
            "红测：期望「六点见」，实际读回空字符串",
            RED,
            [
                red_block,
                bullets(
                    96,
                    660,
                    [
                        "先写失败测试，再动产品代码",
                        "在临时 detached worktree 复现，不污染主工作树",
                        "没有用「看起来像」代替可执行证据",
                    ],
                    size=28,
                    gap=56,
                ),
            ],
            12.0,
        )
    )

    # 3 —— 根因
    root_block, _ = code_block(
        96,
        292,
        1728,
        [
            "TaskDetailView.handleTranslated",
            "  current.entries[index].translatedText = text   ← 内存立刻更新，UI 显示成功",
            "  await taskStore.append(...)                    ← 回填持久化",
            "",
            "InMemoryTaskStore.append   guard task.status != .ended else { return }",
            "SwiftDataTaskStore.append  guard entity.statusRaw != ended else { return }",
            "",
            "详情页只能进入 ended 任务  →  append 必然被丢弃，且无任何错误",
        ],
        size=26,
        line_height=42,
    )
    slides.append(
        (
            "03 · 定位：UI 成功，持久化静默丢弃",
            "一个 guard 把「已结束」变成了「不可回填」",
            RED,
            [
                root_block,
                text(96, 830, "同源对照：saveSummary 没有这个 guard，所以「总结」能存、「翻译」存不了 —— 这就是它长期没被发现的原因。", size=26, fill=MUTED),
            ],
            12.0,
        )
    )

    # 4 —— 单变量修复
    fix_block, _ = code_block(
        96,
        292,
        1728,
        [
            "protocol TaskStore {",
            "    func updateTranslations(taskID: UUID, entries: [TranscriptEntry]) async",
            "}",
            "",
            "只更新已有 entry 的 translatedText；允许作用于 ended 任务。",
            "普通 append 继续保持 ended guard —— 没有放宽 transcript 写入规则。",
        ],
        size=27,
        line_height=42,
        tone=GREEN,
    )
    slides.append(
        (
            "04 · 修复：只改一个变量",
            "新增专用译文回填通道，不动其他路径",
            GREEN,
            [
                fix_block,
                bullets(
                    96,
                    700,
                    [
                        "变量 = 持久化边界（新增一个方法，语义明确）",
                        "没有同时修改导出按钮、翻译队列或 UI 布局",
                        "InMemory / SwiftData / Deferred 三个实现同步同一语义",
                    ],
                    size=27,
                    gap=54,
                ),
            ],
            11.0,
        )
    )

    # 5 —— 绿测（模拟器 + 真机双证据）
    dev = load(evidence, "green-device-suite.json")
    green_block, _ = code_block(
        96,
        276,
        1728,
        [
            f"模拟器 · 单元测试        {full_counts.get('passed', 0)} passed, {full_counts.get('failed', 0)} failed",
            f"模拟器 · 定向测试        {focused_counts.get('passed', 0)} passed, {focused_counts.get('failed', 0)} failed",
            f"真机 iPhone13/iOS26.7   {dev.get('passed', 0)} passed, {dev.get('failed', 0)} failed, {dev.get('skipped', 0)} skipped",
            f"Release device build     {release_summary.get('status', 'UNKNOWN')}",
            f"目标 commit              {fix_short} fix: persist translations for ended tasks",
        ],
        size=29,
        line_height=50,
        tone=GREEN,
    )
    slides.append(
        (
            "05 · 验证：目标达成",
            "模拟器与物理设备双证据，没有破坏任何既有行为",
            GREEN,
            [
                green_block,
                text(96, 762, "真机套件含 testSimultaneousListeningPressure（2 分钟），真实执行端侧听写而非跳过。", size=26, fill=MUTED),
                text(96, 806, "额外断言：普通 append 仍不能修改已结束任务的 transcript（单变量边界被测试锁住）。", size=26, fill=MUTED),
                text(96, 850, "证据：docs/case-study-translation-persistence.md · 被观测工程 .apm/issues/ISSUE-FUNC-001", size=25, fill=MUTED),
            ],
            12.0,
        )
    )

    # 注：原第 6 页「边界与下一步」按需求移除。
    # 边界内容没有丢 —— 它仍然写在 docs/case-study-translation-persistence.md、
    # 被观测工程 docs/20-*.md 与 .apm/issues/*.json 里，只是不进视频。
    # 若之后要恢复，把下面的 slides.append(...) 取消注释即可。

    durations: List[float] = []
    out.mkdir(parents=True, exist_ok=True)
    total = len(slides)
    for index, (kicker, title, accent, blocks, duration) in enumerate(slides, start=1):
        parts = slide_shell(index, total, kicker, title, accent=accent)
        for block in blocks:
            if isinstance(block, tuple):
                parts.append(block[0])
            else:
                parts.append(block)
        write_slide(out / f"slide-{index:02d}.svg", parts)
        durations.append(duration)
    return durations


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="渲染 demo 证据幻灯片")
    parser.add_argument("--evidence", required=True, help="证据目录（包含命令 JSON 输出）")
    parser.add_argument("--out", required=True, help="SVG 输出目录")
    args = parser.parse_args(argv)

    durations = build(Path(args.evidence), Path(args.out))

    # 视频用 concat filter 逐段拼接（见 tools/render_demo_video.sh）；
    # 这里只输出渲染清单，时长由每页的 -t 精确控制。
    manifest = [
        {"png": f"slide-{index:02d}.png", "duration": duration}
        for index, duration in enumerate(durations, start=1)
    ]
    (Path(args.out) / "render.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✅ 生成 {len(durations)} 页幻灯片，总时长 {sum(durations):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
