import type { CrashLayer, CrashRecord, NativeBridge } from '../types';
import type { CrashCollector } from './error-tracker';
import type { BreadcrumbStore } from './breadcrumbs';

/**
 * 四层崩溃捕获的**接线层**。
 *
 * ## 为什么是四层
 * RN 的崩溃分四类，**每一类只有一种机制能捕获，缺一层就是一类盲区**：
 *
 * | 层 | 覆盖 | 缺了会怎样 |
 * |---|---|---|
 * | ErrorBoundary | 同步渲染/生命周期错误 | React 渲染期错误直接整树卸载，无记录 |
 * | JS 全局 | 未处理 JS 异常 | 异步 JS 错误全部丢失 |
 * | Promise | 未处理 rejection | **最大盲区** —— 网络/异步逻辑错误全丢 |
 * | Native | OC/Swift/Java/C++/Hermes 字节码崩溃 | 原生崩溃全丢 |
 *
 * 本模块负责后三层的自动接线（ErrorBoundary 见 error-boundary.tsx）。
 *
 * ## 两条关键的正确性约束
 *
 * 1. **必须链式，不能替换。** `ErrorUtils.setGlobalHandler` 是全局单例，
 *    Sentry/Bugly 等 SDK 也会设置。直接覆盖会**打断别人**，被别人覆盖会**丢自己的数据**。
 *    这里保存并调用前一个 handler。
 *
 * 2. **必须如实报告哪层没接上。** 如果 Promise 跟踪在当前宿主不可用，
 *    必须让人知道 —— 否则会误以为"没崩溃"，实际是"看不见"。
 */

interface ErrorUtilsLike {
  getGlobalHandler?: () => GlobalHandlerFn | undefined;
  setGlobalHandler?: (h: GlobalHandlerFn) => void;
}

type GlobalHandlerFn = (e: unknown, isFatal?: boolean) => void;

export type LayerState = 'active' | 'unavailable' | 'not-installed';

export interface LayerStatus {
  errorBoundary: LayerState;
  jsGlobal: LayerState;
  promise: LayerState;
  native: LayerState;
  /** 每层不可用的原因，用于指导用户补齐 */
  notes: Partial<Record<CrashLayer | 'errorBoundary', string>>;
}

export interface GlobalHandlerOptions {
  collector: CrashCollector;
  breadcrumbs?: BreadcrumbStore;
  native?: NativeBridge;
  /** 崩溃发生时立即回调 —— 调用方应在这里把数据落盘 */
  onCrash?: (record: CrashRecord) => void;
  /** 未显式给出 isFatal 时，JS 全局异常是否视为致命。默认 true。 */
  defaultFatal?: boolean;
}

export interface InstalledHandler {
  uninstall(): void;
  getLayerStatus(): LayerStatus;
  /** ErrorBoundary 挂载时调用，使状态报告准确 */
  markErrorBoundaryMounted(): void;
}

