# 核心功能实现细节

> 每节讲清两件事：**怎么实现的** + **为什么这样设计**。
> 只写「读了代码也未必能推断出原因」的部分。

---

## 0. 一条贯穿全局的原则：确定性任务交给脚本

**为什么不让模型每次现写。**

数据平面的十一个脚本（`plugins/mobile-apm/scripts/`）全部是**零第三方依赖的 Python**，
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

## 2.5 `apm_measure.py` —— 参数化测量 profile

### 为什么不是把 T1 的 shell 脚本原样复制

T1 的 `measure-launch.sh` 证明了测量缺口，但它把 UDID、包名、构建路径硬编码在
被观测工程里，并使用 macOS 默认没有的 GNU `timeout`，还会按已被证伪的
`pre-main` 假设自动分层。平台不能把这些问题复制给下一个用户。

第一版 profile 因此只承诺一个明确边界：`ios-native-startup`。它：

- 通过 `devicectl` 解析并硬校验 physical device、UDID、系统状态；等待 CoreDevice tunnel `connected` 后才开始安装/预热/采样；
- 校验 `.app/Info.plist` 的 bundle id 与参数一致；
- 用 Python 自己的进程超时与有界日志读取，不依赖 GNU `timeout`；
- 保存每次的 total、canonical pre-main、完整 stages、premain candidates 和 raw log；
- 用 `firstStage.since + 后续 delta` 校验时间闭合，不假设某个 SDK 的第一段 delta
  一定是 0 或包含 pre-main；
- 缺 pre-main、缺终点、阶段不闭合时写 invalid，**不写 0**；
- `--warmup-launches` 明确记录安装后的预热次数；预热不计入样本，改变次数会改变 measurement signature，不能混比；
- 采集结束强制调用 `apm_diagnose.py`，诊断不可信时退出 2。

Android / 鸿蒙 / RN 的 profile 尚未落地，skill 会明确报告不可用，不用 iOS profile
冒充跨平台能力。

## 2.6 `apm_diagnose.py` —— 方差诊断

诊断器读取规范化 `metrics.json`，也能读取 T1 风格的 `raw/stages.txt`。它做三件
确定性的事：

1. 计算每个连续指标的 n、p50、mean、CV；
2. 用可解释的「排序后大间隙」启发式标记疑似多簇，并递归检查分解段；
3. 对有逐次配对的数据计算 Pearson/Spearman 相关性，若跨运行方向相反则明确
   标记为不可自动分层。

多簇检测是**诊断启发式，不是正式模态检验**；工具不会挑选快簇、删除慢簇或做
post-hoc 校正。诊断还会比较**同一 commit 的独立 run** 的焦点中位数：超过
`max(minEffect, 2×组内最大标准差)` 就标记 `shift_detected`。不同 commit 的
baseline A/B 不参与这项重复性判定。`apm_baseline.py` 已把高方差/多簇/跨 run
漂移/口径错误变成数据错误退出码 1。

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
本次 iOS 26.4 模拟器在启动后的 0.1s、0.3s、0.6s、1s、2s、4s 抓取 6 帧，
`apm_white_screen.py` 全部判定为 `content_present`，没有复现白屏。

### 3.4 旧 screenshotr 后端边界

iPhone 13 真机上 `idevicescreenshot` 返回
`Could not start screenshotr service: Invalid service`；这条旧后端仍标记为不可用。
但 `apm_screenshot.py` 已验证可选 `pymobiledevice3` DVT 后端可以在同一台真机上
生成 1170×2532 PNG，因此真机白屏验证不再依赖旧 screenshotr 服务。

## 3.5 `apm_screenshot.py` —— iOS 真机截图

白屏分析器只接受图片，不负责采集。`apm_screenshot.py` 补上 iOS 真机采集层：

- 先用 `devicectl` 的结构化设备信息硬校验 `physical`，模拟器不能冒充真机；
- 优先调用可选的 `pymobiledevice3 developer dvt screenshot --native`；
- DVT 不可用时回退 usbmux 模式和 `idevicescreenshot`；
- 校验后端确实写出了非空 PNG/JPEG/TIFF，并记录后端、设备、尺寸和失败原因；
- `pymobiledevice3` 不是运行时依赖，没安装时返回 `unavailable`，不静默降级。

标准用法：

```bash
python3 scripts/apm_screenshot.py \
  --device "<真机 UDID>" --output .apm/white-screen/shot.png \
  --pymobiledevice3-bin /path/to/venv/bin/pymobiledevice3 --json
```

## 3.6 `apm_feasibility.py` —— 目标可行性与对照组闸门

T1 的 200ms 目标是在做完优化后才发现空壳对照组的地板约 230ms。P1 把这个教训
前置成确定性判断：

- `plan` 输出最小 control 实验协议（只改变业务内容，保留生命周期与埋点）；
- `check` 比较 control 与 candidate 的指标分布、方向、context 和 measurement signature；
- 目标低于 control 中位数地板时返回退出码 `2`，要求停止局部优化并升级架构决策；
- 目标落在测量余量内时返回不可判定，要求补样本而不是猜；
- control/candidate 任一质量不可信或口径不同，都拒绝判断。

```bash
python3 scripts/apm_feasibility.py plan \
  --metric startup.cold.first_frame --target 200 --json

python3 scripts/apm_feasibility.py check \
  --control .apm/runs/control/metrics.json \
  --candidate .apm/runs/candidate/metrics.json \
  --metric startup.cold.first_frame --target 200 --json
```

