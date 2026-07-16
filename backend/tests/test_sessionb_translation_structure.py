"""SESSION-B: structure-preserving bilingual translation + research-report path + shared PDF.

These tests prove the invariants are STRUCTURAL (guaranteed by construction), not
audited-after-the-fact:

  * a whole-unit translator that WOULD drop a heading / mangle a table / inject a
    stray number is overridden by the structural skeleton translator, so the output
    keeps the exact heading-level sequence, table row/column shape and numeric
    multiset of the source;
  * immutable tokens (numbers, [S#] citations, URLs, inline code, fences) survive
    byte-for-byte through the slot-based translator;
  * residual source-language lines are re-translated (contamination repair);
  * the same final editorial lint is applied to the translation BEFORE the audit, so
    "would still be rewritten by final editorial lint" is structurally impossible;
  * the research-report translator reuses the same primitives and passes its audit;
  * the research-report PDF reuses the SAME shared LaTeX template function as the
    forecast-report PDF.

No LLM / network: a scripted fake speaks the slot-batch JSON protocol.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import ReportAgent, ReportManager, Report, ReportStatus  # noqa: E402


def _cjk(key: str) -> str:
    return "".join(chr(0x4E00 + (ord(c) - 65)) for c in key)


def _zhify(value: str, key: str = "") -> str:
    """Deterministic Chinese-ish translation that keeps ⟦…⟧ placeholders and any
    alphanumeric proper-noun token (letter-adjacent-to-digit), mimicking a real
    translator's rule to keep names as-is.  A per-slot CJK suffix keeps outputs
    distinct so lint's duplicate-sentence dedup does not fire on the fake."""
    parts = re.split(r"(⟦[^⟧]*⟧)", value)
    out = "".join(
        p if p.startswith("⟦")
        else re.sub(r"(?<![A-Za-z0-9])[A-Za-z]+(?![A-Za-z0-9])", "文", p)
        for p in parts
    )
    return out + (_cjk(key) if key else "")


class _SlotStub:
    """Speaks the structural slot-batch JSON protocol AND, for the whole-unit path,
    deliberately DRIFTS (drops a placeholder) so the structural skeleton takes over."""

    model = "slot-stub"
    provider = "fake"

    def __init__(self):
        self.calls = []

    def chat(self, messages=None, temperature=0.0, max_tokens=4096, tier="strong", **kw):
        self.calls.append(tier)
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "same alphabetic keys" in system:
            req = json.loads(user)
            return json.dumps({k: _zhify(v, k) for k, v in req.items()}, ensure_ascii=False)
        if "one Markdown prose fragment" in system:
            return _zhify(user, "Q")
        # whole-unit translator prompt → drop one placeholder to force the skeleton.
        return re.sub(r"⟦[^⟧]*⟧", "", user, count=1)

    def chat_json(self, messages=None, **kw):
        user = messages[-1]["content"]
        out = {}
        for line in user.splitlines():
            m = re.match(r"^(\d+)\.\s+(.*)$", line)
            if m:
                out[m.group(1)] = _zhify(m.group(2))
        return out


class _HeadingDropperStub:
    """Whole-unit translator that returns Chinese but DROPS the last heading line and
    injects a stray Arabic number — the classic 'drifting' translator.  The structural
    router must reject this candidate and fall back to the skeleton."""

    model = "drift-stub"
    provider = "fake"

    def chat(self, messages=None, **kw):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "same alphabetic keys" in system:
            return json.dumps(
                {k: _zhify(v, k) for k, v in json.loads(user).items()}, ensure_ascii=False
            )
        if "one Markdown prose fragment" in system:
            return _zhify(user, "Q")
        # Whole-unit path: translate prose, but drop the FIRST heading line and add "999".
        lines = user.split("\n")
        kept = [ln for i, ln in enumerate(lines) if not (ln.startswith("#") and i > 0)]
        text = "\n".join(kept)
        return _zhify(text) + "\n\n额外 999 文字"


def _agent(llm, output_language="English"):
    a = ReportAgent.__new__(ReportAgent)
    a.llm = llm
    a.output_language = output_language
    a.simulation_id = "sim_test"
    a.graph_id = "graph_test"
    a._forecast_spine = None
    return a


_SOURCE = (
    "# Deployment Forecast 2030\n\n"
    "> A concise outlook with GR00T and X3 named systems.\n\n"
    "## Executive Summary\n\n"
    "Adoption reaches 42% by 2030 [S1]; the bear case sits at 21% by 2027 [S2]. "
    "See [source](https://example.com/a?y=2030) and `inline_code`.\n\n"
    "### Sub-point\n\n"
    "Secondary detail weighted at 15% with 150,000 units.\n\n"
    "## Scenarios & Data\n\n"
    "| Scenario | Probability | Note |\n"
    "|---|---|---|\n"
    "| Base | 42% | steady through 2030 |\n"
    "| Escalation | 21% | tail risk in 2027 |\n\n"
    "```python\n"
    "# not a heading\n"
    "## fake heading inside a fence\n"
    "x = 42\n"
    "```\n"
)


