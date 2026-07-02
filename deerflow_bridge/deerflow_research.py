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
    """Pick the gathered-research context cap by model context-window class.

    RESEARCH-4: an explicit ``SYNTHESIS_MAX_CONTEXT_CHARS`` env override wins over
    the model-class heuristic (so a deployment can dial the synthesis context up or
    down without a code change); gemini / antigravity are added to the large-context
    class (both expose ~1M-token windows like the MiniMax/Qwen/DeepSeek family), and
    ``SYNTHESIS_MAX_CONTEXT_CHARS_LARGE`` can override the large-class default. Unset
    → the original model-class behavior (byte-identical default path).
    """
    try:
        override = int(os.environ.get("SYNTHESIS_MAX_CONTEXT_CHARS", "0") or "0")
    except ValueError:
        override = 0
    if override > 0:
        return override
    name = (model_name or "").lower()
    if any(k in name for k in ("minimax", "qwen", "deepseek", "gemini", "antigravity")):
        try:
            large = int(os.environ.get("SYNTHESIS_MAX_CONTEXT_CHARS_LARGE", "0") or "0")
        except ValueError:
            large = 0
        return large if large > 0 else SYNTHESIS_MAX_CONTEXT_CHARS_LARGE
    return SYNTHESIS_MAX_CONTEXT_CHARS


def _synth_min_context_chars() -> int:
    """RES-1: 无工具合成的最小「已收集上下文」门槛（字符）。

    MiniMax 0-tool-call 桩回合只留下几十字符的线程上下文；把它喂给裸模型「合成」，
    模型只能凭参数记忆编造整份卷宗/报告（'do not invent' 拦不住），伪造内容再通过
    judge / 抽取毒化全管线。低于门槛就拒绝合成、返回空串走既有降级路径。0 = 关闭。
    """
    try:
        return max(0, int(os.environ.get("ACTOR_SYNTH_MIN_CONTEXT_CHARS", "3000") or "3000"))
    except ValueError:
        return 3000

REPORT_FILENAME = "research_report.md"
REQUIREMENT_FILENAME = "prediction_requirement.txt"
# 双轨：Track B（actor-ontology-research）产出的 actor 卷宗。卷宗作为「主」actor
# 来源喂本体生成/抽取，Track A 的 research_report.md 作为「附加上下文」。关闭双轨或
# Track B 失败/空时不落此文件，行为与现状逐字节一致。
ACTOR_DOSSIER_FILENAME = "actor_dossier.md"
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

# ---------------------------------------------------------------------------
# #1 STRUCTURAL SOURCE CAPTURE — collect URLs the agent ACTUALLY FETCHED.
# The model's extracted sources came out URL-less / partly fabricated; grounding
# sources.json in the real web_fetch calls makes provenance structural, not prompt-
# dependent. run_streamed_turn() records every fetched URL here (run-scoped); the
# count also drives the #2 coverage gate. Reset per run in main().
#
# RES-2: 该全局表被双轨 ThreadPoolExecutor、deep fan-out worker 与单回合内的并行
# 工具调用并发读写；旧实现按「最近一个 pending」LIFO 配对结果，Track B 的结果会
# 确认/删除 Track A 的 URL，重复抓取还会吃掉无关 pending 槽位。默认改为 per-turn
# 本地 pending 表（优先按 tool_call_id 精确配对，缺 id 时 FIFO 兜底），回合结束在
# 锁内把确认成功的行合并进全局。RESEARCH_FETCH_ACCOUNTING_V2=false 回退旧行为。
# ---------------------------------------------------------------------------
_FETCHED_SOURCES: list[dict] = []
_FETCHED_LOCK = threading.Lock()

# QUALITY-OPT S10: accumulate research-degradation events (recursion-limit truncation, tool-free
# synthesis fallback, empty synthesis) so the report/forecast can DISCOUNT confidence and flag
# that some numbers may be ungrounded — instead of silently presenting a salvaged/invented draft
# as full-quality research. Folded into meta['research_quality'].degraded.
_RESEARCH_FLAGS: list[str] = []


def _flag_research_degradation(reason: str) -> None:
    if reason and reason not in _RESEARCH_FLAGS:
        _RESEARCH_FLAGS.append(reason)


def _reset_fetched_sources() -> None:
    _FETCHED_SOURCES.clear()
    _RESEARCH_FLAGS.clear()


def _norm_url(u: Any) -> str:
    """Loose URL normalization for dedup/match (strip whitespace + trailing slash)."""
    return str(u or "").strip().rstrip("/")


def _title_from_url(u: str) -> str:
    """Fallback display title = the host, when no model-supplied title exists."""
    try:
        from urllib.parse import urlparse
        return urlparse(u).netloc or u
    except Exception:  # noqa: BLE001
        return u


def _record_fetched_url(tool_name: Any, args: Any) -> None:
    """Capture a web_fetch's URL — the 'actually opened and read a page' signal.

    Best-effort and never raises (a logging-path failure must not break research).
    web_search is intentionally NOT captured: a search returns candidates, not pages
    you read; only fetched pages are citeable per the deep-research skill (§7/§10).
    """
    try:
        if str(tool_name or "").lower() not in ("web_fetch", "fetch", "read_url", "browse", "open_url"):
            return
        url = None
        if isinstance(args, dict):
            url = args.get("url") or args.get("uri") or args.get("link") or args.get("href")
        elif isinstance(args, str):
            url = args
        url = _norm_url(url)
        if url.startswith("http") and not any(_norm_url(s.get("url")) == url for s in _FETCHED_SOURCES):
            # ok=None → pending; confirmed/dropped by _mark_fetch_result when the result arrives.
            _FETCHED_SOURCES.append({"url": url, "ok": None})
    except Exception:  # noqa: BLE001
        pass


# QUALITY-OPT R1: a fetch that returns nothing usable must NOT count as a source (the corpus
# had 74 "No content could be extracted" + ~32 Jina timeouts all counted). These sentinels mark
# a dead fetch; _mark_fetch_result drops it so coverage + grounding reflect REAL reads.
_FETCH_SENTINELS = (
    "no content could be extracted", "request to jina api failed", "jina api returned status",
    "timeout was reached", "assertionfailureerror", "markdown content: undefined",
    "readtimeout", "connecttimeout", "error: request to", "could not be fetched",
)
_FETCH_TOOLS = ("web_fetch", "fetch", "read_url", "browse", "open_url")


def _is_dead_fetch(content: Any) -> bool:
    """A fetch result is dead if it's trivially short or matches a failure sentinel."""
    c = str(content or "").strip()
    if len(c) < 200:
        return True
    cl = c.lower()
    return any(s in cl for s in _FETCH_SENTINELS)


def _mark_fetch_result(tool_name: Any, content: Any) -> None:
    """Confirm or DROP the most-recent pending fetched URL based on its result content (R1)."""
    try:
        if str(tool_name or "").lower() not in _FETCH_TOOLS:
            return
        for s in reversed(_FETCHED_SOURCES):
            if s.get("ok") is None:
                if _is_dead_fetch(content):
                    _FETCHED_SOURCES.remove(s)      # dead fetch → not a real source
                else:
                    s["ok"] = True
                return
    except Exception:  # noqa: BLE001
        pass


# ---- RES-2: per-turn fetch accounting (v2, default on) ---------------------
# 记录/配对都发生在回合本地的 pending 表上：URL 提取规则与旧路径一致，但重复抓取
# 也追加一行（保持 record↔result 对齐），结果优先按 tool_call_id 精确配对——模型
# 一次发出并行工具批时结果按完成序返回，位置式配对必然串行错配。


def _fetch_accounting_v2() -> bool:
    return _env_flag("RESEARCH_FETCH_ACCOUNTING_V2", True)


def _pending_record_fetch(pending: list, tool_name: Any, args: Any, call_id: Any = None) -> None:
    """Append a pending fetch row to a TURN-LOCAL list (never raises)."""
    try:
        if str(tool_name or "").lower() not in _FETCH_TOOLS:
            return
        url = None
        if isinstance(args, dict):
            url = args.get("url") or args.get("uri") or args.get("link") or args.get("href")
        elif isinstance(args, str):
            url = args
        url = _norm_url(url)
        if url.startswith("http"):
            pending.append({"url": url, "call_id": call_id, "ok": None})
    except Exception:  # noqa: BLE001
        pass


def _pending_mark_result(pending: list, tool_name: Any, content: Any, call_id: Any = None) -> None:
    """Resolve a pending row: exact tool_call_id pairing first, FIFO fallback (never raises)."""
    try:
        if str(tool_name or "").lower() not in _FETCH_TOOLS:
            return
        entry = None
        if call_id:
            for s in pending:
                if s.get("ok") is None and s.get("call_id") == call_id:
                    entry = s
                    break
        if entry is None:
            for s in pending:  # FIFO 兜底：流事件缺 tool_call_id 时按发出顺序配对
                if s.get("ok") is None:
                    entry = s
                    break
        if entry is None:
            return
        entry["ok"] = not _is_dead_fetch(content)
    except Exception:  # noqa: BLE001
        pass


# ---- R2: dead-fetch programmatic retry ------------------------------------
# Jina 瞬时超时/空抽取（"timeout was reached" / "no content could be extracted"）会让一次
# 真实可读的页面被当作死抓取整体丢弃（旧语料一轮就丢 ~32 个 Jina 超时）。在丢弃（合并期
# 只并 ok=True 行）之前，对死抓取的 URL 程序化重试一次（~8s 退避 + stdlib 直抓判活）；
# 复活的行标记 ok=True + retried=True 计回真实来源。注意：重试内容不会回灌给模型（模型
# 已看到死结果），这是**来源记账层**的打捞——瞬时故障 ≠ 死页面。重试发生在回合结束、
# 合并之前（不阻塞流式消费），仅 v2 记账路径（RESEARCH_FETCH_ACCOUNTING_V2，默认开）。
# DEERFLOW_FETCH_RETRY = 每个 URL 的重试次数（默认 1；0 = 关闭）。每回合重试的 URL 数
# 有界（_FETCH_RETRY_MAX_URLS），避免病态回合（几十个死抓取）把研究拖住数分钟。

_FETCH_RETRY_BACKOFF_S = 8.0   # 每次重试前的退避秒数（测试打补丁置 0）
_FETCH_RETRY_TIMEOUT_S = 15.0  # 判活直抓的超时
_FETCH_RETRY_MAX_URLS = 6      # 每回合最多重试的死 URL 数（有界阻塞）


def _fetch_retry_count() -> int:
    """DEERFLOW_FETCH_RETRY：每个死 URL 的重试次数（默认 1，0 关闭；非法值回落 1）。"""
    try:
        return max(0, int(os.environ.get("DEERFLOW_FETCH_RETRY", "1") or "1"))
    except ValueError:
        return 1


