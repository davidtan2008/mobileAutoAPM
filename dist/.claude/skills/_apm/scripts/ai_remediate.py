#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""支柱 A：把「AI 友好度扫描」变成「扫描 → 改造 → 复扫」的闭环。

## 为什么需要它

`ai_readiness.py` 只回答「哪里不友好」。但本项目的另一条铁律是
**无基线不优化** —— 只给建议、不做改造，等于让人自己当执行器，
效果无法量化、也无法验证。

本脚本补上后半段，并且**刻意保留人审环节**。

## 三条不可让步的约束

1. **绝不编造事实。** 生成物里凡是「只有人知道」的位置，一律写
   `TODO(需人工填写)`，**不填任何看起来合理的内容**。
   一个编得很像样的 README 比没有 README 更糟 —— agent 会当真。
2. **默认不落盘。** 生成物一律写到 staging 目录。`apply` 需要显式指定，
   且**拒绝覆盖任何已存在文件**。
3. **改造效果要量化。** `loop` 会在临时副本上应用改造并复扫，
   给出 before/after 分数与逐条消除的 finding —— 这是实测，不是声称。

## 调研依据（决定了为什么保留人审）

> LLM 生成的 context 文件：**成功率 −3%、成本 +20%**。

所以本脚本**不做 LLM 即兴生成**，只做「模板 + 事实抽取」的确定性改造。
凡是超出这个范围的，一律标记为 `needs_human`，不假装能自动解决。

## 用法

```bash
# 1. 只看计划，不产生任何文件
python3 ai_remediate.py plan --path /some/project

# 2. 生成到 staging 目录（仍不动目标工程）
python3 ai_remediate.py generate --path /some/project --out .apm/remediation

# 3. 完整闭环：扫描 → 在临时副本上应用 → 复扫 → 报告量化结果
python3 ai_remediate.py loop --path /some/project

# 4. 人工审阅通过后，才写入目标工程
python3 ai_remediate.py apply --path /some/project --staging .apm/remediation
```

退出码：
- 0 = 正常（含「无可自动改造项」）
- 1 = 参数/输入错误，或 apply 拒绝覆盖
- 2 = 闭环中出现阻断项（需人工介入）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import ai_readiness

TODO = "TODO(需人工填写)"
HERE = Path(__file__).resolve().parent


class RemediationError(Exception):
    """改造流程无法继续。"""


# ---------------------------------------------------------------------------
# 从项目里抽事实（只抽「读得到」的，不推断）
# ---------------------------------------------------------------------------


@dataclass
class Facts:
    """从目标工程里**读到**的事实。读不到的一律留空，由模板输出 TODO。"""

    kinds: List[str] = field(default_factory=list)
    root_kinds: List[str] = field(default_factory=list)
    has_makefile: bool = False
    makefile_test_targets: List[str] = field(default_factory=list)
    package_test_cmd: Optional[str] = None
    package_install_cmd: Optional[str] = None
    swift_package: bool = False
    gradle_wrapper: bool = False
    gradle_tasks_hint: List[str] = field(default_factory=list)
    harmony: bool = False
    detected_test_commands: List[str] = field(default_factory=list)
    note: str = ""


