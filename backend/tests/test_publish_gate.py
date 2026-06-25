"""NEXTSTEPS P2-3: forecast publish-gate (coherence + grounding) tests.

ReportAgent._apply_publish_gate is a pure staticmethod, so we test it directly
without constructing a (heavy) ReportAgent.
"""

from app.services.report_agent import ReportAgent


def _gate(forecast):
    return ReportAgent._apply_publish_gate(dict(forecast))


def test_publish_gate_passes_coherent_grounded_forecast():
    f = _gate({
        "confidence": "high",
        "citation_audit": {"coverage": 0.9},
        "scenarios": [
            {"name": "情景A", "probability": 0.6},
            {"name": "维持现状", "probability": 0.4},
        ],
    })
    assert f["quality"]["passed"] is True
    assert f["confidence"] == "high"  # not demoted


def test_publish_gate_demotes_on_low_coverage():
    f = _gate({
        "confidence": "high",
        "citation_audit": {"coverage": 0.1},
        "scenarios": [
            {"name": "情景A", "probability": 0.6},
            {"name": "维持现状", "probability": 0.4},
        ],
    })
    assert f["quality"]["passed"] is False
    assert f["confidence"] == "medium"  # demoted exactly one level
    assert any("覆盖率" in i for i in f["quality"]["issues"])


def test_publish_gate_flags_missing_residual_and_bad_sum():
    f = _gate({
        "confidence": "medium",
        "citation_audit": {"coverage": 1.0},
        "scenarios": [{"name": "情景A", "probability": 0.6}],  # sum<1, no residual
    })
    issues = f["quality"]["issues"]
    assert any("兜底" in i for i in issues)
    assert any("偏离 1" in i for i in issues)
    assert f["confidence"] == "low"


def test_publish_gate_no_scenarios_is_vacuously_passing():
    f = _gate({"confidence": "medium", "scenarios": []})
    assert f["quality"]["passed"] is True
    assert f["confidence"] == "medium"
