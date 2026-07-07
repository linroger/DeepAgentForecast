"""Offline tests for BILINGUAL auto-translation (english⇄chinese report versions).

No LLM / network: a scripted fake-LLM (`.chat`) drives `_generate_bilingual_report`
via the same `__new__` + attribute-injection harness the other report tests use.
ReportManager.REPORTS_DIR is redirected into tmp so no artifacts leak.

Covers the design's required test bullets:
  * structure preservation — code/mermaid fences copied UNCHANGED, table column
    count kept, an H2-looking line INSIDE a fence is not treated as a boundary;
  * number-integrity flag — a translation that drops a '42%' token records
    translation_quality='warning' + missing_numbers, but still ships the file;
  * skip on non-CJK/non-Latin (other) language — detection returns None → no file;
  * same-language / identity no-op — translation == source → nothing written;
  * degrade on LLM error — every section call raises → falls back to source →
    no crash, no file, main report untouched;
  * Chinese→English direction produces full_report.en.md;
  * PDF/md path helpers + export_pdf(lang=) parameterization;
  * API: GET /<id>/full_report.<lang>.md serving + /pdf?lang= wiring.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportManager, Report, ReportStatus,
)


# ─────────────────────────────── helpers ────────────────────────────────
class _TransLLM:
    """Scripted `.chat` whose response is `translate(user_markdown)`; records calls.

    `translate=None` + `raises=True` simulates an LLM outage (every call raises)."""

    def __init__(self, translate=None, raises=False, model="fake-translator"):
        self.model = model
        self.provider = "fake"
        self.calls = []
        self._t = translate
        self._raises = raises

    def chat(self, messages=None, temperature=0.7, max_tokens=4096, tier="strong", **kw):
        self.calls.append({"messages": messages, "tier": tier, "max_tokens": max_tokens,
                           "temperature": temperature})
        if self._raises:
            raise RuntimeError("simulated LLM outage")
        user = (messages or [{}])[-1].get("content", "")
        return self._t(user) if self._t else user


def _bili_agent(llm, output_language="English"):
    a = ReportAgent.__new__(ReportAgent)
    a.llm = llm
    a.output_language = output_language
    a.simulation_id = "sim_test"
    a.graph_id = "graph_test"
    return a


@pytest.fixture
def reports_tmp(tmp_path, monkeypatch):
    """Redirect ReportManager.REPORTS_DIR into tmp so bilingual artifacts stay isolated."""
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(d))
    return str(d)


# An English source report with: H1 + summary, two H2 sections, an H3, a markdown
# table (3 columns), a fenced code block that CONTAINS a line starting with '## '
# (must NOT be split as a section), and percentage tokens.
_EN_MD = """# Trade Outlook Report

> A concise Outlook of the 2027 trade posture.

## Executive Summary

The Outlook base case holds at 42% while the escalation path sits at 21%.

### Sub-point

Secondary Outlook detail with 15% weighting.

## Scenarios & Data

| Scenario | Probability | Note |
|---|---|---|
| Base | 42% | steady |
| Escalation | 21% | tail |

