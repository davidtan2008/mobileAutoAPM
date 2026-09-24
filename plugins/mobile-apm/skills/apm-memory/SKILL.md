---
name: apm-memory
description: This skill should be used when the user asks about memory usage, memory leaks, OOM, or FOOM — "内存涨"、"内存泄漏"、"内存检测"、"OOM"、"FOOM"、"被系统杀了"、"App 莫名退出"、"heap 增长"、"内存峰值高" — on iOS / React Native / Android / HarmonyOS. Covers correct metrics (PSS vs RSS), the iOS FOOM capability gap and its self-built workaround, RN leak detection procedure, and verification.
version: 0.1.0
---

# 内存：检测 / 归因 / 修复 / 验证

**必读**：`${CLAUDE_PLUGIN_ROOT}/references/metrics-definitions.md` §4。

## 铁律

1. **用 PSS 判断是否会被杀，不是 RSS**。RSS 会重复计算共享库
2. **内存"涨"不等于"泄漏"**。要先区分：缓存正常增长 / 泄漏 / 峰值过高
3. **没有内存水位曲线的内存分析都是猜**。先建立采样，再谈归因

---

## 第一步：明确要查哪一类问题

| 症状 | 类别 | 方向 |
|---|---|---|
| 用久了越来越卡、最后被杀 | **泄漏**（持续增长不释放） | 找未释放的引用 |
| 某个页面打开就爆 | **峰值过高** | 找大对象分配 |
| 后台被系统杀 | **后台占用过高** | 压后台内存 |
| 用户说"莫名退出"、崩溃平台无记录 | **FOOM / jetsam / OOM** | 见下方专章 |

**问清楚是哪一类，不要直接开查。**

## 第二步：建立内存水位采样

**这是性价比最高的基础设施** —— 它给出"崩溃前内存曲线"，是 FOOM 归因的唯一可行路径。

做法：定时采样写入本地环形缓冲，崩溃后随报告一起上报。

```bash
# iOS 真机：查看内存（Instruments Allocations / VM Tracker）
xcrun xctrace record --template 'Allocations' --launch <bundleid> \
  --output .apm/runs/$(date +%s)/alloc.trace

# Android / 鸿蒙通用：PSS
adb shell dumpsys meminfo <package>        # Android
hdc shell hidumper --mem <pid>             # 鸿蒙
```

⚠️ **采样频率与开销需权衡**（建议 5–30s 一次，或关键路径埋点）。

## 第三步：RN 泄漏排查（有明确方法论）

```
1. 放大复现：同一页面反复进出 20–30 次
2. 每次记录 JS Heap 大小 → 建立增长曲线
3. 抓堆快照对比（Hermes heap snapshot）
4. 看 detached React 树：被 listener/闭包持有的 Fiber 节点
5. 查原生节点上的 __reactFiber$ 引用
```

**这套方法能定位 90% 的 RN 泄漏。**

```bash
# Hermes 堆快照（需 debug 构建）
# DevTools → chrome://inspect → Memory → Take Heap Snapshot
```

⚠️ **排查前先怀疑 Hermes Sampling Profiler** ——
它默认开启会导致 `sampledStacks_` 内存无限增长（RNOH PR #3438 提议默认关闭）。
**这可能就是"泄漏"本身**，别去业务代码里找一个不存在的问题。

### RN 常见泄漏源清单

- [ ] `useEffect` 未清理：`setInterval`/`setTimeout`、`DeviceEventEmitter`、
      `AppState`、`Keyboard` 监听、navigation listener、socket、长请求
      → 屏幕级 effect 应用 **`useFocusEffect`** 自动清理
- [ ] `state` 数组/Map 无界增长（只增不删的缓存）
- [ ] navigation params 传大对象（整个列表传参）
- [ ] 自定义原生模块未释放引用（Java/Kotlin 用 `WeakReference`，Swift 用 `weak`）
- [ ] 闭包捕获了大对象且被长生命周期对象持有
- [ ] 全局单例里累积数据
- [ ] 图片缓存无上限

