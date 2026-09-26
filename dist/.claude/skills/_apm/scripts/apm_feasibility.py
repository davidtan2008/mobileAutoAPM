#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""可行性前置判断与最小对照组方法论。

这个脚本不猜根因，也不替代 profile。它只回答一个动手前必须回答的问题：

> 在当前技术栈/测量口径下，目标是否已经被最小对照组的地板挡住？

用法：
  # 先看标准对照组计划
  python3 apm_feasibility.py plan --metric startup.cold.first_frame --target-ms 200

  # 比较最小 control run 与当前 candidate run
  python3 apm_feasibility.py check \
    --control .apm/runs/control/metrics.json \
    --candidate .apm/runs/candidate/metrics.json \
    --metric startup.cold.first_frame --target-ms 200 --json

退出码：
- 0：当前证据允许继续优化（目标未被对照地板挡住）；
- 1：数据、口径或测量质量不足，不能判断；
- 2：目标低于/高于对照地板，当前架构下不可达，应先做架构决策。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence

import apm_baseline
import apm_diagnose

MIN_SAMPLES = 5


class FeasibilityError(Exception):
    """可行性判断无法继续。"""


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def load_payload(path: str) -> Dict[str, Any]:
    try:
        return apm_baseline.load_metrics(path)
    except apm_baseline.BaselineError as exc:
        raise FeasibilityError(str(exc)) from exc


