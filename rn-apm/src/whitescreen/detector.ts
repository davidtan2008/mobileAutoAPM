import type { WhiteScreenRecord } from '../types';
import { type Clock, systemClock } from '../core/clock';

export interface WhiteScreenDetectorOptions {
  clock?: Clock;
  /** 超过多久未渲染判为白屏。默认 2000ms（>1s 即需关注，>2s 视为故障）。 */
  defaultTimeoutMs?: number;
  /** 判定白屏时回调（此时尚未渲染） */
  onWhiteScreen?: (record: WhiteScreenRecord) => void;
  /** 超时后最终又渲染出来了 —— 这不是白屏，而是**慢**，单独回调以便区分 */
  onLateRender?: (record: WhiteScreenRecord) => void;
}

interface PageSession {
  routeName: string;
  startedAtMs: number;
  startedWall: number;
  timeoutMs: number;
  rendered: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  /** 已判定白屏的时长，渲染后回填 */
  timedOutMs: number | null;
}

/**
 * 白屏检测器（超时口径）。
 *
 * ## 口径说明 —— 这一点常被做错
 * **"超时未渲染" ≠ "白屏"。**
 * 页面可能在 5 秒后渲染出来了 —— 那是**慢**，不是白屏。
 * 二者根因和修法完全不同：白屏通常是渲染分支/数据缺失，
 * 慢通常是接口慢或首屏依赖串行。
 *
 * 因此本检测器把两种情况分开回调（`onWhiteScreen` / `onLateRender`），
 * 并在记录里如实标注 `eventuallyRendered` 与 `renderedAfterMs`。
 *
 * ⚠️ **这是三条白屏检测路线中的第一条（打点+超时）**，
 * 它无固有无误报，但需要埋点。另两条（截图比对 / View 树检测）见
 * `plugins/mobile-apm/skills/apm-render`，建议组合使用。
 */
export class WhiteScreenDetector {
  private readonly clock: Clock;
  private readonly defaultTimeoutMs: number;
  private readonly onWhiteScreen?: (r: WhiteScreenRecord) => void;
  private readonly onLateRender?: (r: WhiteScreenRecord) => void;
  private readonly sessions = new Map<string, PageSession>();

  constructor(opts: WhiteScreenDetectorOptions = {}) {
    this.clock = opts.clock ?? systemClock;
    this.defaultTimeoutMs = opts.defaultTimeoutMs ?? 2000;
    this.onWhiteScreen = opts.onWhiteScreen;
    this.onLateRender = opts.onLateRender;
  }

  /** 页面进入时调用（导航到该页 / 组件 mount）。 */
  startPage(routeName = 'default', timeoutMs = this.defaultTimeoutMs): void {
    this.cancel(routeName);
    const session: PageSession = {
      routeName,
      startedAtMs: this.clock.monotonic(),
      startedWall: this.clock.wall(),
      timeoutMs,
      rendered: false,
      timer: null,
      timedOutMs: null,
    };

    session.timer = setTimeout(() => {
      session.timer = null;
      if (session.rendered) return;
      const elapsed = this.clock.monotonic() - session.startedAtMs;
      session.timedOutMs = elapsed;
      try {
        this.onWhiteScreen?.({
          routeName,
          timeoutMs: session.timeoutMs,
          eventuallyRendered: false,
          occurredAt: this.clock.wall(),
        });
      } catch {
        /* 回调失败不影响业务 */
      }
    }, timeoutMs);

    const t = session.timer as unknown as { unref?: () => void };
    if (typeof t.unref === 'function') t.unref();

    this.sessions.set(routeName, session);
  }

  /** 首屏渲染完成时调用。 */
  markRendered(routeName = 'default'): void {
    const session = this.sessions.get(routeName);
    if (!session || session.rendered) return;

    session.rendered = true;
    if (session.timer !== null) {
      clearTimeout(session.timer);
      session.timer = null;
    }

    // 超时后才渲染 —— 这是"慢"，不是白屏，单独上报
    if (session.timedOutMs !== null) {
      try {
        this.onLateRender?.({
          routeName,
          timeoutMs: session.timeoutMs,
          eventuallyRendered: true,
          renderedAfterMs: this.clock.monotonic() - session.startedAtMs,
          occurredAt: this.clock.wall(),
        });
      } catch {
        /* 忽略 */
      }
    }
    this.sessions.delete(routeName);
  }

  /** 页面离开时清理（避免定时器泄漏）。 */
  cancel(routeName = 'default'): void {
    const s = this.sessions.get(routeName);
    if (s?.timer != null) clearTimeout(s.timer);
    this.sessions.delete(routeName);
  }

  cancelAll(): void {
    for (const s of this.sessions.values()) {
      if (s.timer !== null) clearTimeout(s.timer);
    }
    this.sessions.clear();
  }

  /** 当前进行中的页面数（用于诊断是否有页面没清理）。 */
  get pendingCount(): number {
    return this.sessions.size;
  }
}
