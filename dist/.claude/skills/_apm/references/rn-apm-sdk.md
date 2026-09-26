# rn-apm SDK（应用侧埋点）

位于本仓库 `rn-apm/`。**这是补上 P0 缺口的应用侧埋点 SDK。**

## 何时用到它

| 场景 | 用它做什么 |
|---|---|
| 用户说"要能自动发现性能问题" | **前提条件** —— 没有线上埋点，Agent 只能本地跑一次，看不到真实情况 |
| 用户说"用户反馈 App 莫名退出" | **FOOM 归因的唯一路径**（见下） |
| 用户说"要能定位崩溃根因" | 补全四层捕获，否则崩溃数据本身就是残缺的 |
| 用户要建启动基线 | 提供 RN 内部分段（通用工具拿不到） |

## 它补的三块缺口（为什么通用方案做不到）

### 1. 启动分段 —— RN 启动是黑盒

MetricKit / Sentry App Start / Macrobenchmark **都只给总耗时**，拿不到 RN 内部阶段。
不知道时间花在哪一段，优化就只能靠猜。

SDK 提供标准阶段名（`PHASE`），**必须用这些名字，不要自创** —— 同名才能跨版本对比。

### 2. 内存水位 —— iOS FOOM 是官方能力缺口

⚠️ **MetricKit 的 `MXDiagnosticPayload` 不含 jetsam 诊断**，
Apple 增强请求 FB9972410 提了 4 年未落地。官方只给两个**计数**，没有详情。

**不自建内存水位，FOOM 完全不可见。** 表现为"用户莫名退出 + 崩溃平台无记录"。

SDK 用环形缓冲持续采样，崩溃时把水位快照附在崩溃记录里。

### 3. 崩溃四层 —— 缺一层就是一类盲区

| 层 | 覆盖 | 缺了会怎样 |
|---|---|---|
| ErrorBoundary | 同步渲染/生命周期 | React 渲染错误无组件栈 |
| JS 全局 | 未处理 JS 异常 | 异步错误全丢 |
| Promise | 未处理 rejection | **最大盲区** |
| Native | 原生崩溃 | 原生崩溃全不可见 |

`apm.getLayerStatus()` **如实报告哪层没接上**。

## Agent 怎么用它

### 诊断阶段

1. 让用户（或自己）在工程里跑：
   ```bash
   node -e "const{toMetrics}=require('rn-apm');console.log(1)" 2>/dev/null || echo "未安装 rn-apm"
   ```
2. 检查四层状态：
   ```js
   console.table(apm.getLayerStatus());
   console.log(apm.getNativeGaps());   // 原生能力缺口
   ```
   **任何 `unavailable` 都必须先报告给用户** —— 此时看到的崩溃数据不完整。

### 数据接入闭环

```js
import { toMetrics, serializeMetrics } from 'rn-apm';
fs.writeFileSync('run.json', serializeMetrics(toMetrics(payloads, {
  context: {
    commit: GIT_SHA,
    device: DEVICE_LABEL,
    build: 'release',
    measurementMethod: 'rn-toMetrics',
  },
})));
```

然后直接用本插件的工具对比：

```bash
S=".claude/skills/_apm/scripts"
python3 "${S}/apm_diagnose.py" run.json --metric startup.cold.total
python3 "${S}/apm_baseline.py" compare --baseline .apm/baseline/startup.json --run run.json
```

退出码 `0` = 可信且无劣化，`2` = 可确认劣化，`1` = 数据/口径/测量质量问题。

### `toMetrics` 内置的口径纪律（不要绕过它）

- 冷/温/热启动**分开统计**
- 无进程创建时间的样本**不产生分阶段指标**
- 慢渲染与白屏**分开计数**
- 样本混了平台/机型/版本时**输出警告**
- 当前 `toMetrics()` 输出各指标的独立 samples，**尚未保留逐次 observations**；
  在 RN 上做跨段相关/多峰诊断前，必须先补齐逐次配对数据，不能从独立聚合值反推

## 已知限制（必须如实转述，不要夸大）

| 限制 | 说明 |
|---|---|
| 原生 shim **未在真机验证** | `rn-apm/docs/native-shims.md` 是参考实现 |
| 鸿蒙侧未验证 | RNOH 的原生模块形态与标准 RN 有差异 |
| Promise 层可能不可用 | 取决于 RN/Hermes 版本 |
| 冷/温/热判定是启发式 | 拿不准时用 `setLaunchType()` 显式指定 |
| **不采集原生崩溃** | 那需要 KSCrash/Bugly，本 SDK 只做 JS 侧与关联 |

## 与现有工具的关系

| 工具 | 分工 |
|---|---|
| `rn-apm` | **应用侧埋点**：在真实设备上采集 |
| `mobilebuildmcp` | **构建与驱动**：把 App 跑起来、操作、截图 |
| `apm_white_screen.py` | **离线分析**：对截图做像素级白屏判定 |
| `apm_baseline.py` | **判定**：与基线对比、显著性检验 |

四者构成完整链路：**采集 → 驱动 → 分析 → 判定**。
