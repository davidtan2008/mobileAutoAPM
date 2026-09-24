#!/usr/bin/env python3
"""AI-Readiness 扫描：一个项目对 AI Coding Agent 有多友好？

## 为什么需要它
「把人类维护的项目改造成 AI-Coding 友好的项目」是本平台的第一根支柱。
但「AI 友好」如果只是个口号就无法落地 —— 必须**可判定、可打分、可改进**。

本工具把「AI 友好」拆成 5 个维度、20+ 条可检查项，每条都给出：
  · 判定依据（在仓库里实际查什么）
  · 不达标的后果（agent 会因此付出什么代价）
  · 修复建议

## 评分不是目的，定位瓶颈才是
输出的重点是 `findings`：按影响排序的待改项。分数只是让人一眼看到差距。

用法:
  python3 ai_readiness.py --path .              # 人读
  python3 ai_readiness.py --path . --json       # 机器可读（供 Agent 消费）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 扫描时跳过的目录
SKIP_DIRS = {
    ".git", "node_modules", "Pods", ".build", "build", "DerivedData",
    "dist", ".next", "venv", ".venv", "__pycache__", ".gradle", "oh_modules",
    ".hvigor", "vendor", ".idea", ".vscode", "target", ".dart_tool",
}

SEVERITY_WEIGHT = {"blocker": 25, "high": 12, "medium": 6, "low": 2}


@dataclass
class Finding:
    key: str
    title: str
    severity: str          # blocker | high | medium | low
    dimension: str
    evidence: str          # 实际查到什么
    impact: str            # 不修的后果
    fix: str               # 怎么修


@dataclass
class Report:
    path: str
    project_kinds: list[str] = field(default_factory=list)
    #: 仅在**根目录**具备的类型。应用级检查（签名、测试 target…）只对它们生效 ——
    #: 一个"包含 iOS SDK 的平台仓库"本身不是 iOS 应用，套用应用级检查会误报。
    root_kinds: list[str] = field(default_factory=list)
    score: int = 100
    findings: list[Finding] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def walk(root: Path, max_files: int = 20000):
    """遍历源文件，跳过依赖与构建产物。"""
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            yield Path(dirpath) / f
            n += 1
            if n > max_files:
                return


def read_text(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def has_file(root: Path, *names: str) -> Path | None:
    for n in names:
        p = root / n
        if p.is_file():
            return p
    return None


def find_any(root: Path, patterns: list[str], limit: int = 3) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            p = Path(dirpath) / f
            if any(re.fullmatch(pat, f) for pat in patterns):
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


# ---------------------------------------------------------------------------
# 项目类型识别
# ---------------------------------------------------------------------------

def _kinds_of(d: Path) -> set[str]:
    """识别单个目录的项目类型。"""
    k: set[str] = set()
    if list(d.glob("*.xcodeproj")) or list(d.glob("*.xcworkspace")) or (d / "Package.swift").exists():
        k.add("ios")
    pkg = d / "package.json"
    if pkg.exists():
        body = read_text(pkg)
        k.add("react-native" if ("react-native" in body or "expo" in body) else "node")
    if (d / "build-profile.json5").exists() or (d / "oh-package.json5").exists():
        k.add("harmonyos")
    if (d / "android" / "build.gradle").exists() or (d / "build.gradle").exists() or (d / "app" / "build.gradle").exists():
        k.add("android")
    return k


def detect_kinds(root: Path) -> tuple[list[str], list[str]]:
    """识别项目类型，返回 (全部类型, 仅根目录具备的类型)。

    除了根目录，还会看**一层子目录** —— monorepo / 多模块仓库
    （如「平台仓库 + 若干 SDK」的结构）在根目录往往没有任何语言标志文件，
    只看根目录会得出"未识别"这种无用结论。

    但两者必须区分：**「包含一个 iOS SDK 的仓库」不是「一个 iOS 应用」**。
    应用级的检查（签名配置、测试 target、RN 测试框架）只应对根类型生效，
    否则会产生大量误报，让分数失去指导意义。
    """
    root_kinds = _kinds_of(root)
    all_kinds = set(root_kinds)
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in SKIP_DIRS:
            all_kinds |= _kinds_of(d)
    return sorted(all_kinds), sorted(root_kinds)


# ---------------------------------------------------------------------------
# 各维度检查
# ---------------------------------------------------------------------------

def check_docs(root: Path, r: Report) -> None:
    D = "文档层"

    # README
    readme = has_file(root, "README.md", "README.rst", "readme.md", "README")
    if not readme:
        r.findings.append(Finding(
            "no-readme", "缺少 README", "blocker", D, "仓库根目录没有 README",
            "Agent 无法在入口处了解项目是什么、怎么跑",
            "写 README：一句话定位 + 安装 + 快速开始 + 目录说明"))
    else:
        body = read_text(readme)
        r.passed.append("有 README")
        # 价值主张：开头是否有实质描述（而非只有标题）
        head = body[:1200]
        if len(head.strip()) < 200:
            r.findings.append(Finding(
                "thin-readme", "README 开头过薄", "medium", D,
                f"README 前 1200 字符仅 {len(head.strip())} 字符",
                "Agent 与人都无法快速判断项目价值",
                "开头三屏内写清：一句话价值主张、目标用户、核心能力、安装命令"))
        # 构建/测试命令
        # 注意 `python3?` —— 只写 `python\b` 匹配不到 `python3`（`n` 与 `3` 之间无词边界）
        if not re.search(
            r"```[a-z]*\n[^`]*\b(npm|yarn|pnpm|bun|node|swift|xcodebuild|gradle|hvigor|make|cargo|go|python3?|pytest|pip|uv)\b",
            body, re.I):
            r.findings.append(Finding(
                "no-cmd-in-readme", "README 未给出可复制的构建/测试命令", "high", D,
                "README 里没有代码块形式的构建或测试命令",
                "Agent 会自己去猜构建方式，猜错就浪费一整轮",
                "在 README 里贴出**一条**能跑通的构建命令与测试命令"))
        else:
            r.passed.append("README 含可复制命令")

    # AGENTS.md —— 跨 agent 的事实标准
    agents = has_file(root, "AGENTS.md", "agents.md")
    claude = has_file(root, "CLAUDE.md", "claude.md")
    if not agents and not claude:
        r.findings.append(Finding(
            "no-agent-doc", "缺少 AGENTS.md / CLAUDE.md", "high", D,
            "既无 AGENTS.md 也无 CLAUDE.md",
            "每次新会话都要重新摸索项目约定，且不同 agent 的理解可能不一致",
            "建 AGENTS.md 写清：构建/测试命令、目录约定、坑与禁区、验收方式"))
    else:
        r.passed.append("有 agent 指令文件")
        if agents and not claude:
            r.findings.append(Finding(
                "no-claude-md", "只有 AGENTS.md，没有 CLAUDE.md", "low", D,
                "存在 AGENTS.md 但无 CLAUDE.md",
                "部分 Claude Code 版本（<2.1.277）不会读 AGENTS.md",
                "加一个内容为 `@AGENTS.md` 的 CLAUDE.md 兜底（一行即可）"))
        elif claude and not agents:
            r.findings.append(Finding(
                "no-agents-md", "只有 CLAUDE.md，没有 AGENTS.md", "medium", D,
                "存在 CLAUDE.md 但无 AGENTS.md",
                "换用 opencode / Codex / Cursor 时读不到项目约定",
                "把内容迁到 AGENTS.md（跨 agent 标准），CLAUDE.md 只留 `@AGENTS.md`"))

    # 架构文档。注意用 `.*arch` 而非 `arch.*` —— 实际项目常带编号前缀
    # （如 `13-architecture-analysis.md`），只匹配开头会漏掉。
    arch = find_any(root, [r"(?i).*arch.*\.md", r"(?i).*design.*\.md",
                           r"(?i).*架构.*\.md", r"(?i).*overview.*\.md"])
    if not arch:
        r.findings.append(Finding(
            "no-arch-doc", "缺少架构文档", "medium", D,
            "未找到 architecture/design 类文档",
            "Agent 改动大模块时容易破坏既有边界",
            "写 ARCHITECTURE.md：模块划分、依赖方向、关键约定"))


def check_verifiability(root: Path, r: Report) -> None:
    D = "可验证性"

    tests = find_any(root, [r".*Tests?\.swift", r".*\.test\.[jt]sx?", r"test_.*\.py", r".*_test\.py",
                            r".*Test\.java", r".*_test\.go"], limit=5)
    if not tests:
        r.findings.append(Finding(
            "no-tests", "未发现任何测试文件", "blocker", D,
            "遍历源码未匹配到测试文件",
            "**Agent 无法验证自己的改动** —— 这是最致命的，它只能靠猜",
            "至少给核心逻辑加测试；移动端可先补不依赖设备的单元测试"))
    else:
        r.passed.append(f"有测试文件（样本 {len(tests)} 个）")

    # 测试命令是否可发现
    pkg = has_file(root, "package.json")
    has_script = False
    if pkg:
        body = read_text(pkg)
        has_script = bool(re.search(r'"test"\s*:', body))
    if pkg and not has_script:
        r.findings.append(Finding(
            "no-test-script", "package.json 没有 test 脚本", "medium", D,
            "存在 package.json 但无 \"test\" script",
            "Agent 需要猜测试命令",
            "加 `\"test\": \"...\"`，让 `npm test` 一条命令可跑"))
    elif has_script:
        r.passed.append("npm test 可跑")

    # CI
    ci = (root / ".github" / "workflows").exists() or (root / ".gitlab-ci.yml").exists() \
         or (root / ".circleci").exists() or (root / "Jenkinsfile").exists()
    if not ci:
        r.findings.append(Finding(
            "no-ci", "未发现 CI 配置", "medium", D,
            "无 .github/workflows / .gitlab-ci.yml / Jenkinsfile",
            "改动没有自动回归，劣化会静默累积",
            "加最小 CI：跑构建 + 测试（移动端可先只跑单元测试）"))
    else:
        r.passed.append("有 CI 配置")

    # lint / format
    lint = (has_file(root, ".swiftlint.yml", ".eslintrc", ".eslintrc.js", ".eslintrc.json",
                     ".prettierrc", "ruff.toml", ".flake8", "biome.json")
            or (pkg and re.search(r'"lint"\s*:', read_text(pkg))))
    if not lint:
        r.findings.append(Finding(
            "no-lint", "未发现 lint / format 配置", "low", D,
            "无常见 lint 配置且 package.json 无 lint script",
            "Agent 生成的代码风格不一致，review 成本上升",
            "加 lint/format（SwiftLint / ESLint+Prettier / ruff），并在 CI 里跑"))


def check_build(root: Path, r: Report, kinds: list[str]) -> None:
    D = "一键构建"

    # 依赖声明
    if "ios" in kinds:
        has_deps = (has_file(root, "Podfile", "Package.swift")
                    or list(root.glob("*.xcodeproj/project.xcworkspace/xcshareddata/swiftpm")))
        if not has_deps:
            r.findings.append(Finding(
                "ios-deps-unclear", "iOS 依赖声明不明确", "medium", D,
                "未找到 Podfile / Package.swift",
                "Agent 无法确定依赖如何解析",
                "明确依赖管理方式并写进 AGENTS.md"))
        else:
            r.passed.append("iOS 依赖声明存在")

    # scripts 目录
    scripts = (root / "scripts").is_dir() or (root / "tools").is_dir() or (root / "bin").is_dir()
    if not scripts:
        r.findings.append(Finding(
            "no-scripts-dir", "没有 scripts/ 或 tools/ 目录", "low", D,
            "根目录下无 scripts/tools/bin",
            "重复性的构建、检查动作没有沉淀，Agent 每次重写",
            "把重复动作沉淀成脚本 —— 确定性任务交给脚本，比让模型每次生成更可靠"))


def check_structure(root: Path, r: Report) -> None:
    D = "可导航性"

    files = list(walk(root))
    r.stats["fileCount"] = len(files)

    # 超大文件：agent 改起来容易出错
    big: list[tuple[str, int]] = []
    for p in files:
        if p.suffix.lower() in {".swift", ".ts", ".tsx", ".js", ".jsx", ".m", ".mm", ".java", ".kt", ".ets", ".py"}:
            try:
                # 用 with 确保句柄关闭 —— 否则扫描大仓库时会耗尽文件描述符
                with p.open("r", errors="ignore") as fh:
                    lines = sum(1 for _ in fh)
            except Exception:
                continue
            if lines > 1200:
                big.append((str(p.relative_to(root)), lines))
    big.sort(key=lambda x: -x[1])
    r.stats["largeFiles"] = big[:5]
    if big:
        r.findings.append(Finding(
            "large-files", f"{len(big)} 个源文件超过 1200 行", "medium", D,
            "最大的几个：" + ", ".join(f"{name}({lines}行)" for name, lines in big[:3]),
            "超大文件让 Agent 难以精确定位，改动容易误伤",
            "拆分；通常按职责边界拆而非按行数硬切"))

    # 目录深度
    max_depth = 0
    for p in files:
        try:
            max_depth = max(max_depth, len(p.relative_to(root).parts))
        except ValueError:
            continue
    r.stats["maxDepth"] = max_depth
    if max_depth > 8:
        r.findings.append(Finding(
            "deep-tree", f"目录层级过深（{max_depth} 层）", "low", D,
            f"最深路径 {max_depth} 层",
            "Agent 搜索与导航成本上升",
            "收敛层级；常见做法是不超过 6 层"))

    # 构建产物污染
    junk = [p for p in files if p.name in {"node_modules"} or p.suffix in {".o", ".a", ".dylib", ".so"}]
    if junk:
        r.findings.append(Finding(
            "build-artifacts", "构建产物混在源码树里", "medium", D,
            f"扫到 {len(junk)} 个疑似产物文件",
            "Agent 遍历时被噪声淹没，token 浪费且可能误改",
            "加 .gitignore 排除构建产物"))


def check_mobile(root: Path, r: Report, kinds: list[str]) -> None:
    D = "移动端特化"

    if "ios" in kinds:
        # 签名
        pbx = list(root.glob("*.xcodeproj/project.pbxproj"))
        if pbx:
            body = read_text(pbx[0])
            if "DEVELOPMENT_TEAM" not in body:
                r.findings.append(Finding(
                    "ios-no-team", "Xcode 工程未配置 Development Team", "medium", D,
                    "project.pbxproj 里没有 DEVELOPMENT_TEAM",
                    "Agent 无法真机构建与安装，只能停在模拟器",
                    "配好签名（自动签名 + Team ID），或写清签名由谁负责"))
            else:
                r.passed.append("iOS 签名已配置")
            if "ORDER_FILE" in body:
                r.passed.append("已启用二进制重排（ORDER_FILE）")

        # 测试 target
        if not (root / "Package.swift").exists():
            has_test_target = any("Tests" in d.name for d in root.iterdir() if d.is_dir())
            if not has_test_target:
                r.findings.append(Finding(
                    "ios-no-test-target", "iOS 工程没有测试 target", "high", D,
                    "未发现 *Tests 目录",
                    "Agent 改完无法自动验证，只能靠人跑",
                    "加单元测试 target；优先覆盖不依赖设备与 UI 的逻辑"))

    if "react-native" in kinds:
        pkg_json = root / "package.json"
        body = read_text(pkg_json)
        # ⚠️ 必须同时看 devDependencies —— typescript / jest 这类工具链依赖
        # 按惯例装在 devDependencies 里，只查 dependencies 会大面积误报。
        try:
            pkg = json.loads(body) if body else {}
        except json.JSONDecodeError:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        if "typescript" not in deps and "typescript" not in body:
            r.findings.append(Finding(
                "rn-no-ts", "RN 项目未使用 TypeScript", "medium", D,
                "package.json 的 dependencies 与 devDependencies 均无 typescript",
                "缺少类型信息，Agent 改动缺少静态约束，出错率上升",
                "迁到 TypeScript —— 对 Agent 与人都能显著降低改错概率"))
        else:
            r.passed.append("RN 使用 TypeScript")

        # 不只看 jest/vitest —— Node 内建的 `node --test` 也是正经测试框架，
        # 漏认会把「已经有 74 个测试的项目」误报成没有测试。
        has_fw = any(k in body for k in ("jest", "vitest", "mocha", "ava", "tap", "node --test", "node:test"))
        if not has_fw:
            r.findings.append(Finding(
                "rn-no-test-fw", "RN 项目无测试框架", "high", D,
                "package.json 中未发现 jest / vitest / mocha / node --test",
                "纯 JS 逻辑也无法验证",
                "加测试框架覆盖 store / 工具函数等纯逻辑"))
        else:
            r.passed.append("RN 有测试框架")

    if "harmonyos" in kinds:
        r.passed.append("鸿蒙工程（build-profile.json5 / oh-package.json5）")

    # 依赖可复现性。
    #
    # 只在**确实会产出锁文件的生态**里检查：
    #   · npm/yarn/pnpm → package-lock / yarn.lock / pnpm-lock.yaml
    #   · CocoaPods     → Podfile.lock
    # **Swift Package 库不该被要求提交 Package.resolved** —— 库通常把它 gitignore，
    # 且对库而言锁文件反而是反模式。对库报这条是误报。
    scopes = [root] + [
        d for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith(".") and d.name not in SKIP_DIRS
    ]
    manifests: list[tuple[Path, str]] = []
    for d in scopes:
        if (d / "package.json").is_file():
            manifests.append((d, "node"))
        if (d / "Podfile").is_file():
            manifests.append((d, "cocoapods"))

    if manifests:
        missing = [
            d for d, kind in manifests
            if not has_file(d, "package-lock.json", "yarn.lock", "pnpm-lock.yaml")
            and not (kind == "cocoapods" and has_file(d, "Podfile.lock"))
        ]
        if missing:
            r.findings.append(Finding(
                "no-lockfile", "缺少依赖锁文件", "medium", D,
                "有 package.json / Podfile 但未见锁文件：" +
                ", ".join(str(d.relative_to(root)) or "." for d in missing[:3]),
                "Agent 装出来的依赖版本可能与你不同，构建结果不可复现",
                "提交 package-lock.json / yarn.lock / Podfile.lock"))
        else:
            r.passed.append("依赖锁文件齐全")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

DIMENSIONS = ["文档层", "可验证性", "一键构建", "可导航性", "移动端特化"]


def scan(root: Path) -> Report:
    r = Report(path=str(root))
    r.project_kinds, r.root_kinds = detect_kinds(root)
    check_docs(root, r)
    check_verifiability(root, r)
    # 只对**根目录就是**该类型的项目做应用级检查。
    # 不做 fallback —— 否则「含 iOS SDK 的平台仓库」也会被要求声明 iOS 依赖，属误报。
    check_build(root, r, r.root_kinds)
    check_structure(root, r)
    # 应用级检查只对**根目录就是**该类型的项目生效，避免"含 SDK 的平台仓库"被误报
    check_mobile(root, r, r.root_kinds)

    r.findings.sort(key=lambda f: -SEVERITY_WEIGHT.get(f.severity, 0))
    penalty = sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in r.findings)
    r.score = max(0, 100 - penalty)
    return r


SEV_LABEL = {"blocker": "🔴 阻断", "high": "🟠 高", "medium": "🟡 中", "low": "🟢 低"}


def _gate(r: Report, args) -> int:
    """门禁判定。退出码 2 表示未通过门禁，可直接用作 CI 条件。

    阻断项始终导致失败 —— 它意味着 **agent 根本无法验证自己的改动**，
    此时其余优化意义有限（这是 AI 友好度里权重最高的一条）。
    """
    blockers = [f for f in r.findings if f.severity == "blocker"]
    if blockers and not args.no_blocker_fail:
        print(f"\n⛔ {len(blockers)} 个阻断项：{', '.join(f.title for f in blockers)}", file=sys.stderr)
        return 2
    if args.min_score is not None and r.score < args.min_score:
        print(f"\n⛔ AI 友好度 {r.score} 低于门禁阈值 {args.min_score}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-Readiness 扫描")
    ap.add_argument("--path", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=0, help="只显示前 N 条")
    ap.add_argument("--min-score", type=int, default=None,
                    help="门禁：低于该分数则以退出码 2 结束；有阻断项时始终以 2 结束")
    ap.add_argument("--no-blocker-fail", action="store_true",
                    help="有阻断项也不失败（默认阻断项即失败）")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"⛔ 目录不存在：{root}", file=sys.stderr)
        return 1

    r = scan(root)

    if args.json:
        print(json.dumps({
            "path": r.path,
            "projectKinds": r.project_kinds,
            "score": r.score,
            "stats": r.stats,
            "passed": r.passed,
            "findings": [asdict(f) for f in r.findings],
        }, ensure_ascii=False, indent=2))
        return _gate(r, args)

    print("=" * 74)
    print(f"  AI-Readiness 扫描  |  {r.path}")
    print("=" * 74)
    print(f"\n项目类型: {', '.join(r.project_kinds) or '未识别'}")
    print(f"文件数:   {r.stats.get('fileCount', 0)}")
    print("\n  ┌──────────────────────────────────────┐")
    print(f"  │  AI 友好度   {r.score:>3} / 100                  │")
    print("  └──────────────────────────────────────┘")

    if r.passed:
        print(f"\n✅ 已达标（{len(r.passed)} 项）")
        for p in r.passed:
            print(f"   · {p}")

    shown = r.findings[:args.top] if args.top else r.findings
    print(f"\n⚠️  待改进（{len(r.findings)} 项，按影响排序）")
    if not r.findings:
        print("   （无）")
    for f in shown:
        print(f"\n   {SEV_LABEL[f.severity]}  [{f.dimension}] {f.title}")
        print(f"      依据: {f.evidence}")
        print(f"      后果: {f.impact}")
        print(f"      修法: {f.fix}")

    if args.top and len(r.findings) > args.top:
        print(f"\n   … 还有 {len(r.findings) - args.top} 项，用 --json 取全量")

    return _gate(r, args)


if __name__ == "__main__":
    sys.exit(main())
