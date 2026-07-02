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


def _cfg(name: str, default: Any) -> Any:
    """Read a Config flag with a safe default (degrade-safe; never raises)."""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001
        return default


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

    # R2-CAL-14: Jeffreys (Beta(0.5,0.5)) smoothing for per-bin hit-rates + a credible
    # interval, so a 1/1 bin doesn't masquerade as perfectly calibrated.
    alpha = beta = 0.5
    bin_out = []
    cal_err_terms = []
    total = 0
    # accumulators for the Murphy Brier decomposition (R2-CAL-10)
    all_hits = 0.0
    all_n = 0
    rel_terms = 0.0   # sum n_k (p_k - o_k)^2  (reliability, lower=better)
    pooled_sq = 0.0   # sum (p_i - y_i)^2 over every per-scenario prediction
    for b in buckets:
        n = len(b["preds"])
        total += n
        if n:
            mean_pred = sum(b["preds"]) / n
            hits = sum(b["hits"])
            hit_rate = hits / n
            cal_err_terms.append((n, abs(mean_pred - hit_rate)))
            pooled_sq += sum((p - y) ** 2 for p, y in zip(b["preds"], b["hits"]))
            smoothed = (hits + alpha) / (n + alpha + beta)
            # normal-approx credible interval on the smoothed Beta posterior
            a_post, b_post = hits + alpha, (n - hits) + beta
            var = (a_post * b_post) / ((a_post + b_post) ** 2 * (a_post + b_post + 1))
            half = 1.96 * math.sqrt(var)
            ci_low = round(max(0.0, smoothed - half), 4)
            ci_high = round(min(1.0, smoothed + half), 4)
            all_hits += hits
            all_n += n
            rel_terms += n * (mean_pred - hit_rate) ** 2
        else:
            mean_pred = hit_rate = smoothed = ci_low = ci_high = None
        bin_out.append({
            "range": [round(b["lo"], 2), round(b["hi"], 2)],
            "count": n,
            "mean_predicted": round(mean_pred, 4) if mean_pred is not None else None,
            "observed_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "hit_rate_smoothed": round(smoothed, 4) if smoothed is not None else None,
            "ci_low": ci_low,
            "ci_high": ci_high,
        })
    # expected calibration error (count-weighted)
    cal_err: Optional[float] = None
    if total:
        cal_err = round(sum(w * e for w, e in cal_err_terms) / total, 4)

    # R2-CAL-10 — Murphy decomposition: Brier = Reliability − Resolution + Uncertainty.
    decomposition: Optional[Dict[str, float]] = None
    if all_n:
        obar = all_hits / all_n                                   # overall base rate
        reliability = rel_terms / all_n
        resolution = 0.0
        for b in buckets:
            nk = len(b["preds"])
            if nk:
                ok = sum(b["hits"]) / nk
                resolution += nk * (ok - obar) ** 2
        resolution /= all_n
        uncertainty = obar * (1.0 - obar)
        decomposition = {
            "reliability": round(reliability, 4),
            "resolution": round(resolution, 4),
            "uncertainty": round(uncertainty, 4),
            # per-prediction (reliability-diagram) Brier the 3 terms decompose:
            # brier_pooled ≈ reliability − resolution + uncertainty (binning residual aside).
            "brier_pooled": round(pooled_sq / all_n, 4),
        }

    # R2-CAL-14 — CAL_MIN_RESOLVED gate: flag (don't suppress) thin-evidence reports.
    min_resolved = int(_cfg("CAL_MIN_RESOLVED", 0) or 0)
    insufficient = bool(min_resolved and len(items) < min_resolved)

    return {
        "n": len(items),
        "mean_brier": round(sum(briers) / len(briers), 4) if briers else None,
        "calibration_error": cal_err,
        "brier_decomposition": decomposition,    # R2-CAL-10
        "resolution": decomposition.get("resolution") if decomposition else None,
        "insufficient_data": insufficient,       # R2-CAL-14 gate
        "bins": bin_out,
    }


def fit_recalibrator(resolved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """R2-CAL-5: fit a 1-parameter logit-scale recalibrator from resolved forecasts.

    Models observed hit ~ sigmoid(slope · logit(p_pred)); a slope < 1 means the
    forecaster is over-confident (probabilities should be pulled toward 0.5), > 1
    under-confident. Fitted by a tiny gradient descent on log-loss. Returns
    ``{slope, n, fitted}``; ``slope=1.0`` (identity) when there is too little data —
    so applying it is always a no-op until enough labels exist (degrade-safe).

    Application is deliberately left OFF by default (REPORT_RECALIBRATE_FROM_LEDGER);
    callers should only apply ``slope`` once ``n`` is meaningful.
    """
    pts: List = []  # (logit_p, y)
    eps = 1e-6
    for r in (resolved or []):
        if not isinstance(r, dict):
            continue
        fc, outcome = r.get("forecast"), r.get("outcome")
        if not fc or outcome is None:
            continue
        tgt = _norm_name(outcome)
        for s in (fc.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            p = min(1.0 - eps, max(eps, _scenario_prob(s)))
            y = 1.0 if _norm_name(s.get("name")) == tgt else 0.0
            pts.append((math.log(p / (1.0 - p)), y))
    if len(pts) < 10:
        return {"slope": 1.0, "n": len(pts), "fitted": False}
    slope = 1.0
    lr = 0.05
    for _ in range(500):
        grad = 0.0
        for x, y in pts:
            z = max(-50.0, min(50.0, slope * x))
            pred = 1.0 / (1.0 + math.exp(-z))
            grad += (pred - y) * x
        slope -= lr * grad / len(pts)
        slope = max(0.1, min(5.0, slope))
    return {"slope": round(slope, 4), "n": len(pts), "fitted": True}


def apply_recalibration(prob: float, slope: float, eps: float = 1e-6) -> float:
    """Apply a fitted logit-scale ``slope`` to a single probability (R2-CAL-5)."""
    try:
        p = min(1.0 - eps, max(eps, float(prob)))
        z = max(-50.0, min(50.0, float(slope) * math.log(p / (1.0 - p))))
        return 1.0 / (1.0 + math.exp(-z))
    except (TypeError, ValueError, ZeroDivisionError):
        return prob
