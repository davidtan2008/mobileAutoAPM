---
name: apm-startup
description: This skill should be used when the user asks about app launch time — "启动慢"、"冷启动优化"、"启动耗时"、"launch time"、"首屏慢"、"启动优化"、"APP 启动要几秒" — or when startup regression needs to be measured, diagnosed or prevented on iOS / React Native / Android / HarmonyOS. Covers measurement methodology, staged instrumentation, root-cause checklist, and the delete→defer→parallelize→speed-up optimization playbook.
version: 0.1.0
---

# 启动时长：检测 / 定位 / 优化 / 验证

**必读**：`.claude/skills/_apm/references/metrics-definitions.md` §1（口径定义）与
`.claude/skills/_apm/references/stack-selection.md` §3（手段与硬限制）。

## 第一步永远是锁定口径

启动优化最容易失败的地方**不是优化手法，而是口径不一致**。
在测任何数据之前，先明确并记录：

| 必须明确 | 说明 |
|---|---|
| 起点 | **统一用「进程创建」**（iOS 用 `sysctl` 取时间戳） |
| 终点 | **Launch Image 消失第一帧**。iOS 13+ 对齐 `applicationDidBecomeActive` |
| 启动类型 | 冷启动 / 温启动 / 热启动 —— **三者不可混比** |
| 构建类型 | release（⚠️ **绝不用 debug 数据代表线上**） |
| 设备 | 真机型号 + 系统版本（⚠️ 模拟器性能特征完全不同） |

把这份口径写进 `.apm/runs/<时间>/meta.json`，**验证阶段必须复用同一份**。

## 第二步：测量

### 最小可用（无需改业务代码）

```bash
# iOS：用 mobilebuildmcp 构建并运行，配合 xctrace 录制 App Launch
mobilebuildmcp simulator build-and-run --help
xcrun xctrace record --template 'App Launch' --launch <bundleid> \
  --output .apm/runs/$(date +%s)/launch.trace
```

### 线上口径（生产数据）

- **iOS**：MetricKit `MXAppLaunchMetric` ⚠️ 有 **24 小时延迟**，且**模拟器不支持**
- **RN**：`@sentry/react-native` 的 App Start span（自动区分冷/温/热），但**只有总量，无内部分段**
- **Android**：Macrobenchmark `StartupTimingMetric`（必须 release 构建）
- **鸿蒙**：HiAppEvent `APP_LAUNCH`（⚠️ **模拟器不支持订阅**）

**至少测 3 次取中位数**，记录方差。方差 > 30% 先修测量方法，别急着优化。

### ⚠️ 动手之前先回答一个问题

> **我打算优化的幅度，比测量的离散度大吗？**

冷启动测量的方差**经常比要优化的幅度还大**。实测案例：目标优化 50ms，
而冷启动总耗时有一个未受控的 **2 倍方差源**（样本在 279ms 与 541ms 之间跳），
**结果任何优化都无法验证**。

**如果答不上来 —— 停下来先修测量。** 在方差 200ms 的指标上验证 50ms 的优化，
无论得到什么结论都是噪声。

| CV | 判断 | 该怎么做 |
|---|---|---|
| < 10% | 可用 | 能支撑几十 ms 级的优化 |
| 10–30% | 勉强 | 只能验证大幅优化 |
| > 30% | **不可用** | **停止优化，先修测量** |

**发现数据呈多峰时**（例如一半样本快、一半慢）：
**不要挑快的那簇报数 —— 那是伪造。** 正确做法见下。

📖 **完整协议见 `.claude/skills/_apm/references/measurement-protocol.md`** ——
包含已知方差源、降方差实践、以及「总指标不可用时改测分解指标」的做法。

### 一条实用出路：改测可分解的指标

当总指标方差太大时，把它**分解**，选**方差小、且优化真正作用的那几段**做判定。

实测对比（同一次测量）：

| 指标 | CV | 可用性 |
|---|---|---|
| 总耗时 | 19–30% | ❌ |
| 可优化段之和 | **8–10%** | ✅ |

⚠️ 但**两者要一起看** —— 分解指标动了而总指标没动，说明改善被别的方差吃掉了。

## 第三步：分段定位（RN 必修）

