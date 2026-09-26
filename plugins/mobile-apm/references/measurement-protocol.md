# 测量协议

> **这份文件的存在理由**：平台反复强调「口径必须一致」「方差大就不可信」，
> 但如果没有**可执行的协议**，这两句话就只是口号。
>
> 本文来自一次真实的失败演练 —— 目标「冷启动压到 200ms」，
> 结果发现**测量的方差（2 倍）比要优化的幅度（~50ms）还大**，
> 导致任何优化都无法验证。**先把测量修好，再谈优化。**

---

## 一、第一原则：方差大于幅度时，一切优化都不可验证

**动手优化之前，先回答一个问题：**

> 我打算优化的幅度，比测量的离散度大吗？

如果答案是「不确定」或「差不多」——**停下来修测量**。
在一个方差 200ms 的指标上验证 50ms 的优化，无论得到什么结论都是噪声。

### 用 CV 判断测量是否可用

```
CV = 标准差 / 均值
```

| CV | 判断 | 该怎么做 |
|---|---|---|
| < 10% | 可用 | 可以支撑小幅度优化（几十 ms） |
| 10–30% | 勉强 | 只能验证大幅优化；建议先降方差 |
| > 30% | **不可用** | **停止优化，先修测量** |

`apm_baseline.py` 会在 CV > 30% 时主动告警。

---

## 二、冷启动测量的已知方差源

### 方差源 1：进程启动环境冷热（**最大、最难控**）

**实测证据**（iPhone 13 / iOS 26.7 / Release）：

```
样本: 279 278 302 | 491 515 541     ← 明显两簇，相差近 2 倍
```

且与 `pre-main`（进程创建 → 首个构造函数）同向变化。

**⚠️ 但它不是可用的分层变量。** 另一个数据集中趋势**完全相反**：

```
(28,311) (16,450) (27,312) (15,314) (12,431)
 fast 组反而更慢
```

**结论：`pre-main` 与总耗时只是巧合相关，不能用来分层校正。**

这个方差源的候选成因（**均未验证**）：
- iOS app prewarming
- 页缓存 / dyld closure 状态
- 测量脚本自身的干扰（日志流客户端、启动间隔）

### 方差源 2：设备状态

- **发热降频** —— 实测中连续测量的后期样本会系统性变慢
- **后台任务** —— 系统同步、推送、其他 App 活动
- **电量状态** —— 低电量模式会显著影响性能

### 方差源 3：测量脚本自身

- 日志流客户端（`idevicesyslog`）在设备上持续读取日志，可能带来负载
- **启动间隔太短** —— 前一次启动的残留（内存压力、后台清理）会影响下一次

---

## 三、降低方差的实践

### 3.1 必做

| 做法 | 理由 |
|---|---|
| **固定设备、固定构建类型** | 换设备/换构建，数据不可比 |
| **n ≥ 5**，取中位数 | 均值会被离群值拖动 |
| **测量间固定间隔**（建议 ≥ 5s） | 让系统状态回落 |
| **设备降温** | 发热会降频，直接污染数据 |
| **关后台 App / 飞行模式** | 消除无关负载 |
| **每次测量前重启 App 进程** | `--terminate-existing`，保证是冷启动 |
| **明确记录并固定 warmup 次数** | 安装后的首次系统/应用状态可能比后续启动慢；warmup 不计入样本，改变次数必须改变测量口径 |

### 3.2 当方差仍然很大时

**不要硬测。** 按这个顺序排查：

1. **增大样本量**（n=15~20），看中位数是否收敛
   - 收敛 → 只是样本不够
   - 不收敛 → 有系统性因素，继续查
2. **改变测量方式**：
   - 换成**对目标更敏感、对方差更鲁棒**的指标（见 §4）
   - 用 `xcrun xctrace` 的 App Launch 模板（Apple 自己的口径，若设备可用）
3. **记录并分层**：把可疑变量（`pre-main`、首次/非首次、[时间]）一并记录，
   事后分析相关性 —— **但相关系数不等于因果，必须用对照实验验证**

### 3.3 如果修不好测量

**如实报告，而不是硬给结论。**

正确的说法：

> 当前测量方差（CV 30%）大于要验证的优化幅度（~50ms），
> **本次无法判定优化是否有效**。需要先解决测量问题。

