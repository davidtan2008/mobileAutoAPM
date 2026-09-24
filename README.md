# mobileAutoAPM — 移动端 APM Agent 能力集

用 AI Coding Agent（Claude Code / opencode）驱动 **iOS + React Native（含 Android / HarmonyOS）**
的性能与稳定性闭环：

```
发现 → 定位根因 → 修复 → 验证 → 防劣化
```

覆盖：**启动耗时 / 页面渲染 / 白屏 / 内存 / 崩溃**，以及**自动化测试与需求验证**。

## 📍 当前进度

**完整进度、完成度与已知限制见 [`docs/PROGRESS.md`](docs/PROGRESS.md)。**

一句话摘要：工具层已就绪（9/9 能力）、双端埋点 SDK 已完成并有测试
（iOS 11 + RN 74）、跨 harness 生成器可用；
**但「提出需求后自主完成闭环」这个核心承诺尚未被完整验证过** —— 这是下一步。

| 部分 | 状态 |
|---|---|
| `plugins/mobile-apm/` | ✅ 7 技能 / 4 子 Agent / 4 命令 / 1 hook |
| `rn-apm/` | ✅ React Native 埋点 SDK（74 测试） |
| `ios-apm/` | ✅ iOS 原生埋点 SDK（11 测试） |
| `dist/` | ✅ 跨 harness 可移植树（由生成器产出） |
| **自主闭环演练** | ❌ **尚未跑过** |

---

## 仓库结构

```
.
├── plugins/mobile-apm/     ★ 单一真源：技能 / Agent / 命令 / hook / 脚本 / 知识库
├── rn-apm/                 React Native 埋点 SDK（启动分段 / 崩溃四层 / 内存水位）
├── ios-apm/                iOS 原生埋点 SDK（pre-main / 启动分段 / 内存水位）
├── tools/build-portable.py 跨 harness 编译器 → dist/
├── dist/                   生成产物（opencode 等直接可用）
└── docs/
    ├── PROGRESS.md         ★ 进度与完成度
    ├── APM-BLUEPRINT.md    落地蓝图：还缺什么、怎么做
    └── mcp-setup.md        工具链与 MCP 配置（含本机实测）
```

---

## 快速开始


### 1. 安装插件（Claude Code）

```bash
claude plugin marketplace add /path/to/this/repo
claude plugin install mobile-apm@mobile-apm-marketplace
```

验证：

```bash
claude plugin details mobile-apm
```

### 2. 在你的 App 工程里初始化

```
/apm-init
```

会做三件事：体检工具链 → 识别工程类型 → 采集首轮基线。

### 3. 然后就可以直接说需求

| 你说 | Agent 会做 |
|---|---|
| "冷启动太慢，优化一下" | `/apm-startup` → 测量 → 分段定位 → 修复 → 同口径复测 → 显著性检验 |
| "首页会白屏" | `/apm-render` → 多帧截图 + 像素分析 → 定位 → 修复 → 验证 |
| "内存一直涨" | `/apm-memory` → 水位曲线 → 堆快照对比 → 查泄漏源 |
| "这个崩溃帮我看看" | `/apm-crash` → 符号化 → 聚类 → 根因 → 修复 → 验证 |
| "验证这个需求实现了没" | `/apm-autotest` → 拆断言 → 写流程 → 执行 → 留证 |
| "跑一遍性能回归" | `/apm-check` → 全维度采集 → 对比基线 → 有劣化自动进定位 |

### 4. 用 opencode 或其他 Agent

```bash
python3 tools/build-portable.py
cp -R dist/. /path/to/your-app/     # ⚠️ 用 dist/. 不要用 dist/*
```

详见 `docs/APM-BLUEPRINT.md` §6。

---

## 五条铁律

这套体系的价值不在于"能跑命令"，而在于**不撒谎**：

