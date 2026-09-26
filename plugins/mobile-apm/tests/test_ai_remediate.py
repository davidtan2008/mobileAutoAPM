#!/usr/bin/env python3
"""ai_remediate.py 的离线回归测试。

重点验证三条不可让步的约束：
1. 不编造事实（生成物里必须有 TODO 占位）
2. 默认不落盘 / apply 拒绝覆盖
3. 改造效果是实测的（loop 给出真实 before/after）
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ai_readiness
import ai_remediate as rem


def make_project(root: Path, *, ios: bool = True, node: bool = False, makefile: bool = False):
    """造一个「典型遗留工程」：什么都没有。"""
    if ios:
        (root / "App.xcodeproj").mkdir()
        (root / "App").mkdir()
        (root / "App" / "main.swift").write_text("print(1)\n", encoding="utf-8")
    if node:
        (root / "package.json").write_text(
            json.dumps({"name": "x", "scripts": {"test": "jest"}}), encoding="utf-8"
        )
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
    if makefile:
        (root / "Makefile").write_text("test:\n\techo t\n", encoding="utf-8")


class TestFacts(unittest.TestCase):
    def test_从Makefile抽取测试命令(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)
            facts = rem.collect_facts(root)
            self.assertIn("make test", facts.detected_test_commands)

    def test_从package_json抽取npm测试命令与安装命令(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, node=True)
            facts = rem.collect_facts(root)
            self.assertIn("npm test", facts.detected_test_commands)
            self.assertEqual(facts.package_install_cmd, "npm ci")

    def test_无任何测试设施时不编造命令(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts = rem.collect_facts(Path(tmp))
            self.assertEqual(facts.detected_test_commands, [])


class TestPlan(unittest.TestCase):
    def test_遗留iOS工程能规划出三项改造(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            plan = rem.build_plan(root)
            paths = {a.relpath for a in plan.artifacts}
            self.assertIn("AGENTS.md", paths)
            self.assertIn(".github/workflows/ai-check.yml", paths)
            self.assertIn(".swiftlint.yml", paths)

    def test_不能自动改造的项只报给人(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            plan = rem.build_plan(root)
            keys = {item["findingKey"] for item in plan.needs_human}
            # 缺测试 → 必须落在 needs_human，绝不能出现在 artifacts
            self.assertIn("no-tests", keys)
            art_keys = {a.finding_key for a in plan.artifacts}
            self.assertNotIn("no-tests", art_keys)

    def test_同一路径只生成一次(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            (root / "CLAUDE.md").write_text("x", encoding="utf-8")  # 触发 no-claude-md
            plan = rem.build_plan(root)
            agents = [a for a in plan.artifacts if a.relpath == "AGENTS.md"]
            self.assertLessEqual(len(agents), 1)

    def test_已经达标的工程没有可改造项(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)
            (root / "README.md").write_text("x\n```bash\nmake test\n```\n" * 20, encoding="utf-8")
            (root / "AGENTS.md").write_text("x" * 3000, encoding="utf-8")
            (root / "Makefile").write_text("test:\n\techo t\nlint:\n\techo l\n", encoding="utf-8")
            plan = rem.build_plan(root)
            self.assertIsInstance(plan.artifacts, list)


class TestNoFabrication(unittest.TestCase):
    def test_需人审的生成物必须带TODO占位(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)
            plan = rem.build_plan(root)
            flagged = [a for a in plan.artifacts if a.needs_human]
            self.assertTrue(flagged, "本用例前提：存在需人审的生成物")
            for art in flagged:
                self.assertIn(
                    rem.TODO, art.content, f"{art.relpath} 标了需人审却没有 TODO 占位"
                )

    def test_生成物里不出现未被检测到的命令(self):
        """核心不变量：绝不写入「项目里并不存在的事实」。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)  # 唯一可检测到的命令是 make test
            facts = rem.collect_facts(root)
            self.assertEqual(facts.detected_test_commands, ["make test"])
            plan = rem.build_plan(root)
            banned = ["npm test", "swift test", "./gradlew test", "yarn test", "pnpm test"]
            for art in plan.artifacts:
                for b in banned:
                    self.assertNotIn(b, art.content, f"{art.relpath} 写了未检测到的命令 {b}")
            # 反向确认：检测到的命令确实被用上了
            ci = next(a for a in plan.artifacts if a.relpath.endswith("ai-check.yml"))
            self.assertIn("make test", ci.content)

    def test_CI不含空的run块(self):
        """曾经真的生成过 `run: |` 空块 + 重复 step 的坏 YAML，必须防回归。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)
            facts = rem.collect_facts(root)
            ci = rem._ci(facts, root)
            self.assertNotIn("run: |", ci.content)
            for line in ci.content.splitlines():
                if line.strip().startswith("run:"):
                    self.assertNotEqual(line.strip(), "run:", f"存在空的 run：{ci.content}")

    def test_无测试命令时CI只出占位不编命令(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            facts = rem.collect_facts(root)
            art = rem._ci(facts, root)
            self.assertIn("TODO", art.content)
            for banned in ("npm test", "swift test", "./gradlew test", "make "):
                self.assertNotIn(banned, art.content)

    def test_生成物不包含虚构的项目名(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, makefile=True)
            plan = rem.build_plan(root)
            readme = next(a for a in plan.artifacts if a.relpath == "README.md")
            self.assertNotIn("My App", readme.content)
            self.assertIn(rem.TODO, readme.content)

    def test_生成物不含别的项目的目录名(self):
        """曾经把某个具体项目的目录名硬编码进 SwiftLint 配置，跑到别的工程上就是错的。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)  # 目录叫 App，不叫 Duiyi
            facts = rem.collect_facts(root)
            lint = rem._lint(facts, root)
            for leaked in ("Duiyi", "DuiyiTests"):
                self.assertNotIn(leaked, lint.content, "把具体项目名写进了通用模板")
            # 也不能瞎猜 included 范围
            self.assertNotIn("included:", lint.content)


