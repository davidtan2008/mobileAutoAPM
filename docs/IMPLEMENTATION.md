# 核心功能实现细节

> 每节讲清两件事：**怎么实现的** + **为什么这样设计**。
> 只写「读了代码也未必能推断出原因」的部分。

---

## 0. 一条贯穿全局的原则：确定性任务交给脚本

**为什么不让模型每次现写。**

数据平面的六个脚本（`plugins/mobile-apm/scripts/`）全部是**零第三方依赖的 Python**，
每个都能独立运行、有测试、结果可复现。

理由：
- 让模型每次现写一遍「解析 trace」「算显著性」的逻辑，**结果不可复现** —— 同样的输入可能得到不同的处理
- 脚本的执行只有**输出进 context**，代码本身不进 —— 这是最省 token 的形态
- 脚本可以被 CI 直接调用，模型生成的临时代码不能

**判断标准**：这个动作需要「判断」还是只需「计算」？判断留给模型，计算交给脚本。

---

## 1. `apm_doctor.py` —— 能力探测

### 核心设计：**不假设工具存在**

每个任务开始前先探测，而不是假设 `xctrace` / `adb` 存在然后失败。

```python
# 每个工具声明：用途、适用平台、是否必需、安装方式
Tool("hdc", "hdc", "鸿蒙设备调试桥（等价于 adb）", ["harmony"], True,
     "DevEco Studio 自带")
```

探测结果聚合成**能力就绪度**（而不是工具清单）—— 因为 Agent 关心的是「我能不能做这件事」：

```python
CAPABILITIES = {
    "启动耗时检测": ["mobilebuildmcp", "xctrace", "xcresulttool", "python3"],
    "鸿蒙专项":     ["hvigorw", "ohpm", "hdc"],
}
```

### 三个踩过的坑

| 坑 | 表现 | 修法 |
|---|---|---|
| `xcrun` 包装的命令探测不到 | `xctrace` 不是独立可执行文件 | 用 `xcrun xctrace version` 探测，并区分 `is_xcrun_wrapper` |
| `--version` 可能挂起 | 某些工具有交互式帮助 | `subprocess.run(timeout=8)` |
| 返回 0 与「没有数据」混淆 | `premain_millis()` 返回 0 被误读成「极快」 | **取不到时返回 -1**，显式区分 |

---

## 2. `apm_baseline.py` —— 显著性判定

这是整个平台**最核心的一个脚本**：它决定 Agent 能不能诚实地回答「改动到底有没有效果」。

### 2.1 为什么用置换检验而不是 t 检验

性能数据常有长尾、不服从正态分布；样本量又小（n=3~10）。
t 检验的正态假设在这里不成立，置换检验**不依赖分布假设**。

```python
def perm_test(a, b, iters=10000):
    obs = abs(statistics.mean(a) - statistics.mean(b))
    pooled = list(a) + list(b)
    n = len(a)
    rng = random.Random(42)          # 固定种子 —— 结果必须可复现
    hits = 0
    for _ in range(iters):
        rng.shuffle(pooled)
        if abs(statistics.mean(pooled[:n]) - statistics.mean(pooled[n:])) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)   # +1 避免 p=0
```

### 2.2 检验统计量用**均值差**，不用中位数差

这是一个**实测修正**。最初用中位数差，问题是在 n=5 时分辨率极低：

| 统计量 | 清晰改善（−20%）测得的 p |
|---|---|
| 中位数差 | **0.0465**（贴着 0.05 边缘，差点漏判） |
| 均值差 | **0.0088** |

原因：n=5 时中位数只取第 3 个排序值，分辨率粗；均值利用全部样本。
**展示层仍报 p50/p90**（行业口径），但检验统计量用均值 —— 两者分工不同。

### 2.3 双阈值噪声带

```python
def noise_band(a, b, min_effect, k=2.0):
    return max(min_effect, k * pooled_stdev(a, b))
```

**两道阈值必须同时满足**：

