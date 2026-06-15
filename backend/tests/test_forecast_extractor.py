"""Golden tests for structured forecast extraction + citation audit (EXECPLAN2 I-3-0/I-3-1)."""

from app.services.forecast_extractor import (
    audit_citation_grounding,
    extract_structured_forecast,
)
from tests.conftest import FakeLLMClient


def test_audit_citation_grounding_counts_and_coverage():
    md = (
        "# 标题\n"
        "市场份额将达到 45% [S1]。\n"          # cited quantitative
        "营收增长 30%。\n"                      # uncited quantitative
        "到 2030年 格局重塑【S3】。\n"          # cited (CJK marker + year)
        "这是一句没有数字的话。\n"             # not quantitative
    )
    a = audit_citation_grounding(md)
    assert a["quantitative_claims"] == 3
    assert a["cited"] == 2
    assert a["coverage"] == round(2 / 3, 3)
    assert any("营收增长 30%" in s for s in a["unsupported_samples"])


def test_audit_empty_is_full_coverage():
    assert audit_citation_grounding("no numbers here")["coverage"] == 1.0


def test_extract_structured_forecast_normalizes_probabilities():
    fake = FakeLLMClient(json_responses=[{
        "headline": "存储芯片三强争霸",
        "horizon": "2030",
        "scenarios": [
            {"name": "三星领先", "probability": 3, "summary": "...", "key_drivers": ["HBM"],
             "resolution_criteria": "2030 年三星 DRAM 份额>40%"},
            {"name": "格局僵持", "probability": 1, "summary": "...", "key_drivers": [],
             "resolution_criteria": "无单一厂商>40%"},
        ],
        "key_uncertainties": ["出口管制"],
        "confidence": "medium",
    }])
    out = extract_structured_forecast("some report markdown", fake)
    probs = [s["probability"] for s in out["scenarios"]]
    assert abs(sum(probs) - 1.0) < 1e-6      # normalized to sum 1
    assert probs[0] == 0.75 and probs[1] == 0.25
    assert out["schema_version"] == 1
    assert out["confidence"] == "medium"
    assert fake.calls and fake.calls[0]["kind"] == "chat_json"


def test_extract_handles_garbage_reply():
    fake = FakeLLMClient(json_responses=[{"nonsense": True}])
    out = extract_structured_forecast("x", fake)
    assert out["scenarios"] == []
    assert out["confidence"] == "medium"  # safe default