错误但很诱人的说法：

> 从 314ms 优化到 279ms，提升 11%。

（后者是**挑了快模式的样本**得到的 —— 见 §6 反模式）

---

## 四、当总指标不可用时：改测可分解的指标

**这是本次演练最有用的产出。**

冷启动总耗时的方差很大，但它**分解后的各段稳定性差异极大**：

| 指标 | CV | 可用性 |
|---|---|---|
| 总耗时（first-frame） | **19–30%** | ❌ 不可用于小幅度优化 |
| **可优化段之和**（body 求值 + 首帧提交路径） | **8–10%** | ✅ 可用 |

**做法**：把总指标分解，选**方差小、且优化真正作用的那几段**做判定。

```bash
# 从阶段拆分里提取可优化段（示例）
# rootBody + homeBody + rootAppear + firstFrame 的 delta 之和
```

**前提**：分解指标必须来自**同一次测量**，且口径与基线一致。

⚠️ **注意**：分解指标可用 ≠ 总指标改善了。如果分解指标没动，
总指标也不会因为你的改动而改善。**两者要一起看**。

---

## 五、真机 UI 自动化：先预热，别把环境问题当代码问题

> 实测于 iPhone 13 / iOS 26.7 / Xcode 26.4.1 / **网络配对无 USB**。

直接跑真机 UI 测试会以两种形态失败，**都不是代码问题**：

```text
① Early unexpected exit ... exited with code 74
   [DTXConnection] Connection peer refused channel request for
     "dtxproxy:XCTestDriverInterface:XCTestManager_IDEInterface"
   [Default] Exiting due to IDE disconnection.

② The test runner failed to initialize for UI testing.
   (Underlying Error: Timed out while enabling automation mode.)
```

### 判别方法：先只跑单元测试

同设备、同会话下跑一次设备侧单元测试：

- **通过** → `testmanagerd` 本身健康，缺的是 UI automation 通道的初始化时机；
- **也失败** → 才是签名 / 安装 / 连通性问题。

这一步能把「环境故障」和「代码故障」彻底分开，**不要跳过**。

### 修法：先预热，再跑 UI

```bash
# 1) 预热（故意选一组快而稳的单元测试）
mobilebuildmcp device test --device-id <DEV> \
  --json '{"extraArgs":["-only-testing:<UnitTarget>/<SomeTests>"]}'

# 2) 再跑 UI
mobilebuildmcp device test --device-id <DEV>
```

预热后 UI 立刻通过（`Setting up automation session` 从超时降到 ~3.7s）。

> **把它固化成脚本，不要靠口头约定。** 顺序一旦只存在于文档里，
> 迟早有人直接跑 UI 目标而重现 `exit 74`。

### 为什么它无法自愈

```text
xctrace list devices            → 该机列为 Devices Offline
~/Library/Developer/Xcode/DeviceSupport → 空（从未为该设备准备）
设备 iOS 26.7 (23H24)          > Xcode SDK 26.4 (23E252)
USB                            → 未连接
```

Xcode 需要**通过 USB 连接**才会为设备准备匹配的 DeviceSupport。
设备只有网络配对时，这一步做不了，于是 automation 通道起不来。
设备 OS 比 Xcode SDK 更新时尤其容易卡在这里。

**根治**：用 USB 连接设备并保持解锁，让 Xcode 完成 DeviceSupport 准备。

### 另一类假失败：`async` 用例不在主线程

```text
-[XCUIApplication _launchUsingXcode:...] must be called on the main thread
```

`async` 的 XCTest 方法不在主线程执行，而 `XCUIApplication.launch()` 强制要求主线程。

**注意盲区**：如果该用例在模拟器上被 skip（能力不足），skip 发生在 `launch()` **之前**，
这个缺陷在模拟器上完全不会暴露。**只在能跑通的那条路径上验证是不够的。**

---

## 六、反模式（这些都会让结论失真）

| 反模式 | 为什么错 |
|---|---|
| **挑样本**：从多峰数据里挑快的那簇报数 | 这是伪造。**数据里的 279ms 是环境好，不是你优化得好** |
| **只报最好的一次** | 单次样本不构成证据 |
| **用不同口径的基线** | 例：本轮用 sysctl 基准，基线用第三方快照时间戳 —— 差异里混了口径差异 |
| **改完立刻测** | 编译/安装的热状态会污染首次测量 |
| **在模拟器上验证真机优化** | 模拟器与真机失真方向相反（见 `stack-selection.md`） |
| **拿分解指标的改善当总指标的改善** | 分解指标动了但总指标没动，说明改善被别的方差吃掉了 |