def test_structural_translator_guarantees_heading_table_number_parity_by_construction():
    """A drifting whole-unit translator cannot break structure: the router falls back
    to the skeleton, whose output matches the source signatures exactly."""
    agent = _agent(_HeadingDropperStub())
    out = agent._translate_section(_SOURCE, "简体中文（Simplified Chinese）")

    assert ReportAgent._translation_heading_signature(out) == \
        ReportAgent._translation_heading_signature(_SOURCE)
    assert ReportAgent._translation_table_signature(out) == \
        ReportAgent._translation_table_signature(_SOURCE)
    assert ReportAgent._translation_number_multiset(out) == \
        ReportAgent._translation_number_multiset(_SOURCE)
    assert ReportAgent._translation_marker_multiset(out) == \
        ReportAgent._translation_marker_multiset(_SOURCE)
    # No stray injected number survived, and the fence is byte-identical.
    assert "999" not in out
    assert ReportAgent._translation_fence_signature(out) == \
        ReportAgent._translation_fence_signature(_SOURCE)


def test_skeleton_preserves_immutable_tokens_and_proper_nouns_and_translates_prose():
    agent = _agent(_SlotStub())
    out = agent._translate_from_source_skeleton(_SOURCE, "简体中文（Simplified Chinese）")

    # Immutable tokens survive byte-for-byte.
    assert out.count("42%") == _SOURCE.count("42%")
    assert out.count("[S1]") == 1 and out.count("[S2]") == 1
    assert "https://example.com/a?y=2030" in out
    assert "`inline_code`" in out
    assert "```python\n# not a heading\n## fake heading inside a fence\nx = 42\n```" in out
    # Alphanumeric proper nouns (GR00T, X3) preserved; no placeholder leakage.
    assert "GR00T" in out and "X3" in out
    assert "⟦" not in out
    # Prose actually translated (letters became CJK).
    assert "Executive" not in out


def test_number_multiset_ignores_cross_language_sign_and_identifier_adjacency():
    # English "end-2030" (letter before hyphen) vs Chinese "文-2030" (CJK before hyphen)
    # must compare equal; identifier digits (GR00T) are excluded consistently.
    en = "quarter-end-2030 with GR00T at 42% by 2027-2030"
    zh = "季度末-2030 与 GR00T 在 42% 于 2027–2030"
    assert ReportAgent._translation_number_multiset(en) == \
        ReportAgent._translation_number_multiset(zh)
    # Sign normalization does not merge genuinely distinct magnitudes.
    assert ReportAgent._translation_number_multiset("5 and 50") == {"5": 1, "50": 1}


def test_contamination_repair_retranslates_residual_source_lines():
    agent = _agent(_SlotStub())
    contaminated = (
        "# 标题\n\n"
        "这一行是中文。\n\n"
        "This sentence stays entirely in English and must be repaired.\n"
    )
    repaired = agent._repair_variant_contamination(
        contaminated, target_is_cjk=True, target_language_name="简体中文"
    )
    from app.services.report_lint import detect_language_contamination
    assert detect_language_contamination(repaired, "Chinese")["lines"] == 0


def test_lint_before_audit_makes_would_be_rewritten_structurally_impossible(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "REPORT_TRANSLATION_CONCURRENCY", 1)
    rid = "report_lint_before_audit"
    ReportManager._ensure_report_folder(rid)
    # Citation-free source so this test isolates the structure + lint-before-audit
    # invariants (citation-namespace binding is covered by the forecast bilingual suite).
    source_no_cite = (
        "# Deployment Forecast 2030\n\n"
        "> A concise outlook with GR00T and X3 named systems.\n\n"
        "## Executive Summary\n\n"
        "Adoption reaches 42% by 2030; the bear case sits at 21% by 2027.\n\n"
        "### Sub-point\n\n"
        "Secondary detail weighted at 15% with 150,000 units.\n\n"
        "## Scenarios & Data\n\n"
        "| Scenario | Probability | Note |\n"
        "|---|---|---|\n"
        "| Base | 42% | steady through 2030 |\n"
        "| Escalation | 21% | tail risk in 2027 |\n\n"
        "```python\n# not a heading\n## fake heading inside a fence\nx = 42\n```\n"
    )
    report = Report(
        report_id=rid, simulation_id="s", graph_id="g",
        simulation_requirement="req", status=ReportStatus.COMPLETED,
        markdown_content=source_no_cite,
    )
    agent = _agent(_SlotStub())
    agent._generate_bilingual_report(rid, report)

    audit_path = ReportManager._get_report_final_audit_path(rid, "zh")
    audit = json.loads(open(audit_path, encoding="utf-8").read())
    # Structure/number/table parity hold and the lint-before-audit guarantees the
    # audit's own lint re-run is byte-stable, so the rewrite issue never appears.
    assert audit["section_parity"]["passed"] is True
    assert audit["table_parity"]["passed"] is True
    assert audit["number_parity"]["passed"] is True
    assert audit["language_lint"]["changed"] is False
    assert "translation would still be rewritten by final editorial lint" not in \
        audit.get("issues", [])
    assert audit["language_lint"]["language_contamination"]["lines"] == 0
    assert audit["hard_passed"] is True
    assert os.path.exists(ReportManager._get_report_translation_path(rid, "zh"))


