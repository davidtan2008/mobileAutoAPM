# 能力清单与使用指南

> 本文回答两个问题：**这套工具能做什么**、**怎么用**。
> 所有命令都可直接复制执行；所有数字都标注了来源与验证强度。

---

## 0. 三十秒上手

```bash
# 1. 装到任意 AI Coding Agent（opencode / Claude Code / …）
#    dist/ 就是可移植产物，解压即用
cp -R dist ~/.config/opencode/plugins/mobile-apm     # opencode
cp -R dist ~/.claude/plugins/mobile-apm               # Claude Code

# 2. 体检：先确认本机真能做什么
make doctor

# 3. 按需求挑能力（本文第 2 节有对照表）
python3 scripts/apm_feasibility.py plan --metric startup.cold.first_frame --target 200
```

**贯穿一切的铁律**：无基线不优化 · 单变量 · 必须复测 · 绝不伪造 · 结论带来源。
详见 [`AGENTS.md`](../AGENTS.md)。

---

## 1. 它由什么组成

| 层 | 数量 | 说明 |
|---|---|---|
| **数据平面脚本** | 11 | 零第三方依赖，纯标准库。确定性计算都交给它们，不让模型现写 |
| **技能（Skill）** | 7 | 场景化工作流。agent 按描述自动选用 |
| **子 Agent** | 4 | 专职角色：profiler / reviewer / crash-triager / perf-guard |
| **命令** | 4 | `/init` `/check` `/verify` `/crash` |
| **知识库** | 7 篇 | 指标定义、选型硬限制、测量协议、可行性协议等 |
| **跨 agent 产物** | `dist/` | 由 `tools/build-portable.py` 从 `plugins/` 生成，**勿手改** |

```
plugins/mobile-apm/   ★ 单一真源
├── scripts/          11 个数据平面脚本
├── skills/           7 个技能
├── agents/           4 个子 Agent
├── commands/         4 个命令
└── references/       知识库
dist/                 生成物（make build-portable）
```

---

## 2. 我想做的事 → 用哪个

| 你想做的事 | 用这个 | 验证强度 |
|---|---|---|
| 冷启动耗时测量 | `apm_measure.py` | ✅ iPhone 13 真机，clean commit 正式 baseline |
| 判断这批数据能不能用 | `apm_diagnose.py` | ✅ 多峰/分段 CV/跨 run 漂移 |
| 判断改动到底有没有效果 | `apm_baseline.py` | ✅ 置换检验，实测校准过 |
| **动手前先判断目标可达吗** | `apm_feasibility.py` | ✅ 真实 control 实验拦截过 200ms 目标 |
| 截图 / 白屏检测 | `apm_screenshot.py` + `apm_white_screen.py` | ✅ iPhone 13 真机 DVT 截图 |
| RN 崩溃堆栈还原源码 | `rn_symbolicate.py` | ✅ 真实 Hermes 堆栈 20/20 帧 |
| 发版前检查符号文件齐不齐 | `rn_build_symbols.py verify` | ✅ 缺 dSYM 退出码 2 |
| 把遗留项目改造成 AI 友好 | `ai_readiness.py` + `ai_remediate.py` | ✅ 实测 68 → 86 |
| 查本机工具链够不够 | `apm_doctor.py` | ✅ 11 项能力矩阵 |
| 端到端跑一个性能/稳定性问题 | 技能 `apm-loop` | ✅ 1 次成功 + 1 次诚实失败 |

---

## 3. 数据平面脚本详解

### 3.1 `apm_doctor.py` —— 先体检，再动手

**能做什么**：探测本机 iOS / React Native / Android / 鸿蒙 工具链，输出**能力就绪度**而不是工具清单。

```bash
python3 scripts/apm_doctor.py                    # 人读
python3 scripts/apm_doctor.py --platform ios --json
```

**输出示例（实测）**：

```
基础能力 10/11；配置 APM_PYMOBILEDEVICE3_BIN 后 11/11
```

> 「iOS 真机截图」依赖可选的 `pymobiledevice3`。**缺失时报 unavailable，不静默降级。**

---

### 3.2 `apm_measure.py` —— 标准测量

**能做什么**：按统一口径采集指标，产出 `metrics.json` + 逐次 observations + 原始日志。

```bash
python3 scripts/apm_measure.py \
  --profile ios-native-startup \
  --device <CORE_DEVICE_ID> \
  --package-id <BUNDLE_ID> \
  --build-type Release \
  --build-path <绝对路径>/App.app \
  --project-root . \
  --warmup-launches 3 \
  --samples 10 \
  --output .apm/runs/<本次>-launch
```

