import type {
  ApmContext,
  ApmPayload,
  CrashLayer,
  NativeBridge,
  StorageAdapter,
  Transport,
} from './types';
import { type Clock, systemClock } from './core/clock';
import { Reporter } from './core/reporter';
import { StartupTracer, PHASE } from './startup/tracer';
import { BreadcrumbStore } from './crash/breadcrumbs';
import { CrashCollector } from './crash/error-tracker';
import {
  installGlobalHandler,
  type InstalledHandler,
  type LayerStatus,
} from './crash/global-handler';
import { MemorySampler } from './memory/sampler';
import { WhiteScreenDetector } from './whitescreen/detector';
import { createBridge, describeNativeGaps, nullBridge, detectPlatformFromRN } from './native/bridge';

export * from './types';
export { RingBuffer } from './core/ring-buffer';
export * from './core/stats';
export { Reporter } from './core/reporter';
export { MemoryStorage, SafeStorage } from './core/storage';
export { FakeClock, systemClock } from './core/clock';
export type { Clock } from './core/clock';
export { StartupTracer, PHASE } from './startup/tracer';
export { BreadcrumbStore } from './crash/breadcrumbs';
export { CrashCollector, fingerprintError, topFrames, normalizeError } from './crash/error-tracker';
export { installGlobalHandler } from './crash/global-handler';
export type { LayerStatus, InstalledHandler, LayerState } from './crash/global-handler';
export { ApmErrorBoundary } from './crash/error-boundary';
export type { ApmErrorBoundaryProps } from './crash/error-boundary';
export { MemorySampler, describeSlope } from './memory/sampler';
export { WhiteScreenDetector } from './whitescreen/detector';
export { createBridge, nullBridge, describeNativeGaps, detectPlatformFromRN } from './native/bridge';
export { toMetrics, serializeMetrics } from './metrics-export';
export type { MetricSample, MetricsFile, MetricDirection, ToMetricsOptions } from './metrics-export';

export interface ApmConfig {
  appVersion: string;
  buildNumber?: string;
  transport: Transport;
  /** 原生模块对象。不传则原生能力全部降级，SDK 仍提供 JS 侧全部价值。 */
  nativeModule?: unknown;
  /** 直接提供 bridge（比 nativeModule 更灵活，便于测试与自建实现） */
  native?: NativeBridge;
  storage?: StorageAdapter;
  clock?: Clock;
  memorySampleIntervalMs?: number;
  whiteScreenTimeoutMs?: number;
  breadcrumbCapacity?: number;
  maxCrashes?: number;
  reporterFlushIntervalMs?: number;
  /** 是否自动重试队列里的历史数据（下次启动补传）。默认 true。 */
  autoRestore?: boolean;
  onError?: (e: unknown) => void;
}

/**
 * APM 客户端 —— 把所有部件接成一个整体。
 *
 * 用法见 README。核心约定：
 *  - `initApm()` 必须在 index.js **最顶部**调用（越早越准）
 *  - 首屏渲染完成后调用 `startup.markFirstScreen()` 与 `reportStartup()`
 *  - 崩溃由已安装的四层机制自动捕获并**立即落盘**
 */
export class ApmClient {
  readonly startup: StartupTracer;
  readonly breadcrumbs: BreadcrumbStore;
  readonly crashes: CrashCollector;
  readonly memory: MemorySampler;
  readonly whiteScreen: WhiteScreenDetector;
  readonly reporter: Reporter;

  private readonly config: ApmConfig;
  private readonly bridge: NativeBridge;
  private readonly clock: Clock;
  private readonly sessionId: string;
  private handler: InstalledHandler | null = null;
  private started = false;

  constructor(config: ApmConfig) {
    this.config = config;
    this.clock = config.clock ?? systemClock;
    this.bridge = config.native ?? (config.nativeModule ? createBridge(config.nativeModule) : nullBridge);
    this.sessionId = makeSessionId(this.clock);

    this.startup = new StartupTracer({ clock: this.clock, native: this.bridge });
    this.breadcrumbs = new BreadcrumbStore({
      capacity: config.breadcrumbCapacity ?? 50,
      clock: this.clock,
    });
    this.reporter = new Reporter({
      transport: config.transport,
      storage: config.storage,
      clock: this.clock,
      flushIntervalMs: config.reporterFlushIntervalMs ?? 10_000,
      onError: config.onError,
    });
    this.crashes = new CrashCollector({
      clock: this.clock,
      breadcrumbs: this.breadcrumbs,
      native: this.bridge,
      maxCrashes: config.maxCrashes ?? 20,
    });
    this.memory = new MemorySampler({
      clock: this.clock,
      native: this.bridge,
      intervalMs: config.memorySampleIntervalMs ?? 10_000,
    });
    this.whiteScreen = new WhiteScreenDetector({
      clock: this.clock,
      defaultTimeoutMs: config.whiteScreenTimeoutMs ?? 2000,
      onWhiteScreen: (r) => {
        this.breadcrumbs.add('whitescreen', `白屏：${r.routeName ?? 'default'}`);
        this.enqueue({ whiteScreen: r });
      },
      onLateRender: (r) => {
        this.breadcrumbs.add('whitescreen', `慢渲染：${r.routeName ?? 'default'} ${r.renderedAfterMs}ms`);
        this.enqueue({ whiteScreen: r });
      },
    });
  }

