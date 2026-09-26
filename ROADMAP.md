# 路线图

> **给接手的 AI 工程师**：这份文件告诉你项目在哪、下一步做什么、以及哪些线不能碰。
> 先读 [`AGENTS.md`](AGENTS.md)（项目约定），再读 [`ARCHITECTURE.md`](ARCHITECTURE.md)（架构），
> 然后是 [`docs/PROGRESS.md`](docs/PROGRESS.md)（完成度与已知限制）。

---

## ⏰ 先看这一条：开源发布前的前置条件

> **发布会话（HN / Reddit / V2EX）需要一个「目标达成且被验证过」的闭环演示。**
> 第一个成功案例已具备：结束任务的译文持久化修复（红测失败 → 单变量修复 →
> 模拟器 120/120 单测 + Release 构建 + **真机 123/123 全量套件**），
> 见 `docs/case-study-translation-persistence.md`。60–90 秒成片已渲染
> （`docs/demo/translation-persistence/translation-persistence.mp4`，59.0s / 5 页）。
>
> T1 的冷启动 200ms 题目已被最小 control（n=20，p50=210ms）拦截，**不能**用作发布素材。
>
> **当前真实顺序**：P0/P1 已落地 → 第一个确定性功能闭环已跑通 → demo 已渲染 →
> 人工审阅 → push → 发布。

---

## 一句话现状

**平台的能力骨架已经建好并有测试；核心承诺已有一次失败闭环（T1 冷启动）和一次成功闭环
（结束任务译文持久化）。** 下一步是复制成功路径，而不是加新功能。

---

## 已验证 vs 未验证

**这个区分比功能清单重要。** 项目全部价值建立在「不撒谎」上。

| 部分 | 强度 | 依据 |
|---|---|---|
| `rn-apm` 逻辑 | ✅ 74 个测试 | `cd rn-apm && npm test` |
| `ios-apm` 逻辑 | ✅ 11 个测试 | `cd ios-apm && swift test` |
| Python 工具 | ✅ 116 个测试 | `make test-py` |
| 指标闭环（SDK → 基线判定） | ✅ 跑通过 | — |
| 跨 agent 生成器 | ✅ 全新 clone 验证 | `make verify-portable` |
| **原生 shim** | 🟡 **iOS 侧已在真机验证**；Android / 鸿蒙仍未验证 | iPhone 13 / iOS 26.7 实测通过；发现并修复两个只有真跑才暴露的坑，见 `docs/evidence/native-shim/measured.json` |
| **符号化工具在真实构建中** | ✅ **端到端验证通过** | 真实 RN 工具链 + 真实 Hermes 堆栈，20/20 帧 100% 还原；发现并修复一个只有真跑才暴露的解析缺陷 |
| **自主闭环（核心承诺）** | ✅ **1 次成功 + 1 次诚实失败** | 译文持久化闭环见 `case-study-translation-persistence.md`（模拟器 120/120 单测 + Release 构建；后又在 iPhone 13 / iOS 26.7 真机跑通 123/123）；T1 启动闭环被 control 闸门拦截 |

---

## 唯一一次真实的闭环演练及其结论

**T1 · 目标「冷启动压到 200ms」**（被观测对象：一个真实 iOS 工程，iPhone 13 真机）

| 结论 | 证据 |
|---|---|
| ❌ **目标不可达** | 二分实验：把 App 内容换成 `Text("x")` 后，SwiftUI 场景引导耗时不变（152–190ms）→ **空壳 App 的地板就在 230ms** |
| ❌ **做的优化无效果** | 可优化段 126ms → 132ms，**p=0.694**，已回退 |
| ⚠️ **测量方法本身不成立** | 冷启动总耗时有一个未受控的 **~2 倍方差源**，比要优化的幅度（~50ms）还大 |

**这三点都是实测结论，不是推测。** 完整报告：被观测工程里的 `.apm/report.md`。

### 这次演练沉淀下来的平台资产

- **`references/measurement-protocol.md`** ← **新增**，把上述教训写成可执行协议
- 它同时记录了平台自己的三个缺口（见 §「下一件该做的事」）