> 注：`RCTBridge` 类泄漏在**新架构下已不存在**（legacy arch 在 RN 0.82 起强制关闭）。
> 如果你在找 Bridge 泄漏，先确认项目架构版本。

## 第四步：iOS FOOM 专章（官方能力缺口，必须自建）

**这是最容易被漏掉、也最影响体感的一类问题。**

用户说"App 莫名其妙退出了"，崩溃平台**没有任何记录** ——
因为 jetsam 杀进程不产生崩溃信号。

### 官方能力的缺口

⚠️ **MetricKit `MXDiagnosticPayload` 只包含四类诊断**：
`crashDiagnostics` / `hangDiagnostics` / `diskWriteExceptionDiagnostics` /
`cpuExceptionDiagnostics`

👉 **不含 jetsam 诊断**。Apple 增强请求 **FB9972410**（请求在 jetsam 时捕获内存占用）
提了 4 年**至今未落地**。

### 唯一能拿到的官方信号（只有计数，无详情）

`MXAppExitMetric`：
- `cumulativeMemoryResourceLimitExitCount` —— 超内存上限被杀
- `cumulativeMemoryPressureExitCount` —— **jetsam，系统回收**

### 归因三件套（交叉验证）

1. **自建内存水位环形打点** ← 性价比最高
2. **Sentry watchdog / OOM 终止跟踪**
3. **`MXAppExitMetric` 计数交叉验证**

> 如果只做崩溃捕获不做内存水位，**FOOM 是完全不可见的**。
> 这个盲区在体感上表现为"用户流失"，但你看不到任何数据。

### 官方建议

**把后台内存压到 50MB 以下**，可显著降低 jetsam 回收概率。

## 第五步：Android / 鸿蒙

| 平台 | 手段 |
|---|---|
| Android | **`ApplicationExitInfo`（API 30+）**，`REASON_LOW_MEMORY` 是官方 OOM 归因来源，比 iOS 好用得多 |
| Android | LeakCanary 2.14（debug 构建零接入；⚠️ v3 仍是 alpha，生产建议留 2.14） |
| 鸿蒙 | `hidebug` 取 PSS/RSS/heapdump |
| 鸿蒙 | **`jsLeakWatcher`（API 12+）** —— 鸿蒙独有的 JS 对象泄漏检测。⚠️ `enable()` 默认关闭需手动打开 |
| 鸿蒙 | HiAppEvent 订阅内存泄漏故障事件（含 heapdump、内存分配栈） |

## 第六步：验证

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apm_baseline.py" compare \
  --baseline .apm/baseline/memory.json --run .apm/runs/<本次>/metrics.json
```

内存验证的正确方式**不是测一次峰值**，而是：

1. **跑同样的重复操作 N 次**（如页面进出 30 次）
2. 看**增长曲线是否变平** —— 泄漏修复后应趋于平稳而非线性上升
3. 峰值内存对比基线
4. 长时间运行（30min+）确认不再被杀

⚠️ **"峰值降了 10MB"可能毫无意义**（可能是噪声或缓存差异）；
**"斜率从线性增长变成平稳"才是泄漏被修复的证据**。

## 常见误判

| 误判 | 真相 |
|---|---|
| "崩溃平台没记录，是用户自己退的" | 很可能是 FOOM/jetsam，需自建归因 |
| "内存涨了就是泄漏" | 缓存正常增长也会涨，要看**是否释放** |
| "峰值降了就是优化成功" | 泄漏要看**斜率**，不是峰值 |
| "RSS 很高所以会被杀" | 系统按 **PSS** 判定，不是 RSS |
| "RN 内存涨了，去业务代码找泄漏" | **先怀疑 Hermes Sampling Profiler** |
| "MetricKit 会告诉我 jetsam 原因" | **它不会**，MXDiagnosticPayload 不含 jetsam |
