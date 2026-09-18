"""Offline durable accounting invariants: replay, concurrency and crash windows."""

import multiprocessing
import sqlite3

import pytest

from app.utils.usage_ledger import (
    UsageLedger, UsageLedgerConflict, UsageLedgerStorageError, read_snapshot,
)


def _metadata(**overrides):
    result = {"stage": "research", "provider": "minimax", "model": "m",
              "usage_class": "known", "billing_basis": "estimated_api",
              "cost_estimated": True, "fallback": False, "cache_partition_known": True}
    result.update(overrides)
    return result


def _record(ledger, operation="op", attempt="attempt-a", **overrides):
    counters = {"calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                "latency_ms": 2.0, "cost_usd": 0.01, "cache_read_tokens": 60,
                "cache_write_tokens": 10, "uncached_tokens": 30}
    counters.update(overrides.pop("counters", {}))
    return ledger.record_snapshot(run_id="pipe-ledger", attempt_id=attempt,
                                  source="test", operation_id=operation,
                                  metadata=overrides.pop("metadata", _metadata()),
                                  counters=counters, **overrides)


def _process_writer(path, index):
    ledger = UsageLedger(path)
    ledger.initialize("pipe-ledger")
    for _ in range(3):
        _record(ledger, operation=f"op-{index}")
        _record(ledger, operation="shared", counters={"prompt_tokens": 100 + index})


@pytest.fixture
def ledger(tmp_path):
    result = UsageLedger(str(tmp_path / "usage.sqlite3"))
    result.initialize("pipe-ledger")
    return result


def test_duplicate_growth_stale_and_componentwise_highwater(ledger):
    assert _record(ledger)["total_tokens"] == 110
    assert not any(_record(ledger).values())
    delta = _record(ledger, counters={"prompt_tokens": 150, "completion_tokens": 5})
    assert delta["prompt_tokens"] == 50 and delta["completion_tokens"] == 0
    assert delta["calls"] == 0 and delta["total_tokens"] == 50
    _record(ledger, counters={"prompt_tokens": 80, "completion_tokens": 20})
    snapshot = ledger.snapshot("pipe-ledger")
    assert snapshot["total"]["calls"] == 1
    assert snapshot["total"]["total_tokens"] == 170
    assert snapshot["usage_by_class"]["known"]["total_tokens"] == 170


def test_restart_replay_retains_owner_and_only_new_growth_belongs_to_new_attempt(ledger):
    _record(ledger)
    restarted = UsageLedger(str(ledger.path))
    restarted.initialize("pipe-ledger")
    assert not any(_record(restarted, attempt="attempt-b").values())
    assert restarted.snapshot("pipe-ledger", attempt_id="attempt-b")["total"]["total_tokens"] == 0
    _record(restarted, attempt="attempt-b", counters={"prompt_tokens": 120})
    assert restarted.snapshot("pipe-ledger", attempt_id="attempt-a")["total"]["total_tokens"] == 110
    assert restarted.snapshot("pipe-ledger", attempt_id="attempt-b")["total"]["total_tokens"] == 20
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT owner_attempt_id FROM usage_operations").fetchone()[0] == "attempt-a"


