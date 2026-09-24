---
name: apm-render
description: This skill should be used when the user asks about page rendering performance, jank, dropped frames, or blank screens — "页面渲染慢"、"卡顿"、"掉帧"、"FPS 低"、"滑动不顺"、"白屏"、"页面空白"、"首屏渲染"、"TTID"、"TTFD" — on iOS / React Native / Android / HarmonyOS. Covers JS-vs-UI FPS separation, frame overrun analysis, white-screen detection, and their optimization and verification.
version: 0.1.0
---

# 页面渲染 / 卡顿 / 白屏

**必读**：`.claude/skills/_apm/references/metrics-definitions.md` §2、§3。

---

# 一、渲染时长与卡顿

## 第一步：区分"慢"和"卡"（不同的病，不同的药）

| 症状 | 指标 | 含义 |
|---|---|---|
| **慢** | TTID / TTFD | 从进入页面到**首次可见/完全可交互**的时间 |
| **卡** | 帧耗时 / jank | 渲染过程中**掉帧** |

**"页面打开慢"和"页面滑动卡"是两个问题**，先问清楚是哪个。

## 第二步：RN 必须分离观测两种 FPS（关键）

**这是 RN 卡顿归因的核心，只测一个数没有诊断价值。**

| 指标 | 瓶颈指向 | 修法方向 |
|---|---|---|
| **JS FPS 低** | JS 线程被阻塞 | 减少重计算、拆分 setState、避免长任务阻塞 |
| **UI FPS 低** | 原生渲染/布局 | 减少视图层级、优化列表、避免复杂布局 |

> 3 FPS 的 JS + 60 FPS 的 UI，和 60 FPS 的 JS + 3 FPS 的 UI，
> **是完全不同的问题，修法完全相反**。

## 第三步：卡顿指标口径

- **Android**：核心是 **p95 `frameOverrunMs`** —— 它回答"滚动中最坏的那一帧有多糟"。
  负值 = 准时，正值 = 错过 deadline 的毫秒数
- **iOS**：MetricKit `scrollHitchTimeRatio`，单位 ms/s
- **鸿蒙**：`SCROLL_JANK` 事件，**单帧 > 50ms 即上报**
- ⚠️ 用 **p95 而不是平均值** —— 卡顿是长尾事件，平均值看不出来

## 第四步：根因清单

- [ ] **首屏依赖串行**：接口 A 完成才请求 B？能否并行或预取？
- [ ] **列表未虚拟化**：长列表是否用了 `FlatList`/`SectionList`？（RN 最常见）
- [ ] **`key` 使用不当**：用 index 作 key 会导致整表重渲染
- [ ] **过度渲染**：`React.memo` / `useMemo` / `useCallback` 是否缺失？
      （用 React DevTools Profiler 确认，**不要凭感觉加**）
- [ ] **大对象跨桥传递**：新架构下已大幅缓解，但大 payload 仍慢
- [ ] **同步 IO 在主线程**：`onCreate`/`componentDidMount` 里读文件/SharedPreferences
- [ ] **图片未优化**：未压缩、未按需加载、未用缓存
- [ ] **动画用 JS 驱动**：应用 `useNativeDriver: true`
- [ ] ⚠️ **Hermes Sampling Profiler 默认开启会导致内存无限增长**
      （`sampledStacks_`），排查时先怀疑它
- [ ] **布局抖动**：频繁读取 `onLayout` 触发多次 layout pass

## 第五步：优化原则

同上 `apm-startup` 的四步法：**删 → 延迟 → 并发 → 更快**。

RN 特别提示：
- 优先**减少渲染次数**（memo 化、稳定引用）而不是优化单次渲染
- 列表性能问题 90% 出在**虚拟化缺失**或**key 不稳定**
- 不要在没有 Profiler 数据的情况下盲目加 `useMemo`（可能反而更慢）

---

# 二、白屏检测

## 核心结论：2026 年没有单一可用的 RN FCP API

