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
from typing import Any, Dict, List, Optional


def _cfg(name: str, default: Any) -> Any:
    """Read a Config flag with a safe default (degrade-safe; never raises)."""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001
        return default


def _norm_name(name) -> str:
    """Loose scenario-name key so 'Samsung leads' ~ 'samsung  leads.'."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w一-鿿]+", " ", str(name or "").lower())).strip()


def _extremized_logodds(probs: List[float], a: float, eps: float = 1e-6) -> float:
    """R2-CAL-2: geometric (log-odds) pool of probabilities with extremizing factor a.

    pooled = sigmoid( a * mean(logit(p_i)) ). a=1 is plain geometric-odds pooling;
    a>1 sharpens toward 0/1 (counteracts the under-confidence of arithmetic averaging).
    """
    ls = []
    for p in probs:
        try:
            pf = float(p)
        except (TypeError, ValueError):
            continue
        pf = min(1.0 - eps, max(eps, pf))
        ls.append(math.log(pf / (1.0 - pf)))
    if not ls:
        return 0.0
    z = a * (sum(ls) / len(ls))
    z = max(-50.0, min(50.0, z))  # guard exp overflow
    return 1.0 / (1.0 + math.exp(-z))


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

    # R2-CAL-2：用 extremizing log-odds（几何）池化作为发布概率；算术均值保留为诊断。
    # ENSEMBLE_EXTREMIZE_A 未设置时 → 退回算术均值（默认路径逐字节一致，degrade-safe）。
    a_raw = _cfg("ENSEMBLE_EXTREMIZE_A", None)
    try:
        a = float(a_raw) if a_raw is not None else None
    except (TypeError, ValueError):
        a = None

    agg = []
    for b in buckets.values():
        probs = b["probs"] or [0.0]
        mean_p = _mean(probs)
        sd = _stdev(probs)
        row = {
            "name": b["name"],
            "mean_probability": round(mean_p, 4),       # arithmetic mean (diagnostic)
            "stdev": round(sd, 4),
            "min": round(min(probs), 4),
            "max": round(max(probs), 4),
            "support": len(b["probs"]),          # how many runs surfaced it
            "support_ratio": round(len(b["probs"]) / n, 3),
            "key_drivers": sorted(b["drivers"])[:8],
            "resolution_criteria": b["criteria"],
        }
        # pre-renormalization point estimate: extremized pool when a is set, else mean
        row["_point"] = _extremized_logodds(probs, a) if a is not None else mean_p
        agg.append(row)

    # renormalize the chosen point estimate to sum to 1
    total = sum(s["_point"] for s in agg)
    for s in agg:
        s["probability"] = round(s["_point"] / total, 4) if total > 0 else 0.0
        # R2-CAL-17：把跨 run 的离散度落成发布区间 [p_low, p_high]（以 stdev 为半宽）。
        s["p_low"] = round(max(0.0, s["probability"] - s["stdev"]), 4)
        s["p_high"] = round(min(1.0, s["probability"] + s["stdev"]), 4)
        s["pooling"] = "extremized_logodds" if a is not None else "arithmetic_mean"
        del s["_point"]
    agg.sort(key=lambda s: s["probability"], reverse=True)

    return {
        "n_runs": n,
        "scenarios": agg,
        "agreement": _ensemble_agreement(runs),       # R2-CAL-9: TV-distance + support
        "agreement_spread": round(max(0.0, 1.0 - (_mean([s["stdev"] for s in agg]) if agg else 0.0) * 2), 3),
        "extremize_a": a,
        "headline": runs[0].get("headline", ""),
        "horizon": runs[0].get("horizon", ""),
        "schema_version": 1,
    }


def pool_binary_forecasts(primary: List[Dict[str, Any]],
                          secondary: Dict[str, List[Dict[str, Any]]], *,
                          primary_model: str = "primary",
                          extremize_a: Optional[float] = None,
                          spread_threshold: float = 0.15) -> List[str]:
    """ITEM 12：多模型二元预测集成——把主模型与各副模型对同一条二元预测的概率池化（就地改写 primary）。

    ``primary`` 是权威二元预测列表（其陈述/id/判定标准为准）；``secondary`` 为 {模型名: 二元预测列表}
    （各副模型对同一研究档案独立抽取的一遍结果）。逐条 primary 预测，用「归一化陈述」优先、其次「id」
    与每个副模型的预测匹配（同源 F1-Fn 表，故 id/陈述应对齐）；把匹配到的各模型概率与主模型概率一起，
    用与种子集成同一套 extremizing log-odds 池化（``extremize_a`` 给出时；否则退回算术均值）得到发布概率，
    并把 ``{models, probs, pooled, spread}`` 记入 ``binary['ensemble']``、用 pooled 覆盖 ``binary['probability']``。
    ``spread``（各模型概率的样本 stdev）> ``spread_threshold`` 的预测其 id 收进返回列表（low-agreement）。
    某条 primary 无任何副模型匹配 → 保留主模型概率、不加 ensemble 块（unmatched→keep primary）。Pure。
    """
    prim = [b for b in (primary or []) if isinstance(b, dict)]
    if not prim or not secondary:
        return []
    # 复用 forecast_extractor 的陈述归一化键（惰性导入避免模块级循环依赖）。
    try:
        from .forecast_extractor import _binary_key as _bk
    except Exception:  # noqa: BLE001 — 退回等价内联实现
        def _bk(s: Any) -> str:
            return re.sub(r"\W+", " ", str(s or "").lower()).strip()

    # 为每个副模型建两张查找表：陈述键→概率、id→概率（各取首现，忽略非法概率）。
    indexed: Dict[str, Any] = {}
    for model, blist in (secondary or {}).items():
        by_key: Dict[str, float] = {}
        by_id: Dict[str, float] = {}
        for b in (blist or []):
            if not isinstance(b, dict):
                continue
            try:
                pf = float(b.get("probability"))
            except (TypeError, ValueError):
                continue
            k = _bk(b.get("statement"))
            if k and k not in by_key:
                by_key[k] = pf
            bid = str(b.get("id") or "").strip()
            if bid and bid not in by_id:
                by_id[bid] = pf
        indexed[str(model)] = (by_key, by_id)

    try:
        a = float(extremize_a) if extremize_a is not None else None
    except (TypeError, ValueError):
        a = None

    low_agreement: List[str] = []
    for b in prim:
        try:
            p0 = float(b.get("probability"))
        except (TypeError, ValueError):
            continue
        models = [str(primary_model)]
        probs = [p0]
        k = _bk(b.get("statement"))
        bid = str(b.get("id") or "").strip()
        for model, (by_key, by_id) in indexed.items():
            mp: Optional[float] = None
            if k and k in by_key:
                mp = by_key[k]
            elif bid and bid in by_id:
                mp = by_id[bid]
            if mp is not None:
                models.append(model)
                probs.append(mp)
        if len(probs) <= 1:
            continue  # 无副模型匹配 → 保留主模型概率（unmatched→keep primary）
        pooled = _extremized_logodds(probs, a) if a is not None else _mean(probs)
        spread = _stdev(probs)
        b["ensemble"] = {
            "models": models,
            "probs": [round(x, 4) for x in probs],
            "pooled": round(pooled, 4),
            "spread": round(spread, 4),
        }
        b["probability"] = round(pooled, 2)  # 与 _normalize_binaries 的 2 位约定一致
        if spread > spread_threshold:
            _bid = str(b.get("id") or "").strip()
            if _bid:
                low_agreement.append(_bid)
    return low_agreement


def _ensemble_agreement(runs: List[Dict[str, Any]]) -> Optional[float]:
    """R2-CAL-9: inter-run agreement = (1 - mean pairwise total-variation distance)
    between each run's scenario distribution, penalized by low scenario support.

    TV(p,q) = 0.5 * sum_i |p_i - q_i| over the union of scenario names (missing→0).
    Each run's distribution is renormalized first so unequal scenario sets compare
    fairly. The support penalty multiplies by the mean per-scenario presence ratio so
    runs that disagree on *which* scenarios exist cannot score a spuriously high
    agreement. Returns 1.0 for a single run (no disagreement), None when empty.
    """
    if not runs:
        return None
    dists: List[Dict[str, float]] = []
    for f in runs:
        d: Dict[str, float] = {}
        for s in (f.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            k = _norm_name(s.get("name"))
            if not k:
                continue
            try:
                d[k] = d.get(k, 0.0) + max(0.0, float(s.get("probability") or 0.0))
            except (TypeError, ValueError):
                continue
        tot = sum(d.values())
        if tot > 0:
            d = {k: v / tot for k, v in d.items()}
        if d:
            dists.append(d)
    if len(dists) < 2:
        return 1.0
    keys = set().union(*[set(d.keys()) for d in dists])
    tvs = []
    for i in range(len(dists)):
        for j in range(i + 1, len(dists)):
            tv = 0.5 * sum(abs(dists[i].get(k, 0.0) - dists[j].get(k, 0.0)) for k in keys)
            tvs.append(tv)
    agreement_tv = 1.0 - (sum(tvs) / len(tvs) if tvs else 0.0)
    # support penalty: mean fraction of runs in which each scenario appears
    presence = [sum(1 for d in dists if k in d) / len(dists) for k in keys]
    support_factor = sum(presence) / len(presence) if presence else 1.0
    return round(max(0.0, min(1.0, agreement_tv * support_factor)), 3)
