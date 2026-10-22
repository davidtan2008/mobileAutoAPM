# 实现方法（PLAYBOOK · 按任务类型的操作手册）

> 分工：**为什么**读 [`PRINCIPLES.md`](PRINCIPLES.md)，**做什么/先做什么**读 [`PLAN.md`](PLAN.md)，
> 命令速查读 [`usage-guide.md`](usage-guide.md)，数据格式读 [`DATA-MODEL.md`](DATA-MODEL.md)。
> 本文回答：**动手时按什么步骤、每步产出什么、什么情况下必须停**。

所有脚本路径一律显式写 `plugins/mobile-apm/scripts/`（下文称 `$S`）。被观测工程侧目录约定见
[`OBSERVING-PROJECT.md`](OBSERVING-PROJECT.md)。

---

## 任务一：对被观测项目做 APM 闭环（核心任务）

编排逻辑在 `skills/apm-loop/SKILL.md`，闭环为：

```
Phase 0 体检 → Phase 1 基线 → Phase 1.5 可行性 → Phase 2 发现
→ Phase 3 定位 → Phase 4 修复 → Phase 5 验证 → Phase 6 防劣化
                              └── 回归则回到 Phase 3 ──┘
```

### Phase 0 · 体检（每次会话第一次涉及性能任务必做）

```bash
python3 $S/apm_doctor.py
```

产出：本机"现在真的能做什么"（iOS/Android/鸿蒙/渲染/内存/崩溃/UI 自动化/RN 各就绪位）。
**不许跳过**——它决定后面所有口子上限。某能力缺失如实时话，如实写入报告，不许写"假设可用"。

### Phase 1 · 建立正式 baseline（没有这步不许谈优化）

```bash
python3 $S/apm_measure.py \
  --profile ios-native-startup --device <UDID> --package-id <bundleId> \
  --build-type Release --build-path <绝对路径>/App.app \
  --project-root <被观测工程> --output .apm/runs/<本次>
python3 $S/apm_diagnose.py <本次 run 目录> --metric startup.cold.first_frame
```

**合格 baseline 的四个必要条件**（T1 演练的经验，缺一不算）：

1. clean 工作树 + Release 构建（dirty 树的 provisional baseline 只能参考）
2. warmup≥3 次，n≥10
3. `apm_diagnose` 退出码 0（**2 = 测量不可信/样本不完整，整轮不用**）
4. 跨 run 一致：独立重复一两个 run，p50 漂移在阈值内；多峰 run **整轮拒绝并留档**，
   绝不"再测几次挑一次"——被拒绝的 run 就地命名保留，这是证据不是垃圾

判据是多峰/高 CV 时**不要优化，先改测分解指标**（逐段 timing/addressable_segments）——
总耗时指标方差往往比优化幅度大，T1 的教训正是这里。

### Phase 1.5 · 可行性前置判断（绝对目标必做）

```bash
python3 $S/apm_feasibility.py plan --metric <指标> --target <值>   # 出对照组方法论
python3 $S/apm_feasibility.py check --control <对照>/metrics.json \
  --candidate <本次>/metrics.json --metric <指标> --target <值>
```

三分判定（详见 `references/feasibility-protocol.md`）：

```
target <  control.p50        → blocked_by_control_floor        物理上不可能，停
target <  conservativeFloor  → indeterminate_within_control_margin  测不出，升级口径
target ≥  conservativeFloor  → potentially_reachable           进入优化
```

**对照组做法**：把业务内容换掉（如 `HomeView` 换 `Text("x")`），生命周期/埋点/设备/口径全保留。
这是 T1 唯一一次把"目标不可达"变成**实测事实**而不是感觉的动作。

### Phase 2 · 发现 → Phase 3 · 定位

- 全维度异常：`apm_diagnose.py`（多峰检测 / 分段 CV / 同 commit 跨 run 漂移 / 可疑变量提示）
- 崩溃/JS 堆栈：RN 走 `rn_symbolicate.py compose` + `symbolicate`（Hermes 两步合成，见
  `docs/symbolication-pipeline.md`）；符号文件门禁 `rn_build_symbols.py verify --strict`
- 白屏：`apm_screenshot.py --device <UDID> --output .apm/white-screen/shot.png` →
  `apm_white_screen.py shot.png`
- 定位结论写入 `.apm/issues/ISSUE-NNN-*.json`（schema 见 `OBSERVING-PROJECT.md`）

### Phase 4 · 修复

- **单变量**：一次只改一处。改哪段、为什么，先写进 issue 文件再动手
- 不确定收益的改动优先考虑"先做红测"：证明当前测量能复现问题（译文持久化闭环就是这么做的）
- 修复追求**可回退**：无收益按铁律退

