# AGENTS.md — mobileAutoAPM 项目约定

> 这是**唯一真源**。`CLAUDE.md` 只是一行指回这里。
> 无论你用的是 Claude Code / opencode / Codex / Cursor，都从这份文件开始。

---

## 这个项目是什么

**让移动端项目对 AI 友好，并让 AI 自主把移动端性能与稳定性问题解决掉。**

两根支柱，共享底层：

| 支柱 | 做什么 | 入口 |
|---|---|---|
| **A · 项目 AI 化改造** | 遗留项目 → AI 可读、可改、可验证 | `scripts/ai_readiness.py` |
| **B · APM 自主闭环** | 发现 → 定位 → 修复 → 验证 | `skills/apm-loop/` |

完整背景见 `docs/RESTRUCTURE-PLAN.md`，当前进度见 `docs/PROGRESS.md`。

---

## 五条不可违背的原则

这个项目的全部价值都建立在这五条上。**违反任何一条，产出即作废。**

1. **无基线不优化** —— 没有可比基线之前的"优化"都是盲改
2. **单变量** —— 一次只改一处，否则无法归因
3. **必须复测** —— 用**与基线完全相同的口径**（同设备、同构建类型、同命令）
4. **绝不伪造** —— 不许用估算值、历史值、"典型值"充当实测。**测不出来就说测不出来**
5. **结论带来源** —— 每个性能数字都要能追溯到：哪条命令、哪台设备、哪个 commit

> 第 4 条是专门针对 LLM 的：最常见的失败模式是**编一个看起来合理的数字，
> 然后基于它改一堆代码**。在这个项目里，这比不优化更糟。

---

## 怎么跑（每条都已验证）

### Python 工具（零第三方依赖，Python 3.9+）

```bash
# 工具链体检 —— 任何 APM 任务开始前先跑这个
python3 plugins/mobile-apm/scripts/apm_doctor.py

# AI 友好度扫描
python3 plugins/mobile-apm/scripts/ai_readiness.py --path .

# 基线显著性判定（退出码 0=无劣化 2=有劣化）
python3 plugins/mobile-apm/scripts/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/<本次>/metrics.json

# 白屏检测（纯标准库解 PNG）
python3 plugins/mobile-apm/scripts/apm_white_screen.py shot.png

# RN 堆栈符号化（Hermes 两步合成）
python3 plugins/mobile-apm/scripts/rn_symbolicate.py compose \
  --outer bundle.hbc.map --inner bundle.map --out composed.map
python3 plugins/mobile-apm/scripts/rn_symbolicate.py symbolicate \
  --map composed.map --stack crash.txt

# 符号文件门禁（可直接作发版门禁）
python3 plugins/mobile-apm/scripts/rn_build_symbols.py verify \
  --platform ios --build-dir ios/build --strict
```

### 测试

```bash
# Python（46 个测试）
python3 plugins/mobile-apm/tests/test_ai_readiness.py
python3 plugins/mobile-apm/tests/test_rn_symbolicate.py

# RN SDK（74 个测试）
cd rn-apm && npm install && npm test

# iOS SDK（11 个测试）
cd ios-apm && swift test
```

### 跨 agent 产物生成

```bash
python3 tools/build-portable.py        # → dist/
```

---

## 目录约定

```
plugins/mobile-apm/     ★ 单一真源：技能 / Agent / 命令 / hook / 脚本 / 知识库
ios-apm/                iOS 原生埋点 SDK（Swift Package）
rn-apm/                 React Native 埋点 SDK
tools/                  构建与生成脚本
dist/                   生成产物（勿手改，改完源头重新生成）
docs/                   项目文档
```

**改内容只改源头，然后重新生成 `dist/`。不要把 `dist/` 当源文件改。**

---

## 写代码时的约定

### 通用

- **绝不抛错、绝不阻塞**：埋点与工具代码不能因为自身失败而影响被观测对象
- **必须有界**：任何缓冲都要定容。**一个用来发现内存泄漏的工具，自己绝不能泄漏内存**
- **如实报告缺口**：能力不可用时明确说"不可用"，而不是静默降级或给假数据
- **偏好零依赖**：Python 工具只用标准库；SDK 不引第三方运行时依赖

### 判空/失败时的取值

**取不到数据时返回"不可用"的显式值，不要返回值语义上像正常数据的默认值。**

```swift
// ✅ 取不到返回 -1，调用方能区分"没有数据"与"数据是 0"
func premainMillis() -> Int

// ❌ 返回 0 会被误读成"pre-main 极快" —— 这是危险的假数据
```

### 中文注释与文档

本项目主要文档与注释用中文。**术语保留英文原文**（如 `phys_footprint`、`dyld`、`TTID`），
因为它们是行业通用词，翻译反而增加理解成本。

---

## 坑与禁区

| 禁区 | 原因 |
|---|---|
| 不用 `resident_size` 测内存 | 系统按 `phys_footprint` / PSS 判定内存压力，用 RSS 会严重高估 |
| 不直接覆盖 `ErrorUtils.setGlobalHandler` | 会打断 Sentry 等已存在的 handler，**必须链式调用** |
| 不在 `init()` 才首次触碰埋点 | 属性初始化器先于 `init()` 执行，会把最大一块成本藏起来（实测有 219ms 盲区） |
| 不手写多份 agent 适配 | 必然漂移，用 `tools/build-portable.py` 生成 |
| 不用版本号关联 sourcemap | 热修后版本号会错位，**必须用 debug ID** |
| 不给 `firstFrame` 用直接打点 | 会早于 `CA::Transaction::commit`，低估启动耗时；应延迟一个 runloop |
| 不混比不同口径的数据 | 冷/温/热启动、模拟器/真机、debug/release 都**不可直接比** |

---

## 改完之后怎么验证

**顺序不能反**：

1. 跑相关测试（见上方"测试"）
2. 有 Python 改动 → 跑 `ai_readiness.py --path .` 确认分数没退化
3. 有 `plugins/` 改动 → 重新生成 `dist/`，确认无 `CLAUDE_PLUGIN_ROOT` 残留
4. 涉及性能结论 → **必须给出「命令 + 设备 + 样本量 + 显著性」**，缺一不可

**没有验证的改动不要提交。** 宁可如实说"没验证"，也不要假装验证过了。

---

## 环境依赖

### 必需

Python 3.9+ · Node 18+ · Xcode（iOS 任务）

### 按需

| 工具 | 用途 | 位置 |
|---|---|---|
| `mobilebuildmcp` | iOS 构建/模拟器/UI 自动化 | `npm i -g mobilebuildmcp` |
| `agent-device` | 跨平台设备驱动（含鸿蒙） | `npm i -g agent-device` |
| `adb` | Android | Android SDK `platform-tools` |
| `hdc` / `ohpm` / `hvigorw` | 鸿蒙 | DevEco Studio 内置 SDK |

**任何 APM 任务开始前先跑 `apm_doctor.py`** —— 它会告诉你本机"现在真的能做什么"，
而不是让你假设某个工具存在然后失败。

---

## 遇到不确定时

1. 先看 `docs/` 下是否有相关文档
2. 再看 `plugins/mobile-apm/references/` 里的知识库（选型硬限制 / 指标口径）
3. **仍然不确定就问用户** —— 不要猜，尤其不要猜性能数字
