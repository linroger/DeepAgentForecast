"""Narrow budget reads preserve committed cumulative totals and guard thresholds."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerStorageError


RUN = "pipe-budget-totals"


def record(ledger, operation="one", *, attempt="first", tokens=10, cost=0.1, metadata=None, **kwargs):
    meta = {"stage": "research", "provider": "minimax", "model": "fixture", "usage_class": "known",
            "billing_basis": "estimated_api", "cost_estimated": True, "fallback": False,
            "cache_partition_known": False}
    meta.update(metadata or {})
    return ledger.record_snapshot(run_id=RUN, attempt_id=attempt, source="fixture", operation_id=operation,
                                  metadata=meta, counters={"calls": 1, "prompt_tokens": tokens,
                                                          "completion_tokens": 0, "cost_usd": cost}, **kwargs)


@pytest.fixture
def ledger(tmp_path):
    result = UsageLedger(str(tmp_path / "usage.sqlite3"))
    result.initialize(RUN)
    return result


@pytest.fixture
def meter(ledger, monkeypatch):
    previous = tel.get_run_context()
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="first", existing_only=True)
    tel.set_run_context(RUN, "research")
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    yield ledger
    tel.LLMMeter.reset(RUN)
    tel.set_run_context(*previous)


def assert_equivalent(ledger):
    total = ledger.snapshot(RUN)["total"]
    assert ledger.budget_totals(RUN, include_cost=True) == {
        "total_tokens": total["total_tokens"], "cost_usd": total["cost_usd"]}
    assert ledger.budget_totals(RUN, include_cost=False) == {
        "total_tokens": total["total_tokens"], "cost_usd": None}


def test_empty_known_run_is_zero_but_unknown_run_is_unavailable(ledger):
    assert ledger.budget_totals(RUN, include_cost=True) == {"total_tokens": 0, "cost_usd": 0.0}
    assert ledger.budget_totals(RUN, include_cost=False) == {"total_tokens": 0, "cost_usd": None}
    with pytest.raises(UsageLedgerStorageError):
        ledger.budget_totals("unknown-run", include_cost=False)


def test_legacy_seed_growth_stale_replay_and_resume_match_full_projection(tmp_path):
    ledger = UsageLedger(str(tmp_path / "legacy.sqlite3"))
    legacy = {"cumulative_total": {"calls": 2, "prompt_tokens": 100, "completion_tokens": 20, "cost_usd": 0.3}}
    ledger.initialize(RUN, legacy)
    record(ledger, tokens=100, cost=0.2, baseline_included=True)
    assert ledger.budget_totals(RUN, include_cost=True) == {"total_tokens": 120, "cost_usd": 0.3}
    assert_equivalent(ledger)
    for tokens, cost in [(100, 0.2), (150, 0.25), (90, 0.1), (150, 0.25)]:
        record(ledger, tokens=tokens, cost=cost)
        assert_equivalent(ledger)
    resumed = UsageLedger(str(ledger.path))
    resumed.initialize(RUN, {"total": {"prompt_tokens": 999, "cost_usd": 9}})
    record(resumed, attempt="resumed", tokens=150, cost=0.25)
    record(resumed, attempt="resumed", tokens=180, cost=0.3)
    record(UsageLedger(str(ledger.path)), "child-observation", attempt="resumed", tokens=20, cost=0.04)
    assert_equivalent(resumed)
    assert resumed.snapshot(RUN, attempt_id="resumed")["total"]["total_tokens"] == 50
    assert resumed.budget_totals(RUN, include_cost=True) == {"total_tokens": 220, "cost_usd": 0.44}


@pytest.mark.parametrize("dimension,values", [
    ("stage", ("a", "b")), ("provider", ("a", "b")), ("model", ("a", "b")),
    ("usage_class", ("estimated", "known")), ("billing_basis", ("a", "b")),
    ("cost_estimated", (False, True)), ("fallback", (False, True)),
    ("cache_partition_known", (False, True)),
])
def test_cost_rounding_preserves_every_snapshot_group_dimension(ledger, dimension, values):
    for index, (group, cost) in enumerate([(0, 0.1), (1, 0.1), (0, 0.3), (1, 0.0000005)]):
        record(ledger, str(index), cost=cost, metadata={dimension: values[group]})
    with sqlite3.connect(ledger.path) as conn:
        scalar = conn.execute("SELECT SUM(cost_usd) FROM usage_deltas").fetchone()[0]
    assert round(scalar, 6) == 0.5
    assert ledger.snapshot(RUN)["total"]["cost_usd"] == 0.500001
    assert_equivalent(ledger)


def test_read_only_old_v1_database_does_not_create_indexes_or_touch_operation_state(ledger, monkeypatch):
    record(ledger)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("DROP INDEX usage_operations_run_source_status_owner")
        schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    before = ledger.path.read_bytes()
    original_connect = ledger._connect
    reads = []

    def read_only(*, write, create=False):
        assert write is False and create is False
        conn = original_connect(write=write, create=create)

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table, column))
                if table == "usage_operations":
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(ledger, "_connect", read_only)
    assert ledger.budget_totals(RUN, include_cost=False) == {"total_tokens": 10, "cost_usd": None}
    assert not any(column == "cost_usd" for table, column in reads)
    assert ledger.budget_totals(RUN, include_cost=True) == {"total_tokens": 10, "cost_usd": 0.1}
    assert ledger.path.read_bytes() == before
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == schema


@pytest.mark.parametrize("kind", ["missing", "corrupt", "missing_table", "unknown_run"])
@pytest.mark.parametrize("include_cost", [False, True])
def test_bad_storage_never_becomes_zero_or_is_created(tmp_path, kind, include_cost):
    ledger = UsageLedger(str(tmp_path / "bad.sqlite3"))
    if kind == "corrupt":
        ledger.path.write_bytes(b"offline invalid database")
    elif kind in {"missing_table", "unknown_run"}:
        ledger.initialize("different-run" if kind == "unknown_run" else RUN)
        if kind == "missing_table":
            with sqlite3.connect(ledger.path) as conn:
                conn.execute("DROP TABLE usage_deltas")
    before = ledger.path.read_bytes() if ledger.path.exists() else None
    with pytest.raises(UsageLedgerStorageError):
        ledger.budget_totals(RUN, include_cost=include_cost)
    assert (ledger.path.read_bytes() if ledger.path.exists() else None) == before


@pytest.mark.parametrize("include_cost", [False, True])
def test_read_transaction_excludes_commit_after_run_verification(ledger, monkeypatch, include_cost):
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    record(ledger)
    original_connect = ledger._connect
    writer = UsageLedger(str(ledger.path))
    committed = []

    class ReadConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, parameters=()):
            result = self.conn.execute(sql, parameters)
            if sql.startswith("SELECT 1 FROM usage_runs") and not committed:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(record, writer, "concurrent", tokens=20, cost=0.2).result(timeout=5)
                committed.append(True)
            return result

        def commit(self):
            return self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr(ledger, "_connect", lambda **kwargs: ReadConnection(original_connect(**kwargs)))
    assert ledger.budget_totals(RUN, include_cost=include_cost) == {
        "total_tokens": 10, "cost_usd": 0.1 if include_cost else None}
    assert committed == [True]
    assert ledger.budget_totals(RUN, include_cost=include_cost) == {
        "total_tokens": 30, "cost_usd": 0.3 if include_cost else None}


def test_adapter_failure_is_sticky_even_after_readable_storage_recovers(meter, monkeypatch):
    original = UsageLedger.budget_totals

    def fail(*args, **kwargs):
        raise UsageLedgerStorageError("offline unavailable read")

    monkeypatch.setattr(UsageLedger, "budget_totals", fail)
    with pytest.raises(UsageLedgerStorageError, match="unavailable read"):
        tel.LLMMeter.cumulative_budget_totals(RUN, include_cost=False)
    monkeypatch.setattr(UsageLedger, "budget_totals", original)
    assert meter.budget_totals(RUN, include_cost=False)["total_tokens"] == 0
    with pytest.raises(UsageLedgerStorageError, match="failed during this attempt"):
        tel.LLMMeter.cumulative_budget_totals(RUN, include_cost=True)
    with pytest.raises(UsageLedgerStorageError):
        tel.LLMMeter.assert_accounting_available(RUN)


def test_disabled_limits_do_not_read_or_check_existing_latch(meter, monkeypatch):
    tel.LLMMeter._remember_accounting_failure(RUN)

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled budget must not read")

    monkeypatch.setattr(tel.LLMMeter, "cumulative_budget_totals", forbidden)
    monkeypatch.setattr(tel.LLMMeter, "snapshot", forbidden)
    tel.check_budget(RUN)


def test_bound_guard_uses_narrow_read_and_strict_thresholds(meter, monkeypatch):
    record(meter, tokens=10, cost=0.1)

    def forbidden(*args, **kwargs):
        raise AssertionError("budget must not build a full projection")

    monkeypatch.setattr(tel.LLMMeter, "snapshot", forbidden)
    monkeypatch.setattr(UsageLedger, "snapshot", forbidden)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10)
    tel.check_budget()
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 9)
    with pytest.raises(tel.BudgetExceeded, match="token"):
        tel.check_budget()
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0.1)
    tel.check_budget()
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0.099999)
    with pytest.raises(tel.BudgetExceeded, match="cost"):
        tel.check_budget()


def test_unbound_adapter_and_guard_keep_memory_semantics(meter, monkeypatch):
    tel.LLMMeter.reset(RUN)
    assert tel.LLMMeter.cumulative_budget_totals(RUN, include_cost=True) is None
    tel.LLMMeter.record("minimax", "fixture", 10, 2, 0, run_id=RUN)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 12)
    tel.check_budget()
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 11)
    with pytest.raises(tel.BudgetExceeded):
        tel.check_budget()


def test_valid_cross_group_tokens_exceeding_sqlite_integer_range_remain_exact(meter, monkeypatch):
    for index in range(1024):
        record(meter, str(index), tokens=2**53, cost=0, metadata={"stage": str(index)})
    expected = 2**63
    assert meter.snapshot(RUN)["total"]["total_tokens"] == expected
    assert meter.budget_totals(RUN, include_cost=True)["total_tokens"] == expected
    assert meter.budget_totals(RUN, include_cost=False) == {"total_tokens": expected, "cost_usd": None}
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", expected)
    tel.check_budget(RUN)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", expected - 1)
    with pytest.raises(tel.BudgetExceeded, match="token"):
        tel.check_budget(RUN)
    tel.LLMMeter.assert_accounting_available(RUN)
