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
import sys
import types
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
                          "target_words": 1800, "covers": ["KIQ-0"]}

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
        rows[0]["target_words"] = 99999   # clamp down to 1800
        rows[1]["target_words"] = 10      # clamp up to 700
        rows[2]["target_words"] = "junk"  # non-int -> default 1100
        out = mod.parse_synthesis_outline(json.dumps({"sections": rows}))
        assert [s["target_words"] for s in out] == [1800, 700, 1100]
        assert len(out) == 3

    def test_outline_capped_at_twenty_four_sections(self, mod):
        out = mod.parse_synthesis_outline(json.dumps({"sections": _outline(30)}))
        assert len(out) == 24

    def test_parallel_section_targets_share_one_dossier_budget(
            self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MIN_WORDS", raising=False)
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MAX_WORDS", raising=False)
        original = _outline(18, target_words=1800)

        balanced = mod.rebalance_synthesis_outline(original, "deep")

        assert 15000 <= sum(row["target_words"] for row in balanced) <= 22000
        assert max(row["target_words"] for row in balanced) < 1800
        assert all(row["target_words"] == 1800 for row in original)

    def test_standard_twenty_four_section_outline_stays_under_ceiling(
            self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MIN_WORDS", raising=False)
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MAX_WORDS", raising=False)

        balanced = mod.rebalance_synthesis_outline(
            _outline(24, target_words=1800), "standard")

        assert sum(row["target_words"] for row in balanced) == 5175
        assert all(row["target_words"] > 0 for row in balanced)

    @pytest.mark.parametrize("depth,sections,expected_total", [
        ("deep", 18, 24150),
        ("standard", 24, 7245),
    ])
    def test_section_completion_allowances_share_one_aggregate_envelope(
            self, mod, monkeypatch, depth, sections, expected_total):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MIN_WORDS", raising=False)
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MAX_WORDS", raising=False)
        balanced = mod.rebalance_synthesis_outline(
            _outline(sections, target_words=1800), depth)

        budgets = mod.allocate_synthesis_section_output_tokens(balanced, depth)

        assert len(budgets) == sections
        assert sum(budgets) == expected_total
        assert all(value >= 256 for value in budgets)

    def test_outline_contract_adds_mechanisms_and_enriches_dense_owners(
            self, mod):
        original = [
            {"title": "Scenarios", "scope": "four cases", "target_words": 900,
             "covers": []},
            {"title": "Milestones", "scope": "turning points", "target_words": 900,
             "covers": []},
            {"title": "Resolution-Ready Binary Forecasts", "scope": "predictions",
             "target_words": 900, "covers": []},
            {"title": "Actual-Data Visualizations", "scope": "charts",
             "target_words": 900, "covers": []},
        ]

        enforced = mod.enforce_synthesis_outline_contract(
            original,
            "Forecast humanoid robots with shipments, ASP, BOM and payback through 2035",
        )
        joined = "\n".join(
            f"{row['title']} {row['scope']}" for row in enforced)

        # Five historical owners plus the mandatory cast-wide actor-
        # intelligence owner. The latter prevents forward plans, investments,
        # and decision context from living only in an optional sidecar.
        assert len(enforced) == 6
        assert "Cast-Wide Actor Intelligence" in joined
        assert "Causal Mechanism Chains" in joined
        assert "annual shipments, installed base, ASP/BOM cost" in joined
        assert "probabilities total 100%" in joined
        assert "outside-view base rate" in joined
        assert "value, unit, period, data_class" in joined
        assert "numeric trigger" in joined
        assert original[0]["scope"] == "four cases"  # caller data not mutated

    @pytest.mark.parametrize("depth,minimum,maximum", [
        ("deep", 15000, 22000),
        ("standard", 4500, 7000),
    ])
    def test_mandatory_outline_owners_stay_inside_aggregate_budget(
            self, mod, monkeypatch, depth, minimum, maximum):
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MIN_WORDS", raising=False)
        monkeypatch.delenv("RESEARCH_SYNTHESIS_MAX_WORDS", raising=False)

        enforced = mod.enforce_synthesis_outline_contract(
            _outline(20, target_words=1800), "Forecast an industry through 2035")
        balanced = mod.rebalance_synthesis_outline(enforced, depth)
        total = sum(row["target_words"] for row in balanced)

        assert minimum <= total <= maximum
        # The canonical scenario owner also owns its exact matching
        # visualization source table, so the visual contract is intentionally
        # merged rather than creating a competing probability-table section.
        # 20 proposed sections + five appended owners: actor intelligence,
        # mechanisms, scenarios, milestones, and binary forecasts. The visual
        # contract remains merged into its matching owner.
        assert len(balanced) == 25
        assert all(row["target_words"] > 0 for row in balanced)

    def test_section_prompt_carries_a_hard_local_output_limit(self, mod):
        outline = _outline(3, target_words=1000)
        prompt = mod.build_synthesis_section_prompt(
            "Forecast robots", outline, outline[0], 0, 3, "", "evidence", None)

        assert "TARGET LENGTH: about 1000 words" in prompt
        assert "HARD LIMIT: do not exceed 1150 words" in prompt

    @pytest.mark.parametrize(
        "title,required",
        [
            (
                "Cast-Wide Actor Intelligence and Behavioral Drivers",
                "cover every Tier-1/2 actor, not a sample",
            ),
            ("Causal Mechanism Chains", "3–5 numbered A→B→C→outcome chains"),
            ("Scenarios and Probabilities", "exactly four MECE cases"),
            ("Milestones and Inflection Points", "numeric trigger"),
            ("Resolution-Ready Binary Forecasts", "10–12 complete items"),
            ("Sourced Actual-Data Visualizations", "chart descriptions/specifications alone FAIL"),
        ],
    )
    def test_section_prompt_carries_owner_specific_acceptance_contract(
            self, mod, title, required):
        section = {
            "title": title,
            "scope": "deliver the requested evidence",
            "target_words": 1000,
            "covers": [],
        }
        prompt = mod.build_synthesis_section_prompt(
            "Forecast robots", [section], section, 0, 1, "", "evidence", None)

        assert "SECTION-SPECIFIC ACCEPTANCE CONTRACT" in prompt
        assert required in prompt

    def test_judge_prompt_distinguishes_downstream_rendering_from_missing_data(
            self, mod):
        prompt = mod.build_report_judge_prompt(
            "Forecast robots with actual-data visuals", None, "15,000+ words")

        assert "PNG/HTML" in prompt
        assert "不得仅因本阶段未嵌入图片而 FAIL" in prompt
        assert "value/unit/period/data_class/source/as-of" in prompt
        assert "任何章节在句中/表格中/引用标记中截断" in prompt

    def test_tool_free_model_fails_over_once_then_uses_process_circuit(
            self, mod, monkeypatch):
        monkeypatch.setenv("DEERFLOW_FALLBACK_MODEL", "antigravity")
        mod._MODEL_FAILOVER_UNTIL.clear()
        created = []
        invoked = []

        class FakeModel:
            def __init__(self, name):
                self.name = name

            def bind(self, **_kwargs):
                return self

        models_module = types.ModuleType("deerflow.models")

        def create_chat_model(name, **_kwargs):
            created.append(name)
            return FakeModel(name)

        models_module.create_chat_model = create_chat_model
        deerflow_module = types.ModuleType("deerflow")
        deerflow_module.models = models_module
        monkeypatch.setitem(sys.modules, "deerflow", deerflow_module)
        monkeypatch.setitem(sys.modules, "deerflow.models", models_module)

        def invoke(model, _messages):
            invoked.append(model.name)
            if model.name == "minimax":
                raise RuntimeError("429 token plan quota exhausted (2056)")
            return types.SimpleNamespace(content="READY")

        monkeypatch.setattr(mod, "_invoke_model", invoke)

        first, first_provider = mod._invoke_tool_free_model(
            "minimax", ["prompt"], max_output_tokens=32,
            plog=None, label="outline")
        second, second_provider = mod._invoke_tool_free_model(
            "minimax", ["prompt"], max_output_tokens=32,
            plog=None, label="section")

        assert first.content == second.content == "READY"
        assert first_provider == second_provider == "antigravity"
        assert created == ["minimax", "antigravity", "antigravity"]
        assert invoked == created

    def test_tool_free_model_does_not_mask_non_provider_programming_error(
            self, mod, monkeypatch):
        monkeypatch.setenv("DEERFLOW_FALLBACK_MODEL", "antigravity")
        mod._MODEL_FAILOVER_UNTIL.clear()
        created = []

        class FakeModel:
            def bind(self, **_kwargs):
                return self

        models_module = types.ModuleType("deerflow.models")
        models_module.create_chat_model = (
            lambda name, **_kwargs: created.append(name) or FakeModel())
        deerflow_module = types.ModuleType("deerflow")
        deerflow_module.models = models_module
        monkeypatch.setitem(sys.modules, "deerflow", deerflow_module)
        monkeypatch.setitem(sys.modules, "deerflow.models", models_module)
        monkeypatch.setattr(
            mod, "_invoke_model",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("local prompt construction bug")),
        )

        with pytest.raises(ValueError, match="prompt construction"):
            mod._invoke_tool_free_model(
                "minimax", ["prompt"], max_output_tokens=32,
                plog=None, label="outline")

        assert created == ["minimax"]

    @pytest.mark.parametrize(
        "metadata,expected",
        [
            ({"finish_reason": "length"}, "length"),
            ({"stop_reason": "max_tokens"}, "max_tokens"),
            ({"finish_reason": "STOP"}, "stop"),
            ({}, ""),
        ],
    )
    def test_model_finish_reason_normalizes_provider_metadata(
            self, mod, metadata, expected):
        response = types.SimpleNamespace(response_metadata=metadata)

        assert mod._model_finish_reason(response) == expected

    def test_model_output_truncation_detects_reason_and_exact_saturation(
            self, mod):
        by_reason = types.SimpleNamespace(
            response_metadata={"finish_reason": "length"},
            usage_metadata={"output_tokens": 100, "input_tokens": 10},
        )
        by_saturation = types.SimpleNamespace(
            response_metadata={},
            usage_metadata={"output_tokens": 3200, "input_tokens": 10},
        )
        explicit_stop = types.SimpleNamespace(
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"output_tokens": 3200, "input_tokens": 10},
        )

        assert mod._model_output_was_truncated(by_reason, 3200) is True
        assert mod._model_output_was_truncated(by_saturation, 3200) is True
        assert mod._model_output_was_truncated(explicit_stop, 3200) is False

    def test_bare_synthesis_fails_closed_on_truncated_completion(
            self, mod, monkeypatch):
        langchain_core = types.ModuleType("langchain_core")
        messages = types.ModuleType("langchain_core.messages")
        messages.HumanMessage = lambda content: types.SimpleNamespace(content=content)
        langchain_core.messages = messages
        monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.messages", messages)
        response = types.SimpleNamespace(
            content="incomplete sentence",
            response_metadata={"finish_reason": "length"},
            usage_metadata={"output_tokens": 1200, "input_tokens": 10},
        )
        monkeypatch.setattr(
            mod, "_invoke_tool_free_model",
            lambda *_args, **_kwargs: (response, "minimax"),
        )

        with pytest.raises(mod.TruncatedModelOutput, match="test-call truncated"):
            mod._bare_synth_invoke(
                "minimax", "prompt", None, "test-call", 1200, True)

    def test_multipart_section_retries_one_truncation_with_larger_budget(
            self, mod, monkeypatch):
        labels = []
        budgets = {}

        def fake_invoke(_model, _prompt, _plog=None, label="bare-model",
                        max_output_tokens=None, _fail_on_truncation=False):
            labels.append(label)
            budgets[label] = max_output_tokens
            if label == "synthesis-outline":
                return json.dumps({"sections": _outline(3, target_words=1000)})
            if label == "synthesis-section-1":
                raise mod.TruncatedModelOutput("forced cutoff")
            if label == "synthesis-summary":
                return "## Executive Summary\n\nGrounded summary."
            return "Grounded analytical prose with dates, units, and sources. " * 20

        class Log:
            def write(self, _kind, _message):
                pass

        monkeypatch.setattr(mod, "_bare_synth_invoke", fake_invoke)
        monkeypatch.setattr(mod, "_synthesis_workers", lambda: 1)
        monkeypatch.setattr(mod, "_inline_citations_enabled", lambda: False)
        monkeypatch.setattr(mod, "_dedup_shingles_enabled", lambda: False)
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")

        report = mod.synthesize_multipart(
            "Forecast robots through 2035",
            None,
            "deep",
            "minimax",
            ["Observed evidence 2025 source https://example.com"],
            ["working note"],
            "Observed evidence",
            Log(),
        )

        retry = "synthesis-section-1-truncation-retry"
        assert retry in labels
        assert budgets[retry] > budgets["synthesis-section-1"]
        assert "## Section 0" in report

    def test_malformed_outline_uses_deterministic_multipart_skeleton(
            self, mod, monkeypatch):
        calls = []

        def fake_invoke(_model, _prompt, *_args):
            calls.append(_prompt)
            if len(calls) == 1:
                return "not JSON; accidental long prose outline"
            return "Grounded section prose with observations, dates, units, and sources. " * 8

        class Log:
            def __init__(self):
                self.rows = []

            def write(self, kind, message):
                self.rows.append((kind, message))

        monkeypatch.setattr(mod, "_bare_synth_invoke", fake_invoke)
        monkeypatch.setattr(mod, "_synthesis_workers", lambda: 1)
        monkeypatch.setattr(mod, "_inline_citations_enabled", lambda: False)
        monkeypatch.setattr(mod, "_dedup_shingles_enabled", lambda: False)
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")
        log = Log()
        report = mod.synthesize_multipart(
            "Forecast robots through 2035 with scenarios and actual-data visuals",
            None,
            "deep",
            "model",
            ["Observed evidence 2025 source https://example.com"],
            ["working note"],
            "Observed evidence",
            log,
        )
        outline = mod.default_synthesis_outline("Q", None)
        assert len(outline) == 16
        assert all(f"## {section['title']}" in report for section in outline)
        assert any("deterministic 16-section skeleton" in msg for _kind, msg in log.rows)
        # One malformed outline call + one call per deterministic section + summary.
        assert len(calls) == 18

    def test_multipart_rejects_real_output_above_aggregate_ceiling(
            self, mod, monkeypatch):
        """Per-section token allowances cannot bypass the dossier-level maximum."""
        def fake_invoke(_model, _prompt, _plog=None, label="bare-model",
                        max_output_tokens=None, _fail_on_truncation=False):
            if label == "synthesis-outline":
                return json.dumps({"sections": _outline(3, target_words=1000)})
            if label == "synthesis-summary":
                return "executive summary " * 40
            return "evidence grounded analysis " * 80

        class Log:
            def write(self, _kind, _message):
                pass

        monkeypatch.setattr(mod, "_bare_synth_invoke", fake_invoke)
        monkeypatch.setattr(mod, "_synthesis_workers", lambda: 1)
        monkeypatch.setattr(mod, "_inline_citations_enabled", lambda: False)
        monkeypatch.setattr(mod, "_dedup_shingles_enabled", lambda: False)
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MAX_WORDS", "100")

        with pytest.raises(mod.OversizedSynthesisOutput, match="aggregate ceiling 100"):
            mod.synthesize_multipart(
                "Forecast robots through 2035",
                None,
                "deep",
                "minimax",
                ["Observed evidence 2025 source https://example.com"],
                ["working note"],
                "Observed evidence",
                Log(),
            )

    def test_execution_budget_bounds_outline_sections_retries_and_summary(
            self, mod, monkeypatch):
        calls = []

        def fake_invoke(_model, _prompt, _plog=None, label="bare-model",
                        max_output_tokens=None, _fail_on_truncation=False):
            calls.append((label, max_output_tokens))
            if label == "synthesis-outline":
                return json.dumps({"sections": _outline(3, target_words=40)})
            return "grounded evidence " * 20

        class Log:
            def write(self, _kind, _message):
                pass

        monkeypatch.setattr(mod, "_bare_synth_invoke", fake_invoke)
        monkeypatch.setattr(mod, "_synthesis_workers", lambda: 1)
        monkeypatch.setattr(mod, "_inline_citations_enabled", lambda: False)
        monkeypatch.setattr(mod, "_dedup_shingles_enabled", lambda: False)
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MAX_WORDS", "100")
        monkeypatch.setenv(
            "RESEARCH_SYNTHESIS_EXECUTION_MAX_OUTPUT_TOKENS", "3000")

        with pytest.raises(
                mod.SynthesisExecutionBudgetExceeded,
                match="aggregate execution envelope"):
            mod.synthesize_multipart(
                "Forecast robots through 2035",
                None,
                "deep",
                "minimax",
                ["Observed evidence 2025 source https://example.com"],
                ["working note"],
                "Observed evidence",
                Log(),
            )

        assert sum(tokens for _label, tokens in calls) <= 3000

    def test_oversized_multipart_cannot_fall_through_to_single_call(
            self, mod, monkeypatch):
        fallback_calls = []

        monkeypatch.setattr(mod, "_multipart_synthesis_enabled", lambda _depth: True)
        monkeypatch.setattr(
            mod,
            "synthesize_multipart",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                mod.OversizedSynthesisOutput("too large")),
        )
        monkeypatch.setattr(
            mod,
            "_bare_synth_invoke",
            lambda *_args, **_kwargs: fallback_calls.append(True) or "fallback",
        )
        monkeypatch.setattr(mod, "_synth_min_context_chars", lambda: 0)

        with pytest.raises(mod.OversizedSynthesisOutput, match="too large"):
            mod.synthesize_from_evidence_parts(
                ["source-grounded evidence"],
                ["research note"],
                "Forecast robots",
                None,
                "minimax",
                type("Log", (), {"write": lambda *_args: None})(),
                "deep",
            )

        assert fallback_calls == []


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

    def test_selected_positive_blocks_emitted_in_original_order(self, mod):
        packed = mod.pack_context_for_section(self.BLOCKS, "OPEC Saudi crude", cap=10_000)
        # Zero-relevance blocks are not used merely to fill spare context.
        assert packed == "\n\n".join([self.BLOCKS[0], self.BLOCKS[2]])

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

    def test_disjoint_scopes_do_not_replay_each_others_blocks(self, mod):
        oil = mod.pack_context_for_section(
            self.BLOCKS, "OPEC Saudi crude quotas", cap=10_000)
        chips = mod.pack_context_for_section(
            self.BLOCKS, "Taiwan TSMC semiconductor wafer", cap=10_000)
        assert "Semiconductor" not in oil
        assert "OPEC" not in chips
        assert "OPEC" in oil and "Semiconductor" in chips

    def test_max_blocks_prevents_many_small_matches_from_filling_context(self, mod):
        blocks = [f"OPEC evidence block {i}" for i in range(30)]
        packed = mod.pack_context_for_section(
            blocks, "OPEC evidence", cap=100_000, max_blocks=3)
        assert len(packed.split("\n\n")) == 3

    def test_citation_index_is_routed_without_renumbering(self, mod):
        entries = [
            {"n": 1, "title": "OPEC quota decision", "url": "https://oil.test/1"},
            {"n": 2, "title": "TSMC wafer capacity", "url": "https://chips.test/2"},
            {"n": 3, "title": "Saudi crude output", "url": "https://oil.test/3"},
        ]
        routed = mod.route_citation_index_for_scope(
            entries, "OPEC Saudi crude", max_entries=4)
        assert [entry["n"] for entry in routed] == [1, 3]

    def test_generic_urls_route_by_relevant_excerpt_beyond_fallback_eight(
            self, mod):
        fetched = []
        for i in range(1, 13):
            fetched.append({
                "url": f"https://evidence.test/document/{i}",
                "title": "Evidence document",
                "excerpt": (
                    "Lithium refinery bottleneck constrains 2028 supply."
                    if i == 11 else "General background evidence."
                ),
                "ok": True,
            })
        entries = mod.build_citation_index(fetched, cap=20)
        routed = mod.route_citation_index_for_scope(
            entries,
            "lithium refinery bottleneck",
            max_entries=4,
        )

        # The relevant source is outside the old generic first-eight fallback,
        # and keeps its one global source number rather than being renumbered.
        assert [entry["n"] for entry in routed] == [11]
        block = mod.render_citation_index_block(routed)
        assert "[S11]" in block
        assert "[S1]" not in block


