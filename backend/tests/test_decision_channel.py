"""NEXTSTEPS P1-1 integration: decision-channel orchestration (offline, fake LLM)."""

from app.services import decision_channel as dc
from app.services.decision_channel import (
    PUBLIC_BLOCK_ID,
    _activation_weight_map,
    _build_active_roster,
    _build_round_decision_prompt,
    _elicit_round_decisions,
    _inertia_for_gap,
    _outcome_power_map,
    run_decision_channel,
)
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
    out, status = _elicit_round_decisions(
        fake, ["S1", "S2"], [{"agent_id": 1}, {"agent_id": 2}], 1)
    assert len(out) == 1 and out[0]["scenario"] == "S1"   # invalid scenario dropped
    assert status == "committed"                          # Foglamp 1C: typed round status


def test_round_to_date_stamps_trajectory():
    actions = [{"round": 1, "agent_id": 1, "agent_name": "A"}]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1}]}])
    res = run_decision_channel(actions, [{"agent_id": 1}], {"scenarios": ["S1", "S2"]}, fake,
                               round_to_date=lambda r: f"2027-{r:02d}-01")
    snap = [t for t in res["trajectory"] if t["round"] == 1][0]
    assert snap["as_of"] == "2027-01-01"             # P1-2: round stamped with mapped date


# --------------------------------------------------------------- SIM-5 / R2-EXEC-10
def test_power_sort_and_public_block_collapse():
    """SIM-5 + R2-EXEC-10: top-``cap`` by activation kept individually; the tail is
    collapsed into ONE public block whose outcome power sums the tail (never dropped)."""
    entries = [{"agent_id": 1, "influence": 1.0}, {"agent_id": 2, "influence": 5.0},
               {"agent_id": 3, "influence": 2.0}]
    act = {1: 1.0, 2: 5.0, 3: 2.0}
    roster = _build_active_roster(entries, act, act, cap=1)
    assert len(roster) == 2
    assert roster[0]["agent_id"] == 2                 # loudest kept individually
    assert roster[1]["agent_id"] == PUBLIC_BLOCK_ID
    assert roster[1]["outcome_power"] == 1.0 + 2.0    # tail power aggregated, not lost


def test_roster_cache_dedupes_identical_rosters():
    """R2-EXEC-10: a stable roster across rounds reuses one elicitation (one LLM call)."""
    actions = [{"round": 1, "agent_id": 1}, {"round": 2, "agent_id": 1},
               {"round": 3, "agent_id": 1}]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1}]}])
    res = run_decision_channel(actions, [{"agent_id": 1, "influence_weight": 1.0}],
                               {"scenarios": ["S1", "S2"]}, fake)
    assert len(fake.calls) == 1                       # 3 identical rosters → 1 cached call
    assert res["n_rounds"] == 3 and len(res["trajectory"]) == 4


# ------------------------------------------------------------ R2-SIM-2 + Foglamp I-15
def test_outcome_power_distinct_from_influence():
    """Foglamp WP1 (I-15): visibility never defaults to outcome power. An actor
    without an explicit ``outcome_power`` is UNKNOWN (absent from the power map)
    and receives a declared-neutral 1.0 in the roster — never its
    ``influence_weight``. The pre-containment fallback (unknown → influence 5.0)
    let media prominence become institutional power by construction."""
    cfgs = [{"agent_id": 1, "influence_weight": 1.0, "outcome_power": 9.0},
            {"agent_id": 2, "influence_weight": 5.0}]   # no outcome_power → UNKNOWN
    assert _activation_weight_map(cfgs) == {1: 1.0, 2: 5.0}
    assert _outcome_power_map(cfgs) == {1: 9.0}          # I-15: no visibility fallback
    roster = _build_active_roster(
        [{"agent_id": 1, "influence": 1.0}, {"agent_id": 2, "influence": 5.0}],
        _activation_weight_map(cfgs), _outcome_power_map(cfgs), cap=10)
    by_id = {e["agent_id"]: e for e in roster}
    assert by_id[1]["outcome_power"] == 9.0 and by_id[1]["outcome_power_known"] is True
    assert by_id[2]["outcome_power"] == 1.0 and by_id[2]["outcome_power_known"] is False


# ------------------------------------------------------------------------ R2-SIM-1/3
def test_prompt_injects_base_shares_incentives_and_abstention():
    active = [{"agent_id": 1, "name": "A", "stance": "pro",
               "gains_if": "稳价", "loses_if": "失份额", "affect": "情绪亢奋",
               "post": "我支持"}]
    prompt = _build_round_decision_prompt(["S1", "S2"], active, 1, None,
                                          base_shares={"S1": 0.6, "S2": 0.4})
    assert "基线分布" in prompt and "S1=60%" in prompt   # R2-SIM-1 base distribution
    assert "稳价" in prompt and "失份额" in prompt        # R2-SIM-3 incentives
    assert "情绪亢奋" in prompt and "我支持" in prompt     # R2-SIM-1 affect + post
    assert dc.ABSTAIN_TOKEN in prompt                    # abstention offered
    p2 = _build_round_decision_prompt(["S1", "S2"], active, 1, None, abstain_allowed=False)
    assert dc.ABSTAIN_TOKEN not in p2


# ------------------------------------------------------------------------ R2-SIM-12
def test_calendar_scaled_inertia():
    assert _inertia_for_gap(0.7, None, None, 10) is None            # no dates → base
    assert abs(_inertia_for_gap(0.7, "2027-01-01", "2027-01-11", 10) - 0.7) < 1e-9
    assert abs(_inertia_for_gap(0.7, "2027-01-01", "2027-01-21", 10) - 0.7 ** 2) < 1e-9
    assert _inertia_for_gap(0.7, "2027-01-01", "2027-01-02", 10) == 0.95  # clamp high


# --------------------------------------------------------------------------- SIM-1
def test_windowed_convergence(monkeypatch):
    """SIM-1: convergence requires SIM_CONVERGENCE_WINDOW stable rounds before settling."""
    from app import config as cfgmod
    monkeypatch.setattr(cfgmod.Config, "SIM_CONVERGENCE_WINDOW", 3, raising=False)
    actions = [{"round": r, "agent_id": 1} for r in range(1, 8)]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "S1", "magnitude": 1, "confidence": 1}]}])
    res = run_decision_channel(actions, [{"agent_id": 1, "influence_weight": 1.0}],
                               {"scenarios": ["S1", "S2"], "base_rates": {"S1": 0.5, "S2": 0.5}},
                               fake, inertia=0.5)
    ca = res["converged_at"]
    assert ca is None or ca >= 3                        # never before the window fills
