# 60–90 秒 Demo 脚本：译文持久化闭环

> 这是发布候选脚本，尚未录制。录制时只展示真实命令与真实结果。

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

红测结果：

```text
3 passed, 2 failed
期望 "六点见"，实际读回 ""
```

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
TaskStoreTests：5 passed, 0 failed
全部单元测试：120 passed, 0 failed
Release device build：SUCCEEDED
```

## 1:15–1:25 — 边界

> 这是确定性数据丢失修复，所以用红/绿测试验证，不拿模拟器性能数字冒充真机结论。
> 真机 UI runner 仍受免费 provisioning 数量上限阻塞；另一个 UI 可点击性问题保持 open，
> 没有混进本次成功案例。

## 录制检查

- 所有数字必须来自当次命令输出；
- 不展示失败样本被隐藏；
- 不使用 T1 的 200ms 不可达结论作为成功素材；
- 成片人工审阅后再考虑发布。
