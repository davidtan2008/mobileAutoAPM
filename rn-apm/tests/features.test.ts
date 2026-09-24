import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { FakeClock } from '../src/core/clock';
import { MemoryStorage } from '../src/core/storage';
import { StartupTracer, PHASE } from '../src/startup/tracer';
import { BreadcrumbStore } from '../src/crash/breadcrumbs';
import { CrashCollector, fingerprintError, topFrames, normalizeError } from '../src/crash/error-tracker';
import { installGlobalHandler } from '../src/crash/global-handler';
import { MemorySampler, describeSlope } from '../src/memory/sampler';
import { WhiteScreenDetector } from '../src/whitescreen/detector';
import { createBridge, describeNativeGaps } from '../src/native/bridge';
import { ApmClient } from '../src/index';
import type { ApmPayload, NativeBridge, Transport } from '../src/types';

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

// ---------------- StartupTracer ----------------

describe('StartupTracer', () => {
  test('无原生进程时间时如实标注 hasProcessStart=false', () => {
    const clock = new FakeClock();
    const t = new StartupTracer({ clock });
    t.mark(PHASE.JS_START);
    clock.advance(100);
    t.mark(PHASE.ROOT_MOUNTED);
    clock.advance(200);
    t.markFirstScreen();

    const rec = t.getRecord();
    assert.ok(rec);
    assert.equal(rec.hasProcessStart, false, '拿不到进程创建时间必须如实标注');
    assert.equal(rec.totalMs, 300, '退化为相对首个打点的耗时');
    assert.equal(rec.launchType, 'unknown', '拿不准时不许猜');
    assert.deepEqual(
      rec.phases.map((p) => p.name),
      [PHASE.JS_START, PHASE.ROOT_MOUNTED, PHASE.FIRST_SCREEN],
    );
  });

  test('有原生进程时间时以进程创建为原点，并推断冷启动', () => {
    const clock = new FakeClock();
    const processStart = clock.wall() - 800; // 800ms 前进程创建
    const native: NativeBridge = { getProcessStartTime: () => processStart };

    const t = new StartupTracer({ clock, native });
    t.mark(PHASE.JS_START);
    clock.advance(1200);
    t.markFirstScreen();

    const rec = t.getRecord();
    assert.ok(rec);
    assert.equal(rec.hasProcessStart, true);
    assert.equal(rec.launchType, 'cold', '800ms < 3000ms 窗口，应判为冷启动');
    assert.equal(rec.totalMs, 2000, '进程创建到首屏 = 800 + 1200');
    assert.equal(rec.phases[0]?.sinceProcessStartMs, 800);
  });

  test('进程存活已久时判为温启动（而非 cold）', () => {
    const clock = new FakeClock();
    const native: NativeBridge = { getProcessStartTime: () => clock.wall() - 60_000 };
    const t = new StartupTracer({ clock, native });
    t.mark(PHASE.JS_START);
    t.markFirstScreen();
    assert.equal(t.getRecord()?.launchType, 'warm');
  });

  test('显式指定优先于推测', () => {
    const clock = new FakeClock();
    const native: NativeBridge = { getProcessStartTime: () => clock.wall() - 60_000 };
    const t = new StartupTracer({ clock, native });
    t.setLaunchType('cold');
    t.mark(PHASE.JS_START);
    t.markFirstScreen();
    assert.equal(t.getRecord()?.launchType, 'cold');
  });

  test('未标首屏时拒绝出报告（宁可不报，不报不完整的）', () => {
    const clock = new FakeClock();
    const t = new StartupTracer({ clock });
    t.mark(PHASE.JS_START);
    assert.equal(t.getRecord(), null);
    assert.equal(t.isFirstScreenMarked, false);
  });

  test('重复同名打点被忽略（防 hot reload 污染）', () => {
    const clock = new FakeClock();
    const t = new StartupTracer({ clock });
    t.mark(PHASE.JS_START);
    clock.advance(500);
    t.mark(PHASE.JS_START); // 应被忽略
    t.markFirstScreen();
    const rec = t.getRecord();
    assert.equal(rec?.phases.length, 2);
  });

  test('原生 getProcessStartTime 抛错不影响启动', () => {
    const clock = new FakeClock();
    const native: NativeBridge = {
      getProcessStartTime: () => {
        throw new Error('native module gone');
      },
    };
    assert.doesNotThrow(() => new StartupTracer({ clock, native }));
    const t = new StartupTracer({ clock, native });
    t.mark(PHASE.JS_START);
    t.markFirstScreen();
    assert.equal(t.getRecord()?.hasProcessStart, false, '取不到就降级，不能崩');
  });
});

