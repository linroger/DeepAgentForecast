"""NEXTSTEPS — 模拟上下文注入（SIM_WORLD_BRIEF）+ 逐角色人格设计（SIM_PERSONA_DESIGN）。

Offline: no LLM/network. Covers:
- world_brief 的确定性拼装（全量/部分/空输入、400 字问题截断、1400 字总上限、开关）；
- SimulationParameters.to_dict() 对 world_brief 的「空省略」契约；
- run_parallel_simulation._inject_world_brief 的 system-prompt 追加机制（幂等、坏 Agent 隔离）
  与 _world_brief_enabled 开关；
- 逐角色情境工程：LLM 提示词包含人格设计指令块 + 该角色的调研实证上下文、persona_design
  的规整（白名单键/list 拼接/未知键剔除）、LLM 漏产时的档案规则兜底、开关/无档案时的
  逐字节旧行为、规则回退路径的设计摘要行、other_info['persona_design'] 持久化。
"""

import json
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
for _p in (_BACKEND, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_parallel_simulation as rps  # noqa: E402
from app.config import Config  # noqa: E402
from app.services.oasis_profile_generator import (  # noqa: E402
    OasisAgentProfile,
    OasisProfileGenerator,
)
from app.services.simulation_config_generator import (  # noqa: E402
    SimulationConfigGenerator,
    SimulationParameters,
)
from app.services.zep_entity_reader import EntityNode  # noqa: E402


# ---------------------------------------------------------------------------
# 共享夹具：调研档案（真实行动者的实证材料）
# ---------------------------------------------------------------------------

ACTOR = {
    "name": "英伟达",
    "type": "Organization",
    "role": "AI芯片供应商",
    "stance": "支持放宽对华出口许可",
    "influence": "high",
    "goals": ["保住中国市场份额", "维持AI算力生态主导权"],
    "constraints": ["美国出口管制条例"],
    "vulnerabilities": ["对台积电先进制程产能的依赖"],
    "stated_vs_revealed": "公开呼吁自由贸易，实际游说争取定向豁免",
    "memory": "2025年H20禁令导致季度减记约55亿美元",
    "worldview": {
        "identity": "全球AI算力底座供应商",
        "beliefs": ["算力即国力"],
        "frame": "商业利益优先于阵营对抗",
    },
    "incentives": [
        {"driver": "数据中心营收", "gains_if": "对华出口恢复",
         "loses_if": "管制进一步加码", "intensity": "high"},
    ],
    "risk_tolerance": "medium",
}

ACTORS = {
    "as_of_date": "2026-06-08",
    "situation_brief": {
        "current_situation": "美国对华AI芯片出口管制持续收紧，业界游说放宽。",
        "dynamics": "管制派与产业派角力升级，盟友协调出现裂缝。",
    },
    "actors": [
        ACTOR,
        {"name": "美国商务部", "type": "Government", "role": "出口管制主管部门",
         "stance": "维持严格管制", "influence": "high"},
    ],
    "relationships": [
        {"source": "美国商务部", "target": "英伟达", "type": "REGULATES",
         "strength": "high", "basis": "出口许可审批权"},
    ],
    "hot_topics": ["AI芯片出口管制", "H20", "算力竞赛"],
}

QUESTION = "2026年底前美国是否会放宽对华AI芯片出口管制？请评估各情景概率。"


def _config_gen() -> SimulationConfigGenerator:
    """绕过 __init__（不建 LLM 客户端），只测确定性拼装方法。"""
    return SimulationConfigGenerator.__new__(SimulationConfigGenerator)


def _profile_gen() -> OasisProfileGenerator:
    """绕过 __init__（不建 LLM/Zep 客户端）；被测方法均为自足逻辑。"""
    return OasisProfileGenerator.__new__(OasisProfileGenerator)


# ---------------------------------------------------------------------------
# Part 1a — world_brief 拼装（simulation_config_generator）
# ---------------------------------------------------------------------------

def test_world_brief_full_inputs_contains_all_sections():
    brief = _config_gen()._build_world_brief(QUESTION, ACTORS, ACTORS["hot_topics"])
    assert "## 核心预测问题" in brief
    assert QUESTION in brief
    assert "局势简报" in brief          # situation_brief_block 的标题
    assert "管制持续收紧" in brief       # current_situation 内容
    assert "## 热点话题" in brief
    assert "AI芯片出口管制" in brief
    assert len(brief) <= SimulationConfigGenerator.WORLD_BRIEF_MAX_CHARS


def test_world_brief_question_truncated_to_400_chars():
    long_question = "问" * 1000
    brief = _config_gen()._build_world_brief(long_question, None, None)
    assert "问" * 400 in brief
    assert "问" * 401 not in brief


def test_world_brief_partial_inputs_degrade_to_shorter_brief():
    # 只有问题：无局势简报/热点段
    brief = _config_gen()._build_world_brief(QUESTION, None, [])
    assert QUESTION in brief
    assert "局势简报" not in brief
    assert "热点话题" not in brief
    # 只有热点：无问题段
    brief2 = _config_gen()._build_world_brief("", None, ["话题A", " ", "话题B"])
    assert "核心预测问题" not in brief2
    assert "话题A" in brief2 and "话题B" in brief2


def test_world_brief_empty_inputs_returns_empty():
    assert _config_gen()._build_world_brief("", None, None) == ""
    assert _config_gen()._build_world_brief("   ", {}, []) == ""


def test_world_brief_total_length_cap():
    fat_actors = {
        "situation_brief": {
            "current_situation": "态" * 900,
            "context": "景" * 900,
            "dynamics": "动" * 900,
        }
    }
    brief = _config_gen()._build_world_brief("问" * 500, fat_actors, ["题" * 100] * 8)
    assert len(brief) <= SimulationConfigGenerator.WORLD_BRIEF_MAX_CHARS


def test_world_brief_flag_off_returns_empty(monkeypatch):
    monkeypatch.setattr(Config, "SIM_WORLD_BRIEF", "false", raising=False)
    assert _config_gen()._build_world_brief(QUESTION, ACTORS, ["话题"]) == ""


def test_world_brief_flag_default_true_when_absent(monkeypatch):
    monkeypatch.delattr(Config, "SIM_WORLD_BRIEF", raising=False)
    assert QUESTION in _config_gen()._build_world_brief(QUESTION, None, None)


def _params(**kw) -> SimulationParameters:
    return SimulationParameters(
        simulation_id="sim-1", project_id="p-1", graph_id="g-1",
        simulation_requirement=QUESTION, **kw,
    )


def test_params_to_dict_omits_empty_world_brief():
    assert "world_brief" not in _params().to_dict()


def test_params_to_dict_includes_nonempty_world_brief():
    d = _params(world_brief="## 核心预测问题\n测试").to_dict()
    assert d["world_brief"] == "## 核心预测问题\n测试"


# ---------------------------------------------------------------------------
# Part 1b — _inject_world_brief / _world_brief_enabled（run_parallel_simulation）
# ---------------------------------------------------------------------------

class FakeMsg:
    def __init__(self, content):
        self.content = content

    def create_new_instance(self, content):
        return FakeMsg(content)


class FakeAgent:
    def __init__(self, content="你是模拟中的社交媒体用户。"):
        self._original_system_message = FakeMsg(content)
        self._system_message = None
        self.init_calls = 0

    def _generate_system_message_for_output_language(self):
        return self._original_system_message

    def init_messages(self):
        self.init_calls += 1


class BrokenAgent:
    """无 _original_system_message → _inject_behavior_hint 返回 False（降级跳过）。"""
    _original_system_message = None


class FakeGraph:
    def __init__(self, agents):
        self._agents = agents

    def get_agents(self):
        return list(enumerate(self._agents))


def test_inject_world_brief_appends_header_block_to_all_agents():
    agents = [FakeAgent(), FakeAgent("另一个人设。")]
    logs = []
    rps._inject_world_brief(FakeGraph(agents), "全球都在讨论出口管制。", logs.append)
    for a in agents:
        content = a._original_system_message.content
        assert "# WORLD BRIEF（共同世界背景）" in content
        assert "全球都在讨论出口管制。" in content
        assert content.startswith("你是") or content.startswith("另一个")  # 原人设保留在前
        assert a.init_calls == 1
    assert any("已注入 2/2" in m for m in logs)


def test_inject_world_brief_idempotent():
    agent = FakeAgent()
    graph = FakeGraph([agent])
    rps._inject_world_brief(graph, "世界背景X", lambda m: None)
    rps._inject_world_brief(graph, "世界背景X", lambda m: None)
    assert agent._original_system_message.content.count("# WORLD BRIEF（共同世界背景）") == 1


def test_inject_world_brief_empty_brief_or_graph_is_noop():
    agent = FakeAgent()
    rps._inject_world_brief(FakeGraph([agent]), "", lambda m: None)
    rps._inject_world_brief(FakeGraph([agent]), None, lambda m: None)
    rps._inject_world_brief(None, "背景", lambda m: None)
    assert "WORLD BRIEF" not in agent._original_system_message.content
    assert agent.init_calls == 0


def test_inject_world_brief_isolates_broken_agents():
    good = FakeAgent()
    logs = []
    rps._inject_world_brief(FakeGraph([BrokenAgent(), good]), "背景", logs.append)
    assert "# WORLD BRIEF（共同世界背景）" in good._original_system_message.content
    assert any("已注入 1/2" in m for m in logs)


def test_world_brief_enabled_env_flag(monkeypatch):
    monkeypatch.delenv("SIM_WORLD_BRIEF", raising=False)
    monkeypatch.delattr(Config, "SIM_WORLD_BRIEF", raising=False)
    assert rps._world_brief_enabled() is True          # 默认开
    monkeypatch.setenv("SIM_WORLD_BRIEF", "false")
    assert rps._world_brief_enabled() is False
    monkeypatch.setenv("SIM_WORLD_BRIEF", "true")
    assert rps._world_brief_enabled() is True


# ---------------------------------------------------------------------------
# Part 2 — 逐角色人格设计（oasis_profile_generator）
# ---------------------------------------------------------------------------

class StubLLM:
    """记录提示词并按脚本返回 JSON 字符串（模拟 LLMClient.chat）。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, messages=None, **kwargs):
        self.calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)

    def last_user_prompt(self):
        return self.calls[-1][-1]["content"]


BASE_PAYLOAD = {
    "bio": "AI芯片供应商官方账号。",
    "persona": "英伟达是全球AI算力底座供应商，主张放宽对华出口许可。",
    "age": 30, "gender": "other", "mbti": "ENTJ", "country": "美国",
    "profession": "半导体", "interested_topics": ["AI芯片"],
}


def _run_llm_profile(payload, actor=ACTOR, actors=ACTORS, monkeypatch=None, flag=None):
    gen = _profile_gen()
    gen.llm = StubLLM(payload)
    if monkeypatch is not None and flag is not None:
        monkeypatch.setattr(Config, "SIM_PERSONA_DESIGN", flag, raising=False)
    result = gen._generate_profile_with_llm(
        entity_name="英伟达", entity_type="Company", entity_summary="芯片公司",
        entity_attributes={}, context="", actor=actor, actors=actors,
    )
    return gen, result


def test_llm_prompt_contains_design_block_and_researched_context():
    payload = dict(BASE_PAYLOAD)
    payload["persona_design"] = {
        "identity": "全球AI算力底座供应商",
        "views_beliefs": "认为算力即国力，管制终将松动",
        "incentives": "对华出口恢复则营收大增",
        "objectives": "游说定向豁免",
        "relations": "受美国商务部监管",
        "constraints_red_lines": "不越出口管制红线",
        "decision_style": "商业利益优先、渐进游说",
        "rhetoric": "公开呼吁自由贸易",
    }
    gen, result = _run_llm_profile(payload)
    prompt = gen.llm.last_user_prompt()
    # 设计指令块 + 字段契约
    assert "## 人格设计" in prompt
    assert "persona_design" in prompt
    for key in OasisProfileGenerator.PERSONA_DESIGN_KEYS:
        assert key in prompt
    # 该角色的调研实证上下文在同一提示词内（设计只允许从中提炼）
    assert "深度研究实证档案" in prompt
    assert "支持放宽对华出口许可" in prompt      # stance
    assert "【共同背景·局势简报（调研实证）】" in prompt
    # 结构化设计原样通过规整
    assert result["persona_design"]["identity"] == "全球AI算力底座供应商"
    assert result["persona_design"]["rhetoric"] == "公开呼吁自由贸易"


def test_llm_design_normalized_lists_joined_unknown_keys_dropped():
    payload = dict(BASE_PAYLOAD)
    payload["persona_design"] = {
        "identity": "  供应商  ",
        "views_beliefs": ["算力即国力", "管制将松动"],
        "unknown_key": "应被剔除",
        "objectives": "",
    }
    _, result = _run_llm_profile(payload)
    design = result["persona_design"]
    assert design["identity"] == "供应商"
    assert design["views_beliefs"] == "算力即国力；管制将松动"
    assert "unknown_key" not in design
    assert "objectives" not in design  # 空值剔除


def test_llm_design_missing_falls_back_to_dossier_extraction():
    _, result = _run_llm_profile(dict(BASE_PAYLOAD))  # LLM 未产出 persona_design
    design = result["persona_design"]
    assert isinstance(design, dict)
    assert "AI芯片供应商" in design["identity"]                 # role
    assert "支持放宽对华出口许可" in design["views_beliefs"]     # stance
    assert "对华出口恢复" in design["incentives"]               # gains_if
    assert "保住中国市场份额" in design["objectives"]            # goals
    assert "美国出口管制条例" in design["constraints_red_lines"]  # constraints
    assert design["rhetoric"] == ACTOR["stated_vs_revealed"]


def test_llm_flag_off_prompt_and_result_unchanged(monkeypatch):
    payload = dict(BASE_PAYLOAD)
    payload["persona_design"] = {"identity": "幻觉设计"}  # 未请求时的幻觉键应被剔除
    gen, result = _run_llm_profile(payload, monkeypatch=monkeypatch, flag="false")
    assert "## 人格设计" not in gen.llm.last_user_prompt()
    assert "persona_design" not in result


def test_llm_no_dossier_actor_no_design_block():
    gen, result = _run_llm_profile(dict(BASE_PAYLOAD), actor=None, actors=None)
    assert "## 人格设计" not in gen.llm.last_user_prompt()
    assert "persona_design" not in result


def test_design_from_actor_relations_grounded_in_researched_edges():
    design = _profile_gen()._design_from_actor(ACTOR, "英伟达", actors=ACTORS)
    assert "美国商务部" in design.get("relations", "")


def test_design_from_actor_empty_inputs():
    gen = _profile_gen()
    assert gen._design_from_actor(None, "X") is None
    assert gen._design_from_actor({}, "X") is None
    assert gen._design_from_actor({"name": " "}, "X") is None or isinstance(
        gen._design_from_actor({"name": " "}, "X"), dict)


def test_rule_based_fallback_keeps_design_and_summary_line():
    result = _profile_gen()._generate_profile_rule_based(
        entity_name="英伟达", entity_type="Company", entity_summary="芯片公司",
        entity_attributes={}, actor=ACTOR, actors=ACTORS,
    )
    design = result["persona_design"]
    assert "AI芯片供应商" in design["identity"]
    # 降级路径下设计摘要进入 persona 文本（最终落到 system prompt 的 user_char/persona）
    assert "【人格设计·实证提炼】" in result["persona"]
    assert "AI芯片供应商" in result["persona"]


def test_rule_based_flag_off_no_design(monkeypatch):
    monkeypatch.setattr(Config, "SIM_PERSONA_DESIGN", "false", raising=False)
    result = _profile_gen()._generate_profile_rule_based(
        entity_name="英伟达", entity_type="Company", entity_summary="芯片公司",
        entity_attributes={}, actor=ACTOR, actors=ACTORS,
    )
    assert "persona_design" not in result
    assert "【人格设计·实证提炼】" not in result["persona"]


def test_rule_based_without_actor_unchanged():
    result = _profile_gen()._generate_profile_rule_based(
        entity_name="路人甲", entity_type="Company", entity_summary="",
        entity_attributes={}, actor=None, actors=None,
    )
    assert "persona_design" not in result


def test_generate_profile_from_entity_attaches_design_field():
    gen = _profile_gen()
    gen._build_entity_context = lambda entity: ""  # 不触发 Zep
    entity = EntityNode(uuid="u1", name="英伟达", labels=["Company"],
                        summary="芯片公司", attributes={})
    profile = gen.generate_profile_from_entity(
        entity=entity, user_id=0, use_llm=False, actor=ACTOR, actors=ACTORS,
    )
    assert isinstance(profile.persona_design, dict)
    assert "AI芯片供应商" in profile.persona_design["identity"]
    assert "【人格设计·实证提炼】" in profile.persona


def test_profile_to_dict_persists_design_under_other_info():
    with_design = OasisAgentProfile(
        user_id=0, user_name="nvidia", name="英伟达", bio="b", persona="p",
        persona_design={"identity": "供应商"},
    )
    without = OasisAgentProfile(user_id=1, user_name="doc", name="商务部", bio="b", persona="p")
    assert with_design.to_dict()["other_info"]["persona_design"] == {"identity": "供应商"}
    assert "other_info" not in without.to_dict()


def test_reddit_json_persists_design_and_keeps_oasis_schema(tmp_path):
    gen = _profile_gen()
    profiles = [
        OasisAgentProfile(user_id=0, user_name="nvidia", name="英伟达", bio="b", persona="p",
                          persona_design={"identity": "供应商", "rhetoric": "自由贸易"}),
        OasisAgentProfile(user_id=1, user_name="doc", name="商务部", bio="b", persona="p"),
    ]
    path = str(tmp_path / "reddit_profiles.json")
    gen._save_reddit_json(profiles, path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data[0]["other_info"]["persona_design"]["identity"] == "供应商"
    assert "other_info" not in data[1]
    # OASIS 加载器无条件 key 访问的字段必须仍然齐全
    for item in data:
        for key in ("user_id", "username", "persona", "age", "gender", "mbti", "country"):
            assert key in item
