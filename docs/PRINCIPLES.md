# 实现原理

> 这份文档回答一个问题：**为什么这个项目要这么设计**。
>
> 它不是使用手册（那是 [`usage-guide.md`](usage-guide.md)），
> 也不是逐脚本的实现细节（那是 [`IMPLEMENTATION.md`](IMPLEMENTATION.md)）。
> 这里只写**取舍**——每个设计放弃了什么、换来了什么、以及它的边界在哪里。
>
> 写实现原理的目的只有一个：**让接手的人能判断哪些约定是硬约束，哪些只是当前实现。**
> 不知道这一点的人，要么把实现当铁律不敢改，要么把铁律当实现随手改坏。

---

## 目录

- [一、总体分工：确定的算，判断的猜](#一总体分工确定的算判断的猜)
- [二、五条铁律的技术依据](#二五条铁律的技术依据)
- [三、统计判定原理](#三统计判定原理)
- [四、可行性闸门原理](#四可行性闸门原理)
- [五、口径指纹原理](#五口径指纹原理)
- [六、有界性原理](#六有界性原理)
- [七、失败模型：退出码是判定，不是错误](#七失败模型退出码是判定不是错误)
- [八、证据链：每个数字怎么追到源头](#八证据链每个数字怎么追到源头)
- [九、"取不到"必须说"取不到"](#九取不到必须说取不到)
- [十、可复现性原理](#十可复现性原理)
- [十一、明确放弃的东西](#十一明确放弃的东西)

---

## 一、总体分工：确定的算，判断的猜

**核心原则：确定性的计算交给脚本，判断留给 Agent。**

需要"算"的事情全部是零依赖的确定性脚本：

| 事情 | 交给谁 | 为什么 |
|---|---|---|
| 解析 sourcemap、算 CV、跑置换检验 | 脚本 | 同样输入永远同样输出，可复现、可回归测试 |
| 改哪里、要不要问人 | Agent | 需要语义理解，这是模型擅长的 |

反过来做会怎样？让模型每次现写一遍解析逻辑：

1. **浪费 token** —— 每个任务重写一遍统计代码
2. **引入不确定性** —— 同样的数据，两次可能得到不同的 p 值
3. **无法测试** —— 逻辑散在对话里，CI 抓不到

**这个分工决定了仓库的物理结构**：`plugins/mobile-apm/scripts/`（11 个脚本，6140 行）
和 `plugins/mobile-apm/skills/`（7 个技能，1130 行）是两类完全不同的东西，
前者是代码，后者是给模型看的剧本。技能的职责是**编排脚本**，不是替代脚本。

> 仓库里到处能看到这句话的变体，例如 `apm-profiler` 技能：
> "**确定性解析和统计交给脚本；模型不手写解析逻辑、不生成假数字。**"

---

## 二、五条铁律的技术依据

铁律本身写在 [`AGENTS.md`](../AGENTS.md)。这里说**每条背后的技术原因**——
理解原因比背下条文重要，因为条文遇到新情况时需要人来判断，而原因可以推导。

### 铁律 1「无基线不优化」—— 噪声带可能比优化幅度还大

**问题本质**：冷启动测量的方差经常比要优化的幅度还大。

本项目实测到的真实样本（iPhone 13 / iOS 26.7 / Release）：

```text
快的一簇：279  278  302
慢的一簇：491  515  541
```

同一台设备、同一个构建，冷启动总耗时在 279ms 与 541ms 之间跳——**2 倍方差源**。
如果目标是优化 50ms，那么在这个测量条件下**任何优化都无法验证**：
不是"效果小"，是"测不出来"。

更危险的是这个分布**可以被人为挑选**。挑 279 和 278 就能报出漂亮数字。
本项目称之为**伪造**（不是"取样偏差"，是伪造）。

**所以铁律 1 的真实含义不是"先测一次"，而是"先证明你的测量能分辨出目标幅度"。**

CV 判定门槛（`apm_diagnose.py`）：

| CV | 判定 | 动作 |
|---|---|---|
| < 10% | `usable` | 可以做单变量实验 |
| 10% – 30% | `marginal` | 勉强可用，结论要加限定 |
| > 30% | `unusable_high_variance` | **停止优化，先修测量** |

### 铁律 2「单变量」—— 归因的前提

同时改三处再测，你得到的是一个复合 delta。**无法归因**。

这不是风格偏好，是实验设计的基本要求。项目的执行方式：
每次实验只改一处 → 测 → 记录 → 再改下一处。

**优化的优先级也有讲究**，来自抖音《iOS 启动优化实战篇》，被本项目收进
`apm-startup` 技能第五步：

```text
删 → 延迟 → 并发 → 更快
```

严格按这个顺序。绝大多数团队一上来就在做第 4 步（微优化），
而收益最大的往往是第 1、2 步。**先删再优化。**

### 铁律 3「必须复测」—— 口径必须逐字一致

"同口径"听起来是常识，实际上非常容易破。项目把它落成了一个可计算的**指纹**：

```
measurementSignature = sha256(规范化 JSON of 17 个字段)
```

17 个字段包括 `profile / profileVersion / toolVersion / deviceUdid /
deviceOsVersion / deviceOsBuild / platform / packageId / build /
startDefinition / endDefinition / launchType / measurementMethod /
sampleCount / intervalSec / warmupLaunches / deviceReadyTimeoutSec`

**注意后四项**：样本数、采样间隔、预热次数、设备就绪超时。
这四个也进指纹，意味着**改了任何一个，基线对比就会判定"口径不一致"**。

这是有意的：n=5 和 n=10 的样本分布不是同一回事，预热 1 次和预热 3 次的
首帧缓存状态也不同。**宁可判为"不可比"，也不要拿不同口径的数字做对比。**

`apm_baseline.compare_contexts()` 还会额外比对 `device / build / platform /
osVersion / profile / measurementSignature`，任一不一致就产生 context warning，
并让 `compare` 退出码变成 1（数据问题）而不是给出结论。

### 铁律 4「绝不伪造」—— 专门针对 LLM 的失败模式

> LLM 驱动性能优化最常见的失败模式，就是**编造一个看起来合理的数字，
> 然后基于它做一堆改动**。

这条铁律的存在理由是**使用者的身份不同**。人类工程师编数字会被同事质疑；
LLM 编数字会**一路流畅地走完整个流程**，因为它自己相信自己编的数字。

项目的对抗手段不是"提醒模型注意"，而是**在工具层堵死**：

| 堵法 | 在哪 |
|---|---|
| 取不到数据时返回 `null` / `unavailable`，**绝不返回 0** | `apm_measure.py` 的 pre-main 逻辑；iOS SDK 的 `premainMillis() -> Int` 返回 -1 |
| 诊断不可信时**整轮拒绝**，而不是挑掉慢样本 | `apm_diagnose` 的 `unusable_multimodal`；`apm_baseline` 的 `measurement-unreliable` 判定 |
| 跨 run 漂移时**保留原结论但降级**为 `provisionalVerdict` | `apm_baseline` 的降级机制 |
| 脚本自证时序，不信"看起来对" | `render_demo_hero.sh` 的 GIF 帧时间戳断言（时序错就删产物并非 0 退出） |

**关键区别**：这些不是"提示模型别犯错"，是**让犯错在工具层就失败**。
模型绕不过去。

### 铁律 5「结论带来源」—— 数字必须能追到源头

一个性能结论要能回答四个问题：

```text
哪条命令 · 哪台设备 · 哪个 commit · 哪次运行
```

这四问在代码里是具体的字段，不是文档要求：

| 问题 | 字段 | 位置 |
|---|---|---|
| 哪条命令 | `context.command` | `metrics.json` |
| 哪台设备 | `context.device` + `context.deviceInfo.*` | `metrics.json` |
| 哪个 commit | `context.commit` + `context.gitDirty` | `metrics.json` |
| 哪次运行 | `observations[]` 逐次 + `raw/sample-NN.log` | `metrics.json` / `raw/` |

`observations[]` 保留**逐次配对关系**，不是只留各指标独立的中位数——
因为"第 3 次启动的 pre-main 是 200ms"和"第 3 次启动的总耗时是 500ms"这两条
单独看都没用，**配对起来才可能发现相关性**。

---

## 三、统计判定原理

### 3.1 双阈值噪声带

判定"这次改动有没有效果"用了两道闸门，**取较大值**：

```python
noise_band = max(min_effect, 2.0 * pooled_stdev(baseline, run))
```

| 闸门 | 作用 | 挡住的错误 |
|---|---|---|
| `2 × pooled_stdev` | **统计下限**：幅度必须跑赢测量抖动本身 | 被噪声骗 |
| `min_effect` | **实用下限**：不追"真实但无意义"的收益 | 为 0.3ms 的"显著提升"去重构代码 |

**只做显著性检验** → Agent 会为微小但不无意义的收益改代码。
**只做实用阈值** → 会被噪声骗。两个都得做，所以取较大值。

`min_effect` 的来源有优先级：指标自己声明的 `minEffect`（如
`startup.cold.first_frame` 的 30ms）优先，否则用基线中位数的百分比兜底。

### 3.2 为什么用置换检验，而不是 t 检验

`apm_baseline.perm_test()` 的实现要点：

| 选择 | 理由 |
|---|---|
| 置换检验（10000 次） | 不依赖正态假设。启动耗时的分布**从来不是正态的** |
| **固定随机种子 42** | 结果可复现。同一对数据永远得到同一个 p 值 |
| `p = (hits + 1) / (iters + 1)` | +1 修正，避免 p=0 |
| **统计量用均值差，不用中位数差** | 见下 |

**为什么均值差**——这是实测修正，原始记录在 `docs/IMPLEMENTATION.md` §2.2：

| 统计量 | 清晰改善（−20%）测得的 p |
|---|---|
| 中位数差 | **0.0465**（贴着 0.05 边缘，差点漏判） |
| 均值差 | **0.0088** |

原因：n=5 时中位数只取第 3 个排序值，分辨率极低；均值利用全部样本。
**展示层仍然报 p50/p90**（行业口径），但检验统计量用均值——两者分工不同。

### 3.3 CV 门禁只对启动类指标生效

```python
is_variance_sensitive(name)  # 只有 startup.* / latency.* 命中
```

因为 `frameOverrunMs` 这类指标**允许负值**，套用启动指标的 CV 规则会误杀。

### 3.4 疑似多簇是启发式，不是模态检验

`apm_diagnose.suspected_clusters()` 递归二分，条件是：

```text
separation_ratio ≥ 3.0   且   gap_share ≥ 0.35
```

`clusterMethod` 字段**写死**了这句话：

> `"largest-gap heuristic (suspected clusters, not a formal modality test)"`

这是刻意的自我限定。它能抓住"一半快一半慢"这种明显的双峰，
但**不能声称这是统计上的模态检验**。文档里凡是说"疑似多峰"，
都不能写成"已证明是双峰"。

### 3.5 降级机制：原结论保留，但不算数

当焦点指标的方差诊断没过（退出码 2），`apm_baseline` **不会**直接说"没有差异"，
而是：

```json
{
  "verdict": "measurement-unreliable",
  "provisionalVerdict": "regressed",
  "reasons": ["跨 run/测量质量未通过；原统计差异仅保留为 provisionalVerdict，不能当确认结论"]
}
```

**为什么不直接丢弃**——丢弃会让人以为"没发现问题"；
**为什么不直接采信**——那正是铁律 4 禁止的。
所以：保留、标注、**降级为不可采信**，并且让整体退出码变成 1（数据问题）。

### 3.6 相关性只产生假设，不产生结论

`apm_diagnose.correlations_for_run()` 输出里，`interpretation` 字段**固定为**
`"clue_only_not_causality"`。

而且有一个反直觉的实测结论被写进了 `references/measurement-protocol.md`：

```text
样本组 A：(28,311) (16,450) (27,312) (15,314) (12,431)
样本组 B：另一组
→ fast 组反而更慢
```

`pre-main` 与总耗时只是**巧合相关**。所以工具**禁止**自动按 `pre-main` 分层或校正，
只把它列为"可疑变量"。

---

## 四、可行性闸门原理

`apm_feasibility.py` 回答的是一个**在优化之前**就该回答的问题：
**这个目标在这个技术栈上可达吗？**

### 4.1 对照组地板

```
margin      = 指标声明的 minEffect（缺失则 0）
floor       = control.p50 + margin          （lower_is_better）
conservativeFloor = control.p50 + 2 × stdev
```

三分判定：

```text
target <  control.p50        → blocked_by_control_floor        （物理上不可能）
target <  conservativeFloor  → indeterminate_within_control_margin
target ≥  conservativeFloor  → potentially_reachable
```

`higher_is_better` 时对称（`ceiling`）。

### 4.2 为什么要有这道闸门

T1 的真实教训：目标"冷启动 ≤200ms"。可优化段基线 126ms（n=5），Agent 做了 1 处
优化，测得 126ms → 132ms（n=8），**p=0.694，无显著变化**——已回退。

**如果没有这道闸门**，接下来会发生什么？要么换个口径重测到好看为止
（这就是伪造），要么宣布失败（但其实连"为什么不可能"都没查清）。

建了最小对照组（业务内容换成 `Text("x")`，其余全保留）之后，
得到 p50 = **210ms** > 200ms → `blocked_by_control_floor` →
**停止局部优化，升级架构决策**。

**这个"失败"比任何优化成果都值钱**——它避免了在物理上不可能的方向上继续投入。

### 4.3 它明确不承诺什么

`references/feasibility-protocol.md` §四 原文：

> "这不是在证明'目标一定可达'，只是排除一个已经被实测地板挡住的假设。"

所以 `potentially_reachable` **不等于**"目标可达"，只等于"没被地板挡住"。

### 4.4 对照组的纪律

> "对照组不是'再跑一次同一个 App'。它必须只改变一个变量。"

六条要求里最关键的两条：

- 业务内容替换为最小可运行内容，**但保留生命周期、初始化路径、测点**
- **不删埋点、不比起止点、不换 profile**

因为如果对照组把这些也改了，测出来的地板就不是同一个东西的地板了。
所以 `apm_feasibility` 会硬校验两组 `measurementSignature` 一致，不一致直接拒绝。

---

## 五、口径指纹原理

见 §二 铁律 3。这里补一个设计取舍。

**为什么用 sha256 而不是简单拼接？**

因为要判断的是"两个测量是不是同一个东西"，这需要**顺序无关**的规范化。
实现是：17 个字段拼成 dict → `json.dumps(sort_keys=True, separators=(",",":"))`
→ sha256。

`sort_keys=True` 保证字段顺序不影响结果；固定 separators 保证不会因为
Python 版本的格式化差异而漂移。

**这个指纹的代价**：改了 `--samples` 就不能跟旧基线比了。
这是**故意的**——n=5 和 n=10 的分布确实不同。

---

## 六、有界性原理

> **一个用来发现内存泄漏的工具，自己绝不能泄漏内存。**

这条约束推导出所有采集组件都必须定容：

| 组件 | 上限 | 超限行为 |
|---|---|---|
| `apm_measure.LogCollector` | 2000 行 | 丢弃并计数 `_dropped`；**该样本被判 invalid**（不是静默吞掉） |
| `apm_measure.LogCollector` 单行 | 4096 字符 | 截断 |
| `apm_measure` 样本数 | 1–100 | 参数校验拒绝 |
| `apm_measure` 预热次数 | 1–20 | 参数校验拒绝 |
| `apm_measure` 采样间隔 | ≥ 5.0s | 参数校验拒绝 |
| `apm_doctor` 版本字符串 | 120 字符 | 截断 |
| `apm_doctor` 单工具探测超时 | 8s | 超时视为 missing |
| `ai_readiness` 遍历文件数 | 20000 | 超出直接停止遍历 |
| `ai_readiness` 单文件读取 | 200KB | 截断 |
| `ai_remediate` 临时副本 | 256MB（`--max-copy-mb`） | 超出放弃量化 |
| `rn_build_symbols` 打包 stderr | 末 2000 字符 | 截断 |
| `apm_baseline` 置换迭代 | 10000 次 | 固定，CPU 成本有界 |
| `rn-apm` 环形缓冲 | 见 SDK 源码 | 满了丢最旧 |
| `ios-apm` 环形缓冲 | 见 SDK 源码 | 满了丢最旧 |

**注意 `LogCollector` 的处理方式**：溢出不是"丢掉就行"，
而是**把该样本标记为 invalid**。因为一个溢出的样本意味着
**这段日志不完整**，用它算出来的数字是假的。宁可少一个样本，也不能要一个假的。

### 一个已知的不彻底处

`apm_white_screen.read_png_pixels()` 先 `path.read_bytes()` 把整个文件读进内存，
再 `zlib.decompress` 整个 IDAT——**这两处不受 `--sample-step` 约束**。
`--sample-step` 只影响后续的像素采样密度，不影响解码阶段的内存占用。

对一个 4000×3000 的截图，峰值内存与原图同量级。
这是**当前实现的取舍**，不是设计意图。修法见 [`PLAN.md`](PLAN.md) 的 P2-3。

---

## 七、失败模型：退出码是判定，不是错误

这套工具的退出码不是"成功/失败"，而是**可组合的判定**：

| 退出码 | 通用含义 | 具体用法 |
|---|---|---|
| `0` | 判定通过 | 无劣化 / 目标未被挡住 / 测量可信 |
| `1` | **数据或口径有问题** | 判不了。**不是"没差异"** |
| `2` | **可确认劣化 / 目标被挡住 / 测量不可信** | 需要行动 |

**`1` 和 `2` 的区分是这套工具最重要的设计。**

`apm_baseline compare` 的三个出口：

```text
0 = 无劣化
1 = 数据/口径/质量问题  → "无法判定"，需要重测或修测量
2 = 可确认劣化          → 真的变差了，开 issue
```

如果只有 0 和 1，"无法判定"和"没差异"就无法区分——
而这两者的处置方式完全相反。
**把"我不知道"和"我确认没问题"混为一谈，是性能工具最常见也最有害的缺陷。**

同理 `apm_feasibility`：

```text
0 = potentially_reachable    → 可以做单变量实验
1 = 证据不足 / 测量有问题     → 补样本或修测量，**不下结论**
2 = blocked_by_control_floor  → 停止局部优化，升级架构决策
```

以及 `apm_diagnose`：`0` 可用 / `1` 数据问题 / `2` 测量不可信。

**这套语义被写进了技能和子 Agent 的约束文本**，
比如 `perf-guard`：诊断退出 2 时**不得**继续给 CI 写"无劣化"。

---

## 八、证据链：每个数字怎么追到源头

### 8.1 工件布局

```
.apm/
├── baseline/
│   ├── env.json          ← 体检结果（能力锚点）
│   ├── startup.json      ← 启动基线（**必须入库**）
│   └── render.json / memory.json
├── runs/<ISO时间>-<名称>/
│   ├── meta.json         ← 上下文 + 实际执行的命令
│   ├── raw/              ← 逐次原始日志（sample-NN.log 等）
│   ├── metrics.json      ← 指标 + 逐次 observations
│   ├── diagnosis.json    ← 方差诊断报告
│   ├── status.json       ← state + exitCode
│   └── report.md
├── issues/ISSUE-NNN.json ← 问题单（含证据路径与置信度）
└── state.json            ← 闭环状态（Phase / 在办 issue）
```

**`.apm/runs/` 不入库，`.apm/baseline/` 必须入库**——原始测量产物量大且可重采，
基线是判定的锚点，不入库等于没有锚点。

### 8.2 谁产生谁消费

```
apm_measure ──▶ metrics.json ──┐
     │                          ├──▶ apm_diagnose ──▶ diagnosis.json ──┐
     └─ raw/                     │                                     │
                                ▼                                     ▼
                     apm_baseline compare ◀── baseline.json    verdict + exitCode
                                │
                                ▼
                     apm_feasibility check ──▶ 目标可达性判定
```

**每一个箭头的产物都是文件**，不是内存里的临时值。
所以任何一次判定都能被独立重放：拿着 `metrics.json` 和 `baseline.json`，
今天跑 `apm_baseline compare` 得到的结论和三个月前一样——
因为置换检验的随机种子是固定的 42。

### 8.3 issue 的证据必须带路径

`ISSUE-NNN.json` 里存的是**证据的路径**，不是结论的复述：

```json
{
  "id": "ISSUE-FUNC-001",
  "evidence": ["DuiyiTests/TaskStoreTests.swift", ".apm/issues/..."],
  "root_cause_hypothesis": "...",
  "confidence": "..."
}
```

这样第三方可以自己去核对证据，而不是相信 issue 里的描述。

---

## 九、"取不到"必须说"取不到"

项目里有一条容易违反但极重要的约定：

```swift
// ✅ 取不到返回 -1，调用方能区分"没有数据"与"数据是 0"
func premainMillis() -> Int

// ❌ 返回 0 会被误读成"pre-main 极快" —— 这是危险的假数据
```

为什么 0 比 -1 危险？因为 **0 是一个合法的测量值**。
`premainMillis() == 0` 意味着"pre-main 耗时 0 毫秒"，
而这在物理上几乎不可能（进程创建到入口至少要几毫秒）。
所以看到 0 的读者会**倾向于相信它**，而看到 -1 的读者会去查。

`apm_measure.py` 里的对应实现：

```text
premainSource:
  "early_marker"    ← 显式 EARLY pre-main 值
  "iosapm_first_log" ← 取本次启动最先出现的值（绝不拿 activate 时刻冒充 C 时刻）
  "unavailable"     ← 两者都没有 → premainMs = None，且该样本记 error
```

**注意 `unavailable` 时不是"跳过这段"，而是让整个样本失败。**
因为 pre-main 缺失意味着这次启动的日志不完整，用它算总耗时也不可信。

---

## 十、可复现性原理

项目里有三处刻意的可复现设计：

### 10.1 固定随机种子

`apm_baseline.RNG_SEED = 42`。置换检验是随机的，
但**同一对数据必须永远得到同一个 p 值**，否则"结论带来源"就无从谈起。

### 10.2 用内容哈希检测漂移，不用时间戳

`tools/build-diagrams.sh` 的注释写明了原因：

> ⚠️ 为什么不用文件修改时间判断：**git checkout 会让所有文件拿到相同的 mtime**，
> 基于 mtime 的检查在全新 clone 里会误报。所以用 SVG 的内容哈希。

项目里一共有三处"一致性"机制，都用**内容级**判定：

| 机制 | 判定方式 | 位置 |
|---|---|---|
| `dist/` vs `plugins/` | `diff -r` 目录树全量比对 | `make verify-portable` |
| PNG vs SVG | SHA-256 内容哈希 | `make verify-diagrams` |
| 幻灯片 vs 证据 | **无自动校验**（只读不验） | `tools/render_demo_slides.py` ⚠️ |

**第三条是已知的缺口**——幻灯片的叙述性文字是硬编码的，
证据更新后不会自动跟随。见 [`PLAN.md`](PLAN.md) 的 P2-1。

### 10.3 生成物单一真源

`plugins/mobile-apm/` 是唯一真源，`dist/` 是 `tools/build-portable.py` 生成的。

**手写两份必然漂移。** 改了 Claude 侧忘了改 opencode 侧，
是这类项目最常见的腐化方式。生成器处理了三个会致命的兼容点：

1. opencode 要求 `name` 与 `description` **同时存在**，缺一个该 skill **完全不加载**
2. `${CLAUDE_PLUGIN_ROOT}` 在 opencode 里是**字面量** → 重写为工程根相对路径
3. hook 机制差异最大（opencode 无 settings hooks）→ 生成桥接插件，
   把 `.claude/hooks/*.sh` 当子进程调，**一份逻辑服务两个 harness**

`build-portable.py` 在生成前会做 6 项硬校验（缺 `name`、缺 `description`、
`description` 超 1024 字符等），并在生成后扫描 `${CLAUDE_*}` 残留——
**残留即硬失败**，因为那是唯一能真正让 opencode 失效的问题。

---

## 十一、明确放弃的东西

知道一个系统**不做什么**，和知道它做什么一样重要。

| 放弃的 | 原因 |
|---|---|
| Python 工具的第三方依赖 | 工具必须能在任何机器上直接跑；`pip install` 是部署障碍 |
| 自动分层 / 自动校正样本 | 实测证明 `pre-main` 与总耗时只是巧合相关；自动分层会**制造**看起来更好的数字 |
| 正式模态检验 | 启发式够用且诚实；声称"这是正式模态检验"是过度承诺 |
| "优化建议"式输出 | `apm_diagnose` 的 `disclaimer` 明确写"建议不是性能结论"——它给的是**控制实验假设**，不是"你该改这里" |
| 让模型现写解析逻辑 | 见 §一 |
| 假装支持未验证的平台 | Android / 鸿蒙的测量 profile **未实现**，工具会明确报"不可用" |
| 0 字节 .map 当作错误 | Hermes bundle 不是合法 JS，`sentry-cli` 会创建 0 字节占位文件——**这是正常的**；但如果**所有** .map 都是 0 字节，说明 sourcemap 根本没生成 |

---

## 相关文档

| | |
|---|---|
| [`AGENTS.md`](../AGENTS.md) | 五条铁律的条文版本 + 坑与禁区 |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | 分层形状与数据流 |
| [`PLAYBOOK.md`](PLAYBOOK.md) | 实现方法：每类任务怎么走 |
| [`IMPLEMENTATION.md`](IMPLEMENTATION.md) | 逐脚本的设计细节 |
| [`usage-guide.md`](usage-guide.md) | 逐脚本的命令与参数 |
| [`ROADMAP.md`](../ROADMAP.md) | 实践路线图 |
| [`PLAN.md`](PLAN.md) | 待办优先级计划表 |
