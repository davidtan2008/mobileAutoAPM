# 原生补齐参考实现（iOS / Android / HarmonyOS）

> **iOS 侧已在真机验证**（iPhone 13 / iOS 26.7 / RN 0.73.4 / Release）。
> 可用实现见 [`../ios/RnApm.m`](../ios/RnApm.m) + [`../ios/rn-apm-shim.podspec`](../ios/rn-apm-shim.podspec)，
> 真机实测数据见 [`../../docs/evidence/native-shim/measured.json`](../../docs/evidence/native-shim/measured.json)。
> **Android / 鸿蒙侧仍未验证**，下方代码仍是参考实现。
>
> ⚠️ 下方 iOS 片段是**简化示意**。真机验证时在它之上又踩了两个坑，
> 完整说明见 `../ios/RnApm.m` 顶部注释 —— 简写版**不能直接用**。

SDK 需要原生提供三样东西。缺任何一样都不会崩（会自动降级），但会损失能力：

| 能力 | 缺失的后果 |
|---|---|
| `getProcessStartTime` | 启动总耗时只剩相对值，**不可与线上基线对比** |
| `getMemoryUsage` | **内存水位与 FOOM 归因完全不可用** |
| `reportJsError` | JS 崩溃无法与原生崩溃关联 |

---

## ⚠️ 三个最容易做错的点

### 0. Promise 方法必须用 RN 的 typedef，否则真机一调就崩

```objc
// ❌ 手写 block 类型 —— 真机上 App 一调用就崩
//    NSInvalidArgumentException: +[NSInvocation _invocationWithMethodSignature:frame:]:
//    method signature argument cannot be nil
RCT_EXPORT_METHOD(foo:(void (^)(NSNumber *))resolve
                  rejecter:(void (^)(NSString *, NSString *, NSError *))reject)

// ✅ 用 RN 的标准 typedef
RCT_EXPORT_METHOD(foo:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)
```

`RCTModuleMethod` 靠方法签名的类型编码生成 JS 参数转换，自定义 block 类型会让它拿不到
合法的 `NSMethodSignature`。**这在代码里看起来完全正常，只有真跑才暴露。**

### 1. JS 侧的 bridge 是**同步**取值 —— 返回 Promise 等于取不到

`createBridge()` 先试方法、再试 `getConstants()`，全程同步、不 await。所以：

| 值的性质 | 正确做法 |
|---|---|
| 进程生命周期内**不变**（进程创建时间、设备信息） | 放 `constantsToExport` |
| **会变**（内存水位） | `RCT_EXPORT_BLOCKING_SYNCHRONOUS_METHOD` |

实测症状：原生返回 Promise 时，JS 直读拿到 `[object Object]`，而
`bridge.getMemoryUsage()` 返回 `null` —— SDK 全程不知道原生能力其实已就绪，
`getNativeGaps()` 会**误报缺口**。

⚠️ 内存**不要**放进 `constantsToExport`：那等于把首帧读数当成全程水位。

### 2. 内存必须取 PSS / phys_footprint，**不能取 RSS**

**系统是按 PSS（比例分摊后的物理内存）判定是否杀进程的。**
用 RSS 会显著高估（共享库被重复计算），导致内存数据失去指导意义。

| 平台 | 正确取法 | 常见错误 |
|---|---|---|
| iOS | `task_vm_info_data_t.phys_footprint` | 用 `resident_size` |
| Android | `Debug.MemoryInfo.getTotalPss()` | 用 `Runtime.totalMemory()`（那只反映 Java 堆） |
| HarmonyOS | `hidebug.getPss()` | 用 `getRss()` |

> 另外注意：Android 的 `Runtime.totalMemory()` 只反映 Java 堆，
> **RN 的内存大头在 Native 堆与 Hermes 堆**，用它做监控会严重低估。

### 2. 进程创建时间要用系统调用，**不要用 App 启动回调**

`applicationDidFinishLaunching` 之类已经在进程创建**之后**，用它当起点会漏掉
dyld 加载、动态库链接等阶段 —— 而那正是启动优化收益最大的地方。

iOS 用 `sysctl(KERN_PROC_PID)`；Android 用 `Process.getStartUptimeMillis()`。

---

## iOS

```objc
// RnApm.m
#import <React/RCTBridgeModule.h>
#import <sys/sysctl.h>
#import <mach/mach.h>

@implementation RnApm

RCT_EXPORT_MODULE();

+ (BOOL)requiresMainQueueSetup { return NO; }

- (NSDictionary *)constantsToExport {
  return @{ @"processStartTime": @([self processStartTimeMs] ?: 0),
            @"memoryUsage": [self memoryUsage] ?: @{},
            @"deviceInfo": [self deviceInfo] };
}

// 进程创建时间（epoch ms）
- (NSNumber *)processStartTimeMs {
  int mib[4] = { CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid() };
  struct kinfo_proc info;
  size_t size = sizeof(info);
  info.kp_proc.p_flag = 0;
  if (sysctl(mib, 4, &info, &size, NULL, 0) != 0) return nil;
  struct timeval tv = info.kp_proc.p_starttime;
  return @((double)tv.tv_sec * 1000.0 + (double)tv.tv_usec / 1000.0);
}

// ⚠️ 用 phys_footprint，不是 resident_size
- (NSDictionary *)memoryUsage {
  task_vm_info_data_t vmInfo;
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  kern_return_t kr = task_info(mach_task_self(), TASK_VM_INFO,
                               (task_info_t)&vmInfo, &count);
  if (kr != KERN_SUCCESS) return nil;
  return @{ @"usedBytes": @(vmInfo.phys_footprint),
            @"totalBytes": @([NSProcessInfo processInfo].physicalMemory) };
}

- (NSDictionary *)deviceInfo {
  return @{ @"platform": @"ios",
            @"osVersion": [UIDevice currentDevice].systemVersion ?: @"",
            @"deviceModel": [self machineName] };
}

@end
```

