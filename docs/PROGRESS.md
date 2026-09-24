# 进度文档

> 更新：2026-09-24
> 项目：用 Claude Code + 大模型（DeepSeek 等）搭建**移动端 APM 体系**，
> 覆盖 iOS / React Native（跨 iOS、Android、鸿蒙），做到「提出需求后自主完成闭环」。

---

## 一、原始需求的完成度

| # | 需求 | 状态 | 说明 |
|---|---|---|---|
| 1 | 搜索并安装业界成熟 skill 与 MCP | ✅ **完成** | 见 §3。9/9 能力就绪 |
| 2 | 自主学习业界优秀方案并吸收方法论 | ✅ **完成** | 见 §4 |
| 3 | 按需求定制 skill | ✅ **完成** | `mobile-apm` 插件，见 §2 |
| 4 | 给出可落地方案，且能用于 opencode 等 | ✅ **完成** | 见 §5 |

**⚠️ 但最核心的一条尚未验证**：

> 「提出需求后**全都能自主完成**」

已建成的能力很全（7 技能 / 4 子 Agent / 双端 SDK / 数据平面），
但**从未跑过一次完整的、无人干预的闭环**（发现 → 定位 → 修复 → 验证）。
详见 §7「下一步」。

---

## 二、交付物总览

```
mobileAutoAPM/
├── plugins/mobile-apm/     ← Claude Code 插件（单一真源）
│   ├── skills/             7 个技能
│   ├── agents/             4 个子 Agent
│   ├── commands/           4 个斜杠命令
│   ├── hooks/              SessionStart（仅在 .apm/ 工程生效）
│   ├── scripts/            数据平面（5 个零依赖脚本）
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

### 能力矩阵：9/9 就绪

```
✅ 启动耗时检测   ✅ 页面渲染/卡顿   ✅ 白屏检测
✅ 内存检测       ✅ 崩溃符号化根因   ✅ UI 自动化回归
✅ RN 专项        ✅ Android 专项     ✅ 鸿蒙专项
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

### 待完成

| 优先级 | 项 | 门槛 |
|---|---|---|
| **P0** | **跑一次真实的自主闭环演练** | 需要一个**可判定的具体需求**（如「冷启动压到 200ms」「某页面白屏」） |
| P0 | 符号化流水线接入真实构建 | 已有工具（`rn_symbolicate.py` + `rn_build_symbols.py`），未在真实 CI 跑通 |
| P1 | Sentry MCP 鉴权 | 需 Sentry 账号，执行 `/mcp` OAuth |
| P1 | 数据后端选型（含鸿蒙） | 需决策：Sentry self-hosted / 腾讯 Bugly / AGC APMS |
| P2 | Android / 鸿蒙真机验证 | 需接设备或起模拟器（`adb devices` / `hdc list targets` 目前均为空） |

### 已交付的工程骨架（供演练使用）

- `apm_baseline.py`：置换检验判显著性。实测校准过 —— 清晰改善 p=0.0088，
  而 **-4.5% 的小改善被正确判为「不具实际意义」**，不会被包装成优化成果
- `apm_white_screen.py`：纯标准库解 PNG，已用**真实模拟器截图**端到端验证
- `rn_symbolicate.py` / `rn_build_symbols.py`：31 个测试，含 Hermes 两步合成

---

## 八、验证强度说明（如实标注）

**本项目的原则：不把未验证的东西说成已验证的。**

| 部分 | 验证强度 |
|---|---|
| `rn-apm` 逻辑、`ios-apm` 逻辑 | ✅ **真实测试**（74 + 11） |
| 数据平面脚本 | ✅ **真实测试**（含真实截图、真实构建产物） |
| 端到端指标闭环（SDK→基线判定） | ✅ **跑通过** |
| 跨 harness 生成器 | ✅ 在模拟工程中端到端验证 |
| **原生 shim（iOS/Android/鸿蒙）** | ⚠️ **未在真机验证** —— 参考实现见 `rn-apm/docs/native-shims.md` |
| **符号化工具在真实构建中的表现** | ⚠️ **未验证** |
| **自主闭环（核心承诺）** | ❌ **从未完整跑过** |
