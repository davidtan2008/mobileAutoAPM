# 可行性与对照组协议

> **在优化之前先回答：这个目标有没有可能达到？**
>
> T1 的教训是：先把冷启动压到 200ms，做完一轮才发现空壳 App 的地板已经是 230ms。
> 没有前置可行性判断，Agent 会把大量时间花在不可能改变结果的地方。

## 一、什么时候必须先做

以下任一条件成立时，**不得直接进入局部优化**：

- 目标是一个绝对数字（例如“冷启动 ≤200ms”）；
- 目标与现有基线差距小于测量噪声；
- 之前已经换过 UI 内容、初始化顺序或埋点，仍没有可测收益；
- 目标需要改变业务行为、框架层或第三方 SDK；
- 团队没有当前设备/构建类型/命令下的可比基线。

## 二、最小对照组（control）是什么

对照组不是“再跑一次同一个 App”。它必须只改变一个变量：

- 保留完整生命周期、初始化路径和测点；
- 保留同一设备、系统、构建类型、命令、warmup 和样本数；
- 把业务内容替换为最小可运行内容（例如 `Text("x")` 或空白根视图）；
- 不删除埋点、不改变起止点、不换测量 profile；
- 单独保存 control 的逐次样本与 `metrics.json`。

如果对照组和候选组的 `measurementSignature` 不一致，结论无效。

## 三、标准命令

```bash
S=".claude/skills/_apm/scripts"

# 1. 先生成实验计划
python3 "${S}/apm_feasibility.py" plan \
  --metric startup.cold.first_frame --target 200 --json

# 2. 采集最小 control 与当前 candidate 后做判断
python3 "${S}/apm_feasibility.py" check \
  --control .apm/runs/<control>/metrics.json \
  --candidate .apm/runs/<candidate>/metrics.json \
  --metric startup.cold.first_frame --target 200 --json
```

`check` 的退出码：

| 退出码 | 状态 | 动作 |
|---:|---|---|
| 0 | `potentially_reachable` | 可以进入单变量实验 |
| 1 | `indeterminate_*` / 数据问题 | 增加 control 样本或先修测量，不下结论 |
| 2 | `blocked_by_control_*` | 停止局部优化，升级为架构/需求决策 |

## 四、如何解释 control 地板

对 `lower_is_better` 指标：

- `control.p50` 是实测地板；
- `conservativeFloor = p50 + 2 × stdev` 是考虑测量抖动的保守边界；
- 目标低于 `control.p50`：`blocked_by_control_floor`；
- 目标落在 `p50` 与 `conservativeFloor` 之间：`indeterminate`；
- 目标高于保守地板：才允许进入局部优化实验。

这不是在证明“目标一定可达”，只是排除一个已经被实测地板挡住的假设。

## 五、什么时候必须升级给人决策

满足任一项就停止编码，提交 issue 并等待确认：

1. control 地板已经高于目标；
2. 需要替换/新增第三方 SDK 或后端；
3. 需要修改业务逻辑或产品行为；
4. 需要更换框架、生命周期架构或重新设计启动流程；
5. 连续 3 次单变量实验都没有超过测量噪声；
6. 只能通过改变对照组口径才能得到“改善”。

## 六、T1 的实际应用

T1 的空壳 App 对照测得约 230ms，而目标是 200ms。正确结论不是“再优化一轮”，而是：

```text
blocked_by_control_floor
→ 停止局部优化
→ 报告架构级不可达
→ 等待是否接受改目标或换架构
```

这正是 `apm_feasibility.py` 要自动化的判断。
