"""Unresolved API observations survive restart without becoming invented spend."""

import hashlib
import json
import multiprocessing
import os
import sqlite3
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.llm_client import LLMClient
from app.utils.usage_ledger import (
    UsageLedger, UsageLedgerStorageError, UsageLedgerUnresolvedError, read_snapshot,
)


RUN = "pipe-unresolved-ledger"


def record(ledger, operation="operation", owner="attempt-a", status="in_flight", *,
           source="llm_api_attempt", usage="unknown", calls=0, inputs=0, outputs=0):
    return ledger.record_snapshot(
        run_id=RUN, attempt_id=owner, source=source, operation_id=operation,
        status=status, metadata={"stage": "report", "provider": "openai", "model": "fixture",
            "usage_class": usage, "billing_basis": "api"},
        counters={"calls": calls, "prompt_tokens": inputs, "completion_tokens": outputs},
    )


def operation_status(ledger, operation="operation"):
    with sqlite3.connect(ledger.path) as conn:
        return conn.execute("SELECT status FROM usage_operations WHERE operation_id=?", (operation,)).fetchone()[0]


def marker_then_exit(path):
    record(UsageLedger(path))
    os._exit(37)


def competing_marker(path, owner, barrier, outcomes):
    ledger = UsageLedger(path)
    barrier.wait(timeout=10)
    try:
        record(ledger, operation=owner, owner=owner)
    except UsageLedgerUnresolvedError:
        outcomes.put("blocked")
    else:
        outcomes.put("admitted")


@pytest.fixture
def ledger(tmp_path):
    ledger = UsageLedger(str(tmp_path / "usage.sqlite3"))
    ledger.initialize(RUN)
    return ledger


@pytest.fixture
def meter(ledger, monkeypatch):
    previous = tel.get_run_context()
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="attempt-a")
    tel.set_run_context(RUN, "report")
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", False)
    monkeypatch.setattr(Config, "LLM_TIERED_ROUTING", False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")
    yield ledger
    tel.LLMMeter.reset(RUN)
    tel.set_run_context(*previous)


def fake_client(calls, usage=None):
    response = SimpleNamespace(usage=usage or {"prompt_tokens": 2, "completion_tokens": 1},
        choices=[SimpleNamespace(message=SimpleNamespace(content="offline", tool_calls=[]))])
    client = LLMClient(provider="claude-cli", model="fixture")
    client.provider = "openai"
    client._openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: calls.append(True) or response)))
    return client


def test_run_wide_state_is_independent_of_delta_filter_and_current_reference(ledger):
    record(ledger)
    record(ledger, "transport", owner="attempt-b", status="unknown", calls=1, inputs=7)
    record(ledger, "bad", owner="attempt-b", status="accounting_error", calls=1)
    record(ledger, "research", owner="attempt-b", source="research_process")
    filtered = ledger.snapshot(RUN, attempt_id="attempt-b", current_attempt_id="attempt-a")
    assert filtered["total"]["total_tokens"] == 7
    assert filtered["api_operation_state"] == {
        "schema": "llm-api-operation-state/v1", "coverage": "recorded_llm_api_attempts",
        "current_attempt_id": "attempt-a", "in_flight": 1, "current_attempt_in_flight": 1,
        "other_attempt_in_flight": 0, "unknown": 1, "accounting_error": 1,
    }
    state = read_snapshot(str(ledger.path), RUN, attempt_id="attempt-b")["api_operation_state"]
    assert state["current_attempt_id"] is None
    assert state["current_attempt_in_flight"] is None and state["other_attempt_in_flight"] is None
    other = ledger.snapshot(RUN, current_attempt_id="attempt-b")["api_operation_state"]
    assert other["current_attempt_in_flight"] == 0 and other["other_attempt_in_flight"] == 1


def test_subprocess_crash_blocks_restart_but_late_settlement_unblocks_without_reset(meter):
    ledger = meter
    process = multiprocessing.get_context("spawn").Process(target=marker_then_exit, args=(str(ledger.path),))
    process.start()
    process.join(20)
    assert process.exitcode == 37
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), attempt_id="attempt-b")
    calls = []
    with pytest.raises(UsageLedgerUnresolvedError):
        fake_client(calls).chat([])
    assert calls == [] and RUN not in tel.LLMMeter._durable_failures
    snapshot = tel.LLMMeter.snapshot(RUN)
    assert snapshot["total"]["total_tokens"] == 0
    assert snapshot["api_operation_state"]["other_attempt_in_flight"] == 1
    delta = record(ledger, owner="attempt-b", status="completed", usage="known", calls=1, inputs=10, outputs=5)
    assert delta["total_tokens"] == 15
    assert not any(record(ledger, owner="attempt-b", status="completed", usage="known", calls=1, inputs=10, outputs=5).values())
    tel.LLMMeter.assert_accounting_available(RUN)
    assert fake_client(calls).chat([]) == "offline"
    assert calls == [True]
    tel.LLMMeter.assert_accounting_settled(RUN)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT owner_attempt_id FROM usage_operations WHERE operation_id='operation'").fetchone()[0] == "attempt-a"


