"""Tests for the qualitative world-delta digest (spec §4 / §7 item 5).

Pins: determinism, section order, char_cap truncation, the herding-guard regression
(NO percentage / numeric-share tokens ever in agent-facing output), and the
""-on-error / ""-on-empty contract.
"""

import re

from app.services.world_delta import build_world_delta


def _events():
    return [
        {"date": "2027-03-15", "content": "Regulator opens formal inquiry"},
        {"date": "2027-03-20", "content": "Flagship model ships"},
    ]


def _actions():
    return [
        {"actor_name": "Alice", "influence_weight": 0.9, "content": "We commit to the merger."},
        {"actor_name": "Bob", "influence_weight": 0.4, "content": "We will wait and see."},
        {"actor_name": "Carol", "influence_weight": 0.7, "content": "Alliance announced with Dana."},
    ]


# ------------------------------------------------------------------ determinism
def test_deterministic_and_section_order():
    a = build_world_delta(_actions(), _events(),
                          leader_move={"leader": "ScenarioA", "direction": "up"})
    b = build_world_delta(_actions(), _events(),
                          leader_move={"leader": "ScenarioA", "direction": "up"})
    assert a == b and a
    lines = a.split("\n")
    # 事件区在前（[date] content），其后按 influence_weight 降序的有机贴，最后 momentum
    assert lines[0] == "[2027-03-15] Regulator opens formal inquiry"
    assert lines[1] == "[2027-03-20] Flagship model ships"
    assert lines[2].startswith("Alice:")
    assert lines[3].startswith("Carol:")
    assert lines[4].startswith("Bob:")
    assert lines[-1] == "Momentum: ScenarioA strengthened this period."


def test_momentum_directions_and_invalid():
    down = build_world_delta([], _events(), leader_move={"leader": "X", "direction": "down"})
    assert "Momentum: X weakened this period." in down
    flat = build_world_delta([], _events(), leader_move={"leader": "X", "direction": "flat"})
    assert "Momentum: X held this period." in flat
    bad = build_world_delta([], _events(), leader_move={"leader": "X", "direction": "sideways"})
    assert "Momentum" not in bad


def test_top_k_limits_posts():
    actions = [{"actor_name": f"a{i}", "influence_weight": i / 10.0, "content": f"post {i}"}
               for i in range(8)]
    out = build_world_delta(actions, [], top_k=3)
    assert out.count("\n") == 2                      # exactly 3 lines
    assert out.split("\n")[0].startswith("a7:")      # highest weight first


def test_content_clipped_to_140_chars():
    long = "x" * 500
    out = build_world_delta([{"actor_name": "A", "influence_weight": 1.0, "content": long}], [])
    assert out == "A: " + "x" * 140


# --------------------------------------------------------------------- char cap
def test_char_cap_truncation():
    actions = [{"actor_name": f"agent{i}", "influence_weight": 1.0, "content": "y" * 140}
               for i in range(5)]
    out = build_world_delta(actions, _events(), char_cap=200)
    assert len(out) <= 200
    full = build_world_delta(actions, _events(), char_cap=100000)
    assert full.startswith(out)                      # truncation is a plain prefix cut


# ------------------------------------- herding-guard regression (no share tokens)
def test_no_percentage_or_share_tokens_in_output():
    """Even when leader_move smuggles numeric shares, none reach agent-facing text."""
    leader_move = {"leader": "ScenarioA", "direction": "up",
                   "share": 0.62, "leader_share": 0.62, "delta": 0.07, "pct": "62%"}
    actions = [{"actor_name": "Alice", "influence_weight": 0.9,
                "content": "We commit fully to the plan."}]
    events = [{"date": "2027-03-15", "content": "Regulator opens formal inquiry"}]
    out = build_world_delta(actions, events, leader_move=leader_move)
    assert out
    assert "%" not in out
    assert not re.search(r"\d+(\.\d+)?\s*%", out)
    assert not re.search(r"(?<!\d)0\.\d+", out)      # no float-style shares like 0.62
    # momentum 行必须是纯定性模板，不含任何数字
    momentum = [ln for ln in out.split("\n") if ln.startswith("Momentum")]
    assert momentum == ["Momentum: ScenarioA strengthened this period."]
    assert not re.search(r"\d", momentum[0])


# ------------------------------------------------------------- empty / error → ""
def test_empty_inputs_return_empty_string():
    assert build_world_delta([], []) == ""
    assert build_world_delta(None, None) == ""
    assert build_world_delta([], [], leader_move={}) == ""
    assert build_world_delta([], [], leader_move={"leader": "", "direction": "up"}) == ""


def test_garbage_inputs_return_empty_string():
    assert build_world_delta(object(), 42) == ""                      # non-iterables → ""
    assert build_world_delta([1, "x", None], [3.5, None]) == ""       # non-dict items skipped
    assert build_world_delta([{"content": ""}], [{"content": None}]) == ""


def test_event_date_prefix_not_duplicated():
    ev = [{"date": "2027-03-15", "content": "[2027-03-15] Already prefixed event"}]
    out = build_world_delta([], ev)
    assert out == "[2027-03-15] Already prefixed event"
