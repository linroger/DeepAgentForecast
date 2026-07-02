"""NEXTSTEPS P3-8: relationship-trajectory projection (offline, pure)."""

from app.utils.actors import project_relationships, projected_edges_block


def _actors():
    return {
        "actors": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
        "relationships": [
            {"source": "A", "target": "B", "type": "ALLY_OF"},      # allied → persists
            {"source": "A", "target": "C", "type": "OPPOSES"},      # adversarial → escalates
            {"source": "A", "target": "D", "type": "SUPPLIES"},     # transactional → contingent
        ],
    }


def test_project_relationships_maps_valence_to_trajectory():
    proj = {(p["source"], p["target"]): p for p in project_relationships(_actors())}
    assert proj[("A", "B")]["projected"] == "likely_persists"
    assert proj[("A", "C")]["projected"] == "persists_or_escalates"
    assert proj[("A", "D")]["projected"] == "contingent"


def test_projected_block_orders_contingent_first_and_labels_model():
    blk = projected_edges_block(_actors())
    assert "模型推断" in blk or "非证据" in blk          # must flag itself as model, not evidence
    # the contingent (most-flippable) tie is surfaced before the sticky allied one
    assert blk.index("contingent") < blk.index("likely_persists")


def test_projected_edges_empty_is_degrade_safe():
    assert project_relationships(None) == []
    assert projected_edges_block(None) == ""
    assert projected_edges_block({"actors": []}) == ""
