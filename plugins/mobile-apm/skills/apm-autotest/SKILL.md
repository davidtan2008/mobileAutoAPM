---
name: apm-autotest
description: This skill should be used when the user asks for automated testing, requirement verification, regression verification, or having the agent find problems by itself — "自动化测试"、"需求验证"、"回归测试"、"自动发现问题"、"写测试用例"、"验证需求有没有实现"、"e2e"、"UI 自动化"、"性能回归"、"CI 门禁" — on iOS / React Native / Android / HarmonyOS. Covers framework selection, test authoring, requirement-to-test translation, and wiring results back into the APM loop.
version: 0.1.0
---

# 自动化测试 / 需求验证 / 自动发现问题

**必读**：`${CLAUDE_PLUGIN_ROOT}/references/stack-selection.md` §6（框架选型）。

本技能是 APM 闭环的**感知器官** —— 没有它，Agent 就只能等用户报障，
无法"自动发现问题"。

---

## 一、框架选型

**2026 主流结论：Maestro 作默认 + Detox 跑边界用例。**

| 框架 | 定位 | 硬限制 |
|---|---|---|
| **Maestro** | **默认首选** | CI 接入接近零成本（一个二进制 + 一个 YAML，无原生构建步骤）。⚠️ z-index/绝对定位的第三方组件元素选择有问题 |
| **Detox** | RN 边界用例 | RN 上 flakiness <2%（灰盒同步），但 ⚠️ **iOS 不支持真机**、5–10min/套件、**与 RN 版本强耦合** |
| XCUITest | iOS 性能回归 | 与 `XCTMetric` 天然打通，是性能基线的唯一官方路径 |
| **鸿蒙** | **无成熟方案** | 需自建（**未验证**） |

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_doctor.py" --json   # 先确认本机有什么
```

⚠️ **跨所有框架的最大长期成本不是接入，也不是 flakiness，而是 selector 维护。**
**稳定 `testID` 与 accessibility label 是框架无关的最佳实践** —— 先立规范，再写用例。

---

## 二、需求验证（把需求翻译成可执行的验证）

当用户说"验证需求 X 有没有实现"时，按这个流程走：

### 步骤 1：把需求拆成可判定的断言

**需求通常是模糊的，测试必须是明确的。**

| 模糊需求 | 可判定的断言 |
|---|---|
| "支持暗黑模式" | 切换到暗黑模式后，首页背景色 = `#000000`，文字色 = `#FFFFFF` |
| "列表加载快" | 冷启动 → 列表首屏可见耗时 < 800ms（p50，3 次中位数） |
| "登录失败有提示" | 输入错误密码 → 出现含"密码"字样的提示文案 |

**拆不出可判定断言时，必须先向用户澄清需求，不要自己假设。**

### 步骤 2：写测试流程

```yaml
# .apm/flows/<需求ID>.yaml  (Maestro)
appId: com.example.app
---
- launchApp
- tapOn: "登录"
- inputText: "wrong_password"
- takeScreenshot: .apm/runs/<时间>/shots/login_fail
- assertVisible: ".*密码.*"
```

### 步骤 3：执行并留证

```bash
maestro test .apm/flows/<需求ID>.yaml
```

**必须留证**：截图、日志、耗时数据。**没有证据的"验证通过"不算验证。**

### 步骤 4：报告结论

按 `metrics-definitions.md` §6 的报告模板输出。
**如果需求没实现，如实报告"未通过 + 证据"，不要粉饰。**

---

## 三、自动发现问题（闭环的触发端）

### 3.1 功能回归

每次代码变更后跑核心流程（冒烟套件），失败即告警。

### 3.2 性能回归（更有价值，也更容易被忽略）

```
1. 用 XCUITest + XCTMetric / Maestro 采集性能指标
2. 解析结果 → 规范化 JSON
3. 与基线对比 → apm_baseline.py
4. 劣化则开 issue，进入 apm-loop 的 Phase 3
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_baseline.py" compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/<本次>/metrics.json
# 退出码：0=无劣化  2=有劣化（可作 CI 门禁）  1=样本/参数问题
```

**退出码 2 可直接作为 CI 门禁失败条件。**

### 3.3 崩溃/异常扫描

从崩溃平台拉取最近 N 天的崩溃，聚类后按 `影响用户数 × 严重度` 排序 → 进入 `apm-crash`。

---

## 四、CI 集成

```yaml
# 关键点（不是完整配置）
- 用 **release 构建**跑性能测试（debug 数据不可用）
- 固定设备/模拟器型号（基线按机型绑定，换机即失效）
- 性能数据存为 artifact，不要只打日志
- 基线文件**并入版本库**，否则换台机器就失效
- 性能测试失败要**明确告警**，否则会静默失败（这是最常见的坑）
```

⚠️ **iOS 性能基线的硬限制**：`XCTApplicationLaunchMetric` 的 baseline
**按设备型号绑定，换机即失效**；模拟器数据不准；真机测试有系统权限弹窗干扰。
建议**专用机器**跑性能回归。

---

## 五、验证的纪律（与 apm-loop 铁律一致）

1. **测试必须能失败** —— 写完先跑一次看它真的会失败，否则可能是无效断言
2. **不许为了让测试通过而改断言** —— 测试挂了先查是代码问题还是需求变了
3. **性能断言必须带口径** —— "快了"必须写成"p50 < Xms，release 构建，iPhone 17"
4. **留证** —— 截图/日志/数据文件路径，写进报告
5. **需求验证失败要如实说** —— 不要用"基本实现"之类模糊表述

## 常见误判

| 误判 | 真相 |
|---|---|
| "测试通过了，说明没问题" | 先确认这个测试**曾经失败过**，否则可能是无效断言 |
| "CI 里性能测试跑过了" | 检查它是否**真的会因劣化而失败**，很多是静默通过 |
| "换台机器数据应该差不多" | iOS 性能基线**按机型绑定**，换机即失效 |
| "UI 测试太脆，先不写" | 脆弱源于 selector 不稳定，用 `testID` 可解决 |
| "需求验证用模拟器就行" | 功能可以，**性能必须真机** |
