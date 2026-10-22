# 待办与优化优先级计划表

> 这份表回答一个问题：**接下来先做哪件事**。
>
> 它是仓库里**唯一的待办真源**。`ROADMAP.md` 说方向，本表说下一步。
> 两者分工：路线图回答"往哪走"，本表回答"先动哪个、动到什么程度算完"。
>
> **排序依据**（按此顺序判定优先级，不是按谁先提出来）：
>
> | 优先级 | 判据 |
> |---|---|
> | **P0** | 会让工具给出**错误结论**，或让文档**说谎**。这类问题的代价是整个项目价值的归零。 |
> | **P1** | 影响**可维护性与交接质量**：无回归保护、真相源分裂、流程会悄悄腐化。 |
> | **P2** | 质量与体验提升：能省事，但不修也不会造成错误结论。 |
> | **P3** | **需要外部条件**（设备、账号、外部决策）。不是"优先级低"，是"暂时做不了"。 |
>
> 表中每项都标了**来源**（哪个文件哪一行），以便核对与追溯。

---

## 目录

- [P0 · 会产生错误结论或说谎的（9 项，✅ 全部已修）](#p0--会产生错误结论或说谎的9-项)
- [P1 · 可维护性与交接质量（10 项）](#p1--可维护性与交接质量10-项)
- [P2 · 质量与体验（11 项：首批 8 + SDK 增补 3）](#p2--质量与体验11-项首批-8--sdk-增补-3)
- [P3 · 需外部条件（6 项）](#p3--需外部条件6-项)
- [SDK 双端增补（第二批审读）](#sdk-双端增补第二批审读)
- [已在本轮修复的（13 项）](#已在本轮修复的13-项)
- [统计与建议排期](#统计与建议排期)

---

## P0 · 会产生错误结论或说谎的（9 项）

### P0-1 · `apm_baseline` 百分比显示被放大 100 倍 ✅ 已修

| | |
|---|---|
| **问题** | `format_percent` 写成 `f"{value:.1%}"`，而 `%` 格式说明符**会再乘 100**。`delta_pct` 本身已是百分数（5.75），被渲染成 **"575.4%"**。同一份输出里另一处显示 "+5.8%"，自相矛盾。 |
| **影响** | 结论里的百分比是**错的**，而且是那种"看起来很离谱所以读者会怀疑整个工具"的那种错。CV 用的是同一函数但语义不同（比率），改错会连带影响 CV 显示。 |
| **验收** | 真实基线对比输出里 `变化 +14.50 (5.8% p50)`；CV 仍为 `3.5%`。已加回归测试 `test_百分比显示不被放大100倍`。 |
| **来源** | 真实 `apm_baseline compare` 输出（2026-10-22 实测）· `apm_baseline.py:432` |

### P0-2 · `apm_diagnose` 的 stage 派生是死代码 ✅ 已修

| | |
|---|---|
| **问题** | `derive_from_observations()` 要求 `observation["stages"]` 是 **dict**，而 `apm_measure` 写的是 **list**（`[{"name","sinceMs","deltaMs"},…]`）。两者对不上 → 这条路径对新模板数据**永远不会生效**。 |
| **影响** | 它恰恰是「stage 名跨样本不一致 → `apm_measure` 因此不导出 stage 指标」时的**兜底**。兜底失效 = 那种情况下彻底没有分段视图。 |
| **验收** | list 形态能派生出 `startup.stage.*`；dict 形态仍兼容。已加 4 个回归测试。 |
| **来源** | `apm_diagnose.py:232` vs `apm_measure.py:858` |

### P0-3 · 两个脚本的 stage 正则不一致 ✅ 已修

| | |
|---|---|
| **问题** | `apm_measure.STAGE_RE` 允许小数（`name=120.5/+120.5`），`apm_diagnose.STAGE_RE` 只允许整数。 |
| **影响** | 含小数 stage 的日志在**诊断侧解析不到** → 分段指标全空 → 诊断"看起来正常"但少了一半信息。 |
| **验收** | 两边正则统一为同一模式，已加回归测试。 |
| **来源** | `apm_measure.py:75` vs `apm_diagnose.py:43` |

### P0-4 · `docs/diagrams/manifest.json` 从来不是合法 JSON ✅ 已修

| | |
|---|---|
| **问题** | 两个叠加的 bug：① note 行结尾**漏了逗号**；② `「✅ xxx.png」` 日志 echo 落在 `{ … } > "$MANIFEST"` 重定向块里，被一起写进清单。 |
| **影响** | 仓库自己的 `check_all` 用 `grep` 解析、**根本不校验 JSON**，所以这个错从文件诞生起就没人发现。直到外部工具用 `jq` 读它才炸。**一个"图的门禁"自己带着坏数据**。 |
| **验收** | 清单是合法 JSON（`jq` 可读）；`check_all` 先校验 JSON 合法性再比对哈希；顺带修掉了 `grep -A1` 会**连带抓到下一行哈希**的第二层脆弱性。 |
| **来源** | `tools/build-diagrams.sh:57`（日志泄漏）· `:42`（缺逗号） |

### P0-5 · 40 条命令的路径不存在 ✅ 已修

| | |
|---|---|
| **问题** | `README.md` 6 处 + `docs/usage-guide.md` 32 处 + `IMPLEMENTATION.md` 3 处 + `symbolication-pipeline.md` 7 处，把脚本写成 `python3 scripts/xxx.py`。**仓库根没有 `scripts/` 目录**——而 `usage-guide.md` 开头明写"所有命令都可直接复制执行"。 |
| **影响** | 一个**面向使用者的手册**里 40 条命令跑不通。新用户按文档走第一步就撞墙。 |
| **验收** | README 用显式路径 `plugins/mobile-apm/scripts/xxx.py`；usage-guide 开头定义 `$S` 变量并给出两种场景（仓库内 / 插件已安装）；全部路径已用脚本逐条核验存在。 |
| **来源** | 文档盘点报告 C19 |

### P0-6 · `HANDOFF.md` 声称支柱 A 改造闭环"没做" ✅ 已修

| | |
|---|---|
| **问题** | `HANDOFF.md:18` 写"只有扫描器，**改造闭环没做**"，而**同一文件第 81 行**写"✅ `ai_remediate.py` 已落地并量化（iOS 工程 68 → 86）"。同一文档自相矛盾，且第 18 行与另外 6 份文档全部相反。 |
| **影响** | 交接文档的**第一张能力表**就是错的。接手的人会以为支柱 A 还没做，可能重做一遍。 |
| **验收** | 本轮重写 `HANDOFF.md`（改为轻量交接指针 + 文档地图），能力状态收敛到本表与 ROADMAP。 |
| **来源** | 文档盘点报告 C5（标记为**严重**） |

### P0-7 · `HANDOFF.md` 的交接指引指向已完成的阶段 ✅ 已修

| | |
|---|---|
| **问题** | "第一件该做的事：P0 已完成，接下来是 P2 支柱 A"、结尾"做完这些，你可以从 `ROADMAP.md` §P0 开始"——而 P0/P1/P2 **全部已完成**，真正的待办是 P3/P4。 |
| **影响** | 接手的人第一步做的是已经做完的事，浪费一整个上下文窗口。 |
| **验收** | 重写后"接手后第一件事"直接指向 `docs/PLAN.md` 的第一个未完成项，并显式说"不要开新战线"。 |
| **来源** | 文档盘点报告 C17 |

### P0-8 · T1 的"空壳地板"有两个数（230ms vs 210ms）✅ 已修

| | |
|---|---|
| **问题** | 6 份文档写 **230ms**（ROADMAP/PROGRESS/HANDOFF/IMPLEMENTATION/feasibility-protocol），6 份写 **210ms**（README/usage-guide/v2ex-post/demo 证据包）。**没有任何文档说明这两个数的区别**。 |
| **影响** | 读者必然认为其中一份在撒谎。而这恰恰是本项目最不该出的问题。 |
| **验收（已过）** | 核实到证据链后确认**两个数都是真的、但不矛盾**：230 = 09-25 T1 演练中对照组（内容换 `Text("x")`）在旧测量链路下 fast 模式 229–244ms 的粗整（`ISSUE-001`）；210 = 09-26 **修好测量之后**的正式复测 p50=210.0 / CV 2.7%（n=20，`blocked_by_control_floor` 依据，`feasibility/20260926-startup-200ms.json`）。已在 `ROADMAP.md` T1 章节加对照表，正式证据锚定 210，230 保留为历史观察。 |
| **来源** | 文档盘点报告 C2（标记为**高**） |

### P0-9 · T1"优化无效果"用了两组数字 ✅ 已修

| | |
|---|---|
| **问题** | README/v2ex-post 写 `280ms → 279ms, p=0.605`；ROADMAP/HANDOFF/PROGRESS 写 `可优化段 126ms → 132ms, p=0.694`。描述**同一次 T1 结论**却用两组数。 |
| **影响** | README 里那个数在公开内容里流传更广——**而它就是错的**。 |
| **验收（已过）** | ① 用 `grep 0.605` 扫了被观测工程全部 `.apm/runs/` / `baseline/` / `feasibility/`，**该数字从不存在于任何证据**——是文档漂移出来的伪数字。② report.md 原文的真实记录是：优化 **1 处**、可优化段基线 126ms（n=5）→ 132ms（n=8）、**p=0.6941 无显著变化、已回退**。③ README L65/L113、`PRINCIPLES.md`、`drafts/v2ex-post.md` 四处已改为真实数字并标注指标（可优化段 `startup.addressable_segments`）；"三轮优化"改为"1 处"；README L113 的「快的一簇 235ms」改为快簇实测 278–302ms。 |
| **来源** | 文档盘点报告 C3（标记为**高**） |

---

## P1 · 可维护性与交接质量（11 项：首批 10 + SDK 增补 1）

### P1-1 · 三个脚本**零测试** ⬜ 待做

| | |
|---|---|
| **问题** | `apm_doctor.py`（259 行）、`apm_white_screen.py`（309 行）、`rn_build_symbols.py`（295 行）**完全没有测试文件**。合计 863 行无回归保护。 |
| **影响** | `apm_white_screen.py` 尤其危险：它是**手写的 309 行 PNG 解码器**（5 种扫描线滤波、多种位深、隔行拒绝），而白屏判定直接影响"App 白屏了没有"这个结论。手写二进制解析器没有测试 = 静默错误的高发区。 |
| **验收** | 三个脚本各有测试文件；PNG 解码器覆盖 5 种滤波 × 3 种位深 × 隔行拒绝 × 损坏输入；`--strict` 退出码有测试。 |
| **预估** | 白屏 1.5h · doctor 1h · build_symbols 1h |
| **来源** | 代码审读报告 §测试覆盖分析 |

### P1-2 · `apm_measure.LogCollector` 无测试 ⬜ 待做

| | |
|---|---|
| **问题** | 测试全部用 `FakeCollector` 替换真实实现。**有界缓冲（2000 行）、溢出计数 `_dropped`、线程生命周期**都没有测试。 |
| **影响** | 溢出时该样本会被判 invalid——这是**正确行为**（不完整的日志算出的数字是假的），但正因为它正确且关键，必须有测试锁住。 |
| **验收** | 真实 `LogCollector` 的溢出路径、丢弃计数、单行截断有测试。 |
| **来源** | 代码审读报告 §未覆盖分支 |

### P1-3 · `apm_diagnose` / `apm_baseline` 多个判定分支未测 ⬜ 待做

| | |
|---|---|
| **问题** | `apm_diagnose` 的 `insufficient_samples` / `insufficient_variation` / `unusable_high_variance` / `marginal` 四种 status 未测；`stageCandidates`（稳定分解段筛选）完全未测。`apm_baseline` 的 `improved` verdict **从未被测过**（只测了 `regressed`）、`no-practical-change`、`insufficient-data`、`no-baseline`、`missing-in-run`、`context-mismatch` 均未测。 |
| **影响** | 判定阶梯是这套工具的核心逻辑，**正向路径没测**意味着"修好了"这个结论从未被自动化验证过。 |
| **验收** | 9 种 verdict 全部有测试；`stageCandidates` 的"必须在所有 run 中都稳定"约束有测试。 |
| **来源** | 代码审读报告 §未覆盖分支 |

### P1-4 · `apm_feasibility` 的 CLI 退出码映射未测 ⬜ 待做

| | |
|---|---|
| **问题** | 测试只调 `feasibility.check()` 函数与 `main(["plan",…])`。**`main(["check",…])` 的端到端退出码 1/2 映射没有测试**——而这正是"能不能当 CI 门禁"的依据。 |
| **影响** | 退出码语义是这套工具对外的**契约**，必须有测试锁住。 |
| **验收** | `check` 的 0/1/2 三条 CLI 路径各有测试。 |
| **来源** | 代码审读报告 §未覆盖分支 |

### P1-5 · `ai_readiness` 有 11 个 finding key 无专门测试 ⬜ 待做

| | |
|---|---|
| **问题** | `no-ci` / `no-lockfile` / `build-artifacts` / `deep-tree` / `no-scripts-dir` / `no-test-script` / `ios-deps-unclear` / `ios-no-team` / `no-cmd-in-readme` / `thin-readme` / `rn-no-test-fw` 共 11 个 key 无测试；`_gate` 的 `--min-score` / `--no-blocker-fail` 分支、`walk()` 的 20000 文件上限、`read_text()` 的 200KB 截断无测试。 |
| **影响** | `ai_readiness` 是**支柱 A 的评分器**，评分错了改造计划就错了。而且它有大量**防误报**的精细逻辑（根类型 vs 全类型分离、`python3` 词边界、编号前缀架构文档…），这些逻辑没有测试 = 随时会被改坏。 |
| **验收** | 11 个 key 各自有测试；`--min-score` / `--no-blocker-fail` / 两个上限有测试。 |
| **来源** | 代码审读报告 §未覆盖分支 |

### P1-6 · 五套并行的优先级编号 ✅ 已修

| | |
|---|---|
| **问题** | `ROADMAP.md` 用 P0–P4、`RESTRUCTURE-PLAN.md` 用 R0–R5、`TODO.md` 用 P0/P1/P2、`PROGRESS.md` 用 P0–P3、`HANDOFF.md` 用 R0/R1/T1+P0–P4。**五套编号并行**，且 ROADMAP 从不提 R 系列。 |
| **影响** | "下一步做什么"这个问题在仓库里有五个互相不兼容的答案。 |
| **验收（已过）** | `docs/PLAN.md` 成为唯一编号真源；`ROADMAP.md` / `HANDOFF.md` / `README.md` / usage-guide 全部改指本表；三份历史文档归档到 `docs/archive/`（P1-7/P1-8）。 |
| **来源** | 文档盘点报告 C16（标记为**严重**） |

### P1-7 · `RESTRUCTURE-PLAN.md` 规划的目标结构从未落地，但 `AGENTS.md` 指向它 ✅ 已修

| | |
|---|---|
| **问题** | 该文档规划 `core/ adapters/ runtimes/ evolution/` + `tools/build-adapters.py`，实际是 `plugins/mobile-apm/ ios-apm/ rn-apm/ tools/build-portable.py`——**四个目录名全不同**。而 `AGENTS.md:21`（本轮已改）曾写"完整背景见 `docs/RESTRUCTURE-PLAN.md`"。 |
| **影响** | 指向一份未落地的草案当"完整背景"。 |
| **验收（已过）** | 已 `git mv` 到 `docs/archive/`（内容不删）；`docs/archive/README.md` 写明"规划 ≠ 实现"的教训样本；`AGENTS.md` 已改指 ROADMAP + PLAN。 |
| **来源** | 文档盘点报告 C15（标记为**严重**） |

### P1-8 · `APM-BLUEPRINT.md` 内部数字自相矛盾 ✅ 已修

| | |
|---|---|
| **问题** | 同一份文档里：L29 写"11 技能"、L30 写"7 个技能"（实际 7）；L34 数据平面只列 6 个脚本（实际 11）；L35 知识库只列 2 篇（实际 5）；L73 写"31 个测试"（实际 39）；L112 写 `.claude/skills/_apm/scripts/apm_doctor.py`——**该路径在仓库里不存在**。 |
| **影响** | 它是"业界调研蓝图"，被当成半真源引用。 |
| **验收（已过）** | 已 `git mv` 到 `docs/archive/`；状态说明写在归档索引 `docs/archive/README.md`（文件本体不动，保留历史原貌）。 |
| **来源** | 文档盘点报告 C9/C10/C11 |

### P1-9 · 演示素材的叙述性文字是硬编码的 ⬜ 待做

| | |
|---|---|
| **问题** | `render_demo_slides.py` 声明"只允许从证据文件读取数字"，但第 3/4 页的**全部文字**是硬编码；commit message 字面量 `fix: persist translations for ended tasks` 写死在代码里；`「真机 iPhone13/iOS26.7」` 是硬编码标签（JSON 里实际是 `iPhone 13` / `26.7`）。`render_demo_hero.sh` 更直接——`123/123`、`20/20 帧`、`commit 43e6576` 全写死。 |
| **影响** | 证据更新后演示素材**不会自动跟随**，且**没有任何告警**。这与"只从证据读数字"的原则直接冲突。 |
| **验收** | commit message 与设备标签从证据文件读；hero 脚本的数字从 `render.json` 或证据 JSON 读。**最低要求**：加一道断言，证据里的数字与素材里的字面量不一致就失败。 |
| **来源** | 工具链审读报告 §E3 硬编码风险分级 |

### P1-10 · `make lint` 在缺 ruff 时**静默跳过** ⬜ 待做

| | |
|---|---|
| **问题** | `make lint` 的 `py_compile` 只覆盖 `$(SCRIPTS)/*.py tools/*.py`（**不含 tests/**）；ruff 未装时打印"（未装 ruff，跳过风格检查）"并**不失败**。 |
| **影响** | CI 的 lint 步骤在 runner 缺 ruff 时会**变成空门禁**——而 CI 并没有显式安装 ruff（靠 `ubuntu-latest` 镜像预装）。门禁形同虚设是最难发现的一类腐化。 |
| **验收** | CI 显式安装 ruff；`make lint` 在缺 ruff 时**失败**（或至少 `make check` 失败）。`py_compile` 覆盖 tests/。 |
| **来源** | 工具链审读报告 §E4-3 |

### P1-11 · SDK 与 Python 脚本的指标名不对齐 ⬜ 待做

| | |
|---|---|
| **问题** | RN SDK 上报 `startup.cold.total`；Python 数据平面只有 `startup.cold.first_frame`。双向的 stage 命名也未对齐。SDK 的 JSON 一旦被 Python 判定链消费（RN 测量 profile 落地后），基线/显著性判定会**静默失配**。 |
| **影响** | 现在还没有互相消费所以没有立刻出错；但 RN profile 是 P3-1 的第一候选，落地时若名不对齐，第一次比对就会切入"口径不一致→退出码 1"分支——要么阻塞要么诱导有人改口径。**趁现在没有消费方，改名的成本最低。** |
| **验收** | 名单登记进 `references/metric-spec.md`；RN/iOS SDK、`apm_measure`/`apm_baseline`/`apm_diagnose` 用**同一张指标名表**（共享 fixture 文件也可以）；RN profile 落地前完成首个端到端对跑（SDK 上报 → Python 判定）。 |
| **来源** | SDK 双端审读报告 C.2 |

---

## P2 · 质量与体验（11 项：首批 8 + SDK 增补 3）

### P2-1 · 三份 JSON 契约没有文档 ✅ 已建 → [`DATA-MODEL.md`](DATA-MODEL.md)

`metrics.json` / `diagnosis.json` / `baseline.json` 是整套工具对外的**契约**。已按真实
run 样本（control-clean-2b3a1ea-warmup3-n20 等）归纳字段与语义，并写明"改契约先登记
spec + schemaVersion 递增"的规矩。

### P2-2 · 被观测工程侧（`.apm/`）零文档 ✅ 已建 → [`OBSERVING-PROJECT.md`](OBSERVING-PROJECT.md)

已按真实 `.apm/` 实例（baseline 命名即口径、`ISSUE-001` 字段纲、.feasibility 文件、
run 拒绝留档原则）成文。

### P2-9 · RN `ReporterConfig.flushIntervalMs` 从未被读取 ⬜ 待修

定时 flush 本身**存在**（`reporter.start(intervalMs)`，`index.ts:155`），但它读的是**顶层配置**
`config.reporterFlushIntervalMs ?? 10_000`；`reporter.ts:19` 里 `ReporterConfig` 对象内的
`flushIntervalMs` 声明后**没有任何消费点**——把间隔写进 reporter 配置是不生效的，两条入口并存必踩。→ 删除 `ReporterConfig.flushIntervalMs`（或让 `start()` 改读它），并配注释放明唯一入口。

### P2-10 · `MemoryReport.sampleCount` 与 `samples.length` 语义未文档化 ⬜ 待做

`sampleCount` 返回**累计采样次数** `samplesTaken`（含已淘汰样本），`samples` 是 RingBuffer
（capacity 默认 180）内的**存活窗口**——>n 久跑后两者必然不等，`dropped`（淘汰数）单独计数。
语义本身成立，但没有文档就没有定义，消费方极易把 `sampleCount` 当 `samples.length` 用。→ 在 `types.ts` 三个字段上写清语义 + 回归测试锁住不等式的正确性。

### P2-3 · `apm_white_screen` 解码阶段内存不受 `--sample-step` 约束 ⬜ 待做

`read_png_pixels()` 先 `read_bytes()` 把整个文件读进内存再 `zlib.decompress` 整个 IDAT。`--sample-step` 只影响后续采样密度，不影响解码峰值。4000×3000 的图峰值内存与原图同量级。→ 改成分块解码，或在文档里明确标注为已知取舍。

### P2-4 · `apm_doctor` 的能力依赖表把 `mobilebuildmcp` 列为必要 ⬜ 待做

`CAPABILITIES` 里"启动耗时检测""白屏检测""UI 自动化回归""内存检测""崩溃符号化"5 个能力都要求 `mobilebuildmcp`，但**没有任何脚本实际调用它**（`apm_measure`/`apm_screenshot` 走 `xcrun devicectl`）。结果是这些能力会**误报「受限」**。→ 修正依赖表。

### P2-5 · `apm_measure` 的非 `MeasurementError` 异常会变 traceback ⬜ 待做

`main()` 只捕获 `MeasurementError` 与 `KeyboardInterrupt`，其他异常（如 `OSError`）→ traceback，退出码仍是 1 但输出不可操作。→ 补一个兜底 `except Exception`，输出可操作提示。

### P2-6 · `apm_screenshot` 两种失败 JSON schema 不一致 ⬜ 待做

`CaptureFailure` 分支含 `toolVersion`，普通 `ScreenshotError` 分支**不含**。→ 统一。

### P2-7 · `rn_build_symbols` 调 `compose` 的子进程无超时 ⬜ 待做

理论上可无限挂起。→ 加 timeout。

### P2-8 · 重复实现清理 ⬜ 待做

`finite_number` 3 份、`pooled_stdev` 2 份、`run_command` 2 份、`command_text` 2 份、devicectl 设备列表读取 2 份、设备解析 2 份、`positive_int` 2 份。数学上等价但代码不同，改一处漏一处是典型漂移源。→ 抽到共享模块（注意：保持零第三方依赖，且 `dist/` 生成机制要能带上共享模块）。

### P2-11 · WhiteScreenDetector 有一次计时型 flaky 记录 ⬜ 观察

本轮 RN 全量测试中 `WhiteScreenDetector` **闪退过一次**（前后共 3 次复跑均稳定通过），
定性为**计时型** flaky，独立复现门槛未知。对策：不盲改断言；**若再次出现**，
先固定随机种子/放宽首时间的计时断言并注明原因，再配最小复现测试。

---

## P3 · 需外部条件（6 项）

> 这些**不是优先级低**，是**暂时做不了**。列出是为了让接手的人知道边界在哪。

| # | 事项 | 缺什么 | 影响 |
|---|---|---|---|
| **P3-1** | Android / 鸿蒙 / RN 标准测量适配器 | Android/鸿蒙设备（`adb devices` / `hdc list targets` 目前均为空） | 这是**最大的能力缺口**。`apm_measure` 目前只承诺 `ios-native-startup` 一个 profile。 |
| **P3-2** | 原生 shim Android / 鸿蒙侧真机验证 | 同上 | iOS 侧已真机验证（`docs/evidence/native-shim/measured.json`），另两侧未验证。 |
| **P3-3** | Sentry MCP 鉴权 | Sentry 账号 OAuth（`/mcp`） | 崩溃数据的后端链路未打通。 |
| **P3-4** | 数据后端选型 | **需要人做决策**（self-hosted / 腾讯 Bugly / AGC APMS） | 不是技术问题。`references/stack-selection.md` §7 已备好选型依据。 |
| **P3-5** | V2EX 发帖 | 账号注册满 30 天 | 草稿已备好（`docs/drafts/v2ex-post.md`）。 |
| **P3-6** | 自我进化机制 | 闭环样本量不足 | 设计在 `docs/diagrams/evolution.svg`。**当前闭环样本只有 1 成功 + 1 失败**，过早做淘汰机制等于在噪声上建制度。 |

---

## SDK 双端增补（第二批审读）

> 对 `rn-apm`（76 测试）与 `ios-apm`（12 测试）的逐行审读发现。**本轮发现 3 处真实缺陷并已修复**，
> 其余定级入表（P1-11 / P2-9 / P2-10）。

✅ **已修（配两端回归测试）**：

| 缺陷 | 位置 | 修法 |
|---|---|---|
| 负斜率被判为「缓慢增长」 | `rn-apm/src/memory/sampler.ts` `describeSlope` + `ios-apm` `MemoryWatermark.growthDescription` | 两处都漏 `abs()`，负斜率被标成"缓慢增长"；补 abs + 两端回归测试 |
| `createBridge` 没接线 `reportJsError` | `rn-apm/src/native/bridge.ts` | 原生同名方法此前是死代码，`getLayerStatus().native` 恒 unavailable；接线后真机能收到 JS 错误 |
| `describeNativeGaps` 报"方法不存在"而非"取值不可用" | 同上 | 改为探测**实际取值**；`enablePromiseRejectionTracking` 确认为死接口，保留兼容并注释 |
| `type.ts` 注释与 README 过期声明 | `rn-apm/src/types.ts`、rn-apm README | 原生 shim iOS 侧已真机验证，过期声明如实改 |

⬜ **发现未修**：指标名不对齐 → P1-11；`flushIntervalMs` 死入口 → P2-9；`sampleCount` 语义 → P2-10。

---

## 已在本轮修复的（13 项）

> 这一节的作用是**避免同样的问题被重新发现**。每项都留了回归测试或断言。

| # | 修了什么 | 防复发的机制 |
|---|---|---|
| 1 | `apm_baseline` 百分比放大 100 倍 | 回归测试 `test_百分比显示不被放大100倍` |
| 2 | `apm_diagnose` stage 派生只认 dict（死代码） | 4 个回归测试（list/dict 双形态） |
| 3 | 两个脚本 stage 正则不一致 | 回归测试 `test_阶段正则接受小数` |
| 4 | `apm_diagnose.compare_contexts` 与 `apm_baseline.compare_contexts` 同名不同签名 | 改名 `compare_run_contexts` + 注释说明 |
| 5 | `ai_remediate` docstring 承诺从未兑现的退出码 2 | 改掉承诺本身（需人工介入走 `needsHuman` 列表） |
| 6 | `manifest.json` 不是合法 JSON | `check_all` 先校验 JSON 合法性 |
| 7 | 图表门禁 `grep -A1` 抓到下一行哈希 | 改为解析 JSON |
| 8 | 40 条命令路径不存在 | 全部路径逐条核验存在 |
| 9 | `render.json` 被 gitignore 导致干净 clone 跑不了首屏流水线 | 入库 + 拆出 `demo-slides` target |
| 10 | AGENTS.md 测试数 116（实际 128） | 每轮 `make test` 实测后回写 |
| 11 | SDK 负斜率被判「缓慢增长」（RN + iOS 同款） | 两端各配回归测试 |
| 12 | RN `createBridge` 没接线 `reportJsError`（原生死代码） | 接线 + `getLayerStatus().native` 状态可断言 |
| 13 | 文档漂移伪数字 `280→279, p=0.605`（见 P0-9） | grep 全证据链证明不存在 + 四处改真实数字 |

**另外还修了两个 shell 脚本的真 bug**（不是文档问题，但会让"错误提示本身崩掉"）：

| 修了什么 | 说明 |
|---|---|
| `render_demo_video.sh` 的 `$manifest（` | bash 会把紧跟的全角括号的多字节字符并进变量名，`set -u` 下报「未绑定的变量」——**结果连错误提示本身都崩掉**。改用 `${manifest}`。 |
| `render_demo_hero.sh` 的 `$ba_after）` | 同上。**这个行为在 bash 5.3.15 上仍可复现**，不是老版本问题。全仓 `*.sh` 已扫过。 |

---

## 统计与建议排期

### 按优先级 × 状态（可验证：与上文各节标题一一对应）

| 优先级 | 条目 | 已修 | 待动 | 备注 |
|---|---:|---:|---:|---|
| **P0** | 9 | **9** | 0 | 全部闭环（含回归测试或证据核验） |
| **P1** | 11 | 3（P1-6/7/8） | 8 | 待动集中在测试缺口（P1-1/2/3/4/5）、素材硬编码（P1-9）、门禁（P1-10）、指标名（P1-11） |
| **P2** | 11 | 2（P2-1/2） | 9 | 待动：4 个健壮性小修 + 重复实现收敛 + SDK 两项 + flaky 观察（P2-11） |
| **P3** | 6 | 0 | 6 | 全部卡外部条件（设备/账号/决策） |
| **合计** | **37** | 14 | 23 | 修复台账见下节（13 行，含未编号条目；与表内 ✅ **有重叠但不全等**）+ 2 个 shell 真修复 |

### 建议排期

**第 1 批（半天）—— ✅ 已完成（2026-10-22：HANDOFF 重写 + T1 数字修正 + 编号统一）**

1. P0-6 / P0-7：重写 `HANDOFF.md`（本轮已做，见文末）
2. P0-8 / P0-9：说清 T1 的两个地板数与两组优化数字
3. P1-6：统一编号到 `docs/PLAN.md`（本轮已做）

**第 2 批（1 天）—— 给最脆弱的代码加回归保护**

4. P1-1：三个零测试脚本，**先做 `apm_white_screen`**（手写 PNG 解码器，风险最高）
5. P1-2：`LogCollector` 溢出路径
6. P1-3：9 种 verdict 全覆盖

**第 3 批（1 天）—— 堵住会悄悄腐化的门禁**

7. P1-10：`make lint` 缺 ruff 必须失败
8. P1-9：演示素材的硬编码数字加断言
9. P2-4 / P2-5 / P2-6 / P2-7：小的健壮性修复

**第 4 批（按需）—— 清理与扩展**

10. P2-8：重复实现收敛
11. P1-4 / P1-5：剩余测试补齐
12. P2-1 / P2-2：新文档（本轮已建）

**需要设备时**

13. P3-1 / P3-2：Android / 鸿蒙真机——**这是能力上限的提升点**，但要等设备

### 一句话总结

> **P0 里有 5 项是文档说谎，4 项是代码给错结论。**
> 两者都不是"功能缺失"，是**可信度损失**。
> 而这个项目唯一的卖点就是可信度——所以它们必须先于任何新功能。
