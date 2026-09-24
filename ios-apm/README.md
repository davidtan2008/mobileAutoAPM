# ios-apm

iOS 原生侧 APM 埋点。与 [`rn-apm`](../rn-apm) 配对，补齐 **iOS + React Native** 双覆盖。

## 它补的两块缺口

| 缺口 | 不做的后果 |
|---|---|
| **启动分段打点（含 pre-main）** | 纯 Swift 埋点最早的生效点是 `App.init()` —— 在那之前的 dyld、静态构造、运行时初始化全是盲区 |
| **内存水位** | ⚠️ **MetricKit 的 `MXDiagnosticPayload` 不含 jetsam 诊断**（Apple 增强请求 FB9972410 多年未落地），**不自建就完全看不见 FOOM** |

## 已验证的背景

本包的手法来自一个真实工程的实战验证（iPhone 13 真机）：

```
纯 Swift 埋点测得启动 219ms
  ↓ 补上 C 构造函数
发现其中 219ms 全部发生在埋点被触碰之前 —— 整段启动都没有归因
```

根因：`@State private var x = SomeService()` 这类**属性初始化器先于 `init()` 执行**，
而 tracker 直到 `init()` 才被触碰。**埋点把最大的一块成本藏起来了。**

本包用两层保障解决：C 构造函数（早于一切 Swift）+ `activate()` 必须放在最早位置。

## 集成

### 1. 加依赖

Xcode → File → Add Package Dependencies → 指向本目录；
或 `Package.swift` 里 `.package(path: "../ios-apm")`。

### 2. 启动最早处 activate

```swift
import IOSAPM

@main
struct MyApp: App {
    init() {
        apmActivate()                       // ← 第一行
        apmMark(.appInit)

        let env = AppEnvironment()          // 依赖注入
        apmMark(.appReady)
        _environment = State(initialValue: env)
    }

    var body: some Scene {
        let _ = apmMark(.appBody)
        WindowGroup { RootView() }
    }
}
```

### 3. 首屏打点（**延迟一个 runloop**）

```swift
.onAppear {
    DispatchQueue.main.async { apmMarkFirstFrame() }   // 逼近 CA::Transaction::commit
}
```

> ⚠️ 直接打点会早于实际提交，**低估启动耗时**。
> 一旦用了延迟打点，**做对照实验时对方必须用同一终点定义**，否则数字不可比。

### 4. 内存水位（FOOM 归因）

```swift
MemoryWatermark.shared.observeAppLifecycle()   // 前后台自动暂停/恢复
MemoryWatermark.shared.start()
```

App 切后台自动停采样 —— 后台采样无意义、耗电，且会污染判断。

## 日志输出

```
IOSAPM pre-main: 11 ms
IOSAPM launch total: 257 ms
IOSAPM stages (since/+delta ms): processStart=16/+16 appInit=17/+1 appReady=22/+5
  appBody=140/+117 rootBody=163/+22 homeBody=170/+7 firstFrame=257/+87
```

抓取：

```bash
# 模拟器
xcrun simctl spawn booted log show --last 30s --info --style compact \
  --predicate 'subsystem == "com.iosapm"'

# 真机
idevicesyslog -u <UDID> | grep -a IOSAPM
```

> ⚠️ `log show` 默认不显示 info 级别，**必须加 `--info`**。

## 关键实现点（都是踩过的坑）

| 点 | 说明 |
|---|---|
| **`phys_footprint`，不是 `resident_size`** | 系统按 footprint 判定内存压力；用 RSS 会显著高估（共享库被重复计算） |
| **`premain_millis()` 取不到时返回 -1，不是 0** | 返回 0 会被误读成"pre-main 极快" —— 这是危险的假数据 |
| **环形缓冲定容** | 一个用来发现内存泄漏的工具，自己绝不能泄漏内存 |
| **未标首屏时 `makeReport()` 返回 nil** | 宁可不报，也不报一个不完整的启动耗时 |
| **默认打印完整阶段拆分** | 只打印总数等于把最大的一块藏起来（真实踩过的坑） |
| **泄漏看斜率，不看峰值** | 峰值下降可能只是缓存差异；"斜率从增长变为平稳"才是修好的证据 |

## 测试

```bash
swift test        # 11 个测试
```

覆盖：环形缓冲 10 万次写入不增长、回绕顺序、斜率计算边界、
footprint 可读性与合理性、activate 幂等、阶段名稳定性
（**阶段名是跨版本基线的锚点，改名会破坏历史可比性**）。

## 已知限制（如实说明）

| 限制 | 说明 |
|---|---|
| **未在真实工程端到端验证** | 手法来自真机验证，但本包本身尚未在真实 App 里跑过 |
| **不采集原生崩溃** | 需配合 KSCrash / SentryCrash / Bugly；本包只做启动与内存 |
| **无持久化** | 内存水位只在内存里；若进程被杀，样本一并消失。需要跨启动归因时，应把 `current()` 的结果随崩溃报告落盘 |

## 与 Agent 工具链的衔接

启动日志可直接喂给基线工具做劣化判定：

```bash
python3 ../plugins/mobile-apm/scripts/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/<本次>/metrics.json
# 退出码 0=无劣化  2=有劣化（可作 CI 门禁）
```

内存报告用 `growthDescription`（MB/小时）判断是否疑似泄漏。
