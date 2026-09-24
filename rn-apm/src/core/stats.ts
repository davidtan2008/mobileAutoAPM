/**
 * 统计工具。
 *
 * 与 `plugins/mobile-apm/scripts/apm_baseline.py` 保持**同一套口径**：
 *   - 用中位数(p50) 与 p90 描述分布，不用平均值（平均值被长尾拖偏）
 *   - 用变异系数 CV 判定测量是否可靠（> 0.30 视为不可靠）
 *
 * 这样 SDK 采到的数据与 Agent 的基线对比工具能直接对接。
 */

/** 线性插值分位数，p 取 0..100。空数组返回 NaN。 */
export function percentile(xs: readonly number[], p: number): number {
  if (xs.length === 0) return NaN;
  if (xs.length === 1) return xs[0] as number;
  const s = [...xs].sort((a, b) => a - b);
  const k = (s.length - 1) * (p / 100);
  const lo = Math.floor(k);
  const hi = Math.ceil(k);
  if (lo === hi) return s[lo] as number;
  const a = s[lo] as number;
  const b = s[hi] as number;
  return a * (hi - k) + b * (k - lo);
}

export function median(xs: readonly number[]): number {
  return percentile(xs, 50);
}

export function mean(xs: readonly number[]): number {
  if (xs.length === 0) return NaN;
  let sum = 0;
  for (const x of xs) sum += x;
  return sum / xs.length;
}

/** 样本标准差（n-1 分母）。少于 2 个样本时返回 0。 */
export function stdev(xs: readonly number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  let acc = 0;
  for (const x of xs) acc += (x - m) ** 2;
  return Math.sqrt(acc / (xs.length - 1));
}

/** 变异系数 = 标准差 / |均值|。衡量测量噪声，> 0.30 时结论不可信。 */
export function cv(xs: readonly number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  if (m === 0) return 0;
  return stdev(xs) / Math.abs(m);
}

/** 噪声阈值，与 Python 侧保持一致。 */
export const NOISE_CV_THRESHOLD = 0.3;

export function isNoisy(xs: readonly number[]): boolean {
  return cv(xs) > NOISE_CV_THRESHOLD;
}

/**
 * 最小二乘斜率（y 对 x）。
 *
 * **判断内存泄漏用斜率，不用峰值。**
 * 峰值下降可能只是缓存差异；而"斜率从线性增长变为趋于 0"才是泄漏被修复的证据。
 */
export function slope(points: ReadonlyArray<{ x: number; y: number }>): number {
  const n = points.length;
  if (n < 2) return 0;
  let sx = 0;
  let sy = 0;
  for (const p of points) {
    sx += p.x;
    sy += p.y;
  }
  const mx = sx / n;
  const my = sy / n;
  let num = 0;
  let den = 0;
  for (const p of points) {
    num += (p.x - mx) * (p.y - my);
    den += (p.x - mx) ** 2;
  }
  return den === 0 ? 0 : num / den;
}
