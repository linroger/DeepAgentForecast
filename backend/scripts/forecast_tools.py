#!/usr/bin/env python3
"""CLI for ensemble aggregation + backtest/calibration over structured forecasts
(EXECPLAN2 I-9-0/I-2-4/I-3-3/I-9-2).

Operates on the forecast.json files emitted by report generation when
REPORT_STRUCTURED_FORECAST=true. The N-seed *runs* themselves are produced by
re-running the pipeline (the existing PREPARE-fork) with different SIM_SEED; this
tool aggregates/scores their forecasts.

Examples:
    # aggregate several runs' forecasts into one probabilistic ensemble forecast
    python backend/scripts/forecast_tools.py ensemble run1/forecast.json run2/forecast.json ... -o ensemble.json

    # score resolved forecasts (a JSON list of {"forecast": {...}, "outcome": "scenario name"})
    python backend/scripts/forecast_tools.py backtest resolved.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ensemble import aggregate_forecasts  # noqa: E402
from app.services.backtest import calibration_report, score_forecast  # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_ensemble(args) -> int:
    forecasts = [_load(p) for p in args.files]
    agg = aggregate_forecasts(forecasts)
    out = json.dumps(agg, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {args.out} ({agg['n_runs']} runs, {len(agg['scenarios'])} scenarios, agreement={agg['agreement']})")
    else:
        print(out)
    return 0


def cmd_backtest(args) -> int:
    resolved = _load(args.resolved)
    if not isinstance(resolved, list):
        print("resolved file must be a JSON list of {forecast, outcome}", file=sys.stderr)
        return 2
    rep = calibration_report(resolved, bins=args.bins)
    rep["per_forecast"] = [score_forecast(r.get("forecast", {}), r.get("outcome", "")) for r in resolved]
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ensemble + backtest for structured forecasts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensemble", help="aggregate N forecast.json into one probabilistic forecast")
    e.add_argument("files", nargs="+")
    e.add_argument("-o", "--out", default=None)
    e.set_defaults(func=cmd_ensemble)
    b = sub.add_parser("backtest", help="score + calibrate resolved forecasts")
    b.add_argument("resolved")
    b.add_argument("--bins", type=int, default=5)
    b.set_defaults(func=cmd_backtest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
