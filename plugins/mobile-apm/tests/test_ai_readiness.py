#!/usr/bin/env python3
"""ai_readiness 扫描器的测试。

用临时目录构造**已知状态的项目**，断言扫描结果符合预期 ——
避免"扫描器自己报错了却说项目不达标"这类最坏情况。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ai_readiness import scan, detect_kinds  # noqa: E402


def mk(tmp: Path, files: dict[str, str]) -> Path:
    """按 {相对路径: 内容} 造一个项目。"""
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp


def keys(report) -> set[str]:
    return {f.key for f in report.findings}


class TestDetection(unittest.TestCase):
    def test_识别_ios_项目(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {"App.xcodeproj/project.pbxproj": "// stub"})
            kinds, root_kinds = detect_kinds(root)
            self.assertIn("ios", kinds)
            self.assertIn("ios", root_kinds)

    def test_识别_react_native(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {"package.json": '{"dependencies":{"react-native":"0.7"}}'})
            kinds, _ = detect_kinds(root)
            self.assertIn("react-native", kinds)

    def test_识别鸿蒙(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {"build-profile.json5": "{}"})
            kinds, _ = detect_kinds(root)
            self.assertIn("harmonyos", kinds)

    def test_多模块仓库看一层子目录(self):
        """根目录没有任何标志文件时，必须看子目录 —— 否则会得出"未识别"这种无用结论。"""
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "sdk-ios/Package.swift": "// swift-tools-version:5.9",
                "sdk-rn/package.json": '{"dependencies":{"react-native":"0.7"}}',
            })
            kinds, root_kinds = detect_kinds(root)
            self.assertIn("ios", kinds)
            self.assertIn("react-native", kinds)
            # 但根目录**不是** iOS/RN 项目 —— 应用级检查不该对它生效
            self.assertEqual(root_kinds, [], "子目录识别出的类型不能算作根类型")

    def test_含_SDK_的平台仓库不触发应用级误报(self):
        """一个"包含 iOS SDK 的平台仓库"不是 iOS 应用 ——
        不该报「iOS 工程没有测试 target」「签名未配置」这类应用级问题。"""
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "README.md": README_OK,
                "AGENTS.md": "约定",
                "ARCHITECTURE.md": "# 架构",
                "sdk-ios/Package.swift": "// swift-tools-version:5.9",
                "sdk-ios/Tests/T.swift": "import XCTest\n",
            })
            r = scan(root)
            k = keys(r)
            self.assertNotIn("ios-no-test-target", k)
            self.assertNotIn("ios-no-team", k)


README_OK = "# x\n\n" + "内容" * 200


class TestFindings(unittest.TestCase):
    def test_空项目命中阻断项(self):
        with tempfile.TemporaryDirectory() as td:
            r = scan(Path(td))
            self.assertIn("no-readme", keys(r))
            self.assertIn("no-tests", keys(r))
            self.assertLess(r.score, 60, "空项目不应该有高分")

    def test_完整项目拿高分(self):
        """一个各方面都达标的项目应当接近满分 —— 否则说明标准过严，会失去指导意义。"""
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "README.md": "# Demo\n\n一句话说明这个项目做什么、给谁用。\n\n"
                             "## 安装\n\n```bash\nnpm install\n```\n\n"
                             "## 测试\n\n```bash\nnpm test\n```\n",
                "AGENTS.md": "# 项目约定\n\n构建：`npm run build`\n测试：`npm test`\n",
                "CLAUDE.md": "@AGENTS.md\n",
                "ARCHITECTURE.md": "# 架构\n\n模块划分与依赖方向。\n",
                "package.json": '{"scripts":{"test":"jest","lint":"eslint ."}}',
                "package-lock.json": "{}",
                ".eslintrc.json": "{}",
                "src/index.ts": "export const a = 1;\n",
                "src/index.test.ts": "test('a', () => {});\n",
                ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    steps: []\n",
            })
            r = scan(root)
            self.assertGreaterEqual(r.score, 90,
                                    f"达标项目应接近满分，实际 {r.score}；命中 {sorted(keys(r))}")

    def test_架构文档带编号前缀也能识别(self):
        """实际项目常用 `13-architecture-analysis.md` 这类命名，只匹配以 arch 开头会漏。"""
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "README.md": README_OK,
                "docs/13-architecture-analysis.md": "# 架构分析\n",
            })
            r = scan(root)
            self.assertNotIn("no-arch-doc", keys(r))

    def test_只有_claude_md_时提示缺_AGENTS_md(self):
        """CLAUDE.md 只有 Claude Code 读；换 agent 就读不到项目约定了。"""
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {"README.md": README_OK, "CLAUDE.md": "约定"})
            r = scan(root)
            self.assertIn("no-agents-md", keys(r))

    def test_只有_AGENTS_md_时提示补_CLAUDE_md(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {"README.md": README_OK, "AGENTS.md": "约定"})
            r = scan(root)
            self.assertIn("no-claude-md", keys(r))

    def test_超大文件被识别(self):
        with tempfile.TemporaryDirectory() as td:
            big = "\n".join(f"let x{i} = {i}" for i in range(1500))
            root = mk(Path(td), {"README.md": README_OK, "src/Huge.swift": big})
            r = scan(root)
            self.assertIn("large-files", keys(r))

    def test_RN_缺_TypeScript_时提示(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "README.md": README_OK,
                "package.json": '{"dependencies":{"react-native":"0.7"}}',
            })
            r = scan(root)
            self.assertIn("rn-no-ts", keys(r))

    def test_iOS_测试_target_缺失被识别(self):
        with tempfile.TemporaryDirectory() as td:
            root = mk(Path(td), {
                "README.md": README_OK,
                "App.xcodeproj/project.pbxproj": "DEVELOPMENT_TEAM = ABC;",
                "App/ContentView.swift": "import SwiftUI\n",
            })
            r = scan(root)
            self.assertIn("ios-no-test-target", keys(r))


class TestScoring(unittest.TestCase):
    def test_分数随问题严重度下降(self):
        """blocker 的扣分必须显著大于 low —— 否则优先级失去了意义。"""
        with tempfile.TemporaryDirectory() as td:
            r = scan(Path(td))
            by_key = {f.key: f for f in r.findings}
            self.assertEqual(by_key["no-tests"].severity, "blocker")
            self.assertEqual(by_key["no-lint"].severity, "low")

    def test_分数不为负(self):
        with tempfile.TemporaryDirectory() as td:
            r = scan(Path(td))
            self.assertGreaterEqual(r.score, 0)

    def test_findings_按严重度排序(self):
        with tempfile.TemporaryDirectory() as td:
            r = scan(Path(td))
            w = {"blocker": 4, "high": 3, "medium": 2, "low": 1}
            sev = [w[f.severity] for f in r.findings]
            self.assertEqual(sev, sorted(sev, reverse=True), "findings 必须按严重度降序")


if __name__ == "__main__":
    unittest.main(verbosity=2)
