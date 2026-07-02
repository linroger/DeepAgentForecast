"""NEXTSTEPS P2-4: a forecast ledger that finally closes the calibration loop.

A forecaster that never learns whether its 70%s happen 70% of the time is
calibration-*capable*, not calibrated. Every ``forecast.json`` is appended here keyed
by horizon/resolution date; once outcomes are resolved (via the ``/api/v1/resolve``
endpoint or the ``forecast_tools backtest`` CLI), ``backtest.calibration_report`` over
the ledger yields historical Brier / calibration-error — which is surfaced into NEW
forecasts' ``confidence_rationale`` so confidence becomes *earned*, not self-asserted.

jsonl append/read (+ atomic rewrite for resolution) → pure enough to unit-test offline.
``scripts/scheduled_rerun.py`` can use ``due_for_resolution`` to detect forecasts whose
horizon/indicator dates have passed and queue them.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


def ledger_dir() -> str:
    """Resolve the ledger directory (FORECAST_LEDGER_DIR, else under PIPELINE_DATA_DIR)."""
    try:
        from ..config import Config
        d = getattr(Config, "FORECAST_LEDGER_DIR", "") or os.path.join(
            getattr(Config, "PIPELINE_DATA_DIR", "uploads/pipelines"), "_forecast_ledger")
    except Exception:  # noqa: BLE001
        d = os.path.join("uploads/pipelines", "_forecast_ledger")
    return d


def _ledger_file(d: Optional[str] = None) -> str:
    return os.path.join(d or ledger_dir(), "ledger.jsonl")


def append_forecast(forecast: Optional[Dict[str, Any]], *, report_id: str,
                    horizon: Optional[str] = None, resolution_date: Optional[str] = None,
                    created_at: Optional[str] = None, d: Optional[str] = None,
                    objective_signals: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Append one forecast entry to the ledger (jsonl). Best-effort → None on failure.

    Stores only what scoring needs (scenario names + probabilities) + keys for the
    resolution scheduler. ``resolution_date`` defaults to the horizon (year-end).

    EVAL-1 / GAP-4: an optional ``objective_signals`` dict (free, deterministic report
    quality metrics — citation coverage, sharp-criteria ratio, etc.) is persisted
    alongside the entry when supplied, so the ledger doubles as an evaluation log even
    before outcomes resolve. Default None → entry shape unchanged (degrade-safe).
    """
    if not isinstance(forecast, dict):
        return None
    scenarios = [
        {"name": s.get("name"), "probability": s.get("probability"),
         "resolution_criteria": s.get("resolution_criteria")}
        for s in (forecast.get("scenarios") or []) if isinstance(s, dict)
    ]
    if not scenarios:
        return None
    hz = horizon or str(forecast.get("horizon") or "").strip() or None
    entry = {
        "report_id": report_id,
        "horizon": hz,
        "resolution_date": resolution_date or _year_end(hz),
        "created_at": created_at,
        "scenarios": scenarios,
        "confidence": forecast.get("confidence"),
        "resolved": False,
        "outcome": None,
        "schema_version": 1,
    }
    if isinstance(objective_signals, dict) and objective_signals:
        entry["objective_signals"] = objective_signals
    try:
        target = _ledger_file(d)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except OSError:
        return None


def _year_end(horizon: Optional[str]) -> Optional[str]:
    """A free-text horizon like '2030' → '2030-12-31' (resolution date proxy)."""
    if not horizon:
        return None
    import re
    # 数字边界（非 \b：\b 在中日韩字符旁不触发，故"到2027年底"取不到年份）。
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", str(horizon))
    return f"{m.group(1)}-12-31" if m else None


def read_ledger(d: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read all ledger entries (tolerates corrupt/half-written tail lines)."""
    out: List[Dict[str, Any]] = []
    path = _ledger_file(d)
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return out
    return out


def calibration_summary(d: Optional[str] = None, entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Historical calibration over RESOLVED ledger entries (Brier / ECE / count).

    Each resolved entry carries ``outcome`` = the scenario name that actually occurred.
    Returns ``{n_resolved, mean_brier, calibration_error}`` (Nones when nothing resolved).
    """
    led = entries if entries is not None else read_ledger(d)
    resolved = [
        {"forecast": {"scenarios": e.get("scenarios")}, "outcome": e.get("outcome")}
        for e in led
        if e.get("resolved") and e.get("outcome") and e.get("scenarios")
    ]
    if not resolved:
        return {"n_resolved": 0, "mean_brier": None, "calibration_error": None}
    try:
        from .backtest import calibration_report
        rep = calibration_report(resolved)
        return {"n_resolved": len(resolved),
                "mean_brier": rep.get("mean_brier"),
                "calibration_error": rep.get("calibration_error")}
    except Exception:  # noqa: BLE001
        return {"n_resolved": len(resolved), "mean_brier": None, "calibration_error": None}


def due_for_resolution(as_of: str, d: Optional[str] = None) -> List[Dict[str, Any]]:
    """Unresolved entries whose resolution_date has passed (≤ as_of). For the scheduler."""
    led = read_ledger(d)
    out = []
    for e in led:
        if e.get("resolved"):
            continue
        rd = e.get("resolution_date")
        if rd and str(rd) <= str(as_of):
            out.append(e)
    return out


def recalibration_param(d: Optional[str] = None,
                        entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """R2-CAL-5: fit the 1-param logit-scale recalibrator over RESOLVED ledger entries.

    Returns ``{slope, n, fitted, enabled}``. ``enabled`` mirrors the
    ``REPORT_RECALIBRATE_FROM_LEDGER`` flag (default OFF) so a caller can fit the
    parameter for inspection yet only APPLY it once explicitly enabled AND enough
    labels exist (``fitted``). Identity slope (1.0) when data is thin → applying it is
    a no-op (degrade-safe; deferred until labels accrue).
    """
    led = entries if entries is not None else read_ledger(d)
    resolved = [
        {"forecast": {"scenarios": e.get("scenarios")}, "outcome": e.get("outcome")}
        for e in led
        if e.get("resolved") and e.get("outcome") and e.get("scenarios")
    ]
    enabled = False
    try:
        from ..config import Config
        enabled = bool(getattr(Config, "REPORT_RECALIBRATE_FROM_LEDGER", False))
    except Exception:  # noqa: BLE001
        enabled = False
    if not resolved:
        return {"slope": 1.0, "n": 0, "fitted": False, "enabled": enabled}
    try:
        from .backtest import fit_recalibrator
        fit = fit_recalibrator(resolved)
        fit["enabled"] = enabled
        return fit
    except Exception:  # noqa: BLE001
        return {"slope": 1.0, "n": len(resolved), "fitted": False, "enabled": enabled}