// ---------------- Breadcrumbs ----------------

describe('BreadcrumbStore', () => {
  test('有界：超出容量后丢弃最旧', () => {
    const clock = new FakeClock();
    const b = new BreadcrumbStore({ capacity: 3, clock });
    for (const m of ['a', 'b', 'c', 'd', 'e']) b.add('ui', m);
    assert.equal(b.size, 3);
    assert.equal(b.dropped, 2);
    assert.deepEqual(
      b.snapshot().map((x) => x.message),
      ['c', 'd', 'e'],
    );
  });

  test('大对象只记类型，不把对象图拖进内存', () => {
    const b = new BreadcrumbStore({ capacity: 5 });
    const big = { data: new Array(100000).fill(1), nested: { deep: { deeper: 1 } } };
    b.add('data', 'payload', { big, arr: [1, 2, 3], n: 42, s: 'ok' });
    const crumb = b.snapshot()[0];
    assert.ok(crumb);
    assert.equal(crumb.data?.big, '[object]', '对象只记类型');
    assert.equal(crumb.data?.arr, '[Array(3)]', '数组只记长度');
    assert.equal(crumb.data?.n, 42);
    assert.equal(crumb.data?.s, 'ok');
  });

  test('超长字符串被截断', () => {
    const b = new BreadcrumbStore({ capacity: 2 });
    b.add('log', 'x'.repeat(500));
    assert.ok((b.snapshot()[0]?.message.length ?? 0) <= 201);
  });

  test('add 永不抛错', () => {
    const b = new BreadcrumbStore({ capacity: 2 });
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    assert.doesNotThrow(() => b.add('x', 'y', circular));
  });
});

// ---------------- 错误归一化与指纹 ----------------

describe('error-tracker', () => {
  test('topFrames 解析堆栈并去掉行号列号（让同一 bug 跨构建归为一类）', () => {
    const stack = [
      'Error: boom',
      '    at doThing (/app/src/a.ts:12:34)',
      '    at other (/app/src/b.ts:99:1)',
      '    at /app/src/c.ts:5:6',
    ].join('\n');
    const frames = topFrames(stack, 3);
    assert.deepEqual(frames, ['doThing@a.ts', 'other@b.ts', '<anonymous>@c.ts']);
  });

  test('同一错误在不同行号下指纹一致；不同错误指纹不同', () => {
    const s1 = 'Error: boom\n    at f (/a.ts:10:1)\n    at g (/b.ts:2:3)';
    const s2 = 'Error: boom\n    at f (/a.ts:99:88)\n    at g (/b.ts:77:66)';
    const s3 = 'Error: different\n    at f (/a.ts:10:1)\n    at g (/b.ts:2:3)';
    assert.equal(fingerprintError('boom', s1), fingerprintError('boom', s2), '同行号应聚为一类');
    assert.notEqual(fingerprintError('boom', s1), fingerprintError('different', s3));
  });

  test('normalizeError 处理各种抛出物，且永不抛错', () => {
    assert.equal(normalizeError(new Error('e1')).message, 'e1');
    assert.equal(normalizeError('plain string').message, 'plain string');
    assert.equal(normalizeError(null).message, 'null thrown');
    assert.equal(normalizeError(undefined).message, 'undefined thrown');
    assert.equal(normalizeError({ message: 'obj msg' }).message, 'obj msg');
    assert.equal(normalizeError(42).message, '42');

    const circular: Record<string, unknown> = { a: 1 };
    circular.self = circular;
    assert.doesNotThrow(() => normalizeError(circular));
    assert.ok(normalizeError(circular).message.includes('Circular'));
  });

  test('CrashCollector 有界，崩溃风暴不会撑爆内存', () => {
    const clock = new FakeClock();
    const c = new CrashCollector({ clock, maxCrashes: 3 });
    for (let i = 0; i < 50; i++) c.record('jsGlobal', new Error(`e${i}`));
    assert.equal(c.count, 3, '只保留最近 3 条');
    assert.equal(c.totalRecordedCount, 50, '但累计数应如实记录');
    assert.equal(c.droppedCount, 47);
  });

  test('崩溃记录包含轨迹与内存快照（FOOM 归因的关键证据）', () => {
    const clock = new FakeClock();
    const breadcrumbs = new BreadcrumbStore({ clock });
    breadcrumbs.add('nav', '进入首页');
    const native: NativeBridge = { getMemoryUsage: () => ({ usedBytes: 300 * 1024 * 1024 }) };
    const c = new CrashCollector({ clock, breadcrumbs, native });

    const rec = c.record('jsGlobal', new Error('oom-ish'));
    assert.equal(rec.breadcrumbs.length, 1);
    assert.equal(rec.memoryAtCrash?.usedBytes, 300 * 1024 * 1024);
  });

  test('取内存快照失败不影响崩溃记录', () => {
    const native: NativeBridge = {
      getMemoryUsage: () => {
        throw new Error('nope');
      },
    };
    const c = new CrashCollector({ clock: new FakeClock(), native });
    const rec = c.record('jsGlobal', new Error('x'));
    assert.equal(rec.memoryAtCrash, undefined);
    assert.equal(rec.message, 'x');
  });
});