def test_research_report_translation_reuses_structural_translator_and_passes_audit():
    md = (
        "## Executive Summary\n\n"
        "The 2030 base case is 42% [S1]; the plateau path is 21% [S2] with 150,000 units.\n\n"
        "## Scenarios\n\n"
        "| Scenario | Prob | Note |\n"
        "|---|---|---|\n"
        "| Base | 42% | steady |\n"
        "| Plateau | 21% | hardware-bound |\n\n"
        "## References\n\n"
        "1. [S1] IEA Outlook 2026 — iea.org — "
        "[https://www.iea.org/x](https://www.iea.org/x)\n"
        "2. [S2] Omdia 2026 — omdia.com — "
        "[https://omdia.com/y](https://omdia.com/y)\n"
    )
    agent = _agent(_SlotStub())
    result = agent.translate_research_markdown(md, label="research::test")

    assert result["available"] is True
    assert result["src"] == "en" and result["tgt"] == "zh"
    audit = result["audit"]
    assert audit["hard_passed"] is True
    assert audit["section_parity"]["passed"] is True
    assert audit["table_parity"]["passed"] is True
    assert audit["number_parity"]["passed"] is True
    # Marker multiset preserved and every [S#] survives byte-for-byte.
    assert audit["citation_parity"]["source_markers"] == audit["citation_parity"]["variant_markers"]
    variant = result["translated_md"]
    assert variant.count("[S1]") == md.count("[S1]")
    assert "## 参考来源" in variant  # References heading localized deterministically
    assert "https://www.iea.org/x" in variant


def test_research_report_translation_skips_non_en_zh_source():
    cyrillic = "## Обзор\n\nЭто текст на русском языке с 42% долей.\n"
    result = _agent(_SlotStub()).translate_research_markdown(cyrillic)
    assert result["available"] is False


def test_research_and_forecast_pdf_share_one_latex_template(tmp_path, monkeypatch):
    """The research-report PDF MUST reuse the forecast report's shared pandoc/XeLaTeX
    template function.  Both `export_pdf` and `export_document_pdf` funnel through the
    exact same `_export_pdf_pandoc` renderer — assert it is the single call site."""
    monkeypatch.setattr(Config, "REPORT_PDF_EXPORT", True)
    folder = tmp_path / "handoff"
    folder.mkdir()
    md = "# 中文标题\n\n正文 42% [S1]。\n\n| 指标 | 值 |\n|---|---|\n| 份额 | 42% |\n"

    used = {}

    def _fake_pandoc(cls, label, doc_md, doc_folder, pdf_path):
        used["label"] = label
        used["md"] = doc_md
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 shared-template")
        return True

    monkeypatch.setattr(ReportManager, "_export_pdf_pandoc", classmethod(_fake_pandoc))
    monkeypatch.setattr(
        ReportManager, "_validate_pdf_content",
        staticmethod(lambda path, m: (True, {"page_count": 1, "issues": []})),
    )

    out = ReportManager.export_document_pdf(
        md, str(folder), str(folder / "research_report.zh.pdf"), label="research::zh"
    )
    assert out and out.endswith("research_report.zh.pdf")
    # The research document went through the SAME pandoc/XeLaTeX renderer the forecast
    # report uses, proving one shared LaTeX template.
    assert used["label"] == "research::zh"
    assert "中文标题" in used["md"]
    assert ReportManager.export_document_pdf.__func__ is not None


def test_export_document_pdf_fails_closed_when_rich_markdown_cannot_render(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Config, "REPORT_PDF_EXPORT", True)
    folder = tmp_path / "h"
    folder.mkdir()
    # A table makes the doc "rich": if pandoc fails, we must NOT flatten via PyMuPDF.
    md = "# T\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    monkeypatch.setattr(
        ReportManager, "_export_pdf_pandoc",
        classmethod(lambda cls, *a, **k: False),
    )
    called = {"pymupdf": False}

    def _no_pymupdf(cls, *a, **k):
        called["pymupdf"] = True
        return True

    monkeypatch.setattr(ReportManager, "_export_pdf_pymupdf", classmethod(_no_pymupdf))
    out = ReportManager.export_document_pdf(md, str(folder), str(folder / "x.pdf"))
    assert out is None
    assert called["pymupdf"] is False  # rich markdown must not silently flatten
