#!/usr/bin/env python3
"""DeerFlow → MiroFish research bridge (Phase 1 of the integration).

Runs the DeerFlow lead agent (deep-research skill) against a single prompt using
the embedded :class:`deerflow.client.DeerFlowClient`, and writes the **handoff
contract** that the MiroFish prediction pipeline consumes:

    <out-dir>/
        research_report.md         # the full synthesized dossier (REQUIRED)
        prediction_requirement.txt  # the prediction question (REQUIRED)
        actors.json                # structured actors/events/topics (best effort)
        sources.json               # cited URLs/titles (best effort)
        research_progress.log      # streamed tool calls / progress (tail-able)
        meta.json                  # run metadata + status

Design notes
------------
* Runs entirely inside DeerFlow's own venv — it imports ``deerflow`` only, never
  MiroFish. MiroFish launches it with ``subprocess.Popen`` and tails
  ``research_progress.log`` (mirrors how ``SimulationRunner`` drives the OASIS
  process).
* LLM auth comes from the model named in ``config.yaml`` (default ``claude``,
  i.e. ``ClaudeChatModel`` → Claude Code OAuth from ``~/.claude/.credentials.json``).
  No API key required; native tool calling preserved.
* The minimum viable contract is ``research_report.md`` + ``prediction_requirement.txt``.
  ``actors.json`` / ``sources.json`` are a fidelity bonus; failure to produce them
  is logged but does NOT fail the run (exit code stays 0 as long as a report exists).

Exit codes:
    0 = report produced.
    2 = no report produced — includes runtime, import, and unexpected errors caught by
        the catch-all handler (the report file is absent).
    3 = usage/config error before research starts — empty question, or a missing/expired
        Claude credential caught by the pre-flight check.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
import sys
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text: temp file in the same dir, fsync, then os.replace.

    The orchestrator's watchdog SIGKILLs this process group at the depth budget;
    a plain write_text mid-flush leaves a truncated/partial JSON and corrupts the
    cross-stage contract (EXECPLAN2 F-0-4).
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# ---------------------------------------------------------------------------
# Constants / depth presets
# ---------------------------------------------------------------------------

DEPTH_PRESETS: dict[str, dict[str, Any]] = {
    # recursion_limit caps the LangGraph step budget (tool loops); higher == deeper.
    # recursion_limit caps the LangGraph step budget. Each research super-step is a
    # model turn plus its (often parallel) tool calls, so a multi-angle pass needs
    # headroom to finish BOTH researching AND writing the final report. Set generously:
    # an eager model that keeps searching past this still gets caught by the tool-free
    # synthesis net, but a well-behaved run should conclude here on its own.
    "quick": {"recursion_limit": 100, "guidance": "Do a focused, efficient pass: about 4-8 searches across the key angles, then STOP searching and write the report."},
    "standard": {"recursion_limit": 360, "guidance": "Research thoroughly from multiple angles (roughly 14-28 searches), fetch the most important sources in full, cross-check the major claims, then STOP searching and write the report. Do not keep searching for marginal extra detail."},
    "deep": {"recursion_limit": 1660, "guidance": "Run the multi-pass deep research protocol. Do not compress the work into one short search pass: map the source landscape, read primary sources in full, profile actors, test contradictions, and only then synthesize a long evidence-backed dossier."},
}

DEEP_RESEARCH_PHASES: list[dict[str, Any]] = [
    {
        "label": "scope",
        "recursion_limit": 220,
        "focus": (
            "Map the full research space. Identify every major dimension, the exact "
            "sub-questions that must be answered, the most relevant primary-source "
            "classes, the likely data series, and the actors whose incentives matter. "
            "Search broadly, but end this pass with a gap list and source plan. Do NOT "
            "write the final report yet."
        ),
    },
    {
        "label": "primary-evidence",
        "recursion_limit": 360,
        "focus": (
            "Collect and read primary/high-authority evidence in depth: official filings, "
            "policy documents, standards bodies, earnings calls, regulator releases, "
            "company statements, technical roadmaps, credible datasets, and full-text "
            "industry analyses. Prefer original documents over summaries. Capture dates, "
            "numbers, URLs, and exact attribution. Do NOT write the final report yet."
        ),
    },
    {
        "label": "actors-and-incentives",
        "recursion_limit": 300,
        "focus": (
            "Build a detailed actor map. For each named company, government body, "
            "executive, platform, customer group, supplier, or competitor: identify "
            "role, stance, incentives, constraints, assets, vulnerabilities, and likely "
            "moves. Search for actor-specific evidence and conflicts of interest. Do NOT "
            "write the final report yet."
        ),
    },
    {
        "label": "contradictions-and-risks",
        "recursion_limit": 300,
        "focus": (
            "Stress-test the evidence. Search specifically for contrary data, bearish "
            "and bullish cases, regional disagreements, policy uncertainty, technological "
            "bottlenecks, second-order effects, and source conflicts. For every major "
            "claim, note whether it is confirmed, contested, or speculative. Do NOT "
            "write the final report yet."
        ),
    },
    {
        "label": "forecast-implications",
        "recursion_limit": 260,
        "focus": (
            "Translate the gathered evidence into forecast inputs for the downstream "
            "simulation: timelines, catalysts, leading indicators, measurable variables, "
            "base/upside/downside scenarios, likely winners and losers, and what each "
            "actor would know or believe. Fill remaining evidence gaps. Do NOT write the "
            "final report yet."
        ),
    },
]

# When the research turn ends with too little final text — an eager / over-researching
# model (notably MiniMax-M3) can spend its entire recursion budget on tool calls and hit
# the limit BEFORE it ever writes the report, leaving the final AI message empty — force
# ONE tool-free synthesis turn that writes the report from the already-gathered, thread-
# checkpointed research context. Without this, a perfectly good research pass (dozens of
# real sources fetched) is thrown away as "no report text produced".
# A too-SHORT primary report is as useless as a missing one for a downstream
# simulation, so trigger the high-budget, tool-free synthesis whenever the primary
# turn produced less than a full dossier's worth of text. The synthesis path runs
# with thinking OFF and the model's full max_tokens, so it reliably writes long-form;
# 4000 chars (~a couple of pages) is the floor below which we always re-synthesize.
SYNTHESIS_TRIGGER_CHARS = 4000
# Cap on how much gathered-research context to feed the tool-free synthesis net.
# Model-aware: MiniMax-M3 has a ~1M-token context window so it can ingest a very
# large slice of the gathered sources for a richer, more detailed synthesis; Claude
# (Sonnet 4.6, ~200K context) gets a smaller but still generous slice. Bigger context
# in == richer dossier out.
SYNTHESIS_MAX_CONTEXT_CHARS = 400000          # default / Claude-class (~100k tokens)
SYNTHESIS_MAX_CONTEXT_CHARS_LARGE = 900000    # MiniMax-class 1M-context (~225k tokens)


def _synthesis_context_cap(model_name: str) -> int:
    """Pick the gathered-research context cap by model context-window class."""
    name = (model_name or "").lower()
    if "minimax" in name or "qwen" in name or "deepseek" in name:
        return SYNTHESIS_MAX_CONTEXT_CHARS_LARGE
    return SYNTHESIS_MAX_CONTEXT_CHARS

REPORT_FILENAME = "research_report.md"
REQUIREMENT_FILENAME = "prediction_requirement.txt"
ACTORS_FILENAME = "actors.json"
SOURCES_FILENAME = "sources.json"
TIMELINE_FILENAME = "timeline.json"
QUANTITATIVE_FILENAME = "quantitative.json"   # EXECPLAN2 I-0-5
CONTESTED_FILENAME = "contested.json"         # EXECPLAN2 I-0-1
PROGRESS_FILENAME = "research_progress.log"
META_FILENAME = "meta.json"

# Recognized source-quality tiers (SKILL.md §4). Used for the meta tier histogram
# and to validate model-emitted tiers; anything else is dropped to "unknown".
_VALID_TIERS = ("S1", "S2", "S3", "S4")  # EXECPLAN2 I-0-0


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean feature flag from the environment (truthy 1/true/yes/on).

    This bridge runs inside DeerFlow's own venv and imports ``deerflow`` only —
    it can NOT import MiroFish's ``backend.app.config.Config``. The OPTIONAL-DEGRADE
    flags (RESEARCH_EVIDENCE_GRADING / RESEARCH_FORECAST_INPUTS) are therefore read
    from the env, which the orchestrator already forwards verbatim (``env = dict(
    os.environ)``) when it spawns this process. Unset → ``default``.
    EXECPLAN2 I-0-0 / I-0-5.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Progress logging — a tail-able, human-readable event stream
# ---------------------------------------------------------------------------


class ProgressLog:
    """Append-only progress log. MiroFish tails this file for live updates."""

    def __init__(self, path: Path):
        self._path = path
        self._fh = path.open("w", encoding="utf-8")
        # EXECPLAN2 I-0-4: the deep fan-out runs scoped workers in a ThreadPoolExecutor
        # that all call write() concurrently. Serialize the write→flush→print sequence
        # so log lines never interleave/corrupt. Single-threaded callers are unaffected.
        self._lock = threading.Lock()

    def write(self, kind: str, message: str) -> None:
        line = f"{_utcnow()} [{kind}] {message}".rstrip()
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            # Also echo to stdout so a Popen reader without the file still sees it.
            print(line, flush=True)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def _truncate(text: str, limit: int = 280) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _summarize_tool_args(args: Any) -> str:
    if isinstance(args, dict):
        # Prefer the most informative fields for search/fetch tools.
        for key in ("query", "url", "queries", "path", "command"):
            if key in args and args[key]:
                return f"{key}={_truncate(args[key], 160)}"
        return _truncate(json.dumps(args, ensure_ascii=False), 160)
    return _truncate(args, 160)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_research_prompt(question: str, depth: str, target_language: str | None) -> str:
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
    lang_line = ""
    if target_language:
        lang_line = f"\n\nWrite the final report in {target_language}."
    if depth == "deep":
        return (
            "You are a deep-research lead analyst starting a MULTI-PASS investigation. "
            "This is pass 0: orient yourself, load the deep-research workflow, and begin "
            "the source map. You will receive several follow-up research-pass prompts in "
            "this same thread before final synthesis.\n\n"
            f"RESEARCH BRIEF:\n{question}\n\n"
            f"{preset['guidance']}\n\n"
            "For this first pass, search broadly enough to understand the terrain and "
            "produce working notes, not a final report. Identify the key dimensions, "
            "actors, the relationships between actors (who allies/opposes/regulates/"
            "depends-on/influences whom), primary-source targets, likely quantitative "
            "datasets, and open questions. Use tools aggressively where needed. End with "
            "a concise research plan and gap list.\n\n"
            "IMPORTANT: Do NOT write the final dossier yet. Do NOT stop after a short "
            "summary. The downstream simulation needs dense, sourced facts, named actors, "
            "timelines, incentives, and disputed claims gathered across multiple passes."
            f"{lang_line}"
        )
    return (
        "You are a deep-research analyst. Use the deep-research methodology: search "
        "the web from multiple angles, fetch and read important primary sources in "
        "full, gather concrete data, real-world examples, expert opinion, opposing "
        "views, and current developments.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"{preset['guidance']}\n\n"
        "Then produce a single comprehensive Markdown report (your final message) that a "
        "downstream social-simulation engine will use as ground truth. The report MUST "
        "be self-contained and include, where applicable:\n"
        "  1. An executive summary of the situation.\n"
        "  2. The KEY REAL-WORLD ACTORS involved — specific named people, companies, "
        "media outlets, government bodies, and online platforms — with each actor's "
        "role, public stance, influence, and what they know/believe about the event. "
        "(Concrete named entities, NOT abstract themes.) Make the RELATIONSHIPS between "
        "these actors EXPLICIT: who allies with, opposes, competes with, regulates, "
        "depends on, partners with, or influences whom, with a one-line basis each.\n"
        "  3. A timeline of key events with dates.\n"
        "  4. The main points of contention, hot topics, and likely flashpoints.\n"
        "  5. Relevant facts, figures, and quotes, each attributable to a source.\n"
        "  6. A short list of the sources you used (titles + URLs).\n\n"
        "LENGTH & DEPTH: This dossier is the sole ground-truth a downstream simulation "
        "will reason over, so it must be LONG and richly detailed — aim for at least "
        "3,500–6,000 words for standard depth. Organize it with clear Markdown section headings (##), and "
        "under each actor and topic go deep: specific numbers, dated events, direct "
        "quotes, competing perspectives, second-order effects, and concrete scenarios. "
        "Do NOT write a terse summary — exhaustive, well-structured coverage is the goal.\n\n"
        "IMPORTANT: Once you have gathered enough material across the angles above, you "
        "MUST stop calling tools and write the full report as your very next message. The "
        "written report is the deliverable — do not keep searching for marginal extra "
        "detail. A run that never writes the report has failed."
        f"{lang_line}"
    )


def build_deep_phase_prompt(question: str, phase: dict[str, Any], index: int, total: int, target_language: str | None) -> str:
    """Prompt one explicit deep-research pass within the same DeerFlow thread."""
    lang_line = f"\n\nWrite your pass notes in {target_language}." if target_language else ""
    return (
        f"DEEP RESEARCH PASS {index}/{total}: {phase['label']}\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"PASS OBJECTIVE:\n{phase['focus']}\n\n"
        "Use web search and full-text fetching as needed. Prefer primary sources and "
        "high-authority sources. Capture concrete numbers, dates, organizations, named "
        "people, URLs/titles, direct source attribution, and unresolved uncertainty. "
        "Cross-check important claims against at least two independent sources where "
        "possible.\n\n"
        "End this pass with Markdown working notes under these headings:\n"
        "## Evidence gathered\n"
        "## Actor / incentive updates\n"
        "## Actor relationships (who allies/opposes/regulates/depends-on/influences whom, with basis)\n"
        # EXECPLAN2 I-0-5: ask each quantitative fact to carry unit + as-of date +
        # definition so the downstream extraction can build a clean quantitative.json.
        "## Quantitative facts and dates (each number WITH its unit, as-of date, definition, and source tier)\n"
        # EXECPLAN2 I-0-1: ask the contradictions pass to record WHERE the evidence
        # conflicts (positions + sources + why they differ), not just that uncertainty exists.
        "## Contradictions or uncertainty (for each: the disputed claim, the differing positions with their sources, and WHY they differ)\n"
        "## Gaps to carry into the next pass\n\n"
        "Do NOT write the final report yet. Do NOT say the research is complete. "
        "This pass is one layer of a longer investigation."
        f"{lang_line}"
    )


def build_synthesis_prompt(question: str, target_language: str | None, depth: str = "standard") -> str:
    """Prompt for a forced, tool-free 'write the report now' turn.

    Used when the research turn exhausted its step budget before writing the report.
    All gathered research is already in the thread's checkpointed history, so this
    turn must NOT call tools — it just synthesizes the collected material into the
    final report.
    """
    lang_line = f"\n\nWrite the report in {target_language}." if target_language else ""
    word_target = "8,000–12,000 words" if depth == "deep" else "3,500–6,000 words"
    extra_deep = ""
    if depth == "deep":
        extra_deep = (
            "Because this was a multi-pass deep investigation, the final report MUST "
            "preserve the richness of the research instead of compressing it. Include "
            "a detailed source-grounded actor table, a dated timeline, a quantitative "
            "evidence table, explicit winners/losers by segment, base/upside/downside "
            "scenarios, leading indicators to monitor, and a section on contested claims "
            "or evidence quality.\n\n"
        )
    return (
        "STOP researching. Do NOT call any tools, do NOT search, do NOT fetch — you "
        "have already gathered enough material in this conversation.\n\n"
        "Using ONLY the research and sources you have already collected above, write "
        "the FINAL comprehensive Markdown report NOW, as your immediate reply.\n\n"
        f"RESEARCH BRIEF (what the report must answer):\n{question}\n\n"
        "The report MUST be self-contained and include, where applicable:\n"
        "  1. An executive summary of the situation.\n"
        "  2. The KEY REAL-WORLD ACTORS — specific named people, organizations, media "
        "outlets, and government bodies — each with role, public stance, influence, and "
        "what they know/believe; plus the explicit RELATIONSHIPS between them (who allies "
        "with, opposes, competes with, regulates, depends on, or influences whom).\n"
        "  3. A timeline of key events with dates.\n"
        "  4. The main points of contention, hot topics, and likely flashpoints.\n"
        "  5. Relevant facts, figures, and quotes, each attributable to a source.\n"
        "  6. A short list of the sources you used (titles + URLs).\n\n"
        f"{extra_deep}"
        f"LENGTH & DEPTH: Write a LONG, comprehensive dossier — {word_target} — "
        "organized with clear Markdown section headings (##). Use ALL the "
        "material you gathered above: every relevant figure, dated event, direct quote, "
        "and opposing view. Go deep on each actor and topic; do not summarize tersely.\n"
        "Write the report directly — no preamble, no tool calls."
        f"{lang_line}"
    )


def _message_text(content: Any) -> str:
    """Flatten a LangChain message ``content`` (str or list-of-parts) to clean text."""
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", "")))
            else:
                parts.append(str(c))
        content = " ".join(parts)
    return strip_think(str(content or "")).strip()


def synthesize_from_thread(client, thread_id: str, question: str, target_language: str | None, model_name: str, plog: "ProgressLog", depth: str = "standard") -> str:
    """Tool-free report synthesis from a thread's already-gathered research.

    When the research agent exhausts its step budget on tool calls without ever
    writing the report (eager models like MiniMax-M3 keep searching), re-prompting
    the *agent* does not help — it still has tools and keeps calling them. Instead
    we pull the gathered research (tool results + any partial analysis) out of the
    thread's checkpoint history and ask the BARE model — created via
    ``create_chat_model`` with NO tools bound — to write the report. With no tools
    available the model has no choice but to synthesize, reliably producing the
    report from real, already-fetched sources.
    """
    # 1) Pull the most recent checkpoint that has messages.
    try:
        thread = client.get_thread(thread_id)
    except Exception as e:  # noqa: BLE001
        plog.write("warn", f"synthesize: could not load thread ({type(e).__name__}: {e})")
        return ""
    messages: list = []
    for cp in reversed(thread.get("checkpoints") or []):
        vals = cp.get("values") or {}
        if vals.get("messages"):
            messages = vals["messages"]
            break
    if not messages:
        plog.write("warn", "synthesize: no messages found in thread checkpoints")
        return ""

    # 2) Build the gathered-research context (fetched sources + any partial analysis).
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        mtype = m.get("type")
        text = _message_text(m.get("content"))
        if not text:
            continue
        if mtype == "tool":
            name = m.get("name") or "source"
            parts.append(f"[{name}] {text}")
        elif mtype == "ai":
            parts.append(text)  # keep any partial analysis the model already wrote
    context = "\n\n".join(parts).strip()
    if not context:
        plog.write("warn", "synthesize: gathered research context is empty")
        return ""
    _cap = _synthesis_context_cap(model_name)
    if len(context) > _cap:
        context = context[:_cap] + "\n\n[...research context truncated...]"

    # 3) Bare, tool-free model call — it cannot keep researching, so it writes.
    plog.write("stage", f"synthesize: writing report (tool-free) from {len(context)} chars of gathered research")
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        model = create_chat_model(model_name, thinking_enabled=False)
        prompt = (
            build_synthesis_prompt(question, target_language, depth)
            + "\n\n=== GATHERED RESEARCH (base the report ONLY on this; do not invent) ===\n"
            + context
        )
        resp = model.invoke([HumanMessage(content=prompt)])
        text = _message_text(getattr(resp, "content", resp))
        plog.write("stage", f"synthesize: produced {len(text)} chars")
        return text
    except Exception as e:  # noqa: BLE001
        plog.write("warn", f"synthesize: tool-free model call failed ({type(e).__name__}: {e})")
        return ""


def extract_structured_tool_free(report: str, target_language: str | None, model_name: str, depth: str, plog: "ProgressLog") -> str:
    """Tool-free structured extraction from the finished report.

    The agent turn (with tools bound) is unreliable for the JSON extraction: eager
    reasoning models like MiniMax-M3 keep calling ``web_search`` instead of emitting
    the JSON object, so the turn ends with prose/tool-calls that don't parse. Mirroring
    ``synthesize_from_thread``, we call the BARE model (no tools) with the extraction
    prompt + the already-written report, so the model has no choice but to emit JSON.
    Returns the raw model text ('' on failure) for ``extract_json_object`` to parse.
    """
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        model = create_chat_model(model_name, thinking_enabled=False)
        prompt = (
            build_extraction_prompt(target_language, depth)
            + "\n\n=== RESEARCH REPORT (extract the JSON strictly from this; do not search, do not invent) ===\n"
            + report
        )
        resp = model.invoke([HumanMessage(content=prompt)])
        text = _message_text(getattr(resp, "content", resp))
        plog.write("stage", f"extract (tool-free): produced {len(text)} chars")
        return text
    except Exception as e:  # noqa: BLE001
        plog.write("warn", f"extract (tool-free) model call failed ({type(e).__name__}: {e})")
        return ""


def build_extraction_prompt(
    target_language: str | None,
    depth: str = "standard",
    *,
    evidence_grading: bool | None = None,
    forecast_inputs: bool | None = None,
) -> str:
    # EXECPLAN2 I-0-0 / I-0-1 / I-0-5: the SKILL prescribes source tiering (S1-S4),
    # Admiralty-style grading, quantitative number hygiene, and triangulation/conflict
    # tracking — but none of it survived the JSON boundary. We surface it here as
    # OPTIONAL schema fields gated behind env flags (default ON: prompt-only, no tool
    # cost). A model that omits any field degrades to exactly the old contract.
    if evidence_grading is None:
        evidence_grading = _env_flag("RESEARCH_EVIDENCE_GRADING", True)
    if forecast_inputs is None:
        forecast_inputs = _env_flag("RESEARCH_FORECAST_INPUTS", True)

    lang = target_language or "the same language as the research report"
    actor_range = "10-35" if depth == "deep" else "5-20"
    source_hint = (
        "For deep runs, preserve a broad source set: include the most important "
        "primary and high-authority sources across regions, actors, and opposing views."
        if depth == "deep"
        else "Include the most important sources."
    )

    # EXECPLAN2 I-0-0: per-actor / per-relationship optional Admiralty grade line.
    actor_grade = (
        '      "grade": string,                    // OPTIONAL Admiralty grade e.g. "B2" (letter=source reliability A-D, digit=claim credibility 1-4); omit if unsure\n'
        if evidence_grading else ""
    )
    rel_grade = (
        '      "grade": string,                    // OPTIONAL Admiralty grade e.g. "B2"; omit if the basis is single-origin/weak\n'
        if evidence_grading else ""
    )

    # Actor interior (SKILL §8 actors-and-incentives): the research already analyses
    # goals/constraints/assets/vulnerabilities/stated-vs-revealed, but none of it crossed
    # the JSON boundary — it collapsed into one "memory" blob. Surface it as OPTIONAL
    # structured fields so personas become motivational profiles, not stance-label caricatures.
    # Gated behind forecast_inputs (default ON, prompt-only); a model that omits them
    # degrades to exactly the old actors.json shape.
    actor_motive = (
        '      "goals": [ string ],                 // OPTIONAL ranked objectives/incentives driving this actor\n'
        '      "constraints": [ string ],           // OPTIONAL hard limits (capital, power, regulatory, capacity)\n'
        '      "assets": [ string ],                // OPTIONAL capabilities/resources they can deploy\n'
        '      "vulnerabilities": [ string ],       // OPTIONAL exposures / red-lines / weak points\n'
        '      "stated_vs_revealed": string,        // OPTIONAL where the public position diverges from revealed behavior (SKILL §8: the gap is itself evidence)\n'
        if forecast_inputs else ""
    )

    # Richer relationship typing: an OTHER escape-hatch + free-text label (so the research
    # can capture SUPPLIES/FUNDS/OWNS/EMPLOYS/FAMILY_OF etc. beyond the 7-type enum) plus
    # OPTIONAL since/until dates. The OTHER edges are seeded downstream (graph_builder
    # falls back to relation_label → RELATES_TO rather than dropping them).
    rel_since_until = (
        '      "since": string,                     // OPTIONAL YYYY-MM-DD the relationship began / became salient\n'
        '      "until": string,                     // OPTIONAL YYYY-MM-DD it ended (omit if ongoing)\n'
        if forecast_inputs else ""
    )

    # EXECPLAN2 I-0-0: enriched sources schema (tier/date/supports/independent).
    if evidence_grading:
        sources_schema = (
            '  "sources": [\n'
            "    {\n"
            '      "title": string,\n'
            '      "url": string,\n'
            '      "tier": "S1"|"S2"|"S3"|"S4",       // OPTIONAL SKILL §4 source-quality tier (S1=primary/authoritative … S4=reject); omit if unsure\n'
            '      "date": string,                    // OPTIONAL YYYY-MM-DD publication/as-of date of the source\n'
            '      "supports": [ string ],            // OPTIONAL short refs to the claims this source backs\n'
            '      "independent": boolean             // OPTIONAL true if an independent origin, false if it echoes another source\n'
            "    }\n"
            "  ]"
        )
    else:
        sources_schema = '  "sources": [ {"title": string, "url": string} ]'

    # EXECPLAN2 I-0-5: structured quantitative facts (metric + unit + as-of date + definition).
    quant_schema = ""
    if forecast_inputs:
        quant_schema = (
            '  "quantitative_facts": [               // OPTIONAL dated, unit-bearing numbers (SKILL §6 number hygiene)\n'
            "    {\n"
            '      "metric": string,                  // what is measured, e.g. "TSMC 2026 capex guidance"\n'
            '      "value": string,                   // the number as stated, e.g. "52-56"\n'
            '      "unit": string,                    // REQUIRED unit/currency, e.g. "USD billion", "%", "units/yr"\n'
            '      "as_of_date": string,              // REQUIRED YYYY-MM-DD the figure is as-of (NOT the article date if they differ)\n'
            '      "definition": string,              // how the metric is defined (guards against definition drift)\n'
            '      "source": string,                  // short source ref/title for the figure\n'
            '      "tier": "S1"|"S2"|"S3"|"S4"        // OPTIONAL source tier of the figure\n'
            "    }\n"
            "  ],\n"
        )

    # EXECPLAN2 I-0-1: structured contested claims / evidence conflicts.
    contested_schema = ""
    if evidence_grading:
        contested_schema = (
            '  "contested_claims": [                 // OPTIONAL where the EVIDENCE itself conflicts (SKILL §6-§7)\n'
            "    {\n"
            '      "claim": string,                   // the disputed claim/number/thesis\n'
            '      "positions": [                     // the differing stances on it\n'
            "        {\n"
            '          "stance": string,              // what this side holds\n'
            '          "sources": [ string ],         // source refs/titles backing this stance\n'
            '          "tier": "S1"|"S2"|"S3"|"S4"    // OPTIONAL strongest tier behind this stance\n'
            "        }\n"
            "      ],\n"
            '      "status": "confirmed"|"contested"|"speculative"|"single-origin",\n'
            '      "why_they_differ": string          // definition / window / method / incentive driving the disagreement\n'
            "    }\n"
            "  ],\n"
        )

    grading_note = ""
    if evidence_grading:
        grading_note = (
            "EVIDENCE GRADING: where you can, tag sources with their SKILL §4 tier (S1=primary/authoritative, "
            "S2=high-quality secondary, S3=conditional, S4=reject — never cite S4) and load-bearing claims with an "
            "Admiralty grade (letter A-D for source reliability, digit 1-4 for claim credibility, e.g. B2). Base tiers "
            "and grades on the EVIDENCE you actually saw — do NOT invent them; OMIT any tier/grade/date you are unsure of. "
            "CONTESTED_CLAIMS: list only genuine evidence conflicts (two sources disagree on a number, a thesis has a live "
            "bear case, a striking claim is single-origin); each entry must cite >=2 sources across its positions OR carry "
            'status "single-origin". Omit trivial disagreements; an empty array ("contested_claims": []) is fine.\n'
        )
    quant_note = ""
    if forecast_inputs:
        quant_note = (
            "QUANTITATIVE_FACTS: extract the load-bearing numbers from the report, each with its unit and as-of date "
            "(the date the figure refers to, which may differ from the article's publication date) and a one-line definition. "
            'Omit any number you cannot give a unit and as-of date for. An empty array ("quantitative_facts": []) is fine.\n'
        )

    return (
        "Based ONLY on the research you just completed, output a single JSON object "
        "and NOTHING else (no prose, no code fences). It must match this schema:\n\n"
        "{\n"
        '  "central_question": string,            // the prediction question, refined\n'
        '  "as_of_date": string,                  // YYYY-MM-DD, the research cutoff\n'
        '  "situation_brief": {                    // simulation-ready brief of the situation\n'
        '    "current_situation": string,         // 2-4 factual sentences: state of play as of as_of_date\n'
        '    "context": string,                   // how it got here (causal / historical)\n'
        '    "dynamics": string,                  // forces in tension; what is escalating / de-escalating\n'
        '    "fault_lines": [ string ],           // 3-6 issues the actors will argue over\n'
        '    "catalysts": [ string ]              // events/decisions that would shift the situation\n'
        "  },\n"
        f'  "actors": [                             // {actor_range} specific, named real-world actors\n'
        "    {\n"
        '      "name": string,\n'
        '      "type": "Person"|"Organization"|"Media"|"Government"|"Platform"|"Other",\n'
        '      "role": string,                     // their role in the situation\n'
        '      "stance": string,                   // their public position\n'
        '      "influence": "high"|"medium"|"low",\n'
        f"{actor_grade}"
        f"{actor_motive}"
        '      "memory": string                    // what this actor knows/believes\n'
        "    }\n"
        "  ],\n"
        '  "relationships": [                      // directed, typed edges between NAMED actors\n'
        "    {\n"
        '      "source": string,                   // MUST equal an actors[].name\n'
        '      "target": string,                   // MUST equal an actors[].name\n'
        '      "type": "ALLY_OF"|"OPPOSES"|"COMPETES_WITH"|"REGULATES"|"DEPENDS_ON"|"PARTNERS_WITH"|"INFLUENCES"|"OTHER",\n'
        '      "relation_label": string,            // OPTIONAL free-text label when type=="OTHER" (e.g. SUPPLIES, FUNDS, OWNS, EMPLOYS, FAMILY_OF)\n'
        '      "sign": "ally"|"rival"|"neutral",\n'
        '      "strength": "high"|"medium"|"low",\n'
        f"{rel_grade}"
        f"{rel_since_until}"
        '      "basis": string                     // 1-line researched evidence for the edge\n'
        "    }\n"
        "  ],\n"
        '  "key_events": [ {"date": string, "event": string} ],\n'
        '  "hot_topics": [ string ],\n'
        f"{quant_schema}"
        f"{contested_schema}"
        f"{sources_schema}\n"
        "}\n\n"
        f"{source_hint}\n"
        "RELATIONSHIPS: emit edges ONLY between actors named in actors[]; every edge MUST cite a "
        "researched basis. Omit speculative edges. Use OTHER + relation_label only when no listed "
        "type fits; multiple edges between the same pair are allowed when they hold simultaneously "
        "(e.g. REGULATES and DEPENDS_ON). For a single-actor situation use an empty "
        'relationships array ("relationships": []). '
        "ACTORS: when the evidence supports it, populate goals/constraints/assets/vulnerabilities/"
        "stated_vs_revealed from your actors-and-incentives analysis; omit any you did not research, "
        "and do NOT fold them into memory. SITUATION_BRIEF: populate it from your "
        "actors-and-incentives analysis — current_situation and fault_lines are required.\n"
        f"{grading_note}"
        f"{quant_note}"
        f"Write all natural-language string values in {lang}. Output valid JSON only."
    )


# ---------------------------------------------------------------------------
# Robust JSON extraction (the model may wrap JSON in prose/fences despite asking)
# ---------------------------------------------------------------------------


def extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    # 1. Try fenced ```json blocks first.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates: list[str] = []
    if fence:
        candidates.append(fence.group(1))
    # 2. Fall back to the first balanced top-level {...} span. String-aware so
    #    that braces inside quoted JSON string values don't close the span early.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def source_tier_histogram(sources: Any) -> dict[str, int]:
    """Count S1-S4 source tiers for the meta observability block (EXECPLAN2 I-0-0).

    Tolerant of legacy flat sources (no ``tier`` key) and dirty rows: anything that
    is not a recognized tier counts toward ``s_unknown``. Returns
    {s1_count, s2_count, s3_count, s4_count, s_unknown}.
    """
    hist = {"s1_count": 0, "s2_count": 0, "s3_count": 0, "s4_count": 0, "s_unknown": 0}
    if not isinstance(sources, list):
        return hist
    for s in sources:
        tier = ""
        if isinstance(s, dict):
            tier = str(s.get("tier", "") or "").strip().upper()
        if tier in _VALID_TIERS:
            hist[f"{tier.lower()}_count"] += 1
        else:
            hist["s_unknown"] += 1
    return hist


def _clean_optional_rows(value: Any) -> list:
    """Return ``value`` as a list of dict rows, dropping non-dict junk; else []."""
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# LLM-error fallback detection
# ---------------------------------------------------------------------------
# DeerFlow's LLMErrorHandlingMiddleware degrades a failed model call (rate limit,
# quota, connection error, circuit breaker) into a short *user-facing* assistant
# message instead of raising. If that message is the ENTIRE "report", the research
# never actually happened — writing it out as research_report.md would make MiroFish
# build an ontology/graph/simulation from an error string and (worse) report success.
# All such fallbacks begin with this stable prefix (see
# agents/middlewares/llm_error_handling_middleware.py: _build_error_fallback_message).
_LLM_ERROR_SENTINELS = (
    "The configured LLM provider",   # DeerFlow LLMErrorHandlingMiddleware degraded message
    "LLM request failed",            # raw provider error surfaced as the turn's only text
    "unprocessable_entity",          # e.g. MiniMax 422 content-moderation block
    "new_sensitive",                 # MiniMax domestic content filter hit (code 1026)
    "Error code: 4",                 # any 4xx surfaced into the message body
    "Error code: 5",                 # any 5xx surfaced into the message body
)


def looks_like_llm_error(text: str) -> bool:
    """True if *text* is a DeerFlow LLM-error fallback rather than a real report.

    Gated on BOTH a known sentinel substring AND a short length so a genuine
    (always far longer) research report can never be misclassified.
    """
    t = (text or "").strip()
    if not t:
        return True
    return len(t) < 400 and any(s in t for s in _LLM_ERROR_SENTINELS)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DANGLING_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
# DeerFlow's loop-detection middleware appends "[FORCED STOP] …" / "[LOOP
# DETECTED] …" sentences to the assistant text when it intervenes. They are
# harness control-flow notices, not research content — strip them so they
# never pollute the dossier (and downstream graph/personas/report).
_HARNESS_MARKER_RE = re.compile(r"^[ \t]*\[(?:FORCED STOP|LOOP DETECTED)\][^\n]*\n?", re.MULTILINE)


def strip_think(text: str) -> str:
    """Remove inline ``<think>…</think>`` reasoning blocks from model output.

    Reasoning models reachable via the OpenAI-compatible endpoint (notably
    MiniMax-M3) inline their chain-of-thought into ``content`` as ``<think>``
    tags. Strip them so the research report / extraction JSON is clean. Also
    drops a dangling unclosed ``<think>`` (truncated reasoning) to end-of-text.
    """
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    cleaned = _DANGLING_THINK_RE.sub("", cleaned)
    cleaned = _HARNESS_MARKER_RE.sub("", cleaned)
    return cleaned.strip()


_DOC_FENCE_OPEN_RE = re.compile(r"^```[ \t]*(?:markdown|md)?[ \t]*\n", re.IGNORECASE)


def unwrap_markdown_fence(text: str) -> str:
    """Remove a code fence wrapping the ENTIRE report document.

    Models occasionally emit the whole research report inside a single
    ```markdown … ``` fence, which makes every markdown renderer downstream
    show the dossier as one giant code block. Only the outermost wrapper is
    removed, and only when the interior fence count stays balanced — interior
    fenced blocks (e.g. ASCII diagrams) are preserved untouched.
    """
    t = (text or "").strip()
    m = _DOC_FENCE_OPEN_RE.match(t)
    if not m:
        return text
    lines = t.split("\n")
    if lines[-1].strip() != "```":
        return text
    inner = lines[1:-1]
    if sum(1 for ln in inner if ln.lstrip().startswith("```")) % 2 != 0:
        return text
    return "\n".join(inner).strip("\n") + "\n"


# ---------------------------------------------------------------------------
# Streaming run — consume DeerFlowClient.stream, log progress, return final text
# ---------------------------------------------------------------------------


def run_streamed_turn(client, message: str, thread_id: str, recursion_limit: int, plog: ProgressLog, label: str) -> str:
    """Run one agent turn, logging tool activity, returning the final AI text.

    Mirrors ``DeerFlowClient.chat`` (accumulate AI text deltas per id, return the
    last completed message) but also emits progress lines for tool calls/results.
    """
    chunks: dict[str, list[str]] = {}
    last_id = ""
    tool_calls = 0
    plog.write("stage", f"{label}: starting agent turn (recursion_limit={recursion_limit})")

    try:
        for event in client.stream(message, thread_id=thread_id, recursion_limit=recursion_limit):
            etype = event.type
            data = event.data or {}
            if etype == "messages-tuple":
                mtype = data.get("type")
                if mtype == "ai":
                    if data.get("tool_calls"):
                        for tc in data["tool_calls"]:
                            tool_calls += 1
                            plog.write("tool", f"{tc.get('name')}( {_summarize_tool_args(tc.get('args'))} )")
                    delta = data.get("content", "")
                    if delta:
                        msg_id = data.get("id") or ""
                        chunks.setdefault(msg_id, []).append(delta)
                        last_id = msg_id
                elif mtype == "tool":
                    plog.write("result", f"{data.get('name')} → {_truncate(data.get('content', ''))}")
            elif etype == "custom":
                plog.write("custom", _truncate(json.dumps(data, ensure_ascii=False)))
            elif etype == "end":
                usage = data.get("usage", {})
                plog.write("usage", f"tokens in={usage.get('input_tokens')} out={usage.get('output_tokens')} total={usage.get('total_tokens')}")
    except Exception as exc:  # noqa: BLE001 — salvage partial output; never discard accumulated report text
        # LangGraph raises GraphRecursionError when the step budget (recursion_limit)
        # is exhausted; other transient errors can also break the stream mid-turn.
        # Whatever text was accumulated so far is still useful, so we fall through to
        # the existing return instead of letting the exception nuke the whole report.
        kind = "recursion-limit/budget exhausted" if type(exc).__name__ == "GraphRecursionError" else type(exc).__name__
        salvaged_len = len("".join(chunks.get(last_id, ())))
        plog.write("warn", f"{label}: stream ended early ({kind}: {exc}); salvaging {salvaged_len} chars")

    final_text = strip_think("".join(chunks.get(last_id, ())))
    plog.write("stage", f"{label}: turn complete ({tool_calls} tool calls, {len(final_text)} chars)")
    return final_text


# ---------------------------------------------------------------------------
# EXECPLAN2 I-0-4: per-KIQ / per-actor subagent fan-out for the deep protocol.
# After the opening scope pass, dispatch N parallel scoped sub-investigations (one
# per top actor / key question), each on its OWN thread_id (isolated checkpointer
# state), then absorb their merged notes into the MAIN thread so the existing
# contradiction + synthesis passes account for the added breadth. Gated by
# RESEARCH_DEEP_FANOUT (default off) with a RESEARCH_FANOUT_WIDTH cap; when off the
# linear deep protocol runs byte-identically. Best-effort: a dead worker just
# contributes nothing (mirrors run_streamed_turn's salvage-partial pattern).
# ---------------------------------------------------------------------------
_FANOUT_PRIORITY_RE = re.compile(
    r"(actor|stakeholder|player|protagonist|角色|利益相关|参与者|阵营|"
    r"kiq|key question|key intelligence|关键问题|关键信息|核心问题|研究问题)",
    re.I,
)
_FANOUT_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)")
_FANOUT_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _clean_seed(text: str) -> str:
    """Reduce a bullet/bold line to its name/topic head (pure)."""
    s = re.sub(r"^\**\s*", "", (text or "").strip())
    s = _FANOUT_BOLD_RE.sub(r"\1", s)
    for sep in ("：", ":", " — ", " - ", "—", "–", "（", "("):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    return s.strip(" *#`\"'。.")


def extract_kiqs_from_opening(opening_text: str, width: int = 4) -> list[str]:
    """Heuristically parse up to ``width`` salient actor names / key questions from
    the opening pass markdown (pure, deterministic — no LLM). Prefers items under
    actor/stakeholder/KIQ-style sections; falls back to top-level bullets. Returns
    [] when nothing parseable, so the caller skips fan-out cleanly."""
    if not opening_text or width < 1:
        return []
    priority: list[str] = []
    other: list[str] = []
    in_priority = False
    for raw in opening_text.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^\s*#{1,6}\s+(.*\S)", line)
        if heading:
            in_priority = bool(_FANOUT_PRIORITY_RE.search(heading.group(1)))
            continue
        cand = None
        m = _FANOUT_BULLET_RE.match(line)
        if m:
            cand = _clean_seed(m.group(1))
        else:
            b = _FANOUT_BOLD_RE.search(line)
            if b and in_priority:
                cand = _clean_seed(b.group(1))
        if not cand or not (2 <= len(cand) <= 80):
            continue
        (priority if in_priority else other).append(cand)
    seen: set[str] = set()
    out: list[str] = []
    for c in priority + other:
        k = c.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= width:
            break
    return out


def build_scoped_worker_prompt(question: str, kiq: str, target_language: str | None) -> str:
    lang = f" Write your notes in {target_language}." if target_language else ""
    return (
        "You are a scoped sub-investigator within a larger forecasting research effort.\n"
        f"OVERALL QUESTION: {question}\n\n"
        f"YOUR NARROW FOCUS — investigate ONLY this, in depth: {kiq}\n\n"
        "Budget per deep-research tradecraft: ~1/4 scoping the best sources, ~1/2 reading "
        "primary sources in depth on this focus, ~1/4 actively disconfirming. Use your "
        "search/read tools. Return concise, evidence-backed working notes (with source "
        "URLs/titles) on this focus ONLY — do not write the full report; another agent "
        f"synthesizes.{lang}"
    )


def build_fanout_absorption_prompt(question: str, fanout_notes: str, target_language: str | None) -> str:
    cap = 24000
    notes = fanout_notes if len(fanout_notes) <= cap else fanout_notes[:cap] + "\n…(truncated)…"
    lang = f" Respond in {target_language}." if target_language else ""
    return (
        "Several parallel scoped sub-investigations were run on key actors / key questions "
        "for this research. Read and INTERNALIZE their findings below so the upcoming "
        "contradiction-testing and final synthesis account for this breadth. Briefly note "
        "(a few lines) the most important cross-cutting findings and any contradictions they "
        f"surface; do not re-run searches now.{lang}\n\n"
        f"OVERALL QUESTION: {question}\n\n=== PARALLEL SUB-INVESTIGATION NOTES ===\n{notes}"
    )


def run_scoped_worker(client, kiq: str, question: str, parent_thread_id: str, depth: str,
                      target_language: str | None, model_name: str, plog: "ProgressLog", index: int) -> str:
    """Run one scoped sub-investigation on its own isolated thread_id."""
    worker_thread = f"{parent_thread_id}-fanout-{index}-{uuid.uuid4().hex[:6]}"
    return run_streamed_turn(
        client,
        build_scoped_worker_prompt(question, kiq, target_language),
        worker_thread,
        220,
        plog,
        f"research:fanout:{kiq[:24]}",
    )


def run_deep_fanout(client, opening_text: str, question: str, depth: str,
                    target_language: str | None, model_name: str, thread_id: str,
                    plog: "ProgressLog", width: int) -> str:
    """Fan out scoped workers over the opening pass's seed list; merge their notes.

    Returns a single merged markdown block (or '' if no seeds / no notes). Workers
    run concurrently (bounded by ``width``) on isolated thread_ids; a failed worker
    contributes nothing.
    """
    import concurrent.futures as _cf

    seeds = extract_kiqs_from_opening(opening_text, width)
    if not seeds:
        plog.write("warn", "deep fan-out: no KIQ/actor seeds parsed from opening; skipping")
        return ""
    plog.write("stage", f"deep fan-out: {len(seeds)} scoped workers — {', '.join(seeds)}")
    notes: list[str] = []
    with _cf.ThreadPoolExecutor(max_workers=min(width, len(seeds))) as ex:
        futs = {
            ex.submit(run_scoped_worker, client, s, question, thread_id, depth,
                      target_language, model_name, plog, i): s
            for i, s in enumerate(seeds)
        }
        for fut in _cf.as_completed(futs):
            s = futs[fut]
            try:
                txt = fut.result()
                if txt and txt.strip():
                    notes.append(f"## 子调查：{s}\n\n{txt.strip()}")
            except Exception as exc:  # noqa: BLE001 — best-effort per worker
                plog.write("warn", f"deep fan-out worker '{s}' failed: {exc}")
    if not notes:
        return ""
    return "# 并行子调查汇总（per-KIQ/per-actor fan-out）\n\n" + "\n\n---\n\n".join(notes)


def run_research_stage(client, question: str, depth: str, target_language: str | None, model_name: str, thread_id: str, plog: ProgressLog) -> str:
    """Run the research stage.

    Quick/standard remain one DeerFlow turn. Deep is intentionally multi-pass:
    several scoped research turns share the same thread/checkpointer, then a
    tool-free synthesis turn writes the final dossier from all accumulated notes
    and fetched sources.
    """
    preset = DEPTH_PRESETS[depth]
    if depth != "deep":
        return run_streamed_turn(
            client,
            build_research_prompt(question, depth, target_language),
            thread_id,
            preset["recursion_limit"],
            plog,
            "research",
        )

    plog.write("stage", f"deep: starting multi-pass research protocol ({len(DEEP_RESEARCH_PHASES) + 1} research turns + final synthesis)")
    reports: list[str] = []
    opening_limit = int(os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "220"))
    opening = run_streamed_turn(
        client,
        build_research_prompt(question, depth, target_language),
        thread_id,
        opening_limit,
        plog,
        "research:deep-opening",
    )
    if opening.strip():
        reports.append(opening)

    # I-0-4: optional per-KIQ/per-actor fan-out (default off). Runs scoped parallel
    # sub-investigations off the opening's seed list, then absorbs the merged notes
    # into THIS thread so the contradiction + synthesis passes below see the breadth.
    if _env_flag("RESEARCH_DEEP_FANOUT", False) and opening.strip():
        try:
            width = max(1, int(os.environ.get("RESEARCH_FANOUT_WIDTH", "4") or "4"))
            fanout_notes = run_deep_fanout(
                client, opening, question, depth, target_language, model_name, thread_id, plog, width
            )
            if fanout_notes.strip():
                reports.append(fanout_notes)
                run_streamed_turn(
                    client,
                    build_fanout_absorption_prompt(question, fanout_notes, target_language),
                    thread_id,
                    120,
                    plog,
                    "research:deep-fanout-merge",
                )
        except Exception as exc:  # noqa: BLE001 — fan-out is additive; never break the run
            plog.write("warn", f"deep fan-out skipped: {exc}")

    for idx, phase in enumerate(DEEP_RESEARCH_PHASES, start=1):
        limit = int(phase["recursion_limit"])
        phase_text = run_streamed_turn(
            client,
            build_deep_phase_prompt(question, phase, idx, len(DEEP_RESEARCH_PHASES), target_language),
            thread_id,
            limit,
            plog,
            f"research:deep-{idx}-{phase['label']}",
        )
        if phase_text.strip():
            reports.append(phase_text)

    synth = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
    if synth.strip():
        return synth

    plog.write("warn", "deep: tool-free synthesis returned empty text; falling back to concatenated pass notes")
    return "\n\n---\n\n".join(reports)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="DeerFlow deep-research bridge for MiroFish.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt", help="The research / prediction question (inline).")
    src.add_argument("--prompt-file", help="Path to a UTF-8 file containing the question.")
    parser.add_argument("--out-dir", required=True, help="Directory to write the handoff contract into.")
    parser.add_argument("--model", default=os.environ.get("DEERFLOW_RESEARCH_MODEL", "claude"), help="Model name from config.yaml (default: claude).")
    parser.add_argument("--depth", default="standard", choices=list(DEPTH_PRESETS.keys()))
    parser.add_argument("--thread-id", default=None, help="Thread id (default: random).")
    parser.add_argument("--target-language", default=os.environ.get("DEERFLOW_RESEARCH_LANGUAGE") or None, help="Language for the report/JSON (e.g. 'Chinese'). Default: model's choice.")
    parser.add_argument("--no-actors", action="store_true", help="Skip the structured actors.json extraction pass.")
    parser.add_argument("--subagents", action="store_true", help="Enable sub-agent delegation (parallel scoped workers).")
    parser.add_argument("--config", default=None, help="Path to DeerFlow config.yaml (default: repo config resolution).")
    args = parser.parse_args()

    # Resolve the question.
    if args.prompt_file:
        question = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    else:
        question = (args.prompt or "").strip()
    if not question:
        print("ERROR: empty research question", file=sys.stderr)
        return 3

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Always write the requirement immediately so the contract has it even on failure.
    (out_dir / REQUIREMENT_FILENAME).write_text(question + "\n", encoding="utf-8")

    plog = ProgressLog(out_dir / PROGRESS_FILENAME)
    thread_id = args.thread_id or f"research-{uuid.uuid4().hex[:12]}"
    started_at = _utcnow()
    meta: dict[str, Any] = {
        "status": "running",
        "thread_id": thread_id,
        "model": args.model,
        "depth": args.depth,
        "question": question,
        "started_at": started_at,
        "target_language": args.target_language,
    }
    if args.depth == "deep":
        meta["deep_research_phases"] = [
            {"label": "deep-opening", "recursion_limit": int(os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "220"))},
            *[
                {"label": str(phase["label"]), "recursion_limit": int(phase["recursion_limit"])}
                for phase in DEEP_RESEARCH_PHASES
            ],
        ]

    def write_meta() -> None:
        _atomic_write_text(out_dir / META_FILENAME, json.dumps(meta, ensure_ascii=False, indent=2))

    write_meta()

    # Quiet DeerFlow's verbose import-time logging on stderr; keep warnings.
    logging.basicConfig(level=logging.WARNING)

    # --- Provider-key env hygiene (BEFORE the config is loaded) ---
    # DeerFlow's config loader greedily resolves every $VAR in config.yaml; a single
    # unset variable crashes the whole load even when that stanza isn't selected.
    # MiroFish's backend presets empty defaults before spawning this script, but a
    # STANDALONE run (the documented smoke test) doesn't inherit them — preset here
    # too so the default claude path never dies on an unrelated provider's key.
    _PROVIDER_KEY_ENVS = {
        "minimax": "MINIMAX_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "glm": "ZHIPUAI_API_KEY",
        "kimi": "KIMI_API_KEY",
    }
    for _env_name in _PROVIDER_KEY_ENVS.values():
        os.environ.setdefault(_env_name, "")

    def _preflight_fail(msg: str, error: str) -> int:
        meta.update(status="failed", error=error, finished_at=_utcnow())
        write_meta()
        plog.write("error", msg)
        plog.close()
        print(f"ERROR: {msg}", file=sys.stderr)
        return 3

    # --- Selected-model credential preflight (BEFORE client/config construction) ---
    # Models are built lazily on the first stream() call, so a missing key or an
    # absent/stale OAuth token would otherwise surface as an opaque 401/exit-2
    # traceback deep inside Stage 1. Fail fast with an actionable message instead.
    _need_env = _PROVIDER_KEY_ENVS.get(args.model)
    if _need_env and not os.environ.get(_need_env, "").strip():
        return _preflight_fail(
            f"--model {args.model} 需要环境变量 {_need_env}（写入 MiroFish 的 .env 或 export 后重试）。",
            f"missing {_need_env}",
        )
    if args.model == "claude":
        have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        have_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        if not (have_key or have_oauth):
            try:
                from deerflow.models.credential_loader import load_claude_code_credential
                have_oauth = load_claude_code_credential() is not None  # None if missing OR expired
            except Exception:  # loader import/path problems must not crash the preflight
                have_oauth = False
        if not (have_key or have_oauth):
            return _preflight_fail(
                "未找到有效的 Claude 凭据：请设置 $ANTHROPIC_API_KEY，或运行 `claude` 登录以刷新 "
                "~/.claude/.credentials.json（OAuth token 缺失或已过期）。",
                "missing/expired Claude credential",
            )
    elif args.model == "codex":
        try:
            from deerflow.models.credential_loader import load_codex_cli_credential
            have_codex = load_codex_cli_credential() is not None
        except Exception:
            have_codex = os.path.exists(os.path.expanduser(
                os.environ.get("CODEX_AUTH_PATH", "~/.codex/auth.json")))
        if not have_codex:
            return _preflight_fail(
                "未找到有效的 Codex 凭据：运行 `codex` 并用 ChatGPT 账号登录以生成 ~/.codex/auth.json。",
                "missing Codex credential",
            )

    try:
        plog.write("init", f"importing DeerFlow client (model={args.model})")
        from deerflow.client import DeerFlowClient

        client = DeerFlowClient(
            config_path=args.config,
            model_name=args.model,
            thinking_enabled=True,
            subagent_enabled=args.subagents,
        )
        plog.write("init", "client ready; available skills will load on demand (deep-research)")

        # --- Stage 1: research + report ---
        report = run_research_stage(
            client,
            question,
            args.depth,
            args.target_language,
            args.model,
            thread_id,
            plog,
        )

        # SAFETY NET: the primary path is the real agentic research turn above (tools +
        # thinking) writing its own report. But if the agent turn comes back with too
        # little usable text, fall back to a tool-free synthesis FROM the gathered,
        # checkpointed research. This recovers two failure modes:
        #   1. Over-research: an eager model spends the whole step budget on tool calls
        #      and never writes (final AI text is near-empty).
        #   2. Provider STRUCTURAL errors on the final write — e.g. MiniMax 400
        #      "user name must be consistent (2013)" / bad_request, or a connection drop.
        #      The synthesis path issues a CLEAN single-turn call (no 37-message tool
        #      history), which sidesteps the structural rejection and usually succeeds.
        # We skip the net ONLY for a genuine CONTENT-moderation block (422 new_sensitive /
        # unprocessable_entity): re-sending the same gathered content would just be blocked
        # again, so we fail fast instead.
        _stripped = report.strip()
        _is_content_block = bool(_stripped) and any(s in report for s in ("new_sensitive", "unprocessable_entity"))
        if len(_stripped) < SYNTHESIS_TRIGGER_CHARS and not _is_content_block:
            plog.write("warn", f"research turn returned only {len(_stripped)} chars (budget exhausted or a provider error on the final write); synthesizing tool-free from gathered research")
            synth = synthesize_from_thread(client, thread_id, question, args.target_language, args.model, plog, depth=args.depth)
            if len(synth.strip()) > len(_stripped):
                report = synth

        if not report.strip():
            meta.update(status="failed", error="agent produced no report text", finished_at=_utcnow())
            write_meta()
            plog.write("error", "no report text produced")
            plog.close()
            return 2

        # The model call may have failed and been degraded by DeerFlow into a short
        # provider-error message. That is NOT a research report — do not write it,
        # so MiroFish sees "no report produced" and fails fast instead of building a
        # graph from an error string and reporting success.
        if looks_like_llm_error(report):
            meta.update(status="failed", error="LLM provider returned an error fallback, not a research report", finished_at=_utcnow())
            write_meta()
            plog.write("error", f"研究失败：LLM 提供方返回降级错误消息而非研究报告，未写出报告（{_truncate(report, 160)}）")
            plog.close()
            return 2

        report = unwrap_markdown_fence(report)
        _atomic_write_text(out_dir / REPORT_FILENAME, report)
        meta["report_chars"] = len(report)
        plog.write("ok", f"wrote {REPORT_FILENAME} ({len(report)} chars)")
        write_meta()

        # --- Stage 2: structured extraction (best effort) ---
        if not args.no_actors:
            try:
                # PRIMARY: tool-free extraction from the finished report — reliable JSON
                # (eager models like MiniMax-M3 otherwise keep calling web_search during the
                # agent turn and never emit parseable JSON, dropping the whole enriched contract).
                raw = extract_structured_tool_free(report, args.target_language, args.model, args.depth, plog)
                obj = extract_json_object(raw)
                if obj is None:
                    # FALLBACK: the in-thread agent turn (older path) in case the bare call failed.
                    plog.write("warn", "tool-free extraction unparseable; falling back to in-thread agent extraction")
                    raw = run_streamed_turn(
                        client,
                        build_extraction_prompt(args.target_language, args.depth),
                        thread_id,  # same thread → research context preserved via checkpointer
                        80 if args.depth == "deep" else 40,
                        plog,
                        "extract",
                    )
                    obj = extract_json_object(raw)
                if obj is None:
                    plog.write("warn", "could not parse structured JSON; actors.json/sources.json skipped")
                else:
                    sources = obj.pop("sources", None)
                    # actors.json keeps the full object (incl. situation_brief, relationships,
                    # key_events, quantitative_facts, contested_claims) so one dossier read
                    # carries the whole enriched contract.
                    _atomic_write_text(out_dir / ACTORS_FILENAME, json.dumps(obj, ensure_ascii=False, indent=2))
                    meta["actors_count"] = len(obj.get("actors", []) or [])
                    meta["relationships_count"] = len(obj.get("relationships", []) or [])
                    meta["has_situation_brief"] = bool(obj.get("situation_brief"))
                    plog.write("ok", f"wrote {ACTORS_FILENAME} ({meta['actors_count']} actors, {meta['relationships_count']} relationships, brief={meta['has_situation_brief']})")
                    # T1.2: promote key_events to a first-class timeline.json (kept inside
                    # actors.json too for back-compat). Gives downstream a clean valid_at source.
                    key_events = obj.get("key_events")
                    if isinstance(key_events, list) and key_events:
                        _atomic_write_text(out_dir / TIMELINE_FILENAME, json.dumps(key_events, ensure_ascii=False, indent=2))
                        meta["timeline_count"] = len(key_events)
                        plog.write("ok", f"wrote {TIMELINE_FILENAME} ({len(key_events)} events)")
                    # EXECPLAN2 I-0-5: promote quantitative_facts to a first-class
                    # quantitative.json (mirrors the timeline.json pattern; kept inside
                    # actors.json too for back-compat). Degrades silently when absent.
                    quant = _clean_optional_rows(obj.get("quantitative_facts"))
                    if quant:
                        _atomic_write_text(out_dir / QUANTITATIVE_FILENAME, json.dumps(quant, ensure_ascii=False, indent=2))
                        meta["quantitative_count"] = len(quant)
                        plog.write("ok", f"wrote {QUANTITATIVE_FILENAME} ({len(quant)} facts)")
                    # EXECPLAN2 I-0-1: promote contested_claims to a first-class
                    # contested.json so the adversarial work survives the JSON boundary.
                    contested = _clean_optional_rows(obj.get("contested_claims"))
                    if contested:
                        _atomic_write_text(out_dir / CONTESTED_FILENAME, json.dumps(contested, ensure_ascii=False, indent=2))
                        meta["contested_count"] = len(contested)
                        plog.write("ok", f"wrote {CONTESTED_FILENAME} ({len(contested)} contested claims)")
                    if isinstance(sources, list) and sources:
                        _atomic_write_text(out_dir / SOURCES_FILENAME, json.dumps(sources, ensure_ascii=False, indent=2))
                        meta["sources_count"] = len(sources)
                        # EXECPLAN2 I-0-0: tier histogram for observability (and so a
                        # downstream coverage gate can reject S4-heavy dossiers).
                        meta["source_tiers"] = source_tier_histogram(sources)
                        plog.write(
                            "ok",
                            f"wrote {SOURCES_FILENAME} ({len(sources)} sources; tiers={meta['source_tiers']})",
                        )
            except Exception as e:  # extraction must never fail the whole run
                plog.write("warn", f"structured extraction failed (non-fatal): {e}")

        meta.update(status="completed", finished_at=_utcnow())
        write_meta()
        plog.write("done", "research complete")
        plog.close()
        return 0

    except Exception as e:
        meta.update(status="failed", error=str(e), traceback=traceback.format_exc(), finished_at=_utcnow())
        write_meta()
        try:
            plog.write("error", f"{type(e).__name__}: {e}")
            plog.close()
        except Exception:
            pass
        print(f"ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
