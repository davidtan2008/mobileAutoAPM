# 移动端 APM Agent 体系落地蓝图

> 面向：iOS + React Native（覆盖 Android / HarmonyOS）
> Agent 宿主：Claude Code / opencode / 其他 AI coding agent

---

## 〇、先看清一件事：Agent 层与数据层是两回事

调研 Meta 与 Netflix 的公开实践后，最值得抄的架构是**两层分离**：

| 层 | 是什么 | 谁提供 | 本项目状态 |
|---|---|---|---|
| **能力层**（Tools/MCP） | 查性能数据、拉崩溃、跑构建、取 trace、查变更历史 | MCP servers + CLI | **部分完成**，见 §3 |
| **判断力层**（Skills） | 把资深性能工程师的直觉编码进去：看到什么信号该查哪里 | Skills | **已完成**，见 §1 |

> Meta 的经验：**主动优化（攻）与劣化修复（防）共用同一套 tools，只有 skills 不同** ——
> 这样新增一个 agent 几乎零集成成本。

**关键含义**：只有 skills 而没有数据源，Agent 就只能靠"本地跑一次"来推断，
看不到线上真实情况。**§3 的数据层才是决定这套体系上限的东西。**

---

## 一、已交付（本次完成并验证）

| 交付物 | 内容 | 验证状态 |
|---|---|---|
| **Plugin** `mobile-apm@0.2.0` | 已安装启用 | `claude plugin details` 确认：11 技能 / 4 Agent / 1 hook |
| **7 个技能** | apm-loop（主控编排）、apm-doctor、apm-startup、apm-render、apm-memory、apm-crash、apm-autotest | 已装载 |
| **4 个子 Agent** | apm-profiler（采集）、crash-triager（崩溃分诊）、perf-guard（回归门禁）、apm-reviewer（静态审查） | 已装载 |
| **4 个斜杠命令** | `/apm-init`、`/apm-check`、`/apm-crash`、`/apm-verify` | 已装载 |
| **SessionStart hook** | 仅在 `.apm/` 存在的工程注入状态，其他工程零输出零成本 | 三条路径实测通过 |
| **数据平面脚本** | `apm_doctor.py`、`apm_baseline.py`（置换检验）、`apm_feasibility.py`（control 闸门）、`apm_white_screen.py`（纯标准库 PNG 解码）、`apm_screenshot.py`（iOS 真机 DVT 截图） | 全部实测通过 |
| **知识库** | `stack-selection.md`（选型+硬限制）、`metrics-definitions.md`（口径） | — |
| **跨 harness 生成器** | `tools/build-portable.py` → 单一真源编译出 Claude Code / opencode 双目标 | 在模拟工程中端到端跑通 |

**核心设计**：五条铁律（无基线不优化 / 单变量 / 必须复测 / 绝不伪造 / 结论带来源）。
这不是形式主义 —— LLM 做性能优化最常见的失败模式就是**编一个看起来合理的数字然后基于它改一堆代码**。

---

## 二、还缺什么（按优先级）

### P0 — 没有这些，体系跑不起来

#### 1. 应用侧埋点 SDK（最大的缺口）

Agent 现在只能**本地测一次**。要"自动发现问题"，必须有线上埋点：

**✅ 已实现：`rn-apm/`（2289 行源码 / 74 个测试全部通过）。**
详见 `rn-apm/README.md` 与 `references/rn-apm-sdk.md`。

- [x] **启动分段打点** —— 标准阶段常量，以进程创建时间为原点
- [x] **内存水位环形缓冲** —— ⚠️ **FOOM 归因的唯一可行路径**，崩溃时附水位快照
- [x] **白屏超时埋点** —— 且**区分「白屏」与「慢」**（超时后最终渲染 = 慢，不是白屏）
- [x] **崩溃四层捕获** —— 含 `ErrorUtils` **链式**接管（不打断 Sentry 等），
      并**如实报告哪层没接上**（缺一层 = 一类盲区）
- [x] **崩溃立即落盘** —— 跨进程恢复，进程死亡不丢数据
- [x] **指标导出** —— `toMetrics()` 直接对接 `apm_baseline.py`，**闭环已端到端验证**

