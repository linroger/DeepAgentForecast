#!/usr/bin/env python3
"""Golden-question forecast-quality evaluation harness (EVAL-1).

WHY THIS EXISTS — full pytest can go green while the pipeline quietly produces
WORSE forecasts (vaguer, over-hedged, mis-calibrated). Unit tests check code
shape; they cannot tell you whether a 70% actually happens ~70% of the time.
This harness turns forecast quality into a NUMBER against ground truth: it scores
the pipeline's binary_forecasts against a curated set of REAL, already-resolved
2024-2026 yes/no questions (``backend/tests/eval/golden_questions.json``) and
emits Brier score, logarithmic score, calibration bins + ECE, resolution
accuracy, and per-category / per-difficulty breakdowns — so you can compare
``eval_report.json`` across code versions and SEE whether a change helped.

Relation to ``eval_forecast_quality.py`` (I-7-7): that harness is an LLM-JUDGE
rubric scorer for report *prose* (groundedness/coverage/…). THIS harness is a
purely-deterministic, offline OUTCOME scorer — no LLM, no network — that grades
probabilities against known truth. They are complementary, not duplicative.

INTENDED WORKFLOW (the whole point — read this before using):
  1. For each golden question, build a research brief and run the pipeline with an
     AS-OF constraint at/near the question's ``as_of_date`` (the knowledge cutoff a
     fair forecaster should be scored at — do NOT let it peek past the outcome).
  2. Set each produced binary forecast's ``id`` to the golden question's ``id``
     (matching is by id), or curate the golden ``id``s to match your forecast ids.
  3. Score the pipeline's ``forecast.json`` against the golden set:
         python backend/scripts/golden_eval.py score-forecast-file \
             --forecast backend/uploads/pipelines/<pid>/report/forecast.json \
             -o eval_report.json --markdown eval_report.md
  4. Commit ``eval_report.json`` and DIFF it across code versions — falling Brier /
     ECE and rising resolution-accuracy = the change made forecasts better.

  Optional — accumulate golden outcomes into the calibration ledger so
  ``report_visualizer`` calibration curves grow over time (opt-in; default OFF so
  the read-only scorer never pollutes the production ledger):
         GOLDEN_EVAL_LEDGER=true python backend/scripts/golden_eval.py \
             score-forecast-file --forecast <...>/forecast.json --to-ledger

  Score everything already recorded in the forecast ledger (real pipeline
  resolutions + any golden outcomes appended above):
         python backend/scripts/golden_eval.py score-ledger -o ledger_eval.json

This script NEVER runs the pipeline itself — it only scores its outputs.

The pure scoring core (brier / log-score / ECE bins / matching / breakdowns) is
side-effect-free and offline-unit-tested (backend/tests/test_golden_eval.py).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL_DIR = os.path.join(REPO_ROOT, "backend", "tests", "eval")
GOLDEN_PATH = os.path.join(EVAL_DIR, "golden_questions.json")

DEFAULT_BINS = 10          # calibration bins over [0,1]
_EPS = 1e-9                # log-score clamp so ln() never blows up on p∈{0,1}


# ============================================================ pure scoring core
# Everything in this block is deterministic and side-effect-free (no LLM, no
# network, no disk) so it can be unit-tested offline.

def _coerce_prob(v: Any) -> Optional[float]:
    """Coerce a value to a probability in [0,1]; None on garbage/NaN/inf/out-of-range."""
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    if p != p or p in (float("inf"), float("-inf")):
        return None
    if p < 0.0 or p > 1.0:
        return None
    return p


def binary_brier(p: float, outcome: bool) -> float:
    """Binary Brier score for a single YES-probability vs a boolean outcome.

    (p − y)² where y=1 if the event happened (YES) else 0. Range [0,1], LOWER=better.
    """
    y = 1.0 if outcome else 0.0
    return (float(p) - y) ** 2


def binary_log_score(p: float, outcome: bool, eps: float = _EPS) -> float:
    """Logarithmic score = ln(probability assigned to the REALIZED outcome).

    ln(p) if YES happened else ln(1−p). Range (−∞, 0], HIGHER (closer to 0)=better.
    p is clamped to [eps, 1−eps] so a confidently-wrong 0/1 forecast gets a large
    finite penalty instead of −∞.
    """
    p = min(1.0 - eps, max(eps, float(p)))
    po = p if outcome else (1.0 - p)
    return math.log(po)


def calibration_bins(pairs: List[Tuple[float, bool]], bins: int = DEFAULT_BINS) -> Dict[str, Any]:
    """Reliability-diagram bins + Expected Calibration Error over (p_yes, outcome) pairs.

    Each YES-probability is dropped into one of ``bins`` equal-width buckets on
    [0,1]; per bucket we compare the mean predicted probability to the observed
    YES frequency. ECE = Σ (n_k/N)·|mean_pred_k − observed_k| (count-weighted).
    Returns ``{ece, n, bins:[{range,count,mean_predicted,observed_frequency,gap}]}``.
    """
    bins = max(1, int(bins))
    buckets = [{"preds": [], "hits": []} for _ in range(bins)]
    for p, y in pairs:
        pp = _coerce_prob(p)
        if pp is None:
            continue
        idx = min(bins - 1, max(0, int(pp * bins)))
        buckets[idx]["preds"].append(pp)
        buckets[idx]["hits"].append(1.0 if y else 0.0)

    total = sum(len(b["preds"]) for b in buckets)
    bin_out: List[Dict[str, Any]] = []
    ece_terms: List[Tuple[int, float]] = []
    for i, b in enumerate(buckets):
        n = len(b["preds"])
        lo, hi = i / bins, (i + 1) / bins
        if n:
            mean_pred = sum(b["preds"]) / n
            observed = sum(b["hits"]) / n
            gap = abs(mean_pred - observed)
            ece_terms.append((n, gap))
        else:
            mean_pred = observed = gap = None
        bin_out.append({
            "range": [round(lo, 3), round(hi, 3)],
            "count": n,
            "mean_predicted": round(mean_pred, 4) if mean_pred is not None else None,
            "observed_frequency": round(observed, 4) if observed is not None else None,
            "gap": round(gap, 4) if gap is not None else None,
        })
    ece = round(sum(w * g for w, g in ece_terms) / total, 4) if total else None
    return {"ece": ece, "n": total, "bins": bin_out}


def score_pairs(scored: List[Dict[str, Any]], bins: int = DEFAULT_BINS,
                threshold: float = 0.5) -> Dict[str, Any]:
    """Aggregate metrics over matched, scored questions.

    ``scored`` items: ``{id, probability, outcome(bool), category, difficulty}``.
    Returns overall Brier / log-score / resolution-accuracy / ECE + calibration
    bins + per-category and per-difficulty breakdowns. A forecast is "resolution
    correct" when (probability >= threshold) equals the actual outcome.
    """
    if not scored:
        return {"n": 0, "mean_brier": None, "mean_log_score": None,
                "resolution_accuracy": None, "base_rate": None,
                "calibration": calibration_bins([], bins),
                "by_category": {}, "by_difficulty": {}}
    briers, logs, correct, hits = [], [], 0, 0
    pairs: List[Tuple[float, bool]] = []
    for s in scored:
        p, y = float(s["probability"]), bool(s["outcome"])
        briers.append(binary_brier(p, y))
        logs.append(binary_log_score(p, y))
        pred_yes = p >= threshold
        correct += 1 if pred_yes == y else 0
        hits += 1 if y else 0
        pairs.append((p, y))
    n = len(scored)
    out: Dict[str, Any] = {
        "n": n,
        "mean_brier": round(sum(briers) / n, 4),
        "mean_log_score": round(sum(logs) / n, 4),
        "resolution_accuracy": round(correct / n, 4),
        "base_rate": round(hits / n, 4),
        "calibration": calibration_bins(pairs, bins),
        "by_category": _breakdown(scored, "category", threshold),
        "by_difficulty": _breakdown(scored, "difficulty", threshold),
    }
    return out


def _breakdown(scored: List[Dict[str, Any]], field: str, threshold: float) -> Dict[str, Any]:
    """Per-group {n, mean_brier, resolution_accuracy, base_rate} keyed by ``field``."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for s in scored:
        key = str(s.get(field) or "unknown")
        groups.setdefault(key, []).append(s)
    out: Dict[str, Any] = {}
    for key in sorted(groups):
        rows = groups[key]
        n = len(rows)
        briers = [binary_brier(float(r["probability"]), bool(r["outcome"])) for r in rows]
        correct = sum(1 for r in rows if (float(r["probability"]) >= threshold) == bool(r["outcome"]))
        hits = sum(1 for r in rows if bool(r["outcome"]))
        out[key] = {
            "n": n,
            "mean_brier": round(sum(briers) / n, 4),
            "resolution_accuracy": round(correct / n, 4),
            "base_rate": round(hits / n, 4),
        }
    return out