```python
# This is code, not a heading
## fake heading inside a fence must survive verbatim
x = 42
```
"""


def _translate_outlook(user: str) -> str:
    """Faithful fake translation: only touches prose word 'Outlook' → '展望';
    leaves fences, tables, numbers, citation markup byte-identical."""
    return user.replace("Outlook", "展望")


# ─────────────────────────── section splitting ──────────────────────────
def test_split_h2_respects_fences_and_boundaries():
    chunks = ReportAgent._split_markdown_h2_sections(_EN_MD)
    # preamble (H1 + summary) + 'Executive Summary' + 'Scenarios & Data' = 3 chunks.
    assert len(chunks) == 3
    assert chunks[0].startswith("# Trade Outlook Report")
    assert chunks[1].startswith("## Executive Summary")
    assert chunks[2].startswith("## Scenarios & Data")
    # The '## fake heading' inside the code fence must live in chunk 2, not split out.
    assert "## fake heading inside a fence" in chunks[2]
    # Lossless: concatenation with '\n' reproduces the source exactly.
    assert "\n".join(chunks) == _EN_MD


# ─────────────────────────── structure preservation ─────────────────────
def test_bilingual_preserves_structure_and_writes_zh(reports_tmp):
    rid = "report_struct"
    ReportManager._ensure_report_folder(rid)
    report = Report(report_id=rid, simulation_id="sim_test", graph_id="graph_test",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_MD)
    agent = _bili_agent(_TransLLM(translate=_translate_outlook), output_language="English")

    agent._generate_bilingual_report(rid, report)

    zh_path = os.path.join(reports_tmp, rid, "full_report.zh.md")
    assert os.path.exists(zh_path)
    with open(zh_path, encoding="utf-8") as f:
        out = f.read()
    # fence content copied UNCHANGED (the in-fence pseudo-heading + code survive verbatim)
    assert "## fake heading inside a fence must survive verbatim" in out
    assert "x = 42" in out
    # table shape kept: separator row + 3-column data rows intact
    assert "|---|---|---|" in out
    assert out.count("| Base | 42% | steady |") == 1
    # prose actually translated (Outlook → 展望) — proves it wasn't a no-op skip
    assert "展望" in out and "Outlook" not in out
    # meta entry recorded on the report, number integrity OK
    assert report.translations and len(report.translations) == 1
    entry = report.translations[0]
    assert entry["lang"] == "zh" and entry["source_lang"] == "en"
    assert entry["path"] == "full_report.zh.md"
    assert entry["translation_quality"] == "ok"
    assert entry["missing_numbers"] == []
    assert entry["model"] == "fake-translator"
    # primary deliverable never mutated
    assert report.markdown_content == _EN_MD
    # meta.json round-trips the translations block
    ReportManager.save_report(report)
    reloaded = ReportManager.get_report(rid)
    assert reloaded.translations and reloaded.translations[0]["lang"] == "zh"


# ─────────────────────────── number-integrity flag ──────────────────────
def test_bilingual_number_integrity_warns_on_dropped_percent(reports_tmp):
    rid = "report_numint"
    ReportManager._ensure_report_folder(rid)
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_MD)
    # Corrupt the numbers: drop the '%' on 42% (→ '42 percent') in prose + table.
    llm = _TransLLM(translate=lambda u: u.replace("42%", "42 percent"))
    agent = _bili_agent(llm)

    agent._generate_bilingual_report(rid, report)

    # File still shipped (degrade: still shows the translation)
    assert os.path.exists(os.path.join(reports_tmp, rid, "full_report.zh.md"))
    entry = report.translations[0]
    assert entry["translation_quality"] == "warning"
    assert "42%" in entry["missing_numbers"]
    # 21% / 15% were preserved, so they must NOT appear as missing
    assert "21%" not in entry["missing_numbers"]


# ─────────────────────────── skip: other language ───────────────────────
def test_bilingual_skips_non_cjk_non_latin(reports_tmp):
    rid = "report_other"
    ReportManager._ensure_report_folder(rid)
    cyrillic = "# Отчет\n\n> Резюме\n\n## Обзор\n\nЭто текст на русском языке с 42% долей.\n"
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=cyrillic)
    llm = _TransLLM(translate=lambda u: u + " X")
    agent = _bili_agent(llm)

    agent._generate_bilingual_report(rid, report)

    # Other-script → detection returns None → no file, no translations, no LLM call.
    assert not os.path.exists(os.path.join(reports_tmp, rid, "full_report.en.md"))
    assert not os.path.exists(os.path.join(reports_tmp, rid, "full_report.zh.md"))
    assert report.translations is None
    assert llm.calls == []


# ─────────────────────────── skip: identity no-op ───────────────────────
def test_bilingual_identity_translation_is_noop(reports_tmp):
    rid = "report_ident"
    ReportManager._ensure_report_folder(rid)
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_MD)
    # Identity "translation" → translated == source → nothing to ship.
    agent = _bili_agent(_TransLLM(translate=lambda u: u))

    agent._generate_bilingual_report(rid, report)

    assert not os.path.exists(os.path.join(reports_tmp, rid, "full_report.zh.md"))
    assert report.translations is None


# ─────────────────────────── degrade on LLM error ───────────────────────
def test_bilingual_degrades_on_llm_error(reports_tmp):
    rid = "report_err"
    ReportManager._ensure_report_folder(rid)
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_MD)
    agent = _bili_agent(_TransLLM(raises=True))

    # Must not raise; every section falls back to source → identity → no file.
    agent._generate_bilingual_report(rid, report)

    assert not os.path.exists(os.path.join(reports_tmp, rid, "full_report.zh.md"))
    assert report.translations is None
    assert report.markdown_content == _EN_MD  # main deliverable untouched


# ─────────────────────── disabled flag → hard skip ──────────────────────
def test_bilingual_disabled_flag_skips(reports_tmp, monkeypatch):
    monkeypatch.setattr(Config, "REPORT_BILINGUAL", False)
    rid = "report_off"
    ReportManager._ensure_report_folder(rid)
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=_EN_MD)
    llm = _TransLLM(translate=_translate_outlook)
    agent = _bili_agent(llm)

    agent._generate_bilingual_report(rid, report)

    assert not os.path.exists(os.path.join(reports_tmp, rid, "full_report.zh.md"))
    assert report.translations is None
    assert llm.calls == []


# ─────────────────────── zh→en direction ─────────────────────────────────
def test_bilingual_chinese_source_produces_english(reports_tmp):
    rid = "report_zh"
    ReportManager._ensure_report_folder(rid)
    zh_md = ("# 贸易展望报告\n\n> 简要摘要\n\n## 执行摘要\n\n"
             "基准情景维持在 42%，升级路径为 21%。\n")
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content=zh_md)
    # Fake en translation that keeps the % tokens.
    agent = _bili_agent(_TransLLM(translate=lambda u: u.replace("执行摘要", "Executive Summary")),
                        output_language="Chinese")

    agent._generate_bilingual_report(rid, report)

    en_path = os.path.join(reports_tmp, rid, "full_report.en.md")
    assert os.path.exists(en_path)
    assert report.translations[0]["lang"] == "en"
    assert report.translations[0]["source_lang"] == "zh"


# ─────────────────────── path helpers + export_pdf(lang=) ───────────────
def test_pdf_and_translation_path_helpers(reports_tmp):
    rid = "report_paths"
    assert ReportManager._get_report_pdf_path(rid).endswith("full_report.pdf")
    assert ReportManager._get_report_pdf_path(rid, "zh").endswith("full_report.zh.pdf")
    # bogus lang → primary pdf (never a full_report.None.pdf)
    assert ReportManager._get_report_pdf_path(rid, "xx").endswith("full_report.pdf")
    assert ReportManager._get_report_translation_path(rid, "en").endswith("full_report.en.md")


def test_export_pdf_uses_translation_source(reports_tmp, monkeypatch):
    rid = "report_pdf_lang"
    ReportManager._ensure_report_folder(rid)
    # Write a zh translation md; stub the actual PDF backends so no pandoc/PyMuPDF needed.
    zh_path = ReportManager._get_report_translation_path(rid, "zh")
    with open(zh_path, "w", encoding="utf-8") as f:
        f.write("# 标题\n\n正文 42%。\n")

    seen = {}

    def _fake_pandoc(cls, report_id, md, folder, pdf_path):
        seen["pdf_path"] = pdf_path
        seen["md"] = md
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        return True

    monkeypatch.setattr(Config, "REPORT_PDF_EXPORT", True)
    monkeypatch.setattr(ReportManager, "_export_pdf_pandoc", classmethod(_fake_pandoc))

    out = ReportManager.export_pdf(rid, lang="zh")
    assert out and out.endswith("full_report.zh.pdf")
    assert seen["pdf_path"].endswith("full_report.zh.pdf")
    assert "标题" in seen["md"]
    # Missing translation for 'en' → None (degrade-safe), no crash.
    assert ReportManager.export_pdf(rid, lang="en") is None


# ─────────────────────── API: md serving + pdf?lang= ─────────────────────
@pytest.fixture
def client(reports_tmp, monkeypatch):
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_api_serves_translation_md(client, reports_tmp):
    rid = "report_api_md"
    ReportManager._ensure_report_folder(rid)
    with open(ReportManager._get_report_translation_path(rid, "zh"), "w", encoding="utf-8") as f:
        f.write("# 中文标题\n\n译文正文。\n")

    resp = client.get(f"/api/report/{rid}/full_report.zh.md")
    assert resp.status_code == 200
    assert "中文标题" in resp.get_data(as_text=True)

    # Missing en version → 404 (degrade-safe)
    assert client.get(f"/api/report/{rid}/full_report.en.md").status_code == 404
    # Unsupported lang → 404
    assert client.get(f"/api/report/{rid}/full_report.fr.md").status_code == 404


def test_api_pdf_lang_param(client, reports_tmp, monkeypatch):
    rid = "report_api_pdf"
    ReportManager._ensure_report_folder(rid)
    # meta.json so ReportManager.get_report(rid) succeeds inside the endpoint.
    report = Report(report_id=rid, simulation_id="s", graph_id="g",
                    simulation_requirement="req", status=ReportStatus.COMPLETED,
                    markdown_content="# T\n\nbody 42%.\n")
    ReportManager.save_report(report)
    with open(ReportManager._get_report_translation_path(rid, "zh"), "w", encoding="utf-8") as f:
        f.write("# 标题\n\n正文 42%。\n")

    captured = {}

    def _fake_export(cls, report_id, force=False, lang=None):
        captured["lang"] = lang
        p = ReportManager._get_report_pdf_path(report_id, lang)
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        return p

    monkeypatch.setattr(ReportManager, "export_pdf", classmethod(_fake_export))

    resp = client.get(f"/api/report/{rid}/pdf?lang=zh")
    assert resp.status_code == 200
    assert captured["lang"] == "zh"
    # bogus lang normalizes to primary (None)
    client.get(f"/api/report/{rid}/pdf?lang=fr")
    assert captured["lang"] is None