class TestSynthesisInputBudgets:
    def test_profile_budget_reserves_output_and_prompt_tokens(
            self, mod, monkeypatch):
        monkeypatch.delenv("SYNTHESIS_MAX_CONTEXT_CHARS", raising=False)
        monkeypatch.setenv("SYNTHESIS_CONTEXT_WINDOW_TOKENS", "200000")
        monkeypatch.setenv("SYNTHESIS_OUTPUT_RESERVE_TOKENS", "64000")
        monkeypatch.setenv("SYNTHESIS_PROMPT_OVERHEAD_TOKENS", "8000")
        monkeypatch.setenv("SYNTHESIS_CHARS_PER_TOKEN", "3.2")
        monkeypatch.setenv("SYNTHESIS_INPUT_SAFETY_CAP_CHARS", "1500000")
        assert mod._synthesis_context_cap("any-profile") == 409600

    def test_aggregate_section_budget_is_bounded(self, mod, monkeypatch):
        monkeypatch.setenv("SYNTHESIS_SECTION_CONTEXT_CHARS", "60000")
        monkeypatch.setenv("SYNTHESIS_TOTAL_ROUTED_CONTEXT_CHARS", "600000")
        per_section = mod._synthesis_section_context_cap(20, 400000)
        assert per_section == 30000
        assert per_section * 20 <= 600000

    def test_cjk_context_uses_conservative_character_conversion(
            self, mod, monkeypatch):
        monkeypatch.delenv("SYNTHESIS_MAX_CONTEXT_CHARS", raising=False)
        monkeypatch.delenv("SYNTHESIS_CHARS_PER_TOKEN", raising=False)
        monkeypatch.setenv("SYNTHESIS_CONTEXT_WINDOW_TOKENS", "200000")
        monkeypatch.setenv("SYNTHESIS_OUTPUT_RESERVE_TOKENS", "64000")
        monkeypatch.setenv("SYNTHESIS_PROMPT_OVERHEAD_TOKENS", "8000")
        monkeypatch.setenv("SYNTHESIS_INPUT_SAFETY_CAP_CHARS", "1500000")
        chinese_cap = mod._synthesis_context_cap("claude", "研究证据与预测" * 100)
        english_cap = mod._synthesis_context_cap(
            "claude", "research evidence and forecast " * 100)
        assert chinese_cap == 204800
        assert english_cap == 409600


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
        assert mod._synthesis_min_words("deep") == 15000
        assert mod._synthesis_min_words("standard") == 4500
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "12000")
        assert mod._synthesis_min_words("deep") == 12000
        assert mod._synthesis_min_words("quick") == 12000
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "0")  # 0 = gate off
        assert mod._synthesis_min_words("deep") == 0
        monkeypatch.setenv("RESEARCH_SYNTHESIS_MIN_WORDS", "junk")  # 非法回退默认
        assert mod._synthesis_min_words("deep") == 15000

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
        monkeypatch.delenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", raising=False)
        monkeypatch.delenv("RESEARCH_SYNTHESIS_WORKERS", raising=False)
        assert mod._synthesis_workers() == 4
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "9")
        assert mod._synthesis_workers() == 9
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "0")
        assert mod._synthesis_workers() == 1  # floor
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "junk")
        assert mod._synthesis_workers() == 4

    def test_workers_obey_global_model_envelope(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_SYNTHESIS_WORKERS", "9")
        monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "3")
        assert mod._synthesis_workers() == 3


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

    def test_complete_sequential_ledger_removes_resolved_gaps(self, mod):
        previous = ["closed by this pass", "still open"]
        notes = _worker_notes("phase", "still open")
        advanced, plateau = mod.advance_gap_set_from_notes(previous, notes)
        assert advanced == ["still open"]
        assert plateau is False

    def test_empty_explicit_ledger_converges_but_missing_ledger_does_not(
            self, mod):
        previous = ["open KIQ"]
        closed, _ = mod.advance_gap_set_from_notes(
            previous, "## Evidence gathered\nDone.\n\n## Gaps to carry into the next pass\n")
        missing, _ = mod.advance_gap_set_from_notes(
            previous, "## Evidence gathered\nDone without required heading.")
        assert closed == []
        assert missing == previous

    def test_parallel_ledgers_close_old_if_any_worker_resolves_and_union_new(
            self, mod):
        previous = ["old A", "old B"]
        note_a = _worker_notes("a", "old A", "new C")
        note_b = _worker_notes("b", "old B", "new D")
        merged, plateau = mod.reconcile_parallel_gap_sets(
            previous, [note_a, note_b])
        assert merged == ["new C", "new D"]
        assert plateau is False


class TestConvergencePhaseScheduler:
    def test_complete_opening_ledger_replaces_scope_and_shared_actor_phase(
            self, mod):
        opening = _worker_notes("opening", "one unresolved KIQ")
        assert mod.planned_deep_phase_indices(
            opening, shared_actor_track=True) == [2, 4, 5]

    def test_missing_opening_ledger_keeps_scope(self, mod):
        assert mod.planned_deep_phase_indices(
            "working notes without ledger", shared_actor_track=True) == [1, 2, 4, 5]

    def test_scheduler_can_be_disabled_for_compatibility(self, mod):
        opening = _worker_notes("opening", "one unresolved KIQ")
        assert mod.planned_deep_phase_indices(
            opening,
            shared_actor_track=True,
            convergence_scheduler=False,
        ) == [1, 2, 3, 4, 5]


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
