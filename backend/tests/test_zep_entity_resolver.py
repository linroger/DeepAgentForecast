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
