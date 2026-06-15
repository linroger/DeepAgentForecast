"""Offline tests for concurrent report section generation (EXECPLAN2 I-6-3).

Covers the pure helpers (_is_tail_section, _build_synthesis_brief) and the
_generate_sections_concurrent orchestration with a stubbed _generate_section —
no LLM, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportOutline, ReportSection, SECTION_FAILURE_PLACEHOLDER,
)


def _outline(*titles_descs):
    secs = [ReportSection(title=t, description=d) for t, d in titles_descs]
    return ReportOutline(title="T", summary="S", sections=secs)


def _agent_with_stub(record):
    a = ReportAgent.__new__(ReportAgent)

    def _stub(section, outline, previous_sections, progress_callback, section_index):
        record.append({"title": section.title, "idx": section_index,
                       "prev": list(previous_sections)})
        if section.title == "BOOM":
            raise RuntimeError("section blew up")
        return f"CONTENT[{section.title}]"

    a._generate_section = _stub
    return a


# ----------------------------------------------------------- _is_tail_section
def test_is_tail_section():
    assert ReportAgent._is_tail_section("总结与建议")
    assert ReportAgent._is_tail_section("结论")
    assert ReportAgent._is_tail_section("执行摘要")
    assert ReportAgent._is_tail_section("Conclusion and outlook")
    assert ReportAgent._is_tail_section("Executive Summary")
    assert not ReportAgent._is_tail_section("预测场景与核心发现")
    assert not ReportAgent._is_tail_section("人群行为预测分析")
    assert not ReportAgent._is_tail_section("")


# -------------------------------------------------------- _build_synthesis_brief
def test_build_synthesis_brief_numbers_titles_and_descs():
    brief = ReportAgent._build_synthesis_brief(
        [ReportSection(title="A", description="desc-a"), ReportSection(title="B")])
    assert "1. A：desc-a" in brief
    assert "2. B" in brief
    assert "大纲" in brief  # header present


# ---------------------------------------------------- _generate_sections_concurrent
def test_concurrent_produces_all_sections_in_order():
    record = []
    a = _agent_with_stub(record)
    outline = _outline(("正文1", ""), ("正文2", ""), ("总结", ""))
    contents = a._generate_sections_concurrent(outline, concurrency=2)
    assert set(contents.keys()) == {0, 1, 2}
    assert contents[0] == "CONTENT[正文1]"
    assert contents[1] == "CONTENT[正文2]"
    assert contents[2] == "CONTENT[总结]"


def test_concurrent_body_uses_brief_tail_uses_full_body():
    record = []
    a = _agent_with_stub(record)
    outline = _outline(("正文1", "d1"), ("总结", "d2"))
    a._generate_sections_concurrent(outline, concurrency=2)
    body_call = [r for r in record if r["title"] == "正文1"][0]
    tail_call = [r for r in record if r["title"] == "总结"][0]
    # body section gets the compact synthesis brief, not other sections' full text
    assert any("大纲" in p for p in body_call["prev"])
    # tail section gets the full body text (so it can summarize concretely)
    assert any("CONTENT[正文1]" in p for p in tail_call["prev"])


def test_concurrent_failure_degrades_to_placeholder():
    a = _agent_with_stub([])
    outline = _outline(("BOOM", ""), ("ok", ""))
    contents = a._generate_sections_concurrent(outline, concurrency=2)
    assert contents[0] == SECTION_FAILURE_PLACEHOLDER
    assert contents[1] == "CONTENT[ok]"


def test_concurrent_all_tail_falls_back_to_body():
    # if every section looks like a summary, treat them as body so we still parallelize
    record = []
    a = _agent_with_stub(record)
    outline = _outline(("总结A", ""), ("结论B", ""))
    contents = a._generate_sections_concurrent(outline, concurrency=2)
    assert contents[0] == "CONTENT[总结A]" and contents[1] == "CONTENT[结论B]"
