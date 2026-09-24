import type { LaunchType, NativeBridge, StartupPhase, StartupRecord } from '../types';
import { type Clock, systemClock } from '../core/clock';

export interface StartupTracerOptions {
  clock?: Clock;
  native?: NativeBridge;
  /**
   * 距离进程创建多久之内算冷启动（毫秒）。
   * 这只是**启发式** —— 真正准确的冷/温/热判定需要原生提供，
   * 拿不准时请用 `setLaunchType()` 显式指定，而不是相信推测。
   */
  coldWindowMs?: number;
}

interface Mark {
  name: string;
  monotonic: number;
  wall: number;
}

/**
 * 启动分段打点。
 *
 * ## 为什么必须自建
 * 所有通用手段（MetricKit / Sentry App Start / Macrobenchmark）都只能给出
 * **启动总耗时**，拿不到 RN 内部阶段。而不知道"时间花在哪一段"，
 * 就只能靠猜。这个类是 RN 启动黑盒唯一的解法。
 *
 * ## 口径（与 apm 知识库一致）
 * - 起点：**进程创建**（由原生通过 NativeBridge 提供）
 * - 终点：**首屏渲染完成**（`markFirstScreen()`）
 * - 拿不到进程创建时间时，`hasProcessStart = false`，总耗时只是相对值，
 *   **不可与线上基线对比** —— 记录里会如实标注。
 */
export class StartupTracer {
  private readonly clock: Clock;
  private readonly native?: NativeBridge;
  private readonly coldWindowMs: number;

  /** 同一次实例化即代表"当前 JS 上下文是新的" */
  private readonly anchorMonotonic: number;
  private readonly anchorWall: number;

  private readonly marks: Mark[] = [];
  private explicitLaunchType: LaunchType | null = null;
  private readonly processStartEpochMs: number | null;
  private firstScreenMarked = false;

  constructor(opts: StartupTracerOptions = {}) {
    this.clock = opts.clock ?? systemClock;
    this.native = opts.native;
    this.coldWindowMs = opts.coldWindowMs ?? 3000;

    this.anchorMonotonic = this.clock.monotonic();
    this.anchorWall = this.clock.wall();

    let ps: number | null = null;
    try {
      ps = this.native?.getProcessStartTime?.() ?? null;
    } catch {
      ps = null; // 原生能力不可用绝不影响启动
    }
    this.processStartEpochMs = ps;
  }

  get hasProcessStart(): boolean {
    return this.processStartEpochMs !== null;
  }

  /** 显式指定启动类型。拿不准时用这个，而不是依赖推测。 */
  setLaunchType(t: LaunchType): void {
    this.explicitLaunchType = t;
  }

  /**
   * 打一个时间点。重复同名会被忽略（保留第一次，避免 hot reload 污染）。
   * **该方法是同步且极廉价的** —— 它会被插在启动关键路径上。
   */
  mark(name: string): void {
    if (this.marks.some((m) => m.name === name)) return;
    this.marks.push({
      name,
      monotonic: this.clock.monotonic(),
      wall: this.clock.wall(),
    });
  }

  /** 标记首屏渲染完成。这是启动的终点。 */
  markFirstScreen(): void {
    this.mark('firstScreen');
    this.firstScreenMarked = true;
  }

  get isFirstScreenMarked(): boolean {
    return this.firstScreenMarked;
  }

  private detectLaunchType(): LaunchType {
    if (this.explicitLaunchType) return this.explicitLaunchType;
    if (this.processStartEpochMs === null) return 'unknown';
    const delta = this.anchorWall - this.processStartEpochMs;
    if (delta < 0) return 'unknown'; // 时钟异常，不猜
    return delta <= this.coldWindowMs ? 'cold' : 'warm';
  }

  /**
   * 生成启动记录。
   * 未标记首屏时返回 null —— **宁可不报，也不要报一个不完整的启动时长**。
   */
  getRecord(): StartupRecord | null {
    if (!this.firstScreenMarked || this.marks.length < 2) return null;

    const launchType = this.detectLaunchType();
    const baseMonotonic = this.marks[0]?.monotonic ?? 0;

    const phases: StartupPhase[] = [];
    let prev = baseMonotonic;
    for (const m of this.marks) {
      // 有进程创建时间就以其为原点，否则退化为相对首个打点
      const sinceProcessStartMs =
        this.processStartEpochMs !== null ? m.wall - this.processStartEpochMs : m.monotonic - baseMonotonic;
      phases.push({
        name: m.name,
        sinceProcessStartMs,
        deltaMs: m.monotonic - prev,
      });
      prev = m.monotonic;
    }

    const last = this.marks[this.marks.length - 1] as Mark;
    const totalMs =
      this.processStartEpochMs !== null
        ? last.wall - this.processStartEpochMs
        : last.monotonic - baseMonotonic;

    return { launchType, phases, totalMs, hasProcessStart: this.hasProcessStart };
  }

  /** 已打点数量，用于白屏检测判断"首屏打点是否发生"。 */
  get markCount(): number {
    return this.marks.length;
  }

  reset(): void {
    this.marks.length = 0;
    this.firstScreenMarked = false;
  }
}

/**
 * **标准阶段名常量** —— 请务必用这些，不要自创。
 * 阶段名不一致会让基线对比失去意义（同名才能比）。
 */
export const PHASE = {
  /** 原生进程创建（由 NativeBridge 自动提供，无需手动打） */
  PROCESS_START: 'processStart',
  /** JS bundle 开始求值（在 index.js 顶部调用 mark(PHASE.JS_START)） */
  JS_START: 'jsStart',
  /** 根组件开始挂载 */
  ROOT_MOUNT: 'rootMount',
  /** 根组件挂载完成 */
  ROOT_MOUNTED: 'rootMounted',
  /** 首屏数据请求开始 */
  FIRST_REQUEST: 'firstRequest',
  /** 首屏数据返回 */
  FIRST_RESPONSE: 'firstResponse',
  /** 首屏渲染完成（终点） */
  FIRST_SCREEN: 'firstScreen',
} as const;