def test_same_owner_concurrency_is_allowed_but_completion_waits(meter):
    record(meter)
    tel.LLMMeter.assert_accounting_available(RUN)
    record(meter, operation="parallel")
    with pytest.raises(UsageLedgerUnresolvedError):
        tel.LLMMeter.assert_accounting_settled(RUN)
    assert RUN not in tel.LLMMeter._durable_failures
    record(meter, status="unknown", calls=1)
    record(meter, operation="parallel", status="unknown", calls=1)
    tel.LLMMeter.assert_accounting_available(RUN)
    tel.LLMMeter.assert_accounting_settled(RUN)
    assert tel.LLMMeter.cumulative_snapshot(RUN)["api_operation_state"]["unknown"] == 2


def test_different_process_owners_cannot_both_insert_dispatch_markers(ledger):
    context = multiprocessing.get_context("spawn")
    barrier, outcomes = context.Barrier(2), context.Queue()
    processes = [context.Process(target=competing_marker,
        args=(str(ledger.path), owner, barrier, outcomes)) for owner in ("attempt-a", "attempt-b")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert sorted(outcomes.get(timeout=5) for _ in processes) == ["admitted", "blocked"]
    assert ledger.snapshot(RUN)["api_operation_state"]["in_flight"] == 1


def test_marker_race_policy_error_does_not_latch_or_block_late_settlement(meter):
    record(meter, owner="other")
    with pytest.raises(UsageLedgerUnresolvedError):
        tel.LLMMeter.record_snapshot("llm_api_attempt", "new", "openai", "fixture", 0, 0, 0,
                                    run_id=RUN, calls=0, status="in_flight")
    assert RUN not in tel.LLMMeter._durable_failures
    record(meter, owner="other", status="completed", usage="known", calls=1, inputs=10)
    tel.LLMMeter.assert_accounting_available(RUN)


def test_malformed_usage_error_survives_reset_until_precise_recovery(meter):
    calls = []
    with pytest.raises(UsageLedgerStorageError):
        fake_client(calls, {"prompt_tokens": True, "completion_tokens": 1}).chat([])
    assert calls == [True]
    assert tel.LLMMeter.snapshot(RUN)["api_operation_state"]["accounting_error"] == 1
    with sqlite3.connect(meter.path) as conn:
        operation = conn.execute("SELECT operation_id FROM usage_operations").fetchone()[0]
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(meter.path), attempt_id="recovery")
    with pytest.raises(UsageLedgerUnresolvedError):
        fake_client(calls).chat([])
    assert calls == [True]
    assert RUN not in tel.LLMMeter._durable_failures
    record(meter, operation, owner="recovery", status="completed", usage="known", calls=1, inputs=10, outputs=5)
    tel.LLMMeter.assert_accounting_available(RUN)
    tel.LLMMeter.assert_accounting_settled(RUN)


def test_precise_recovery_releases_originating_malformed_stop_without_reset(meter):
    calls = []
    with pytest.raises(UsageLedgerUnresolvedError):
        fake_client(calls, {"prompt_tokens": True, "completion_tokens": 1}).chat([])
    assert RUN not in tel.LLMMeter._durable_failures
    with pytest.raises(UsageLedgerUnresolvedError):
        fake_client(calls).chat([])
    assert calls == [True]
    with sqlite3.connect(meter.path) as conn:
        operation = conn.execute("SELECT operation_id FROM usage_operations").fetchone()[0]
    record(meter, operation, status="completed", usage="known", calls=1, inputs=2, outputs=1)
    assert fake_client(calls).chat([]) == "offline"
    assert calls == [True, True]


def test_failed_accounting_error_write_remains_sticky_after_exact_settlement(meter, monkeypatch):
    calls = []

    def fail_write(*args, **kwargs):
        raise sqlite3.OperationalError("offline full disk")

    with monkeypatch.context() as scoped:
        scoped.setattr(UsageLedger, "_insert_delta", staticmethod(fail_write))
        with pytest.raises(UsageLedgerStorageError) as error:
            fake_client(calls, {"prompt_tokens": True, "completion_tokens": 1}).chat([])
        assert not isinstance(error.value, UsageLedgerUnresolvedError)
    with sqlite3.connect(meter.path) as conn:
        operation, status = conn.execute("SELECT operation_id, status FROM usage_operations").fetchone()
    assert status == "in_flight"
    record(meter, operation, status="completed", usage="known", calls=1, inputs=2, outputs=1)
    assert meter.snapshot(RUN)["api_operation_state"]["in_flight"] == 0
    with pytest.raises(UsageLedgerStorageError, match="failed during this attempt"):
        fake_client(calls).chat([])
    assert calls == [True]


