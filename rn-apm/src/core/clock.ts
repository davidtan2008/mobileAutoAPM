/**
 * 时钟抽象。
 *
 * 为什么不用 Date.now() 直接测耗时：**墙钟会被 NTP 校时、用户改时间影响**，
 * 可能出现负数耗时。测耗时必须用单调时钟。
 *
 * 测试时注入 `FakeClock` 即可让所有时序逻辑可确定性验证。
 */
export interface Clock {
  /** 单调递增毫秒。用于测耗时。 */
  monotonic(): number;
  /** 墙钟 epoch 毫秒。用于记录"何时发生"。 */
  wall(): number;
}

/** 默认时钟：优先用 performance.now（RN 的 Hermes/JSC 都提供）。 */
export const systemClock: Clock = (() => {
  const perf =
    typeof globalThis !== 'undefined' &&
    typeof (globalThis as { performance?: { now?: () => number } }).performance?.now === 'function'
      ? (globalThis as { performance: { now: () => number } }).performance
      : null;

  const origin = Date.now();
  return {
    monotonic: () => (perf ? perf.now() : Date.now() - origin),
    wall: () => Date.now(),
  };
})();

/** 测试用可控时钟。 */
export class FakeClock implements Clock {
  constructor(private t = 0, private wallTime = 1_700_000_000_000) {}

  monotonic(): number {
    return this.t;
  }

  wall(): number {
    return this.wallTime;
  }

  /** 推进单调时钟（通常也同步推进墙钟）。 */
  advance(ms: number): void {
    if (ms < 0) throw new RangeError('时钟不能倒流');
    this.t += ms;
    this.wallTime += ms;
  }
}
