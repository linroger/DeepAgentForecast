"""Offline tests for the report bucket spine/turn-budget changes.

Covers, with no LLM/network (stubbed deps):
  * R2-KG-7  _build_causal_spine_block (chokepoint neighborhoods + source→outcome path)
  * R2-DETAIL-2 plan_outline forecast-spine + required-structure injection (and no-op default)
  * REPORT-9  per-turn max_tokens (tool-turn budget below MIN, full SECTION budget at/after MIN)
  * REPORT-10 ReAct temperature reads Config.REPORT_AGENT_TEMPERATURE
  * REPORT-8  interview_agents excluded from the unused-tools nudge set
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportOutline, ReportSection,
)


# ───────────────────────────────── R2-KG-7 ──────────────────────────────────
class _CascadeZT:
    """Stub zep_tools recording trace_cascade calls; returns plausible renders."""

    def __init__(self, degraded=False):
        self.calls = []
        self.degraded = degraded

    def trace_cascade(self, graph_id, source="", target="", center="", causal_only=True):
        self.calls.append({"graph_id": graph_id, "source": source,
                            "target": target, "center": center})
        if self.degraded:
            return "（图谱遍历失败）"
        if center:
            return f"【{center} 的多跳传导邻域（2 条边）】\n· {center} --[CAUSES]--> X"
        if source and target:
            return f"【传导路径：{source} → {target}】\n· (1跳) {source} --[CAUSES]--> {target}"
        return "（trace_cascade 需要 source+target 或 center）"


def _causal_agent(actors, zt):
    a = ReportAgent.__new__(ReportAgent)
    a.graph_id = "g1"
    a.actors = actors
    a.zep_tools = zt
    return a


def test_causal_spine_block_renders_chokepoints_and_path():
    zt = _CascadeZT()
    actors = {"actors": [
        {"name": "Alpha", "salience": {"score": 0.9}},
        {"name": "Beta", "salience": {"score": 0.6}},
    ]}
    block = _causal_agent(actors, zt)._build_causal_spine_block()
    assert "因果骨架" in block            # header present
    assert "Alpha" in block               # top chokepoint neighborhood
    assert "Beta" in block                # source→outcome path target
    # two center sweeps (Alpha, Beta) + one source→target path (Alpha→Beta)
    centers = [c for c in zt.calls if c["center"]]
    paths = [c for c in zt.calls if c["source"] and c["target"]]
    assert {c["center"] for c in centers} == {"Alpha", "Beta"}
    assert paths and paths[0]["source"] == "Alpha" and paths[0]["target"] == "Beta"


def test_causal_spine_block_ranks_by_salience():
    """Highest-salience actor is the primary chokepoint (path source)."""
    zt = _CascadeZT()
    actors = {"actors": [
        {"name": "Low", "salience": {"score": 0.3}},
        {"name": "High", "salience": {"score": 0.95}},
    ]}
    _causal_agent(actors, zt)._build_causal_spine_block()
    paths = [c for c in zt.calls if c["source"] and c["target"]]
    assert paths and paths[0]["source"] == "High"


def test_causal_spine_block_degrades_to_empty():
    # No actors → no chokepoints → "".
    assert _causal_agent(None, _CascadeZT())._build_causal_spine_block() == ""
    # All traversals degraded → filtered out → "".
    actors = {"actors": [{"name": "Alpha"}, {"name": "Beta"}]}
    assert _causal_agent(actors, _CascadeZT(degraded=True))._build_causal_spine_block() == ""


# ─────────────────────────────── R2-DETAIL-2 ────────────────────────────────
class _PlanZT:
    def get_simulation_context(self, graph_id=None, simulation_requirement=None):
        return {"graph_statistics": {}, "total_entities": 0, "related_facts": []}

    def insight_forge(self, **kw):
        raise RuntimeError("no forge in test")  # caught + ignored

    def simulation_outcomes(self, simulation_id, top_n=10):
        return ""  # falsy → skipped


class _PlanLLM:
    def __init__(self):
        self.user_prompt = None

    def chat_json(self, messages=None, temperature=0.3, **kw):
        self.user_prompt = messages[-1]["content"]
        return {"title": "T", "summary": "S",
                "sections": [{"title": f"S{i}", "description": ""} for i in range(5)]}


def _plan_agent():
    a = ReportAgent.__new__(ReportAgent)
    a.graph_id = "g1"
    a.simulation_id = "sim1"
    a.simulation_requirement = "会发生什么？"
    a.base_simulation_id = None
    a.situation_brief = ""
    a.actors = None
    a.sources = []
    a._background_block = ""
    a._sources_index = ""
    a._forecast_spine_block = ""
    a._signal_pack = ""
    a.zep_tools = _PlanZT()
    a.llm = _PlanLLM()
    return a


def test_plan_outline_injects_spine_and_required_structure():
    a = _plan_agent()
    outline = a.plan_outline(
        forecast_spine_block="SPINE_BLOCK_XYZ",
        require_forecast_structure=True,
    )
    assert isinstance(outline, ReportOutline)
    assert "SPINE_BLOCK_XYZ" in a.llm.user_prompt
    assert "结构强制要求（预测优先）" in a.llm.user_prompt


def test_plan_outline_default_is_noop():
    a = _plan_agent()
    a.plan_outline()  # defaults: no spine block, no forced structure
    assert "SPINE_BLOCK_XYZ" not in a.llm.user_prompt
    assert "结构强制要求（预测优先）" not in a.llm.user_prompt


# ───────────────────────── REPORT-8 / REPORT-9 / REPORT-10 ───────────────────
class _ReactLLM:
    """Scripted ReAct LLM recording temperature + max_tokens per chat() call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self._i = 0

    def chat(self, messages=None, temperature=0.3, max_tokens=4096, **kw):
        self.calls.append({"temperature": temperature, "max_tokens": max_tokens,
                            "messages": [dict(m) for m in messages]})
        r = self.responses[min(self._i, len(self.responses) - 1)]
        self._i += 1
        return r


