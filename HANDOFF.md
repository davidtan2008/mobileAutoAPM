# 交接文档

> **给接手的开发者**：这份文件回答「这是什么、现在在哪、我该从哪开始、有哪些坑」。
> 读完这一份你就能开工。要动手前再读 `AGENTS.md` 与 `ROADMAP.md`。

---

## 1. 这是什么项目

**一句话**：让移动端项目对 AI 友好，并让 AI 自主把移动端性能与稳定性问题解决掉。

面向 **iOS + React Native（跨 iOS / Android / 鸿蒙）**。

### 两根支柱

| | 做什么 | 现状 |
|---|---|---|
| **A · 项目 AI 化改造** | 把人类维护的项目改造成 AI 可读、可改、可验证 | 只有扫描器，**改造闭环没做** |
| **B · APM 自主闭环** | 发现 → 定位 → 修复 → 验证 → 防劣化 | 能力齐全；已有 1 次成功闭环（译文持久化）与 1 次诚实失败（T1 启动） |

**为什么 A 在 B 前面**：一个没有测试、构建命令不明、缺 context 文件的项目，
AI Agent 既无法理解它，也**无法验证自己的任何改动** —— 此时谈"自主优化"是空话。

### 和其他 APM 产品的区别

Sentry / Crashlytics 解决「线上看见了什么」。
本项目解决「**看见了之后，谁去修、怎么证明修好了**」。

所以整个项目的重心不在"采集"，在**测量的可信度**。

---

## 2. 五分钟上手

```bash
git clone git@github.com:davidtan2008/mobileAutoAPM.git
cd mobileAutoAPM

make help      # 看所有可用命令
make test      # 跑全部测试（三套：Python / rn-apm / ios-apm）
make doctor    # 体检本机移动端工具链
```

**期望结果**：`make test` 全绿。如果没绿，先解决环境问题再往下。

### 依赖

- **必需**：Python 3.9+、Node 18+
- **iOS 任务**：Xcode、`mobilebuildmcp`（`npm i -g mobilebuildmcp`）
- **Android**：Android SDK 的 `platform-tools` 在 PATH
- **鸿蒙**：DevEco Studio（自带 SDK），`hdc`/`ohpm`/`hvigorw` 在 PATH
- **图表**：`brew install librsvg`（只需 `rsvg-convert`）

`make doctor` 会告诉你缺什么、怎么装。

---

## 3. 现在在哪

### 提交历史

```
453c8e9  修复图表门禁：改用内容哈希
5093b27  T1 闭环演练结论 + 测量协议 + 交接路线图
28e9ff8  R1 定位落地：架构图 / 原理图 / 实现细节
e5cbe4b  统一入口 + 库质量收口
e85bb17  R0 自我达标 + 市场调研落地
4bea8bc  初始化
```

### 完成度

| 路线图 | 状态 |
|---|---|
| R0 自我达标（AI 友好度 100/100） | ✅ |
| R1 定位落地（README / 架构图 / 原理图 / 实现细节） | ✅ |
| **T1 闭环演练** | ⚠️ **诚实失败**：200ms 目标被 control p50=210ms 拦截（见 §4） |
| **首个成功闭环** | ✅ 结束任务译文持久化：红测→单变量修复→120/120 单测 + Release 构建 |
| **P0 补齐测量能力** | 🟡 **iOS profile + 方差诊断已落地；重启后 warmup=3/n=10 两次通过并生成 provisional baseline，仍待干净 commit 基线** |
| P1 把 T1 结论做成能力 | ✅ **可行性/对照组闸门已落地并真实验证；200ms 目标被 control 地板拦截** |
| P2 支柱 A 改造闭环 | ⬜ |
| P3 自我进化 | ⬜ 设计已有，未实现 |
| P4 开源运营 | ⬜ |

### 仓库结构

```
plugins/mobile-apm/   ★ 单一真源：7 技能 / 4 子 Agent / 4 命令 / 1 hook / 10 脚本 / 知识库
ios-apm/              iOS 原生埋点 SDK（Swift Package，11 测试）
rn-apm/               React Native 埋点 SDK（npm，74 测试）
tools/                跨 agent 编译器 + 图表渲染
docs/                 文档与图表
dist/                 生成物（**勿手改**）
```

---

## 4. 你必须知道的一件事：T1 的故事

**这是本项目的转折点，直接决定了下一步做什么。**

### 发生了什么

T1 是第一次真实的自主闭环演练，题目是「**冷启动压到 200ms**」（被观测对象：一个真实 iOS 工程，iPhone 13 真机）。

结果：**目标不可达，且做的那次优化无效。**

### 三条实测结论

**① 目标不可达**

把 App 的内容换成 `Text("x")`（保留全部结构与埋点），SwiftUI 场景引导耗时**不变**（152–190ms）。

> **一个空壳 App 的地板就是 230ms。** 200ms 需要的不是优化，而是换掉整个 App 生命周期结构。

