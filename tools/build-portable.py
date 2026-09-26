#!/usr/bin/env python3
"""把 Claude Code 插件源码编译成**跨 Agent 可移植树**。

单一真源 = `plugins/mobile-apm/`，本脚本产出可被
**Claude Code / opencode**（以及任何读 `.claude/skills/` 的 harness）直接使用的目录树。

为什么不手写两份：两份必然漂移。生成器保证真源唯一。

用法:
  python3 tools/build-portable.py                # 输出到 dist/
  python3 tools/build-portable.py --target /tmp/x
  python3 tools/build-portable.py --check        # 只校验，不写文件

产出结构（可直接 cp 进你的 App 工程根目录）:
  dist/
  ├── AGENTS.md                      # 唯一真源指令（Claude Code ≥2.1.277 / opencode / Codex 都读）
  ├── CLAUDE.md                      # 只有一行 @AGENTS.md（兼容旧版本）
  ├── opencode.json                  # opencode 主配置
  ├── .claude/
  │   ├── skills/                    # opencode 原生就读这里
  │   │   ├── apm-*/SKILL.md
  │   │   └── _apm/{scripts,references}/   # 共享资源
  │   └── hooks/apm-session-start.sh
  └── .opencode/
      ├── agents/*.md
      ├── commands/*.md
      └── plugins/apm-hook-bridge.ts
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "mobile-apm"

# 可移植格式里，共享资源的落点（相对工程根目录）
PORTABLE_SHARED = ".claude/skills/_apm"

# Claude Code 专属变量 → 可移植相对路径
REWRITES = [
    ("${CLAUDE_PLUGIN_ROOT}/scripts/", f"{PORTABLE_SHARED}/scripts/"),
    ("${CLAUDE_PLUGIN_ROOT}/references/", f"{PORTABLE_SHARED}/references/"),
    ("${CLAUDE_PLUGIN_ROOT}/hooks/scripts/", ".claude/hooks/"),
    ("${CLAUDE_PLUGIN_ROOT}", PORTABLE_SHARED),
]


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """极简 YAML frontmatter 解析（只处理本项目用到的标量与列表）。"""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, "", text
    raw = m.group(1)
    body = text[m.end():]
    data: dict = {}
    key = None
    buf: list[str] = []
    for line in raw.split("\n"):
        if re.match(r"^[a-zA-Z_-]+:", line):
            if key:
                data[key] = "\n".join(buf).strip()
            key, _, val = line.partition(":")
            key = key.strip()
            buf = [val.strip()]
        elif key is not None:
            buf.append(line)
    if key:
        data[key] = "\n".join(buf).strip()
    return data, raw, body


def rewrite_paths(text: str) -> str:
    for a, b in REWRITES:
        text = text.replace(a, b)
    return text


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------- 校验 ----------------

def validate() -> list[str]:
    """校验真源是否符合跨 harness 的硬约束（在生成前拦住问题）。"""
    problems: list[str] = []
    skills_dir = PLUGIN / "skills"
    if not skills_dir.is_dir():
        return [f"找不到技能目录 {skills_dir}"]

    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        sk = d / "SKILL.md"
        if not sk.exists():
            problems.append(f"{d.name}: 缺少 SKILL.md")
            continue
        fm, _, _ = parse_frontmatter(sk.read_text(encoding="utf-8"))

        # opencode 硬性要求 name 与 description 同时存在，否则 skill 不加载
        if not fm.get("name"):
            problems.append(f"{d.name}: 缺 name —— opencode 下该 skill 会**完全不加载**")
        elif fm["name"] != d.name:
            problems.append(f"{d.name}: name='{fm['name']}' 与目录名不一致 —— opencode 要求二者相等")
        if not fm.get("description"):
            problems.append(f"{d.name}: 缺 description —— opencode 下该 skill 会**完全不加载**")
        else:
            dl = len(fm["description"])
            if dl > 1024:
                problems.append(f"{d.name}: description {dl} 字符 > opencode 上限 1024")

        # 正文里点名 Claude 专属的大写工具名会在 opencode 下失去意义
        _, _, body = parse_frontmatter(sk.read_text(encoding="utf-8"))
        body_nocode = re.sub(r"```.*?```", "", body, flags=re.S)
        named = [t for t in ("Read", "Write", "Edit", "Glob", "Grep", "Task")
                 if re.search(rf"(?<![A-Za-z_]){t}(?![A-Za-z_])", body_nocode)]
        if named:
            problems.append(f"{d.name}: 正文点名了工具 {named} —— opencode 工具名全小写，建议改成描述性说法")
    return problems


# ---------------- 生成 ----------------

def emit_skills(target: Path) -> int:
    n = 0
    for d in sorted((PLUGIN / "skills").iterdir()):
        sk = d / "SKILL.md"
        if not d.is_dir() or not sk.exists():
            continue
        text = rewrite_paths(sk.read_text(encoding="utf-8"))
        write(target / ".claude" / "skills" / d.name / "SKILL.md", text)
        n += 1
    return n


def emit_shared(target: Path) -> None:
    for sub in ("scripts", "references", "docs"):
        src = PLUGIN / sub
        if not src.is_dir():
            continue
        for f in sorted(src.iterdir()):
            if f.is_file():
                dst = target / PORTABLE_SHARED / sub / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix in (".md", ".py", ".sh"):
                    content = rewrite_paths(f.read_text(encoding="utf-8"))
                    dst.write_text(content, encoding="utf-8")
                    if f.suffix in (".py", ".sh"):
                        dst.chmod(0o755)
                else:
                    shutil.copy2(f, dst)
    # session-start hook 脚本
    hs = PLUGIN / "hooks" / "scripts" / "session-start.sh"
    if hs.exists():
        dst = target / ".claude" / "hooks" / "apm-session-start.sh"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hs, dst)
        dst.chmod(0o755)


def emit_agents(target: Path) -> int:
    """Claude Code agent → opencode agent。

    差异（据调研）：
      - opencode **以文件名为 agent 名**，没有 name 字段
      - 需要 `mode: subagent`，否则会出现在主 agent 列表里
      - `tools` 字段已弃用 → 改用 `permission`
    """
    src = PLUGIN / "agents"
    if not src.is_dir():
        return 0
    n = 0
    for f in sorted(src.glob("*.md")):
        fm, _, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        name = fm.get("name", f.stem)
        tools_raw = fm.get("tools", "")
        can_write = bool(re.search(r"\b(Write|Edit)\b", tools_raw))

        front = [
            "---",
            f"description: {fm.get('description', '').strip()}",
            "mode: subagent",
            "permission:",
            f"  edit: {'allow' if can_write else 'deny'}",
            "  bash: allow",
            "  webfetch: deny",
            "---",
            "",
        ]
        out = "\n".join(front) + rewrite_paths(body)
        write(target / ".opencode" / "agents" / f"{name}.md", out)
        n += 1
    return n


def emit_commands(target: Path) -> int:
    src = PLUGIN / "commands"
    if not src.is_dir():
        return 0
    n = 0
    for f in sorted(src.glob("*.md")):
        fm, _, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        desc = fm.get("description", "").strip()
        out = f"---\ndescription: {desc}\n---\n\n" + rewrite_paths(body)
        write(target / ".opencode" / "commands" / f.name, out)
        n += 1
    return n


HOOK_BRIDGE = '''// APM hook 桥接：让 opencode 复用 Claude Code 的 hook 脚本。
//
// opencode 没有 settings hooks，只有插件 API。这里把 .claude/hooks/*.sh
// 当子进程调用，从而**同一份 hook 逻辑同时服务两个 harness**。
//
// 约定（与 Claude Code 对齐）：脚本读 stdin 的 JSON，exit 2 表示阻塞。

import type { Plugin } from "@opencode-ai/plugin"

export const ApmHookBridge: Plugin = async ({ $, directory }) => {
  const runHook = async (script: string, payload: unknown) => {
    const path = `${directory}/${script}`
    const proc = Bun.spawn(["bash", path], {
      stdin: "pipe", stdout: "pipe", stderr: "pipe",
    })
    proc.stdin.write(JSON.stringify(payload))
    proc.stdin.end()
    const code = await proc.exited
    const out = await new Response(proc.stdout).text()
    return { code, out }
  }

  return {
    // 会话开始：注入 APM 状态（与 Claude Code 的 SessionStart 等价）
    event: async ({ event }) => {
      if (event.type !== "session.created") return
      const { code, out } = await runHook(
        ".claude/hooks/apm-session-start.sh",
        { event: "SessionStart", cwd: directory },
      )
      if (code === 0 && out.trim()) {
        // 交给模型作为上下文
        return { context: out.trim() }
      }
    },
  }
}
'''

OPENCODE_JSON = {
    "$schema": "https://opencode.ai/config.json",
    "instructions": ["AGENTS.md"],
    "permission": {
        "skill": {"*": "allow"},
        # 保守默认：不自动改文件，性能任务多为「测量→报告」
        "edit": "ask",
        "bash": {
            "*": "ask",
            "xcrun xctrace *": "allow",
            "xcrun simctl *": "allow",
            "mobilebuildmcp *": "allow",
            "python3 .claude/skills/_apm/scripts/*": "allow",
            "adb devices": "allow",
            "adb shell dumpsys *": "allow",
            "hdc shell *": "allow",
            "git log *": "allow",
            "git diff *": "allow",
        },
    },
}


def emit_root_files(target: Path, n_skills: int, n_agents: int, n_cmds: int) -> None:
    agents_md = f"""# 移动端 APM 工程规范