// ---------------- 全局 handler（最关键的正确性测试） ----------------

describe('installGlobalHandler', () => {
  const g = globalThis as unknown as {
    ErrorUtils?: {
      getGlobalHandler?: () => ((e: unknown, isFatal?: boolean) => void) | undefined;
      setGlobalHandler?: (h: (e: unknown, isFatal?: boolean) => void) => void;
    };
  };

  beforeEach(() => {
    g.ErrorUtils = undefined;
  });

  afterEach(() => {
    g.ErrorUtils = undefined;
  });

  test('**链式调用：不打断已存在的 handler**（否则会破坏其他 SDK）', () => {
    const calls: string[] = [];
    let installed: ((e: unknown, isFatal?: boolean) => void) | undefined;
    const eu = {
      getGlobalHandler: (): ((e: unknown, isFatal?: boolean) => void) | undefined => installed,
      setGlobalHandler: (h: (e: unknown, isFatal?: boolean) => void): void => {
        installed = h;
      },
    };
    g.ErrorUtils = eu;
    // 模拟 Sentry 先装了它的 handler
    eu.setGlobalHandler((e) => calls.push(`sentry:${String(e)}`));

    const collector = new CrashCollector({ clock: new FakeClock() });
    const h = installGlobalHandler({ collector });
    // 现在触发崩溃
    installed?.(new Error('boom'), true);

    assert.deepEqual(calls, ['sentry:Error: boom'], '**前一个 handler 必须仍被调用**');
    assert.equal(collector.count, 1, '我们也应记录到');
    h.uninstall();
  });

  test('uninstall 后恢复前一个 handler', () => {
    const calls: string[] = [];
    const original = (e: unknown): void => void calls.push(`orig:${String(e)}`);
    let installed: ((e: unknown, isFatal?: boolean) => void) | undefined = original;
    g.ErrorUtils = {
      getGlobalHandler: () => installed,
      setGlobalHandler: (h) => {
        installed = h;
      },
    };

    const h = installGlobalHandler({ collector: new CrashCollector({ clock: new FakeClock() }) });
    assert.notEqual(installed, original, '安装后应换成我们的');
    h.uninstall();
    assert.equal(installed, original, '卸载后应恢复');
  });

  test('前一个 handler 抛错不影响我们记录', () => {
    let installed: ((e: unknown, isFatal?: boolean) => void) | undefined;
    const eu = {
      getGlobalHandler: (): ((e: unknown, isFatal?: boolean) => void) | undefined => installed,
      setGlobalHandler: (h: (e: unknown, isFatal?: boolean) => void): void => {
        installed = h;
      },
    };
    g.ErrorUtils = eu;
    eu.setGlobalHandler(() => {
      throw new Error('别人的 handler 炸了');
    });

    const collector = new CrashCollector({ clock: new FakeClock() });
    const h = installGlobalHandler({ collector });
    assert.doesNotThrow(() => installed?.(new Error('boom'), true));
    assert.equal(collector.count, 1);
    h.uninstall();
  });

  test('**如实报告缺失的层**（不能假装都接好了）', () => {
    // 没有 ErrorUtils、没有 Hermes tracker、没有 addEventListener
    const collector = new CrashCollector({ clock: new FakeClock() });
    const h = installGlobalHandler({ collector });
    const status = h.getLayerStatus();

    assert.equal(status.jsGlobal, 'unavailable', '没有 ErrorUtils 必须报 unavailable');
    assert.equal(status.errorBoundary, 'not-installed');
    assert.ok(status.notes.jsGlobal, '必须给出原因');
    assert.ok(status.notes.native, '未接原生崩溃库必须提示');
    assert.ok(status.notes.errorBoundary, '未挂载 ErrorBoundary 必须提示');
    h.uninstall();
  });

  test('markErrorBoundaryMounted 后状态变为 active', () => {
    const collector = new CrashCollector({ clock: new FakeClock() });
    const h = installGlobalHandler({ collector });
    h.markErrorBoundaryMounted();
    const status = h.getLayerStatus();
    assert.equal(status.errorBoundary, 'active');
    assert.equal(status.notes.errorBoundary, undefined);
    h.uninstall();
  });

  test('接入原生桥后 native 层报 active', () => {
    const native: NativeBridge = { reportJsError: () => {} };
    const collector = new CrashCollector({ clock: new FakeClock(), native });
    const h = installGlobalHandler({ collector, native });
    assert.equal(h.getLayerStatus().native, 'active');
    h.uninstall();
  });
});