def test_separate_processes_atomic_dedup_and_growth(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    processes = [multiprocessing.get_context("spawn").Process(target=_process_writer, args=(path, i))
                 for i in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    snapshot = UsageLedger(path).snapshot("pipe-ledger")
    assert snapshot["total"]["calls"] == 5
    assert snapshot["total"]["total_tokens"] == 4 * 110 + 113


def test_failed_delta_write_rolls_back_dedup_marker(ledger, monkeypatch):
    original = ledger._insert_delta

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("simulated full disk")

    monkeypatch.setattr(ledger, "_insert_delta", fail)
    with pytest.raises(UsageLedgerStorageError):
        _record(ledger)
    assert ledger.snapshot("pipe-ledger")["total"]["calls"] == 0
    monkeypatch.setattr(ledger, "_insert_delta", original)
    assert _record(ledger)["total_tokens"] == 110


def test_crash_after_commit_before_projection_is_recoverable(ledger, monkeypatch):
    _record(ledger)
    monkeypatch.setattr(ledger, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("projection interrupted")))
    with pytest.raises(RuntimeError, match="projection"):
        ledger.snapshot("pipe-ledger")
    recovered = UsageLedger(str(ledger.path))
    assert not any(_record(recovered).values())
    assert recovered.snapshot("pipe-ledger")["total"]["total_tokens"] == 110


def test_legacy_baseline_import_is_once_and_is_explicitly_opaque(tmp_path):
    ledger = UsageLedger(str(tmp_path / "usage.sqlite3"))
    baseline = {"total": {"calls": 2, "prompt_tokens": 5, "completion_tokens": 5},
                "cumulative_total": {"calls": 10, "prompt_tokens": 500, "completion_tokens": 50},
                "report_id": "old-report", "cost_basis": "subscription"}
    ledger.initialize("pipe-ledger", baseline)
    ledger.initialize("pipe-ledger", {"total": {"calls": 20, "prompt_tokens": 9999}})
    snapshot = ledger.snapshot("pipe-ledger")
    assert snapshot["total"]["total_tokens"] == 550
    assert snapshot["by_stage"]["_legacy"]["calls"] == 10
    assert snapshot["usage_by_class"]["unknown"]["total_tokens"] == 550
    assert snapshot["legacy_baseline_present"] is True
    assert snapshot["usage_complete"] is False
    assert ledger.snapshot("pipe-ledger", attempt_id="attempt-a")["total"]["calls"] == 0


def test_baseline_seed_avoids_old_child_double_credit_and_preserves_growth(tmp_path):
    ledger = UsageLedger(str(tmp_path / "usage.sqlite3"))
    ledger.initialize("pipe-ledger", {"total": {"calls": 1, "prompt_tokens": 100, "completion_tokens": 10}})
    assert not any(_record(ledger, baseline_included=True).values())
    assert not any(_record(ledger, baseline_included=True).values())
    assert not any(_record(ledger).values())
    _record(ledger, counters={"prompt_tokens": 150})
    snapshot = ledger.snapshot("pipe-ledger")
    assert snapshot["total"]["total_tokens"] == 160
    assert snapshot["legacy_baseline_ambiguous"] is True


def test_baseline_seed_requires_imported_baseline(ledger):
    with pytest.raises(UsageLedgerStorageError, match="commit"):
        _record(ledger, baseline_included=True)
    assert _record(ledger)["total_tokens"] == 110


def test_identity_conflict_cannot_reassign_spend(ledger):
    _record(ledger)
    with pytest.raises(UsageLedgerConflict):
        _record(ledger, metadata=_metadata(provider="other"))
    assert ledger.snapshot("pipe-ledger")["total"]["total_tokens"] == 110


def test_status_update_and_late_old_status_do_not_add_spend(ledger):
    _record(ledger, status="in_flight")
    _record(ledger, status="completed")
    _record(ledger, status="in_flight")
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT status FROM usage_operations").fetchone()[0] == "completed"
    assert ledger.snapshot("pipe-ledger")["total"]["calls"] == 1


def test_read_only_missing_unknown_and_corrupt_are_distinct(tmp_path):
    path = tmp_path / "missing" / "usage.sqlite3"
    assert read_snapshot(str(path), "pipe-ledger") is None
    assert not path.parent.exists()
    ledger = UsageLedger(str(path))
    ledger.initialize("pipe-ledger")
    assert read_snapshot(str(path), "other") is None
    assert read_snapshot(str(path), "pipe-ledger")["total"]["total_tokens"] == 0
    path.write_text("corrupt", encoding="utf-8")
    with pytest.raises(UsageLedgerStorageError):
        read_snapshot(str(path), "pipe-ledger")


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), 0.5, "4", True, False, "", None])
def test_invalid_token_observations_are_rejected(ledger, value):
    with pytest.raises(ValueError):
        _record(ledger, counters={"prompt_tokens": value})
    assert ledger.snapshot("pipe-ledger")["total"]["calls"] == 0


@pytest.mark.parametrize("counter", [
    {"total_tokens": 1000},
    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 1000},
    {"prompt_tokens": False}, {"prompt_tokens": ""},
])
def test_legacy_inconsistent_or_untyped_totals_fail_closed(tmp_path, counter):
    ledger = UsageLedger(str(tmp_path / "usage.sqlite3"))
    with pytest.raises(UsageLedgerStorageError):
        ledger.initialize("pipe-ledger", {"cumulative_total": counter})