本工程使用 **mobile-apm** 能力集做 iOS / React Native / Android / HarmonyOS 的
性能与稳定性治理。以下规则对**所有** AI coding agent 生效（Claude Code / opencode / 其他）。

## 五条铁律（违反即视为任务失败）

1. **无基线，不优化。** 没有可比基线之前的"优化"都是盲改。
2. **单变量。** 一次只改一处，否则无法归因。
3. **必须复测。** 改完用**与基线完全相同的口径**重跑（同设备、同构建类型、同命令）。
4. **绝不伪造。** 不许用估算值、历史值、"典型值"充当本机实测。测不出来就说测不出来。
5. **结论带来源。** 每个性能数字都要能追溯到命令 / 设备 / commit / 运行记录。

## 开工前

1. 跑环境体检，确认本机**实际**能做什么：
   ```bash
   python3 .claude/skills/_apm/scripts/apm_doctor.py
   ```
   缺工具时如实报告并给安装命令，**不要硬跑**。
2. 读 `.apm/state.json` 了解闭环当前处于哪一阶段。

## 可用技能

| 技能 | 用途 |
|---|---|
| `apm-loop` | 主控编排：发现→定位→修复→验证→防劣化 |
| `apm-doctor` | 环境能力就绪度自检 |
| `apm-startup` | 启动耗时检测与优化 |
| `apm-render` | 页面渲染 / 卡顿 / 白屏 |
| `apm-memory` | 内存 / 泄漏 / FOOM |
| `apm-crash` | 崩溃符号化 / 聚类 / 根因 |
| `apm-autotest` | 自动化测试 / 需求验证 / 性能回归门禁 |

