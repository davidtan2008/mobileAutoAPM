---
description: 在当前工程初始化 APM 体系（建 .apm/ 目录、体检工具链、采集首轮基线）
---


在当前工程初始化 APM 工作区，为后续的自主闭环建立地基。

## 执行步骤

1. **环境体检**，产出能力就绪度：
   ```bash
   python3 ".claude/skills/_apm/scripts/apm_doctor.py" --json
   ```
   把结果写入 `.apm/baseline/env.json`。
   **若必需工具缺失，先如实报告并给出安装命令**（不要跳过、不要假装可用）。

2. **识别工程类型**，据此决定默认测量手段：
   - 有 `*.xcodeproj` / `*.xcworkspace` → iOS 原生
   - 有 `package.json` 且依赖含 `react-native` → RN（再看 `ios/`、`android/`、`harmony/`）
   - 有 `build-profile.json5` / `oh-package.json5` → 鸿蒙

3. **建立目录结构**：
   ```
   .apm/
   ├── baseline/     # 基线数据（应并入版本库）
   ├── runs/         # 每次测量（建议 gitignore）
   ├── issues/       # 发现的问题
   └── state.json    # 闭环状态
   ```

4. **写入 `state.json`**：
   ```json
   {"phase": "Phase 1 基线", "current_issue": null, "project_type": "<识别结果>",
    "updated_at": "<ISO时间>"}
   ```

5. **采集首轮基线**：调用 `apm-profiler` 子 Agent。启动任务优先使用标准
   `apm_measure.py --profile ios-native-startup`，默认至少 **5 次**；采集完成后
   必须运行 `apm_diagnose.py`。诊断退出 `2` 时不要写入「可信基线」，先修测量。
   通过后用 `apm_baseline.py record --require-healthy` 写入基线。

6. **建议 gitignore**：`.apm/runs/`（原始数据体积大），但 `.apm/baseline/` **必须入库**。

## 完成后报告

- 能力就绪度摘要（哪些能跑、哪些受限）
- 工程类型识别结果
- 基线采集的指标与数值（含样本数、离散度）
- 下一步建议（通常是"告诉我你想优化什么"）

**注意**：绝不假设工具可用；绝不伪造基线数据。
