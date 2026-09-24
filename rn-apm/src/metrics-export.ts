import type { ApmPayload } from './types';

/**
 * 把 SDK 采集到的 payload 转换成 **APM Agent 的指标格式**。
 *
 * 这一步是**闭环的关键**：SDK 负责在真实设备上采集，
 * Agent 负责与基线对比、判定劣化、定位根因。
 * 两边必须用同一套格式，否则数据接不上。
 *
 * 输出格式与 `plugins/mobile-apm/scripts/apm_baseline.py` 的输入**完全一致**：
 * ```json
 * {"context": {...}, "metrics": [
 *   {"name":"startup.total","unit":"ms","direction":"lower_is_better","samples":[...]}
 * ]}
 * ```
 * 于是可以直接：
 * ```bash
 * python3 apm_baseline.py compare --baseline b.json --run <本函数输出>.json
 * ```
 */

export type MetricDirection = 'lower_is_better' | 'higher_is_better';

export interface MetricSample {
  name: string;
  unit: string;
  direction: MetricDirection;
  samples: number[];
}

export interface MetricsFile {
  context: Record<string, unknown>;
  metrics: MetricSample[];
}

export interface ToMetricsOptions {
  /** 只保留样本数 ≥ 该值的指标（默认 1，即都保留） */
  minSamples?: number;
  /** 额外写入的上下文（如设备池名称、构建号） */
  context?: Record<string, unknown>;
}

/**
 * 聚合多个 payload 为可对比的指标文件。
 *
 * ⚠️ **只有口径相同的样本才会被聚合到一起。**
 * 例如冷启动与热启动是两个不同的指标名（`startup.cold.total` vs `startup.hot.total`），
 * 混在一起算中位数是没有意义的 —— 这正是知识库里反复强调的口径纪律。
 */
export function toMetrics(payloads: readonly ApmPayload[], opts: ToMetricsOptions = {}): MetricsFile {
  const buckets = new Map<string, MetricSample>();

  const add = (name: string, unit: string, direction: MetricDirection, value: number): void => {
    if (!Number.isFinite(value)) return;
    const key = `${name}|${unit}|${direction}`;
    let b = buckets.get(key);
    if (!b) {
      b = { name, unit, direction, samples: [] };
      buckets.set(key, b);
    }
    b.samples.push(value);
  };

  const contextSources: ApmPayload['context'][] = [];

  for (const p of payloads) {
    contextSources.push(p.context);

    if (p.startup) {
      const lt = p.startup.launchType;
      // 冷/温/热分开统计 —— 混比会让结论失真
      add(`startup.${lt}.total`, 'ms', 'lower_is_better', p.startup.totalMs);

      if (lt === 'cold') {
        add('startup.total', 'ms', 'lower_is_better', p.startup.totalMs);
      }

      // 分阶段：只有口径可信（有进程创建时间）时才计入
      if (p.startup.hasProcessStart) {
        for (const ph of p.startup.phases) {
          add(`startup.phase.${ph.name}`, 'ms', 'lower_is_better', ph.deltaMs);
        }
      }
    }

    if (p.memory) {
      add('memory.peak', 'bytes', 'lower_is_better', p.memory.peakBytes);
      add('memory.slope', 'bytes_per_sec', 'lower_is_better', p.memory.slopeBytesPerSec);
    }

    if (p.whiteScreen) {
      if (p.whiteScreen.eventuallyRendered) {
        // 慢渲染：这是「慢」，不是「白屏」，指标名必须区分开
        add('whitescreen.late_render', 'ms', 'lower_is_better', p.whiteScreen.renderedAfterMs ?? 0);
      } else {
        add('whitescreen.count', 'count', 'lower_is_better', 1);
      }
    }

    for (const c of p.crashes ?? []) {
      // 崩溃按指纹计数，便于看"哪一类最多"
      if (c.isFatal) add('crash.fatal.count', 'count', 'lower_is_better', 1);
      else add('crash.nonfatal.count', 'count', 'lower_is_better', 1);
    }
  }

  const min = opts.minSamples ?? 1;
  const metrics = [...buckets.values()].filter((m) => m.samples.length >= min);
  metrics.sort((a, b) => a.name.localeCompare(b.name));

  return {
    context: summarizeContext(contextSources, opts.context),
    metrics,
  };
}

/**
 * 汇总上下文。
 *
 * **必须如实反映样本的构成** —— 如果样本里混了不同的设备或构建类型，
 * 对比结论就会失真，所以这里把它们都列出来供人检查。
 */
function summarizeContext(
  sources: readonly ApmPayload['context'][],
  extra?: Record<string, unknown>,
): Record<string, unknown> {
  const uniq = (fn: (c: ApmPayload['context']) => string | undefined): string[] =>
    [...new Set(sources.map(fn).filter((v): v is string => typeof v === 'string' && v.length > 0))];

  const platforms = uniq((c) => c.platform);
  const versions = uniq((c) => c.appVersion);
  const devices = uniq((c) => c.deviceModel);
  const osVersions = uniq((c) => c.osVersion);

  const out: Record<string, unknown> = {
    sampleCount: sources.length,
    platform: platforms.length === 1 ? platforms[0] : platforms,
    appVersion: versions.length === 1 ? versions[0] : versions,
  };
  if (devices.length > 0) out.deviceModel = devices.length === 1 ? devices[0] : devices;
  if (osVersions.length > 0) out.osVersion = osVersions.length === 1 ? osVersions[0] : osVersions;

  // 混样本警告 —— 让对比工具与人都能立刻看出问题
  const warnings: string[] = [];
  if (platforms.length > 1) warnings.push(`样本包含多个平台：${platforms.join(', ')}`);
  if (versions.length > 1) warnings.push(`样本包含多个版本：${versions.join(', ')}`);
  if (devices.length > 1) warnings.push(`样本包含多个机型：${devices.join(', ')}`);
  if (warnings.length > 0) out.warnings = warnings;

  if (extra) Object.assign(out, extra);
  return out;
}

/** 序列化为 JSON 字符串（便于直接写文件）。 */
export function serializeMetrics(file: MetricsFile): string {
  return JSON.stringify(file, null, 2);
}