**② 那次优化无效果**

可优化段 126ms → 132ms，**p=0.694**。改动确实按设计生效了，但没有可测收益 → **已回退**。

**③ 测量方法本身不成立**

冷启动总耗时有一个未受控的 **~2 倍方差源**（样本在 279ms 与 541ms 之间跳），
**比要优化的幅度（~50ms）还大**。

曾试图按 `pre-main` 分层消除干扰，**但被自己的数据证伪了** —— 两个数据集的趋势完全相反。

### 为什么这件事重要

**它暴露的不是 App 的问题，是平台的缺口。**

平台反复强调「方差大就不可信」，却**没有给出可执行的测量协议**。
而那个 2 倍方差**不是这个 App 特有的** —— 任何用户让平台优化冷启动都会撞上。

> **测量不可靠 → 「验证」环节失效 → 核心承诺崩塌。**

**所以下一步不是继续优化 App，是补平台的测量能力。**

### 一个值得记住的时刻

数据里有 fast 模式的样本（279ms）。**挑两个对比基线，就能报出「优化 11%」的漂亮数字。**

**没有做。** 那是伪造。

---

## 5. 从这里开始

### 第一件该做的事：P0 · 补齐测量能力

详见 `ROADMAP.md` §P0。当前第一版已经落地：

| # | 能力 | 当前状态 |
|---|---|---|
| 1 | iOS 原生参数化测量 profile | `apm_measure.py`；物理设备/bundle/构建产物硬校验，CoreDevice readiness 闸门，逐次 observations + raw 工件；Release 真机链路已跑通 |
| 2 | 方差诊断 | `apm_diagnose.py`；CV、疑似多簇、分段 CV、跨运行相关性与同 commit 跨 run 漂移；已用 T1 与多轮真机 run 回归 |
| 3 | 判据建议 | 高方差/多簇/跨 run 漂移时建议增加样本、控制变量或改测同次分解指标；baseline 已将质量失败返回 `1` |

**边界**：当前只承诺 iOS 原生冷启动 profile；Android / 鸿蒙 / RN 标准适配器尚未宣称完成。
`pre-main` 只记录为可疑变量，不自动分层。重启并解锁后，固定 warmup=3、n=10 的两次
独立 run（p50=256ms/CV=9.6%、266ms/CV=7.1%）通过，跨 run 诊断为 `consistent`，
已生成 provisional baseline；历史失败证据见目标工程 `.apm/issues/ISSUE-P0-002-cross-run-shift.json`
与 `ISSUE-P0-003-warmup-repeatability.json`。

**背景知识在 `plugins/mobile-apm/references/measurement-protocol.md`** —— 先读它，那里有完整的实测数据与推理过程。

### 然后：按 P1 先做可行性/对照组，再找一个**确实可达成的**需求跑通闭环

T1 目标「冷启动 ≤200ms」已被真实最小 control（n=20，p50=210ms）拦截；
这条启动路线不再继续局部优化。绝对目标先运行：

```bash
python3 plugins/mobile-apm/scripts/apm_feasibility.py plan \
  --metric <指标> --target <目标>
```

没有 `potentially_reachable` 结论，不进入局部优化。

发布需要「目标达成且验证过」的演示。候选：

- **已确认的劣化项** —— 用 `apm_baseline.py` 对比两个 commit
- **白屏问题** —— 有现成工具（`apm_white_screen.py`）
- **崩溃根因** —— 有完整链路（符号化 → 聚类 → 根因）

⚠️ **不要再用「启动压到 X ms」这类题目** —— 除非先做了可行性判断（见 §6 的坑 5）。

---

## 6. 九个你会踩的坑

### ① `dist/` 是生成物，不是源码

改内容**只改 `plugins/mobile-apm/`**，然后 `make build-portable`。
CI 会校验一致性，直接改 `dist/` 会失败。

同理：**图表改了 SVG 必须 `make build-diagrams`** —— 有门禁会拦你。

### ② 全角标点在 shell 里是变量名的一部分

**本项目文档大量使用中文全角标点。** `"$var（说明）"` 里的 `（` **不是** bash 的变量名分隔符，
bash 会尝试展开一个叫 `var（说明）` 的变量，在 `set -u` 下直接报错。

**写法**：`"${var}（说明）"`。这个坑真实发生过。

### ③ 测量口径不一致 = 结论无效

冷/温/热启动、模拟器/真机、debug/release —— **都不可直接比**。
用不同口径的基线对比，差异里混了口径差异。

**这个坑也真实发生过**：T1 早期得出过一个错误结论，就是因为对照组用了不同口径。

### ④ 不要挑样本

数据有多峰时，**挑快的那簇报数就是伪造**。正确做法是修测量，或改测方差不敏感的指标。

### ⑤ 动手前先判断可行性

T1 的教训：**先干了活才发现目标不可达**。

**正确顺序**：先用最简对照组测出「地板」（把内容换成空壳，看还剩多少），
再判断目标是否可达。