def _retry_fetch_url(url: str, timeout: float = _FETCH_RETRY_TIMEOUT_S) -> str:
    """stdlib 直抓一次 URL 判活（截读 512KB 足够过 _is_dead_fetch）；失败向上抛（调用方兜底）。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; DeepResearchBridge/1.0)",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(512 * 1024)
    return data.decode("utf-8", errors="replace")


def _retry_dead_fetches(pending: list, plog: "ProgressLog | None" = None) -> None:
    """R2: 回合结束时对本回合的死抓取行（ok=False）逐个重试判活，复活则改记 ok=True。

    每个 URL：退避 ~8s → stdlib 直抓 → _is_dead_fetch 判定；每个重试过的 URL 记一行
    日志（含结局）。绝不抛异常；DEERFLOW_FETCH_RETRY=0 或无死行时为纯 no-op。
    """
    try:
        import time
        retries = _fetch_retry_count()
        if retries <= 0:
            return
        dead = [s for s in pending
                if s.get("ok") is False and str(s.get("url", "")).startswith("http")]
        for s in dead[:_FETCH_RETRY_MAX_URLS]:
            url = str(s["url"])
            revived = False
            for _ in range(retries):
                try:
                    time.sleep(_FETCH_RETRY_BACKOFF_S)
                    content = _retry_fetch_url(url)
                    if not _is_dead_fetch(content):
                        revived = True
                        break
                except Exception:  # noqa: BLE001 — 单 URL 重试失败只影响该行
                    continue
            if revived:
                s["ok"] = True
                s["retried"] = True
            if plog is not None:
                plog.write("retry", f"dead fetch retried: {url} → "
                                    f"{'alive (kept as source)' if revived else 'still dead (dropped)'}")
    except Exception:  # noqa: BLE001 — 打捞层绝不影响主流程
        pass


def _merge_pending_fetches(pending: list) -> None:
    """Merge a turn's CONFIRMED fetches into the run-global table under the lock.

    只合并 ok=True 的行：死抓取与从未收到结果的 pending 不算真实来源；对已存在的
    URL 只强化为 ok=True，绝不删除别的回合确认过的活行（never raises）。
    """
    try:
        with _FETCHED_LOCK:
            for s in pending:
                if s.get("ok") is not True:
                    continue
                url = _norm_url(s.get("url"))
                if not url.startswith("http"):
                    continue
                existing = next((r for r in _FETCHED_SOURCES if _norm_url(r.get("url")) == url), None)
                if existing is None:
                    _FETCHED_SOURCES.append({"url": url, "ok": True})
                else:
                    existing["ok"] = True
    except Exception:  # noqa: BLE001
        pass


# QUALITY-OPT D7: baseline credibility tier from the domain when the model didn't tier a
# fetched source (the corpus had 16/48 untiered + aggregator/SEO domains tiered S1).
_S1_DOMAINS = (".gov", ".mil", "europa.eu", "imf.org", "oecd.org", "iea.org", "worldbank.org",
               "bis.org", "federalreserve.gov", "ecb.europa.eu", "budgetlab.yale.edu",
               "budgetmodel.wharton.upenn.edu", "nber.org", "ustr.gov", "census.gov",
               "bls.gov", "congress.gov", "sec.gov", "uscc.gov", "epoch.ai")
_S2_DOMAINS = ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "economist.com",
               "nytimes.com", "csis.org", "brookings.edu", "carnegieendowment.org",
               "piie.com", "rand.org", "chathamhouse.org", "nature.com", "science.org",
               "apnews.com", "caixinglobal.com", "nikkei.com")
_S4_DOMAINS = ("opentools.ai", "awesomeagents.ai", "how2shout.com", "southfront",
               "msadvisory.com", "atlaspcb.com", "edwardconard.com", "sozai.app")


def _tier_from_domain(url: Any) -> "str | None":
    u = str(url or "").lower()
    if any(d in u for d in _S4_DOMAINS):
        return "S4"
    if any(d in u for d in _S1_DOMAINS):
        return "S1"
    if any(d in u for d in _S2_DOMAINS):
        return "S2"
    return None


def distinct_fetched_count() -> int:
    """Distinct real URLs actually fetched-and-read this run (dead fetches already dropped;
    excludes still-pending entries so the #2 coverage gate counts confirmed reads only).

    RES-2: v2 只数 ok=True（与本 docstring 一致——旧谓词 ``ok is not False`` 恒真，把
    未决 pending 也计入，覆盖门被虚高）。收紧后覆盖门在途中可能触发更多 top-up pass，
    这是有意的成本变化；RESEARCH_FETCH_ACCOUNTING_V2=false 回退旧计数。
    """
    if _fetch_accounting_v2():
        with _FETCHED_LOCK:
            return len({_norm_url(s.get("url")) for s in _FETCHED_SOURCES
                        if _norm_url(s.get("url")).startswith("http") and s.get("ok") is True})
    return len({_norm_url(s.get("url")) for s in _FETCHED_SOURCES
                if _norm_url(s.get("url")).startswith("http") and s.get("ok") is not False})


def merge_fetched_into_sources(extracted: Any) -> "tuple[list[dict], int]":
    """#1: ground sources.json in actually-fetched URLs. Returns (grounded, dropped).

    - Every fetched URL becomes a grounded source (source_origin='fetched'), enriched
      with the model's tier/date/title/independent when the model listed that same URL.
    - Model sources carrying a REAL url NOT in the fetched set are kept as
      source_origin='cited' (referenced a search hit it didn't open).
    - Model sources with NO real url are DROPPED as ungrounded (this is what removed the
      URL-less / fabricated entries); the dropped count is returned for logging.
    """
    ex = [s for s in (extracted or []) if isinstance(s, dict)]
    by_url: dict[str, dict] = {}
    dropped = 0
    for s in ex:
        u = _norm_url(s.get("url"))
        if u.startswith("http"):
            by_url[u] = s
        else:
            dropped += 1
    out: list[dict] = []
    seen: set[str] = set()
    s4_dropped = 0

    def _finalize_tier(row: dict) -> bool:
        """Apply domain tiering when the model gave none; DROP S4 (D5). Returns keep?."""
        t = str(row.get("tier") or "").strip().upper()
        if t not in _VALID_TIERS:
            t = _tier_from_domain(row.get("url")) or ""
            if t:
                row["tier"] = t
        if (row.get("tier") or "").upper() == "S4" or _tier_from_domain(row.get("url")) == "S4":
            return False  # reject-tier source: never cite (SKILL §4 / D5)
        return True

    for f in _FETCHED_SOURCES:                       # grounded backbone (fetched-and-read)
        u = _norm_url(f.get("url"))
        if not u.startswith("http") or u in seen or f.get("ok") is False:
            continue
        seen.add(u)
        m = by_url.get(u, {})
        row: dict[str, Any] = {"url": u, "source_origin": "fetched", "reachable": True,
                               "title": (m.get("title") or _title_from_url(u))}
        for k in ("tier", "date", "independent", "supports", "jurisdiction", "lang"):
            if m.get(k) not in (None, ""):
                row[k] = m[k]
        if not _finalize_tier(row):
            s4_dropped += 1
            seen.discard(u)
            continue
        out.append(row)
    for u, m in by_url.items():                      # cited-but-not-fetched (still real URLs)
        if u in seen:
            continue
        seen.add(u)
        row = dict(m)
        row["url"] = u
        row.setdefault("source_origin", "cited")
        if not row.get("title"):
            row["title"] = _title_from_url(u)
        if not _finalize_tier(row):
            s4_dropped += 1
            seen.discard(u)
            continue
        out.append(row)
    return out, dropped + s4_dropped


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
            "TOOLING: This is WEB research. Work via web_search and web_fetch ONLY. There is "
            "NO local file corpus, dataset, or workspace to inspect — do NOT call ls / "
            "read_file / glob / bash on the filesystem. If any filesystem/workspace tool "
            "returns a permission error, IGNORE it and go straight to web_search. Your "
            "evidence comes entirely from pages you fetch off the web.\n\n"
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


def build_deep_phase_prompt(question: str, phase: dict[str, Any], index: int, total: int, target_language: str | None, prior_gaps: list | None = None) -> str:
    """Prompt one explicit deep-research pass within the same DeerFlow thread.

    R2-RES-9: when the prior passes left an explicit gap list (parsed from their
    "## Gaps to carry into the next pass" sections), thread it in so each pass is
    steered to actually CLOSE the unresolved questions rather than re-cover ground.
    ``prior_gaps`` empty/None → the original prompt (byte-identical default path).
    """
    lang_line = f"\n\nWrite your pass notes in {target_language}." if target_language else ""
    gap_block = ""
    if prior_gaps:
        gap_lines = "\n".join(f"- {str(g)}" for g in list(prior_gaps)[:12])
        gap_block = (
            "UNRESOLVED GAPS carried from earlier passes — prioritize CLOSING these "
            "(search/fetch specifically for them) before broadening:\n"
            f"{gap_lines}\n\n"
        )
    return (
        f"DEEP RESEARCH PASS {index}/{total}: {phase['label']}\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"{gap_block}"
        f"PASS OBJECTIVE:\n{phase['focus']}\n\n"
        "Use web search and full-text fetching as needed. Prefer primary sources and "
        "high-authority sources. Capture concrete numbers, dates, organizations, named "
        "people, URLs/titles, direct source attribution, and unresolved uncertainty. "
        "Cross-check important claims against at least two independent sources where "
        "possible.\n\n"
        # —— ANTI-FIXATION / SEARCH DISCIPLINE (the #1 efficiency failure to avoid) ——
        "SEARCH DISCIPLINE — read before every search:\n"
        "- ONE fact, at most TWO attempts. If a specific fact/quote/document isn't found "
        "in 1–2 searches, it is probably not freely indexed: record it as an open gap and "
        "MOVE ON. Do NOT keep hunting the same item.\n"
        "- NEVER reissue a near-duplicate query. A query that differs from a previous one "
        "ONLY by quotes, reordered OR-terms, an added `site:`/`filetype:`, or a synonym is "
        "a DUPLICATE and is forbidden — it wastes the budget and finds nothing new. If a "
        "result is thin, change the ANGLE (a different actor, mechanism, document type, "
        "language, or time window), not the wording.\n"
        "- BREADTH BEATS A WHITE WHALE. Each search should target a DIFFERENT actor, driver, "
        "relationship, number, or scenario from the brief. Covering 30 entities/drivers once "
        "is far more valuable than 15 reworded attempts at one elusive quote. Spread coverage "
        "across the whole cast and every key dimension; do not over-invest in any single "
        "source or quotation.\n"
        # —— SOURCE GROUNDING (the sources must be REAL and VERIFIABLE) ——
        "SOURCE GROUNDING — non-negotiable:\n"
        "- Only record a source you ACTUALLY FETCHED and read. Capture its REAL URL and its "
        "publication date AS SHOWN ON THE PAGE.\n"
        "- NEVER fabricate a source, URL, title, or date from memory or expectation. A source "
        "with no real fetched URL is NOT a source — drop it. NEVER record a future-dated or "
        "hypothetical document as if it were established/published fact.\n"
        "- Aim for WIDE high-tier coverage: many distinct primary (S1) and high-quality "
        "secondary (S2) sources spread across regions, actors, and opposing views — not a "
        "handful re-cited.\n\n"
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


def build_coverage_topup_prompt(question: str, gaps: list | None, have: int, target: int, target_language: str | None) -> str:
    """#2: a source-broadening top-up pass fired when too few distinct pages were fetched.

    Steers the agent to WIDEN coverage (new actors/drivers/regions) and actually FETCH
    high-tier pages — explicitly NOT to re-hunt anything already attempted.
    """
    lang_line = f"\n\nWrite your pass notes in {target_language}." if target_language else ""
    gap_block = ""
    if gaps:
        gap_block = "Known open gaps (good targets, but do not thrash on any single one):\n" + \
            "\n".join(f"- {str(g)}" for g in list(gaps)[:12]) + "\n\n"
    return (
        "COVERAGE TOP-UP PASS — broaden the evidence base.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"So far only ~{have} distinct sources have been fetched-and-read; the floor for a "
        f"thorough dossier is ~{target}. The evidence base is too narrow.\n\n"
        f"{gap_block}"
        "OBJECTIVE: find and FETCH NEW, high-quality sources that cover parts of the brief "
        "not yet evidenced — DIFFERENT actors, drivers, regions, mechanisms, numbers, and "
        "opposing views. Prioritise primary (S1) and high-authority secondary (S2) sources, "
        "and read them in full.\n\n"
        "HARD RULES:\n"
        "- Each search must open a DIFFERENT part of the problem. Do NOT reissue near-duplicate "
        "queries (same intent, only re-quoted / new site: / reshuffled OR terms) and do NOT "
        "re-hunt any specific fact/quote you already failed to find — log it as a gap and move on.\n"
        "- BREADTH is the goal here: one new fetched source on an under-covered actor/driver is "
        "worth more than another angle on something already covered.\n"
        "- Only record sources you ACTUALLY FETCHED, with their real URL and on-page date. Never "
        "fabricate or future-date a source.\n\n"
        "End with the same Markdown working-note headings as the deep passes "
        "(## Evidence gathered / ## Actor … / ## Quantitative facts … / ## Contradictions … / "
        "## Gaps to carry into the next pass). Do NOT write the final report yet."
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
        # —— QUALITY-OPT D1: INSIGHT CONTRACT — what turns a competent summary into a sharp,
        # defensible forecast (the brief's 'sharp human POV, not generic LLM output' test). These
        # are HARD requirements, not suggestions; convert the search tradecraft into output.
        "INSIGHT CONTRACT (mandatory — this is what separates a sharp forecast from generic slop):\n"
        "  • ONE load-bearing THESIS sentence the whole dossier defends — a specific, falsifiable "
        "claim, not a hedge. State it up front and return to it.\n"
        "  • For each major forecast: a reference-class BASE RATE (outside view) + the historical "
        "analogue it rests on and how that case resolved, THEN your case-specific adjustment.\n"
        "  • 3–5 explicit CAUSE→EFFECT chains with their SECOND-ORDER effects (X raises Y, which "
        "forces Z) — mechanisms, not vibes.\n"
        "  • A 'NON-OBVIOUS / CONTRARIAN' subsection: name what the consensus or a generic analysis "
        "gets wrong here, and why.\n"
        "  • A THESIS STRESS-TEST: the single strongest piece of DISCONFIRMING evidence and what "
        "would have to be true for the thesis to break.\n\n"
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
    # RES-1: 上下文低于门槛时拒绝无工具合成——裸模型只会凭记忆编造报告。返回空串：
    # deep 路径降级为拼接 pass notes，safety-net 路径保留原 report 交由最短长度门处理。
    _min_ctx = _synth_min_context_chars()
    if _min_ctx and len(context) < _min_ctx:
        plog.write("warn", f"synthesize: gathered context only {len(context)} chars (< {_min_ctx}); refusing tool-free synthesis (would fabricate)")
        _flag_research_degradation("synthesize: near-empty research context; refused tool-free fabrication")
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

        # RESEARCH-5: a master "light extraction" switch drops the heaviest OPTIONAL
        # schema blocks (evidence grading + forecast-input DNA) for latency/speed runs,
        # and the report fed to the extractor is capped to a configurable excerpt so a
        # huge dossier doesn't blow the extraction context. Both default to no-op:
        # light off → full schema; excerpt cap 0 → the whole report (byte-identical).
        light = _env_flag("RESEARCH_LIGHT_EXTRACTION", False)
        model = create_chat_model(model_name, thinking_enabled=False)
        prompt = (
            build_extraction_prompt(
                target_language, depth,
                evidence_grading=(False if light else None),
                forecast_inputs=(False if light else None),
            )
            + "\n\n=== RESEARCH REPORT (extract the JSON strictly from this; do not search, do not invent) ===\n"
            + _extraction_report_excerpt(report)
        )
        resp = model.invoke([HumanMessage(content=prompt)])
        text = _message_text(getattr(resp, "content", resp))
        plog.write("stage", f"extract (tool-free): produced {len(text)} chars")
        return text
    except Exception as e:  # noqa: BLE001
        plog.write("warn", f"extract (tool-free) model call failed ({type(e).__name__}: {e})")
        return ""


def _extraction_report_excerpt(report: str) -> str:
    """RESEARCH-5: cap the report text fed to the extractor to a configurable excerpt.

    ``EXTRACTION_REPORT_EXCERPT_CHARS`` unset/0 → the full report (default, no-op).
    A speed run can set it (e.g. 60000) to bound the extraction context; the dossier
    (the primary extraction input when present) is intentionally NOT capped here.
    """
    try:
        cap = int(os.environ.get("EXTRACTION_REPORT_EXCERPT_CHARS", "0") or "0")
    except ValueError:
        cap = 0
    if cap > 0 and len(report or "") > cap:
        return report[:cap] + "\n\n[...report excerpt truncated for extraction...]"
    return report


def build_extraction_prompt(
    target_language: str | None,
    depth: str = "standard",
    *,
    evidence_grading: bool | None = None,
    forecast_inputs: bool | None = None,
    source_diversity: bool | None = None,
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
    # R2-RES-12: ask for per-source jurisdiction + language so the bridge can compute a
    # source-diversity histogram and warn on a single-region monoculture. Prompt-only,
    # OPTIONAL fields; a model that omits them degrades to exactly the old sources shape.
    if source_diversity is None:
        source_diversity = _env_flag("RESEARCH_SOURCE_DIVERSITY", True)

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
        '      "description": string,               // OPTIONAL ONE sentence pinning who/what this is (disambiguating IDENTITY, e.g. "TSMC, the Taiwanese contract chip foundry"), grounded in the research; powers KG entity resolution\n'
        '      "aliases": [ string ],               // OPTIONAL other names this entity is known by (synonyms, abbreviations, foreign-language forms) — for zero-overlap alias resolution\n'
        '      "goals": [ string ],                 // OPTIONAL ranked objectives/incentives driving this actor\n'
        '      "constraints": [ string ],           // OPTIONAL hard limits (capital, power, regulatory, capacity)\n'
        '      "assets": [ string ],                // OPTIONAL capabilities/resources they can deploy\n'
        '      "vulnerabilities": [ string ],       // OPTIONAL exposures / red-lines / weak points\n'
        '      "stated_vs_revealed": string,        // OPTIONAL where the public position diverges from revealed behavior (SKILL §8: the gap is itself evidence)\n'
        if forecast_inputs else ""
    )

    # ONTOLOGY: entity classification + behavioral DNA (GEMINI_PRO_ONTOLOGY §2 tiering /
    # §3 behavioral DNA, CLAUDE_ONTOLOGY §2-§3). Lets the synthesis pass distinguish a
    # decision-making actor from a SOURCE it merely cites or an abstract concept, and
    # equip the real movers with values/beliefs/incentives/resources so personas become
    # motivational profiles rather than stance-label caricatures. All OPTIONAL and gated
    # behind forecast_inputs (default ON, prompt-only) so a model that omits them degrades
    # to exactly the old actors.json shape.
    actor_archetype = (
        '      "archetype": string,                 // OPTIONAL one of actor|collective|institution_rule|asset_object|event|signal|claim_narrative|constraint_resource|place_jurisdiction|source|scenario; omit to default "actor"\n'
        '      "simulation_tier": 1|2|3|4,          // OPTIONAL 1=core decision-maker, 2=stakeholder/faction, 3=passive info/source (e.g. a reporter/outlet that itself moves the outcome), 4=abstract concept/resource; omit to let it be inferred\n'
        '      "role_class": "principal"|"arbiter"|"stakeholder"|"amplifier"|"intermediary",  // OPTIONAL functional role in the contest\n'
        # XRUN-13: the SKILL's §2.3 salience triage (tier + one-line basis) never crossed the
        # JSON boundary — the downstream coverage metric salience_basis_present was permanently
        # 0.0 and agent-cap ranking never saw an explicit salience. Shape matches what
        # backend actors.py salience_score()/dossier_coverage() already consume.
        '      "salience": {                        // OPTIONAL the §2.3 salience triage result — why this actor makes the cast\n'
        '        "tier": "high"|"medium"|"low",     //   decision-power/stake tier (NOT coverage volume)\n'
        '        "score": number,                   //   OPTIONAL numeric salience in [0,1]\n'
        '        "basis": string                    //   1-line researched basis for the tier\n'
        "      },\n"
        if forecast_inputs else ""
    )
    actor_dna = (
        '      "worldview": {                       // OPTIONAL behavioral DNA — values/beliefs/identity that drive this actor (profile tier 1-2 actors here)\n'
        '        "values": [ string ],              //   what they hold sacred / optimize for\n'
        '        "beliefs": [ string ],             //   how they read the world (e.g. "AI regulation stifles innovation")\n'
        '        "identity": string,                //   how they see themselves\n'
        '        "frame": string                    //   the lens through which they interpret events\n'
        "      },\n"
        '      "incentives": [                      // OPTIONAL ranked drivers with their win/lose conditions\n'
        "        {\n"
        '          "driver": string,                //   what they are maximizing/minimizing (e.g. "shareholder value", "re-election")\n'
        '          "gains_if": string,              //   the outcome that rewards them\n'
        '          "loses_if": string,              //   the outcome that costs them\n'
        '          "intensity": "high"|"medium"|"low"\n'
        "        }\n"
        "      ],\n"
        '      "resources": [ string ],             // OPTIONAL levers/capabilities they can deploy (superset of assets)\n'
        '      "risk_tolerance": "low"|"medium"|"high",  // OPTIONAL appetite for risky moves\n'
        if forecast_inputs else ""
    )

    # ONTOLOGY: valenced/directional relations (CLAUDE_ONTOLOGY §4, contract relations[]).
    # OPTIONAL valence/polarity let downstream tell rivals from partners from buyers; both
    # are gated behind forecast_inputs and degrade-safe (absent => derived from type).
    rel_valence = (
        '      "valence": "allied"|"adversarial"|"neutral"|"transactional"|"directional",  // OPTIONAL relationship colour; omit to derive from type\n'
        '      "polarity": number,                  // OPTIONAL signed strength in [-1,1] (+ cooperative, - antagonistic); omit to derive from valence/type\n'
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

    # R2-RES-12: optional jurisdiction + language lines (appended into either schema).
    diversity_lines = (
        '      "jurisdiction": string,            // OPTIONAL country/region of the source (e.g. "US", "China", "EU", "Taiwan"); powers the source-diversity histogram\n'
        '      "lang": string,                    // OPTIONAL primary language of the source (e.g. "en", "zh")\n'
        if source_diversity else ""
    )

    # EXECPLAN2 I-0-0: enriched sources schema (tier/date/supports/independent).
    if evidence_grading:
        sources_schema = (
            "  // SOURCES MUST BE REAL: include ONLY sources that were ACTUALLY FETCHED during research, each\n"
            "  // with its true URL and the date shown on the page. Do NOT invent sources, URLs, titles, or dates\n"
            "  // from memory; do NOT include future-dated or hypothetical documents. A source with no real fetched\n"
            "  // URL must be OMITTED. Prefer many distinct S1/S2 sources across regions and actors over a few re-cited.\n"
            '  "sources": [\n'
            "    {\n"
            '      "title": string,\n'
            '      "url": string,                     // REQUIRED real URL you actually fetched; OMIT the whole source if you have no real fetched URL — never fabricate one\n'
            '      "tier": "S1"|"S2"|"S3"|"S4",       // OPTIONAL SKILL §4 source-quality tier (S1=primary/authoritative … S4=reject); omit if unsure\n'
            '      "date": string,                    // OPTIONAL YYYY-MM-DD publication/as-of date AS SHOWN ON THE PAGE (not guessed)\n'
            '      "supports": [ string ],            // OPTIONAL short refs to the claims this source backs\n'
            '      "independent": boolean,            // OPTIONAL true if an independent origin, false if it echoes another source\n'
            f"{diversity_lines}"
            "    }\n"
            "  ]"
        )
    elif source_diversity:
        sources_schema = (
            '  "sources": [\n'
            "    {\n"
            '      "title": string,\n'
            '      "url": string,\n'
            f"{diversity_lines}"
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

    # ONTOLOGY inclusion/tiering rule (CLAUDE_ONTOLOGY §2 / GEMINI_PRO_ONTOLOGY §6.1):
    # keep journalists/outlets/pollsters/analysts you merely CITE out of actors[] (they
    # are SOURCES) unless they themselves move the outcome; classify concepts as tier 4.
    tiering_note = ""
    if forecast_inputs:
        tiering_note = (
            "ENTITY TIERING: a news outlet, reporter, pollster, or analyst that you only CITE is a SOURCE — put it in "
            "sources[], NOT actors[]. Include such an entity as an actor ONLY if it itself moves the outcome, and then "
            'set simulation_tier=3. Set simulation_tier=4 for abstract concepts/resources; 2 for stakeholders/factions; '
            "1 for core decision-makers. Profile tier 1-2 actors deeply — populate worldview (values/beliefs), incentives "
            "(driver/gains_if/loses_if), resources, and risk_tolerance from your actors-and-incentives analysis; leave them "
            "thin for tier 3-4. SALIENCE: record each actor's salience as {tier, basis} (optionally a score in [0,1]) "
            "from your salience triage — the basis must cite decision power/stake over the outcome, not coverage volume. "
            "RELATIONSHIP VALENCE: when you can, tag each edge with its valence (allied/adversarial/"
            "neutral/transactional/directional) and a polarity in [-1,1] so rivals are not confused with partners or "
            "buyers; omit either if unsure (it is derived from type). Prefer a specific relationship type (SUPPLIES, "
            "CUSTOMER_OF, FUNDS, INVESTS_IN, BACKS, OWNS, SUPPORTS, SANCTIONS, REPORTS_ON, CONSUMES, ENDORSES, CRITICIZES, "
            "LITIGATES_AGAINST) over OTHER when one fits. CAUSAL/MECHANISM EDGES (high value for forecasting): when the "
            "research establishes a transmission mechanism, emit it as CAUSES / ENABLES / CONSTRAINS / TRIGGERS / "
            "ACCELERATES (with sign/strength/basis in the edge's fields) — these are how a shock propagates to the outcome, "
            "not just who-knows-whom. Omit any of these fields you did not research; a model that omits "
            "all of them still produces a valid actors.json.\n"
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
        f"{actor_archetype}"
        f"{actor_motive}"
        f"{actor_dna}"
        '      "memory": string                    // what this actor knows/believes\n'
        "    }\n"
        "  ],\n"
        '  "relationships": [                      // directed, typed edges between NAMED actors\n'
        "    {\n"
        '      "source": string,                   // MUST equal an actors[].name\n'
        '      "target": string,                   // MUST equal an actors[].name\n'
        '      "type": "ALLY_OF"|"OPPOSES"|"COMPETES_WITH"|"REGULATES"|"DEPENDS_ON"|"PARTNERS_WITH"|"INFLUENCES"|"SUPPLIES"|"CUSTOMER_OF"|"FUNDS"|"INVESTS_IN"|"BACKS"|"OWNS"|"SUPPORTS"|"SANCTIONS"|"REPORTS_ON"|"CONSUMES"|"ENDORSES"|"CRITICIZES"|"LITIGATES_AGAINST"|"CAUSES"|"ENABLES"|"CONSTRAINS"|"TRIGGERS"|"ACCELERATES"|"OTHER",\n'
        '      "relation_label": string,            // OPTIONAL free-text label when type=="OTHER" (e.g. EMPLOYS, FAMILY_OF) — prefer a listed type when one fits\n'
        '      "sign": "ally"|"rival"|"neutral",\n'
        '      "strength": "high"|"medium"|"low",\n'
        f"{rel_grade}"
        f"{rel_valence}"
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
        "ACTORS: include ONLY actors CENTRAL to the central_question — those whose decisions, "
        "incentives, or capabilities materially move the outcome; exclude entities mentioned only in "
        "passing. Give each a one-sentence description (disambiguating identity) and known aliases. "
        "When the evidence supports it, populate goals/constraints/assets/vulnerabilities/"
        "stated_vs_revealed from your actors-and-incentives analysis; omit any you did not research, "
        "and do NOT fold them into memory. SITUATION_BRIEF: populate it from your "
        "actors-and-incentives analysis — current_situation and fault_lines are required.\n"
        f"{grading_note}"
        f"{quant_note}"
        f"{tiering_note}"
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
# Evidence-quality analytics (R2-RES-1/4/6/10/11/12) — pure, deterministic
# post-processing over the extracted obj/sources. No LLM, no I/O; each is wrapped
# by a feature flag at the call site and degrades to a no-op when data is absent.
# ---------------------------------------------------------------------------

_DATE_FULL_RE = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_YM_RE = re.compile(r"\s*(\d{4})-(\d{1,2})(?!\d)")
_DATE_Y_RE = re.compile(r"\s*(\d{4})(?!\d)")


def _parse_date(s: Any) -> "_dt.date | None":
    """Parse a YYYY-MM-DD / YYYY-MM / YYYY prefix to a date (else None). Stdlib only."""
    if not s:
        return None
    text = str(s)
    m = _DATE_FULL_RE.match(text)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_YM_RE.match(text)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    m = _DATE_Y_RE.match(text)
    if m:
        try:
            return _dt.date(int(m.group(1)), 1, 1)
        except ValueError:
            return None
    return None


def _first_number(s: Any) -> "float | None":
    """First signed/grouped numeric token in *s* as a float (e.g. '52-56'→52.0); None."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _norm_text(s: Any) -> str:
    """Lowercase, strip punctuation (keep % and word chars), collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", str(s or "").lower())).strip()


# ---- R2-RES-12: source-diversity histogram --------------------------------

def source_diversity_histogram(sources: Any) -> dict:
    """Jurisdiction/language histogram + single-region monoculture warning.

    Flags ``single_region_warning`` when >70% of jurisdiction-tagged sources share one
    jurisdiction (and there are >=3 such sources), so a downstream gate / the run log can
    surface a regional blind spot. Tolerant of legacy sources with no jurisdiction/lang.
    """
    out = {
        "by_jurisdiction": {}, "by_language": {},
        "n_total": 0, "n_with_jurisdiction": 0,
        "dominant_jurisdiction": None, "dominant_share": 0.0,
        "single_region_warning": False,
    }
    if not isinstance(sources, list):
        return out
    juris: dict[str, int] = {}
    langs: dict[str, int] = {}
    for s in sources:
        if not isinstance(s, dict):
            continue
        out["n_total"] += 1
        j = str(s.get("jurisdiction", "") or "").strip()
        if j:
            juris[j.lower()] = juris.get(j.lower(), 0) + 1
            out["n_with_jurisdiction"] += 1
        lang = str(s.get("lang", "") or s.get("language", "") or "").strip()
        if lang:
            langs[lang.lower()] = langs.get(lang.lower(), 0) + 1
    out["by_jurisdiction"] = juris
    out["by_language"] = langs
    if juris:
        dom, dom_n = max(juris.items(), key=lambda kv: kv[1])
        total = sum(juris.values())
        share = dom_n / total if total else 0.0
        out["dominant_jurisdiction"] = dom
        out["dominant_share"] = round(share, 3)
        out["single_region_warning"] = bool(share > 0.7 and total >= 3)
    return out


# ---- R2-RES-1: research-quality scorecard ---------------------------------

_TIER_WEIGHT = {"S1": 1.0, "S2": 0.7, "S3": 0.4, "S4": 0.0}


def _source_tier_mix(sources: Any) -> "float | None":
    """0-1 weighted source-quality mix (S1 best); untagged → 0.3. None if no sources."""
    if not isinstance(sources, list):
        return None
    vals: list[float] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        t = str(s.get("tier", "") or "").strip().upper()
        vals.append(_TIER_WEIGHT.get(t, 0.3))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def _dossier_richness(obj: Any) -> "float | None":
    """0-1 structural richness of the extracted dossier (incentives/worldview/valence)."""
    if not isinstance(obj, dict):
        return None
    actors = obj.get("actors")
    if not isinstance(actors, list) or not actors:
        return None
    n = 0
    with_inc = 0
    tier12 = 0
    tier12_world = 0
    for a in actors:
        if not isinstance(a, dict):
            continue
        n += 1
        if isinstance(a.get("incentives"), list) and a.get("incentives"):
            with_inc += 1
        infl = str(a.get("influence", "")).strip().lower()
        tier = a.get("simulation_tier")
        is_tier12 = infl in ("high", "medium") or tier in (1, 2, "1", "2")
        if is_tier12:
            tier12 += 1
            wv = a.get("worldview")
            if isinstance(wv, dict) and any(wv.get(k) for k in ("values", "beliefs", "identity", "frame")):
                tier12_world += 1
    if n == 0:
        return None
    rels = obj.get("relationships")
    n_rel = 0
    valenced = 0
    if isinstance(rels, list):
        for r in rels:
            if not isinstance(r, dict):
                continue
            n_rel += 1
            if str(r.get("valence", "")).strip():
                valenced += 1
    pct_inc = with_inc / n
    pct_world = (tier12_world / tier12) if tier12 else 0.0
    pct_val = (valenced / n_rel) if n_rel else 0.0
    edges_per_actor = min(1.0, (n_rel / n) / 2.0)  # ~2 edges/actor saturates the signal
    parts = [pct_inc, pct_world, pct_val, edges_per_actor]
    return round(sum(parts) / len(parts), 3)


def _judge_mean01(scorecard: Any) -> "float | None":
    """0-1 mean of an AI-judge scorecard's 0-5 scores (None if unparseable)."""
    if not isinstance(scorecard, dict):
        return None
    scores = scorecard.get("scores")
    if not isinstance(scores, dict) or not scores:
        return None
    vals: list[float] = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            pass
    if not vals:
        return None
    return round((sum(vals) / len(vals)) / 5.0, 3)


def compute_research_quality(sources: Any, actors_obj: Any, judge_scorecard: Any = None,
                             grounding: "float | None" = None, quant_penalty: float = 0.0) -> dict:
    """R2-RES-1: a 0-1 research-quality score from source tiers, dossier richness, and
    (optional) the AI-judge mean. Components are weighted over only those that could be
    computed, so a sparse run still yields a defined score from what evidence exists.

    RES-4: 原三个分量（模型自评 tier / actors.json 结构形状 / 无法核实 grounding 的 judge）
    全都可被「编造」满足——伪造 run 得 0.889 反超真实 run。新增第四个 ``grounding`` 分量
    （真实抓取数 × 合成降级折扣，调用方计算）与封顶 ``quant_penalty`` 扣减；二者默认
    None/0.0 时逐字节等于旧评分（degrade-safe）。
    """
    comps = {
        "source_tier_mix": _source_tier_mix(sources),
        "dossier_richness": _dossier_richness(actors_obj),
        "judge_mean": _judge_mean01(judge_scorecard),
        "grounding": grounding,
    }
    weights = {"source_tier_mix": 0.35, "dossier_richness": 0.40, "judge_mean": 0.25, "grounding": 0.25}
    num = 0.0
    den = 0.0
    for k, w in weights.items():
        v = comps[k]
        if v is None:
            continue
        num += w * v
        den += w
    score = round(max(0.0, num / den - min(0.15, max(0.0, quant_penalty))), 3) if den > 0 else None
    out = {"score": score, "components": comps}
    if quant_penalty:
        out["quant_penalty"] = round(min(0.15, max(0.0, quant_penalty)), 3)
    return out


# ---- R2-RES-6: deterministic completeness probe ---------------------------

_NAMED_ENTITY_RE = re.compile(r"\b([A-Z][\w.&'-]+(?:\s+(?:of|the|for|and|&)?\s*[A-Z][\w.&'-]+)*)\b")
_ENTITY_STOPWORDS = {"the", "a", "an", "this", "that", "will", "who", "what", "when", "how", "by"}


def _named_entities(text: str) -> list[str]:
    """Capitalized multi/-single-word entity candidates from English-ish text (best effort)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _NAMED_ENTITY_RE.finditer(str(text or "")):
        ent = m.group(1).strip()
        if ent.lower() in _ENTITY_STOPWORDS or len(ent) < 3:
            continue
        if ent.lower() in seen:
            continue
        seen.add(ent.lower())
        out.append(ent)
    return out


# RES-11: 问题里的大写「概念」（Modern Mercantilism / Artificial Intelligence …）按 SKILL
# 属 tier-4 上下文对象，本就不该出现在 cast 里；把它们报成 missing_named_entities 会训练
# 运维忽略 coverage_gaps。词头（末 token）命中此表且未在 cast 中匹配到时不再报警。
_CONCEPT_HEAD_STOPWORDS = frozenset({
    "intelligence", "mercantilism", "economy", "economics", "policy", "policies",
    "technology", "technologies", "trade", "war", "growth", "inflation", "recession",
    "globalization", "globalisation", "tariff", "tariffs", "order", "system", "strategy",
    "decoupling", "protectionism", "nationalism", "capitalism", "socialism", "democracy",
    "security", "resilience", "transition", "energy", "supply", "chain", "chains",
    "ai", "automation", "geopolitics", "diplomacy", "sanctions", "hegemony",
})


def _is_abstract_concept(entity: str) -> bool:
    """RES-11: heuristic — the entity's head noun marks it as an abstract concept."""
    toks = _norm_text(entity).split()
    return bool(toks) and toks[-1] in _CONCEPT_HEAD_STOPWORDS


def compute_coverage_gaps(obj: Any) -> dict:
    """R2-RES-6: verify central_question / fault_lines named entities map to the cast.

    Emits ``missing_named_entities`` (entities named in the question that appear in no
    actor name/alias) and ``orphan_fault_lines`` (fault lines whose named entities are
    none of them in the cast) so a thin/blind dossier is diagnosable. Deterministic;
    Chinese-only text simply yields fewer entity hits (degrades to empty, not noise).
    """
    out: dict[str, list] = {"missing_named_entities": [], "orphan_fault_lines": []}
    if not isinstance(obj, dict):
        return out
    actor_names: list[str] = []
    for a in (obj.get("actors") or []):
        if not isinstance(a, dict):
            continue
        if a.get("name"):
            actor_names.append(str(a["name"]))
        for al in (a.get("aliases") or []):
            if al:
                actor_names.append(str(al))
        if a.get("role"):
            actor_names.append(str(a["role"]))
    hay = _norm_text(" \n ".join(actor_names))

    def _grounded(entity: str) -> bool:
        e = _norm_text(entity)
        if not e:
            return True
        # grounded if the entity (or any of its >3-char tokens) appears in the cast text
        if e in hay:
            return True
        return any(tok in hay for tok in e.split() if len(tok) > 3)

    cq = str(obj.get("central_question", "") or "")
    for ent in _named_entities(cq)[:40]:
        if not _grounded(ent) and not _is_abstract_concept(ent):
            out["missing_named_entities"].append(ent)
            if len(out["missing_named_entities"]) >= 20:
                break

    brief = obj.get("situation_brief")
    fault_lines = brief.get("fault_lines") if isinstance(brief, dict) else None
    for fl in (fault_lines or []):
        ents = _named_entities(str(fl))
        if ents and not any(_grounded(e) for e in ents):
            out["orphan_fault_lines"].append(str(fl))
            if len(out["orphan_fault_lines"]) >= 20:
                break
    return out


# ---- R2-RES-10: triangulation / single-origin audit -----------------------

def audit_triangulation(sources: Any, contested_claims: Any) -> list:
    """R2-RES-10: flag load-bearing claims resting on a single origin.

    Two signals: (1) contested_claims explicitly tagged ``status == "single-origin"``;
    (2) a claim referenced via sources[].supports backed by <=1 INDEPENDENT source —
    only evaluated when at least one source carries an explicit ``independent`` flag,
    so an untagged corpus never floods this list.
    """
    flagged: list[dict] = []
    seen: set[str] = set()
    for c in (contested_claims or []):
        if isinstance(c, dict) and str(c.get("status", "")).strip().lower() == "single-origin":
            claim = str(c.get("claim", "")).strip()
            if claim and claim.lower() not in seen:
                seen.add(claim.lower())
                flagged.append({"claim": claim, "reason": "status=single-origin", "independent_sources": 0})

    has_independence_signal = False
    support: dict[str, dict] = {}
    if isinstance(sources, list):
        for s in sources:
            if not isinstance(s, dict):
                continue
            indep = s.get("independent")
            if isinstance(indep, bool):
                has_independence_signal = True
            for ref in (s.get("supports") or []):
                ref = str(ref).strip()
                if not ref:
                    continue
                key = ref.lower()
                row = support.setdefault(key, {"claim": ref, "indep": 0, "total": 0})
                row["total"] += 1
                if indep is True:
                    row["indep"] += 1
    if has_independence_signal:
        for key, row in support.items():
            if row["total"] >= 1 and row["indep"] <= 1 and key not in seen:
                seen.add(key)
                flagged.append({
                    "claim": row["claim"],
                    "reason": "single-independent-source",
                    "independent_sources": row["indep"],
                })
    return flagged


# ---- R2-RES-11: quantitative-fact reconciliation --------------------------

def reconcile_quantitative(quant_facts: Any) -> tuple[list, list]:
    """R2-RES-11: reconcile numeric facts grouped by normalized (metric, unit).

    Returns ``(extra_contested, unit_errors)``: when two+ facts on the same metric/unit
    disagree by >10% a contested_claim is synthesized; when the high/low ratio lands in
    the ~300-3000x band the disagreement is additionally flagged as a probable unit-scale
    (~1000x) error. Empty inputs / no disagreement → ([], []).
    """
    extra_contested: list[dict] = []
    unit_errors: list[dict] = []
    groups: dict[tuple, list] = {}
    for q in (quant_facts or []):
        if not isinstance(q, dict):
            continue
        metric = _norm_text(q.get("metric"))
        if not metric:
            continue
        unit = _norm_text(q.get("unit"))
        groups.setdefault((metric, unit), []).append(q)
    for (metric, unit), rows in groups.items():
        nums = [(_first_number(r.get("value")), r) for r in rows]
        nums = [(v, r) for v, r in nums if v is not None and v > 0]
        if len(nums) < 2:
            continue
        vals = [v for v, _ in nums]
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            continue
        ratio = hi / lo
        if ratio < 1.1:  # within ~10% → not a material disagreement
            continue
        positions = []
        for v, r in nums:
            src = str(r.get("source", "") or "").strip()
            positions.append({
                "stance": f"{r.get('value')} {r.get('unit', '')}".strip() + (f" ({src})" if src else ""),
                "sources": [src] if src else [],
                "tier": r.get("tier"),
            })
        probable_unit_error = bool(300 <= ratio <= 3000)
        why = (
            f"quantitative disagreement on '{rows[0].get('metric')}' reconciled by (metric,unit); "
            f"high/low ratio={round(ratio, 1)}"
            + ("; ~1000x apart — probable unit-scale error" if probable_unit_error else "")
        )
        extra_contested.append({
            "claim": str(rows[0].get("metric", metric)),
            "positions": positions,
            "status": "contested",
            "why_they_differ": why,
            "origin": "quant_reconcile",
        })
        if probable_unit_error:
            unit_errors.append({
                "metric": str(rows[0].get("metric", metric)),
                "unit": str(rows[0].get("unit", unit)),
                "ratio": round(ratio, 1),
                "values": [str(r.get("value")) for _, r in nums],
            })
    return extra_contested, unit_errors


# ---- R2-RES-4: recency / staleness annotation -----------------------------

def _clamp_asof_reference(extracted: "_dt.date | None", run_date: "_dt.date") -> "tuple[_dt.date, dict | None]":
    """RES-3: pick the quant-sanity/staleness reference date, distrusting a stale extraction.

    MiniMax 会把训练截止日（如 2026-01-15）报成研究截止日，于是当天抓到的一切时新事实
    全被打成「未来日期的伪实况」（单次 run 80 条 quant_implausible 假阳性）+ staleness
    整体偏移。抓取发生在运行日：抽出的截止日远早于运行日在构造上必为幻觉——钳制到
    运行日并返回 override 记录（供 meta 观测）。RESEARCH_ASOF_MAX_LAG_DAYS 控制容忍窗。
    Returns ``(ref_date, override|None)``；extracted 为 None 或在容忍窗内时 override=None。
    """
    if extracted is None:
        return run_date, None
    try:
        max_lag = int(os.environ.get("RESEARCH_ASOF_MAX_LAG_DAYS", "45") or "45")
    except ValueError:
        max_lag = 45
    if extracted < run_date - _dt.timedelta(days=max_lag):
        return run_date, {"extracted": extracted.isoformat(), "used": run_date.isoformat()}
    return extracted, None


def flag_implausible_quant(facts: Any, ref_date: "_dt.date | None") -> list:
    """QUALITY-OPT S12-structured: flag quantitative facts that are FUTURE-DATED relative to the
    research cutoff (e.g. '2026 Q3 earnings' with an as_of before Q3 exists) or carry an
    implausibly extreme growth value (>150% YoY). These single numbers anchor whole reports, so
    surfacing them lets downstream discount or re-verify. Observability → meta['quant_implausible']."""
    flags: list = []
    for r in (facts or []):
        if not isinstance(r, dict):
            continue
        metric = str(r.get("metric") or "")[:60]
        d = _parse_date(r.get("as_of_date"))
        # Only flag future-dated facts presented as ACTUALS. A forecasting dossier legitimately
        # carries forward-looking PROJECTIONS/forecasts/targets with a future as_of — those are
        # not implausible. The real failure is a claimed-actual with an impossible date
        # (e.g. "2026 Q3 earnings" stamped before Q3 exists).
        _ctx = (metric + " " + str(r.get("definition") or "") + " " + str(r.get("unit") or "")).lower()
        _is_projection = any(w in _ctx for w in (
            "projection", "projected", "forecast", "estimate", "estimated", "expected",
            "target", "outlook", "guidance", "by 20", "预测", "预计", "目标", "展望"))
        if d and ref_date and d > ref_date and not _is_projection:
            flags.append(f"{metric}: as_of {d.isoformat()} is AFTER research cutoff {ref_date.isoformat()} (claimed-actual with future date)")
        unit = str(r.get("unit") or "").lower()
        try:
            v = float(str(r.get("value")).replace("%", "").replace(",", "").strip())
        except (TypeError, ValueError):
            v = None
        if v is not None and abs(v) > 150 and (
            "%" in unit or "yoy" in unit or "growth" in unit or "增长" in metric or "同比" in metric
        ):
            flags.append(f"{metric}: {r.get('value')} {unit} — extreme growth (>150%); verify vs parent total")
    return flags


def annotate_recency_rows(rows: Any, ref_date: "_dt.date", stale_days: int, date_key: str = "date") -> dict:
    """R2-RES-4: annotate each row IN PLACE with ``staleness_days`` + ``is_stale`` and
    return a freshness histogram. ``ref_date`` is the research as-of date; a row older
    than ``stale_days`` is flagged stale. Undated rows are counted but not annotated.
    """
    hist = {"fresh_le_90": 0, "recent_le_365": 0, "stale_gt_365": 0, "undated": 0, "n_stale": 0}
    if not isinstance(rows, list):
        return hist
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _parse_date(r.get(date_key) or r.get("as_of_date") or r.get("date"))
        if d is None:
            hist["undated"] += 1
            continue
        age = (ref_date - d).days
        r["staleness_days"] = age
        is_stale = age > stale_days
        r["is_stale"] = is_stale
        if age <= 90:
            hist["fresh_le_90"] += 1
        elif age <= 365:
            hist["recent_le_365"] += 1
        else:
            hist["stale_gt_365"] += 1
        if is_stale:
            hist["n_stale"] += 1
    return hist


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


def _is_degraded_artifact(text: str, min_chars: int) -> bool:
    """RES-8: True 当非空文本是 LLM 错误降级消息或低于最短长度门。

    用于卷宗级守卫（卷宗此前完全绕过 report 的错误哨兵检查）：命中即应把变量清空
    走单轨降级，而不仅是跳过落盘——同一变量还会作为抽取「主」输入。空文本返回
    False（调用方对空已有独立分支）。min_chars<=0 只保留错误哨兵检查（旧行为）。
    """
    t = (text or "").strip()
    if not t:
        return False
    return looks_like_llm_error(t) or len(t) < max(0, min_chars)


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
    # RES-2: per-turn pending 表（v2）——双轨/fan-out 并发回合各自记账，回合末在锁内合并，
    # 杜绝跨线程 LIFO 错配（Track B 的结果确认/删除 Track A 的 URL）。
    _v2 = _fetch_accounting_v2()
    _pending_fetches: list[dict] = []
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
                            if _v2:  # #1 capture fetched URLs (turn-local, id-paired)
                                _pending_record_fetch(_pending_fetches, tc.get("name"), tc.get("args"), call_id=tc.get("id"))
                            else:
                                _record_fetched_url(tc.get("name"), tc.get("args"))
                    delta = data.get("content", "")
                    if delta:
                        msg_id = data.get("id") or ""
                        chunks.setdefault(msg_id, []).append(delta)
                        last_id = msg_id
                elif mtype == "tool":
                    plog.write("result", f"{data.get('name')} → {_truncate(data.get('content', ''))}")
                    if _v2:  # R1: drop dead fetches (exact tool_call_id pairing, FIFO fallback)
                        _pending_mark_result(_pending_fetches, data.get("name"), data.get("content"), call_id=data.get("tool_call_id"))
                    else:
                        _mark_fetch_result(data.get("name"), data.get("content"))
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
        _flag_research_degradation(f"{label}: {kind} (salvaged {salvaged_len} chars)")  # S10

    if _v2:
        _retry_dead_fetches(_pending_fetches, plog)  # R2: 死抓取丢弃前程序化重试一次（~8s 退避）
        _merge_pending_fetches(_pending_fetches)  # RES-2: 锁内合并本回合确认成功的抓取
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


_GAP_HEADING_RE = re.compile(r"gaps?\b", re.I)


def parse_gaps_from_notes(notes: str, limit: int = 12) -> list[str]:
    """Pull bullet items under a 'Gaps …' heading from a deep-pass's working notes.

    R2-RES-9 helper (pure, deterministic — no LLM). The deep-pass prompt asks each
    turn to end with a "## Gaps to carry into the next pass" section; this lifts those
    bullets so the next pass can be steered to close them. Returns [] when no gap
    section is present, so the caller threads nothing and the default path is unchanged.
    """
    if not notes:
        return []
    out: list[str] = []
    in_gaps = False
    for raw in notes.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^\s*#{1,6}\s+(.*\S)", line)
        if heading:
            in_gaps = bool(_GAP_HEADING_RE.search(heading.group(1)))
            continue
        if not in_gaps:
            continue
        m = _FANOUT_BULLET_RE.match(line)
        if m:
            item = m.group(1).strip(" *#`\"'。.")
            if 3 <= len(item) <= 200:
                out.append(item)
                if len(out) >= limit:
                    break
    return out


def _merge_gaps(accumulated: list[str], new_gaps: list[str], cap: int = 12) -> list[str]:
    """Case-insensitive dedup-merge of gap lists, keeping the most recent ``cap``."""
    seen = {g.lower() for g in accumulated}
    for g in new_gaps:
        if g.lower() not in seen:
            seen.add(g.lower())
            accumulated.append(g)
    return accumulated[-cap:]


def run_research_stage(client, question: str, depth: str, target_language: str | None, model_name: str, thread_id: str, plog: ProgressLog) -> str:
    """Run the research stage.

    Quick/standard remain one DeerFlow turn. Deep is intentionally multi-pass:
    several scoped research turns share the same thread/checkpointer, then a
    tool-free synthesis turn writes the final dossier from all accumulated notes
    and fetched sources.
    """
    preset = DEPTH_PRESETS[depth]
    if depth != "deep":
        text = run_streamed_turn(
            client,
            build_research_prompt(question, depth, target_language),
            thread_id,
            preset["recursion_limit"],
            plog,
            "research",
        )
        # RES-11: RESEARCH_COVERAGE_GATE 文档上是通用默认开的门，但此前只在 deep 生效——
        # 默认深度 standard 完全没有来源下限。RESEARCH_COVERAGE_GATE_STANDARD=true（默认关，
        # 保持今日行为/成本）时对 standard 也跑有界 top-up + 重合成；任何失败保留原文。
        if (depth == "standard" and _env_flag("RESEARCH_COVERAGE_GATE", True)
                and _env_flag("RESEARCH_COVERAGE_GATE_STANDARD", False)):
            try:
                try:
                    _min_src = int(os.environ.get("RESEARCH_MIN_SOURCES", "20") or "20")
                except ValueError:
                    _min_src = 20
                try:
                    _max_topups = max(0, int(os.environ.get("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", "2") or "2"))
                except ValueError:
                    _max_topups = 2
                _ran_topup = False
                for _round in range(_max_topups):
                    have = distinct_fetched_count()
                    if have >= _min_src:
                        break
                    plog.write("warn", f"coverage gate/standard (round {_round + 1}/{_max_topups}): only {have} distinct sources fetched (<{_min_src}); running a source-broadening top-up pass")
                    topup = run_streamed_turn(
                        client,
                        build_coverage_topup_prompt(question, None, have, _min_src, target_language),
                        thread_id, 240, plog, f"research:standard-coverage-topup-{_round + 1}",
                    )
                    _ran_topup = _ran_topup or bool(topup.strip())
                if _ran_topup:
                    # top-up 的笔记在同一线程里；重合成把新证据并入报告，失败则保留原文。
                    synth = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
                    if len(synth.strip()) > len(text.strip()):
                        text = synth
                plog.write("stage", f"coverage gate/standard: {distinct_fetched_count()} distinct sources fetched (floor {_min_src})")
            except Exception as _te:  # noqa: BLE001 — top-up 只做加法，绝不破坏本轮
                plog.write("warn", f"standard coverage top-up skipped (non-fatal): {_te}")
        return text

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

    # R2-RES-9: carry unresolved gaps forward between deep passes so each pass is
    # steered to close earlier open questions (default on; degrade-safe — empty gap
    # list yields the original prompt). Seed from the opening pass's gap section.
    _gap_threading = _env_flag("RESEARCH_DEEP_GAP_THREADING", True)
    accumulated_gaps: list[str] = parse_gaps_from_notes(opening) if _gap_threading else []
    for idx, phase in enumerate(DEEP_RESEARCH_PHASES, start=1):
        limit = int(phase["recursion_limit"])
        phase_text = run_streamed_turn(
            client,
            build_deep_phase_prompt(
                question, phase, idx, len(DEEP_RESEARCH_PHASES), target_language,
                prior_gaps=accumulated_gaps if _gap_threading else None,
            ),
            thread_id,
            limit,
            plog,
            f"research:deep-{idx}-{phase['label']}",
        )
        if phase_text.strip():
            reports.append(phase_text)
            if _gap_threading:
                accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(phase_text))

    # #2 COVERAGE GATE — actionable, not just a warning. If too few DISTINCT sources were
    # actually fetched-and-read, run a bounded number of top-up passes that broaden
    # high-tier coverage (and FETCH the pages) BEFORE synthesis, so the dossier rests on a
    # wide evidence base rather than a handful. Uses the #1 live fetched-URL count. Default
    # on; degrade-safe (wrapped; never breaks the run; SOFT — only adds passes, never fails
    # the run, distinct from the orchestrator's hard RESEARCH_QUALITY_GATE). Bounded by
    # RESEARCH_COVERAGE_GATE_MAX_ROUNDS.
    if _env_flag("RESEARCH_COVERAGE_GATE", True):
        try:
            min_sources = int(os.environ.get("RESEARCH_MIN_SOURCES", "20") or "20")
        except ValueError:
            min_sources = 20
        try:
            max_topups = max(0, int(os.environ.get("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", "2") or "2"))
        except ValueError:
            max_topups = 2
        for _round in range(max_topups):
            have = distinct_fetched_count()
            if have >= min_sources:
                break
            plog.write("warn", f"coverage gate (round {_round + 1}/{max_topups}): only {have} distinct sources fetched (<{min_sources}); running a source-broadening top-up pass")
            try:
                topup = run_streamed_turn(
                    client,
                    build_coverage_topup_prompt(question, accumulated_gaps, have, min_sources, target_language),
                    thread_id, 360, plog, f"research:deep-coverage-topup-{_round + 1}",
                )
                if topup.strip():
                    reports.append(topup)
                    if _gap_threading:
                        accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(topup))
            except Exception as _te:  # noqa: BLE001 — top-up is additive; never break the run
                plog.write("warn", f"coverage top-up pass skipped (non-fatal): {_te}")
                break
        plog.write("stage", f"coverage gate: {distinct_fetched_count()} distinct sources fetched (floor {min_sources})")

    synth = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
    if synth.strip():
        return synth

    plog.write("warn", "deep: tool-free synthesis returned empty text; falling back to concatenated pass notes")
    _flag_research_degradation("deep synthesis empty → concatenated pass notes (numbers may be ungrounded)")  # S10
    return "\n\n---\n\n".join(reports)


