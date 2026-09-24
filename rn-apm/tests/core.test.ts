import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { RingBuffer } from '../src/core/ring-buffer';
import { percentile, median, mean, stdev, cv, slope, isNoisy } from '../src/core/stats';
import { MemoryStorage } from '../src/core/storage';
import { Reporter } from '../src/core/reporter';
import { FakeClock } from '../src/core/clock';
import type { ApmPayload, Transport } from '../src/types';

// ---------------- RingBuffer ----------------

describe('RingBuffer', () => {
  test('按时间顺序导出（最旧 → 最新）', () => {
    const rb = new RingBuffer<number>(5);
    rb.push(1);
    rb.push(2);
    rb.push(3);
    assert.deepEqual(rb.toArray(), [1, 2, 3]);
    assert.equal(rb.size, 3);
    assert.equal(rb.dropped, 0);
    assert.equal(rb.isFull, false);
  });

  test('写满后覆盖最旧元素并计入 dropped', () => {
    const rb = new RingBuffer<number>(3);
    for (const n of [1, 2, 3, 4, 5]) rb.push(n);
    assert.deepEqual(rb.toArray(), [3, 4, 5], '应保留最新的 3 个且顺序正确');
    assert.equal(rb.size, 3);
    assert.equal(rb.dropped, 2);
    assert.equal(rb.isFull, true);
  });

  test('环形回绕后顺序仍然正确（这是最容易写错的地方）', () => {
    const rb = new RingBuffer<number>(4);
    // 写入 10 个，容量 4 → 保留 7,8,9,10
    for (let i = 1; i <= 10; i++) rb.push(i);
    assert.deepEqual(rb.toArray(), [7, 8, 9, 10]);
  });

  test('last(n) 取最新 n 个', () => {
    const rb = new RingBuffer<number>(10);
    for (const n of [1, 2, 3, 4, 5]) rb.push(n);
    assert.deepEqual(rb.last(2), [4, 5]);
    assert.deepEqual(rb.last(99), [1, 2, 3, 4, 5]);
  });

  test('clear 后可从零开始，且底层数组不重建', () => {
    const rb = new RingBuffer<number>(3);
    for (const n of [1, 2, 3, 4]) rb.push(n);
    rb.clear();
    assert.deepEqual(rb.toArray(), []);
    assert.equal(rb.size, 0);
    assert.equal(rb.dropped, 0);
    rb.push(9);
    assert.deepEqual(rb.toArray(), [9]);
  });

  test('容量必须为正整数', () => {
    assert.throws(() => new RingBuffer<number>(0), RangeError);
    assert.throws(() => new RingBuffer<number>(-1), RangeError);
    assert.throws(() => new RingBuffer<number>(1.5), RangeError);
  });

  test('长时间运行不增长（内存工具自身绝不许泄漏）', () => {
    const cap = 50;
    const rb = new RingBuffer<{ v: number }>(cap);
    for (let i = 0; i < 100_000; i++) rb.push({ v: i });
    assert.equal(rb.size, cap, '元素个数必须恒定在容量上');
    assert.equal(rb.toArray().length, cap);
    assert.equal(rb.dropped, 100_000 - cap);
  });
});

// ---------------- stats ----------------