def _react_agent(responses):
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
    # keep the loop short: 1 tool call satisfies MIN, 2 is the ceiling
    a.MIN_TOOL_CALLS_PER_SECTION = 1
    a.MAX_TOOL_CALLS_PER_SECTION = 2
    a.llm = _ReactLLM(responses)
    a._execute_tool = lambda name, params, report_context="": "RESULT-DATA"
    return a


def _run_react(a):
    section = ReportSection(title="正文1", description="")
    outline = ReportOutline(title="T", summary="S", sections=[section])
    body = "这是一段足够长的中文正文内容。" * 30
    return a._generate_section_react(section, outline, previous_sections=[]), body


def test_react_turn_budgets_and_temperature():
    body = "这是一段足够长的中文正文内容。" * 30
    a = _react_agent([
        '<tool_call>\n{"name": "insight_forge", "parameters": {"query": "q"}}\n</tool_call>',
        "Final Answer: " + body,
    ])
    result, _ = _run_react(a)
    assert body in result
    assert len(a.llm.calls) == 2
    # REPORT-10: every ReAct turn uses the configured temperature (not hardcoded 0.5).
    for c in a.llm.calls:
        assert c["temperature"] == Config.REPORT_AGENT_TEMPERATURE
    # REPORT-9: turn-1 (below MIN) uses the small tool-turn budget; turn-2 (at MIN, can
    # emit the final answer) uses the full section budget.
    tool_turn = getattr(Config, "REPORT_AGENT_TOOL_TURN_MAX_TOKENS", 8192)
    assert a.llm.calls[0]["max_tokens"] == tool_turn
    assert a.llm.calls[1]["max_tokens"] == Config.REPORT_AGENT_SECTION_MAX_TOKENS


def test_react_unused_hint_excludes_interview_agents():
    """REPORT-8: the 'tools you haven't used' nudge must never recommend interview_agents."""
    body = "这是一段足够长的中文正文内容。" * 30
    a = _react_agent([
        '<tool_call>\n{"name": "insight_forge", "parameters": {"query": "q"}}\n</tool_call>',
        "Final Answer: " + body,
    ])
    _run_react(a)
    # The observation after the first tool call carries the unused-tools hint.
    hint_msgs = [
        m["content"] for call in a.llm.calls for m in call["messages"]
        if isinstance(m.get("content"), str) and "你还没有使用过" in m["content"]
    ]
    assert hint_msgs, "expected an unused-tools nudge to be emitted"
    for msg in hint_msgs:
        assert "interview_agents" not in msg