# ---------------------------------------------------------------------------
# 双轨 Track B —— actor-ontology 卷宗（与 Track A 的 deep-research 报告并行）
# ---------------------------------------------------------------------------


def build_actor_ontology_prompt(question: str, depth: str, target_language: str | None) -> str:
    """构造 Track B 的提示词：产出「本体就绪的 actor 卷宗」（actor_dossier.md）。

    与 :func:`build_research_prompt` 同风格/同签名。Track A 产出广覆盖证据报告；
    Track B 把同样的搜证功夫专门拧向 ACTOR 维度，遵循 ``actor-ontology-research``
    技能的输出契约：预测框架 + 情势简报、按显著度排序的真实关键 actor 阵容、每个
    actor 的深度画像、有向/有类型/带极性的关系网络，以及 actor 与关系随时间的演化。
    报告（Track A）解决「发生了什么」；卷宗（Track B）解决「谁在决定、谁受影响、
    他们如何相连」，作为下游本体生成与 actor 抽取的主来源。
    """
    lang_line = ""
    if target_language:
        lang_line = f"\n\nWrite the dossier in {target_language}."
    return (
        "You are an actor-ontology research lead producing the SEED material for a "
        "forecasting pipeline (knowledge graph + ontology + actor-based simulation). "
        "FOLLOW THE 'actor-ontology-research' skill: build on the deep-research skill's "
        "search craft, source tiering (S1–S4), evidence grading, triangulation, and "
        "verification, but specialize the mission toward an ACTOR-CENTRIC, "
        "ONTOLOGY-READY dossier rather than a generic topic report.\n\n"
        "TOOLING: This is WEB research — use web_search and web_fetch ONLY. There is NO local "
        "file corpus or workspace to inspect; do NOT call ls / read_file / glob / bash, and if "
        "a filesystem tool returns a permission error, ignore it and go straight to web_search.\n\n"
        f"FORECAST QUESTION:\n{question}\n\n"
        "Search the web from multiple angles, fetch and read the most important primary "
        "sources in full, then produce a SINGLE ontology-ready Markdown ACTOR DOSSIER "
        "(your final message) per the skill's OUTPUT CONTRACT. The dossier MUST contain, "
        "with these explicit labeled sections:\n\n"
        "1. FORECAST FRAME & SITUATION BRIEF — the forecast object, horizon, and as-of "
        "date; the current situation, how it got here, the forces in tension, the 3–6 "
        "fault lines the actors argue over, and the catalysts that would shift things.\n\n"
        "2. THE CAST (key actors), in SALIENCE ORDER — the real key actors only. Apply "
        "the role/salience triage rigorously: EXPLICITLY DEMOTE cited reporters, news "
        "outlets, wire services, and pollsters to SOURCES, not actors (simulation_tier 3); "
        "abstract concepts, products, metrics, rules, and other context objects are tier 4 "
        "— NOT cast members; the core decision-makers whose choices move the outcome are "
        "tier 1 (principals); materially-affected stakeholders are tier 2. An outlet is an "
        "actor ONLY if it itself moves the outcome. Aim for roughly 8–20 deeply-profiled "
        "cast members (up to ~35 for sprawling multi-party situations), chosen by causal "
        "role, not by how often a name appeared.\n"
        "   For EACH key actor, go deep (a thin label is a failure): canonical name + "
        "aliases and a one-line disambiguator; archetype (actor vs collective); "
        "simulation_tier (1 principal / 2 stakeholder / 3 source / 4 context-object) and "
        "role-class (principal / arbiter / stakeholder / amplifier / intermediary) with a "
        "salience tier and basis; jurisdiction/sector; WHY it matters to the outcome; its "
        "VALUES; BELIEFS / worldview; INCENTIVES (what it GAINS and what it LOSES under "
        "each plausible outcome); ranked GOALS with horizon; CONSTRAINTS; RESOURCES / "
        "capabilities; VULNERABILITIES; decision rights; and STATED position vs REVEALED "
        "behavior (surface the gap explicitly); plus its history/evolution (how it got "
        "here, how its strategy changed, its track record on commitments).\n\n"
        "3. THE RELATIONSHIP NETWORK — an explicit, enumerated list of DIRECTED, TYPED, "
        "VALENCED edges between cast members, one per line as "
        "`Source —[TYPE, valence, strength]→ Target — basis`. Cover the load-bearing "
        "relationships: allies, opponents, competitors, customers, suppliers, "
        "backers/investors, and regulators. State DIRECTION explicitly (who → whom); pick a "
        "precise TYPE (ALLY_OF / SUPPORTS / PARTNERS_WITH / OPPOSES / COMPETES_WITH / "
        "REGULATES / SANCTIONS / SUPPLIES / CUSTOMER_OF / FUNDS / INVESTS_IN / BACKS / "
        "DEPENDS_ON / INFLUENCES, or a precise free-text label); carry a VALENCE (allied / "
        "adversarial / neutral / transactional — a partner and a rival MUST be "
        "distinguishable, never flatten opposition into 'connected to'); a strength "
        "(high / medium / low); and a one-line researched basis. Every endpoint must be a "
        "canonical name from the cast.\n\n"
        "4. PER-ACTOR RELATIONAL ROSTER — within or beside each profile, name the actor's "
        "allies / opponents / competitors / customers / suppliers / backers-investors / "
        "supporters / regulators / dependents.\n\n"
        "5. EVOLUTION & TIMELINE — the dated sequence of how the cast and its "
        "alliances/rivalries FORMED and CHANGED: inflection points, realignments, "
        "entries/exits — not a present-day snapshot.\n\n"
        "6. DRIVERS, INDICATORS & SCENARIOS, then CONTESTED CLAIMS & a tiered SOURCE LIST "
        "(each source with its S1–S4 tier and date).\n\n"
        "CONSISTENCY: use the SAME canonical name for an actor everywhere (cast, network, "
        "roster, timeline) so downstream extraction resolves entities cleanly.\n\n"
        "IMPORTANT: Once you have gathered enough material, you MUST stop calling tools and "
        "write the full dossier as your very next message. The written dossier is the "
        "deliverable — do not keep searching for marginal extra detail. A run that never "
        "writes the dossier has failed."
        f"{lang_line}"
    )


