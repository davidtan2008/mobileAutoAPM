import XCTest
@testable import IOSAPM

final class RingBufferTests: XCTestCase {
    /// **最重要的测试**：定容缓冲长时间写入不许增长。
    /// 一个用来发现内存泄漏的工具，自己绝不能泄漏内存。
    func testDoesNotGrowUnderSustainedWrites() {
        let rb = RingBuffer<Int>(capacity: 50)
        for i in 0..<100_000 { rb.push(i) }
        XCTAssertEqual(rb.count, 50, "元素个数必须恒定在容量上")
        XCTAssertEqual(rb.snapshot().count, 50)
        XCTAssertEqual(rb.dropped, 100_000 - 50)
    }

    func testOrderIsOldestToNewestAfterWrap() {
        let rb = RingBuffer<Int>(capacity: 4)
        for i in 1...10 { rb.push(i) }
        XCTAssertEqual(rb.snapshot(), [7, 8, 9, 10], "回绕后顺序仍须正确")
    }

    func testClearResetsState() {
        let rb = RingBuffer<Int>(capacity: 3)
        for i in 1...5 { rb.push(i) }
        rb.clear()
        XCTAssertEqual(rb.count, 0)
        XCTAssertEqual(rb.dropped, 0)
        XCTAssertEqual(rb.snapshot(), [])
        rb.push(9)
        XCTAssertEqual(rb.snapshot(), [9])
    }
}

final class MemoryWatermarkTests: XCTestCase {
    func testFootprintIsAvailable() {
        let bytes = MemoryWatermark.footprintBytes()
        XCTAssertNotNil(bytes, "phys_footprint 必须可读")
        if let b = bytes {
            XCTAssertGreaterThan(b, 0)
            // 合理性：不应超过设备物理内存
            XCTAssertLessThan(b, MemoryWatermark.physicalMemory() * 2)
        }
    }

    func testSlopeDetectsGrowth() {
        let rising: [(x: Double, y: Double)] = [(0, 100), (1, 200), (2, 300), (3, 400)]
        XCTAssertEqual(MemoryWatermark.slope(rising), 100, accuracy: 1e-9)

        let flat: [(x: Double, y: Double)] = [(0, 100), (1, 101), (2, 99), (3, 100)]
        XCTAssertEqual(MemoryWatermark.slope(flat), 0, accuracy: 1)

        let falling: [(x: Double, y: Double)] = [(0, 400), (1, 300), (2, 200)]
        XCTAssertLessThan(MemoryWatermark.slope(falling), 0)
    }

    func testSlopeEdgeCases() {
        XCTAssertEqual(MemoryWatermark.slope([]), 0)
        XCTAssertEqual(MemoryWatermark.slope([(x: 1, y: 5)]), 0)
        XCTAssertEqual(MemoryWatermark.slope([(x: 1, y: 5), (x: 1, y: 9)]), 0)
    }

    func testNegativeSlopeIsNotDescribedAsGrowth() {
        // 起因：growthDescription 的第二个判断写成 `mbPerHour < 10`（漏了 abs），
        // 于是内存**明显下降**（如 −20 MB/h，缓存释放 / 对象已回收）
        // 会落进「缓慢增长」分支，标签与事实相反。
        let shrinking = MemoryReport(
            samples: [], peakBytes: 0,
            slopeBytesPerSec: -20 * 1024 * 1024,
            sampleCount: 0, droppedSamples: 0
        )
        let text = shrinking.growthDescription
        XCTAssertTrue(text.contains("下降"), "提示语要说明是下降，实际：\(text)")
        XCTAssertFalse(text.contains("增长"), "不得出现「增长」字样，实际：\(text)")

        // 大幅下降同样不能报成增长
        let bigDrop = MemoryReport(
            samples: [], peakBytes: 0,
            slopeBytesPerSec: -100 * 1024 * 1024,
            sampleCount: 0, droppedSamples: 0
        )
        XCTAssertFalse(bigDrop.growthDescription.contains("增长"))

        // 极小的负斜率仍算平稳。
        // 注意 growthDescription 的入参是 **bytes/秒**，内部乘 3600 变成 MB/小时。
        // 要落进「平稳」区间（|MB每小时| < 1），slope 需 < 1MB/h ≈ 291 bytes/s。
        let flat = MemoryReport(
            samples: [], peakBytes: 0,
            slopeBytesPerSec: -100,
            sampleCount: 0, droppedSamples: 0
        )
        XCTAssertTrue(flat.growthDescription.contains("平稳"))
    }

    func testSamplingProducesReport() {
        let wm = MemoryWatermark(capacity: 5, interval: 1000)
        for _ in 0..<3 { XCTAssertTrue(wm.sampleOnce()) }
        let report = wm.makeReport()
        XCTAssertNotNil(report)
        XCTAssertEqual(report?.samples.count, 3)
        XCTAssertEqual(report?.droppedSamples, 0)
        XCTAssertGreaterThan(report?.peakBytes ?? 0, 0)
    }

    func testReportNilWhenNoSamples() {
        let wm = MemoryWatermark(capacity: 5)
        XCTAssertNil(wm.makeReport())
    }
}

final class LaunchTrackerTests: XCTestCase {
    func testActivateIsIdempotentAndReadsPremain() {
        let t = LaunchTracker.shared
        let first = t.activate()
        let second = t.activate()
        XCTAssertTrue(first, "首次 activate 应返回 true")
        XCTAssertFalse(second, "重复 activate 应返回 false（幂等）")
    }

    func testReportIsNilBeforeFirstFrame() {
        // 未标首屏就出报告，会给出误导性的"启动耗时"
        let report = LaunchTracker.shared.makeReport()
        if let r = report {
            XCTAssertTrue(r.marks.contains { $0.stage == .firstFrame },
                          "有报告就必须已含 firstFrame")
        }
    }

    func testStageRawValuesAreStable() {
        // 阶段名是跨版本对比基线的锚点，**改名会破坏历史可比性**
        XCTAssertEqual(LaunchStage.processStart.rawValue, "processStart")
        XCTAssertEqual(LaunchStage.firstFrame.rawValue, "firstFrame")
        XCTAssertEqual(LaunchStage.appReady.rawValue, "appReady")
    }
}