**仍需完成**：

- [ ] **原生 shim** —— 参考实现见 `rn-apm/docs/native-shims.md`（⚠️ **未在真机验证**）：
      iOS `sysctl` 进程创建时间 + `phys_footprint`；Android `Process.getStartUptimeMillis()` + PSS；
      鸿蒙 HiAppEvent + `hidebug`（PSS）
- [ ] **RN 的 JS↔Native 崩溃关联** —— 需接入 KSCrash / SentryCrash / Bugly
      并实现 `NativeBridge.reportJsError`（`NativeLinkedErrors`）
- [ ] **在真实工程验证**，尤其鸿蒙 RNOH 侧的原生模块形态

#### 2. 符号化流水线接入 CI（最容易做错的一环）

**✅ 工具已实现（31 个测试全通过，端到端验证）：**

- [x] **`rn_symbolicate.py`** —— 纯标准库实现 Base64 VLQ 解码 +
      sourcemap v3 解析 + **两张 map 合成** + Hermes/标准 JS 堆栈解析
- [x] **`rn_build_symbols.py`** —— 构建期产出与 **CI 门禁校验**
      （退出码 `2` 表示必需符号文件缺失，可直接卡住发布）
- [x] **坑位知识内置进工具**：iOS sourcemap 默认缺失、Hermes 两步合成、
      0 字节占位是正常现象、debug ID 而非版本号

**已验证的关键行为**：

| 场景 | 结果 |
|---|---|
| 未合成 → 只还原到 `index.bundle` | ✅ 正确，并提示需要 compose |
| 合成后 → 直达 `src/api/client.ts:17:4` | ✅ 端到端验证 |
| 两张 map 不匹配 | ✅ 告警"可能不正确"，但**不擅自判失败** |
| 命中距离过大 | ✅ 附"请核对 map 是否为本次构建"提示 |
| 原生帧 | ✅ 跳过并说明需由 dSYM 处理（不误报失败） |
| iOS 缺 sourcemap | ✅ 门禁退出码 2，并给出 Xcode 打包脚本修法 |

**仍需你完成**：

- [ ] 在真实工程的 Xcode 打包脚本里导出 `SOURCEMAP_FILE`
      （见 `plugins/mobile-apm/docs/symbolication-pipeline.md` 坑 1）
- [ ] Android `build.gradle` 确保 `hermesFlags` 含 `-output-source-map`
- [ ] 生成并注入 per-build **debug ID**
- [ ] 把 `rn_build_symbols.py verify --strict` 加进发版流程

> 行业事实：**符号化链路没修好之前，崩溃分析基本是白做的。**
> 未符号化的栈只有地址 —— 拿它分析根因，比不分析更糟。

#### 3. 工具链补齐（本机已实测缺失）

| 缺失 | 影响 | 安装 |
|---|---|---|
| `adb` + Android SDK | Android 全部能力 | `brew install --cask android-platform-tools` + Android Studio |
| `hvigorw`/`ohpm`/`hdc` | 鸿蒙全部能力 | 装 DevEco Studio 并把工具加入 PATH |
| Maestro | UI 自动化 | `curl -Ls https://get.maestro.mobile.dev \| bash` |

一条命令查看当前状态：`python3 .claude/skills/_apm/scripts/apm_doctor.py`

---

### P1 — 决定体系上限

#### 4. 数据上报后端选型（含鸿蒙，这是第一约束）

⚠️ **鸿蒙不是附加项，是选型的第一约束。** Sentry / Firebase Crashlytics / Detox / OTel
在鸿蒙上**全部不可用**（Sentry 只有社区 beta 移植）。**先定鸿蒙方案，再定其他端**，反过来会推翻重来。

