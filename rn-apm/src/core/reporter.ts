import type { ApmPayload, StorageAdapter, Transport } from '../types';
import { type Clock, systemClock } from './clock';
import { SafeStorage } from './storage';

const QUEUE_KEY = 'rn-apm:queue:v1';

export interface ReporterOptions {
  transport: Transport;
  /** 落盘队列，使崩溃数据能跨进程存活。不传则只存内存。 */
  storage?: StorageAdapter;
  clock?: Clock;
  /** 内存队列上限。**必须有界** —— 无界队列在断网时会吃光内存。 */
  maxQueueSize?: number;
  /** 落地到存储的上限（比内存队列更小，避免存储被撑爆）。 */
  maxPersisted?: number;
  /** 每轮 flush 最多发送几条 */
  batchSize?: number;
  /** 自动 flush 间隔（毫秒）。0 表示不自动 flush。 */
  flushIntervalMs?: number;
  /** 单条最大重试次数，超过则丢弃（避免死信永久占位） */
  maxAttempts?: number;
  baseBackoffMs?: number;
  onError?: (e: unknown) => void;
}

interface QueueEntry {
  payload: ApmPayload;
  attempts: number;
  /** 下次可尝试时间（单调时钟） */
  nextAttemptAt: number;
}

/**
 * 上报器。
 *
 * 三条不可违背的约束：
 *  1. **绝不抛错**：任何异常都被吞掉并计入 onError。埋点不能影响业务。
 *  2. **绝不阻塞**：`enqueue` 是同步的，网络与存储都在后台进行。
 *  3. **队列有界**：断网时队列会满，此时丢弃**最旧**的并计数，而不是无限增长。
 *
 * 另有一条容易被忽略的：**崩溃数据要立即落盘**。
 * 崩溃发生后进程可能马上死掉，只放内存队列必然丢失 —— 那正是最需要的数据。
 * 用 `enqueue(payload, { persist: true })`。
 */
export class Reporter {
  private readonly queue: QueueEntry[] = [];
  private readonly maxQueueSize: number;
  private readonly maxPersisted: number;
  private readonly batchSize: number;
  private readonly maxAttempts: number;
  private readonly baseBackoffMs: number;
  private readonly clock: Clock;
  private readonly storage?: SafeStorage;
  private readonly transport: Transport;
  private readonly onError?: (e: unknown) => void;

  private dropped = 0;
  private sent = 0;
  private failed = 0;
  private flushing = false;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(opts: ReporterOptions) {
    this.transport = opts.transport;
    this.clock = opts.clock ?? systemClock;
    this.storage = opts.storage ? new SafeStorage(opts.storage) : undefined;
    this.maxQueueSize = opts.maxQueueSize ?? 200;
    this.maxPersisted = opts.maxPersisted ?? 50;
    this.batchSize = opts.batchSize ?? 10;
    this.maxAttempts = opts.maxAttempts ?? 5;
    this.baseBackoffMs = opts.baseBackoffMs ?? 2000;
    this.onError = opts.onError;
  }

  get queueSize(): number {
    return this.queue.length;
  }

  /** 因队列满被丢弃的条数。**持续增长说明上报通道有问题**，需要告警。 */
  get droppedCount(): number {
    return this.dropped;
  }

  get sentCount(): number {
    return this.sent;
  }

  get failedCount(): number {
    return this.failed;
  }

  /**
   * 入队。同步返回，永不抛错。
   * @param persist 是否立即落盘。**崩溃数据必须传 true**。
   */
  enqueue(payload: ApmPayload, opts: { persist?: boolean } = {}): void {
    try {
      if (this.queue.length >= this.maxQueueSize) {
        this.queue.shift();
        this.dropped++;
      }
      this.queue.push({
        payload,
        attempts: 0,
        nextAttemptAt: this.clock.monotonic(),
      });
      if (opts.persist) {
        // 不 await —— 崩溃路径上不能等待异步
        void this.persist();
      }
    } catch (e) {
      this.report(e);
    }
  }

  /** 从存储恢复上次未发完的队列（下次启动时调用）。 */
  async restore(): Promise<number> {
    if (!this.storage) return 0;
    try {
      const raw = await this.storage.getItem(QUEUE_KEY);
      if (!raw) return 0;
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return 0;
      let n = 0;
      for (const payload of parsed as ApmPayload[]) {
        if (this.queue.length >= this.maxQueueSize) break;
        this.queue.push({ payload, attempts: 0, nextAttemptAt: this.clock.monotonic() });
        n++;
      }
      return n;
    } catch (e) {
      this.report(e);
      return 0;
    }
  }

  /** 立即尝试发送一批。永不抛错。 */
  async flush(): Promise<void> {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const now = this.clock.monotonic();
      let processed = 0;

      while (this.queue.length > 0 && processed < this.batchSize) {
        const entry = this.queue[0];
        if (!entry) break;
        if (entry.nextAttemptAt > now) break; // 退避中，本轮跳过

        try {
          await this.transport.send(entry.payload);
          this.queue.shift();
          this.sent++;
        } catch (e) {
          entry.attempts++;
          this.failed++;
          this.report(e);
          if (entry.attempts >= this.maxAttempts) {
            // 超过重试上限：丢弃，避免死信永久占用队列
            this.queue.shift();
            this.dropped++;
          } else {
            entry.nextAttemptAt = now + this.baseBackoffMs * 2 ** (entry.attempts - 1);
          }
        }
        processed++;
      }

      if (this.storage && processed > 0) {
        await this.persist();
      }
    } catch (e) {
      this.report(e);
    } finally {
      this.flushing = false;
    }
  }

  /** 启动定时 flush。 */
  start(intervalMs = 10_000): void {
    this.stop();
    if (intervalMs <= 0) return;
    this.timer = setInterval(() => {
      void this.flush();
    }, intervalMs);
    // Node/RN 环境下不要让定时器拖住进程退出
    const t = this.timer as unknown as { unref?: () => void };
    if (typeof t.unref === 'function') t.unref();
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** 只剩落盘动作时用的快照，便于测试与诊断。 */
  stats(): { queueSize: number; dropped: number; sent: number; failed: number } {
    return {
      queueSize: this.queue.length,
      dropped: this.dropped,
      sent: this.sent,
      failed: this.failed,
    };
  }

  private async persist(): Promise<void> {
    if (!this.storage) return;
    try {
      // 只落盘最近 maxPersisted 条，避免存储被撑爆
      const tail = this.queue.slice(-this.maxPersisted).map((e) => e.payload);
      await this.storage.setItem(QUEUE_KEY, JSON.stringify(tail));
    } catch (e) {
      this.report(e);
    }
  }

  private report(e: unknown): void {
    try {
      this.onError?.(e);
    } catch {
      // onError 自己抛错也要吞掉
    }
  }
}
