import Foundation
import os
import IOSAPMEarlyMark

/// 启动阶段。
///
/// **用这些标准名字，不要自创** —— 阶段名一致才能跨版本对比基线。
public enum LaunchStage: String, CaseIterable, Sendable {
    /// 原生进程创建（由 C 构造函数提供，无需手动打点）
    case processStart
    /// App 初始化开始
    case appInit
    /// App 初始化完成（依赖注入等就绪）
    case appReady
    /// SwiftUI Scene 首次求值 / 根视图构造开始
    case appBody
    /// 根视图 body 首次求值
    case rootBody
    /// 首页 body 首次求值
    case homeBody
    /// 持久化（数据库）就绪
    case storeReady
    /// ViewModel 就绪
    case viewModelsReady
    /// 首屏内容渲染完成（终点）
    case firstFrame
}

public struct LaunchMark: Sendable {
    public let stage: LaunchStage
    /// 相对**进程创建**的毫秒数
    public let sinceProcessStartMs: Int
    /// 相对上一个阶段的毫秒数
    public let deltaMs: Int
}

public struct LaunchReport: Sendable {
    public let marks: [LaunchMark]
    /// 进程创建 → 首屏渲染
    public let totalMs: Int
    /// **是否拿到了真实的进程创建时间**。
    /// 为 false 时 totalMs 只是相对值，**不可与线上基线对比**。
    public let hasProcessStart: Bool
    /// pre-main 耗时（dyld + 镜像初始化）。取不到为 nil。
    public let premainMs: Int?

    /// 便于直接喂给基线工具 / 打日志。
    public var stageBreakdown: String {
        marks.map { "\($0.stage.rawValue)=\($0.sinceProcessStartMs)/+\($0.deltaMs)" }
            .joined(separator: " ")
    }
}

/// 启动分段打点。
///
/// ## 为什么必须自建
/// 所有通用手段（MetricKit / 第三方 App Start / 各类 APM SDK）
/// **都只能给出启动总耗时**，拿不到 App 内部阶段。
/// 不知道"时间花在哪一段"，优化就只能靠猜。
///
/// ## ⚠️ 一个真实踩过的坑
/// 若 `LaunchTracker.shared` 直到 `App.init()` 才被首次触碰，
/// 那么「进程创建 → 埋点首次触碰」这段（**实测可达 219ms，占启动一半以上**）
/// 会完全不被归因。
///
/// 本实现因此提供两层保障：
///  1. `EarlyLaunchMark.c` 的 C 构造函数在**任何 Swift 代码之前**记录 pre-main；
///  2. `activate()` 应在 `main` / `App.init` 的**第一行**调用。
///
/// 并且 `report()` 会打印**完整阶段拆分**，而不只是总数 ——
/// 只打印总数等于把最大的一块藏起来。
public final class LaunchTracker: @unchecked Sendable {
    public static let shared = LaunchTracker()

    private let log = Logger(subsystem: "com.iosapm", category: "launch")
    /// os_signpost 需要 OSLog，不能用 Logger
    private let signpostLog = OSLog(subsystem: "com.iosapm", category: .pointsOfInterest)
    private let lock = NSLock()
    private var marks: [(stage: LaunchStage, at: CFAbsoluteTime)] = []
    private var didStart = false

    /// 进程创建时间（CFAbsoluteTime）。取不到为 0。
    private var processStart: CFAbsoluteTime = 0
    private var premainMs: Int?

    private init() {}

    /// **必须在 App 生命周期的最早时机调用**（`App.init` 第一行，或自定义 `main`）。
    /// 幂等。
    @discardableResult
    public func activate() -> Bool {
        lock.lock()
        if didStart { lock.unlock(); return false }
        didStart = true
        lock.unlock()

        // 从 C 层拿进程创建时间与 pre-main —— 口径与 C 构造函数一致
        let epoch = iosapm_process_start_time()
        processStart = epoch > 0 ? epoch - 978307200.0 : 0   // Unix epoch → CFAbsoluteTime

        let pm = iosapm_premain_millis()   // C int → Swift Int32
        premainMs = pm >= 0 ? Int(pm) : nil

        record(.processStart)
        return true
    }

    /// 打一个阶段点。**同步且廉价** —— 它会被插在启动关键路径上。
    /// 除 processStart 外，同一阶段只记录首次（防热重载污染）。
    public func mark(_ stage: LaunchStage) {
        lock.lock()
        let exists = marks.contains { $0.stage == stage }
        lock.unlock()
        guard !exists else { return }
        record(stage)
    }

    private func record(_ stage: LaunchStage) {
        let now = CFAbsoluteTimeGetCurrent()
        lock.lock()
        marks.append((stage, now))
        lock.unlock()
        // signpost 开销近乎为零，可安全用于启动路径
        os_signpost(.event, log: signpostLog, name: "launch", "%{public}s", stage.rawValue)
    }

    /// 标记首屏渲染完成。
    ///
    /// **在 `onAppear` 里延迟一个 runloop 调用**，使终点逼近
    /// `CA::Transaction::commit` —— 直接打点会早于实际提交，低估启动耗时：
    /// ```swift
    /// .onAppear {
    ///     DispatchQueue.main.async { LaunchTracker.shared.markFirstFrame() }
    /// }
    /// ```
    /// ⚠️ 用延迟打点后，**做对照实验时对方也必须用同一终点定义**，否则数字不可比。
    public func markFirstFrame() {
        mark(.firstFrame)
        report()
    }

    /// 生成报告。未标首屏时返回 nil —— **宁可不报，也不报一个不完整的启动时长**。
    public func makeReport() -> LaunchReport? {
        lock.lock()
        let snapshot = marks
        lock.unlock()

        guard snapshot.contains(where: { $0.stage == .firstFrame }), snapshot.count >= 2 else {
            return nil
        }

        let base = snapshot[0].at
        var out: [LaunchMark] = []
        var prev = base
        for m in snapshot {
            let since = processStart > 0 ? Int((m.at - processStart) * 1000) : Int((m.at - base) * 1000)
            out.append(LaunchMark(stage: m.stage,
                                  sinceProcessStartMs: since,
                                  deltaMs: Int((m.at - prev) * 1000)))
            prev = m.at
        }

        let last = snapshot[snapshot.count - 1]
        let total = processStart > 0 ? Int((last.at - processStart) * 1000) : Int((last.at - base) * 1000)

        return LaunchReport(marks: out,
                            totalMs: total,
                            hasProcessStart: processStart > 0,
                            premainMs: premainMs)
    }

    /// 把报告打到日志。**这是默认行为，不要省** ——
    /// 只记录不输出，等于把最大的那块藏起来（真实踩过的坑）。
    @discardableResult
    public func report() -> LaunchReport? {
        guard let r = makeReport() else { return nil }
        if let pm = r.premainMs {
            log.info("IOSAPM pre-main: \(pm, privacy: .public) ms")
        } else {
            log.info("IOSAPM pre-main: unavailable")
        }
        if !r.hasProcessStart {
            log.warning("IOSAPM 未取到进程创建时间，total 仅为相对值，**不可与线上基线对比**")
        }
        log.info("IOSAPM launch total: \(r.totalMs, privacy: .public) ms")
        log.info("IOSAPM stages (since/+delta ms): \(r.stageBreakdown, privacy: .public)")
        return r
    }
}

// MARK: - 便捷全局函数

public func apmActivate() { LaunchTracker.shared.activate() }
public func apmMark(_ stage: LaunchStage) { LaunchTracker.shared.mark(stage) }
public func apmMarkFirstFrame() { LaunchTracker.shared.markFirstFrame() }
