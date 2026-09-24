#!/usr/bin/env bash
# 把 docs/diagrams/*.svg 渲染成同尺寸 PNG。
#
# 为什么要双份：
#   · SVG —— 网页/文档站里高清、可缩放、体积极小
#   · PNG —— GitHub README 与 issue 里可靠显示（某些环境不渲染 SVG）
#
# 为什么要有脚本：手改 SVG 后忘记重生成 PNG，两者就会漂移，
# 而漂移的图比没有图更糟（读者看到的是过期结论）。
#
# 用法：
#   tools/build-diagrams.sh          # 渲染全部
#   tools/build-diagrams.sh --check  # 只检查是否有 PNG 落后于 SVG（CI 门禁用）

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docs/diagrams"
SCALE="${SCALE:-2}"     # 2 倍分辨率，供高分屏与打印

if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "⛔ 需要 rsvg-convert：brew install librsvg" >&2
  exit 1
fi

stale=0
rendered=0

for svg in "$DIR"/*.svg; do
  [ -e "$svg" ] || continue
  png="${svg%.svg}.png"

  if [ "${1:-}" = "--check" ]; then
    if [ ! -f "$png" ] || [ "$svg" -nt "$png" ]; then
      echo "  ⛔ 过期：$(basename "$png")（SVG 比 PNG 新）"
      stale=$((stale + 1))
    fi
    continue
  fi

  width=$(grep -oE 'width="[0-9]+"' "$svg" | head -1 | grep -oE '[0-9]+' || echo 1200)
  rsvg-convert -w $((width * SCALE)) -o "$png" "$svg"
  echo "  ✅ $(basename "$png")  ($((width * SCALE))px)"
  rendered=$((rendered + 1))
done

if [ "${1:-}" = "--check" ]; then
  if [ "$stale" -gt 0 ]; then
    echo ""
    echo "⛔ 有 $stale 个 PNG 落后于 SVG —— 请跑 tools/build-diagrams.sh 后提交" >&2
    exit 1
  fi
  echo "✅ 全部 PNG 与 SVG 一致"
else
  echo "已渲染 $rendered 个图"
fi