  /** 启动。幂等。 */
  start(): void {
    if (this.started) return;
    this.started = true;

    // 1) 安装四层捕获中的三层（ErrorBoundary 需在 React 树里挂载）
    this.handler = installGlobalHandler({
      collector: this.crashes,
      breadcrumbs: this.breadcrumbs,
      native: this.bridge,
      onCrash: (record) => {
        // **崩溃必须立即落盘** —— 进程可能马上死掉
        this.enqueue({ crashes: [record] }, { persist: true });
      },
    });


    // 2) 恢复上次未发完的数据（含上次崩溃）
    if (this.config.autoRestore !== false) {
      void this.reporter.restore().then((n) => {
        if (n > 0) this.breadcrumbs.add('reporter', `恢复未上报数据 ${n} 条`);
        void this.reporter.flush();
      });
    }

    // 3) 启动上报与内存采样
    this.reporter.start(this.config.reporterFlushIntervalMs ?? 10_000);
    this.memory.start(this.config.memorySampleIntervalMs ?? 10_000);

    this.breadcrumbs.add('lifecycle', 'APM 已启动');
  }

  stop(): void {
    this.handler?.uninstall();
    this.handler = null;
    this.reporter.stop();
    this.memory.stop();
    this.whiteScreen.cancelAll();
    this.started = false;
  }

  /**
   * 四层捕获的实际状态。
   *
   * **请务必检查它。** 任何一层 `unavailable` 都意味着一类崩溃完全不可见，
   * 此时看到的"崩溃很少"是假的。
   */
  getLayerStatus(): LayerStatus {
    return (
      this.handler?.getLayerStatus() ?? {
        errorBoundary: 'not-installed',
        jsGlobal: 'not-installed',
        promise: 'not-installed',
        native: 'not-installed',
        notes: {},
      }
    );
  }

  /** 由 <ApmErrorBoundary onMount> 调用。 */
  markErrorBoundaryMounted(): void {
    this.handler?.markErrorBoundaryMounted();
  }

  /** 由 <ApmErrorBoundary onError> 调用。 */
  reportErrorBoundaryError(error: Error, componentStack: string): void {
    this.captureCrash('errorBoundary', error, { isFatal: false, componentStack });
  }

  /** 原生能力缺口清单，用于指导补齐。 */
  getNativeGaps(): string[] {
    return describeNativeGaps(this.bridge);
  }

  // ---------------- App 生命周期 ----------------

  /** 切后台时调用：**必须停内存采样**（后台采样无意义、耗电、且污染判断）。 */
  onBackground(): void {
    this.memory.stop();
    this.breadcrumbs.add('lifecycle', 'app 进入后台');
    // 后台是补传数据的好时机
    void this.reporter.flush();
  }

  onForeground(): void {
    this.memory.start(this.config.memorySampleIntervalMs ?? 10_000);
    this.breadcrumbs.add('lifecycle', 'app 回到前台');
  }

  // ---------------- 上报 ----------------

  /** 组装上下文。 */
  getContext(): ApmContext {
    let device: { osVersion?: string; deviceModel?: string; platform?: string } = {};
    try {
      device = this.bridge.getDeviceInfo?.() ?? {};
    } catch {
      /* 忽略 */
    }
    const ctx: ApmContext = {
      appVersion: this.config.appVersion,
      platform: (device.platform as ApmContext['platform']) ?? detectPlatformFromRN(),
      sessionId: this.sessionId,
      launchType: this.startup.getRecord()?.launchType ?? 'unknown',
    };
    if (this.config.buildNumber) ctx.buildNumber = this.config.buildNumber;
    if (device.osVersion) ctx.osVersion = device.osVersion;
    if (device.deviceModel) ctx.deviceModel = device.deviceModel;
    return ctx;
  }

  /** 上报启动数据。**应在 markFirstScreen() 之后调用。** */
  reportStartup(): boolean {
    const record = this.startup.getRecord();
    if (!record) return false; // 未标首屏就不报 —— 宁可不报，不报不完整的
    this.enqueue({ startup: record });
    return true;
  }

  /** 上报内存水位。 */
  reportMemory(): boolean {
    const report = this.memory.getReport();
    if (!report) return false;
    this.enqueue({ memory: report });
    return true;
  }

  /**
   * 记录并**立即**上报一条崩溃。
   *
   * 为什么不"先放进 collector 缓冲、之后再统一上报"：
   *  1. 崩溃后进程可能马上死掉，**缓冲里的数据会一起消失** —— 而它正是最需要的那条；
   *  2. 崩溃风暴时缓冲会被覆盖，早期崩溃会丢。
   *
   * **所有崩溃路径都必须走这里**（全局 handler、ErrorBoundary、手动上报）。
   * collector 仍然记录一份，用于诊断（peek / 崩溃风暴计数）。
   */
  private captureCrash(
    layer: CrashLayer,
    error: unknown,
    opts: { isFatal?: boolean; componentStack?: string } = {},
  ): void {
    const record = this.crashes.record(layer, error, opts);
    this.enqueue({ crashes: [record] }, { persist: true });
  }

  flush(): Promise<void> {
    return this.reporter.flush();
  }

  private enqueue(
    partial: Partial<Pick<ApmPayload, 'startup' | 'crashes' | 'memory' | 'whiteScreen'>>,
    opts: { persist?: boolean } = {},
  ): void {
    this.reporter.enqueue({ context: this.getContext(), sentAt: this.clock.wall(), ...partial }, opts);
  }
}

function makeSessionId(clock: Clock): string {
  const rnd = Math.random().toString(36).slice(2, 10);
  return `${clock.wall().toString(36)}-${rnd}`;
}

/** 便利函数：创建并启动。 */
export function initApm(config: ApmConfig): ApmClient {
  const client = new ApmClient(config);
  client.start();
  return client;
}

// 让使用者方便引到阶段常量
export { PHASE as StartupPhases };
