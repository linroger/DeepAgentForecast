"""Durable facade stays compatible while surviving meter/process resets."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerConflict, UsageLedgerStorageError


@pytest.fixture
def run(tmp_path):
    rid = "pipe-durable-facade"
    tel.LLMMeter.reset(rid)
    yield rid, str(tmp_path / "usage.sqlite3")
    tel.LLMMeter.reset(rid)


def _snapshot(rid, **kwargs):
    return tel.LLMMeter.record_snapshot("simulation_process", "sim:attempt", "minimax", "m", 100, 10, 1.0,
                                        run_id=rid, stage="run", usage_source="provider", **kwargs)


def test_unbound_calls_never_create_storage(run):
    rid, path = run
    tel.LLMMeter.record("minimax", "m", 10, 5, 1.0, run_id=rid)
    assert tel.LLMMeter.cumulative_snapshot(rid) is None
    assert tel.LLMMeter.snapshot(rid)["total"]["total_tokens"] == 15
    from pathlib import Path
    assert not Path(path).exists()


def test_bound_record_mints_distinct_calls_and_snapshots_deduplicate(run):
    rid, path = run
    tel.LLMMeter.attach_durable_run(rid, path, "attempt-a")
    tel.LLMMeter.record("minimax", "m", 10, 5, 1.0, run_id=rid, stage="report")
    tel.LLMMeter.record("minimax", "m", 10, 5, 1.0, run_id=rid, stage="report")
    _snapshot(rid)
    _snapshot(rid)
    current = tel.LLMMeter.snapshot(rid)
    assert current["total"]["calls"] == 3
    assert current["total"]["total_tokens"] == 140
    assert current["attempt_id"] == "attempt-a"
    assert current["usage_by_class"]["unknown"]["total_tokens"] == 30
    assert current["usage_by_class"]["known"]["total_tokens"] == 110


def test_reset_detaches_but_never_deletes_spend(run):
    rid, path = run
    assert tel.LLMMeter.is_durable_run(rid) is False
    tel.LLMMeter.attach_durable_run(rid, path, "attempt-a")
    assert tel.LLMMeter.is_durable_run(rid) is True
    _snapshot(rid)
    tel.LLMMeter.reset(rid)
    assert tel.LLMMeter.is_durable_run(rid) is False
    assert tel.LLMMeter.cumulative_snapshot(rid) is None
    assert UsageLedger(path).snapshot(rid)["total"]["total_tokens"] == 110
    tel.LLMMeter.attach_durable_run(rid, path, "attempt-b")
    _snapshot(rid)
    assert tel.LLMMeter.snapshot(rid)["total"]["total_tokens"] == 0
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 110


def test_binding_is_idempotent_and_rejects_live_reassignment(run):
    rid, path = run
    attempt = tel.LLMMeter.attach_durable_run(rid, path)
    assert tel.LLMMeter.attach_durable_run(rid, path) == attempt
    with pytest.raises(UsageLedgerConflict):
        tel.LLMMeter.attach_durable_run(rid, path, "other-attempt")


def test_binding_after_memory_spend_is_rejected(run):
    rid, path = run
    tel.LLMMeter.record("minimax", "m", 1, 1, 1.0, run_id=rid)
    with pytest.raises(UsageLedgerConflict, match="before recording"):
        tel.LLMMeter.attach_durable_run(rid, path)


def test_concurrent_threads_reconcile_one_snapshot(run):
    rid, path = run
    tel.LLMMeter.attach_durable_run(rid, path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _snapshot(rid), range(40)))
    assert tel.LLMMeter.snapshot(rid)["total"]["total_tokens"] == 110


def test_ledger_survives_compatibility_projection_failure(run, tmp_path, monkeypatch):
    rid, path = run
    tel.LLMMeter.attach_durable_run(rid, path)
    _snapshot(rid)
    from app.utils import atomic

    def fail(*args, **kwargs):
        raise OSError("disk full writing JSON projection")

    monkeypatch.setattr(atomic, "write_json_atomic", fail)
    with pytest.raises(OSError):
        tel.LLMMeter.write_run_telemetry(str(tmp_path / "run.json"), rid)
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, path)
    assert not any(_snapshot(rid).values())
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 110


def test_bound_missing_storage_is_error_not_zero_or_memory_fallback(run):
    rid, path = run
    tel.LLMMeter.attach_durable_run(rid, path)
    _snapshot(rid)
    from pathlib import Path
    Path(path).unlink()
    with pytest.raises(UsageLedgerStorageError):
        tel.LLMMeter.snapshot(rid)
    with pytest.raises(UsageLedgerStorageError):
        tel.LLMMeter.cumulative_snapshot(rid)
    with pytest.raises(UsageLedgerStorageError):
        _snapshot(rid)
    assert not Path(path).exists()


@pytest.mark.parametrize("durable", [False, True])
def test_repeated_flush_and_resume_add_each_attempt_once(run, tmp_path, durable):
    rid, path = run
    output = tmp_path / "run_telemetry.json"
    if durable:
        tel.LLMMeter.attach_durable_run(rid, path)
    tel.LLMMeter.record("minimax", "m", 10, 5, 1.0, run_id=rid)
    for _ in range(3):
        tel.LLMMeter.write_run_telemetry(str(output), rid, {"report_id": "a"})
    assert json.loads(output.read_text())["cumulative_total"]["total_tokens"] == 15
    tel.LLMMeter.record("minimax", "m", 20, 10, 1.0, run_id=rid)
    tel.LLMMeter.write_run_telemetry(str(output), rid)
    assert json.loads(output.read_text())["cumulative_total"]["total_tokens"] == 45
    tel.LLMMeter.reset(rid)
    if durable:
        tel.LLMMeter.attach_durable_run(rid, path)
    tel.LLMMeter.record("minimax", "m", 30, 15, 1.0, run_id=rid)
    for _ in range(2):
        tel.LLMMeter.write_run_telemetry(str(output), rid, {"report_id": "b"})
    assert json.loads(output.read_text())["cumulative_total"]["total_tokens"] == 90


def test_resume_budget_reads_cumulative_spend(run, monkeypatch):
    rid, path = run
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 100)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    tel.LLMMeter.attach_durable_run(rid, path)
    _snapshot(rid)
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, path)
    assert tel.LLMMeter.snapshot(rid)["total"]["total_tokens"] == 0
    with pytest.raises(tel.BudgetExceeded, match="110 > 100"):
        tel.check_budget(rid)


def test_cache_partition_and_subscription_cost_are_labeled(run):
    rid, path = run
    tel.LLMMeter.attach_durable_run(rid, path)
    tel.LLMMeter.record_snapshot("request", "req", "claude-cli", "claude", 100, 10, 1.0,
                                run_id=rid, stage="research", usage_source="estimate",
                                cache_read_tokens=60, cache_write_tokens=10, uncached_tokens=30)
    snap = tel.LLMMeter.snapshot(rid)
    assert snap["total"]["cost_usd"] == 0
    assert snap["cost_basis"] == "subscription"
    assert snap["cost_estimated"] is True
    assert snap["cache_partition_known"] is True
    assert snap["total"]["cache_read_tokens"] == 60
    assert snap["usage_by_class"]["estimated"]["calls"] == 1