# ===================== NEXTSTEPS P3-1: actor-dossier AI-judge → refine loop =====================
# 整条流水线的准确度被 actor 卷宗封顶；actor-ontology SKILL §6–§8 完整规定了「多pass + 8维
# AI-judge 门（PASS 标准 + ≤3 轮定向 refine）」，但此前 Track B 只跑「一次研究 + 一次合成」就发首稿
# ——正是 SKILL 明令禁止的「ship the first draft」。这里补上 judge→refine 环（默认开，预算有界）。

_JUDGE_DIMS = (
    "cast_correctness", "salience_ranking", "per_actor_depth", "relationship_completeness",
    "history_evolution", "evidence_grounding", "contradiction_handling", "ontology_readiness",
)
# §8 的四个不可妥协维度（cast 正确性 / 单 actor 深度 / 关系完整性 / 本体就绪度）。
_JUDGE_CRITICAL = ("cast_correctness", "per_actor_depth", "relationship_completeness", "ontology_readiness")


def build_judge_prompt(question: str, target_language: str | None, source_context: str | None = None) -> str:
    """构造对 actor 卷宗的 8 维 AI-judge 提示词（默认怀疑：未证明优秀即不合格）。只输出 JSON。

    R2-RES-8: 当可从卷宗自动统计出来源信号（S1–S4 分级提及、引用链接数）时，把它作为
    **校准参考**注入，帮助 evidence_grounding 维度评分；``source_context`` 为空则与原提示词
    逐字节一致（degrade-safe）。
    """
    lang = f"（用{target_language}书写 gaps）" if target_language else ""
    dims = "、".join(_JUDGE_DIMS)
    ctx = ""
    if source_context:
        ctx = (
            "\n来源信号（自动统计，仅供 evidence_grounding/ontology_readiness 维度校准，"
            f"不可替代你对卷宗的独立判断）：{source_context}\n"
        )
    return (
        "你是一名严苛的研究评审（actor-ontology-research SKILL §7–§8）。默认怀疑：一份卷宗未被证明"
        "优秀即视为不合格。针对下方【预测问题】评审【卷宗】，对以下 8 个维度各打 0–5 分并给定 verdict。\n"
        f"维度：{dims}。\n"
        "PASS 标准（§8，不可妥协）：无任何维度 <3；且 cast_correctness / per_actor_depth / "
        "relationship_completeness / ontology_readiness 四项各 ≥4；且总体均分 ≥4。否则 FAIL。\n"
        "若 FAIL，给出**定向**的 gaps 清单（具体、可执行，如：'缺少关键主体 X'、'X↔Y 边无 valence'、"
        f"'媒体 W 被误列为 actor，应降级为 source'）{lang}。只输出 JSON，不要解释：\n"
        '{"scores": {' + ", ".join(f'"{d}": 0-5' for d in _JUDGE_DIMS) + '}, '
        '"verdict": "PASS|FAIL", "gaps": ["..."]}\n\n'
        f"=== 预测问题 ===\n{question}\n"
        f"{ctx}"
    )


