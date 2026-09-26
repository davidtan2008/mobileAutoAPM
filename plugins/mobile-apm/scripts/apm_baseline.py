#!/usr/bin/env python3
"""基线对比与显著性判定 —— APM 闭环"验证"阶段的核心。

回答一个 Agent 最容易自欺欺人的问题：**这次改动到底有没有真的改善？**

只用标准库，无第三方依赖。用置换检验(permutation test)判断差异是否显著，
避免"测了两次数字不一样就宣布优化成功"。

用法:
  # 记录基线
  python3 apm_baseline.py record --in run.json --out .apm/baseline/startup.json

  # 与基线对比
  python3 apm_baseline.py compare --baseline .apm/baseline/startup.json --run run.json

  # 机器可读（JSON 模式仍保留退出码）
  python3 apm_baseline.py compare --baseline b.json --run r.json --json

  # 只做方差诊断
  python3 apm_baseline.py diagnose --input r.json

退出码：`0` = 测量可信且无劣化，`1` = 数据/口径/测量质量问题，
`2` = 可确认劣化。高方差或多峰不会被包装成 `2`。

数据格式（baseline 与 run 同构）:
{
  "context": {"commit":"abc","device":"iPhone 17 Pro","build":"release",
              "command":"mobilebuildmcp simulator build-and-run ..."},
  "metrics": [
    {"name":"startup.cold","unit":"ms","direction":"lower_is_better",
     "samples":[1234,1250,1210,1244]}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

import apm_diagnose

PERM_ITERS = 10000
RNG_SEED = 42
DIAGNOSTIC_MIN_SAMPLES = 5


class BaselineError(Exception):
    """基线输入或比较契约不满足。"""


# ---------- 输入与统计工具 ----------

def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_payload(data, path: str) -> dict:
    """在 record/compare 前拒绝会制造假结论的输入。"""
    if not isinstance(data, dict):
        raise BaselineError(f"{path}: 顶层必须是对象")
    metrics = data.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise BaselineError(f"{path}: metrics 必须是非空数组")
    seen = set()
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise BaselineError(f"{path}: metrics[{index}] 必须是对象")
        name = metric.get("name")
        if not isinstance(name, str) or not name:
            raise BaselineError(f"{path}: metrics[{index}] 缺少 name")
        if name in seen:
            raise BaselineError(f"{path}: 重复指标名 {name}")
        seen.add(name)
        samples = metric.get("samples")
        if not isinstance(samples, list) or not samples:
            raise BaselineError(f"{path}: {name}.samples 必须是非空数组")
        for sample in samples:
            number = _finite_number(sample)
            if number is None:
                raise BaselineError(f"{path}: {name}.samples 含非有限数")
        unit = metric.get("unit", "")
        if not isinstance(unit, str):
            raise BaselineError(f"{path}: {name}.unit 必须是字符串")
        direction = metric.get("direction", "lower_is_better")
        if direction not in ("lower_is_better", "higher_is_better"):
            raise BaselineError(f"{path}: {name}.direction 无效：{direction}")
        if metric.get("minEffect") is not None:
            minimum = _finite_number(metric["minEffect"])
            if minimum is None or minimum < 0:
                raise BaselineError(f"{path}: {name}.minEffect 必须是非负有限数")
    context = data.get("context", {})
    if not isinstance(context, dict):
        raise BaselineError(f"{path}: context 必须是对象")
    return data


def percentile(xs: list[float], p: float):
    """线性插值分位数，p 取 0..100。"""
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def cv(xs: list[float]):
    """变异系数 = 标准差 / 均值；不可计算时返回 None。"""
    if len(xs) < 2:
        return None
    m = statistics.mean(xs)
    if m == 0:
        return None
    return statistics.stdev(xs) / abs(m)


def pooled_stdev(a: list[float], b: list[float]) -> float:
    """合并样本标准差（pooled standard deviation）。

    用于估计「两组测量各自的抖动有多大」——
    改进幅度必须跑赢这个抖动才有意义。
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return 0.0
    s1, s2 = statistics.stdev(a), statistics.stdev(b)
    return math.sqrt(((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / (n1 + n2 - 2))


def noise_band(a: list[float], b: list[float], min_effect: float, k: float = 2.0) -> float:
    """噪声带 —— 改进必须跨过它才算数。

    **两道阈值必须同时满足**：

      · k·pooled_std —— **统计下限**：幅度必须跑赢测量抖动本身
      · min_effect   —— **实用下限**：不追"真实但无意义"的收益

    只做显著性检验会让 Agent 为 0.3ms 的"显著提升"去改代码；
    只做实用阈值则会被噪声骗。**两者取较大值**才能同时挡住这两类错误。

    借鉴自 uphold/metrognome 的双阈值闸门设计。
    """
    return max(min_effect, k * pooled_stdev(a, b))


def perm_test(a: list[float], b: list[float], iters: int = PERM_ITERS) -> float:
    """置换检验：返回"两组无差异"这一原假设的 p 值。

    不依赖正态假设。**检验统计量用均值差**而非中位数差——中位数在小样本下
    分辨率极低（n=5 时最小可达 p 约 0.004，实际数据完全分离也只能测到 ~0.05），
    会大量漏判真实改善。均值利用全部样本，功效高得多；
    离群值带来的风险由上层的 CV 噪声检查兜住。

    展示层仍报告 p50/p90（行业口径），二者分工不同。
    """
    if len(a) < 2 or len(b) < 2:
        return 1.0
    obs = abs(statistics.mean(a) - statistics.mean(b))
    pooled = list(a) + list(b)
    n = len(a)
    rng = random.Random(RNG_SEED)
    hits = 0
    for _ in range(iters):
        rng.shuffle(pooled)
        if abs(statistics.mean(pooled[:n]) - statistics.mean(pooled[n:])) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)


# ---------- 对比逻辑 ----------

def is_variance_sensitive(metric: dict) -> bool:
    """只对启动/延迟等正连续量做 CV 门禁。

    `frameOverrunMs` 等指标允许负值（负值表示提前完成），不能套启动 CV 规则。
    """
    name = str(metric.get("name", "")).lower()
    return name.startswith("startup.") or name.startswith("latency.")


def quality_for_metric(metric: dict) -> dict:
    summary = apm_diagnose.summarize_metric(metric, DIAGNOSTIC_MIN_SAMPLES)
    return {
        "status": summary["status"],
        "cv": summary["cv"],
        "suspectedClusters": summary["suspectedClusters"],
    }


def compare_metric(base: dict, run: dict, min_effect_pct: float) -> dict:
    name = base.get("name") or run.get("name") or "?"
    unit = run.get("unit", base.get("unit", ""))
    direction = run.get("direction", base.get("direction", "lower_is_better"))
    declared = run.get("minEffect", base.get("minEffect"))
    declared_min_effect = _finite_number(declared) if declared is not None else None

    a = [float(x) for x in base.get("samples", [])]
    b = [float(x) for x in run.get("samples", [])]
    out = {
        "name": name, "unit": unit, "direction": direction,
        "n_baseline": len(a), "n_run": len(b),
        "verdict": None, "reasons": [],
    }

    if len(a) < 2 or len(b) < 2:
        out["verdict"] = "insufficient-data"
        out["reasons"].append(
            f"样本不足（基线 {len(a)} 次 / 本次 {len(b)} 次），至少各需 2 次，建议 ≥5 次")
        return out

    med_a, med_b = statistics.median(a), statistics.median(b)
    cv_a, cv_b = cv(a), cv(b)
    out.update({
        "baseline": {"p50": med_a, "p90": percentile(a, 90),
                     "mean": statistics.mean(a), "stdev": statistics.stdev(a),
                     "cv": cv_a},
        "run": {"p50": med_b, "p90": percentile(b, 90),
                "mean": statistics.mean(b), "stdev": statistics.stdev(b),
                "cv": cv_b},
    })

    if is_variance_sensitive(run):
        quality = quality_for_metric({
            "name": name,
            "unit": unit,
            "direction": direction,
            "samples": a,
        })
        run_quality = quality_for_metric({
            "name": name,
            "unit": unit,
            "direction": direction,
            "samples": b,
        })
        out["measurement_quality"] = {"baseline": quality, "run": run_quality}
        hard_bad = {"unusable_multimodal", "unusable_high_variance", "insufficient_variation"}
        if quality["status"] in hard_bad or run_quality["status"] in hard_bad:
            out["verdict"] = "measurement-unreliable"
            out["reasons"].append(
                "测量质量门禁未通过：基线或本次指标存在高方差/疑似多峰/无法计算噪声；"
                "先修测量，不把统计差异包装成优化结论")
            if quality["status"] == "unusable_multimodal" or run_quality["status"] == "unusable_multimodal":
                out["reasons"].append("检测到疑似多簇，禁止挑快样本或自动分层")
            return out
        if quality["status"] == "marginal" or run_quality["status"] == "marginal":
            out["reasons"].append("测量 CV 处于 10–30%，只能支撑大幅变化")

    delta = med_b - med_a
    pct = (delta / med_a * 100.0) if med_a else None
    out["delta_p50"] = delta
    out["delta_pct"] = pct

    practical = declared_min_effect if declared_min_effect is not None \
        else (abs(med_a) * min_effect_pct / 100.0)
    band = noise_band(a, b, practical)
    out["noise_band"] = band
    out["noise_band_source"] = "declared" if declared_min_effect is not None else "percent"
    out["pooled_stdev"] = pooled_stdev(a, b)

    p = perm_test(a, b)
    out["p_value"] = p

    if p >= 0.05:
        out["verdict"] = "no-significant-change"
        out["reasons"].append(f"置换检验 p={p:.3f} ≥ 0.05，差异不显著")
        if min(len(a), len(b)) < 5:
            out["reasons"].append(
                "⚠️ 样本偏少（n<5），检验功效有限——『不显著』可能是样本不够，"
                "而非真的没变化。要确认小幅改善，需增加测量次数")
        return out

    if abs(delta) < band:
        out["verdict"] = "no-practical-change"
        pct_text = "不可用" if pct is None else f"{pct:+.1f}%"
        out["reasons"].append(
            f"统计显著但幅度 {delta:+.2f}{unit}（{pct_text}）未跨过噪声带 {band:.2f}{unit}"
            f"（= max(实用下限 {practical:.2f}, 2×合并标准差 {2 * out['pooled_stdev']:.2f})）。"
            "可能是噪声或无关紧要的变化")
        return out

    improved = (delta < 0) if direction == "lower_is_better" else (delta > 0)
    out["verdict"] = "improved" if improved else "regressed"
    pct_text = "不可用" if pct is None else f"{pct:+.1f}%"
    out["reasons"].append(
        f"p={p:.4f}，p50 变化 {pct_text}，跨过噪声带 {band:.2f}{unit}"
        f"（{'越低越好' if direction == 'lower_is_better' else '越高越好'}）")
    return out


VERDICT_LABEL = {
    "improved": "✅ 改善",
    "regressed": "❌ 劣化",
    "no-significant-change": "➖ 无显著变化",
    "no-practical-change": "➖ 变化不具实际意义",
    "insufficient-data": "⚠️ 样本不足",
    "measurement-unreliable": "⛔ 测量不可信",
    "no-baseline": "⚠️ 无基线",
    "missing-in-run": "⚠️ 本次漏采",
    "context-mismatch": "⛔ 口径不一致",
}


def load_metrics(path: str) -> dict:
    source = Path(path)
    if source.is_dir():
        source = source / "metrics.json"
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineError(f"找不到输入文件：{source}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"JSON 无法解析：{source}: {exc}") from exc
    return validate_payload(data, str(source))


def context_value(context: dict, *names):
    for name in names:
        if name in context and context[name] is not None:
            return context[name]
    return None


def compare_contexts(base: dict, run: dict):
    warnings = []
    base_context = base.get("context", {})
    run_context = run.get("context", {})
    aliases = {
        "device": ("device", "deviceModel"),
        "build": ("build", "buildType"),
    }
    for canonical, names in aliases.items():
        left = context_value(base_context, *names)
        right = context_value(run_context, *names)
        if left is None or right is None:
            warnings.append(f"{canonical}: 至少一侧缺失，无法确认口径一致")
        elif left != right:
            warnings.append(f"{canonical}: 基线={left} vs 本次={right}")

    for canonical in ("platform", "osVersion", "profile"):
        left = context_value(base_context, canonical)
        right = context_value(run_context, canonical)
        if left is not None and right is not None and left != right:
            warnings.append(f"{canonical}: 基线={left} vs 本次={right}")

    left_signature = context_value(base_context, "measurementSignature")
    right_signature = context_value(run_context, "measurementSignature")
    if left_signature and right_signature:
        if left_signature != right_signature:
            warnings.append("measurementSignature: 测量方法/起点/终点/样本计划不一致")
    else:
        left_method = context_value(base_context, "measurementMethod", "method", "command")
        right_method = context_value(run_context, "measurementMethod", "method", "command")
        if left_method and right_method and left_method != right_method:
            warnings.append(f"command/method: 基线={left_method} vs 本次={right_method}")
    return warnings


def diagnosis_report(path: str, metric=None, effect_ms=None):
    return apm_diagnose.build_report(
        [Path(path)], metric, DIAGNOSTIC_MIN_SAMPLES, effect_ms
    )


def choose_compare_focus(base: dict, run: dict):
    run_names = [metric["name"] for metric in run["metrics"]]
    base_names = {metric["name"] for metric in base["metrics"]}
    common = [name for name in run_names if name in base_names]
    preferred = [name for name in common if "first_frame" in name or name.endswith(".total")]
    return (preferred or common or [None])[0]


def cmd_record(args) -> int:
    data = load_metrics(args.input)
    if args.require_healthy:
        report = diagnosis_report(args.input)
        if report["verdict"]["exitCode"] != 0:
            raise BaselineError(
                "输入测量未通过方差诊断，拒绝写入基线："
                + "；".join(report["verdict"]["reasons"])
            )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 记录基线时若已有旧基线，先备份，避免无声覆盖
    if out.exists() and not args.force:
        bak = out.with_suffix(out.suffix + ".bak")
        bak.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已存在基线，旧文件备份至 {bak}")
    try:
        serialized = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError as exc:
        raise BaselineError(f"输入含非标准 JSON 数字：{exc}") from exc
    out.write_text(serialized + "\n", encoding="utf-8")
    print(f"✅ 基线已写入 {out}（{len(data['metrics'])} 个指标）")
    if not args.require_healthy:
        try:
            report = diagnosis_report(args.input)
            if report["verdict"]["exitCode"] != 0:
                print("⚠️ 基线已记录，但测量质量未达门槛；不能据此宣布优化有效。")
        except apm_diagnose.DiagnoseError:
            pass
    return 0


def cmd_diagnose(args) -> int:
    if args.effect_ms is not None and (not math.isfinite(args.effect_ms) or args.effect_ms < 0):
        raise BaselineError("--effect-ms 必须是非负有限数")
    report = diagnosis_report(args.input, args.metric, args.effect_ms)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        apm_diagnose.print_text(report)
    return int(report["verdict"]["exitCode"])


def format_number(value, digits=2):
    return "不可用" if value is None else f"{value:.{digits}f}"


def format_percent(value):
    return "不可用" if value is None else f"{value:.1%}"


def cmd_compare(args) -> int:
    base = load_metrics(args.baseline)
    run = load_metrics(args.run)
    ctx_warn = compare_contexts(base, run)
    base_context = base.get("context", {})
    run_context = run.get("context", {})
    same_commit = bool(
        base_context.get("commit")
        and run_context.get("commit")
        and base_context.get("commit") == run_context.get("commit")
    )

    base_by = {metric["name"]: metric for metric in base["metrics"]}
    run_by = {metric["name"]: metric for metric in run["metrics"]}
    results = []
    for metric in run["metrics"]:
        baseline_metric = base_by.get(metric["name"])
        if baseline_metric is None:
            results.append({
                "name": metric["name"],
                "verdict": "no-baseline",
                "reasons": ["基线中无此指标，无法对比"],
            })
            continue
        if baseline_metric.get("unit", "") != metric.get("unit", ""):
            results.append({
                "name": metric["name"],
                "verdict": "context-mismatch",
                "reasons": [
                    f"unit 不一致：基线={baseline_metric.get('unit')} vs 本次={metric.get('unit')}"
                ],
            })
            continue
        if baseline_metric.get("direction", "lower_is_better") != metric.get("direction", "lower_is_better"):
            results.append({
                "name": metric["name"],
                "verdict": "context-mismatch",
                "reasons": ["direction 不一致"],
            })
            continue
        results.append(compare_metric(baseline_metric, metric, args.min_effect))

    for name in sorted(set(base_by) - set(run_by)):
        results.append({
            "name": name,
            "verdict": "missing-in-run",
            "reasons": ["基线有此指标，但本次运行未采到；不能静默忽略"],
        })

    focus = choose_compare_focus(base, run)
    diagnosis = None
    diagnosis_error = None
    if focus:
        try:
            diagnosis = apm_diagnose.build_report(
                [Path(args.baseline), Path(args.run)],
                focus,
                DIAGNOSTIC_MIN_SAMPLES,
                None,
            )
            for warning in diagnosis.get("contextWarnings", []):
                if warning not in ctx_warn:
                    ctx_warn.append(warning)
        except apm_diagnose.DiagnoseError as exc:
            diagnosis_error = str(exc)
            ctx_warn.append(f"方差诊断失败：{exc}")
    else:
        diagnosis_error = "baseline/run 没有共同指标，无法诊断"

    if diagnosis is not None and diagnosis["verdict"]["exitCode"] != 0:
        for result in results:
            if result["name"] != focus or result["verdict"] not in {
                "improved", "regressed", "no-significant-change", "no-practical-change"
            }:
                continue
            result["provisionalVerdict"] = result["verdict"]
            result["verdict"] = "measurement-unreliable"
            result["reasons"].append(
                "跨 run/测量质量未通过；原统计差异仅保留为 provisionalVerdict，不能当确认结论"
            )

    focus_quality_error = any(
        result["name"] == focus
        and result["verdict"] in {"measurement-unreliable", "insufficient-data"}
        for result in results
    )
    data_error = bool(
        ctx_warn
        or diagnosis_error
        or (diagnosis is not None and diagnosis["verdict"]["exitCode"] != 0)
        or focus_quality_error
        or any(
            result["verdict"] in {"no-baseline", "missing-in-run", "context-mismatch"}
            for result in results
        )
    )
    regressions = [result for result in results if result["verdict"] == "regressed"]
    payload = {
        "context_warnings": ctx_warn,
        "same_commit": same_commit,
        "focus_metric": focus,
        "results": results,
        "diagnosis": diagnosis,
        "diagnosis_error": diagnosis_error,
        "data_error": data_error,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if data_error else (2 if regressions else 0)

    print("=" * 72)
    print("  APM 基线对比")
    print("=" * 72)
    if ctx_warn:
        print("\n⚠️  测量上下文/质量不一致 —— 以下差异不能归因于代码：")
        for warning in ctx_warn:
            print(f"    · {warning}")
    if same_commit:
        print("\nℹ️  基线与本次为同一 commit：这是无改动对照，可用于验证测量方法本身是否可靠。")
    elif base_context.get("commit") and run_context.get("commit"):
        print("\nℹ️  基线与本次 commit 不同；仍需先确认上方口径检查通过。")

    for result in results:
        verdict_label = VERDICT_LABEL.get(result["verdict"], result["verdict"])
        if result["verdict"] == "measurement-unreliable" and result["name"] != focus:
            verdict_label = "⚠️ 测量不可信（辅助指标，不阻断焦点）"
        print(f"\n▎{result['name']}  {verdict_label}")
        if "baseline" in result:
            baseline_stats = result["baseline"]
            run_stats = result["run"]
            print(
                f"    基线  p50={format_number(baseline_stats['p50'])}  "
                f"p90={format_number(baseline_stats['p90'])}  "
                f"n={result['n_baseline']}  CV={format_percent(baseline_stats['cv'])}"
            )
            print(
                f"    本次  p50={format_number(run_stats['p50'])}  "
                f"p90={format_number(run_stats['p90'])}  "
                f"n={result['n_run']}  CV={format_percent(run_stats['cv'])}"
            )
            if "delta_p50" in result:
                print(
                    f"    变化  {result['delta_p50']:+.2f} "
                    f"({format_percent(result['delta_pct'])} p50)"
                )
            if "p_value" in result:
                print(f"    p={result['p_value']:.4f}")
            if "noise_band" in result:
                source = "指标声明" if result.get("noise_band_source") == "declared" else "百分比口径"
                print(
                    f"    噪声带 {result['noise_band']:.2f}  "
                    f"({source}；合并标准差 {result['pooled_stdev']:.2f}×2)"
                )
        for reason in result["reasons"]:
            print(f"    └─ {reason}")

    if diagnosis and not args.json:
        print("\n▎方差诊断建议")
        for suggestion in diagnosis["verdict"]["suggestions"]:
            print(f"    → {suggestion}")

    print("\n" + "=" * 72)
    if data_error:
        print("⛔ 数据或测量口径有问题：不能把本次结果当成有效劣化/改善结论。")
        return 1
    if regressions:
        print(f"⛔ {len(regressions)} 项劣化，必须处理："
              f"{', '.join(result['name'] for result in regressions)}")
        return 2
    print("✅ 无劣化项。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="APM 基线与显著性对比")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="写入/更新基线")
    r.add_argument("--in", dest="input", required=True)
    r.add_argument("--out", dest="output", required=True)
    r.add_argument("--force", action="store_true", help="直接覆盖，不备份旧基线")
    r.add_argument("--require-healthy", action="store_true",
                   help="方差诊断未通过时拒绝写入基线")
    r.set_defaults(func=cmd_record)

    c = sub.add_parser("compare", help="与基线对比")
    c.add_argument("--baseline", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--min-effect", type=float, default=5.0,
                   help="最小关注幅度(%%)，低于此值即使显著也判为无实际意义，默认 5")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_compare)

    d = sub.add_parser("diagnose", help="只诊断测量方差与判据，不做基线比较")
    d.add_argument("--input", required=True, help="run 目录或 metrics.json")
    d.add_argument("--metric", help="焦点指标；默认优先 first_frame / total")
    d.add_argument("--min-samples", type=apm_diagnose.positive_int, default=DIAGNOSTIC_MIN_SAMPLES)
    d.add_argument("--effect-ms", type=float)
    d.add_argument("--format", choices=("text", "json"), default="text")
    d.set_defaults(func=cmd_diagnose)

    args = ap.parse_args()
    try:
        return args.func(args)
    except (BaselineError, apm_diagnose.DiagnoseError) as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
