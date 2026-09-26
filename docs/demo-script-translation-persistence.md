# 59 秒 Demo 脚本：译文持久化闭环

> **状态：已渲染为视频，尚未人工审阅。**
> 成片：`docs/demo/translation-persistence/translation-persistence.mp4`（59.0s / 1920×1080 / 5 页）
> 证据包与重渲染命令：`docs/demo/translation-persistence/README.md`
> 重渲染：`make demo-video`
>
> 下面这份脚本是视频的**文字版**，同时供人工配音或改版使用。
> 视频里的每个数字都由 `tools/render_demo_slides.py` 从命令原始输出 JSON 读取，
> 不存在手写结论。人工审阅通过前不得发布。

## 0:00–0:12 — 问题

字幕：

> 会议记录已经结束，用户点击“翻译成我的语言”。译文当场出现；返回历史再进入，译文却全部消失。

展示：目标工程 commit `43e6576` 与 issue：

```text
.apm/issues/ISSUE-FUNC-001-ended-translation-not-persisted.json
```

## 0:12–0:28 — 先证明它坏

```bash
mobilebuildmcp simulator test \
  --project-path Duiyi.xcodeproj --scheme Duiyi \
  --simulator-id 9FF1773D-E9DF-45FC-9732-875718B48935 \
  --configuration Debug \
  --json '{"extraArgs":["-only-testing:DuiyiTests/TaskStoreTests"]}'
```

红测结果（证据：`docs/demo/translation-persistence/red-test.json`）：

```text
passed=0  failed=1
Expectation failed: (reloaded?.entries.first?.translatedText → "") == "六点见"
```

> 注：这里是一次「只测新回归用例」的结果（1 条用例失败）。
> 早期在同一修复点上的整类运行是 `3 passed, 2 failed`。
> 视频采用前者，因为它对应可独立复现的单条命令。

## 0:28–0:42 — 根因

高亮三处：

```text
TaskDetailView：先改内存，看起来成功
InMemoryTaskStore.append：ended → return
SwiftDataTaskStore.append：ended → return
```

一句话：**UI 成功，持久化静默丢弃。**

## 0:42–0:58 — 单变量修复

展示新增接口：

```swift
func updateTranslations(taskID: UUID, entries: [TranscriptEntry]) async
```

强调：

- 只更新已有译文；
- 允许 ended 任务；
- 普通 append 仍然拒绝 ended；
- 没有同时改导出或其他路径。

## 0:58–1:15 — 验证

```text
模拟器 · 单元测试    120 passed, 0 failed
模拟器 · 定向测试      5 passed, 0 failed
真机 iPhone13/iOS26.7 123 passed, 0 failed, 0 skipped
Release device build SUCCEEDED
```

> 真机套件里含 `testSimultaneousListeningPressure`，跑了 2 分钟，
> **真实执行端侧听写而不是被 skip** —— 这是"模拟器与物理设备双证据"的意思。

## 关于边界

原第 6 页「边界与下一步」按需求移出视频。边界内容**没有丢**，仍在：

- `docs/case-study-translation-persistence.md`
- 被观测工程 `docs/20-ui-test-hittable-fix.md`
- 被观测工程 `.apm/issues/ISSUE-FUNC-001` / `ISSUE-UI-001` / `ISSUE-UI-002`

要恢复该页：`tools/render_demo_slides.py` 里 `build()` 末尾有一段被注释的
`slides.append(...)`，取消注释并重跑 `make demo-video` 即可。

## 录制检查

- 所有数字必须来自当次命令输出；
- 不展示失败样本被隐藏；
- 不使用 T1 的 200ms 不可达结论作为成功素材；
- 成片人工审阅后再考虑发布。