### Phase 5 · 验证

**成败只看这一条：用与 Phase 1 完全相同的口径重测**（同设备/同构建/同命令/同 measurement fingerprint）。

```bash
python3 $S/apm_baseline.py compare \
  --baseline .apm/baseline/<正式>.json --run <本次 run>/metrics.json
```

三分判定（退出码）：

| 退出码 | 含义 | 动作 |
|---|---|---|
| 0 | 无显著劣化（含显著改善与无显著变化） | 按报告表述提交 |
| 2 | 可确认劣化 | 回到 Phase 3 |
| 1 | 数据/口径/质量问题 | **不许下结论**，回到 Phase 1 修测量 |

**p>0.05 / 无显著变化 ≠ 失败也不许继续加码**：T1 里 126→132ms p=0.694 的改动按设计生效了
但无可测收益，结论是**回退**，不是再试一次。

### Phase 6 · 防劣化

通过判定的 run 升级为正式 baseline（`.apm/baseline/`），并在仓库里留下 compare 约定。
之后任何变更可复用 Phase 5 的三分判定作为门禁。

---

## 任务二：项目 AI 化改造（支柱 A）

```bash
python3 $S/ai_readiness.py --path <项目>          # 扫（只读，分数 + 明细）
python3 $S/ai_remediate.py plan --path <项目>     # 改造计划（只出计划不写目标工程）
python3 $S/ai_remediate.py loop --path <项目>     # 计划 → 生成 → 人审 → apply → 复扫（量化）
```

- 生成物**只进 staging 目录**；`apply` 需显式调用且**拒绝覆盖已存在文件**——人审环节不可绕过
- 事实位置一律 `TODO(需人工填写)`，不编造（如 Team ID）
- 闭环的量化口径：改前改后分数对比（真机实测案例：68 → 86，见 ROADMAP）

---

## 任务三：新增/修改测量口径

1. 在 `plugins/mobile-apm/references/metric-spec.md`（口径真源）登记：字段名、单位、direction、
   聚合口径、`higher_is_better` 与否
2. Python 侧与 SDK 侧**指标名必须一致**（现状未全对齐，改哪个先看 [`PLAN.md`](PLAN.md) 对应条目）
3. 配最小回归测试（Python `make test-py`；SDK 改动配两端各自测试）
4. 计算/判定逻辑改动 → 用 `.apm/` 归档的真实数据做输入过一遍，证明判定不变位

---

## 任务四：改插件本体（脚本 / 技能 / 文档）

```bash
# 只改 plugins/mobile-apm/**，然后:
make build-portable        # 重新生成 dist/
make verify-portable       # 确认 dist 可用、无 CLAUDE_PLUGIN_ROOT 残留
make test                  # 三套测试全绿
```

- `dist/` 是生成物，**不要手改**——CI 会因不一致失败
- Python 脚本零第三方依赖，Python 3.9+；失败分支显式"不可用"，不许返回值语义像正常数据的默认值（如 pre-main 取不到返回 -1 而不是 0）

## 任务五：改 SDK（rn-apm / ios-apm）

| 改动位置 | 测试 | 关约 |
|---|---|---|
| `rn-apm/**` | `cd rn-apm && npm test`（node --test，76 个） | 埋点/缓冲必须有界；`ErrorUtils.setGlobalHandler` 必须链式调用 |
| `ios-apm/**` | `cd ios-apm && swift test`（12 个） | 取不到数据返回 -1/显式值；不给 firstFrame 上提早点 |

新增能力先写回归测试再改实现（现在的负斜率标签、`reportJsError` 接线都是这么修的）。

---

## 什么时候必须停（不许自作主张，停下问人）

详细信息见 `skills/apm-loop/SKILL.md` 的「停止条件」。摘要：

1. feasibility 判定 `blocked_by_control_floor` / `indeterminate_within_control_margin` →
   停止局部优化，升级为架构/需求决策
2. 达不成目标时**不许改目标自我安慰**——除非改动有对应证据（如把 200 改成最终 260 的建议要带"为什么"）
3. 需要未经授权的破坏性操作（改 CI 门禁、动签名、删 baseline）
4. 测量口径对不上且数据不足以归因

## 提交前的验证清单（按改动类型）

| 改了什么 | 必须跑 |
|---|---|
| 任何代码 | `make test`（= test-py + rn + ios 三套） |
| Python 脚本 | `make lint test-py` |
| `plugins/**` | 加 `make build-portable verify-portable` |
| 图（`tools/build-diagrams.sh`） | `make build-diagrams verify-diagrams` |
| 涉及性能数字 | **命令 + 设备 + 样本量 + 显著性**，缺一不可 |
