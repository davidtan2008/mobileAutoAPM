# 参与贡献

感谢你愿意花时间在这个项目上。这份文档只讲三件事：**怎么跑起来、改哪里、以及哪些东西不能动**。

如果你只是想**用**它，不需要读这一份 —— 看 [使用指南](docs/usage-guide.md) 就行。

---

## 30 秒跑起来

```bash
git clone https://github.com/davidtan2008/mobileAutoAPM.git
cd mobileAutoAPM

make test    # 全部测试：Python 123 + RN 74 + iOS 11
make check   # 快速自检（lint + AI 友好度）
```

| 需要 | 版本 |
|---|---|
| Python | 3.9+（工具**零第三方依赖**，不需要 pip install） |
| Node | 18+（只有 RN SDK 需要；CI 用 20） |
| Xcode | 仅 iOS SDK 需要（`Package.swift` 声明 iOS 15+ / macOS 12+；CI 跑 macos-14） |

跑不起来的话，先跑 `make doctor` 看本机缺什么。

---

## 只有一条硬规则

> **不要在文档、代码注释或 PR 描述里写没验证过的数字。**

这是本项目唯一不可让步的规则，也是它和同类项目最大的区别。

具体说：

| 不要 | 要 |
|---|---|
| 「启动快了约 20%」 | 「p50 252ms，n=10，CV 3.5%，跨 run `consistent`，commit `2b3a1ea`」 |
| 「这个改动有效果」 | 「−68ms，置换检验 p=0.0088，但噪声带 75ms → 判为**不具实际意义**」 |
| 「在 iPhone 上测过」 | 「iPhone 13 / iOS 26.7 / 物理设备，commit `43e6576`」 |
| 「Android 也支持」 | 「Android 测量 profile **尚未实现**」 |
| 「内存占用很低」 | 「`phys_footprint` 15.9MB（**不用** RSS，见下）」 |

第二行值得多看一眼：它**统计上显著，但工具判为不值得改**。一个只知道「p < 0.05」的 Agent 会把噪声包装成成果。

为什么这么严？因为这类项目最常见的失败不是代码写错，而是**给了一个看起来合理但没人验证过的数字**，然后所有人基于它做决策。这个项目存在的意义就是消灭这种数字。

如果你手上有一个数字但**测不出来**，直接写「测不出来」比编一个强得多。

---

## 改之前先知道这些

### 单一真源：`plugins/` → `dist/` 是生成物

```
plugins/mobile-apm/    ← 唯一真源，改这里
        │ make build-portable
        ▼
dist/                   ← 生成物，**不要手改**（CI 会校验不一致）
```

`dist/` 是由 `tools/build-portable.py` 生成的，供 Claude Code / opencode 等多个 agent 复用。改了 `plugins/` 忘记重新生成，CI 会失败——这不是 CI 故意为难人，而是防止两个副本悄悄漂移。

### 三个「不要」

| 不要 | 原因 |
|---|---|
| 不用 `resident_size` 测内存 | 系统按 `phys_footprint` / PSS 判定内存压力，用 RSS 会**严重高估**（实测 15.9MB vs 83.9MB，差 5 倍） |
| 直接覆盖 `ErrorUtils.setGlobalHandler` | 会打断 Sentry 等已存在的 handler，**必须链式调用** |
| 不用版本号关联 sourcemap | 热修后版本号会错位，**必须用 debug ID** |

### 判空时别返回「看起来像正常数据」的值

```swift
// ✅ 取不到返回 -1，调用方能区分「没有数据」和「数据是 0」
func premainMillis() -> Int

// ❌ 返回 0 会被误读成「pre-main 极快」——这是危险的假数据
```

---

## 提 PR 之前

```bash
make test                            # 1. 三套测试全绿（Python / RN / iOS）
python3 plugins/mobile-apm/scripts/ai_readiness.py --path .   # 2. 友好度不退化
make build-portable && make verify-portable                    # 3. 改过 plugins/ 才需要
make verify-diagrams                                           # 4. 改过 SVG 才需要
git diff --check                     # 5. 无行尾空白
```

**没有验证过的改动不要提交。** 宁可 PR 里写「这部分我没验证，因为手头没有 XX 设备」，也不要假装验证过了。

---

## 提 issue 前

请尽量附上这三样，能省掉一大半来回：

1. **你跑的那条完整命令**（含参数）
2. **`make doctor` 的输出**（说明本机有什么工具）
3. **你的 commit / 设备 / 构建类型**

如果是性能问题，附上 `.apm/runs/<你的run>/metrics.json` 最好——工具的判断逻辑全在 JSON 里，直接看比描述快得多。

---

## 代码风格

- Python 工具**只用标准库**，不引第三方依赖
- SDK 不引第三方**运行时**依赖
- 注释和文档用中文，**术语保留英文原文**（`phys_footprint`、`dyld`、`TTID` 等）——它们是行业通用词，翻译反而增加理解成本
- 埋点与工具代码**绝不抛错、绝不阻塞**：不能因为自身失败而影响被观测对象
- 任何缓冲都要**定容**。一个用来发现内存泄漏的工具，自己绝不能泄漏内存
- 能力不可用时明确说「不可用」，不要静默降级或给假数据

---

## 适合上手的第一个 PR

按难度从低到高：

| 难度 | 方向 |
|---|---|
| ⭐ | 补一个 `plugins/mobile-apm/tests/` 下的测试用例 |
| ⭐⭐ | 修 `docs/` 里的**文档失真**（哪个数字和实测对不上，请直接开 issue 指出） |
| ⭐⭐ | 给某个脚本补 `--help` 说明，或把裸 traceback 改成友好报错 |
| ⭐⭐⭐ | 加一个新的测量 profile（需要真机，**先开 issue 讨论口径**） |
| ⭐⭐⭐⭐ | Android / 鸿蒙 侧适配（缺口明确，但需要设备验证） |

最后提醒一句：**文档失真类 PR 特别欢迎**。这个项目里「文档说的」和「实际测的」偶尔会对不上，把它们抓出来是实打实的贡献。

---

## 我怎么用你的 PR

会先看三件事：

1. 数字能不能追溯到具体命令/设备/commit
2. `make test` 是否绿
3. 有没有超出能力边界的地方被写成了「已支持」

第 3 条我会直接问，不会默默改掉你的结论。

---

## 相关文档

| | |
|---|---|
| [使用指南](docs/usage-guide.md) | 怎么用 |
| [项目约定](AGENTS.md) | 给 AI Agent 看的完整铁律 |
| [路线图](ROADMAP.md) | 现状与**发布前置条件** |
| [交接文档](HANDOFF.md) | 踩过的坑 |
