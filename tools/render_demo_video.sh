#!/usr/bin/env bash
# 把 slides/render.json 里列出的 PNG 拼成 MP4。
#
# 为什么用 concat filter 而不是 concat demuxer：
# demuxer 会给最后一帧重复计一次时长（实测 68s 的脚本被编码成 79s），
# filter 模式下每段时长由各自的 -t 精确决定，总时长 = 各段之和，可复现。
set -euo pipefail

slides_dir="${1:?用法: render_demo_video.sh <slides_dir> [output.mp4]}"
output="${2:-$(dirname "$slides_dir")/translation-persistence.mp4}"
manifest="$slides_dir/render.json"

command -v ffmpeg >/dev/null || { echo "⛔ 缺少 ffmpeg"; exit 1; }
[ -f "$manifest" ] || { echo "⛔ 缺少渲染清单：$manifest（先跑 render_demo_slides.py）"; exit 1; }

inputs=()
labels=()
index=0
while IFS=$'\t' read -r png duration; do
  [ -n "$png" ] || continue
  [ -f "$slides_dir/$png" ] || { echo "⛔ 缺少幻灯片：$slides_dir/$png"; exit 1; }
  inputs+=(-loop 1 -framerate 30 -t "$duration" -i "$slides_dir/$png")
  labels+=("[$index:v]")
  index=$((index + 1))
done < <(python3 -c "
import json, sys
for item in json.load(open(sys.argv[1])):
    print(item['png'] + '\t' + str(item['duration']))
" "$manifest")

[ "${#inputs[@]}" -gt 0 ] || { echo "⛔ 渲染清单为空"; exit 1; }

filter="$(IFS=; echo "${labels[*]}")concat=n=${#labels[@]}:v=1:a=0,format=yuv420p[v]"

ffmpeg -y -loglevel error "${inputs[@]}" \
  -filter_complex "$filter" -map "[v]" -r 30 -movflags +faststart "$output"

echo "✅ $output"
ffprobe -v error -show_entries format=duration,size -of default=nw=1 "$output"