已在 iPhone 13 / iOS 26.7 上真实验证：DVT 后端生成 1170×2532 PNG，随后
`apm_white_screen.py` 正确判定为 `content_present`；P1 的 control/candidate 判定
则由离线 fixture 回归覆盖，真实 control run 仍需在目标工程中执行。

## 3.7 `ai_remediate.py` —— 支柱 A 的「扫描 → 改造 → 复扫」闭环

`ai_readiness.py` 只回答「哪里不友好」。但本项目的铁律之一是**无基线不优化** ——
只给建议、不做改造，效果就无法量化、也无法验证。`ai_remediate.py` 补上后半段。

### 三条不可让步的约束

| 约束 | 实现 | 依据 |
|---|---|---|
| **绝不编造事实** | 生成物里凡是「只有人知道」的位置一律写 `TODO(需人工填写)` | LLM 生成的 context 文件实测成功率 **−3%**、成本 **+20%**；编造内容比留空更糟 |
| **默认不落盘** | 生成物只写 staging 目录；`apply` 需显式调用，且**拒绝覆盖已存在文件** | 骨架直接覆盖真实文件会丢内容 |
| **效果要实测** | `loop` 把工程复制到临时目录 → 应用生成物 → 复扫，给出真实 before/after | 「量化提升」不能是声称 |

### 四段式命令

```bash
python3 ai_remediate.py plan     --path <项目>   # 只看计划
python3 ai_remediate.py generate --path <项目> --out .apm/remediation
python3 ai_remediate.py loop     --path <项目>   # 量化
python3 ai_remediate.py apply    --path <项目> --staging .apm/remediation
```

### 真实项目上的量化结果

在 iOS 工程（`realtimeTranslatorOptimize`）上跑 `loop`：

```text
改造前 68 → 改造后 86（Δ +18）
消除：no-agents-md / no-ci / no-lint
仍需人工：no-cmd-in-readme（README 缺可复制命令）
appliedToRealProject: false
```

### 明确不自动做的事

`NEEDS_HUMAN` 表把 15 类 finding 钉死为「只能人工处理」，并给出理由。
其中最关键的两条：

- `no-tests` —— **不能凭空生成测试**，造出来的只会制造虚假安全感；
- `ios-no-team` —— 签名 Team ID 是账号信息，机器上不可能知道。

写测试时这两条都被做成了回归用例：`test_不能自动改造的项只报给人`、
`test_生成物里不出现未被检测到的命令`、`test_生成物不含别的项目的目录名`
（后者防的是「把某个具体项目的目录名硬编码进通用模板」这种真实踩过的坑）。

---

## 3.8 `verify_symbol_pipeline.py` —— 符号化流水线的端到端自检

### 为什么单元测试不够

`rn_symbolicate.py` 的测试用的是**简写**堆栈：

```text
anonymous@1:999
```

而真实 Hermes 运行时输出是：

```text
at anonymous (address at /abs/path/app.hbc:1:49386)
```

路径里有空格与冒号，`_RE_HERMES` 匹配失败 → 帧被误判成普通
`file:line:column`，`file` 变成 `"address at /path"` 这种假路径 →
**20/20 帧全部还原失败，而所有测试依然全绿**。

这就是「无基线不优化」在工具上的同款问题：**没有真实输入，就没有真实结论。**

### 端到端链路

```bash
make verify-symbols RN_APP=/path/to/rn-app
```

依次执行（全部用真实工具链，零合成 fixture）：

| 步 | 命令 | 实测产出 |
|---|---|---|
| 1 | `react-native bundle --dev false` | bundle 1.47 MB / metro map 5.73 MB |
| 2 | `hermesc -emit-binary -O -output-source-map` | HBC 1.32 MB / hbc.map 1.15 MB |
| 3 | `rn_symbolicate.py compose` | 186,386 条映射 / 891 源文件 / 6,433 函数名 |
| 4 | `hermes` CLI 执行 HBC | 真实堆栈 20 帧，20 帧带字节码偏移 |
| 5 | `rn_symbolicate.py symbolicate` | **20/20 帧 100% 还原** |

整条链路本地 **4.6 秒**，因此可以放进 CI 每次跑。

### 两个必须照抄 RN 的细节

```bash
# ❌ 错误：给 -output-source-map 传值
#    → Multiple files must use CommonJS modules.
# ✅ 与 react-native-xcode.sh 完全一致：flag 不带值，Hermes 按 -out 推导 map 名
hermesc -emit-binary -max-diagnostic-width=80 -O -output-source-map \
        -out app.hbc app.js     # 产出 app.hbc.map
```

另外：喂给 `hermes` CLI 执行的文件**必须是 `.hbc` 后缀**，
否则 Hermes 会把字节码当 UTF-8 源码解析，报一堆 `Invalid UTF-8 continuation byte`。

### CI 接入

`.github/workflows/ci.yml` 增加独立 job `symbol-pipeline`：
用 `@react-native-community/cli init` 生成真实 RN 0.73.4 工程 →
`npm install` → 写一个必然抛错的入口 → 跑端到端自检。

**不用自己造 fixture**：造出来的输入验证不了真实格式，这正是本节要修的缺陷。

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
