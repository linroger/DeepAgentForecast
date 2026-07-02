"""NEXTSTEPS P0-3: seed-ensemble pure helpers (offline)."""

from app.services.pipeline_orchestrator import PipelineOrchestrator


def test_agreement_to_confidence_buckets():
    f = PipelineOrchestrator._agreement_to_confidence
    assert f(0.95) == "high"
    assert f(0.75) == "high"          # boundary inclusive
    assert f(0.6) == "medium"
    assert f(0.45) == "medium"        # boundary inclusive
    assert f(0.2) == "low"
    assert f(None) == "medium"        # invalid → safe default
    assert f("nope") == "medium"


def test_read_report_forecast_missing_returns_none():
    assert PipelineOrchestrator._read_report_forecast(None) is None
    assert PipelineOrchestrator._read_report_forecast("report_does_not_exist_xyz") is None
