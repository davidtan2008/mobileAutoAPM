# 交接（HANDOFF）

> 这份文件只回答一个问题：**新会话接手后怎么开始干活**。
> 它不重复其它文档的内容，只指路。真源的优先级：`AGENTS.md` > `docs/PLAN.md` > `ROADMAP.md`。
> 提交历史与工作树状态会过期，**用 `git log` / `git status` 现查**，不要信本文的快照日期。

---

## 0. 项目是什么（30 秒版）

让移动端项目对 AI 友好，并让 AI 自主把移动端性能与稳定性问题解决掉。

- **A · 项目 AI 化改造**：`scripts/ai_readiness.py`（扫描）+ `scripts/ai_remediate.py`（计划/改造/复扫闭环）
- **B · APM 自主闭环**：`skills/apm-loop/`（发现 → 定位 → 修复 → 验证），底层 11 个零依赖 Python 脚本

文风与铁律见 [`AGENTS.md`](AGENTS.md)——**五条原则**（无基线不优化 / 单变量 / 必须复测 /
绝不伪造 / 结论带来源）。违反任何一条，产出即作废。

## 1. 文档地图（哪份管什么）

| 文档 | 什么时候读 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | **任何工作开始前**。铁律、坑与禁区、验证顺序 |
| [`docs/PLAN.md`](docs/PLAN.md) | **定优先级、开活前**。37 项待办的唯一真源（P0/P1/P2/P3 编号） |
| [`ROADMAP.md`](ROADMAP.md) | 想知道**项目在哪、要去哪**、T1 演练的完整证据链 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 改代码前想看清**结构**（分层/数据流/技能层接线） |
| [`docs/PRINCIPLES.md`](docs/PRINCIPLES.md) | 想知道**为什么这样设计**（统计判定/口径指纹/失败模型/证据链） |
| [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md) | **动手前**。按任务类型（APM 任务四阶段 / 文档 / 口径新增 / SDK）的操作手册 |
| [`docs/DATA-MODEL.md`](docs/DATA-MODEL.md) | 改 `metrics.json` / `diagnosis.json` / `baseline.json` 前，三份 JSON 契约 |
| [`docs/OBSERVING-PROJECT.md`](docs/OBSERVING-PROJECT.md) | 被观测工程侧 `.apm/` 目录的 schema 与 `ISSUE-NNN.json` 规范 |
| [`docs/usage-guide.md`](docs/usage-guide.md) | 命令行用法速查（`$S` 变量开头，两种安装场景） |
| `docs/archive/` | 已完成/已废弃文档（PROGRESS、TODO、RESTRUCTURE-PLAN、APM-BLUEPRINT） |

## 2. 当前状态（重点 ⭕）

**两根支柱都已成体系并有测试**：

| 支柱 | 状态 | 验证入口 |
|---|---|---|
| A · AI 化改造 | ✅ 闭环真机实测：68 → 86（+18） | `make readiness` / `ai_remediate.py loop` |
| B · APM 闭环 | ✅ 1 次成功闭环 + 1 次诚实失败闭环（T1 均为真机实测，见下） | `make test-py` 128 个 |

测试基线：**Python 128 / rn-apm 76 / ios-apm 12 = 216 全绿**。用 `make test` 一次跑齐。
这个数字会随代码演进，**以 `make test` 实际输出为准**（APM）与 `docs/PLAN.md` 的"翻新指令"一节。

**T1（冷启动 200ms 目标）**——项目史上最重要的资产之一，两个结论都是实测：

- 目标**不可达**：最小对照组（内容换 `Text("x")`）在正式口径下 p50=210ms（n=20, CV 2.7%），
  `apm_feasibility` → `blocked_by_control_floor`
- 优化**无效果**：可优化段 126→132ms，p=0.694，无显著变化，已回退
- 完整证据链（含 230 vs 210 两个地板数的关系）见 [`ROADMAP.md`](ROADMAP.md) 的 T1 章节

## 3. 接手后第一件事

```bash
git status && git log --oneline -5    # 现查提交与工作树状态，不要信文档快照
make test                            # 确认三套测试基线是绿的（这是起点，不是可选步骤）
python3 plugins/mobile-apm/scripts/apm_doctor.py   # 查本机现在真的能做什么
```

然后打开 [`docs/PLAN.md`](docs/PLAN.md)，从第一个未完成项做起。**不要开新战线**：
PLAN 里未完成项的顺序就是答案（P0 → P1 → P2 → P3；每项都带验收标准和来源）。

## 4. 高频环境事实

| 事项 | 事实 |
|---|---|
| Python 工具 | **零第三方依赖**，Python 3.9+；改完跑 `make test-py`（`-W error::ResourceWarning`，逐文件） |
| `dist/` | **生成物勿手改**；改 `plugins/` → `make build-portable` 重新生成，再 `make verify-portable` |
| CI 完整清单 | `make lint test-py gate verify-portable verify-diagrams`（提交前全绿——逐条见 AGENTS「改完之后怎么验证」） |
| iOS 测量 | 需要**物理真机** + Release 构建；模拟器与 debug 构建**不可比**（口径不同） |
| Android / 鸿蒙实测 | 代码与适配层已写好，但**从未真机验证**（无设备）——对外表述时必须明示 |
| iOS 真机截图 | `apm_screenshot.py` 优先 DVT，需 `pymobiledevice3` 做后备（可选依赖） |

## 5. 已知限制（不能对外说 "已验证" 的部分）

- **RN / iOS SDK 与 Python 脚本的指标名还没完全对齐**
  （`startup.cold.total` vs `startup.cold.first_frame` 等，详见 `docs/PLAN.md` P1 段的相应条目）。
- **WhiteScreenDetector 有计时类 flaky 记录**（RN 测试失败过一次，3 次复跑通过）——在 PLAN.md 有条目。
- **apm_measure 的 device id、构建路径是插件外置参数**，插件的其它 profile（Android/鸿蒙/RN）尚未验证 ——
  需要设备；验证前，任何相关文档必须带「未验证」标注。
- **性能数字一律现场实测**：本仓库没有任何"典型值"。旧值的唯一用途是作为历史证据引用，
  且必须带出处（哪台设备/哪个 commit/哪条命令）。

## 6. 常见坑（已被踩过）

1. **不要把"我快写完了"当"完工"**：验收标准看 PLAN.md，不看自己的体感。
2. **数字对本不上就停下**：每条性能数字必须能说出哪条命令/哪台设备/哪个 commit 得到的；
   说不出来 → 删掉该数字，或者现场实测。历史上 `README/v2ex` 就发生过
   「280→279, p=0.605」这类**完全无证据**的数字混进公开文档（已修，教训见 PLAN）。
3. **真跑才暴露的坑都已沉淀**：插件知识库 `plugins/mobile-apm/references/`（选型硬限制 /
   指标口径 / 测量协议）与 `ROADMAP.md` 的 T1 章节。**不要再凭记忆复述这些坑**——引用它们。
4. **不要新建第三套优先级编号**：PLAN.md 是唯一真源；每做完一项就回去把状态改掉。
5. **evidence 目录**: `.apm/` 是被观测工程侧的知识库（含 ISSUE-NNN.json 一致性规范，见
   `docs/OBSERVING-PROJECT.md`），不要在本仓库根目录随手建 `.apm/`。
