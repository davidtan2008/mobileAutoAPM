#!/usr/bin/env python3
"""白屏 / 黑屏 / 纯色屏检测 —— 基于截图的像素分析。

**零第三方依赖**：纯标准库解码 PNG（zlib + struct），非 PNG 输入自动用系统
自带的 `sips` 归一化。这样在任何 macOS 机器上都能跑，无需 pip install。

用法:
  python3 apm_white_screen.py shot.png
  python3 apm_white_screen.py --json shot.png
  python3 apm_white_screen.py frame1.png frame2.png frame3.png   # 多帧（视频抽帧）
  python3 apm_white_screen.py --sample-step 4 big.png            # 大图加速

判定逻辑（三重证据，避免单一阈值误判）:
  - edge_density（相邻像素差异占比）低 → 没有内容纹理
  - luminance_std（亮度标准差）低     → 颜色单一
  - modal_ratio（主色占比）高         → 大面积同一颜色

三者同时满足才判为空白屏。纯色设计的页面可能触发 modal_ratio，但
edge_density 会把它救回来——因为真实页面总有文字/图标边缘。
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

# 判定阈值（可按项目调整）
EDGE_DENSITY_MAX = 0.002   # 边缘密度低于此值 = 无内容
LUM_STD_MAX = 3.0          # 亮度标准差低于此值 = 颜色单一
MODAL_RATIO_MIN = 0.90     # 主色占比高于此值 = 大面积同色
EDGE_DIFF_THRESHOLD = 12   # 相邻像素灰度差超过此值才算"边缘"（抗抗锯齿噪声）
WHITE_LUM = 235            # 高于此亮度视为白
DARK_LUM = 25              # 低于此亮度视为黑


# ---------------- PNG 解码（纯标准库） ----------------

def _unfilter(raw: bytes, stride: int, height: int, bpp: int) -> list[bytearray]:
    """还原 PNG 扫描线滤波。返回每行的字节数组。"""
    out: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) < stride:
            raise ValueError("PNG 数据不完整")
        if ft == 0:
            pass
        elif ft == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ValueError(f"未知 PNG 滤波类型 {ft}")
        out.append(line)
        prev = line
    return out


def _unpack_subbyte(line: bytearray, width: int, depth: int) -> list[int]:
    """把 1/2/4 bit 的行解包成每像素索引/灰度值。"""
    vals: list[int] = []
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    for byte in line:
        for k in range(per_byte):
            if len(vals) >= width:
                return vals
            shift = 8 - depth * (k + 1)
            vals.append((byte >> shift) & mask)
    return vals


def read_png_pixels(path: Path, sample_step: int = 1) -> tuple[int, int, list[tuple[int, int, int]]]:
    """读取 PNG，返回 (宽, 高, 采样后的 RGB 像素列表)。"""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是合法 PNG")

    pos = 8
    idat = bytearray()
    w = h = depth = ctype = None
    interlace = 0
    plte: bytes | None = None

    while pos < len(data):
        if pos + 8 > len(data):
            break
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        ctyp = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if ctyp == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctyp == b"PLTE":
            plte = chunk
        elif ctyp == b"IDAT":
            idat.extend(chunk)
        elif ctyp == b"IEND":
            break
        pos += 12 + ln

    if w is None:
        raise ValueError("PNG 缺少 IHDR")
    if interlace != 0:
        raise ValueError("不支持隔行扫描 PNG，请先用 sips 转换")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise ValueError(f"不支持的 PNG 颜色类型 {ctype}")
    if depth not in (1, 2, 4, 8, 16):
        raise ValueError(f"不支持的位深 {depth}")
    if depth < 8 and channels != 1:
        raise ValueError("位深<8 仅支持灰度/调色板 PNG")

    bits_per_pixel = channels * depth
    stride = (w * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)

    rows = _unfilter(zlib.decompress(bytes(idat)), stride, h, bpp)

    # 调色板
    palette: list[tuple[int, int, int]] = []
    if plte:
        for i in range(0, len(plte) - 2, 3):
            palette.append((plte[i], plte[i + 1], plte[i + 2]))

    pixels: list[tuple[int, int, int]] = []
    for y in range(0, h, sample_step):
        line = rows[y]
        if depth < 8:
            idxs = _unpack_subbyte(line, w, depth)
            for x in range(0, w, sample_step):
                v = idxs[x]
                if ctype == 3 and palette:
                    pixels.append(palette[v] if v < len(palette) else (0, 0, 0))
                else:
                    g = v * (255 // ((1 << depth) - 1))
                    pixels.append((g, g, g))
        elif depth == 8:
            for x in range(0, w, sample_step):
                o = x * channels
                if ctype == 3 and palette:
                    v = line[o]
                    pixels.append(palette[v] if v < len(palette) else (0, 0, 0))
                elif channels == 1:
                    g = line[o]
                    pixels.append((g, g, g))
                elif channels == 2:
                    g = line[o]
                    pixels.append((g, g, g))
                else:
                    pixels.append((line[o], line[o + 1], line[o + 2]))
        else:  # 16-bit：取高字节
            for x in range(0, w, sample_step):
                o = x * channels * 2
                if channels <= 2:
                    g = line[o]
                    pixels.append((g, g, g))
                else:
                    pixels.append((line[o], line[o + 2], line[o + 4]))
    return w, h, pixels


def ensure_png(path: Path) -> tuple[Path, Path | None]:
    """非 PNG 用 sips 转成临时 PNG。返回 (png路径, 临时文件或None)。"""
    if path.suffix.lower() == ".png":
        return path, None
    sips = shutil.which("sips")
    if not sips:
        raise SystemExit(f"{path}: 非 PNG 且系统无 sips，无法转换")
    tmp = Path(tempfile.mkdtemp()) / (path.stem + ".png")
    r = subprocess.run([sips, "-s", "format", "png", str(path), "--out", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        raise SystemExit(f"sips 转换失败: {r.stderr.strip()}")
    return tmp, tmp


# ---------------- 分析 ----------------

def analyze(pixels: list[tuple[int, int, int]], sample_step: int, width: int) -> dict:
    n = len(pixels)
    if n == 0:
        return {"error": "无像素"}

    lums = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    mean_lum = sum(lums) / n
    var = sum((x - mean_lum) ** 2 for x in lums) / n
    std_lum = var ** 0.5

    modal_color, modal_count = Counter(pixels).most_common(1)[0]
    modal_ratio = modal_count / n
    unique_colors = len(set(pixels))

    # 边缘密度：横向相邻采样点差异
    per_row = max(1, width // sample_step)
    edges = 0
    pairs = 0
    for i in range(1, n):
        if i % per_row == 0:
            continue  # 跨行不算
        if abs(lums[i] - lums[i - 1]) > EDGE_DIFF_THRESHOLD:
            edges += 1
        pairs += 1
    edge_density = edges / pairs if pairs else 0.0

    blank = (edge_density < EDGE_DENSITY_MAX
             and std_lum < LUM_STD_MAX
             and modal_ratio > MODAL_RATIO_MIN)

    if blank:
        if mean_lum > WHITE_LUM:
            verdict, label = "blank_white", "⚪ 白屏"
        elif mean_lum < DARK_LUM:
            verdict, label = "blank_dark", "⚫ 黑屏"
        else:
            verdict, label = "blank_uniform", "🔳 纯色屏（疑似未渲染）"
    else:
        verdict, label = "content_present", "✅ 有内容"

    return {
        "verdict": verdict, "label": label,
        "sampled_pixels": n,
        "modal_color": "#%02x%02x%02x" % modal_color,
        "modal_ratio": round(modal_ratio, 4),
        "unique_colors": unique_colors,
        "luminance_mean": round(mean_lum, 2),
        "luminance_std": round(std_lum, 3),
        "edge_density": round(edge_density, 5),
        "thresholds": {"edge_density_max": EDGE_DENSITY_MAX,
                       "luminance_std_max": LUM_STD_MAX,
                       "modal_ratio_min": MODAL_RATIO_MIN},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="白屏/黑屏/纯色屏检测")
    ap.add_argument("images", nargs="+", help="截图路径（PNG 或其他 sips 支持的格式）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sample-step", type=int, default=2,
                    help="采样步长，越大越快越省内存，默认 2")
    args = ap.parse_args()

    results = []
    for img in args.images:
        p = Path(img)
        if not p.exists():
            results.append({"file": img, "error": "文件不存在"})
            continue
        try:
            png, tmp = ensure_png(p)
            w, h, pixels = read_png_pixels(png, args.sample_step)
            info = analyze(pixels, args.sample_step, w)
            info.update({"file": img, "width": w, "height": h})
            results.append(info)
            if tmp:
                tmp.unlink(missing_ok=True)
                tmp.parent.rmdir()
        except Exception as e:  # noqa: BLE001
            results.append({"file": img, "error": f"{type(e).__name__}: {e}"})

    blanks = [r for r in results if r.get("verdict", "").startswith("blank")]

    if args.json:
        print(json.dumps({"results": results, "blank_count": len(blanks),
                          "total": len(results)}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if "error" in r:
                print(f"❌ {r['file']}: {r['error']}")
                continue
            print(f"{r['label']}  {r['file']}  ({r['width']}x{r['height']})")
            print(f"   主色 {r['modal_color']} 占比 {r['modal_ratio']:.1%} | "
                  f"边缘密度 {r['edge_density']:.5f} | 亮度 {r['luminance_mean']:.0f}±{r['luminance_std']:.2f} | "
                  f"色数 {r['unique_colors']}")
        print()
        if blanks:
            print(f"⛔ {len(blanks)}/{len(results)} 帧判定为空白屏")
        else:
            print(f"✅ {len(results)} 帧均检测到内容")

    return 1 if blanks else 0


if __name__ == "__main__":
    sys.exit(main())
