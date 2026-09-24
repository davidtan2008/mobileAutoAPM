import Foundation
import os
#if canImport(UIKit)
import UIKit
#endif

/// 内存水位样本。
public struct MemorySample: Sendable {
    /// 相对采样开始的毫秒数
    public let t: Int
    /// **phys_footprint**（字节）
    public let footprint: Int
    /// 设备物理内存上限（字节）
    public let limit: Int
}

public struct MemoryReport: Sendable {
    public let samples: [MemorySample]
    public let peakBytes: Int
    /// 线性回归斜率（字节/秒）。
    /// **判断泄漏看斜率，不看峰值** —— 峰值下降可能只是缓存差异。
    public let slopeBytesPerSec: Double
    public let sampleCount: Int
    /// 因缓冲写满被丢弃的样本数（>0 说明采样过密或缓冲过小）
    public let droppedSamples: Int

    /// 泄漏程度的启发式描述。**这是启发式不是判决** ——
    /// 缓存预热期本来就该涨，需结合业务判断。
    public var growthDescription: String {
        let mbPerHour = slopeBytesPerSec * 3600 / (1024 * 1024)
        if abs(mbPerHour) < 1 { return "趋于平稳（\(String(format: "%.2f", mbPerHour)) MB/小时）" }
        if mbPerHour < 10 { return "缓慢增长（\(String(format: "%.1f", mbPerHour)) MB/小时），需确认是否正常缓存预热" }
        return "⚠️ 快速增长（\(String(format: "%.1f", mbPerHour)) MB/小时），疑似泄漏"
    }
}

/// 内存水位采样器 —— **iOS FOOM 归因的唯一可行路径**。
///
/// ## 为什么这是必需的
/// **MetricKit 的 `MXDiagnosticPayload` 不包含 jetsam 诊断** ——
/// 它只有 crash / hang / diskWrite / cpuException 四类。
/// Apple 增强请求 FB9972410（请求在 jetsam 时上报内存占用）提出多年仍未落地。
///
/// 官方唯一能给到的只有 `MXAppExitMetric` 的两个**计数**，没有详情。
///
/// 也就是说：**不自建内存水位，FOOM 是完全不可见的。**
/// 用户表现为"App 莫名退出"，而崩溃平台上看不到任何记录。
///
/// ## 关键实现点
/// - 用 **`phys_footprint`**，不是 `resident_size`。
///   系统按 footprint/PSS 判定是否回收进程，用 RSS 会严重高估。
/// - 环形缓冲定容，长时间运行不增长。
/// - App 切后台时**停止采样**（后台采样无意义、耗电、且污染判断）。
public final class MemoryWatermark: @unchecked Sendable {
    public static let shared = MemoryWatermark()

    private let log = Logger(subsystem: "com.iosapm", category: "memory")
    private let buffer: RingBuffer<MemorySample>
    private var timer: DispatchSourceTimer?
    private var startedAt: CFAbsoluteTime = 0
    private var taken = 0
    private var failed = 0

    /// - Parameters:
    ///   - capacity: 环形缓冲容量。默认 180 个样本。
    ///   - interval: 采样间隔（秒）。默认 10s —— 太密有开销，太疏会漏掉峰值。
    public init(capacity: Int = 180, interval: TimeInterval = 10) {
        self.buffer = RingBuffer(capacity: capacity)
        self.interval = interval
    }

    private let interval: TimeInterval

    public var sampleCount: Int { taken }
    public var failedCount: Int { failed }
    public var isRunning: Bool { timer != nil }

    /// 开始周期采样。幂等。
    public func start() {
        guard timer == nil else { return }
        if startedAt == 0 { startedAt = CFAbsoluteTimeGetCurrent() }
        _ = sampleOnce()

        let t = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        t.schedule(deadline: .now() + interval, repeating: interval)
        t.setEventHandler { [weak self] in _ = self?.sampleOnce() }
        t.resume()
        timer = t
    }

    /// 停止采样。**切后台时必须调用。**
    public func stop() {
        timer?.cancel()
        timer = nil
    }

    @discardableResult
    public func sampleOnce() -> Bool {
        guard let bytes = Self.footprintBytes() else {
            failed += 1
            return false
        }
        let sample = MemorySample(
            t: Int((CFAbsoluteTimeGetCurrent() - startedAt) * 1000),
            footprint: bytes,
            limit: Self.physicalMemory()
        )
        buffer.push(sample)
        taken += 1
        return true
    }

    /// 当前水位快照（供崩溃报告附带）。
    public func current() -> MemorySample? {
        guard let bytes = Self.footprintBytes() else { return nil }
        return MemorySample(
            t: Int((CFAbsoluteTimeGetCurrent() - startedAt) * 1000),
            footprint: bytes,
            limit: Self.physicalMemory()
        )
    }

    public func makeReport() -> MemoryReport? {
        let samples = buffer.snapshot()
        guard !samples.isEmpty else { return nil }

        let peak = samples.map(\.footprint).max() ?? 0
        let points = samples.map { (x: Double($0.t) / 1000.0, y: Double($0.footprint)) }
        return MemoryReport(
            samples: samples,
            peakBytes: peak,
            slopeBytesPerSec: Self.slope(points),
            sampleCount: taken,
            droppedSamples: buffer.dropped
        )
    }

    public func reset() {
        buffer.clear()
        taken = 0
        failed = 0
        startedAt = CFAbsoluteTimeGetCurrent()
    }

    // MARK: - 系统取值

    /// ⚠️ 用 `phys_footprint`，**不是 `resident_size`**。
    /// 系统按 footprint 判定内存压力，用 RSS 会显著高估（共享库被重复计算）。
    public static func footprintBytes() -> Int? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        guard kr == KERN_SUCCESS else { return nil }
        return Int(info.phys_footprint)
    }

    public static func physicalMemory() -> Int {
        Int(ProcessInfo.processInfo.physicalMemory)
    }

    // MARK: - 统计

    /// 最小二乘斜率。
    static func slope(_ points: [(x: Double, y: Double)]) -> Double {
        let n = points.count
        guard n >= 2 else { return 0 }
        let mx = points.reduce(0) { $0 + $1.x } / Double(n)
        let my = points.reduce(0) { $0 + $1.y } / Double(n)
        var num = 0.0, den = 0.0
        for p in points {
            num += (p.x - mx) * (p.y - my)
            den += (p.x - mx) * (p.x - mx)
        }
        return den == 0 ? 0 : num / den
    }
}

// MARK: - 生命周期

#if canImport(UIKit)
public extension MemoryWatermark {
    /// 注册前后台自动暂停/恢复。**强烈建议在 App 启动时调用一次。**
    func observeAppLifecycle() {
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            self?.stop()
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            self?.start()
        }
    }
}
#endif
