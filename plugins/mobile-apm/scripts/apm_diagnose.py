#!/usr/bin/env python3
# ruff: noqa: UP006, UP045, UP035
"""APM 测量方差诊断器。

回答的不是「哪次最快」，而是：

1. 当前指标能不能支撑优化验证；
2. 总方差是否来自疑似多簇；
3. 哪些分解段更稳定；
4. 哪些变量值得做控制实验。

**相关不等于因果。** 本工具只报告可疑相关性，绝不自动分层、校正或丢弃样本。

支持直接读取 T1 风格的 run 目录（``metrics.json`` + ``raw/stages.txt``），
也支持新测量模板输出的逐次 observations。

退出码：
- 0：焦点指标达到当前样本量下的可用门槛；
- 2：测量不可用或只勉强可用，应先修测量；
- 1：输入数据有问题。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
DEFAULT_MIN_SAMPLES = 5
CV_MARGINAL = 0.10
CV_UNUSABLE = 0.30
MULTIMODAL_MIN_SAMPLES = 5
MULTIMODAL_MIN_CLUSTER = 2
MULTIMODAL_SEPARATION_RATIO = 3.0
MULTIMODAL_GAP_SHARE = 0.35
STAGE_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=(\d+)/\+(\d+)")
CORRELATION_KEYWORDS = (
    "premain",
    "pre_main",
    "pre-main",
    "battery",
    "thermal",
    "temperature",
    "low_power",
    "lowpower",
    "sample_index",
    "sampleindex",
    "first_mark",
    "firstmark",
    "process_start",
    "processstart",
)


class DiagnoseError(Exception):
    """输入或诊断失败。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite_number(value: Any) -> Optional[float]:
    """只接受有限数字；bool 不可伪装成 0/1 样本。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DiagnoseError(f"找不到文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise DiagnoseError(f"JSON 无法解析：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DiagnoseError(f"JSON 顶层必须是对象：{path}")
    return value


def read_numeric_lines(path: Path) -> List[float]:
    if not path.exists():
        return []
    values: List[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        try:
            value = float(fields[0])
        except ValueError:
            continue
        if math.isfinite(value) and value >= 0:
            values.append(value)
    return values


def parse_stage_line(line: str) -> Dict[str, Dict[str, float]]:
    stages: Dict[str, Dict[str, float]] = {}
    for name, since, delta in STAGE_RE.findall(line):
        stages[name] = {"sinceMs": float(since), "deltaMs": float(delta)}
    return stages


def normalize_metrics(raw_metrics: Any, source: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(raw_metrics, list):
        raise DiagnoseError(f"{source}: metrics 必须是数组")

    metrics: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_metrics):
        if not isinstance(item, dict):
            warnings.append(f"{source}: metrics[{index}] 不是对象，已忽略")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            warnings.append(f"{source}: metrics[{index}] 缺 name，已忽略")
            continue
        if name in seen:
            raise DiagnoseError(f"{source}: 重复指标名 {name}，无法无歧义诊断")
        seen.add(name)

        raw_samples = item.get("samples")
        if not isinstance(raw_samples, list):
            warnings.append(f"{source}: {name}.samples 不是数组，已忽略")
            continue
        samples: List[float] = []
        discarded = 0
        for raw in raw_samples:
            value = finite_number(raw)
            if value is None:
                discarded += 1
            else:
                samples.append(value)
        if not samples:
            warnings.append(f"{source}: {name} 没有有效样本，已忽略")
            continue
        if discarded:
            warnings.append(f"{source}: {name} 丢弃 {discarded} 个非有限数样本")
        metrics.append(
            {
                "name": name,
                "unit": str(item.get("unit", "unknown")),
                "direction": str(item.get("direction", "unknown")),
                "samples": samples,
                "minEffect": finite_number(item.get("minEffect")),
            }
        )
    return metrics, warnings


def derive_legacy_metrics(run_dir: Path, metrics: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """补齐 T1 旧格式中未进入 metrics.json 的原始分段。"""
    derived: List[Dict[str, Any]] = []
    existing = {metric["name"] for metric in metrics}

    if not any(name.startswith("startup.stage.") for name in existing):
        stage_path = run_dir / "raw" / "stages.txt"
        by_stage: Dict[str, List[float]] = {}
        if stage_path.exists():
            for line in stage_path.read_text(encoding="utf-8").splitlines():
                for stage, values in parse_stage_line(line).items():
                    by_stage.setdefault(stage, []).append(values["deltaMs"])
        for stage, samples in sorted(by_stage.items()):
            if len(samples) < 2:
                continue
            derived.append(
                {
                    "name": f"startup.stage.{stage}",
                    "unit": "ms",
                    "direction": "lower_is_better",
                    "samples": samples,
                    "minEffect": None,
                    "derivedFrom": str(stage_path),
                }
            )

    if "startup.premain" not in existing:
        paired_path = run_dir / "raw" / "paired.txt"
        premain: List[float] = []
        if paired_path.exists():
            for line in paired_path.read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) < 2:
                    continue
                first = finite_number(fields[0])
                second = finite_number(fields[1])
                if first is not None and second is not None and first >= 0 and second >= 0:
                    premain.append(first)
        if not premain:
            premain = read_numeric_lines(run_dir / "raw" / "premain.txt")
        if len(premain) >= 2:
            derived.append(
                {
                    "name": "startup.premain",
                    "unit": "ms",
                    "direction": "lower_is_better",
                    "samples": premain,
                    "minEffect": None,
                    "derivedFrom": str(paired_path if paired_path.exists() else run_dir / "raw" / "premain.txt"),
                }
            )
    return derived, ([f"从旧版 raw 数据派生 {len(derived)} 个分段指标"] if derived else [])


def derive_from_observations(
    observations: Any, metrics: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """新模板若已导出 stage metrics 则不重复派生。"""
    if not isinstance(observations, list) or any(
        metric["name"].startswith("startup.stage.") for metric in metrics
    ):
        return [], []

    by_stage: Dict[str, List[float]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        stages = observation.get("stages")
        if not isinstance(stages, dict):
            continue
        for stage, value in stages.items():
            if not isinstance(stage, str):
                continue
            if isinstance(value, dict):
                value = value.get("deltaMs")
            delta = finite_number(value)
            if delta is not None and delta >= 0:
                by_stage.setdefault(stage, []).append(delta)

    derived: List[Dict[str, Any]] = []
    for stage, samples in sorted(by_stage.items()):
        if len(samples) < 2:
            continue
        derived.append(
            {
                "name": f"startup.stage.{stage}",
                "unit": "ms",
                "direction": "lower_is_better",
                "samples": samples,
                "minEffect": None,
                "derivedFrom": "observations[].stages",
            }
        )
    return derived, ([f"从逐次 observations 派生 {len(derived)} 个分段指标"] if derived else [])


def load_run(path: Path) -> Dict[str, Any]:
    path = path.expanduser().resolve()
    if path.is_file():
        metrics_path = path
        run_dir = path.parent
    elif path.is_dir():
        run_dir = path
        metrics_path = path / "metrics.json"
    else:
        raise DiagnoseError(f"找不到 run 路径：{path}")

    raw: Dict[str, Any] = {}
    if metrics_path.exists():
        raw = read_json(metrics_path)
        metrics, warnings = normalize_metrics(raw.get("metrics"), metrics_path)
        observations = raw.get("observations", [])
    else:
        samples = read_numeric_lines(run_dir / "raw" / "samples.txt")
        if not samples:
            raise DiagnoseError(f"{run_dir}: 既没有 metrics.json，也没有 raw/samples.txt")
        metrics = [
            {
                "name": "startup.cold.first_frame",
                "unit": "ms",
                "direction": "lower_is_better",
                "samples": samples,
                "minEffect": None,
                "derivedFrom": str(run_dir / "raw" / "samples.txt"),
            }
        ]
        observations = []
        warnings = ["未找到 metrics.json，仅从 raw/samples.txt 构造总指标"]

    observation_metrics, observation_warnings = derive_from_observations(observations, metrics)
    metrics.extend(observation_metrics)
    warnings.extend(observation_warnings)
    legacy_metrics, legacy_warnings = derive_legacy_metrics(run_dir, metrics)
    metrics.extend(legacy_metrics)
    warnings.extend(legacy_warnings)

    if not metrics:
        raise DiagnoseError(f"{run_dir}: 没有可诊断的指标")
    return {
        "path": str(run_dir),
        "metricsPath": str(metrics_path) if metrics_path.exists() else None,
        "context": raw.get("context", {}) if isinstance(raw.get("context", {}), dict) else {},
        "observations": observations if isinstance(observations, list) else [],
        "metrics": metrics,
        "warnings": warnings,
    }


def pooled_stdev(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = 0.0
    degrees = 0
    for values in (left, right):
        if len(values) > 1:
            sd = statistics.stdev(values)
            numerator += (len(values) - 1) * sd * sd
            degrees += len(values) - 1
    return math.sqrt(numerator / degrees) if degrees else 0.0


def best_split(values: Sequence[float]) -> Optional[Tuple[float, float, int, List[float], List[float]]]:
    ordered = sorted(values)
    best: Optional[Tuple[float, float, int, List[float], List[float]]] = None
    for index in range(MULTIMODAL_MIN_CLUSTER, len(ordered) - MULTIMODAL_MIN_CLUSTER + 1):
        left = ordered[:index]
        right = ordered[index:]
        within = pooled_stdev(left, right)
        separation = abs(statistics.mean(right) - statistics.mean(left))
        value_range = ordered[-1] - ordered[0]
        gap_share = (ordered[index] - ordered[index - 1]) / value_range if value_range else 0.0
        ratio = separation / within if within > 0 else math.inf
        score = (ratio, gap_share)
        if best is None or score > (best[0], best[1]):
            best = (ratio, gap_share, index, left, right)
    return best


def suspicious_clusters(values: Sequence[float]) -> List[List[float]]:
    """用可解释的大间隙启发式找疑似簇，不声称这是正式模态检验。"""

    def split(group: List[float]) -> List[List[float]]:
        if len(group) < MULTIMODAL_MIN_SAMPLES:
            return [group]
        candidate = best_split(group)
        if candidate is None:
            return [group]
        ratio, gap_share, _index, left, right = candidate
        if ratio < MULTIMODAL_SEPARATION_RATIO or gap_share < MULTIMODAL_GAP_SHARE:
            return [group]
        return split(left) + split(right)

    if len(values) < MULTIMODAL_MIN_SAMPLES:
        return []
    return split(sorted(values))


def summarize_metric(metric: Dict[str, Any], min_samples: int) -> Dict[str, Any]:
    samples = metric["samples"]
    ordered = sorted(samples)
    mean = statistics.mean(samples)
    median = statistics.median(samples)
    stdev = statistics.stdev(samples) if len(samples) > 1 else None
    cv = stdev / abs(mean) if stdev is not None and mean else None
    clusters = suspicious_clusters(samples)
    cluster_summaries = [
        {
            "n": len(cluster),
            "min": min(cluster),
            "median": statistics.median(cluster),
            "max": max(cluster),
        }
        for cluster in clusters
    ]

    if len(samples) < min_samples:
        status = "insufficient_samples"
    elif len(clusters) >= 2:
        status = "unusable_multimodal"
    elif cv is None:
        status = "insufficient_variation"
    elif cv > CV_UNUSABLE:
        status = "unusable_high_variance"
    elif cv >= CV_MARGINAL:
        status = "marginal"
    else:
        status = "usable"

    return {
        "name": metric["name"],
        "unit": metric["unit"],
        "direction": metric["direction"],
        "n": len(samples),
        "min": ordered[0],
        "median": median,
        "mean": mean,
        "max": ordered[-1],
        "stdev": stdev,
        "cv": cv,
        "status": status,
        "suspectedClusters": cluster_summaries,
        "clusterMethod": "largest-gap heuristic (suspected clusters, not a formal modality test)",
        "derivedFrom": metric.get("derivedFrom"),
    }


def rankdata(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[indexed[position][0]] = rank
        index = end
    return ranks


def pearson(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left) * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def correlation_strength(value: float) -> str:
    absolute = abs(value)
    if absolute >= 0.7:
        return "strong"
    if absolute >= 0.4:
        return "moderate"
    return "weak"


def metric_samples(metrics: Sequence[Dict[str, Any]], name: str) -> Optional[List[float]]:
    for metric in metrics:
        if metric["name"] == name:
            return list(metric["samples"])
    return None


def focus_observations(run: Dict[str, Any], focus_name: str) -> List[Dict[str, Any]]:
    observations = run["observations"]
    if not isinstance(observations, list):
        return []
    focus_metric = next(metric for metric in run["metrics"] if metric["name"] == focus_name)
    pairs: List[Tuple[float, Dict[str, float]]] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            continue
        if observation.get("status") not in (None, "valid"):
            continue
        value: Any = None
        if "firstFrameMs" in observation and (
            "first_frame" in focus_name or focus_name.endswith(".total")
        ):
            value = observation.get("firstFrameMs")
        if value is None and index < len(focus_metric["samples"]):
            value = focus_metric["samples"][index]
        number = finite_number(value)
        if number is None:
            continue
        candidates: Dict[str, float] = {}
        for key, raw in observation.items():
            if key in {
                "index", "startedAt", "rawLog", "stages", "firstFrameMs", "premainSource",
                "premainCandidatesMs", "accountedTimeMs", "unattributedResidualMs", "status",
            }:
                continue
            candidate = finite_number(raw)
            if candidate is not None:
                canonical_key = {
                    "premainMs": "startup.premain",
                    "processToFirstMarkMs": "startup.process_to_first_mark",
                }.get(key, key)
                candidates[canonical_key] = candidate
        pairs.append((number, candidates))
    return [{"focus": focus, "candidates": candidates} for focus, candidates in pairs]


def correlations_for_run(run: Dict[str, Any], focus_name: str) -> List[Dict[str, Any]]:
    focus_metric = next(metric for metric in run["metrics"] if metric["name"] == focus_name)
    candidates: Dict[str, List[float]] = {}

    paired = focus_observations(run, focus_name)
    if paired:
        for observation in paired:
            for name, value in observation["candidates"].items():
                candidates.setdefault(name, []).append(value)
                candidates.setdefault(f"__focus__{name}", []).append(observation["focus"])

    for metric in run["metrics"]:
        name = metric["name"]
        lowered = name.lower()
        if name == focus_name or not any(keyword in lowered for keyword in CORRELATION_KEYWORDS):
            continue
        if name == "startup.stage.processStart" and "startup.process_to_first_mark" in candidates:
            continue
        if len(metric["samples"]) == len(focus_metric["samples"]):
            candidates[name] = list(metric["samples"])
            candidates[f"__focus__{name}"] = list(focus_metric["samples"])

    results: List[Dict[str, Any]] = []
    for name in sorted(candidates):
        if name.startswith("__focus__"):
            continue
        left = candidates[name]
        right = candidates.get(f"__focus__{name}", [])
        if len(left) != len(right) or len(left) < 4:
            continue
        pearson_value = pearson(left, right)
        spearman_value = pearson(rankdata(left), rankdata(right))
        if pearson_value is None or spearman_value is None:
            continue
        results.append(
            {
                "candidate": name,
                "n": len(left),
                "pearson": pearson_value,
                "spearman": spearman_value,
                "strength": correlation_strength(pearson_value),
                "interpretation": "clue_only_not_causality",
            }
        )
    return sorted(results, key=lambda item: abs(item["pearson"]), reverse=True)


def context_identity(context: Dict[str, Any]) -> Dict[str, Any]:
    device = context.get("device")
    build = context.get("build")
    device_id = device.get("udid") if isinstance(device, dict) else context.get("device")
    if device_id is None:
        device_id = context.get("deviceInfo", {}).get("udid") if isinstance(context.get("deviceInfo"), dict) else None
    build_type = build.get("type") if isinstance(build, dict) else context.get("build")
    return {
        "device": device_id,
        "build": build_type,
        "method": context.get("measurementMethod") or context.get("method"),
        "profile": context.get("profile"),
        "bundleId": context.get("bundleId"),
    }


def compare_contexts(runs: Sequence[Dict[str, Any]]) -> List[str]:
    if len(runs) < 2:
        return []
    identities = [context_identity(run["context"]) for run in runs]
    warnings: List[str] = []
    optional = {"profile", "bundleId"}
    for key in identities[0]:
        values = [identity.get(key) for identity in identities]
        if all(value is None for value in values) and key in optional:
            continue
        if any(value is None for value in values):
            warnings.append(f"跨运行上下文缺少 {key}，无法确认口径一致")
        elif len(set(values)) != 1:
            warnings.append(f"跨运行 {key} 不一致：{values}")
    return warnings


def aggregate_correlations(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_candidate: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        for item in run["correlations"]:
            by_candidate.setdefault(item["candidate"], []).append(
                {"path": run["path"], **item}
            )

    results: List[Dict[str, Any]] = []
    for candidate, entries in sorted(by_candidate.items()):
        signs = {1 if entry["pearson"] > 0 else -1 for entry in entries if entry["pearson"] != 0}
        if len(entries) >= 2 and len(signs) > 1:
            stability = "direction_inconsistent"
            advice = "不可作为自动分层或校正变量；需要控制实验验证"
        elif len(entries) == 1:
            stability = "single_run_only"
            advice = "只有一个运行的数据，不能证明稳定相关"
        else:
            stability = "same_direction_observed"
            advice = "方向暂一致，但仍不等于因果"
        results.append(
            {
                "candidate": candidate,
                "stability": stability,
                "advice": advice,
                "observations": entries,
            }
        )
    return results


def cross_run_focus_analysis(runs: Sequence[Dict[str, Any]], focus_name: str) -> Dict[str, Any]:
    """检查每个 run 内部稳定时，run 之间是否仍发生整体漂移。"""
    if len(runs) < 2:
        return {
            "status": "not_enough_runs",
            "comparisonBasis": "not_enough_runs",
            "focusMetric": focus_name,
            "runs": [],
        }

    commits = [run["context"].get("commit") for run in runs]
    if any(commit is None for commit in commits):
        comparison_basis = "missing_commit"
    elif len(set(commits)) != 1:
        comparison_basis = "different_commits"
    else:
        comparison_basis = "same_commit"
    if comparison_basis != "same_commit":
        return {
            "status": "not_comparable",
            "comparisonBasis": comparison_basis,
            "focusMetric": focus_name,
            "runs": [],
            "medianDelta": None,
            "interpretation": "输入不是同一 commit 的重复测量，不做 run 间漂移判定",
        }

    run_summaries = []
    declared_min_effect = None
    for run in runs:
        summary = run["focusSummary"]
        metric = next(item for item in run["metrics"] if item["name"] == focus_name)
        if declared_min_effect is None:
            declared_min_effect = finite_number(metric.get("minEffect"))
        run_summaries.append(
            {
                "path": run["path"],
                "n": summary["n"],
                "unit": summary["unit"],
                "median": summary["median"],
                "mean": summary["mean"],
                "stdev": summary["stdev"],
                "cv": summary["cv"],
                "status": summary["status"],
            }
        )

    # 高方差 run 不能用自己的巨大标准差把 run 间漂移门槛放大。
    eligible = [item for item in run_summaries if item["status"] == "usable"]
    excluded = [item for item in run_summaries if item["status"] != "usable"]
    if len(eligible) < 2:
        return {
            "status": "not_enough_usable_runs",
            "comparisonBasis": comparison_basis,
            "focusMetric": focus_name,
            "runs": run_summaries,
            "eligibleRuns": eligible,
            "excludedRuns": excluded,
            "medianDelta": None,
            "interpretation": "可用 run 少于 2 个，不能判断 run 间漂移；先修复各 run 内测量质量",
        }

    medians = [item["median"] for item in eligible]
    median_delta = max(medians) - min(medians)
    stdevs = [item["stdev"] for item in eligible if item["stdev"] is not None]
    within_run_two_sigma = max((2.0 * value for value in stdevs), default=None)
    practical_threshold = declared_min_effect
    if practical_threshold is None:
        practical_threshold = max(abs(statistics.median(medians)) * 0.10, 1e-9)
    threshold = max(practical_threshold, within_run_two_sigma or 0.0)
    shifted = median_delta > threshold
    return {
        "status": "shift_detected" if shifted else "consistent",
        "comparisonBasis": comparison_basis,
        "focusMetric": focus_name,
        "runs": run_summaries,
        "eligibleRuns": eligible,
        "excludedRuns": excluded,
        "medianDelta": median_delta,
        "withinRunTwoSigmaMax": within_run_two_sigma,
        "practicalThreshold": practical_threshold,
        "decisionThreshold": threshold,
        "interpretation": (
            "各可用 run 内部看似稳定，但焦点指标跨 run 漂移超过实用/噪声门槛；不能直接记录基线"
            if shifted
            else "当前可用 run 间未检测到超过门槛的焦点中位数漂移"
        ),
    }


def choose_focus(run: Dict[str, Any], requested: Optional[str]) -> str:
    names = [metric["name"] for metric in run["metrics"]]
    if requested:
        if requested not in names:
            raise DiagnoseError(f"{run['path']}: 找不到焦点指标 {requested}；可用：{', '.join(names)}")
        return requested
    preferred = [
        name
        for name in names
        if "first_frame" in name.lower() or name.lower().endswith(".total")
    ]
    return preferred[0] if preferred else names[0]


def build_report(
    paths: Sequence[Path], requested_metric: Optional[str], min_samples: int, effect_ms: Optional[float]
) -> Dict[str, Any]:
    loaded = [load_run(path) for path in paths]
    focus_names = [choose_focus(run, requested_metric) for run in loaded]
    if len(set(focus_names)) > 1:
        raise DiagnoseError(f"多个 run 的焦点指标不一致：{focus_names}")

    focus_name = focus_names[0]
    for run in loaded:
        summaries = [summarize_metric(metric, min_samples) for metric in run["metrics"]]
        run["focusMetric"] = focus_name
        run["focusSummary"] = next(item for item in summaries if item["name"] == focus_name)
        run["metricSummaries"] = sorted(
            summaries,
            key=lambda item: (float("inf") if item["cv"] is None else item["cv"], item["name"]),
        )
        run["correlations"] = correlations_for_run(run, focus_name)

    context_warnings = compare_contexts(loaded)
    cross_correlations = aggregate_correlations(loaded)
    cross_run_focus = cross_run_focus_analysis(loaded, focus_name)
    statuses = [run["focusSummary"]["status"] for run in loaded]
    reasons: List[str] = []
    suggestions: List[str] = []

    if any(status == "insufficient_samples" for status in statuses):
        reasons.append(f"样本量不足：每组至少需要 {min_samples} 个有效样本")
        suggestions.append(f"把样本量扩到 {min_samples}；仍不收敛时扩到 15–20，并保留全部样本")
    if any(status == "unusable_multimodal" for status in statuses):
        reasons.append("焦点指标检出疑似多簇；混合分布不是单一测量口径")
        suggestions.append("不要挑快簇或自动分层；固定环境变量后做同 commit 重复测量")
    if any(status == "unusable_high_variance" for status in statuses):
        reasons.append("焦点指标 CV > 30%，当前离散度超过常见小幅度优化")
        suggestions.append("停止用总指标验证小改动，先降低测量方差")
    if any(status == "marginal" for status in statuses):
        reasons.append("焦点指标 CV 处于 10–30%，只能支撑大幅变化")
        suggestions.append("扩大样本并固定设备温度、电量、后台状态与测量间隔")
    if cross_run_focus["status"] == "shift_detected":
        reasons.append(
            f"跨 run 焦点中位数漂移 {cross_run_focus['medianDelta']:.1f}ms，"
            f"超过判定门槛 {cross_run_focus['decisionThreshold']:.1f}ms"
        )
        suggestions.append(
            "不要把任一 run 直接当稳定基线；先固定环境并做同 commit 重复测量，"
            "同时保留两次 run 的全部样本"
        )

    inconsistent = [item for item in cross_correlations if item["stability"] == "direction_inconsistent"]
    for item in inconsistent:
        reasons.append(f"可疑变量 {item['candidate']} 在不同运行中相关方向不一致")
        suggestions.append(f"不要按 {item['candidate']} 自动分层；它只能作为控制实验假设")
    if context_warnings:
        reasons.extend(context_warnings)
        suggestions.append("先统一设备、构建、方法与命令，再比较方差")

    stage_observations: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for run in loaded:
        for summary in run["metricSummaries"]:
            if not summary["name"].startswith("startup.stage."):
                continue
            if summary["n"] < min_samples or summary["cv"] is None or summary["cv"] >= CV_MARGINAL:
                continue
            if summary["median"] <= 0:
                continue
            stage_observations.setdefault(summary["name"], []).append((run["path"], summary))

    # 只有在所有传入 run 中都稳定的分解段才可作为候选；一段数据偶然稳定不够。
    stage_candidates = [
        {
            "name": name,
            "unit": observations[0][1]["unit"],
            "worstCv": max(item[1]["cv"] for item in observations),
            "minN": min(item[1]["n"] for item in observations),
            "runCount": len(observations),
            "runPaths": [path for path, _summary in observations],
        }
        for name, observations in stage_observations.items()
        if len(observations) == len(loaded)
    ]
    stage_candidates.sort(key=lambda item: (item["worstCv"], item["name"]))
    if stage_candidates:
        names = "、".join(item["name"] for item in stage_candidates[:3])
        suggestions.append(f"可把同次测量中的稳定分段作为候选：{names}；仍须同时报告总指标")
    elif any(status != "usable" for status in statuses):
        suggestions.append("当前没有可靠的稳定分解段；补齐阶段打点，或改用对目标更敏感的指标")

    effect_assessment: Optional[Dict[str, Any]] = None
    if effect_ms is not None:
        effect_assessment = []
        for run in loaded:
            summary = run["focusSummary"]
            if summary["unit"] != "ms" or summary["stdev"] is None:
                continue
            noise = 2.0 * summary["stdev"]
            effect_assessment.append(
                {
                    "path": run["path"],
                    "effectMs": effect_ms,
                    "approxTwoSigmaMs": noise,
                    "effectBelowNoise": effect_ms < noise,
                }
            )
        if any(item["effectBelowNoise"] for item in effect_assessment):
            reasons.append("目标变化幅度小于焦点指标约 2×标准差，当前样本无法可靠归因")
            suggestions.append("先修测量，或改用方差更小的同口径分解指标")

    usable = (
        all(status == "usable" for status in statuses)
        and not context_warnings
        and cross_run_focus["status"] != "shift_detected"
    )
    if not reasons:
        reasons.append("焦点指标达到当前样本量与 CV 门槛")
    if not suggestions:
        suggestions.append("可进入同口径 baseline 比较；仍须执行功能回归并保留全部来源")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "tool": "apm_diagnose",
        "generatedAt": utc_now(),
        "focusMetric": focus_name,
        "minSamples": min_samples,
        "thresholds": {
            "usableCvBelow": CV_MARGINAL,
            "unusableCvAbove": CV_UNUSABLE,
            "multimodalSeparationRatio": MULTIMODAL_SEPARATION_RATIO,
            "multimodalGapShare": MULTIMODAL_GAP_SHARE,
        },
        "runs": loaded,
        "contextWarnings": context_warnings,
        "crossRunCorrelations": cross_correlations,
        "crossRunFocus": cross_run_focus,
        "effectAssessment": effect_assessment,
        "stageCandidates": stage_candidates,
        "verdict": {
            "usable": usable,
            "exitCode": 0 if usable else 2,
            "reasons": reasons,
            "suggestions": suggestions,
        },
        "disclaimer": "相关性仅用于提出控制实验假设；本工具不自动分层、不校正、不丢弃任何样本。",
    }


def format_percent(value: Optional[float]) -> str:
    return "不可用" if value is None else f"{value * 100:.1f}%"


def print_text(report: Dict[str, Any]) -> None:
    print("════════ APM 测量方差诊断 ════════")
    print(f"焦点指标：{report['focusMetric']}   最低样本：{report['minSamples']}")
    for run in report["runs"]:
        print(f"\n▎{run['path']}")
        summary = run["focusSummary"]
        print(
            f"  总量  n={summary['n']}  p50={summary['median']:.0f}{summary['unit']}  "
            f"CV={format_percent(summary['cv'])}  判定={summary['status']}"
        )
        clusters = summary["suspectedClusters"]
        if len(clusters) >= 2:
            rendered = " | ".join(
                f"n={item['n']} {item['min']:.0f}–{item['max']:.0f}" for item in clusters
            )
            print(f"  ⚠️ 疑似多簇：{rendered}")
        print("  分段 CV（按稳定度排序）：")
        for item in run["metricSummaries"]:
            if item["name"].startswith("startup.stage."):
                print(
                    f"    {item['name']:<32} n={item['n']:<3} p50={item['median']:>7.1f}"
                    f"{item['unit']}  CV={format_percent(item['cv'])}"
                )
        if run["correlations"]:
            print("  可疑变量相关性（仅线索，不是因果）：")
            for item in run["correlations"][:5]:
                print(
                    f"    {item['candidate']}  n={item['n']}  "
                    f"Pearson r={item['pearson']:+.3f}  Spearman ρ={item['spearman']:+.3f}"
                )
        for warning in run["warnings"]:
            print(f"  ⚠️ {warning}")

    if report["contextWarnings"]:
        print("\n⚠️ 跨运行口径问题")
        for warning in report["contextWarnings"]:
            print(f"  · {warning}")

    cross_focus = report["crossRunFocus"]
    if cross_focus["status"] in {"shift_detected", "consistent"}:
        print("\n▎跨 run 焦点漂移")
        medians = " | ".join(
            f"{item['path']}: p50={item['median']:.0f}{item.get('unit', 'ms')}"
            for item in cross_focus["runs"]
        )
        print(f"  {medians}")
        print(
            f"  Δp50={cross_focus['medianDelta']:.1f}ms  "
            f"判定门槛={cross_focus['decisionThreshold']:.1f}ms  "
            f"状态={cross_focus['status']}"
        )

    inconsistent = [
        item for item in report["crossRunCorrelations"] if item["stability"] == "direction_inconsistent"
    ]
    if inconsistent:
        print("\n⚠️ 跨运行相关方向")
        for item in inconsistent:
            print(f"  · {item['candidate']}：方向不一致，禁止自动分层或校正")

    if report["effectAssessment"]:
        print("\n▎目标幅度 vs 测量噪声")
        for item in report["effectAssessment"]:
            verdict = "小于" if item["effectBelowNoise"] else "不小于"
            print(
                f"  {item['path']}  目标 {item['effectMs']:.0f}ms {verdict} "
                f"约 2σ={item['approxTwoSigmaMs']:.1f}ms"
            )

    print("\n▎判据建议")
    for reason in report["verdict"]["reasons"]:
        print(f"  · {reason}")
    for suggestion in report["verdict"]["suggestions"]:
        print(f"  → {suggestion}")
    print("\n" + report["disclaimer"])


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须 >= 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="诊断 APM 测量方差、疑似多簇与分解段稳定性")
    parser.add_argument("runs", nargs="+", type=Path, help="run 目录或 metrics.json（可传多个）")
    parser.add_argument("--metric", help="焦点指标；默认优先 first_frame / total")
    parser.add_argument(
        "--min-samples", type=positive_int, default=DEFAULT_MIN_SAMPLES, help="每组最低有效样本量"
    )
    parser.add_argument("--effect-ms", type=float, help="待验证变化幅度，用于与约 2σ 比较")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.effect_ms is not None and (not math.isfinite(args.effect_ms) or args.effect_ms < 0):
        print("⛔ --effect-ms 必须是有限的非负数", file=sys.stderr)
        return 1
    try:
        report = build_report(args.runs, args.metric, args.min_samples, args.effect_ms)
    except DiagnoseError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print_text(report)
    return int(report["verdict"]["exitCode"])


if __name__ == "__main__":
    sys.exit(main())