def _dossier_source_signal(dossier: str) -> str:
    """R2-RES-8: cheap, deterministic source-signal summary from a dossier's text.

    Counts S1–S4 tier mentions and citation links so the judge gets a calibration hint
    without a parsed sources.json (Track B runs before structured extraction). Returns
    '' when nothing is detectable, so the judge prompt stays unchanged.
    """
    if not dossier:
        return ""
    tiers = {t: len(re.findall(rf"\b{t}\b", dossier)) for t in _VALID_TIERS}
    links = len(re.findall(r"https?://", dossier))
    tier_part = ", ".join(f"{t}={n}" for t, n in tiers.items() if n)
    bits = []
    if tier_part:
        bits.append(f"tier mentions: {tier_part}")
    if links:
        bits.append(f"~{links} cited links")
    return "; ".join(bits)


def dossier_passes(scorecard) -> bool:
    """按 SKILL §8 判定卷宗是否通过。无有效记分牌时**不阻断**（degrade：回退为今日"发首稿"行为）。

    RES-9: 默认实现的是「无维度 <3 + 四关键维度 ≥4 + 均分 ≥4」（judge 打分噪声下更稳）；
    SKILL §8.2 已同步为此标准。ACTOR_DOSSIER_JUDGE_STRICT=true 可升级为全维度 ≥4。
    """
    if not isinstance(scorecard, dict):
        return True
    scores = scorecard.get("scores")
    if not isinstance(scores, dict) or not scores:
        return str(scorecard.get("verdict", "")).upper() != "FAIL"
    vals = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            pass
    if not vals:
        return str(scorecard.get("verdict", "")).upper() != "FAIL"
    if _env_flag("ACTOR_DOSSIER_JUDGE_STRICT", False) and min(vals) < 4:
        return False
    if min(vals) < 3:
        return False
    for k in _JUDGE_CRITICAL:
        try:
            if float(scores.get(k, 0)) < 4:
                return False
        except (TypeError, ValueError):
            return False
    return (sum(vals) / len(vals)) >= 4.0