**当前只承诺 `ios-native-startup`**。Android / 鸿蒙 / RN 适配器未落地时**不会**被写成可用。

**硬校验**（不满足直接失败，不给假数据）：

- 必须是**物理设备**（模拟器冒充真机 → 拒绝）
- bundle id 与 `.app` 内嵌的必须一致
- 设备必须 `connected` 且已解锁
- 阶段日志必须闭合校验（防"首帧"被低估）

---

### 3.3 `apm_diagnose.py` —— 这批数据能用吗

**能做什么**：CV、疑似多簇、分段 CV 对比、可疑变量相关性、同 commit 跨 run 漂移。

```bash
python3 scripts/apm_diagnose.py .apm/runs/<run> --metric startup.cold.first_frame
python3 scripts/apm_diagnose.py <runA> <runB> --metric startup.cold.first_frame   # 跨 run
```

**退出码**：`0` 可用 · `1` 样本不足 · `2` 高方差/多簇/漂移（**修测量，不许进入优化**）

> 关键设计：**不自动分层、不挑簇、不丢弃异常 run**。
> 实测有一次 run 因多簇被整轮拒绝（CV=21.5%），前 3 次 368–413ms 全部保留在案。

---

### 3.4 `apm_baseline.py` —— 改动到底有没有效果

**能做什么**：置换检验判显著性（不依赖正态假设），固定随机种子保证可复现。

```bash
# 记录基线（可拒绝不可信数据）
python3 scripts/apm_baseline.py record --in run/metrics.json \
  --out .apm/baseline/startup.json --require-healthy

# 对比
python3 scripts/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run run/metrics.json --json
```

**退出码**：`0` 无劣化 · `1` 数据/口径/质量问题 · `2` 可确认劣化

**实测校准**：清晰改善 p=0.0088；**−4.5% 的小改善被正确判为「不具实际意义」**，不会被包装成成果。

---

### 3.5 `apm_feasibility.py` —— 动手前先判断目标可达吗 ⭐

**这是本项目最该先用的脚本。** T1 的教训是「目标不可达却先干了活」。

```bash
# 1) 生成最小对照组实验计划
python3 scripts/apm_feasibility.py plan \
  --metric startup.cold.first_frame --target 200 --json

# 2) 采集 control 与 candidate 后判断
python3 scripts/apm_feasibility.py check \
  --control .apm/runs/<control>/metrics.json \
  --candidate .apm/runs/<candidate>/metrics.json \
  --metric startup.cold.first_frame --target 200 --json
```

**退出码**：

| 码 | 状态 | 该做什么 |
|---:|---|---|
| 0 | `potentially_reachable` | 可以进入单变量实验 |
| 1 | `indeterminate_*` / 数据问题 | 补样本或修测量，**不下结论** |
| 2 | `blocked_by_control_floor` | **停止局部优化**，升级架构/需求决策 |

**真实战绩**：对 iOS 工程跑最小 control（首页内容换成 `Text("x")`，n=20）
得到 p50=210ms，正式拦截「冷启动 ≤200ms」目标 → 退出码 2。

> control 的定义与铁律见 [`references/feasibility-protocol.md`](../plugins/mobile-apm/references/feasibility-protocol.md)。

---

### 3.6 `apm_screenshot.py` / `apm_white_screen.py` —— 截图与白屏

```bash
# 真机截图（可选依赖 pymobiledevice3，走 DVT/CoreDevice 通道）
python3 scripts/apm_screenshot.py \
  --device <UDID> --output .apm/white-screen/shot.png --json

# 白屏 / 黑屏 / 纯色屏检测（纯标准库解 PNG，零依赖）
python3 scripts/apm_white_screen.py .apm/white-screen/shot.png
```

**后端优先级**：`pymobiledevice3` DVT → DVT usbmux → `idevicescreenshot`。
`pymobiledevice3` 不是运行时依赖，没装就明确报 `unavailable`。

**实测**：iPhone 13 / iOS 26.7 生成 1170×2532 PNG；
旧 `idevicescreenshot` 后端在本机报 `Invalid service` 不可用。

---

### 3.7 `rn_symbolicate.py` —— RN 崩溃堆栈还原

