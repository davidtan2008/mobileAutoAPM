#!/usr/bin/env python3
"""apm_feasibility.py 的离线回归测试。"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apm_feasibility as feasibility


def payload(samples, *, device="iPhone 13", commit="commit-a", min_effect=5, direction="lower_is_better"):
    return {
        "context": {
            "device": device,
            "build": "Release",
            "platform": "iOS",
            "profile": "ios-native-startup",
            "measurementMethod": "fixed",
            "measurementSignature": "same-signature",
            "commit": commit,
        },
        "metrics": [
            {
                "name": "startup.cold.first_frame",
                "unit": "ms",
                "direction": direction,
                "samples": samples,
                "minEffect": min_effect,
            }
        ],
    }


def write_payload(root, name, data):
    path = Path(root) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TestFeasibility(unittest.TestCase):
    def test_目标低于对照地板时必须升级架构(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(temporary, "control.json", payload([230, 231, 229, 232, 230]))
            candidate = write_payload(
                temporary, "candidate.json", payload([260, 259, 261, 258, 260], commit="commit-b")
            )
            result = feasibility.check(control, candidate, "startup.cold.first_frame", 200)
            self.assertEqual(result["status"], "blocked_by_control_floor")
            self.assertEqual(result["decision"], "stop_and_escalate_architecture")

    def test_目标高于对照地板时允许进入单变量实验(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(temporary, "control.json", payload([100, 101, 99, 100, 102]))
            candidate = write_payload(
                temporary, "candidate.json", payload([110, 111, 109, 112, 110], commit="commit-b")
            )
            result = feasibility.check(control, candidate, "startup.cold.first_frame", 120)
            self.assertEqual(result["status"], "potentially_reachable")

    def test_目标落在对照余量内时保持不可判定(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(temporary, "control.json", payload([100, 101, 99, 100, 102]))
            candidate = write_payload(
                temporary, "candidate.json", payload([110, 111, 109, 112, 110], commit="commit-b")
            )
            result = feasibility.check(control, candidate, "startup.cold.first_frame", 103)
            self.assertEqual(result["status"], "indeterminate_within_control_margin")
            self.assertEqual(result["decision"], "collect_more_control_evidence")

    def test_不同commit允许比较但measurementSignature必须一致(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(temporary, "control.json", payload([100, 101, 99, 100, 102]))
            candidate = write_payload(
                temporary, "candidate.json", payload([110, 111, 109, 112, 110], commit="commit-b")
            )
            result = feasibility.check(control, candidate, "startup.cold.first_frame", 120)
            self.assertEqual(result["status"], "potentially_reachable")

            changed = payload([110, 111, 109, 112, 110], commit="commit-b")
            changed["context"]["measurementSignature"] = "other-signature"
            mismatch = write_payload(temporary, "mismatch.json", changed)
            with self.assertRaises(feasibility.FeasibilityError):
                feasibility.check(control, mismatch, "startup.cold.first_frame", 120)

    def test_对照组质量不可信时拒绝判断(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(temporary, "control.json", payload([100, 200, 100, 200, 100]))
            candidate = write_payload(temporary, "candidate.json", payload([100, 101, 99, 100, 102]))
            with self.assertRaises(feasibility.FeasibilityError):
                feasibility.check(control, candidate, "startup.cold.first_frame", 120)

    def test_higher_is_better也能识别对照上限(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = write_payload(
                temporary,
                "control.json",
                payload([50, 51, 49, 50, 52], direction="higher_is_better"),
            )
            candidate = write_payload(
                temporary,
                "candidate.json",
                payload([60, 61, 59, 60, 61], direction="higher_is_better", commit="commit-b"),
            )
            result = feasibility.check(
                control, candidate, "startup.cold.first_frame", 80, direction="higher_is_better"
            )
            self.assertEqual(result["status"], "blocked_by_control_ceiling")

    def test_plan输出对照组协议与升级条件(self):
        result = feasibility.plan("startup.cold.first_frame", 200)
        self.assertEqual(result["status"], "plan")
        self.assertTrue(any("measurement signature" in step for step in result["controlProtocol"]))
        self.assertTrue(any("架构" in step for step in result["escalationCriteria"]))

    def test_cli_plan_json(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = feasibility.main(
                ["plan", "--metric", "startup.cold.first_frame", "--target", "200", "--json"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["tool"], "apm_feasibility")


if __name__ == "__main__":
    unittest.main(verbosity=2)
