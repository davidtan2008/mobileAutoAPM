---
name: apm-loop
description: This skill should be used when the user asks to autonomously find, diagnose, fix and verify any mobile performance or stability problem — "启动慢"、"页面卡"、"白屏"、"内存涨"、"崩溃"、"性能优化"、"APM 闭环"、"自动发现问题"、"跑一遍性能回归"、"优化并验证" — or when asked to set up an autonomous APM workflow for an iOS / React Native / Android / HarmonyOS project. It is the orchestrator that drives discover→diagnose→fix→verify→guard and enforces measurement discipline.
version: 0.1.0
---

# APM 自主闭环编排

这是主控技能。它不直接做剖析，而是**编排**：决定现在处于哪一阶段、该调用哪个专项技能、
以及**什么情况下必须停下来问人**。

## 五条铁律（违反即视为任务失败）

1. **无基线，不优化。** 没有可比的基线数据之前，任何"优化"都是盲目改代码。
2. **单变量。** 一次只改一个因素。同时改三处再测，等于没测——你无法归因。
3. **必须复测。** 改完必须用**与基线完全相同的口径**重跑（同设备、同构建类型、同测量命令）。
4. **绝不伪造。** 不许用估算值、历史值、"典型值"充当本机实测。测不出来就说测不出来。
5. **结论带来源。** 每个性能数字必须能追溯到：哪条命令、哪台设备/模拟器、哪个 commit、哪次运行。

> 这五条不是形式主义。LLM 驱动性能优化最常见的失败模式，就是
> **编造一个看起来合理的数字，然后基于它做一堆改动**。铁律 4 专门防这个。

## 闭环六阶段

```
Phase 0 体检 → Phase 1 基线 → Phase 2 发现 → Phase 3 定位 → Phase 4 修复 → Phase 5 验证 → Phase 6 防劣化
                                    ↑                                                    │
                                    └──────────────── 回归则回到 Phase 3 ────────────────┘
```

### Phase 0 — 体检（每次会话第一次涉及性能任务时必做）

用 `apm-doctor` 技能探测本机能力。**不要假设 xctrace / adb / maestro 存在。**

产出：`.apm/baseline/env.json`（记录当时的能力就绪度，后续对比时要确认环境没变）

### Phase 1 — 建立基线

在任何优化之前，先测出"现在是多少"。

- 明确本次要观测的指标（见 `references/metrics-definitions.md`）
- 用与目标指标匹配的工具测量：启动用专项技能 `apm-startup`，渲染用 `apm-render`，内存用 `apm-memory`，崩溃用 `apm-crash`
- **至少测 3 次取中位数**，并记录方差。方差过大说明测量方法本身不可靠，先修测量再谈优化
- 基线写入 `.apm/baseline/<维度>.json`，**必须包含**：commit sha、设备/模拟器型号、构建类型(release/debug)、测量命令、原始数据路径

### Phase 2 — 发现

两条路径，**都要走**：

**A. 数据驱动**：跑自动化测试/性能测试，与基线对比，找出劣化项。
**B. 静态审查**：带着 `references/` 里的反面清单去读代码（例：抖音案例中 `+load` 里做事、
动态库超过 6 个、`UIImage imageNamed` 触发 dyld 全局锁等待）。

产出：`.apm/issues/ISSUE-NNN.json`，每条包含：现象、证据(数据路径)、影响面(用户量/频率)、
疑似根因、置信度。**按影响面排序，先修高价值的。**

### Phase 3 — 定位根因

**禁止跳过这一步直接改代码。** 猜出来的根因不算根因。

定位手段按问题类型分派（见下方"分派表"）。定位的**完成标准**是：
能指出**具体是代码的哪一行/哪个调用在什么条件下**消耗了时间或内存，并且有 profile 数据支撑。

如果定位不出来，**如实报告"根因未确定"**，并给出下一步排查计划。不要编一个根因。

### Phase 4 — 修复

- 遵守**单变量**原则
- 修复方案要记录"为什么这样改"（依据是哪条 `references/` 里的方法论或哪份 profile）
- 优先选**结构性**修复（删除/延迟/并发），而不是微优化（抖音四步法：**删 → 延迟 → 并发 → 更快**）

