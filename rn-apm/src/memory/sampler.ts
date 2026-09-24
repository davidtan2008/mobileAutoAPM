import type { MemoryReport, MemoryUsage, NativeBridge } from '../types';
import { RingBuffer } from '../core/ring-buffer';
import { slope } from '../core/stats';
import { type Clock, systemClock } from '../core/clock';

export interface MemorySamplerOptions {
  native?: NativeBridge;
  clock?: Clock;
  /** 采样间隔。太密会有开销，太疏会漏掉峰值。默认 10s。 */
  intervalMs?: number;
  /** 环形缓冲容量。**必须有界**。默认 180 个样本（10s 间隔 ≈ 30 分钟）。 */
  capacity?: number;
  onSample?: (u: MemoryUsage) => void;
}

/**
 * 内存水位采样器。
 *
 * ## 为什么这是 P0 能力
 * iOS 上 **MetricKit 的 MXDiagnosticPayload 不含 jetsam 诊断**
 * （Apple 增强请求 FB9972410 提了 4 年仍未落地），
 * 官方唯一能给到的只有 `MXAppExitMetric` 的两个**计数**，没有详情。
 *
 * 也就是说：**不自建内存水位，FOOM 是完全不可见的。**
 * 用户表现为"App 莫名退出"，而你在崩溃平台上看不到任何记录。
 *
 * ## 两条设计约束
 * 1. **有界**：样本存环形缓冲，长时间运行不增长。
 * 2. **廉价且不抛错**：采样失败静默跳过，绝不影响业务。
 */
export class MemorySampler {
  private readonly buf: RingBuffer<MemoryUsage>;
  private readonly clock: Clock;
  private readonly native?: NativeBridge;
  private readonly intervalMs: number;
  private readonly onSample?: (u: MemoryUsage) => void;

  private timer: ReturnType<typeof setInterval> | null = null;
  private startedAt = 0;
  private samplesTaken = 0;
  private failedSamples = 0;

  constructor(opts: MemorySamplerOptions = {}) {
    this.clock = opts.clock ?? systemClock;
    this.native = opts.native;
    this.intervalMs = opts.intervalMs ?? 10_000;
    this.buf = new RingBuffer<MemoryUsage>(opts.capacity ?? 180);
    this.onSample = opts.onSample;
  }

  get isRunning(): boolean {
    return this.timer !== null;
  }

  get sampleCount(): number {
    return this.samplesTaken;
  }

  get failedCount(): number {
    return this.failedSamples;
  }

  /** 采一次样。返回是否成功（拿不到原生能力时返回 false，不抛错）。 */
  sampleOnce(): boolean {
    try {
      const mem = this.native?.getMemoryUsage?.();
      if (!mem || typeof mem.usedBytes !== 'number' || !Number.isFinite(mem.usedBytes)) {
        this.failedSamples++;
        return false;
      }
      const usage: MemoryUsage = {
        t: this.clock.monotonic() - this.startedAt,
        usedBytes: mem.usedBytes,
      };
      if (typeof mem.totalBytes === 'number') usage.totalBytes = mem.totalBytes;
      this.buf.push(usage);
      this.samplesTaken++;
      this.onSample?.(usage);
      return true;
    } catch {
      this.failedSamples++;
      return false;
    }
  }

  /** 开始周期采样。重复调用是幂等的。 */
  start(intervalMs = this.intervalMs): void {
    if (this.timer !== null) return;
    if (this.startedAt === 0) this.startedAt = this.clock.monotonic();
    this.sampleOnce(); // 立刻采一次，避免开头空白
    this.timer = setInterval(() => this.sampleOnce(), intervalMs);
    const t = this.timer as unknown as { unref?: () => void };
    if (typeof t.unref === 'function') t.unref();
  }

  /**
   * 暂停采样。**App 切到后台时必须调用** —— 后台采样既无意义又耗电，
   * 且会干扰对"后台内存"的判断。
   */
  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /**
   * 生成内存报告。
   *
   * ⚠️ **判断泄漏看斜率，不看峰值。**
   * 峰值下降可能只是缓存差异；"斜率从线性增长变为趋于 0"才是泄漏被修复的证据。
   */
  getReport(): MemoryReport | null {
    const samples = this.buf.toArray();
    if (samples.length === 0) return null;

    let peak = 0;
    for (const s of samples) {
      if (s.usedBytes > peak) peak = s.usedBytes;
    }

    const points = samples.map((s) => ({ x: s.t / 1000, y: s.usedBytes }));
    const slopeBytesPerSec = slope(points);

    return {
      samples,
      peakBytes: peak,
      slopeBytesPerSec,
      sampleCount: this.samplesTaken,
      droppedSamples: this.buf.dropped,
    };
  }

  /** 崩溃瞬间的内存快照，供 CrashCollector 使用。 */
  current(): MemoryUsage | null {
    try {
      const mem = this.native?.getMemoryUsage?.();
      if (!mem || typeof mem.usedBytes !== 'number') return null;
      const usage: MemoryUsage = {
        t: this.clock.monotonic() - this.startedAt,
        usedBytes: mem.usedBytes,
      };
      if (typeof mem.totalBytes === 'number') usage.totalBytes = mem.totalBytes;
      return usage;
    } catch {
      return null;
    }
  }

  reset(): void {
    this.buf.clear();
    this.samplesTaken = 0;
    this.failedSamples = 0;
    this.startedAt = this.clock.monotonic();
  }
}

/**
 * 泄漏判定的参考阈值。
 *
 * ⚠️ **这些是启发式，不是判决。** 真实判定必须结合业务：
 * 缓存预热期本来就该涨。"疑似泄漏"只应触发进一步排查，不应直接下结论。
 */
export function describeSlope(slopeBytesPerSec: number): {
  level: 'flat' | 'slow-growth' | 'fast-growth';
  hint: string;
} {
  const mbPerHour = (slopeBytesPerSec * 3600) / (1024 * 1024);
  if (Math.abs(mbPerHour) < 1) {
    return { level: 'flat', hint: `约 ${mbPerHour.toFixed(2)} MB/小时，趋于平稳` };
  }
  if (mbPerHour < 10) {
    return {
      level: 'slow-growth',
      hint: `约 ${mbPerHour.toFixed(1)} MB/小时，缓慢增长。需确认是否为正常缓存预热`,
    };
  }
  return {
    level: 'fast-growth',
    hint: `约 ${mbPerHour.toFixed(1)} MB/小时，**快速增长，疑似泄漏**。建议按同页面进出 20-30 次放大复现，再抓堆快照对比`,
  };
}
