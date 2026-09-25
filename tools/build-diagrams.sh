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
  {
    echo "{"
    echo "  \"note\": \"由 tools/build-diagrams.sh 维护。记录每个 SVG 的内容哈希，用于检测 PNG 是否落后。\""
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
      echo "  ✅ $base.png  ($((width * SCALE))px)"
      n=$((n + 1))
    done
    echo ""
    echo "  }"
    echo "}"
  } > "$MANIFEST"
  echo "已渲染 $n 个图，清单 → docs/diagrams/manifest.json"
}

check_all() {
  [ -f "$MANIFEST" ] || { echo "⛔ 缺少 docs/diagrams/manifest.json，请先跑 tools/build-diagrams.sh" >&2; exit 1; }

  local stale=0 missing=0
  for svg in "$DIR"/*.svg; do
    [ -e "$svg" ] || continue
    local base png cur recorded
    base="$(basename "$svg" .svg)"
    png="$DIR/$base.png"
    cur="$(svg_hash "$svg")"
    # 从清单里取记录值（不引 jq，保持零依赖）
    recorded=$(grep -A1 "\"$base\"" "$MANIFEST" | grep -oE '"svgSha256": "[0-9a-f]+"' | grep -oE '[0-9a-f]{64}' || true)

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
