#!/usr/bin/env python3
"""React Native / Hermes 堆栈符号化。

**零第三方依赖**（纯标准库实现 VLQ 解码与 sourcemap v3 解析）。

解决 RN 崩溃分析里最容易做错的一环：

    Hermes 的栈长这样：  p@1:132161
    这里的 1:132161 不是 JS 行号，是**字节码位置**。

正确还原需要**两步合成**：

    .hbc.map   (字节码 → bundle JS)
        ↓  compose
    metro.map  (bundle JS → 原始 TS/JS)
        ↓
    合成后的单张 map (字节码 → 原始源码)

**漏掉合成这一步，就永远还原不出正确行号。** 这是 RN 崩溃分析最经典的坑。

用法:
  # 符号化一个堆栈
  python3 rn_symbolicate.py symbolicate --map bundle.map --stack crash.txt

  # 合成 Hermes + Metro 两张 map（关键步骤）
  python3 rn_symbolicate.py compose \\
      --outer index.android.bundle.hbc.map \\
      --inner index.android.bundle.map \\
      --out composed.map

  # 检查一张 map 的基本信息
  python3 rn_symbolicate.py inspect --map bundle.map

  # 从管道读堆栈
  cat crash.txt | python3 rn_symbolicate.py symbolicate --map bundle.map
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Base64 VLQ —— sourcemap 的编码基础
# ---------------------------------------------------------------------------

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_INDEX = {c: i for i, c in enumerate(_B64)}


class SourceMapError(ValueError):
    """sourcemap 格式错误。"""


def decode_vlq(segment: str) -> list[int]:
    """解码一段 Base64 VLQ，返回整数列表。

    VLQ 规则：每个字符 6 bit —— 低 5 位是数据，第 6 位(0x20)是续位标志。
    数值最低位是符号位（1 表示负数），其余位是绝对值。
    """
    values: list[int] = []
    shift = 0
    acc = 0
    for ch in segment:
        digit = _B64_INDEX.get(ch)
        if digit is None:
            raise SourceMapError(f"非法的 Base64 VLQ 字符: {ch!r}")
        cont = digit & 0x20
        acc += (digit & 0x1F) << shift
        if cont:
            shift += 5
        else:
            negative = acc & 1
            acc >>= 1
            values.append(-acc if negative else acc)
            acc = 0
            shift = 0
    if shift != 0:
        raise SourceMapError("VLQ 序列在续位中途结束")
    return values


def encode_vlq(values: list[int]) -> str:
    """编码为 Base64 VLQ（用于合成后的 map 与测试往返）。"""
    out: list[str] = []
    for value in values:
        v = (-value << 1) | 1 if value < 0 else value << 1
        while True:
            digit = v & 0x1F
            v >>= 5
            if v:
                digit |= 0x20
            out.append(_B64[digit])
            if not v:
                break
    return "".join(out)


# ---------------------------------------------------------------------------
# SourceMap
# ---------------------------------------------------------------------------


@dataclass
class Mapping:
    """一条映射：生成的 (line, col) → 原始的 (source, line, col, name)。"""

    gen_line: int      # 1-based
    gen_col: int       # 0-based
    src_index: int
    src_line: int      # 0-based（sourcemap 内部约定）
    src_col: int
    name_index: int | None = None


@dataclass
class OriginalPosition:
    source: str
    line: int          # 1-based（对外）
    column: int        # 0-based
    name: str | None = None

    def __str__(self) -> str:
        loc = f"{self.source}:{self.line}:{self.column}"
        return f"{self.name} ({loc})" if self.name else loc


class SourceMap:
    """sourcemap v3 的解析与查询。"""

    def __init__(self, data: dict):
        if data.get("version") != 3:
            raise SourceMapError(f"只支持 sourcemap v3，收到 version={data.get('version')}")
        self.sources: list[str] = list(data.get("sources") or [])
        self.names: list[str] = list(data.get("names") or [])
        self.sources_content: list[str | None] = list(data.get("sourcesContent") or [])
        self.file: str | None = data.get("file")
        self._mappings_raw: str = data.get("mappings") or ""

        # gen_line(1-based) -> 排序后的 Mapping 列表
        self._lines: dict[int, list[Mapping]] = {}
        self._gen_cols: dict[int, list[int]] = {}
        self._parse()

    @classmethod
    def from_file(cls, path: str | Path) -> SourceMap:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SourceMapError(f"{p} 不是合法 JSON: {e}") from e
        if not isinstance(data, dict):
            raise SourceMapError(f"{p} 顶层不是对象")
        return cls(data)

    def to_dict(self, include_content: bool = False) -> dict:
        out: dict = {
            "version": 3,
            "sources": self.sources,
            "names": self.names,
            "mappings": self._encode_mappings(),
        }
        if self.file:
            out["file"] = self.file
        if include_content and self.sources_content:
            out["sourcesContent"] = self.sources_content
        return out

    # ---- 解析 ----

    def _parse(self) -> None:
        # sourcemap 的状态是**跨行累积**的（除 gen_col 每行重置）
        src_index = 0
        src_line = 0
        src_col = 0
        name_index = 0

        for line_no, line_str in enumerate(self._mappings_raw.split(";"), start=1):
            if not line_str:
                continue
            gen_col = 0
            for seg in line_str.split(","):
                if not seg:
                    continue
                vals = decode_vlq(seg)
                if len(vals) not in (1, 4, 5):
                    raise SourceMapError(
                        f"第 {line_no} 行的映射段有 {len(vals)} 个字段，应为 1/4/5"
                    )
                gen_col += vals[0]
                if len(vals) == 1:
                    # 只有生成列 —— 表示这段没有对应的源码位置
                    continue
                src_index += vals[1]
                src_line += vals[2]
                src_col += vals[3]
                nm: int | None = None
                if len(vals) == 5:
                    name_index += vals[4]
                    nm = name_index
                self._lines.setdefault(line_no, []).append(
                    Mapping(line_no, gen_col, src_index, src_line, src_col, nm)
                )

        for line_no, maps in self._lines.items():
            maps.sort(key=lambda m: m.gen_col)
            self._gen_cols[line_no] = [m.gen_col for m in maps]

    def _encode_mappings(self) -> str:
        if not self._lines:
            return ""
        max_line = max(self._lines)
        parts: list[str] = []
        prev_src_index = prev_src_line = prev_src_col = prev_name = 0
        for line_no in range(1, max_line + 1):
            maps = self._lines.get(line_no, [])
            segs: list[str] = []
            prev_gen_col = 0
            for m in maps:
                vals = [
                    m.gen_col - prev_gen_col,
                    m.src_index - prev_src_index,
                    m.src_line - prev_src_line,
                    m.src_col - prev_src_col,
                ]
                if m.name_index is not None:
                    vals.append(m.name_index - prev_name)
                    prev_name = m.name_index
                segs.append(encode_vlq(vals))
                prev_gen_col = m.gen_col
                prev_src_index = m.src_index
                prev_src_line = m.src_line
                prev_src_col = m.src_col
            parts.append(",".join(segs))
        return ";".join(parts)

    # ---- 查询 ----

    def lookup_ex(self, line: int, column: int) -> tuple[OriginalPosition | None, int | None]:
        """与 `lookup` 相同，但额外返回**命中的映射距查询列有多远**。

        距离是个有用的诊断信号：如果按标准回退语义命中的映射离查询列很远，
        说明这张 map 很可能与产生该堆栈的构建不匹配。**这里只报告，不擅自判定失败** ——
        因为合理的稀疏映射也会产生较大的距离，拍一个阈值等于在猜。
        """
        maps = self._lines.get(line)
        if not maps:
            return None, None
        cols = self._gen_cols[line]
        idx = bisect_right(cols, column) - 1
        if idx < 0:
            return None, None
        m = maps[idx]
        src = self.sources[m.src_index] if 0 <= m.src_index < len(self.sources) else "<unknown>"
        name = self.names[m.name_index] if m.name_index is not None and m.name_index < len(self.names) else None
        return OriginalPosition(src, m.src_line + 1, m.src_col, name), column - m.gen_col

    def lookup(self, line: int, column: int) -> OriginalPosition | None:
        """生成文件的 (line 1-based, column 0-based) → 原始位置。

        规则（与浏览器 DevTools 一致）：取同一行中 gen_col ≤ column 的**最后一个**映射。
        该行没有映射时返回 None —— 这是**唯一可靠的失配信号**。
        """
        return self.lookup_ex(line, column)[0]

    @property
    def mapping_count(self) -> int:
        return sum(len(v) for v in self._lines.values())

    def source_at(self, index: int) -> str | None:
        return self.sources[index] if 0 <= index < len(self.sources) else None


# ---------------------------------------------------------------------------
# 合成（compose）—— RN 符号化的关键步骤
# ---------------------------------------------------------------------------


# 命中距离超过这个值就认为"可疑"。**这是启发式，不是判决** ——
# 真实的稀疏映射也可能超过它，所以只报警告、不判失败。
SUSPICIOUS_COL_DISTANCE = 1000


def _basename(p: str) -> str:
    return p.replace("\\", "/").rsplit("/", 1)[-1]


def compose(outer: SourceMap, inner: SourceMap, warn: list[str] | None = None) -> SourceMap:
    """把 outer 的映射再往下追一层。

    用于 RN：**outer = Hermes .hbc.map（字节码→bundle），inner = Metro map（bundle→源码）**。

    对 outer 的每条映射：拿它的源位置去 inner 里查，查到就把 outer 的生成位置
    直接指向 inner 的原始位置 —— 得到「字节码 → 原始源码」的单张 map。
    """
    warnings = warn if warn is not None else []

    # 注意：这里**按位置查询**（用外层引用的源坐标去内层查生成坐标），
    # 而不是按文件名匹配 —— 外层引用 bundle 时常用相对路径、内层可能用绝对路径，
    # 按名字匹配会大面积失败。位置才是可靠的对齐依据。
    out_sources: list[str] = []
    out_names: list[str] = []
    out_lines: dict[int, list[Mapping]] = {}
    src_cache: dict[str, int] = {}
    name_cache: dict[str, int] = {}
    unresolved = 0
    # 命中距离的统计：距离普遍偏大是"两张 map 不匹配"的强信号
    max_distance = 0
    far_hits = 0

    def intern_source(s: str) -> int:
        if s not in src_cache:
            src_cache[s] = len(out_sources)
            out_sources.append(s)
        return src_cache[s]

    def intern_name(n: str) -> int:
        if n not in name_cache:
            name_cache[n] = len(out_names)
            out_names.append(n)
        return name_cache[n]

    for line_no, maps in outer._lines.items():
        for m in maps:
            outer_src = outer.source_at(m.src_index)
            if outer_src is None:
                unresolved += 1
                continue

            # 在外层引用的那个文件，去内层查它的原始位置
            pos, distance = inner.lookup_ex(m.src_line + 1, m.src_col)
            if pos is None:
                unresolved += 1
                continue
            if distance is not None:
                if distance > max_distance:
                    max_distance = distance
                if distance > SUSPICIOUS_COL_DISTANCE:
                    far_hits += 1

            out_lines.setdefault(line_no, []).append(
                Mapping(
                    gen_line=line_no,
                    gen_col=m.gen_col,
                    src_index=intern_source(pos.source),
                    src_line=pos.line - 1,   # 回到 0-based 内部约定
                    src_col=pos.column,
                    name_index=intern_name(pos.name) if pos.name else None,
                )
            )

    if unresolved:
        warnings.append(
            f"{unresolved} 条映射无法解析（外层引用的文件在内层 map 里找不到对应源）。"
            "常见原因：两张 map 不是同一次构建产生的。"
        )
    if far_hits:
        warnings.append(
            f"{far_hits} 条映射的命中距离超过 {SUSPICIOUS_COL_DISTANCE} 列"
            f"（最大 {max_distance}）。合成结果**可能不正确** —— "
            "请核对两张 map 是否来自同一次构建。"
        )

    result = SourceMap.__new__(SourceMap)
    result.sources = out_sources
    result.names = out_names
    result.sources_content = []
    result.file = outer.file
    result._mappings_raw = ""
    result._lines = {k: sorted(v, key=lambda x: x.gen_col) for k, v in out_lines.items()}
    result._gen_cols = {k: [m.gen_col for m in v] for k, v in result._lines.items()}
    return result


# ---------------------------------------------------------------------------
# 堆栈解析
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """一个栈帧。"""

    raw: str
    function: str | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None
    # Hermes 字节码位置：不是文件位置，需要 map 还原
    hermes_offset: tuple[int, int] | None = None
    is_native: bool = False

    def is_symbolicable(self) -> bool:
        return self.hermes_offset is not None or (self.file is not None and self.line is not None)


@dataclass
class StackTrace:
    header: str | None = None
    frames: list[Frame] = field(default_factory=list)
    # 非 JS 帧（原生部分），原样保留
    pending: list[str] = field(default_factory=list)


# Hermes 字节码帧：p@1:132161  或  anonymous@1:132161
_RE_HERMES = re.compile(r"^(?P<fn>[\w$.<>\[\]/-]*?)@(?P<line>\d+):(?P<col>\d+)$")

# Hermes 运行时真实输出（真机/Hermes CLI 抓下来的就是这种）：
#   at anonymous (address at /abs/path/app.hbc:1:49386)
#   at global (address at /abs/path/app.hbc:1:27660)
# 关键点：路径里**可能有空格**，且整段夹在括号里。
# 早先只认 "address at 1:132161"（无路径）这种简写形式，
# 真实输出走不到那条分支 → 帧被误当成普通 file:line:column，
# 于是 20/20 帧全部还原失败。**这是只有真跑 Hermes 才暴露的缺陷。**
_RE_ADDRESS_AT = re.compile(
    r"^address at (?P<path>.*):(?P<line>\d+):(?P<col>\d+)$"
)

# 标准 JS 帧：at foo (file.js:10:5) / at file.js:10:5 / at foo (native)
_RE_JS_AT = re.compile(
    r"^\s*at\s+(?:(?P<fn>[^\s(]+)\s+)?\(?(?P<loc>[^()]*?)\)?\s*$"
)
_RE_LOC = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)$")


def parse_frame(line: str) -> Frame | None:
    """解析单行栈帧。返回 None 表示不是可识别的帧。"""
    raw = line.rstrip()
    stripped = raw.strip()
    if not stripped:
        return None

    # Hermes 字节码形式（可能带前导 "at "）
    candidate = stripped[3:].strip() if stripped.startswith("at ") else stripped
    m = _RE_HERMES.match(candidate)
    if m and ":" in candidate and " " not in candidate:
        fn = m.group("fn") or None
        return Frame(
            raw=raw,
            function=fn,
            hermes_offset=(int(m.group("line")), int(m.group("col"))),
        )

    # 标准 JS 形式
    if stripped.startswith("at "):
        body = stripped[3:].strip()
        if body == "native" or body.endswith("(native)"):
            return Frame(raw=raw, is_native=True, function=body.replace("(native)", "").strip() or None)
        m2 = _RE_JS_AT.match(raw)
        if m2:
            fn = (m2.group("fn") or "").strip() or None
            loc = (m2.group("loc") or "").strip()

            # 真实 Hermes 输出：address at <path>:<line>:<col>
            # 必须**先于** _RE_LOC 判断 —— 路径里可能有空格/冒号，
            # 否则会被误当成普通 file:line:column 而丢掉字节码偏移。
            m_addr = _RE_ADDRESS_AT.match(loc)
            if m_addr:
                return Frame(
                    raw=raw, function=fn,
                    hermes_offset=(int(m_addr.group("line")), int(m_addr.group("col"))),
                )
            # 简写形式：address at 1:132161（无路径）
            m_shorthand = re.match(r"^address at (?P<line>\d+):(?P<col>\d+)$", loc)
            if m_shorthand:
                return Frame(
                    raw=raw, function=fn,
                    hermes_offset=(int(m_shorthand.group("line")), int(m_shorthand.group("col"))),
                )

            m3 = _RE_LOC.match(loc)
            if m3:
                file = m3.group("file")
                # "address at 1:132161" 这种也归为字节码偏移
                m4 = _RE_HERMES.match(file)
                if m4:
                    return Frame(raw=raw, function=fn, hermes_offset=(int(m4.group("line")), int(m4.group("col"))))
                return Frame(
                    raw=raw, function=fn, file=file,
                    line=int(m3.group("line")), column=int(m3.group("col")),
                )
            if loc and not fn:
                fn = loc
                loc = ""
            if loc:
                return Frame(raw=raw, function=fn, file=loc)
    return None


def parse_stack(text: str) -> StackTrace:
    """解析整段堆栈文本。"""
    st = StackTrace()
    for line in text.splitlines():
        if not line.strip():
            continue
        f = parse_frame(line)
        if f is not None:
            st.frames.append(f)
        elif st.frames:
            st.pending.append(line)
        elif st.header is None:
            st.header = line.strip()
        else:
            st.pending.append(line)
    return st


# ---------------------------------------------------------------------------
# 符号化
# ---------------------------------------------------------------------------


@dataclass
class SymbolicatedFrame:
    original: Frame
    position: OriginalPosition | None
    note: str | None = None
    # 命中的映射距查询列有多远。0 = 精确命中。
    # 明显偏大时提示"map 可能不是本次构建的"，但**不据此判定失败**。
    distance: int | None = None

    def format(self) -> str:
        if self.position is not None:
            line = f"  {self.position}"
            if self.distance is not None and self.distance > 1000:
                line += f"    # 距命中映射 {self.distance} 列，若符号可疑请核对 map 是否为本次构建"
            return line
        if self.note:
            return f"  {self.original.raw.strip()}   # {self.note}"
        return f"  {self.original.raw.strip()}"


class Symbolicator:
    """把堆栈帧还原成原始源码位置。"""

    def __init__(self, sourcemap: SourceMap):
        self.map = sourcemap

    def symbolicate_frame(self, frame: Frame) -> SymbolicatedFrame:
        # Hermes 字节码：把 (line, col) 当作生成位置查表
        if frame.hermes_offset is not None:
            line, col = frame.hermes_offset
            pos, distance = self.map.lookup_ex(line, col)
            if pos is None:
                return SymbolicatedFrame(
                    frame, None,
                    "map 的该行上没有映射。最常见原因：map 与产生该堆栈的构建不是同一次，"
                    "或 Hermes 项目忘了用 compose 合成 .hbc.map",
                )
            # Hermes 自身解析出的函数名往往比 map 里的更准，优先用它
            if frame.function and pos.name is None:
                pos.name = frame.function
            return SymbolicatedFrame(frame, pos, distance=distance)

        # 已经是源码位置：原样保留（交由调用方决定是否二次还原）
        if frame.file and frame.line is not None:
            return SymbolicatedFrame(frame, None, None)
        return SymbolicatedFrame(frame, None, "无可用的位置信息")

    def symbolicate(self, stack: StackTrace) -> list[SymbolicatedFrame]:
        return [self.symbolicate_frame(f) for f in stack.frames]

    def format_report(self, stack: StackTrace, title: str | None = None) -> str:
        out: list[str] = []
        if title:
            out.append(title)
        if stack.header:
            out.append(stack.header)
        for sf in self.symbolicate(stack):
            out.append(sf.format())
        if stack.pending:
            out.append("  # 未识别为 JS 帧的行（原生部分请交给 dSYM/mapping 符号化）:")
            out.extend(f"  {p.strip()}" for p in stack.pending)
        return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_symbolicate(args) -> int:
    sm = SourceMap.from_file(args.map)
    text = Path(args.stack).read_text(encoding="utf-8") if args.stack else sys.stdin.read()
    stack = parse_stack(text)

    if not stack.frames:
        print("⚠️  未从输入中解析出任何栈帧。", file=sys.stderr)
        print("    支持的格式：Hermes `p@1:132161` / 标准 `at fn (file.js:10:5)`", file=sys.stderr)
        return 1

    sym = Symbolicator(sm)
    print("=" * 72)
    print(f"  符号化结果  (map: {args.map}, {sm.mapping_count} 条映射)")
    print("=" * 72)
    print(sym.format_report(stack))

    # 原生帧（`at objc_msgSend (native)` 这类）本就该由 dSYM/mapping 处理，
    # 不计入未还原 —— 否则每次带原生帧的堆栈都会误报失败。
    js_frames = [f for f in stack.frames if not f.is_native]
    unresolved = sum(1 for f in js_frames if sym.symbolicate_frame(f).position is None)
    native_count = len(stack.frames) - len(js_frames)

    print()
    if native_count:
        print(f"ℹ️  跳过 {native_count} 个原生帧（需由 dSYM / mapping.txt 符号化，不是 JS sourcemap 的职责）。")
    if unresolved:
        print(f"⚠️  {unresolved}/{len(js_frames)} 个 JS 帧未能还原。")
        print("   检查：map 是否与产生该堆栈的构建一致？")
        print("   Hermes 项目需要先用 `compose` 合成 .hbc.map 与 metro map。")
        return 2
    if not js_frames:
        print("⚠️  输入中没有可符号化的 JS 帧。")
        return 1
    print(f"✅ {len(js_frames)} 个 JS 帧全部还原。")
    return 0


def cmd_compose(args) -> int:
    outer = SourceMap.from_file(args.outer)
    inner = SourceMap.from_file(args.inner)
    warnings: list[str] = []
    result = compose(outer, inner, warnings)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")

    print(f"✅ 已合成 → {out_path}")
    print(f"   外层 {outer.mapping_count} 条 + 内层 {inner.mapping_count} 条 → 合成 {result.mapping_count} 条")
    print(f"   源文件 {len(result.sources)} 个，函数名 {len(result.names)} 个")
    for w in warnings:
        print(f"   ⚠️  {w}")
    if result.mapping_count == 0:
        print("   ⛔ 合成结果为空 —— 两张 map 很可能不是同一次构建产生的。")
        return 1
    return 0


def cmd_inspect(args) -> int:
    sm = SourceMap.from_file(args.map)
    print(f"文件:      {args.map}")
    print("version:   3")
    print(f"file:      {sm.file or '(未指定)'}")
    print(f"映射条数:  {sm.mapping_count}")
    print(f"源文件:    {len(sm.sources)} 个")
    for s in sm.sources[:10]:
        print(f"           - {s}")
    if len(sm.sources) > 10:
        print(f"           … 还有 {len(sm.sources) - 10} 个")
    print(f"函数名:    {len(sm.names)} 个")
    if args.probe:
        line_s, col_s = args.probe.split(":")
        pos, distance = sm.lookup_ex(int(line_s), int(col_s))
        if pos is None:
            print(f"探测 {args.probe} → 未命中（该行没有映射）")
        else:
            print(f"探测 {args.probe} → {pos}")
            if distance:
                print(f"           距命中映射 {distance} 列（0 = 精确命中）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="React Native / Hermes 堆栈符号化")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("symbolicate", help="符号化堆栈")
    s.add_argument("--map", required=True, help="sourcemap 文件")
    s.add_argument("--stack", help="堆栈文件；省略则从 stdin 读")
    s.set_defaults(func=cmd_symbolicate)

    c = sub.add_parser("compose", help="合成两张 map（Hermes + Metro）")
    c.add_argument("--outer", required=True, help="外层 map（字节码 → bundle）")
    c.add_argument("--inner", required=True, help="内层 map（bundle → 源码）")
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_compose)

    i = sub.add_parser("inspect", help="查看 map 基本信息")
    i.add_argument("--map", required=True)
    i.add_argument("--probe", help="探测一个位置，格式 line:col（1-based 行）")
    i.set_defaults(func=cmd_inspect)

    args = ap.parse_args()

    # 输入错误要给出**可操作的提示**，不能把 traceback 甩给用户。
    # 这些是「用户传错了参数」，不是程序内部异常。
    try:
        return args.func(args)
    except SourceMapError as e:
        print(f"⛔ {e}", file=sys.stderr)
        print("   sourcemap 必须是 JSON 且 version=3。用 `inspect --map <file>` 可先确认。", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"⛔ 找不到文件：{e.filename}", file=sys.stderr)
        return 1
    except IsADirectoryError as e:
        print(f"⛔ 这是一个目录，不是文件：{e.filename}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as e:
        print(f"⛔ 文件不是 UTF-8 文本（若是 Hermes 字节码，请用对应的 .map）：{e}", file=sys.stderr)
        return 1
    except ValueError as e:
        # --probe 的 line:col 解析失败等
        print(f"⛔ 参数不合法：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
