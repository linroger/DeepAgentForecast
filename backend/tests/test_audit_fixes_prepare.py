"""Audit fixes — prepare group (PREP-1/2/4/8/9/10/11, XRUN-10/13).

Offline: no LLM/network. Generator instances are built via __new__ (matching the
existing test convention) and LLM entry points are stubbed per-test.
"""

import json

import pytest

from app.config import Config
from app.services.simulation_config_generator import (
    ACTIVITY_PROFILES,
    AgentActivityConfig,
    SimulationConfigGenerator,
    TimeSimulationConfig,
)
from app.services.oasis_profile_generator import (
    OasisAgentProfile,
    OasisProfileGenerator,
)
from app.utils.actors import dossier_coverage, salience_score


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _StubEntity:
    """EntityNode 的最小鸭子类型替身（name/uuid/summary/attributes/get_entity_type）。"""

    def __init__(self, name, entity_type, uuid="u", summary=""):
        self.name = name
        self.uuid = uuid
        self.summary = summary
        self.attributes = {}
        self._t = entity_type

    def get_entity_type(self):
        return self._t


def _gen():
    return SimulationConfigGenerator.__new__(SimulationConfigGenerator)


def _agents(specs):
    """[(agent_id, name, influence), ...] → AgentActivityConfig list."""
    return [
        AgentActivityConfig(agent_id=i, entity_uuid=f"u{i}", entity_name=n,
                            entity_type="Organization", influence_weight=w)
        for (i, n, w) in specs
    ]


# ---------------------------------------------------------------------------
# PREP-1: scheduled events clamped to the executed round budget
# ---------------------------------------------------------------------------

_ACTORS_TIMELINE = {
    "as_of_date": "2026-06-01",
    "key_events": [
        {"date": "2026-11-27", "event": "Rare-earth truce expiry — forecast flashpoint"},
    ],
}


def test_scheduled_events_clamped_by_explicit_max_rounds():
    g = _gen()
    tc = TimeSimulationConfig(total_simulation_hours=72, minutes_per_round=60)  # 72 config rounds
    agents = _agents([(0, "PRC", 3.0), (1, "US Federal Executive", 2.5)])
    events = g._build_scheduled_events(_ACTORS_TIMELINE, tc, agents, max_rounds=24)
    assert events, "flashpoint event must survive"
    assert all(0 <= e["round"] < 24 for e in events), \
        f"event scheduled outside executed window: {events}"


def test_scheduled_events_default_falls_back_to_oasis_default_cap(monkeypatch):
    monkeypatch.setattr(Config, "SIM_SCHEDULE_CLAMP_ROUNDS", True, raising=False)
    monkeypatch.setattr(Config, "OASIS_DEFAULT_MAX_ROUNDS", 36, raising=False)
    g = _gen()
    tc = TimeSimulationConfig(total_simulation_hours=72, minutes_per_round=60)
    agents = _agents([(0, "PRC", 3.0)])
    events = g._build_scheduled_events(_ACTORS_TIMELINE, tc, agents)  # max_rounds unwired
    assert events and all(e["round"] < 36 for e in events)


def test_scheduled_events_unchanged_when_clamp_disabled(monkeypatch):
    monkeypatch.setattr(Config, "SIM_SCHEDULE_CLAMP_ROUNDS", False, raising=False)
    g = _gen()
    tc = TimeSimulationConfig(total_simulation_hours=72, minutes_per_round=60)
    agents = _agents([(0, "PRC", 3.0)])
    events = g._build_scheduled_events(_ACTORS_TIMELINE, tc, agents)
    # 关闭钳制 → 映射域是配置全轮数 [0, 72)（PREP-5 反应缓冲可能把末事件前移到 ~58，
    # 但绝不会 <36 —— 即证明未被 24/36 的执行预算钳制）
    top = max(e["round"] for e in events)
    assert events and 36 <= top < 72


# ---------------------------------------------------------------------------
# PREP-2: rule fallback recognizes the live ontology + dossier stance survives
# ---------------------------------------------------------------------------