def collect_facts(root: Path) -> Facts:
    f = Facts()
    try:
        f.kinds, f.root_kinds = ai_readiness.detect_kinds(root)
    except Exception:  # 识别失败不应中断，只会导致模板更保守
        f.kinds, f.root_kinds = [], []

    makefile = ai_readiness.has_file(root, "Makefile", "makefile", "GNUmakefile")
    if makefile:
        f.has_makefile = True
        try:
            for line in ai_readiness.read_text(makefile).splitlines():
                m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
                if m and any(k in m.group(1).lower() for k in ("test", "check", "lint")):
                    f.makefile_test_targets.append(m.group(1))
        except Exception:
            pass

    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(ai_readiness.read_text(pkg))
            scripts = data.get("scripts") or {}
            if any(k in scripts for k in ("test", "jest", "vitest")):
                f.package_test_cmd = "npm test"
            lock = any((root / n).exists() for n in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"))
            f.package_install_cmd = "npm ci" if lock else "npm install"
        except Exception:
            pass

    f.swift_package = (root / "Package.swift").exists()
    f.gradle_wrapper = (root / "gradlew").exists() and os.access(root / "gradlew", os.X_OK)
    f.harmony = (root / "oh-package.json5").exists() or (root / "build-profile.json5").exists()

    # 测试命令只从「确实存在的东西」推导；推不出就是空列表
    if f.has_makefile and f.makefile_test_targets:
        first = f.makefile_test_targets[0]
        f.detected_test_commands.append(f"make {first}")
    if f.package_test_cmd:
        f.detected_test_commands.append(f.package_test_cmd)
    if f.swift_package:
        f.detected_test_commands.append("swift test")
    if f.gradle_wrapper:
        f.detected_test_commands.append("./gradlew test")
    return f


# ---------------------------------------------------------------------------
# 改造项：finding key -> 生成物
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    finding_key: str
    relpath: str
    content: str
    rationale: str
    needs_human: List[str] = field(default_factory=list)


def _readme(f: Facts, root: Path) -> Artifact:
    """README 骨架。只写「从项目里读得到」的结构，事实位置留 TODO。"""
    kinds = ", ".join(f.kinds) if f.kinds else TODO
    if f.package_test_cmd and f.package_install_cmd:
        install = f.package_install_cmd
    else:
        install = TODO
    cmd_block = "\n".join(f.detected_test_commands) if f.detected_test_commands else TODO
    if cmd_block != TODO:
        quickstart = "# 安装依赖\n" + install + "\n\n# 跑测试\n" + cmd_block
    else:
        quickstart = TODO

    lines = [
        "# " + TODO + " —— 项目名",
        "",
        "> 由 `ai_remediate.py` 生成的骨架。**所有 `TODO(需人工填写)` 必须由人补齐后才能提交。**",
        "> 原因：LLM 生成的 context 文件实测「成功率 −3%、成本 +20%」（见 ROADMAP P2），",
        "> 编造内容比留空更糟 —— agent 会把它当事实。",
        "",
        "## 这是什么",
        "",
        TODO + " —— 一句话价值主张 + 目标用户。",
        "",
        "## 技术栈",
        "",
        "类型：" + kinds,
        "",
        "## 快速开始",
        "",
        "```bash",
        quickstart,
        "```",
        "",
        "## 目录结构",
        "",
        "```",
        TODO + " —— 列出主要目录及职责",
        "```",
        "",
        "## 常见问题",
        "",
        TODO,
        "",
    ]
    return Artifact(
        "no-readme",
        "README.md",
        "\n".join(lines),
        "缺少 README；已生成骨架，事实位置留 TODO",
        needs_human=["项目定位与价值主张", "真实安装/测试命令", "目录职责"],
    )


def _agents_md(f: Facts, root: Path) -> Artifact:
    """AGENTS.md 骨架 —— agent 的入口契约。"""
    test_cmd = "\n".join(f.detected_test_commands) if f.detected_test_commands else TODO
    principle = TODO + " —— 例如：改完必须跑什么验证、哪些目录不能手改"
    layout = TODO + " | " + TODO
    layout_row = "| " + layout + " |"
    lines = [
        "# AGENTS.md —— 给 AI Coding Agent 的项目约定",
        "",
        "> 由 `ai_remediate.py` 生成。`TODO(需人工填写)` 必须人工补齐。",
        "> 本文件是 agent 判断「怎么做才算对」的**唯一真源**，写错比不写更危险。",
        "",
        "## 这个项目是什么",
        "",
        TODO,
        "",
        "## 不可违背的原则",
        "",
        principle,
        "",
        "## 怎么验证自己的改动",
        "",
        "```bash",
        test_cmd,
        "```",
        "",
        "> 这是本文件里最重要的一节。agent 必须能**一条命令验证自己没搞坏东西**。",
        "",
        "## 目录约定",
        "",
        "| 路径 | 约定 |",
        "|---|---|",
        layout_row,
        "",
        "## 坑与禁区",
        "",
        TODO + " —— 写下这个项目里「看起来该做但其实不能做」的事",
        "",
    ]
    return Artifact(
        "no-agents-md",
        "AGENTS.md",
        "\n".join(lines),
        "缺少 agent 指令文件；已生成骨架",
        needs_human=["项目定位", "不可违背的原则", "目录约定", "坑与禁区"],
    )


def _ci(f: Facts, root: Path) -> Artifact:
    """CI workflow —— 跑项目**自己**的测试命令，不发明。"""
    if not f.detected_test_commands:
        return Artifact(
            "no-ci",
            ".github/workflows/ai-check.yml",
            _ci_stub(f),
            "缺少 CI；已生成占位 workflow（未接入测试命令，需人工指定）",
            needs_human=["测试命令", "Node 版本 / JDK 等运行时版本"],
        )
    run = "\n".join(
        f"      - name: 验证（{cmd}）\n        run: {cmd}" for cmd in f.detected_test_commands
    )
    lines = [
        "# 由 ai_remediate.py 生成：AI 可验证性门禁",
        "#",
        "# 目的：让 agent 能用「一条命令」确认自己没破坏任何东西。",
        "# 缺失 CI 是 AI 友好度的**阻断项** —— agent 无法自我验证。",
        "name: ai-check",
        "",
        "on:",
        "  push:",
        "  pull_request:",
        "",
        "jobs:",
        "  verify:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
    ]
    if f.package_test_cmd:
        lines += [
            "      - uses: actions/setup-node@v4",
            "        with:",
            "          node-version: '20'",
        ]
    lines.append(run)
    content = "\n".join(lines) + "\n"
    return Artifact(
        "no-ci",
        ".github/workflows/ai-check.yml",
        content,
        "缺少 CI；已按项目实际检测到的测试命令生成 workflow",
        needs_human=[] if f.package_test_cmd is None else ["Node 版本"],
    )


def _ci_stub(f: Facts) -> str:
    return f"""# 由 ai_remediate.py 生成：AI 可验证性门禁（占位）
#
# ⚠️ 未检测到本项目的测试命令，因此**没有**写入任何 run 步骤。
# 请人工填入真实命令后再生效 —— 宁可空着，也不要写一个跑不起来的命令。
name: ai-check

on:
  push:
  pull_request:

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: TODO 填入本项目真实的验证命令
        run: echo "{TODO}"
"""


def _lint(f: Facts, root: Path) -> Artifact:
    """lint 配置 —— 按检测到的类型选，不跨类型乱配。"""
    if "react-native" in f.kinds or "node" in f.kinds:
        content = "{\n  \"root\": true\n}\n"
        return Artifact(
            "no-lint",
            ".eslintrc.json",
            content,
            "缺少 lint 配置；检测到 Node/RN 项目，生成最小 ESLint 配置",
            needs_human=[],
        )
    if f.kinds == ["ios"] or "ios" in f.kinds:
        # 不写 included —— 那需要知道真实的源码目录名，猜错比不写更糟。
        # SwiftLint 缺省扫描全项目，这里只排除确定无意义的构建产物。
        content = "\n".join(
            [
                "# 由 ai_remediate.py 生成的最小 SwiftLint 配置",
                "#",
                "# 刻意**不写** included：源码目录名无法可靠推断，猜错会让规则作用在空目录上。",
                "# 需要限定范围时请人工填入；" + TODO,
                "excluded:",
                "  - .build",
                "  - Pods",
                "  - Carthage",
                "  - DerivedData",
                "",
            ]
        )
        return Artifact(
            "no-lint",
            ".swiftlint.yml",
            content,
            "缺少 lint 配置；检测到 iOS 项目，生成最小 SwiftLint 配置（不含猜测的目录名）",
            needs_human=["是否需要限定 included 范围", "采用哪套 lint 规则"],
        )
    return Artifact(
        "no-lint",
        ".editorconfig",
        "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\n",
        "缺少 lint 配置；未识别到明确语言栈，生成与语言无关的 .editorconfig",
        needs_human=["真正的 lint 工具选型"],
    )


def _arch_doc(f: Facts, root: Path) -> Artifact:
    one_line = TODO + " —— 这个系统由哪几块组成，各自职责"
    flow = TODO + " —— 从入口到出口，画出调用/数据流向"
    pick = TODO
    picked = TODO
    cost = TODO
    lines = [
        "# ARCHITECTURE —— 整体形状",
        "",
        "> 由 `ai_remediate.py` 生成骨架。`TODO(需人工填写)` 必须人工补齐。",
        "",
        "## 一句话",
        "",
        one_line,
        "",
        "## 分层",
        "",
        "```",
        TODO,
        "```",
        "",
        "## 关键数据流",
        "",
        "```",
        flow,
        "```",
        "",
        "## 关键取舍",
        "",
        "| 取舍 | 选择 | 代价 |",
        "|---|---|---|",
        "| " + pick + " | " + picked + " | " + cost + " |",
        "",
    ]
    return Artifact(
        "no-arch-doc",
        "ARCHITECTURE.md",
        "\n".join(lines),
        "缺少架构文档；已生成骨架",
        needs_human=["分层", "数据流", "关键取舍"],
    )


#: finding key -> 生成器。只收录**能确定性产出、且不编造事实**的项。
GENERATORS: Dict[str, Callable[[Facts, Path], Artifact]] = {
    "no-readme": _readme,
    "no-agents-md": _agents_md,
    "no-agent-doc": _agents_md,
    "no-claude-md": _agents_md,
    "no-ci": _ci,
    "no-lint": _lint,
    "no-arch-doc": _arch_doc,
}

#: 明确**不能**自动改造的项 —— 只能报给人，假装能改就是造假。
NEEDS_HUMAN: Dict[str, str] = {
    "thin-readme": "README 已有但内容过薄；补内容需要真实项目知识，模板只会写出空话",
    "no-cmd-in-readme": "README 里缺可复制命令；必须填真实命令，不能编",
    "no-tests": "**不能凭空生成测试** —— 造出来的测试只会制造虚假安全感",
    "no-test-script": "缺测试脚本；需按项目实际情况编写，模板无法判断",
    "no-lockfile": "缺依赖锁文件；需执行真实的安装命令生成，不手工构造",
    "ios-no-team": "缺签名 Team ID；这是账号信息，机器上不可能知道",
    "ios-no-test-target": "缺测试 target；需在 Xcode 工程里配置，无法用文件生成",
    "ios-deps-unclear": "依赖来源不清晰；需人工判断该用 SPM 还是 CocoaPods",
    "rn-no-test-fw": "RN 测试框架未选型；需人工决策 Jest/Vitest/RNTL",
    "rn-no-ts": "RN 项目缺 TypeScript 配置；需人工确认是否要引入",
    "large-files": "仓库有大文件；需人工判断是历史遗留还是必要资产",
    "deep-tree": "目录层级过深；需人工判断是否重组（改动面大，不可自动做）",
    "build-artifacts": "疑似提交了构建产物；清理需人工确认，不能自动删",
    "no-scripts-dir": "缺脚本目录；需人工确认要放哪些脚本",
}


# ---------------------------------------------------------------------------
# 计划 / 生成
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    path: str
    score_before: int
    kinds: List[str]
    artifacts: List[Artifact]
    needs_human: List[Dict[str, str]]
    findings_before: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": 1,
            "tool": "ai_remediate",
            "path": self.path,
            "scoreBefore": self.score_before,
            "projectKinds": self.kinds,
            "autoRemediable": [
                {
                    "findingKey": a.finding_key,
                    "relpath": a.relpath,
                    "rationale": a.rationale,
                    "needsHumanReview": a.needs_human,
                }
                for a in self.artifacts
            ],
            "needsHuman": self.needs_human,
            "findingsBefore": self.findings_before,
        }


