---
name: apm-crash
description: This skill should be used when the user asks to analyze, triage, locate root cause of, or fix crashes — "崩溃分析"、"崩溃日志"、"定位根因"、"crash"、"闪退"、"符号化"、"dSYM"、"sourcemap 还原"、"FOOM"、"OOM"、"被系统杀了"、"崩溃率" — on iOS / React Native / Android / HarmonyOS. Covers the required layered capture, the symbolication pipeline and its pitfalls, clustering, prioritization, and root-cause procedure.
version: 0.1.0
---

# 崩溃：捕获 / 符号化 / 聚类 / 根因 / 验证

**必读**：`.claude/skills/_apm/references/stack-selection.md` §1（捕获）、§2（符号化）、§5.1（FOOM）。

## 铁律：没有符号化的崩溃报告等于没有报告

未符号化的栈只有地址，**看不出任何根因**。
如果拿到的崩溃报告没有符号，第一件事是**把符号化链路修好**，而不是去猜。

---

## 第一步：确认捕获是否完整（RN 最容易漏）

**RN 崩溃必须分层捕获，任何单一方案都有盲区。** 先确认这四层是不是都接了：

| 错误类型 | 必须的机制 | 缺了会怎样 |
|---|---|---|
| 同步渲染/生命周期错误 | React ErrorBoundary | React 渲染期错误直接整树卸载 |
| 未处理 JS 异常 | `ErrorUtils.setGlobalHandler` | 异步 JS 错误全部丢失 |
| 未处理 Promise rejection | Hermes `enablePromiseRejectionTracker` | **最大盲区**，网络/异步逻辑错误全丢 |
| 原生崩溃 | KSCrash / SentryCrash / Bugly | OC/Swift/Java/C++/Hermes 字节码 panic 全丢 |

> 自查：如果这个 App 只接了 ErrorBoundary，那它**只能看到同步渲染错误**，
> 其他三类崩溃在系统里是隐形的。**先补全捕获，再谈分析。**

**JS↔Native 关联**靠 `NativeLinkedErrors`（`@sentry/react-native`）。

### 平台手段

| 平台 | 方案 | 注意 |
|---|---|---|
| iOS | `sentry-cocoa` / KSCrash | KSCrash 只采集不上报，需自建上报 |
| RN | `@sentry/react-native` | 生态最完整 |
| Android | sentry-android / Bugly | — |
| 鸿蒙 | HiAppEvent `APP_CRASH` / `APP_FREEZE` | ⚠️ **崩溃时进程已退出，只能下次启动时取** |

---

## 第二步：符号化（坑最多的一步）

### 四类产物，一个都不能少

| 产物 | 平台 | 关键坑 |
|---|---|---|
| `dSYM` | iOS | 必须 `DEBUG_INFORMATION_FORMAT=dwarf-with-dsym` |
| `mapping.txt` | Android | 必须与 `versionCode` 严格对应 |
| **RN sourcemap** | RN | ⚠️ **iOS 默认不生成！** 必须在 "Bundle React Native code and images" 阶段手动导出 `SOURCEMAP_FILE` 环境变量 |
| **Hermes `.hbc.map`** | RN | ⚠️ 见下 |

### Hermes 堆栈的两步合成（最容易做错）

Hermes 的栈长这样：`p@1:132161` —— 这不是 JS 行号，是字节码位置。

正确流程：
```
hermesc -O -emit-binary -output-source-map  →  .hbc.map
        ↓
compose-source-maps.js 把 .hbc.map 与 Metro sourcemap 合成一张
        ↓
metro-symbolicate 用合成后的 map 还原
```

**漏掉合成这一步，就永远还原不出正确行号。** 这是 RN 崩溃分析最经典的坑。

### 用工具做，不要手工拼

```bash
S=".claude/skills/_apm/scripts"

# 1) 合成两张 map（关键步骤）—— 不匹配时会告警
python3 $S/rn_symbolicate.py compose \
  --outer index.bundle.hbc.map --inner index.bundle.map --out composed.map

# 2) 符号化堆栈
python3 $S/rn_symbolicate.py symbolicate --map composed.map --stack crash.txt

# 3) 诊断：探测某个位置是否对得上（排查 map 不匹配）
python3 $S/rn_symbolicate.py inspect --map composed.map --probe 1:62000

# 4) 校验一次构建的符号文件是否齐全（可直接作发版门禁）
python3 $S/rn_build_symbols.py verify --platform ios --build-dir ios/build --strict
```

⚠️ **拿一个不确定的符号化结果去分析根因，比不分析更糟** ——
会得出错误结论并据此改代码。工具会提示"距离命中映射 N 列，请核对 map"这类信号，
**看到就先去核对，不要硬着头皮往下分析**。

完整的构建期接入（含 iOS 默认不生成 sourcemap 的修法）见
`.claude/skills/_apm/docs/symbolication-pipeline.md`。

### 关联用 debug ID，不要用版本号

