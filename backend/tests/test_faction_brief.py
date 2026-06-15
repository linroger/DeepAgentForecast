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
from app.services.zep_tools import ZepToolsService  # noqa: E402


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
