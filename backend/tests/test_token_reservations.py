"""Transactional estimated token allowances across dispatch, failures and restart."""

import json
import multiprocessing
import os
import sqlite3

import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.usage_ledger import (
    UsageLedger, UsageLedgerBudgetExceeded, UsageLedgerConflict, UsageLedgerStorageError,
    UsageLedgerUnresolvedError,
)


RUN = "pipe-token-reservations"


def plan(limit=100, prompt=20, completion=40):
    return {"token_limit": limit, "prompt_tokens_estimate": prompt,
            "completion_tokens_limit": completion, "estimator": "utf8-json-quarter/v1"}


def observe(ledger, operation="one", *, status="in_flight", usage="unknown", inputs=0, outputs=0,
            attempt="shared", reservation=None, cost=0):
    return ledger.record_snapshot(
        run_id=RUN, attempt_id=attempt, source="llm_api_attempt", operation_id=operation,
        metadata={"stage": "run", "provider": "minimax", "model": "fixture", "usage_class": usage,
                  "billing_basis": "api", "cost_estimated": True, "fallback": False},
        counters={"calls": 0 if status == "in_flight" else 1, "prompt_tokens": inputs,
                  "completion_tokens": outputs, "cost_usd": cost},
        status=status, token_reservation=reservation,
    )


@pytest.fixture
def ledger(tmp_path):
    result = UsageLedger(str(tmp_path / "usage.sqlite3"))
    result.initialize(RUN)
    return result


def state(ledger):
    return ledger.snapshot(RUN)["token_reservation_state"]


def held(ledger):
    return state(ledger)["reserved_tokens"]


def test_atomic_marker_and_allowance_preserve_strict_capacity_and_replay(ledger):
    observe(ledger, reservation=plan())
    assert held(ledger) == 60
    assert ledger.snapshot(RUN)["total"]["calls"] == 0
    assert not any(observe(ledger, reservation=plan()).values())
    assert held(ledger) == 60
    observe(ledger, "two", reservation=plan(prompt=10, completion=30))
    assert held(ledger) == 100
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "rejected", reservation=plan(prompt=0, completion=1))
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_operations").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM usage_reservations").fetchone()[0] == 2


@pytest.mark.parametrize("status", ["completed", "unknown"])
def test_reported_terminal_usage_releases_in_same_commit_and_never_reholds(ledger, status):
    observe(ledger, reservation=plan())
    observe(ledger, status=status, usage="known", inputs=8, outputs=12, cost=0.0000015)
    snapshot = ledger.snapshot(RUN)
    assert snapshot["total"]["total_tokens"] == 20
    assert snapshot["total"]["cost_usd"] == 0.000002
    assert snapshot["token_reservation_state"]["reserved_tokens"] == 0
    assert snapshot["token_reservation_state"]["active_operations"] == 0
    observe(ledger, reservation=plan())
    assert held(ledger) == 0
    observe(ledger, "two", reservation=plan(prompt=40, completion=40))
    assert held(ledger) == 80


@pytest.mark.parametrize("status,usage", [("unknown", "unknown"), ("completed", "estimated"),
                                         ("accounting_error", "unknown")])
def test_uncertain_usage_retains_residual_without_double_counting(ledger, status, usage):
    observe(ledger, reservation=plan())
    observe(ledger, status=status, usage=usage, inputs=5, outputs=10)
    snapshot = ledger.snapshot(RUN)
    assert snapshot["total"]["total_tokens"] + held(ledger) == 60
    assert held(ledger) == 45 and state(ledger)["active_operations"] == 1
    observe(ledger, status=status, usage=usage, inputs=10, outputs=20)
    observe(ledger, status=status, usage=usage, inputs=2, outputs=5)
    assert held(ledger) == 30
    observe(ledger, status="completed", usage="known", inputs=10, outputs=20)
    assert held(ledger) == 0 and state(ledger)["active_operations"] == 0


def test_stale_known_observation_cannot_release_uncovered_growth(ledger):
    observe(ledger, reservation=plan())
    observe(ledger, status="completed", usage="estimated", inputs=20, outputs=10)
    observe(ledger, status="completed", usage="known", inputs=10, outputs=10)
    assert held(ledger) == 30
    observe(ledger, status="completed", usage="known", inputs=20, outputs=10)
    assert held(ledger) == 0


def test_actual_overrun_is_committed_unclipped_and_blocks_next_reservation(ledger):
    observe(ledger, reservation=plan())
    observe(ledger, status="completed", usage="known", inputs=100, outputs=30)
    assert ledger.snapshot(RUN)["total"]["total_tokens"] == 130
    assert held(ledger) == 0
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, "next", reservation=plan(prompt=0, completion=1))