def build_actor_refinement_prompt(question: str, gaps: list, depth: str,
                                  target_language: str | None) -> str:
    """构造一次**定向** refine 研究回合提示词：只补 judge 指出的 gaps，不重写整份卷宗。"""
    gap_lines = "\n".join(f"- {str(g)}" for g in (gaps or [])[:12])
    lang = f"\n用{target_language}书写。" if target_language else ""
    return (
        "对【预测问题】的 actor 卷宗，一名评审指出了以下**具体缺口**。只针对这些缺口做定向研究"
        "（必要时搜索/取证），补齐相应主体画像、关系 valence、来源分级或纠正误判，**不要**重写"
        "整份卷宗、不要偏离这些缺口。完成后把新发现以工作笔记形式给出，供随后合成采纳。\n\n"
        f"=== 缺口清单 ===\n{gap_lines}\n\n=== 预测问题 ===\n{question}{lang}\n"
    )


def judge_dossier(dossier: str, question: str, target_language: str | None,
                  model_name: str, plog: "ProgressLog") -> dict | None:
    """对卷宗做一次无工具的 AI-judge 评审，返回记分牌 dict（解析失败/异常→None）。"""
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        # R2-RES-8: route the judge to a distinct DEERFLOW_JUDGE_MODEL when configured
        # (a stronger/cheaper critic than the research model); unset → reuse model_name.
        judge_model = os.environ.get("DEERFLOW_JUDGE_MODEL", "").strip() or model_name
        model = create_chat_model(judge_model, thinking_enabled=False)
        prompt = (
            build_judge_prompt(question, target_language, _dossier_source_signal(dossier or ""))
            + "\n=== 卷宗 ===\n" + (dossier or "")[:60000]
        )
        resp = model.invoke([HumanMessage(content=prompt)])
        text = _message_text(getattr(resp, "content", resp))
        sc = extract_json_object(text)
        if isinstance(sc, dict):
            return sc
        plog.write("warn", "actor-ontology judge: could not parse scorecard JSON")
        return None
    except Exception as e:  # noqa: BLE001 — judge 失败不阻断，回退发当前稿
        plog.write("warn", f"actor-ontology judge failed ({type(e).__name__}: {e})")
        return None


def run_actor_ontology_stage(client, question: str, depth: str, target_language: str | None,
                             model_name: str, thread_id: str, plog: "ProgressLog",
                             out_dir=None) -> str:
    """运行 Track B：产出 actor-ontology 卷宗（actor_dossier.md 的内容）。

    一次有工具的研究回合（用 actor-ontology 提示词搜证、画像、建关系网），随后做一次
    无工具的合成回合，从同一线程的检查点上下文把材料写成卷宗——这样即便是「过度搜索」
    的模型（如 MiniMax-M3）也能可靠落出卷宗。刻意保持有界：不在 Track B 内跑完整 deep
    fan-out（那是 Track A 的职责），只跑「研究回合 + 合成」这一对，避免把研究阶段成本再翻一倍。

    返回卷宗 Markdown；任何失败由调用方（main）兜底为单轨。
    """
    # 给 Track B 的研究回合一个合理的递归预算：deep 复用 deep-opening 的预算，
    # 否则用该 depth preset 的 recursion_limit。
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
    if depth == "deep":
        research_limit = int(os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "220"))
    else:
        research_limit = int(preset["recursion_limit"])

    plog.write("stage", "actor-ontology (Track B): starting actor/ontology research turn")
    research_text = run_streamed_turn(
        client,
        build_actor_ontology_prompt(question, depth, target_language),
        thread_id,
        research_limit,
        plog,
        "actor-ontology",
    )

    # 无工具合成（可被 refine 后重复调用）：从本线程**当前**已收集的研究上下文把卷宗写出来。
    # 优先用 actor-ontology 提示词做裸模型合成，保证卷宗结构正确不退化为普通研究报告。
    def _synthesize() -> str:
        try:
            thread = client.get_thread(thread_id)
        except Exception as e:  # noqa: BLE001 — 线程读不到则退回研究回合文本
            plog.write("warn", f"actor-ontology synthesize: could not load thread ({type(e).__name__}: {e})")
            return research_text
        messages: list = []
        for cp in reversed(thread.get("checkpoints") or []):
            vals = cp.get("values") or {}
            if vals.get("messages"):
                messages = vals["messages"]
                break
        if not messages:
            plog.write("warn", "actor-ontology synthesize: no messages in thread; using research-turn text")
            return research_text
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
                parts.append(text)
        context = "\n\n".join(parts).strip()
        if not context:
            plog.write("warn", "actor-ontology synthesize: gathered context empty; using research-turn text")
            return research_text
        # RES-1: MiniMax 0-tool-call 桩回合只留几十字符上下文，裸模型「合成」= 凭参数记忆
        # 编造整份卷宗（唯一守卫 len(dossier)>=len(research_text) 对桩恒真），伪造 actor 阵容
        # 毒化 graph/persona/forecast。低于门槛拒绝合成，返回空串 → main() 降级单轨。
        _min_ctx = _synth_min_context_chars()
        if _min_ctx and len(context) < _min_ctx:
            plog.write("warn", f"actor-ontology synthesize: gathered context only {len(context)} chars (< {_min_ctx}); refusing tool-free fabrication")
            _flag_research_degradation("actor-ontology: near-empty research context; refused tool-free fabrication")
            return ""
        _cap = _synthesis_context_cap(model_name)
        if len(context) > _cap:
            context = context[:_cap] + "\n\n[...research context truncated...]"
        plog.write("stage", f"actor-ontology synthesize: writing dossier (tool-free) from {len(context)} chars")
        try:
            from deerflow.models import create_chat_model
            from langchain_core.messages import HumanMessage

            model = create_chat_model(model_name, thinking_enabled=False)
            prompt = (
                build_actor_ontology_prompt(question, depth, target_language)
                + "\n\nSTOP researching. Do NOT call any tools — base the dossier ONLY on the "
                "research already gathered below; do not invent.\n\n"
                "=== GATHERED RESEARCH ===\n"
                + context
            )
            resp = model.invoke([HumanMessage(content=prompt)])
            dossier = _message_text(getattr(resp, "content", resp))
            plog.write("stage", f"actor-ontology synthesize: produced {len(dossier)} chars")
            if len(dossier.strip()) >= len(research_text.strip()):
                return dossier
            return research_text
        except Exception as e:  # noqa: BLE001 — 合成失败退回研究回合文本
            plog.write("warn", f"actor-ontology synthesize: tool-free call failed ({type(e).__name__}: {e})")
            return research_text

    dossier = _synthesize()

    # RES-1: 空卷宗（合成被最小上下文门拒绝，或研究回合本身为空）直接短路出去 ——
    # judge_dossier('') 会白烧一次 judge 调用 + 最多 MAX_ROUNDS 轮 refine。
    # main() 对空卷宗已有干净的单轨降级路径。
    if not dossier.strip():
        plog.write("warn", "actor-ontology: empty dossier after synthesis; skipping judge loop (single-track degrade)")
        return ""

    # NEXTSTEPS P3-1: AI-judge → 定向 refine 环（默认开，预算有界）。判不合格则按 gap 清单做一次
    # 定向研究回合再重合成，最多 ACTOR_DOSSIER_JUDGE_MAX_ROUNDS 轮。任何失败都回退当前稿（degrade）。
    if not _env_flag("ACTOR_DOSSIER_JUDGE", True):
        return dossier
    # RESEARCH-3: latency lever — skip the judge→refine loop entirely when the dossier is
    # already long (a strong, lengthy dossier rarely fails the §8 gate, so the extra judge
    # + refine round-trip is pure latency). Default 0 = off (judge always runs, as today).
    try:
        skip_if_chars = int(os.environ.get("ACTOR_DOSSIER_JUDGE_SKIP_IF_CHARS_GTE", "0") or "0")
    except ValueError:
        skip_if_chars = 0
    if skip_if_chars > 0 and len((dossier or "").strip()) >= skip_if_chars:
        plog.write("ok", f"actor-ontology judge skipped (dossier {len(dossier.strip())} chars >= {skip_if_chars}; latency lever)")
        return dossier
    try:
        max_rounds = max(0, int(os.environ.get("ACTOR_DOSSIER_JUDGE_MAX_ROUNDS", "1") or "1"))
    except ValueError:
        max_rounds = 1
    scorecard = None
    for r in range(max_rounds):
        scorecard = judge_dossier(dossier, question, target_language, model_name, plog)
        if scorecard is None:
            break
        plog.write("stage",
                   f"actor-ontology judge round {r + 1}: verdict={scorecard.get('verdict')} "
                   f"scores={scorecard.get('scores')}")
        if dossier_passes(scorecard):
            plog.write("ok", f"actor-ontology judge: PASS at round {r + 1}")
            break
        gaps = scorecard.get("gaps") or []
        if not gaps:
            break
        plog.write("stage", f"actor-ontology refine round {r + 1}: addressing {len(gaps)} gaps")
        try:
            run_streamed_turn(
                client,
                build_actor_refinement_prompt(question, gaps, depth, target_language),
                thread_id, research_limit, plog, "actor-ontology-refine",
            )
            dossier = _synthesize()
            if not dossier.strip():  # RES-1: refine 后仍低于上下文门槛 → 不再对空稿评审
                plog.write("warn", "actor-ontology refine: synthesis still empty; single-track degrade")
                return ""
        except Exception as e:  # noqa: BLE001 — refine 失败发当前稿
            plog.write("warn", f"actor-ontology refine failed ({type(e).__name__}: {e}); shipping current dossier")
            break
    # 落记分牌到 out_dir（供运维/质量面板查看；best-effort）。
    if out_dir is not None and scorecard is not None:
        try:
            _atomic_write_text(
                out_dir / "actor_dossier_judge.json",
                json.dumps(scorecard, ensure_ascii=False, indent=2),
            )
        except Exception:  # noqa: BLE001
            pass
    return dossier


