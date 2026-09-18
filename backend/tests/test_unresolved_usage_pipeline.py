"""Crash, restart and public status scenarios for uncertain physical API work."""

import json
from pathlib import Path
import subprocess
import sys

from flask import Flask
import pytest

from app.api import research_bp
from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter, get_run_context, set_run_context
from app.utils.usage_ledger import UsageLedger, UsageLedgerStorageError
from test_llm_physical_attempts import client as api_client, response
from test_orchestrator_research_wiring import _exercise_prepare_run_resume


def _marker(ledger, run, owner="old-owner", status="in_flight", operation="interrupted"):
    ledger.record_snapshot(
        run_id=run, attempt_id=owner, source="llm_api_attempt", operation_id=operation,
        metadata={"stage": "report", "provider": "minimax", "model": "offline",
                  "usage_class": "unknown", "billing_basis": "estimated_api"},
        counters={}, status=status,
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    run = "pipe_unresolved_scenario"
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")
    previous = get_run_context()
    LLMMeter.reset(run)
    ledger = UsageLedger(str(tmp_path / "usage_ledger.sqlite3"))
    ledger.initialize(run)
    state = po.PipelineState(pipeline_id=run, prompt="offline", task_id="new-owner")
    state.options["usage_attempt_id"] = "new-owner"
    state.status = "running"
    po.PipelineManager.save(state)
    yield ledger, state, tmp_path
    LLMMeter.reset(run)
    set_run_context(*previous)


@pytest.mark.parametrize("budget", [0, 1000])
def test_process_exit_after_dispatch_marker_stops_new_sdk_work(isolated, monkeypatch, budget):
    ledger, state, _ = isolated
    # os._exit bypasses Python cleanup exactly after the actual shared helper
    # has committed its marker. The fixture transport performs no network I/O.
    program = """
import os, sys
from types import SimpleNamespace
from app.config import Config
from app.utils.llm_client import LLMClient
from app.utils.telemetry import LLMMeter, set_run_context
Config.LLM_TELEMETRY_ENABLED = True
Config.LLM_RUN_BUDGET_TOKENS = 0
Config.LLM_RUN_BUDGET_USD = 0
LLMMeter.attach_durable_run(sys.argv[1], sys.argv[2], 'crashed-owner')
set_run_context(sys.argv[1], 'report')
sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: os._exit(37))))
LLMClient.__new__(LLMClient)._create_openai_completion(sdk, 'minimax', 'offline', {'model': 'offline', 'messages': []})
"""
    result = subprocess.run(
        [sys.executable, "-c", program, state.pipeline_id, str(ledger.path)],
        cwd=Path(__file__).resolve().parents[1], check=False,
        capture_output=True, text=True,
    )
    assert result.returncode == 37, result.stderr
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", budget)
    LLMMeter.attach_durable_run(state.pipeline_id, str(ledger.path), "new-owner")
    set_run_context(state.pipeline_id, "report")
    snap = LLMMeter.cumulative_snapshot(state.pipeline_id)
    assert snap["total"]["total_tokens"] == snap["total"]["calls"] == 0
    assert snap["api_operation_state"]["in_flight"] == 1
    assert snap["api_operation_state"]["other_attempt_in_flight"] == 1
    transports = []
    with pytest.raises(UsageLedgerStorageError):
        api_client(lambda **kw: transports.append(True) or response()).chat([])
    assert transports == []


@pytest.mark.parametrize("status", ["in_flight", "accounting_error", "unknown"])
def test_status_exposes_uncertainty_without_erasing_recorded_zero(isolated, status):
    ledger, state, _ = isolated
    _marker(ledger, state.pipeline_id, status=status)
    before = ledger.path.read_bytes()
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    result = app.test_client().get(f"/api/research/status/{state.pipeline_id}")
    assert result.status_code == 200
    live = result.get_json()["data"]["live"]
    spend = live["spend_so_far"]
    assert spend["available"] is True and spend["tokens"] == 0
    assert spend["usage_complete"] is False
    assert spend["api_operation_state"][status] == 1
    blocked = status != "unknown"
    assert live["budget"]["available"] is (not blocked)
    assert live["budget"]["remaining_tokens"] == (None if blocked else 1000)
    assert live["budget"]["unavailable_reason"] == ("unresolved_api_operations" if blocked else None)
    assert ledger.path.read_bytes() == before
    assert not LLMMeter.is_durable_run(state.pipeline_id)


def test_flush_keeps_run_wide_operation_state_separate_from_attempt_totals(isolated):
    ledger, state, _ = isolated
    _marker(ledger, state.pipeline_id)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    orch._flush_run_telemetry(state)
    saved = json.loads(Path(orch._tel_path).read_text())
    assert saved["total"]["calls"] == saved["cumulative_total"]["calls"] == 0
    assert saved["api_operation_state"]["other_attempt_in_flight"] == 1
    assert saved["usage_accounting"]["api_operation_state"]["other_attempt_in_flight"] == 1


def test_flush_uses_cumulative_operation_state_when_response_settles_between_reads(isolated, monkeypatch):
    ledger, state, _ = isolated
    _marker(ledger, state.pipeline_id)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    cumulative = LLMMeter.cumulative_snapshot

    def settle_before_cumulative(cls, run_id):
        ledger.record_snapshot(
            run_id=run_id, attempt_id="old-owner", source="llm_api_attempt",
            operation_id="interrupted", status="completed",
            metadata={"stage": "report", "provider": "minimax", "model": "offline",
                      "usage_class": "known", "billing_basis": "estimated_api"},
            counters={"calls": 1, "prompt_tokens": 10, "completion_tokens": 5},
        )
        return cumulative(run_id)

    monkeypatch.setattr(LLMMeter, "cumulative_snapshot", classmethod(settle_before_cumulative))
    orch._flush_run_telemetry(state)
    saved = json.loads(Path(orch._tel_path).read_text())
    assert saved["cumulative_total"]["total_tokens"] == 15
    assert saved["api_operation_state"]["in_flight"] == 0
    assert saved["api_operation_state"] == saved["usage_accounting"]["api_operation_state"]


@pytest.mark.parametrize("reference_present", [True, False])
def test_pending_budget_never_infers_owner_liveness(isolated, reference_present):
    ledger, state, _ = isolated
    _marker(ledger, state.pipeline_id, owner="new-owner")
    if not reference_present:
        state.options.pop("usage_attempt_id")
        po.PipelineManager.save(state)
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    live = app.test_client().get(f"/api/research/status/{state.pipeline_id}").get_json()["data"]["live"]
    operations = live["spend_so_far"]["api_operation_state"]
    assert operations["current_attempt_in_flight"] == (1 if reference_present else None)
    assert operations["other_attempt_in_flight"] == (0 if reference_present else None)
    assert live["budget"]["spent_tokens"] == 0
    assert live["budget"]["remaining_tokens"] is None


@pytest.mark.parametrize("status", ["in_flight", "accounting_error"])
def test_restart_stops_before_any_stage_is_started(isolated, monkeypatch, status):
    ledger, state, _ = isolated
    _marker(ledger, state.pipeline_id, status=status)
    entered = []
    monkeypatch.setattr(po.PipelineOrchestrator, "_start_heartbeat", lambda *a: None)
    monkeypatch.setattr(po.PipelineOrchestrator, "_write_run_manifest", lambda *a: None)
    monkeypatch.setattr(po, "_register_outage_breaker", lambda *a: None)

    def start_stage(*args):
        entered.append(True)
        raise AssertionError("Unresolved restart must not reach a stage")

    monkeypatch.setattr(po.PipelineOrchestrator, "_make_stage_updater", start_stage)
    po.PipelineOrchestrator._run(state)
    assert state.status == "failed"
    assert entered == []
    assert not LLMMeter.is_durable_run(state.pipeline_id)
    assert ledger.snapshot(state.pipeline_id)["api_operation_state"][status] == 1


@pytest.mark.parametrize("mode", ["research_only", "full"])
@pytest.mark.parametrize("status", ["in_flight", "accounting_error", "unknown", "completed"])
def test_completion_rejects_unfinished_work_but_preserves_transport_retry_policy(
    monkeypatch, tmp_path, mode, status,
):
    original_run = po.PipelineOrchestrator._run
    ledger_path = str(tmp_path / "completion-operations.sqlite3")
    observed = []

    def final_stage_hook(self, state, *args):
        LLMMeter.attach_durable_run(state.pipeline_id, ledger_path, "final-stage")
        _marker(UsageLedger(ledger_path), state.pipeline_id, "final-stage", status)
        observed.append("valid_artifact_retained")

    def run_with_final_hook(cls, state):
        state.mode = mode
        target = "_surface_forecast_confidence_penalty" if mode == "research_only" else "_enforce_pipeline_health"
        monkeypatch.setattr(cls, target, final_stage_hook)
        return original_run(state)

    monkeypatch.setattr(po.PipelineOrchestrator, "_run", classmethod(run_with_final_hook))
    result = _exercise_prepare_run_resume(monkeypatch, tmp_path, rebuild_prepare=False)
    assert observed == ["valid_artifact_retained"]
    assert result.state.status == ("failed" if status in {"in_flight", "accounting_error"} else "completed")
    assert not LLMMeter.is_durable_run(result.state.pipeline_id)