def test_unknown_reservation_survives_resume_and_exact_late_settlement(ledger):
    observe(ledger, reservation=plan())
    observe(ledger, status="unknown")
    restarted = UsageLedger(str(ledger.path))
    restarted.initialize(RUN)
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(restarted, "next", attempt="resumed", reservation=plan())
    observe(restarted, status="completed", usage="known", inputs=10, attempt="resumed")
    assert held(restarted) == 0
    assert restarted.snapshot(RUN, attempt_id="resumed")["total"]["total_tokens"] == 10
    observe(restarted, "next", attempt="resumed", reservation=plan())


def test_failed_settlement_rolls_back_usage_and_hold_release_together(ledger, monkeypatch):
    observe(ledger, reservation=plan())

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("offline full disk")

    with monkeypatch.context() as scoped:
        scoped.setattr(UsageLedger, "_insert_delta", staticmethod(fail))
        with pytest.raises(UsageLedgerStorageError):
            observe(ledger, status="completed", usage="known", inputs=10)
    assert held(ledger) == 60
    snapshot = ledger.snapshot(RUN)
    assert snapshot["total"]["total_tokens"] == 0
    assert snapshot["api_operation_state"]["in_flight"] == 1
    observe(ledger, status="completed", usage="known", inputs=10)
    assert held(ledger) == 0


@pytest.mark.parametrize("old_status", ["in_flight", "unknown", "accounting_error"])
def test_activation_rejects_preexisting_unreserved_uncertainty(ledger, old_status):
    observe(ledger, "old", status=old_status)
    with pytest.raises(UsageLedgerUnresolvedError):
        observe(ledger, reservation=plan())
    assert state(ledger)["token_limit"] is None
    observe(ledger, "old", status="completed", usage="known", inputs=5)
    observe(ledger, reservation=plan())
    assert held(ledger) == 60


def test_legacy_and_settled_estimates_are_explicit_historical_spend(tmp_path):
    ledger = UsageLedger(str(tmp_path / "legacy.sqlite3"))
    ledger.initialize(RUN, {"total": {"prompt_tokens": 10, "completion_tokens": 5}})
    observe(ledger, "old", status="completed", usage="estimated", inputs=25)
    observe(ledger, reservation=plan())
    snapshot = ledger.snapshot(RUN)
    assert snapshot["total"]["total_tokens"] == 40 and held(ledger) == 60
    assert snapshot["legacy_baseline_present"] and not snapshot["usage_complete"]
    assert state(ledger)["estimated"] is True


def test_policy_is_pinned_even_on_denial_and_cannot_be_disabled_by_new_marker(ledger):
    with pytest.raises(UsageLedgerBudgetExceeded):
        observe(ledger, reservation=plan(limit=10))
    assert state(ledger)["token_limit"] == 10 and held(ledger) == 0
    with pytest.raises(UsageLedgerBudgetExceeded, match="differs"):
        observe(ledger, reservation=plan(limit=100))
    with pytest.raises(UsageLedgerBudgetExceeded, match="requires"):
        observe(ledger)


def test_old_writer_marker_is_blocked_but_old_terminal_write_retains_hold(ledger):
    observe(ledger, reservation=plan())
    with sqlite3.connect(ledger.path) as conn:
        original = conn.execute("SELECT * FROM usage_operations").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="reservation required"):
            conn.execute("INSERT INTO usage_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (*original[:2], "old-writer", *original[3:]))
        # Prior implementations update only the operation/counters, with no
        # sidecar knowledge. That cannot grant capacity by releasing a hold.
        conn.execute("UPDATE usage_operations SET status='completed' WHERE operation_id='one'")
    assert held(ledger) == 60
    observe(ledger, status="completed", usage="known", inputs=10)
    assert held(ledger) == 0


def test_read_only_old_v1_without_extension_preserves_disabled_mode(ledger):
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("DROP TRIGGER usage_require_api_reservation")
        conn.execute("DROP TABLE usage_reservations")
        conn.execute("DROP TABLE usage_token_policy")
    before = ledger.path.read_bytes()
    assert state(ledger)["token_limit"] is None
    tel.LLMMeter.reset(RUN)
    try:
        tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), "shared", existing_only=True)
        assert ledger.path.read_bytes() == before
        observe(ledger)
        observe(ledger, status="completed", usage="known", inputs=1)
        with pytest.raises(UsageLedgerStorageError):
            observe(ledger, "enabled", reservation=plan())
    finally:
        tel.LLMMeter.reset(RUN)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='usage_reservations'").fetchone() is None


def _compete(path, barrier, results, number):
    ledger = UsageLedger(path)
    barrier.wait(timeout=10)
    try:
        observe(ledger, f"process-{number}", reservation=plan())
        results.put("admitted")
    except UsageLedgerBudgetExceeded:
        results.put("denied")


def test_same_owner_processes_cannot_both_spend_the_last_allowance(ledger):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_compete, args=(str(ledger.path), barrier, results, index)) for index in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in processes) == ["admitted", "denied"]
    assert held(ledger) == 60


