"""Golden tests for structured forecast extraction + citation audit (EXECPLAN2 I-3-0/I-3-1)."""

from app.services.forecast_extractor import (
    audit_citation_grounding,
    derive_forecast_spine,
    extract_structured_forecast,
    render_forecast_spine_block,
    render_resolution_block,
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


# ----------------------------------------------- NEXTSTEPS P0-1: forecast spine
def test_derive_forecast_spine_from_signals_not_prose():
    fake = FakeLLMClient(json_responses=[{
        "headline": "X",
        "horizon": "2030",
        "scenarios": [
            {"name": "情景A", "probability": 2, "resolution_criteria": "到2030 A>50%"},
            {"name": "维持现状", "probability": 2, "resolution_criteria": "无变化"},
        ],
        "confidence": "high",
    }])
    out = derive_forecast_spine(
        fake, central_question="谁会赢", horizon="2030",
        situation_brief="某态势简报",
        forecast_inputs="参考类基率: 历史延续率 70%", signal_pack="Top actor: NVIDIA",
    )
    assert out["derived_from"] == "spine"
    probs = [s["probability"] for s in out["scenarios"]]
    assert abs(sum(probs) - 1.0) < 1e-6 and probs == [0.5, 0.5]
    # the spine prompt is seeded by SIGNALS (inputs + question), not finished prose
    msg = fake.calls[0]["messages"][0]["content"]
    assert "参考类基率" in msg and "Top actor" in msg and "谁会赢" in msg


def test_render_forecast_spine_block_empty_when_no_scenarios():
    assert render_forecast_spine_block(None) == ""
    assert render_forecast_spine_block({"scenarios": []}) == ""


def test_render_forecast_spine_block_lists_scenarios_with_pct():
    block = render_forecast_spine_block({
        "headline": "核心结论X", "horizon": "2030", "confidence": "medium",
        "scenarios": [
            {"name": "情景A", "probability": 0.6, "resolution_criteria": "A>50%"},
            {"name": "维持现状", "probability": 0.4, "resolution_criteria": "无变化"},
        ],
    })
    assert "预测骨架" in block
    assert "60%" in block and "情景A" in block
    assert "40%" in block and "维持现状" in block
    assert "判定" in block


# ------------------------------------------ NEXTSTEPS P2-2: resolution section
def test_render_resolution_block_lists_criteria_and_indicators():
    fc = {"scenarios": [
        {"name": "情景A", "probability": 0.6, "resolution_criteria": "2030 A 份额>50%"},
        {"name": "维持现状", "probability": 0.4, "resolution_criteria": "无重大变化"},
    ]}
    inds = [{"indicator": "DRAM 份额", "date_or_trigger": "2030Q4", "discriminates": "情景A"}]
    block = render_resolution_block(fc, inds)
    assert "如何验证本预测" in block
    assert "情景A" in block and "2030 A 份额>50%" in block
    assert "DRAM 份额" in block and "2030Q4" in block          # indicator table


def test_render_resolution_block_empty_without_scenarios():
    assert render_resolution_block(None) == ""
    assert render_resolution_block({"scenarios": []}) == ""
