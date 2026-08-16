"""FOG-TEL-1: single-active-run fallback attribution for unattributed LLM metering.

Forensic evidence (2026-07 audit): graphiti extraction runs on a ThreadPoolExecutor
whose workers do not inherit the run/stage contextvars, so 100+ minutes of graph-stage
LLM spend landed in the '_global' bucket — never persisted to any run artifact and
invisible to check_budget. New contract pinned here:

- A metering record with no run context, while EXACTLY ONE run is active in this
  process, is attributed to that run and separately counted under
  ``fallback_attributed`` (so forensics can tell inferred from explicit attribution).
- With zero or 2+ active runs the old '_global' behavior is preserved (attribution
  is genuinely ambiguous; per-run budgets must not see that spend).
- Every run snapshot now also carries the process-wide '_global' totals under
  ``unattributed_process`` so no spend is invisible in persisted artifacts.
- check_budget counts fallback-attributed spend toward LLM_RUN_BUDGET_TOKENS, both
  when called with the run id and when called from the unattributed worker thread.
"""

import threading

import pytest

from app.config import Config
from app.utils import telemetry as T


@pytest.fixture(autouse=True)
def _clean_slate():
    """Isolate from other tests: clear thread context, active-run registry, buckets."""
    T.set_run_context(None, None)
    T._clear_active_runs()
    T.LLMMeter.reset(T._DEFAULT_BUCKET)
    yield
    T.set_run_context(None, None)
    T._clear_active_runs()
    T.LLMMeter.reset(T._DEFAULT_BUCKET)


def _record_from_plain_thread(errors, n=1, tokens=(1000, 500)):
    """Record ``n`` unattributed calls from a fresh thread (no contextvars inherited),
    mimicking a ThreadPoolExecutor worker in the graphiti extraction pool."""

    def worker():
        try:
            # A fresh thread must NOT see the main thread's run context.
            assert T.get_run_context() == (None, None)
            for _ in range(n):
                T.LLMMeter.record("minimax", "MiniMax-M3", tokens[0], tokens[1], 10.0)
        except Exception as exc:  # noqa: BLE001 — surface thread failures to the test
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()


# ---------------------------------------------------------------- (a) single active run
def test_plain_thread_record_falls_back_to_sole_active_run():
    T.LLMMeter.reset("run-a")
    T.set_run_context("run-a")  # registers run-a as active in this process
    errors: list = []
    _record_from_plain_thread(errors)
    assert not errors

    snap = T.LLMMeter.snapshot("run-a")
    # Spend is attributed to the run (was: lost in '_global').
    assert snap["total"]["calls"] == 1
    assert snap["total"]["total_tokens"] == 1500
    # ...and tagged as fallback-attributed so it stays distinguishable.
    assert snap["fallback_attributed"]["calls"] == 1
    assert snap["fallback_attributed"]["total_tokens"] == 1500
    # '_global' stays empty: nothing leaked to the process bucket.
    assert T.LLMMeter.snapshot(T._DEFAULT_BUCKET)["total"]["calls"] == 0
    T.LLMMeter.reset("run-a")


def test_fallback_spend_counts_toward_run_budget(monkeypatch):
    T.LLMMeter.reset("run-a")
    T.set_run_context("run-a")
    errors: list = []
    _record_from_plain_thread(errors)
    assert not errors

    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000, raising=False)
    # Budget check by run id (orchestrator side) sees the fallback spend.
    with pytest.raises(T.BudgetExceeded):
        T.check_budget("run-a")

    # Budget check from the unattributed worker thread itself (llm_client passes the
    # contextvar-derived run_id, i.e. None there) must also enforce the run's budget.
    caught: list = []

    def worker():
        try:
            T.check_budget(None)
        except T.BudgetExceeded as exc:
            caught.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    assert caught, "check_budget from an unattributed thread must trip the run budget"
    T.LLMMeter.reset("run-a")


# ---------------------------------------------------------------- (b) two active runs
def test_two_active_runs_keep_global_bucket_and_budget_exclusion(monkeypatch):
    T.LLMMeter.reset("run-a")
    T.LLMMeter.reset("run-b")
    # Register two concurrently active runs (attribution genuinely ambiguous).
    T.set_run_context("run-a")
    T.set_run_context("run-b")
    assert sorted(T.active_run_ids()) == ["run-a", "run-b"]

    errors: list = []
    _record_from_plain_thread(errors)
    assert not errors

    # Old semantics preserved: spend stays in '_global', never guessed onto a run.
    assert T.LLMMeter.snapshot(T._DEFAULT_BUCKET)["total"]["calls"] == 1
    for rid in ("run-a", "run-b"):
        snap = T.LLMMeter.snapshot(rid)
        assert snap["total"]["calls"] == 0
        assert snap["fallback_attributed"]["calls"] == 0

    # '_global'-only spend is excluded from each run's budget.
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000, raising=False)
    T.check_budget("run-a")  # no raise
    T.check_budget("run-b")  # no raise
    T.LLMMeter.reset("run-a")
    T.LLMMeter.reset("run-b")


