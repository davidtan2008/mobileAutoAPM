import type { CrashRecord, NativeBridge, Platform } from '../types';

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

    // ⚠️ 这里必须接线。早先 createBridge 只造了 3 个方法，
    // `reportJsError` 声明在 NativeBridge 上却**从未被实现**，
    // 于是：
    //   1. 原生模块（如 iOS shim 的 reportJsError）实现的同名方法**永远不会被调用**；
    //   2. `getLayerStatus().native` **恒为 'unavailable'**；
    //   3. `getNativeGaps()` **恒返回 3 条**（第三条永远是「缺 reportJsError」）。
    // 它是「fire-and-forget」语义（返回 void），所以同步调用即可；
    // 原生侧若是 Promise 方法，这里拿到的 Promise 直接忽略。
    reportJsError(record: CrashRecord): void {
      try {
        const fn = m.reportJsError;
        if (typeof fn === 'function') {
          (fn as (r: CrashRecord) => unknown).call(mod, record);
        }
      } catch {
        // 原生侧抛错不能影响 JS 侧（崩溃路径上尤其重要）
      }
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

  // ⚠️ 判定方式必须是**探测实际取值**，不能只看方法存不存在。
  // 原因：`createBridge` **总是**定义那三个读取方法（取不到时返回 null），
  // 所以「方法在」不等于「原生侧真有这个能力」。早先只看方法存在性，
  // 于是 createBridge 的产物永远报 0 个缺口 —— 而实际上原生模块可能
  // 什么都没提供。reportJsError 是 fire-and-forget（有副作用），
  // 只能看方法在不在，不能探测调用。
  const readProcessStart = (() => {
    try {
      return bridge.getProcessStartTime?.() ?? null;
    } catch {
      return null;
    }
  })();
  if (!bridge.getProcessStartTime || readProcessStart === null) {
    gaps.push(
      '缺 getProcessStartTime：启动总耗时只能算相对值，**不可与线上基线对比**。' +
        'iOS 用 sysctl(KERN_PROC_PID) 取 p_starttime；Android 用 Process.getStartUptimeMillis()；鸿蒙用 HiAppEvent APP_LAUNCH。',
    );
  }

  const readMemory = (() => {
    try {
      return bridge.getMemoryUsage?.() ?? null;
    } catch {
      return null;
    }
  })();
  if (!bridge.getMemoryUsage || readMemory === null) {
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
