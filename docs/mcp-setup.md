# 工具与 MCP 配置（含本机实测）

> 全部为**本机实际验证过的结果**，不是抄文档。
> 最后更新：2026-09-24。

---

## 一、⚠️ 最重要的一条：网络决定一切

本机直连时：

| 目标 | 直连 | 走代理 |
|---|---|---|
| `github.com` / `api.github.com` | ❌ 超时 | ✅ 200 |
| `developerknowledge.googleapis.com` | ❌ 不可达 | ✅ 可达 |
| `mcp.sentry.dev` | ✅ 可达（需鉴权） | ✅ |
| npm registry | ✅ | ✅ |

**代理**：

```bash
export http_proxy=http://127.0.0.1:10987
export https_proxy=http://127.0.0.1:10987
```

⚠️ **代理不会自动对已配置的 MCP 生效**。需要走外网的 MCP（如 Firebase）
要单独在其配置里带上代理环境变量，或全局开启代理后再启动 Claude Code。

---

## 二、工具链 PATH 修复（本次的关键修复）

**问题**：`adb` / `hdc` 一直找不到，根因是 `~/.zshrc` 里的路径**过时** ——
应用已迁到 `/Volumes/DevDisk`，但配置还指向 `/Users/tanwei/Library/Android/sdk`
与 `/Applications/DevEco-Studio.app`。

**已修复** `~/.zshrc`（备份在 `~/.zshrc.bak-*`）：

```bash
# Android SDK
export ANDROID_HOME="/Volumes/DevDisk/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

# HarmonyOS (DevEco Studio)
export DEVECO_HOME="/Volumes/DevDisk/Applications/DevEco-Studio.app/Contents"
export HDC_PATH="$DEVECO_HOME/sdk/default/openharmony/toolchains"
export PATH="$HDC_PATH:$DEVECO_HOME/tools/ohpm/bin:$DEVECO_HOME/tools/hvigor/bin:$PATH"
```

**验证**（全新登录 shell）：

```
adb      → /Volumes/DevDisk/Library/Android/sdk/platform-tools/adb      1.0.41
hdc      → DevEco-Studio.app/.../openharmony/toolchains/hdc            3.2.0f
ohpm     → DevEco-Studio.app/Contents/tools/ohpm/bin/ohpm              26.0.0.630
hvigorw  → DevEco-Studio.app/Contents/tools/hvigor/bin/hvigorw         6.26.4
```

**能力矩阵变化**：Android 与鸿蒙从「⚠️ 受限」→「✅ 可执行」，
`apm_doctor.py` 现在报告 **9/9 全部就绪**。

其他工具位置：Xcode `/Volumes/DevDisk/Applications/Xcode.app`；
OpenHarmony SDK `/Users/tanwei/Library/OpenHarmony/Sdk`（含 10 / 12 两版）。

---

## 三、已装并连通的 MCP

| MCP | 作用 | 状态 |
|---|---|---|
| **`mobilebuildmcp`** | iOS 构建 / 模拟器 / rs-1 语义快照 / LLDB / 覆盖率（72 工具） | ✅ |
| **`agent-device`** | **跨平台设备驱动：iOS / Android / HarmonyOS / TV / web** | ✅ |
| **`chrome-devtools`** | RN Hermes 调试（CDP）、Web 性能 trace | ✅ |
| **`firebase`** | Crashlytics / Performance / Firestore | ✅ |
| `zread` / `web-search-prime` / `web-reader` / `zai` | 检索与图像 | ✅ |
| `sentry` | 崩溃与 issue 数据源 | ⚠️ 待 `/mcp` 完成 OAuth |
| `expo` | Expo 生态（EAS build / observe） | ⚠️ 待鉴权 |

安装命令：

```bash
claude mcp add mobilebuildmcp -s user -- npx -y mobilebuildmcp@latest mcp
claude mcp add agent-device   -s user -- agent-device mcp
```

### ⚠️ `mobilebuildmcp` 的两个坑

1. **v2.0.0 起启动命令必须带 `mcp` 子命令**，否则客户端连不上。
2. **默认只启用 `simulator` workflow**，其余需在工程的 `.xcodebuildmcp/config.yaml` 打开：
   ```yaml
   schemaVersion: 1
   enabledWorkflows: [simulator, ui-automation, debugging, coverage]
   ```

### ⚠️ `agent-device` 的鸿蒙支持有前提

README 明确说明：**鸿蒙是较新后端，只覆盖部分命令**。

```bash
agent-device capabilities --platform harmonyos   # 查实际支持哪些命令
agent-device capabilities --platform ios         # 对照：iOS 有 43 个命令
```

iOS 的 43 个命令里包含 `perf`、`network`、`logs`、`snapshot`、
**`react-native`**（对 RN 项目直接相关）。

鸿蒙侧依赖 `hdc`（已修好 PATH）与 ArkUI `uitest`。

---

## 四、已装插件

| 插件 | 内容 |
|---|---|
| **`mobile-apm`** | 本项目自建：7 技能 / 4 子 Agent / 4 命令 / 1 hook |
| **`firebase@firebase`** | `firebase-crashlytics`、`xcode-project-setup` 等 13 个官方 skill |
| `firebase@claude-plugins-official` | Firebase MCP 集成 |
| `expo@claude-plugins-official` | Expo 官方 skills（含 EAS observe / update insights） |
| `chrome-devtools-mcp@claude-plugins-official` | Chrome DevTools MCP |
| `swift-lsp` / `clangd-lsp` | 代码智能 |

```bash
claude plugin marketplace add firebase/agent-skills
claude plugin install firebase@firebase
```

---

## 五、仍需外部条件的两项

| 项 | 门槛 |
|---|---|
| **Sentry MCP** | 执行 `/mcp` 完成 OAuth（需 Sentry 账号） |
| **真机 / 模拟器验证 Android、鸿蒙** | `adb devices` / `hdc list targets` 目前都是空的，需接设备或起模拟器 |

⚠️ **Firebase 的适用性提醒**：Firebase 全家桶依赖 Google 基础设施，
国内需代理。且 **Crashlytics 依赖 GMS，华为设备与鸿蒙不可用**。
数据后端选型见 `plugins/mobile-apm/references/stack-selection.md` §0。