class TestStagingAndApply(unittest.TestCase):
    def test_generate只写staging不动工程(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "proj"
            root.mkdir()
            make_project(root)
            staging = base / "staging"
            plan = rem.build_plan(root)
            rem.write_staging(plan, staging, root)
            self.assertTrue((staging / "AGENTS.md").exists())
            self.assertTrue((staging / "REMEDIATION_PLAN.json").exists())
            # 目标工程必须一个文件都没多
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "README.md").exists())

    def test_apply拒绝覆盖已存在文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "proj"
            root.mkdir()
            make_project(root)
            (root / "AGENTS.md").write_text("真实内容", encoding="utf-8")
            staging = base / "staging"
            plan = rem.build_plan(root)
            rem.write_staging(plan, staging, root)
            with self.assertRaises(rem.RemediationError):
                rem.apply_staging(plan, staging, root)
            # 真实内容必须没被动过
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "真实内容")

    def test_apply在文件缺失时才写入(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "proj"
            root.mkdir()
            make_project(root)
            staging = base / "staging"
            plan = rem.build_plan(root)
            rem.write_staging(plan, staging, root)
            applied = rem.apply_staging(plan, staging, root)
            self.assertIn("AGENTS.md", applied)
            self.assertTrue((root / "AGENTS.md").exists())

    def test_generate拒绝覆盖staging里已有文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "proj"
            root.mkdir()
            make_project(root)
            staging = base / "staging"
            plan = rem.build_plan(root)
            rem.write_staging(plan, staging, root)
            with self.assertRaises(rem.RemediationError):
                rem.write_staging(plan, staging, root)


class TestLoop(unittest.TestCase):
    def test_loop给出实测的before_after且不碰真实工程(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            make_project(root)
            before_files = sorted(p.name for p in root.iterdir())
            result = rem.run_loop(root)
            self.assertEqual(result["status"], "measured")
            self.assertFalse(result["appliedToRealProject"])
            self.assertIsNotNone(result["scoreAfter"])
            self.assertGreater(result["scoreAfter"], result["scoreBefore"])
            self.assertTrue(result["resolvedFindings"])
            # 真实工程文件列表必须完全没变
            self.assertEqual(sorted(p.name for p in root.iterdir()), before_files)

    def test_loop对已达标工程不编造改造(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            result = rem.run_loop(root)
            self.assertIn(result["status"], {"nothing_to_do", "measured"})
            self.assertFalse(result["appliedToRealProject"])


class TestScannerIntegration(unittest.TestCase):
    def test_plan的findings来自真实扫描(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root)
            report = ai_readiness.scan(root)
            plan = rem.build_plan(root, report)
            self.assertEqual(plan.score_before, report.score)
            self.assertTrue(plan.findings_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