必须**组合三条路线**，任何单一路线都有误报：

| 方法 | 原理 | 误报风险 |
|---|---|---|
| **分段打点 + 超时**（主力） | 首屏渲染点未在阈值内触发即告警 | 无（但需埋点） |
| **截图像素分析** | 判断是否纯色 | 纯色设计的页面会误判 |
| **View 树检测** | 根节点无子节点 | 页面本就为空时误判 |

## 截图检测（已内置脚本，零依赖）

```bash
python3 ".claude/skills/_apm/scripts/apm_white_screen.py" \
  .apm/runs/<本次>/screenshots/*.png

# 机器可读
python3 ".claude/skills/_apm/scripts/apm_white_screen.py" --json shot.png
```

脚本用**三重证据**判定（边缘密度 + 亮度标准差 + 主色占比），
避免"纯色设计页面"被误判为白屏。已验证可处理真实模拟器截图。

**用法**：配合 `mobilebuildmcp` 截图能力采集页面加载过程的多帧：

```bash
mobilebuildmcp simulator screenshot --help
mobilebuildmcp simulator record-video --help   # 录视频后抽帧
```

对多帧运行脚本 → 若前 N 帧空白 → 量化出**白屏持续时长**。

## 鸿蒙独门利器

监听渲染子进程崩溃 **`onRenderExited`**（API 9+），
`RenderExitReason` 可直接区分根因：

| 值 | 含义 |
|---|---|
| `ProcessAbnormalTermination(0)` | 异常终止 |
| `ProcessWasKilled(1)` | 被杀死 |
| **`ProcessCrashed(2)`** | 渲染进程崩溃 |
| **`ProcessOom(3)`** | **内存不足被杀** ← 直接指向内存问题 |

这是鸿蒙独有的白屏归因能力，其他平台需要自行推断。

## 白屏常见根因

- [ ] **接口未返回 / 超时**：页面依赖的数据没到，UI 无兜底 → 加 loading/骨架屏
- [ ] **条件渲染分支问题**：`data && <View>` 在 data 为 `[]` 或 `0` 时渲染成空
- [ ] **异常被吞**：错误被 try/catch 吞掉，页面停在空白
- [ ] **路由/参数错误**：页面组件 mount 了但内容为空
- [ ] **首屏渲染阻塞**：主线程被同步计算占用（鸿蒙注意：**单纯 `async` 不会切线程**，
      要真正移交到 TaskPool/Worker）
- [ ] **Splash 未正确 finish**

## 感知优化（性价比最高）

**用 `react-native-bootsplash` 等把"白屏等待"变成"品牌 Splash"。**

⚠️ 注意：这只是**遮蔽**不是解决。真正的问题（数据慢、渲染慢）仍要修。
但感知收益极大 —— 用户看到品牌动画 vs 看到白屏，体感天差地别。

**同时要修根因**：骨架屏 + 数据预取 + 局部渲染。

---

## 验证

```bash
python3 ".claude/skills/_apm/scripts/apm_baseline.py" compare \
  --baseline .apm/baseline/render.json --run .apm/runs/<本次>/metrics.json
```

- 渲染耗时的验证同 `apm-startup`：**口径一致 + 超噪声 + 功能回归**
- 白屏验证：用**相同操作路径**重跑多帧截图，确认空白帧消失
- 卡顿验证：**必须看 p95 `frameOverrunMs`，不能只看平均帧率**

## 常见误判

| 误判 | 真相 |
|---|---|
| "平均 FPS 60，说明不卡" | 平均值掩盖长尾。要看 **p95** |
| "截图是白的，所以白屏了" | 纯色设计的页面也会是白的，要看边缘密度 |
| "加了骨架屏就算优化完了" | 骨架屏是遮蔽，根因（数据慢）还在 |
| "JS FPS 正常所以不是 RN 的问题" | 可能是 UI FPS 低，即原生渲染问题 |
| "模拟器上不卡" | 模拟器性能特征与真机完全不同 |