```bash
# Hermes 两步合成（顺序不能反）
python3 scripts/rn_symbolicate.py compose \
  --outer main.jsbundle.map --inner metro.map --out composed.map

# 符号化
python3 scripts/rn_symbolicate.py symbolicate --map composed.map --stack crash.txt

# 看 map 基本信息
python3 scripts/rn_symbolicate.py inspect --map composed.map
```

**支持的栈格式**：

```text
at anonymous (address at /abs/app.hbc:1:49386)   ← 真实 Hermes 输出
anonymous@1:132161                                  ← 简写形式
at renderHome (/app/src/App.tsx:10:4)               ← 标准 JS
at foo (native)                                     ← 原生帧（交给 dSYM）
```

**端到端自检**（真实 RN 工具链 + 真实 Hermes 堆栈，4.6 秒）：

```bash
make verify-symbols RN_APP=/path/to/rn-app
```

> 这一步不是形式：修复前真实堆栈 **20/20 帧全部还原失败**，而单元测试全绿。

---

### 3.8 `rn_build_symbols.py` —— 符号文件产出与门禁

```bash
# 产出 bundle + sourcemap（iOS 默认不生成 sourcemap，必须显式传）
python3 scripts/rn_build_symbols.py bundle --platform ios --out dist/js

# 生成符号清单（务必带 --debug-id，版本号在热修后会错位）
python3 scripts/rn_build_symbols.py manifest \
  --platform ios --build-dir <app> --version 1.4.2 --build-number 42 \
  --debug-id <hex> --out symbols.json

# 发版门禁：缺必需符号文件 → 退出码 2
python3 scripts/rn_build_symbols.py verify --platform ios --build-dir <app> --strict
```

**实测**：缺 dSYM → 退出码 2；补齐 → 0。

---

### 3.9 `ai_readiness.py` —— 项目对 AI 友好吗

**能做什么**：5 维度 20+ 检查项，每条给出**判定依据 / 不修的后果 / 怎么修**。

```bash
python3 scripts/ai_readiness.py --path .
python3 scripts/ai_readiness.py --path . --json --top 10
make gate    # CI 门禁：低于阈值或有阻断项 → 退出码 2
```

**重点不是分数，是 `findings`** —— 按影响排序的待改项。

---

### 3.10 `ai_remediate.py` —— 扫描→改造→复扫闭环 ⭐

**能做什么**：把「哪里不友好」变成「已经改好了，且效果是实测的」。

```bash
# 1) 只看计划，不产生任何文件
python3 scripts/ai_remediate.py plan --path <项目>

# 2) 生成到 staging（**不动目标工程**）
python3 scripts/ai_remediate.py generate --path <项目> --out .apm/remediation

# 3) 完整闭环 + 量化 before/after
python3 scripts/ai_remediate.py loop --path <项目> --json

# 4) 人工审阅通过后才写入
python3 scripts/ai_remediate.py apply --path <项目> --staging .apm/remediation
```

**三条硬约束**（都写进了测试）：

| 约束 | 实现 |
|---|---|
| 绝不编造事实 | 事实位置一律 `TODO(需人工填写)` |
| 默认不落盘 | 生成物只进 staging；`apply` 拒绝覆盖已存在文件 |
| 效果要实测 | `loop` 在临时副本应用并复扫，给出真实 before/after |

**实测**：iOS 工程 **68 → 86（+18）**。

> 15 类 finding 被明确标为「只能人工处理」，理由都写了。
> 例如 `no-tests`：**不能凭空生成测试**，造出来的只会制造虚假安全感。

---

## 4. 技能（Skill）—— agent 自动选用

| 技能 | 什么时候用 |
|---|---|
| `apm-loop` | 「自主完成闭环」「跑一遍性能回归」——**主编排器** |
| `apm-doctor` | 任何 APM 任务开始前（Phase 0） |
| `apm-startup` | 「启动慢」「冷启动优化」 |
| `apm-render` | 「卡顿」「掉帧」「白屏」 |
| `apm-memory` | 「内存泄漏」「OOM」「FOOM」 |
| `apm-crash` | 「崩溃」「查崩溃原因」 |
| `apm-autotest` | 「自动化测试」「验证需求」 |

**闭环六阶段**（`apm-loop` 驱动）：

```
Phase 0 体检 → Phase 1 基线 → Phase 1.5 可行性 → Phase 2 发现
   → Phase 3 定位 → Phase 4 修复 → Phase 5 验证 → Phase 6 防劣化
                                       ↑                │
                                       └─── 回归则回到 3 ──┘
```

---

## 5. 命令

