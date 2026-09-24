import type { Breadcrumb } from '../types';
import { RingBuffer } from '../core/ring-buffer';
import { type Clock, systemClock } from '../core/clock';

/**
 * 崩溃前轨迹。
 *
 * 崩溃报告只告诉你"在哪崩的"，不告诉你"用户之前做了什么"。
 * 没有轨迹的崩溃分析，一半时间花在复现上。
 *
 * 用**定容环形缓冲**实现：轨迹不能成为内存增长源。
 */
export class BreadcrumbStore {
  private readonly buf: RingBuffer<Breadcrumb>;
  private readonly clock: Clock;
  private readonly startedAt: number;

  constructor(opts: { capacity?: number; clock?: Clock } = {}) {
    this.buf = new RingBuffer<Breadcrumb>(opts.capacity ?? 50);
    this.clock = opts.clock ?? systemClock;
    this.startedAt = this.clock.monotonic();
  }

  /** 记一条轨迹。**必须廉价且永不抛错** —— 它会被插在业务路径上。 */
  add(category: string, message: string, data?: Record<string, unknown>): void {
    try {
      this.buf.push({
        t: this.clock.monotonic() - this.startedAt,
        category,
        message: message.length > 200 ? `${message.slice(0, 200)}…` : message,
        // 只留浅层数据，避免把大对象引用进缓冲区（那本身就是内存泄漏）
        data: data ? shallowCopy(data) : undefined,
      });
    } catch {
      // 轨迹记录失败绝不能影响业务
    }
  }

  snapshot(): Breadcrumb[] {
    return this.buf.toArray().map((b) => ({ ...b }));
  }

  get size(): number {
    return this.buf.size;
  }

  get dropped(): number {
    return this.buf.dropped;
  }

  clear(): void {
    this.buf.clear();
  }
}

/** 只拷贝一层，且丢弃函数/循环引用/超大值。 */
function shallowCopy(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (typeof v === 'function') continue;
    if (typeof v === 'string') {
      out[k] = v.length > 200 ? `${v.slice(0, 200)}…` : v;
      continue;
    }
    if (v === null || typeof v === 'number' || typeof v === 'boolean' || typeof v === 'undefined') {
      out[k] = v;
      continue;
    }
    // 对象/数组只记类型，不深拷贝 —— 深拷贝会把整个对象图拖进内存
    out[k] = Array.isArray(v) ? `[Array(${v.length})]` : `[${typeof v}]`;
  }
  return out;
}