- `k × pooled_stdev` —— **统计下限**：幅度必须跑赢测量抖动本身
- `min_effect` —— **实用下限**：不追「真实但无意义」的收益

只做前者 → Agent 会为 0.3ms 的「显著提升」去重构代码；
只做后者 → 会被噪声骗。

**`min_effect` 可以是绝对值**（指标自行声明 `minEffect`），因为不同指标的有意义最小变化差一个量级：

```json
{"name": "render.fps",  "minEffect": 2,       "unit": "fps"}
{"name": "memory.peak", "minEffect": 2097152, "unit": "bytes"}
```

未声明时退化为「基线的 5%」这一百分比口径。

### 2.4 实测行为

| 场景 | 变化 | 噪声带 | 判定 |
|---|---|---|---|
| 大幅改善 | −300ms | 75ms | ✅ 改善 |
| **小改善（p=0.0088 显著）** | **−68ms** | **75ms** | ➖ **不具实际意义** |
| 劣化 | +295ms | 75ms | ❌ 劣化（退出码 2） |

第 2 行是关键：**统计显著 ≠ 值得改**。这一点让 Agent 不会把噪声包装成成果。

---

## 3. `apm_white_screen.py` —— 白屏检测

### 3.1 为什么自己写 PNG 解码器

**为了零依赖。** 这个脚本要能在任何 macOS 机器上直接跑，不能要求 `pip install Pillow`。

纯标准库实现（`zlib` + `struct`）：

```
PNG 结构：signature → IHDR → [PLTE] → IDAT* → IEND
解码流程：拼接 IDAT → zlib.decompress → 逐行反滤波 → 按颜色类型取 RGB
```

支持 8/16 位深与灰度/调色板/RGB/RGBA 五种颜色类型。非 PNG 输入自动用系统自带的 `sips` 归一化。

### 3.2 三重证据，避免单一阈值误判

```
blank = 边缘密度低  AND  亮度标准差低  AND  主色占比高
```

**为什么必须三个条件同时成立**：纯色设计的页面（如品牌色背景）主色占比会高达 100%，
但真实页面总有文字/图标边缘，边缘密度会把它救回来。

实测验证：一张 93% 是白色的页面（模拟白底内容页），亮度标准差 48.52 远超阈值 → 正确判为「有内容」。

### 3.3 用真实截图验证过

用模拟器实拍截图（1206×2622，2.9MB，63010 种颜色）端到端跑通 —— 不只是合成图。

---

## 4. `rn_symbolicate.py` —— React Native 堆栈符号化

### 4.1 纯标准库实现 Base64 VLQ

sourcemap 的 `mappings` 字段是 VLQ 编码，需要自己解：

```python
def decode_vlq(segment):
    # 每字符 6 bit：低 5 位数据，第 6 位(0x20)续位；数值最低位是符号位
    ...
```

用 sourcemap 规范里的已知值做测试（`"A"`→0、`"C"`→1、`"D"`→−1、`"gB"`→16），
保证实现没偏。

### 4.2 核心：Hermes 的两步合成

Hermes 的栈长这样：`p@1:132161` —— **这不是 JS 行号，是字节码位置**。

```
.hbc.map   （字节码 → bundle JS）
    ↓ compose
metro.map  （bundle JS → 原始 TS/JS）
```

**只上传 metro.map 是不够的** —— 字节码位置在它里面查不到。这是 RN 崩溃分析最经典的坑。

合成实现要点：**按位置查询**，不按文件名匹配 ——
外层引用 bundle 时常用相对路径、内层可能用绝对路径，按名字匹配会大面积失败。

### 4.3 两个诚实的判定

**① 失配的可靠信号是「目标行上没有映射」**，而不是「命中距离远」。
我最初想拍一个列距离阈值，但那是在猜 —— 稀疏映射也会产生大距离。
所以：行上无映射 → 判定未命中；行内有映射但距离大 → **给位置 + 告警「请核对 map 是否为本次构建」**，不擅自判失败。

**② 原生帧不算「未还原」**。带 `at objc_msgSend (native)` 的堆栈每次都返回失败退出码，
会让 CI 门禁永远为红、最终被忽略。原生帧本就该由 dSYM 处理，已单独跳过并说明。

