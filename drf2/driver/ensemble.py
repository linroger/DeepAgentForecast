"""Multi-seed ensemble fan-out + log-odds pooling.

The pooling MATH is imported from the legacy, unit-tested module
(app/services/ensemble.py: extremized log-odds pool R2-CAL-2, TV-distance agreement
R2-CAL-9, spread stats). Only the aggregation LOOP is re-expressed here because the
legacy ``aggregate_forecasts`` reads its extremizing factor from the global Flask-era
Config; the driver passes it explicitly (deterministic, config-injected — REDESIGN §2:
"calibration math must not be LLM-mediated" and must not depend on ambient state).
Output schema matches legacy (schema_version 1) so downstream consumers are portable.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .legacy import ensure_backend_importable

ensure_backend_importable()
from app.services.ensemble import (  # noqa: E402 — 唯一真源的池化/一致性数学
    _ensemble_agreement,
    _extremized_logodds,
    _mean,
    _norm_name,
    _stdev,
)


def aggregate_ensemble(forecasts: List[Dict[str, Any]], *,
                       extremize_a: Optional[float] = None) -> Dict[str, Any]:
    """聚合 N 份结构化预测（forecast_extractor 输出形状）为一份分布式预测。

    情景按规范化名称跨 run 匹配；发布概率 = extremize_a 非空时的 extremized log-odds
    池化（否则算术均值），再归一化到和为 1；stdev 落成 [p_low, p_high] 发布区间。
    """
    runs = [f for f in (forecasts or []) if isinstance(f, dict)]
    n = len(runs)
    if n == 0:
        return {"n_runs": 0, "scenarios": [], "agreement": None, "schema_version": 1}

    buckets: Dict[str, Dict[str, Any]] = {}
    for f in runs:
        for s in (f.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            key = _norm_name(s.get("name"))
            if not key:
                continue
            b = buckets.setdefault(key, {"name": s.get("name"), "probs": [],
                                         "drivers": set(), "criteria": ""})
            try:
                b["probs"].append(float(s.get("probability") or 0.0))
            except (TypeError, ValueError):
                pass
            for d in (s.get("key_drivers") or []):
                b["drivers"].add(str(d))
            if not b["criteria"] and s.get("resolution_criteria"):
                b["criteria"] = str(s.get("resolution_criteria"))

    try:
        a = float(extremize_a) if extremize_a is not None else None
    except (TypeError, ValueError):
        a = None

    agg = []
    for b in buckets.values():
        probs = b["probs"] or [0.0]
        row = {
            "name": b["name"],
            "mean_probability": round(_mean(probs), 4),  # 算术均值保留为诊断
            "stdev": round(_stdev(probs), 4),
            "min": round(min(probs), 4),
            "max": round(max(probs), 4),
            "support": len(b["probs"]),
            "support_ratio": round(len(b["probs"]) / n, 3),
            "key_drivers": sorted(b["drivers"])[:8],
            "resolution_criteria": b["criteria"],
        }
        row["_point"] = _extremized_logodds(probs, a) if a is not None else _mean(probs)
        agg.append(row)

    total = sum(s["_point"] for s in agg)
    for s in agg:
        s["probability"] = round(s["_point"] / total, 4) if total > 0 else 0.0
        s["p_low"] = round(max(0.0, s["probability"] - s["stdev"]), 4)
        s["p_high"] = round(min(1.0, s["probability"] + s["stdev"]), 4)
        s["pooling"] = "extremized_logodds" if a is not None else "arithmetic_mean"
        del s["_point"]
    agg.sort(key=lambda s: s["probability"], reverse=True)

    return {
        "n_runs": n,
        "scenarios": agg,
        "agreement": _ensemble_agreement(runs),
        "agreement_spread": round(
            max(0.0, 1.0 - (_mean([s["stdev"] for s in agg]) if agg else 0.0) * 2), 3),
        "extremize_a": a,
        "headline": runs[0].get("headline", ""),
        "horizon": runs[0].get("horizon", ""),
        "schema_version": 1,
    }


def fan_out(seeds: List[int], run_one: Callable[[int], Dict[str, Any]], *,
            extremize_a: Optional[float] = None) -> Dict[str, Any]:
    """多种子扇出：逐种子执行 run_one(seed) → 结构化预测，失败的种子记录错误但不
    中断其余种子（一次种子失败 ≠ 集合失败）；对成功者做 aggregate_ensemble。

    顺序执行（确定性、易审计）；并行扇出属 cutover 之后的优化项。
    """
    results: Dict[str, Dict[str, Any]] = {}
    ok_forecasts: List[Dict[str, Any]] = []
    for seed in seeds:
        try:
            fc = run_one(seed)
            if not isinstance(fc, dict):
                raise TypeError(f"run_one({seed}) returned {type(fc).__name__}, expected dict")
            results[str(seed)] = {"ok": True}
            ok_forecasts.append(fc)
        except Exception as e:  # noqa: BLE001 — 单种子失败必须被记录而非炸掉扇出
            results[str(seed)] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {
        "seeds": list(seeds),
        "results": results,
        "n_ok": len(ok_forecasts),
        "aggregate": aggregate_ensemble(ok_forecasts, extremize_a=extremize_a),
    }