| 路线 | 适用 | 关键取舍 |
|---|---|---|
| **Sentry self-hosted** | 数据合规要求高、有运维能力 | 数据不出境；但架构重（PG+Redis+Kafka+ClickHouse），**4核16G+50GB 起**；`@sentry/react-native@8` 要求 ≥25.11.1 |
| **腾讯 Bugly** | 要含鸿蒙的轻量方案 | 当前免费；**鸿蒙能力最全**（三类崩溃+FPS+内存+符号表还原）；跨三端统一控制台 |
| **AGC APMS** | 鸿蒙零成本兜底 | **零 SDK 集成**；但仅 1 个月数据、不支持自定义 userId |
| 火山 APMPlus / 阿里 ARMS RUM | 鸿蒙备选 | 鸿蒙原生支持 |

**推荐组合**：iOS/Android/RN 用 Sentry self-hosted（或 Bugly）；
鸿蒙用 **AGC APMS 零成本兜底 + Bugly 鸿蒙版做深度分析**。

#### 5. 把线上数据接进 Agent（MCP）

这是**能力层**的补全，直接决定 Agent 能看到什么。**按"能否拿到数据"排序，不是按名气**：

| MCP / 工具 | 作用 | 状态 |
|---|---|---|
| `mobilebuildmcp mcp` | iOS 构建/模拟器/UI自动化/LLDB | ✅ **已安装并连通** |
| **Sentry MCP** | 拉取/查询崩溃与 issue | 已配置（远端 HTTP），**待你 `/mcp` 完成 OAuth** |
| **`callstack/agent-device`** | **官方支持 iOS / Android / HarmonyOS / TV / web** 的设备驱动 | ⭐ **优先装** —— 唯一覆盖鸿蒙的方案 |
| `metro-mcp` | RN/Hermes CDP 调试 | ⭐ 解决 **Hermes 只允许一个 CDP 连接**的冲突（内置 CDP 代理，让 DevTools 与 MCP 共存） |
| `agent-react-devtools` | 组件树 / re-render profiling | RN 渲染性能归因用 |
| 鸿蒙 `harmonyos-mcp` / `harmony-build` | hdc/hvigorw/模拟器，含 profiler 子包 | 鸿蒙专项补充 |
| Firebase MCP | Crashlytics / Performance | ⚠️ **本机实测不可用**（工具定义需从 Google 远端拉取，域名不可达） |

⚠️ **Firebase 的实测结论对选型有直接含义**：它依赖 Google 基础设施，
国内网络下不可靠。**你的数据后端不应选 Firebase** —— 见 §2 P1-4。

安装命令与实测结果见 `docs/mcp-setup.md`（含网络可达性实测表）。

#### 6. CI/CD 性能门禁

- [ ] **专用机器 + 固定设备**（iOS baseline 按机型绑定，换机即失效；模拟器数据不准）
- [ ] **release 构建**跑性能测试（debug 数据不可用）
- [ ] 基线文件**并入版本库**
- [ ] 门禁条件：`apm_baseline.py compare` 退出码 `2`
- [ ] ⚠️ **性能测试失败必须明确告警** —— 静默失败是最常见的坑

---

### P2 — 决定长期存活

#### 7. 测试基础设施规范化

- [ ] **先立 `testID` / accessibility label 规范，再写用例**
      —— 跨所有框架的最大长期成本不是接入也不是 flakiness，**而是 selector 维护**
- [ ] Maestro 跑冒烟（CI 零成本）+ Detox 跑边界用例（RN flakiness <2%）
- [ ] 核心流程冒烟套件（每次变更后自动跑）

#### 8. 组织流程（技术之外，但决定成败）

- [ ] **准入项**（借自抖音实践）：新增动态库、新增 `+load`/静态初始化、新增启动任务
      **必须 Code Review**
- [ ] 灰度 + AB 实验：**线上数据既是优化指南针，也是唯一衡量方式**
- [ ] 指标看板：以 **pct50** 为主，配合 pct90

---

## 三、业界标杆做对了什么（两条关键启示）

### 启示 1：Netflix —— **profiler 给的是"改进潜力估计"，canary 才是"真值验证"**

> 这一条直接印证了本项目的铁律 3（必须复测）与铁律 4（绝不伪造）。

**落地含义**：Agent 报的"提升 X%"永远是**受控环境下的估计**。
真实效果必须由**灰度/AB 实验**确认。不要把本地测量结果当成线上结论。

