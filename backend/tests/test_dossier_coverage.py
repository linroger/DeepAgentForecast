"""NEXTSTEPS P3-4: dossier coverage gate (pure function)."""

from app.utils.actors import dossier_coverage


def test_dossier_coverage_empty():
    c = dossier_coverage(None)
    assert c["n_actors"] == 0 and c["pct_actors_with_incentives"] == 0.0
    assert dossier_coverage({"actors": []})["n_actors"] == 0


def test_dossier_coverage_rich():
    actors = {
        "actors": [
            {"name": "A", "influence": "high",
             "incentives": [{"driver": "x", "gains_if": "y"}],
             "worldview": {"identity": "i", "values": ["v"]},
             "salience": {"score": 0.8}},
            {"name": "B", "influence": "high",
             "incentives": [{"driver": "z"}],
             "worldview": {"identity": "j"}},
        ],
        "relationships": [
            {"source": "A", "target": "B", "type": "OPPOSES", "valence": "adversarial"},
        ],
    }
    c = dossier_coverage(actors)
    assert c["n_actors"] == 2
    assert c["pct_actors_with_incentives"] == 1.0
    assert c["n_tier12"] == 2                      # both high-influence → tier 1
    assert c["pct_tier12_with_worldview"] == 1.0
    assert c["n_relationships"] == 1
    assert c["pct_edges_valenced"] == 1.0          # explicit valence present
    assert c["edges_per_actor"] == 0.5
    assert c["salience_basis_present"] == 0.5      # only A carries salience


def test_dossier_coverage_hollow_flags_low():
    actors = {
        "actors": [{"name": "A", "influence": "high"}, {"name": "B", "influence": "low"}],
        "relationships": [{"source": "A", "target": "B", "type": "OPPOSES"}],  # no explicit valence
    }
    c = dossier_coverage(actors)
    assert c["pct_actors_with_incentives"] == 0.0
    assert c["pct_edges_valenced"] == 0.0          # OPPOSES infers valence but no EXPLICIT field
    assert c["pct_tier12_with_worldview"] == 0.0