describe('stats', () => {
  test('percentile 线性插值', () => {
    const xs = [1, 2, 3, 4, 5];
    assert.equal(percentile(xs, 50), 3);
    assert.equal(percentile(xs, 0), 1);
    assert.equal(percentile(xs, 100), 5);
    assert.equal(percentile(xs, 25), 2);
  });

  test('空数组返回 NaN，单元素返回自身', () => {
    assert.ok(Number.isNaN(percentile([], 50)));
    assert.equal(percentile([7], 90), 7);
  });

  test('median/mean/stdev 基本正确', () => {
    assert.equal(median([3, 1, 2]), 2);
    assert.equal(mean([1, 2, 3, 4]), 2.5);
    assert.equal(stdev([2, 2, 2]), 0);
    // 样本标准差（n-1）：[2,4,4,4,5,5,7,9] 的样本 stdev = 2.138...
    assert.ok(Math.abs(stdev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.1381) < 0.001);
  });

  test('cv 判定噪声：与 Python 侧同一阈值 30%', () => {
    assert.ok(cv([100, 100, 100]) < 0.01);
    assert.ok(isNoisy([100, 300, 150, 400, 120]), '离散度大应判为噪声');
    assert.ok(!isNoisy([1500, 1520, 1480, 1510, 1490]), '稳定数据不应判为噪声');
  });

  test('slope：泄漏检测看斜率而非峰值', () => {
    // 线性增长 = 疑似泄漏
    const leaky = [
      { x: 0, y: 100 },
      { x: 1, y: 200 },
      { x: 2, y: 300 },
      { x: 3, y: 400 },
    ];
    assert.ok(Math.abs(slope(leaky) - 100) < 1e-9);

    // 平稳 = 无泄漏
    const flat = [
      { x: 0, y: 100 },
      { x: 1, y: 101 },
      { x: 2, y: 99 },
      { x: 3, y: 100 },
    ];
    assert.ok(Math.abs(slope(flat)) < 1);

    // 下降（修复后）
    const fixed = [
      { x: 0, y: 400 },
      { x: 1, y: 300 },
      { x: 2, y: 200 },
    ];
    assert.ok(slope(fixed) < 0);
  });

  test('slope 边界：少于 2 点或 x 全相同返回 0', () => {
    assert.equal(slope([]), 0);
    assert.equal(slope([{ x: 0, y: 5 }]), 0);
    assert.equal(slope([{ x: 1, y: 5 }, { x: 1, y: 9 }]), 0);
  });
});

// ---------------- Reporter ----------------

class FakeTransport implements Transport {
  sent: ApmPayload[] = [];
  failNext = 0;
  throwOnEvery = false;

  async send(p: ApmPayload): Promise<void> {
    if (this.throwOnEvery || this.failNext > 0) {
      if (this.failNext > 0) this.failNext--;
      throw new Error('network down');
    }
    this.sent.push(p);
  }
}

function payload(tag: string): ApmPayload {
  return {
    context: {
      appVersion: '1.0.0',
      platform: 'ios',
      sessionId: 's1',
      launchType: 'cold',
    },
    sentAt: 0,
    // 用 startup 承载 tag 便于断言
    startup: {
      launchType: 'cold',
      phases: [{ name: tag, sinceProcessStartMs: 0, deltaMs: 0 }],
      totalMs: 0,
      hasProcessStart: true,
    },
  };
}

const tagOf = (p: ApmPayload) => p.startup?.phases[0]?.name;

