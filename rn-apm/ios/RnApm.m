// RnApm.m —— iOS 原生补齐实现（参考实现升级为可用实现）
//
// 为什么这个文件存在
// JS 侧 rn-apm 依赖三项原生能力，缺任何一项都会降级：
//   getProcessStartTime → 启动总耗时只剩相对值，**不可与线上基线对比**
//   getMemoryUsage      → **内存水位与 FOOM 归因完全不可用**
//   reportJsError       → JS 崩溃无法与原生崩溃关联
// 缺口由 `apm.getNativeGaps()` 报告。
//
// 两个最容易做错的点（这里刻意做对）
// 1. 内存取 **phys_footprint**，不取 resident_size。
//    系统按 PSS 判内存压力，用 RSS 会把共享库重复计算、严重高估。
// 2. 进程创建时间用 **sysctl(KERN_PROC_PID)**，不用 applicationDidFinishLaunching。
//    后者已在进程创建之后，会漏掉 dyld 加载与动态库链接 —— 那正是启动优化收益最大的阶段。
//
// 降级约定：任何一项取不到都返回 nil / 空，**绝不返回 0**。
// 0 会被 JS 侧当成"极快/极小"，那是危险的假数据。

#import <React/RCTBridgeModule.h>
#import <UIKit/UIKit.h>
#import <mach/mach.h>
#import <sys/sysctl.h>
#import <sys/types.h>
#import <unistd.h>

@interface RnApm : NSObject <RCTBridgeModule>
@end

@implementation RnApm

RCT_EXPORT_MODULE(RnApm);

// 纯计算 + sysctl，不碰 UI
+ (BOOL)requiresMainQueueSetup
{
  return NO;
}

#pragma mark - 进程创建时间

/// 进程创建时间（epoch 毫秒）。取不到返回 nil —— 不要返回 0。
// ─────────────────────────────────────────────────────────────
// ⚠️ 这里踩了两次真机才搞对，务必保留这段说明。
//
// 坑一：Promise 方法**必须**用 RN 的 RCTPromiseResolveBlock / RCTPromiseRejectBlock。
//   手写 block 类型 → App 一调用就崩：
//     NSInvalidArgumentException: +[NSInvocation _invocationWithMethodSignature:frame:]:
//     method signature argument cannot be nil
//   原因：RCTModuleMethod 靠方法签名的类型编码生成 JS 参数转换。
//
// 坑二：JS 侧的 createBridge 是**同步**取值的（先试方法、再试 constants）。
//   如果原生方法返回 Promise，JS 拿到的是 Promise 对象，
//   createBridge 的类型校验会判定"不是 number / 不是对象" → 一律降级为 null。
//   实测症状：直读原生返回 [object Object]，
//   而 bridge.getProcessStartTime() 返回 null。
//
// 因此按「值会不会变」分成两类：
//   · 不变的（进程创建时间、设备信息）→ 走 constantsToExport，同步可取
//   · 会变的（内存水位）→ 必须实时取，用 RCT_EXPORT_BLOCKING_SYNCHRONOUS_METHOD
//
// 内存这里用同步方法是有代价的（占用 JS 线程），但 task_info 是一次很轻的
// 系统调用；换来的是 SDK 现有 bridge 层零改动即可用。
// 将来若要彻底避免阻塞，应改成 JS 侧支持 await 的原生方法。
// ─────────────────────────────────────────────────────────────

/// 进程创建时间（epoch 毫秒）。取不到返回 nil —— 不要返回 0。
///
/// 走 constants：它在进程生命周期内**永不改变**，且必须能同步取到。
+ (NSNumber *_Nullable)processStartTimeMs
{
  int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()};
  struct kinfo_proc info;
  size_t size = sizeof(info);
  memset(&info, 0, sizeof(info));
  if (sysctl(mib, 4, &info, &size, NULL, 0) != 0 || size == 0) {
    return nil;
  }

  struct timeval start = info.kp_proc.p_starttime;
  NSTimeInterval epoch = (NSTimeInterval)start.tv_sec + (NSTimeInterval)start.tv_usec / 1e6;
  if (epoch <= 0) {
    return nil;
  }
  return @(llround(epoch * 1000.0));
}