def _crash_after_marker(path):
    observe(UsageLedger(path), reservation=plan())
    os._exit(37)


def test_process_crash_retains_allowance_and_prior_owner_stop(ledger):
    process = multiprocessing.get_context("spawn").Process(target=_crash_after_marker, args=(str(ledger.path),))
    process.start()
    process.join(15)
    assert process.exitcode == 37 and held(ledger) == 60
    with pytest.raises(UsageLedgerUnresolvedError):
        observe(ledger, "resumed", attempt="next-owner", reservation=plan())
    observe(ledger, status="completed", usage="known", attempt="next-owner", inputs=10)
    observe(ledger, "resumed", attempt="next-owner", reservation=plan())


def test_meter_translates_policy_denial_without_poisoning_storage_latch(ledger):
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), "shared", existing_only=True)
    try:
        def marker(operation):
            return tel.LLMMeter.record_snapshot("llm_api_attempt", operation, "minimax", "fixture", 0, 0, 0,
                                               run_id=RUN, stage="run", calls=0, status="in_flight",
                                               token_reservation=plan())
        marker("one")
        with pytest.raises(tel.BudgetExceeded):
            marker("two")
        with pytest.raises(tel.BudgetExceeded):
            tel.LLMMeter.record_snapshot("llm_api_attempt", "disabled", "minimax", "fixture", 0, 0, 0,
                                        run_id=RUN, stage="run", calls=0, status="in_flight")
        tel.LLMMeter.assert_accounting_available(RUN)
        tel.LLMMeter.record_snapshot("llm_api_attempt", "one", "minimax", "fixture", 5, 5, 0,
                                    run_id=RUN, stage="run", usage_source="known")
        marker("two")
        assert tel.LLMMeter.cumulative_snapshot(RUN)["token_reservation_state"]["reserved_tokens"] == 60
    finally:
        tel.LLMMeter.reset(RUN)


@pytest.mark.parametrize("invalid", [plan(limit=0), plan(prompt=True), {**plan(), "estimator": "unknown"}])
def test_invalid_allowance_is_rejected_before_any_marker(ledger, invalid):
    with pytest.raises(ValueError):
        observe(ledger, reservation=invalid)
    assert state(ledger)["token_limit"] is None


def test_reservation_identity_conflict_does_not_change_capacity(ledger):
    observe(ledger, reservation=plan())
    with pytest.raises(UsageLedgerConflict):
        observe(ledger, reservation=plan(prompt=30))
    assert held(ledger) == 60
    with sqlite3.connect(ledger.path) as conn:
        assert json.loads(conn.execute("SELECT counter_json FROM usage_operations").fetchone()[0])["calls"] == 0


@pytest.mark.parametrize("object_type,name", [("TABLE", "usage_reservations"),
                                             ("TABLE", "usage_token_policy"),
                                             ("TRIGGER", "usage_require_api_reservation")])
def test_partial_extension_loss_cannot_silently_recreate_or_hide_holds(ledger, object_type, name):
    observe(ledger, reservation=plan())
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"DROP {object_type} {name}")
    before = ledger.path.read_bytes()
    with pytest.raises(UsageLedgerStorageError):
        ledger.snapshot(RUN)
    with pytest.raises(UsageLedgerStorageError):
        ledger.initialize(RUN)
    with pytest.raises(UsageLedgerStorageError):
        observe(ledger, "next", reservation=plan())
    assert ledger.path.read_bytes() == before


def test_reservation_state_survives_meter_status_flush_and_reset(ledger, tmp_path):
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), "shared", existing_only=True)
    try:
        observe(ledger, reservation=plan())
        expected = state(ledger)
        assert tel.LLMMeter.status_snapshot(RUN)["token_reservation_state"] == expected
        output = tmp_path / "run.json"
        tel.LLMMeter.write_run_telemetry(str(output), RUN, extra={"token_reservation_state": "stale"})
        tel.LLMMeter.write_run_telemetry(str(output), RUN)
        assert json.loads(output.read_text())["token_reservation_state"] == expected
    finally:
        tel.LLMMeter.reset(RUN)
    assert state(ledger) == expected


@pytest.mark.parametrize("changed_limit", [0, 1000])
def test_captured_token_limit_survives_config_change_before_postcheck(ledger, monkeypatch, changed_limit):
    tel.LLMMeter.reset(RUN)
    tel.LLMMeter.attach_durable_run(RUN, str(ledger.path), "shared", existing_only=True)
    try:
        observe(ledger, reservation=plan(limit=20, prompt=5, completion=10))
        observe(ledger, status="completed", usage="known", inputs=20, outputs=5)
        monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", changed_limit)
        monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
        with pytest.raises(tel.BudgetExceeded, match="25 > 20"):
            tel.check_budget(RUN, token_limit=20)
    finally:
        tel.LLMMeter.reset(RUN)
