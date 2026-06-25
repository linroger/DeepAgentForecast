"""NEXTSTEPS P1-1 integration: decision-channel orchestration (offline, fake LLM)."""

from app.services.decision_channel import _elicit_round_decisions, run_decision_channel
from tests.conftest import FakeLLMClient


def test_run_decision_channel_evolves_outcome():
    actions = [
        {"round": 1, "agent_id": 1, "agent_name": "A"},
        {"round": 1, "agent_id": 2, "agent_name": "B"},
        {"round": 2, "agent_id": 1, "agent_name": "A"},
    ]
    agent_configs = [
        {"agent_id": 1, "entity_name": "A", "stance": "pro", "influence_weight": 3.0},
        {"agent_id": 2, "entity_name": "B", "stance": "con", "influence_weight": 1.0},
    ]
    seed = {"scenarios": ["S1", "S2"], "base_rates": {"S1": 0.5, "S2": 0.5}}
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1},
                       {"agent_id": 2, "scenario": "S1", "magnitude": 1, "confidence": 1}]},
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1}]},
    ])
    res = run_decision_channel(actions, agent_configs, seed, fake, inertia=0.5)
    assert res["outcome"]["leader"] == "S1"         # evolved toward the committed scenario
    assert res["outcome"]["shares"]["S1"] > 0.5
    assert res["n_rounds"] == 2
    assert len(res["trajectory"]) == 3              # round 0 (seed) + rounds 1, 2
    assert len(fake.calls) == 2                      # exactly one batched call per round


def test_decision_channel_empty_seed_is_noop():
    assert run_decision_channel([], None, {}, FakeLLMClient()) == {}
    assert run_decision_channel([], None, {"scenarios": []}, FakeLLMClient()) == {}


def test_elicit_round_filters_invalid_scenarios():
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1},
                       {"agent_id": 2, "scenario": "BOGUS", "magnitude": 1, "confidence": 1}]},
    ])
    out = _elicit_round_decisions(fake, ["S1", "S2"], [{"agent_id": 1}, {"agent_id": 2}], 1)
    assert len(out) == 1 and out[0]["scenario"] == "S1"   # invalid scenario dropped


def test_round_to_date_stamps_trajectory():
    actions = [{"round": 1, "agent_id": 1, "agent_name": "A"}]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1}]}])
    res = run_decision_channel(actions, [{"agent_id": 1}], {"scenarios": ["S1", "S2"]}, fake,
                               round_to_date=lambda r: f"2027-{r:02d}-01")
    snap = [t for t in res["trajectory"] if t["round"] == 1][0]
    assert snap["as_of"] == "2027-01-01"             # P1-2: round stamped with mapped date