def build_plan(root: Path, report: Optional[ai_readiness.Report] = None) -> Plan:
    if report is None:
        report = ai_readiness.scan(root)
    facts = collect_facts(root)
    keys = [f.key for f in report.findings]

    artifacts: List[Artifact] = []
    seen_paths = set()
    for key in keys:
        gen = GENERATORS.get(key)
        if gen is None:
            continue
        art = gen(facts, root)
        # 同一路径只生成一次（例如 no-agents-md / no-claude-md 会指向同一文件）
        if art.relpath in seen_paths:
            continue
        seen_paths.add(art.relpath)
        artifacts.append(art)

    needs_human = [
        {"findingKey": f.key, "title": f.title, "severity": f.severity, "reason": NEEDS_HUMAN[f.key]}
        for f in report.findings
        if f.key in NEEDS_HUMAN
    ]
    return Plan(
        path=str(root),
        score_before=report.score,
        kinds=list(report.project_kinds),
        artifacts=artifacts,
        needs_human=needs_human,
        findings_before=[
            {"key": f.key, "title": f.title, "severity": f.severity, "dimension": f.dimension}
            for f in report.findings
        ],
    )


def write_staging(plan: Plan, staging: Path, root: Path) -> List[Path]:
    """写到 staging 目录。**不碰目标工程**。"""
    written: List[Path] = []
    for art in plan.artifacts:
        target = staging / art.relpath
        if target.exists():
            raise RemediationError(f"staging 里已存在 {art.relpath}，拒绝覆盖")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(art.content, encoding="utf-8")
        written.append(target)
    (staging / "REMEDIATION_PLAN.json").write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written.append(staging / "REMEDIATION_PLAN.json")
    return written


