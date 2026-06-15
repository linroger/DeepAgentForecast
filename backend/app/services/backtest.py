"""Backtesting & calibration scoring for structured forecasts (EXECPLAN2 I-9-2).

Once a forecast's horizon passes and the real outcome is known, score how good
the probabilities were — Brier score (lower=better) and log-loss — and, across
many resolved forecasts, a calibration report (do things predicted at ~70%
actually happen ~70% of the time?). This is what closes the loop from
"forecasting tool" to "calibratable forecasting tool". Pure / offline-testable.

A resolved forecast pairs a structured forecast (scenarios w/ probabilities) with
an ``outcome``: the name of the scenario that actually occurred (or its index).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .ensemble import _norm_name


def _scenario_prob(scenario: Dict[str, Any]) -> float:
    for k in ("probability", "mean_probability"):
        v = scenario.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def score_forecast(forecast: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    """Score one resolved forecast against the scenario that actually happened.

    Multi-class Brier score = sum over scenarios of (p_i - y_i)^2 where y_i is 1
    for the realized scenario else 0. Also returns the probability the forecast
    assigned to the realized scenario and a log-loss (clamped).
    """
    scenarios = [s for s in (forecast.get("scenarios") or []) if isinstance(s, dict)]
    if not scenarios:
        return {"error": "no scenarios", "brier": None, "realized_probability": None}
    target = _norm_name(outcome)
    names = [_norm_name(s.get("name")) for s in scenarios]
    matched = target in names
    brier = 0.0
    realized_p = 0.0
    for s, nm in zip(scenarios, names):
        p = _scenario_prob(s)
        y = 1.0 if nm == target else 0.0
        brier += (p - y) ** 2
        if y:
            realized_p = p
    eps = 1e-9
    log_loss = -math.log(min(1.0, max(eps, realized_p))) if matched else None
    return {
        "brier": round(brier, 4),
        "realized_probability": round(realized_p, 4),
        "log_loss": round(log_loss, 4) if log_loss is not None else None,
        "outcome_matched_a_scenario": matched,
        "n_scenarios": len(scenarios),
    }


def calibration_report(resolved: List[Dict[str, Any]],
                       bins: int = 5) -> Dict[str, Any]:
    """Aggregate calibration across many resolved forecasts.

    Each item: {"forecast": <forecast dict>, "outcome": <scenario name>}. Buckets
    every scenario's predicted probability and compares the bucket's mean
    predicted probability to the observed hit-rate (how often those scenarios
    actually occurred). Returns per-bin stats + mean Brier + a calibration error.
    """
    items = [r for r in (resolved or []) if isinstance(r, dict) and r.get("forecast")]
    if not items:
        return {"n": 0, "mean_brier": None, "bins": [], "calibration_error": None}

    edges = [i / bins for i in range(bins + 1)]
    buckets = [{"lo": edges[i], "hi": edges[i + 1], "preds": [], "hits": []} for i in range(bins)]
    briers: List[float] = []

    for r in items:
        fc, outcome = r["forecast"], r.get("outcome")
        sc = score_forecast(fc, outcome or "")
        if sc.get("brier") is not None:
            briers.append(sc["brier"])
        tgt = _norm_name(outcome)
        for s in (fc.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            p = _scenario_prob(s)
            hit = 1.0 if _norm_name(s.get("name")) == tgt else 0.0
            idx = min(bins - 1, max(0, int(p * bins)))
            buckets[idx]["preds"].append(p)
            buckets[idx]["hits"].append(hit)

    bin_out = []
    cal_err_terms = []
    total = 0
    for b in buckets:
        n = len(b["preds"])
        total += n
        if n:
            mean_pred = sum(b["preds"]) / n
            hit_rate = sum(b["hits"]) / n
            cal_err_terms.append((n, abs(mean_pred - hit_rate)))
        else:
            mean_pred = hit_rate = None
        bin_out.append({
            "range": [round(b["lo"], 2), round(b["hi"], 2)],
            "count": n,
            "mean_predicted": round(mean_pred, 4) if mean_pred is not None else None,
            "observed_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        })
    # expected calibration error (count-weighted)
    cal_err: Optional[float] = None
    if total:
        cal_err = round(sum(w * e for w, e in cal_err_terms) / total, 4)
    return {
        "n": len(items),
        "mean_brier": round(sum(briers) / len(briers), 4) if briers else None,
        "calibration_error": cal_err,
        "bins": bin_out,
    }
