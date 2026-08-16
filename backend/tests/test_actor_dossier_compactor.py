from app.services.actor_dossier_compactor import render_compact_actor_dossier


def _actor(name, score, tier=1):
    return {
        "name": name,
        "type": "Organization",
        "role": f"{name} role " * 20,
        "simulation_tier": tier,
        "salience": {"score": score},
        "goals": ["g1", "g1", "g2", "g3", "g4", "g5"],
        "constraints": ["c1", "c2"],
    }


def test_compactor_centres_key_cast_and_prunes_unrelated_sprawl():
    actors = {
        "central_question": "Who wins?",
        "actors": [_actor("Core A", .9), _actor("Core B", .8), _actor("Low", .1, 3)],
        "relationships": [
            {"source": "Core A", "target": "Core B", "type": "COMPETES_WITH"},
            {"source": "Core A", "target": "Supplier X", "type": "DEPENDS_ON"},
            {"source": "Unrelated 1", "target": "Unrelated 2", "type": "KNOWS"},
            {"source": "Core A", "target": "Core B", "type": "COMPETES_WITH"},
        ],
    }

    md, stats = render_compact_actor_dossier(actors, max_actors=2)

    assert md.count("### Core A") == 1 and md.count("### Core B") == 1
    assert "### Low" not in md
    assert "Supplier X" in md
    assert "Unrelated 1" not in md and "Unrelated 2" not in md
    assert md.count("COMPETES_WITH") == 1
    assert stats["actors_rendered"] == 2
    assert stats["relationships_rendered"] == 2
    assert stats["context_neighbors_rendered"] == 1


def test_compactor_is_deterministic_and_obeys_size_cap():
    actors = {
        "actors": [_actor(f"Actor {i}", 1 - i / 20) for i in range(10)],
        "relationships": [],
    }
    first, stats = render_compact_actor_dossier(actors, max_chars=1400, max_actors=10)
    second, stats2 = render_compact_actor_dossier(actors, max_chars=1400, max_actors=10)

    assert first == second and stats == stats2
    assert len(first) <= 1400
    assert stats["truncated"] is True


def test_compactor_handles_missing_or_malformed_input():
    assert render_compact_actor_dossier(None)[0] == ""
    assert render_compact_actor_dossier({"actors": "bad"})[0] == ""


def test_compactor_renders_real_structured_brief_and_incentives_as_markdown():
    actors = {
        "central_question": "Will the open-source model become the market leader?",
        "as_of_date": "2026-06-08",
        "situation_brief": {
            "current_situation": "The market is consolidating around a small cast.",
            "context": "A price war followed the first open-source release.",
            "dynamics": "Open and closed model strategies remain in tension.",
            "fault_lines": [
                "Open source versus closed source",
                "Domestic compute versus international procurement",
            ],
            "catalysts": ["A new export-control rule", "The next benchmark release"],
        },
        "actors": [{
            **_actor("DeepSeek", .98),
            "incentives": [{
                "driver": "National backing and developer adoption",
                "gains_if": "The next model leads global benchmarks",
                "loses_if": "Commercialization lags valuation",
                "intensity": "high",
            }],
        }],
        "relationships": [],
    }

    md, stats = render_compact_actor_dossier(actors)

    assert "## Situation Brief" in md
    assert "### State of Play\n\nThe market is consolidating" in md
    assert "### Context\n\nA price war" in md
    assert "### Dynamics\n\nOpen and closed model strategies" in md
    assert "### Fault Lines\n- Open source versus closed source" in md
    assert "### Catalysts\n- A new export-control rule" in md
    assert "**Incentives:**\n- **Driver:** National backing" in md
    assert "**Gains if:** The next model leads global benchmarks" in md
    assert "**Loses if:** Commercialization lags valuation" in md
    assert "**Intensity:** high" in md
    assert "{'" not in md and "'}" not in md
    assert stats["actors_rendered"] == 1


def test_compactor_preserves_bounded_actor_intelligence_for_graph_seeding():
    actor = _actor("Northstar", .98)
    actor["intelligence"] = {
        "schema_version": "actor-intelligence/v1",
        "dimensions": {
            "identity_history": {"claims": [{"claim": "Founded in 2004.", "source_refs": ["S1"]}]},
            "motivations": {"claims": [{"claim": "Preserve market leadership.", "source_refs": ["S2"]}]},
            "capabilities": {"claims": [{"claim": "Can deploy 2 GW.", "source_refs": ["S3"]}]},
            "current_actions": {"claims": [{"claim": "Is building storage.", "source_refs": ["S4"]}]},
            "future_plans": {"claims": [{"claim": "Plans an acquisition if rates hold.", "source_refs": ["S5"]}]},
            "investments_capital_allocation": {"claims": [{
                "claim": "Allocated $800 million to resilience.", "source_refs": ["S6"]
            }]},
            "decision_rights_process_triggers": {"claims": [{
                "claim": "Board approval is required.", "source_refs": ["S7"]
            }]},
            "knowledge_state": {"claims": [{"claim": "Knows its private outage forecast."}]},
            "red_lines": {"claims": [{"claim": "Rejects unfunded mandates."}]},
        },
        "evidence_gaps": {"future_plans": ["Target identity is unknown."]},
    }

    md, stats = render_compact_actor_dossier({"actors": [actor], "relationships": []})

    for expected in (
        "**History / track record:**",
        "Founded in 2004",
        "**Motivations:**",
        "**Capabilities / limits:**",
        "**Current actions:**",
        "Is building storage",
        "**Future plans / conditions:**",
        "Plans an acquisition",
        "**Investments / capital allocation:**",
        "Allocated $800 million",
        "**Decision model / triggers:**",
        "**Knowledge state:**",
        "**Actual red lines:**",
        "**Actor-intelligence evidence gaps:**",
        "Target identity is unknown",
    ):
        assert expected in md
    assert stats["truncated"] is False
