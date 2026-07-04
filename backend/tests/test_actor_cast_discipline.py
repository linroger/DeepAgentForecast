"""ACTOR-CAST DISCIPLINE — 主角色阵容纪律（≤ ACTOR_CAST_MAX 个 main actors）。

Offline: no LLM/network. Covers:
- Config 新旗标默认值（ACTOR_CAST_MAX=20 / ACTOR_EXCLUDE_MEDIA=true / SIM_AUDIENCE_AGENTS=0）；
- 媒体/观察者判定与降级（is_media_entity / entity_simulation_tier / is_agent_eligible，
  含 ACTOR_EXCLUDE_MEDIA 开关与显式 simulation_tier 覆盖）；
- deerflow bridge 的抽取后阵容执法（enforce_actor_cast）：媒体降级为 context、超上限按
  tier/salience/影响力排序裁剪、meta.actors_truncated_from 记录、关系边端点不变式、
  安全网（绝不清空阵容）、cap=0 恢复旧行为；
- 抽取/本体提示词携带主角色纪律措辞（cap 数字 + 媒体排除）；
- 模拟 agent 池派生（simulation_manager.select_agent_pool）：主阵容路径（不再向
  OASIS_MAX_AGENTS 填充图谱通用节点）、媒体排除、无 actors 时按 cap 裁剪、
  ACTOR_CAST_MAX unset-high 时逐字节恢复旧 T3.13 填充行为；
- 受众填充（SIM_AUDIENCE_AGENTS）：默认 0 → 无受众；>0 → M 个连续 agent_id 的受众配置
  + 与之对齐的零 LLM 受众 profile（generate_audience_profiles）。
"""