def load_golden_set(path: str = GOLDEN_PATH) -> List[Dict[str, Any]]:
    """Load + validate the golden question set. Accepts either a top-level list or
    an object with a ``questions`` list. Each entry needs a non-empty ``id`` and a
    boolean ``resolved_outcome``; malformed entries raise ValueError (fail loud —
    a silently-dropped golden question would understate coverage)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"golden set {path} has no 'questions' list")
    for q in questions:
        if not isinstance(q, dict) or not str(q.get("id") or "").strip():
            raise ValueError(f"golden entry missing 'id': {q!r}")
        if not isinstance(q.get("resolved_outcome"), bool):
            raise ValueError(f"golden entry {q.get('id')!r} needs boolean 'resolved_outcome'")
    return questions


def index_golden(questions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index golden questions by (stripped) id. Later duplicates overwrite earlier."""
    return {str(q["id"]).strip(): q for q in questions}


def extract_binary_forecasts(forecast_obj: Any) -> List[Dict[str, Any]]:
    """Pull the binary_forecasts list out of a loaded forecast.json.

    Tolerates: the full forecast dict ({"binary_forecasts":[...]}), a bare list, or
    a wrapper with a nested "forecast". Non-dict rows are dropped.
    """
    if isinstance(forecast_obj, dict):
        items = forecast_obj.get("binary_forecasts")
        if items is None and isinstance(forecast_obj.get("forecast"), dict):
            items = forecast_obj["forecast"].get("binary_forecasts")
    elif isinstance(forecast_obj, list):
        items = forecast_obj
    else:
        items = None
    return [it for it in (items or []) if isinstance(it, dict)]


