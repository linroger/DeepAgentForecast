"""SCALE-1 multi-part synthesis — unit tests for the pure helpers.

The bridge (deerflow_bridge/deerflow_research.py) splits the tool-free report
synthesis into outline → parallel per-section calls → deterministic stitch →
prose-word length gate. Everything LLM-shaped lives behind ``_bare_synth_invoke``;
these tests cover ONLY the pure, deterministic helpers (no model calls):
outline JSON parse + fallback, keyword-sharding block selection, prose-word
gate math, and stitch order. Loaded via importlib like test_audit_fixes_research
(the bridge is stdlib-only at import time).
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PY = REPO / "deerflow_bridge" / "deerflow_research.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("deerflow_research_multipart", BRIDGE_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _outline(n=4, **overrides):
    rows = []
    for i in range(n):
        row = {
            "title": f"Section {i}",
            "scope": f"scope keywords {i}",
            "target_words": 2000,
            "covers": [f"KIQ-{i}"],
        }
        row.update(overrides)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# (1) Outline JSON parse + fallback
# ---------------------------------------------------------------------------

class TestOutlineParse:
    def test_parses_sections_object(self, mod):
        text = json.dumps({"sections": _outline(11)})
        out = mod.parse_synthesis_outline(text)
        assert len(out) == 11
        assert out[0] == {"title": "Section 0", "scope": "scope keywords 0",
                          "target_words": 2000, "covers": ["KIQ-0"]}

    def test_parses_fenced_json_with_prose_around_it(self, mod):
        text = "Here is the plan:\n```json\n" + json.dumps({"sections": _outline(5)}) + "\n```\nDone."
        assert len(mod.parse_synthesis_outline(text)) == 5

    def test_parses_bare_top_level_array(self, mod):
        text = "Sure!\n" + json.dumps(_outline(6)) + "\nthat's the outline"
        assert len(mod.parse_synthesis_outline(text)) == 6

    def test_bracket_inside_string_does_not_break_array_scan(self, mod):
        rows = _outline(4)
        rows[0]["scope"] = 'covers the "[contested]" claims and A[1] notation'
        out = mod.parse_synthesis_outline(json.dumps(rows))
        assert len(out) == 4
        assert "[contested]" in out[0]["scope"]

    def test_garbage_returns_empty_for_fallback(self, mod):
        # 解析失败 → [] → 调用方回退单调用路径
        assert mod.parse_synthesis_outline("no json here at all") == []
        assert mod.parse_synthesis_outline("") == []
        assert mod.parse_synthesis_outline('{"sections": "not-a-list"}') == []
        assert mod.parse_synthesis_outline("[1, 2, 3]") == []  # rows aren't dicts

    def test_fewer_than_three_valid_sections_is_a_parse_failure(self, mod):
        assert mod.parse_synthesis_outline(json.dumps({"sections": _outline(2)})) == []

    def test_untitled_rows_dropped_and_target_words_clamped(self, mod):
        rows = _outline(3) + [{"scope": "no title -> dropped"}]
        rows[0]["target_words"] = 99999   # clamp down to 2500
        rows[1]["target_words"] = 10      # clamp up to 1500
        rows[2]["target_words"] = "junk"  # non-int -> default 2000
        out = mod.parse_synthesis_outline(json.dumps({"sections": rows}))
        assert [s["target_words"] for s in out] == [2500, 1500, 2000]
        assert len(out) == 3

    def test_outline_capped_at_sixteen_sections(self, mod):
        out = mod.parse_synthesis_outline(json.dumps({"sections": _outline(30)}))
        assert len(out) == 16


# ---------------------------------------------------------------------------
# (2) Keyword-sharding block selection
# ---------------------------------------------------------------------------

class TestKeywordSharding:
    BLOCKS = [
        "[web_fetch] OPEC production quotas and Saudi Arabia crude output rose in May.",
        "[web_fetch] Semiconductor exports from Taiwan hit a record; TSMC wafer capacity.",
        "[web_fetch] OPEC ministers met Saudi Arabia officials about crude quotas again.",
        "Working note: election polling in Ohio and Michigan tightened.",
    ]

    def test_relevant_blocks_beat_head_position(self, mod):
        # scope 指向石油——第 2 块（半导体）在头部也不该被选中
        packed = mod.pack_context_for_section(
            self.BLOCKS, "OPEC Saudi Arabia crude oil quotas", cap=len(self.BLOCKS[0]) + len(self.BLOCKS[2]) + 2)
        assert "OPEC production quotas" in packed
        assert "crude quotas again" in packed
        assert "Semiconductor" not in packed
        assert "polling" not in packed

    def test_selected_blocks_emitted_in_original_order(self, mod):
        packed = mod.pack_context_for_section(self.BLOCKS, "OPEC Saudi crude", cap=10_000)
        # 全部装得下 → 全选，但顺序必须是原始顺序（不是得分序）
        assert packed == "\n\n".join(self.BLOCKS)

    def test_cap_is_respected(self, mod):
        cap = len(self.BLOCKS[0]) + 5
        packed = mod.pack_context_for_section(self.BLOCKS, "OPEC Saudi crude quotas", cap=cap)
        assert len(packed) <= cap

    def test_no_scope_terms_degrades_to_head_pack(self, mod):
        # 全停用词 scope → 词项为空 → 按原始顺序头部装填（等价旧头截断）
        packed = mod.pack_context_for_section(self.BLOCKS, "the and for with", cap=len(self.BLOCKS[0]))
        assert packed == self.BLOCKS[0]

    def test_single_oversized_block_is_truncated_not_dropped(self, mod):
        big = "OPEC crude " * 500
        packed = mod.pack_context_for_section([big], "OPEC crude", cap=100)
        assert packed == big[:100]

    def test_empty_inputs(self, mod):
        assert mod.pack_context_for_section([], "anything", 1000) == ""
        assert mod.pack_context_for_section(self.BLOCKS, "OPEC", 0) == ""

    def test_cjk_scope_terms_route_chinese_blocks(self, mod):
        blocks = ["半导体出口创新高，台积电产能满载。", "石油减产协议延长，沙特下调产量。"]
        packed = mod.pack_context_for_section(blocks, "石油 减产 沙特", cap=len(blocks[1]))
        assert packed == blocks[1]

    def test_score_prefers_distinct_term_coverage_over_repetition(self, mod):
        terms = ["opec", "saudi", "crude"]
        spam = "opec " * 50                       # 1 个词项刷 50 次
        broad = "opec saudi crude quotas"          # 3 个词项各 1 次
        assert mod.score_block_for_scope(broad, terms) > mod.score_block_for_scope(spam, terms)
        assert mod.score_block_for_scope("irrelevant text", terms) == 0


# ---------------------------------------------------------------------------
# (3) Prose-word-count gate math
# ---------------------------------------------------------------------------

class TestProseWordGate:
    def test_plain_prose_counts_words(self, mod):
        assert mod.count_prose_words("five plain english words here") == 5

    def test_table_rows_urls_and_fences_do_not_count(self, mod):
        text = (
            "real prose line\n"
            "| col_a | col_b |\n"
            "| ----- | ----- |\n"
            "| 1,234 | 5,678 |\n"
            "see https://example.com/very/long/path?q=1 for more\n"
            "```python\nx = 1\nprint(x)\n```\n"
        )
        # real(1) prose(2) line(3) + see(4) for(5) more(6) — 表格/URL/代码全不计
        assert mod.count_prose_words(text) == 6

    def test_unclosed_fence_stripped_to_end(self, mod):
        assert mod.count_prose_words("two words\n```\ncode until end of text") == 2

    def test_cjk_chars_count_as_words(self, mod):
        # 6 个汉字 + 2 个英文词
        assert mod.count_prose_words("深度研究报告 deep research") == 8

    def test_hyphens_and_apostrophes_stay_one_word(self, mod):
        assert mod.count_prose_words("state-of-the-art isn't two") == 3

    def test_empty(self, mod):
        assert mod.count_prose_words("") == 0
        assert mod.count_prose_words(None) == 0

    def test_min_words_defaults_and_override(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MIN_WORDS", raising=False)
        assert mod._synthesis_min_words("deep") == 9000
        assert mod._synthesis_min_words("standard") == 4500
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "12000")
        assert mod._synthesis_min_words("deep") == 12000
        assert mod._synthesis_min_words("quick") == 12000
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")  # 0 = gate off
        assert mod._synthesis_min_words("deep") == 0
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "junk")  # 非法回退默认
        assert mod._synthesis_min_words("deep") == 9000

    def test_select_thinnest_sections_orders_by_prose_words(self, mod):
        outline = _outline(4)
        texts = ["w " * 50, "w " * 5, "", "w " * 20]
        # 空节不参与再扩写；最薄的两个非空节 = index 1 (5 词) 和 index 3 (20 词)
        assert mod.select_thinnest_sections(outline, texts, k=2) == [1, 3]

    def test_select_thinnest_ties_break_by_index(self, mod):
        outline = _outline(3)
        texts = ["same words here", "same words here", "same words here"]
        assert mod.select_thinnest_sections(outline, texts, k=2) == [0, 1]


# ---------------------------------------------------------------------------
# (4) Deterministic stitch order
# ---------------------------------------------------------------------------

class TestStitch:
    def test_stitch_preserves_outline_order(self, mod):
        outline = _outline(4)
        # texts 以「乱序完成」的内容填充也没关系——列表本身就是按大纲下标索引的
        texts = [f"body {i}" for i in range(4)]
        stitched = mod.stitch_synthesis_sections(outline, texts)
        positions = [stitched.index(f"## Section {i}") for i in range(4)]
        assert positions == sorted(positions)
        assert stitched.split("\n\n")[0] == "## Section 0"

    def test_empty_sections_skipped(self, mod):
        outline = _outline(3)
        stitched = mod.stitch_synthesis_sections(outline, ["body 0", "", "body 2"])
        assert "## Section 1" not in stitched
        assert "## Section 0" in stitched and "## Section 2" in stitched

    def test_duplicate_leading_heading_deduped(self, mod):
        outline = [{"title": "Actors", "scope": "s", "target_words": 2000, "covers": []}]
        stitched = mod.stitch_synthesis_sections(outline, ["## Actors\nThe cast is small."])
        assert stitched == "## Actors\n\nThe cast is small."

    def test_non_matching_heading_preserved(self, mod):
        outline = [{"title": "Actors", "scope": "s", "target_words": 2000, "covers": []}]
        stitched = mod.stitch_synthesis_sections(outline, ["### Sub-point\nDetail."])
        assert stitched == "## Actors\n\n### Sub-point\nDetail."

    def test_all_empty_yields_empty(self, mod):
        assert mod.stitch_synthesis_sections(_outline(3), ["", "", ""]) == ""


# ---------------------------------------------------------------------------
# Env-knob defaults (degrade-safe)
# ---------------------------------------------------------------------------

class TestKnobs:
    def test_multipart_default_deep_only(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_MULTIPART_SYNTHESIS", raising=False)
        assert mod._multipart_synthesis_enabled("deep") is True
        assert mod._multipart_synthesis_enabled("standard") is False
        assert mod._multipart_synthesis_enabled("quick") is False

    def test_multipart_env_forces_all_depths(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_MULTIPART_SYNTHESIS", "true")
        assert mod._multipart_synthesis_enabled("quick") is True
        monkeypatch.setenv("RESEARCH_MULTIPART_SYNTHESIS", "false")
        assert mod._multipart_synthesis_enabled("deep") is False

    def test_workers_default_and_bad_value(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_WORKERS", raising=False)
        assert mod._synthesis_workers() == 4
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "9")
        assert mod._synthesis_workers() == 9
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "0")
        assert mod._synthesis_workers() == 1  # floor
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "junk")
        assert mod._synthesis_workers() == 4


# ---------------------------------------------------------------------------
# PAR-1 — widen fan-out + parallelize deep phases (pure helpers only)
# ---------------------------------------------------------------------------

def _worker_notes(label, *gaps):
    """A worker's markdown notes block ending with a '## Gaps to carry' section."""
    gap_lines = "\n".join(f"- {g}" for g in gaps)
    return (
        f"## Evidence gathered\nFindings for {label}.\n\n"
        "## Gaps to carry into the next pass\n"
        f"{gap_lines}\n"
    )


