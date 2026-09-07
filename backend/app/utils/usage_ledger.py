"""Transactional cumulative usage observations, independent of process memory.

An operation owns a monotonically increasing high-water mark. Every accepted
positive delta is committed with that mark and attributed to the observing
attempt. Replaying an old operation on resume therefore does not move old spend
into the new attempt; growth discovered on resume belongs to the new attempt.
The ledger contains usage metadata only, never prompts, credentials or responses.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any


METRICS = (
    "calls", "cached", "prompt_tokens", "completion_tokens", "total_tokens",
    "latency_ms", "cost_usd", "cache_read_tokens", "cache_write_tokens",
    "uncached_tokens",
)
_FLOAT_METRICS = {"latency_ms", "cost_usd"}
_USAGE_CLASSES = {"known", "estimated", "unknown", "mixed"}


class UsageLedgerStorageError(RuntimeError):
    """Durable accounting is unavailable; callers must not assume zero spend."""


class UsageLedgerUnresolvedError(UsageLedgerStorageError):
    """Recorded API uncertainty blocks work until evidence settles the operation."""


class UsageLedgerConflict(ValueError):
    """An operation identity was reused with different immutable attribution."""


def empty_counter() -> dict[str, Any]:
    return {key: 0.0 if key in _FLOAT_METRICS else 0 for key in METRICS}


def _label(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{name} must be a nonempty string of at most 512 characters")
    return value


def _number(value: Any, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a nonnegative finite number")
    if not math.isfinite(value) or value < 0 or value > 2**53:
        raise ValueError(f"{name} must be a nonnegative finite number below 2**53")
    if name not in _FLOAT_METRICS and int(value) != value:
        raise ValueError(f"{name} must be an integer")
    return float(value) if name in _FLOAT_METRICS else int(value)


def normalize_counter(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("Usage counters must be an object")
    result = {key: _number(values.get(key, 0), key) for key in METRICS}
    expected_total = result["prompt_tokens"] + result["completion_tokens"]
    if "total_tokens" in values and result["total_tokens"] != expected_total:
        raise ValueError("Total tokens must match explicit input and output attribution")
    result["total_tokens"] = expected_total
    return result


class UsageLedger:
    """Connection-per-operation SQLite store, safe across threads and processes.

    SUM projections read committed delta rows inside one read transaction. There
    is no separately updated JSON total that could diverge after a crash.
    """

    def __init__(self, path: str):
        self.path = Path(path).absolute()

    def _connect(self, *, write: bool, create: bool = False) -> sqlite3.Connection:
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        else:
            conn = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True,
                                   timeout=30, isolation_level=None)
        if write:
            conn.execute("PRAGMA synchronous=FULL")
        conn.row_factory = sqlite3.Row
        return conn

    def assert_available(self, run_id: str, *, current_attempt_id: str | None = None,
                         require_settled: bool = False) -> None:
        """Check indexed API uncertainty without aggregating usage deltas.

        Admission allows concurrent work owned by the referenced attempt. With
        no reference, any in-flight operation is unresolved. Completion always
        requires all in-flight work to settle. Unknown transport outcomes remain
        visible but do not disable the configured provider retry policy.
        """
        conn = None
        try:
            if current_attempt_id is not None:
                _label(current_attempt_id, "current_attempt_id")
            conn = self._connect(write=False)
            conn.execute("BEGIN")
            if conn.execute("SELECT 1 FROM usage_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise ValueError("Unknown durable run")
            self._assert_api_admission(conn, run_id, current_attempt_id, require_settled)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise UsageLedgerStorageError("Durable usage storage is unavailable") from exc
        finally:
            if conn is not None:
                conn.close()

    def assert_scope_settled(self, run_id: str, attempt_id: str, scope: str) -> None:
        """One child can finish while current-owner sibling scopes remain busy."""
        conn = None
        try:
            _label(scope, "operation_scope")
            conn = self._connect(write=False)
            conn.execute("BEGIN")
            if conn.execute("SELECT 1 FROM usage_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise ValueError("Unknown durable run")
            self._assert_api_admission(conn, run_id, attempt_id, False)
            if conn.execute(
                "SELECT 1 FROM usage_operations WHERE run_id=? AND source='llm_api_attempt' "
                "AND status='in_flight' AND operation_id>=? AND operation_id<? LIMIT 1",
                (run_id, scope + ":", scope + ";"),
            ).fetchone():
                raise UsageLedgerUnresolvedError("This simulation launch has unfinished API work")
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise UsageLedgerStorageError("Durable usage storage is unavailable") from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _assert_api_admission(conn: sqlite3.Connection, run_id: str,
                              current_attempt_id: str | None, require_settled: bool) -> None:
        predicate = "run_id=? AND source='llm_api_attempt'"
        if conn.execute(f"SELECT 1 FROM usage_operations WHERE {predicate} AND status='accounting_error' LIMIT 1",
                        (run_id,)).fetchone():
            raise UsageLedgerUnresolvedError("API accounting error requires precise usage settlement")
        owner_condition, params = "", [run_id]
        if not require_settled and current_attempt_id is not None:
            owner_condition = " AND owner_attempt_id<>?"
            params.append(current_attempt_id)
        if conn.execute(f"SELECT 1 FROM usage_operations WHERE {predicate} AND status='in_flight'{owner_condition} LIMIT 1",
                        params).fetchone():
            reason = "Unsettled API operation" if require_settled else "API operation from another or unknown attempt"
            raise UsageLedgerUnresolvedError(f"{reason} remains in flight")

    @staticmethod
    def _schema(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS usage_schema (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM usage_schema").fetchall()
        if not row:
            conn.execute("INSERT INTO usage_schema VALUES (1)")
        elif len(row) != 1 or row[0][0] != 1:
            raise ValueError("Unsupported usage ledger schema")
        conn.execute("""CREATE TABLE IF NOT EXISTS usage_runs (
            run_id TEXT PRIMARY KEY, legacy_json TEXT,
            legacy_ambiguous INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS usage_operations (
            run_id TEXT NOT NULL, source TEXT NOT NULL, operation_id TEXT NOT NULL,
            owner_attempt_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
            counter_json TEXT NOT NULL, status TEXT NOT NULL,
            snapshot_version INTEGER NOT NULL,
            PRIMARY KEY (run_id, source, operation_id)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS usage_operations_run_source_status_owner "
                     "ON usage_operations(run_id, source, status, owner_attempt_id)")
        columns = ", ".join(f"{key} {'REAL' if key in _FLOAT_METRICS else 'INTEGER'} NOT NULL" for key in METRICS)
        conn.execute(f"""CREATE TABLE IF NOT EXISTS usage_deltas (
            id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            source TEXT NOT NULL, operation_id TEXT NOT NULL,
            stage TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            usage_class TEXT NOT NULL, billing_basis TEXT NOT NULL,
            cost_estimated INTEGER NOT NULL, fallback INTEGER NOT NULL,
            cache_partition_known INTEGER NOT NULL, {columns}
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS usage_deltas_run_attempt ON usage_deltas(run_id, attempt_id)")

    @staticmethod
    def _insert_delta(conn: sqlite3.Connection, run_id: str, attempt_id: str,
                      source: str, operation_id: str, meta: dict, delta: dict) -> None:
        names = ("run_id", "attempt_id", "source", "operation_id", "stage", "provider",
                 "model", "usage_class", "billing_basis", "cost_estimated", "fallback",
                 "cache_partition_known", *METRICS)
        values = (run_id, attempt_id, source, operation_id, meta["stage"], meta["provider"],
                  meta["model"], meta["usage_class"], meta["billing_basis"],
                  int(meta["cost_estimated"]), int(meta["fallback"]),
                  int(meta["cache_partition_known"]), *(delta[key] for key in METRICS))
        conn.execute(f"INSERT INTO usage_deltas ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})", values)

    def initialize(self, run_id: str, legacy_snapshot: dict | None = None) -> None:
        """Inspect/import the old aggregate once, even when no old file exists.

        Legacy totals have no operation history. Keep the supplied snapshot as
        opaque provenance and attribute its cumulative total to ``_legacy``.
        """
        _label(run_id, "run_id")
        conn = None
        try:
            conn = self._connect(write=True, create=True)
            conn.execute("BEGIN IMMEDIATE")
            self._schema(conn)
            exists = conn.execute("SELECT 1 FROM usage_runs WHERE run_id=?", (run_id,)).fetchone()
            if exists is None:
                legacy_json = None
                if legacy_snapshot is not None:
                    if not isinstance(legacy_snapshot, dict):
                        raise ValueError("Legacy snapshot must be an object")
                    serialized = json.dumps(legacy_snapshot, allow_nan=False, sort_keys=True)
                    counter = normalize_counter(legacy_snapshot.get("cumulative_total") or legacy_snapshot.get("total") or {})
                    # Retain the accounting projection and fingerprint, not
                    # arbitrary nested report payloads from a legacy file.
                    legacy_json = json.dumps({
                        "schema": "opaque-legacy-baseline/v1", "total": counter,
                        "snapshot_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                        "report_id": legacy_snapshot.get("report_id"),
                        "status": legacy_snapshot.get("status"),
                        "cost_basis": legacy_snapshot.get("cost_basis") or "unknown",
                    }, allow_nan=False, sort_keys=True)
                    if any(counter.values()):
                        meta = {"stage": "_legacy", "provider": "legacy", "model": "opaque-aggregate",
                                "usage_class": "unknown", "billing_basis": legacy_snapshot.get("cost_basis") or "unknown",
                                "cost_estimated": True, "fallback": False, "cache_partition_known": False}
                        self._insert_delta(conn, run_id, "_legacy", "legacy", "baseline", meta, counter)
                conn.execute("INSERT INTO usage_runs (run_id, legacy_json) VALUES (?, ?)", (run_id, legacy_json))
            conn.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise UsageLedgerStorageError("Cannot initialize durable usage accounting") from exc
        finally:
            if conn is not None:
                conn.close()

    def record_snapshot(self, *, run_id: str, attempt_id: str, source: str,
                        operation_id: str, metadata: dict, counters: dict,
                        status: str = "completed", baseline_included: bool = False) -> dict[str, Any]:
        for name, value in (("run_id", run_id), ("attempt_id", attempt_id), ("source", source),
                            ("operation_id", operation_id), ("status", status)):
            _label(value, name)
        if source == "llm_api_attempt" and status not in {"in_flight", "completed", "unknown", "accounting_error"}:
            raise ValueError("Invalid API operation status")
        meta = dict(metadata)
        for key in ("stage", "provider", "model", "billing_basis"):
            _label(meta.get(key), key)
        if meta.get("usage_class") not in _USAGE_CLASSES:
            raise ValueError("Invalid usage classification")
        meta.setdefault("fallback", False)
        meta.setdefault("cost_estimated", True)
        meta.setdefault("cache_partition_known", False)
        current = normalize_counter(counters)
        conn = None
        try:
            conn = self._connect(write=True)
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT legacy_json FROM usage_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("Run must be initialized before accounting")
            if baseline_included and run["legacy_json"] is None:
                raise ValueError("A baseline-included snapshot requires an imported legacy baseline")
            old = conn.execute("SELECT * FROM usage_operations WHERE run_id=? AND source=? AND operation_id=?",
                               (run_id, source, operation_id)).fetchone()
            if source == "llm_api_attempt" and status == "in_flight" and old is None:
                # Serialize dispatch markers across attempt owners. Read/check
                # races cannot authorize a second owner after the first marker.
                self._assert_api_admission(conn, run_id, attempt_id, False)
            previous = json.loads(old["counter_json"]) if old is not None else empty_counter()
            if old is not None:
                original_meta = json.loads(old["metadata_json"])
                if any(original_meta.get(key) != meta.get(key) for key in ("stage", "provider", "model", "billing_basis", "fallback")):
                    raise UsageLedgerConflict("Usage operation identity has different attribution")
            highwater = {key: max(previous[key], current[key]) for key in METRICS}
            highwater["total_tokens"] = highwater["prompt_tokens"] + highwater["completion_tokens"]
            delta = {key: highwater[key] - previous[key] for key in METRICS}
            version = (old["snapshot_version"] if old is not None else 0) + 1
            # A regressing/replayed snapshot cannot revert a completed status.
            effective_status = status
            if source == "llm_api_attempt" and old is not None:
                old_status = old["status"]
                # A replay may add genuine positive counters, but cannot turn
                # an already-observed outcome back into a dispatch marker.
                precise_settlement = (
                    status == "completed" and meta["usage_class"] == "known"
                    and all(current[key] >= previous[key] for key in (
                        "calls", "prompt_tokens", "completion_tokens", "cache_read_tokens",
                        "cache_write_tokens", "uncached_tokens"))
                )
                if old_status == "accounting_error" and not precise_settlement:
                    effective_status = "accounting_error"
                elif old_status == "unknown" and status == "completed" and not precise_settlement:
                    effective_status = "unknown"
                elif old_status == "completed" and status in {"in_flight", "unknown"}:
                    effective_status = "completed"
                elif old_status != "in_flight" and status == "in_flight":
                    effective_status = old_status
            elif old is not None and not any(delta.values()) and old["status"] not in {"in_flight", "running", "unknown"}:
                effective_status = old["status"]
            conn.execute("""INSERT INTO usage_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source, operation_id) DO UPDATE SET
                metadata_json=excluded.metadata_json, counter_json=excluded.counter_json,
                status=excluded.status, snapshot_version=excluded.snapshot_version""",
                         (run_id, source, operation_id, old["owner_attempt_id"] if old is not None else attempt_id,
                          json.dumps(meta, sort_keys=True), json.dumps(highwater), effective_status, version))
            if baseline_included:
                conn.execute("UPDATE usage_runs SET legacy_ambiguous=1 WHERE run_id=?", (run_id,))
                # Only the first seed can suppress credit. A later seed with
                # larger values must not conceal newly observed consumption.
                if old is None:
                    delta = empty_counter()
            if any(delta.values()):
                self._insert_delta(conn, run_id, attempt_id, source, operation_id, meta, delta)
            conn.commit()
            return delta
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, UsageLedgerConflict):
                raise
            raise UsageLedgerStorageError("Cannot commit durable usage observation") from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _api_operation_state(conn: sqlite3.Connection, run_id: str,
                             current_attempt_id: str | None) -> dict[str, Any]:
        """Run-wide operation state, independent of delta attribution filters."""
        state = {"schema": "llm-api-operation-state/v1", "coverage": "recorded_llm_api_attempts",
                 "current_attempt_id": current_attempt_id, "in_flight": 0,
                 "current_attempt_in_flight": 0 if current_attempt_id is not None else None,
                 "other_attempt_in_flight": 0 if current_attempt_id is not None else None,
                 "unknown": 0, "accounting_error": 0}
        rows = conn.execute("""SELECT status, COUNT(*) AS operations,
            SUM(CASE WHEN owner_attempt_id=? THEN 1 ELSE 0 END) AS current_operations
            FROM usage_operations WHERE run_id=? AND source='llm_api_attempt'
            AND status IN ('in_flight', 'unknown', 'accounting_error') GROUP BY status""",
                            (current_attempt_id, run_id)).fetchall()
        for row in rows:
            state[row["status"]] = row["operations"]
            if row["status"] == "in_flight" and current_attempt_id is not None:
                state["current_attempt_in_flight"] = row["current_operations"]
                state["other_attempt_in_flight"] = row["operations"] - row["current_operations"]
        return state

    def snapshot(self, run_id: str, *, attempt_id: str | None = None,
                 current_attempt_id: str | None = None,
                 missing_ok: bool = False) -> dict[str, Any] | None:
        """Read one consistent projection; missing/corrupt storage is not zero."""
        if missing_ok and not self.path.exists():
            return None
        conn = None
        try:
            if current_attempt_id is not None:
                _label(current_attempt_id, "current_attempt_id")
            conn = self._connect(write=False)
            conn.execute("BEGIN")
            run = conn.execute("SELECT legacy_json, legacy_ambiguous FROM usage_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                if missing_ok:
                    return None
                raise ValueError("Unknown durable run")
            condition, params = "run_id=?", [run_id]
            if attempt_id is not None:
                condition += " AND attempt_id=?"
                params.append(attempt_id)
            dimensions = "stage, provider, model, usage_class, billing_basis, cost_estimated, fallback, cache_partition_known"
            sums = ", ".join(f"SUM({key}) AS {key}" for key in METRICS)
            rows = conn.execute(f"SELECT {dimensions}, {sums} FROM usage_deltas WHERE {condition} GROUP BY {dimensions}", params).fetchall()
            total, fallback = empty_counter(), empty_counter()
            by_stage: dict[str, dict] = {}
            by_model: dict[str, dict] = {}
            by_source: dict[str, dict] = {}
            bases: set[str] = set()
            estimated = False
            cache_partition_known = True
            for row in rows:
                counter = {key: row[key] for key in METRICS}
                targets = [total, by_stage.setdefault(row["stage"], empty_counter()),
                           by_model.setdefault(f"{row['provider']}:{row['model']}", empty_counter()),
                           by_source.setdefault(row["usage_class"], empty_counter())]
                if row["fallback"]:
                    targets.append(fallback)
                for target in targets:
                    for key in METRICS:
                        target[key] += counter[key]
                if counter["calls"] or counter["total_tokens"]:
                    bases.add(row["billing_basis"])
                    estimated = estimated or bool(row["cost_estimated"])
                # Late cache detail can grow in a resumed attempt without any
                # new calls or total tokens; its partition remains unknown.
                cache_partition_known = cache_partition_known and bool(row["cache_partition_known"])
            for counter in [total, fallback, *by_stage.values(), *by_model.values(), *by_source.values()]:
                counter["latency_ms"] = round(counter["latency_ms"], 1)
                counter["cost_usd"] = round(counter["cost_usd"], 6)
            api_operation_state = self._api_operation_state(conn, run_id, current_attempt_id)
            conn.commit()
            return {"run_id": run_id, "attempt_id": attempt_id, "total": total,
                    "api_operation_state": api_operation_state,
                    "by_stage": by_stage, "by_model": by_model,
                    "usage_by_class": by_source, "fallback_attributed": fallback,
                    "cost_estimated": estimated, "cost_basis": next(iter(bases)) if len(bases) == 1 else ("mixed" if bases else "unknown"),
                    "cache_partition_known": cache_partition_known if rows else False,
                    "durable": True, "ledger_schema": "usage-ledger/v1",
                    "legacy_baseline_present": run["legacy_json"] is not None,
                    "legacy_baseline_ambiguous": bool(run["legacy_ambiguous"]),
                    "coverage": "recorded_observations", "usage_complete": False}
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise UsageLedgerStorageError("Cannot read durable usage accounting") from exc
        finally:
            if conn is not None:
                conn.close()


def read_snapshot(ledger_path: str, run_id: str,
                  attempt_id: str | None = None, *,
                  current_attempt_id: str | None = None) -> dict[str, Any] | None:
    """Read without attaching or creating storage; distinguish absent from bad."""
    return UsageLedger(ledger_path).snapshot(run_id, attempt_id=attempt_id,
                                            current_attempt_id=current_attempt_id, missing_ok=True)
