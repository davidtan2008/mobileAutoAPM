# rn-apm

React Native APM 埋点 SDK —— 补上 **[mobile-apm](../README.md) 蓝图里标记为 P0 的三块缺口**：

| 缺口 | 不做的后果 |
|---|---|
| **启动分段打点** | RN 启动是黑盒，所有通用工具只能给总耗时，不知道时间花在哪 |
| **内存水位环形缓冲** | ⚠️ iOS MetricKit **不含 jetsam 诊断**，**FOOM 完全不可见** |
| **崩溃四层捕获** | ⚠️ 缺任何一层 = 一类崩溃完全不可见，此时"崩溃很少"是假的 |

外加**白屏超时检测**。

---

## 设计原则

1. **零原生依赖即可工作。** 没有原生模块时，JS 侧的全部价值（崩溃四层、白屏、启动分段、
   缓冲与上报）照常提供，只是拿不到进程创建时间和内存数据。
2. **绝不抛错、绝不阻塞。** 埋点不能影响业务 —— 存储失败、原生模块消失、
   上报抛错，全部静默降级。
3. **必须有界。** 所有缓冲都是定容环形缓冲。
   **一个用来发现内存泄漏的工具，自己绝不能泄漏内存。**
4. **如实报告缺口。** 哪一层没接上、哪个原生能力缺失，都明说 —— 而不是假装都好了。

---

## 安装

```bash
npm install /path/to/rn-apm
```

无第三方运行时依赖。`react` 是可选 peer（只有用 `<ApmErrorBoundary>` 时才需要）。

---

## 接入

### 1. `index.js` —— 必须放在**最顶部**

启动打点越早越准。放在所有 `import` 之前。

```js
import { initApm, PHASE } from 'rn-apm';

const apm = initApm({
  appVersion: '2.3.4',
  buildNumber: '567',
  nativeModule: NativeModules.RnApm,        // 可选，见「原生补齐」
  storage: yourAsyncStorageAdapter,          // 可选，用于崩溃数据落盘
  transport: {
    async send(payload) {
      await fetch('https://your-collector/apm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    },
  },
});

apm.startup.mark(PHASE.JS_START);
```

### 2. 挂 ErrorBoundary（第 1 层，不可省）

```jsx
import { ApmErrorBoundary } from 'rn-apm';

<ApmErrorBoundary
  onMount={() => apm.markErrorBoundaryMounted()}
  onError={(e, componentStack) => apm.reportErrorBoundaryError(e, componentStack)}
  fallback={(err, reset) => <ErrorScreen error={err} onRetry={reset} />}
>
  <App />
</ApmErrorBoundary>
```

> 这一层的价值：**只有它能拿到 `componentStack`**（哪个组件的哪次渲染炸的）。
> 全局 handler 能收到错误，但拿不到组件栈。

### 3. 打首屏

```jsx
function HomeScreen() {
  useEffect(() => {
    apm.startup.mark(PHASE.FIRST_RESPONSE);
    apm.startup.markFirstScreen();
    apm.reportStartup();
    apm.reportMemory();
  }, []);
  // ...
}
```

### 4. 白屏检测（按页面）

```jsx
useEffect(() => {
  apm.whiteScreen.startPage('Home');
  return () => apm.whiteScreen.cancel('Home');
}, []);

// 内容真正渲染出来时
apm.whiteScreen.markRendered('Home');
```

### 5. App 前后台（**必须接，否则内存采样会在后台跑**）

```jsx
import { AppState } from 'react-native';
AppState.addEventListener('change', (s) => {
  if (s === 'active') apm.onForeground();
  else apm.onBackground();
});
```

### 6. 轨迹（可选，但崩溃分析价值极高）

```js
apm.breadcrumbs.add('nav', '进入商品详情', { productId: 'p123' });
apm.breadcrumbs.add('api', 'GET /cart 失败', { status: 500 });
```

---

## ⚠️ 上线前必做：检查四层状态

```js
const status = apm.getLayerStatus();
console.table(status);
if (Object.values(status).includes('unavailable')) {
  console.warn('有崩溃层未接上 —— 此时看到的崩溃数据是不完整的', status.notes);
}
```

`getLayerStatus()` 会返回每一层的真实状态与**不可用的原因**。典型输出：

```
errorBoundary: active        // 已挂载
jsGlobal:      active        // ErrorUtils 已接
promise:       unavailable   // ← 最大盲区！
native:        unavailable   // ← 原生崩溃完全不可见
notes.promise: "当前宿主既无 HermesInternal.enablePromiseRejectionTracker，
                也无 unhandledrejection 事件..."
notes.native:  "未接入原生崩溃库..."
```