---

## 5. `rn_build_symbols.py` —— 符号化门禁

### 内置的坑位知识

不是写在文档里让人自己看，而是**编进判定逻辑**：

| 场景 | 工具的反应 |
|---|---|
| iOS 缺 sourcemap | 直接打印 Xcode 打包脚本的修法（默认不生成，需导出 `SOURCEMAP_FILE`） |
| 发现 0 字节 `.map` | 说明「Hermes 占位属正常现象」，但「全是 0 字节说明根本没生成」 |
| 生成清单 | 主动警告「建议用 debug ID 而非版本号」（热修后版本号会错位） |

退出码 `2` = 必需符号文件缺失，可直接作为发版门禁。

---

## 6. `ai_readiness.py` —— AI 友好度扫描

### 设计原则：可判定，不是口号

5 个维度、20+ 检查项，**每条都给出「依据 / 后果 / 修法」三件套**：

```python
Finding(
    key="no-tests",
    title="未发现任何测试文件",
    severity="blocker",
    evidence="遍历源码未匹配到测试文件",      # 实际查到什么
    impact="**Agent 无法验证自己的改动**",    # 不修的后果
    fix="至少给核心逻辑加测试")               # 怎么修
```

评分只是让人一眼看到差距，**输出重点是按影响排序的待改进项**。

### 6 个被测试抓出的真实缺陷

这些全是误报或漏报 —— **一个会误报的检查器会教用户学错东西**：

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | 架构文档只匹配以 `arch` 开头的文件名，漏掉 `13-architecture-analysis.md` | 改用 `.*arch.*\.md` |
| 2 | 多模块仓库根目录无标志文件 → 识别为「未识别」 | 下探一层子目录 |
| 3 | 扫描大仓库时文件句柄未关闭 | `with open(...)` |
| 4 | **「含 iOS SDK 的平台仓库」被套用应用级检查** | 引入 `root_kinds` 区分「根目录就是该类型」与「子目录含有」 |
| 5 | 只认 jest/vitest，把用 `node --test` 的 74 个测试误报成「无测试框架」 | 扩展到 6 种框架 |
| 6 | RN 的 TypeScript 检查只看 `dependencies`，不看 `devDependencies` | 合并两个字段再查 |

第 4 条最典型：**「包含一个 iOS SDK 的仓库」不是「一个 iOS 应用」** ——
不加区分就会报「签名未配置」「没有测试 target」这类无关问题，让分数失去指导意义。

---

## 7. `ios-apm` —— pre-main 打点

### 为什么必须用 C 构造函数

任何 Swift 侧埋点**最早只能在 `App.init()` 生效**。而在那之前，dyld 加载镜像、
运行静态构造、Swift 运行时初始化都已发生 —— **这段在纯 Swift 埋点里是完全的盲区**。

实测案例（一个真实 iOS 工程）：

```
纯 Swift 埋点测得「启动 219ms」
  ↓ 补上 C 构造函数
发现其中 219ms 全部发生在埋点被触碰之前 —— 整段启动都没有归因
```

根因：`@State private var x = SomeService()` 这类**属性初始化器先于 `init()` 执行**，
而埋点直到 `init()` 才被首次触碰。**埋点把最大的一块成本藏起来了。**

```c
__attribute__((constructor))
static void iosapm_early_launch_mark(void) {
    // 由 dyld 在任何 Swift 代码之前执行
    int premain = iosapm_premain_millis();
    os_log(...);
}
```

刻意用**纯 C + os_log**：不需要 bridging header，拖入文件即生效，不动工程配置。

### 两个细节

| 细节 | 理由 |
|---|---|
| `premain_millis()` 取不到返回 **−1**，不是 0 | 返回 0 会被误读成「pre-main 极快」——这是危险的假数据 |
| SwiftPM 不支持混合语言 target | 因此拆成 `IOSAPMEarlyMark`（C）+ `IOSAPM`（Swift）两个 target |