# ---------------------------------------------------------------------------
# Prediction-market signals (Oddpool aggregates Kalshi + Polymarket)
# ---------------------------------------------------------------------------
# 研究报告落盘后，用研究问题/hot_topics/头部 actor 名确定性派生几条短检索词，抓取
# 相关活跃预测市场的隐含概率，落 prediction_markets.json 并向 research_report.md
# 追加一个机器抓取的确定性 markdown 节——下游（合成/报告/预测抽取）把市场价格当
# **校准锚点**（非真值）。本 bridge 跑在 DeerFlow 自己的 venv 里，只用 stdlib
# urllib（镜像 backend/app/utils/prediction_markets.py 的同一批端点与规整规则）。
# 全程 degrade-safe：无 ODDPOOL_API_KEY / 无结果 / 网络错误 → 一行日志静默跳过。

PREDICTION_MARKETS_FILENAME = "prediction_markets.json"
_ODDPOOL_BASE_URL = "https://api.oddpool.com"
_ODDPOOL_TRANSIENT = (429, 500, 502, 503, 504)

# 与 backend/app/utils/prediction_markets.py 保持一致的停用词/短语启发式（无 LLM）。
_PM_STOPWORDS = frozenset("""
a an the and or but of to in on for with by from at as is are was were be been being
will would could should shall may might must can do does did not no nor this that these
those it its their his her our your my we they you he she i who whom whose which what
when where why how whether if than then so such very just now here there per via about
into over under between among during before after above below up down out off again
further once more most other some any all both each few own same too s t don also
year years month months week weeks day days future likely impact effect effects report
research question forecast prediction predict analysis analyze scenario scenarios
""".split())
_PM_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|[一-鿿]{2,}")


def _pm_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _pm_salient_phrases(text: str, max_words: int = 3) -> list[str]:
    """从研究问题抽取显著短语：连续非停用词 token 聚成 ≤max_words 词短语（确定性）。"""
    phrases: list[str] = []
    cur: list[str] = []

    def _flush() -> None:
        if cur:
            phrases.append(" ".join(cur))
            cur.clear()

    for tok in _PM_TOKEN_RE.findall(str(text or "")):
        if re.match(r"[一-鿿]", tok):
            cur.append(tok[:12])
            _flush()
            continue
        # 全大写缩略词（AI/EU/GDP）即使短也保留；其余 <3 字符英文词按噪声丢弃。
        if tok.lower() in _PM_STOPWORDS or (len(tok) < 3 and not tok.isupper()):
            _flush()
            continue
        cur.append(tok)
        if len(cur) >= max_words:
            _flush()
    _flush()
    seen: set = set()
    out: list[str] = []
    for p in phrases:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _pm_derive_queries(question: str, hot_topics: list | None = None,
                       actor_names: list | None = None,
                       max_queries: int = 6, max_words: int = 5) -> list[str]:
    """确定性派生市场检索词（每条 ≤5 词，全文检索短词效果最好），去重限量。"""
    out: list[str] = []
    seen: set = set()

    def _add(q: Any) -> None:
        s = re.sub(r"\s+", " ", str(q or "")).strip(" \t\"'.,;:!?()[]{}")
        if not s:
            return
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words])
        if s.lower() in seen or len(out) >= max_queries:
            return
        seen.add(s.lower())
        out.append(s)

    for ph in _pm_salient_phrases(question, max_words=3)[:3]:
        _add(ph)
    for t in (hot_topics or [])[:4]:
        _add(t)
    for n in (actor_names or [])[:3]:
        _add(n)
    return out


def _oddpool_get(path: str, params: dict, api_key: str, timeout: float = 15.0) -> Any:
    """stdlib GET + JSON。瞬时错误（网络/超时/5xx/429）重试一次；仍失败则抛（调用方兜底）。"""
    import urllib.error
    import urllib.parse
    import urllib.request
    url = _ODDPOOL_BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-Key": api_key,
                                               "Accept": "application/json"})
    last_err: Any = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in _ODDPOOL_TRANSIENT and attempt == 1:
                continue
            break  # 4xx 鉴权/参数错误不重试
        except Exception as e:  # noqa: BLE001 — URLError/超时/JSON 解析 → 重试一次
            last_err = e
            continue
    raise RuntimeError(f"oddpool GET {path} failed: {last_err}")


