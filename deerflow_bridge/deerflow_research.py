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
import traceback
import uuid
from pathlib import Path
from typing import Any

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
    "standard": {"recursion_limit": 320, "guidance": "Research thoroughly from multiple angles (roughly 10-20 searches), fetch the most important sources in full, then STOP searching and write the report. Do not keep searching for marginal extra detail."},
    "deep": {"recursion_limit": 1000, "guidance": "Research exhaustively across every major dimension (you may use up to ~500 tool calls — roughly 25-60 searches plus full-text fetches of key primary sources). Cross-check the important claims across multiple independent sources. You have a very large step budget and a 1M-token context window, so be genuinely thorough and read primary sources in full. Once you have strong, cross-checked coverage of all dimensions, STOP searching and write a comprehensive, well-structured dossier."},
}

# When the research turn ends with too little final text — an eager / over-researching
# model (notably MiniMax-M3) can spend its entire recursion budget on tool calls and hit
# the limit BEFORE it ever writes the report, leaving the final AI message empty — force
# ONE tool-free synthesis turn that writes the report from the already-gathered, thread-
# checkpointed research context. Without this, a perfectly good research pass (dozens of
# real sources fetched) is thrown away as "no report text produced".
SYNTHESIS_TRIGGER_CHARS = 600
# Cap on how much gathered-research context to feed the tool-free synthesis net.
# MiniMax-M3 has a ~1M-token context window, so we can pass a very large slice of
# the gathered sources (~400k chars ≈ ~120k tokens) and still leave ample headroom.
SYNTHESIS_MAX_CONTEXT_CHARS = 400000

REPORT_FILENAME = "research_report.md"
REQUIREMENT_FILENAME = "prediction_requirement.txt"
ACTORS_FILENAME = "actors.json"
SOURCES_FILENAME = "sources.json"
PROGRESS_FILENAME = "research_progress.log"
META_FILENAME = "meta.json"


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

    def write(self, kind: str, message: str) -> None:
        line = f"{_utcnow()} [{kind}] {message}".rstrip()
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
        "(Concrete named entities, NOT abstract themes.)\n"
        "  3. A timeline of key events with dates.\n"
        "  4. The main points of contention, hot topics, and likely flashpoints.\n"
        "  5. Relevant facts, figures, and quotes, each attributable to a source.\n"
        "  6. A short list of the sources you used (titles + URLs).\n\n"
        "IMPORTANT: Once you have gathered enough material across the angles above, you "
        "MUST stop calling tools and write the full report as your very next message. The "
        "written report is the deliverable — do not keep searching for marginal extra "
        "detail. A run that never writes the report has failed."
        f"{lang_line}"
    )


def build_synthesis_prompt(question: str, target_language: str | None) -> str:
    """Prompt for a forced, tool-free 'write the report now' turn.

    Used when the research turn exhausted its step budget before writing the report.
    All gathered research is already in the thread's checkpointed history, so this
    turn must NOT call tools — it just synthesizes the collected material into the
    final report.
    """
    lang_line = f"\n\nWrite the report in {target_language}." if target_language else ""
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
        "what they know/believe.\n"
        "  3. A timeline of key events with dates.\n"
        "  4. The main points of contention, hot topics, and likely flashpoints.\n"
        "  5. Relevant facts, figures, and quotes, each attributable to a source.\n"
        "  6. A short list of the sources you used (titles + URLs).\n"
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


