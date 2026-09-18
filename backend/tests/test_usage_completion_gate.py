"""A swallowed final accounting failure cannot publish a completed pipeline."""

from pathlib import Path
import sqlite3

import pytest

from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter
from app.utils.usage_ledger import UsageLedger, UsageLedgerStorageError
from test_orchestrator_research_wiring import _exercise_prepare_run_resume


@pytest.mark.parametrize("mode", ["research_only", "full"])
@pytest.mark.parametrize("failed_write", [False, True])
def test_final_accounting_gate_survives_upstream_error_swallow(
    monkeypatch, tmp_path, mode, failed_write,
):
    """Reuse the offline state-machine fixture and fail after all paid work.

    The last stage hook mimics a consumer retaining valid output while catching
    a failed usage write. Storage is readable again before completion, and no
    later provider call occurs to rediscover the sticky accounting stop.
    """
    original_run = po.PipelineOrchestrator._run
    observed = []
    ledger_path = str(tmp_path / "completion-usage.sqlite3")

    def final_stage_hook(self, state, *args):
        LLMMeter.attach_durable_run(state.pipeline_id, ledger_path, "final-stage")

        def fail_delta(*args, **kwargs):
            raise sqlite3.OperationalError("offline final usage write failed")

        with monkeypatch.context() as scoped:
            if failed_write:
                scoped.setattr(UsageLedger, "_insert_delta", staticmethod(fail_delta))
            try:
                LLMMeter.record("minimax", "offline", 10, 5, 1,
                                run_id=state.pipeline_id, stage=mode)
            except UsageLedgerStorageError:
                # An optional stage wrapper can catch the error and keep an
                # otherwise valid artifact; completion must still reject it.
                observed.append("accounting_error_swallowed")
        assert UsageLedger(ledger_path).snapshot(state.pipeline_id)["total"]["total_tokens"] == (
            0 if failed_write else 15
        )
        observed.append("final_stage_returned")

    def run_with_final_hook(cls, state):
        state.mode = mode
        target = "_surface_forecast_confidence_penalty" if mode == "research_only" else "_enforce_pipeline_health"
        monkeypatch.setattr(cls, target, final_stage_hook)
        return original_run(state)

    monkeypatch.setattr(po.PipelineOrchestrator, "_run", classmethod(run_with_final_hook))
    result = _exercise_prepare_run_resume(monkeypatch, tmp_path, rebuild_prepare=False)

    assert "final_stage_returned" in observed
    assert result.state.status == ("failed" if failed_write else "completed")
    if failed_write:
        assert observed == ["accounting_error_swallowed", "final_stage_returned"]
        assert "Durable accounting failed" in result.state.error
    assert not LLMMeter.is_durable_run(result.state.pipeline_id)
    assert Path(ledger_path).is_file()
