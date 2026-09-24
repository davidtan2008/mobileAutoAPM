---
name: apm-doctor
description: This skill should be used when starting any mobile APM/perf/crash task, when the user asks "环境能不能跑"、"工具链检查"、"apm doctor"、"为什么这条命令失败", or before the first build/profile/automation run in a session. It reports which APM capabilities (startup, render, white-screen, memory, crash, UI automation, Android, HarmonyOS) are actually executable on this machine, so the agent never assumes a missing tool exists.
version: 0.1.0
---

# APM Doctor — 能力就绪度自检

## 目的

在执行任何移动端性能/崩溃任务**之前**，确定本机"哪些能力真的能做"。
不要假设 `adb`、`maestro`、`xctrace` 存在 —— 先探测，再决定路径。

## 何时使用

- 会话中第一次涉及 build / profile / 录屏 / 崩溃符号化 / UI 自动化时
- 用户抱怨"命令找不到"、"跑不起来"、"为什么失败"时
- 计划里要落一条新的自动化链路之前

## 执行

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py"
```

机器可读（供决策）：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --json
```

只看某平台：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --platform ios
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --platform rn
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --platform android
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --platform harmony
```

## 决策规则（关键）

读取 `--json` 输出中的 `capabilities` 字段：

| 状态 | 含义 | Agent 该怎么做 |
|---|---|---|
| `ready: true` | 该能力的工具链齐全 | 直接执行，走全自动路径 |
| `ready: false` | 缺工具 | **降级**，不要硬跑 |

**降级策略**（按优先级）：

1. **先修工具**：如果 `missing` 里的工具能一条命令装上（如 `brew install watchman`），
   先装，再重跑 doctor。装完必须复验。
2. **换等效路径**：例如缺 `xctrace` 就用 `mobilebuildmcp` 的能力代替；
   缺 `adb` 就用 Android Studio 的 SDK Manager 补齐后再继续。
3. **明确告知并停止**：如果该能力是本次任务的硬前提且无法安装（如需真机的项目），
   向用户报告「缺 X，无法完成 Y」，给出安装命令，**不要伪造结果**。

## 硬性约束

- 绝不在工具缺失时假装跑出了性能数据。
- 绝不用估算值、历史值或"典型值"充当本机实测结果。
- 报告性能数字时，必须写清来源（哪个命令、哪台设备/模拟器、哪个 commit）。
- 首次在某个工程上使用本插件时，先跑一次 doctor，并把结果写进该工程的
  `.apm/baseline/env.json`，作为后续对比的锚点。