import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE = os.path.join(os.path.dirname(_BACKEND), "deerflow_bridge")
for _p in (_BACKEND, _BRIDGE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import deerflow_research as dr  # noqa: E402
from app.config import Config  # noqa: E402
from app.services.oasis_profile_generator import OasisProfileGenerator  # noqa: E402
from app.services.simulation_config_generator import (  # noqa: E402
    EventConfig,
    SimulationConfigGenerator,
)
from app.services.simulation_manager import select_agent_pool  # noqa: E402
from app.services.zep_entity_reader import EntityNode  # noqa: E402
from app.utils.actors import (  # noqa: E402
    entity_simulation_tier,
    is_agent_eligible,
    is_media_entity,
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _actor(name, typ="Organization", influence="medium", **kw):
    row = {"name": name, "type": typ, "influence": influence}
    row.update(kw)
    return row


def _entity(name, n_edges=0):
    return EntityNode(
        uuid=f"uuid-{name}",
        name=name,
        labels=["Entity", "Organization"],
        summary=f"{name} summary",
        attributes={},
        related_edges=[{"uuid": f"e{i}"} for i in range(n_edges)],
    )


def _make_actors(rows):
    return {"actors": rows, "relationships": []}


# ---------------------------------------------------------------------------
# 1. Config 默认值
# ---------------------------------------------------------------------------

def test_config_defaults():
    assert Config.ACTOR_CAST_MAX == 20
    assert Config.ACTOR_EXCLUDE_MEDIA is True
    assert Config.SIM_AUDIENCE_AGENTS == 0


# ---------------------------------------------------------------------------
# 2. 媒体/观察者判定与 tier 降级（actors.py）
# ---------------------------------------------------------------------------

def test_is_media_entity_by_type():
    assert is_media_entity(_actor("路透社", typ="Media")) is True
    assert is_media_entity(_actor("美国商务部", typ="Government")) is False


def test_is_media_entity_by_role_keywords():
    assert is_media_entity(_actor("张三", typ="Person", role="senior journalist")) is True
    assert is_media_entity(_actor("某智库", typ="Organization", role="智库研究机构")) is True
    assert is_media_entity(_actor("英伟达", typ="Organization", role="AI芯片供应商")) is False


def test_is_media_entity_by_source_archetype():
    assert is_media_entity(_actor("某报告", archetype="source")) is True


def test_is_media_entity_explicit_core_tier_overrides():
    # 研究方显式判定该媒体本身推动结局（tier 1/2）→ 不降级
    assert is_media_entity(_actor("福克斯新闻", typ="Media", simulation_tier=1)) is False
    assert is_media_entity(_actor("福克斯新闻", typ="Media", simulation_tier=2)) is False


def test_media_actor_demoted_to_tier3_when_flag_on(monkeypatch):
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    media = _actor("路透社", typ="Media", influence="high")
    assert entity_simulation_tier(media) == 3
    assert is_agent_eligible(media) is False


def test_media_actor_kept_when_flag_off(monkeypatch):
    # 旧行为恢复口：flag 关 → Media 默认按能动 actor 推断（influence high → tier 1）
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", False, raising=False)
    media = _actor("路透社", typ="Media", influence="high")
    assert entity_simulation_tier(media) == 1
    assert is_agent_eligible(media) is True


def test_explicit_tier_beats_media_inference(monkeypatch):
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    media_principal = _actor("福克斯新闻", typ="Media", influence="high", simulation_tier=1)
    assert entity_simulation_tier(media_principal) == 1
    assert is_agent_eligible(media_principal) is True


def test_non_media_actor_inference_unchanged(monkeypatch):
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    assert entity_simulation_tier(_actor("英伟达", influence="high")) == 1
    assert entity_simulation_tier(_actor("某协会", influence="low")) == 2
    assert entity_simulation_tier(None) == 1  # 无信号 → 默认能动（旧行为）


# ---------------------------------------------------------------------------
# 3. deerflow bridge：抽取后阵容执法（enforce_actor_cast）
# ---------------------------------------------------------------------------

def _bridge_obj(n_actors, media_names=(), rels=None):
    actors = []
    for i in range(n_actors):
        name = f"Actor{i}"
        influence = "high" if i < 5 else ("medium" if i < 15 else "low")
        actors.append({"name": name, "type": "Organization",
                       "role": f"player {i}", "influence": influence})
    for m in media_names:
        actors.append({"name": m, "type": "Media", "role": "news outlet",
                       "influence": "high"})
    return {"actors": actors, "relationships": list(rels or [])}


def test_bridge_cap_truncation_and_meta(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = _bridge_obj(30)
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert len(obj["actors"]) == 20
    assert meta["actors_truncated_from"] == 30
    # 排序裁剪：高影响力（前 5 个 high）必须全部保留
    kept_names = {a["name"] for a in obj["actors"]}
    for i in range(5):
        assert f"Actor{i}" in kept_names
    # 被裁掉的保留为 context_entities，不是凭空丢弃
    assert len(obj["context_entities"]) == 10


def test_bridge_media_demoted_to_context(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = _bridge_obj(10, media_names=("路透社", "CNN"))
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    kept_names = {a["name"] for a in obj["actors"]}
    assert "路透社" not in kept_names and "CNN" not in kept_names
    assert meta["actors_media_demoted"] == 2
    assert "actors_truncated_from" not in meta  # 12-2=10 ≤ 20，无排序裁剪
    ctx_names = {a["name"] for a in obj["context_entities"]}
    assert {"路透社", "CNN"} <= ctx_names


def test_bridge_media_with_explicit_core_tier_kept(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = {"actors": [
        {"name": "福克斯新闻", "type": "Media", "influence": "high", "simulation_tier": 1},
        {"name": "美国商务部", "type": "Government", "influence": "high"},
    ], "relationships": []}
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert {a["name"] for a in obj["actors"]} == {"福克斯新闻", "美国商务部"}
    assert meta == {}


def test_bridge_offcast_relationships_dropped(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    rels = [
        {"source": "Actor0", "target": "Actor1", "type": "OPPOSES"},
        {"source": "Actor0", "target": "路透社", "type": "REPORTS_ON"},
    ]
    obj = _bridge_obj(10, media_names=("路透社",), rels=rels)
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert len(obj["relationships"]) == 1
    assert obj["relationships"][0]["target"] == "Actor1"
    assert meta["relationships_dropped_offcast"] == 1


def test_bridge_noop_when_under_cap_and_no_media(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = _bridge_obj(8)
    before = [dict(a) for a in obj["actors"]]
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert obj["actors"] == before
    assert "context_entities" not in obj
    assert meta == {}


def test_bridge_cap_zero_disables_truncation(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "0")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "false")
    obj = _bridge_obj(40, media_names=("路透社",))
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert len(obj["actors"]) == 41  # 旧行为：不裁剪、不降级
    assert meta == {}


def test_bridge_safety_net_never_empties_cast(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = {"actors": [
        {"name": "路透社", "type": "Media", "influence": "high"},
        {"name": "CNN", "type": "Media", "influence": "medium"},
    ], "relationships": []}
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert len(obj["actors"]) == 2  # 全媒体阵容：跳过降级而不是清空


def test_bridge_aliases_keep_relationship_endpoints(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    monkeypatch.setenv("ACTOR_EXCLUDE_MEDIA", "true")
    obj = {
        "actors": (
            [{"name": "NVIDIA", "type": "Organization", "influence": "high",
              "aliases": ["英伟达"]}]
            + [{"name": f"Actor{i}", "type": "Organization", "influence": "low"}
               for i in range(25)]
        ),
        "relationships": [
            {"source": "英伟达", "target": "Actor0", "type": "SUPPLIES"},
        ],
    }
    meta = {}
    dr.enforce_actor_cast(obj, meta)
    assert len(obj["actors"]) == 20
    # NVIDIA (high) 与 Actor0 均在阵容内；别名端点的边保留
    assert len(obj["relationships"]) == 1


# ---------------------------------------------------------------------------
# 4. 提示词措辞（抽取 + 本体）
# ---------------------------------------------------------------------------

def test_extraction_prompt_carries_cast_discipline(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    prompt = dr.build_extraction_prompt(None, "deep")
    assert "8-20 specific, named real-world actors" in prompt
    assert "MAIN ACTORS ONLY" in prompt
    assert "causally affect the outcome" in prompt
    assert "FORCE-RANK" in prompt
    assert "AT MOST 20 actors" in prompt
    assert "EXCLUDE media organizations, journalists, commentators" in prompt


def test_extraction_prompt_cap_zero_restores_old_range(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "0")
    prompt = dr.build_extraction_prompt(None, "deep")
    assert "10-35 specific, named real-world actors" in prompt


def test_actor_ontology_prompt_carries_cap(monkeypatch):
    monkeypatch.setenv("ACTOR_CAST_MAX", "20")
    prompt = dr.build_actor_ontology_prompt("Will X happen?", "deep", None)
    assert "NEVER more than 20" in prompt
    assert "journalists, commentators, analysts, and pollsters" in prompt


def test_ontology_generator_prompt_carries_discipline(monkeypatch):
    from app.services.ontology_generator import OntologyGenerator
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    for template in ("social_opinion", "general_forecast"):
        prompt = OntologyGenerator._effective_system_prompt(template)
        assert "主角色纪律" in prompt
        assert "≤20 个主角色" in prompt
        assert "不是能动 actor" in prompt


def test_ontology_generator_prompt_discipline_off(monkeypatch):
    from app.services.ontology_generator import OntologyGenerator
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 0, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", False, raising=False)
    prompt = OntologyGenerator._effective_system_prompt("general_forecast")
    assert "主角色纪律" not in prompt


# ---------------------------------------------------------------------------
# 5. 模拟 agent 池派生（select_agent_pool）
# ---------------------------------------------------------------------------

def test_pool_cast_discipline_caps_and_drops_unmatched(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    rows = [_actor(f"Cast{i}", influence=("high" if i < 10 else "low")) for i in range(25)]
    entities = [_entity(f"Cast{i}", n_edges=i) for i in range(25)]
    entities += [_entity(f"GraphNoise{i}", n_edges=50) for i in range(40)]  # 未匹配图谱节点
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    assert len(kept) == 20
    names = {e.name for e in kept}
    assert all(n.startswith("Cast") for n in names)  # 不再向 80 填充图谱噪声节点
    for i in range(10):
        assert f"Cast{i}" in names  # 高影响力主阵容必留


def test_pool_cast_discipline_excludes_media_actors(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    rows = [_actor(f"Cast{i}", influence="high") for i in range(5)]
    rows.append(_actor("路透社", typ="Media", influence="high"))
    entities = [_entity(f"Cast{i}") for i in range(5)] + [_entity("路透社")]
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    names = {e.name for e in kept}
    assert "路透社" not in names
    assert len(kept) == 5


def test_pool_media_kept_when_exclusion_off(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", False, raising=False)
    rows = [_actor("Cast0", influence="high"), _actor("路透社", typ="Media", influence="high")]
    entities = [_entity("Cast0"), _entity("路透社")]
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    assert {e.name for e in kept} == {"Cast0", "路透社"}


def test_pool_no_actors_falls_back_to_cap(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    entities = [_entity(f"E{i}", n_edges=i) for i in range(60)]
    kept = select_agent_pool(entities, actors=None)
    assert len(kept) == 20
    # 无 actors 时按旧排序键（邻边度数）保留最高的 20 个
    assert {e.name for e in kept} == {f"E{i}" for i in range(40, 60)}


def test_pool_dedupes_unresolved_alias_nodes_of_same_actor(monkeypatch):
    """2026-07-03 live-surfaced：一次真实 forecast run 的图谱把「China」「CCP」×2
    「Beijing」×2「Government of the People's Republic of China」「MOFCOM」解析成 6 个
    不同图谱节点，而 actors.json 里这些全是同一条记录的 aliases——select_agent_pool
    过滤/排序对它们一视同仁，20 席阵容里同一个真实 actor 就占了 6 席。
    修复：按匹配到的 actor 规范名去重，同一 actor 的多个别名节点只保留排序最靠前的一个。
    """
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    rows = [
        _actor(
            "Government of the People's Republic of China",
            influence="high",
            aliases=["PRC", "CCP", "China", "Beijing", "MOFCOM"],
        ),
    ]
    rows += [_actor(f"Cast{i}", influence="medium") for i in range(10)]
    # 6 个不同图谱节点，全部别名匹配同一条 actor 记录 —— 邻边数递增以制造明确的排序优先级。
    alias_surface_forms = ["China", "CCP", "Beijing", "Government of the People's Republic of China",
                           "MOFCOM", "Beijing"]
    entities = [_entity(name, n_edges=i) for i, name in enumerate(alias_surface_forms)]
    entities += [_entity(f"Cast{i}", n_edges=100) for i in range(10)]  # 确保阵容其余席位不受影响
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    kept_names = [e.name for e in kept]
    china_alias_hits = sum(1 for n in kept_names if n in alias_surface_forms)
    assert china_alias_hits == 1, f"same real actor should occupy exactly one seat, got {kept_names}"
    # 去重后应保留邻边数最高（排序最靠前）的别名节点：alias_surface_forms 里最后一个
    # "Beijing"（index 5, n_edges=5）优先于其它同名/同 actor 的低邻边数节点。
    assert "Beijing" in kept_names
    # 其余 10 个 distinct Cast 主体一个都不应因去重而被误伤。
    for i in range(10):
        assert f"Cast{i}" in kept_names


def test_pool_legacy_recovery_unset_high(monkeypatch):
    # ACTOR_CAST_MAX ≥ OASIS_MAX_AGENTS → 旧 T3.13 行为：匹配 actor 必留 + 填充到 80
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 999, raising=False)
    rows = [_actor(f"Cast{i}", influence="high") for i in range(10)]
    entities = [_entity(f"Cast{i}") for i in range(10)]
    entities += [_entity(f"GraphNoise{i}", n_edges=i) for i in range(100)]
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    assert len(kept) == 80  # 填充回旧上限
    names = {e.name for e in kept}
    assert all(f"Cast{i}" in names for i in range(10))
    assert any(n.startswith("GraphNoise") for n in names)  # 旧的填充行为可恢复


def test_pool_legacy_under_cap_untouched(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 999, raising=False)
    entities = [_entity(f"E{i}") for i in range(30)]
    kept = select_agent_pool(entities, actors=None)
    assert [e.name for e in kept] == [f"E{i}" for i in range(30)]


def test_pool_all_media_safety_net(monkeypatch):
    monkeypatch.setattr(Config, "OASIS_MAX_AGENTS", 80, raising=False)
    monkeypatch.setattr(Config, "ACTOR_CAST_MAX", 20, raising=False)
    monkeypatch.setattr(Config, "ACTOR_EXCLUDE_MEDIA", True, raising=False)
    rows = [_actor(f"Media{i}", typ="Media", influence="high") for i in range(3)]
    entities = [_entity(f"Media{i}") for i in range(3)]
    kept = select_agent_pool(entities, actors=_make_actors(rows))
    assert len(kept) == 3  # 全媒体匹配：回退保留全部匹配者，池子绝不清空


# ---------------------------------------------------------------------------
# 6. 受众填充（SIM_AUDIENCE_AGENTS）
# ---------------------------------------------------------------------------

def _config_gen():
    return SimulationConfigGenerator.__new__(SimulationConfigGenerator)


def test_audience_default_zero_no_configs(monkeypatch):
    monkeypatch.setattr(Config, "SIM_AUDIENCE_AGENTS", 0, raising=False)
    monkeypatch.setattr(Config, "SIM_AUDIENCE_SIZE", 0, raising=False)
    out = _config_gen()._generate_audience_agent_configs(
        start_idx=20, event_config=EventConfig(), actors=None)
    assert out == []


def test_audience_positive_generates_sequential_ids(monkeypatch):
    monkeypatch.setattr(Config, "SIM_AUDIENCE_AGENTS", 5, raising=False)
    out = _config_gen()._generate_audience_agent_configs(
        start_idx=20, event_config=EventConfig(hot_topics=["AI 芯片"]), actors=None)
    assert len(out) == 5
    assert [c.agent_id for c in out] == [20, 21, 22, 23, 24]
    assert all(c.entity_type == SimulationConfigGenerator.AUDIENCE_ENTITY_TYPE for c in out)
    assert all(c.influence_weight <= 0.8 for c in out)  # 低影响力潜水受众


def test_audience_legacy_attr_name_still_honored(monkeypatch):
    # 兼容旧名 SIM_AUDIENCE_SIZE（I-2-2 的属性注入口径）
    monkeypatch.setattr(Config, "SIM_AUDIENCE_AGENTS", 0, raising=False)
    monkeypatch.setattr(Config, "SIM_AUDIENCE_SIZE", 3, raising=False)
    out = _config_gen()._generate_audience_agent_configs(
        start_idx=10, event_config=EventConfig(), actors=None)
    assert len(out) == 3


def test_generate_audience_profiles_aligned_with_configs(monkeypatch):
    monkeypatch.setattr(Config, "SIM_AUDIENCE_AGENTS", 4, raising=False)
    cfgs = _config_gen()._generate_audience_agent_configs(
        start_idx=20, event_config=EventConfig(hot_topics=["出口管制"]), actors=None)
    gen = OasisProfileGenerator.__new__(OasisProfileGenerator)
    gen.persona_language = None
    profiles = gen.generate_audience_profiles(cfgs, start_user_id=20)
    assert len(profiles) == 4
    assert [p.user_id for p in profiles] == [20, 21, 22, 23]
    for p, c in zip(profiles, cfgs):
        assert p.name == c.entity_name  # 名称与配置一一对应（关注图/名字映射依赖）
        assert p.source_entity_type == SimulationConfigGenerator.AUDIENCE_ENTITY_TYPE
        assert p.generation_path == "rule"
        assert "silent" in p.persona  # 沉默大多数人设