def test_rule_config_covers_dynamic_ontology_types():
    g = _gen()
    gov = g._generate_agent_config_by_rule(_StubEntity("US Congress", "Government"))
    org = g._generate_agent_config_by_rule(_StubEntity("Nvidia", "Organization"))
    asset = g._generate_agent_config_by_rule(_StubEntity("H200", "StrategicAsset"))
    generic = g._generate_agent_config_by_rule(_StubEntity("Someone", "Unknown"))

    # Government → 官方机构口径（高影响力、低活跃），不再是「普通人」
    assert gov["influence_weight"] == 3.0 and gov["activity_level"] == 0.2
    # Organization → 机构口径
    assert org["influence_weight"] == 2.0 and org["activity_level"] != generic["activity_level"]
    # StrategicAsset → 低活跃观察者
    assert asset["stance"] == "observer" and asset["activity_level"] < 0.3
    # 三者不再共享 else 分支的统一指纹
    fingerprints = {(c["activity_level"], c["posts_per_hour"], tuple(c["active_hours"]))
                    for c in (gov, org, asset)}
    assert len(fingerprints) == 3


def test_batch_rule_fallback_applies_dossier_stance(monkeypatch):
    monkeypatch.setattr(Config, "SIM_RULE_FALLBACK_STANCE", True, raising=False)
    g = _gen()
    # LLM 整批失败 → 全部走规则兜底
    def _boom(prompt, system_prompt):
        raise RuntimeError("batch LLM down")
    g._call_llm_with_retry = _boom
    g._agent_batch_stats = {"llm_batches": 0, "rule_batches": 0, "rule_agents": 0}

    actors = {
        "actors": [
            {"name": "PRC", "stance": "反对出口管制，批评美方施压", "influence": "high"},
            {"name": "US Federal Executive", "stance": "支持强硬管制", "influence": "high"},
        ],
        "relationships": [],
    }
    entities = [
        _StubEntity("PRC", "Government", uuid="u1"),
        _StubEntity("US Federal Executive", "Government", uuid="u2"),
    ]
    cfgs = g._generate_agent_configs_batch(
        context="", entities=entities, start_idx=0,
        simulation_requirement="", actors=actors,
    )
    by_name = {c.entity_name: c for c in cfgs}
    assert by_name["PRC"].stance == "opposing"
    assert by_name["PRC"].sentiment_bias == -0.3
    assert by_name["US Federal Executive"].stance == "supportive"
    assert by_name["US Federal Executive"].sentiment_bias == 0.3
    # PREP-4(1): 批级统计记录了规则回退
    assert g._agent_batch_stats["rule_batches"] == 1
    assert g._agent_batch_stats["rule_agents"] == 2


def test_batch_rule_fallback_stance_flag_off(monkeypatch):
    monkeypatch.setattr(Config, "SIM_RULE_FALLBACK_STANCE", False, raising=False)
    g = _gen()
    g._call_llm_with_retry = lambda p, s: (_ for _ in ()).throw(RuntimeError("down"))
    actors = {"actors": [{"name": "PRC", "stance": "反对出口管制", "influence": "high"}]}
    cfgs = g._generate_agent_configs_batch(
        context="", entities=[_StubEntity("PRC", "Government")], start_idx=0,
        simulation_requirement="", actors=actors,
    )
    assert cfgs[0].stance == "neutral"  # 旗标关 → 今日行为


# ---------------------------------------------------------------------------
# PREP-9: time-config coercion & clamping
# ---------------------------------------------------------------------------

def test_parse_time_config_coerces_and_clamps_garbage():
    g = _gen()
    tc = g._parse_time_config({
        "total_simulation_hours": "60分钟",   # 非数值 → 默认 72
        "minutes_per_round": 0,               # 0 → 夹到 30（否则运行脚本除零）
        "agents_per_hour_min": "abc",         # 非数值 → 默认
        "agents_per_hour_max": 999,           # 越界 → 修正
    }, num_entities=10)
    assert tc.total_simulation_hours == 72
    assert tc.minutes_per_round == 30
    assert 1 <= tc.agents_per_hour_min < tc.agents_per_hour_max <= 10


def test_parse_time_config_clamps_pathological_hours():
    g = _gen()
    tc = g._parse_time_config({"total_simulation_hours": 500, "minutes_per_round": 60},
                              num_entities=10)
    assert tc.total_simulation_hours == 168  # 提示词承诺的上限


def test_parse_time_config_passthrough_wellformed():
    g = _gen()
    tc = g._parse_time_config({"total_simulation_hours": 96, "minutes_per_round": 60},
                              num_entities=20)
    assert tc.total_simulation_hours == 96 and tc.minutes_per_round == 60


# ---------------------------------------------------------------------------
# PREP-10: duplicate-name tie-break is first-wins in _build_initial_follows
# ---------------------------------------------------------------------------

