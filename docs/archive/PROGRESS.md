# 进度文档

> 更新：2026-09-26
> 项目：用 Claude Code + 大模型（DeepSeek 等）搭建**移动端 APM 体系**，
> 覆盖 iOS / React Native（跨 iOS、Android、鸿蒙），做到「提出需求后自主完成闭环」。

---

## 一、原始需求的完成度

| # | 需求 | 状态 | 说明 |
|---|---|---|---|
| 1 | 搜索并安装业界成熟 skill 与 MCP | ✅ **完成** | 见 §3。基础能力 10/11；配置 `APM_PYMOBILEDEVICE3_BIN` 后 11/11 |
| 2 | 自主学习业界优秀方案并吸收方法论 | ✅ **完成** | 见 §4 |
| 3 | 按需求定制 skill | ✅ **完成** | `mobile-apm` 插件，见 §2 |
| 4 | 给出可落地方案，且能用于 opencode 等 | ✅ **完成** | 见 §5 |

**✅ 核心承诺已完成一次可验证闭环**：

> 已结束会议记录的译文持久化修复：红测失败 → 单变量修复 → 120/120 单元测试
> + Release 构建通过。详见 `docs/case-study-translation-persistence.md`。

另有 T1 冷启动诚实失败：200ms 目标被最小 control p50=210ms 拦截。能力覆盖
7 技能 / 4 子 Agent / 双端 SDK / 数据平面，但“任意需求都能自主完成”仍不能泛化。

---

## 二、交付物总览

```
mobileAutoAPM/
├── plugins/mobile-apm/     ← Claude Code 插件（单一真源）
│   ├── skills/             7 个技能
│   ├── agents/             4 个子 Agent
│   ├── commands/           4 个斜杠命令
│   ├── hooks/              SessionStart（仅在 .apm/ 工程生效）
│   ├── scripts/            数据平面（11 个零依赖脚本）
│   ├── references/         知识库（选型 / 指标口径 / SDK 说明）
│   └── docs/               符号化流水线接入指南
├── rn-apm/                 React Native 埋点 SDK
├── ios-apm/                iOS 原生埋点 SDK
├── tools/build-portable.py 跨 harness 编译器（→ dist/）
├── dist/                   生成的可移植树（供 opencode 等使用）
└── docs/                   本仓库文档
```

### 7 个技能

| 技能 | 作用 |
|---|---|
| `apm-loop` | **主控编排**：发现→定位→修复→验证→防劣化，含五条铁律与停止条件 |
| `apm-doctor` | 能力就绪度自检 —— 明确「本机现在真的能做什么」 |
| `apm-startup` | 启动耗时检测与优化 |
| `apm-render` | 页面渲染 / 卡顿 / 白屏 |
| `apm-memory` | 内存 / 泄漏 / FOOM |
| `apm-crash` | 崩溃符号化 / 聚类 / 根因 |
| `apm-autotest` | 自动化测试 / 需求验证 / 性能回归门禁 |

### 4 个子 Agent

`apm-profiler`（采集）· `crash-triager`（崩溃分诊）· `perf-guard`（回归门禁）· `apm-reviewer`（静态审查）

---

## 三、工具层（需求 1）

### 已装并连通

| 工具 | 作用 |
|---|---|
| `mobilebuildmcp` MCP | iOS 构建 / 模拟器 / rs-1 语义快照 / LLDB / 覆盖率（72 工具） |
| `agent-device` MCP | **跨平台设备驱动：iOS / Android / HarmonyOS** / TV / web |
| `chrome-devtools` MCP | RN Hermes 调试（CDP）、Web 性能 trace |
| `firebase` MCP | Crashlytics / Performance / Firestore |
| `zread` / `web-search-prime` / `web-reader` / `zai` | 检索与图像 |
| `sentry` MCP | 崩溃数据源（⚠️ 待 `/mcp` 完成 OAuth） |

### 已装插件

`firebase@firebase`（13 个官方 skill，含 `firebase-crashlytics`）· `expo` ·
`chrome-devtools-mcp` · `swift-lsp` · `clangd-lsp` · `mobile-apm`（本项目的）