---

## 下一件该做的事

### 🎯 P0 · 补齐测量能力（最优先）

**为什么最优先**：测量是闭环的地基。测量不可靠 → 「验证」环节失效 → 核心承诺崩塌。
而且**这不是某一个 App 的问题** —— 任何用户让平台优化冷启动都会撞上。

| # | 能力项 | 出口标准 | 当前 |
|---|---|---|---|
| 1 | **把测量脚本做成平台模板** —— 现在它写在被观测工程的 `.apm/` 里，不可复用 | 参数化设备/包名/构建路径，随插件分发；`make` 或 skill 能直接调用 | ✅ `apm_measure.py` 已随插件分发；clean commit `2b3a1ea` 上的**正式 baseline** 已记录（n=10 连续两 run 跨 run `consistent`） |
| 2 | **方差诊断** —— 现在只在 CV>30% 时告警，不帮定位成因 | 检测多峰、输出分段 CV 对比、提示可疑变量、检测同 commit 跨 run 漂移；诊断结果可读 | ✅ `apm_diagnose.py`，已用 T1 归档数据与多轮真机 run 回归 |
| 3 | **判据建议** —— 多峰时不知道该怎么办 | 检测到多峰/高方差时，主动建议「改测分解指标」并给出具体做法 | ✅ 已接入 `metrics.json` / `diagnosis.json` / baseline 闸门 |

参考 `references/measurement-protocol.md` §8。

**正式 baseline 的建立过程（iPhone 13 / iOS 26.7 / Release / warmup=3 / n=10）**：

| run | p50 | CV | 判定 |
|---|---|---|---|
| B | 261ms | 21.5% | ❌ `unusable_multimodal`（前 3 次 368–413ms）——**整 run 拒绝** |
| C | 252ms | 3.5% | ✅ `usable` |
| D | 257ms | 2.8% | ✅ `usable` |

C/D 跨 run `consistent`（Δp50=5ms，判定门槛约 30ms），
`apm_baseline compare` 返回 `no-significant-change`（退出码 0）。
B 的失败被完整保留在 `.apm/runs/`，**没有挑选后续样本**。

**本轮实现边界**：`apm_measure.py` 目前只承诺 `ios-native-startup`，不把尚未验证的
Android / 鸿蒙 / RN 适配器写成可用；`pre-main` 只作为成对观测与可疑变量，绝不自动分层。

### P1 · 把 T1 的结论做成能力

**状态：✅ 第一版已落地并完成一次真实验证。** `apm_feasibility.py`、
`feasibility-protocol.md` 与 `apm-loop` 的 Phase 1.5 已接入；在 clean commit
`2b3a1ea` 上用 n=20 最小 control 得到 p50=210ms，正式拦截 200ms 启动目标。

| # | 待实现 | 说明 |
|---|---|---|
| 4 | **可行性前置判断** | T1 的教训是「目标不可达却先干了活」。应在动工前先回答「这个目标在这个技术栈上可达吗」—— 例如先用最简对照组测出地板 | ✅ `apm_feasibility.py check`，含 context/质量闸门与退出码；已真实验证 |
| 5 | **对照组方法论** | T1 里「把内容换成 `Text("x")`」这个手法极其有效，应沉淀成标准动作 | ✅ `apm_feasibility.py plan` + `feasibility-protocol.md`；已真实验证 |
| 6 | **架构级改动的升级判据** | 当发现「需要换掉框架层才能达标」时，应停下来问人而不是硬做 | ✅ `apm-loop` 停止条件与升级判据已接入 |

### P2 · 支柱 A（项目 AI 化改造）

扫描器早已有（`ai_readiness.py`），**扫描 → 改造 → 复扫 的闭环已落地**（`ai_remediate.py`）。

| # | 项 | 状态 |
|---|---|---|
| 7 | 改造项生成（`AGENTS.md` / CI / lint 的最小可用版本） | ✅ 模板化生成，**事实位置一律留 `TODO(需人工填写)`**，不编造 |
| 8 | **人审环节** | ✅ 生成物只进 staging 目录；`apply` 需显式调用且**拒绝覆盖已存在文件** |
| 9 | 在一个真实遗留项目上跑通并量化提升 | ✅ iOS 工程实测 **68 → 86（+18）**，消除 `no-agents-md` / `no-ci` / `no-lint` |