// ---------------- MemorySampler ----------------

describe('MemorySampler', () => {
  test('采样有界且能算出斜率', () => {
    const clock = new FakeClock();
    let mem = 100 * 1024 * 1024;
    const native: NativeBridge = { getMemoryUsage: () => ({ usedBytes: mem }) };
    const s = new MemorySampler({ clock, native, capacity: 10 });

    for (let i = 0; i < 20; i++) {
      s.sampleOnce();
      mem += 1024 * 1024; // 每次 +1MB
      clock.advance(1000);
    }
    const rep = s.getReport();
    assert.ok(rep);
    assert.equal(rep.samples.length, 10, '样本数受环形缓冲限制');
    assert.equal(rep.droppedSamples, 10);
    assert.ok(rep.slopeBytesPerSec > 0, '内存递增 → 斜率应为正');

    const d = describeSlope(rep.slopeBytesPerSec);
    assert.notEqual(d.level, 'flat', '持续增长不应判为平稳');
  });

  test('平稳内存斜率接近 0', () => {
    const clock = new FakeClock();
    const native: NativeBridge = { getMemoryUsage: () => ({ usedBytes: 100 * 1024 * 1024 }) };
    const s = new MemorySampler({ clock, native, capacity: 20 });
    for (let i = 0; i < 10; i++) {
      s.sampleOnce();
      clock.advance(1000);
    }
    assert.ok(Math.abs(s.getReport()?.slopeBytesPerSec ?? 0) < 1);
  });

  test('无原生能力时采样失败被计数，不抛错', () => {
    const s = new MemorySampler({ clock: new FakeClock() });
    assert.equal(s.sampleOnce(), false);
    assert.equal(s.failedCount, 1);
    assert.equal(s.getReport(), null);
  });

  test('start/stop 幂等，且 stop 后不再采样', async () => {
    const clock = new FakeClock();
    const native: NativeBridge = { getMemoryUsage: () => ({ usedBytes: 1 }) };
    const s = new MemorySampler({ clock, native, intervalMs: 5 });
    s.start();
    s.start(); // 幂等
    assert.equal(s.isRunning, true);
    const n = s.sampleCount;
    await sleep(20);
    assert.ok(s.sampleCount > n, '应在持续采样');
    s.stop();
    const afterStop = s.sampleCount;
    await sleep(20);
    assert.equal(s.sampleCount, afterStop, 'stop 后不应再采样');
  });
});

