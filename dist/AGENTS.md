# 移动端 APM 工程规范

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

## 数据平面

```bash
S=.claude/skills/_apm/scripts
python3 $S/apm_doctor.py                # 能力体检
python3 $S/apm_baseline.py compare --baseline .apm/baseline/X.json --run <run>.json
python3 $S/apm_white_screen.py shot.png # 白屏检测
```
`apm_baseline.py compare` 退出码：`0` 无劣化 / `2` **有劣化** / `1` 数据问题。
**退出码 2 可直接作为 CI 门禁。**

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

<!-- 生成统计：7 skills / 4 agents / 4 commands -->
