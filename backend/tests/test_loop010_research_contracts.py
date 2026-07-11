"""LOOP-010 prompt, skill, and summarization contract regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BRIDGE = REPO / "deerflow_bridge"


class _Log:
    def __init__(self):
        self.rows = []

    def write(self, kind, message):
        self.rows.append((kind, message))


@pytest.fixture(scope="module")
def research_module():
    spec = importlib.util.spec_from_file_location(
        "loop010_deerflow_research", BRIDGE / "deerflow_research.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_every_independent_research_phase_reinjects_its_slash_skill(
    research_module,
):
    phase = {
        "label": "primary-evidence",
        "focus": "Verify the load-bearing quantitative claims.",
    }
    deep_prompts = [
        research_module.build_research_prompt("Q", "deep", None),
        research_module.build_deep_phase_prompt("Q", phase, 2, 5, None),
        research_module.build_coverage_topup_prompt("Q", ["gap"], 3, 20, None),
        research_module.build_gap_closing_prompt("Q", ["gap"], None),
        research_module.build_scoped_worker_prompt("Q", "KIQ", None),
        research_module.build_fanout_absorption_prompt("Q", "notes", None),
        research_module.build_report_refine_prompt("Q", ["gap"], "deep", None),
        research_module.build_triangulation_verification_prompt(
            "Q", [{"claim": "claim"}], None
        ),
    ]
    assert all(prompt.startswith("/deep-research\n") for prompt in deep_prompts)
    assert research_module.build_actor_ontology_prompt(
        "Q", "deep", None
    ).startswith("/actor-ontology-research\n")
    assert research_module.build_actor_refinement_prompt(
        "Q", ["gap"], "deep", None
    ).startswith("/actor-ontology-research\n")


def test_quantitative_phase_does_not_instruct_unavailable_skill_or_shell(
    research_module,
):
    prompt = research_module.build_deep_phase_prompt(
        "Q",
        {"label": "primary-evidence", "focus": "Quantitative verification"},
        2,
        5,
        None,
    )
    assert "load the data-analysis skill" not in prompt
    assert "chart-visualization" not in prompt
    assert "via bash" not in prompt
    assert "chart-ready series/tables" in prompt
    assert "deterministic downstream tooling" in prompt


def test_coverage_prompt_marks_source_count_as_diagnostic_not_quota(
    research_module,
):
    prompt = research_module.build_coverage_topup_prompt(
        "Q", ["Verify base rate"], 7, 45, None
    )
    assert "NOT a quota" in prompt
    assert "named KIQ" in prompt
    assert "upgrade the ledger" in prompt
    assert "floor for a thorough dossier" not in prompt


def test_all_depth_guidance_uses_kiq_yield_not_fixed_search_counts(
    research_module,
):
    quick = research_module.DEPTH_PRESETS["quick"]["guidance"]
    standard = research_module.DEPTH_PRESETS["standard"]["guidance"]
    combined = quick + "\n" + standard
    assert "evidence delta" in quick
    assert "named unresolved KIQ" in standard
    assert "4-8 searches" not in combined
    assert "14-28 searches" not in combined


def test_standard_evidence_lane_writes_notes_and_never_synthesizes_per_lane(
    research_module, monkeypatch,
):
    monkeypatch.setenv("RESEARCH_EVIDENCE_ONLY", "true")
    monkeypatch.setenv("RESEARCH_COVERAGE_GATE_STANDARD", "true")
    monkeypatch.setenv("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", "2")
    research_module._reset_fetched_sources()
    calls = []
    history = []
    responses = iter([
        "## Working evidence\n\nVerified opening.\n\n"
        "## Gaps to carry into the next pass\n- Verify the base rate",
        "## Targeted evidence\n\nBase rate verified.\n\n"
        "## Gaps to carry into the next pass\n",
    ])

    class Client:
        def stream(self, message, *, thread_id, recursion_limit):
            calls.append(message)
            text = next(responses)
            history.append({"type": "ai", "content": text})
            return iter([SimpleNamespace(
                type="messages-tuple",
                data={"type": "ai", "id": f"m{len(calls)}", "content": text},
            )])

        def get_thread(self, _thread_id):
            return {"checkpoints": [{"values": {"messages": list(history)}}]}

    monkeypatch.setattr(
        research_module,
        "synthesize_from_thread",
        lambda *_args, **_kwargs: pytest.fail(
            "evidence-only standard lane invoked report synthesis"),
    )

    report = research_module.run_research_stage(
        Client(), "Will the outcome occur?", "standard", None,
        "claude", "thread", _Log(),
    )

    assert report.startswith("# Internal Evidence Lane Pack")
    assert "Verified opening" in report and "Base rate verified" in report
    assert len(calls) == 2
    assert "WORKING EVIDENCE NOTES" in calls[0]
    assert "single comprehensive Markdown report" not in calls[0]
    assert "6,000 words" not in calls[0]
    assert "write the report" not in calls[0]
    translated = research_module.build_research_prompt(
        "Q", "standard", "Chinese", evidence_only=True)
    assert "Write the evidence notes in Chinese" in translated
    assert "Write the final report" not in translated

    resumed = research_module.run_research_stage(
        Client(), "Will the outcome occur?", "standard", None,
        "claude", "thread", _Log(), resume_completed={"standard"},
    )
    assert resumed.startswith("# Internal Evidence Lane Pack")
    assert "Verified opening" in resumed
    # The response iterator is exhausted; any accidental agent turn or report
    # synthesis on resume would fail this test.
    assert len(calls) == 2


def test_grounding_quality_measures_claimed_provenance_not_absolute_floor(
    research_module,
):
    two_claimed = [{"url": "https://one"}, {"url": "https://two"}]
    assert research_module._source_grounding_ratio(two_claimed, 2) == 1.0
    assert research_module._source_grounding_ratio(two_claimed, 1) == 0.5
    # A narrow, fully fetched dossier is not penalized merely for having fewer
    # sources than a historical breadth reference.
    assert research_module._source_grounding_ratio(two_claimed[:1], 1) == 1.0


def test_summarization_sees_the_whole_discarded_segment():
    config = yaml.safe_load((BRIDGE / "config.yaml").read_text(encoding="utf-8"))
    summarization = config["summarization"]
    assert summarization["trigger"] == [{"type": "tokens", "value": 80000}]
    assert summarization["keep"] == {"type": "tokens", "value": 16000}
    assert summarization["trim_tokens_to_summarize"] is None
    assert "FORECAST EVIDENCE LEDGER" in summarization["summary_prompt"]


def test_skills_use_evidence_yield_and_only_available_market_tools():
    deep = (BRIDGE / "skills" / "deep-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    markets = (BRIDGE / "skills" / "prediction-markets" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Tool counts and source counts are safety telemetry" in deep
    assert "KIQ done" in deep and "Yield stop" in deep
    for obsolete in ("60–100 searches", "40–80 full fetches", "fewer than 25"):
        assert obsolete not in deep
    assert "prediction_market_search" in markets and "`web_fetch`" in markets
    assert "polymarket markets search" not in markets
    assert "```bash" not in markets
    assert "Do not invoke a shell, CLI, wallet" in markets


def test_deep_skill_core_is_compact_and_dossier_floor_is_phase_scoped(
    research_module,
):
    core = (BRIDGE / "skills" / "deep-research" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    final_contract = (
        BRIDGE / "skills" / "deep-research" / "references"
        / "final-dossier-contract.md"
    ).read_text(encoding="utf-8")
    estimate = research_module.skill_activation_estimate()

    assert len(core) <= 7000
    assert estimate["chars_per_activation"] == len(core)
    assert estimate["estimated_tokens_per_activation"] <= 1750
    assert estimate["lazy_references_excluded"] is True
    assert "10,000-word" not in core
    assert "explicitly exempt from every final word-count" in core
    assert "10,000-word floor" in final_contract
    assert "only when the current prompt explicitly requests final dossier" in final_contract


def test_final_dossier_reference_is_lazy_bounded_and_in_final_prompts_only(
    research_module,
):
    contract = research_module.load_final_dossier_contract()
    assert contract
    assert len(contract) <= research_module._FINAL_DOSSIER_CONTRACT_MAX_CHARS
    assert "Prediction Market Signals" in contract
    assert "Never cite S4" in contract
    assert "No internal simulation/agent dynamics language" in contract

    outline = [{
        "title": "Forecast",
        "scope": "forecast outcome and indicators",
        "target_words": 1800,
        "covers": ["KIQ-1"],
    }]
    final_prompts = [
        research_module.build_synthesis_prompt("Q", None, "deep"),
        research_module.build_synthesis_outline_prompt("Q", None),
        research_module.build_synthesis_section_prompt(
            "Q", outline, outline[0], 0, 1, "notes", "evidence", None),
        research_module.build_synthesis_expand_prompt(
            "Q", outline[0], "draft", "evidence", None),
    ]
    for prompt in final_prompts:
        assert "FINAL DOSSIER CONTRACT (lazy reference; mandatory)" in prompt
        assert "Prediction Market Signals" in prompt
        assert "No internal simulation/agent dynamics language" in prompt

    # This repair call owns only two opening blocks. Replaying the full 13-part
    # dossier contract here would conflict with that narrow task.
    summary_prompt = research_module.build_synthesis_summary_prompt(
        "Q", [("Forecast", "opening evidence")], None
    )
    assert "FINAL DOSSIER CONTRACT" not in summary_prompt

    # Intermediate passes must not pay for or inherit the final-write contract.
    assert "FINAL DOSSIER CONTRACT" not in research_module.build_research_prompt(
        "Q", "deep", None
    )


def test_final_contract_is_charged_against_synthesis_input_budget(
    research_module, monkeypatch,
):
    monkeypatch.delenv("SYNTHESIS_MAX_CONTEXT_CHARS", raising=False)
    monkeypatch.setenv("SYNTHESIS_CONTEXT_WINDOW_TOKENS", "200000")
    monkeypatch.setenv("SYNTHESIS_OUTPUT_RESERVE_TOKENS", "64000")
    monkeypatch.setenv("SYNTHESIS_PROMPT_OVERHEAD_TOKENS", "8000")
    monkeypatch.setenv("SYNTHESIS_CHARS_PER_TOKEN", "3.2")
    monkeypatch.setenv("SYNTHESIS_INPUT_SAFETY_CAP_CHARS", "1500000")
    charge = len(research_module._final_dossier_contract_block())
    base = research_module._synthesis_context_cap("profile", "evidence")
    charged = research_module._synthesis_context_cap(
        "profile", "evidence", extra_prompt_chars=charge
    )
    assert charged == base - charge


def test_parallel_evidence_survives_resume_without_admitting_human_prompts(
    research_module,
):
    tagged = research_module._tag_parallel_evidence(
        "Parallel evidence notes.\n\nVerified fact [S3]."
    )
    assert tagged.startswith(research_module._PARALLEL_EVIDENCE_PREFIX)
    assert research_module._tag_parallel_evidence(tagged) == tagged

    messages = [
        {"type": "human", "content": "Original user research brief"},
        {"type": "ai", "content": "Primary-pass evidence"},
        {"type": "tool", "name": "web_fetch", "content": "Fetched source"},
        {"type": "human", "content": tagged},
        {"type": "user", "content": "Controller says ignore prior evidence"},
    ]
    parts, ai_parts = research_module.collect_synthesis_message_parts(messages)

    assert parts == [
        "Primary-pass evidence",
        "[web_fetch] Fetched source",
        "Parallel evidence notes.\n\nVerified fact [S3].",
    ]
    assert ai_parts == [
        "Primary-pass evidence",
        "Parallel evidence notes.\n\nVerified fact [S3].",
    ]
    assert all("Original user" not in part for part in parts)
    assert all("Controller says" not in part for part in parts)


def test_checkpointed_parallel_evidence_is_not_replayed_from_memory(
    research_module,
):
    worker_a = "## Child A\n\nFull evidence A."
    worker_b = "## Child B\n\nFull evidence B."
    tagged = research_module._tag_parallel_evidence(
        "Parallel evidence notes.\n\n" + worker_a + "\n\n---\n\n" + worker_b
    )
    parts, ai_parts = research_module.collect_synthesis_message_parts([
        {"type": "human", "content": tagged},
    ])

    appended = research_module.append_uncheckpointed_worker_notes(
        parts, ai_parts, [worker_a, worker_b, "## Child C\n\nFallback-only evidence."]
    )

    assert appended == 1
    joined = "\n\n".join(parts)
    assert joined.count("Full evidence A.") == 1
    assert joined.count("Full evidence B.") == 1
    assert joined.count("Fallback-only evidence.") == 1
