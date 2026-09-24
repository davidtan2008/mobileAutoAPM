# 移动端 APM 技术选型（iOS / React Native / Android / HarmonyOS）

> 调研基线：2026-09。**动手前必须先用 `apm-doctor` 探测本机实际有什么，不要假设。**

## 0. 最重要的一条：鸿蒙是选型的第一约束

以下方案在 HarmonyOS 上**完全不可用**，不要浪费时间尝试：

| 不可用方案 | 原因 |
|---|---|
| Sentry（官方） | 官方平台列表无 HarmonyOS；只有社区 beta 移植 `@ohos-ports/sentry-react-native@8.27.0-beta.0`（**未验证**） |
| Firebase Crashlytics | 依赖 GMS；RN Firebase 官方矩阵标记 Crashlytics 在 "Other" 平台 🔴 |
| Detox | 无鸿蒙 driver |
| OpenTelemetry | 无鸿蒙 SDK |

鸿蒙**可用且成熟**的只有这几条路：

| 方案 | 类型 | 能力 |
|---|---|---|
| **HiAppEvent**（官方首选） | 系统能力，免费 | `APP_CRASH` / `APP_FREEZE` / `APP_LAUNCH` / `SCROLL_JANK` / `RESOURCE_OVERLIMIT`；`params.uuid` 是故障特征码（用于聚类） |
| **hiTraceMeter** | 系统能力 | `startTrace/finishTrace` 分阶段打点 → `hdc shell hitrace` 导出 → DevEco Profiler 看瀑布图 |
| **hidebug** | 系统能力 | PSS / RSS / heapdump |
| **jsLeakWatcher**（API 12+） | 系统能力 | **鸿蒙独有的 JS 对象泄漏检测**，对 RNOH 排查 JS 泄漏很有价值。注意 `enable()` 默认关闭 |
| **AGC APMS** | 商业，当前免费 | **零 SDK 集成**，控制台开启即可。缺点：仅 1 个月数据、不支持自定义 userId/tag |
| **腾讯 Bugly 鸿蒙版** | 商业，当前免费 | **第三方里鸿蒙能力最全**：三类崩溃 + 卡顿(FPS/挂起率) + 内存(PSS/VSS/JS堆) + 符号表还原(SO UUID + nameCache + sourceMaps) |
| 火山 APMPlus / 阿里 ARMS RUM | 商业 | 鸿蒙原生支持（ohpm `@volcengine/apmplus`、`@alibabacloud_rum/harmony_sdk`，均 2.1.1） |

**鸿蒙崩溃取数有一个硬限制**：崩溃/冻屏时进程已退出，**只能在下次启动时取**。
若要覆盖"崩溃后再没打开过 App"的场景，用 `FaultLogExtensionAbility`（API 21+），但**它只有 10 秒处理时间**。

---

## 1. 崩溃捕获：RN 必须分层，单一方案一定有盲区

这是 RN 崩溃治理最容易被做错的地方。**每一层覆盖的错误类型不同，缺一层就是一类盲区**：

| 错误类型 | 必须用哪个机制 | 常见错误 |
|---|---|---|
| 同步渲染/生命周期错误 | React ErrorBoundary / `react-native-error-boundary` | 以为它能捕获所有 JS 错误 ❌ |
| 未处理 JS 异常 | `ErrorUtils.setGlobalHandler` | 没接，导致异步错误全丢 |
| 未处理 Promise rejection | Hermes `enablePromiseRejectionTracker` | 完全遗漏，是最大盲区 |
| 原生崩溃（OC/Swift/Java/Kotlin/C++/Hermes 字节码 panic） | 原生崩溃库（KSCrash / SentryCrash / Bugly） | 用 JS 方案兜原生崩溃 ❌ |

**JS 崩溃与原生崩溃的关联**靠 `@sentry/react-native` 的 `NativeLinkedErrors`。

### 平台方案速查