def find_metric(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    for metric in payload.get("metrics", []):
        if metric.get("name") == name:
            return metric
    raise FeasibilityError(f"输入中找不到指标：{name}")


def metric_summary(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    metric = find_metric(payload, name)
    summary = apm_diagnose.summarize_metric(metric, MIN_SAMPLES)
    if summary["status"] != "usable":
        raise FeasibilityError(
            f"指标 {name} 测量质量为 {summary['status']}，不能用于可行性判断"
        )
    samples = [float(value) for value in metric["samples"]]
    stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    p50 = float(statistics.median(samples))
    mean = float(statistics.mean(samples))
    if metric["direction"] == "lower_is_better":
        conservative_floor = p50 + 2.0 * stdev
    else:
        conservative_floor = p50 - 2.0 * stdev
    return {
        "name": name,
        "unit": metric.get("unit", ""),
        "direction": metric.get("direction", "lower_is_better"),
        "n": len(samples),
        "p50": p50,
        "mean": mean,
        "stdev": stdev,
        "cv": summary["cv"],
        "minEffect": metric.get("minEffect"),
        "conservativeFloor": conservative_floor,
    }


def _context_warnings(control: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
    return apm_baseline.compare_contexts(control, candidate)


def check(
    control_path: str,
    candidate_path: str,
    metric: str,
    target: float,
    direction: Optional[str] = None,
    margin: Optional[float] = None,
) -> Dict[str, Any]:
    if not math.isfinite(target):
        raise FeasibilityError("--target 必须是有限数")
    if margin is not None and (not math.isfinite(margin) or margin < 0):
        raise FeasibilityError("--margin 必须是有限非负数")

    control = load_payload(control_path)
    candidate = load_payload(candidate_path)
    warnings = _context_warnings(control, candidate)
    if warnings:
        raise FeasibilityError("对照组与候选组口径不一致：" + "；".join(warnings))

    control_metric = find_metric(control, metric)
    candidate_metric = find_metric(candidate, metric)
    expected_direction = direction or control_metric.get("direction", "lower_is_better")
    if expected_direction not in ("lower_is_better", "higher_is_better"):
        raise FeasibilityError(f"无效 direction：{expected_direction}")
    if candidate_metric.get("direction", "lower_is_better") != expected_direction:
        raise FeasibilityError("对照组与候选组的 direction 不一致")

    control_summary = metric_summary(control, metric)
    candidate_summary = metric_summary(candidate, metric)
    if margin is None:
        declared = _finite(control_metric.get("minEffect"))
        margin = declared if declared is not None else 0.0

    if expected_direction == "lower_is_better":
        floor = control_summary["p50"] + margin
        if target < control_summary["p50"]:
            status = "blocked_by_control_floor"
            decision = "stop_and_escalate_architecture"
            rationale = "目标低于最小对照组的实测中位数地板；继续做局部优化无法跨越地板。"
        elif target < floor:
            status = "indeterminate_within_control_margin"
            decision = "collect_more_control_evidence"
            rationale = "目标落在对照中位数与测量余量之间，当前样本不足以断言可达或不可达。"
        else:
            status = "potentially_reachable"
            decision = "proceed_with_single_variable_experiment"
            rationale = "目标未被最小对照组地板和当前测量余量挡住，可以进入单变量实验。"
    else:
        ceiling = control_summary["p50"] - margin
        if target > control_summary["p50"]:
            status = "blocked_by_control_ceiling"
            decision = "stop_and_escalate_architecture"
            rationale = "目标高于最小对照组的实测中位数上限；继续做局部优化无法跨越上限。"
        elif target > ceiling:
            status = "indeterminate_within_control_margin"
            decision = "collect_more_control_evidence"
            rationale = "目标落在对照中位数与测量余量之间，当前样本不足以断言可达或不可达。"
        else:
            status = "potentially_reachable"
            decision = "proceed_with_single_variable_experiment"
            rationale = "目标未被最小对照组上限和当前测量余量挡住，可以进入单变量实验。"

    return {
        "schemaVersion": 1,
        "tool": "apm_feasibility",
        "status": status,
        "decision": decision,
        "rationale": rationale,
        "metric": metric,
        "direction": expected_direction,
        "target": target,
        "margin": margin,
        "control": {**control_summary, "path": control_path},
        "candidate": {**candidate_summary, "path": candidate_path},
        "nextSteps": [
            "保留 control 与 candidate 的全部样本，不挑选快 run",
            "若状态不是 potentially_reachable，不进入局部优化",
            "若需要架构决策，提交 issue 并等待人工确认",
        ],
    }


def plan(metric: str, target: float, direction: str = "lower_is_better") -> Dict[str, Any]:
    if direction not in ("lower_is_better", "higher_is_better"):
        raise FeasibilityError(f"无效 direction：{direction}")
    if not math.isfinite(target):
        raise FeasibilityError("--target 必须是有限数")
    return {
        "schemaVersion": 1,
        "tool": "apm_feasibility",
        "status": "plan",
        "metric": metric,
        "direction": direction,
        "target": target,
        "controlProtocol": [
            "保持与候选组完全相同的设备、系统、构建类型、命令和 measurement signature",
            "只改变一个变量：保留生命周期与埋点，把业务内容替换为最小可运行内容",
            "control 与 candidate 各至少 n=5，warmup 不计入样本",
            "两组测量都先通过 apm_diagnose；质量失败时不得比较",
            "用 apm_feasibility.py check 读取结论，不手工挑选簇或快样本",
        ],
        "escalationCriteria": [
            "目标低于/高于 control 中位数地板：停止局部优化，评估架构或框架层",
            "control 与 candidate measurement signature 不一致：先修口径，不做结论",
            "需要修改业务逻辑、替换 SDK 或改变产品行为：必须人工确认",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可行性前置判断与最小对照组")
    sub = parser.add_subparsers(dest="command", required=True)

    check_parser = sub.add_parser("check", help="比较 control 地板与 candidate")
    check_parser.add_argument("--control", required=True, help="control metrics.json 或 run 目录")
    check_parser.add_argument("--candidate", required=True, help="candidate metrics.json 或 run 目录")
    check_parser.add_argument("--metric", required=True)
    check_parser.add_argument("--target", "--target-ms", dest="target", type=float, required=True, help="目标值")
    check_parser.add_argument("--direction", choices=("lower_is_better", "higher_is_better"))
    check_parser.add_argument("--margin", type=float, help="额外的实用余量；默认取指标 minEffect")
    check_parser.add_argument("--json", action="store_true")

    plan_parser = sub.add_parser("plan", help="输出最小对照组实验计划")
    plan_parser.add_argument("--metric", required=True)
    plan_parser.add_argument("--target", "--target-ms", dest="target", type=float, required=True)
    plan_parser.add_argument("--direction", choices=("lower_is_better", "higher_is_better"), default="lower_is_better")
    plan_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.metric, args.target, args.direction)
            code = 0
        else:
            result = check(
                args.control,
                args.candidate,
                args.metric,
                args.target,
                direction=args.direction,
                margin=args.margin,
            )
            code = {"blocked_by_control_floor": 2, "blocked_by_control_ceiling": 2}.get(result["status"], 1 if result["status"].startswith("indeterminate") else 0)
    except FeasibilityError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "unavailable", "tool": "apm_feasibility", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"⛔ {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("════════ APM 可行性前置判断 ════════")
        print(f"指标：{result['metric']}   目标：{result['target']}{result.get('control', {}).get('unit', '')}")
        print(f"状态：{result['status']}")
        print(f"决策：{result['decision']}")
        print(f"理由：{result['rationale']}")
        if "control" in result:
            print(f"control p50={result['control']['p50']:.2f}  conservativeFloor={result['control']['conservativeFloor']:.2f}")
            print(f"candidate p50={result['candidate']['p50']:.2f}")
        for step in result["nextSteps"] if "nextSteps" in result else result["controlProtocol"]:
            print(f"  → {step}")
    return code


if __name__ == "__main__":
    sys.exit(main())