### 启示 2：Meta —— **fix-forward PR，路由回原作者 review，不自动合并**

> Meta 的 AI 劣化修复流程产出 PR，但**不自动合并**，交回原作者评审。

**落地含义**：这套体系里 Agent 的边界应该是：

| Agent 可以自主做 | Agent 必须停下来问人 |
|---|---|
| 测量、复测、对比基线 | 修改业务逻辑 |
| 定位根因（有证据） | 新增/替换三方 SDK 或后端 |
| 生成修复补丁 | 合并代码 |
| 跑回归验证 | 更新基线（应由人确认） |
| 建 issue、排序、写报告 | 架构级重构 |

这与 `apm-loop` 技能里的「停止条件」一致。

**Meta 的量化收益**（供立项参考）：人工劣化排查 **约 10 小时 → 约 30 分钟**。
其检测底座 FBDetect（SOSP '24 最佳论文）可捕捉小至 **0.005%** 的时序劣化，监控约 80 万条时间序列。

---

## 四、落地路线图

| 阶段 | 目标 | 出口标准 |
|---|---|---|
| **第 1 周** | 地基 | `apm-doctor` 全绿（Android/鸿蒙工具补齐）；`/apm-init` 在真实工程跑通；拿到首份启动基线 |
| **第 2–3 周** | 能看见 | 埋点 SDK 上线（启动分段 + 内存水位 + 白屏超时 + 崩溃四层）；符号化接入 CI 且做成门禁 |
| **第 4–5 周** | 能发现 | 后端选型落地（含鸿蒙）；Sentry/Firebase MCP 接入 Agent；CI 性能门禁跑通（退出码 2 生效） |
| **第 6–8 周** | 能闭环 | `/apm-check` → 自动建 issue → 根因定位 → 修复 → `/apm-verify` 全流程无人干预跑通（除合并外） |
| **持续** | 防劣化 | 准入项 CR 生效；灰度/AB 验证线上效果；定期更新基线 |

---

## 五、反面清单（不要做）

- ❌ **不要在没有线上数据时声称"体系已建成"** —— 本地测量只是脚手架
- ❌ **不要让 Agent 自动合并修复** —— Meta 都不这么做
- ❌ **不要把本地测量结果当成线上结论** —— canary 才是真值
- ❌ **不要先建 iOS 方案再补鸿蒙** —— 会推翻重来
- ❌ **不要在符号化链路没修好时分析崩溃** —— 白做
- ❌ **不要上 Hermes Sampling Profiler 而不评估内存影响** —— `sampledStacks_` 会无限增长
- ❌ **不要信"平均 FPS 60 所以不卡"** —— 要看 p95
- ❌ **不要用 RSS 判断 OOM** —— 系统按 PSS 判定

---

## 六、跨 Agent（opencode 等）使用

单一真源在 `plugins/mobile-apm/`，用生成器编译出可移植树：

```bash
python3 tools/build-portable.py          # 产出 dist/
cp -R dist/. /path/to/your-app/          # ⚠️ 必须用 dist/. 而非 dist/*（点文件）
```

产出 `AGENTS.md`（Claude Code ≥2.1.277 / opencode / Codex 都读）+ `.claude/skills/`
（**opencode 原生就读这个目录**）+ `.opencode/agents|commands|plugins`。

**兼容性要点**（已在生成器中自动处理）：
- 技能名必须等于目录名，`name` + `description` 缺一 opencode 下**完全不加载**
- `${CLAUDE_PLUGIN_ROOT}` 在 opencode 里是字面量 → 生成器重写为工程根相对路径
- 正文不点名工具（opencode 工具名全小写）
- hook 差异最大：opencode 无 settings hooks，用 `.opencode/plugins/apm-hook-bridge.ts`
  把 `.claude/hooks/*.sh` 当子进程调用，**一份 hook 逻辑服务两个 harness**

⚠️ **每个 harness 只选一条安装路径**：插件与手工拷贝叠加会导致 skill/hook 重复加载。
