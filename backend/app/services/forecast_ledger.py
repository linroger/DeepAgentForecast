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


def append_golden_result(*, question_id: str, probability: Any, resolved_outcome: bool,
                         question: Optional[str] = None, category: Optional[str] = None,
                         resolution_date: Optional[str] = None, as_of_date: Optional[str] = None,
                         resolution_criteria: Optional[str] = None,
                         report_id: Optional[str] = None, d: Optional[str] = None,
                         objective_signals: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """EVAL-1: append ONE already-resolved golden binary question to the ledger.

    黄金题是「已判定」的二元(YES/NO)问题；把它建模成两情景（YES=p、NO=1-p）的 *已解析*
    预测写入同一账本，于是既有校准机器（``calibration_summary`` → ``backtest.calibration_report``，
    以及 ``report_visualizer`` 渲染的校准曲线）会把黄金题结局与真实流水线预测一并累积——
    这正是把「预测好不好」变成可累加数字所需的闭环。

    与 ``append_forecast`` 的差异（故意各自独立，绝不改后者契约）：本函数写入 ``resolved=True``
    且直接带 ``outcome``（'YES' 或 'NO'），因为黄金题的真实结局本就已知。``probability`` 为
    模型给出的 YES 概率 [0,1]；非法概率钳到 [0,1]（缺失/无法解析 → None → 跳过，绝不污染账本）。
    Best-effort → 失败或输入非法时返回 None（degrade-safe）。

    ``golden=True`` + ``question_id`` 提供溯源，便于日后从账本里挑出/剔除黄金题条目。
    """
    try:
        p = float(probability)
    except (TypeError, ValueError):
        return None
    if p != p or p in (float("inf"), float("-inf")):  # NaN/inf → 拒收
        return None
    p = max(0.0, min(1.0, p))
    qid = str(question_id or "").strip()
    if not qid:
        return None
    outcome = "YES" if resolved_outcome else "NO"
    scenarios = [
        {"name": "YES", "probability": round(p, 4), "resolution_criteria": str(resolution_criteria or "")},
        {"name": "NO", "probability": round(1.0 - p, 4), "resolution_criteria": str(resolution_criteria or "")},
    ]
    entry = {
        "report_id": report_id or f"golden:{qid}",
        "horizon": None,
        "resolution_date": resolution_date,
        "created_at": as_of_date,
        "scenarios": scenarios,
        "confidence": None,
        "resolved": True,
        "outcome": outcome,
        "schema_version": 1,
        # 溯源：把黄金题条目标记出来，供筛选/剔除；不参与评分逻辑。
        "golden": True,
        "question_id": qid,
        "category": category,
        "question": question,
        "as_of_date": as_of_date,
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


# ---------------------------------------------------------------------------
# MON-1: 市场判定账本（resolutions.jsonl）——锚定市场一旦 resolve，模型概率就有了真值标签
# ---------------------------------------------------------------------------
# 与 scenario 预测的 ledger.jsonl 正交、同目录：这里记「二元预测 × 锚定市场」的判定结果，
# 每条形如 {report_id, forecast_id, market_id, resolved_outcome, model_p,
# market_p_at_research, brier_contribution, resolved_at, ...}。resolution_monitor.py 追加、
# 幂等（(report_id, forecast_id, market_id) 唯一），并据此算持续 Brier。


def _resolutions_file(d: Optional[str] = None) -> str:
    return os.path.join(d or ledger_dir(), "resolutions.jsonl")


def read_market_resolutions(d: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取全部市场判定记录（容忍损坏/半写的尾行；文件缺失 → []）。"""
    out: List[Dict[str, Any]] = []
    path = _resolutions_file(d)
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


def _resolution_key(report_id: Any, forecast_id: Any, market_id: Any) -> str:
    """判定记录的幂等键：同一 (报告, 预测, 市场) 只记一次，重跑不重复入账。"""
    return f"{str(report_id or '')}\x1f{str(forecast_id or '')}\x1f{str(market_id or '')}"


def append_market_resolution(*, report_id: str, forecast_id: str, market_id: str,
                             resolved_outcome: Optional[str], model_p: Optional[float],
                             market_p_at_research: Optional[float],
                             brier_contribution: Optional[float], resolved_at: str,
                             resolved_yes_price: Optional[float] = None,
                             d: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """追加一条市场判定记录（jsonl）；**幂等**——同一 (report_id, forecast_id, market_id)
    已在账本里则跳过并返回 None，故 resolution_monitor 反复重跑不会重复入账、不污染 Brier。

    Best-effort：缺 report_id/forecast_id/market_id → None；落盘失败 → None（degrade-safe）。
    返回新写入的 entry；重复/失败 → None。
    """
    rid = str(report_id or "").strip()
    fid = str(forecast_id or "").strip()
    mid = str(market_id or "").strip()
    if not rid or not fid or not mid:
        return None
    # 幂等门：先读现有键集，命中即跳过（同目录小文件，成本可忽略）。
    key = _resolution_key(rid, fid, mid)
    existing = read_market_resolutions(d)
    seen = {_resolution_key(e.get("report_id"), e.get("forecast_id"), e.get("market_id"))
            for e in existing}
    if key in seen:
        return None
    entry: Dict[str, Any] = {
        "report_id": rid,
        "forecast_id": fid,
        "market_id": mid,
        "resolved_outcome": resolved_outcome,
        "resolved_yes_price": resolved_yes_price,
        "model_p": model_p,
        "market_p_at_research": market_p_at_research,
        "brier_contribution": brier_contribution,
        "resolved_at": resolved_at,
        "schema_version": 1,
    }
    try:
        target = _resolutions_file(d)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except OSError:
        return None


def market_brier_summary(d: Optional[str] = None,
                         entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """MON-1：对已入账的市场判定记录汇总持续 Brier（模型概率 vs 已判定真值）。

    每条记录的 ``brier_contribution`` = (model_p − y)²，y∈{0,1} 取自市场终态。返回
    ``{n_resolved, mean_brier}``（无记录时 mean_brier=None）。纯读取、无 LLM/无网络。
    """
    recs = entries if entries is not None else read_market_resolutions(d)
    briers = [e.get("brier_contribution") for e in recs
              if isinstance(e.get("brier_contribution"), (int, float))]
    if not briers:
        return {"n_resolved": len(recs), "mean_brier": None}
    return {"n_resolved": len(recs),
            "mean_brier": round(sum(briers) / len(briers), 4)}
