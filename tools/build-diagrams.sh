#!/usr/bin/env bash
# 把 docs/diagrams/*.svg 渲染成同尺寸 PNG，并维护一份内容清单。
#
# 为什么双份：
#   · SVG —— 网页/文档站里高清、可缩放、体积极小
#   · PNG —— GitHub README 与 issue 里可靠显示（某些环境不渲染 SVG）
#
# 为什么要有脚本 + 清单：
#   手改 SVG 后忘记重生成 PNG，两者就会漂移，而**漂移的图比没有图更糟**
#   （读者看到的是过期结论）。
#
# ⚠️ 为什么不用文件修改时间判断：**git checkout 会让所有文件拿到相同的 mtime**，
#    基于 mtime 的检查在全新 clone 里会误报。所以用 SVG 的内容哈希。
#
# 用法：
#   tools/build-diagrams.sh          # 渲染全部 + 更新清单
#   tools/build-diagrams.sh --check  # 校验 PNG 与 SVG 是否对应（CI 门禁用）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/docs/diagrams"
MANIFEST="$DIR/manifest.json"
SCALE="${SCALE:-2}"     # 2 倍分辨率，供高分屏与打印

if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "⛔ 需要 rsvg-convert：brew install librsvg" >&2
  exit 1
fi

svg_hash() {  # 内容哈希 —— 跨机器、跨 checkout 稳定
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

render_all() {
  local n=0
  # 保存原始 stdout 到 fd 3。
  # 下面那个 `{ ... } > "$MANIFEST"` 块会把块内**所有** stdout 写进清单文件 ——
  # 早先那句「✅ xxx.png」日志就漏在清单里，导致 manifest.json 不是合法 JSON
  # （外部工具用 jq 读会直接失败）。把日志显式引到 fd 3（原始 stdout）即可。
  exec 3>&1
  {
    echo "{"
    # ⚠️ 每个成员后面都要有逗号（最后一个除外）。早先 note 行漏了逗号，
    #    而 check_all 用 grep 解析、根本不校验 JSON，所以这个错一直没人发现 ——
    #    直到有外部工具用 jq 读它才炸。check_all 现在会先校验 JSON 合法性。
    echo "  \"note\": \"由 tools/build-diagrams.sh 维护。记录每个 SVG 的内容哈希，用于检测 PNG 是否落后。\","
    echo "  \"scale\": $SCALE,"
    echo "  \"diagrams\": {"
    local first=1
    for svg in "$DIR"/*.svg; do
      [ -e "$svg" ] || continue
      local base png width
      base="$(basename "$svg" .svg)"
      png="$DIR/$base.png"
      width=$(grep -oE 'width="[0-9]+"' "$svg" | head -1 | grep -oE '[0-9]+' || echo 1200)
      rsvg-convert -w $((width * SCALE)) -o "$png" "$svg"
      [ $first -eq 1 ] || echo ","
      first=0
      printf '    "%s": { "svgSha256": "%s", "width": %s }' "$base" "$(svg_hash "$svg")" "$width"
      echo "  ✅ $base.png  ($((width * SCALE))px)" >&3
      n=$((n + 1))
    done
    echo ""
    echo "  }"
    echo "}"
  } > "$MANIFEST"
  exec 3>&-
  echo "已渲染 $n 个图，清单 → docs/diagrams/manifest.json"
}

check_all() {
  [ -f "$MANIFEST" ] || { echo "⛔ 缺少 docs/diagrams/manifest.json，请先跑 tools/build-diagrams.sh" >&2; exit 1; }

  # 清单必须是**合法 JSON**。它要被外部工具读（jq / python），
  # 一旦有日志行漏进文件，那是最难发现的一类漂移 —— 本脚本自己用 grep 解析，
  # 所以照样能工作，坏的是所有外部读取方。
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$MANIFEST" 2>/dev/null; then
      echo "⛔ docs/diagrams/manifest.json 不是合法 JSON —— 请跑 tools/build-diagrams.sh 重新生成" >&2
      exit 1
    fi
  fi

  local stale=0 missing=0

  # 取清单里记录的哈希。
  # 早先用 `grep -A1 "\"$base\""` 抓 —— 那会**连带抓到下一行**的哈希
  # （两条记录相邻时），拼成两个哈希串，比较必然失败。
  # 以前中间恰好隔着日志行才没暴露。现在清单已是合法 JSON，直接解析它。
  recorded_hash() {
    local base="$1"
    if command -v python3 >/dev/null 2>&1; then
      python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
print(data.get('diagrams', {}).get(sys.argv[2], {}).get('svgSha256', ''))
" "$MANIFEST" "$base" 2>/dev/null || true
    else
      grep -oE "\"${base}\"[^}]*\"svgSha256\": \"[0-9a-f]{64}\"" "$MANIFEST" \
        | grep -oE '[0-9a-f]{64}' | head -1 || true
    fi
  }

  for svg in "$DIR"/*.svg; do
    [ -e "$svg" ] || continue
    local base png cur recorded
    base="$(basename "$svg" .svg)"
    png="$DIR/$base.png"
    cur="$(svg_hash "$svg")"
    recorded="$(recorded_hash "$base")"

    if [ ! -f "$png" ]; then
      echo "  ⛔ 缺 PNG：$base.png"; missing=$((missing + 1)); continue
    fi
    if [ -z "$recorded" ]; then
      # ⚠️ 必须写 ${base} 而不是 $base —— 后面紧跟的全角括号不是变量名分隔符，
      #    bash 会尝试展开一个叫 `base（...` 的变量，在 set -u 下直接报错。
      echo "  ⛔ 清单里没有记录：${base}（新增的图？）"; stale=$((stale + 1)); continue
    fi
    if [ "$cur" != "$recorded" ]; then
      echo "  ⛔ 已过期：${base}（SVG 内容变了，PNG 未重生成）"; stale=$((stale + 1))
    fi
  done

  if [ $((stale + missing)) -gt 0 ]; then
    echo ""
    echo "⛔ $((stale + missing)) 个图的 PNG 与 SVG 不对应 —— 请跑 tools/build-diagrams.sh 后提交" >&2
    exit 1
  fi
  echo "✅ 全部 PNG 与 SVG 对应（按内容哈希校验）"
}

if [ "${1:-}" = "--check" ]; then check_all; else render_all; fi
