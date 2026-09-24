---
description: 分析崩溃并定位根因（符号化 → 聚类 → 排序 → 根因假设 → 修复 → 验证）
---


分析崩溃，定位根因，修复并验证。

参数：`$ARGUMENTS` —— 崩溃日志路径 / 崩溃平台 issue ID / 或留空走线上拉取。

## 执行步骤

1. **调用 `crash-triager` 子 Agent**，让它完成：
   - **先确认四层捕获机制是否完整**（ErrorBoundary / ErrorUtils / Promise rejection / 原生库）
     —— 有盲区要先报告，因为当前崩溃数据本身不完整
   - 符号化（⚠️ RN 注意 iOS 的 `SOURCEMAP_FILE` 与 Hermes `.hbc.map` 合成）
   - 聚类去重
   - 按 `影响用户数 × 严重度` 排序，**输出 top 3**
   - 给出根因假设 + 置信度

2. **区分崩溃类型**：
   - 用户说"莫名退出"且平台无记录 → **FOOM，转 `apm-memory`**，不要继续找崩溃栈
   - Hang（卡死）→ 转 `apm-render`
   - 普通崩溃 → 继续

3. **定位根因**（不许跳过）：
   - 必现 → LLDB 复现：
     ```bash
     mobilebuildmcp debugging attach --help
     mobilebuildmcp debugging add-breakpoint --help
     mobilebuildmcp debugging stack --help
     ```
   - 概率性 → 加日志/压力复现
   - **完成标准**：能指出具体哪一行、在什么条件下出错，且有证据

4. **修复**：单变量原则，记录"为什么这样改"。

5. **验证三件事**（缺一不可）：
   - 原崩溃**不再复现**（同前置条件）
   - 崩溃率下降（灰度/线上）
   - **功能没被改坏** → 调 `apm-autotest`

## 硬性约束

- **根因未确定就如实说"未确定"** + 给出下一步排查计划。**绝不编造根因。**
- 修复后必须能演示"改之前崩、改之后不崩"。
- 若根因在三方 SDK：给出升级/替代/调用侧防御的具体方案，说明取舍。

## 完成后

更新 `.apm/issues/ISSUE-NNN.json` 的 `status` 与 `root_cause_hypothesis`，
并向用户报告：根因、证据、修复方式、验证结果、**以及是否已消除该类崩溃的盲区**。