@pytest.mark.parametrize("terminal", ["completed", "unknown", "accounting_error"])
def test_growing_replay_never_regresses_api_terminal_to_in_flight(ledger, terminal):
    record(ledger, status=terminal, calls=1, inputs=10)
    delta = record(ledger, status="in_flight", calls=1, inputs=20)
    assert delta["total_tokens"] == 10
    assert operation_status(ledger) == terminal
    assert ledger.snapshot(RUN)["api_operation_state"]["in_flight"] == 0


def test_accounting_error_cannot_be_laundered_by_unknown_estimates_or_stale_known(ledger):
    record(ledger, status="accounting_error", calls=1, inputs=10)
    for status, usage, inputs in [("unknown", "unknown", 20), ("completed", "estimated", 30),
                                  ("completed", "known", 10)]:
        record(ledger, status=status, usage=usage, calls=1, inputs=inputs)
        assert operation_status(ledger) == "accounting_error"
    record(ledger, status="completed", usage="known", calls=1, inputs=30)
    assert operation_status(ledger) == "completed"
    ledger.assert_available(RUN, require_settled=True)
    assert ledger.snapshot(RUN)["total"]["total_tokens"] == 30


def test_research_growth_keeps_legacy_status_semantics(ledger):
    record(ledger, source="research_process", status="completed", calls=1, inputs=10)
    record(ledger, source="research_process", status="in_flight", calls=1, inputs=20)
    assert operation_status(ledger) == "in_flight"
    ledger.assert_available(RUN, require_settled=True)


def test_read_only_v1_without_new_index_is_compatible_and_unchanged(ledger):
    record(ledger)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("DROP INDEX usage_operations_run_source_status_owner")
    before = hashlib.sha256(ledger.path.read_bytes()).hexdigest()
    assert read_snapshot(str(ledger.path), RUN)["api_operation_state"]["in_flight"] == 1
    assert hashlib.sha256(ledger.path.read_bytes()).hexdigest() == before
    ledger.initialize(RUN)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT version FROM usage_schema").fetchone()[0] == 1
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT 1 FROM usage_operations WHERE run_id=? "
                            "AND source='llm_api_attempt' AND status='in_flight' AND owner_attempt_id<>? LIMIT 1",
                            (RUN, "other")).fetchall()
    assert any("usage_operations_run_source_status_owner" in row[3] for row in plan)


def test_bound_projections_include_current_reference_and_flush_state(meter, tmp_path):
    record(meter)
    for snapshot in (tel.LLMMeter.snapshot(RUN), tel.LLMMeter.cumulative_snapshot(RUN),
                     tel.LLMMeter.status_snapshot(RUN)):
        assert snapshot["api_operation_state"]["current_attempt_id"] == "attempt-a"
        assert snapshot["api_operation_state"]["current_attempt_in_flight"] == 1
    path = tmp_path / "telemetry.json"
    tel.LLMMeter.write_run_telemetry(str(path), RUN)
    assert json.loads(path.read_text())["api_operation_state"]["in_flight"] == 1


def test_flush_state_tracks_same_cumulative_read_as_spend(meter, tmp_path, monkeypatch):
    record(meter)
    original = tel.LLMMeter.cumulative_snapshot

    def settle_then_read(cls, run_id):
        record(meter, status="completed", usage="known", calls=1, inputs=10)
        return original(run_id)

    monkeypatch.setattr(tel.LLMMeter, "cumulative_snapshot", classmethod(settle_then_read))
    path = tmp_path / "late-settlement.json"
    tel.LLMMeter.write_run_telemetry(str(path), RUN)
    data = json.loads(path.read_text())
    assert data["cumulative_total"]["total_tokens"] == 10
    assert data["api_operation_state"]["in_flight"] == 0


@pytest.mark.parametrize("invalid", [False, 0, "", [], {}])
def test_current_attempt_reference_is_typed(ledger, invalid):
    with pytest.raises(UsageLedgerStorageError):
        ledger.snapshot(RUN, current_attempt_id=invalid)
    with pytest.raises(UsageLedgerStorageError):
        ledger.assert_available(RUN, current_attempt_id=invalid)


def test_storage_failure_remains_sticky_after_file_recovery(meter):
    saved = meter.path.read_bytes()
    meter.path.unlink()
    with pytest.raises(UsageLedgerStorageError) as error:
        tel.LLMMeter.assert_accounting_available(RUN)
    assert not isinstance(error.value, UsageLedgerUnresolvedError)
    meter.path.write_bytes(saved)
    with pytest.raises(UsageLedgerStorageError, match="failed during this attempt"):
        tel.LLMMeter.assert_accounting_available(RUN)
