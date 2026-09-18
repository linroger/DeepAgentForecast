#!/usr/bin/env python3
"""Compare pinned/current budget guards over temporary synthetic ledger histories.

Only check_budget from the pinned revision is loaded; both guards use the same
current meter and durable ledger. Fixture population/fsync are outside timing.
No provider is constructed, and saved runtime artifacts are never modified.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.config import Config  # noqa: E402
from app.utils import telemetry as tel, usage_ledger as ledger_module  # noqa: E402


META = {"provider": "offline", "usage_class": "known", "billing_basis": "api",
        "cost_estimated": True, "fallback": False, "cache_partition_known": False}


def populate(path: Path, rows: int) -> ledger_module.UsageLedger:
    ledger = ledger_module.UsageLedger(str(path))
    ledger.initialize("fixture-run")
    counter = ledger_module.empty_counter()
    counter.update(calls=1, prompt_tokens=100, completion_tokens=50,
                   total_tokens=150, cost_usd=0.000173)
    # Every fixture operation has one settled delta; this is an explicit scale
    # assumption, not a reconstruction of saved aggregate call counters.
    with sqlite3.connect(path) as conn:
        for index in range(rows):
            meta = {**META, "stage": ("research", "graph", "run", "report")[index % 4],
                    "model": f"fixture-model-{index % 20}"}
            operation = f"fixture-{index}"
            conn.execute("INSERT INTO usage_operations VALUES (?,?,?,?,?,?,?,?)",
                         ("fixture-run", "llm_api_attempt", operation, f"owner-{index % 5}",
                          json.dumps(meta), json.dumps(counter), "completed", 1))
            ledger._insert_delta(conn, "fixture-run", f"owner-{index % 5}", "llm_api_attempt",
                                 operation, meta, counter)
    return ledger


def observe(before, path: Path, rows: int, samples: int) -> dict:
    ledger = populate(path, rows)
    run = "fixture-run"
    tel.LLMMeter.attach_durable_run(run, str(path), "current", existing_only=True)
    previous = tel.get_run_context()
    tel.set_run_context(run, "run")
    try:
        expected = ledger.snapshot(run)["total"]
        assert expected["total_tokens"] == 150 * rows
        assert ledger.budget_totals(run, include_cost=False) == {"total_tokens": expected["total_tokens"], "cost_usd": None}
        assert ledger.budget_totals(run, include_cost=True) == {key: expected[key] for key in ("total_tokens", "cost_usd")}
        result = {}
        for mode in ("tokens", "cost", "both"):
            timings = {"before": [], "after": []}
            with patch.object(Config, "LLM_RUN_BUDGET_TOKENS", expected["total_tokens"] + 1 if mode != "cost" else 0), \
                    patch.object(Config, "LLM_RUN_BUDGET_USD", expected["cost_usd"] + 1 if mode != "tokens" else 0):
                # Warm both guards, then alternate execution order to reduce
                # cache/order bias. Neither path writes the ledger.
                before(run)
                tel.check_budget(run)
                for sample in range(samples):
                    order = (("before", before), ("after", tel.check_budget))
                    if sample % 2:
                        order = tuple(reversed(order))
                    for label, guard in order:
                        start = time.perf_counter()
                        guard(run)
                        timings[label].append((time.perf_counter() - start) * 1000)
            result[mode] = {label: {"samples": len(values), "median_check_ms": round(statistics.median(values), 4)}
                            for label, values in timings.items()}
            old = result[mode]["before"]["median_check_ms"]
            new = result[mode]["after"]["median_check_ms"]
            result[mode]["reduction_percent"] = round(100 * (old - new) / old, 2)
        return {"delta_rows": rows, "operation_rows": rows, "recorded_tokens": expected["total_tokens"],
                "recorded_cost_usd": expected["cost_usd"], "guard_modes": result}
    finally:
        tel.LLMMeter.reset(run)
        tel.set_run_context(*previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="1c87a29")
    parser.add_argument("--rows", type=int, nargs="+", default=[100, 1000, 10000, 100000])
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000 or any(not 1 <= rows <= 1000000 for rows in args.rows):
        parser.error("samples must be1–1000 and each row count1–1000000")
    revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], cwd=BACKEND, text=True).strip()
    source = subprocess.check_output(["git", "show", revision + ":backend/app/utils/telemetry.py"], cwd=BACKEND, text=True)
    function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "check_budget")
    namespace = {**vars(tel), "__name__": "app.utils._astra_baseline_budget"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<local-git-budget-guard>", "exec"), namespace)
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with tempfile.TemporaryDirectory(prefix="astra-budget-comparison-") as temporary:
            cases = []
            for rows in args.rows:
                case = observe(namespace["check_budget"], Path(temporary) / f"{rows}.sqlite3", rows, args.samples)
                cases.append(case)
                print(json.dumps({"rows": rows, "guard_modes": case["guard_modes"]}), flush=True)
    finally:
        logging.disable(disabled)
    receipt = {"schema": "astra-budget-comparison/v1", "baseline_commit": revision,
               "baseline_telemetry_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "current_source_sha256": {str(path.relative_to(BACKEND.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in (Path(tel.__file__), Path(ledger_module.__file__), Path(__file__))},
               "method": "Pinned check_budget function and current check_budget share current meter/full snapshot and same fixture ledger; warm-up then alternating order; no writes in timed intervals.",
               "provider_requests": 0, "network_requests": 0, "cases": cases,
               "limits": ["Budget limits default to zero; the unchanged disabled path gets no claimed performance benefit.",
                          "Saved aggregate call counts do not establish physical ledger sizes; 100 is observed-scale adjacency, 1000 is sensitivity, and10000/100000 are scaling stress.",
                          "Read latency is local synthetic performance, not production or whole-workflow speedup.",
                          "Both narrow projections remain linear in the run's delta history; cost grouping is retained for rounding compatibility.",
                          "No schema/index/write-path change, reservations or complete invoice bound is included."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
