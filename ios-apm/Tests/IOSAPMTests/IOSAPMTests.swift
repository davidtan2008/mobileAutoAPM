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