---

## 七、一次规范的测量应该留下什么

按 `metrics-definitions.md` §6 的报告模板，**至少包含**：

```
- 口径：<起止点定义>
- 测量环境：<设备 / 系统 / 构建类型 / commit>
- 测量命令：<可复现>
- 样本：N=<次数>，CV=<离散度>
- 判定：<显著性 + 效果量 + 噪声带>
- 原始数据：<路径>
```

**缺任何一项，结论就不成立。**

---

## 八、平台已提供的保障（P0 实现与边界）

本次演练暴露的三个缺口已经落成第一版可执行能力。它们是**数据质量闸门**，
不是新的性能数字来源：

| # | 能力 | 入口 | 边界 |
|---|---|---|---|
| 1 | 参数化 iOS 原生冷启动测量 profile | `${CLAUDE_PLUGIN_ROOT}/scripts/apm_measure.py --profile ios-native-startup` | 当前只覆盖 iOS 原生；物理设备、`.app`、bundle id 都会校验；Android/鸿蒙/RN 适配器尚未宣称完成 |
| 2 | 方差诊断 | `${CLAUDE_PLUGIN_ROOT}/scripts/apm_diagnose.py <run>` | 多峰是启发式「疑似簇」，不是正式模态检验；不自动丢弃样本 |
| 3 | 判据建议 | 诊断文本 / `diagnosis.json` | 高方差或多峰时建议增加样本、控制变量、改测同次分解指标；建议不是性能结论 |

### 标准命令

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"

# 采集（iOS 原生 profile；至少 n=5，间隔默认 5s）
python3 "${S}/apm_measure.py" \
  --profile ios-native-startup \
  --device "<真机 UDID>" --package-id "<bundle id>" \
  --build-type Release --build-path "<绝对路径>/App.app" \
  --warmup-launches 1 \
  --project-root . --output .apm/runs/<本次>-launch

# 单独诊断（也可对 T1 旧 run 目录执行）
python3 "${S}/apm_diagnose.py" .apm/runs/<本次>-launch \
  --metric startup.cold.first_frame --effect-ms 50

# 诊断通过后才允许把启动基线写入锚点
python3 "${S}/apm_baseline.py" record \
  --in .apm/runs/<本次>-launch --out .apm/baseline/startup.json \
  --require-healthy
```

### 诊断输出怎么读

- `CV < 10%` 且没有疑似多簇：才可进入同口径比较；
- `CV 10–30%`：只能支撑大幅变化；
- `CV > 30%`、疑似多簇、缺阶段/缺 pre-main：退出码 `2`，停止优化；
- 同一变量在不同 run 中相关方向相反：只能作为控制实验假设，**禁止自动分层**；
- 分段指标变好但总指标没变：不能报告总启动改善；
- **同一 commit 的独立重复测量**还要比较 run 间焦点中位数漂移；超过
  `max(指标 minEffect, 2×组内最大标准差)` 时标记 `shift_detected`，不能直接记录基线；
- baseline 与候选 run **不同 commit** 时属于正常 A/B，不把预期代码差异误判为重复性漂移；
- `--warmup-launches` 是测量口径的一部分；它不计入样本，不能把不同 warmup 次数的 run 混比。

`metrics.json` 中的 `observations[]` 保留逐次配对关系；`premainMs` 取不到时
明确为 unavailable，绝不写 0。阶段日志由毫秒整数截断时，闭合校验使用
`max(5ms, stageCount + 2ms)` 的有界容差，并把实际舍入残差写入 warning；超出该上限仍拒绝样本。
当前实现已用 T1 归档数值做离线回归，并在 iPhone 13 真机完成多轮 Release 采集；
设备重启并解锁后，固定 warmup=3、n=10 的两次独立 run 为 p50=256ms/CV=9.6% 与
266ms/CV=7.1%，跨 run 诊断 `consistent`，已生成 provisional baseline；因工作树 dirty，
仍不是干净 commit 基线。
