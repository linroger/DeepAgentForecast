# -*- coding: utf-8 -*-
"""Audit fixes — ontology group (ONT-1/3/5/6/9/11 + PREP-5).

每个测试对应 findings_ontology.json 的一条已核实缺陷；断言修复后的行为，
并覆盖 degrade-safe 路径（旗标关闭 → 旧行为）。
"""

import pytest

from app.config import Config
from app.utils.actors import events_to_schedule, ontology_seed_block
from app.services.ontology_generator import (
    ONTOLOGY_RICH_SCHEMA_ADDENDUM,
    SOCIAL_OPINION_KEYWORDS,
    OntologyGenerator,
)


class _DummyLLM:
    """_validate_and_process 等纯后处理路径不触达 LLM，占位即可。"""


@pytest.fixture
def gen():
    return OntologyGenerator(llm_client=_DummyLLM())


# ----------------------------------------- ONT-1: seed block 自适应新增预算
def test_seed_budget_adapts_to_coarse_cast():
    # 只有 2 个粗类型（4 类型回归的实际形态）→ 允许补足到 8：新增预算 6
    actors = {"actors": [
        {"name": "US Gov", "type": "Government"},
        {"name": "OPEC", "type": "Organization"},
    ]}
    block = ontology_seed_block(actors)
    assert "至多再新增 6 个" in block
    assert "总数不超过 10" in block
    assert "Government" in block and "Organization" in block


def test_seed_budget_floor_stays_two_for_rich_cast():
    # 种子已有 8 个类型 → 预算回到下限 2（不放大）
    types = ["T%d" % i for i in range(8)]
    actors = {"actors": [{"name": f"A{i}", "type": t} for i, t in enumerate(types)]}
    block = ontology_seed_block(actors)
    assert "至多再新增 2 个" in block


def test_seed_budget_flag_off_restores_legacy(monkeypatch):
    monkeypatch.setattr(Config, "ONTOLOGY_SEED_ADAPTIVE_BUDGET", False, raising=False)
    actors = {"actors": [{"name": "US Gov", "type": "Government"}]}
    assert "至多再新增 2 个" in ontology_seed_block(actors)


def test_seed_block_still_degrades_to_empty():
    assert ontology_seed_block(None) == ""
    assert ontology_seed_block({"actors": [{"name": "A"}]}) == ""  # 无 type 无 rels


