#!/usr/bin/env python3
"""Regression checks for the DeerFlow deep-research bridge protocol."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "deerflow_bridge" / "deerflow_research.py"
DEPLOYED_PATH = ROOT / "deer-flow" / "deerflow_research.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("deerflow_research_bridge", BRIDGE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProgressLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def write(self, kind: str, message: str) -> None:
        self.events.append((kind, message))


def assert_bridge_synced() -> None:
    assert DEPLOYED_PATH.exists(), f"missing installed DeerFlow bridge: {DEPLOYED_PATH}"
    assert BRIDGE_PATH.read_text(encoding="utf-8") == DEPLOYED_PATH.read_text(encoding="utf-8")


def assert_loop_detection_patch() -> None:
    """The multi-pass protocol depends on per-run loop-budget resets.

    Upstream DeerFlow accumulates per-tool call counts across ALL turns of a
    thread, so deep research's pass 2+ would instantly hit
    "[FORCED STOP] Tool web_search called N times" once pass 0/1 crossed the
    cumulative limit. The overlay ships a patched middleware that resets the
    counters at the start of each agent run; verify the patch exists, is
    deployed, and stays byte-identical to the overlay copy.
    """
    overlay = ROOT / "deerflow_bridge" / "patches" / "middlewares" / "loop_detection_middleware.py"
    deployed = ROOT / "deer-flow" / "backend" / "packages" / "harness" / "deerflow" / "agents" / "middlewares" / "loop_detection_middleware.py"
    assert overlay.exists(), f"missing overlay middleware patch: {overlay}"
    text = overlay.read_text(encoding="utf-8")
    assert "_reset_run_scoped_loop_state" in text, "overlay middleware lost the per-run reset"
    for hook in ("def before_agent", "async def abefore_agent"):
        idx = text.find(hook)
        assert idx != -1 and "_reset_run_scoped_loop_state" in text[idx : idx + 400], (
            f"{hook} no longer calls the per-run reset"
        )
    if deployed.exists():
        assert deployed.read_text(encoding="utf-8") == text, (
            "deployed deer-flow middleware differs from overlay — re-run ./setup.sh"
        )


def assert_harness_marker_stripping(module) -> None:
    """Loop-detection notices must never leak into the research dossier."""
    dirty = (
        "## 主要证据\n\n实质内容第一段。\n"
        "[FORCED STOP] Tool web_search called 52 times — exceeded the per-tool safety limit. Producing final answer with results collected so far.\n"
        "实质内容第二段。\n"
        "  [LOOP DETECTED] You have called web_search 30 times without producing a final answer. Stop calling tools and produce your final answer now.\n"
        "结论。"
    )
    cleaned = module.strip_think(dirty)
    assert "FORCED STOP" not in cleaned and "LOOP DETECTED" not in cleaned
    assert "实质内容第一段。" in cleaned and "实质内容第二段。" in cleaned and "结论。" in cleaned


def assert_deep_prompt_contract(module) -> None:
    prompt = module.build_research_prompt("semiconductor supply chain outlook", "deep", "Chinese")
    assert "MULTI-PASS" in prompt
    assert "Do NOT write the final dossier yet" in prompt
    assert len(module.DEEP_RESEARCH_PHASES) == 5
    assert [phase["label"] for phase in module.DEEP_RESEARCH_PHASES] == [
        "scope",
        "primary-evidence",
        "actors-and-incentives",
        "contradictions-and-risks",
        "forecast-implications",
    ]
    synthesis = module.build_synthesis_prompt("semiconductor supply chain outlook", "Chinese", "deep")
    assert "8,000" in synthesis
    assert "multi-pass deep investigation" in synthesis


def assert_deep_runner_sequence(module) -> None:
    calls: list[tuple[str, int]] = []
    original_run = module.run_streamed_turn
    original_synth = module.synthesize_from_thread

    def fake_run(client, message, thread_id, recursion_limit, plog, label):
        calls.append((label, recursion_limit))
        return f"notes from {label}"

    def fake_synth(client, thread_id, question, target_language, model_name, plog, depth="standard"):
        assert depth == "deep"
        return "final multi-pass dossier"

    try:
        module.run_streamed_turn = fake_run
        module.synthesize_from_thread = fake_synth
        result = module.run_research_stage(
            client=object(),
            question="semiconductor supply chain outlook",
            depth="deep",
            target_language="Chinese",
            model_name="minimax",
            thread_id="thread-test",
            plog=FakeProgressLog(),
        )
    finally:
        module.run_streamed_turn = original_run
        module.synthesize_from_thread = original_synth

    assert result == "final multi-pass dossier"
    assert [label for label, _ in calls] == [
        "research:deep-opening",
        "research:deep-1-scope",
        "research:deep-2-primary-evidence",
        "research:deep-3-actors-and-incentives",
        "research:deep-4-contradictions-and-risks",
        "research:deep-5-forecast-implications",
    ]
    assert calls[0][1] == 220
    assert sum(limit for _, limit in calls) == 1660


if __name__ == "__main__":
    assert_bridge_synced()
    assert_loop_detection_patch()
    bridge = load_bridge()
    assert_deep_prompt_contract(bridge)
    assert_deep_runner_sequence(bridge)
    assert_harness_marker_stripping(bridge)
    print("DeerFlow deep-research protocol checks passed")
