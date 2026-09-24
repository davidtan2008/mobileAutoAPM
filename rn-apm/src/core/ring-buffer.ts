/**
 * 定容环形缓冲。
 *
 * **这个类最关键的约束是"绝不增长"。**
 * 它被用来缓存内存水位样本和崩溃前轨迹 —— 如果缓冲本身会随运行时长增长，
 * 那就出现了一个荒谬的结果：**一个用来发现内存泄漏的工具自己泄漏内存**。
 *
 * 因此：
 *   - 容量在构造时确定，底层数组一次性分配，之后不再分配新数组
 *   - 写满后覆盖最旧元素，并累计 `dropped`（用于发现"采样过密"这类配置问题）
 */
export class RingBuffer<T> {
  private readonly buf: Array<T | undefined>;
  /** 下一个写入位置 */
  private next = 0;
  /** 当前已写入的元素个数（≤ capacity） */
  private filled = 0;
  /** 因写满而被覆盖的元素总数 */
  private evicted = 0;

  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      throw new RangeError(`RingBuffer capacity 必须是正整数，收到 ${capacity}`);
    }
    this.buf = new Array<T | undefined>(capacity);
  }

  /** 写入一个元素。写满时覆盖最旧元素并计入 dropped。 */
  push(item: T): void {
    if (this.filled === this.capacity) {
      this.evicted++;
    }
    this.buf[this.next] = item;
    this.next = (this.next + 1) % this.capacity;
    if (this.filled < this.capacity) {
      this.filled++;
    }
  }

  /** 按时间顺序（最旧 → 最新）导出快照。 */
  toArray(): T[] {
    const out: T[] = [];
    const start = this.filled === this.capacity ? this.next : 0;
    for (let i = 0; i < this.filled; i++) {
      out.push(this.buf[(start + i) % this.capacity] as T);
    }
    return out;
  }

  /** 最新的 n 个元素（最新 → 最旧也可通过 reverse 得到）。 */
  last(n: number): T[] {
    const all = this.toArray();
    return n >= all.length ? all : all.slice(all.length - n);
  }

  /** 当前元素个数。 */
  get size(): number {
    return this.filled;
  }

  /** 被覆盖掉的元素总数。持续增长说明容量配置偏小。 */
  get dropped(): number {
    return this.evicted;
  }

  get isFull(): boolean {
    return this.filled === this.capacity;
  }

  /** 清空（不释放底层数组，避免反复分配）。 */
  clear(): void {
    this.buf.fill(undefined);
    this.next = 0;
    this.filled = 0;
    this.evicted = 0;
  }
}
