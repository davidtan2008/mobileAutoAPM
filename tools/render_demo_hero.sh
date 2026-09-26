#!/usr/bin/env bash
# 从 slides/render.json 生成 README 首屏素材：静态拼图 + 循环动图。
#
# 为什么从 render.json 生成，而不是从 MP4 抽帧：
# 抽帧必须硬编码时间戳（"第 3 页在 30s 处"）。一旦某页时长被改，
# 图会**悄悄错位**——展示的是错的页，而且没人看得出来。
# 直接读渲染清单，页数与时长变化都会自动跟随。
#
# 为什么同时产出 GIF 和 PNG：
# GitHub 正文列宽约 1012px。2880px 的拼图会被压到每格 337px，
# 字已经看不清了；GIF 只有 760px 宽，按原尺寸显示而且会动。
# 动图当首屏，拼图留给证据包（有些环境只显示 GIF 首帧）。
set -euo pipefail

slides_dir="${1:?用法: render_demo_hero.sh <slides_dir> [out_dir]}"
out_dir="${2:-$(dirname "$(dirname "$slides_dir")")}"
# concat demuxer 的相对路径是相对**清单文件**解析的，不是相对 cwd。
# 统一转绝对路径，否则换目录调用就找不到幻灯片。
abspath() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s\n' "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")" ;;
  esac
}
slides_dir="$(abspath "$slides_dir")"
out_dir="$(abspath "$out_dir")"
mkdir -p "$out_dir"
manifest="$slides_dir/render.json"

# 配色必须与 render_demo_slides.py 的设计 token 一致
BG="0x0a0f1f"
CARD="0x12192c"
ACCENT="0xfdc856"
MUTED="0x7d8aa3"

COLS=3
CELL_W=960
CELL_H=540
GIF_W=760
# 一页 12s 在动图里压到 2.4s；按清单里的真实时长折算，改时长不用改这里
GIF_TIME_SCALE=0.2
GIF_TIME_MIN=1.5

# 引导格文案（这个闭环专属；换案例时改这四行）
CTA_TITLE="${HERO_CTA_TITLE:-完整闭环 · 59 秒}"
CTA_SUB="${HERO_CTA_SUB:-译文持久化修复}"
CTA_NOTE="${HERO_CTA_NOTE:-真机 123/123 · 符号化 20/20 帧}"

command -v ffmpeg >/dev/null || { echo "⛔ 缺少 ffmpeg"; exit 1; }
[ -f "$manifest" ] || { echo "⛔ 缺少渲染清单：$manifest（先跑 make demo-video）"; exit 1; }

# 中文字体：drawtext 在本机 ffmpeg 上没有 fontindex 选项，用不了 .ttc 集合
font=""
for candidate in \
  "${HERO_FONT:-}" \
  /System/Library/Fonts/Supplemental/Arial\ Unicode.ttf \
  /System/Library/Fonts/Hiragino\ Sans\ GB.ttc \
  /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
do
  [ -n "$candidate" ] && [ -f "$candidate" ] && { font="$candidate"; break; }