| 平台 | 首选 | 备注 |
|---|---|---|
| iOS | `sentry-cocoa` 9.29 / KSCrash 2.6.0 | KSCrash 2.6 新增 Watchdog monitor + MetricKit monitor plugin。**只做采集不做上报** |
| RN | `@sentry/react-native` 8.28.0 | 生态最完整；`NativeLinkedErrors` 关联 JS↔Native |
| Android | sentry-android / Bugly | LeakCanary 只管内存泄漏，不管崩溃 |
| 鸿蒙 | HiAppEvent + Bugly 鸿蒙版 | 见第 0 节 |

**已停滞，谨慎选**：腾讯 Matrix 最后一次 Release 停在 **2023-03-21**（v2.1.0），近 3 年无更新。

---

## 2. 符号化：这里坑最多，做错等于没做崩溃分析

### 2.1 四类产物

| 产物 | 平台 | 生成 | 关键坑 |
|---|---|---|---|
| `dSYM` | iOS | Xcode Archive | 必须 `DEBUG_INFORMATION_FORMAT=dwarf-with-dsym` |
| `mapping.txt` | Android | R8/ProGuard | 必须与 `versionCode` 严格对应 |
| **RN sourcemap** | iOS/Android | `npx react-native bundle --sourcemap-output` | ⚠️ **iOS 默认不生成！** 必须在 "Bundle React Native code and images" 阶段手动导出 `SOURCEMAP_FILE` |
| **Hermes 字节码 sourcemap** | iOS/Android | `hermesc -O -emit-binary -output-source-map` → `.hbc.map` | ⚠️ Hermes 栈长这样：`p@1:132161`。必须用 `compose-source-maps.js` 把 `.hbc.map` 与 Metro sourcemap **合成一张**，再喂 `metro-symbolicate`。**两步合成，漏一步就还原不出行号** |

### 2.2 关联用 debug ID，不要用版本号

**热修后版本号极易错位**。唯一可靠的关联方式是 per-build 唯一的 **debug ID**，同时写入 bundle 与 sourcemap：

```bash
sentry-cli sourcemaps upload --debug-id-reference
```

### 2.3 一个正常但反直觉的现象

Hermes bundle 不是合法 JS，`sentry-cli` 会**跳过上传并创建一个 0 字节占位文件**指向 sourcemap。
**这是正常的**，不是上传失败 —— 不要在这里浪费时间 debug。

### 2.4 反面案例

**不要用 Fishhook 做线上符号化**：首次调用耗时极高（大型 App 冷启 200ms+），且抖音明确说"最好不要带到线上"。

---

## 3. 启动时长

### 3.1 度量手段与各自的硬限制

| 手段 | 平台 | 关键限制 |
|---|---|---|
| MetricKit `MXAppLaunchMetric` | iOS | ⚠️ **24 小时延迟**；需用户开启"与 App 开发者共享"；**模拟器不支持** |
| MetricKit `MXAppLaunchDiagnostic` | iOS 16+ | 栈未符号化，需按 `binaryUUID` 匹配 dSYM + `atos` |
| XCTest `XCTApplicationLaunchMetric` | iOS | ⚠️ **baseline 按设备型号绑定，换机即失效**；模拟器数据不准 |
| Sentry `App Start` | RN/iOS/Android | 开箱即用，自动区分冷/温/热启动。但**只有总量，没有 RN 内部分段** |
| Android Macrobenchmark | Android | 必须 release 构建；`timeToFullDisplay` 不调 `ReportDrawn` 会静默退化成 TTID（低估） |
| 鸿蒙 `APP_LAUNCH` | HarmonyOS | ⚠️ **模拟器不支持订阅** |

### 3.2 RN 启动黑盒必须自建分段打点

各手段都拿不到 RN 内部阶段。**唯一能拆清黑盒的办法是自建埋点**：

```
Native 启动 → ReactInstanceManager 初始化 → bundle 加载/解析
→ Hermes 字节码加载 → 根组件挂载 → 首屏渲染 → 业务初始化
```

### 3.3 抖音《iOS 启动优化实战篇》方法论（可直接复用）