# ----------------------------------------- PREP-5: key_event 不再钉死在最后一轮
def test_farthest_event_leaves_reaction_rounds():
    # 复现 sim_440ef4b11fa8：as_of 2026-07-01，事件 2026-11-27，72 轮。
    # 旧逻辑 → 71（最后一轮，零轮可反应）；修复后压进缓冲窗口。
    actors = {"key_events": [{"date": "2026-11-27", "event": "OPEC decision"}]}
    sched = events_to_schedule(actors, total_rounds=72, as_of_date="2026-07-01")
    assert len(sched) == 1
    r = sched[0]["round"]
    buffer_rounds = max(2, 72 // 5)
    assert r == 72 - buffer_rounds  # 最远事件落在缓冲窗口末端
    assert r <= 72 - 1 - 2          # 至少留 2 轮反应期


def test_event_buffer_flag_off_restores_legacy(monkeypatch):
    monkeypatch.setattr(Config, "SIM_EVENT_REACT_BUFFER", False, raising=False)
    actors = {"key_events": [{"date": "2026-11-27", "event": "OPEC decision"}]}
    sched = events_to_schedule(actors, total_rounds=72, as_of_date="2026-07-01")
    assert sched[0]["round"] == 71  # 旧行为：夹在最后一轮


def test_event_schedule_degrade_paths_unchanged():
    assert events_to_schedule(None, 72, "2026-07-01") == []
    assert events_to_schedule({"key_events": "junk"}, 72, "2026-07-01") == []
    assert events_to_schedule({"key_events": [{"date": "someday"}]}, 72, "2026-07-01") == []
    # 早于 as_of 的事件仍被跳过
    actors = {"key_events": [{"date": "2026-01-01", "event": "past"}]}
    assert events_to_schedule(actors, 72, "2026-07-01") == []


def test_event_schedule_ordering_preserved():
    # 较近事件仍映射到较早轮次（比例映射不变，仅窗口压缩）
    actors = {"key_events": [
        {"date": "2026-08-01", "event": "near"},
        {"date": "2026-11-27", "event": "far"},
    ]}
    sched = events_to_schedule(actors, total_rounds=72, as_of_date="2026-07-01")
    rounds = {e["event"]: e["round"] for e in sched}
    assert rounds["near"] < rounds["far"]


# ----------------------------------------- ONT-3: null description 不再 TypeError
def test_null_description_does_not_crash(gen):
    result = {
        "entity_types": [{"name": "Government", "description": None}],
        "edge_types": [{"name": "REGULATES", "description": None}],
    }
    out = gen._validate_and_process(result)  # 修复前：TypeError len(None)
    ent = [e for e in out["entity_types"] if e["name"] == "Government"][0]
    assert ent["description"] == ""
    edge = [e for e in out["edge_types"] if e["name"] == "REGULATES"][0]
    assert edge["description"] == ""


def test_long_description_still_truncated(gen):
    long = "x" * 150
    out = gen._validate_and_process({
        "entity_types": [{"name": "Government", "description": long}],
        "edge_types": [{"name": "REGULATES", "description": long}],
    })
    ent = [e for e in out["entity_types"] if e["name"] == "Government"][0]
    assert ent["description"] == "x" * 97 + "..."


# ----------------------------------------- ONT-5: 数据缺失 ≠ 实证无人/无组织
def test_fallback_needs_absent_actors_returns_safe(gen):
    assert gen._fallback_needs_from_actors(None) == (True, True)
    assert gen._fallback_needs_from_actors({}) == (True, True)
    assert gen._fallback_needs_from_actors({"actors": []}) == (True, True)
    assert gen._fallback_needs_from_actors({"actors": "junk"}) == (True, True)
    # 阵容存在但 type 全不可用 → 同样按降级处理
    assert gen._fallback_needs_from_actors({"actors": [{"name": "A"}]}) == (True, True)


def test_fallback_needs_pure_object_cast_still_skips(gen):
    # 真实非空阵容、纯客体类型 → 仍允许不注入兜底（把预算留给领域类型）
    actors = {"actors": [{"name": "BTC", "type": "Asset"}, {"name": "Gold", "type": "Commodity"}]}
    assert gen._fallback_needs_from_actors(actors) == (False, False)


def test_fallback_needs_person_org_detection_unchanged(gen):
    actors = {"actors": [{"name": "A", "type": "Person"}, {"name": "B", "type": "Company"}]}
    assert gen._fallback_needs_from_actors(actors) == (True, True)


def test_missing_actors_json_injects_fallbacks_general_forecast(gen):
    # 端到端：general_forecast + actors 缺失 → Person/Organization 兜底被注入
    out = gen._validate_and_process(
        {"entity_types": [{"name": "ChokepointAsset", "description": "d"}], "edge_types": []},
        template="general_forecast",
        actors=None,
    )
    names = {e["name"] for e in out["entity_types"]}
    assert {"Person", "Organization"} <= names


# ----------------------------------------- ONT-6: 因果边族不再在出生时丢失
def test_addendum_family_enum_includes_causal():
    assert "`causal`" in ONTOLOGY_RICH_SCHEMA_ADDENDUM


def test_causal_edge_name_overrides_llm_family():
    # 实测形态（pipe_a335177097fb）：CONSTRAINS 被 LLM 标成 dependency/adversarial
    result = {"entity_types": [], "edge_types": [
        {"name": "CONSTRAINS", "family": "dependency", "valence": "adversarial"},
        {"name": "CAUSES"},  # 漏标 → 补全
    ]}
    OntologyGenerator._normalize_rich_schema(result)
    by_name = {e["name"]: e for e in result["edge_types"]}
    assert by_name["CONSTRAINS"]["family"] == "causal"
    assert by_name["CONSTRAINS"]["valence"] == "directional"
    assert by_name["CAUSES"]["family"] == "causal"
    assert by_name["CAUSES"]["valence"] == "directional"


def test_non_causal_llm_values_still_preserved():
    # 非因果边名：LLM 显式值一律保留（原契约不变）
    result = {"entity_types": [], "edge_types": [
        {"name": "REGULATES", "family": "influence", "valence": "neutral"},
        {"name": "REGULATES_X"},  # 未知边名漏标 → other/neutral
    ]}
    OntologyGenerator._normalize_rich_schema(result)
    by_name = {e["name"]: e for e in result["edge_types"]}
    assert by_name["REGULATES"]["family"] == "influence"
    assert by_name["REGULATES"]["valence"] == "neutral"
    assert by_name["REGULATES_X"]["family"] == "other"


# ----------------------------------------- ONT-9: 死代码 generate_python_code 已删除
def test_generate_python_code_removed():
    assert not hasattr(OntologyGenerator, "generate_python_code")


# ----------------------------------------- ONT-11: 分类器不再被裸单词误触发
def test_bare_ambiguous_keywords_removed():
    for bad in ("sentiment", "opinion", "情绪", "观点"):
        assert bad not in SOCIAL_OPINION_KEYWORDS


def test_market_sentiment_routes_general_forecast():
    q = "Will investor sentiment push BTC above $100k? Expert opinion is divided."
    assert OntologyGenerator._auto_select_template(q, "social_opinion") == "general_forecast"


def test_public_opinion_still_routes_social():
    assert OntologyGenerator._auto_select_template(
        "How will public opinion react to the scandal?", "general_forecast"
    ) == "social_opinion"
    assert OntologyGenerator._auto_select_template(
        "该事件的网络舆论走向如何？", "general_forecast"
    ) == "social_opinion"


def test_empty_prompt_returns_default():
    assert OntologyGenerator._auto_select_template("", "social_opinion") == "social_opinion"
