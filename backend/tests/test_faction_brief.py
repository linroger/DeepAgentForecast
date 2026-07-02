"""Offline tests for faction-aware GraphRAG (EXECPLAN2 I-1-2).

Covers faction_brief formatting + degrade-to-coalition_map, and the persona
community-identity matching — without a real graph (runtime is monkeypatched).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.services.graphiti_client.runtime as rt_mod  # noqa: E402
from app.config import Config  # noqa: E402
from app.services.oasis_profile_generator import OasisProfileGenerator  # noqa: E402
from app.services.zep_tools import ZepToolsService, NodeInfo, EdgeInfo  # noqa: E402


class _FakeRT:
    def __init__(self, communities):
        self._c = communities

    def list_communities(self, graph_id):
        return self._c


def _bare_service():
    # bypass __init__ (which builds a Zep client) — faction_brief only needs
    # self.coalition_map + get_runtime + Config.
    svc = ZepToolsService.__new__(ZepToolsService)
    svc.coalition_map = lambda gid, sid: f"COALITION_MAP({gid},{sid})"
    return svc


# --------------------------------------------------------------- faction_brief
def test_faction_brief_disabled_degrades_to_coalition_map(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", False)
    svc = _bare_service()
    assert svc.faction_brief("g1", "", "sim1") == "COALITION_MAP(g1,sim1)"


def test_faction_brief_disabled_no_sim_returns_notice(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", False)
    svc = _bare_service()
    out = svc.faction_brief("g1", "")
    assert "未启用" in out


def test_faction_brief_no_communities_degrades(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT([]))
    svc = _bare_service()
    assert svc.faction_brief("g1", "", "sim1") == "COALITION_MAP(g1,sim1)"


def test_faction_brief_renders_communities_ranked(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    comms = [
        {"name": "小阵营", "summary": "s1", "members": ["A"]},
        {"name": "大阵营", "summary": "联盟摘要", "members": ["X", "Y", "Z"]},
    ]
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT(comms))
    svc = _bare_service()
    out = svc.faction_brief("g1", "")
    assert "派系/社区简报" in out
    # larger community ranked first
    assert out.index("大阵营") < out.index("小阵营")
    assert "联盟摘要" in out
    assert "X、Y、Z" in out


def test_faction_brief_query_prioritizes_relevant(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    comms = [
        {"name": "big", "summary": "", "members": ["a", "b", "c", "d"]},
        {"name": "small-semiconductor", "summary": "semiconductor faction", "members": ["m"]},
    ]
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT(comms))
    svc = _bare_service()
    out = svc.faction_brief("g1", "semiconductor")
    # query relevance beats raw size
    assert out.index("small-semiconductor") < out.index("big")


def test_faction_brief_tolerates_non_string_members(monkeypatch):
    # adversarial-review regression: graph could return non-string member names;
    # join() must not TypeError (defensive str() coercion).
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    comms = [{"name": 42, "summary": None, "members": ["A", 7, None, {"x": 1}]}]
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT(comms))
    svc = _bare_service()
    out = svc.faction_brief("g1", "a")  # query path also exercises the hay join
    assert "派系" in out  # rendered without crashing


def test_faction_brief_runtime_error_degrades(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)

    def _boom():
        raise RuntimeError("graph down")

    monkeypatch.setattr(rt_mod, "get_runtime", _boom)
    svc = _bare_service()
    assert svc.faction_brief("g1", "", "sim1") == "COALITION_MAP(g1,sim1)"


# -------------------------------------------- R2-KG-11 inter-community tension
def test_inter_community_tension_matrix_renders_antagonism(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    comms = [
        {"name": "派系A", "summary": "", "members": ["Alice", "Bob"]},
        {"name": "派系B", "summary": "", "members": ["Xavier", "Yara"]},
    ]
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT(comms))
    svc = _bare_service()
    nodes = [
        NodeInfo("n1", "Alice", ["Person"], "", {}),
        NodeInfo("n2", "Bob", ["Person"], "", {}),
        NodeInfo("n3", "Xavier", ["Person"], "", {}),
        NodeInfo("n4", "Yara", ["Person"], "", {}),
    ]
    edges = [
        EdgeInfo("e1", "OPPOSES", "Alice opposes Xavier", "n1", "n3"),
        EdgeInfo("e2", "OPPOSES", "Bob opposes Yara", "n2", "n4"),
    ]
    svc.get_all_nodes = lambda gid: nodes
    svc.get_all_edges = lambda gid: edges
    out = svc.faction_brief("g1", "")
    assert "社区间张力矩阵" in out
    assert "派系A ↔ 派系B" in out
    assert "对抗" in out          # net polarity negative → antagonistic


def test_inter_community_tension_no_cross_edges_is_silent(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_COMMUNITY_RETRIEVAL", True)
    comms = [
        {"name": "派系A", "summary": "", "members": ["Alice"]},
        {"name": "派系B", "summary": "", "members": ["Xavier"]},
    ]
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _FakeRT(comms))
    svc = _bare_service()
    # only an INTRA-community edge → no cross-boundary tension to report
    svc.get_all_nodes = lambda gid: [NodeInfo("n1", "Alice", ["Person"], "", {})]
    svc.get_all_edges = lambda gid: [EdgeInfo("e1", "SUPPORTS", "f", "n1", "n1")]
    out = svc.faction_brief("g1", "")
    assert "社区间张力矩阵" not in out   # degrade-safe: no spurious section


# ------------------------------------------- R2-KG-9 insight_forge node-scope
def test_insight_forge_folds_node_scope_summaries():
    import threading

    from app.services.zep_tools import SearchResult

    svc = ZepToolsService.__new__(ZepToolsService)
    svc._coverage = {}
    svc._cache_lock = threading.RLock()
    svc._generate_sub_queries = lambda **kw: []
    svc.get_all_nodes = lambda gid: [
        NodeInfo("n1", "EdgeActor", ["Company"], "edge summary", {})
    ]

    def fake_search(graph_id, query, limit=10, scope="edges", recipe=None,
                    search_filter=None):
        if scope == "nodes":
            # a node with a rich summary but NO edges → only reachable via node-scope
            return SearchResult(
                facts=[], edges=[],
                nodes=[{"uuid": "n2", "name": "NodeOnlyActor", "labels": ["Person"],
                        "summary": "a key node-only actor"}],
                query=query, total_count=0,
            )
        return SearchResult(
            facts=["EdgeActor does X"],
            edges=[{"uuid": "e1", "name": "CAUSES", "fact": "EdgeActor does X",
                    "source_node_uuid": "n1", "target_node_uuid": "n1"}],
            nodes=[], query=query, total_count=1,
        )

    svc.search_graph = fake_search
    res = svc.insight_forge("g1", "Q", "sim req")
    names = {ei["name"] for ei in res.entity_insights}
    assert "EdgeActor" in names        # edge-derived entity (existing behavior)
    assert "NodeOnlyActor" in names    # R2-KG-9: node-scope summary folded in
    assert res.total_entities == len(res.entity_insights)


# ------------------------------------------------ R2-KG-2 trace_cascade resolve
def test_resolve_entity_name_normalizes_to_graph_node():
    svc = ZepToolsService.__new__(ZepToolsService)
    svc.get_all_nodes = lambda gid: [
        NodeInfo("n1", "OpenAI, Inc.", ["Company"], "", {}),
        NodeInfo("n2", "Anthropic", ["Company"], "", {}),
    ]
    # case/punctuation/containment differences resolve to the real node name
    assert svc._resolve_entity_name("g1", "openai") == "OpenAI, Inc."
    # unmatched input is returned verbatim (degrade-safe)
    assert svc._resolve_entity_name("g1", "Tesla") == "Tesla"
    assert svc._resolve_entity_name("g1", "") == ""


def test_resolve_entity_name_degrades_on_read_failure():
    svc = ZepToolsService.__new__(ZepToolsService)

    def _boom(gid):
        raise RuntimeError("graph down")

    svc.get_all_nodes = _boom
    assert svc._resolve_entity_name("g1", "OpenAI") == "OpenAI"  # falls back to raw


def test_fmt_edge_label_handles_bare_and_rich():
    # bare relation name (current runtime projection)
    assert ZepToolsService._fmt_edge_label("CAUSES") == "CAUSES"
    # rich per-hop dict (forward-compat with sign/strength/lag projection)
    rich = {"name": "CONSTRAINS", "sign": "-", "strength": "high", "lag": "2q"}
    out = ZepToolsService._fmt_edge_label(rich)
    assert out.startswith("CONSTRAINS")
    assert "sign=-" in out and "strength=high" in out and "lag=2q" in out


# ------------------------------------------------------ persona community match
def _bare_generator(cache):
    gen = OasisProfileGenerator.__new__(OasisProfileGenerator)
    gen.graph_id = "g1"
    gen._communities_cache = cache
    return gen


def test_community_for_entity_normalized_match():
    gen = _bare_generator([{"name": "阵营A", "summary": "x", "members": ["OpenAI", "Anthropic"]}])
    assert gen._community_for_entity("openai")["name"] == "阵营A"     # case/normalize
    assert gen._community_for_entity("OpenAI 公司")["name"] == "阵营A"  # containment
    assert gen._community_for_entity("Tesla") is None                  # no match


def test_community_for_entity_guards():
    gen = _bare_generator([])
    assert gen._community_for_entity("") is None
    gen2 = OasisProfileGenerator.__new__(OasisProfileGenerator)
    gen2.graph_id = None
    gen2._communities_cache = None
    assert gen2._community_for_entity("OpenAI") is None  # no graph_id → None