**埋点口径**：起点统一为 `进程创建`；终点是 Launch Image 消失首帧。
- iOS 13+ 对齐 `applicationDidBecomeActive`，最接近 Apple 自己的 `CA::Transaction::commit`
- 观察者选型有坑：**iOS 13+ 用 `kCFRunLoopBeforeTimers`，iOS 13 以下用 `CFRunLoopPerformBlock`**

**无侵入分段**：`进程创建(sysctl)` → `最早 +load(AAA 前缀 Pod)` → `didFinishLaunching` → `首屏渲染完成`

**优化四步法**：**删 → 延迟 → 并发 → 更快**（都不行才考虑让代码本身更快）

**高价值手段**：
- 动态库数量 Apple 建议 **< 6 个**；能转静态就转
- **二进制重排**：`ld -order_file`，抖音方案覆盖 ~90% 符号
- **段重命名**：把 `__cstring`/`__objc_methname` 移到 `__RODATA`，减少 Page In 解密耗时
- 三方 SDK 延迟初始化（抖音下线 Fabric 后 pct50 快约 70ms）
- ⚠️ **不要删 `tmp/com.apple.dyld`** —— 存 iOS 13+ 启动闭包，删了冷启动变慢

**工程纪律**（比技术手段更重要）：
- 研发期防劣化：定时打包 → 自动化测试 → 上报看板
- 准入项：新增动态库、新增 `+load`/静态初始化、新增启动任务必须 Code Review
- 控制变量：关 iCloud/不登录 AppleID/飞行模式、降温、重启静置、多次测量取平均与方差
- 线上用 **pct50** 为主指标，配合 AB 实验

---

## 4. 渲染 / 卡顿 / 白屏

### 4.1 卡顿

| 手段 | 平台 | 备注 |
|---|---|---|
| MetricKit `MXHangDiagnostic` | iOS 14+ | ⚠️ **只有 stacktrace，没有异常类型和消息**；栈未符号化 |
| MetricKit `MXAnimationMetric` | iOS | `scrollHitchTimeRatio`。字段类型各来源矛盾（**未验证**） |
| Android `JankStats` | Android | ⚠️ API 16 以下无动作，**API 24+ 才可靠，API 31+ 才精确**；`jankHeuristicMultiplier` 默认 2 |
| Macrobenchmark `FrameTimingMetric` | Android | 核心指标是 **P95 `frameOverrunMs`**（滚动中最坏一帧的错过量） |
| 鸿蒙 `SCROLL_JANK` | HarmonyOS | 单帧 >50ms 即上报。⚠️ **采栈只支持 ARM64，且一个进程一天至多采一次** |

**RN 卡顿归因的关键**：必须**分离观测 JS FPS 与 UI FPS**，才能判断瓶颈在 JS 线程还是 UI 线程。

### 4.2 白屏检测

**结论：2026 年没有单一可用的 RN FCP API。** 必须组合三条路线：

1. **启动链路分段打点** + 首屏渲染点 + FCP/TTI KPI + 超时告警（主力）
2. **截图/像素比对**（原生侧抓 RootView 快照）—— 开销大，需脱敏
3. **View 树检测**（根节点无子节点/子节点数为 0）—— 有误报

配合 **Native Splash / 骨架屏遮蔽**（`react-native-bootsplash` 7.3.3）把"白屏等待"变成"品牌感知"，这是性价比最高的感知优化。

**鸿蒙白屏有独门利器**：监听渲染子进程崩溃 `onRenderExited`（API 9+），
`RenderExitReason` 可直接区分 `ProcessOom(3)`（内存不足被杀）等根因。

---

## 5. 内存

### 5.1 iOS FOOM 是官方能力缺口（必须自建）

**MetricKit `MXDiagnosticPayload` 只包含四类诊断**：
`crashDiagnostics` / `hangDiagnostics` / `diskWriteExceptionDiagnostics` / `cpuExceptionDiagnostics`

👉 **不含 jetsam 诊断**。Apple 增强请求 **FB9972410**（请求 jetsam 时上报内存占用）**至今未落地**。

