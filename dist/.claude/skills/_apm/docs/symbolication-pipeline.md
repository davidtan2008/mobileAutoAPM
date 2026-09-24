# 符号化流水线接入

> **前提认知**：符号化链路没修好之前，崩溃分析基本是白做的。
> 未符号化的栈只有地址，看不出任何根因 —— 此时"分析崩溃"是在浪费时间和 token。

配套工具（本插件自带，零第三方依赖）：

| 工具 | 用途 |
|---|---|
| `scripts/rn_symbolicate.py` | 堆栈符号化 + **两张 map 合成**（核心） |
| `scripts/rn_build_symbols.py` | 构建期产出 + **CI 门禁校验** |

---

## 一、四个必须知道的坑

### 坑 1 ⚠️ iOS 默认不生成 sourcemap

这是最高频的遗漏。Xcode 的 "Bundle React Native code and images" 阶段
**默认只产出 bundle，不产出 sourcemap** —— 于是 iOS 线上崩溃永远还原不出行号。

**修复**：在工程的 Build Phases 里，把该阶段的 shell 脚本改成：

```bash
#!/bin/bash
set -e

export NODE_BINARY=$(command -v node)
export SOURCEMAP_FILE="$DERIVED_FILE_DIR/main.jsbundle.map"   # ← 关键：默认没有这一行

if [[ -f "$SRCROOT/../node_modules/react-native/scripts/react-native-xcode.sh" ]]; then
  "$SRCROOT/../node_modules/react-native/scripts/react-native-xcode.sh"
elif [[ -f "$SRCROOT/../node_modules/@react-native-community/cli/..." ]]; then
  # RN 0.71+ 新路径
  "$SRCROOT/../node_modules/react-native/scripts/react-native-xcode.sh"
else
  echo "找不到 react-native-xcode.sh" >&2
  exit 1
fi

# 把 sourcemap 复制到产物可归档的位置
if [[ -f "$SOURCEMAP_FILE" ]]; then
  cp "$SOURCEMAP_FILE" "${BUILT_PRODUCTS_DIR}/${PRODUCT_NAME}.app/main.jsbundle.map"
fi
```

> 改完后**必须验证**：跑一次 release 打包，确认 `.app` 里真的有 `main.jsbundle.map`
> 且**大小不为 0**。用 `rn_build_symbols.py verify` 一条命令就能确认。

### 坑 2 ⚠️ Hermes 需要两步合成

Hermes 的栈长这样：

```
TypeError: undefined is not an object
    at p@1:62000
```

`1:62000` **不是 JS 行号，是字节码位置**。要还原成源码行号必须两步：

```
.hbc.map   (字节码 → bundle JS)
    ↓ compose
metro.map  (bundle JS → 原始 TS/JS)
    ↓
合成后的单张 map
```

**只上传 metro.map 是不够的** —— 字节码位置在它里面查不到。

```bash
python3 scripts/rn_symbolicate.py compose \
    --outer index.android.bundle.hbc.map \
    --inner index.android.bundle.map \
    --out   index.android.bundle.composed.map
```

工具会在两张 map 不匹配时告警（这才是最常见的失败原因：上传了**上一次构建**的 map）。

### 坑 3 ⚠️ 0 字节的 sourcemap 占位文件是正常的

Hermes bundle 不是合法 JS，`sentry-cli` 会**跳过上传并创建一个 0 字节占位文件**
指向真实的 sourcemap。

**这是正常现象，不是上传失败 —— 不要在这里浪费时间 debug。**

但要注意区分：**如果所有 `.map` 都是 0 字节**，那说明 sourcemap 根本没生成（见坑 1）。

### 坑 4 ⚠️ 关联必须用 debug ID，不是版本号

热修（CodePush / EAS Update）之后，**版本号会错位** ——
拿版本号匹配 sourcemap 会静默取到错误的文件，符号化出来的行号是错的，
而且没有任何报错。

正确做法：每次构建生成唯一 **debug ID**，同时写入 bundle 与符号上传元数据。

---

## 二、各平台产物清单

### iOS