1. **无基线，不优化** —— 没有可比基线之前的优化都是盲改
2. **单变量** —— 一次只改一处，否则无法归因
3. **必须复测** —— 用与基线**完全相同**的口径（同设备、同构建类型、同命令）
4. **绝不伪造** —— 不许用估算值、历史值、"典型值"充当本机实测
5. **结论带来源** —— 每个数字都要能追溯到命令 / 设备 / commit

> LLM 做性能优化最常见的失败模式，是**编一个看起来合理的数字，然后基于它改一堆代码**。
> 铁律 4 专门防这个。`apm_baseline.py` 用置换检验做显著性判定，
> 并会主动识别"统计显著但幅度无意义"（如 -4.5%）与"测量噪声过大"。

---

## 组成

| 组件 | 说明 |
|---|---|
| **7 技能** | `apm-loop`（主控）、`apm-doctor`、`apm-startup`、`apm-render`、`apm-memory`、`apm-crash`、`apm-autotest` |
| **4 子 Agent** | `apm-profiler`（采集）、`crash-triager`（分诊）、`perf-guard`（门禁）、`apm-reviewer`（静态审查） |
| **4 命令** | `/apm-init`、`/apm-check`、`/apm-crash`、`/apm-verify` |
| **1 hook** | SessionStart，仅在存在 `.apm/` 的工程注入状态（其他工程零输出） |
| **数据平面** | `apm_doctor.py` / `apm_baseline.py` / `apm_white_screen.py`（零第三方依赖） |
| **知识库** | `references/stack-selection.md`（选型 + 各平台硬限制）、`references/metrics-definitions.md`（指标口径） |

---

## 两个必读的知识库文件

Agent 在动手前会读它们 —— 里面是**大量"看起来该这样做但实际是坑"的内容**：

- **`references/stack-selection.md`**
  例：鸿蒙是第一约束（Sentry/Firebase/Detox 全不可用）；
  RN 崩溃必须四层捕获；iOS FOOM 是官方能力缺口必须自建

- **`references/metrics-definitions.md`**
  例：用 PSS 而非 RSS 判断 OOM；卡顿看 p95 而非平均 FPS；
  RN 必须分离观测 JS FPS 与 UI FPS

---

## 数据平面速查

```bash
S=.claude/skills/_apm/scripts      # 可移植树；插件形态用 ${CLAUDE_PLUGIN_ROOT}/scripts

python3 $S/apm_doctor.py                    # 能力体检（装软件前先跑这个）
python3 $S/apm_white_screen.py shot.png     # 白屏检测
python3 $S/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/<本次>/metrics.json
```

`apm_baseline.py compare` 退出码：**`0` 无劣化 / `2` 有劣化 / `1` 数据问题**。
退出码 `2` 可直接作为 CI 门禁。

---

## 工程结构

```
.
├── plugins/mobile-apm/          # ★ 单一真源
│   ├── .claude-plugin/plugin.json
│   ├── skills/                  # 7 个技能
│   ├── agents/                  # 4 个子 Agent
│   ├── commands/                # 4 个命令
│   ├── hooks/                   # SessionStart
│   ├── scripts/                 # 数据平面
│   └── references/              # 知识库
├── tools/build-portable.py      # 跨 harness 编译器
├── dist/                        # 生成产物（opencode 等用）
└── docs/
    ├── APM-BLUEPRINT.md         # ★ 落地蓝图：还缺什么、怎么做
    └── mcp-setup.md             # MCP 安装配置
```

**改内容只改 `plugins/mobile-apm/`**，然后重跑生成器。不要手改 `dist/`。

---

## 下一步

读 **`docs/APM-BLUEPRINT.md`** —— 它说明了这套体系目前**还缺什么**才能算真正建成
（最重要的是：应用侧埋点 SDK、符号化流水线、线上数据源接入）。

**当前状态**：Agent 能自主完成**本地测量、定位、修复、验证**的闭环；
但"自动发现问题"还需要线上数据源（见蓝图 §2 的 P0/P1）。
