# Demo 证据包：结束任务译文持久化

> 60–90 秒发布候选视频的**全部原料**。这里每个数字都来自一次真实命令输出，
> 没有手写结论、没有估算值、没有挑样本。

## 产物

| 文件 | 说明 |
|---|---|
| `translation-persistence.mp4` | 68.0s / 1920×1080 / 6 页（`make demo-video` 生成） |
| `slides/slide-0*.svg` | 视频源页面（文字与数字来自证据 JSON，非手写） |
| `red-test.json` | 修复前 commit `fb95d1b` 的失败测试原始输出 |
| `green-focused.json` | 修复后 `TaskStoreTests` 5/5 |
| `green-full-unit.json` | 修复后全部单元测试 120/120 |
| `green-release-build.json` | 修复后 Release device build |
| `commits.txt` | 红测 / 绿测各自所在 commit 的真实 `git show` 输出 |
| `fix-commit.txt` / `fix-diff.txt` | 目标工程 `43e6576` 的提交与差异（`fix-diff.txt` 仅去掉行尾空白以过 whitespace 门禁） |

## 证据要点（均可在 JSON 里逐条核对）

```text
红：passed=0 failed=1
    Expectation failed: (reloaded?.entries.first?.translatedText → "") == "六点见"
绿：TaskStoreTests 5 passed, 0 failed
    全部单元测试 120 passed, 0 failed
    Release device build SUCCEEDED
```

## 如何重新生成

```bash
# 1. 幻灯片（读取本目录的证据 JSON，不接受硬编码结论）
python3 tools/render_demo_slides.py \
  --evidence docs/demo/translation-persistence \
  --out docs/demo/translation-persistence/slides

# 2. 视频（需要 ffmpeg + rsvg-convert）
make demo-video
```

`make demo-video` 会重跑第 1、2 步并打印 `ffprobe` 时长/体积，可用于核对成片。

## 如何重新采集证据（不是重放）

红测必须在**修复前**的 commit 上跑：

```bash
# 修复前：临时 detached worktree，主工作树不受影响
git -C /path/to/realtimeTranslatorOptimize worktree add --detach /tmp/before-fix fb95d1b
# 在 /tmp/before-fix 放入 DuiyiTests/TranslationPersistenceRedTests.swift
mobilebuildmcp simulator test \
  --project-path /tmp/before-fix/Duiyi.xcodeproj --scheme Duiyi \
  --simulator-id 9FF1773D-E9DF-45FC-9732-875718B48935 --configuration Debug \
  --json '{"extraArgs":["-only-testing:DuiyiTests/TranslationPersistenceRedTests"]}' \
  --output json > red-test.json
```

绿测与构建在修复后 commit `43e6576` 的工作树上跑，见 `docs/demo-script-translation-persistence.md`。

## 边界（发布时必须一起说）

- 这是**确定性功能闭环**，用红/绿测试验证；没有用模拟器性能数字冒充真机结论；
- 真机 UI test runner 仍被免费 provisioning 的 App 数量上限阻塞；
- 模拟器 UI 套件另有 open 问题：`home-module-simultaneous` 存在但不可点击；
- 导出按钮同页刷新是独立后续项，未混入本次修复；
- T1 冷启动 200ms 已被最小 control（p50=210ms）拦截，**不得**用作成功素材。
