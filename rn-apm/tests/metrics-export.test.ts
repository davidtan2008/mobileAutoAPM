import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { toMetrics } from '../src/metrics-export';
import type { ApmContext, ApmPayload } from '../src/types';

const ctx = (over: Partial<ApmContext> = {}): ApmContext => ({
  appVersion: '1.0.0',
  platform: 'ios',
  sessionId: 's',
  launchType: 'cold',
  ...over,
});

function startupPayload(totalMs: number, launchType: ApmContext['launchType'] = 'cold'): ApmPayload {
  return {
    context: ctx({ launchType }),
    sentAt: 0,
    startup: {
      launchType,
      totalMs,
      hasProcessStart: true,
      phases: [
        { name: 'jsStart', sinceProcessStartMs: 100, deltaMs: 100 },
        { name: 'firstScreen', sinceProcessStartMs: totalMs, deltaMs: totalMs - 100 },
      ],
    },
  };
}

describe('toMetrics', () => {
  test('产出与 apm_baseline.py 完全一致的格式', () => {
    const file = toMetrics([startupPayload(1000), startupPayload(1100), startupPayload(1050)]);
    const m = file.metrics.find((x) => x.name === 'startup.cold.total');
    assert.ok(m, '应有 startup.cold.total');
    assert.equal(m.unit, 'ms');
    assert.equal(m.direction, 'lower_is_better');
    assert.deepEqual(m.samples, [1000, 1100, 1050]);
  });

  test('冷/热启动分开统计 —— 混比会让结论失真', () => {
    const payloads = [
      startupPayload(1000, 'cold'),
      startupPayload(2000, 'cold'),
      startupPayload(200, 'hot'),
      startupPayload(250, 'hot'),
    ];
    const file = toMetrics(payloads);
    const cold = file.metrics.find((x) => x.name === 'startup.cold.total');
    const hot = file.metrics.find((x) => x.name === 'startup.hot.total');
    assert.deepEqual(cold?.samples, [1000, 2000], '冷启动不应混入热启动样本');
    assert.deepEqual(hot?.samples, [200, 250]);

    // 汇总指标只统计冷启动（线上主指标）
    const total = file.metrics.find((x) => x.name === 'startup.total');
    assert.deepEqual(total?.samples, [1000, 2000], 'startup.total 只应含冷启动');
  });

  test('无进程创建时间时不计入分阶段指标（口径不可信的样本不污染基线）', () => {
    const bad: ApmPayload = {
      context: ctx(),
      sentAt: 0,
      startup: {
        launchType: 'cold',
        totalMs: 999,
        hasProcessStart: false, // 口径不可信
        phases: [{ name: 'jsStart', sinceProcessStartMs: 0, deltaMs: 0 }],
      },
    };
    const file = toMetrics([bad]);
    assert.equal(
      file.metrics.find((x) => x.name.startsWith('startup.phase.')),
      undefined,
      '口径不可信的样本不应产生分阶段指标',
    );
    // 但总耗时仍可用（相对值），只是不能与线上比
    assert.ok(file.metrics.find((x) => x.name === 'startup.cold.total'));
  });

  test('慢渲染与白屏分开统计（两者根因与修法完全不同）', () => {
    const payloads: ApmPayload[] = [
      {
        context: ctx(),
        sentAt: 0,
        whiteScreen: { routeName: 'A', timeoutMs: 2000, eventuallyRendered: true, renderedAfterMs: 5000, occurredAt: 0 },
      },
      {
        context: ctx(),
        sentAt: 0,
        whiteScreen: { routeName: 'B', timeoutMs: 2000, eventuallyRendered: false, occurredAt: 0 },
      },
    ];
    const file = toMetrics(payloads);
    assert.deepEqual(file.metrics.find((x) => x.name === 'whitescreen.late_render')?.samples, [5000]);
    assert.deepEqual(file.metrics.find((x) => x.name === 'whitescreen.count')?.samples, [1]);
  });

  test('内存指标：峰值与斜率分开', () => {
    const file = toMetrics([
      {
        context: ctx(),
        sentAt: 0,
        memory: {
          samples: [],
          peakBytes: 300,
          slopeBytesPerSec: 12.5,
          sampleCount: 5,
          droppedSamples: 0,
        },
      },
    ]);
    assert.equal(file.metrics.find((x) => x.name === 'memory.peak')?.unit, 'bytes');
    assert.equal(file.metrics.find((x) => x.name === 'memory.slope')?.unit, 'bytes_per_sec');
  });

  test('上下文混样本时给出警告（防止拿不可比的数据做对比）', () => {
    const file = toMetrics([
      startupPayload(1000),
      { ...startupPayload(1000), context: ctx({ platform: 'android' }) },
    ]);
    const warnings = file.context.warnings as string[];
    assert.ok(Array.isArray(warnings), '混平台必须产生警告');
    assert.ok(warnings.some((w) => w.includes('平台')));
    assert.equal(file.context.sampleCount, 2);
  });

  test('单一样本时上下文为标量而非数组', () => {
    const file = toMetrics([startupPayload(1000)]);
    assert.equal(file.context.platform, 'ios');
    assert.equal(file.context.appVersion, '1.0.0');
    assert.equal(file.context.warnings, undefined);
  });

  test('NaN / Infinity 被丢弃，不污染样本', () => {
    const file = toMetrics([startupPayload(NaN), startupPayload(1000), startupPayload(Infinity)]);
    const m = file.metrics.find((x) => x.name === 'startup.cold.total');
    assert.deepEqual(m?.samples, [1000]);
  });

  test('minSamples 可过滤掉样本过少的指标', () => {
    const file = toMetrics([startupPayload(1000), startupPayload(1100)], { minSamples: 3 });
    assert.equal(file.metrics.length, 0, '样本不足 3 的指标应被过滤');
  });

  test('崩溃按致命/非致命分开计数', () => {
    const mk = (isFatal: boolean): ApmPayload => ({
      context: ctx(),
      sentAt: 0,
      crashes: [
        {
          id: 'x',
          layer: 'jsGlobal',
          message: 'm',
          fingerprint: 'f',
          isFatal,
          occurredAt: 0,
          breadcrumbs: [],
        },
      ],
    });
    const file = toMetrics([mk(true), mk(true), mk(false)]);
    assert.equal(file.metrics.find((x) => x.name === 'crash.fatal.count')?.samples.length, 2);
    assert.equal(file.metrics.find((x) => x.name === 'crash.nonfatal.count')?.samples.length, 1);
  });
});
