import type { NativeBridge, Platform } from '../types';

/**
 * 原生桥接的辅助工具。
 *
 * **核心原则：原生能力一律可缺失，缺失时优雅降级，绝不因此崩溃或阻塞。**
 * 这样这套 SDK 才能同时跑在 iOS / Android / HarmonyOS(ROH) 上 ——
 * 鸿蒙侧的原生模块往往要自己写，不该成为"能不能跑起来"的前提。
 */

/** 空实现。所有能力都不可用时使用，SDK 仍能提供 JS 侧的全部价值。 */
export const nullBridge: NativeBridge = {};

/**
 * 从一个原生模块对象构建 bridge。
 *
 * 兼容两种形态：
 *  - 方法：`mod.getMemoryUsage()`
 *  - 常量：`mod.getConstants?.().memoryUsage`（老架构 NativeModule 风格）
 *
 * 所有调用都包了 try/catch —— 原生方法抛错（如模块被卸载）不能影响 JS 侧。
 */
export function createBridge(mod: unknown, fallback: Partial<NativeBridge> = {}): NativeBridge {
  if (!mod || typeof mod !== 'object') return { ...fallback };

  const m = mod as Record<string, unknown>;
  const constants = (() => {
    try {
      const fn = m.getConstants;
      if (typeof fn === 'function') {
        const c = (fn as () => unknown).call(mod);
        return c && typeof c === 'object' ? (c as Record<string, unknown>) : {};
      }
    } catch {
      /* 忽略 */
    }
    return {};
  })();

  const pick = <T>(methodName: string, constName: string): T | null => {
    try {
      const fn = m[methodName];
      if (typeof fn === 'function') {
        const v = (fn as () => unknown).call(mod);
        return (v ?? null) as T | null;
      }
      const c = constants[constName];
      return (c ?? null) as T | null;
    } catch {
      return null;
    }
  };

  const bridge: NativeBridge = {
    getProcessStartTime(): number | null {
      const v = pick<number>('getProcessStartTime', 'processStartTime');
      return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
    },

    getMemoryUsage(): { usedBytes: number; totalBytes?: number } | null {
      const v = pick<{ usedBytes?: number; totalBytes?: number }>('getMemoryUsage', 'memoryUsage');
      if (!v || typeof v.usedBytes !== 'number' || !Number.isFinite(v.usedBytes)) return null;
      const out: { usedBytes: number; totalBytes?: number } = { usedBytes: v.usedBytes };
      if (typeof v.totalBytes === 'number') out.totalBytes = v.totalBytes;
      return out;
    },

    getDeviceInfo(): { osVersion?: string; deviceModel?: string; platform?: Platform } {
      const v = pick<Record<string, unknown>>('getDeviceInfo', 'deviceInfo');
      const out: { osVersion?: string; deviceModel?: string; platform?: Platform } = {};
      if (v && typeof v === 'object') {
        if (typeof v.osVersion === 'string') out.osVersion = v.osVersion;
        if (typeof v.deviceModel === 'string') out.deviceModel = v.deviceModel;
        if (v.platform === 'ios' || v.platform === 'android' || v.platform === 'harmony') {
          out.platform = v.platform;
        }
      }
      // 平台至少有 RN 的 Platform.OS 可兜底
      if (!out.platform) out.platform = detectPlatformFromRN();
      return out;
    },
  };

  return { ...bridge, ...fallback };
}

/** 用 RN 的 Platform.OS 兜底判断平台（不需要原生模块）。 */
export function detectPlatformFromRN(): Platform {
  try {
    const g = globalThis as unknown as {
      require?: (name: string) => unknown;
    };
    if (typeof g.require === 'function') {
      const rn = g.require('react-native') as { Platform?: { OS?: string } } | undefined;
      const os = rn?.Platform?.OS;
      if (os === 'ios' || os === 'android') return os;
      // 鸿蒙侧 RNOH 通常报 'harmony' 或 'harmonyos'
      if (os === 'harmony' || os === 'harmonyos') return 'harmony';
    }
  } catch {
    /* 非 RN 环境 */
  }
  return 'unknown';
}

/**
 * 生成一个"常量型"原生模块的参考实现说明。
 * 这里的值仅用于在没有原生模块时给出可辨识的空缺，**不是真实数据**。
 */
export function describeNativeGaps(bridge: NativeBridge): string[] {
  const gaps: string[] = [];
  if (!bridge.getProcessStartTime) {
    gaps.push(
      '缺 getProcessStartTime：启动总耗时只能算相对值，**不可与线上基线对比**。' +
        'iOS 用 sysctl(KERN_PROC_PID) 取 p_starttime；Android 用 Process.getStartUptimeMillis()；鸿蒙用 HiAppEvent APP_LAUNCH。',
    );
  }
  if (!bridge.getMemoryUsage) {
    gaps.push(
      '缺 getMemoryUsage：**内存水位与 FOOM 归因完全不可用**。' +
        'iOS 用 task_vm_info.phys_footprint；Android/鸿蒙用 PSS。',
    );
  }
  if (!bridge.reportJsError) {
    gaps.push(
      '缺 reportJsError：JS 崩溃无法与原生崩溃关联。' +
        '接入 KSCrash / SentryCrash / Bugly 后实现该方法。',
    );
  }
  return gaps;
}
