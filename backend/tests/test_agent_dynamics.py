"""Offline unit tests for per-agent affective-state dynamics (EXECPLAN2 I-2-1).

Exercises the pure update math, state rendering, config seeding, and per-round
signal extraction — no camel/oasis/DB.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.agent_dynamics import (  # noqa: E402
    AgentDynamicsTracker,
    extract_round_signals,
)


# ----------------------------------------------------------- extract_round_signals
def test_extract_signals_pos_neg_engage_and_activity():
    name_to_id = {"Alice": 1, "Bob": 2}
    actions = [
        {"agent_id": 2, "action_type": "LIKE_POST", "action_args": {"post_author_name": "Alice"}},
        {"agent_id": 3, "action_type": "DISLIKE_POST", "action_args": {"post_author_name": "Alice"}},
        {"agent_id": 2, "action_type": "CREATE_COMMENT", "action_args": {"comment_author_name": "Bob"}},
        {"agent_id": 1, "action_type": "CREATE_POST", "action_args": {}},  # no target
    ]
    received, activity = extract_round_signals(actions, name_to_id)
    assert received[1] == {"pos": 1, "neg": 1, "engage": 0}   # Alice: +like, -dislike
    assert received[2] == {"pos": 0, "neg": 0, "engage": 1}   # Bob: a comment
    assert activity == {2: 2, 3: 1, 1: 1}                      # actions taken per agent


def test_extract_signals_unresolved_target_skipped():
    received, _ = extract_round_signals(
        [{"agent_id": 1, "action_type": "LIKE_POST", "action_args": {"post_author_name": "Ghost"}}],
        {"Alice": 1},
    )
    assert received == {}  # 'Ghost' not in name_to_id → no received credit


def test_extract_signals_empty():
    assert extract_round_signals([], {}) == ({}, {})
    assert extract_round_signals(None, None) == ({}, {})


# -------------------------------------------------------------------- from_config
def test_from_config_seeds_state_and_reads_constants():
    cfg_obj = types.SimpleNamespace(
        SIM_DYNAMICS_MOOD_LR=0.3, SIM_DYNAMICS_OPINION_LR=0.2,
        SIM_DYNAMICS_FATIGUE_RATE=0.4, SIM_DYNAMICS_FATIGUE_DECAY=0.05,
    )
    configs = [
        {"agent_id": 1, "sentiment_bias": 0.5, "influence": 0.8},
        {"agent_id": 2, "sentiment_bias": -0.9},
        {"not_an_agent": True},  # skipped (no agent_id)
    ]
    t = AgentDynamicsTracker.from_config(configs, config_obj=cfg_obj)
    assert t.mood_lr == 0.3 and t.fatigue_rate == 0.4
    assert t.get_state(1)["mood"] == 0.5
    assert t.get_state(1)["opinion_strength"] == 0.8
    assert t.get_state(2)["mood"] == -0.9
    assert t.get_state(2)["opinion_strength"] == 0.3   # default when no influence
    assert t.get_state(99) == {}                        # unknown agent


# -------------------------------------------------------------------- observe_round
def test_observe_mood_follows_net_valence_and_clamps():
    t = AgentDynamicsTracker([1], mood_lr=0.5, opinion_lr=0.1, fatigue_rate=0.2, fatigue_decay=0.1)
    # heavy positive reception → mood rises but stays <= 1
    for _ in range(20):
        t.observe_round({1: {"pos": 10, "neg": 0, "engage": 0}}, {1: 1})
    assert 0.0 < t.get_state(1)["mood"] <= 1.0
    # now heavy negative → mood drops, stays >= -1
    for _ in range(40):
        t.observe_round({1: {"pos": 0, "neg": 10, "engage": 0}}, {1: 1})
    assert -1.0 <= t.get_state(1)["mood"] < 0.0


def test_observe_engagement_hardens_opinion():
    t = AgentDynamicsTracker([1], opinion_lr=0.3)
    before = t.get_state(1)["opinion_strength"]
    t.observe_round({1: {"pos": 0, "neg": 0, "engage": 5}}, {1: 1})
    assert t.get_state(1)["opinion_strength"] > before  # attention hardens stance
    assert t.get_state(1)["opinion_strength"] <= 1.0


def test_observe_fatigue_accrues_and_decays_energy_complement():
    t = AgentDynamicsTracker([1], fatigue_rate=0.5, fatigue_decay=0.1)
    t.observe_round({}, {1: 3})        # acted → fatigue up
    f1 = t.get_state(1)["fatigue"]
    assert f1 > 0.0
    assert abs(t.get_state(1)["energy"] - (1.0 - f1)) < 1e-9
    # idle round → fatigue decays
    t.observe_round({}, {})            # did not act
    assert t.get_state(1)["fatigue"] < f1


# ---------------------------------------------------------------------- state_line
def test_state_line_neutral_is_empty():
    t = AgentDynamicsTracker([1])  # fresh: mood 0, opinion 0.3, fatigue 0
    assert t.state_line(1) == ""
    assert t.state_line(999) == ""   # unknown agent


def test_state_line_reflects_excitement_opinion_fatigue():
    t = AgentDynamicsTracker([1])
    t._state[1] = {"mood": 0.8, "energy": 0.2, "opinion_strength": 0.9, "fatigue": 0.8}
    line = t.state_line(1)
    assert "亢奋" in line or "激动" in line
    assert "立场已明显强化" in line
    assert "疲惫" in line
    # low mood
    t._state[1] = {"mood": -0.7, "energy": 1.0, "opinion_strength": 0.3, "fatigue": 0.0}
    assert "低落" in t.state_line(1) or "愤懑" in t.state_line(1)
