# 成功闭环案例：结束任务的译文持久化

> 这是项目第一个“目标达成且验证过”的自主闭环。目标不是性能数字，而是修复一个确定性的用户数据丢失问题。

## 用户可见问题

在已结束的会议记录详情页点击“翻译成我的语言”：

1. 译文当场显示，UI 看起来成功；
2. 返回历史再进入同一任务；
3. 译文全部消失。

影响：所有多语会议记录都可能丢失人工触发生成的译文。

## 发现与定位

只读探索代理从代码路径定位到：

```text
TaskDetailView.handleTranslated
  → 内存 current.entries[index].translatedText = text（UI 立刻显示）
  → TaskStore.append（被 ended guard 直接 return，没有落盘）
```

`InMemoryTaskStore` 和 `SwiftDataTaskStore` 都有相同规则：ended 任务不能 append transcript。

## 先证明它坏

在修复前新增两条回归测试：

- `endedTaskTranslationBackfillInMemory`
- `endedTaskTranslationBackfillInSwiftData`

结果：

```text
3 passed, 2 failed
期望 "六点见"，实际读回 ""
```

这一步证明问题不是静态猜测。

## 单变量修复

新增专用接口：

```swift
func updateTranslations(taskID: UUID, entries: [TranscriptEntry]) async
```

约束：

- 只更新已有 entry 的 `translatedText`；
- 允许作用于 ended 任务；
- 普通 `append` 继续拒绝 ended 任务；
- `DeferredTaskStore` 只做透明转发；
- 不同时修改导出按钮或其他翻译路径。

## 验证

```text
TaskStoreTests：5 passed, 0 failed
全部单元测试：120 passed, 0 failed
Release device build：SUCCEEDED
```

目标工程 commit：

```text
43e6576 fix: persist translations for ended tasks
```

工件：

- `Duiyi/Features/History/TaskDetailView.swift`
- `Duiyi/Domain/TaskSession.swift`
- `Duiyi/Services/Persistence/{InMemoryTaskStore,SwiftDataTaskStore,DeferredTaskStore}.swift`
- `DuiyiTests/TaskStoreTests.swift`
- 被观测工程 `.apm/issues/ISSUE-FUNC-001-ended-translation-not-persisted.json`

## 边界

- 这是模拟器上的确定性功能闭环，不宣称完成真机 UI 自动化；
- 真机 UI test runner 仍被免费 provisioning App 数量上限阻塞；
- `ExportShareButton` 的同页导出刷新是独立后续项，没有混入本次修复；
- 这次闭环不依赖性能基线，因为目标是数据是否丢失，可用确定性测试直接验证。
