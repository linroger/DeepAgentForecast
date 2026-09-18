#!/usr/bin/env python3
"""Measure synthetic usage-ledger overhead without touching runtime data.

Example, from the repository root:
    python backend/scripts/benchmark_usage_ledger.py --operations 500 \
        --output docs/research/astra-usage-benchmark.json

Every database lives in a fresh TemporaryDirectory and is removed afterward.
The result measures local instrumentation overhead, not production improvement.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import platform
import sqlite3
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


def positive_integer(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def timing_summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "median_ms": round(median(samples), 6),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 6),
    }


def load_ledger_class():
    # Import the pure stdlib module directly, avoiding Flask app initialization,
    # Config/.env loading, logging setup and any provider dependency imports.
    source = Path(__file__).resolve().parents[1] / "app" / "utils" / "usage_ledger.py"
    spec = importlib.util.spec_from_file_location("astra_benchmark_usage_ledger", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the repository usage-ledger module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UsageLedger


def run_benchmark(operations: int, reads: int) -> dict:
    ledger_class = load_ledger_class()
    run_id = "astra-synthetic-benchmark"
    metadata = {
        "stage": "report", "provider": "synthetic", "model": "fixture",
        "usage_class": "known", "billing_basis": "synthetic_unpriced",
        "cost_estimated": True, "fallback": False, "cache_partition_known": False,
    }
    counters = {
        "calls": 1, "prompt_tokens": 1000, "completion_tokens": 100,
        "latency_ms": 1.0, "cost_usd": 0.0,
    }
    writes: list[float] = []
    duplicates: list[float] = []
    snapshots: list[float] = []
    guards: list[float] = []
    with TemporaryDirectory(prefix="astra-usage-benchmark-") as directory:
        database = Path(directory) / "usage.sqlite3"
        ledger = ledger_class(str(database))
        ledger.initialize(run_id)

        def record(index: int):
            return ledger.record_snapshot(
                run_id=run_id, attempt_id="synthetic-attempt", source="fixture",
                operation_id=str(index), metadata=metadata, counters=counters,
            )

        for index in range(operations):
            started = perf_counter()
            delta = record(index)
            writes.append((perf_counter() - started) * 1000)
            if delta["calls"] != 1 or delta["total_tokens"] != 1100:
                raise RuntimeError("Fresh operation did not add its exact synthetic usage")

        for index in range(operations):
            started = perf_counter()
            delta = record(index)
            duplicates.append((perf_counter() - started) * 1000)
            if any(delta.values()):
                raise RuntimeError("An unchanged operation added usage on replay")

        expected = {"calls": operations, "prompt_tokens": operations * 1000,
                    "completion_tokens": operations * 100, "total_tokens": operations * 1100}
        for _ in range(reads):
            started = perf_counter()
            snapshot = ledger.snapshot(run_id)
            snapshots.append((perf_counter() - started) * 1000)
            if any(snapshot["total"][key] != value for key, value in expected.items()):
                raise RuntimeError("Repeated projection changed exact usage arithmetic")
            started = perf_counter()
            ledger.assert_available(run_id)
            guards.append((perf_counter() - started) * 1000)
        database_bytes = database.stat().st_size

    return {
        "schema_version": "astra-usage-benchmark/v1",
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "measurement": "synthetic_local_instrumentation_overhead",
        "production_gain_measured": False,
        "limitations": [
            "Local single-writer run; does not estimate provider latency or production savings.",
            "Timing depends on machine, filesystem and OS caches; power-loss durability is not tested.",
            "Only the database is temporary; an explicitly requested JSON report is retained.",
        ],
        "environment": {
            "python": platform.python_version(), "python_implementation": platform.python_implementation(),
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
            "machine": platform.machine(), "synchronous": "FULL",
        },
        "inputs": {"operations": operations, "replayed_operations": operations,
                   "snapshot_reads": reads, "guard_checks": reads,
                   "synthetic_usage_per_operation": counters},
        "timings": {
            "operation_write": timing_summary(writes),
            "identical_snapshot_replay": timing_summary(duplicates),
            "aggregate_snapshot_read": timing_summary(snapshots),
            "availability_guard": timing_summary(guards),
        },
        "verification": {"passed": True, "exact_totals": expected,
                         "duplicate_added_tokens": 0, "repeated_snapshots_unchanged": True,
                         "temporary_database_removed": not database.exists()},
        "temporary_database_bytes": database_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=positive_integer, default=500)
    parser.add_argument("--reads", type=positive_integer, default=30)
    parser.add_argument("--output", type=Path, help="Optional JSON report path; runtime data is never opened")
    arguments = parser.parse_args()
    report = run_benchmark(arguments.operations, arguments.reads)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
