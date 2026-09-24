import Foundation

/// 定容环形缓冲。
///
/// **核心约束：绝不增长。**
/// 它用来缓存内存水位样本 —— 如果缓冲本身随运行时长增长，
/// 就会出现一个荒谬的结果：**一个用来发现内存泄漏的工具，自己泄漏内存。**
///
/// 因此：容量在构造时确定，底层数组一次性分配，写满后覆盖最旧元素，
/// 并累计 `dropped`（持续增长说明容量配置偏小）。
public final class RingBuffer<T> {
    private var storage: [T?]
    private var next = 0
    private var filled = 0
    private var evicted = 0
    private let lock = NSLock()

    public let capacity: Int

    public init(capacity: Int) {
        precondition(capacity > 0, "RingBuffer capacity 必须是正整数，收到 \(capacity)")
        self.capacity = capacity
        self.storage = Array(repeating: nil, count: capacity)
    }

    /// 写入。写满时覆盖最旧元素并计入 dropped。
    public func push(_ item: T) {
        lock.lock()
        defer { lock.unlock() }
        if filled == capacity { evicted += 1 }
        storage[next] = item
        next = (next + 1) % capacity
        if filled < capacity { filled += 1 }
    }

    /// 按时间顺序（最旧 → 最新）导出快照。
    public func snapshot() -> [T] {
        lock.lock()
        defer { lock.unlock() }
        var out: [T] = []
        out.reserveCapacity(filled)
        let start = filled == capacity ? next : 0
        for i in 0..<filled {
            if let v = storage[(start + i) % capacity] { out.append(v) }
        }
        return out
    }

    public var count: Int {
        lock.lock(); defer { lock.unlock() }
        return filled
    }

    /// 被覆盖掉的元素总数。持续增长说明容量偏小。
    public var dropped: Int {
        lock.lock(); defer { lock.unlock() }
        return evicted
    }

    public var isFull: Bool {
        lock.lock(); defer { lock.unlock() }
        return filled == capacity
    }

    /// 清空（不释放底层数组，避免反复分配）。
    public func clear() {
        lock.lock()
        defer { lock.unlock() }
        for i in 0..<capacity { storage[i] = nil }
        next = 0
        filled = 0
        evicted = 0
    }
}