def test_zero_active_runs_keep_global_bucket():
    errors: list = []
    _record_from_plain_thread(errors)
    assert not errors
    assert T.LLMMeter.snapshot(T._DEFAULT_BUCKET)["total"]["calls"] == 1
    assert T.LLMMeter.snapshot(T._DEFAULT_BUCKET)["total"]["total_tokens"] == 1500


# ---------------------------------------------------------------- (c) snapshot carries '_global'
def test_snapshot_carries_unattributed_process_bucket():
    # Put ambiguous spend into '_global' (zero active runs).
    errors: list = []
    _record_from_plain_thread(errors, tokens=(200, 100))
    assert not errors

    # A run snapshot (even for an empty run) surfaces the process-wide bucket, so the
    # persisted run_telemetry.json can never hide unattributed spend again.
    snap = T.LLMMeter.snapshot("never-recorded-run")
    assert snap["unattributed_process"]["calls"] == 1
    assert snap["unattributed_process"]["total_tokens"] == 300

    T.LLMMeter.reset("run-c")
    T.LLMMeter.record("minimax", "MiniMax-M3", 10, 5, 1.0, run_id="run-c", stage="graph")
    snap_c = T.LLMMeter.snapshot("run-c")
    assert snap_c["unattributed_process"]["total_tokens"] == 300
    # Existing keys keep their meaning (additive-only change).
    assert snap_c["total"]["calls"] == 1
    assert snap_c["by_stage"]["graph"]["total_tokens"] == 15
    assert {"run_id", "total", "by_stage", "by_model",
            "cost_estimated", "cost_basis"} <= set(snap_c)

    # The stage-telemetry artifact view (telemetry.json) carries it too.
    stage_tel = T.build_stage_telemetry("run-c", {"graph": 2.0})
    assert stage_tel["unattributed_process"]["total_tokens"] == 300
    assert stage_tel["by_stage"]["graph"]["calls"] == 1
    T.LLMMeter.reset("run-c")


# ---------------------------------------------------------------- (d) normal attribution unchanged
def test_contextvar_attribution_unchanged():
    T.LLMMeter.reset("run-ctx")
    T.set_run_context("run-ctx", "graph")
    T.LLMMeter.record("minimax", "MiniMax-M3", 100, 50, 5.0)
    snap = T.LLMMeter.snapshot("run-ctx")
    assert snap["total"]["calls"] == 1
    assert snap["by_stage"]["graph"]["calls"] == 1
    # Context-attributed spend is NOT tagged as fallback.
    assert snap["fallback_attributed"]["calls"] == 0
    T.LLMMeter.reset("run-ctx")


def test_explicit_run_id_wins_over_fallback():
    T.LLMMeter.reset("run-active")
    T.LLMMeter.reset("run-explicit")
    T.set_run_context("run-active")
    T.LLMMeter.record("minimax", "MiniMax-M3", 10, 5, 1.0, run_id="run-explicit")
    assert T.LLMMeter.snapshot("run-explicit")["total"]["calls"] == 1
    assert T.LLMMeter.snapshot("run-explicit")["fallback_attributed"]["calls"] == 0
    assert T.LLMMeter.snapshot("run-active")["total"]["calls"] == 0
    T.LLMMeter.reset("run-active")
    T.LLMMeter.reset("run-explicit")


# ---------------------------------------------------------------- registry lifecycle
def test_active_run_registry_lifecycle():
    assert T.active_run_ids() == []
    T.set_run_context("run-l")
    assert T.active_run_ids() == ["run-l"]
    # Pipeline end path: LLMMeter.reset deactivates the run.
    T.LLMMeter.reset("run-l")
    assert T.active_run_ids() == []

    # Standalone-report path: restoring a (None, None) context deactivates the run
    # the calling thread had set (report_agent's finally).
    T.set_run_context("run-m", "report")
    assert T.active_run_ids() == ["run-m"]
    T.set_run_context(None, None)
    assert T.active_run_ids() == []