---

## 8. `rn-apm` —— 崩溃四层捕获

### 8.1 为什么是四层

RN 的崩溃分四类，**每一类只有一种机制能捕获，缺一层就是一类盲区**：

| 层 | 覆盖 | 缺了会怎样 |
|---|---|---|
| ErrorBoundary | 同步渲染/生命周期 | React 渲染期错误直接整树卸载，无记录 |
| JS 全局 | 未处理 JS 异常 | 异步 JS 错误全部丢失 |
| Promise | 未处理 rejection | **最大盲区** |
| Native | 原生崩溃 | 全丢 |

### 8.2 必须链式，不能替换

`ErrorUtils.setGlobalHandler` 是**全局单例**，Sentry/Bugly 等 SDK 也会设置。
直接覆盖会**打断别人**，被别人覆盖会**丢自己的数据**。

```swift
const prev = errorUtils.getGlobalHandler?.();
const handler = (e, isFatal) => {
    emit(...);                       // 先记录我们的
    try { prev?.(e, isFatal) }       // 再调用前一个 —— 绝不吞掉
    catch { /* 别人的 handler 出错不该影响我们 */ }
};
```

卸载时恢复前一个 handler。测试覆盖了「前一个 handler 仍被调用」与「别人抛错不影响我们」。

### 8.3 崩溃数据必须**立即落盘**

```
❌ 先放进 collector 缓冲，之后再统一上报
   → 崩溃后进程可能马上死掉，缓冲里的数据一起消失 —— 而那正是最需要的数据
   → 崩溃风暴时缓冲会被覆盖，早期崩溃会丢
```

这个问题是**测试抓出来的**：`reportErrorBoundaryError` 只写进了环形缓冲，
测试断言 `queueSize === 1` 失败。修复后所有崩溃路径统一走「记录 + 立即持久化」。

### 8.4 「环形缓冲定容」不是小事

内存水位采样用定容环形缓冲。**一个用来发现内存泄漏的工具，自己绝不能泄漏内存。**
测试断言：10 万次 `push` 后元素个数恒定在容量上。

---

## 9. 跨 Agent 适配

### 9.1 手写两份必然漂移

```
plugins/mobile-apm/（唯一真源）
        ↓ tools/build-portable.py
  .claude/          .opencode/
```

改了 Claude 侧忘了改 opencode 侧，是这类项目最常见的腐化方式。

### 9.2 三个会致命的兼容点

| # | 兼容点 | 处理 |
|---|---|---|
| 1 | opencode 要求 `name` 与 `description` **同时存在**，缺一个该 skill **完全不加载** | 生成前校验 |
| 2 | `${CLAUDE_PLUGIN_ROOT}` 在 opencode 里是**字面量** | 重写为工程根相对路径 |
| 3 | opencode 无 settings hooks | 生成 `.opencode/plugins/apm-hook-bridge.ts`，把 `.claude/hooks/*.sh` 当子进程调 |

第 3 条的思路：**一份 hook 逻辑服务两个 harness**，而不是各写一份。

生成后自动检查是否还有 `CLAUDE_*` 残留，有则报错退出。

---

## 10. 设计决策速查

| 决策 | 理由 |
|---|---|
| 数据平面零第三方依赖 | 任何机器上都能直接跑，不需要 pip install |
| 取不到值返回 −1 而非 0 | 显式区分「没有数据」与「数据是 0」 |
| 所有缓冲定容 | 测内存泄漏的工具自己不能泄漏 |
| 埋点绝不抛错、绝不阻塞 | 不能因为自身失败而影响被观测对象 |
| 崩溃数据立即落盘 | 进程可能马上死掉 |
| 未标首屏时拒绝出启动报告 | 宁可不报，不报一个不完整的 |
| 失配时告警但不擅自判失败 | 拍一个阈值等于在猜 |
| 单一真源 + 生成器 | 手写多份必然漂移 |
| 图表 SVG + PNG 双份并加门禁 | PNG 落后于 SVG 会让读者看到过期结论 |