| 命令 | 作用 |
|---|---|
| `/init` | 在目标工程里初始化 `.apm/`（含 agent 指令） |
| `/check` | 体检 + 友好度门禁 |
| `/verify` | 复扫 + 符号门禁 |
| `/crash` | 崩溃分诊入口 |

---

## 6. 典型工作流

### 6.1 性能优化（以 T1 为例）

```bash
# 0. 体检
make doctor

# 1. 建基线（固定口径：同设备/同构建/同命令/同样本量）
python3 scripts/apm_measure.py --profile ios-native-startup ... --warmup-launches 3 --samples 10

# 2. 数据能用吗
python3 scripts/apm_diagnose.py .apm/runs/<run> --metric startup.cold.first_frame

# 3. 目标可达吗（绝对目标必做）
python3 scripts/apm_feasibility.py plan --metric startup.cold.first_frame --target 200
#    → 按提示造最小 control，再 check

# 4. 只改一处，再测
python3 scripts/apm_measure.py ...（同口径）

# 5. 有效果吗
python3 scripts/apm_baseline.py compare --baseline .apm/baseline/startup.json \
  --run .apm/runs/<新run>/metrics.json --json
```

### 6.2 遗留项目 AI 化改造

```bash
python3 scripts/ai_readiness.py --path /path/to/legacy      # 扫描
python3 scripts/ai_remediate.py loop --path /path/to/legacy  # 量化
python3 scripts/ai_remediate.py generate --path /path/to/legacy --out .apm/remediation
# 人审 → 补齐 TODO → apply → 复扫
```

---

## 7. 明确的能力边界

**不承诺的**，遇到时工具会明确报 `unavailable`，不会假装：

- Android / 鸿蒙 / RN 的**标准测量 profile**（未落地）
- Android / 鸿蒙侧**原生 shim 真机验证**（无设备）
- 新架构（Fabric/TurboModules）下的符号化
- 鸿蒙的运行时能力边界

**已验证但有条件**的：

| 能力 | 条件 |
|---|---|
| iOS 原生冷启动测量 | 仅 `ios-native-startup` profile |
| iOS 真机截图 | 需可选 `pymobiledevice3` |
| 真机 UI 自动化 | 需先预热 `testmanagerd`（见下方） |
| 端侧听写 | **仅物理设备**，模拟器 `SpeechTranscriber.isAvailable=false` |

**真机 UI 测试的坑**（实测踩过）：

```text
[DTXConnection] Connection peer refused channel request ...
The test runner failed to initialize for UI testing.
(Timed out while enabling automation mode.)
```

先跑一次设备侧单元测试把 `testmanagerd` 拉起来，再跑 UI。
被观测工程里 `.apm/tools/device-test.sh` 已把这个顺序固化成脚本。

---

## 8. 常见问题

**Q：`make gate` 失败了怎么办？**
A：看 `findings`，按 `blocker > high > medium > low` 顺序改。
`blocker` 的含义是「agent 无法验证自己的改动」，此时其他优化意义有限。

**Q：为什么我的改动「看起来快了 11%」但工具说没效果？**
A：那多半是**挑了快模式的样本**。正确做法是同口径 n≥5 重复 + `apm_baseline compare`。
被工具判成「无显著变化」时，如实报告，不要换口径重测到好看为止。

**Q：为什么 `apm_measure.py` 拒绝我的设备？**
A：它在硬校验物理设备、bundle id 一致性、设备已连接且解锁。
这些不是形式主义 —— 每一项放松都会产生无法归因的假数据。

**Q：能只装一部分吗？**
A：能。`dist/` 里 `.opencode/` 与 `.claude/` 是并行的，按你用的 agent 取对应目录；
`AGENTS.md` 是两边共用的唯一真源入口。

---

## 相关文档

| 文档 | 什么时候读 |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | **先读这个** —— 项目约定与铁律 |
| [`../ROADMAP.md`](../ROADMAP.md) | 现在在哪、下一步做什么、哪些线不能碰 |
| [`../HANDOFF.md`](../HANDOFF.md) | 接手须知与踩过的坑 |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | 系统怎么搭的 |
| [`../docs/PROGRESS.md`](../docs/PROGRESS.md) | 完成度与**验证强度**（如实标注） |
| [`../docs/IMPLEMENTATION.md`](../docs/IMPLEMENTATION.md) | 每个脚本「为什么这么设计」 |
| [`case-study-translation-persistence.md`](case-study-translation-persistence.md) | 一个完整成功闭环长什么样 |
