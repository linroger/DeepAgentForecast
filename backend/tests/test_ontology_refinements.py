"""Offline golden tests for the ONTOLOGY refinements in app.utils.actors.

These are pure, deterministic, network/LLM-free unit tests covering the new
ontology layer added on top of actors.json:

* entity_archetype / entity_simulation_tier / is_agent_eligible — the "agentic"
  admission gate (sources / abstract concepts must NOT become simulation agents).
* salience_score — salience.score → influence fallback → low default.
* relation_valence / relation_polarity — derive valence/polarity from relation
  type, with explicit fields overriding the derivation.
* relational_roster — projects relationships[] into named buckets.
* build_initial_follow_graph — DEGRADE-SAFETY: legacy (8-type, no valence)
  fixtures yield the SAME follow edges as before the ontology change, and the
  NEW economic types (SUPPLIES/FUNDS) create the expected new follows.
* behavioral_dna_block — "" when no DNA fields; a block with values/incentives
  when present.

Design contract (from actors.py docstring): every NEW field is optional; when
ABSENT, behavior degrades to the legacy path. The degrade-safety tests below are
the load-bearing guarantee that adding the ontology fields did not change the
existing follow-graph wiring.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.actors import (  # noqa: E402
    REL_EDGE_NAME,
    REL_LABEL,
    behavioral_dna_block,
    build_initial_follow_graph,
    entity_archetype,
    entity_simulation_tier,
    is_agent_eligible,
    normalize_name,
    relation_polarity,
    relation_valence,
    relational_roster,
    salience_score,
)


# --------------------------------------------------------------------------- #
# 1) entity_archetype / entity_simulation_tier / is_agent_eligible
# --------------------------------------------------------------------------- #
def test_source_tier3_actor_is_not_agent_eligible():
    # A research SOURCE / passive reporter is tier-3 and must be kept out of the
    # agent pool (it reports, it does not act).
    src = {"name": "Reuters", "archetype": "source"}
    assert entity_archetype(src) == "source"
    assert entity_simulation_tier(src) == 3
    assert is_agent_eligible(src) is False

    # An explicit simulation_tier=3 likewise excludes (e.g. a media outlet only
    # quoted, not a decision-maker).
    tier3 = {"name": "WireService", "simulation_tier": 3}
    assert entity_simulation_tier(tier3) == 3
    assert is_agent_eligible(tier3) is False


def test_actor_tier1_is_agent_eligible():
    # A real decision-maker: archetype actor + simulation_tier 1 → eligible.
    act = {"name": "OpenAI", "archetype": "actor", "simulation_tier": 1}
    assert entity_archetype(act) == "actor"
    assert entity_simulation_tier(act) == 1
    assert is_agent_eligible(act) is True

    # tier 2 (stakeholder) is also admitted to the agent pool.
    stakeholder = {"name": "Anthropic", "archetype": "actor", "simulation_tier": 2}
    assert entity_simulation_tier(stakeholder) == 2
    assert is_agent_eligible(stakeholder) is True


def test_absent_fields_default_to_actor_and_eligible():
    # DEGRADE-SAFE: a legacy actor row with no ontology fields must behave as
    # before — treated as an agentic actor and admitted.
    legacy = {"name": "LegacyCo"}
    assert entity_archetype(legacy) == "actor"
    assert entity_simulation_tier(legacy) == 1
    assert is_agent_eligible(legacy) is True

    # Non-dict / empty also degrade safely (never raise).
    assert entity_archetype(None) == "actor"
    assert entity_simulation_tier(None) == 1
    assert is_agent_eligible(None) is True

    # An unknown/illegal archetype string falls back to "actor" (one is agentic).
    weird = {"name": "X", "archetype": "definitely_not_an_archetype"}
    assert entity_archetype(weird) == "actor"
    assert is_agent_eligible(weird) is True

    # bool simulation_tier must be rejected (bool is an int subclass) and the
    # row degrades to the archetype/influence inference (here → tier 1).
    bool_tier = {"name": "Y", "simulation_tier": True}
    assert entity_simulation_tier(bool_tier) == 1


def test_non_actor_archetype_is_tier4_abstract():
    # Abstract entities (events / concepts / resources) → tier 4, not agentic.
    for arch in ("event", "signal", "constraint_resource", "place_jurisdiction"):
        row = {"name": "n", "archetype": arch}
        assert entity_simulation_tier(row) == 4
        assert is_agent_eligible(row) is False


# --------------------------------------------------------------------------- #
# 2) salience_score
# --------------------------------------------------------------------------- #
def test_salience_reads_explicit_score():
    assert salience_score({"name": "A", "salience": {"score": 0.85}}) == 0.85
    # Clamped to [0, 1].
    assert salience_score({"name": "A", "salience": {"score": 1.7}}) == 1.0
    assert salience_score({"name": "A", "salience": {"score": -0.5}}) == 0.0


def test_salience_falls_back_to_influence_when_no_score():
    # No salience block at all → influence high/medium/low → 0.85/0.55/0.3.
    assert salience_score({"name": "A", "influence": "high"}) == 0.85
    assert salience_score({"name": "A", "influence": "medium"}) == 0.55
    assert salience_score({"name": "A", "influence": "low"}) == 0.3


def test_salience_reads_tier_text_when_score_absent():
    # salience present but score non-numeric → read salience.tier text.
    assert salience_score({"name": "A", "salience": {"tier": "high"}}) == 0.85
    assert salience_score({"name": "A", "salience": {"tier": "medium"}}) == 0.55
    assert salience_score({"name": "A", "salience": {"tier": "low"}}) == 0.3


def test_salience_defaults_to_low_when_absent():
    # No salience and no parseable influence → low default 0.3.
    assert salience_score({"name": "A"}) == 0.3
    assert salience_score(None) == 0.3
    # bool score is rejected (bool is int subclass) → falls through to default.
    assert salience_score({"name": "A", "salience": {"score": True}}) == 0.3


# --------------------------------------------------------------------------- #
# 3) relation_valence / relation_polarity
# --------------------------------------------------------------------------- #
def test_valence_allied_positive():
    rel = {"source": "A", "target": "B", "type": "ALLY_OF"}
    assert relation_valence(rel) == "allied"
    assert relation_polarity(rel) > 0


def test_valence_adversarial_negative():
    for typ in ("OPPOSES", "COMPETES_WITH"):
        rel = {"source": "A", "target": "B", "type": typ}
        assert relation_valence(rel) == "adversarial"
        assert relation_polarity(rel) < 0


def test_valence_transactional_small_positive():
    for typ in ("SUPPLIES", "FUNDS"):
        rel = {"source": "A", "target": "B", "type": typ}
        assert relation_valence(rel) == "transactional"
        pol = relation_polarity(rel)
        assert pol > 0
        # transactional polarity is *small* — strictly weaker than an allied tie.
        assert pol < relation_polarity({"type": "ALLY_OF"})


def test_explicit_valence_and_polarity_override_derivation():
    # Explicit valence wins over the type-derived one.
    rel = {"type": "OPPOSES", "valence": "allied"}
    assert relation_valence(rel) == "allied"

    # Explicit polarity wins over the valence/type-derived one (and is clamped).
    rel2 = {"type": "ALLY_OF", "polarity": -0.9}
    assert relation_polarity(rel2) == -0.9
    assert relation_polarity({"type": "OPPOSES", "polarity": 2.0}) == 1.0


def test_valence_unknown_and_directional_are_neutral_polarity():
    # OTHER / unknown → neutral with zero polarity.
    assert relation_valence({"type": "OTHER"}) == "neutral"
    assert relation_polarity({"type": "OTHER"}) == 0.0
    # Directional (governance/influence) types → directional valence, 0 polarity.
    assert relation_valence({"type": "REGULATES"}) == "directional"
    assert relation_polarity({"type": "REGULATES"}) == 0.0


def test_polarity_scales_with_strength():
    # Strong allied tie has higher polarity than a weak one.
    strong = relation_polarity({"type": "ALLY_OF", "strength": "high"})
    weak = relation_polarity({"type": "ALLY_OF", "strength": "low"})
    assert strong > weak > 0


# --------------------------------------------------------------------------- #
# 4) relational_roster
# --------------------------------------------------------------------------- #
def _roster_fixture():
    """A small named cast + relationships covering several roster buckets.

    Hero is the focal actor:
      * ALLY_OF Friend            → allies
      * COMPETES_WITH Rival       → competitors
      * SUPPLIES Customer         → Customer is Hero's customer
      * FUNDS-backer Backer FUNDS Hero → Backer is Hero's backer/investor
      * Regulator REGULATES Hero  → Regulator regulates Hero
    """
    return {
        "actors": [
            {"name": "Hero"},
            {"name": "Friend"},
            {"name": "Rival"},
            {"name": "Customer"},
            {"name": "Backer"},
            {"name": "Regulator"},
        ],
        "relationships": [
            {"source": "Hero", "target": "Friend", "type": "ALLY_OF"},
            {"source": "Hero", "target": "Rival", "type": "COMPETES_WITH"},
            {"source": "Hero", "target": "Customer", "type": "SUPPLIES"},
            {"source": "Backer", "target": "Hero", "type": "FUNDS"},
            {"source": "Regulator", "target": "Hero", "type": "REGULATES"},
        ],
    }


def _names(bucket):
    return [item["name"] for item in bucket]


def test_relational_roster_projects_into_named_buckets():
    roster = relational_roster("Hero", _roster_fixture())
    assert _names(roster["allies"]) == ["Friend"]
    assert _names(roster["competitors"]) == ["Rival"]
    assert _names(roster["customers"]) == ["Customer"]
    assert _names(roster["backers_investors"]) == ["Backer"]
    assert _names(roster["regulators"]) == ["Regulator"]


def test_relational_roster_empty_for_unknown_actor_and_no_self_edges():
    roster = relational_roster("Hero", _roster_fixture())
    # Hero never appears in its own buckets.
    for bucket in roster.values():
        assert "Hero" not in _names(bucket)
    # Every bucket present, all 10 named keys exist.
    assert set(roster.keys()) == {
        "allies", "opponents", "competitors", "customers", "suppliers",
        "backers_investors", "supporters", "regulators", "dependents", "partners",
    }
    # An actor with no relationships → 10 empty buckets.
    empty = relational_roster("Nobody", _roster_fixture())
    assert all(v == [] for v in empty.values())


# --------------------------------------------------------------------------- #
# 5) build_initial_follow_graph — DEGRADE-SAFETY + new types
# --------------------------------------------------------------------------- #
# Influence levels chosen so ALLY_OF direction is deterministic: the
# lower-influence actor follows the higher-influence one.
_LEGACY_ACTORS = [
    {"name": "Low", "influence": "low"},
    {"name": "High", "influence": "high"},
    {"name": "P1", "influence": "medium"},
    {"name": "P2", "influence": "medium"},
    {"name": "Dep", "influence": "medium"},
    {"name": "Core", "influence": "high"},
    {"name": "Pundit", "influence": "high"},
    {"name": "Crowd", "influence": "low"},
    {"name": "Reg", "influence": "high"},
    {"name": "Firm", "influence": "medium"},
    {"name": "OtherA", "influence": "medium"},
    {"name": "OtherB", "influence": "medium"},
]


def _id_map(actors):
    """normalize_name -> sequential int id (build_initial_follow_graph contract)."""
    return {normalize_name(a["name"]): i for i, a in enumerate(actors)}


def _follow_set(actors, relationships):
    payload = {"actors": actors, "relationships": relationships}
    edges = build_initial_follow_graph(payload, _id_map(actors))
    return {tuple(e) for e in edges}


def _aid(actors, name):
    return _id_map(actors)[normalize_name(name)]


def test_legacy_follow_graph_directions_unchanged():
    """The 8 legacy edge types must produce exactly the documented directions.

    This is the degrade-safety guarantee: no ontology field is present, so the
    follow wiring must match the pre-ontology behavior byte-for-byte.
    """
    a = _LEGACY_ACTORS
    rels = [
        {"source": "Low", "target": "High", "type": "ALLY_OF"},
        {"source": "P1", "target": "P2", "type": "PARTNERS_WITH"},
        {"source": "Dep", "target": "Core", "type": "DEPENDS_ON"},
        {"source": "Pundit", "target": "Crowd", "type": "INFLUENCES"},
        {"source": "Reg", "target": "Firm", "type": "REGULATES"},
        {"source": "OtherA", "target": "OtherB", "type": "OTHER"},
    ]
    got = _follow_set(a, rels)

    low, high = _aid(a, "Low"), _aid(a, "High")
    p1, p2 = _aid(a, "P1"), _aid(a, "P2")
    dep, core = _aid(a, "Dep"), _aid(a, "Core")
    pundit, crowd = _aid(a, "Pundit"), _aid(a, "Crowd")
    reg, firm = _aid(a, "Reg"), _aid(a, "Firm")

    expected = {
        (low, high),          # ALLY_OF: lower influence follows higher
        (p1, p2), (p2, p1),   # PARTNERS_WITH: bidirectional
        (dep, core),          # DEPENDS_ON: depender follows depended-on
        (crowd, pundit),      # INFLUENCES: audience follows influencer (dst->src)
        (reg, firm),          # REGULATES: regulator follows regulated (src->dst)
        # OTHER: no follow edge
    }
    assert got == expected


def test_legacy_opposes_and_competes_are_bidirectional():
    a = _LEGACY_ACTORS
    rels = [
        {"source": "P1", "target": "P2", "type": "OPPOSES"},
        {"source": "OtherA", "target": "OtherB", "type": "COMPETES_WITH"},
    ]
    got = _follow_set(a, rels)
    p1, p2 = _aid(a, "P1"), _aid(a, "P2")
    oa, ob = _aid(a, "OtherA"), _aid(a, "OtherB")
    assert got == {(p1, p2), (p2, p1), (oa, ob), (ob, oa)}


def test_other_type_creates_no_follow_edge():
    a = _LEGACY_ACTORS
    got = _follow_set(a, [{"source": "OtherA", "target": "OtherB", "type": "OTHER"}])
    assert got == set()


def test_new_supplies_and_funds_create_expected_follows():
    """New ONTOLOGY economic types add follow edges the legacy path never made.

    SUPPLIES(source=supplier) → the customer (target) follows the supplier.
    FUNDS(source=backer)      → the funded party (target) follows the backer.
    """
    a = _LEGACY_ACTORS
    rels = [
        {"source": "Core", "target": "Firm", "type": "SUPPLIES"},  # Core supplies Firm
        {"source": "High", "target": "Low", "type": "FUNDS"},      # High funds Low
    ]
    got = _follow_set(a, rels)
    core, firm = _aid(a, "Core"), _aid(a, "Firm")
    high, low = _aid(a, "High"), _aid(a, "Low")
    expected = {
        (firm, core),   # SUPPLIES: customer (target) follows supplier (source)
        (low, high),    # FUNDS: funded (target) follows backer (source)
    }
    assert got == expected


def test_follow_graph_skips_self_loops_and_unresolved_endpoints():
    a = _LEGACY_ACTORS
    rels = [
        {"source": "P1", "target": "P1", "type": "PARTNERS_WITH"},   # self loop
        {"source": "P1", "target": "Ghost", "type": "ALLY_OF"},      # target not an actor
    ]
    # Both rows are dropped (Ghost not in actor table → row filtered upstream;
    # self loop skipped inside the builder). Result is empty.
    assert _follow_set(a, rels) == set()


# --------------------------------------------------------------------------- #
# 6) behavioral_dna_block
# --------------------------------------------------------------------------- #
def test_behavioral_dna_empty_when_no_dna_fields():
    # No worldview / incentives / resources / risk_tolerance → empty string
    # (legacy rows render nothing new).
    assert behavioral_dna_block({"name": "A", "role": "x", "stance": "y"}) == ""
    assert behavioral_dna_block({}) == ""
    assert behavioral_dna_block(None) == ""


def test_behavioral_dna_block_contains_values_and_incentives():
    actor = {
        "name": "OpenAI",
        "worldview": {
            "identity": "前沿实验室",
            "values": ["安全", "规模"],
            "beliefs": ["AGI 不可避免"],
        },
        "incentives": [
            {"driver": "市场份额", "gains_if": "更快发布", "loses_if": "被监管", "intensity": "high"},
        ],
        "resources": ["算力", "人才"],
        "risk_tolerance": "high",
    }
    block = behavioral_dna_block(actor)
    assert block != ""
    # values present
    assert "安全" in block
    assert "规模" in block
    # incentive driver + gains/loses surfaced
    assert "市场份额" in block
    assert "更快发布" in block
    assert "被监管" in block
    # resources + risk tolerance surfaced
    assert "算力" in block
    assert "high" in block


# ----------------------------------------- NEXTSTEPS P3-5: causal/mechanism edges
def test_causal_edge_family_wired():
    """CAUSES/ENABLES/CONSTRAINS/TRIGGERS/ACCELERATES are first-class typed edges
    (identity edge name, not collapsed to RELATES_TO) with directional valence."""
    for t in ("CAUSES", "ENABLES", "CONSTRAINS", "TRIGGERS", "ACCELERATES"):
        assert REL_EDGE_NAME[t] == t            # not collapsed to RELATES_TO
        assert t in REL_LABEL                   # has a zh label for persona/report blocks
        assert relation_valence({"type": t}) == "directional"


def test_causal_edges_do_not_create_follows():
    """Causal/mechanism edges are not social relations → no initial follow edges
    (like REPORTS_ON/CONSUMES). They inform retrieval/propagation, not the social graph."""
    actors = [{"name": "A", "type": "Organization"}, {"name": "B", "type": "Organization"}]
    rels = [{"source": "A", "target": "B", "type": "CAUSES"}]
    assert _follow_set(actors, rels) == set()


# ----------------------------------------- NEXTSTEPS P3-3: ontology from dossier
def test_ontology_from_actors_projects_types():
    from app.utils.actors import ontology_from_actors
    actors = {
        "actors": [{"name": "A", "type": "Organization"}, {"name": "B", "type": "Media"}],
        "relationships": [
            {"source": "A", "target": "B", "type": "OPPOSES"},
            {"source": "A", "target": "B", "type": "CAUSES"},
        ],
    }
    onto = ontology_from_actors(actors)
    ent_names = {e["name"] for e in onto["entity_types"]}
    edge_names = {e["name"] for e in onto["edge_types"]}
    assert ent_names == {"Organization", "Media"}
    assert "OPPOSES" in edge_names and "CAUSES" in edge_names      # realized + causal projected
    causal = [e for e in onto["edge_types"] if e["name"] == "CAUSES"][0]
    assert causal["valence"] == "directional"                     # valence carried through


def test_ontology_from_actors_empty_when_no_types():
    from app.utils.actors import ontology_from_actors, ontology_seed_block
    assert ontology_from_actors(None) == {}
    assert ontology_from_actors({"actors": [{"name": "A"}]}) == {}  # no type, no rels
    assert ontology_seed_block(None) == ""                          # renderer degrades to ""
