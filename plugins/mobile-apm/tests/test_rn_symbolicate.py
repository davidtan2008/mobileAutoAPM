#!/usr/bin/env python3
"""rn_symbolicate 的测试。

用 `python3 -m unittest discover` 或直接 `python3 tests/test_rn_symbolicate.py` 运行。
零第三方依赖。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from rn_symbolicate import (
    SourceMap,
    SourceMapError,
    Symbolicator,
    compose,
    decode_vlq,
    encode_vlq,
    parse_frame,
    parse_stack,
)


class TestVLQ(unittest.TestCase):
    def test_已知值(self):
        # 这些是 sourcemap 规范里的例子，用来验证实现没有偏
        self.assertEqual(decode_vlq("A"), [0])
        self.assertEqual(decode_vlq("C"), [1])
        self.assertEqual(decode_vlq("D"), [-1])
        self.assertEqual(decode_vlq("gB"), [16])
        self.assertEqual(decode_vlq("hB"), [-16])
        self.assertEqual(decode_vlq("AA"), [0, 0])
        self.assertEqual(decode_vlq("AAAA"), [0, 0, 0, 0])

    def test_往返一致(self):
        cases = [
            [0],
            [1],
            [-1],
            [16],
            [-16],
            [1000],
            [-1000],
            [123456],
            [1, 0, -5, 300, 0],
            [0, 0, 0, 0, 0],
        ]
        for vals in cases:
            with self.subTest(vals=vals):
                self.assertEqual(decode_vlq(encode_vlq(vals)), vals)

    def test_非法字符报错(self):
        with self.assertRaises(SourceMapError):
            decode_vlq("A!B")

    def test_续位中途结束报错(self):
        # 'g' 有续位标志但没有后续字符
        with self.assertRaises(SourceMapError):
            decode_vlq("g")


def make_map(sources, names, mappings, file=None):
    d = {"version": 3, "sources": sources, "names": names, "mappings": mappings}
    if file:
        d["file"] = file
    return SourceMap(d)


class TestSourceMap(unittest.TestCase):
    def test_解析与查询(self):
        # 生成文件第 1 行：col 0 → src0 line0 col0；col 50 → src0 line0 col20
        mappings = encode_vlq([0, 0, 0, 0]) + "," + encode_vlq([50, 0, 0, 20])
        sm = make_map(["a.ts"], [], mappings)

        p0 = sm.lookup(1, 0)
        self.assertIsNotNone(p0)
        self.assertEqual((p0.source, p0.line, p0.column), ("a.ts", 1, 0))

        # col 50 命中第二条
        p50 = sm.lookup(1, 50)
        self.assertEqual(p50.column, 20)

        # col 49 应回退到第一条（取 gen_col ≤ col 的最后一个）
        p49 = sm.lookup(1, 49)
        self.assertEqual(p49.column, 0)

        # 超出范围的列仍命中最后一条
        p99 = sm.lookup(1, 99)
        self.assertEqual(p99.column, 20)

    def test_未命中返回_None(self):
        sm = make_map(["a.ts"], [], encode_vlq([100, 0, 0, 0]))
        self.assertIsNone(sm.lookup(1, 0), "第一段之前没有映射")
        self.assertIsNone(sm.lookup(2, 0), "没有映射的行")

    def test_函数名解析(self):
        mappings = encode_vlq([0, 0, 5, 0, 0])
        sm = make_map(["a.ts"], ["renderHome"], mappings)
        p = sm.lookup(1, 0)
        self.assertEqual(p.name, "renderHome")
        self.assertEqual(p.line, 6, "0-based 行 5 → 1-based 行 6")

    def test_多行映射与跨行累积状态(self):
        # 行1: col0→src0(0,0)；行2: col0→src0(1,0) —— srcLine 是跨行累积的增量
        line1 = encode_vlq([0, 0, 0, 0])
        line2 = encode_vlq([0, 0, 1, 0])  # delta +1
        sm = make_map(["a.ts"], [], f"{line1};{line2}")
        self.assertEqual(sm.lookup(1, 0).line, 1)
        self.assertEqual(sm.lookup(2, 0).line, 2)

    def test_拒绝非_v3(self):
        with self.assertRaises(SourceMapError):
            SourceMap({"version": 2, "mappings": ""})

    def test_空_mappings_不报错(self):
        sm = make_map(["a.ts"], [], "")
        self.assertEqual(sm.mapping_count, 0)
        self.assertIsNone(sm.lookup(1, 0))

    def test_序列化往返(self):
        mappings = encode_vlq([0, 0, 0, 0]) + "," + encode_vlq([50, 0, 0, 20])
        sm = make_map(["a.ts"], [], mappings)
        d = sm.to_dict()
        sm2 = SourceMap(d)
        self.assertEqual(sm2.mapping_count, sm.mapping_count)
        self.assertEqual(sm2.lookup(1, 50).column, 20)


class TestCompose(unittest.TestCase):
    """RN 符号化的核心：Hermes 字节码 map 与 Metro map 的合成。"""

    def _maps(self):
        # Metro map：bundle 第1行 col500 → App.tsx 第10行 col4，函数 renderHome
        metro = make_map(
            ["/app/src/screens/App.tsx"], ["renderHome"],
            encode_vlq([500, 0, 9, 4, 0]),
        )
        # Hermes map：字节码 1:132161 → bundle 第1行 col500
        hbc = make_map(
            ["/app/build/index.bundle"], [],
            encode_vlq([132161, 0, 0, 500]),
        )
        return hbc, metro

    def test_合成后字节码位置直达原始源码(self):
        hbc, metro = self._maps()
        result = compose(hbc, metro)

        self.assertEqual(result.mapping_count, 1)
        pos = result.lookup(1, 132161)
        self.assertIsNotNone(pos, "合成后应能直接查到字节码位置")
        self.assertEqual(pos.source, "/app/src/screens/App.tsx")
        self.assertEqual(pos.line, 10)
        self.assertEqual(pos.column, 4)
        self.assertEqual(pos.name, "renderHome")

    def test_合成结果可序列化并可再次加载(self):
        hbc, metro = self._maps()
        result = compose(hbc, metro)
        reloaded = SourceMap(json.loads(json.dumps(result.to_dict())))
        pos = reloaded.lookup(1, 132161)
        self.assertEqual(pos.line, 10)

    def test_两张_map_不匹配时给出警告且不静默成功(self):
        """失配的可靠信号是**目标行上没有映射** —— 此时必须报错而不是静默产出。"""
        hbc, _ = self._maps()
        # 内层 map 只在第 5 行有映射 —— 第 1 行完全无覆盖
        unrelated = make_map(["/other/x.ts"], [], f";;;;{encode_vlq([0, 0, 0, 0])}")
        warnings: list[str] = []
        result = compose(hbc, unrelated, warnings)
        self.assertEqual(result.mapping_count, 0)
        self.assertTrue(warnings, "必须给出警告，不能静默产出空 map")
        self.assertIn("同一次构建", warnings[0])

    def test_行内回退语义是刻意保留的(self):
        """同一行内，col 会回退到 gen_col ≤ col 的最后一个映射（与 DevTools 一致）。

        这不是 bug —— 但意味着**跨构建的 map 可能静默给出错误符号**，
        所以 lookup_ex 额外返回距离，compose 会据此告警。
        """
        inner = make_map(["/app/src/App.tsx"], [], encode_vlq([10, 0, 0, 0]))
        pos, distance = inner.lookup_ex(1, 500)
        self.assertIsNotNone(pos, "行内有映射时会按标准回退命中")
        self.assertEqual(distance, 490, "距离应被如实报告，供判断 map 是否匹配")

    def test_命中距离过大时告警但_不擅自判失败(self):
        # 外层引用的 bundle 列 5000，内层只在 col 10 有映射 → 回退命中，距离 4990
        hbc = make_map(["/app/build/index.bundle"], [], encode_vlq([132161, 0, 0, 5000]))
        inner = make_map(["/app/src/App.tsx"], [], encode_vlq([10, 0, 0, 0]))
        warnings: list[str] = []
        result = compose(hbc, inner, warnings)
        self.assertEqual(result.mapping_count, 1, "可疑不等于确定失败，仍应产出")
        self.assertTrue(any("命中距离" in w for w in warnings), "必须告警提示可能不正确")
        self.assertTrue(any("核对" in w for w in warnings), "告警要可操作")

    def test_合成保持多条映射的顺序(self):
        outer = make_map(["b.js"], [], encode_vlq([0, 0, 0, 0]) + "," + encode_vlq([100, 0, 0, 10]))
        inner = make_map(
            ["s.ts"], [],
            encode_vlq([0, 0, 0, 0]) + "," + encode_vlq([10, 0, 0, 5]),
        )
        result = compose(outer, inner)
        self.assertEqual(result.mapping_count, 2)
        self.assertEqual(result.lookup(1, 0).column, 0)
        self.assertEqual(result.lookup(1, 100).column, 5)


class TestFrameParsing(unittest.TestCase):
    def test_hermes_字节码帧(self):
        f = parse_frame("p@1:132161")
        self.assertIsNotNone(f)
        self.assertEqual(f.function, "p")
        self.assertEqual(f.hermes_offset, (1, 132161), "Hermes 的 col 是字节码偏移，不是列号")

    def test_hermes_带_at_前缀(self):
        f = parse_frame("    at anonymous@1:999")
        self.assertIsNotNone(f)
        self.assertEqual(f.hermes_offset, (1, 999))

    # ── 以下三行是**真实 Hermes 运行时输出**逐字拷贝 ──────────────
    # 来源：RN 0.73.4 Release bundle 经 hermesc 编译后，用 hermes CLI 执行
    # 真实抛出的堆栈。修复前这三种形式全部解析失败（20/20 帧未还原），
    # 因为路径里有空格/冒号，被误当成普通 file:line:column。
    def test_真实Hermes输出_带路径的_address_at(self):
        f = parse_frame(
            "    at anonymous (address at /tmp/build/hbc/app.hbc:1:49386)"
        )
        self.assertIsNotNone(f)
        self.assertEqual(f.function, "anonymous")
        self.assertEqual(
            f.hermes_offset, (1, 49386), "col 是字节码偏移，必须保留"
        )

    def test_真实Hermes输出_路径含空格(self):
        f = parse_frame(
            "    at global (address at /Users/me/My App/main.jsbundle:1:27660)"
        )
        self.assertIsNotNone(f)
        self.assertEqual(f.hermes_offset, (1, 27660))

    def test_真实Hermes输出_带路径简写(self):
        # 无扩展名也要认
        f = parse_frame("    at h (address at /tmp/out/bundle:1:28729)")
        self.assertIsNotNone(f)
        self.assertEqual(f.hermes_offset, (1, 28729))

    def test_真实Hermes输出_不得被误判为普通文件行(self):
        """曾经的缺陷：hermes_offset 为空，file 变成 'address at /path' 这种假路径。"""
        f = parse_frame(
            "    at anonymous (address at /tmp/build/hbc/app.hbc:1:49386)"
        )
        self.assertIsNone(f.file, "不应把 'address at <path>' 当成文件名")
        self.assertIsNotNone(f.hermes_offset)

    def test_标准_js_帧(self):
        f = parse_frame("    at renderHome (/app/src/App.tsx:10:4)")
        self.assertIsNotNone(f)
        self.assertEqual(f.function, "renderHome")
        self.assertEqual(f.file, "/app/src/App.tsx")
        self.assertEqual((f.line, f.column), (10, 4))
        self.assertIsNone(f.hermes_offset)

    def test_无函数名的位置帧(self):
        f = parse_frame("    at /app/src/App.tsx:10:4")
        self.assertEqual(f.file, "/app/src/App.tsx")
        self.assertEqual(f.line, 10)

    def test_native_帧(self):
        f = parse_frame("    at foo (native)")
        self.assertTrue(f.is_native)

    def test_非帧行返回_None(self):
        self.assertIsNone(parse_frame("Error: something broke"))
        self.assertIsNone(parse_frame(""))

    def test_解析整段堆栈(self):
        text = "\n".join([
            "Error: boom",
            "    at p@1:132161",
            "    at q@1:500",
            "    at nativeCall (native)",
        ])
        st = parse_stack(text)
        self.assertEqual(st.header, "Error: boom")
        self.assertEqual(len(st.frames), 3)
        self.assertEqual(st.frames[0].hermes_offset, (1, 132161))
        self.assertTrue(st.frames[2].is_native)


class TestEndToEnd(unittest.TestCase):
    """完整链路：合成两张 map → 符号化 Hermes 堆栈。"""

    def test_hermes_堆栈还原到原始_tsx_行号(self):
        metro = make_map(
            ["/app/src/screens/App.tsx"], ["renderHome"],
            encode_vlq([500, 0, 9, 4, 0]),
        )
        hbc = make_map(
            ["/app/build/index.bundle"], [],
            encode_vlq([132161, 0, 0, 500]),
        )
        composed = compose(hbc, metro)

        stack = parse_stack("TypeError: undefined is not an object\n    at p@1:132161")
        report = Symbolicator(composed).symbolicate(stack)

        self.assertEqual(len(report), 1)
        pos = report[0].position
        self.assertIsNotNone(pos, "必须还原成功")
        self.assertEqual(pos.line, 10)
        self.assertEqual(pos.source, "/app/src/screens/App.tsx")

    def test_未合成时明确提示需要_compose(self):
        # 只用 hbc map 直接符号化，会得到 bundle 位置而非源码位置 —— 这正是常见的错误
        hbc = make_map(["/app/build/index.bundle"], [], encode_vlq([132161, 0, 0, 500]))
        stack = parse_stack("    at p@1:132161")
        report = Symbolicator(hbc).symbolicate(stack)
        pos = report[0].position
        self.assertIsNotNone(pos)
        self.assertIn("index.bundle", pos.source, "未合成时只能还原到 bundle 层")

    def test_堆栈命中不了时给出可操作的提示(self):
        # map 只在第 2 行有映射，查询第 1 行 → 必然未命中
        sm = make_map(["a.ts"], [], f";{encode_vlq([0, 0, 0, 0])}")
        stack = parse_stack("    at p@1:999999")
        report = Symbolicator(sm).symbolicate(stack)
        self.assertIsNone(report[0].position)
        note = report[0].note or ""
        self.assertIn("同一次", note, "必须指出最常见原因")
        self.assertIn("compose", note, "Hermes 项目必须提示需要合成")

    def test_命中距离过大时给出核对提示但不误判为失败(self):
        # 第 1 行只在 col 10 有映射，查询 col 999999 → 回退命中但距离巨大
        sm = make_map(["/app/src/App.tsx"], [], encode_vlq([10, 0, 0, 0]))
        stack = parse_stack("    at p@1:999999")
        frame = Symbolicator(sm).symbolicate(stack)[0]
        self.assertIsNotNone(frame.position, "标准回退语义下仍应给出位置")
        self.assertGreater(frame.distance or 0, 1000)
        self.assertIn("核对", frame.format(), "距离过大时应提示核对 map 是否为本次构建")

    def test_原生帧被保留而不是丢弃(self):
        metro = make_map(["a.ts"], [], encode_vlq([0, 0, 0, 0]))
        stack = parse_stack("\n".join([
            "Error: x",
            "    at p@1:0",
            "    at objc_msgSend (native)",
        ]))
        report = Symbolicator(metro).symbolicate(stack)
        self.assertEqual(len(report), 2)
        self.assertTrue(report[1].original.is_native, "原生帧应保留，交由 dSYM 处理")


class TestCLI(unittest.TestCase):
    def test_compose_写文件并可被再次读取(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            metro = td / "metro.map"
            hbc = td / "hbc.map"
            out = td / "composed.map"

            metro.write_text(json.dumps({
                "version": 3, "sources": ["/app/src/App.tsx"], "names": ["f"],
                "mappings": encode_vlq([500, 0, 9, 4, 0]),
            }), encoding="utf-8")
            hbc.write_text(json.dumps({
                "version": 3, "sources": ["/app/build/index.bundle"], "names": [],
                "mappings": encode_vlq([132161, 0, 0, 500]),
            }), encoding="utf-8")

            sys.argv = ["rn_symbolicate.py", "compose",
                        "--outer", str(hbc), "--inner", str(metro), "--out", str(out)]
            from rn_symbolicate import main
            rc = main()
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())

            sm = SourceMap.from_file(out)
            self.assertEqual(sm.lookup(1, 132161).line, 10)

    def test_符号化无帧输入时返回非零(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "m.map"
            m.write_text(json.dumps({"version": 3, "sources": ["a.ts"], "names": [], "mappings": ""}),
                         encoding="utf-8")
            s = Path(td) / "s.txt"
            s.write_text("这不是堆栈\n", encoding="utf-8")
            sys.argv = ["rn_symbolicate.py", "symbolicate", "--map", str(m), "--stack", str(s)]
            from rn_symbolicate import main
            self.assertEqual(main(), 1)

    # ── 输入错误必须是「可操作提示」，不能甩 traceback ──────────
    # 起因：早先 main() 不接 SourceMapError，虽然消息本身友好，
    # 但整段被 traceback 埋掉，agent/用户只看到一屏栈。
    def _run(self, argv):
        from rn_symbolicate import main
        sys.argv = ["rn_symbolicate.py", *argv]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main()
        return rc, err.getvalue()

    def test_map不是JSON时给清晰提示而非traceback(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "not-a-map.txt"
            bad.write_text("Uncaught Invariant Violation: xxx\n", encoding="utf-8")
            rc, err = self._run(["inspect", "--map", str(bad)])
            self.assertEqual(rc, 1)
            self.assertIn("不是合法 JSON", err)
            self.assertNotIn("Traceback", err)

    def test_map文件不存在时给清晰提示(self):
        rc, err = self._run(["inspect", "--map", "/tmp/definitely-not-here-12345.map"])
        self.assertEqual(rc, 1)
        self.assertIn("找不到文件", err)
        self.assertNotIn("Traceback", err)

    def test_map传目录时给清晰提示(self):
        with tempfile.TemporaryDirectory() as td:
            rc, err = self._run(["inspect", "--map", td])
            self.assertEqual(rc, 1)
            self.assertNotIn("Traceback", err)

    def test_probe格式错误时给清晰提示(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "m.map"
            m.write_text(json.dumps({"version": 3, "sources": ["a.ts"], "names": [], "mappings": ""}),
                         encoding="utf-8")
            rc, err = self._run(["inspect", "--map", str(m), "--probe", "not-a-position"])
            self.assertEqual(rc, 1)
            self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