### 能力矩阵：11 项（基础 10/11；配置可选 DVT 截图后 11/11）

```
✅ 启动耗时检测   ✅ 页面渲染/卡顿   ✅ 白屏检测
✅ 内存检测       ✅ 崩溃符号化根因   ✅ UI 自动化回归
✅ RN 专项        ✅ Android 专项     ✅ 鸿蒙专项
✅ 可行性前置判断  ✅ iOS 真机截图（配置 DVT 后端后）
```

### ⚠️ 一个修好的历史遗留问题

`adb` / `hdc` 长期找不到，根因是 **`~/.zshrc` 路径过时** ——
应用已迁到 `/Volumes/DevDisk`，配置仍指向旧路径。已修正（备份在 `~/.zshrc.bak-*`）。

详细配置与实测见 [`mcp-setup.md`](mcp-setup.md)。

### 已知限制

| 限制 | 说明 |
|---|---|
| `agent-device` 的鸿蒙后端 | README 明确说明**只覆盖部分命令**，需 `agent-device capabilities --platform harmonyos` 实查 |
| 走外网的 MCP | **代理不会自动生效**，需单独带代理环境变量 |
| Firebase | 依赖 Google 基础设施，国内需代理；**Crashlytics 依赖 GMS，鸿蒙不可用** |

---

## 四、方法论吸收（需求 2）

来源：字节《抖音品质建设 - iOS 启动优化实战篇》，已蒸馏进
`plugins/mobile-apm/references/`。

| 知识库 | 内容 |
|---|---|
| `stack-selection.md` | 技术选型 + **各平台硬限制**（鸿蒙是第一约束；RN 崩溃必须四层；iOS FOOM 是官方能力缺口） |
| `metrics-definitions.md` | 指标口径（用 PSS 不是 RSS；卡顿看 p95；RN 必须分离 JS FPS / UI FPS） |
| `rn-apm-sdk.md` | RN 埋点 SDK 的用法与限制 |

吸收的抖音方法论：**删 → 延迟 → 并发 → 更快**；
埋点口径（起点用 `sysctl` 进程创建时间，终点逼近 `CA::Transaction::commit`）；
工程纪律（准入项 CR、灰度、pct50、AB 实验）。

---

## 五、跨 Agent 兼容（需求 4）

**单一真源 + 生成器**，不手写两份（必然漂移）：

```bash
python3 tools/build-portable.py          # → dist/
cp -R dist/. /path/to/your-app/          # ⚠️ 用 dist/. 不能用 dist/*
```

生成器自动处理了三处**会致命的兼容点**：

1. 技能名必须等于目录名，`name` + `description` 缺一 opencode 下**完全不加载**
2. `${CLAUDE_PLUGIN_ROOT}` 在 opencode 里是字面量 → 重写为工程根相对路径
3. hook 差异最大：opencode 无 settings hooks → 生成 `.opencode/plugins/apm-hook-bridge.ts`
   把 `.claude/hooks/*.sh` 当子进程调，**一份逻辑服务两个 harness**

---

## 六、双端埋点 SDK

### `rn-apm`（React Native）

**74 个测试全部通过。** 补三块缺口：

- 启动分段打点（RN 启动是黑盒，通用工具只给总耗时）
- **内存水位环形缓冲**（iOS FOOM 归因的唯一路径）
- **崩溃四层捕获**（`ErrorUtils` **链式**接管，不打断 Sentry；**如实报告哪层没接上**）

### `ios-apm`（iOS 原生）

**11 个测试全部通过。** 与 `rn-apm` 配对：

- `IOSAPMEarlyMark`（C target）：**pre-main 打点** —— 早于任何 Swift 代码
- `LaunchTracker`：启动分段 + 默认打印完整拆分
- `MemoryWatermark`：内存水位（`phys_footprint`，**不是 `resident_size`**）

### 两条经过实战的教训已固化为代码约束

