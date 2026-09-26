# mobileAutoAPM

**让 AI Agent 自己把 App 的性能问题修好 —— 而且你能验证它没在骗你。**

一套面向 iOS / React Native / HarmonyOS 的移动端 APM Agent 能力集：
**发现 → 定位根因 → 修复 → 验证 → 防劣化**，每一步都有可复现的证据。

```bash
claude plugin marketplace add davidtan2008/mobileAutoAPM
claude plugin install mobile-apm@mobile-apm-marketplace
```

然后在你自己的工程里说一句话：

```
冷启动太慢了，帮我看看
```

Agent 会自己走完：测量 → 与基线对比 → 分行定位 → 改代码 → **同口径复测** → 给出带显著性的结论。

![自主闭环数据流](docs/diagrams/loop.png)

<sub>[SVG 版](docs/diagrams/loop.svg) · [完整架构](docs/diagrams/architecture.png)</sub>

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [凭什么和 Sentry / Firebase 不一样](#凭什么和-sentry-firebase-不一样)
- [核心原理：五条铁律](#核心原理五条铁律)
- [技术架构](#技术架构)
- [亮点](#亮点)
- [快速开始](#快速开始)
- [能力边界（先说不能做的）](#能力边界先说不能做的)
- [文档](#文档)

---

## 它解决什么问题

崩溃上报工具（Sentry / Bugly / Firebase）解决的是**「线上出事了，怎么看到」**。

它们不回答的问题有三个，而这些恰恰是性能优化的全部难点：

| 问题 | 为什么难 |
|---|---|
| **这个目标到底可达吗？** | 很多"优化"其实物理上不可能——空壳 App 的启动地板就在那儿 |
| **这次改动真的有效果吗？** | 性能数据方差常常比优化幅度还大，测两次不一样太正常了 |
| **为什么突然变慢了？** | 同一个 App 里方差源能有两倍差，挑样本很容易得到漂亮但假的结论 |

**大多数"性能优化"失败，不是因为改错了，而是因为无法判断有没有改对。**

mobileAutoAPM 的做法是：**先让"有没有效果"变成一个可判定的问题，再谈优化。**

### 一个真实例子

某 iOS 项目的目标是「冷启动 ≤200ms」。Agent 做了三轮优化，测出来 280ms → 279ms，p=0.605。

它没有宣布失败就收工，而是先造了一个**最小对照组**：把首页内容换成 `Text("x")`，其余全保留，测出 p50 = **210ms**。

```text
目标 200ms  <  空壳 App 地板 210ms
→ blocked_by_control_floor
→ 停止局部优化，升级为架构决策
```

这个"失败"比任何优化成果都值钱——**它避免了在物理上不可能的方向上继续投入**。

---

## 凭什么和 Sentry / Firebase 不一样

| | Sentry / Firebase | mobileAutoAPM |
|---|---|---|
| 定位 | **线上一旦出事**怎么看到 | **动手之前**怎么判断值不值得做 |
| 输入 | 线上崩溃/性能采样 | 你的仓库 + 一台真机 |
| 输出 | 告警、趋势图、根因线索 | **改完之后的显著性结论** |
| 谁来用 | 值班工程师、SRE | **AI Agent**（也可人用） |
| 核心能力 | 采集与聚合 | **测量纪律 + 判定逻辑** |
| 失败模式 | 漏报、噪音告警 | **把"测不出来"包装成"优化成功"** |

两者是**互补**的，不是替代。mobileAutoAPM 负责动手前的判断和事后的验证，Sentry 负责线上真实世界的监控。

> 一句话：**Sentry 告诉你"出事了"，mobileAutoAPM 告诉你"到底改没改对"。**

---

## 核心原理：五条铁律

这五条写进了每一个技能里，Agent 违反任何一条就算任务失败。

| # | 铁律 | 为什么 |
|---|---|---|
| 1 | **无基线不优化** | 没有可比基线之前的"优化"全是盲改 |
| 2 | **单变量** | 一次只改一处，否则无法归因 |
| 3 | **必须复测** | 同设备、同构建、同命令、同样本量 |
| 4 | **绝不伪造** | 不许用估算值/历史值/典型值充当实测 |
| 5 | **结论带来源** | 每个数字都要能追到：哪条命令、哪台设备、哪个 commit |

### 铁律 4 是专门为 LLM 设的

> 「LLM 做性能优化最常见的失败模式，是**编一个看起来合理的数字，然后基于它改一堆代码**。」

这条在本项目里被真实触发过。一个 iOS 目标工程的冷启动数据呈**双峰分布**——
快的一簇 235ms、慢的一簇 500ms，**挑两个快的就能报出"优化了 11%"**。

那是伪造。所以工具的做法是：**方差超阈值就整轮拒绝**（实测有一次 CV=21.5% 的 run 被整轮作废，前 3 次异常样本完整留档），并明确告诉你"本次无法判定"，而不是换个口径重测到好看为止。

---

## 技术架构

**核心设计原则：确定性的计算交给脚本，判断留给 Agent。**

需要"算"的事情（解析 sourcemap、算 CV、跑置换检验）全部是零依赖的确定性脚本；
需要"判断"的事情（改哪里、要不要问人）才交给模型。这样同样的输入永远得到同样的结论。

```
┌─────────────────────────────────────────────────────────────┐
│  技能层（7）                                                 │
│  apm-loop(编排) · doctor · startup · render · memory         │
│  crash · autotest                                           │
├─────────────────────────────────────────────────────────────┤
│  子 Agent（4）      profiler · reviewer · crash-triager ·    │
│                     perf-guard                              │
├─────────────────────────────────────────────────────────────┤
│  数据平面（11 个脚本 · 零第三方依赖 · 纯标准库）              │
│  测量 apm_measure     诊断 apm_diagnose     判定 apm_baseline│
│  可行性 apm_feasibility  截图 apm_screenshot  白屏 apm_white_screen
│  符号化 rn_symbolicate  符号门禁 rn_build_symbols             │
│  友好度 ai_readiness   改造 ai_remediate     体检 apm_doctor  │
├─────────────────────────────────────────────────────────────┤
│  知识库（7 篇）      指标定义 · 选型硬限制 · 测量协议 ·      │
│                      可行性协议 …                            │
└─────────────────────────────────────────────────────────────┘
```

| 能力 | 脚本 | 干什么 |
|---|---|---|
| **测量** | `apm_measure.py` | 统一口径采集，物理设备硬校验，保留逐次样本与原始日志 |
| **诊断** | `apm_diagnose.py` | CV、多簇检测、分段 CV、可疑变量、跨 run 漂移 |
| **判定** | `apm_baseline.py` | 置换检验判显著性（不依赖正态假设） |
| **可行性** ⭐ | `apm_feasibility.py` | **动手前**测最小对照地板，判断目标可达吗 |
| **符号化** | `rn_symbolicate.py` | Hermes 两步合成，字节码偏移 → 源码行号 |
| **改造闭环** | `ai_remediate.py` | 扫描 → 生成 → 人审 → 复扫量化 |

**跨 agent 可移植**：由 `tools/build-portable.py` 从单一真源生成 `dist/`，
同一份能力包可装进 opencode / Claude Code / 其他 Agent，**CI 校验生成物与源头一致**。

---

## 亮点

### ⭐ 1. 可行性前置判断——最该先用的能力

```bash
# 造最小对照组，把业务内容换成 Text("x")，其余全保留
python3 scripts/apm_feasibility.py plan --metric startup.cold.first_frame --target 200
python3 scripts/apm_feasibility.py check --control control/metrics.json \
  --candidate candidate/metrics.json --metric startup.cold.first_frame --target 200
```

| 退出码 | 含义 | 该做什么 |
|---:|---|---|
| 0 | `potentially_reachable` | 可以进单变量实验 |
| 1 | 证据不足 / 测量有问题 | 补样本或修测量，**不下结论** |
| 2 | `blocked_by_control_floor` | **停止局部优化**，升级架构决策 |

> 真实战绩：拦截了某项目"冷启动 ≤200ms"的目标。**在开工前省掉了整轮无效优化。**

### ⭐ 2. 不会把"测不出来"说成"优化成功"

```bash
python3 scripts/apm_baseline.py compare --baseline baseline.json --run run.json --json
```

实测校准过：清晰改善 p=0.0088；而 **−4.5% 的小改善被正确判为「不具实际意义」**。

### ⭐ 3. 全链路真机验证，不只是单测

| 能力 | 验证方式 | 结果 |
|---|---|---|
| iOS 冷启动测量 | iPhone 13 / iOS 26.7 真机 | clean commit 正式 baseline，跨 run `consistent`（Δp50=5ms） |
| 真机 UI 自动化 | iPhone 13 全量套件 | **123 passed, 0 failed, 0 skipped** |
| RN 符号化 | 真实 RN 工具链 + 真实 Hermes 堆栈 | 186,386 条映射 / 891 源文件 / **20/20 帧还原** |
| iOS 原生 shim | 真机加载 RN 模块 | `phys_footprint` 15.9MB vs `resident` 83.9MB（**18.9%**） |

> 这些数字全部来自真实命令输出，证据在 `docs/evidence/`。**没有一条是估算值。**

### ⭐ 4. 质量门禁内建

```bash
make gate   # AI 友好度门禁（CI 同款）
make test   # Python 123 + RN 74 + iOS 11 全部测试
```

工具**自己**也被同一套纪律要求：CI 校验 `dist/` 与源头一致、图表 SVG/PNG 不得漂移。

### ⭐ 5. 能在任何 agent 上用

```
plugins/mobile-apm/   ★ 单一真源
        │ make build-portable
        ▼
dist/  →  opencode | Claude Code | 其他
```

---

## 快速开始

### 1. 装上

```bash
# Claude Code
claude plugin marketplace add davidtan2008/mobileAutoAPM
claude plugin install mobile-apm@mobile-apm-marketplace

# 或手动（任何 agent 通用）
cp -R dist ~/.config/opencode/plugins/mobile-apm
```

### 2. 确认本机能做什么

```bash
make doctor
```

```
✅ 启动耗时检测   ✅ 页面渲染/卡顿   ✅ 白屏检测   ✅ 内存检测
✅ 崩溃符号化      ✅ UI 自动化        ✅ RN 专项    ✅ Android 专项  ✅ 鸿蒙专项
```

不可用的能力会**明确报 `unavailable`**，不会静默降级。

### 3. 在你的工程里用

```bash
cd /path/to/your/app
/apm-init          # 或按 docs/usage-guide.md 手动建 .apm/
```

然后直接提需求：

```
冷启动太慢了，帮我看看
```

Agent 会自动走完闭环，并给你一个带设备、样本量、显著性的结论。

### 自己手动用

```bash
# 测
python3 scripts/apm_measure.py --profile ios-native-startup \
  --device 00008110-000805902684801E --package-id com.example.app \
  --build-type Release --build-path /path/to/App.app --samples 10

# 判断数据能不能用
python3 scripts/apm_diagnose.py .apm/runs/latest --metric startup.cold.first_frame

# 判断改动有没有效果
python3 scripts/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/new/metrics.json
```

---

## 能力边界（先说不能做的）

**不吹，是这个项目的底线。**

| 能力 | 状态 |
|---|---|
| iOS 原生冷启动测量 | ✅ 真机验证，clean commit 正式 baseline |
| 真机截图 / 白屏检测 | ✅ iPhone 13 真机 |
| RN 崩溃符号化 | ✅ 真实 Hermes 堆栈端到端 |
| RN / iOS SDK | ✅ 74 + 11 个测试 |
| Android / 鸿蒙 **测量 profile** | ⛔ **未实现**——不会假装可用 |
| Android / 鸿蒙 **原生 shim 真机验证** | ⛔ **未验证**——手头无设备 |
| 新架构（Fabric/TurboModules）符号化 | ⛔ **未验证** |

**已验证但有前提的**：

- 端侧听写只在**物理设备**可用（模拟器 `SpeechTranscriber.isAvailable=false`）
- 真机 UI 自动化需先预热 `testmanagerd`（见 `docs/usage-guide.md` §7）
- iOS 真机截图需可选依赖 `pymobiledevice3`

**当前只承诺 `ios-native-startup` 一个测量 profile。** 别的平台在真机验证之前，
工具会明确说"不可用"，而不是给一个看起来能用的数字。

---

## 文档

| | |
|---|---|
| [**使用指南**](docs/usage-guide.md) | **能做什么 / 怎么用** —— 11 个脚本逐个说明、我想做的事→用哪个、典型工作流 |
| [**交接文档**](HANDOFF.md) | 接手须知与踩过的坑 |
| [**路线图**](ROADMAP.md) | 现状、下一步、**发布前置条件** |
| [架构与数据流](ARCHITECTURE.md) | 整体形状、闭环机制、设计原则 |
| [**成功闭环案例**](docs/case-study-translation-persistence.md) | 一个完整闭环长什么样（含 60 秒 demo） |
| [**核心实现细节**](docs/IMPLEMENTATION.md) | 每个脚本「为什么这么设计」 |
| [项目约定](AGENTS.md) | **给 AI Agent 看的** |
| [进度与完成度](docs/PROGRESS.md) | **含已知限制与未验证项** |
| [图表源文件](docs/diagrams/) | 架构图 / 闭环图 / 闸门图 / 能力矩阵 |

---

## 参与

欢迎。这是个**重证据**的项目，PR 时请注意：

1. 改动后 `make test` 必须绿（Python / RN / iOS 三套）
2. 改了 `plugins/` → `make build-portable`（`dist/` 是生成物，**勿手改**）
3. 改了 SVG → `make build-diagrams`
4. **不要在文档里写没验证过的数字**——这是本项目唯一不可让步的规则

## License

MIT
