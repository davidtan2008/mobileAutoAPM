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

  # 机器可读
  python3 apm_baseline.py compare --baseline b.json --run r.json --json

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

PERM_ITERS = 10000
RNG_SEED = 42


# ---------- 统计工具 ----------

def percentile(xs: list[float], p: float) -> float:
    """线性插值分位数，p 取 0..100。"""
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def cv(xs: list[float]) -> float:
    """变异系数 = 标准差 / 均值。衡量测量噪声。"""
    if len(xs) < 2:
        return 0.0
    m = statistics.mean(xs)
    if m == 0:
        return 0.0
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

def compare_metric(base: dict, run: dict, min_effect_pct: float) -> dict:
    name = base.get("name") or run.get("name") or "?"
    unit = run.get("unit", base.get("unit", ""))
    direction = run.get("direction", base.get("direction", "lower_is_better"))
    # 每指标可声明绝对实用阈值（单位同 metric.unit）。
    # 不同指标的"有意义的最小变化"差一个量级 —— 帧率差 2fps 有意义，内存差 2 字节没有。
    declared_min_effect = run.get("minEffect", base.get("minEffect"))

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

    delta = med_b - med_a
    pct = (delta / med_a * 100.0) if med_a else float("nan")
    out["delta_p50"] = delta
    out["delta_pct"] = pct

    # ── 噪声带：改进必须跨过它 ──────────────────────────────
    # 未声明 minEffect 时，退化为「基线的 X%」这一百分比口径
    practical = declared_min_effect if declared_min_effect is not None \
        else (abs(med_a) * min_effect_pct / 100.0)
    band = noise_band(a, b, practical)
    out["noise_band"] = band
    out["noise_band_source"] = "declared" if declared_min_effect is not None else "percent"
    out["pooled_stdev"] = pooled_stdev(a, b)

    # 噪声检查：噪声太大时任何结论都不可信
    noisy = cv_a > 0.30 or cv_b > 0.30
    if noisy:
        out["reasons"].append(
            f"测量噪声过大（基线 CV={cv_a:.0%} / 本次 CV={cv_b:.0%}，阈值 30%）。"
            "此时结论不可信，应先稳定测量（增加样本、控制变量、降温、固定设备）")

    p = perm_test(a, b)
    out["p_value"] = p

    # 统计显著性
    if p >= 0.05:
        out["verdict"] = "no-significant-change"
        out["reasons"].append(f"置换检验 p={p:.3f} ≥ 0.05，差异不显著")
        if min(len(a), len(b)) < 5:
            out["reasons"].append(
                "⚠️ 样本偏少（n<5），检验功效有限——『不显著』可能是样本不够，"
                "而非真的没变化。要确认小幅改善，需增加测量次数")
        return out

    # 实用显著性：幅度必须跨过噪声带（= max(实用下限, 2×合并标准差)）
    if abs(delta) < band:
        out["verdict"] = "no-practical-change"
        out["reasons"].append(
            f"统计显著但幅度 {delta:+.2f}{unit}（{pct:+.1f}%）未跨过噪声带 {band:.2f}{unit}"
            f"（= max(实用下限 {practical:.2f}, 2×合并标准差 {2 * out['pooled_stdev']:.2f})）。"
            "可能是噪声或无关紧要的变化")
        return out

    improved = (delta < 0) if direction == "lower_is_better" else (delta > 0)
    out["verdict"] = "improved" if improved else "regressed"
    out["reasons"].append(
        f"p={p:.4f}，p50 变化 {pct:+.1f}%，跨过噪声带 {band:.2f}{unit}"
        f"（{'越低越好' if direction == 'lower_is_better' else '越高越好'}）")
    if noisy:
        out["reasons"].append("⚠️ 尽管跨过噪声带，但测量噪声偏大，建议复测确认")
    return out


VERDICT_LABEL = {
    "improved": "✅ 改善",
    "regressed": "❌ 劣化",
    "no-significant-change": "➖ 无显著变化",
    "no-practical-change": "➖ 变化不具实际意义",
    "insufficient-data": "⚠️ 样本不足",
}


def load_metrics(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "metrics" not in data:
        raise SystemExit(f"{path}: 缺少 'metrics' 字段")
    return data


def cmd_record(args) -> int:
    data = load_metrics(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 记录基线时若已有旧基线，先备份，避免无声覆盖
    if out.exists() and not args.force:
        bak = out.with_suffix(out.suffix + ".bak")
        bak.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已存在基线，旧文件备份至 {bak}")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 基线已写入 {out}（{len(data['metrics'])} 个指标）")
    return 0


def cmd_compare(args) -> int:
    base = load_metrics(args.baseline)
    run = load_metrics(args.run)

    ctx_a, ctx_b = base.get("context", {}), run.get("context", {})
    ctx_warn = []
    for k in ("device", "build"):
        if ctx_a.get(k) and ctx_b.get(k) and ctx_a[k] != ctx_b[k]:
            ctx_warn.append(f"{k}: 基线={ctx_a[k]} vs 本次={ctx_b[k]}")
    if ctx_a.get("commit") and ctx_b.get("commit") and ctx_a["commit"] != ctx_b["commit"]:
        ctx_diff_commit = True
    else:
        ctx_diff_commit = False

    base_by = {m["name"]: m for m in base["metrics"]}
    results = []
    for m in run["metrics"]:
        b = base_by.get(m["name"])
        if not b:
            results.append({"name": m["name"], "verdict": "no-baseline",
                            "reasons": ["基线中无此指标，无法对比"]})
            continue
        results.append(compare_metric(b, m, args.min_effect))

    payload = {"context_warnings": ctx_warn, "same_commit": not ctx_diff_commit,
               "results": results}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("=" * 72)
    print("  APM 基线对比")
    print("=" * 72)
    if ctx_warn:
        print("\n⚠️  测量上下文不一致 —— 以下差异可能是环境差异而非代码效果：")
        for w in ctx_warn:
            print(f"    · {w}")
    if not ctx_diff_commit:
        print("\nℹ️  基线与本次为同一 commit：这是**无改动对照**，可用于验证测量方法本身是否可靠。")

    for r in results:
        print(f"\n▎{r['name']}  {VERDICT_LABEL.get(r['verdict'], r['verdict'])}")
        if "baseline" in r:
            b, n = r["baseline"], r["run"]
            print(f"    基线  p50={b['p50']:.2f}  p90={b['p90']:.2f}  n={r['n_baseline']}  CV={b['cv']:.1%}")
            print(f"    本次  p50={n['p50']:.2f}  p90={n['p90']:.2f}  n={r['n_run']}  CV={n['cv']:.1%}")
            print(f"    变化  {r['delta_p50']:+.2f} ({r['delta_pct']:+.1f}%)   p={r['p_value']:.4f}")
            if "noise_band" in r:
                src = "指标声明" if r.get("noise_band_source") == "declared" else "百分比口径"
                print(f"    噪声带 {r['noise_band']:.2f}  ({src}；合并标准差 {r['pooled_stdev']:.2f}×2)")
        for reason in r["reasons"]:
            print(f"    └─ {reason}")

    regressions = [r for r in results if r["verdict"] == "regressed"]
    print("\n" + "=" * 72)
    if regressions:
        print(f"⛔ {len(regressions)} 项劣化，必须处理："
              f"{', '.join(r['name'] for r in regressions)}")
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
    r.set_defaults(func=cmd_record)

    c = sub.add_parser("compare", help="与基线对比")
    c.add_argument("--baseline", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--min-effect", type=float, default=5.0,
                   help="最小关注幅度(%%)，低于此值即使显著也判为无实际意义，默认 5")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