| 教训 | 固化方式 |
|---|---|
| 埋点起步太晚会把最大成本藏起来（实测某 App 有 **219ms 完全未归因**） | C 构造函数 + `activate()` 必须放最早位置 + **默认打印完整阶段拆分** |
| 取不到值时返回 0 会被误读成「极快」 | `premain_millis()` 取不到返回 **-1**，显式区分"没有数据"与"数据是 0" |

---

## 七、下一步

> **详细路线图见 [`ROADMAP.md`](../ROADMAP.md)**（面向接手的工程师）。

### T1 演练已完成（2026-09-25）

**目标**：冷启动压到 200ms（被观测对象：一个真实 iOS 工程，iPhone 13 真机）

| 结论 | 证据 |
|---|---|
| ❌ **目标不可达** | 二分实验：App 内容换成 `Text("x")` 后 SwiftUI 场景引导耗时不变 → **空壳 App 地板 = 230ms** |
| ❌ **优化无效果** | 可优化段 126→132ms，**p=0.694**，已回退 |
| ⚠️ **测量方法本身不成立** | 冷启动总耗时有一个未受控的 **~2 倍方差源**，比要优化的幅度还大 |

**沉淀的平台资产**：`references/measurement-protocol.md`（新增）——
把测量纪律从口号变成可执行协议，并记录了平台自己的三个测量能力缺口。

**这次演练的价值**：五条铁律全部被触发过。最危险的时刻是有 fast 模式样本摆在那里，
挑两个就能报出「优化 11%」的漂亮数字 —— **那是伪造，没有做。**

### P0 第一版已落地（2026-09-25）

- `apm_measure.py --profile ios-native-startup`：参数化物理设备、bundle id、`.app`、
  样本量与间隔；保留逐次日志、`pre-main` candidates、完整 stages 和 context。
- `apm_diagnose.py`：CV、疑似多簇、分段 CV、跨运行相关性与同 commit 跨 run 漂移；不自动分层/挑簇。
- `apm_baseline.py`：高方差/多簇/跨 run 漂移/口径缺失返回数据错误 `1`；JSON 模式保留退出码；
  `record --require-healthy` 可阻止不可信基线写入。
- Python 测试从 47 增至 **92**；T1 数值作为仓库内 fixture 回归。
- ✅ **正式 baseline 已记录**（clean commit `2b3a1ea`，iPhone 13 / iOS 26.7 / Release /
  warmup=3 / n=10）：run C p50=252ms/CV=3.5%、run D p50=257ms/CV=2.8%，跨 run `consistent`
  （Δp50=5ms），`compare` 退出码 0。前一轮 run B（p50=261ms/CV=21.5%，多簇）被整轮拒绝
  并保留为筛选依据，未挑选样本。详见被观测工程 `.apm/baseline/README.md`。

### 待完成

| 优先级 | 项 | 门槛 |
|---|---|---|
| **P0** | **补齐测量能力**（模板化测量脚本 / 方差诊断 / 判据建议） | ✅ iOS profile、诊断器、baseline 闸门已落地；clean commit 上的**正式 baseline** 已记录（n=10 跨 run `consistent`） |
| **P0** | Android / 鸿蒙 / RN 标准测量适配器 | ⬜ 零进度；需先接设备（`adb` / `hdc` 目前为空） |
| P1 | 把 T1 的结论做成能力（可行性前置判断 / 对照组方法论） | ✅ `apm_feasibility.py`、协议和 skill 闸门已落地并真实验证；200ms 目标被 control p50=210ms 拦截 |
| P1 | 支柱 A 的改造闭环 | ✅ `ai_remediate.py` 已落地：模板化生成 + 人审闸门 + 实测量化（iOS 工程 68 → 86） |
| P1 | Sentry MCP 鉴权 | 需 Sentry 账号，执行 `/mcp` OAuth |
| P1 | 数据后端选型（含鸿蒙） | 需决策：Sentry self-hosted / 腾讯 Bugly / AGC APMS |
| P2 | 符号化流水线接入真实构建 | 工具已有，未在真实 CI 跑通 |
| P2 | Android / 鸿蒙真机验证 | 需接设备（`adb devices` / `hdc list targets` 目前为空） |
| P3 | 自我进化机制 | 设计见 `docs/diagrams/evolution.svg`，未实现 |