**所有通用手段都拿不到 RN 内部阶段**，必须自建埋点：

```
进程创建 → 最早 +load → didFinishLaunching
  → ReactInstanceManager 初始化 → bundle 加载/解析
  → Hermes 字节码加载 → 根组件挂载 → 首屏渲染 → 业务初始化
```

⚠️ **观察者选型有坑**：iOS 13+ 用 `kCFRunLoopBeforeTimers` 更准；
iOS 13 以下用 `CFRunLoopPerformBlock` 注入 block 更准。选错会得到偏差很大的数据。

**分段的作用是找到"时间花在哪一段"**，没有分段就只能在 `main()` 前后瞎猜。

## 第四步：根因清单（按性价比排序）

带着这份清单去读代码，比盲目 profile 更快命中：

### Main 之前（dyld 阶段）

- [ ] **动态库数量**：Apple 建议 < 6 个。能转静态就转，不链接用不到的库
- [ ] **`+load` 和静态初始化**：是否在做重活？能否迁到编译期或首次调用时
- [ ] **二进制重排**：`ld -order_file`，抖音方案覆盖 ~90% 符号
- [ ] **Page In 耗时**：用 ld 的 `rename_section` 把 `__cstring`/`__objc_methname`
      移到 `__RODATA`，减少 App Store 加密段的解密开销
- [ ] ⚠️ **不要删 `tmp/com.apple.dyld`** —— 存 iOS 13+ 启动闭包，删了冷启动变慢

### Main 之后

- [ ] **三方 SDK 初始化**：能否延迟？（抖音下线 Fabric 后 pct50 快约 70ms）
- [ ] **高频方法**：如反复读 Info.plist 配置 → 加内存缓存
- [ ] **隐藏的全局锁**：`UIImage imageNamed` 会触发 `dlopen` 等待 dyld 全局锁
- [ ] **线程数量与 QoS**：并发不宜过多，用 QoS 配优先级
- [ ] **图片**：用 Asset 而非直接放 bundle；提前在子线程预加载
- [ ] ⚠️ **Fishhook 首次调用耗时极高**（大型 App 冷启 200ms+），**不要带到线上**
- [ ] **首屏渲染**：Lottie 先显示静态帧；loading 动画别用 gif（60帧 gif 近 70ms）

## 第五步：优化四步法（严格按顺序）

**删 → 延迟 → 并发 → 更快**

1. **删**：这段代码能不能不执行？（下线无用功能、移除不用的库）
2. **延迟**：能不能不在启动路径上？（懒加载、首次使用时初始化）
3. **并发**：能不能并行？（但注意线程爆炸反而更慢）
4. **更快**：以上都不行，才考虑优化算法本身

> 这个顺序很重要。绝大多数团队一上来就在做第 4 步（微优化），
> 而收益最大的往往是第 1、2 步。**先删再优化。**

## 第六步：验证（不许偷懒）

```bash
python3 ".claude/skills/_apm/scripts/apm_baseline.py" compare \
  --baseline .apm/baseline/startup.json --run .apm/runs/<本次>/metrics.json
```

**必须确认**：
- 口径与基线完全一致（同设备、同构建类型、同命令）
- 提升幅度**超出噪声范围**（脚本会做置换检验并给 p 值）
- **功能没被改坏** —— 跑 `apm-autotest` 回归

⚠️ 如果基线用模拟器、验证用真机，或基线 debug、验证 release，
**测出来的差异里混了口径差异，结论无效**。脚本会检测并警告上下文不一致。

## 防劣化（单次优化会被侵蚀）

- 把指标写入 CI 门禁
- **准入项**（借自抖音实践）：新增动态库、新增 `+load`/静态初始化、新增启动任务
  必须 Code Review
- 线上用 **pct50** 为主指标 + AB 实验验证

## 常见误判

| 误判 | 真相 |
|---|---|
| "模拟器上快了 200ms" | 模拟器性能特征与真机完全不同，不能代表线上 |
| "debug 构建对比也是这个结论" | debug 有大量额外开销，会掩盖真实差异 |
| "只改了启动代码，不用跑功能测试" | 启动改动常影响初始化顺序，很容易改坏功能 |
| "predicted 能提升 30%" | **没有实测就没有预计**。不许报预计值 |