**热修后版本号极易错位。** 用 per-build 唯一的 debug ID：

```bash
sentry-cli sourcemaps upload --debug-id-reference
```

### 一个正常但反直觉的现象

Hermes bundle 不是合法 JS，`sentry-cli` 会**跳过上传并创建 0 字节占位文件**指向 sourcemap。
**这是正常的**，不是上传失败 —— 不要在这里浪费时间 debug。

---

## 第三步：聚类与排序（先聚类，再修）

**不要逐个修崩溃。** 长尾会把时间吃光。

**聚类键（按平台）**：
1. **鸿蒙**：HiAppEvent `params.uuid`（官方故障特征码）
2. **iOS**：符号化后栈的 top frame + 异常类型
3. **RN**：JS 错误 message + 栈指纹

**排序依据：影响用户数 × 单次严重度。优先修 top 3。**

> 反模式：因为某个崩溃"看起来好修"就先修它，而它只影响 3 个用户。
> 正确做法是先修影响 10 万用户的那个，哪怕它更难。

---

## 第四步：根因定位

**禁止跳过定位直接改代码。** 猜出来的根因不算根因。

每个崩溃必须回答四个问题：

1. **崩溃在谁的代码里**？业务 / 三方 SDK / 系统
   → 三方 SDK 的崩溃要找替代方案或升级版本，业务崩溃才谈修
2. **前置条件是什么**？特定机型？系统版本？操作序列？网络状态？
3. **必现还是概率性**？概率性的话分布特征是什么（内存压力下？特定并发下？）
4. **是不是回归**？对比历史版本，是不是最近某次改动引入的

### 定位工具

```bash
# iOS：LLDB 复现（mobilebuildmcp 提供）
mobilebuildmcp debugging attach --help
mobilebuildmcp debugging add-breakpoint --help
mobilebuildmcp debugging stack --help
```

- 必现崩溃 → LLDB 断点 + 看栈和变量
- 概率性崩溃 → 加日志/水位记录，跑压力场景复现
- 内存相关 → 见 `apm-memory` 技能

**定位完成标准**：能指出**具体哪一行在什么条件下**出错，且有证据（栈、日志、复现步骤）。

**定位不出来就如实说"根因未确定"**，给出下一步排查计划。**不要编一个根因。**

---

## 第五步：修复

- 遵守**单变量**原则，一次只改一处
- 记录"为什么这样改"（依据是哪个栈帧/哪条日志）
- 如果根因在三方 SDK：升级版本 / 找替代 / 在调用侧加防御
- 加**防御性代码**时注意：不要吞掉异常，要保留上报

---

## 第六步：验证

必须同时验证三件事：

1. **原崩溃不再复现** —— 用相同的前置条件复现（自动化脚本最佳）
2. **崩溃率下降** —— 灰度/线上数据确认（注意指标口径）
3. **没有引入新问题** —— 跑 `apm-autotest` 回归

⚠️ **不允许**："改了代码，看起来没问题，就算修好了"。
必须能复现出"改之前崩、改之后不崩"。

---

## iOS FOOM：不会出现在崩溃报告里的"崩溃"

**这是最容易被漏掉的一类。**

用户说"App 莫名其妙退出了"，但崩溃平台**没有任何记录** —— 因为 jetsam 杀进程
不产生崩溃信号。

⚠️ **MetricKit `MXDiagnosticPayload` 只包含四类诊断**：
`crashDiagnostics` / `hangDiagnostics` / `diskWriteExceptionDiagnostics` /
`cpuExceptionDiagnostics` —— **不含 jetsam 诊断**。
Apple 增强请求 **FB9972410** 提了 4 年仍未落地。

**唯一官方信号**是 `MXAppExitMetric` 的两个**计数**（无详情）：
- `cumulativeMemoryResourceLimitExitCount`
- `cumulativeMemoryPressureExitCount`（**jetsam**）

**归因必须自建**（三件套交叉验证）：
1. 自建内存水位环形打点（性价比最高，给出"崩溃前内存曲线"）
2. Sentry watchdog / OOM 终止跟踪
3. `MXAppExitMetric` 计数交叉验证

若怀疑 FOOM，转 `apm-memory` 技能。

---

## 常见误判

| 误判 | 真相 |
|---|---|
| "崩溃平台没记录，说明是用户自己退的" | 很可能是 FOOM/jetsam，需要自建归因 |
| "堆栈符号化出来是空的，是符号文件上传失败" | Hermes 的 0 字节占位是**正常现象** |
| "用了版本号关联 sourcemap，应该没问题" | 热修后版本号会错位，**必须用 debug ID** |
| "修了 top1 崩溃，崩溃率没降" | 可能 top1 影响面小，要看 `影响用户数 × 严重度` 的排序 |
| "ErrorBoundary 没捕获到，说明这个错误不严重" | 说明这类错误**根本没被捕获**，是盲区不是不重要 |