describe('Reporter', () => {
  test('正常 flush 发送并清空队列', async () => {
    const t = new FakeTransport();
    const r = new Reporter({ transport: t, flushIntervalMs: 0 });
    r.enqueue(payload('a'));
    r.enqueue(payload('b'));
    await r.flush();
    assert.equal(t.sent.length, 2);
    assert.equal(r.queueSize, 0);
    assert.equal(r.sentCount, 2);
  });

  test('发送失败进入退避，不丢数据', async () => {
    const t = new FakeTransport();
    const clock = new FakeClock();
    const r = new Reporter({ transport: t, clock, flushIntervalMs: 0, baseBackoffMs: 1000 });
    t.failNext = 1;
    r.enqueue(payload('a'));
    await r.flush();
    assert.equal(r.queueSize, 1, '失败后不应丢数据');
    assert.equal(r.failedCount, 1);

    await r.flush();
    assert.equal(r.queueSize, 1, '退避期内不应重试');

    clock.advance(1000);
    await r.flush();
    assert.equal(r.queueSize, 0, '退避结束后应重试成功');
    assert.equal(t.sent.length, 1);
  });

  test('超过重试上限后丢弃，避免死信永久占位', async () => {
    const t = new FakeTransport();
    const clock = new FakeClock();
    const r = new Reporter({
      transport: t,
      clock,
      flushIntervalMs: 0,
      maxAttempts: 3,
      baseBackoffMs: 10,
    });
    t.throwOnEvery = true;
    r.enqueue(payload('a'));
    for (let i = 0; i < 5; i++) {
      await r.flush();
      clock.advance(100_000);
    }
    assert.equal(r.queueSize, 0);
    assert.equal(r.droppedCount, 1, '超限的条目应计入 dropped');
  });

  test('队列有界：满时丢弃最旧的，绝不无限增长', async () => {
    const t = new FakeTransport();
    t.throwOnEvery = true;
    const r = new Reporter({ transport: t, flushIntervalMs: 0, maxQueueSize: 3 });
    for (const tag of ['a', 'b', 'c', 'd', 'e']) r.enqueue(payload(tag));
    // 先 flush 一次让 attempts 增长（但仍在队列）
    assert.equal(r.queueSize, 3, '队列长度必须被限制在 3');
    assert.equal(r.droppedCount, 2, '多出的 2 条应被丢弃');

    // 恢复网络后，留下的应该是最新的三条
    t.throwOnEvery = false;
    await r.flush();
    const tags = t.sent.map(tagOf);
    assert.deepEqual(tags, ['c', 'd', 'e'], '应保留最新的三条');
  });

  test('enqueue 永不抛错（即使 transport 抛错）', async () => {
    const t = new FakeTransport();
    t.throwOnEvery = true;
    const r = new Reporter({ transport: t, flushIntervalMs: 0, maxAttempts: 1 });
    assert.doesNotThrow(() => r.enqueue(payload('a')));
    await assert.doesNotReject(() => r.flush());
  });

  test('崩溃数据立即落盘，可跨进程恢复', async () => {
    const storage = new MemoryStorage();
    const t = new FakeTransport();
    t.throwOnEvery = true; // 模拟崩溃时网络不可用

    const r1 = new Reporter({ transport: t, storage, flushIntervalMs: 0 });
    r1.enqueue(payload('crash-data'), { persist: true });
    // persist 是 fire-and-forget，给它一个微任务周期
    await new Promise((res) => setTimeout(res, 10));

    // 模拟进程重启：新的 Reporter 从存储恢复
    const r2 = new Reporter({ transport: new FakeTransport(), storage, flushIntervalMs: 0 });
    const restored = await r2.restore();
    assert.equal(restored, 1, '崩溃数据应能从存储恢复');
    assert.equal(r2.queueSize, 1);
  });

  test('落盘条数有上限，避免存储被撑爆', async () => {
    const storage = new MemoryStorage();
    const t = new FakeTransport();
    t.throwOnEvery = true;
    const r = new Reporter({ transport: t, storage, flushIntervalMs: 0, maxPersisted: 2 });
    for (const tag of ['a', 'b', 'c', 'd']) r.enqueue(payload(tag), { persist: true });
    await new Promise((res) => setTimeout(res, 10));

    const raw = await storage.getItem('rn-apm:queue:v1');
    const parsed = JSON.parse(raw as string) as ApmPayload[];
    assert.equal(parsed.length, 2, '落盘条数应被限制');
  });

  test('onError 自己抛错不会传播', async () => {
    const t = new FakeTransport();
    t.throwOnEvery = true;
    const r = new Reporter({
      transport: t,
      flushIntervalMs: 0,
      maxAttempts: 1,
      onError: () => {
        throw new Error('onError 也炸了');
      },
    });
    r.enqueue(payload('a'));
    await assert.doesNotReject(() => r.flush());
  });

  test('restore 面对损坏数据不崩', async () => {
    const storage = new MemoryStorage();
    await storage.setItem('rn-apm:queue:v1', '{不是合法JSON');
    const r = new Reporter({ transport: new FakeTransport(), storage, flushIntervalMs: 0 });
    const n = await r.restore();
    assert.equal(n, 0);
    assert.equal(r.queueSize, 0);
  });
});