done
[ -n "$font" ] || { echo "⛔ 找不到中文字体，可用 HERO_FONT=/path/to/font.ttf 指定"; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# ── 读清单 ──────────────────────────────────────────────
slides=()
while IFS= read -r png; do
  [ -n "$png" ] || continue
  [ -f "$slides_dir/$png" ] || { echo "⛔ 缺少幻灯片：$slides_dir/$png"; exit 1; }
  slides+=("$png")
done < <(python3 -c "
import json, sys
for item in json.load(open(sys.argv[1])):
    print(item['png'])
" "$manifest")

[ "${#slides[@]}" -gt 0 ] || { echo "⛔ 渲染清单为空"; exit 1; }

# 动图每段时长（按清单真实时长折算）
gif_durations=()
while IFS=$'\t' read -r _png duration; do
  [ -n "$_png" ] || continue
  d="$(python3 -c "print(max($GIF_TIME_MIN, round($duration * $GIF_TIME_SCALE, 1)))")"
  gif_durations+=("$d")
done < <(python3 -c "
import json, sys
for item in json.load(open(sys.argv[1])):
    print(item['png'] + '\t' + str(item['duration']))
" "$manifest")

# ── 引导格 ──────────────────────────────────────────────
cta="$tmp/cta.png"
ffmpeg -y -loglevel error -f lavfi -i "color=c=$BG:s=${CELL_W}x${CELL_H}" -frames:v 1 \
  -vf "drawbox=x=60:y=150:w=$((CELL_W - 120)):h=250:color=$CARD@1:t=fill,\
drawtext=fontfile='$font':text='$CTA_TITLE':fontcolor=$ACCENT:fontsize=60:x=(w-tw)/2:y=188,\
drawtext=fontfile='$font':text='$CTA_SUB':fontcolor=0xffffff:fontsize=44:x=(w-tw)/2:y=272,\
drawtext=fontfile='$font':text='$CTA_NOTE':fontcolor=$MUTED:fontsize=30:x=(w-tw)/2:y=342" \
  "$cta"

# ── 静态拼图（3 列，末行不足处补背景色） ────────────────
total=$((${#slides[@]} + 1))          # 幻灯片 + 引导格
rows=$(((total + COLS - 1) / COLS))
slots=$((rows * COLS))

inputs=()
labels=()
# 幻灯片是 1920x1080，引导格/占位格是 960x540 —— 尺寸不统一 hstack 会直接报错。
# 所以每个输入都先过一遍 scale 归一到格子尺寸。
for i in $(seq 0 $((slots - 1))); do
  if [ "$i" -lt "${#slides[@]}" ]; then
    inputs+=(-i "$slides_dir/${slides[$i]}")
  elif [ "$i" -eq "${#slides[@]}" ]; then
    inputs+=(-i "$cta")
  else
    inputs+=(-f lavfi -i "color=c=$BG:s=${CELL_W}x${CELL_H}")
  fi
  labels+=("[$i:v]scale=${CELL_W}:${CELL_H}[cell${i}]")
done

# 注意：这里逐段显式拼接。不要用 `IFS=;` —— 那实际是「把 IFS 设为空」
# （`;` 是命令结束符，不是赋值内容），行与行之间会丢掉分隔符。
# render_demo_video.sh 里的 `IFS=;` 能跑通是因为它要的输入列表本来就不需要分隔符。
# 第一段：逐个输入归一到格子尺寸，产出 [cellN]
chain=""
for i in $(seq 0 $((slots - 1))); do
  [ -n "$chain" ] && chain="${chain};"
  chain="${chain}${labels[$i]}"
done

# 第二段：每行 hstack
row_refs=""
for r in $(seq 0 $((rows - 1))); do
  start=$((r * COLS))
  row_in=""
  for c in $(seq 0 $((COLS - 1))); do
    row_in="${row_in}[cell$((start + c))]"
  done
  chain="${chain};${row_in}hstack=inputs=${COLS}[row${r}]"
  row_refs="${row_refs}[row${r}]"
done

# 第三段：所有行 vstack
chain="${chain};${row_refs}vstack=inputs=${rows}[full]"

png_out="$out_dir/hero-panels.png"
ffmpeg -y -loglevel error "${inputs[@]}" \
  -filter_complex "$chain" \
  -map "[full]" -frames:v 1 -update 1 -pix_fmt rgb24 "$png_out"

# ── 循环动图 ────────────────────────────────────────────
# 末段重复一次：让 GIF 循环时最后一步不会闪回第一帧
cta_duration="${gif_durations[0]:-2.4}"
list="$tmp/gif.txt"
: > "$list"
for i in "${!slides[@]}"; do
  printf 'file %s/%s\nduration %s\n' "$slides_dir" "${slides[$i]}" "${gif_durations[$i]}" >> "$list"
done
printf 'file %s\nduration %s\n' "$cta" "$cta_duration" >> "$list"
printf 'file %s\n' "$cta" >> "$list"

# 这里**不能**用经典的 split + palettegen/paletteuse 压体积。
# 那套写法在本机 ffmpeg 上会破坏时序：6 段 16.4s 的片子被压成 48 帧 / 4.8s，
# 也就是每页只闪 0.8 秒。实测对比：
#   split+palettegen+fps=10 → 48 帧 / 4.8s   ❌ 时序被毁
#   split+palettegen+vfr    → 2 帧            ❌ 更糟
#   无调色板 + vfr          → 7 帧 / 16.6s   ✅
# 内容本来就是 6 张静态图，GIF 每帧本来就支持长 delay，
# 所以正确做法是 -vsync vfr 让每段只留一帧，而不是逐帧复制再压色。
gif_out="$out_dir/hero.gif"
ffmpeg -y -loglevel error -f concat -safe 0 -i "$list" \
  -vf "scale=${GIF_W}:-1:flags=lanczos" \
  -vsync vfr -loop 0 "$gif_out"

# 断言时序：concat 的每段时长必须真的落到 GIF 的帧时间戳上。
# 没有这道断言，上面那类「文件能生成、能播、但时序是错的」问题会静默溜过去。
expected_pts="$(python3 -c "
import sys
durations = [float(x) for x in sys.argv[1:]]
pts, acc = [], 0.0
for d in durations:
    pts.append(round(acc, 3)); acc += d
pts.append(round(acc, 3))   # 末尾重复帧
print(','.join(f'{p:.3f}' for p in pts))
" "${gif_durations[@]}" "$cta_duration")"
actual_pts="$(ffprobe -v error -select_streams v -show_entries frame=pts_time \
  -of csv=p=0 "$gif_out" 2>/dev/null | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')"
# ffprobe 给的是 6 位小数，统一成 3 位再比，否则纯格式差异会被当成时序错误
actual_pts="$(python3 -c "
import sys
raw = [x for x in sys.argv[1].split(',') if x.strip()]
print(','.join(f'{float(x):.3f}' for x in raw))
" "$actual_pts")"

if [ "$actual_pts" != "$expected_pts" ]; then
  # 坏产物必须删掉，不能留在磁盘上 —— 一个「能播但节奏是错的」GIF
  # 看起来完全正常，最容易被误当成成品提交上去。
  rm -f "$gif_out"
  echo "⛔ GIF 时序与预期不符，已删除 $gif_out" >&2
  echo "   预期帧时间戳: $expected_pts" >&2
  echo "   实际帧时间戳: ${actual_pts:-（读不到）}" >&2
  exit 1
fi

echo "✅ $png_out  （$rows 行 × $COLS 列，源 $total 格）"
ffprobe -v error -show_entries stream=width,height -of default=nw=1 "$png_out"
echo "✅ $gif_out  （${#slides[@]} 页 + 引导格，时序已断言）"
ffprobe -v error -show_entries stream=width,height -of default=nw=1 "$gif_out"
echo "   帧时间戳: $actual_pts"
ls -la "$png_out" "$gif_out" | awk '{printf "   %-6.2f MB  %s\n", $5/1048576, $NF}'