| 产物 | 必需 | 说明 |
|---|---|---|
| `*.app.dSYM` | ✅ | 原生崩溃符号化。必须 `DEBUG_INFORMATION_FORMAT=dwarf-with-dsym` |
| `main.jsbundle.map` | ✅ | ⚠️ 默认不生成，见坑 1 |
| `*.hbc.map` | Hermes 项目必需 | |

### Android

| 产物 | 必需 | 说明 |
|---|---|---|
| `mapping.txt` | ✅ | R8/ProGuard。**必须与 versionCode 严格对应** |
| `index.android.bundle.map` | ✅ | |
| `*.hbc.map` | Hermes 项目必需 | 在 `build.gradle` 里确保 `hermesFlags` 含 `-output-source-map` |

### HarmonyOS

| 产物 | 必需 | 说明 |
|---|---|---|
| SO 符号表 | ✅ | 按 **SO UUID** 匹配 |
| `nameCache` | ✅ | 鸿蒙混淆还原（对应 Android 的 mapping） |
| `sourceMaps` | 可选 | JS/TS 还原 |

---

## 三、CI 门禁

**把符号归档做成发版门禁** —— 否则漏一次就有一整版崩溃无法分析。

```bash
# 打包后立即校验，不齐全就卡住
python3 scripts/rn_build_symbols.py verify \
    --platform ios \
    --build-dir ios/build \
    --strict
```

退出码：`0` 齐全 / `1` 有问题 / `2` **必需文件缺失**（strict 模式）。

**性能回归门禁**与**符号门禁**应当一起加到发版流程：

```yaml
# .github/workflows/release.yml（节选）
- name: 校验符号文件（缺失则中止发布）
  run: python3 scripts/rn_build_symbols.py verify --platform ios --build-dir ios/build --strict

- name: 上传符号
  run: |
    # 合成 Hermes 两步 map
    if [ -f "$OUT/main.jsbundle.hbc.map" ]; then
      python3 scripts/rn_symbolicate.py compose \
        --outer "$OUT/main.jsbundle.hbc.map" \
        --inner "$OUT/main.jsbundle.map" \
        --out   "$OUT/main.jsbundle.composed.map"
    fi
    sentry-cli upload-dif "$OUT"                        # dSYM
    sentry-cli sourcemaps upload --debug-id-reference "$OUT"
```

---

## 四、排查崩溃时的用法

```bash
# 1) 先看 map 本身对不对
python3 scripts/rn_symbolicate.py inspect --map composed.map

# 2) 探测一个具体位置（确认 map 与堆栈对得上）
python3 scripts/rn_symbolicate.py inspect --map composed.map --probe 1:62000

# 3) 符号化整个堆栈
python3 scripts/rn_symbolicate.py symbolicate --map composed.map --stack crash.txt
```

**输出示例**：

```
TypeError: undefined is not an object (evaluating 'resp.data.items')
  fetchCart (/app/src/api/client.ts:17:4)
  onPressHandler (/app/src/App.tsx:42:8)
  at objc_msgSend (native)   # 无可用的位置信息

ℹ️  跳过 1 个原生帧（需由 dSYM / mapping.txt 符号化，不是 JS sourcemap 的职责）。
✅ 2 个 JS 帧全部还原。
```

---

## 五、符号化失败时的诊断顺序

工具会给出提示，按这个顺序排查：

| 症状 | 最可能的原因 | 怎么确认 |
|---|---|---|
| 只还原到 `index.bundle` | **忘了 compose** | 是否用了 `.hbc.map` 而不是合成后的 map |
| 该行没有映射 / 命中距离很大 | **map 与构建不匹配** | 核对 debug ID 是否一致 |
| 全部 `.map` 都是 0 字节 | **sourcemap 没生成** | iOS 见坑 1；Android 查 `hermesFlags` |
| 行号对不上但能还原 | **用了版本号关联** | 改用 debug ID |
| 崩溃栈里只有地址 | **dSYM 没归档** | `rn_build_symbols.py verify --platform ios` |

> ⚠️ **不要在这些排查上跳过步骤。**
> 拿一个不确定的符号化结果去分析根因，比不分析更糟 —— 会得出错误结论并据此改代码。
