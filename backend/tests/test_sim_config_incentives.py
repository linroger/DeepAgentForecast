"""R2-SIM-3: actor incentives → AgentActivityConfig (offline, no LLM/oasis)."""

from dataclasses import asdict

from app.services.simulation_config_generator import (
    AgentActivityConfig,
    SimulationConfigGenerator,
)


def test_extract_actor_incentive_summary():
    actor = {"incentives": [
        {"driver": "市场份额", "gains_if": "稳住定价", "loses_if": "丢失客户"},
        {"gains_if": "扩产", "loses_if": ""},
        {"gains_if": "稳住定价"},   # duplicate gains → deduped
    ]}
    gains, loses = SimulationConfigGenerator._extract_actor_incentive_summary(actor)
    assert gains == "稳住定价；扩产"
    assert loses == "丢失客户"


def test_extract_incentive_summary_degrades_on_missing():
    assert SimulationConfigGenerator._extract_actor_incentive_summary(None) == ("", "")
    assert SimulationConfigGenerator._extract_actor_incentive_summary({}) == ("", "")
    assert SimulationConfigGenerator._extract_actor_incentive_summary(
        {"incentives": "not-a-list"}) == ("", "")


def test_agent_activity_config_serializes_incentives():
    c = AgentActivityConfig(agent_id=1, entity_uuid="u", entity_name="X",
                            entity_type="Org", gains_if="稳价", loses_if="失份额")
    d = asdict(c)
    assert d["gains_if"] == "稳价" and d["loses_if"] == "失份额"
    # default (no incentives) stays empty so old dossiers are byte-identical
    d0 = asdict(AgentActivityConfig(agent_id=2, entity_uuid="u", entity_name="Y",
                                    entity_type="Org"))
    assert d0["gains_if"] == "" and d0["loses_if"] == ""


def test_synthesize_initial_posts_from_dossier():
    """Seed-content fallback: empty LLM initial_posts → posts synthesized from actor stances.

    Root-cause fix for the hollow sim (empty feed → agents only FOLLOW → 0 organic posts).
    """
    g = SimulationConfigGenerator.__new__(SimulationConfigGenerator)
    actors = {
        "actors": [
            {"name": "Alpha Gov", "type": "Government", "stance": "Tariffs are leverage.",
             "influence": "high", "simulation_tier": "1"},
            {"name": "Beta Corp", "type": "Organization", "stance": "Open trade wins.",
             "influence": "medium", "simulation_tier": "2"},
            {"name": "NoStance", "type": "Organization", "stance": "", "influence": "high"},
        ],
    }
    posts = SimulationConfigGenerator._synthesize_initial_posts(
        g, actors, ["tariff durability"])
    assert len(posts) == 2  # NoStance dropped (no stance)
    # high-influence/tier-1 actor leads
    assert posts[0]["poster_name"] == "Alpha Gov"
    assert posts[0]["poster_type"] == "Government"
    assert all(p.get("content") and p.get("poster_type") and p.get("poster_name") for p in posts)
    assert "tariff durability" in posts[0]["content"]


def test_synthesize_initial_posts_degrades_on_missing():
    g = SimulationConfigGenerator.__new__(SimulationConfigGenerator)
    assert SimulationConfigGenerator._synthesize_initial_posts(g, None, []) == []
    assert SimulationConfigGenerator._synthesize_initial_posts(g, {}, []) == []
    assert SimulationConfigGenerator._synthesize_initial_posts(
        g, {"actors": "not-a-list"}, []) == []