def synthesize_from_thread(client, thread_id: str, question: str, target_language: str | None, model_name: str, plog: "ProgressLog") -> str:
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
    if len(context) > SYNTHESIS_MAX_CONTEXT_CHARS:
        context = context[:SYNTHESIS_MAX_CONTEXT_CHARS] + "\n\n[...research context truncated...]"

    # 3) Bare, tool-free model call — it cannot keep researching, so it writes.
    plog.write("stage", f"synthesize: writing report (tool-free) from {len(context)} chars of gathered research")
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        model = create_chat_model(model_name, thinking_enabled=False)
        prompt = (
            build_synthesis_prompt(question, target_language)
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


def build_extraction_prompt(target_language: str | None) -> str:
    lang = target_language or "the same language as the research report"
    return (
        "Based ONLY on the research you just completed, output a single JSON object "
        "and NOTHING else (no prose, no code fences). It must match this schema:\n\n"
        "{\n"
        '  "central_question": string,            // the prediction question, refined\n'
        '  "as_of_date": string,                  // YYYY-MM-DD, the research cutoff\n'
        '  "actors": [                             // 5-20 specific, named real-world actors\n'
        "    {\n"
        '      "name": string,\n'
        '      "type": "Person"|"Organization"|"Media"|"Government"|"Platform"|"Other",\n'
        '      "role": string,                     // their role in the situation\n'
        '      "stance": string,                   // their public position\n'
        '      "influence": "high"|"medium"|"low",\n'
        '      "memory": string                    // what this actor knows/believes\n'
        "    }\n"
        "  ],\n"
        '  "key_events": [ {"date": string, "event": string} ],\n'
        '  "hot_topics": [ string ],\n'
        '  "sources": [ {"title": string, "url": string} ]\n'
        "}\n\n"
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
    return cleaned.strip()


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

    def write_meta() -> None:
        (out_dir / META_FILENAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    write_meta()

    # Quiet DeerFlow's verbose import-time logging on stderr; keep warnings.
    logging.basicConfig(level=logging.WARNING)

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

        # --- Pre-flight: fail fast on a missing/expired Claude credential ---
        # The model is built lazily on the first stream() call, so an absent or stale
        # OAuth token would otherwise surface as an opaque 401/403 deep inside Stage 1
        # and be reported only as a raw exception. Surface a clear, actionable error up
        # front instead (matches DEERFLOW_INTEGRATION.md §8 risk row + §10 prerequisite).
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
                msg = ("未找到有效的 Claude 凭据：请设置 $ANTHROPIC_API_KEY，或运行 `claude` 登录以刷新 "
                       "~/.claude/.credentials.json（OAuth token 缺失或已过期）。")
                meta.update(status="failed", error="missing/expired Claude credential", finished_at=_utcnow())
                write_meta()
                plog.write("error", msg)
                plog.close()
                print(f"ERROR: {msg}", file=sys.stderr)
                return 3

        # --- Stage 1: research + report ---
        recursion_limit = DEPTH_PRESETS[args.depth]["recursion_limit"]
        report = run_streamed_turn(
            client,
            build_research_prompt(question, args.depth, args.target_language),
            thread_id,
            recursion_limit,
            plog,
            "research",
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
            synth = synthesize_from_thread(client, thread_id, question, args.target_language, args.model, plog)
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

        (out_dir / REPORT_FILENAME).write_text(report, encoding="utf-8")
        meta["report_chars"] = len(report)
        plog.write("ok", f"wrote {REPORT_FILENAME} ({len(report)} chars)")
        write_meta()

        # --- Stage 2: structured extraction (best effort) ---
        if not args.no_actors:
            try:
                raw = run_streamed_turn(
                    client,
                    build_extraction_prompt(args.target_language),
                    thread_id,  # same thread → research context preserved via checkpointer
                    40,
                    plog,
                    "extract",
                )
                obj = extract_json_object(raw)
                if obj is None:
                    plog.write("warn", "could not parse structured JSON; actors.json/sources.json skipped")
                else:
                    sources = obj.pop("sources", None)
                    (out_dir / ACTORS_FILENAME).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
                    meta["actors_count"] = len(obj.get("actors", []) or [])
                    plog.write("ok", f"wrote {ACTORS_FILENAME} ({meta['actors_count']} actors)")
                    if isinstance(sources, list) and sources:
                        (out_dir / SOURCES_FILENAME).write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
                        meta["sources_count"] = len(sources)
                        plog.write("ok", f"wrote {SOURCES_FILENAME} ({len(sources)} sources)")
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