**关联原生崩溃**（需接入 KSCrash / SentryCrash）：

```objc
// 把 JS 崩溃转交给原生崩溃 SDK，使 JS 与原生崩溃能关联
RCT_EXPORT_METHOD(reportJsError:(NSDictionary *)record) {
  [SentrySDK captureMessage:record[@"message"]];  // 或你的崩溃 SDK
}
```

---

## Android

```kotlin
// RnApmModule.kt
class RnApmModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName() = "RnApm"

  override fun getConstants(): Map<String, Any> = mapOf(
    "processStartTime" to processStartTimeMs(),
    "memoryUsage" to memoryUsage(),
    "deviceInfo" to deviceInfo()
  )

  // 进程创建时间（epoch ms）
  private fun processStartTimeMs(): Double {
    val uptime = SystemClock.uptimeMillis()
    // getStartUptimeMillis 需要 API 24+
    val startUptime = Process.getStartUptimeMillis()
    val elapsed = uptime - startUptime
    return (System.currentTimeMillis() - elapsed).toDouble()
  }

  // ⚠️ 用 PSS，不是 Runtime.totalMemory()
  private fun memoryUsage(): Map<String, Any> {
    val info = Debug.MemoryInfo()
    Debug.getMemoryInfo(info)
    return mapOf(
      "usedBytes" to info.totalPss.toLong() * 1024L,          // PSS 单位是 KB
      "totalBytes" to Runtime.getRuntime().maxMemory() * 4L    // 粗略上限参考
    )
  }

  private fun deviceInfo(): Map<String, Any> = mapOf(
    "platform" to "android",
    "osVersion" to Build.VERSION.RELEASE,
    "deviceModel" to "${Build.MANUFACTURER} ${Build.MODEL}"
  )
}
```

**OOM 归因的额外建议**：Android 有 `ApplicationExitInfo`（API 30+），
`reason == REASON_LOW_MEMORY` 是官方 OOM 归因来源 —— 比 iOS 的 MetricKit 好用得多。
建议在下次启动时读取并上报。

---

## HarmonyOS（RNOH）

鸿蒙侧的原生模块形态与标准 RN 不同，需要按 RNOH 的方式注册。
能力可从系统 Kit 获取：

```typescript
// 鸿蒙侧能力来源（供参考，需按 RNOH 模块规范封装）
import { hiAppEvent, hilog } from '@kit.PerformanceAnalysisKit';
import { hidebug } from '@kit.PerformanceAnalysisKit';
import deviceInfo from '@ohos.deviceInfo';
import process from '@ohos.process';

// 内存：⚠️ 用 getPss()，不要用 getRss()
const usedBytes = hidebug.getPss();   // 单位 KB，需 ×1024

// 设备信息
const info = {
  platform: 'harmony',
  osVersion: deviceInfo.osFullName,
  deviceModel: deviceInfo.marketName,
};

// 进程创建时间：鸿蒙没有直接 API，建议用 HiAppEvent 的 APP_LAUNCH 事件
// （订阅后系统会给出启动相关时间戳）
```

### 鸿蒙崩溃能力（独立于本 SDK）

鸿蒙的崩溃捕获走系统能力，**不是** KSCrash 那一套：

| 事件 / API | 用途 |
|---|---|
| HiAppEvent `APP_CRASH` | 崩溃（含 JS Crash / CPP Crash） |
| HiAppEvent `APP_FREEZE` | 冻屏 |
| HiAppEvent `APP_LAUNCH` | 启动耗时 |
| HiAppEvent `SCROLL_JANK` | 滑动丢帧（单帧 >50ms 上报） |
| `hidebug` | PSS / RSS / heapdump |
| `jsLeakWatcher`（API 12+） | **鸿蒙独有的 JS 对象泄漏检测** |
| `onRenderExited`（API 9+） | **白屏归因利器** —— 可直接区分渲染进程是否因 OOM 被杀 |

⚠️ **鸿蒙崩溃时进程已退出，事件只能在下次启动时取。**
若要覆盖"崩溃后再没打开过 App"的场景，用 `FaultLogExtensionAbility`（API 21+），
但**它只有 10 秒处理时间**。

详见 `../plugins/mobile-apm/references/stack-selection.md` §0。

---

## 注册与注入

```js
// index.js
import { NativeModules } from 'react-native';
import { initApm } from 'rn-apm';

const apm = initApm({
  appVersion: '2.3.4',
  nativeModule: NativeModules.RnApm,   // ← 传入后自动适配
  transport: { /* ... */ },
});

// 验证是否生效
console.log(apm.getNativeGaps());   // 期望输出 []
console.log(apm.getLayerStatus());  // native 应为 active（若接了崩溃 SDK）
```

`createBridge` 兼容两种原生模块形态：

```js
// 形态 A：方法式
getProcessStartTime: () => 1234567890
// 形态 B：常量式（老架构 NativeModule 常见）
getConstants: () => ({ processStartTime: 1234567890 })
```

两种都能被自动识别。原生方法抛错会自动降级为 `null`，不会传播到 JS。
