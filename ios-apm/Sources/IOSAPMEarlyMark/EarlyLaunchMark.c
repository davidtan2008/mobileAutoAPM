// EarlyLaunchMark.c
//
// 目的：让 **pre-main（dyld + 镜像初始化）** 的耗时可见。
//
// 为什么需要它：
//   任何 Swift 侧的埋点最早也只能在 `App.init()` 里生效 —— 而在那之前，
//   dyld 加载镜像、运行静态构造、Swift 运行时初始化都已经发生。
//   这段耗时在纯 Swift 埋点里是完全的盲区。
//
//   实测案例：某 App 纯 Swift 埋点测得「启动 219ms」，补上本文件后发现
//   其中 219ms 全部发生在埋点被触碰之前 —— 即整段启动都没有归因。
//
// 本文件的构造函数由 dyld 在**任何 Swift 代码之前**执行，因此
// 「进程创建 → 本构造函数」就是 pre-main 耗时。
//
// 刻意用纯 C + os_log：
//   不引入 Swift/C 互操作，免去 bridging header 与工程配置改动，
//   拖入文件即可生效。

#include <os/log.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

/// 进程创建时间（epoch 秒，含小数）。
/// 与 AppLaunchTracker 使用同一口径（sysctl KERN_PROC_PID）。
/// 失败返回 0。
double iosapm_process_start_time(void) {
    int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()};
    struct kinfo_proc info;
    size_t size = sizeof(info);
    if (sysctl(mib, 4, &info, &size, NULL, 0) != 0) {
        return 0;
    }
    struct timeval tv = info.kp_proc.p_starttime;
    return (double)tv.tv_sec + (double)tv.tv_usec / 1000000.0;
}

/// pre-main 耗时（毫秒）。供 Swift 侧读取，避免重复实现 sysctl。
int iosapm_premain_millis(void) {
    double start = iosapm_process_start_time();
    if (start <= 0) {
        return -1;  // 取不到时不报 0 —— 0 会被误读成"pre-main 极快"
    }
    struct timeval now_tv;
    gettimeofday(&now_tv, NULL);
    double now = (double)now_tv.tv_sec + (double)now_tv.tv_usec / 1000000.0;
    double ms = (now - start) * 1000.0;
    return ms < 0 ? -1 : (int)ms;
}

__attribute__((constructor))
static void iosapm_early_launch_mark(void) {
    int premain = iosapm_premain_millis();
    os_log_t log = os_log_create("com.iosapm", "launch");
    if (premain < 0) {
        os_log(log, "IOSAPM pre-main: unavailable (sysctl failed)");
    } else {
        os_log(log, "IOSAPM pre-main: %{public}d ms since process start", premain);
    }
}
