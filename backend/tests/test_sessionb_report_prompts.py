"""SESSION-B（LOOP-015）离线测试：report_agent 提示词去重 + 条件工具下限 + 大纲收敛 + 分层护栏。

全程无 LLM/网络（stubbed deps），覆盖四个已验证缺陷的修复：
  1. SECTION 提示词去重——每条行为规则在合并模板中只出现一次，且占位符集合与 HEAD 逐字节等价；
  2. 条件工具下限——padding 章节有效下限 0、证据预注入章节下限 1、普通章节维持配置值；
  3. 元章节合并——结构强制指令改为一节「预测总表与校准」，旧的两节纯方法学标题从指令中移除；
     大纲 >=4 节即接受不补齐，<4 节补齐到 4 且补齐章节带 padded 标记；
  4. 方差护栏——动作计数近乎持平（max/min < 1.5）时 salience_tiers_from_outcomes 判无信号返回 ""。
"""

import ast
import os
import string
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.report_agent import (  # noqa: E402
    FORECAST_STRUCTURE_MANDATE,
    REACT_UNUSED_TOOLS_HINT,
    ReportAgent,
    ReportOutline,
    ReportSection,
    SECTION_SYSTEM_PROMPT_TEMPLATE,
    SECTION_USER_PROMPT_TEMPLATE,
    _actor_counts_flat,
    _parse_outcome_actors,
    salience_tiers_from_outcomes,
)

_COMBINED = SECTION_SYSTEM_PROMPT_TEMPLATE + SECTION_USER_PROMPT_TEMPLATE


# ───────────────────── (a) 每条行为规则在合并模板中恰好一次 ─────────────────────
@pytest.mark.parametrize("rule_marker", [
    "####",                      # #### 及更深层级的禁令（旧模板出现 4 次）
    "禁止使用 # 或 ##",           # 一级/二级标题禁令
    "禁止在内容开头重复本章标题",     # 标题复述禁令
    "### 三级小标题",              # 2-4 个 ### 子小节的正向组织规则
    "【正确示例】",                # 单个正向示例（旧模板另有【错误示例】+ 引语正误例）
    "严禁编造",                   # 反捏造禁令
    "本报告估计",                  # 承重数字的估计标注规则
    "禁止使用你自己的知识",          # 证据只来自检索的规则
    "严禁把内部观点伪装成",          # 内部分析不可伪装成信源
    "【语言一致性】",              # 引用内容翻译为报告语言
    "引语独立成段",                # 直接引语格式规则
    '"Final Answer:"',           # 最终输出协议
    "提纲式摘要",                  # 篇幅下限规则（禁短摘要）
    "steelman",                  # 真实分歧要求
])
def test_each_behavioural_rule_stated_exactly_once(rule_marker):
    assert _COMBINED.count(rule_marker) == 1, (
        f"规则记号 {rule_marker!r} 在合并章节模板中出现 {_COMBINED.count(rule_marker)} 次，应恰好 1 次"
    )


def test_hard_ban_vocabulary_block_stated_once():
    # 方法学词汇硬性禁令整块只出现一次（用低频词组合探测）。
    assert _COMBINED.count("simulation-derived") == 1
    assert _COMBINED.count("【硬性禁令】") == 1