// ---------------- WhiteScreenDetector ----------------

describe('WhiteScreenDetector', () => {
  test('超时未渲染 → 判为白屏', async () => {
    const hits: unknown[] = [];
    const d = new WhiteScreenDetector({ defaultTimeoutMs: 20, onWhiteScreen: (r) => hits.push(r) });
    d.startPage('Home');
    await sleep(50);
    assert.equal(hits.length, 1);
    const rec = hits[0] as { eventuallyRendered: boolean; routeName: string };
    assert.equal(rec.eventuallyRendered, false);
    assert.equal(rec.routeName, 'Home');
    d.cancelAll();
  });

  test('**超时后最终渲染出来 → 判为「慢」，不是白屏**', async () => {
    const whites: unknown[] = [];
    const lates: Array<{ renderedAfterMs?: number }> = [];
    const d = new WhiteScreenDetector({
      defaultTimeoutMs: 20,
      onWhiteScreen: (r) => whites.push(r),
      onLateRender: (r) => lates.push(r),
    });
    d.startPage('Slow');
    await sleep(40); // 超时先触发
    d.markRendered('Slow');
    assert.equal(whites.length, 1, '超时瞬间确实未渲染');
    assert.equal(lates.length, 1, '后续渲染应单独上报为「慢」');
    assert.ok((lates[0]?.renderedAfterMs ?? 0) >= 40);
    d.cancelAll();
  });

  test('及时渲染不触发任何告警', async () => {
    const hits: unknown[] = [];
    const d = new WhiteScreenDetector({ defaultTimeoutMs: 50, onWhiteScreen: (r) => hits.push(r) });
    d.startPage('Fast');
    d.markRendered('Fast');
    await sleep(70);
    assert.equal(hits.length, 0);
    assert.equal(d.pendingCount, 0, '渲染后应清理会话');
  });

  test('cancel 清理定时器，不泄漏', async () => {
    const hits: unknown[] = [];
    const d = new WhiteScreenDetector({ defaultTimeoutMs: 20, onWhiteScreen: (r) => hits.push(r) });
    d.startPage('A');
    d.cancel('A');
    await sleep(40);
    assert.equal(hits.length, 0);
    assert.equal(d.pendingCount, 0);
  });
});

// ---------------- createBridge ----------------

describe('createBridge', () => {
  test('兼容方法式与常量式原生模块', () => {
    const viaMethod = createBridge({
      getProcessStartTime: () => 12345,
      getMemoryUsage: () => ({ usedBytes: 999 }),
    });
    assert.equal(viaMethod.getProcessStartTime?.(), 12345);
    assert.equal(viaMethod.getMemoryUsage?.()?.usedBytes, 999);

    const viaConstants = createBridge({
      getConstants: () => ({ processStartTime: 777, memoryUsage: { usedBytes: 888 } }),
    });
    assert.equal(viaConstants.getProcessStartTime?.(), 777);
    assert.equal(viaConstants.getMemoryUsage?.()?.usedBytes, 888);
  });

  test('原生方法抛错时降级为 null，不传播', () => {
    const b = createBridge({
      getMemoryUsage: () => {
        throw new Error('native gone');
      },
    });
    assert.equal(b.getMemoryUsage?.(), null);
  });

  test('返回非法的内存值被拒绝', () => {
    const b = createBridge({ getMemoryUsage: () => ({ usedBytes: NaN }) });
    assert.equal(b.getMemoryUsage?.(), null);
  });

  test('describeNativeGaps 指出缺口并给出实现路径', () => {
    const gaps = describeNativeGaps({});
    assert.equal(gaps.length, 3);
    assert.ok(gaps.some((g) => g.includes('sysctl')));
    assert.ok(gaps.some((g) => g.includes('FOOM')));
  });
});

// ---------------- ApmClient 集成 ----------------

class CollectTransport implements Transport {
  payloads: ApmPayload[] = [];
  async send(p: ApmPayload): Promise<void> {
    this.payloads.push(p);
  }
}