#pragma mark - 内存

/// 当前物理内存占用（phys_footprint 口径）。
/// 当前物理内存占用（phys_footprint 口径）。
///
/// 内存会持续变化，**不能**走 constants（那会把首帧的值当全程水位）。
/// 必须实时取 → 同步阻塞方法，让 JS 侧的 createBridge 能直接拿到数值。
RCT_EXPORT_BLOCKING_SYNCHRONOUS_METHOD(getMemoryUsage)
{
  return [RnApm memoryUsage];
}

+ (nullable NSDictionary *)memoryUsage
{
  task_vm_info_data_t info;
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  kern_return_t kr = task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count);
  if (kr != KERN_SUCCESS) {
    return nil;
  }

  // phys_footprint：系统判内存压力用的口径。
  // 绝不用 resident_size —— 那会把多进程共享的框架页重复计入，严重高估。
  uint64_t footprint = info.phys_footprint;
  if (footprint == 0) {
    return nil;
  }

  NSMutableDictionary *out = [NSMutableDictionary dictionary];
  out[@"usedBytes"] = @(footprint);
  out[@"footprintBytes"] = @(footprint);
  out[@"residentBytes"] = @(info.resident_size);   // 仅供对照，不作为判定依据
  out[@"source"] = @"phys_footprint";
  return out;
}

#pragma mark - 设备信息

RCT_EXPORT_METHOD(getDeviceInfoAsync:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)
{
  resolve([RnApm deviceInfo]);
}

+ (NSDictionary *)deviceInfo
{
  static NSString *model = nil;
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    // 机型用硬件标识（不含版本后缀），比 UIDevice.model 有用
    NSString *hardware = [RnApm hardwareIdentifier];
    model = hardware.length > 0 ? hardware : UIDevice.currentDevice.model;
  });

  return @{
    @"osVersion": UIDevice.currentDevice.systemVersion,
    @"deviceModel": model ?: @"unknown",
    @"platform": @"ios",
  };
}

+ (NSString *)hardwareIdentifier
{
  size_t size = 0;
  if (sysctlbyname("hw.machine", NULL, &size, NULL, 0) != 0 || size == 0) {
    return nil;
  }
  char *buf = malloc(size);
  if (buf == NULL) {
    return nil;
  }
  NSString *result = nil;
  if (sysctlbyname("hw.machine", buf, &size, NULL, 0) == 0) {
    result = [NSString stringWithUTF8String:buf];
  }
  free(buf);
  return result;
}

#pragma mark - 崩溃关联

/// 把 JS 崩溃交给原生崩溃 SDK 归因。
///
/// 刻意**不自己实现崩溃捕获**：在 App 里自己装 signal handler 容易与
/// KSCrash / Sentry / Bugly 打架，反而制造丢栈。
/// 这里只做转发；接了哪个原生 SDK 由宿主 App 决定。
RCT_EXPORT_METHOD(reportJsError:(NSDictionary *)record
                  resolve:(RCTPromiseResolveBlock)resolve
                  reject:(RCTPromiseRejectBlock)reject)
{
  // 默认实现：打日志，不吞掉
  NSLog(@"[RnApm] JS error (未接入原生崩溃 SDK，仅记录): %@", record[@"message"] ?: record);
  resolve(nil);
}

#pragma mark - Promise rejection 跟踪

RCT_EXPORT_METHOD(enablePromiseRejectionTracking:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)
{
  // Hermes 侧由宿主 App 决定是否开启（RNAppDelegate 里的配置）。
  // 这里如实回报"未开启"，不谎称已开启。
  resolve(@NO);
}

#pragma mark - 老架构常量

/// 同步常量：只在进程生命周期内**不变**的值才放这里。
///
/// 刻意**不放** memoryUsage —— 内存是波动的，放进常量等于把首帧读数
/// 当成全程水位，正是 memory 模块最忌讳的假数据。
- (NSDictionary *)constantsToExport
{
  NSMutableDictionary *out = [NSMutableDictionary dictionary];
  NSNumber *start = [RnApm processStartTimeMs];
  if (start != nil) {
    out[@"processStartTime"] = start;
  }
  out[@"deviceInfo"] = [RnApm deviceInfo];
  return out;
}

@end
