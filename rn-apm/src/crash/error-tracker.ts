import type { CrashLayer, CrashRecord, MemoryUsage, NativeBridge } from '../types';
import { RingBuffer } from '../core/ring-buffer';
import { type Clock, systemClock } from '../core/clock';
import type { BreadcrumbStore } from './breadcrumbs';

/** FNV-1a 32 位哈希。用于生成稳定的崩溃指纹。 */
function fnv1a(str: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

/** 从堆栈里取顶部 n 帧的「函数@文件」，**去掉行号列号**。 */
export function topFrames(stack: string | undefined, n = 3): string[] {
  if (!stack) return [];
  const out: string[] = [];
  const lines = stack.split('\n');
  for (const line of lines) {
    const t = line.trim();
    if (!t.startsWith('at ')) continue;
    // 形如 "at foo (file.js:12:34)" 或 "at file.js:12:34"
    const m = /^at\s+(.*?)(?:\s+\((.*?)\))?$/.exec(t);
    if (!m) continue;
    let fn = m[1] ?? '';
    let loc = m[2] ?? '';
    if (!loc) {
      // 没有括号形式，说明 fn 本身就是位置
      loc = fn;
      fn = '<anonymous>';
    }
    // 去掉 :行:列，让同一 bug 跨构建归为一类
    const file = loc.replace(/:\d+:\d+$/, '').replace(/:\d+$/, '');
    out.push(`${fn}@${basename(file)}`);
    if (out.length >= n) break;
  }
  return out;
}

function basename(p: string): string {
  const s = p.split(/[\\/]/);
  return s[s.length - 1] ?? p;
}

/**
 * 生成崩溃指纹。**同指纹的崩溃会被聚为一类** —— 这是"先聚类再修"的前提，
 * 否则会在长尾上浪费全部时间。
 */
export function fingerprintError(message: string, stack?: string): string {
  const frames = topFrames(stack, 3);
  return fnv1a(`${message}||${frames.join('|')}`);
}

/** 把任意抛出物规范化成 message + stack。永不抛错。 */
export function normalizeError(e: unknown): { message: string; stack?: string } {
  try {
    if (e instanceof Error) {
      const msg = e.message || e.name || 'Error';
      return { message: msg, stack: e.stack };
    }
    if (typeof e === 'string') return { message: e };
    if (e === null) return { message: 'null thrown' };
    if (e === undefined) return { message: 'undefined thrown' };
    if (typeof e === 'object') {
      const o = e as Record<string, unknown>;
      const msg =
        typeof o.message === 'string'
          ? o.message
          : safeStringify(e);
      return { message: msg, stack: typeof o.stack === 'string' ? o.stack : undefined };
    }
    return { message: String(e) };
  } catch {
    return { message: '<无法规范化的抛出物>' };
  }
}

function safeStringify(v: unknown): string {
  try {
    const seen = new WeakSet<object>();
    return JSON.stringify(v, (_k, val: unknown) => {
      if (typeof val === 'object' && val !== null) {
        if (seen.has(val as object)) return '[Circular]';
        seen.add(val as object);
      }
      if (typeof val === 'function') return '[Function]';
      return val;
    }) ?? String(v);
  } catch {
    return '<不可序列化>';
  }
}

export interface CrashCollectorOptions {
  clock?: Clock;
  breadcrumbs?: BreadcrumbStore;
  native?: NativeBridge;
  /** 本地最多保留多少条崩溃（有界，避免崩溃风暴撑爆内存） */
  maxCrashes?: number;
}

/**
 * 崩溃收集器。
 *
 * 只负责"收集 + 归一化 + 有界缓存"，不做上报 —— 上报由 Reporter 负责。
 * 这样职责清晰，也便于测试。
 */
export class CrashCollector {
  private readonly buf: RingBuffer<CrashRecord>;
  private readonly clock: Clock;
  private readonly breadcrumbs?: BreadcrumbStore;
  private readonly native?: NativeBridge;
  private totalRecorded = 0;

  constructor(opts: CrashCollectorOptions = {}) {
    this.clock = opts.clock ?? systemClock;
    this.buf = new RingBuffer<CrashRecord>(opts.maxCrashes ?? 20);
    this.breadcrumbs = opts.breadcrumbs;
    this.native = opts.native;
  }

  /** 记录一次崩溃。永不抛错 —— 它是在崩溃路径上运行的。 */
  record(
    layer: CrashLayer,
    error: unknown,
    opts: { isFatal?: boolean; componentStack?: string } = {},
  ): CrashRecord {
    const { message, stack } = normalizeError(error);
    const record: CrashRecord = {
      id: `${this.clock.wall()}-${fnv1a(message + layer)}`,
      layer,
      message,
      stack,
      fingerprint: fingerprintError(message, stack),
      isFatal: opts.isFatal ?? true,
      occurredAt: this.clock.wall(),
      breadcrumbs: this.breadcrumbs?.snapshot() ?? [],
    };
    if (opts.componentStack) record.componentStack = opts.componentStack;

    // 崩溃瞬间的内存水位 —— FOOM / 大内存崩溃归因的关键证据
    try {
      const mem = this.native?.getMemoryUsage?.();
      if (mem && typeof mem.usedBytes === 'number') {
        const usage: MemoryUsage = { t: 0, usedBytes: mem.usedBytes };
        if (typeof mem.totalBytes === 'number') usage.totalBytes = mem.totalBytes;
        record.memoryAtCrash = usage;
      }
    } catch {
      // 拿不到就算了，不能因为取内存而二次崩溃
    }

    this.totalRecorded++;
    this.buf.push(record);

    // 顺带把 JS 崩溃交给原生 SDK，做 JS↔Native 关联
    try {
      this.native?.reportJsError?.(record);
    } catch {
      // 忽略
    }
    return record;
  }

  /** 取出并清空（用于上报）。 */
  drain(): CrashRecord[] {
    const all = this.buf.toArray();
    this.buf.clear();
    return all;
  }

  /** 只读快照，不清空。 */
  peek(): CrashRecord[] {
    return this.buf.toArray();
  }

  get count(): number {
    return this.buf.size;
  }

  /** 历史累计记录数（含已被覆盖的），用于发现"崩溃风暴"。 */
  get totalRecordedCount(): number {
    return this.totalRecorded;
  }

  get droppedCount(): number {
    return this.buf.dropped;
  }
}
