# TODO

> 按优先级排列。完成的项移到 `PROGRESS.md`。

---

## P0 · 核心承诺验证

### T1 · 跑一次真实的自主闭环演练

**为什么最重要**：项目定位的核心承诺是「用户提出需求后**全都自主完成**」。
已建成 7 技能 / 4 子 Agent / 双端 SDK / 数据平面，**但从未完整跑过一次无人干预的闭环**。

**验收标准**：给一个可判定的具体需求（如「冷启动压到 200ms 以内」「某页面白屏」），
从需求出发跑完 发现 → 定位 → 修复 → 验证，**中途不向用户提问**，最后交报告。
**没修好也要如实说**，不许包装。

**前置条件**：已全部满足（9/9 能力就绪、iOS/RN 双端 SDK、真机可连）。

---

## P1 · 平台化重构（进行中）

- [ ] 市场调研：同类/竞品 GitHub 项目
- [ ] 重新定位与路线图设计
- [ ] 按新定位重构仓库结构
- [ ] README / 架构图 / 原理图 / 实现细节文档
- [ ] 自我进化机制（定期更新 skill 与 MCP、沉淀大厂方案）
- [ ] 多 Agent 友好性（换任何 AI code agent 都能快速接手）
- [ ] GitHub 搜索优化

---

## P2 · 待外部条件

- [ ] Sentry MCP 鉴权（执行 `/mcp` 完成 OAuth）
- [ ] 符号化流水线在真实 CI 跑通
- [ ] 数据后端选型（含鸿蒙）：Sentry self-hosted / 腾讯 Bugly / AGC APMS
- [ ] Android / 鸿蒙真机验证（`adb devices` / `hdc list targets` 目前均为空）
- [ ] 原生 shim 真机验证（`rn-apm/docs/native-shims.md`）

---

## 已完成

- [x] 工具层：PATH 修复、9/9 能力就绪
- [x] 安装 agent-device（含鸿蒙）/ firebase skills / expo / chrome-devtools
- [x] `rn-apm`：React Native 埋点 SDK（74 测试）
- [x] `ios-apm`：iOS 原生埋点 SDK（11 测试）
- [x] 跨 harness 编译器（`tools/build-portable.py`）
- [x] 符号化工具（`rn_symbolicate.py` / `rn_build_symbols.py`，31 测试）
- [x] 基线显著性判定（`apm_baseline.py`）
- [x] 白屏检测（`apm_white_screen.py`）
- [x] 仓库开源（github.com/davidtan2008/mobileAutoAPM）