def match_forecasts(binary_forecasts: List[Dict[str, Any]],
                    golden_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Match forecast rows to golden questions by ``id``; score the intersection.

    Returns ``{matched:[…scored…], unmatched_forecast_ids, unmatched_golden_ids,
    invalid_probability_ids}``. A matched row with an unparseable/out-of-range
    probability is reported under ``invalid_probability_ids`` (never silently kept).
    """
    matched: List[Dict[str, Any]] = []
    matched_ids: set = set()
    invalid: List[str] = []
    for row in binary_forecasts:
        fid = str(row.get("id") or "").strip()
        if not fid or fid not in golden_index:
            continue
        g = golden_index[fid]
        p = _coerce_prob(row.get("probability"))
        if p is None:
            invalid.append(fid)
            continue
        matched_ids.add(fid)
        matched.append({
            "id": fid,
            "probability": p,
            "outcome": bool(g["resolved_outcome"]),
            "category": g.get("category"),
            "difficulty": g.get("difficulty"),
            "question": g.get("question"),
            "as_of_date": g.get("as_of_date"),
            "resolution_date": g.get("resolution_date"),
            "brier": round(binary_brier(p, bool(g["resolved_outcome"])), 4),
            "log_score": round(binary_log_score(p, bool(g["resolved_outcome"])), 4),
        })
    forecast_ids = {str(r.get("id") or "").strip() for r in binary_forecasts if str(r.get("id") or "").strip()}
    return {
        "matched": matched,
        "unmatched_forecast_ids": sorted(forecast_ids - matched_ids - set(invalid)),
        "unmatched_golden_ids": sorted(set(golden_index) - matched_ids),
        "invalid_probability_ids": sorted(invalid),
    }


# =============================================================== markdown render

def _fmt(v: Any) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def render_markdown(report: Dict[str, Any]) -> str:
    """Human-readable markdown summary of a score-forecast-file report."""
    m = report.get("metrics", {})
    lines: List[str] = ["# Golden-question forecast evaluation", ""]
    lines.append(f"- source: `{report.get('forecast_path', '')}`")
    lines.append(f"- golden: `{report.get('golden_path', '')}` "
                 f"({report.get('golden_count', 0)} questions)")
    lines.append(f"- matched / scored: **{m.get('n', 0)}**")
    um = report.get("unmatched_golden_ids") or []
    if um:
        lines.append(f"- unmatched golden ids ({len(um)}): {', '.join(um)}")
    inv = report.get("invalid_probability_ids") or []
    if inv:
        lines.append(f"- dropped (bad probability): {', '.join(inv)}")
    lines += ["", "## Overall", "", "| metric | value |", "|---|---|",
              f"| mean Brier (lower=better) | {_fmt(m.get('mean_brier'))} |",
              f"| mean log-score (higher=better) | {_fmt(m.get('mean_log_score'))} |",
              f"| resolution accuracy | {_fmt(m.get('resolution_accuracy'))} |",
              f"| ECE (lower=better) | {_fmt(m.get('calibration', {}).get('ece'))} |",
              f"| base rate (YES share) | {_fmt(m.get('base_rate'))} |", ""]

    def _table(title: str, bd: Dict[str, Any]) -> None:
        if not bd:
            return
        lines.append(f"## By {title}")
        lines.append("")
        lines.append("| " + title + " | n | mean Brier | accuracy | base rate |")
        lines.append("|---|---|---|---|---|")
        for key, v in bd.items():
            lines.append(f"| {key} | {v['n']} | {_fmt(v['mean_brier'])} | "
                         f"{_fmt(v['resolution_accuracy'])} | {_fmt(v['base_rate'])} |")
        lines.append("")

    _table("category", m.get("by_category", {}))
    _table("difficulty", m.get("by_difficulty", {}))

    cal = m.get("calibration", {})
    if cal.get("bins"):
        lines += ["## Calibration bins", "",
                  "| range | n | mean pred | observed | gap |", "|---|---|---|---|---|"]
        for b in cal["bins"]:
            if not b["count"]:
                continue
            rng = f"{b['range'][0]:.1f}–{b['range'][1]:.1f}"
            lines.append(f"| {rng} | {b['count']} | {_fmt(b['mean_predicted'])} | "
                         f"{_fmt(b['observed_frequency'])} | {_fmt(b['gap'])} |")
        lines.append("")

    rows = report.get("matched") or []
    if rows:
        lines += ["## Per-question", "",
                  "| id | category | p(YES) | outcome | Brier |", "|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['id']} | {r.get('category') or '—'} | {r['probability']:.2f} | "
                         f"{'YES' if r['outcome'] else 'NO'} | {_fmt(r.get('brier'))} |")
        lines.append("")
    return "\n".join(lines)


# =============================================================== ledger bridge

def _ledger_enabled(args) -> bool:
    """Appending golden outcomes to the calibration ledger is opt-in: requires
    Config.GOLDEN_EVAL_LEDGER or an explicit --to-ledger (mirrors the eval --live guard)."""
    try:
        from app.config import Config
        env_on = bool(getattr(Config, "GOLDEN_EVAL_LEDGER", False))
    except Exception:  # noqa: BLE001
        env_on = False
    return bool(env_on or getattr(args, "to_ledger", False))


def _append_matched_to_ledger(matched: List[Dict[str, Any]], ledger_dir: Optional[str]) -> int:
    """Append each matched (probability, known outcome) as a resolved golden binary
    forecast so the calibration curve accumulates. Returns count appended."""
    from app.services.forecast_ledger import append_golden_result
    appended = 0
    for r in matched:
        e = append_golden_result(
            question_id=r["id"], probability=r["probability"], resolved_outcome=r["outcome"],
            question=r.get("question"), category=r.get("category"),
            resolution_date=r.get("resolution_date"), as_of_date=r.get("as_of_date"),
            d=ledger_dir,
        )
        if e:
            appended += 1
    return appended


def _write_outputs(report: Dict[str, Any], out_path: Optional[str], md_path: Optional[str]) -> None:
    """Write eval_report.json (--out) and/or the readable markdown (--markdown)."""
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"wrote {out_path}")
    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(report))
        print(f"wrote {md_path}")
    if not out_path and not md_path:
        print(payload)


# =============================================================== CLI commands

def cmd_score_forecast_file(args) -> int:
    questions = load_golden_set(args.golden)
    gindex = index_golden(questions)
    with open(args.forecast, encoding="utf-8") as f:
        forecast_obj = json.load(f)
    binaries = extract_binary_forecasts(forecast_obj)
    match = match_forecasts(binaries, gindex)
    metrics = score_pairs(match["matched"], bins=args.bins)

    ledger_appended = 0
    if _ledger_enabled(args) and match["matched"]:
        ledger_appended = _append_matched_to_ledger(match["matched"], args.ledger_dir)

    report: Dict[str, Any] = {
        "mode": "score-forecast-file",
        "forecast_path": args.forecast,
        "golden_path": args.golden,
        "golden_count": len(questions),
        "metrics": metrics,
        "matched": match["matched"],
        "unmatched_forecast_ids": match["unmatched_forecast_ids"],
        "unmatched_golden_ids": match["unmatched_golden_ids"],
        "invalid_probability_ids": match["invalid_probability_ids"],
        "ledger_appended": ledger_appended,
    }
    _write_outputs(report, args.out, args.markdown)
    # Non-zero exit only when NOTHING matched — a signal the ids are misaligned, not
    # a quality gate (there is no committed golden baseline to gate against here).
    return 0 if match["matched"] else 3


def cmd_score_ledger(args) -> int:
    from app.services.forecast_ledger import calibration_summary, read_ledger
    from app.services.backtest import calibration_report
    entries = read_ledger(args.ledger_dir)
    resolved = [
        {"forecast": {"scenarios": e.get("scenarios")}, "outcome": e.get("outcome")}
        for e in entries
        if e.get("resolved") and e.get("outcome") and e.get("scenarios")
    ]
    cal = calibration_report(resolved, bins=args.bins) if resolved else {"n": 0}
    summary = calibration_summary(args.ledger_dir, entries=entries)

    # Golden-tagged entries carry a category → a per-category binary breakdown, using
    # the YES-scenario probability as the model's p and outcome=='YES' as the label.
    golden_scored: List[Dict[str, Any]] = []
    for e in entries:
        if not (e.get("golden") and e.get("resolved") and e.get("scenarios")):
            continue
        yes = next((s for s in e["scenarios"] if str(s.get("name")).upper() == "YES"), None)
        p = _coerce_prob(yes.get("probability")) if isinstance(yes, dict) else None
        if p is None:
            continue
        golden_scored.append({
            "id": e.get("question_id") or e.get("report_id"),
            "probability": p,
            "outcome": str(e.get("outcome")).upper() == "YES",
            "category": e.get("category"),
            "difficulty": None,
        })

    report: Dict[str, Any] = {
        "mode": "score-ledger",
        "ledger_dir": args.ledger_dir or "(default)",
        "n_entries": len(entries),
        "n_resolved": summary.get("n_resolved", 0),
        "mean_brier": summary.get("mean_brier"),
        "calibration_error": summary.get("calibration_error"),
        "calibration_report": cal,
        "golden": score_pairs(golden_scored, bins=args.bins) if golden_scored else {"n": 0},
    }
    _write_outputs(report, args.out, args.markdown)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="golden-question forecast-quality evaluation (deterministic, offline)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("score-forecast-file",
                       help="score a pipeline forecast.json's binary_forecasts vs the golden set")
    a.add_argument("--forecast", required=True, help="path to forecast.json (pipeline binary_forecasts schema)")
    a.add_argument("--golden", default=GOLDEN_PATH, help="golden question set JSON")
    a.add_argument("--bins", type=int, default=DEFAULT_BINS, help="calibration bins over [0,1]")
    a.add_argument("-o", "--out", default=None, help="write eval_report.json (default: stdout)")
    a.add_argument("--markdown", default=None, help="also write a readable markdown table")
    a.add_argument("--to-ledger", action="store_true",
                   help="append matched outcomes to the calibration ledger (opt-in; else needs GOLDEN_EVAL_LEDGER)")
    a.add_argument("--ledger-dir", default=None, help="override ledger dir (default: FORECAST_LEDGER_DIR)")
    a.set_defaults(func=cmd_score_forecast_file)

    b = sub.add_parser("score-ledger",
                       help="score every recorded resolution in the forecast ledger")
    b.add_argument("--ledger-dir", default=None, help="override ledger dir (default: FORECAST_LEDGER_DIR)")
    b.add_argument("--bins", type=int, default=DEFAULT_BINS)
    b.add_argument("-o", "--out", default=None, help="write report JSON (default: stdout)")
    b.add_argument("--markdown", default=None, help="also write a readable markdown table")
    b.set_defaults(func=cmd_score_ledger)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