export function installGlobalHandler(opts: GlobalHandlerOptions): InstalledHandler {
  const { collector, breadcrumbs, native, onCrash } = opts;
  const defaultFatal = opts.defaultFatal ?? true;
  const notes: LayerStatus['notes'] = {};

  let errorBoundaryState: LayerState = 'not-installed';
  let promiseState: LayerState = 'not-installed';
  let promiseCleanup: (() => void) | null = null;

  const emit = (layer: CrashLayer, error: unknown, isFatal: boolean, componentStack?: string): void => {
    try {
      breadcrumbs?.add('crash', `${layer}: ${String(error).slice(0, 100)}`);
      const rec = collector.record(layer, error, { isFatal, componentStack });
      // 立即回调，让调用方落盘 —— 进程可能马上死掉
      onCrash?.(rec);
    } catch {
      // 崩溃路径上任何异常都必须吞掉，否则会掩盖原始崩溃
    }
  };

  // ---------- 第 2 层：未处理 JS 异常 ----------
  const g = globalThis as unknown as { ErrorUtils?: ErrorUtilsLike };
  const errorUtils = g.ErrorUtils;
  let jsGlobalState: LayerState = 'unavailable';
  let prevHandler: GlobalHandlerFn | undefined;
  let ourHandler: GlobalHandlerFn | undefined;

  if (errorUtils && typeof errorUtils.setGlobalHandler === 'function') {
    prevHandler = errorUtils.getGlobalHandler?.();
    ourHandler = (e: unknown, isFatal?: boolean): void => {
      emit('jsGlobal', e, isFatal ?? defaultFatal);
      // 链式调用前一个 handler —— **绝不吞掉别人**
      if (prevHandler) {
        try {
          prevHandler(e, isFatal);
        } catch {
          /* 别人的 handler 出错不该影响我们 */
        }
      }
    };
    try {
      errorUtils.setGlobalHandler(ourHandler);
      jsGlobalState = 'active';
    } catch (err) {
      jsGlobalState = 'unavailable';
      notes.jsGlobal = `setGlobalHandler 调用失败：${String(err)}`;
    }
  } else {
    notes.jsGlobal =
      'globalThis.ErrorUtils 不存在。非 React Native 宿主（或在极早期调用）时属于正常现象。';
  }

  // ---------- 第 3 层：未处理 Promise rejection ----------
  promiseCleanup = installPromiseTracking(
    (error) => emit('promise', error, false),
    (state, note) => {
      promiseState = state;
      if (note) notes.promise = note;
    },
  );

  // ---------- 第 4 层：原生崩溃 ----------
  // 由原生崩溃库（KSCrash/SentryCrash/Bugly）负责采集，
  // 这里只做能力探测与状态报告 —— 我们无法从 JS 侧捕获原生崩溃。
  const nativeState: LayerState = native?.reportJsError ? 'active' : 'unavailable';
  if (!native?.reportJsError) {
    notes.native =
      '未接入原生崩溃库。JS↔Native 崩溃关联与原生崩溃捕获需要 KSCrash/SentryCrash/Bugly 等，' +
      '并实现 NativeBridge.reportJsError 做关联。**这一层缺失时原生崩溃完全不可见。**';
  }

  return {
    uninstall(): void {
      if (ourHandler && errorUtils?.getGlobalHandler?.() === ourHandler) {
        try {
          errorUtils.setGlobalHandler?.(prevHandler ?? ((): void => {}));
        } catch {
          /* 忽略 */
        }
      }
      promiseCleanup?.();
      promiseCleanup = null;
    },

    getLayerStatus(): LayerStatus {
      const s: LayerStatus = {
        errorBoundary: errorBoundaryState,
        jsGlobal: jsGlobalState,
        promise: promiseState,
        native: nativeState,
        notes: { ...notes },
      };
      if (errorBoundaryState === 'not-installed') {
        s.notes.errorBoundary =
          '尚未挂载 <ApmErrorBoundary>。**没有它，React 渲染期错误不会被记录**（同步渲染错误只有它和全局 handler 能捕获）。';
      }
      return s;
    },

    markErrorBoundaryMounted(): void {
      errorBoundaryState = 'active';
      delete notes.errorBoundary;
    },
  };
}

/**
 * 安装 Promise rejection 跟踪。**按可用性逐级降级，并如实报告结果。**
 *
 * RN 里没有一个统一可靠的办法：
 *  - Hermes 需要 `enablePromiseRejectionTracker`（部分版本需要原生开启）
 *  - `unhandledrejection` 事件在 RN 里行为不一致
 * 无法安装时必须明说，不能静默当作"已接好"。
 */
function installPromiseTracking(
  onUnhandled: (error: unknown) => void,
  setState: (state: LayerState, note?: string) => void,
): (() => void) | null {
  // 路径 A：Hermes 原生 tracker
  const hermes = (globalThis as unknown as {
    HermesInternal?: {
      enablePromiseRejectionTracker?: (opts: {
        allRejections?: boolean;
        onUnhandled?: (id: number, error: unknown) => void;
        onHandled?: (id: number) => void;
      }) => void;
    };
  }).HermesInternal;

  if (hermes && typeof hermes.enablePromiseRejectionTracker === 'function') {
    try {
      hermes.enablePromiseRejectionTracker({
        allRejections: true,
        onUnhandled: (_id: number, error: unknown) => onUnhandled(error),
      });
      setState('active');
      return null; // Hermes tracker 没有关闭接口
    } catch (e) {
      setState('unavailable', `Hermes enablePromiseRejectionTracker 调用失败：${String(e)}`);
    }
  }

  // 路径 B：标准 unhandledrejection 事件
  const target = globalThis as unknown as {
    addEventListener?: (t: string, h: (ev: unknown) => void) => void;
    removeEventListener?: (t: string, h: (ev: unknown) => void) => void;
  };

  if (typeof target.addEventListener === 'function') {
    const handler = (ev: unknown): void => {
      const e = ev as { reason?: unknown; detail?: { reason?: unknown } };
      onUnhandled(e?.reason ?? e?.detail?.reason ?? ev);
    };
    try {
      target.addEventListener('unhandledrejection', handler);
      setState('active');
      return () => target.removeEventListener?.('unhandledrejection', handler);
    } catch (e) {
      setState('unavailable', `addEventListener('unhandledrejection') 失败：${String(e)}`);
    }
  }

  setState(
    'unavailable',
    '当前宿主既无 HermesInternal.enablePromiseRejectionTracker，也无 unhandledrejection 事件。' +
      '**未处理的 Promise rejection 完全不可见 —— 这是 RN 最大的崩溃盲区。**' +
      '解决方式：升级 RN/Hermes 版本，或在原生侧开启 Promise rejection 跟踪。',
  );
  return null;
}