class TestFanoutWorkerBudget:
    def test_default_is_260(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_FANOUT_WORKER_BUDGET", raising=False)
        assert mod._fanout_worker_budget() == 260

    def test_env_override(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_FANOUT_WORKER_BUDGET", "420")
        assert mod._fanout_worker_budget() == 420

    def test_bad_value_falls_back(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_FANOUT_WORKER_BUDGET", "junk")
        assert mod._fanout_worker_budget() == 260

    def test_non_positive_falls_back(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_FANOUT_WORKER_BUDGET", "0")
        assert mod._fanout_worker_budget() == 260
        monkeypatch.setenv("RESEARCH_FANOUT_WORKER_BUDGET", "-5")
        assert mod._fanout_worker_budget() == 260

    def test_blank_falls_back(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_FANOUT_WORKER_BUDGET", "")
        assert mod._fanout_worker_budget() == 260


class TestParallelPhasesFlag:
    def test_default_on(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_PARALLEL_PHASES", raising=False)
        assert mod._env_flag("RESEARCH_PARALLEL_PHASES", True) is True

    def test_env_off(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_PARALLEL_PHASES", "false")
        assert mod._env_flag("RESEARCH_PARALLEL_PHASES", True) is False


class TestParallelPhaseSlicing:
    """The parallel-phase rework slices DEEP_RESEARCH_PHASES into scope / middle / final.
    Assert the split matches the phases the spec names (primary-evidence, actors, contradictions
    run in parallel; scope first sequential; forecast-implications last sequential)."""

    def test_middle_group_is_phases_2_to_4(self, mod):
        phases = mod.DEEP_RESEARCH_PHASES
        assert phases[0]["label"] == "scope"
        assert [p["label"] for p in phases[1:-1]] == [
            "primary-evidence", "actors-and-incentives", "contradictions-and-risks",
        ]
        assert phases[-1]["label"] == "forecast-implications"


class TestGapMergeFromParallelWorkers:
    def test_merges_gaps_across_parallel_worker_notes(self, mod):
        # Three parallel workers each carry a gap section; the join collects all of them.
        w2 = _worker_notes("primary-evidence", "export-control timeline", "HBM yield data")
        w3 = _worker_notes("actors-and-incentives", "supplier contracts", "HBM yield data")  # dup
        w4 = _worker_notes("contradictions-and-risks", "demand elasticity")
        acc = []
        for note in (w2, w3, w4):
            acc = mod._merge_gaps(acc, mod.parse_gaps_from_notes(note))
        # Dedup is case-insensitive and preserves first-seen (oldest) order.
        assert acc == [
            "export-control timeline",
            "HBM yield data",
            "supplier contracts",
            "demand elasticity",
        ]

    def test_case_insensitive_dedup(self, mod):
        w_a = _worker_notes("a", "Taiwan Fab Capacity")
        w_b = _worker_notes("b", "taiwan fab capacity")
        acc = mod._merge_gaps([], mod.parse_gaps_from_notes(w_a))
        acc = mod._merge_gaps(acc, mod.parse_gaps_from_notes(w_b))
        assert acc == ["Taiwan Fab Capacity"]

    def test_empty_worker_notes_thread_nothing(self, mod):
        acc = ["seed gap"]
        acc = mod._merge_gaps(acc, mod.parse_gaps_from_notes("no gap heading here"))
        assert acc == ["seed gap"]


class TestWorkerNotesFoldIn:
    """PAR-1 full-note retention: workers on isolated threads record their FULL notes into a
    run-scoped list that synthesis folds in; reset clears it. Pure state, no model calls."""

    def test_record_and_collect_roundtrip(self, mod):
        mod._reset_fetched_sources()
        mod._record_worker_notes("子调查：TSMC", "Full notes about TSMC capex.")
        mod._record_worker_notes("阶段并行调查：primary-evidence", "Primary evidence body.")
        collected = mod._collected_worker_notes()
        assert len(collected) == 2
        assert collected[0] == "## 子调查：TSMC\n\nFull notes about TSMC capex."
        assert "Primary evidence body." in collected[1]

    def test_blank_notes_ignored(self, mod):
        mod._reset_fetched_sources()
        mod._record_worker_notes("hdr", "   ")
        mod._record_worker_notes("hdr", "")
        assert mod._collected_worker_notes() == []

    def test_reset_clears_notes(self, mod):
        mod._reset_fetched_sources()
        mod._record_worker_notes("hdr", "body")
        assert len(mod._collected_worker_notes()) == 1
        mod._reset_fetched_sources()
        assert mod._collected_worker_notes() == []


class TestFanoutAbsorptionCap:
    def test_cap_raised_to_100000(self, mod):
        # A 60k-char note (over the old 24000 cap, under the new 100000) must pass uncut.
        big = "x" * 60000
        out = mod.build_fanout_absorption_prompt("Q", big, None)
        assert "…(truncated)…" not in out
        assert big in out

    def test_over_new_cap_truncates(self, mod):
        huge = "y" * 120000
        out = mod.build_fanout_absorption_prompt("Q", huge, None)
        assert "…(truncated)…" in out
