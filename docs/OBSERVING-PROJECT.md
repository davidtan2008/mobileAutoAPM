# 被观测工程侧的 `.apm/` 目录（OBSERVING-PROJECT）

> **目录属于被观测工程，不属于本仓库。** 它是 apm-loop 在目标工程里留下的
> 可复核证据库（commit / 命令 / 样本 / 结论），让"三个月后还想查为什么"成为可能。
> 本文按真实实例（`realtimeTranslatorOptimize/.apm/`）归纳约定；新增目录条类前先对表。

## 1. 顶层约定

| 路径 | 存什么 | 谁读 |
|---|---|---|
| `baseline/` | 正式基线（详见 §2） | `apm_baseline compare`、每次闭环开工 |
| `runs/<id>/` | 每次 run 的完整落档（详见 §3） | `apm_diagnose`、人 |
| `issues/` | 结构化问题记录 `ISSUE-NNN-*.json`（§4） | Agent 工作台、人 |
| `feasibility/` | `apm_feasibility` 判定产物（§5） | 决策翻转点 |
| `white-screen/` | 白屏检测截图与 README | 人 |
| `tools/` | 目标工程内的辅助测量工具（逐个说明用途） | 人 |
| `report.md`（根） | 本次演练/任务的总报告（人类可读） | 人 |
| `state.json`（可选） | apm-loop 当前 Phase / 当前 issue 状态 | **每次开工先读** |

## 2. `baseline/` —— 命名即口径

- 过程基线：`startup-p0-working-tree.json`（dirty / 临时）→ **只能参考，不得用于判定**
- 正式基线：`startup-clean-<commit>-warmup<n>-n<k>.json`（clean / 暖机 / n 样本）
- **`baseline/README.md` 是淘汰法庭**：每个被拒 baseline 的原因（run B 多簇 368–413ms、
  post-reboot provisional 等）与现存依据（C/D 跨 run `consistent`）都要写字留档。
  没有 README 的 baseline 目录等于没有资格被叫"正式"

## 3. `runs/<id>/` —— 单次 run 的档案

目录名建议 `<ISO 日期时间>-<指标面>-<条件>`（实例 `20260926-clean-2b3a1ea-warmup3-n20-candidate`、
`control-clean-2b3a1ea-warmup3-n20`、`opt-defer-state`）。

约定文件（`skills/apm-loop/SKILL.md` 工件布局为准）：

```
metrics.json     规范化指标（契约见 [DATA-MODEL.md](DATA-MODEL.md) §1）
diagnosis.json   方差/多簇/跨 run 相关性与判据建议（§2）
meta.json        commit / 设备 / 构建类型 / 命令 / 环境
raw/             原始 trace、日志、截图
status.json      完整/不完整/测量不可信
report.md        人类可读结论（可选，重要 run 必留）
```

- **整 run 拒绝不等于删 run**：多簇/高 CV 的 run 原样保留（名字里可见其身份，
  如 `-b`/`-repeat`），作为"拒绝为什么"的证据链
- 同 commit 跨 run 对比时，相关 run 必须在同一日期段生成的 `diagnosis-cross-run*.json` 可查

## 4. `issues/` —— `ISSUE-NNN-*.json` 规范

文件命名：`ISSUE-<NNN>-<短slug>.json`（NNN 工程内递增，不复用——即使 issue 被否定）。
字段以真实样例 `ISSUE-001-target-unreachable.json` 为纲：

| 字段 | 语义 | 约束 |
|---|---|---|
| `id` / `type` / `title` | `ISSUE-001` / 主题分类 / 一句话结论 | type 是自述性标签（feasibility 等），不引入全国性枚举 |
| `status` | `confirmed` / `provisional` / `retracted` … | 状态演进改 status，**不删文件** |
| `severity` / `confidence` | 高/中/低 | confidence 影响是否升级问人 |
| `evidence.method` | **做了什么实验、怎么对照** | 必须可复述（换 `Text("x")` 实验即范例） |
| `evidence.minimalBody` / `evidence.fullApp` | 对照双方的关键数字 | 数字要写区间或原始样本，不只给中位数 |
| `root_cause` / `implication` | 机理结论 / 对需求的含义 | 写到"可决策"为止（如"属架构级改动"） |
| `addressable` | 哪部分还有得优化（可量化） | 与 `feasibility` 判定互斥之处要说明 |
| `next_steps[]` | 可执行下一步（含"问人"选项） | 不许空 |

**命名后缀还可复合来源**：实测出现过 `ISSUE-P0-002-cross-run-shift.json`
（测量类问题挂到平台缺口编号上）与 `ISSUE-FUNC-001`、`ISSUE-UI-001`（功能/UI 类）。
复合前缀规则：`ISSUE-<类别>-<NNN>`；纯候选问题用 `ISSUE-<NNN>`。

## 5. `feasibility/` —— 目标可达性判定

文件名 `<日期>-<指标>-<目标>.json`（实例 `20260926-startup-200ms.json`）。核心字段：

```
status        blocked_by_control_floor / indeterminate_within_control_margin /
              potentially_reachable
decision      stop_and_escalate_architecture ...
control{}     对照组：路径（注明 worktree 来源与 change 内容）、n、p50、cv、diagnosis
candidate{}   本次全量：路径、sourceCommit、n、p50、cv
notClaimed[]  明示"没有声称"什么（防误读清单，这个字段不许删）
artifacts{}   controlMetrics / candidateMetrics / feasibilityCommand
```

**这些文件的意义**：目标"冷启动 ≤200ms"被 210ms 的实测对照组挡住，
`stop_and_escalate_architecture` 不是失败而是**给用户的决策输入**。

## 6. 入库与否

`.apm/` 建议作为结构化证据**入库**（目标工程实际如此处理），exceptions：

- 机器/会话瞬时文件（state.json、venv、大体积 raw）可 gitignore
- 只要不删证据，取舍由被观测工程决定；在本仓库侧**不要**为 `.apm/` 建 out-of-band 索引

## 7. 与本仓库的关系

- 本仓库 `plugins/mobile-apm/` 里的技能/脚本**生成**这些结构，但**不读取**它来做任何本仓库决策
- 平台自身的问题（对被观测工程而言的 ISSUE-P0-*）最后要**回流**成
  [`../PLAN.md`](PLAN.md) 的条目——闭环的闭环