# ───────────────── (b) 占位符集合与 git HEAD 版本逐一等价 ─────────────────
def _placeholders(template: str) -> set:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def _head_templates() -> dict:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        head_src = subprocess.run(
            ["git", "-C", repo_root, "show", "HEAD:backend/app/services/report_agent.py"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:  # git 不可用时跳过而非误报
        pytest.skip(f"git show HEAD 不可用: {e}")
    wanted = {"SECTION_SYSTEM_PROMPT_TEMPLATE", "SECTION_USER_PROMPT_TEMPLATE"}
    found = {}
    for node in ast.walk(ast.parse(head_src)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in wanted
                and isinstance(node.value, ast.Constant)):
            found[node.targets[0].id] = node.value.value
    assert set(found) == wanted, f"HEAD 中未找到全部模板赋值: {set(found)}"
    return found


def test_placeholder_sets_unchanged_vs_head():
    head = _head_templates()
    assert _placeholders(SECTION_SYSTEM_PROMPT_TEMPLATE) == _placeholders(
        head["SECTION_SYSTEM_PROMPT_TEMPLATE"])
    assert _placeholders(SECTION_USER_PROMPT_TEMPLATE) == _placeholders(
        head["SECTION_USER_PROMPT_TEMPLATE"])


def test_templates_format_cleanly():
    shared = dict(min_tool_calls=4, max_tool_calls=12,
                  section_floor_chars=2000, section_target_lo=3000, section_target_hi=6000)
    rendered_sys = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
        report_title="标题", report_summary="摘要", simulation_requirement="需求",
        section_title="某章", tools_description="工具", tool_usage_hints="提示", **shared)
    rendered_usr = SECTION_USER_PROMPT_TEMPLATE.format(
        previous_content="（这是第一个章节）", section_title="某章", **shared)
    # 工具调用示例的转义花括号在 format 后须还原成单层 JSON 花括号。
    assert '{"name": "工具名称", "parameters": {"参数名": "参数值"}}' in rendered_sys
    assert "某章" in rendered_usr and "2000" in rendered_usr


# ───────────────────── (c) 方差护栏：持平计数 → 无信号 ─────────────────────
def _outcomes_text(counts) -> str:
    lines = ["### 最活跃 Agent（按总动作数）"]
    lines += [f"- Actor{i}(id={i}): 共 {c} 次动作 [CREATE_POST×{c}]"
              for i, c in enumerate(counts, 1)]
    return "\n".join(lines)


def test_flat_action_counts_yield_no_signal():
    # 实测形态：12 个行为者全落在 52-63（配置化节奏跑平），max/min ≈ 1.21 < 1.5。
    flat = [52, 55, 63, 58, 60, 53, 57, 61, 54, 59, 56, 62]
    text = _outcomes_text(flat)
    assert _actor_counts_flat(_parse_outcome_actors(text)) is True
    assert salience_tiers_from_outcomes(text) == ""


def test_varied_action_counts_yield_nondegenerate_tiers():
    text = _outcomes_text([48, 40, 5])
    assert _actor_counts_flat(_parse_outcome_actors(text)) is False
    tiers = salience_tiers_from_outcomes(text)
    assert "第一梯队" in tiers and "第三梯队" in tiers   # 层级非退化（不止一档）
    assert "48" not in tiers and "次动作" not in tiers   # 机制数字仍不进注入文本
    # 边界：min=0 时比值无意义，按有差异处理（不误杀）。
    assert _actor_counts_flat([("A", 0), ("B", 10)]) is False


# ──────────────── (d) 大纲：>=4 节接受不补齐 / <4 节补齐并打标 ────────────────
class _PlanZT:
    def get_simulation_context(self, graph_id=None, simulation_requirement=None, **kw):
        return {"graph_statistics": {}, "total_entities": 0, "related_facts": []}

    def insight_forge(self, **kw):
        raise RuntimeError("no forge in test")  # plan_outline 内被捕获忽略

    def simulation_outcomes(self, simulation_id, top_n=10):
        return ""  # falsy → 跳过


class _PlanLLM:
    def __init__(self, n_sections):
        self.n_sections = n_sections
        self.user_prompt = None

    def chat_json(self, messages=None, temperature=0.3, **kw):
        self.user_prompt = messages[-1]["content"]
        return {"title": "T", "summary": "S",
                "sections": [{"title": f"连贯章节{i}", "description": ""}
                             for i in range(self.n_sections)]}


def _plan_agent(n_sections):
    a = ReportAgent.__new__(ReportAgent)
    a.graph_id = "g1"
    a.simulation_id = "sim1"
    a.simulation_requirement = "会发生什么？"
    a.base_simulation_id = None
    a.actors = None
    a.sources = []
    a._background_block = ""
    a._sources_index = ""
    a._forecast_spine_block = ""
    a._signal_pack = ""
    a.zep_tools = _PlanZT()
    a.llm = _PlanLLM(n_sections)
    return a


def test_outline_with_five_coherent_sections_accepted_unpadded():
    outline = _plan_agent(5).plan_outline()
    assert len(outline.sections) == 5
    assert all(not s.padded for s in outline.sections)


def test_outline_below_four_sections_padded_and_flagged():
    outline = _plan_agent(3).plan_outline()
    assert len(outline.sections) == ReportAgent.OUTLINE_PAD_FLOOR_SECTIONS == 4
    assert [s.padded for s in outline.sections] == [False, False, False, True]
    # 补齐标题取自兜底清单且不与既有标题重复。
    assert outline.sections[3].title in ReportAgent._FALLBACK_SECTION_TITLES


def test_forecast_mandate_single_combined_meta_section():
    assert "预测总表与校准" in FORECAST_STRUCTURE_MANDATE
    assert "逐情景预测" in FORECAST_STRUCTURE_MANDATE
    # 旧的两节强制方法学章节标题从指令中移除（合并为一节）。
    assert "预测框架与方法" not in FORECAST_STRUCTURE_MANDATE
    assert "校准与信心" not in FORECAST_STRUCTURE_MANDATE
    # 既有测试依赖的指令头保持不变。
    assert "结构强制要求（预测优先）" in FORECAST_STRUCTURE_MANDATE


def test_plan_outline_injects_combined_mandate():
    a = _plan_agent(6)
    a.plan_outline(require_forecast_structure=True)
    assert "预测总表与校准" in a.llm.user_prompt
    assert "预测框架与方法" not in a.llm.user_prompt


# ───────────── (2) 条件工具下限：padding=0 / 证据预注入=1 / 普通=配置值 ─────────────
def _min_calls_agent():
    a = ReportAgent.__new__(ReportAgent)
    a.MIN_TOOL_CALLS_PER_SECTION = 4
    a._contested_table_block = "【争议性论断表】\n| 论断 | 双方 |"
    a._chronology_block = ""
    return a


def test_effective_min_tool_calls_padded_section_is_zero():
    a = _min_calls_agent()
    assert a._effective_min_tool_calls(ReportSection(title="校准与信心评估", padded=True)) == 0


def test_effective_min_tool_calls_evidence_injected_is_one():
    a = _min_calls_agent()
    # 标题命中争议关键词且争议表非空 → 提示词已自带匹配证据 → 有效下限 1。
    assert a._effective_min_tool_calls(ReportSection(title="风险信号与不确定性")) == 1
    # 命中关键词但对应块为空 → 无注入 → 维持配置下限。
    a._contested_table_block = ""
    assert a._effective_min_tool_calls(ReportSection(title="风险信号与不确定性")) == 4


def test_effective_min_tool_calls_plain_section_unchanged():
    a = _min_calls_agent()
    assert a._effective_min_tool_calls(ReportSection(title="产业格局展望")) == 4


def test_unused_tools_hint_is_relevance_gated():
    rendered = REACT_UNUSED_TOOLS_HINT.format(unused_list="quick_search")
    assert "承重论断" in rendered and "quick_search" in rendered
    assert "建议尝试不同工具" not in REACT_UNUSED_TOOLS_HINT  # 旧的「凑多样性」措辞已移除


def test_react_loop_accepts_final_answer_after_one_call_with_evidence_block():
    """集成线：证据预注入章节在 1 次工具调用后即可 Final Answer（旧下限 4 会拒绝）。"""
    body = "这是一段足够长的中文正文内容。" * 60  # >= MIN_VALID_SECTION_CHARS(800)

    class _LLM:
        def __init__(self):
            self.calls = []
            self._responses = [
                '<tool_call>\n{"name": "insight_forge", "parameters": {"query": "q"}}\n</tool_call>',
                "Final Answer: " + body,
            ]
            self._i = 0

        def chat(self, messages=None, temperature=0.3, max_tokens=4096, **kw):
            self.calls.append([dict(m) for m in messages])
            r = self._responses[min(self._i, len(self._responses) - 1)]
            self._i += 1
            return r

    a = ReportAgent.__new__(ReportAgent)
    a.simulation_requirement = "会发生什么？"
    a.base_simulation_id = None
    a.report_logger = None
    a.tools = {}
    a.sources = []
    a._background_block = ""
    a._sources_index = ""
    a._forecast_spine_block = ""
    a._signal_pack = ""
    a._section_tool_calls = 0
    a._contested_table_block = "【争议性论断表】\n| 论断 | 双方 |"
    a._chronology_block = ""
    a.MIN_TOOL_CALLS_PER_SECTION = 4   # 配置下限 4；证据预注入应下调为 1
    a.MAX_TOOL_CALLS_PER_SECTION = 8
    a.llm = _LLM()
    a._execute_tool = lambda name, params, report_context="": "RESULT-DATA"

    section = ReportSection(title="风险信号与不确定性", description="")
    outline = ReportOutline(title="T", summary="S", sections=[section])
    result = a._generate_section_react(section, outline, previous_sections=[])
    assert body in result
    # 恰好 2 次 LLM 调用：1 次工具决策 + 1 次 Final Answer（未被「不足 4 次」拒绝驳回）。
    assert len(a.llm.calls) == 2
    # 争议表确实被注入了系统提示词。
    assert "【争议性论断表】" in a.llm.calls[0][0]["content"]
