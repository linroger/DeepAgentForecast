"""SESSIONB 下游硬门审计回归：终审可否决缺陷必须在稳定化环节有对应的确定性修复。

背景（pipe_bef6879b2e94 复跑保障）：只读终审 _final_audit_integrity_issues 对
「引用来源过度集中」与「仍存在失败占位符章节」一票否决整份成稿，但修复链此前
没有任何一环削减集中度、也不会在组装前重试占位符章节——一份收敛「稳定」的成稿
可以在全部章节成本烧完后被确定性丢弃。本文件锁定两条新增修复：

  * ReportAgent._repair_overused_citations —— 审计同源扫描（围栏/表头/分隔行/
    References 附录跳过、悬空记号不计数），非数字行按首现顺序裁到上限；数字行
    记号钉住（防与 _repair_final_quantitative_grounding 的回填互相振荡）；
  * _stabilize_publish_markdown 收敛判定与终审对齐（overused_sources 必须为空）；
  * ReportAgent._resurrect_failed_sections —— 组装前对占位符章节再走一轮既有
    生成链路，成功回写 section_NN.md；仍失败保留占位符（终审语义不变）。

无 LLM / 无网络：与其余报告测试同一套 __new__ + 属性注入 harness。
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import (  # noqa: E402
    SECTION_FAILURE_PLACEHOLDER,
    ReportAgent,
    ReportManager,
    ReportOutline,
    ReportSection,
)


# ─────────────────────────────── helpers ────────────────────────────────
def _agent(**over):
    a = ReportAgent.__new__(ReportAgent)
    a.sources = []
    a.research_report = ""
    a.situation_brief = ""
    a._background_block = ""
    a._outline_summary = ""
    a._forecast_spine = None
    a.output_language = "English"
    for k, v in over.items():
        setattr(a, k, v)
    return a


@pytest.fixture
def reports_tmp(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(d), raising=False)
    return str(d)


_S1 = {
    "title": "Official humanoid deployment tracker",
    "url": "https://example.gov/humanoid-tracker",
    "date": "2026-05-01",
    "supports": [
        "Official adoption of humanoid platforms accelerated across logistics facilities",
        "Deployment programs expanded across european warehouse operators",
        "Manufacturers signed multi-year integration agreements with automotive plants",
        "Field trials matured into standing commercial contracts in retail distribution",
    ],
}
_S2 = {
    "title": "Independent robotics market review",
    "url": "https://example.org/robotics-review",
    "date": "2026-04-02",
    "supports": ["Component supply chains consolidated around a few actuator vendors"],
}


# ───────────────── _repair_overused_citations（审计同源裁剪） ─────────────────
def test_overuse_repair_caps_plain_lines_and_audit_clears(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_MAX_CITATIONS_PER_SOURCE", 2, raising=False)
    a = _agent(sources=[_S1, _S2], _citation_index={"S1": _S1, "S2": _S2})
    md = "\n".join([
        "# Forecast",
        "",
        "Official adoption of humanoid platforms accelerated across logistics facilities. [S1]",
        "Deployment programs expanded across european warehouse operators. [S1]",
        "Manufacturers signed multi-year integration agreements with automotive plants. [S1]",
        "Field trials matured into standing commercial contracts in retail distribution. [S1]",
        "Component supply chains consolidated around a few actuator vendors. [S2]",
    ])

    repaired, stripped = a._repair_overused_citations(md, {"S1": _S1, "S2": _S2})

    assert stripped == 2
    # 首现顺序的前 cap 次保留，其后剥离；论断文字原样保留。
    assert repaired.count("[S1]") == 2
    assert "accelerated across logistics facilities. [S1]" in repaired
    assert "expanded across european warehouse operators. [S1]" in repaired
    assert "integration agreements with automotive plants." in repaired
    assert "integration agreements with automotive plants. [S1]" not in repaired
    assert "[S2]" in repaired  # 未超限来源不动
    # 修复后审计同源判定必须清零（收敛判定据此对齐终审）。
    audit = a._audit_semantic_citations(repaired, {"S1": _S1, "S2": _S2})
    assert audit["overused_sources"] == []
    assert audit["unsupported"] == 0


def test_overuse_repair_pins_numeric_lines_against_backfill_oscillation(monkeypatch):
    """数字行记号钉住：_repair_final_quantitative_grounding 会给覆盖率不足时的含数字
    无记号论断回填记号，从数字行摘记号会造成 摘除↔回填 永不收敛。"""
    monkeypatch.setattr(Config, "REPORT_MAX_CITATIONS_PER_SOURCE", 1, raising=False)
    a = _agent(sources=[_S1], _citation_index={"S1": _S1})
    md = "\n".join([
        "Adoption reached 42% across logistics facilities. [S1]",
        "Deployment programs expanded across european warehouse operators. [S1]",
        "Field trials matured into standing commercial contracts in retail distribution. [S1]",
    ])

    repaired, stripped = a._repair_overused_citations(md, {"S1": _S1})

    # 数字行（42%）钉住占掉整个预算 → 两条非数字行的记号全部剥离。
    assert stripped == 2
    assert "Adoption reached 42% across logistics facilities. [S1]" in repaired
    assert repaired.count("[S1]") == 1


def test_overuse_repair_skips_fences_tables_references_and_dangling(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_MAX_CITATIONS_PER_SOURCE", 1, raising=False)
    a = _agent(sources=[_S1], _citation_index={"S1": _S1})
    md = "\n".join([
        "Deployment programs expanded across european warehouse operators. [S1]",
        "Field trials matured into standing commercial contracts in retail distribution. [S1]",
        "```",
        "literal fenced content [S1]",
        "```",
        "| Metric [S1] | Value |",
        "|---|---|",
        "Dangling marker stays untouched. [S9]",
        "## References",
        "",
        "1. [S1] Official humanoid deployment tracker — example.gov",
    ])

    repaired, stripped = a._repair_overused_citations(md, {"S1": _S1})

    assert stripped == 1
    assert "literal fenced content [S1]" in repaired          # 围栏字面内容不动
    assert "| Metric [S1] | Value |" in repaired              # 表头行不计数不裁剪
    assert "[S9]" in repaired                                 # 悬空记号归悬空修复管
    assert "1. [S1] Official humanoid deployment tracker" in repaired  # 附录不动
    assert repaired.count("[S1]") == 5 - 1                    # 仅正文富余记号被剥离


def test_overuse_repair_noop_below_cap(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_MAX_CITATIONS_PER_SOURCE", 20, raising=False)
    a = _agent(sources=[_S1], _citation_index={"S1": _S1})
    md = "Deployment programs expanded across european warehouse operators. [S1]"
    repaired, stripped = a._repair_overused_citations(md, {"S1": _S1})
    assert stripped == 0
    assert repaired == md


# ───────────── 稳定化收敛与终审对齐（overused 一票否决 ⇒ 必须先修复） ─────────────
def test_publish_stabilizer_converges_past_overuse_hard_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "REPORT_MAX_CITATIONS_PER_SOURCE", 2, raising=False)
    reports = tmp_path / "reports"
    report_id = "report_sessionb_overuse"
    (reports / report_id).mkdir(parents=True)
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports), raising=False)
    initial = "\n".join([
        "# Forecast",
        "",
        "Official adoption of humanoid platforms accelerated across logistics facilities. [S1]",
        "",
        "Deployment programs expanded across european warehouse operators. [S1]",
        "",
        "Manufacturers signed multi-year integration agreements with automotive plants. [S1]",
        "",
        "Field trials matured into standing commercial contracts in retail distribution. [S1]",
        "",
        "Component supply chains consolidated around a few actuator vendors. [S2]",
        "",
    ])
    (reports / report_id / "full_report.md").write_text(initial, encoding="utf-8")
    report = SimpleNamespace(markdown_content=initial, failed_sections=[])
    agent = _agent(sources=[_S1, _S2], _citation_index={"S1": _S1, "S2": _S2})

    result = agent._stabilize_publish_markdown(report_id, report)

    assert result["stable"] is True
    assert result["overuse_stripped"] == 2
    # 收敛后的成稿必须能通过终审的集中度硬门（这正是此前被确定性否决的组合）。
    audit_body = "\n".join(
        chunk for chunk in ReportAgent._split_markdown_h2_sections(
            report.markdown_content
        )
        if chunk.split("\n", 1)[0].strip() not in ("## References", "## 参考来源")
    )
    semantic = agent._audit_semantic_citations(
        audit_body, {"S1": _S1, "S2": _S2}
    )
    assert semantic["overused_sources"] == []
    assert "## References" in report.markdown_content


# ─────────────── _resurrect_failed_sections（组装前占位符复活） ───────────────
def _outline():
    return ReportOutline(
        title="Humanoid outlook",
        summary="Commercialization trajectory 2026-2030.",
        sections=[
            ReportSection(title="Landscape", content="ok"),
            ReportSection(title="Supply chain", content=SECTION_FAILURE_PLACEHOLDER),
            ReportSection(title="Adoption", content=SECTION_FAILURE_PLACEHOLDER),
        ],
    )


def test_resurrect_recovers_transient_failure_and_keeps_persistent_one(
    reports_tmp, monkeypatch
):
    a = _agent()
    outline = _outline()
    report_id = "report_sessionb_resurrect"
    generated = [f"## {s.title}\n\n{s.content}" for s in outline.sections]

    def _retry(section, outline, previous_sections, progress_callback, section_index):
        if section.title == "Supply chain":
            return "Recovered supply-chain analysis grounded in pinned research."
        raise RuntimeError("provider still refuses this section")

    monkeypatch.setattr(
        a, "_generate_section_with_retry",
        lambda **kw: _retry(**kw), raising=False,
    )

    still_failed = a._resurrect_failed_sections(
        report_id, outline, ["Supply chain", "Adoption"], generated
    )

    assert still_failed == ["Adoption"]
    # 成功章节回写 section_NN.md（组装 assemble_full_report 以章节文件为准）。
    saved = open(
        os.path.join(reports_tmp, report_id, "section_02.md"), encoding="utf-8"
    ).read()
    assert "Recovered supply-chain analysis" in saved
    assert SECTION_FAILURE_PLACEHOLDER not in saved
    # 上下文同步替换；仍失败章节保留占位符。
    assert "Recovered supply-chain analysis" in generated[1]
    assert outline.sections[1].content.startswith("Recovered supply-chain")
    assert outline.sections[2].content == SECTION_FAILURE_PLACEHOLDER


def test_resurrect_treats_placeholder_and_empty_output_as_failure(
    reports_tmp, monkeypatch
):
    a = _agent()
    outline = _outline()
    outputs = {"Supply chain": SECTION_FAILURE_PLACEHOLDER, "Adoption": ""}
    monkeypatch.setattr(
        a, "_generate_section_with_retry",
        lambda **kw: outputs[kw["section"].title], raising=False,
    )

    still_failed = a._resurrect_failed_sections(
        "report_sessionb_resurrect_fail", outline,
        ["Supply chain", "Adoption"], [],
    )

    assert sorted(still_failed) == ["Adoption", "Supply chain"]
    assert outline.sections[1].content == SECTION_FAILURE_PLACEHOLDER


def test_resurrect_never_launders_unknown_titles(reports_tmp, monkeypatch):
    a = _agent()
    outline = _outline()
    monkeypatch.setattr(
        a, "_generate_section_with_retry",
        lambda **kw: "irrelevant", raising=False,
    )
    still_failed = a._resurrect_failed_sections(
        "report_sessionb_resurrect_unknown", outline, ["Ghost section"], [],
    )
    assert still_failed == ["Ghost section"]
