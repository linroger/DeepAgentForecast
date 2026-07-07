"""Offline tests for RQ-2 report repair passes + RQ-5 per-section reflection.

No LLM/network for the repair passes (deterministic string surgery); a scripted
fake-LLM (`.chat` / `.chat_json`) for the reflection + language-purity paths — the
same __new__ + attribute-injection harness the other report tests use.

Covers:
  * RQ-2 citation backfill: uncited quant line whose number appears in a source →
    [S{i}] appended; coverage re-audited; forecast['quality']['repair'] recorded (merged).
  * RQ-2 quote grounding: ungrounded blockquote removed; cited/labeled quotes kept.
  * RQ-2 placeholder resolution: literal [S?-a]/【S?】/[S#] removed (multi-source) or
    resolved to [S1] (single source).
  * RQ-2 _run_repair_passes end-to-end: before/after recorded, existing quality keys merged.
  * RQ-2 language purity: CJK run in an English-target report translated inline;
    blockquote source string keeps original as a parenthetical; disabled/empty → no-op.
  * RQ-5 reflection gating: PASS → draft unchanged (1 critique call); revision
    instruction → revised draft adopted; invalid revision → original kept; disabled /
    MAX_REFLECTION_ROUNDS<=0 / contaminated draft → skipped (no LLM calls); wired into
    _generate_section_with_retry.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportManager, ReportOutline, ReportSection, MIN_VALID_SECTION_CHARS,
)


# ─────────────────────────────── helpers ────────────────────────────────
def _repair_agent(**over):
    """Bare agent with just the attrs the repair passes + audits touch."""
    a = ReportAgent.__new__(ReportAgent)
    a.sources = []
    a.research_report = ""
    a.situation_brief = ""
    a._background_block = ""
    a._outline_summary = ""
    a.output_language = "English"
    for k, v in over.items():
        setattr(a, k, v)
    return a


class _ChatLLM:
    """Scripted `.chat` recording every call; returns responses in order (last repeats)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages=None, temperature=0.7, max_tokens=4096, tier="strong", **kw):
        self.calls.append({"messages": messages, "temperature": temperature,
                           "max_tokens": max_tokens, "tier": tier})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class _JsonChatLLM:
    """Scripted `.chat_json` for the language-purity batch-translation path."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def chat_json(self, messages=None, temperature=0.3, max_tokens=4096, tier="strong", **kw):
        self.calls.append({"messages": messages, "tier": tier})
        return dict(self.mapping)


_LONG = "This is a sufficiently long English body paragraph with real analysis. " * 20  # >800 chars


# ─────────────────────────── RQ-2 citation backfill ─────────────────────────
def test_repair_citation_backfill_appends_matching_source_tag():
    a = _repair_agent(sources=[{"title": "Trade dossier",
                                "content": "export volume rose 37% through 2027"}])
    md = "# Title\n\nThe share reached 37% by mid-year and held.\n"
    new_md, n = a._repair_citation_backfill(md)
    assert n == 1
    assert "[S1]" in new_md
    # heading + already-cited lines are never touched
    md2 = "# 37% headline\n\nGrowth of 37% here already cited [S2].\n"
    _, n2 = a._repair_citation_backfill(md2)
    assert n2 == 0


def test_repair_citation_backfill_no_match_no_insert():
    a = _repair_agent(sources=[{"title": "Unrelated", "content": "nothing numeric here"}])
    md = "Revenue hit 99% penetration last quarter.\n"
    new_md, n = a._repair_citation_backfill(md)
    assert n == 0 and new_md == md


# ──────────────────────────── RQ-2 quote grounding ──────────────────────────
def test_repair_quote_grounding_removes_only_ungrounded():
    a = _repair_agent(research_report="The GDP grew steadily according to the ministry report.")
    md = (
        "# T\n\n"
        "> The GDP grew steadily according to the ministry report.\n\n"   # grounded → kept
        "> Aliens seized the harbor in Tokyo overnight without warning.\n\n"  # ungrounded → removed
        "> A simulated agent predicted a collapse of the coalition soon.\n"   # sim-labeled → kept
    )
    new_md, removed = a._repair_quote_grounding(md)
    assert removed == 1
    assert "ministry report" in new_md
    assert "simulated agent" in new_md
    assert "Aliens seized the harbor" not in new_md


def test_repair_quote_grounding_keeps_cited_quote():
    a = _repair_agent(research_report="irrelevant grounding corpus")
    md = "> An unmatched but properly sourced statement here [S3].\n"
    new_md, removed = a._repair_quote_grounding(md)
    assert removed == 0 and new_md == md  # has [S#] → not fabricated, kept


# ───────────────────────── RQ-2 placeholder resolution ──────────────────────
def test_repair_placeholder_tokens_removed_multi_source():
    a = _repair_agent(sources=[{"title": "a"}, {"title": "b"}])
    md = "Growth was strong [S?-a] and steady 【S?】 plus [S#] overall.\n"
    new_md, n = a._repair_placeholder_tokens(md)
    assert n == 3
    assert "[S?" not in new_md and "【S?】" not in new_md and "[S#]" not in new_md


def test_repair_placeholder_tokens_resolved_single_source():
    a = _repair_agent(sources=[{"title": "only"}])
    md = "Momentum held [S?-a] into Q4.\n"
    new_md, n = a._repair_placeholder_tokens(md)
    assert n == 1 and "[S1]" in new_md and "[S?" not in new_md


# ───────────────────────── RQ-2 _run_repair_passes E2E ──────────────────────
def test_run_repair_passes_records_before_after_and_merges(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_PUBLISH_GATE_MIN_COVERAGE", 0.5, raising=False)
    a = _repair_agent(sources=[{"title": "src", "content": "penetration reached 42% in 2027"}])
    md = "# T\n\nAdoption hit 42% across the region this cycle.\n"
    forecast = {
        "citation_audit": {"coverage": 0.0, "quantitative_claims": 1},
        # pre-existing audit finding must survive the merge (never overwrite)
        "quality": {"numeric_consistency": {"mismatch_count": 2}},
    }
    new_md = a._run_repair_passes("rid_x", forecast, md, report=None)
    assert "[S1]" in new_md
    rep = forecast["quality"]["repair"]
    assert rep["applied"] is True
    assert rep["before"]["citation_coverage"] == 0.0
    assert rep["after"]["citation_coverage"] == 1.0
    assert any(p["dimension"] == "citation_backfill" for p in rep["passes"])
    # forecast.citation_audit was re-run on the repaired markdown
    assert forecast["citation_audit"]["coverage"] == 1.0
    # existing quality key preserved (merge, not overwrite)
    assert forecast["quality"]["numeric_consistency"] == {"mismatch_count": 2}


def test_run_repair_passes_noop_when_no_dimension_fails():
    a = _repair_agent()
    md = "# T\n\nNothing to fix here.\n"
    forecast = {"citation_audit": {"coverage": 1.0, "quantitative_claims": 0}, "quality": {}}
    out = a._run_repair_passes("rid_y", forecast, md, report=None)
    assert out == md
    assert "repair" not in forecast["quality"]  # untouched


# ─────────────────────────── RQ-2 language purity ───────────────────────────
def test_language_purity_translates_cjk_in_english_report(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "REPORT_LANGUAGE_PURITY", True, raising=False)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path), raising=False)
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"), raising=False)
    llm = _JsonChatLLM({"1": "market sentiment", "2": "supply chain shifted"})
    a = _repair_agent(output_language="English", llm=llm)

    class _Rep:
        markdown_content = (
            "# Title\n\n"
            "Analysts weigh 市场情绪 heading into Q3.\n\n"
            "> The 供应链转移 reshaped the sector.\n"
        )

    rep = _Rep()
    # full_report.md write is best-effort (wrapped in try/except); missing folder is tolerated.
    a._apply_language_purity("rid_lp", rep)
    md = rep.markdown_content
    assert "市场情绪" not in md              # prose CJK translated inline (replaced)
    assert "market sentiment" in md
    # blockquote (source string) keeps the original as a parenthetical (English-target form)
    assert "supply chain shifted (供应链转移)" in md
    assert llm.calls and llm.calls[0]["tier"] == "fast"


def test_language_purity_noop_when_pure(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_LANGUAGE_PURITY", True, raising=False)
    llm = _JsonChatLLM({})
    a = _repair_agent(output_language="English", llm=llm)

    class _Rep:
        markdown_content = "# Title\n\nA fully English report with no contamination.\n"

    rep = _Rep()
    before = rep.markdown_content
    a._apply_language_purity("rid_pure", rep)
    assert rep.markdown_content == before
    assert not llm.calls  # no segments → no LLM call


def test_language_purity_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_LANGUAGE_PURITY", False, raising=False)
    llm = _JsonChatLLM({"1": "x"})
    a = _repair_agent(output_language="English", llm=llm)

    class _Rep:
        markdown_content = "# T\n\n市场情绪 lingers.\n"

    rep = _Rep()
    before = rep.markdown_content
    a._apply_language_purity("rid_off", rep)
    assert rep.markdown_content == before and not llm.calls


# ─────────────────────────── RQ-5 section reflection ────────────────────────
def _reflect_agent(responses, **over):
    a = ReportAgent.__new__(ReportAgent)
    a.output_language = "English"
    a._forecast_spine = {"scenarios": [{"name": "Escalation", "probability": 0.6},
                                       {"name": "Status quo", "probability": 0.4}]}
    a._forecast_spine_block = ""
    a._signal_pack = "Top actor: Alpha; peak actions: 42"
    a.llm = _ChatLLM(responses)
    for k, v in over.items():
        setattr(a, k, v)
    return a


def _sec_outline():
    sec = ReportSection(title="Body 1", description="")
    return sec, ReportOutline(title="T", summary="S", sections=[sec])


def test_reflection_pass_keeps_draft():
    a = _reflect_agent(["PASS"])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG, previous_sections=[])
    assert out == _LONG
    assert len(a.llm.calls) == 1                      # only the critique call
    assert a.llm.calls[0]["tier"] == "fast"           # critique routed to the cheap tier


def test_reflection_revises_on_instruction():
    revised = "Revised: " + _LONG
    a = _reflect_agent(["Align the escalation probability with the spine's 60%.", revised])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG, previous_sections=[])
    assert out == revised.strip()                     # _revise_section_draft strips the response
    assert len(a.llm.calls) == 2                      # critique + one revision draw


def test_reflection_invalid_revision_keeps_original():
    a = _reflect_agent(["Fix it.", "too short"])       # revision fails validity → keep original
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG, previous_sections=[])
    assert out == _LONG
    assert len(a.llm.calls) == 2


def test_reflection_disabled_skips(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_SECTION_REFLECTION", False, raising=False)
    a = _reflect_agent(["PASS"])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG, previous_sections=[])
    assert out == _LONG and not a.llm.calls


def test_reflection_capped_by_max_rounds_zero():
    a = _reflect_agent(["PASS"], MAX_REFLECTION_ROUNDS=0)
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG, previous_sections=[])
    assert out == _LONG and not a.llm.calls


def test_reflection_skips_contaminated_draft():
    a = _reflect_agent(["PASS"])
    sec, outline = _sec_outline()
    short = "too short to be valid"                    # < MIN_VALID_SECTION_CHARS → skip
    assert len(short) < MIN_VALID_SECTION_CHARS
    out = a._reflect_and_maybe_revise_section(sec, outline, short, previous_sections=[])
    assert out == short and not a.llm.calls


def test_reflection_wired_into_generate_section_with_retry(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_SECTION_RETRY_MAX", 0, raising=False)
    monkeypatch.setattr(Config, "REPORT_SECTION_REFLECTION", True, raising=False)
    a = _reflect_agent(["PASS"])
    a._generate_section = lambda *args, **kw: _LONG    # stub the actual draft generator
    sec, outline = _sec_outline()
    out = a._generate_section_with_retry(sec, outline, previous_sections=[])
    assert out == _LONG
    assert len(a.llm.calls) == 1                       # reflection critique ran once