**任何一层 `unavailable` 都不是小事** —— 它意味着一整类崩溃你现在看不见。

---

## 原生补齐（当前**未在真机验证**）

SDK 在无原生模块时完整可用，但有三项能力需要原生提供。
缺口清单可直接打印：

```js
console.log(apm.getNativeGaps());
```

参考实现见 **[docs/native-shims.md](docs/native-shims.md)**，覆盖 iOS / Android / HarmonyOS：

| 能力 | iOS | Android | HarmonyOS |
|---|---|---|---|
| 进程创建时间 | `sysctl` KERN_PROC_PID | `Process.getStartUptimeMillis()` | HiAppEvent `APP_LAUNCH` |
| 内存占用 | `task_vm_info.phys_footprint` | `Debug.MemoryInfo`（**PSS**） | `hidebug`（**PSS**） |
| JS↔Native 关联 | KSCrash / SentryCrash | Bugly / sentry-android | Bugly 鸿蒙版 / HiAppEvent |

> ⚠️ **内存必须取 PSS/footprint，不能取 RSS** —— 系统按 PSS 判定是否杀进程。
> 这点搞错会让内存数据完全失去指导意义。

---

## 闭环：把数据交给 Agent

SDK 采集 → 转成指标 → Agent 的基线工具判定劣化：

```js
import { toMetrics, serializeMetrics } from 'rn-apm';

const file = toMetrics(collectedPayloads, {
  context: { commit: process.env.GIT_SHA, buildType: 'release' },
});
fs.writeFileSync('run.json', serializeMetrics(file));
```

```bash
python3 ../plugins/mobile-apm/scripts/apm_baseline.py compare \
  --baseline .apm/baseline/startup.json --run run.json
# 退出码 0=无劣化  2=有劣化（可直接作 CI 门禁）  1=数据问题
```

### 实际效果（本仓库已验证的端到端示例）

一次"启动优化"的对比结果：

| 指标 | 判定 |
|---|---|
| `startup.cold.total` | ✅ 改善 **-20%**（p=0.0088） |
| `startup.phase.jsStart` | ✅ 改善 **-40%** ← 指出最大功臣 |
| `memory.peak` +1.8% | ➖ **判为「不具实际意义」**（低于 5% 阈值） |
| `memory.slope` +20% | ❌ **劣化** |

**退出码 2。** 报的是「启动提升 20%，但引入了内存劣化，必须处理」——
而不是「优化成功」。

`toMetrics` 里内置了口径纪律：
- 冷/温/热启动**分开统计**（混比会让结论失真）
- 无进程创建时间的样本**不产生分阶段指标**（口径不可信的不污染基线）
- 慢渲染与白屏**分开计数**（两者根因完全不同）
- 样本混了平台/机型/版本时**输出警告**

---

## 测试

```bash
npm test        # 74 个测试，全部通过
```

覆盖的关键正确性场景：

- **环形缓冲 10 万次 push 后内存恒定**（工具自身不泄漏）
- **`ErrorUtils.setGlobalHandler` 链式调用** —— 不打断 Sentry 等已存在的 handler，
  且他人的 handler 抛错不影响我们，卸载后能恢复
- **崩溃数据跨进程恢复**（落盘 → 新实例 restore）
- **队列有界**：满时丢最旧而非无限增长；超重试上限丢弃避免死信占位
- **超时后最终渲染判为「慢」而非「白屏」**
- **无原生能力时不崩、不阻塞**，并如实报告 3 项能力缺口

---

## 已知限制（如实说明）

| 限制 | 说明 |
|---|---|
| **原生 shim 未在真机验证** | 本仓库环境无 RN 工程，`docs/native-shims.md` 的代码是参考实现，需自行验证 |
| **鸿蒙侧未验证** | RNOH 下的 `HermesInternal`、原生模块形态与标准 RN 可能有差异 |
| **Promise rejection 层可能不可用** | 取决于 RN/Hermes 版本，用 `getLayerStatus()` 确认 |
| **冷/温/热启动判定是启发式** | 拿不准时用 `setLaunchType()` 显式指定 |
| **不采集原生崩溃** | 那需要 KSCrash/Bugly 等，本 SDK 只做 JS 侧与关联 |

---

## 目录结构

```
src/
├── core/         ring-buffer / stats / storage / reporter / clock
├── startup/      启动分段打点
├── crash/        breadcrumbs / error-tracker / global-handler / error-boundary
├── memory/       内存水位采样
├── whitescreen/  超时白屏检测
├── native/       原生桥接与降级
├── metrics-export.ts   转成 Agent 可对比的指标格式
└── index.ts      ApmClient 主入口
```
