#!/usr/bin/env python3
"""Offline comparison of unresolved-operation visibility and indexed admission.

Only temporary fixture ledgers and the requested JSON output are written.
No provider is constructed. Timings describe local SQLite checks, not a pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import types

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.utils import usage_ledger as current  # noqa: E402


META = {"stage": "report", "provider": "offline", "model": "fixture",
        "usage_class": "known", "billing_basis": "unknown", "cost_estimated": True,
        "fallback": False, "cache_partition_known": False}


def _fixture(path, status, owner, rows):
    ledger = current.UsageLedger(str(path))
    ledger.initialize("fixture-run")
    # Bulk seeding models a completed history without timing setup/fsync calls.
    # Operations and matching deltas are installed in the same transaction.
    counter = current.empty_counter()
    counter.update(calls=1, prompt_tokens=10, completion_tokens=5, total_tokens=15)
    with sqlite3.connect(path) as conn:
        for i in range(rows):
            operation = f"completed-{i}"
            conn.execute("INSERT INTO usage_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         ("fixture-run", "llm_api_attempt", operation, "old-owner",
                          json.dumps(META), json.dumps(counter), "completed", 1))
            current.UsageLedger._insert_delta(
                conn, "fixture-run", "old-owner", "llm_api_attempt", operation, META, counter)
    if status:
        ledger.record_snapshot(
            run_id="fixture-run", attempt_id=owner, source="llm_api_attempt",
            operation_id="uncertain", metadata={**META, "usage_class": "unknown"},
            counters={}, status=status,
        )
    return ledger


def _observe(module, path, *, reference, samples):
    ledger = module.UsageLedger(str(path))
    kwargs = {"current_attempt_id": reference} if module is current else {}
    snapshot = ledger.snapshot("fixture-run", **kwargs)
    durations = []
    allowed = None
    error = None
    for _ in range(samples):
        started = time.perf_counter()
        try:
            ledger.assert_available("fixture-run", **kwargs)
            allowed, error = True, None
        except module.UsageLedgerStorageError as exc:
            allowed, error = False, type(exc).__name__
        durations.append((time.perf_counter() - started) * 1000)
    return {"admission_allowed": allowed, "error_type": error,
            "api_operation_state": snapshot.get("api_operation_state"),
            "recorded_calls": snapshot["total"]["calls"],
            "recorded_tokens": snapshot["total"]["total_tokens"],
            "median_admission_ms": round(statistics.median(durations), 4),
            "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="d09ee4f")
    parser.add_argument("--history-rows", type=int, default=10000)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.history_rows <= 100000 or not 1 <= args.samples <= 10000:
        parser.error("history-rows must be 1..100000 and samples must be 1..10000")
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], cwd=BACKEND, text=True).strip()
    source = subprocess.check_output(
        ["git", "show", revision + ":backend/app/utils/usage_ledger.py"], cwd=BACKEND, text=True)
    baseline = types.ModuleType("astra_baseline_usage_ledger")
    exec(compile(source, "<local-git-baseline>", "exec"), baseline.__dict__)
    with tempfile.TemporaryDirectory(prefix="astra-unresolved-comparison-") as temporary:
        directory = Path(temporary)
        cases = {}
        for name, status, owner in (
            ("healthy", None, "old-owner"),
            ("same_owner_in_flight", "in_flight", "current-owner"),
            ("other_owner_in_flight", "in_flight", "old-owner"),
            ("durable_accounting_error", "accounting_error", "old-owner"),
            ("unknown_transport", "unknown", "old-owner"),
        ):
            path = directory / (name + ".sqlite3")
            _fixture(path, status, owner, 0)
            cases[name] = {label: _observe(module, path, reference="current-owner", samples=args.samples)
                           for label, module in (("before", baseline), ("after", current))}
        path = directory / "large-history.sqlite3"
        _fixture(path, "in_flight", "old-owner", args.history_rows)
        scaling = {"completed_operations": args.history_rows,
                   "before": _observe(baseline, path, reference="current-owner", samples=args.samples),
                   "after": _observe(current, path, reference="current-owner", samples=args.samples)}
        with sqlite3.connect(path) as conn:
            scaling["query_plan"] = [list(row) for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT 1 FROM usage_operations "
                "WHERE run_id=? AND source='llm_api_attempt' AND status='in_flight' "
                "AND owner_attempt_id != ? LIMIT 1", ("fixture-run", "current-owner"))]
    receipt = {
        "schema": "astra-unresolved-comparison/v1", "baseline_commit": revision,
        "source_sha256": {"before": hashlib.sha256(source.encode()).hexdigest(),
                          "after": hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest()},
        "provider_requests": 0, "network_requests": 0, "cases": cases, "scaling": scaling,
        "limits": [
            "Temporary synthetic operation histories; no historical production crash frequency is inferred.",
            "Admission decisions are ledger checks, not provider dispatch or transactional token reservations.",
            "Both implementations read the same current-format fixture database, including its optional index.",
            "The accounting_error case tests durable policy; the baseline producer did not yet emit that distinct status.",
            "Timings characterize local read overhead only; no workflow speedup or spend bound is claimed.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
