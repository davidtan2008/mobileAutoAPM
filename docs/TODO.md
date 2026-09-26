# TODO

> 按优先级排列。完成的项移到 `PROGRESS.md`。

---

## P0 · 核心承诺验证

### T1 · 跑一次真实的自主闭环演练

**状态：已完成，但目标未达成。** 详见 `ROADMAP.md` 与被观测工程 `.apm/report.md`。
这次演练验证了平台会诚实报告“不可达 / 无显著改善”，也暴露了测量能力缺口。

### P0 · 补齐测量能力（第一版完成，跨平台适配器仍待做）

- [x] iOS 原生参数化测量 profile（`apm_measure.py`）
- [x] 方差诊断与判据建议（`apm_diagnose.py`）
- [x] baseline 质量闸门与回归测试
- [x] 物理 iPhone 端到端复测（clean commit 2b3a1ea，正式 baseline 已记录）
- [ ] Android / 鸿蒙 / RN 标准测量适配器

---

## P1 · 平台化重构（进行中）

- [x] 支柱 A 改造闭环（`ai_remediate.py`：生成 + 人审闸门 + 实测量化，iOS 工程 68 → 86）
- [ ] 市场调研：同类/竞品 GitHub 项目
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

- [x] 工具层：PATH 修复、基础能力 10/11；配置 `APM_PYMOBILEDEVICE3_BIN` 后 11/11
- [x] 安装 agent-device（含鸿蒙）/ firebase skills / expo / chrome-devtools
- [x] `rn-apm`：React Native 埋点 SDK（74 测试）
- [x] `ios-apm`：iOS 原生埋点 SDK（11 测试）
- [x] 跨 harness 编译器（`tools/build-portable.py`）
- [x] 符号化工具（`rn_symbolicate.py` / `rn_build_symbols.py`，31 测试）
- [x] 基线显著性判定（`apm_baseline.py`）
- [x] 白屏检测（`apm_white_screen.py`）
- [x] iOS 真机截图（`apm_screenshot.py`，pymobiledevice3 DVT 后端已真机验证）
- [x] 可行性前置判断与最小对照组闸门（`apm_feasibility.py`）
- [x] 首个成功自主闭环：结束任务译文持久化（红测→修复→120/120 单测 + Release 构建）
- [x] 仓库开源（github.com/davidtan2008/mobileAutoAPM）
