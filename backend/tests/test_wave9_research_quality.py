"""WAVE9 research-quality — unit tests for the pure helpers in the deerflow bridge.

Covers the deterministic pieces added for the dossier-quality workstream:
  RQ2 inline citations: build_citation_index / parse_references_section /
      strip_dangling_citation_markers / render_references_section /
      finalize_report_citations (with a stub ProgressLog);
  RQ3 cross-section dedup: paragraph_shingles / dedup_cross_section_paragraphs;
  RQ4 chart embedding: embed_chart_refs;
  PM-HZ market horizon degradation: degrade_market_queries.

Everything LLM-shaped stays behind ``_bare_synth_invoke`` — no model calls here.
Loaded via importlib like test_multipart_synthesis (the bridge is stdlib-only at
import time).
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PY = REPO / "deerflow_bridge" / "deerflow_research.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("deerflow_research_wave9", BRIDGE_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _StubLog:
    """Duck-typed ProgressLog — records lines so tests can assert on messaging."""

    def __init__(self):
        self.lines = []

    def write(self, kind, msg):
        self.lines.append((kind, msg))


# ---------------------------------------------------------------------------
# RQ2 (1) citation index construction — mirrors merge_fetched_into_sources
# ---------------------------------------------------------------------------

class TestCitationIndex:
    def test_orders_dedups_and_skips_dead_fetches(self, mod):
        fetched = [
            {"url": "https://a.example/one", "ok": True},
            {"url": "https://a.example/one", "ok": True},      # dup URL → dropped
            {"url": "https://b.example/two", "ok": False},     # dead fetch → dropped
            {"url": "not-a-url", "ok": True},                  # invalid → dropped
            {"url": "https://c.example/three", "ok": None},    # pending kept (mirrors merge)
        ]
        idx = mod.build_citation_index(fetched, cap=10)
        assert [e["url"] for e in idx] == ["https://a.example/one", "https://c.example/three"]
        assert [e["n"] for e in idx] == [1, 2]
        assert all(e["title"] for e in idx)

    def test_cap_is_respected(self, mod):
        fetched = [{"url": f"https://x.example/{i}", "ok": True} for i in range(50)]
        assert len(mod.build_citation_index(fetched, cap=7)) == 7

    def test_index_block_renders_tokens(self, mod):
        idx = mod.build_citation_index([{"url": "https://a.example/one", "ok": True}], cap=5)
        block = mod.render_citation_index_block(idx)
        assert "[S1]" in block and "https://a.example/one" in block
        assert mod.render_citation_index_block([]) == ""


# ---------------------------------------------------------------------------
# RQ2 (2) references parsing + dangling-marker stripping
# ---------------------------------------------------------------------------

class TestReferencesAndStripping:
    REPORT = (
        "## Findings\n\nTSMC guided capex to $54B [S1] while share held [S2]. "
        "A bogus claim [S9] sneaks in.\n\n"
        "## References\n\n- [S1] TSMC IR — https://a.example/one\n"
        "2. Reuters — 2026-05-12 — https://b.example/two\n\n"
        "## Appendix\n\nMore text [S2].\n"
    )

    def test_parse_references_both_entry_shapes(self, mod):
        refs = mod.parse_references_section(self.REPORT)
        assert set(refs) == {1, 2}
        assert "a.example" in refs[1] and "b.example" in refs[2]

    def test_parse_stops_at_next_heading(self, mod):
        refs = mod.parse_references_section(self.REPORT + "\n3. Ghost — https://g.example\n")
        # entry '3.' comes after '## Appendix' → outside the References section
        assert 3 not in refs

    def test_strip_dangling_keeps_valid(self, mod):
        out, kept, stripped = mod.strip_dangling_citation_markers(self.REPORT, {1, 2})
        # 4 kept = 正文 [S1]/[S2]/[S2] + References 节自己的 [S1] 条目行
        assert stripped == 1 and kept == 4
        assert "[S9]" not in out and "[S1]" in out and "[S2]" in out
        assert "claim sneaks in" in out  # 前导空格随记号一起剔除，不留双空格

    def test_strip_all_when_valid_empty(self, mod):
        out, kept, stripped = mod.strip_dangling_citation_markers("a [S1] b [S2]", set())
        assert kept == 0 and stripped == 2 and "[S" not in out

    def test_finalize_uses_report_own_references(self, mod):
        log = _StubLog()
        out = mod.finalize_report_citations(self.REPORT, log)
        assert "[S9]" not in out and "[S1]" in out
        assert any("stripped 1" in msg for _, msg in log.lines)

    def test_finalize_appends_deterministic_references_from_pinned_index(self, mod, monkeypatch):
        idx = [{"n": 1, "title": "TSMC IR", "url": "https://a.example/one"},
               {"n": 2, "title": "Reuters", "url": "https://b.example/two"}]
        monkeypatch.setattr(mod, "_PINNED_CITATION_INDEX", idx)
        log = _StubLog()
        out = mod.finalize_report_citations("Claim one [S1]. Claim ghost [S7].", log)
        assert "## References" in out
        assert "[S1] TSMC IR" in out
        assert "[S2]" not in out          # 未被引用的索引条目不进参考节
        assert "[S7]" not in out          # 越界记号剔除
        # 幂等：再跑一遍走「报告自带 References」分支，不重复追加
        out2 = mod.finalize_report_citations(out, _StubLog())
        assert out2.count("## References") == 1

    def test_finalize_strips_all_when_no_refs_and_no_index(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_PINNED_CITATION_INDEX", [])
        log = _StubLog()
        out = mod.finalize_report_citations("Model-invented [S246] number.", log)
        assert "[S246]" not in out
        assert any("stripped all" in msg for _, msg in log.lines)

    def test_finalize_disabled_is_noop(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_INLINE_CITATIONS", "false")
        txt = "Dangling [S99] stays."
        assert mod.finalize_report_citations(txt, _StubLog()) == txt


# ---------------------------------------------------------------------------
# RQ3 cross-section shingle dedup
# ---------------------------------------------------------------------------

class TestShingleDedup:
    PARA = ("The structural supercycle thesis rests on hyperscaler capex commitments "
            "totaling four hundred billion dollars across the twenty twenty six fiscal "
            "year according to consolidated guidance from all four major providers.")

    def test_verbatim_repeat_across_sections_removed(self, mod):
        s1 = f"Intro text.\n\n{self.PARA}\n\nMore analysis here."
        s2 = f"Different opening.\n\n{self.PARA}\n\nUnique closing thoughts."
        out, removed = mod.dedup_cross_section_paragraphs([s1, s2])
        assert removed == 1
        assert self.PARA in out[0]      # 首现保留
        assert self.PARA not in out[1]  # 跨节重复剔除
        assert "Unique closing thoughts." in out[1]

    def test_within_section_repeat_untouched(self, mod):
        s1 = f"{self.PARA}\n\n{self.PARA}"
        out, removed = mod.dedup_cross_section_paragraphs([s1])
        assert removed == 0 and out[0].count("structural supercycle") == 2

    def test_short_and_structural_paragraphs_ignored(self, mod):
        head = "## Same heading text repeated verbatim across sections here"
        table = "| a | b |\n| 1 | 2 |"
        s1 = f"{head}\n\n{table}\n\nShort line."
        s2 = f"{head}\n\n{table}\n\nShort line."
        out, removed = mod.dedup_cross_section_paragraphs([s1, s2])
        assert removed == 0 and out == [s1, s2]

    def test_cjk_paragraphs_dedupable(self, mod):
        para = "台积电在先进制程上的结构性领先来自三纳米与两纳米节点的良率优势以及封装产能的垂直整合。"
        out, removed = mod.dedup_cross_section_paragraphs([para, f"开头不同。\n\n{para}"])
        assert removed == 1 and para not in out[1]

    def test_shingles_empty_for_short_text(self, mod):
        assert mod.paragraph_shingles("too short", 12) == set()


# ---------------------------------------------------------------------------
# RQ4 chart embedding (deterministic Visual Annex)
# ---------------------------------------------------------------------------

class TestEmbedChartRefs:
    CHARTS = [
        {"title": "Actor network", "caption": "BIS→TSMC edge gates shipments.",
         "path": "charts/actor_network.png", "html_path": "charts/actor_network.html"},
        {"title": "Timeline", "path": "charts/timeline.html"},
    ]

    def test_appends_annex_with_png_and_html_link(self, mod):
        out = mod.embed_chart_refs("# Report\n\nBody.", self.CHARTS)
        assert "## Visual Annex" in out
        assert "![Actor network](charts/actor_network.png)" in out
        assert "[Interactive version](charts/actor_network.html)" in out
        assert "[Interactive version](charts/timeline.html)" in out
        assert "_BIS→TSMC edge gates shipments._" in out

    def test_idempotent_and_empty_safe(self, mod):
        once = mod.embed_chart_refs("Body.", self.CHARTS)
        assert mod.embed_chart_refs(once, self.CHARTS) == once
        assert mod.embed_chart_refs("Body.", []) == "Body."

    def test_annex_only_adds_figures_not_already_contextually_embedded(self, mod):
        report = ("# Report\n\n## Actors\n\n"
                  "![Actor network](charts/actor_network.png)\n\nAnalysis.")
        out = mod.embed_chart_refs(report, self.CHARTS)
        assert out.count("](charts/actor_network.png)") == 1
        assert "## Visual Annex" in out
        assert "[Interactive version](charts/timeline.html)" in out


# ---------------------------------------------------------------------------
# PM-HZ market query horizon degradation
# ---------------------------------------------------------------------------

class TestDegradeMarketQueries:
    def test_two_stage_ladder(self, mod):
        stages = mod.degrade_market_queries(
            ["TSMC advanced node market share 2030", "AI accelerator total revenue 2030", "HBM4 supply"])
        names = [s for s, _ in stages]
        assert names == ["year_stripped", "event_level"]
        s1 = dict(stages)["year_stripped"]
        assert "TSMC advanced node market share" in s1 and "AI accelerator total revenue" in s1
        assert "HBM4 supply" not in s1  # 与原始集合相同 → 不重复进阶段
        s2 = dict(stages)["event_level"]
        assert "TSMC advanced node" in s2 and "AI accelerator total" in s2
        assert all(len(q.split()) <= 3 for q in s2)

    def test_no_years_still_yields_event_level(self, mod):
        stages = mod.degrade_market_queries(["semiconductor export controls escalation timeline"])
        d = dict(stages)
        assert "year_stripped" not in d
        assert d["event_level"] == ["semiconductor export controls"]

    def test_empty_input(self, mod):
        assert mod.degrade_market_queries([]) == []

    def test_dedup_within_stage(self, mod):
        stages = dict(mod.degrade_market_queries(["Fed rate cut 2026", "Fed rate cut 2027"]))
        assert stages["year_stripped"] == ["Fed rate cut"]


# ---------------------------------------------------------------------------
# Prompt-side contracts (cheap smoke checks — no LLM)
# ---------------------------------------------------------------------------

class TestPromptContracts:
    def test_style_rules_injected_and_gateable(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_BAN_PROCESS_NARRATION", raising=False)
        assert "HARD STYLE RULES" in mod._dossier_style_rules_block()
        monkeypatch.setenv("RESEARCH_BAN_PROCESS_NARRATION", "false")
        assert mod._dossier_style_rules_block() == ""

    def test_section_prompt_carries_rules_and_citation_block(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_BAN_PROCESS_NARRATION", raising=False)
        outline = [{"title": "A", "scope": "s", "target_words": 1500, "covers": []}]
        p = mod.build_synthesis_section_prompt(
            "Q?", outline, outline[0], 0, 1, "digest line", "evidence", None,
            citation_block="\n=== SOURCE INDEX ===\n[S1] t — https://a.example")
        assert "HEADLINE-NUMBER DISCIPLINE" in p
        assert "HARD STYLE RULES" in p
        assert "SOURCE INDEX" in p
        assert "INTERNAL" in p  # working-notes digest 标头声明内部性

    def test_write_step_prompt_requires_charts_by_default(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_CHARTS_MIN", raising=False)
        monkeypatch.setenv("DEERFLOW_ALLOW_HOST_BASH", "true")
        p = mod.build_synthesis_prompt("Q?", None, depth="deep")
        assert "REQUIRED CHARTS" in p and "charts/actor_network.png" in p
        monkeypatch.setenv("RESEARCH_CHARTS_MIN", "0")
        p0 = mod.build_synthesis_prompt("Q?", None, depth="deep")
        assert "OPTIONAL CHARTS" in p0