def test_initial_follows_duplicate_names_first_wins():
    g = _gen()
    agents = _agents([(0, "SoftBank", 2.0), (1, "X Corp", 1.0), (5, "SoftBank", 1.0)])
    actors = {
        "actors": [{"name": "SoftBank", "influence": "high"},
                   {"name": "X Corp", "influence": "low"}],
        "relationships": [
            {"source": "SoftBank", "target": "X Corp", "type": "ALLY_OF",
             "sign": "ally", "strength": "high"},
        ],
    }
    follows = g._build_initial_follows(agents, [], actors)
    flat = {aid for pair in follows for aid in pair}
    assert follows, "relationship edge must produce a follow"
    assert 5 not in flat, "duplicate SoftBank must resolve to the FIRST agent (id 0)"
    assert 0 in flat


# ---------------------------------------------------------------------------
# PREP-11: synthesized seed posts — alternating/localized templates
# ---------------------------------------------------------------------------

_SEED_ACTORS = {
    "actors": [
        {"name": "Alpha Gov", "type": "Government", "stance": "Tariffs are leverage.",
         "influence": "high", "simulation_tier": "1"},
        {"name": "Beta Corp", "type": "Organization", "stance": "Open trade wins.",
         "influence": "medium", "simulation_tier": "2"},
    ],
}


def test_seed_posts_variants_localized_chinese(monkeypatch):
    monkeypatch.setattr(Config, "SIM_SEED_POST_VARIANTS", True, raising=False)
    g = _gen()
    g._profile_override = "china_social"
    posts = g._synthesize_initial_posts(_SEED_ACTORS, ["关税持久度"])
    assert len(posts) == 2
    assert posts[0]["content"].startswith("关于关税持久度")
    assert "谁受益、谁受损" in posts[1]["content"]  # 交替的争议提问模板
    assert all(len(p["content"]) <= 600 for p in posts)


def test_seed_posts_variants_english_profile(monkeypatch):
    monkeypatch.setattr(Config, "SIM_SEED_POST_VARIANTS", True, raising=False)
    g = _gen()
    g._profile_override = "global_market"
    posts = g._synthesize_initial_posts(_SEED_ACTORS, ["tariff durability"])
    assert posts[0]["content"].startswith("On tariff durability")
    assert "Who gains and who loses" in posts[1]["content"]


def test_seed_posts_flag_off_restores_legacy_template(monkeypatch):
    monkeypatch.setattr(Config, "SIM_SEED_POST_VARIANTS", False, raising=False)
    g = _gen()
    g._profile_override = "china_social"
    posts = g._synthesize_initial_posts(_SEED_ACTORS, ["tariff durability"])
    assert posts[0]["content"] == "On tariff durability — our position: Tariffs are leverage."
    assert posts[1]["content"] == "On tariff durability — our position: Open trade wins."


# ---------------------------------------------------------------------------
# PREP-4(2): activity-profile override selection in generate_config
# ---------------------------------------------------------------------------

def test_activity_profile_override_selection(monkeypatch):
    monkeypatch.delenv("SIM_ACTIVITY_PROFILE", raising=False)
    g = _gen()
    # 未覆盖 → 跟随全局配置（默认 china_social）
    g._profile_override = None
    assert g._activity_profile() is ACTIVITY_PROFILES[
        str(getattr(Config, "SIM_ACTIVITY_PROFILE", "china_social") or "china_social").strip().lower()
        if str(getattr(Config, "SIM_ACTIVITY_PROFILE", "china_social") or "").strip().lower() in ACTIVITY_PROFILES
        else "china_social"
    ]
    # 覆盖生效
    g._profile_override = "global_market"
    assert g._activity_profile() is ACTIVITY_PROFILES["global_market"]
    # 非法覆盖回退 china_social
    g._profile_override = "no_such_profile"
    assert g._activity_profile() is ACTIVITY_PROFILES["china_social"]


# ---------------------------------------------------------------------------
# PREP-8 / XRUN-10: profile generator — degradation stats, bio cap, defaults
# ---------------------------------------------------------------------------

def _pgen(persona_language=None):
    gen = OasisProfileGenerator.__new__(OasisProfileGenerator)
    gen.persona_language = persona_language
    return gen


def test_generation_path_marks_llm_fallback():
    gen = _pgen()
    gen._build_entity_context = lambda e: ""
    gen._generate_profile_with_llm = lambda **kw: {
        "bio": "b", "persona": "p", "_generation_path": "rule_fallback",
    }
    prof = gen.generate_profile_from_entity(
        _StubEntity("Amazon", "Organization"), user_id=0, use_llm=True)
    assert prof.generation_path == "rule_fallback"
    assert prof.bio == "b"