### Phase 5 — 验证

**这是最容易糊弄的一步，必须严格。**

1. 用**与 Phase 1 完全相同的口径**重跑（同命令、同设备、同构建类型）
2. 对照基线，给出**提升幅度 + 是否超过测量噪声**
3. **必须同时验证没有引入回归**：跑 `apm-autotest` 的回归套件，确认功能没坏
4. 如果提升幅度在噪声范围内 → **如实说"无显著改善"**，不要包装成优化成果

### Phase 6 — 防劣化

单次优化会随时间被新代码侵蚀。把防线自动化：

- 把验证过的指标写入 `.apm/baseline/`，作为 CI 门禁的阈值
- 用 `perf-guard` 子 Agent / hook 在代码变更后自动跑回归
- 新基线**必须并入版本库**，否则换台机器就失效

## 工件布局（Agent 的状态）

```
.apm/
├── baseline/
│   ├── env.json          # 能力就绪度快照
│   ├── startup.json      # 启动耗时基线
│   ├── render.json       # 渲染/卡顿基线
│   └── memory.json       # 内存基线
├── runs/
│   └── <ISO时间>-<名称>/
│       ├── meta.json     # commit / 设备 / 构建类型 / 命令 / 环境
│       ├── raw/          # 原始 trace、日志、截图
│       ├── metrics.json  # 解析后的规范化指标
│       └── report.md     # 人类可读结论
├── issues/
│   └── ISSUE-001.json    # 发现的问题及其状态
└── state.json            # 当前处于哪个 Phase、当前在查哪个 issue
```

**每次开工先读 `state.json`**，避免重复劳动或丢失上下文。

## 分派表

| 现象 | 调用技能 | 首要定位手段 |
|---|---|---|
| 启动慢 | `apm-startup` | Instruments App Launch / `xctrace` + RN 分段打点 |
| 页面卡、掉帧、滑动不顺 | `apm-render` | Time Profiler + 分离观测 JS FPS / UI FPS |
| 白屏 | `apm-render` | 分段打点 + 截图比对 + View 树检测；鸿蒙查 `onRenderExited` |
| 内存涨、被杀（FOOM/OOM） | `apm-memory` | 内存水位曲线 + 堆快照对比；iOS 见 `references/stack-selection.md` §5 |
| 崩溃 | `apm-crash` | 符号化 → 聚类 → 根因；**注意 RN 四层机制的分工** |
| 需求验证 / 自动化测试 | `apm-autotest` | Maestro/Detox 流程 + 断言 |
| 不知道从哪下手 | 本技能 Phase 2 | 先跑一遍全维度基线 |

**必读**：动手前读 `references/stack-selection.md`（技术选型与硬限制）和
`references/metrics-definitions.md`（指标口径）。这两份文件里有大量"看起来该这样做但实际是坑"的内容。

## 停止条件（何时必须问人，不许自作主张）

自主不等于莽。遇到以下情况**必须停下来向用户报告并等待决策**：

| 情况 | 为什么 |
|---|---|
| 需要修改业务逻辑（不只是性能相关代码） | 可能改变产品行为 |
| 需要新增/替换三方 SDK 或后端 | 涉及合规、预算、架构决策 |
| 根因指向"需要重构架构" | 成本远超一次性能任务 |
| 测量环境不可靠（方差 > 30%） | 此时任何结论都不可信 |
| 需要真机/特定设备但本机没有 | 无法产出可信数据 |
| 优化收益与风险不匹配 | 应由人决策 |
| 连续 3 次尝试修复均未改善 | 说明根因判断错了，需要重新理解问题 |

## 反模式（自查清单）

做完一轮后自查，命中任何一条即为不合格：

- ❌ 报告了性能数字但说不出它是哪条命令产生的
- ❌ 一次提交里改了多处且没有分别测量
- ❌ "预计可以提升 X%" —— 没有实测就不该有预计
- ❌ 用模拟器数据代表真机结论（模拟器性能特征完全不同）
- ❌ 用 debug 构建的数据代表线上（debug 有大量额外开销）
- ❌ 声称修好了但没跑功能回归（可能为了性能改坏了功能）
- ❌ 优化了但没更新基线，下次对比时拿旧基线比新构建