# ---------------------------------------------------------------------------
# 人审后写入
# ---------------------------------------------------------------------------


def apply_staging(plan: Plan, staging: Path, root: Path, force: bool = False) -> List[str]:
    """把 staging 里的文件写入目标工程。**拒绝覆盖已存在文件**。"""
    applied: List[str] = []
    for art in plan.artifacts:
        src = staging / art.relpath
        if not src.exists():
            raise RemediationError(f"staging 缺少 {art.relpath}")
        dst = root / art.relpath
        if dst.exists() and not force:
            raise RemediationError(
                f"{art.relpath} 已存在，拒绝覆盖。"
                "生成物是骨架，直接覆盖会丢掉真实内容 —— 请人工合并。"
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        applied.append(art.relpath)
    return applied


# ---------------------------------------------------------------------------
# 闭环：扫描 → 临时副本应用 → 复扫 → 量化
# ---------------------------------------------------------------------------


def _copy_for_scan(root: Path, dest: Path, max_bytes: int) -> Optional[str]:
    """把项目复制到临时目录（跳过构建产物），用于「应用后复扫」。

    复制真实文件而不是凭空构造，才能得到可比的 before/after。
    """
    total = 0
    ignore = ai_readiness.SKIP_DIRS | {".apm", ".git"}

    def _ignore(_dir: str, names: List[str]) -> set:
        return {n for n in names if n in ignore}

    try:
        shutil.copytree(root, dest, ignore=_ignore, symlinks=True, dirs_exist_ok=False)
    except Exception as exc:
        return f"复制失败：{exc}"

    for path in dest.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
            if total > max_bytes:
                return f"项目副本超过 {max_bytes // (1024 * 1024)}MB，放弃量化"
    return None


def run_loop(root: Path, max_copy_mb: int = 256) -> Dict[str, Any]:
    """完整闭环。**不修改目标工程** —— 改造只发生在临时副本上。"""
    before = ai_readiness.scan(root)
    plan = build_plan(root, before)

    result: Dict[str, Any] = {
        "schemaVersion": 1,
        "tool": "ai_remediate",
        "mode": "loop",
        "path": str(root),
        "scoreBefore": before.score,
        "generated": [a.relpath for a in plan.artifacts],
        "needsHuman": plan.needs_human,
        "appliedToRealProject": False,
    }
    if not plan.artifacts:
        result["status"] = "nothing_to_do"
        result["scoreAfter"] = before.score
        result["delta"] = 0
        result["note"] = "没有可自动改造的项；改造要么已在位，要么属于 needs_human"
        return result

    with tempfile.TemporaryDirectory(prefix="ai-remediate-") as tmp:
        tmpdir = Path(tmp)
        project_copy = tmpdir / "project"
        reason = _copy_for_scan(root, project_copy, max_copy_mb * 1024 * 1024)
        if reason:
            result["status"] = "quantification_skipped"
            result["scoreAfter"] = None
            result["delta"] = None
            result["note"] = reason
            return result

        try:
            apply_staging(plan, _staging_in(project_copy, plan), project_copy, force=False)
        except RemediationError:
            # 副本里可能已存在同名文件（扫描说是缺失，通常不会），如实报告
            result["status"] = "apply_conflict"
            result["scoreAfter"] = None
            result["delta"] = None
            result["note"] = "临时副本应用时发生冲突，已放弃量化"
            return result

        after = ai_readiness.scan(project_copy)
        after_keys = {f.key for f in after.findings}

        result["status"] = "measured"
        result["scoreAfter"] = after.score
        result["delta"] = after.score - before.score
        result["resolvedFindings"] = [
            a.finding_key for a in plan.artifacts if a.finding_key not in after_keys
        ]
        result["remainingFindings"] = [
            {"key": f.key, "title": f.title, "severity": f.severity} for f in after.findings
        ]
        result["note"] = (
            "before/after 均在**同一台机器、同一份代码副本**上实测；"
            "after 是「假设人审通过并落盘」的效果，实测不等于已交付。"
        )
    return result


def _staging_in(project_copy: Path, plan: Plan) -> Path:
    staging = project_copy / ".ai_remediate_staging"
    staging.mkdir(parents=True, exist_ok=True)
    for art in plan.artifacts:
        p = staging / art.relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(art.content, encoding="utf-8")
    return staging


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def print_plan(plan: Plan) -> None:
    print("════════ AI 友好度改造计划 ════════")
    print(f"  项目: {plan.path}")
    print(f"  类型: {', '.join(plan.kinds) or '未识别'}")
    print(f"  当前分数: {plan.score_before}")
    print()
    if plan.artifacts:
        print(f"  可自动改造（{len(plan.artifacts)} 项，事实位置留 TODO）：")
        for a in plan.artifacts:
            print(f"    → {a.relpath}   [{a.finding_key}]")
            if a.needs_human:
                print(f"        需人审: {'、'.join(a.needs_human)}")
    else:
        print("  可自动改造: 无")
    if plan.needs_human:
        print()
        print(f"  只能人工处理（{len(plan.needs_human)} 项）：")
        for item in plan.needs_human:
            print(f"    ✗ [{item['severity']}] {item['title']}")
            print(f"        {item['reason']}")
    print()
    print("  ⚠️ 生成物**不会**自动写入目标工程；先审阅，再 apply。")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="支柱 A：扫描 → 改造 → 复扫 闭环")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="只看计划，不产生文件")
    p_plan.add_argument("--path", default=".")
    p_plan.add_argument("--json", action="store_true")

    p_gen = sub.add_parser("generate", help="生成到 staging 目录")
    p_gen.add_argument("--path", default=".")
    p_gen.add_argument("--out", required=True)

    p_loop = sub.add_parser("loop", help="完整闭环并量化 before/after")
    p_loop.add_argument("--path", default=".")
    p_loop.add_argument("--json", action="store_true")
    p_loop.add_argument("--max-copy-mb", type=int, default=256)

    p_apply = sub.add_parser("apply", help="人审后写入目标工程")
    p_apply.add_argument("--path", default=".")
    p_apply.add_argument("--staging", required=True)
    p_apply.add_argument("--force", action="store_true", help="允许覆盖（会丢真实内容，慎用）")

    args = ap.parse_args(argv)
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"⛔ 不是目录：{root}", file=sys.stderr)
        return 1

    try:
        if args.cmd == "plan":
            plan = build_plan(root)
            if args.json:
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
            else:
                print_plan(plan)
            return 0

        if args.cmd == "generate":
            plan = build_plan(root)
            staging = Path(args.out).resolve()
            written = write_staging(plan, staging, root)
            print(f"✅ 生成 {len(written)} 个文件到 {staging}")
            print("   目标工程未被修改。审阅后再执行 apply。")
            for a in plan.artifacts:
                if a.needs_human:
                    print(f"   ⚠️  {a.relpath} 需人审: {'、'.join(a.needs_human)}")
            return 0

        if args.cmd == "loop":
            result = run_loop(root, max_copy_mb=args.max_copy_mb)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("════════ 改造闭环（量化） ════════")
                print(f"  改造前: {result['scoreBefore']}")
                print(f"  状态:   {result['status']}")
                if result.get("scoreAfter") is not None:
                    print(f"  改造后: {result['scoreAfter']}  (Δ {result['delta']:+d})")
                    print(f"  消除的 finding: {', '.join(result.get('resolvedFindings') or []) or '无'}")
                print(f"  {result.get('note', '')}")
                if result.get("needsHuman"):
                    print(f"  仍需人工: {len(result['needsHuman'])} 项")
                print(f"  已写入目标工程: {result['appliedToRealProject']}")
            return 0

        if args.cmd == "apply":
            plan = build_plan(root)
            applied = apply_staging(plan, Path(args.staging).resolve(), root, force=args.force)
            print(f"✅ 已写入 {len(applied)} 个文件:")
            for p in applied:
                print(f"   {p}")
            print("   下一步：补齐所有 TODO(需人工填写)，然后跑 make gate 复扫。")
            return 0

    except RemediationError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
