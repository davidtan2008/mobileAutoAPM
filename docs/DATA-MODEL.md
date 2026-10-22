# 数据模型（DATA-MODEL · 三份 JSON 契约）

> APM 工具链围绕三份 JSON 工作。**改任何生成或解析它们的代码前，先对表。**
> 真源是脚本本体（`plugins/mobile-apm/scripts/`）与真实样本
> （被观测工程 `.apm/runs/**`）；本文是索引与语义，不重复逐字段定义。
> 字段集会演进——以 `schemaVersion` 为准。

## 1. `runs/<id>/metrics.json`（run 产物 = baseline 本体）

一次真机测量的完整落档。`apm_baseline` 的正式 baseline 文件与它**同构**（同一脚本生成），
差别只在内容判级：baseline 是通过质量闸门的 run。

```
context            口径指纹（约 27 键）——判级靠它，见 §1.1
metrics[]          指标声明行数组（ios-native-startup 共 16 项）
observations[]     每次启动观测一条（n = 样本数）
```

### 1.1 context —— 口径指纹（为什么 compare 敢直接比）

两层都在时 `apm_baseline compare` 才会去做显著性判定；口径不同直接拒绝并给出原因。

| 键 | 含义 |
|---|---|
| `profile` / `profileVersion` | 测量 profile 名与版本（如 `ios-native-startup`） |
| `toolVersion` | 生成脚本的版本 |
| `device` / `deviceInfo` | UDID 字符串 + 13 键设备详情（真实型号/OS build/transport 等） |
| `build` / `buildInfo` | 构建口径：`type`（Release/Debug）、`appPath`、`packageId`、`debugId` |
| `command` / `measurementMethod` | 实际执行的命令与测法（如 sysctl 基准 + 快照轮询） |
| `startDefinition` / `endDefinition` | 起点与终点定义（`first_frame` 口径，不是泛泛的"启动时间"） |

### 1.2 metrics[] —— 指标声明（聚合数据的"产品形态"）

```json
{ "name": "startup.cold.first_frame", "unit": "ms",
  "direction": "lower_is_better",
  "samples": [202, 211, ...],        # 全量原始样本，n=20
  "minEffect": 30.0 }                # 该指标有意义的最低变化幅度（判显著性用）
```

要点：
- **samples 放原始值不放只放 p50** —— 显著性检验用原始样本算，p50 只是 CLI 展示
- `minEffect` 逐指标各自声明；噪声带 = 合并标准差 ×2 与 minEffect 的关系见
  [`PRINCIPLES.md`](PRINCIPLES.md) §统计判定
- ios-native-startup 的 16 项 = `startup.cold.first_frame` + `startup.premain` +
  `startup.process_to_first_mark` + `startup.stage.*`（13 个分段时间）
- **SDK 指标名与 Python 的映射未完全对齐**（如 SDK 侧曾写 `startup.cold.total`，
  Python 侧从来只有 `startup.cold.first_frame`）——对齐前别互相消费对方的 JSON

### 1.3 observations[] —— 每次启动的原始观测（拆解证据所在）

```json
{ "index": 1, "startedAt": "...", "status": "valid",   // status 非 valid 会整条进 errors 途径
  "firstFrameMs": 202, "premainMs": 10,
  "premainSource": "early_marker", "premainCandidatesMs": [10],
  "stages": [ { "name": "processStart", "sinceMs": 13, "deltaMs": 13 }, ... ],
  "accountedTimeMs": ..., "unattributedResidualMs": ..., "closureToleranceMs": ...,
  "rawLog": "...", "errors": [], "warnings": [] }
```

要点：
- `stages[].sinceMs/deltaMs` 是 `apm_diagnose` 分段分析的原料（可优化段来自这里）
- `premainMs` 取不到时是 **`null` + `premainSource: "unavailable"`**（显式缺席，不是 0）；
  iOS SDK 侧的同类约定是返回 `-1`——**返回 0 会被误读成 pre-main 极快，属于危险假数据**
- `unattributedResidualMs` 超 `closureToleranceMs` 说明观测有缺段，诊断会计入告警

## 2. `runs/<id>/diagnosis.json`（诊断产物）

`apm_diagnose` 的输出。结构 ≈「能不能用 + 为什么 + 接下来该怎么做」：

```
schemaVersion / tool / generatedAt
focusMetric                     本次聚焦的指标
minSamples / thresholds{4}      usableCvBelow / unusableCvAbove /
                                multimodalSeparationRatio / multimodalGapShare
runs[]                          各 run 的逐项统计
contextWarnings                 口径不一致、运行环境可疑等（有义名单）
crossRunCorrelations / crossRunFocus        同 commit 跨 run 漂移检测
effectAssessment                可评估时才有值
stageCandidates                 定位建议（哪些段值得看）
verdict{
  usable, exitCode, reasons[], suggestions[]   ← 人类阅读的第一站
}
disclaimer                      明示"诊断不是判定，判定在 compare"
```

**退出码语义（消费方必须尊重）**：`0` 可信；`2` 测量不可信/样本不完整——
**对应 run 不得用于任何判定**。

## 3. `baseline` 文件（与 run metrics.json 同构）

```
.apm/baseline/startup-clean-2b3a1ea-warmup3-n10.json
```

- 命名约定把口径写进文件名：`<指标面>-<commit>-warmup<n>-n<样本>`
- **谁的证据强谁上**：对比失败（B 多簇整轮拒绝）、C/D 跨 run `consistent` 后才升级正式
- baseline 的可比性由 §1.1 的 context 保护；`apm_baseline compare` 遇到口径不一致
  退出码 1 并点名差异字段——**不许为结果改口径再比**

## 4. 判定的完整数据流（谁消费谁）

```
apm_measure ──▶ metrics.json ─▶ apm_diagnose ─▶ diagnosis.json ─┐
                                                               ├─▶ apm_feasibility / apm_baseline compare ─▶ 三分判定
                     baseline.json ◀── 升级自通过闸门的 run ────┘
```

- `apm_feasibility` 输出文件约定放 `.apm/feasibility/<日期>-<指标>-<目标>.json`
  （字段含 status / control / candidate / decision / notClaimed / artifacts，
  样例见被观测工程 `.apm/feasibility/20260926-startup-200ms.json`）
- `apm_baseline compare` 退出码：`0` 无显著劣化 / `2` 可确认劣化 / `1` 数据·口径·质量问题
- 所有产物路径约定与 `ISSUE-NNN-*.json` 规范见 [`OBSERVING-PROJECT.md`](OBSERVING-PROJECT.md)

## 5. 改这些契约的规矩

1. **先改 spec 再改代码**：字段增删在 `references/metric-spec.md` 登记 + 本文同步
2. **schemaVersion 必须递增**（消费方按版本兼容，不许静默改形）
3. 新字段要**有真实数据过闸**：用 `.apm/` 归档样本回归（不能只造 fixture）
4. 改动配回测试；脚本零第三方依赖的约定不变