def test_generation_path_rule_when_llm_disabled():
    gen = _pgen()
    gen._build_entity_context = lambda e: ""
    prof = gen.generate_profile_from_entity(
        _StubEntity("Amazon", "Organization"), user_id=0, use_llm=False)
    assert prof.generation_path == "rule"
    # 默认值：未标记的 profile 是 llm 路径
    assert OasisAgentProfile(user_id=1, user_name="x", name="X",
                             bio="b", persona="p").generation_path == "llm"


def test_reddit_json_bio_cap_and_country_defaults(tmp_path):
    long_bio = "A" * 250
    profiles = [
        OasisAgentProfile(user_id=0, user_name="c", name="Congress",
                          bio=long_bio, persona="p"),  # country 未给
        OasisAgentProfile(user_id=1, user_name="t", name="TSMC",
                          bio="short", persona="p", country="台湾"),
    ]
    # 默认（中文口径）：bio 上限 300，country 缺省仍回填「中国」（逐字节兼容）
    gen = _pgen()
    out = tmp_path / "reddit.json"
    gen._save_reddit_json(profiles, str(out))
    data = json.loads(out.read_text())
    assert data[0]["bio"] == long_bio            # PREP-8: 不再 150 截断
    assert data[0]["country"] == "中国"
    assert data[1]["country"] == "台湾"
    # 英文口径：country 缺省为空串，不再打成中国籍
    gen_en = _pgen(persona_language="en")
    out_en = tmp_path / "reddit_en.json"
    gen_en._save_reddit_json(profiles, str(out_en))
    data_en = json.loads(out_en.read_text())
    assert data_en[0]["country"] == ""
    assert data_en[1]["country"] == "台湾"
    # OASIS 必需键仍然全部在场
    for row in data_en:
        for key in ("age", "gender", "mbti", "country", "user_id", "bio", "persona"):
            assert key in row


def test_persona_prompts_field_extraction_and_language(monkeypatch):
    monkeypatch.setattr(Config, "PERSONA_FIELD_EXTRACTION", True, raising=False)
    gen = _pgen()
    ind = gen._build_individual_persona_prompt("Howard Lutnick", "Person", "s", {}, "")
    grp = gen._build_group_persona_prompt("TSMC", "Organization", "s", {}, "")
    # XRUN-10: 不再用「中国」作示例锚定；要求从档案抽取、无依据则省略
    assert '如"中国"' not in ind and '如"中国"' not in grp
    assert "无法推断则省略" in ind
    assert "总部" in grp
    # 默认（非英文口径）语言规则与系统提示词保持旧口径
    assert "使用中文" in ind
    assert gen._get_system_prompt(True).endswith("使用中文。")
    # 英文口径切换
    gen_en = _pgen(persona_language="en")
    assert "English" in gen_en._get_system_prompt(True)
    assert "使用英文撰写" in gen_en._build_individual_persona_prompt("A", "Person", "s", {}, "")
    # 旗标关 → 旧措辞回归
    monkeypatch.setattr(Config, "PERSONA_FIELD_EXTRACTION", False, raising=False)
    legacy = gen._build_individual_persona_prompt("A", "Person", "s", {}, "")
    assert '如"中国"' in legacy


def test_profile_generator_env_profile_selects_english(monkeypatch):
    monkeypatch.setenv("SIM_ACTIVITY_PROFILE", "global_market")
    monkeypatch.setattr(Config, "LLM_PROVIDER", "claude-cli", raising=False)
    monkeypatch.setattr(Config, "ZEP_API_KEY", "", raising=False)
    gen = OasisProfileGenerator()
    assert gen._english_persona() is True
    monkeypatch.delenv("SIM_ACTIVITY_PROFILE")
    gen2 = OasisProfileGenerator()
    assert gen2._english_persona() is False


# ---------------------------------------------------------------------------
# XRUN-13: salience crosses the JSON boundary and the coverage metric sees it
# ---------------------------------------------------------------------------

def test_dossier_coverage_salience_nonzero_on_schema_shaped_fixture():
    actors = {
        "actors": [
            {"name": "US Federal Executive",
             "salience": {"tier": "high", "basis": "controls export licensing"}},
            {"name": "Pundit X"},
        ],
        "relationships": [],
    }
    cov = dossier_coverage(actors)
    assert cov["salience_basis_present"] == 0.5
    assert salience_score(actors["actors"][0]) == 0.85  # tier high → 0.85


def test_extraction_schema_emits_salience():
    """deerflow_research.py 的结构化抽取 schema 必须包含 salience 契约（XRUN-13）。"""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "deerflow_bridge", "deerflow_research.py")
    text = open(os.path.abspath(path), encoding="utf-8").read()
    assert '"salience": {' in text
    assert '"basis": string' in text
