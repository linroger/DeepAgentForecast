"""Offline unit tests for entity resolution / canonical-alias merge (EXECPLAN2 I-1-4).

Exercises the pure clustering/survivor-selection logic and the resolve_entities
orchestration via a fake runtime — no graph, no embedder, no LLM.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import zep_entity_resolver as er  # noqa: E402


def _node(uuid, name, label="Company"):
    return {"uuid": uuid, "name": name, "labels": [label], "summary": ""}


# -------------------------------------------------------------------- helpers
def test_name_match_exact_containment_and_short_noise():
    assert er._name_match("openai", "openai") is True
    assert er._name_match("openai", "openai公司") is True       # containment
    assert er._name_match("ab", "abcdef") is True               # >=2 char overlap
    assert er._name_match("a", "abcdef") is False               # 1-char shorter rejected
    assert er._name_match("openai", "anthropic") is False
    assert er._name_match("", "openai") is False


def test_cosine_unit_and_missing():
    assert er._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(er._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert er._cosine([1.0, 0.0], None) is None
    assert er._cosine([], [1.0]) is None
    assert er._cosine([1.0, 0.0], [1.0]) is None  # length mismatch


def test_canonical_norm_set():
    s = er.canonical_norm_set({"actors": [{"name": "OpenAI"}, {"name": "Anthropic, Inc."}]})
    assert "openai" in s and "anthropicinc" in s


# ------------------------------------------------------------------ plan_merges
def test_plan_merges_basic_alias_into_canonical():
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI 公司"), _node("u3", "Anthropic")]
    emb = [[1.0, 0.0], [0.98, 0.2], [0.0, 1.0]]
    plan = er.plan_merges(nodes, emb, {"openai"}, threshold=0.9)
    assert len(plan) == 1
    cluster = plan[0]
    assert cluster["survivor_uuid"] == "u1"          # canonical wins
    assert [v["uuid"] for v in cluster["victims"]] == ["u2"]
    assert cluster["victims"][0]["similarity"] >= 0.9


def test_plan_merges_never_merges_two_distinct_canonicals():
    # two DISTINCT canonicals whose names overlap (containment) + high cosine must
    # still NOT merge; same-canonical duplicates are handled elsewhere.
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI Foundation")]
    emb = [[1.0, 0.0], [0.99, 0.05]]
    plan = er.plan_merges(nodes, emb, {"openai", "openaifoundation"}, threshold=0.9)
    assert plan == []   # distinct canonicals must not merge into each other


def test_plan_merges_same_canonical_duplicates_do_merge():
    # two nodes that BOTH normalize to the same canonical ARE duplicates → merge.
    nodes = [_node("u1", "OpenAI"), _node("u2", "openai")]
    emb = [[1.0, 0.0], [1.0, 0.0]]
    plan = er.plan_merges(nodes, emb, {"openai"}, threshold=0.9)
    assert len(plan) == 1 and len(plan[0]["victims"]) == 1


def test_plan_merges_respects_label_boundary():
    nodes = [_node("u1", "Apple", "Company"), _node("u2", "Apple", "Person")]
    emb = [[1.0, 0.0], [1.0, 0.0]]
    plan = er.plan_merges(nodes, emb, set(), threshold=0.5)
    assert plan == []   # same name, different primary label → never merge


def test_plan_merges_embedding_gate_blocks_low_cosine():
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI 公司")]
    emb = [[1.0, 0.0], [0.0, 1.0]]  # name-match but orthogonal embeddings
    plan = er.plan_merges(nodes, emb, {"openai"}, threshold=0.88)
    assert plan == []   # cosine below threshold → no merge despite name match


def test_plan_merges_no_embeddings_requires_exact_norm():
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI 公司"), _node("u3", "openai")]
    # no embeddings → only EXACT normalized matches merge ("OpenAI"~"openai"), not containment
    plan = er.plan_merges(nodes, None, {"openai"}, threshold=0.88)
    assert len(plan) == 1
    victims = {v["uuid"] for v in plan[0]["victims"]}
    assert victims == {"u3"}              # exact-norm match only
    assert plan[0]["survivor_uuid"] == "u1"


def test_plan_merges_survivor_longest_when_no_canonical():
    nodes = [_node("u1", "OAI"), _node("u2", "OAI Labs")]
    emb = [[1.0, 0.0], [0.99, 0.05]]
    plan = er.plan_merges(nodes, emb, set(), threshold=0.9)  # no canonicals
    assert len(plan) == 1
    assert plan[0]["survivor_uuid"] == "u2"  # longer normalized name survives


def test_plan_merges_splits_component_with_two_canonicals():
    # bridge node matches both canonicals; component has 2 canonicals → must split
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI Anthropic"), _node("u3", "Anthropic")]
    emb = [[1.0, 0.0], [0.95, 0.1], [0.0, 1.0]]
    plan = er.plan_merges(nodes, emb, {"openai", "anthropic"}, threshold=0.85)
    survivors = {c["survivor_uuid"] for c in plan}
    # the two canonicals each remain a survivor; bridge attaches to one, never merging canonicals
    assert "u1" in survivors or "u3" in survivors
    for c in plan:
        for v in c["victims"]:
            assert v["uuid"] != "u1" and v["uuid"] != "u3"  # canonicals never become victims


# ------------------------------------------------ R2-KG-8 alias / cross-label
def _node_alias(uuid, name, aliases, label="Company"):
    return {"uuid": uuid, "name": name, "labels": [label], "summary": "",
            "attributes": {"aliases": aliases}}


def test_plan_merges_alias_zero_overlap_merges():
    # 'MSFT' and 'Microsoft' share no characters; the explicit alias table bridges
    # them where name-containment never could.
    nodes = [_node_alias("u1", "Microsoft", ["MSFT"]), _node("u2", "MSFT")]
    emb = [[1.0, 0.0], [0.97, 0.1]]
    plan = er.plan_merges(nodes, emb, {"microsoft"}, threshold=0.9)
    assert len(plan) == 1
    assert plan[0]["survivor_uuid"] == "u1"            # canonical wins
    assert [v["uuid"] for v in plan[0]["victims"]] == ["u2"]


def test_plan_merges_alias_requires_cosine_gate():
    # alias match alone is not enough; orthogonal embeddings (cosine 0) block it.
    nodes = [_node_alias("u1", "Microsoft", ["MSFT"]), _node("u2", "MSFT")]
    emb = [[1.0, 0.0], [0.0, 1.0]]
    plan = er.plan_merges(nodes, emb, {"microsoft"}, threshold=0.88)
    assert plan == []


# ---------------------------------------------------- 2026-07-03 dossier alias_map
# Live-surfaced bug: graphiti's own per-chunk extraction has NO alias metadata on the
# nodes it produces (attributes.aliases is empty in practice — the LLM extractor only
# sees isolated prose per chunk). actors.json's aliases field is the actual ground
# truth. actor_alias_map + the dossier_ok signal in plan_merges/plan_merges_fast let
# these merge WITHOUT relying on the graph node carrying any alias attribute at all.

def test_actor_alias_map_builds_from_actors_json():
    actors = {"actors": [
        {"name": "Government of the People's Republic of China",
         "aliases": ["PRC", "CCP", "China", "Beijing", "MOFCOM"]},
        {"name": "United States Congress", "aliases": ["US Congress", "Congress"]},
    ]}
    amap = er.actor_alias_map(actors)
    assert amap[er.normalize_name("China")] == er.normalize_name(
        "Government of the People's Republic of China")
    assert amap[er.normalize_name("CCP")] == er.normalize_name(
        "Government of the People's Republic of China")
    assert amap[er.normalize_name("Congress")] == er.normalize_name("United States Congress")
    # canonical name itself is never its own alias key
    assert er.normalize_name("Government of the People's Republic of China") not in amap


def test_plan_merges_dossier_alias_merges_zero_overlap_nodes_without_node_metadata():
    """Reproduces the live bug exactly: 6 graph nodes for ONE real actor, NONE of them
    carrying any attributes.aliases (i.e. _node_aliases returns empty for every one —
    the realistic case), merged purely via the actors.json-derived alias_map."""
    canonical = "Government of the People's Republic of China"
    nodes = [
        _node("u1", "China"),
        _node("u2", "CCP"),
        _node("u3", "Beijing"),
        _node("u4", canonical),
        _node("u5", "MOFCOM"),
        _node("u6", "Beijing"),  # a second, independently-extracted "Beijing" node
    ]
    # Orthogonal/absent embeddings on purpose — proves the dossier signal alone
    # (not cosine similarity) drives the merge, exactly like the exact-alias fallback.
    emb = None
    amap = er.actor_alias_map({"actors": [
        {"name": canonical, "aliases": ["PRC", "CCP", "China", "Beijing", "MOFCOM"]},
    ]})
    canon_set = {er.normalize_name(canonical)}
    plan = er.plan_merges(nodes, emb, canon_set, threshold=0.88, alias_map=amap)
    assert len(plan) == 1
    cluster = plan[0]
    assert cluster["survivor_uuid"] == "u4"  # the canonical node wins survivorship
    victim_uuids = {v["uuid"] for v in cluster["victims"]}
    assert victim_uuids == {"u1", "u2", "u3", "u5", "u6"}  # all 5 alias nodes merged in


def test_plan_merges_fast_dossier_alias_buckets_together():
    canonical = "Government of the People's Republic of China"
    nodes = [
        _node("u1", "China"), _node("u2", "CCP"), _node("u3", "Beijing"),
        _node("u4", canonical), _node("u5", "MOFCOM"),
    ]
    amap = er.actor_alias_map({"actors": [
        {"name": canonical, "aliases": ["PRC", "CCP", "China", "Beijing", "MOFCOM"]},
    ]})
    canon_set = {er.normalize_name(canonical)}
    plan = er.plan_merges_fast(nodes, None, canon_set, threshold=0.88, alias_map=amap)
    assert len(plan) == 1
    assert plan[0]["survivor_uuid"] == "u4"
    assert {v["uuid"] for v in plan[0]["victims"]} == {"u1", "u2", "u3", "u5"}


def test_dossier_alias_never_merges_two_distinct_canonicals():
    # Two DISTINCT canonicals must never merge even if some quirk of alias_map
    # construction (e.g. bad dossier data) tried to equate them.
    nodes = [_node("u1", "United States Congress"), _node("u2", "European Parliament")]
    amap = {}  # no aliasing between these two — sanity: distinct canonicals stay apart
    canon_set = {er.normalize_name("United States Congress"),
                er.normalize_name("European Parliament")}
    plan = er.plan_merges(nodes, None, canon_set, threshold=0.88, alias_map=amap)
    assert plan == []


def test_plan_merges_cross_label_entity_generic():
    # an untyped 'Entity' duplicate of a typed node merges INTO the typed node and
    # the survivor keeps its type.
    nodes = [_node("u1", "OpenAI", "Company"), _node("u2", "OpenAI", "Entity")]
    emb = [[1.0, 0.0], [1.0, 0.0]]
    plan = er.plan_merges(nodes, emb, set(), threshold=0.9)
    assert len(plan) == 1
    assert plan[0]["survivor_uuid"] == "u1"
    assert plan[0]["primary_label"] == "Company"
    assert plan[0]["cross_label"] is True


def test_plan_merges_entity_bridge_cannot_merge_two_typed_labels():
    # 'Apple'(Company) and 'Apple'(Person) must NEVER co-merge, even bridged by a
    # generic 'Apple'(Entity) node — the over-merge guard splits them.
    nodes = [_node("u1", "Apple", "Company"), _node("u2", "Apple", "Entity"),
             _node("u3", "Apple", "Person")]
    emb = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    plan = er.plan_merges(nodes, emb, set(), threshold=0.9)
    for c in plan:
        labels_in_cluster = {c["primary_label"]} | {v["label"] for v in c["victims"]}
        assert not ({"Company", "Person"} <= labels_in_cluster)


# --------------------------------------------------------------- orchestration
class _FakeRuntime:
    def __init__(self, nodes, embeddings=None, fail_embed=False):
        self._nodes = nodes
        self._emb = embeddings
        self._fail_embed = fail_embed
        self.merge_calls = []

    def all_entity_nodes(self, graph_id):
        return self._nodes

    def embed_texts(self, texts):
        if self._fail_embed:
            raise RuntimeError("embedder down")
        return self._emb

    def merge_nodes(self, graph_id, survivor, victim):
        self.merge_calls.append((survivor, victim))
        return {"rewired": 2, "deleted": True}


def test_resolve_entities_executes_merges():
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI 公司"), _node("u3", "Anthropic")]
    emb = [[1.0, 0.0], [0.98, 0.1], [0.0, 1.0]]
    rt = _FakeRuntime(nodes, emb)
    audit = er.resolve_entities("g1", {"actors": [{"name": "OpenAI"}]}, threshold=0.9, runtime=rt)
    assert audit["nodes_scanned"] == 3
    assert audit["clusters"] == 1
    assert audit["merged_nodes"] == 1
    assert rt.merge_calls == [("u1", "u2")]
    assert audit["merges"][0]["execution"][0]["deleted"] is True


def test_resolve_entities_noop_under_two_nodes():
    rt = _FakeRuntime([_node("u1", "Solo")])
    audit = er.resolve_entities("g1", {"actors": []}, threshold=0.9, runtime=rt)
    assert audit["clusters"] == 0 and audit["merged_nodes"] == 0
    assert rt.merge_calls == []


def test_resolve_entities_degrades_when_embedder_fails():
    # embedder down → exact-norm fallback; "OpenAI" vs "openai" still merge
    nodes = [_node("u1", "OpenAI"), _node("u2", "openai"), _node("u3", "OpenAI 公司")]
    rt = _FakeRuntime(nodes, embeddings=None, fail_embed=True)
    audit = er.resolve_entities("g1", {"actors": [{"name": "OpenAI"}]}, threshold=0.9, runtime=rt)
    assert audit["merged_nodes"] == 1
    assert rt.merge_calls == [("u1", "u2")]  # only exact-norm match merged


# ----------------------------------------------- O(N) fast path + node-count cap
def test_plan_merges_fast_exact_merges_containment_skipped():
    # The O(N) fast path merges EXACT-norm duplicates but skips containment refinement.
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI"), _node("u3", "OpenAI 公司")]
    emb = [[1.0, 0.0], [1.0, 0.0], [0.99, 0.05]]
    plan = er.plan_merges_fast(nodes, emb, {"openai"}, threshold=0.9)
    victims = {v["uuid"] for c in plan for v in c["victims"]}
    survivors = {c["survivor_uuid"] for c in plan}
    assert victims == {"u2"}          # exact-norm duplicate merged
    assert survivors == {"u1"}        # canonical survives
    assert "u3" not in victims        # containment-only NOT merged by fast path


def test_resolve_entities_cap_forces_fast_path(monkeypatch):
    # nodes > cap → fast path: only exact-norm dup merges, containment 'OpenAI 公司' does not,
    # and it must return quickly without the O(N^2) scan.
    from app.config import Config
    monkeypatch.setattr(Config, "GRAPH_RESOLVE_MAX_NODES", 2, raising=False)
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI"), _node("u3", "OpenAI 公司")]
    emb = [[1.0, 0.0], [1.0, 0.0], [0.99, 0.05]]
    rt = _FakeRuntime(nodes, emb)
    audit = er.resolve_entities("g1", {"actors": [{"name": "OpenAI"}]}, threshold=0.9, runtime=rt)
    assert audit["merged_nodes"] == 1
    assert rt.merge_calls == [("u1", "u2")]


def test_resolve_entities_under_cap_keeps_full_containment(monkeypatch):
    # under cap → full plan_merges → containment 'OpenAI 公司' DOES merge (byte-identical to before)
    from app.config import Config
    monkeypatch.setattr(Config, "GRAPH_RESOLVE_MAX_NODES", 1000, raising=False)
    nodes = [_node("u1", "OpenAI"), _node("u2", "OpenAI 公司")]
    emb = [[1.0, 0.0], [0.98, 0.1]]
    rt = _FakeRuntime(nodes, emb)
    audit = er.resolve_entities("g1", {"actors": [{"name": "OpenAI"}]}, threshold=0.9, runtime=rt)
    assert audit["merged_nodes"] == 1
    assert rt.merge_calls == [("u1", "u2")]
