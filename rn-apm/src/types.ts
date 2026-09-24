/**
 * rn-apm 公共类型定义。
 *
 * 设计原则：**核心逻辑不依赖 React Native 运行时**，便于在 Node 下测试，
 * 也便于换宿主（RN / HarmonyOS RNOH / 甚至 Web）。
 * 所有与宿主相关的能力都通过 NativeBridge 注入，缺失时优雅降级。
 */

export type Platform = 'ios' | 'android' | 'harmony' | 'unknown';

/** 启动类型 —— 三者不可混比，必须分开统计。 */
export type LaunchType = 'cold' | 'warm' | 'hot' | 'unknown';

/** 会话与应用上下文，随每条上报一起发送。 */
export interface ApmContext {
  appVersion: string;
  buildNumber?: string;
  platform: Platform;
  osVersion?: string;
  deviceModel?: string;
  sessionId: string;
  launchType: LaunchType;
}

// ---------------- 存储 / 上报 ----------------

/** 存储适配器。RN 侧用 AsyncStorage/MMKV 实现；测试用内存实现。 */
export interface StorageAdapter {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
}

/** 上报载荷。 */
export interface ApmPayload {
  context: ApmContext;
  startup?: StartupRecord;
  crashes?: CrashRecord[];
  memory?: MemoryReport;
  whiteScreen?: WhiteScreenRecord;
  sentAt: number;
}

/** 上报通道。由使用方提供（HTTP / 原生 SDK / 先落盘后补传）。 */
export interface Transport {
  send(payload: ApmPayload): Promise<void>;
}

// ---------------- 启动 ----------------

export interface StartupPhase {
  /** 阶段名 */
  name: string;
  /** 相对「进程创建」的毫秒数；拿不到进程创建时间时为相对首个打点 */
  sinceProcessStartMs: number;
  /** 相邻阶段耗时（本阶段 - 上一阶段） */
  deltaMs: number;
}

export interface StartupRecord {
  launchType: LaunchType;
  /** 各阶段 */
  phases: StartupPhase[];
  /** 从进程创建到首屏渲染完成的总耗时 */
  totalMs: number;
  /** 是否有原生提供的进程创建时间戳（决定 totalMs 是否可信） */
  hasProcessStart: boolean;
}

// ---------------- 崩溃 ----------------

export type CrashLayer =
  | 'errorBoundary'   // 同步渲染/生命周期
  | 'jsGlobal'        // 未处理 JS 异常
  | 'promise'         // 未处理 Promise rejection
  | 'native';         // 原生崩溃（由原生 SDK 上报，此处仅占位）

export interface Breadcrumb {
  t: number;
  category: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface CrashRecord {
  id: string;
  layer: CrashLayer;
  message: string;
  stack?: string;
  /** 崩溃指纹，用于聚类 */
  fingerprint: string;
  isFatal: boolean;
  componentStack?: string;
  occurredAt: number;
  /** 崩溃前的轨迹（用于还原现场） */
  breadcrumbs: Breadcrumb[];
  /** 崩溃瞬间的内存水位（FOOM/大内存崩溃归因的关键） */
  memoryAtCrash?: MemoryUsage;
}

// ---------------- 内存 ----------------

export interface MemoryUsage {
  /** 采样时刻（相对会话开始的毫秒数） */
  t: number;
  /** 已用字节。iOS 用 footprint，Android/HarmonyOS 用 PSS */
  usedBytes: number;
  /** 采不到时为 undefined */
  totalBytes?: number;
}

export interface MemoryReport {
  samples: MemoryUsage[];
  peakBytes: number;
  /** 首末样本的斜率（字节/秒）——**判断泄漏看斜率，不看峰值** */
  slopeBytesPerSec: number;
  sampleCount: number;
  /** 因超出缓冲容量被丢弃的样本数（>0 说明采样过密或缓冲过小） */
  droppedSamples: number;
}

// ---------------- 白屏 ----------------

export interface WhiteScreenRecord {
  routeName?: string;
  /** 从页面进入到判定白屏的耗时 */
  timeoutMs: number;
  /** 是否最终渲染出来了（超时后才渲染 = 慢，而不是白屏） */
  eventuallyRendered: boolean;
  /** 最终渲染耗时；未渲染则 undefined */
  renderedAfterMs?: number;
  occurredAt: number;
}

// ---------------- NativeBridge ----------------

/**
 * 宿主能力注入点。**每个方法都可缺失** —— 缺失时对应功能自动降级，
 * 绝不因为拿不到原生能力而崩溃或阻塞。
 */
export interface NativeBridge {
  /** 进程创建时间（epoch ms）。拿不到则启动总耗时只能算相对值 */
  getProcessStartTime?(): number | null;
  /** 当前内存占用 */
  getMemoryUsage?(): { usedBytes: number; totalBytes?: number } | null;
  /** 设备/系统信息 */
  getDeviceInfo?(): Partial<Pick<ApmContext, 'osVersion' | 'deviceModel' | 'platform'>>;
  /** 开启 Hermes 的 Promise rejection 跟踪（需原生配置） */
  enablePromiseRejectionTracking?(): boolean;
  /** 错误上报给原生崩溃 SDK（做 JS↔Native 关联） */
  reportJsError?(record: CrashRecord): void;
}