def _pm_normalize_market(raw: Any, matched_query: str, min_volume: float) -> dict | None:
    """单条市场规整化（镜像 backend 规则）；已结算/无价/低量/零流动性 → None。"""
    if not isinstance(raw, dict):
        return None
    market_id = str(raw.get("market_id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not market_id or not question:
        return None
    status = str(raw.get("status") or "").strip().lower()
    if raw.get("settled_at") or status in ("settled", "closed", "finalized", "resolved"):
        return None
    prob = _pm_float(raw.get("last_yes_price"))
    if prob is None or not (0.0 <= prob <= 1.0):
        return None
    volume = _pm_float(raw.get("volume")) or 0.0
    liquidity = _pm_float(raw.get("liquidity")) or 0.0
    if liquidity <= 0 or volume < float(min_volume):
        return None
    return {
        "market_id": market_id,
        "exchange": str(raw.get("exchange") or "").strip().lower(),
        "question": question,
        "implied_yes_prob": round(prob, 4),
        "volume": volume,
        "liquidity": liquidity,
        "event_title": str(raw.get("event_title") or "").strip(),
        "matched_query": matched_query,
    }


def _pm_snapshot(queries: list[str], api_key: str, per_query: int = 8,
                 max_total: int = 20, min_volume: float = 200) -> list[dict]:
    """对一组检索词取市场快照：按 market_id 去重、过滤、按成交量降序限量。
    单个 query 失败只丢那一批（degrade-safe）。"""
    by_id: dict[str, dict] = {}
    for q in queries:
        q = str(q or "").strip()
        if not q:
            continue
        try:
            data = _oddpool_get("/search/markets",
                                {"q": q, "status": "active", "limit": per_query}, api_key)
        except Exception:  # noqa: BLE001 — 单 query 失败不影响其余
            continue
        if not isinstance(data, list):
            continue
        for raw in data:
            norm = _pm_normalize_market(raw, matched_query=q, min_volume=min_volume)
            if norm is not None and norm["market_id"] not in by_id:
                by_id[norm["market_id"]] = norm
    markets = sorted(by_id.values(), key=lambda m: -(m.get("volume") or 0.0))
    return markets[:max(0, int(max_total))]


def _pm_render_section(markets: list[dict], as_of: str) -> str:
    """确定性渲染追加到 research_report.md 的「预测市场信号」markdown 节。"""
    lines = [
        "## Prediction Market Signals",
        "",
        (f"> Machine-fetched from Oddpool (Kalshi + Polymarket) as of {as_of}. "
         "Market-implied probabilities are **calibration anchors, not ground truth** — "
         "prices move continuously; mind freshness before relying on them."),
        "",
        "### Prediction Market Signals (Kalshi/Polymarket via Oddpool)",
        "",
        "| # | Market question | Venue | Implied P(yes) | Volume |",
        "|---|---|---|---|---|",
    ]

    def _cell(x: Any) -> str:
        return str(x).replace("|", "／").replace("\n", " ").strip()

    for i, m in enumerate(markets, 1):
        prob = _pm_float(m.get("implied_yes_prob"))
        vol = _pm_float(m.get("volume"))
        lines.append("| {i} | {q} ({mid}) | {ex} | {p} | {v} |".format(
            i=i,
            q=_cell(str(m.get("question") or "")[:160]),
            mid=_cell(m.get("market_id") or ""),
            ex=_cell(m.get("exchange") or "—"),
            p=(f"{prob * 100:.0f}%" if prob is not None else "—"),
            v=(f"{vol:,.0f}" if vol is not None else "—"),
        ))
    return "\n".join(lines)


def _collect_prediction_markets(out_dir: Path, question: str, report: str,
                                meta: dict, plog: "ProgressLog") -> None:
    """研究报告落盘后抓取预测市场信号：落 prediction_markets.json + 追加报告节 + 注册进 meta。

    调用方包 try/except；本函数内部对「无 key/无结果」也只记一行日志（degrade-safe）。
    """
    api_key = os.environ.get("ODDPOOL_API_KEY", "").strip()
    if not api_key or not _env_flag("PREDICTION_MARKETS_ENABLED", True):
        plog.write("warn", "prediction markets skipped (no ODDPOOL_API_KEY or disabled)")
        return
    # hot_topics / 头部 actor 名来自已落盘的 actors.json（缺失/未抽取时仅用研究问题）。
    hot_topics: list = []
    actor_names: list = []
    try:
        actors_path = out_dir / ACTORS_FILENAME
        if actors_path.exists():
            obj = json.loads(actors_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                hot_topics = [str(t) for t in (obj.get("hot_topics") or []) if t]
                actor_names = [str((a or {}).get("name") or "").strip()
                               for a in (obj.get("actors") or []) if isinstance(a, dict)]
                actor_names = [n for n in actor_names if n]
    except Exception:  # noqa: BLE001 — actors.json 只是查询词的可选增强
        pass
    queries = _pm_derive_queries(question, hot_topics, actor_names)
    if not queries:
        plog.write("warn", "prediction markets skipped (no derivable queries)")
        return
    try:
        max_total = int(os.environ.get("ODDPOOL_MAX_MARKETS", "20") or "20")
    except ValueError:
        max_total = 20
    markets = _pm_snapshot(queries, api_key, max_total=max_total)
    if not markets:
        plog.write("warn", f"prediction markets: no relevant active markets (queries={queries})")
        return
    as_of = _utcnow()
    payload = {"as_of": as_of, "source": "oddpool", "queries": queries, "markets": markets}
    _atomic_write_text(out_dir / PREDICTION_MARKETS_FILENAME,
                       json.dumps(payload, ensure_ascii=False, indent=2))
    # 追加确定性 markdown 节到已落盘的 research_report.md（下游合成/报告阶段读文件即得）。
    section = _pm_render_section(markets, as_of)
    new_report = report.rstrip() + "\n\n" + section + "\n"
    _atomic_write_text(out_dir / REPORT_FILENAME, new_report)
    # 注册进 meta（与 sources_count/quantitative_count 同一「artifact 登记」模式）。
    meta["report_chars"] = len(new_report)
    meta["prediction_markets_count"] = len(markets)
    meta["prediction_markets_as_of"] = as_of
    plog.write("ok", f"wrote {PREDICTION_MARKETS_FILENAME} ({len(markets)} markets, "
                     f"{len(queries)} queries) + appended report section")


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
    # RES-10: 走原子写 — 该文件被后续阶段逐字消费（报告背景/预测问题），watchdog SIGKILL
    # 时机不巧会留下截断的 brief，静默降级整条交付链（与其余契约文件同一保证）。
    _atomic_write_text(out_dir / REQUIREMENT_FILENAME, question + "\n")

    plog = ProgressLog(out_dir / PROGRESS_FILENAME)
    _reset_fetched_sources()  # #1: fresh fetched-URL collector per run
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
        # 双轨：开启 DEERFLOW_DUAL_TRACK（默认开）时，Track A（广覆盖证据报告）与
        # Track B（actor-ontology 卷宗）在 SAME client 上用 ISOLATED thread_id 并发跑
        # （沿用 run_deep_fanout 已验证安全的并发回合模式）。Track A 结果仍是 report，
        # 下游逻辑逐字节不变；Track B 结果记为 dossier。Track B 任何异常/空 → dossier=""
        # 并告警，整轮退回单轨继续。关闭双轨时走原始单轨调用，行为逐字节一致。
        dossier = ""
        if _env_flag("DEERFLOW_DUAL_TRACK", True):
            import concurrent.futures as _cf

            actor_thread_id = thread_id + "-actor"
            plog.write("stage", "dual-track: running Track A (report) + Track B (actor dossier) concurrently")
            with _cf.ThreadPoolExecutor(max_workers=2) as _ex:
                _fut_a = _ex.submit(
                    run_research_stage,
                    client,
                    question,
                    args.depth,
                    args.target_language,
                    args.model,
                    thread_id,
                    plog,
                )
                _fut_b = _ex.submit(
                    run_actor_ontology_stage,
                    client,
                    question,
                    args.depth,
                    args.target_language,
                    args.model,
                    actor_thread_id,
                    plog,
                    out_dir,  # NEXTSTEPS P3-1: 让 Track B 把 AI-judge 记分牌落到 out_dir
                )
                report = _fut_a.result()
                try:
                    dossier = _fut_b.result() or ""
                except Exception as _exc:  # noqa: BLE001 — Track B 失败退回单轨
                    dossier = ""
                    plog.write("warn", f"dual-track: Track B (actor dossier) failed; continuing single-track ({type(_exc).__name__}: {_exc})")
            if not dossier.strip():
                plog.write("warn", "dual-track: Track B produced no dossier; continuing single-track")
        else:
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

        # RES-1: 最短报告门。一个无错误哨兵的短桩（如 MiniMax 0-tool-call 的 47 字符回合）
        # 能同时穿过 looks_like_llm_error 与 orchestrator 的 LIVE 守卫（:809 只拒 <400 且带
        # 错误标记），下游会拿桩建图并报成功。低于门槛按「无报告」诚实失败。0 = 关闭（回退旧行为）。
        try:
            _min_report = max(0, int(os.environ.get("RESEARCH_MIN_REPORT_CHARS", "400") or "400"))
        except ValueError:
            _min_report = 400
        _report_len = len(report.strip())
        if _report_len == 0 or _report_len < _min_report:
            _err = ("agent produced no report text" if _report_len == 0
                    else f"report too short ({_report_len} chars < {_min_report}) — treated as no report")
            meta.update(status="failed", error=_err, finished_at=_utcnow())
            write_meta()
            plog.write("error", f"no usable report text produced ({_report_len} chars)")
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

        # RES-8: 卷宗与 report 适用同一 LLM 错误哨兵/最短长度门，且必须清掉「变量」而非只跳过
        # 落盘——同一个 dossier 局部变量还会在下方作为抽取「主」输入排在真报告之前；一条短的
        # 422/new_sensitive 降级消息会直接播种 actors.json。清空后走既有单轨降级。
        if _is_degraded_artifact(dossier, _min_report):
            plog.write("warn", f"dual-track: dossier looks like an LLM error/stub ({len(dossier.strip())} chars); dropping to single-track ({_truncate(dossier, 120)})")
            dossier = ""

        # 双轨：Track B 的 actor 卷宗（若有内容）。report 通过安全网/校验并落盘后再写卷宗，
        # 与 report 一样去掉外层 markdown 围栏。空卷宗（关闭双轨或 Track B 失败）不落此文件，
        # 行为与现状逐字节一致。
        if dossier.strip():
            dossier = unwrap_markdown_fence(dossier)
            _atomic_write_text(out_dir / ACTOR_DOSSIER_FILENAME, dossier)
            meta["actor_dossier_chars"] = len(dossier)
            plog.write("ok", f"wrote {ACTOR_DOSSIER_FILENAME} ({len(dossier)} chars)")
            write_meta()

        # --- Stage 2: structured extraction (best effort) ---
        if not args.no_actors:
            try:
                # PRIMARY: tool-free extraction from the finished report — reliable JSON
                # (eager models like MiniMax-M3 otherwise keep calling web_search during the
                # agent turn and never emit parseable JSON, dropping the whole enriched contract).
                # 双轨：有卷宗时，卷宗是 actor 抽取的「主」输入，报告作为「附加上下文」augmentation；
                # 无卷宗时退回原行为（仅喂报告）。
                if dossier.strip():
                    extraction_input = (
                        dossier
                        + "\n\n---\n\n## 补充：广度深度研究报告（附加上下文）\n\n"
                        + report
                    )
                else:
                    extraction_input = report
                raw = extract_structured_tool_free(extraction_input, args.target_language, args.model, args.depth, plog)
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
                    # #1: ground sources.json in URLs the agent ACTUALLY FETCHED (drops
                    # URL-less/fabricated model entries; enriches fetched URLs with the
                    # model's tier/date/title). Default on; degrade-safe (no fetched URLs
                    # AND no model sources -> empty -> falls through to old behavior).
                    if _env_flag("RESEARCH_GROUND_SOURCES", True):
                        try:
                            grounded, _dropped = merge_fetched_into_sources(sources)
                            if grounded:
                                sources = grounded
                                meta["sources_fetched"] = sum(1 for s in grounded if s.get("source_origin") == "fetched")
                                if _dropped:
                                    plog.write("warn", f"source grounding: dropped {_dropped} ungrounded (URL-less) model source(s); kept {len(grounded)} grounded")
                                else:
                                    plog.write("ok", f"source grounding: {len(grounded)} sources ({meta['sources_fetched']} fetched-and-read)")
                        except Exception as _ge:  # noqa: BLE001 — grounding is additive
                            plog.write("warn", f"source grounding skipped (non-fatal): {_ge}")
                    # R2-RES-4: research as-of date + staleness threshold for recency
                    # annotation below (used for sources AND quantitative facts).
                    # RES-3: 不再盲信模型抽出的 as_of_date（见 _clamp_asof_reference）。
                    ref_date, _asof_override = _clamp_asof_reference(
                        _parse_date(obj.get("as_of_date")), _dt.date.today())
                    if _asof_override:
                        meta["as_of_date_overridden"] = _asof_override
                        plog.write("warn", f"as_of_date {_asof_override['extracted']} is far before the run date (model hallucinated its training cutoff); using {_asof_override['used']} as reference")
                    try:
                        stale_days = int(os.environ.get("RESEARCH_STALE_DAYS", "365") or "365")
                    except ValueError:
                        stale_days = 365
                    _recency_on = _env_flag("RESEARCH_RECENCY_WEIGHTING", True)
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
                    # R2-RES-11: reconcile numeric facts by (metric,unit) BEFORE writing
                    # contested.json so synthesized disagreements survive the JSON boundary.
                    extra_contested: list = []
                    if quant and _env_flag("RESEARCH_QUANT_RECONCILE", True):
                        extra_contested, unit_errors = reconcile_quantitative(quant)
                        if unit_errors:
                            meta["quant_unit_warnings"] = unit_errors
                            plog.write("warn", f"quant reconcile: {len(unit_errors)} probable unit-scale (~1000x) disagreement(s)")
                        if extra_contested:
                            plog.write("ok", f"quant reconcile: +{len(extra_contested)} contested claim(s) from numeric disagreement")
                    if quant:
                        # R2-RES-4: annotate each fact with staleness_days/is_stale in place.
                        if _recency_on:
                            meta["quant_freshness"] = annotate_recency_rows(quant, ref_date, stale_days, date_key="as_of_date")
                        _impl = flag_implausible_quant(quant, ref_date)  # S12-structured
                        if _impl:
                            meta["quant_implausible"] = _impl
                            plog.write("warn", f"quant sanity: {len(_impl)} implausible/future-dated fact(s): {_impl[:2]}")
                        _atomic_write_text(out_dir / QUANTITATIVE_FILENAME, json.dumps(quant, ensure_ascii=False, indent=2))
                        meta["quantitative_count"] = len(quant)
                        plog.write("ok", f"wrote {QUANTITATIVE_FILENAME} ({len(quant)} facts)")
                    # EXECPLAN2 I-0-1: promote contested_claims to a first-class
                    # contested.json so the adversarial work survives the JSON boundary.
                    # R2-RES-11: fold in the reconciled numeric disagreements.
                    contested = _clean_optional_rows(obj.get("contested_claims")) + extra_contested
                    if contested:
                        _atomic_write_text(out_dir / CONTESTED_FILENAME, json.dumps(contested, ensure_ascii=False, indent=2))
                        meta["contested_count"] = len(contested)
                        plog.write("ok", f"wrote {CONTESTED_FILENAME} ({len(contested)} contested claims)")
                    if isinstance(sources, list) and sources:
                        # R2-RES-4: annotate sources with staleness before persisting.
                        if _recency_on:
                            meta["source_freshness"] = annotate_recency_rows(sources, ref_date, stale_days, date_key="date")
                        # R2-RES-12: jurisdiction/language histogram + monoculture warning.
                        if _env_flag("RESEARCH_SOURCE_DIVERSITY", True):
                            meta["source_diversity"] = source_diversity_histogram(sources)
                            if meta["source_diversity"].get("single_region_warning"):
                                _sd = meta["source_diversity"]
                                plog.write("warn", f"source diversity: {int(_sd['dominant_share'] * 100)}% of jurisdiction-tagged sources from '{_sd['dominant_jurisdiction']}'")
                        _atomic_write_text(out_dir / SOURCES_FILENAME, json.dumps(sources, ensure_ascii=False, indent=2))
                        meta["sources_count"] = len(sources)
                        # EXECPLAN2 I-0-0: tier histogram for observability (and so a
                        # downstream coverage gate can reject S4-heavy dossiers).
                        meta["source_tiers"] = source_tier_histogram(sources)
                        plog.write(
                            "ok",
                            f"wrote {SOURCES_FILENAME} ({len(sources)} sources; tiers={meta['source_tiers']})",
                        )

                    # --- Evidence-quality scorecard + audits (R2-RES-1/6/10) ---
                    # All deterministic, post-extraction, and best-effort: wrapped so a
                    # malformed obj can never fail the run. research_quality is the
                    # contract-critical meta key consumed by the RESEARCH_QUALITY_FLOOR gate.
                    try:
                        judge_sc = None
                        _jp = out_dir / "actor_dossier_judge.json"
                        if _jp.exists():
                            try:
                                judge_sc = json.loads(_jp.read_text(encoding="utf-8"))
                            except Exception:  # noqa: BLE001
                                judge_sc = None
                        if _env_flag("RESEARCH_COMPLETENESS_PROBE", True):
                            gaps = compute_coverage_gaps(obj)
                            meta["coverage_gaps"] = gaps
                            if gaps.get("missing_named_entities") or gaps.get("orphan_fault_lines"):
                                plog.write("warn", f"completeness probe: {len(gaps['missing_named_entities'])} unmapped named entities, {len(gaps['orphan_fault_lines'])} orphan fault lines")
                        if _env_flag("RESEARCH_TRIANGULATION_AUDIT", True):
                            flagged = audit_triangulation(sources, contested)
                            if flagged:
                                meta["single_origin_loadbearing"] = flagged
                                plog.write("warn", f"triangulation audit: {len(flagged)} single-origin load-bearing claim(s)")
                        # RES-4: grounding 分量 = 真实抓取数/来源门槛 ×（合成曾拒绝编造→0.5 折扣），
                        # 外加 quant_implausible 占比的封顶扣减（≤0.15）。全部来自现场已有信号；
                        # RESEARCH_QUALITY_GROUNDING=false 时回退旧三分量评分。
                        _grounding = None
                        _q_penalty = 0.0
                        if _env_flag("RESEARCH_QUALITY_GROUNDING", True):
                            try:
                                _fetched_n = meta.get("sources_fetched")
                                if _fetched_n is None:
                                    _fetched_n = distinct_fetched_count()
                                try:
                                    _min_src = int(os.environ.get("RESEARCH_MIN_SOURCES", "20") or "20")
                                except ValueError:
                                    _min_src = 20
                                _grounding = min(1.0, float(_fetched_n) / max(1, _min_src))
                                if any("refused tool-free fabrication" in f for f in _RESEARCH_FLAGS):
                                    _grounding *= 0.5  # 合成网曾因上下文近空拒绝编造：该 run 的落地度存疑
                                _grounding = round(_grounding, 3)
                                _impl_n = len(meta.get("quant_implausible") or [])
                                if quant and _impl_n:
                                    _q_penalty = 0.15 * min(1.0, _impl_n / max(1, len(quant)))
                            except Exception:  # noqa: BLE001 — 评分增强绝不阻断
                                _grounding = None
                                _q_penalty = 0.0
                        rq = compute_research_quality(sources, obj, judge_sc, grounding=_grounding, quant_penalty=_q_penalty)
                        # S10: fold research-degradation events (recursion truncation / tool-free
                        # fallback) into the quality block so downstream discounts confidence and
                        # the report flags that some numbers may be ungrounded.
                        if _RESEARCH_FLAGS:
                            rq["degraded"] = True
                            rq["degradation"] = list(_RESEARCH_FLAGS)
                            meta["research_degradation"] = list(_RESEARCH_FLAGS)
                            plog.write("warn", f"research degraded ({len(_RESEARCH_FLAGS)} event(s)): {_RESEARCH_FLAGS[:2]}")
                        meta["research_quality"] = rq
                        try:
                            quality_floor = float(os.environ.get("RESEARCH_QUALITY_FLOOR", "0.45") or "0.45")
                        except ValueError:
                            quality_floor = 0.45
                        if rq.get("score") is not None and rq["score"] < quality_floor:
                            plog.write("warn", f"research_quality {rq['score']:.2f} < floor {quality_floor:.2f} (components={rq['components']})")
                        else:
                            plog.write("ok", f"research_quality={rq.get('score')} (components={rq['components']})")
                    except Exception as _qe:  # noqa: BLE001 — analytics must never fail the run
                        plog.write("warn", f"research-quality analytics failed (non-fatal): {_qe}")
            except Exception as e:  # extraction must never fail the whole run
                plog.write("warn", f"structured extraction failed (non-fatal): {e}")

        # --- Stage 3: 预测市场信号（Oddpool 聚合 Kalshi/Polymarket；best effort）---
        # 报告与结构化抽取都已落盘后再抓取（可复用 actors.json 的 hot_topics/actor 名派生
        # 检索词）。市场隐含概率作为下游报告/预测的「校准锚点」（非真值）。Degrade-safe：
        # 无 key/无结果/网络错误 → 一行日志跳过，绝不影响已产出的研究契约。
        try:
            _collect_prediction_markets(out_dir, question, report, meta, plog)
            write_meta()
        except Exception as _pm_err:  # noqa: BLE001 — 市场信号为可选增强
            plog.write("warn", f"prediction markets skipped (non-fatal): {_pm_err}")

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