### 已交付的工程骨架（供演练使用）

- `apm_baseline.py`：置换检验判显著性。实测校准过 —— 清晰改善 p=0.0088，
  而 **-4.5% 的小改善被正确判为「不具实际意义」**，不会被包装成优化成果
- `apm_white_screen.py`：纯标准库解 PNG，已用**真实模拟器截图**与 iPhone 13 真机 DVT
  截图端到端验证；本次模拟器 6 帧与真机截图均为 `content_present`，未复现白屏。
  旧 `idevicescreenshot` 后端不可用，但可选 `pymobiledevice3` DVT 后端已验证成功。
- `rn_symbolicate.py` / `rn_build_symbols.py`：31 个测试，含 Hermes 两步合成
- `apm_feasibility.py`：目标可行性闸门。真实 control 实验（最小 `Text("x")`，
  iPhone 13 / n=20）得 p50=210ms，正式拦截 200ms 冷启动目标 → `blocked_by_control_floor`
- `apm_screenshot.py`：iOS 真机截图。可选 `pymobiledevice3` 的 DVT/CoreDevice 通道
  已在 iPhone 13 / iOS 26.7 验证（1170×2532 PNG，`apm_white_screen.py` 判 `content_present`）
- `.apm/tools/device-test.sh`（被观测工程）：真机测试统一入口，强制
  「先预热 testmanagerd，再跑 UI」；脚本自身端到端验证 → 真机 123 passed
- `ai_remediate.py`：支柱 A 闭环。`plan` / `generate` / `loop` / `apply` 四段；
  生成物里的事实位置一律 `TODO(需人工填写)`，`apply` 拒绝覆盖已存在文件；
  `loop` 在**临时副本**上应用并复扫，给出实测 before/after（不碰目标工程）

---

## 八、验证强度说明（如实标注）

**本项目的原则：不把未验证的东西说成已验证的。**

| 部分 | 验证强度 |
|---|---|
| `rn-apm` 逻辑、`ios-apm` 逻辑 | ✅ **真实测试**（74 + 11） |
| 数据平面脚本 | ✅ **单元/回归测试**（含真实模拟器/真机截图、真实构建产物；iOS 真机测量链路已跑通，clean commit 上的正式 baseline 已记录） |
| 端到端指标闭环（SDK→基线判定） | ✅ **跑通过** |
| 跨 harness 生成器 | ✅ 在模拟工程中端到端验证 |
| **原生 shim（iOS）** | ✅ **真机验证通过**（iPhone 13 / iOS 26.7 / RN 0.73.4 / Release）：模块注册、进程创建时间、phys_footprint 内存、设备信息全部取到真值；`footprint/resident = 18.9%` 实证 RSS 会严重高估 |
| **原生 shim（Android/鸿蒙）** | ⚠️ **未在真机验证** —— 无设备；参考实现见 `rn-apm/docs/native-shims.md` |
| **被观测工程的真机 UI 套件** | ✅ **iPhone 13 / iOS 26.7：123 passed, 0 failed, 0 skipped**（含 1分59秒 的真实端侧听写用例）。环境前置与预热顺序见 `references/measurement-protocol.md` §5 |
| **符号化工具在真实构建中的表现** | ✅ **端到端验证通过**（真实 RN 0.73.4 工具链 + 真实 Hermes 堆栈）：bundle → hermesc → compose → 符号化，20/20 帧 100% 还原，耗时 4.6s；已接入 CI 独立 job。修复了解析器不认真实 `address at <path>:<line>:<col>` 格式的缺陷 |
| **自主闭环（核心承诺）** | ✅ **1 次成功**（译文持久化，模拟器 + 真机双重背书）+ ⚠️ 1 次诚实失败（T1 启动）；尚不能泛化到任意需求 |