```bash
make remediate                                              # 本仓的改造计划
python3 plugins/mobile-apm/scripts/ai_remediate.py loop --path <项目>   # 量化
```

**为什么坚持人审**：调研证据显示 LLM 生成的 context 文件 **成功率 −3% / 成本 +20%**。
所以本脚本只做「模板 + 事实抽取」的确定性改造，凡是超出这个范围的
（如「凭空生成测试」「填签名 Team ID」）一律标为 `needs_human`，**不假装能自动解决**。

### P3 · 自我进化

设计已在 `docs/diagrams/evolution.svg` 里，未实现。

**关键约束（有实证支撑，不能省）**：
- 准入必须是**写入前的闸门**（污染不可逆）
- active skill 有**硬上限**（202 个 skill 的库 → 性能下降最多 21%）
- 按**结果**淘汰，不按新旧
- **不要指望 LLM 生成 skill 有效**（LLM 自写 +0.0pp vs 人工精选 +16.2pp）

### P4 · 开源运营

- ⏰ **V2EX 注册**（发帖要求注册满 30 天，越早越好）
- ✅ **demo 视频已渲染** —— 成片 `docs/demo/translation-persistence/translation-persistence.mp4`
  （59.0s / 1920×1080 / 5 页，无音轨）；证据包 `docs/demo/translation-persistence/`；
  视频里每个数字都由 `tools/render_demo_slides.py` 从命令原始输出 JSON 读取，无手写结论；
  重渲染 `make demo-video`（会打印 `ffprobe` 时长/体积可自检）
- ⚠️ **发布前仍需**：
  1. **人工审阅**所有数字与边界表述（本项目不代替人做发布决策）
  2. **push 两个仓库**（主仓 / 目标工程当前均为本地领先 origin）
  3. 不得使用 T1 的 200ms 不可达结论、或任何 skip/未验证路径作为成功素材

---

## 不能碰的线

这些不是建议，是**项目的全部价值所在**。违反任何一条，产出即作废。

| 线 | 含义 |
|---|---|
| **绝不伪造** | 不许用估算值、历史值、"典型值"充当实测。**测不出来就说测不出来** |
| **无基线不优化** | 没有可比基线之前的"优化"都是盲改 |
| **单变量** | 一次只改一处，否则无法归因 |
| **必须复测** | 同设备、同构建类型、同命令 |
| **结论带来源** | 命令 + 设备 + 样本量 + 显著性，缺一不可 |

**T1 演练里这五条全部被触发过。** 最危险的时刻是有 fast 模式样本摆在那里 ——
挑两个就能报出「优化 11%」的漂亮数字。**那是伪造。**

详见 `AGENTS.md` 的「五条不可违背的原则」与「坑与禁区」。

---

## 怎么快速上手

```bash
make help          # 看所有可用命令
make test          # 跑全部测试（改完必跑）
make gate          # AI 友好度门禁（CI 同款）
make doctor        # 体检本机移动端工具链
```

**改任何东西之前**：
1. 读 `AGENTS.md` 的「坑与禁区」
2. 跑 `make test` 确认基线是绿的
3. 改了 `plugins/` → `make build-portable`；改了图 → `make build-diagrams`

**提交前**：`make lint test-py gate verify-portable verify-diagrams` 全绿。

---

## 项目结构速览

```
plugins/mobile-apm/   ★ 单一真源：技能 / Agent / 命令 / hook / 脚本 / 知识库
ios-apm/              iOS 原生埋点 SDK（Swift Package，11 测试）
rn-apm/               React Native 埋点 SDK（npm，74 测试）
tools/                跨 agent 编译器 + 图表渲染
docs/                 文档与图表
dist/                 生成物（勿手改）
```

改内容**只改 `plugins/mobile-apm/`**，然后重新生成。
