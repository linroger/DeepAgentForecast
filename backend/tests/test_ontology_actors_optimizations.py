"""Offline, deterministic tests for the ontology-actors optimization bucket.

Covers:
* R2-CAL-13 / R2-SIM-9 — world_state_seed_from_actors now parses probability_band
  midpoints and forecast_inputs.base_rates outcome_frequency (the real schema), and
  emits a uniform_prior flag instead of letting WorldState silently fall to 50/50.
* ONTO-3 — head+middle+tail sampling of over-cap ontology source text.
* ONTO-6 — edge source_targets whose endpoints are not defined entity types are
  dropped; case-only drift is remapped to the canonical name.
* CHUNK-1 — TextProcessor.split_text honors Config.DEFAULT_CHUNK_SIZE when no
  explicit size is passed, and stays byte-identical when an explicit size is given.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.actors import (  # noqa: E402
    _match_reference_class_to_scenario,
    _parse_probability_value,
    world_state_seed_from_actors,
)
from app.services.ontology_generator import OntologyGenerator  # noqa: E402
from app.services.text_processor import TextProcessor  # noqa: E402


# ----------------------------------------------------- R2-CAL-13: band parsing
def test_parse_probability_value_forms():
    assert _parse_probability_value("30-40%") == 0.35
    assert _parse_probability_value("around 60%") == 0.60
    assert _parse_probability_value("0.3-0.5") == 0.40
    assert _parse_probability_value("25%") == 0.25
    assert _parse_probability_value("0.2") == 0.20
    assert _parse_probability_value("10–20%") == 0.15        # en dash range
    # no usable signal → None
    assert _parse_probability_value("") is None
    assert _parse_probability_value("likely") is None
    assert _parse_probability_value(None) is None
    assert _parse_probability_value(True) is None             # bool excluded
    # numeric passthrough, out-of-range rejected
    assert _parse_probability_value(0.4) == 0.4
    assert _parse_probability_value(5) is None


def test_match_reference_class_to_scenario():
    names = ["base", "upside", "downside"]
    assert _match_reference_class_to_scenario(
        "historical base rate of incumbent holding share", names) == "base"
    assert _match_reference_class_to_scenario("pure downside reference class", names) == "downside"
    assert _match_reference_class_to_scenario("unrelated class", names) is None


def test_seed_parses_probability_band_midpoint():
    """The real forecast_inputs schema carries probability_band, not a bare probability."""
    actors = {"forecast_inputs": {"scenarios": [
        {"name": "base", "probability_band": "40-55%"},
        {"name": "downside", "probability_band": "around 20%"},
    ]}}
    seed = world_state_seed_from_actors(actors)
    assert seed["scenarios"] == ["base", "downside"]
    assert seed["base_rates"]["base"] == 0.475
    assert seed["base_rates"]["downside"] == 0.20
    assert seed["uniform_prior"] is False


def test_seed_fills_from_base_rates_outcome_frequency():
    """Scenario without its own probability gets a base rate mapped from base_rates."""
    actors = {"forecast_inputs": {
        "scenarios": [{"name": "base"}, {"name": "downside"}],
        "base_rates": [
            {"reference_class": "base case for this type of negotiation",
             "outcome_frequency": "65%"},
            {"reference_class": "downside historical frequency", "outcome_frequency": "0.15"},
        ],
    }}
    seed = world_state_seed_from_actors(actors)
    assert seed["base_rates"]["base"] == 0.65
    assert seed["base_rates"]["downside"] == 0.15
    assert seed["uniform_prior"] is False


def test_seed_scenario_probability_takes_priority_over_base_rates():
    actors = {"forecast_inputs": {
        "scenarios": [{"name": "base", "probability": 0.7}],
        "base_rates": [{"reference_class": "base", "outcome_frequency": "10%"}],
    }}
    seed = world_state_seed_from_actors(actors)
    assert seed["base_rates"]["base"] == 0.7          # bare key wins over base_rate fill


def test_seed_uniform_prior_when_genuinely_absent():
    """Scenarios present but no parseable probability anywhere → uniform_prior True."""
    actors = {"forecast_inputs": {"scenarios": [
        {"name": "base", "probability_band": "unknown"},
        {"name": "downside"},
    ]}}
    seed = world_state_seed_from_actors(actors)
    assert seed["scenarios"] == ["base", "downside"]
    assert seed["base_rates"] == {}
    assert seed["uniform_prior"] is True


def test_seed_byte_stable_for_bare_probability():
    """Legacy fixtures (bare probability key) keep the exact prior behavior, plus the
    additive uniform_prior flag."""
    actors = {"forecast_inputs": {"scenarios": [
        {"name": "NVIDIA holds", "probability": 0.6},
        {"name": "ASICs erode", "probability": 0.3},
        {"name": "维持现状"},
    ]}}
    seed = world_state_seed_from_actors(actors)
    assert seed["scenarios"] == ["NVIDIA holds", "ASICs erode", "维持现状"]
    assert seed["base_rates"]["NVIDIA holds"] == 0.6
    assert "维持现状" not in seed["base_rates"]
    assert seed["uniform_prior"] is False


def test_seed_empty_without_scenarios():
    assert world_state_seed_from_actors(None) == {}
    assert world_state_seed_from_actors({"actors": []}) == {}


# ------------------------------------------------------------ ONTO-3: sampling
def test_sample_head_middle_tail_under_cap_is_identity():
    txt = "abcdef"
    assert OntologyGenerator._sample_head_middle_tail(txt, 100) == txt


def test_sample_head_middle_tail_includes_head_mid_tail():
    # distinctive markers at head / middle / tail of a long doc
    body = ("H" * 1000) + ("M" * 1000) + ("T" * 1000)
    head = "HEADMARK" + body[8:]
    mid = head[:1500] + "MIDMARK" + head[1507:]
    full = mid[:-8] + "TAILMARK"
    sampled = OntologyGenerator._sample_head_middle_tail(full, 1200)
    assert len(sampled) < len(full)
    assert "HEADMARK" in sampled        # head retained
    assert "TAILMARK" in sampled        # tail retained (pure head-truncation would lose this)
    assert "中段节选" in sampled and "尾段节选" in sampled


# -------------------------------------------------- ONTO-6: edge endpoint fixups
def _gen():
    # construct without touching the network: __init__ only builds an LLMClient,
    # but we call the pure static/instance validators directly.
    g = OntologyGenerator.__new__(OntologyGenerator)
    return g


def test_reconcile_drops_undefined_edge_endpoints():
    result = {
        "entity_types": [{"name": "Company"}, {"name": "Regulator"}],
        "edge_types": [{
            "name": "REGULATES",
            "source_targets": [
                {"source": "Regulator", "target": "Company"},      # valid
                {"source": "Regulator", "target": "Ghost"},        # Ghost undefined → drop
            ],
        }],
    }
    OntologyGenerator._reconcile_edge_endpoints(result)
    sts = result["edge_types"][0]["source_targets"]
    assert sts == [{"source": "Regulator", "target": "Company"}]


def test_reconcile_remaps_case_drift():
    result = {
        "entity_types": [{"name": "Company"}, {"name": "Regulator"}],
        "edge_types": [{
            "name": "REGULATES",
            "source_targets": [{"source": "regulator", "target": "COMPANY"}],
        }],
    }
    OntologyGenerator._reconcile_edge_endpoints(result)
    assert result["edge_types"][0]["source_targets"] == [
        {"source": "Regulator", "target": "Company"}
    ]


def test_reconcile_noop_without_entity_types():
    result = {"entity_types": [], "edge_types": [
        {"name": "X", "source_targets": [{"source": "A", "target": "B"}]}
    ]}
    OntologyGenerator._reconcile_edge_endpoints(result)
    # nothing defined → leave edges untouched (degrade-safe)
    assert result["edge_types"][0]["source_targets"] == [{"source": "A", "target": "B"}]


# --------------------------------------------------- CHUNK-1: split_text honors cfg
def test_split_text_explicit_size_byte_stable():
    text = "句子一。句子二。句子三。" * 50
    explicit = TextProcessor.split_text(text, 500, 50)
    from app.utils.file_parser import split_text_into_chunks
    assert explicit == split_text_into_chunks(text, 500, 50)


def test_split_text_default_honors_config(monkeypatch):
    import app.services.text_processor as tp
    text = "x" * 6000

    class _Cfg:
        DEFAULT_CHUNK_SIZE = 2500
        DEFAULT_CHUNK_OVERLAP = 250

    monkeypatch.setattr(tp, "_Config", _Cfg)
    chunks_default = TextProcessor.split_text(text)        # no explicit size
    chunks_small = TextProcessor.split_text(text, 500, 50)
    # larger configured chunk size → strictly fewer chunks (the wedge-fix lever)
    assert len(chunks_default) < len(chunks_small)


# ── _infer_archetype_from_name: "state" substring collision (live-surfaced 2026-07-03) ─────
# A real MiniMax forecast run produced an entity type named "HeadOfState" with no explicit
# archetype; the name-hint fallback used to match the bare "state" hint (meant for
# place/jurisdiction types) as a substring of "HeadOfState", misclassifying a top-tier
# decision-maker actor type as a place — which would then get excluded from the simulation
# agent pool by entity_simulation_tier's archetype-based tier-4 (abstract) branch.

def test_infer_archetype_head_of_state_is_actor_not_place():
    assert OntologyGenerator._infer_archetype_from_name("HeadOfState") == "actor"


def test_infer_archetype_secretary_of_state_is_actor_not_place():
    assert OntologyGenerator._infer_archetype_from_name("SecretaryOfState") == "actor"


def test_infer_archetype_state_department_is_actor_not_place():
    assert OntologyGenerator._infer_archetype_from_name("StateDepartment") == "actor"


def test_infer_archetype_genuine_place_types_still_classify():
    # nation/country/jurisdiction/region still correctly resolve to place_jurisdiction —
    # removing the bare "state" hint must not break real place-type names.
    assert OntologyGenerator._infer_archetype_from_name("NationState") == "place_jurisdiction"
    assert OntologyGenerator._infer_archetype_from_name("Country") == "place_jurisdiction"
    assert OntologyGenerator._infer_archetype_from_name("Jurisdiction") == "place_jurisdiction"
    assert OntologyGenerator._infer_archetype_from_name("Region") == "place_jurisdiction"


def test_infer_archetype_does_not_override_explicit_llm_value():
    # _normalize_rich_schema only backfills when archetype is missing/empty — an explicit
    # (even if seemingly wrong) LLM-provided value is never overwritten.
    result = {"entity_types": [
        {"name": "HeadOfState", "archetype": "collective"},
        {"name": "HeadOfState", "archetype": ""},
    ]}
    OntologyGenerator._normalize_rich_schema(result)
    assert result["entity_types"][0]["archetype"] == "collective"  # explicit, kept as-is
    assert result["entity_types"][1]["archetype"] == "actor"       # empty, backfilled correctly
