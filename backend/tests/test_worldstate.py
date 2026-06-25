"""NEXTSTEPS P1-1: pure WorldState outcome-model tests (offline, no LLM/oasis)."""

from app.services.worldstate import WorldState, commitments_from_decisions


def test_seed_from_base_rates_normalizes():
    ws = WorldState(["A", "B"], base_rates={"A": 0.7, "B": 0.3})
    assert ws.shares == {"A": 0.7, "B": 0.3}


def test_seed_uniform_when_no_base_rates():
    ws = WorldState(["A", "B", "C"])
    third = round(1 / 3, 6)
    assert ws.shares == {"A": third, "B": third, "C": third}


def test_step_moves_toward_committed_scenario():
    ws = WorldState(["A", "B"], base_rates={"A": 0.5, "B": 0.5}, inertia=0.5)
    ws.step([{"scenario": "A", "magnitude": 1.0, "weight": 1.0}])
    assert ws.shares["A"] > 0.5 and ws.shares["B"] < 0.5
    assert abs(sum(ws.shares.values()) - 1.0) < 1e-6


def test_resource_weighting_dominates():
    ws = WorldState(["A", "B"], base_rates={"A": 0.5, "B": 0.5}, inertia=0.0)
    ws.step([
        {"scenario": "A", "magnitude": 1.0, "weight": 1.0},
        {"scenario": "B", "magnitude": 1.0, "weight": 9.0},   # 9x resource on B
    ])
    assert ws.shares["B"] == 0.9 and ws.shares["A"] == 0.1


def test_no_commitments_leaves_state_unchanged():
    ws = WorldState(["A", "B"], base_rates={"A": 0.6, "B": 0.4})
    before = dict(ws.shares)
    ws.step([])
    assert ws.shares == before


def test_convergence_detected_when_stable():
    ws = WorldState(["A", "B"], base_rates={"A": 0.5, "B": 0.5}, inertia=0.9)
    for _ in range(40):  # commit to the SAME equilibrium each round → deltas shrink
        ws.step([{"scenario": "A", "magnitude": 1.0, "weight": 1.0},
                 {"scenario": "B", "magnitude": 1.0, "weight": 1.0}])
    assert ws.converged(eps=0.02)
    out = ws.outcome()
    assert out["converged"] is True and out["rounds"] == 40


def test_commitments_from_decisions_weights_by_resource_and_confidence():
    decisions = [
        {"agent_id": 1, "scenario": "A", "magnitude": 1.0, "confidence": 0.5},
        {"agent_id": 2, "scenario": "B", "magnitude": 2.0, "confidence": 1.0},
        {"agent_id": 3, "scenario": "", "magnitude": 1.0},   # no scenario → dropped
    ]
    cs = commitments_from_decisions(decisions, {1: 4.0, 2: 1.0})
    assert len(cs) == 2
    a = [c for c in cs if c["scenario"] == "A"][0]
    b = [c for c in cs if c["scenario"] == "B"][0]
    assert a["weight"] == 4.0 * 0.5      # resource 4 × confidence 0.5
    assert b["weight"] == 1.0 * 1.0


def test_outcome_reports_leader():
    out = WorldState(["A", "B"], base_rates={"A": 0.8, "B": 0.2}).outcome()
    assert out["leader"] == "A" and out["leader_share"] == 0.8


def test_empty_scenarios_safe():
    ws = WorldState([])
    assert ws.step([{"scenario": "A", "magnitude": 1}]) == {}
    assert ws.outcome()["leader"] is None


# ----------------------------- WorldState seed extraction from forecast_inputs
def test_world_state_seed_from_actors():
    from app.utils.actors import world_state_seed_from_actors
    actors = {"forecast_inputs": {"scenarios": [
        {"name": "NVIDIA holds", "probability": 0.6},
        {"name": "ASICs erode", "probability": 0.3},
        {"name": "维持现状"},   # no probability → name kept, omitted from base_rates
    ]}}
    seed = world_state_seed_from_actors(actors)
    assert seed["scenarios"] == ["NVIDIA holds", "ASICs erode", "维持现状"]
    assert seed["base_rates"]["NVIDIA holds"] == 0.6
    assert "维持现状" not in seed["base_rates"]
    # seed feeds WorldState directly → base-rate leader
    ws = WorldState(seed["scenarios"], seed["base_rates"])
    assert ws.outcome()["leader"] == "NVIDIA holds"


def test_world_state_seed_empty_without_scenarios():
    from app.utils.actors import world_state_seed_from_actors
    assert world_state_seed_from_actors(None) == {}
    assert world_state_seed_from_actors({"actors": []}) == {}