describe('ApmClient 集成', () => {
  test('未标首屏时不上报启动数据', () => {
    const t = new CollectTransport();
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: t,
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
    });
    assert.equal(client.reportStartup(), false);
    assert.equal(client.reporter.queueSize, 0);
  });

  test('标首屏后上报启动数据，且带完整上下文', async () => {
    const t = new CollectTransport();
    const clock = new FakeClock();
    const client = new ApmClient({
      appVersion: '2.3.4',
      buildNumber: '567',
      transport: t,
      clock,
      memorySampleIntervalMs: 0,
      reporterFlushIntervalMs: 0,
      native: { getDeviceInfo: () => ({ platform: 'harmony', osVersion: '5.0', deviceModel: 'Mate 60' }) },
      autoRestore: false,
    });
    client.startup.mark(PHASE.JS_START);
    clock.advance(500);
    client.startup.markFirstScreen();
    assert.equal(client.reportStartup(), true);
    await client.flush();

    const p = t.payloads[0];
    assert.ok(p);
    assert.equal(p.context.appVersion, '2.3.4');
    assert.equal(p.context.buildNumber, '567');
    assert.equal(p.context.platform, 'harmony', '鸿蒙平台应被正确传递');
    assert.equal(p.startup?.totalMs, 500);
    client.stop();
  });

  test('ErrorBoundary 崩溃立即入队**并落盘**（进程可能马上死掉）', async () => {
    const storage = new MemoryStorage();
    const t = new CollectTransport();
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: t,
      storage,
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
      autoRestore: false,
    });
    client.reportErrorBoundaryError(new Error('render failed'), 'in <Home>');

    assert.equal(client.reporter.queueSize, 1, '崩溃应立刻入队');
    assert.equal(client.crashes.count, 1, '同时记入 collector 供诊断');

    // 关键：必须已经落盘 —— 否则进程崩溃时这条数据会一起消失
    await sleep(10);
    const raw = await storage.getItem('rn-apm:queue:v1');
    assert.ok(raw, '崩溃数据必须已写入存储');
    const parsed = JSON.parse(raw as string) as ApmPayload[];
    assert.equal(parsed.length, 1);
    assert.equal(parsed[0]?.crashes?.[0]?.layer, 'errorBoundary');
  });

  test('崩溃记录带组件栈（全局 handler 拿不到，只有 ErrorBoundary 能提供）', () => {
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: new CollectTransport(),
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
      autoRestore: false,
    });
    client.reportErrorBoundaryError(new Error('boom'), 'in <Home> (at App.tsx:10)');
    const rec = client.crashes.peek()[0];
    assert.equal(rec?.componentStack, 'in <Home> (at App.tsx:10)');
    assert.equal(rec?.isFatal, false, '渲染错误被 ErrorBoundary 兜住，不是致命崩溃');
  });

  test('四层状态可查，缺失层有说明', () => {
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: new CollectTransport(),
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
      autoRestore: false,
    });
    client.start();
    const s = client.getLayerStatus();
    assert.ok(['active', 'unavailable', 'not-installed'].includes(s.promise));
    assert.ok(s.notes.native, '未接原生崩溃库必须有提示');
    client.stop();
  });

  test('后台停采样、前台恢复', () => {
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: new CollectTransport(),
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
      memorySampleIntervalMs: 5,
      autoRestore: false,
      native: { getMemoryUsage: () => ({ usedBytes: 1 }) },
    });
    client.start();
    assert.equal(client.memory.isRunning, true);
    client.onBackground();
    assert.equal(client.memory.isRunning, false, '后台必须停采样');
    client.onForeground();
    assert.equal(client.memory.isRunning, true);
    client.stop();
  });

  test('无原生能力时 SDK 仍可工作（不崩、不阻塞）', () => {
    const t = new CollectTransport();
    const client = new ApmClient({
      appVersion: '1.0.0',
      transport: t,
      clock: new FakeClock(),
      reporterFlushIntervalMs: 0,
      autoRestore: false,
    });
    assert.doesNotThrow(() => client.start());
    client.breadcrumbs.add('ui', 'tap');
    client.startup.mark(PHASE.JS_START);
    client.startup.markFirstScreen();
    assert.equal(client.reportStartup(), true);
    assert.equal(client.getNativeGaps().length, 3, '应如实报告 3 项原生能力缺口');
    client.stop();
  });
});