知识库在 `.claude/skills/_apm/references/`：
- `stack-selection.md` —— 技术选型与**各平台硬限制**（动手前必读）
- `metrics-definitions.md` —— 指标口径定义（报告必须遵守）
- `measurement-protocol.md` —— 方差诊断与「不可信就停」协议

## 数据平面

```bash
S=.claude/skills/_apm/scripts
python3 "${{S}}/apm_doctor.py"                # 能力体检
python3 "${{S}}/apm_measure.py" --help        # iOS 原生标准测量 profile
python3 "${{S}}/apm_diagnose.py" <run>       # 方差诊断
python3 "${{S}}/apm_baseline.py" compare --baseline .apm/baseline/X.json --run <run>.json
python3 "${{S}}/apm_white_screen.py" shot.png # 白屏检测
```
`apm_diagnose.py` 退出码：`0` 测量可用 / `2` 测量不可信或样本不完整 / `1` 数据问题。
`apm_baseline.py compare` 退出码：`0` 无劣化 / `2` **可确认劣化** / `1` 数据或口径问题。
**退出码 2 可直接作为 CI 门禁；退出码 1 不能被当成「无劣化」。**

## 工件布局

```
.apm/
├── baseline/   # 基线（必须入库，否则换机器即失效）
├── runs/       # 每次测量（建议 gitignore）
├── issues/     # 发现的问题
└── state.json  # 闭环状态
```

## 反面清单

- ❌ 报性能数字却说不出来源
- ❌ 一次改多处且未分别测量
- ❌ 报"预计提升 X%"
- ❌ 用模拟器数据代表真机，或用 debug 数据代表线上
- ❌ 声称修好但没跑功能回归
- ❌ 优化后不更新基线，下次拿旧基线比新构建

（本文件由 `tools/build-portable.py` 从 `plugins/mobile-apm/` 生成，请勿直接编辑。）

<!-- 生成统计：{n_skills} skills / {n_agents} agents / {n_cmds} commands -->
"""
    write(target / "AGENTS.md", agents_md)

    # 旧版本 Claude Code / 特殊会话回退
    write(target / "CLAUDE.md", "@AGENTS.md\n")

    write(target / "opencode.json",
          json.dumps(OPENCODE_JSON, ensure_ascii=False, indent=2) + "\n")

    write(target / ".opencode" / "plugins" / "apm-hook-bridge.ts", HOOK_BRIDGE)

    write(target / ".gitignore",
          "node_modules/\n.apm/runs/\n*.trace\n*.xcresult\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="编译跨 Agent 可移植树")
    ap.add_argument("--target", default=str(REPO / "dist"))
    ap.add_argument("--check", action="store_true", help="只校验真源，不生成")
    args = ap.parse_args()

    problems = validate()
    if args.check:
        if problems:
            print("❌ 校验未通过：")
            for p in problems:
                print(f"   · {p}")
            return 1
        print("✅ 校验通过：所有技能满足跨 harness 硬约束。")
        return 0
    if problems:
        print("⚠️  校验发现问题（仍会生成，但请修复）：")
        for p in problems:
            print(f"   · {p}")
        print()

    target = Path(args.target)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    n_skills = emit_skills(target)
    emit_shared(target)
    n_agents = emit_agents(target)
    n_cmds = emit_commands(target)
    emit_root_files(target, n_skills, n_agents, n_cmds)

    # 残留的 Claude 专属变量检查
    leftover = []
    for f in target.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".py", ".sh"):
            try:
                if "${CLAUDE_" in f.read_text(encoding="utf-8"):
                    leftover.append(str(f.relative_to(target)))
            except Exception:
                pass

    print(f"✅ 已生成到 {target}")
    print(f"   技能 {n_skills} / 子Agent {n_agents} / 命令 {n_cmds}")
    if leftover:
        print(f"   ⚠️ 仍有 ${{CLAUDE_*}} 残留（opencode 下会失效）：{leftover}")
        return 1
    print("   无 Claude 专属变量残留，opencode 可直接使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