**唯一能拿到的官方信号**是 `MXAppExitMetric` 的两个**计数**（无详情）：
- `cumulativeMemoryResourceLimitExitCount`（超内存上限）
- `cumulativeMemoryPressureExitCount`（**jetsam，系统回收**）

**落地做法（三件套交叉验证）**：
1. **自建内存水位环形打点**（性价比最高，能给出"崩溃前内存曲线"）
2. Sentry watchdog / OOM 终止跟踪
3. `MXAppExitMetric` 计数交叉验证

⚠️ 这类崩溃**永远不会出现在普通崩溃报告里** —— 如果只做崩溃捕获，会完全漏掉 FOOM。

### 5.2 RN 内存

- **Hermes heap snapshot** 是唯一官方堆分析途径（DevTools → Memory）
- 排查方法：**同页面进出 20–30 次放大** → 抓堆快照对比 → 看 **detached React 树**（被 listener/闭包持有的 Fiber 节点）→ 查原生节点上的 `__reactFiber$` 引用
- ⚠️ **Hermes Sampling Profiler 默认开启会导致 `sampledStacks_` 内存无限增长**。排查 RN 内存增长时**先怀疑它**（RNOH PR #3438 提议默认关闭）

**RN 常见泄漏源清单**：未清理的 `useEffect`（timer/interval、`DeviceEventEmitter`/`AppState`/`Keyboard` 监听、navigation listener、socket、长请求）、state 数组无界增长、navigation params 传大对象、自定义原生模块未释放引用。屏幕级 effect 应用 `useFocusEffect` 自动清理。

---

## 6. 自动化测试

**2026 主流结论**：**Maestro 作默认 + Detox 跑边界用例**。

| 框架 | 定位 | 硬限制 |
|---|---|---|
| **Maestro** | 默认首选 | CI 接入接近零成本（一个二进制 + 一个 YAML，无原生构建）。⚠️ z-index/绝对定位的第三方组件元素选择有问题 |
| **Detox** | RN 边界用例 | RN 上 flakiness <2%，但 ⚠️ **iOS 不支持真机**（仅模拟器）、5–10 分钟/套件、**与 RN 版本强耦合** |
| XCUITest | iOS 性能回归 | 与 `XCTMetric` 天然打通 |
| Appium | 跨平台兜底 | ⚠️ 慢（首页 ~24s vs Maestro ~12s）；selector 维护税 30–50% |
| 鸿蒙 | 无成熟方案 | **未验证** |

> **跨所有框架的最大长期成本不是接入也不是 flakiness，而是 selector 维护。**
> 稳定 `testID` 与 accessibility label 是框架无关的最佳实践。

---

## 7. 后端选型

| 方案 | 适用 | 关键限制 |
|---|---|---|
| Sentry Self-hosted | 数据合规要求高 | 架构重：PostgreSQL+Redis+Kafka+ClickHouse+ZooKeeper。**4 核 16G + 50GB SSD 起**。⚠️ **不要 clone master**，从 Release 下稳定版；`@sentry/react-native@8` 的 CLI v3 要求 **≥ 25.11.1** |
| Sentry SaaS | — | ⚠️ **国内仅"部分可用"**：端点常被封锁/限流，导致上报超时、错误可见性缺口 |
| 腾讯 Bugly | 含鸿蒙的轻量方案 | 当前免费，跨三端统一控制台 |
| AGC APMS | 鸿蒙零成本兜底 | 零 SDK 集成，但仅 1 个月数据、不支持自定义 userId |
| OpenTelemetry | 标准化导出 | Swift 官方 **Stable**；**Kotlin/KMP 侧仍 experimental**（API 可能 breaking change 且不通知） |

---

## 8. 三条结论性建议

1. **鸿蒙是选型第一约束**。先确定鸿蒙方案，再决定其他端 —— 反过来做会推翻重来。
2. **RN 崩溃治理必须分层**，四层机制各管一类错误，缺一层就是盲区。`NativeLinkedErrors` 是串起 JS 与原生崩溃的关键。
3. **iOS FOOM 必须自建**。不要指望 MetricKit 给 jetsam 诊断，它没有，且短期内不会有。
