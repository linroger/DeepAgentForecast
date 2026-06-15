"""Ensemble aggregation of multiple structured forecasts (EXECPLAN2 I-9-0/I-2-4/I-3-3).

A single OASIS run is one stochastic sample. Running the sim+report N times (with
different seeds) and aggregating the resulting structured forecasts turns a point
estimate into a *distribution*: frequency-derived scenario probabilities with
spread (a confidence-interval proxy) and an inter-run agreement score. Pure and
side-effect-free so it is unit-testable offline; the N-run driver lives in
scripts/forecast_tools.py (built on the existing PREPARE-fork).
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List


def _norm_name(name) -> str:
    """Loose scenario-name key so 'Samsung leads' ~ 'samsung  leads.'."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w一-鿿]+", " ", str(name or "").lower())).strip()


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def aggregate_forecasts(forecasts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate N structured-forecast dicts (from forecast_extractor) into one.

    Scenarios are matched across runs by normalized name. For each matched
    scenario we report the mean probability, spread (stdev + min/max as a CI
    proxy), and how many of the N runs surfaced it (support). Probabilities are
    renormalized to sum to 1. ``agreement`` ∈ [0,1] is 1 minus the mean spread.
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
            b = buckets.setdefault(key, {"name": s.get("name"), "probs": [], "drivers": set(), "criteria": ""})
            try:
                b["probs"].append(float(s.get("probability") or 0.0))
            except (TypeError, ValueError):
                pass
            for d in (s.get("key_drivers") or []):
                b["drivers"].add(str(d))
            if not b["criteria"] and s.get("resolution_criteria"):
                b["criteria"] = str(s.get("resolution_criteria"))

    agg = []
    for b in buckets.values():
        probs = b["probs"] or [0.0]
        agg.append({
            "name": b["name"],
            "mean_probability": round(_mean(probs), 4),
            "stdev": round(_stdev(probs), 4),
            "min": round(min(probs), 4),
            "max": round(max(probs), 4),
            "support": len(b["probs"]),          # how many runs surfaced it
            "support_ratio": round(len(b["probs"]) / n, 3),
            "key_drivers": sorted(b["drivers"])[:8],
            "resolution_criteria": b["criteria"],
        })
    # renormalize mean probabilities to sum to 1
    total = sum(s["mean_probability"] for s in agg)
    if total > 0:
        for s in agg:
            s["probability"] = round(s["mean_probability"] / total, 4)
    else:
        for s in agg:
            s["probability"] = 0.0
    agg.sort(key=lambda s: s["probability"], reverse=True)

    mean_spread = _mean([s["stdev"] for s in agg]) if agg else 0.0
    return {
        "n_runs": n,
        "scenarios": agg,
        "agreement": round(max(0.0, 1.0 - mean_spread * 2), 3),  # scaled spread→agreement
        "headline": runs[0].get("headline", ""),
        "horizon": runs[0].get("horizon", ""),
        "schema_version": 1,
    }