### ⑥ iOS 模拟器测不了真机的东西

- **卡顿/FPS 完全测不了**（Apple 不暴露帧时序，平台限制）
- 数据特征与真机**失真方向相反**：实测 pre-main 模拟器 257ms vs 真机 **11ms**

### ⑦ 内存用 `phys_footprint`，不用 `resident_size`

系统按 footprint / PSS 判定内存压力。用 RSS 会严重高估。

### ⑧ `ErrorUtils.setGlobalHandler` 必须链式，不能替换

全局单例，Sentry 等 SDK 也会设置。直接覆盖会**打断别人**。

### ⑨ 取不到值时返回「不可用」的显式值

```swift
func premainMillis() -> Int   // 取不到返回 -1
```
返回 0 会被误读成「pre-main 极快」—— **这是危险的假数据**。

**完整清单见 `AGENTS.md` 的「坑与禁区」。**

---

## 7. 五条铁律

**这不是口号，是项目的全部价值所在。违反任何一条，产出即作废。**

| # | 铁律 | 含义 |
|---|---|---|
| 1 | **无基线不优化** | 没有可比基线之前的"优化"都是盲改 |
| 2 | **单变量** | 一次只改一处，否则无法归因 |
| 3 | **必须复测** | 同设备、同构建类型、同命令 |
| 4 | **绝不伪造** | 测不出来就说测不出来 |
| 5 | **结论带来源** | 命令 + 设备 + 样本量 + 显著性，缺一不可 |

> 第 4 条是专门针对 LLM 的：最常见的失败模式是**编一个看起来合理的数字，
> 然后基于它改一堆代码**。这比不优化更糟。

**T1 演练里这五条全部被触发过。** 这不是理论。

---

## 8. 关键文件导航

### 先读这四份

| 文件 | 作用 |
|---|---|
| `HANDOFF.md` | ← 你在读的 |
| `ROADMAP.md` | 现状、下一步、不能碰的线、**发布前置条件** |
| `AGENTS.md` | 项目约定（构建/测试/坑与禁区） |
| `ARCHITECTURE.md` | 整体架构与闭环机制 |

### 按需查

| 想看什么 | 去哪 |
|---|---|
| 每个脚本怎么实现的、为什么这样设计 | `docs/IMPLEMENTATION.md` |
| 测量方法论（**T1 的完整教训**） | `plugins/mobile-apm/references/measurement-protocol.md` |
| 平台能力边界（什么测不了） | `docs/diagrams/capability-matrix.svg` |
| 技术选型与各平台硬限制 | `plugins/mobile-apm/references/stack-selection.md` |
| 指标口径定义 | `plugins/mobile-apm/references/metrics-definitions.md` |
| 完成度与已知限制 | `docs/PROGRESS.md` |
| 工具链与 MCP 配置（本机实测） | `docs/mcp-setup.md` |
| 双阈值闸门原理 | `docs/diagrams/gate.svg` |
| 自我进化设计 | `docs/diagrams/evolution.svg` |

---

## 9. 常用命令

```bash
make help              # 全部命令
make test              # 三套测试（改完必跑）
make test-py           # 只跑 Python（最快，92 个测试）
python3 plugins/mobile-apm/scripts/apm_measure.py --help  # iOS 标准测量 profile
python3 plugins/mobile-apm/scripts/apm_diagnose.py --help # 方差诊断
make gate              # AI 友好度门禁（CI 同款）
make check             # 快速自检
make doctor            # 工具链体检
make build-portable    # 重新生成 dist/
make build-diagrams    # 重新渲染图表
make clean
```

**提交前**：`make lint test-py gate verify-portable verify-diagrams` 全绿。

---

## 10. 需要外部条件的事（可能卡住你）

| 项 | 缺什么 |
|---|---|
| Sentry MCP | 需 Sentry 账号，在 Claude Code 里执行 `/mcp` 完成 OAuth |
| Android / 鸿蒙真机验证 | 需接设备（`adb devices` / `hdc list targets` 目前为空） |
| Firebase 相关 | 依赖 Google 基础设施，国内需代理；**Crashlytics 在鸿蒙不可用** |
| iOS 真机测量/截图 | 需要一台真机；测量用 `idevicesyslog`，截图推荐可选 `pymobiledevice3`（也可用 `--pymobiledevice3-bin` 指向隔离安装） |

---

## 11. 交接清单

- [ ] `make test` 全绿
- [ ] `make doctor` 看过，知道本机缺什么
- [ ] 读过 `ROADMAP.md` 的 **⏰ 发布前置条件**
- [ ] 读过 §4「T1 的故事」—— 理解为什么下一步是补测量而不是加功能
- [ ] 知道 `dist/` 是生成物、`${var}` 要加花括号
- [ ] 知道**不能挑样本报数**

**做完这些，你可以从 `ROADMAP.md` §P0 开始。**
