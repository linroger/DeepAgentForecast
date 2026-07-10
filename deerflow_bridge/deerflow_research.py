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
import hashlib
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
    # SCALE-2: deep 档没有单回合预算 —— 它的步进预算来自 DEERFLOW_DEEP_OPENING_RECURSION_LIMIT
    # （开场）+ DEEP_RESEARCH_PHASES（各 pass，经 RESEARCH_PHASE_BUDGET_MULT 缩放）。原
    # recursion_limit=1660 字面量是死值：deep 路径从不读 preset["recursion_limit"]（2 处读点均在
    # depth != "deep" 分支），故删除以免误导调参。
    "deep": {"guidance": "Run the multi-pass deep research protocol. Do not compress the work into one short search pass: map the source landscape, read primary sources in full, profile actors, test contradictions, and only then synthesize a long evidence-backed dossier."},
}

DEEP_RESEARCH_PHASES: list[dict[str, Any]] = [
    # SCALE-2: 各 pass 预算 ×1.5（220/360/300/300/260 → 330/540/450/450/390）——实测 deep 档
    # 的瓶颈是 pass 内步进预算而非 pass 数；读取时还会再乘 RESEARCH_PHASE_BUDGET_MULT（见
    # _phase_budget，默认 1.0 = 仅下方基线）。
    {
        "label": "scope",
        "recursion_limit": 330,
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
        "recursion_limit": 540,
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
        "recursion_limit": 450,
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
        "recursion_limit": 450,
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
        "recursion_limit": 390,
        "focus": (
            "Translate the gathered evidence into forecast inputs for the downstream "
            "simulation: timelines, catalysts, leading indicators, measurable variables, "
            "base/upside/downside scenarios, likely winners and losers, and what each "
            "actor would know or believe. Fill remaining evidence gaps. Do NOT write the "
            "final report yet."
        ),
    },
]


def _phase_budget(base: int) -> int:
    """SCALE-2: deep pass 步进预算在**读取时**乘以 ``RESEARCH_PHASE_BUDGET_MULT``。

    环境倍率（默认 1.0 = 用 :data:`DEEP_RESEARCH_PHASES` 的基线，不改今日行为）让部署
    无需改码即可整体拉伸/压缩 deep 档的每-pass 预算（例如 0.5 做快省冒烟、2.0 做加深跑）。
    非法/非正值一律回退 1.0（degrade-safe）；结果至少为 1。
    """
    try:
        mult = float(os.environ.get("RESEARCH_PHASE_BUDGET_MULT", "1.0") or "1.0")
    except ValueError:
        mult = 1.0
    if mult <= 0:
        mult = 1.0
    return max(1, int(round(int(base) * mult)))

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
# SCALE-2: quick/standard 保持 4000；deep 档一份合格卷宗远不止几页，触发线由
# :func:`_synthesis_trigger_chars` 按深度取 15000（SYNTHESIS_TRIGGER_CHARS_DEEP 可调）。
SYNTHESIS_TRIGGER_CHARS = 4000


def _synthesis_trigger_chars(depth: str) -> int:
    """SCALE-2: 深度感知的「报告太短 → 重合成」触发线（字符）。

    deep 档的交付物是长卷宗，4000 字符只相当于几页纸——低到让一份严重缩水的 deep 报告
    直接放行。deep 默认 15000（``SYNTHESIS_TRIGGER_CHARS_DEEP`` 可覆盖；非法值回退默认），
    quick/standard 保持原 4000 常量（行为与现状逐字节一致）。
    """
    if depth == "deep":
        try:
            v = int(os.environ.get("SYNTHESIS_TRIGGER_CHARS_DEEP", "15000") or "15000")
        except ValueError:
            v = 15000
        return max(0, v)
    return SYNTHESIS_TRIGGER_CHARS
# Cap on how much gathered-research context to feed the tool-free synthesis net.
# Model-aware: MiniMax-M3 has a ~1M-token context window so it can ingest a very
# large slice of the gathered sources for a richer, more detailed synthesis; Claude
# (Sonnet 4.6, ~200K context) gets a smaller but still generous slice. Bigger context
# in == richer dossier out.
# SCALE-2: 400000→650000 —— Claude 类 ~200K token 窗口下 400K 字符（~100k tokens）留白过多，
# 抬到 650000（~162k tokens）让合成吃进更多已抓取证据；LARGE 大窗口类保持 900000 不变。
SYNTHESIS_MAX_CONTEXT_CHARS = 650000          # default / Claude-class (~162k tokens)
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


def _research_min_sources(depth: str) -> int:
    """SCALE-2: 深度感知的覆盖门来源下限（不同已抓取来源数）。

    显式设置的 ``RESEARCH_MIN_SOURCES`` 对所有深度一律生效（与旧语义一致，部署可统一
    调门槛）；未设置时 deep 档默认 20→45 —— 多-pass ×1.5 预算下 20 个来源的门形同虚设，
    45 才配得上「证据面够宽」；quick/standard 保持 20（成本/行为与现状一致）。非法值
    回退按深度取默认（degrade-safe）。
    """
    raw = (os.environ.get("RESEARCH_MIN_SOURCES", "") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 45 if depth == "deep" else 20

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
# ITEM-3 RESEARCH CHECKPOINTING — 断点续跑
#
# 桥跑在一个持久化的 LangGraph checkpointer 上（config.yaml database: sqlite
# .deer-flow/data），线程状态（每 pass 的搜索/抓取笔记）按 thread_id 持久落盘。但每次
# run 都会 mint 一个全新的随机 thread_id，且编排器崩溃/超时重试时从 pass 0 全量重启——
# 一整轮已完成的 deep 研究（数十个已抓取来源、多轮多相位）在崩溃时被整体丢弃。
#
# 修复：每完成一个 pass/phase/worker，就把 research_checkpoint.json 原子落到 out_dir，
# 记录 {thread_id, completed_passes, fetched_source_count, gaps, depth, question_hash,
# updated_at}。--resume 时（且 checkpoint 存在、question_hash 匹配、depth 一致）复用记录
# 的 thread_id（checkpointer 仍持有该线程的全部笔记），跳过 completed_passes 列出的 pass，
# 从下一 pass 续跑（覆盖门 + 合成照常重跑——合成相对研究很便宜）。stale/缺失/hash 不匹配
# 的 checkpoint → 全量重启并打标。RESEARCH_CHECKPOINT=false 时所有记录为 no-op（degrade-safe）。
# ---------------------------------------------------------------------------
RESEARCH_CHECKPOINT_FILENAME = "research_checkpoint.json"
_RESEARCH_CHECKPOINT_VERSION = 1
_RESEARCH_CHECKPOINT_MAX_GAPS = 60  # gaps 列表落盘上限（护栏：避免 checkpoint 无界膨胀）


def _question_hash(question: str) -> str:
    """研究问题的稳定归一化哈希（首 16 位 sha256）。

    归一化：折叠空白 + 去首尾 + 小写，让「同一问题」的表层差异（多余空格/大小写）
    不影响续跑判定；不同问题则 hash 不同 → plan_research_resume 拒绝续跑、全量重启。
    """
    norm = " ".join((question or "").split()).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _checkpoint_enabled() -> bool:
    """RESEARCH_CHECKPOINT（默认 true）——研究断点续跑总开关。"""
    return _env_flag("RESEARCH_CHECKPOINT", True)


def _extract_only_min_chars() -> int:
    """ITEM-14 --extract-only：既存 research_report.md 的最短字符门（不足=诚实非零退出）。

    RESEARCH_EXTRACT_ONLY_MIN_CHARS 覆盖（默认 400，与 RES-1 的报告下限同量级）；非法/非正
    值回退 400。抽取-only 打捞的前提是「已有一份可抽取的报告」，太小的残段抽不出有用结构。
    """
    try:
        return max(1, int(os.environ.get("RESEARCH_EXTRACT_ONLY_MIN_CHARS", "400") or "400"))
    except ValueError:
        return 400


def write_research_checkpoint(out_dir, *, thread_id: str, completed_passes,
                              fetched_source_count: int, gaps, depth: str,
                              question_hash: str, updated_at: str | None = None) -> None:
    """把 research_checkpoint.json 原子写到 out_dir（out_dir=None → no-op）。

    与其余契约文件同一原子写保证：watchdog SIGKILL 时机不巧也不会留下半写的 JSON。
    """
    if out_dir is None:
        return
    payload = {
        "version": _RESEARCH_CHECKPOINT_VERSION,
        "thread_id": thread_id,
        "question_hash": question_hash,
        "depth": depth,
        "completed_passes": list(completed_passes or []),
        "fetched_source_count": int(fetched_source_count or 0),
        "gaps": list(gaps or [])[:_RESEARCH_CHECKPOINT_MAX_GAPS],
        "updated_at": updated_at or _utcnow(),
    }
    _atomic_write_text(Path(out_dir) / RESEARCH_CHECKPOINT_FILENAME,
                       json.dumps(payload, ensure_ascii=False, indent=2))


def load_research_checkpoint(out_dir):
    """读回 research_checkpoint.json；缺失/损坏/非 dict → None（degrade-safe，绝不抛）。"""
    if out_dir is None:
        return None
    p = Path(out_dir) / RESEARCH_CHECKPOINT_FILENAME
    try:
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001 — 损坏的 checkpoint 视为无 checkpoint（全量重启）
        return None


def plan_research_resume(checkpoint, question: str, depth: str) -> dict:
    """纯函数：给定磁盘上的 checkpoint 与当前 question/depth，判定能否续跑。

    返回 ``{resume: bool, thread_id: str|None, completed_passes: list, reason: str}``。
    仅当 checkpoint 是 dict、含非空 thread_id、question_hash 与当前问题匹配、depth 一致
    时 resume=True；任一不满足 → resume=False + 原因（调用方据此全量重启并打标）。
    零 I/O、零副作用，便于单测。
    """
    _miss = {"resume": False, "thread_id": None, "completed_passes": [], "reason": ""}
    if not isinstance(checkpoint, dict):
        return {**_miss, "reason": "no checkpoint"}
    tid = checkpoint.get("thread_id")
    if not tid:
        return {**_miss, "reason": "checkpoint missing thread_id"}
    want = _question_hash(question)
    got = checkpoint.get("question_hash")
    if got != want:
        return {**_miss, "reason": f"question hash mismatch ({got} != {want})"}
    ckpt_depth = checkpoint.get("depth")
    if ckpt_depth is not None and depth is not None and ckpt_depth != depth:
        return {**_miss, "reason": f"depth mismatch ({ckpt_depth} != {depth})"}
    completed = checkpoint.get("completed_passes")
    if not isinstance(completed, list):
        completed = []
    return {"resume": True, "thread_id": tid, "completed_passes": list(completed), "reason": "ok"}


def should_run_pass(pass_id: str, completed_passes, resume: bool) -> bool:
    """纯函数（pass-skip planner）：续跑时已记完成的 pass 跳过，其余照跑。

    非续跑（resume=False）→ 恒 True（逐字节不改今日行为）。续跑时：pass_id 已在
    completed_passes 中 → False（跳过，其笔记已在复用线程的 checkpoint 里）。
    """
    if not resume:
        return True
    return pass_id not in set(completed_passes or [])


class ResearchCheckpointer:
    """研究阶段的断点续跑记录器：每完成一个 pass 就把 research_checkpoint.json 落盘。

    线程安全（deep 并行相位/扇出 worker 并发调用 record_pass）。``enabled=False`` 或
    ``out_dir=None`` 时所有方法为 no-op（degrade-safe，逐字节不改今日无 checkpoint 行为）。
    completed_passes 存的是稳定的 pass-id（deep-opening / deep-phase-N / standard），
    与并行/回退无关，供 --resume 侧的 should_run_pass 判跳过。
    """

    def __init__(self, out_dir, thread_id: str, depth: str, question: str, *, enabled: bool = True):
        self.out_dir = Path(out_dir) if out_dir is not None else None
        self.thread_id = thread_id
        self.depth = depth
        self.question_hash = _question_hash(question)
        self.enabled = bool(enabled) and self.out_dir is not None
        self._lock = threading.Lock()
        self.completed_passes: list[str] = []
        self.gaps: list[str] = []
        self.fetched_source_count = 0

    def seed_completed(self, completed_passes) -> None:
        """续跑时用磁盘 checkpoint 里已完成的 pass-id 预置本记录器（保留历史，不覆盖）。"""
        with self._lock:
            for pid in (completed_passes or []):
                if pid and pid not in self.completed_passes:
                    self.completed_passes.append(pid)

    def _flush_locked(self) -> None:
        if not self.enabled:
            return
        try:
            write_research_checkpoint(
                self.out_dir,
                thread_id=self.thread_id,
                completed_passes=self.completed_passes,
                fetched_source_count=self.fetched_source_count,
                gaps=self.gaps,
                depth=self.depth,
                question_hash=self.question_hash,
            )
        except Exception:  # noqa: BLE001 — 断点记录纯增益，绝不阻断研究
            pass

    def _refresh_fetched_locked(self, fetched_source_count) -> None:
        if fetched_source_count is not None:
            self.fetched_source_count = int(fetched_source_count)
        else:
            try:
                self.fetched_source_count = distinct_fetched_count()
            except Exception:  # noqa: BLE001
                pass

    def record_pass(self, pass_id: str, *, gaps=None, fetched_source_count=None) -> None:
        """记录一个已完成 pass（其笔记确已落在复用线程的 checkpoint 里）+ 刷新进度落盘。"""
        if not self.enabled or not pass_id:
            return
        with self._lock:
            if pass_id not in self.completed_passes:
                self.completed_passes.append(pass_id)
            if gaps is not None:
                self.gaps = list(gaps)
            self._refresh_fetched_locked(fetched_source_count)
            self._flush_locked()

    def update_progress(self, *, gaps=None, fetched_source_count=None) -> None:
        """只刷新进度（gaps/来源数），不新增 completed pass（覆盖门/自适应轮用）。"""
        if not self.enabled:
            return
        with self._lock:
            if gaps is not None:
                self.gaps = list(gaps)
            self._refresh_fetched_locked(fetched_source_count)
            self._flush_locked()

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

# PAR-1: 并行 scoped worker（KIQ fan-out 与 deep 阶段并行）跑在各自隔离的 thread_id 上，
# 其 checkpoint 不在主线程里；旧路径只把「几行摘要」写回主线程（build_fanout_absorption_prompt），
# 一个 8-worker fan-out 约 70% 的证据在合成前就被丢弃。这里用一个 run-scoped 全表累积每个
# worker 的**完整**笔记（并发安全），供 synthesize_from_thread 在合成时整体折叠进 gathered
# context。空表（standard/quick 或未开 fan-out）→ 逐字节不改今日行为。
_FANOUT_WORKER_NOTES: list[str] = []
_FANOUT_NOTES_LOCK = threading.Lock()

# PM-4: 研究开跑前先取一份 Polymarket 快照，把一段紧凑「当前市场定价」块注入 pass-0
# 提示词（让开场就带着「市场把 X 定在 NN%——去查为什么」的锚点搜）。空串 = 无块（默认，
# 逐字节不改今日行为）。build_research_prompt 读取本值；INT-1 复用初始快照喂给结构化抽取。
_MARKET_PRICING_BLOCK: str = ""
_INITIAL_PM_MARKETS: list[dict] = []

# AGENTIC-SEARCH: 当 --subagents 开启（client 侧 subagent_enabled=True，解锁 harness 内建
# `task` 工具，lead agent 可委派 scoped-researcher 子代理）时置 True。研究阶段的各提示词
# 构造器据此注入「主动委派」指令块（否则空串，逐字节不改今日无委派行为）。由 main() 依
# args.subagents 设置、每 run 在 _reset_fetched_sources 复位。指令块本身另受 env 开关
# RESEARCH_AGENTIC_SEARCH（默认 true）二次门控，可独立于 --subagents 关掉指令注入。
_AGENTIC_DELEGATION: bool = False

# WAVE9-RQ2: 合成时钉进提示词的引注索引（[S<n>] → 已抓取真实来源，与
# merge_fetched_into_sources 的 fetched 主干同顺序）。由 synthesize_multipart /
# 单调用合成在注入 SOURCE INDEX 块时设置；finalize_report_citations 据此在落盘前
# 校验记号、剔悬空、补确定性 '## References' 节。空表 = 本轮未钉过索引
# （standard 主路径由 agent 自编号参考节，或引注功能关闭）。
_PINNED_CITATION_INDEX: list[dict] = []


def _set_agentic_delegation(enabled: bool) -> None:
    global _AGENTIC_DELEGATION
    _AGENTIC_DELEGATION = bool(enabled)


def _set_pinned_citation_index(entries: "list[dict]") -> None:
    global _PINNED_CITATION_INDEX
    _PINNED_CITATION_INDEX = list(entries or [])


def _set_market_pricing_block(text: str) -> None:
    global _MARKET_PRICING_BLOCK
    _MARKET_PRICING_BLOCK = text or ""


def _set_initial_pm_markets(markets: list[dict]) -> None:
    global _INITIAL_PM_MARKETS
    _INITIAL_PM_MARKETS = list(markets or [])


def _flag_research_degradation(reason: str) -> None:
    if reason and reason not in _RESEARCH_FLAGS:
        _RESEARCH_FLAGS.append(reason)


def _record_worker_notes(header: str, text: str) -> None:
    """PAR-1: 记录一个隔离线程 worker 的完整笔记，供最终合成折叠。并发安全、绝不抛错。"""
    if not text or not text.strip():
        return
    block = f"## {header}\n\n{text.strip()}" if header else text.strip()
    with _FANOUT_NOTES_LOCK:
        _FANOUT_WORKER_NOTES.append(block)


def _collected_worker_notes() -> list[str]:
    """PAR-1: 当前 run 已累积的全部 worker 完整笔记（快照拷贝，读侧不持锁运算）。"""
    with _FANOUT_NOTES_LOCK:
        return list(_FANOUT_WORKER_NOTES)


def _fanout_worker_budget() -> int:
    """PAR-1: 每个 fan-out scoped worker 的步进预算（recursion_limit）。

    原死值 220 抬到默认 260，并可经 ``RESEARCH_FANOUT_WORKER_BUDGET`` 调整（并行阶段
    则各自传入自己的 phase 预算，走 recursion_limit 显式参数，不读此默认）。非法/非正
    值一律回退 260（degrade-safe）。
    """
    try:
        v = int(os.environ.get("RESEARCH_FANOUT_WORKER_BUDGET", "260") or "260")
    except ValueError:
        v = 260
    return v if v > 0 else 260


def _reset_fetched_sources() -> None:
    _FETCHED_SOURCES.clear()
    _RESEARCH_FLAGS.clear()
    _set_market_pricing_block("")   # PM-4: 每 run 重置注入的市场定价块
    _set_initial_pm_markets([])
    _set_pinned_citation_index([])  # WAVE9-RQ2: 每 run 重置钉住的引注索引
    _set_agentic_delegation(False)  # AGENTIC-SEARCH: 每 run 复位；main() 依 --subagents 重设
    with _FANOUT_NOTES_LOCK:
        _FANOUT_WORKER_NOTES.clear()


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


# INT-2: 工具参数 JSON 校验/修复（在把 URL 计入抓取账/写 sources.json 前把关）。
_SEARCH_TOOLS = ("web_search", "search", "tavily_search", "google_search", "bing_search")
_BARE_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?(\.[A-Za-z0-9\-]+)+(/[^\s]*)?$")


def _is_valid_http_url(u: Any) -> bool:
    """URL 合法性：必须有 http/https scheme 且有 host（netloc）。纯函数、可单测。"""
    try:
        from urllib.parse import urlparse
        p = urlparse(str(u or "").strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:  # noqa: BLE001
        return False


def _repair_url(u: Any) -> str:
    """把裸 host（'polymarket.com/x'）或协议相对（'//host/x'）修复为带 scheme 的合法 URL；
    无法修复（含空格/非域名形状）→ ''。纯函数、可单测。"""
    s = str(u or "").strip()
    if not s or " " in s:
        return ""
    if s.startswith("//"):
        s = "https:" + s
    elif "://" not in s and _BARE_HOST_RE.match(s):
        s = "https://" + s
    return s if _is_valid_http_url(s) else ""


def _extract_arg_url(args: Any) -> Any:
    """从工具参数里取 URL 字段（dict 的 url/uri/link/href 或裸字符串）。"""
    if isinstance(args, dict):
        return args.get("url") or args.get("uri") or args.get("link") or args.get("href")
    if isinstance(args, str):
        return args
    return None


def _validate_tool_args(name: Any, args: Any) -> "tuple[Any, bool, str]":
    """INT-2: 在 dispatch/记账前校验并（尽量）修复一次完整的工具参数。返回
    (args, valid, reason)。非搜索/抓取工具一律放行。web_search 的 query 去空白后
    <4 字符 → 拒；web_fetch 的 URL 无 scheme+host → 先尝试修复（补 https://），仍非法则拒。
    纯函数（除 urllib 解析），可单测。"""
    n = str(name or "").lower()
    if n in _SEARCH_TOOLS:
        q = ""
        if isinstance(args, dict):
            raw_q = args.get("query") or args.get("q") or args.get("queries") or ""
            q = " ".join(str(x) for x in raw_q) if isinstance(raw_q, list) else str(raw_q)
        elif isinstance(args, str):
            q = args
        if len(q.strip()) < 4:
            return args, False, f"web_search query too short ({len(q.strip())} chars < 4)"
        return args, True, ""
    if n in _FETCH_TOOLS:
        raw_url = _extract_arg_url(args)
        # 参数里根本没有可识别的 URL 字段（可能是未知键）——无法校验，放行（沿用旧行为，
        # 记账侧本就只在 URL 以 http 打头时才落账），只处理「有 URL 串但非法」的情形。
        if raw_url is None:
            return args, True, ""
        url = str(raw_url or "").strip()
        if url and not _is_valid_http_url(url):
            repaired = _repair_url(url)
            if repaired:
                if isinstance(args, dict):
                    args = dict(args)
                    for k in ("url", "uri", "link", "href"):
                        if args.get(k):
                            args[k] = repaired
                            break
                else:
                    args = repaired
                url = repaired
        if not _is_valid_http_url(url):
            return args, False, "web_fetch URL invalid (no scheme+host)"
        return args, True, ""
    return args, True, ""


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


def _default_source_tier() -> str:
    """SCALE-5: 通用兜底来源分级。域名表命不中、模型也没给 tier 的**已抓取真实来源**该拿一个
    基线 tier（默认 S3）——untiered 在 source_tier_mix 里按 0.3 计权，比 S3 的 0.4 还低，会把
    80/104 untiered 的运行拖成 research_quality≈0.73。非法值回退 S3；用 RESEARCH_UNIVERSAL_TIERING
    总开关关闭本行为（关闭后 untiered 保持无 tier，与旧行为逐字节一致）。"""
    raw = (os.environ.get("RESEARCH_DEFAULT_SOURCE_TIER", "S3") or "S3").strip().upper()
    # 只允许 S1/S2/S3 作为兜底（S4=reject-tier，作默认会让所有 untiered 抓取源被 D5 丢弃）。
    return raw if raw in ("S1", "S2", "S3") else "S3"


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
        # INT-2: URL 须有 scheme+host 才算真来源；裸 host / 无协议串先尝试修复，仍非法则丢弃。
        if not _is_valid_http_url(u):
            repaired = _repair_url(u)
            u = _norm_url(repaired) if repaired else ""
        if _is_valid_http_url(u):
            by_url[u] = s
        else:
            dropped += 1
    out: list[dict] = []
    seen: set[str] = set()
    s4_dropped = 0

    _universal_tiering = _env_flag("RESEARCH_UNIVERSAL_TIERING", True)
    _default_tier = _default_source_tier()

    def _finalize_tier(row: dict) -> bool:
        """Apply domain tiering when the model gave none; DROP S4 (D5). Returns keep?."""
        t = str(row.get("tier") or "").strip().upper()
        if t not in _VALID_TIERS:
            t = _tier_from_domain(row.get("url")) or ""
            if t:
                row["tier"] = t
        # SCALE-5: 域名表也命不中、模型也没给 tier 的**已抓取真实来源** → 给基线 tier（默认 S3），
        # 而非留 untiered（后者在 source_tier_mix 里比 S3 计权还低）。只对 fetched 生效——cited-未抓取
        # 的存疑来源保持 untiered。RESEARCH_UNIVERSAL_TIERING=false 关闭本行为（回退旧的 untiered）。
        if (_universal_tiering and row.get("source_origin") == "fetched"
                and str(row.get("tier") or "").strip().upper() not in _VALID_TIERS):
            row["tier"] = _default_tier
        if (row.get("tier") or "").upper() == "S4" or _tier_from_domain(row.get("url")) == "S4":
            return False  # reject-tier source: never cite (SKILL §4 / D5)
        return True

    for f in _FETCHED_SOURCES:                       # grounded backbone (fetched-and-read)
        u = _norm_url(f.get("url"))
        if not _is_valid_http_url(u) or u in seen or f.get("ok") is False:  # INT-2: scheme+host 才算真来源
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


def _bash_sandbox_enabled() -> bool:
    """ITEM-7 门控：data-analysis / 图表落盘的提示只在 host bash 执行面可用时才注入。

    权威开关是 config.yaml 的 ``sandbox.allow_host_bash``（本部署为 true——脚本型技能
    chart-visualization / data-analysis / forecast-visuals 靠它才有真实 shell/node/python
    执行面）。桥进程用 env 镜像 ``DEERFLOW_ALLOW_HOST_BASH`` 读它（编排器可下发），缺省 True
    与部署 config 对齐。任何不满足「完全受信任、单用户」前提、把 config 改回 allow_host_bash:
    false 的机器，应同时设 DEERFLOW_ALLOW_HOST_BASH=false——两处一致后，这些依赖 bash 的
    提示句自动消失（degrade-safe：agent 不会被指向一个它其实没有的执行面）。
    """
    return _env_flag("DEERFLOW_ALLOW_HOST_BASH", True)


def _actor_cast_max() -> int:
    """ACTOR-CAST DISCIPLINE: hard cap on MAIN actors extracted/kept through the pipeline.

    Any real forecast simulation should distill to <20 main actors — only those whose
    decisions and actions causally affect the forecasted outcome. Env-read (the backend
    orchestrator forwards its env verbatim); unset/invalid → 20. 0 disables the cap
    (recovers the old uncapped extraction).
    """
    raw = os.environ.get("ACTOR_CAST_MAX")
    try:
        v = int(str(raw).strip()) if raw is not None and str(raw).strip() else 20
    except (TypeError, ValueError):
        v = 20
    return max(0, v)


# Media/observer markers for ACTOR_EXCLUDE_MEDIA (mirrors backend actors.is_media_entity):
# reporting/commentary entities are CONTEXT (sources), never cast members, unless the
# researcher explicitly marks them simulation_tier 1/2 (i.e. they themselves move the outcome).
_MEDIA_ROLE_KEYWORDS = (
    "journalist", "reporter", "correspondent", "columnist", "commentator", "pundit",
    "news outlet", "news agency", "newswire", "wire service", "newspaper", "broadcaster",
    "news channel", "media outlet", "media organization", "media organisation", "media company",
    "pollster", "think tank", "think-tank", "blogger", "podcaster", "media analyst",
    "记者", "评论员", "专栏作家", "媒体机构", "新闻机构", "通讯社", "报社", "电视台", "智库", "民调机构",
)


def _actor_explicit_tier(actor: dict) -> "int | None":
    """The actor's explicit simulation_tier (1-4) if the extraction provided one, else None."""
    raw = actor.get("simulation_tier")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw in (1, 2, 3, 4):
        return raw
    if isinstance(raw, str):
        m = re.search(r"[1-4]", raw)
        if m:
            return int(m.group(0))
    return None


def _actor_is_media(actor: dict) -> bool:
    """True if the actor row looks like a media/observer entity (context, not cast).

    Signals: type=="Media", archetype=="source", or a reporting/commentary keyword in
    role/role_class. An EXPLICIT simulation_tier of 1/2 overrides — the researcher
    deliberately judged that this entity itself moves the outcome.
    """
    if not isinstance(actor, dict):
        return False
    if _actor_explicit_tier(actor) in (1, 2):
        return False
    if str(actor.get("type", "") or "").strip().lower() == "media":
        return True
    if str(actor.get("archetype", "") or "").strip().lower() == "source":
        return True
    haystack = " ".join(
        str(actor.get(k, "") or "") for k in ("role", "role_class", "description")
    ).lower()
    return any(kw in haystack for kw in _MEDIA_ROLE_KEYWORDS)


_INFLUENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _actor_cast_rank(actor: dict) -> tuple:
    """Sort key (higher = more causally influential): tier weight, salience, influence."""
    tier = _actor_explicit_tier(actor)
    if tier is None:
        arch = str(actor.get("archetype", "") or "").strip().lower()
        if arch == "source":
            tier = 3
        elif arch and arch not in ("actor", "collective"):
            tier = 4
        else:
            infl = str(actor.get("influence", "") or "").strip().lower()
            tier = 1 if infl == "high" else 2
    tier_weight = {1: 4, 2: 3, 3: 2, 4: 1}.get(tier, 3)
    sal = 0.0
    sal_obj = actor.get("salience")
    if isinstance(sal_obj, dict):
        score = sal_obj.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            sal = max(0.0, min(1.0, float(score)))
        else:
            sal = {"high": 0.85, "medium": 0.55, "low": 0.3}.get(
                str(sal_obj.get("tier", "") or "").strip().lower(), 0.0)
    if sal == 0.0:
        sal = {"high": 0.85, "medium": 0.55, "low": 0.3}.get(
            str(actor.get("influence", "") or "").strip().lower(), 0.3)
    infl_rank = _INFLUENCE_RANK.get(str(actor.get("influence", "") or "").strip().lower(), 2)
    return (tier_weight, sal, infl_rank)


def _cast_norm(name: Any) -> str:
    return " ".join(str(name or "").strip().casefold().split())


def enforce_actor_cast(obj: dict, meta: dict, plog: "ProgressLog | None" = None) -> None:
    """ACTOR-CAST DISCIPLINE (post-extraction, in place, degrade-safe).

    1. ACTOR_EXCLUDE_MEDIA (default on): demote media/observer rows (type=Media,
       archetype=source, journalist/commentator/pollster/think-tank roles) out of
       actors[] — they are context, not decision-makers with causal agency.
    2. ACTOR_CAST_MAX (default 20): if more actors remain, keep the top-ranked by
       (simulation tier, salience, influence) and record the cut in
       meta["actors_truncated_from"].

    Demoted/cut rows are preserved under obj["context_entities"] (downstream readers
    only consume obj["actors"], so the extra key is purely additive); relationships
    whose endpoints left the cast are dropped (edges MUST connect cast members).
    Never empties the cast: if media-demotion would remove everything, it is skipped.
    """
    actors = obj.get("actors")
    if not isinstance(actors, list) or not actors:
        return
    rows = [a for a in actors if isinstance(a, dict)]
    if not rows:
        return
    original_n = len(rows)
    cap = _actor_cast_max()
    exclude_media = _env_flag("ACTOR_EXCLUDE_MEDIA", True)

    demoted: list = []
    kept: list = []
    if exclude_media:
        for a in rows:
            (demoted if _actor_is_media(a) else kept).append(a)
        if not kept:  # safety net: never empty the cast
            kept, demoted = rows, []
    else:
        kept = list(rows)

    truncated: list = []
    if cap > 0 and len(kept) > cap:
        kept = sorted(kept, key=_actor_cast_rank, reverse=True)
        truncated = kept[cap:]
        kept = kept[:cap]

    if not demoted and not truncated:
        return

    obj["actors"] = kept
    obj["context_entities"] = (obj.get("context_entities") or []) + demoted + truncated

    # Keep the relationship invariant: every edge endpoint MUST be a cast member.
    kept_names = set()
    for a in kept:
        kept_names.add(_cast_norm(a.get("name")))
        for alias in (a.get("aliases") or []):
            kept_names.add(_cast_norm(alias))
    kept_names.discard("")
    rels = obj.get("relationships")
    rels_dropped = 0
    if isinstance(rels, list) and rels:
        kept_rels = [
            r for r in rels
            if isinstance(r, dict)
            and _cast_norm(r.get("source")) in kept_names
            and _cast_norm(r.get("target")) in kept_names
        ]
        rels_dropped = len(rels) - len(kept_rels)
        if rels_dropped:
            obj["relationships"] = kept_rels

    if truncated:
        meta["actors_truncated_from"] = original_n
    if demoted:
        meta["actors_media_demoted"] = len(demoted)
    if rels_dropped:
        meta["relationships_dropped_offcast"] = rels_dropped
    if plog is not None:
        plog.write(
            "ok",
            f"actor-cast discipline: {original_n} extracted → {len(kept)} main actors kept "
            f"(cap={cap}, media demoted={len(demoted)}, rank-cut={len(truncated)}, "
            f"off-cast edges dropped={rels_dropped})",
        )


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


def _market_pricing_prompt_block() -> str:
    """PM-4: 若已注入『当前市场定价』块（初始 Polymarket 快照）则返回它+空行，否则空串。"""
    return (_MARKET_PRICING_BLOCK + "\n\n") if _MARKET_PRICING_BLOCK.strip() else ""


# AGENTIC-SEARCH: TRUE agentic delegation 指令块。仅当 (a) 本 run 以 --subagents 启动
# （_AGENTIC_DELEGATION，client 侧已 subagent_enabled=True、harness `task` 工具可用）且
# (b) env RESEARCH_AGENTIC_SEARCH（默认 true）为真时返回指令文本+空行，否则空串（逐字节
# 不改无委派路径）。指令告诉本回合的 lead agent：把 breadth 工作（逐 actor/逐 KIQ/语言与
# 地域 pivot/反证搜寻）拆成 3–5 个 single-focus 并行 scoped-researcher 子任务，收回后先
# 校验（子代理会出错——对承重声明逐条对 URL 抽检）再并入自己的笔记；synthesis / 承重声明
# 的证据分级 / 来源账本永不外包。中英双语版本对齐两类提示词（英文 pass 提示 / 中文 top-up）。
def _agentic_delegation_enabled() -> bool:
    return _AGENTIC_DELEGATION and _env_flag("RESEARCH_AGENTIC_SEARCH", True)


def _agentic_delegation_block(chinese: bool = False) -> str:
    if not _agentic_delegation_enabled():
        return ""
    if chinese:
        return (
            "主动委派（AGENTIC DELEGATION）——你有一个 `task` 工具，接的是 `scoped-researcher` "
            "子代理（并行的定域网页调查员）。用它做 BREADTH（广度）而非判断深度：\n"
            "- 委派（一次派 3–5 个并行子任务，每个一个 SINGLE-FOCUS 简报）：逐 actor 画像、逐 KIQ 取证扫、"
            "地域/其它语言的来源 pivot、以及反证搜寻（为某条承重声明找最强的反面证据）。\n"
            "- 每份简报只问 ONE 个问题 + 你期望的来源类别（一手申报/监管·官方/本地语言媒体/数据集），"
            "并要求回传带分级的证据笔记 + 真实已抓取 URL 列表——不要成稿报告。\n"
            "- 永不委派：最终合成、承重声明的证据分级、来源账本——这些留在你手里。\n"
            "- 子代理会出错甚至臆造：采纳前先校验——对每个承重的数字/引述逐条比对其 URL，再并入你自己的笔记。\n\n"
        )
    return (
        "AGENTIC DELEGATION — you have a `task` tool wired to `scoped-researcher` sub-agents "
        "(parallel scoped web investigators). Use it for BREADTH, not depth-of-judgment:\n"
        "- DELEGATE (dispatch 3–5 parallel tasks, each a tight SINGLE-FOCUS brief): per-actor "
        "profiles, per-KIQ evidence sweeps, regional / other-language source pivots, and "
        "disconfirmation hunts (find the strongest case AGAINST a load-bearing claim).\n"
        "- Each brief states ONE question + the source classes you expect (primary filings, "
        "regulator/official, local-language press, datasets) and asks for GRADED evidence notes "
        "+ a real fetched-URL list — NOT a polished report.\n"
        "- NEVER delegate the final synthesis, the evidence grading of load-bearing claims, or "
        "the source ledger — those stay with you.\n"
        "- Sub-agents can err or hallucinate: VERIFY before adopting — spot-check every "
        "load-bearing number/quote against its cited URL, then integrate their notes into your own.\n\n"
    )


# ---------------------------------------------------------------------------
# WAVE9-RQ: DOSSIER STYLE + CITATION CONTRACT —— 卷宗是客户交付物，不是流程日志。
# 三条治理线（各自独立 env 开关，关闭即与旧提示词/旧行为逐字节一致，degrade-safe）：
#   RQ1 禁流程叙事：三次真实运行都把「Pass N working notes」泄进成稿散文——所有合成/
#       分节提示词注入硬风格规则（禁 passes/working notes/tracks/coverage gates/字数目标/
#       [citation:...] 语法/自指编辑注记）。
#   RQ2 内联引注：研究阶段就定义 [S<n>] 记号 + 机器可解析 '## References' 节；合成路径
#       钉一个与 sources.json fetched 主干同序的 SOURCE INDEX 进提示词，落盘前跑确定性
#       校验（悬空记号剔除并计数，参考节缺失时确定性补齐）。
#   RQ3 跨节去重：多段合成的缝合步跑归一化 12-gram shingle 检测，剔除跨节逐字重复段。
# ---------------------------------------------------------------------------


def _ban_process_narration() -> bool:
    """WAVE9-RQ1: 禁流程叙事硬规则开关（默认开）。=false → 各提示词与旧文本逐字节一致。"""
    return _env_flag("RESEARCH_BAN_PROCESS_NARRATION", True)


def _inline_citations_enabled() -> bool:
    """WAVE9-RQ2: 内联引注（[S<n>] + References 节 + 落盘前校验）开关（默认开）。"""
    return _env_flag("RESEARCH_INLINE_CITATIONS", True)


def _dedup_shingles_enabled() -> bool:
    """WAVE9-RQ3: 多段合成跨节 shingle 去重开关（默认开）。"""
    return _env_flag("RESEARCH_DEDUP_SHINGLES", True)


def _citation_index_cap() -> int:
    """WAVE9-RQ2: 钉进合成提示词的引注索引条数上限（防提示词膨胀）。非法/非正值回退 100。"""
    try:
        v = int(os.environ.get("RESEARCH_CITATION_INDEX_MAX", "100") or "100")
    except ValueError:
        v = 100
    return v if v > 0 else 100


def _research_charts_min() -> int:
    """WAVE9-RQ4: 卷宗最少图表数（actor 网络 / 时间线 / 定量 Top 指标）。0 = 关闭强制图表
    （write-step 提示词回退旧 OPTIONAL 措辞，确定性渲染步跳过）。非法值回退 3。"""
    try:
        v = int(os.environ.get("RESEARCH_CHARTS_MIN", "3") or "3")
    except ValueError:
        v = 3
    return max(0, v)


def _dossier_style_rules_block() -> str:
    """WAVE9-RQ1: 卷宗散文的硬风格规则块（注入所有写作/合成提示词）。开关关 → 空串。"""
    if not _ban_process_narration():
        return ""
    return (
        "\n\nHARD STYLE RULES — the dossier is a client-facing deliverable; violations are defects:\n"
        "- NEVER reference the research process in dossier prose: no mention of research "
        "passes ('Pass 3', 'Pass 2 working notes'), working notes, tracks, coverage gates, "
        "word counts/targets, prompts, or any internal tooling. The reader must see only "
        "subject-matter analysis, never how it was produced.\n"
        "- NEVER use [citation:...](url) syntax anywhere. Cite sources by name and date in "
        "prose (e.g. (Reuters, 2026-05-12)) or with [S<n>] reference markers where a source "
        "index is provided.\n"
        "- NEVER include self-referential editing notes such as 'corrected attribution', "
        "'was mislabeled in early passes', or 'as flagged in a previous draft'. Fix the text "
        "silently — do not narrate the fix."
    )


# WAVE9-RQ2: 引注记号与参考节的确定性解析（纯函数，均可离线单测）。
_CITATION_MARKER_RE = re.compile(r"\[\s*S(\d+)\s*\]")
_REFERENCES_HEADING_RE = re.compile(r"^#{2,3}\s*(?:References|参考来源|参考文献)\s*$", re.IGNORECASE)
# 参考条目行：'- [S3] ...' / '[S3] ...' / '3. ...' / '3) ...' 四种形态都认。
_REFERENCE_ENTRY_RE = re.compile(r"^\s*(?:[-*+]\s*)?(?:\[\s*S(\d+)\s*\]|(\d+)[.)])\s*(\S.*)$")


def build_citation_index(fetched: "list[dict]", cap: int = 100) -> "list[dict]":
    """WAVE9-RQ2（纯函数）：从已抓取来源表构造 [S<n>] 引注索引。

    与 :func:`merge_fetched_into_sources` 的 fetched 主干同谓词/同顺序（合法 http URL、
    按规整化 URL 去重、剔除 ok=False 的死抓取），因此确定性参考节的编号顺序与
    sources.json 的 fetched 主干结构性对齐。行形态 {"n": 1 起的序号, "title", "url"}。
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for f in fetched or []:
        if not isinstance(f, dict):
            continue
        u = _norm_url(f.get("url"))
        if not _is_valid_http_url(u) or u in seen or f.get("ok") is False:
            continue
        seen.add(u)
        title = str(f.get("title") or "").strip() or _title_from_url(u)
        entries.append({"n": len(entries) + 1, "title": title, "url": u})
        if len(entries) >= max(1, int(cap)):
            break
    return entries


def render_citation_index_block(entries: "list[dict]") -> str:
    """WAVE9-RQ2: 渲染钉进合成提示词的 CITATION CONVENTION + SOURCE INDEX 块。空索引 → 空串。"""
    if not entries:
        return ""
    lines = "\n".join(f"[S{e['n']}] {e['title']} — {e['url']}" for e in entries)
    return (
        "\n\nCITATION CONVENTION (mandatory): tag every load-bearing claim, figure, and "
        "quote inline with a marker [S<n>] drawn ONLY from the SOURCE INDEX below, e.g. "
        "'TSMC guided 2026 capex to $54B [S7].'. Never invent an index number, never use "
        "[citation:...] syntax, and do NOT write your own references/sources section — "
        "the pipeline appends a machine-parsable '## References' section from these exact "
        "numbers after assembly.\n"
        "=== SOURCE INDEX (the ONLY valid [S<n>] targets) ===\n"
        f"{lines}"
    )


def parse_references_section(report: str) -> "dict[int, str]":
    """WAVE9-RQ2（纯函数）：解析报告的 '## References'（或中文「参考来源/参考文献」）节
    → {编号: 条目文本}。节缺失/无可解析条目 → {}。遇到下一个 H1-H3 标题即视为节结束。"""
    refs: dict[int, str] = {}
    in_refs = False
    for ln in (report or "").splitlines():
        stripped = ln.strip()
        if _REFERENCES_HEADING_RE.match(stripped):
            in_refs = True
            continue
        if in_refs:
            if re.match(r"^#{1,3}\s+\S", stripped):
                break  # 下一个标题 → 参考节结束
            m = _REFERENCE_ENTRY_RE.match(ln)
            if m:
                try:
                    n = int(m.group(1) or m.group(2))
                except (TypeError, ValueError):
                    continue
                refs[n] = m.group(3).strip()
    return refs


def strip_dangling_citation_markers(report: str, valid: "set[int]") -> "tuple[str, int, int]":
    """WAVE9-RQ2（纯函数）：剔除解析不到参考条目的 [S<n>] 记号（连同前导空白，保留可解析
    的记号原样）。返回 (新文本, 保留记号数, 剔除记号数)。valid 为空集 → 剔除全部记号。"""
    kept = 0
    stripped = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal kept, stripped
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            n = -1
        if n in valid:
            kept += 1
            return m.group(0)
        stripped += 1
        return ""

    out = re.sub(r"[ \t]*\[\s*S(\d+)\s*\]", _sub, report or "")
    return out, kept, stripped


def render_references_section(entries: "list[dict]", cited: "set[int] | list[int]") -> str:
    """WAVE9-RQ2（纯函数）：从钉住的引注索引渲染机器可解析的 '## References' 节 —— 只列
    被正文引用的条目，保留 [S<n>] 原编号（与正文记号 1:1，且与 sources.json fetched
    主干顺序对齐）。无可列条目 → 空串。"""
    by_n = {e.get("n"): e for e in entries or [] if isinstance(e, dict)}
    rows = [
        f"- [S{n}] {by_n[n].get('title') or 'source'} — {by_n[n].get('url') or ''}".rstrip(" —")
        for n in sorted(set(int(c) for c in cited))
        if n in by_n
    ]
    if not rows:
        return ""
    return "## References\n\n" + "\n".join(rows)


def finalize_report_citations(report: str, plog: "ProgressLog") -> str:
    """WAVE9-RQ2: 落盘前的确定性引注校验/修复（合成后跑，幂等）。

    1) 报告自带 References 节 → 以该节为准校验记号，剔悬空并计数。
    2) 无 References 节但本轮钉过 SOURCE INDEX → 按索引校验、剔越界记号，并确定性
       补一个只含被引条目的 '## References' 节（编号与索引/sources.json 主干对齐）。
    3) 两者皆无（模型自造编号）→ 诚实降级：剔除全部 [S<n>] 记号并告警。
    开关关 / 无记号 → 原样返回。任何路径都只做确定性文本操作，不发 LLM 调用。
    """
    if not _inline_citations_enabled() or not (report or "").strip():
        return report
    markers = [int(n) for n in _CITATION_MARKER_RE.findall(report)]
    if not markers:
        plog.write("stage", "citations: no inline [S<n>] markers in the report; nothing to validate")
        return report
    refs = parse_references_section(report)
    if refs:
        out, kept, dangling = strip_dangling_citation_markers(report, set(refs))
        plog.write("warn" if dangling else "ok",
                   f"citations: {kept} marker(s) resolved against the report's References section; "
                   f"stripped {dangling} dangling marker(s)")
        return out
    if _PINNED_CITATION_INDEX:
        valid = {e.get("n") for e in _PINNED_CITATION_INDEX}
        out, kept, dangling = strip_dangling_citation_markers(report, valid)
        cited = sorted({n for n in markers if n in valid})
        section = render_references_section(_PINNED_CITATION_INDEX, cited) if cited else ""
        if section:
            out = out.rstrip() + "\n\n" + section + "\n"
        plog.write("warn" if dangling else "ok",
                   f"citations: {kept} marker(s) resolved against the pinned source index "
                   f"({len(cited)} distinct); stripped {dangling} dangling; "
                   f"{'appended deterministic References section' if section else 'no References section appended'}")
        return out
    out, _, dangling = strip_dangling_citation_markers(report, set())
    plog.write("warn", f"citations: {dangling} [S<n>] marker(s) had neither a References "
                       "section nor a pinned source index (model-invented numbering); stripped all")
    return out


def build_research_prompt(question: str, depth: str, target_language: str | None) -> str:
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
    lang_line = ""
    if target_language:
        lang_line = f"\n\nWrite the final report in {target_language}."
    if depth == "deep":
        return (
            "You are a deep-research lead analyst starting a MULTI-PASS investigation. "
            "This is pass 0: orient yourself, load the deep-research AND prediction-markets "
            "skills, and begin the source map. You will receive several follow-up "
            "research-pass prompts in this same thread before final synthesis.\n\n"
            "TOOLING: This is WEB research. Work via web_search and web_fetch ONLY. There is "
            "NO local file corpus, dataset, or workspace to inspect — do NOT call ls / "
            "read_file / glob / bash on the filesystem. If any filesystem/workspace tool "
            "returns a permission error, IGNORE it and go straight to web_search. Your "
            "evidence comes entirely from pages you fetch off the web.\n\n"
            f"RESEARCH BRIEF:\n{question}\n\n"
            f"{_market_pricing_prompt_block()}"
            f"{_agentic_delegation_block()}"
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
    # WAVE9-RQ2: standard 主路径由 agent 在同一条消息里自编号——[S<n>] 记号必须与其
    # 自写的机器可解析 '## References' 节 1:1 对应（落盘前 finalize_report_citations
    # 以该节为准校验）。开关关 → 空串，提示词与旧文本逐字节一致。
    citation_line = ""
    if _inline_citations_enabled():
        citation_line = (
            "CITATIONS: tag every load-bearing claim, figure, and quote inline with a "
            "marker [S<n>], and make item 6 a machine-parsable '## References' section "
            "whose entries are numbered to match — one line per source, formatted "
            "'<n>. <Title> — <publication date> — <URL>', listing ONLY sources you "
            "actually fetched. Every [S<n>] in the text MUST resolve to entry <n>; "
            "never use [citation:...] syntax.\n\n"
        )
    return (
        "You are a deep-research analyst. Use the deep-research methodology: search "
        "the web from multiple angles, fetch and read important primary sources in "
        "full, gather concrete data, real-world examples, expert opinion, opposing "
        "views, and current developments.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"{_market_pricing_prompt_block()}"
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
        f"{citation_line}"
        "LENGTH & DEPTH: This dossier is the sole ground-truth a downstream simulation "
        "will reason over, so it must be LONG and richly detailed — aim for at least "
        # SCALE-1: 3,500–6,000 → 6,000–10,000 —— standard 档的一手报告目标与多段合成的
        # 长度纪律对齐；短报告的结构性根因在合成侧，但一手 agent 回合也不该按几页纸交差。
        "6,000–10,000 words for standard depth. Organize it with clear Markdown section headings (##), and "
        "under each actor and topic go deep: specific numbers, dated events, direct "
        "quotes, competing perspectives, second-order effects, and concrete scenarios. "
        "Do NOT write a terse summary — exhaustive, well-structured coverage is the goal.\n\n"
        "IMPORTANT: Once you have gathered enough material across the angles above, you "
        "MUST stop calling tools and write the full report as your very next message. The "
        "written report is the deliverable — do not keep searching for marginal extra "
        "detail. A run that never writes the report has failed."
        f"{_dossier_style_rules_block()}"
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
        # SCALE-2: 12→20 —— 与 parse_gaps_from_notes/_merge_gaps 的缺口上限同步扩容，
        # ×1.5 预算下每 pass 有余力多收几条未决缺口。
        gap_lines = "\n".join(f"- {str(g)}" for g in list(prior_gaps)[:20])
        gap_block = (
            "UNRESOLVED GAPS carried from earlier passes — prioritize CLOSING these "
            "(search/fetch specifically for them) before broadening:\n"
            f"{gap_lines}\n\n"
        )
    # SKILL-1 / ITEM-7: 定量取证的 pass 指向 data-analysis 技能（表格/数列/口径核对），并把
    # agent 产出的图表落到 out_dir 的 charts/ + charts.json 清单（编排器工件通道会拾取）。
    # 两句都只在 host bash 执行面可用（sandbox.allow_host_bash）时注入——脚本型技能靠 bash 才能跑。
    _label = str(phase.get("label") or "")
    skill_line = ""
    if ("primary" in _label or "evidence" in _label) and _bash_sandbox_enabled():
        skill_line = (
            "For quantitative work on the numbers you gather in this pass — reconciling "
            "figures, fitting trends, computing reference-class base rates, checking sums "
            "and units/definitions — load the data-analysis skill and run the computations "
            "in the sandbox via bash (do NOT eyeball the arithmetic).\n"
            "If you produce any charts (via the forecast-visuals / chart-visualization "
            "skills), write the rendered files into a charts/ subdirectory of your output "
            "directory and register each in a charts.json manifest "
            "({title, caption, source_data}) so the pipeline's artifact channel picks them up.\n\n"
        )
    return (
        f"DEEP RESEARCH PASS {index}/{total}: {phase['label']}\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"{gap_block}"
        f"PASS OBJECTIVE:\n{phase['focus']}\n\n"
        f"{skill_line}"
        f"{_agentic_delegation_block()}"
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
        # WAVE9-RQ1: working notes 是内部脚手架——三次真实运行都把「Pass N working notes」
        # 泄进成稿散文，从 pass 阶段就声明这些标签绝不能作为最终卷宗的引用对象。
        + ("These working notes are INTERNAL SCAFFOLDING for later passes — the final "
           "dossier must never quote or reference them ('Pass N', 'working notes', "
           "'flagged in Pass 4') as if they were sources; attribute every claim to its "
           "real fetched source instead.\n\n" if _ban_process_narration() else "")
        + "Do NOT write the final report yet. Do NOT say the research is complete. "
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
        f"{_agentic_delegation_block()}"
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


def build_gap_closing_prompt(question: str, gaps: list | None, target_language: str | None) -> str:
    """SCALE-5: 一次**定向**收口 pass —— 覆盖门（源数量）已满足后，专门关闭仍未决的 gaps。

    与 build_coverage_topup_prompt（拓宽源面）互补：这里 DEPTH-first，只针对 judge/前序 pass
    留下的具体开放问题深挖并 FETCH，而非泛化拓源。同样要求以 ## Gaps 收尾，以便下游判断是否收敛。
    """
    lang_line = f"\n\nWrite your pass notes in {target_language}." if target_language else ""
    gap_block = "\n".join(f"- {str(g)}" for g in list(gaps or [])[:14]) or "- (see brief; close the most load-bearing open questions)"
    return (
        "TARGETED GAP-CLOSING PASS — resolve the specific open questions below.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        "The evidence base is wide enough, but these gaps are still unresolved and are the "
        "highest-value thing left to nail down:\n"
        f"{gap_block}\n\n"
        f"{_agentic_delegation_block()}"
        "OBJECTIVE: for EACH gap, run focused searches and FETCH the primary/high-authority "
        "pages that would actually close it (a specific number with its as-of date and unit, a "
        "named actor's stated position, a dated event, a corroborating or refuting source). Go "
        "DEEP on these specific questions rather than broadening to new topics.\n\n"
        "HARD RULES:\n"
        "- If a gap is genuinely unanswerable after a real attempt, say so explicitly and drop "
        "it — do NOT thrash on it with re-quoted near-duplicate searches.\n"
        "- Only record sources you ACTUALLY FETCHED, with their real URL and on-page date. Never "
        "fabricate or future-date a source.\n\n"
        "End with the same Markdown working-note headings as the deep passes "
        "(## Evidence gathered / ## Actor … / ## Quantitative facts … / ## Contradictions … / "
        "## Gaps to carry into the next pass) — list ONLY genuinely still-open gaps under the "
        "Gaps heading (an empty gaps section signals you closed them). Do NOT write the final report yet."
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
    # SCALE-1: deep 8,000–12,000 → 15,000–25,000 —— 本提示词只服务单调用回退路径
    # （multipart 关闭 / 大纲解析失败时），deep 主路径改走逐节多段合成，各节自带
    # 1,500–2,500 词目标；单调用回退也不该再按旧的缩水目标写。standard 保持原目标。
    word_target = "15,000–25,000 words" if depth == "deep" else "3,500–6,000 words"
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
    # ITEM-7: 图表落盘句——只在 host bash 执行面可用（sandbox.allow_host_bash）时追加，且明确
    # 把「渲染图表」框定为本写作步唯一允许的工具动作（仍禁止 search/fetch），故不重开研究之门。
    # bash 关闭时 charts_line 为空串 → 合成提示词与今日逐字节一致。
    # WAVE9-RQ4: RESEARCH_CHARTS_MIN>0（默认 3）时从「OPTIONAL」升级为硬性最少图表数
    # （actor 网络 / 时间线 / 定量 Top 指标），并要求以 ![](charts/x.png) 内嵌进卷宗；
    # =0 回退旧 OPTIONAL 措辞。渲染失败一律降级续写，绝不阻断报告。
    charts_line = ""
    if _bash_sandbox_enabled():
        _charts_min = _research_charts_min()
        if _charts_min > 0:
            charts_line = (
                f"\n\nREQUIRED CHARTS — the dossier MUST embed at least {_charts_min} charts "
                "as markdown images: (1) the actor relationship network, (2) the dated event "
                "timeline, (3) the top quantitative metrics. The ONLY tool use permitted in "
                "this write step is rendering them from data you ALREADY gathered above (do "
                "NOT search or fetch): load the forecast-visuals skill and run its bundled "
                "scripts/render.py, which writes self-contained charts/*.html + charts/*.png "
                "plus a charts.json manifest ({title, caption, source_data, path}) into your "
                "output directory; then reference each figure inline where it belongs, e.g. "
                "![Actor network](charts/actor_network.png). If python/plotly is unavailable "
                "or a render fails, fall back to the skill's mermaid/table fallback and KEEP "
                "WRITING — a missing chart must never block the dossier, and the failure must "
                "never be mentioned in the dossier prose."
            )
        else:
            charts_line = (
                "\n\nOPTIONAL CHARTS — the ONLY tool use permitted in this write step is "
                "rendering charts from data you ALREADY gathered above (via the forecast-visuals "
                "/ chart-visualization skills); do NOT search or fetch. If you render any, write "
                "the files into a charts/ subdirectory of your output directory and register each "
                "in a charts.json manifest ({title, caption, source_data}) so the pipeline's "
                "artifact channel picks them up."
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
        # SKILL-1: 明示输出长度是硬契约（合成侧对短报告零容忍），一句话保持紧凑。
        f"OUTPUT-LENGTH CONTRACT: {word_target} of prose is a hard requirement, not a "
        "suggestion — a dossier materially shorter than this is treated as a failed write.\n"
        "Write the report directly — no preamble, no tool calls."
        f"{_dossier_style_rules_block()}"
        f"{charts_line}"
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


# ---------------------------------------------------------------------------
# SCALE-1: MULTI-PART SYNTHESIS — the structural fix for short reports.
# A single bare-model call physically cannot write a 15k+ word dossier (output
# token ceilings + attention collapse over a 650K-char context head-truncate the
# evidence AND the prose). Split the write into: (1) one OUTLINE call emitting a
# JSON section plan; (2) one bare call PER SECTION, run in parallel (mirrors
# run_deep_fanout's ThreadPoolExecutor pattern), each fed a KEYWORD-SHARDED slice
# of the gathered context (highest-scoring blocks for that section's scope, not
# head-truncation) plus the requirement text and working-notes digests; (3) a
# deterministic stitch in outline order under '## ' headings + ONE light call for
# the executive summary / cross-references; (4) a prose-word LENGTH GATE that
# re-expands the thinnest sections. Gated by RESEARCH_MULTIPART_SYNTHESIS
# (unset → on for deep only); ANY structural failure falls back to today's
# single-call path and is recorded in _RESEARCH_FLAGS (failure honesty).
# ---------------------------------------------------------------------------


def _multipart_synthesis_enabled(depth: str) -> bool:
    """SCALE-1: 多段合成开关。env 显式设置对所有深度一律生效；未设置 → 仅 deep 开
    （quick/standard 保持单调用现状，成本/行为与今天逐字节一致）。"""
    raw = (os.environ.get("RESEARCH_MULTIPART_SYNTHESIS", "") or "").strip()
    if raw:
        return raw.lower() in ("1", "true", "yes", "on")
    return depth == "deep"


def _synthesis_workers() -> int:
    """SCALE-1: 逐节合成的并发上限（镜像 RESEARCH_FANOUT_WIDTH 的读法；非法值回退 4）。"""
    try:
        return max(1, int(os.environ.get("RESEARCH_SYNTHESIS_WORKERS", "4") or "4"))
    except ValueError:
        return 4


def _synthesis_min_words(depth: str) -> int:
    """SCALE-1: 长度门的最少散文词数。env 显式设置对所有深度生效（0=关闭）；未设置 →
    deep 9000 / 其余 4500（standard 的 6,000–10,000 词目标留出下限余量）。"""
    raw = (os.environ.get("RESEARCH_SYNTHESIS_MIN_WORDS", "") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 9000 if depth == "deep" else 4500


def build_synthesis_outline_prompt(question: str, target_language: str | None) -> str:
    """SCALE-1 大纲调用提示词：只要一个 JSON 分节计划，不要任何报告正文。"""
    lang_line = f"\nSection titles and scopes must be in {target_language}." if target_language else ""
    return (
        "You are planning a LONG multi-section research dossier that will be written "
        "one section at a time by parallel writers. Do NOT write the dossier now.\n\n"
        f"RESEARCH BRIEF (what the dossier must answer):\n{question}\n\n"
        "From the gathered research below, emit ONLY a JSON object (no prose, no "
        "markdown fence commentary) with this exact shape:\n"
        '{"sections": [{"title": string, "scope": string, "target_words": int, "covers": [string]}]}\n\n'
        "REQUIREMENTS:\n"
        "- 10-14 sections that together cover EVERYTHING the brief demands: executive "
        "context, actors & relationships, timeline, quantitative evidence, contested "
        "claims, mechanisms/cause-effect chains, scenarios, contrarian view, leading "
        "indicators, and sources.\n"
        "- 'scope': 2-4 sentences of concrete keywords — the named actors, numbers, "
        "events, and questions THIS section must cover (used to route evidence to the "
        "section writer, so be specific).\n"
        "- 'target_words': 1500-2500 per section.\n"
        "- 'covers': the evidence clusters / key intelligence questions from the "
        "research this section is responsible for.\n"
        f"{lang_line}\n\n"
        "=== GATHERED RESEARCH (plan strictly from this) ===\n"
    )


def _parse_json_array(text: str) -> list | None:
    """SCALE-1（纯函数）：从模型输出里挖出第一个平衡的顶层 JSON 数组（字符串感知，
    引号内的方括号不会提前闭合）。extract_json_object 只认 dict，这里补上裸数组形态。"""
    if not text:
        return None
    start = text.find("[")
    if start == -1:
        return None
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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, list) else None
    return None


def parse_synthesis_outline(text: str) -> list[dict]:
    """SCALE-1（纯函数）：稳健解析大纲 JSON → 规范化的分节清单；解析失败返回 []
    （调用方据此回退单调用路径并打 _RESEARCH_FLAGS）。

    容错：``{"sections":[...]}`` / ``{"outline":[...]}`` / 裸顶层数组 / 围栏包裹均可；
    行内缺 title 的条目丢弃；target_words 钳到 1500-2500（缺失/非法 → 2000）；
    有效分节 <3 视为解析失败（一份 1-2 节的"大纲"不构成多段合成的骨架），>16 截断。
    """
    rows: list | None = None
    obj = extract_json_object(text or "")
    if isinstance(obj, dict):
        for key in ("sections", "outline"):
            if isinstance(obj.get(key), list):
                rows = obj[key]
                break
    if rows is None:
        rows = _parse_json_array(text or "")
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        if not title:
            continue
        scope = str(row.get("scope") or row.get("description") or "").strip() or title
        try:
            tw = int(row.get("target_words") or 0)
        except (TypeError, ValueError):
            tw = 0
        tw = min(2500, max(1500, tw)) if tw > 0 else 2000
        covers_raw = row.get("covers")
        covers = (
            [str(c).strip() for c in covers_raw if str(c).strip()][:12]
            if isinstance(covers_raw, list) else []
        )
        out.append({"title": title, "scope": scope, "target_words": tw, "covers": covers})
        if len(out) >= 16:
            break
    return out if len(out) >= 3 else []


# SCALE-1 关键词分片：scope 文本 → 计分词项。ASCII 词 ≥3 字符（去常见停用词），
# CJK 连续段 2-6 字直接作子串计数（中文 scope 无空格分词也能路由）。
_SCOPE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-'’]{2,}|[一-鿿]{2,6}")
_SCOPE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "will", "must", "section", "cover", "covers", "covering", "include", "includes",
    "including", "their", "them", "they", "its", "his", "her", "who", "what",
    "which", "how", "why", "when", "where", "each", "all", "any", "into", "over",
    "under", "between", "about", "these", "those", "such", "also", "than", "then",
    "not", "but", "has", "have", "had", "can", "could", "should", "would", "may",
})


def _scope_terms(scope_text: str) -> list[str]:
    """SCALE-1（纯函数）：从分节 scope 文本提取去重的小写计分词项（保序，上限 60）。"""
    terms: list[str] = []
    seen: set[str] = set()
    for tok in _SCOPE_TOKEN_RE.findall((scope_text or "").lower()):
        if tok in _SCOPE_STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)
        if len(terms) >= 60:
            break
    return terms


def score_block_for_scope(block: str, terms: list[str]) -> int:
    """SCALE-1（纯函数）：一个已收集上下文块对某分节 scope 的相关性得分。
    命中的**不同**词项权重远高于同一词项的重复出现（覆盖面 > 词频），单词项出现
    次数封顶 8 防大块刷分。0 = 完全不相关。"""
    if not block or not terms:
        return 0
    bl = block.lower()
    score = 0
    for t in terms:
        c = bl.count(t)
        if c:
            score += 10 + min(c, 8)
    return score


def pack_context_for_section(blocks: list[str], scope_text: str, cap: int) -> str:
    """SCALE-1（纯函数）：按 scope 关键词给每个上下文块计分，贪心装入得分最高的块
    直到 ``cap`` 字符 —— 取代头部截断，让每个分节看到**属于它的**证据。

    确定性：得分降序、同分按原始下标升序选块；选出的块按**原始顺序**输出（保持
    研究叙事的时间/线程顺序）。scope 提不出词项 → 退化为按原始顺序头部装填
    （等价于现状的头截断，degrade-safe）。装不下任何整块时截断得分最高的单块。
    """
    if not blocks or cap <= 0:
        return ""
    terms = _scope_terms(scope_text)
    if terms:
        order = sorted(range(len(blocks)), key=lambda i: (-score_block_for_scope(blocks[i], terms), i))
    else:
        order = list(range(len(blocks)))
    chosen: set[int] = set()
    used = 0
    for i in order:
        need = len(blocks[i]) + (2 if chosen else 0)  # 计入 "\n\n" 接缝
        if used + need > cap:
            continue
        chosen.add(i)
        used += need
    if not chosen:
        return blocks[order[0]][:cap]
    return "\n\n".join(blocks[i] for i in sorted(chosen))


def build_notes_digest(ai_parts: list[str], per_note_chars: int = 600, total_cap: int = 9000) -> str:
    """SCALE-1（纯函数）：working-notes 摘要 —— 每条 AI 笔记取头部 ``per_note_chars``
    字符压成一行 bullet，总量封顶 ``total_cap``。每个分节调用都随附（连同需求原文），
    保证分片没选中的 pass 笔记也以摘要形态在场。"""
    out: list[str] = []
    used = 0
    for note in ai_parts:
        n = " ".join((note or "").split())
        if not n:
            continue
        snip = n[:per_note_chars]
        if used + len(snip) > total_cap:
            break
        out.append(f"- {snip}")
        used += len(snip)
    return "\n".join(out)


# SCALE-1 散文词数统计：剔除表格行 / URL / 代码围栏后计词 —— 表格和链接堆不出
# 「长报告」，长度门只认真正的散文。CJK 每字计 1 词（中文报告无空格分词）。
_PROSE_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_PROSE_URL_RE = re.compile(r"https?://\S+")
_PROSE_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_PROSE_CJK_RE = re.compile(r"[一-鿿]")


def count_prose_words(text: str) -> int:
    """SCALE-1（纯函数）：报告的散文词数（表格行/URL/代码围栏不计入）。"""
    if not text:
        return 0
    t = _PROSE_FENCE_RE.sub(" ", text)
    t = "\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("|"))
    t = _PROSE_URL_RE.sub(" ", t)
    return len(_PROSE_WORD_RE.findall(t)) + len(_PROSE_CJK_RE.findall(t))


# WAVE9-RQ3: 跨节去重 —— 归一化 12-gram shingle。分词：ASCII 词一个 token，CJK 逐字
# 一个 token（中文段无空格也能成 shingle）。
_SHINGLE_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")


def paragraph_shingles(text: str, n: int = 12) -> "set[str]":
    """WAVE9-RQ3（纯函数）：段落的归一化 n-gram shingle 集（小写、去标点）。token 数
    不足 n → 空集（太短的段不参与判重，避免误杀短小过渡句）。"""
    toks = _SHINGLE_TOKEN_RE.findall((text or "").lower())
    if len(toks) < max(2, int(n)):
        return set()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def dedup_cross_section_paragraphs(texts: "list[str]", n: int = 12,
                                   threshold: float = 0.75) -> "tuple[list[str], int]":
    """WAVE9-RQ3（纯函数）：跨节逐字重复段检测/剔除（保首现）。

    按节序处理：段落（空行分隔）的 shingle 集与**此前各节**已见 shingle 的重叠率
    ≥ ``threshold`` → 判为跨节 verbatim 重复，剔除该段。标题（#）/表格行（|）/含代码
    围栏的段不参与判重（结构性内容不是散文重复）。同节内的重复不动——那是写手自己
    的结构选择，判重只跨节。返回 (新 texts, 剔除段数)。确定性、无 LLM 调用。
    """
    seen: set[str] = set()
    removed = 0
    out: list[str] = []
    for t in texts:
        if not (t or "").strip():
            out.append(t)
            continue
        paras = re.split(r"\n{2,}", t)
        kept: list[str] = []
        section_sh: set[str] = set()
        for p in paras:
            ps = p.strip()
            sh: set[str] = set()
            if ps and not ps.startswith("#") and not ps.startswith("|") and "```" not in ps:
                sh = paragraph_shingles(ps, n)
            if sh and seen and len(sh & seen) / len(sh) >= threshold:
                removed += 1
                continue
            kept.append(p)
            section_sh |= sh
        seen |= section_sh
        out.append("\n\n".join(kept))
    return out, removed


def stitch_synthesis_sections(outline: list[dict], texts: list[str]) -> str:
    """SCALE-1（纯函数）：按大纲顺序确定性拼接分节 —— 每节冠 '## <title>' 标题；
    空节跳过；分节正文若以重复大纲标题的 markdown 标题开头则去掉那一行（写手常
    自带标题，避免 '## X' 下再来一个 '## X'）。并发完成顺序不影响输出顺序。"""
    parts: list[str] = []
    for sec, txt in zip(outline, texts):
        body = (txt or "").strip()
        if not body:
            continue
        first, _, rest = body.partition("\n")
        h = re.match(r"^\s*#{1,6}\s+(.*\S)", first)
        if h and h.group(1).strip().strip("*# ").lower() == str(sec.get("title", "")).strip().lower():
            body = rest.strip()
        parts.append(f"## {sec['title']}\n\n{body}")
    return "\n\n".join(parts)


def select_thinnest_sections(outline: list[dict], texts: list[str], k: int = 2) -> list[int]:
    """SCALE-1（纯函数）：返回散文词数最少的 ``k`` 个**非空**分节下标（词数升序、
    同词数按下标升序 —— 确定性），供长度门定向再扩写。"""
    idx = [i for i in range(min(len(outline), len(texts))) if (texts[i] or "").strip()]
    idx.sort(key=lambda i: (count_prose_words(texts[i]), i))
    return idx[:max(0, k)]


def _section_lead(text: str, max_words: int = 200) -> str:
    """SCALE-1（纯函数）：分节开头 ~200 词（供执笔执行摘要的轻量调用，不喂全文）。"""
    words = (text or "").split()
    return " ".join(words[:max_words])


def build_synthesis_section_prompt(question: str, outline: list[dict], section: dict,
                                   index: int, total: int, notes_digest: str,
                                   context: str, target_language: str | None,
                                   citation_block: str = "") -> str:
    """SCALE-1 分节调用提示词：大纲全貌 + 本节任务书 + 需求原文 + 笔记摘要 + 分片证据。

    WAVE9-RQ: ``citation_block``（钉住的 SOURCE INDEX，可空）+ 硬风格规则 + 头条数字
    纪律 —— 三次真实运行的流程叙事泄漏 / 跨节重复陈述头条数字都在这一层修。
    """
    lang_line = f"\nWrite this section in {target_language}." if target_language else ""
    outline_lines = "\n".join(
        f"{i + 1}. {s['title']}" + (" ← YOU ARE WRITING THIS ONE" if i == index else "")
        for i, s in enumerate(outline)
    )
    covers_line = ("\nEVIDENCE / KEY QUESTIONS THIS SECTION COVERS:\n" +
                   "\n".join(f"- {c}" for c in section.get("covers", []))) if section.get("covers") else ""
    # WAVE9-RQ1: 摘要块标头明示 INTERNAL——digest 里的「Pass N」字样绝不能进成稿散文。
    digest_block = (
        "\n\n=== WORKING-NOTES DIGEST (INTERNAL — one line per research pass; use the "
        "facts, but NEVER cite, quote, or mention these notes/passes in the section "
        f"text) ===\n{notes_digest}"
        if notes_digest else "")
    return (
        f"You are writing SECTION {index + 1} of {total} of a long research dossier. "
        "Other writers handle the other sections in parallel — write ONLY yours.\n\n"
        f"RESEARCH BRIEF (the dossier's overall requirement):\n{question}\n\n"
        f"FULL DOSSIER OUTLINE (for coherence — do not write the other sections):\n{outline_lines}\n\n"
        f"YOUR SECTION: {section['title']}\n"
        f"SCOPE: {section['scope']}\n"
        f"TARGET LENGTH: about {section['target_words']} words of dense analytical prose."
        f"{covers_line}\n\n"
        "RULES:\n"
        "- Base EVERY claim strictly on the gathered research below; never invent facts, "
        "numbers, quotes, or sources. Attribute figures and quotes to their sources.\n"
        "- Start directly with the section body (use ### sub-headings inside if useful); "
        "do NOT repeat the section title as a heading, do NOT write an intro for the "
        "whole dossier, no preamble, no meta-commentary.\n"
        "- Go deep: specific numbers with units and as-of dates, dated events, named "
        "actors and their incentives, competing views, second-order effects.\n"
        "- Do not summarize other sections' territory; a one-line cross-reference is fine.\n"
        # WAVE9-RQ3: 头条数字纪律 + 跨节矛盾守卫——同一头条数字只在「所属节」完整展开一次，
        # 其余节短引；证据相左时显式呈现分歧，绝不在不同节里静默给出互相矛盾的数字。
        "- HEADLINE-NUMBER DISCIPLINE: a headline figure (the dossier's top-line forecast "
        "numbers, market sizes, key probabilities) may be stated IN FULL — with derivation "
        "and context — only in the ONE section that owns that topic per the outline. If "
        "your section is not the owner, reference it briefly (e.g. 'the ~$1.5T base case; "
        "see the scenarios section') without re-deriving it, and NEVER state a variant "
        "that contradicts the owning section — when your evidence disagrees, present the "
        "disagreement explicitly with both sources instead of silently picking a different "
        "number."
        f"{_dossier_style_rules_block()}"
        f"{citation_block}"
        f"{lang_line}"
        f"{digest_block}\n\n"
        "=== GATHERED RESEARCH FOR THIS SECTION (write ONLY from this) ===\n"
        f"{context}"
    )


def build_synthesis_expand_prompt(question: str, section: dict, current_text: str,
                                  context: str, target_language: str | None) -> str:
    """SCALE-1 长度门再扩写提示词：给出该节现稿 + 其分片证据，要求成倍加深。"""
    lang_line = f"\nWrite in {target_language}." if target_language else ""
    return (
        "The dossier section below came back too thin. REWRITE it substantially longer "
        "and deeper — roughly DOUBLE its current length, and at least "
        f"{section['target_words']} words of analytical prose — while keeping every "
        "existing fact. Add the depth from the gathered research: more numbers with "
        "units/dates, more dated events, more named actors and incentives, competing "
        "views, and second-order effects. NEVER invent facts or sources. Keep every "
        "existing [S<n>] citation marker attached to its claim. Start directly "
        "with the section body; do not repeat the section title as a heading."
        f"{_dossier_style_rules_block()}"
        f"{lang_line}\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"SECTION: {section['title']}\nSCOPE: {section['scope']}\n\n"
        f"=== CURRENT (too thin) SECTION TEXT ===\n{current_text}\n\n"
        f"=== GATHERED RESEARCH FOR THIS SECTION ===\n{context}"
    )


def build_synthesis_summary_prompt(question: str, leads: list[tuple[str, str]],
                                   target_language: str | None) -> str:
    """SCALE-1 缝合后的轻量调用：只喂分节清单 + 每节开头 ~200 词（不是全文），
    产出执行摘要 + 跨节引用/论题串联。"""
    lang_line = f"\nWrite it in {target_language}." if target_language else ""
    lead_block = "\n\n".join(f"### {t}\n{lead}" for t, lead in leads)
    return (
        "A long multi-section research dossier has just been assembled. Below are its "
        "section titles and the first ~200 words of each section. Write the dossier's "
        "opening block ONLY:\n"
        "- '## Executive Summary' (600-900 words): the load-bearing THESIS sentence the "
        "dossier defends, the key findings, and the main forecast with its basis.\n"
        "- '## How to Read This Dossier' (a short paragraph plus bullets): cross-references "
        "tying the sections together — which sections carry the evidence for which claims.\n"
        "Do NOT rewrite or summarize each section one by one, do NOT invent facts not "
        "implied below, and do NOT output anything after these two blocks."
        f"{_dossier_style_rules_block()}"
        f"{lang_line}\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"=== SECTION OPENINGS ===\n{lead_block}"
    )


def _bare_synth_invoke(synth_model: str, prompt: str) -> str:
    """SCALE-1: 一次裸模型（无工具、无思考）调用 → strip_think 后的纯文本。
    与 synthesize_from_thread 的单调用路径同一套 create_chat_model 机制；每次调用
    独立建模型实例，供 ThreadPoolExecutor 的并发分节调用安全复用。"""
    from deerflow.models import create_chat_model
    from langchain_core.messages import HumanMessage

    model = create_chat_model(synth_model, thinking_enabled=False)
    resp = model.invoke([HumanMessage(content=prompt)])
    return _message_text(getattr(resp, "content", resp))


def synthesize_multipart(question: str, target_language: str | None, depth: str,
                         synth_model: str, blocks: list[str], ai_parts: list[str],
                         context: str, plog: "ProgressLog") -> str:
    """SCALE-1 多段合成主流程。返回 '' 表示结构性失败（大纲解析失败/过半分节空），
    调用方回退到今天的单调用路径；所有降级均已写入 _RESEARCH_FLAGS。"""
    import concurrent.futures as _cf

    # (1) OUTLINE —— 一次裸调用产出 JSON 分节计划（喂已截断的 gathered context）。
    plog.write("stage", "synthesize/multipart: requesting section outline (10-14 sections)")
    outline_raw = _bare_synth_invoke(synth_model, build_synthesis_outline_prompt(question, target_language) + context)
    outline = parse_synthesis_outline(outline_raw)
    if not outline:
        plog.write("warn", f"synthesize/multipart: outline JSON unparseable ({len(outline_raw)} chars); falling back to single-call synthesis")
        _flag_research_degradation("multipart synthesis: outline JSON unparseable; fell back to single-call synthesis")
        return ""
    plog.write("stage", f"synthesize/multipart: outline parsed — {len(outline)} sections: " + ", ".join(s["title"][:40] for s in outline))

    # (2) SECTION CALLS —— 逐节并行裸调用（镜像 run_deep_fanout 的执行器模式）。
    # 每节吃：大纲全貌 + 本节任务书 + 需求原文 + working-notes 摘要 + 关键词分片证据。
    notes_digest = build_notes_digest(ai_parts)
    # WAVE9-RQ2: 并行写手无法互相协调编号 —— 由管线钉一个共享 SOURCE INDEX（与
    # sources.json fetched 主干同序）进每个分节提示词，[S<n>] 编号全局一致；落盘前
    # finalize_report_citations 据同一索引校验并确定性补 '## References' 节。
    citation_block = ""
    if _inline_citations_enabled():
        _cit_entries = build_citation_index(_FETCHED_SOURCES, _citation_index_cap())
        if _cit_entries:
            _set_pinned_citation_index(_cit_entries)
            citation_block = render_citation_index_block(_cit_entries)
            plog.write("stage", f"synthesize/multipart: pinned citation index ({len(_cit_entries)} fetched sources) into section prompts")
    cap = _synthesis_context_cap(synth_model)
    section_cap = max(20000, cap - len(notes_digest) - len(citation_block) - 6000)  # 给提示词骨架+摘要+索引留余量
    workers = min(_synthesis_workers(), len(outline))
    texts: list[str] = [""] * len(outline)

    def _write_section(i: int) -> str:
        sec = outline[i]
        scope_text = " ".join([sec["title"], sec["scope"], " ".join(sec["covers"])])
        sec_ctx = pack_context_for_section(blocks, scope_text, section_cap)
        return _bare_synth_invoke(synth_model, build_synthesis_section_prompt(
            question, outline, sec, i, len(outline), notes_digest, sec_ctx, target_language,
            citation_block=citation_block))

    plog.write("stage", f"synthesize/multipart: writing {len(outline)} sections in parallel (workers={workers})")
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_write_section, i): i for i in range(len(outline))}
        for fut in _cf.as_completed(futs):
            i = futs[fut]
            try:
                texts[i] = (fut.result() or "").strip()
            except Exception as exc:  # noqa: BLE001 — 单节失败不拖垮整份报告
                plog.write("warn", f"synthesize/multipart: section '{outline[i]['title']}' failed ({type(exc).__name__}: {exc})")
                texts[i] = ""
    written = sum(1 for t in texts if t)
    if written < max(3, (len(outline) + 1) // 2):
        plog.write("warn", f"synthesize/multipart: only {written}/{len(outline)} sections produced text; falling back to single-call synthesis")
        _flag_research_degradation(f"multipart synthesis: only {written}/{len(outline)} sections written; fell back to single-call synthesis")
        return ""
    for i, t in enumerate(texts):
        if not t:
            _flag_research_degradation(f"multipart synthesis: section '{outline[i]['title']}' empty/failed; omitted from dossier")

    # (3a) STITCH —— 大纲顺序确定性拼接（执行摘要在长度门之后生成，避免基于将被
    # 再扩写的旧节稿写摘要）。
    body = stitch_synthesis_sections(outline, texts)

    # (4) LENGTH GATE —— 散文词数（剔表格/URL/代码围栏）不足 → 定向再扩写 2 个最薄节。
    min_words = _synthesis_min_words(depth)
    total_words = count_prose_words(body)
    if min_words and total_words < min_words:
        thin = select_thinnest_sections(outline, texts, k=2)
        plog.write("warn", f"synthesize/multipart: {total_words} prose words < floor {min_words}; re-expanding {len(thin)} thinnest section(s)")
        for i in thin:
            sec = outline[i]
            scope_text = " ".join([sec["title"], sec["scope"], " ".join(sec["covers"])])
            sec_ctx = pack_context_for_section(blocks, scope_text, section_cap)
            try:
                expanded = _bare_synth_invoke(synth_model, build_synthesis_expand_prompt(
                    question, sec, texts[i], sec_ctx, target_language)).strip()
            except Exception as exc:  # noqa: BLE001 — 再扩写是加法，失败保留原节稿
                plog.write("warn", f"synthesize/multipart: re-expansion of '{sec['title']}' failed ({type(exc).__name__}: {exc})")
                expanded = ""
            if expanded and count_prose_words(expanded) > count_prose_words(texts[i]):
                texts[i] = expanded
        body = stitch_synthesis_sections(outline, texts)
        total_words = count_prose_words(body)
        if total_words < min_words:
            _flag_research_degradation(f"multipart synthesis: dossier {total_words} prose words < floor {min_words} even after re-expansion")

    # (4b) WAVE9-RQ3: 跨节去重 —— 归一化 12-gram shingle 检测跨节逐字重复段并剔除
    # （保首现；同节内不动）。放在长度门之后：再扩写可能重新引入重复。确定性、无 LLM。
    if _dedup_shingles_enabled():
        try:
            texts, _dups = dedup_cross_section_paragraphs(texts)
            if _dups:
                plog.write("warn", f"synthesize/multipart: removed {_dups} paragraph(s) repeated verbatim across sections (12-gram shingle dedup)")
                body = stitch_synthesis_sections(outline, texts)
        except Exception as _dd_err:  # noqa: BLE001 — 去重是加法，失败保留原文
            plog.write("warn", f"synthesize/multipart: cross-section dedup skipped (non-fatal): {_dd_err}")

    # (3b) EXEC SUMMARY —— 一次轻量调用：只喂分节清单 + 每节开头 ~200 词。
    summary = ""
    try:
        leads = [(outline[i]["title"], _section_lead(texts[i])) for i in range(len(outline)) if texts[i]]
        summary = _bare_synth_invoke(synth_model, build_synthesis_summary_prompt(question, leads, target_language)).strip()
    except Exception as exc:  # noqa: BLE001 — 摘要是加法，失败不弃正文
        plog.write("warn", f"synthesize/multipart: exec-summary call failed ({type(exc).__name__}: {exc})")
    if summary:
        report = summary + "\n\n" + body
    else:
        _flag_research_degradation("multipart synthesis: exec-summary call failed/empty; dossier shipped without executive summary")
        report = body
    plog.write("stage", f"synthesize/multipart: produced {len(report)} chars / {count_prose_words(report)} prose words across {written} sections")
    return report


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
    # SCALE-1: 保留 parts 的块级结构（多段合成按块做关键词分片）并单独收集 AI 笔记
    # （working-notes 摘要的原料）；单调用路径仍消费拼接后的 context，行为不变。
    parts: list[str] = []
    ai_parts: list[str] = []
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
            ai_parts.append(text)
    # PAR-1: 折叠并行 worker 的**完整**笔记。这些 worker 跑在隔离 thread_id 上，其
    # 证据不在本线程 checkpoint 里（主线程只吸收了一段简短摘要）；把保留的全文原料并入
    # parts/ai_parts，让 ~70% 会被丢弃的 fan-out 证据同样进入单调用/多段两条合成路径。
    # 空表（standard/quick 或未开 fan-out）→ 无操作，逐字节不改今日行为。
    for _wn in _collected_worker_notes():
        parts.append(_wn)
        ai_parts.append(_wn)
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
    # SCALE-4: 复刻 DEERFLOW_JUDGE_MODEL 的路由模式 —— 合成可指到专门模型（例如大窗口
    # 长文写手），走同一 create_chat_model 查表机制；未设置 → 沿用研究模型，逐字节不变。
    # 上下文上限按**实际执笔的**合成模型判类：路由到 MiniMax/Qwen/Gemini 等大窗口类时，
    # _synthesis_context_cap 的既有 large-class 逻辑自动选 900K 档。
    synth_model = os.environ.get("DEERFLOW_SYNTHESIS_MODEL", "").strip() or model_name
    _cap = _synthesis_context_cap(synth_model)
    if len(context) > _cap:
        context = context[:_cap] + "\n\n[...research context truncated...]"

    # SCALE-1: 多段合成（大纲 → 并行分节 → 缝合+摘要 → 长度门）。RES-1 反编造门在
    # 上方已把关；任何结构性失败（大纲解析失败/过半分节空/异常）→ 打 _RESEARCH_FLAGS
    # 并落回下方的单调用路径（今天的行为），degrade-safe。
    if _multipart_synthesis_enabled(depth):
        try:
            multi = synthesize_multipart(question, target_language, depth, synth_model, parts, ai_parts, context, plog)
            if multi.strip():
                return multi
        except Exception as e:  # noqa: BLE001 — 多段合成绝不让整轮合成失败
            plog.write("warn", f"synthesize/multipart: crashed ({type(e).__name__}: {e}); falling back to single-call synthesis")
            _flag_research_degradation(f"multipart synthesis crashed ({type(e).__name__}); fell back to single-call synthesis")

    # 3) Bare, tool-free model call — it cannot keep researching, so it writes.
    plog.write("stage", f"synthesize: writing report (tool-free) from {len(context)} chars of gathered research")
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        model = create_chat_model(synth_model, thinking_enabled=False)
        # WAVE9-RQ2: 单调用合成同样钉 SOURCE INDEX（与多段路径同一索引构造），
        # [S<n>] 编号由管线派发而非模型自造；落盘前 finalize_report_citations 校验。
        _cit_block = ""
        if _inline_citations_enabled():
            _cit_entries = build_citation_index(_FETCHED_SOURCES, _citation_index_cap())
            if _cit_entries:
                _set_pinned_citation_index(_cit_entries)
                _cit_block = render_citation_index_block(_cit_entries)
        prompt = (
            build_synthesis_prompt(question, target_language, depth)
            + _cit_block
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
        # SCALE-4: 复刻 DEERFLOW_JUDGE_MODEL 的路由模式 —— 结构化抽取可指到专门模型
        # （更强/更便宜的 JSON 产出器），同一 create_chat_model 查表机制；未设置 →
        # 沿用研究模型，行为逐字节不变。
        extraction_model = os.environ.get("DEERFLOW_EXTRACTION_MODEL", "").strip() or model_name
        model = create_chat_model(extraction_model, thinking_enabled=False)
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
    # ACTOR-CAST DISCIPLINE: any real forecast simulation distills to <=ACTOR_CAST_MAX
    # (default 20) MAIN actors. The old "10-35" range let extraction balloon (a wedged
    # run produced 56 actors → 56 personas → per-round sim cost). Cap=0 restores the
    # old uncapped ranges (degrade-safe).
    _cast_cap = _actor_cast_max()
    if _cast_cap > 0:
        actor_range = (
            f"{min(8, _cast_cap)}-{_cast_cap}" if depth == "deep"
            else f"{min(5, _cast_cap)}-{min(_cast_cap, 20)}"
        )
    else:
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
        "ACTORS — MAIN ACTORS ONLY: identify ONLY the actors whose decisions and actions will "
        "causally affect the outcome of the central_question — decision-makers with real agency "
        "over the forecasted event. Exclude irrelevant entities and entities mentioned only in "
        "passing. Explicitly EXCLUDE media organizations, journalists, commentators, analysts, "
        "pollsters, and think-tanks that merely report on or discuss the situation — they are "
        "context (put them in sources[]), NOT actors; include such an entity ONLY if it itself "
        f"moves the outcome. FORCE-RANK the cast by causal influence over the outcome and list "
        f"actors in that order (most influential first); output AT MOST {_cast_cap or 35} actors — "
        "a real forecast distills to a small cast of main actors, and if you are tempted to exceed "
        "the cap, cut the least causally-influential entries rather than thinning the profiles. "
        "Give each a one-sentence description (disambiguating identity) and known aliases. "
        "When the evidence supports it, populate goals/constraints/assets/vulnerabilities/"
        "stated_vs_revealed from your actors-and-incentives analysis; omit any you did not research, "
        "and do NOT fold them into memory. SITUATION_BRIEF: populate it from your "
        "actors-and-incentives analysis — current_situation and fault_lines are required.\n"
        f"{grading_note}"
        f"{quant_note}"
        f"{tiering_note}"
        # INT-1: 让市场隐含概率落进 situation_brief/catalysts/hot_topics，并把市场与其支撑配角当作上下文而非 actors。
        "PREDICTION MARKETS: if the input includes a PREDICTION MARKET SIGNALS table (machine-fetched "
        "market-implied probabilities), treat those probabilities as calibration signals — fold the "
        "notable ones into situation_brief.catalysts / dynamics and hot_topics — and treat the markets "
        "and any listed supporting-cast entities as CONTEXT, never as actors[].\n"
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


class _DegenerateToolLoopError(RuntimeError):
    """WAVE9：连续被拒的工具调用达到熔断阈值——中断回合、按既有 salvage 路径打捞已有文本。"""


# WAVE9：注入的单行纠偏消息（确定性文本，无需模型生成）。实测一条轨里 201 次空-query
# web_search 被拒——每次仍是一步全上下文模型调用（~8 分钟纯损耗），而 _validate_tool_args
# 只跳过记账、无人终止或纠正这种退化环。
_DEGEN_CORRECTIVE_MESSAGE = (
    "CORRECTION: your recent tool calls were REJECTED as malformed (e.g. web_search with an "
    "empty/too-short query, web_fetch without a full http(s) URL). Every web_search MUST carry "
    "a non-empty, specific query string and every web_fetch a complete URL. Stop repeating the "
    "broken call — either fix the arguments or move on and write your working notes now."
)


def _degen_loop_thresholds() -> "tuple[int, int]":
    """WAVE9 退化工具环阈值（纠偏阈值, 熔断阈值）。

    env RESEARCH_DEGENERATE_TOOL_CORRECT_AT（默认 8）/ RESEARCH_DEGENERATE_TOOL_BREAK_AT
    （默认 16）；<=0 关闭对应动作；非法值回退默认（degrade-safe）。连续（中间无有效工具
    调用）被拒到纠偏阈值 → 注入一条单行纠偏消息续跑（一次为限）；到熔断阈值 → 中断本回合
    并按 GraphRecursionError 同一 salvage 路径打捞。
    """
    def _read(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default
    return (_read("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", 8),
            _read("RESEARCH_DEGENERATE_TOOL_BREAK_AT", 16))


def run_streamed_turn(client, message: str, thread_id: str, recursion_limit: int, plog: ProgressLog, label: str) -> str:
    """Run one agent turn, logging tool activity, returning the final AI text.

    Mirrors ``DeerFlowClient.chat`` (accumulate AI text deltas per id, return the
    last completed message) but also emits progress lines for tool calls/results.

    WAVE9：退化工具环熔断——连续被拒（malformed）的工具调用到纠偏阈值时结束当前流段、
    以一条单行纠偏消息开新流段续跑（同一 thread、剩余预算）；到熔断阈值时抛
    :class:`_DegenerateToolLoopError` 走既有 salvage。阈值关闭（<=0）时行为与旧版一致。
    """
    chunks: dict[str, list[str]] = {}
    last_id = ""
    tool_calls = 0
    # RES-2: per-turn pending 表（v2）——双轨/fan-out 并发回合各自记账，回合末在锁内合并，
    # 杜绝跨线程 LIFO 错配（Track B 的结果确认/删除 Track A 的 URL）。
    _v2 = _fetch_accounting_v2()
    _pending_fetches: list[dict] = []
    _correct_at, _break_at = _degen_loop_thresholds()
    _consec_rejected = 0        # 连续被拒计数；任一有效工具调用即清零
    _corrective_pending = False  # 已到纠偏阈值，待结束本流段注入纠偏消息
    _corrective_sent = False     # 纠偏消息只注入一次
    _next_message = message
    _next_limit = recursion_limit
    plog.write("stage", f"{label}: starting agent turn (recursion_limit={recursion_limit})")

    try:
        while True:
            for event in client.stream(_next_message, thread_id=thread_id, recursion_limit=_next_limit):
                if _corrective_pending:
                    break  # 触发事件已完整处理；结束本流段去注入纠偏消息（生成器随 break 关闭）
                etype = event.type
                data = event.data or {}
                if etype == "messages-tuple":
                    mtype = data.get("type")
                    if mtype == "ai":
                        if data.get("tool_calls"):
                            for tc in data["tool_calls"]:
                                tool_calls += 1
                                _tname = tc.get("name")
                                # INT-2: 在记账/写 sources 前校验并（尽量）修复完整工具参数——
                                # query 过短的 web_search、无 scheme+host 的 web_fetch 直接拒（不计入
                                # 抓取账）。参数分块到达时，这里看到的是已组装好的整条 args。
                                _targs, _ok, _why = _validate_tool_args(_tname, tc.get("args"))
                                plog.write("tool", f"{_tname}( {_summarize_tool_args(_targs)} )")
                                if not _ok:
                                    plog.write("warn", f"{label}: rejected malformed {_tname} tool args ({_why}); not counted")
                                    _consec_rejected += 1
                                    if _break_at > 0 and _consec_rejected >= _break_at:
                                        raise _DegenerateToolLoopError(
                                            f"{_consec_rejected} consecutive rejected/malformed tool calls"
                                        )
                                    if _correct_at > 0 and not _corrective_sent and _consec_rejected >= _correct_at:
                                        _corrective_pending = True
                                    continue
                                _consec_rejected = 0
                                if _v2:  # #1 capture fetched URLs (turn-local, id-paired)
                                    _pending_record_fetch(_pending_fetches, _tname, _targs, call_id=tc.get("id"))
                                else:
                                    _record_fetched_url(_tname, _targs)
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
            if _corrective_pending and not _corrective_sent:
                _corrective_pending = False
                _corrective_sent = True
                plog.write("warn", f"{label}: {_consec_rejected} consecutive rejected tool calls — injecting one-line corrective message and continuing the turn")
                _flag_research_degradation(f"{label}: degenerate tool loop ({_consec_rejected} consecutive rejections); corrective message injected")  # S10
                _next_message = _DEGEN_CORRECTIVE_MESSAGE
                _next_limit = max(32, int(recursion_limit) - tool_calls)  # 续跑段用剩余预算的近似值
                continue
            break
    except Exception as exc:  # noqa: BLE001 — salvage partial output; never discard accumulated report text
        # LangGraph raises GraphRecursionError when the step budget (recursion_limit)
        # is exhausted; other transient errors can also break the stream mid-turn.
        # Whatever text was accumulated so far is still useful, so we fall through to
        # the existing return instead of letting the exception nuke the whole report.
        if isinstance(exc, _DegenerateToolLoopError):
            kind = "degenerate tool-loop break"
        elif type(exc).__name__ == "GraphRecursionError":
            kind = "recursion-limit/budget exhausted"
        else:
            kind = type(exc).__name__
        salvaged_len = len("".join(chunks.get(last_id, ())))
        plog.write("warn", f"{label}: stream ended early ({kind}: {exc}); salvaging {salvaged_len} chars")
        _flag_research_degradation(f"{label}: {kind} (salvaged {salvaged_len} chars)")  # S10

    if _v2:
        _retry_dead_fetches(_pending_fetches, plog)  # R2: 死抓取丢弃前程序化重试一次（~8s 退避）
        _merge_pending_fetches(_pending_fetches)  # RES-2: 锁内合并本回合确认成功的抓取
    final_text = strip_think("".join(chunks.get(last_id, ())))
    plog.write("stage", f"{label}: turn complete ({tool_calls} tool calls, {len(final_text)} chars)")
    return final_text


def inject_thread_message(client, thread_id: str, text: str) -> bool:
    """WAVE9：把一条**确定性**消息直接写进线程 checkpoint——零工具、零模型调用、零 agent 回合。

    走 LangGraph 编译图的 ``update_state``（messages 走 add_messages reducer 追加），供
    「注入即够」的场景（如 brief-drift 纠偏：纠偏文本本就是确定性生成的，此前却烧一个
    recursion_limit=8 的完整 agent 回合、实测 40 次工具调用后 budget exhausted）。
    依赖 client 的内部 agent 句柄（embedded DeerFlowClient，仓内 API）；任何异常/句柄
    缺失 → False，调用方回退旧的 agent-turn 注入路径（degrade-safe）。
    """
    try:
        cfg = client._get_runnable_config(thread_id)
        client._ensure_agent(cfg)
        agent = getattr(client, "_agent", None)
        if agent is None or not hasattr(agent, "update_state"):
            return False
        from langchain_core.messages import HumanMessage

        agent.update_state({"configurable": {"thread_id": thread_id}},
                           {"messages": [HumanMessage(content=text)]})
        return True
    except Exception:  # noqa: BLE001 — 注入失败绝不阻断；调用方回退 agent 回合
        return False


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
        "synthesizes.\n\n"
        # WAVE9：notes-first —— worker 的步进预算是硬的（预算耗尽即整段被打捞），实测 40–75
        # 次工具调用的 worker 只交出 121–2,318 字符的残稿：证据都搜到了、却没写下来就被截断。
        "WRITE NOTES AS YOU GO — your tool-step budget is HARD and findings never written "
        "down are LOST when it runs out. After each batch of searching/reading, immediately "
        "record what you learned (specific facts with units/dates, source URL + title) in "
        "your running notes. Well BEFORE ~80% of your budget is spent, STOP searching and "
        "write out your complete final notes — a finished, evidence-backed note set beats an "
        f"unfinished search trail every time.{lang}"
    )


def build_fanout_absorption_prompt(question: str, fanout_notes: str, target_language: str | None) -> str:
    # PAR-1: 24000→100000。旧 24000 上限对一个 8-worker fan-out 只放行约 30% 的合并笔记，
    # 主线程吸收回合看不到其余证据；抬到 100000 让并行子调查的笔记基本原封不动地进入主线程
    # 的吸收上下文（合成路径另有 _collected_worker_notes 折叠全文，两处互补）。
    cap = 100000
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
                      target_language: str | None, model_name: str, plog: "ProgressLog", index: int,
                      recursion_limit: int | None = None, prompt: str | None = None,
                      label: str | None = None) -> str:
    """Run one scoped sub-investigation on its own isolated thread_id.

    PAR-1: ``recursion_limit`` 原为死值 220 —— 现默认走 :func:`_fanout_worker_budget`
    （env ``RESEARCH_FANOUT_WORKER_BUDGET``，默认 260），显式传入则用之（deep 阶段并行
    时各阶段传自己的 phase 预算）。``prompt``/``label`` 让同一套隔离-线程机制既能跑
    KIQ 子调查（默认 build_scoped_worker_prompt），也能把一个 deep 阶段当成 scoped
    worker 跑在独立线程上（传入该阶段的 build_deep_phase_prompt 与阶段标签）。
    """
    worker_thread = f"{parent_thread_id}-fanout-{index}-{uuid.uuid4().hex[:6]}"
    return run_streamed_turn(
        client,
        prompt if prompt is not None else build_scoped_worker_prompt(question, kiq, target_language),
        worker_thread,
        recursion_limit if recursion_limit is not None else _fanout_worker_budget(),
        plog,
        label or f"research:fanout:{kiq[:24]}",
    )


def start_deep_fanout(client, opening_text: str, question: str, depth: str,
                      target_language: str | None, model_name: str, thread_id: str,
                      plog: "ProgressLog", width: int):
    """WAVE9：**非阻塞**启动 fan-out worker 池，返回 (executor, {future: seed}) 句柄。

    无种子可派 → None（调用方按「无 fan-out」继续）。worker 各自跑在隔离 thread_id 上，
    与主线程回合可安全并发（抓取记账走 per-turn pending 表）。句柄必须交给
    :func:`join_deep_fanout` 收割——executor 的 shutdown 在 join 内完成。
    """
    import concurrent.futures as _cf

    seeds = extract_kiqs_from_opening(opening_text, width)
    if not seeds:
        plog.write("warn", "deep fan-out: no KIQ/actor seeds parsed from opening; skipping")
        return None
    plog.write("stage", f"deep fan-out: {len(seeds)} scoped workers — {', '.join(seeds)}")
    ex = _cf.ThreadPoolExecutor(max_workers=min(width, len(seeds)))
    futs = {
        ex.submit(run_scoped_worker, client, s, question, thread_id, depth,
                  target_language, model_name, plog, i): s
        for i, s in enumerate(seeds)
    }
    return (ex, futs)


def join_deep_fanout(handle, plog: "ProgressLog") -> str:
    """WAVE9：收割 :func:`start_deep_fanout` 的 worker 池，合并笔记（与旧阻塞版同一合并逻辑）。

    返回单块合并 markdown（无笔记 → ''）；单 worker 失败只贡献空。无论成败都 shutdown executor。
    """
    import concurrent.futures as _cf

    ex, futs = handle
    notes: list[str] = []
    try:
        for fut in _cf.as_completed(futs):
            s = futs[fut]
            try:
                txt = fut.result()
                if txt and txt.strip():
                    notes.append(f"## 子调查：{s}\n\n{txt.strip()}")
                    # PAR-1: 保留该 worker 的完整笔记供最终合成折叠（隔离线程证据不在主线程 checkpoint）。
                    _record_worker_notes(f"子调查：{s}", txt)
            except Exception as exc:  # noqa: BLE001 — best-effort per worker
                plog.write("warn", f"deep fan-out worker '{s}' failed: {exc}")
    finally:
        ex.shutdown(wait=False)
    if not notes:
        return ""
    return "# 并行子调查汇总（per-KIQ/per-actor fan-out）\n\n" + "\n\n---\n\n".join(notes)


def run_deep_fanout(client, opening_text: str, question: str, depth: str,
                    target_language: str | None, model_name: str, thread_id: str,
                    plog: "ProgressLog", width: int) -> str:
    """Fan out scoped workers over the opening pass's seed list; merge their notes.

    Returns a single merged markdown block (or '' if no seeds / no notes). Workers
    run concurrently (bounded by ``width``) on isolated thread_ids; a failed worker
    contributes nothing. WAVE9 起为 :func:`start_deep_fanout` + :func:`join_deep_fanout`
    的阻塞组合（行为与旧实现一致）；overlap 路径直接用 start/join 两段式。
    """
    handle = start_deep_fanout(client, opening_text, question, depth,
                               target_language, model_name, thread_id, plog, width)
    if handle is None:
        return ""
    return join_deep_fanout(handle, plog)


_GAP_HEADING_RE = re.compile(r"gaps?\b", re.I)


def parse_gaps_from_notes(notes: str, limit: int = 20) -> list[str]:
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


def _merge_gaps(accumulated: list[str], new_gaps: list[str], cap: int = 20) -> list[str]:
    """Case-insensitive dedup-merge of gap lists, keeping the OLDEST ``cap`` entries.

    SCALE-2: cap 12→20，且截断从「保留最新」改为「保留最旧」——早期 pass 留下的缺口
    悬置越久越说明它难找/重要，原 ``[-cap:]`` 会让新缺口把老缺口挤出清单，导致最早的
    未决问题永远得不到定向补查；已解决的缺口不会再被下游 pass 复述，随缺口清单被
    prompt 消费自然失效。
    """
    seen = {g.lower() for g in accumulated}
    for g in new_gaps:
        if g.lower() not in seen:
            seen.add(g.lower())
            accumulated.append(g)
    return accumulated[:cap]


def _normalize_gap(gap: Any) -> str:
    """WAVE9 纯 helper：gap 文本归一化（strip + 空白折叠 + 小写 + 去尾部标点）供集合比较。"""
    s = re.sub(r"\s+", " ", str(gap or "").strip()).lower()
    return s.rstrip(" 。.;；,，")


def advance_gap_set(previous: "list[str] | None", fresh: "list[str] | None", *,
                    replace: bool = True, cap: int = 20) -> "tuple[list[str], bool]":
    """WAVE9 纯 helper：一轮收口 pass 后推进缺口集合，返回 (新集合, 是否平台期)。

    自适应收口环的旧写法 ``accumulated_gaps = _merge_gaps(accumulated, fresh)`` 只增不减
    ——已闭合的缺口永不出清单，缺口集**按构造不可能收敛**（实测最慢轨跑满全部预算、每轮
    都报 "20 unresolved gap(s)"，44 分钟 / ~15.6M prompt tokens 纯尾巴）。而收口提示词
    （build_gap_closing_prompt）本就要求「只列**仍未决**的 gaps」，故：

    * replace=True（默认）：直接用本轮 fresh **整体替换**（截 cap 条）——闭合即出清单；
    * replace=False：恢复旧 merge 语义（RESEARCH_GAP_SET_REPLACE=false 的回退路径）；
    * 平台期：fresh 非空且归一化后与上一轮集合完全相同 → True（连续两轮零进展，调用方停）。
    """
    fresh_list = [str(g) for g in (fresh or []) if str(g or "").strip()][:max(1, int(cap))]
    prev_norm = {_normalize_gap(g) for g in (previous or []) if _normalize_gap(g)}
    new_norm = {_normalize_gap(g) for g in fresh_list if _normalize_gap(g)}
    plateau = bool(new_norm) and new_norm == prev_norm
    if replace:
        return fresh_list, plateau
    return _merge_gaps(list(previous or []), list(fresh or []), cap=cap), plateau


def adaptive_passes_remaining(coverage_rounds_run: int, max_adaptive_total: int) -> int:
    """SCALE-5 纯 helper：自适应收口 pass 的剩余预算。

    总上限 max_adaptive_total（RESEARCH_MAX_ADAPTIVE_PASSES）含固定的 1 开场 + N 固定相位
    （len(DEEP_RESEARCH_PHASES)）+ 已用覆盖轮 coverage_rounds_run；剩余 = 总 - 固定 - 覆盖，下限 0。
    可离线单测（不触网、不调 LLM）。"""
    fixed = 1 + len(DEEP_RESEARCH_PHASES)  # 开场 + 固定相位
    try:
        used = fixed + max(0, int(coverage_rounds_run))
        total = max(0, int(max_adaptive_total))
    except (TypeError, ValueError):
        return 0
    return max(0, total - used)


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _detect_year_drift(brief: str, notes: str) -> dict | None:
    """RQ-6: 便宜的确定性核对——开场笔记的核心年份是否与 brief 指定年份不符。

    从 brief 抽取显著年份集合；若开场笔记的**主导**年份不在该集合、且明显压过 brief
    指定年份的出现次数（≥2 次且多于 brief 年份命中数），判定为漂移（如 brief 是 2026
    midterm、开场却围着 2028 转）。纯函数、可单测；无漂移/信息不足 → None。
    """
    brief_years = set(_YEAR_RE.findall(str(brief or "")))
    if not brief_years:
        return None
    notes_years = _YEAR_RE.findall(str(notes or ""))
    if not notes_years:
        return None
    from collections import Counter
    cnt = Counter(notes_years)
    dominant, dom_n = cnt.most_common(1)[0]
    if dominant in brief_years:
        return None
    brief_hits = sum(cnt.get(y, 0) for y in brief_years)
    if dom_n >= 2 and dom_n > brief_hits:
        return {
            "brief_years": sorted(brief_years),
            "notes_year": dominant,
            "notes_year_count": dom_n,
            "brief_year_count": brief_hits,
        }
    return None


def build_brief_correction_prompt(question: str, drift: dict, target_language: str | None) -> str:
    """RQ-6: 注入线程的纠偏消息（无工具、便宜）——把后续 pass 重新锚回 brief 的时间框。"""
    lang = f" Write your acknowledgement in {target_language}." if target_language else ""
    brief_years = ", ".join(str(y) for y in (drift.get("brief_years") or []))
    return (
        "CORRECTION — RE-ANCHOR TO THE BRIEF. Your opening notes centered on the year "
        f"{drift.get('notes_year')}, but the research brief specifies {brief_years}. Do NOT "
        "search now. Acknowledge the correct time frame, and for every remaining pass "
        "investigate the events, actors, and outcomes for the brief's year(s) — NOT "
        f"{drift.get('notes_year')}.\n\nRESEARCH BRIEF:\n{question}"
        f"{lang}"
    )


# ===================== RQ-3: research-report AI-judge → targeted top-up loop =====================
# 镜像 Track B 的卷宗 judge（build_judge_prompt/judge_dossier/dossier_passes/refine 环）：合成出的
# 研究报告（Track A）此前「一次合成即发」，从不按 INSIGHT CONTRACT 自评。这里补上：对报告按 7 个
# 契约维度打分（论点具体性/基率使用/机制链/量化密度/反共识覆盖/长度达标/引用覆盖），判不合格就按
# judge 点名的 gaps 做一次定向 top-up 研究回合 + 重合成。默认开、预算有界
# （RESEARCH_REPORT_JUDGE_MAX_ROUNDS 默认 1），judge 路由走 DEERFLOW_JUDGE_MODEL。任何解析失败/异常
# → pass-through（发当前稿，与 judge_dossier 同一 degrade 语义）。仅对 deep 生效（多-pass 路径下
# top-up 研究相对总预算便宜，且 INSIGHT CONTRACT 本就是 deep 合成的契约）。

# RQ-3: judge 输入上限（卷宗与研究报告共用）60000→200000，见 judge_dossier 的截断说明。
_JUDGE_INPUT_CAP = 200000

_REPORT_JUDGE_DIMS = (
    "thesis_specificity", "base_rate_usage", "mechanism_chains", "quantitative_density",
    "contrarian_coverage", "length_vs_target", "citation_coverage",
)
# 不可妥协的四维：论点具体性 / 机制链 / 反共识覆盖 / 引用覆盖 —— 最能区分「锐利 POV」与「泛化 slop」。
_REPORT_JUDGE_CRITICAL = ("thesis_specificity", "mechanism_chains", "contrarian_coverage", "citation_coverage")


def build_report_judge_prompt(question: str, target_language: str | None,
                              target_words: str, source_context: str | None = None) -> str:
    """构造对研究报告的 7 维 INSIGHT-CONTRACT AI-judge 提示词（默认怀疑：未证明达标即不合格）。
    只输出 JSON。``source_context`` 为空则与无上下文时逐字节一致（degrade-safe）。"""
    lang = f"（用{target_language}书写 gaps）" if target_language else ""
    dims = "、".join(_REPORT_JUDGE_DIMS)
    ctx = ""
    if source_context:
        ctx = ("\n来源信号（自动统计，仅供 citation_coverage/quantitative_density 维度校准，"
               f"不可替代你的独立判断）：{source_context}\n")
    return (
        "你是一名严苛的预测研究评审。默认怀疑：一份研究报告未被证明达到 INSIGHT CONTRACT 即视为不合格。"
        "针对下方【预测问题】评审【研究报告】，对以下 7 个维度各打 0–5 分并给 verdict。\n"
        f"维度：{dims}。逐维标准：\n"
        "- thesis_specificity：是否有一句可证伪、具体的 load-bearing 论点（非模棱两可的对冲）并贯穿全文；\n"
        "- base_rate_usage：每个主要预测是否给了参照类基率（outside view）+ 历史类比及其结局，再做个案调整；\n"
        "- mechanism_chains：是否有 3–5 条显式 因→果 链及其二阶效应（机制，而非口号）；\n"
        "- quantitative_density：是否密集使用带单位/as-of 日期/来源的具体数字，而非泛化定性描述；\n"
        "- contrarian_coverage：是否有『非共识/反直觉』小节点名共识错在哪 + 论点压力测试（最强反证）；\n"
        f"- length_vs_target：报告长度是否达到目标（约 {target_words}）——明显过短视为失败写；\n"
        "- citation_coverage：主要断言是否可归因到具名来源（标题+URL/来源分级）。\n"
        "PASS 标准（不可妥协）：无任何维度 <3；且 thesis_specificity / mechanism_chains / "
        "contrarian_coverage / citation_coverage 四项各 ≥4；且总体均分 ≥4。否则 FAIL。\n"
        "若 FAIL，给出**定向**的 gaps 清单（具体、可执行，如：'缺 X 预测的基率与历史类比'、"
        f"'无反共识小节'、'断言 Y 无来源归属'）{lang}。只输出 JSON，不要解释：\n"
        '{"scores": {' + ", ".join(f'"{d}": 0-5' for d in _REPORT_JUDGE_DIMS) + '}, '
        '"verdict": "PASS|FAIL", "gaps": ["..."]}\n\n'
        f"=== 预测问题 ===\n{question}\n"
        f"{ctx}"
    )


def report_passes(scorecard: Any) -> bool:
    """按 INSIGHT-CONTRACT 判定研究报告是否通过（镜像 dossier_passes 的稳健判定）。
    无有效记分牌时**不阻断**（degrade：回退为"发首稿"行为）。
    RESEARCH_REPORT_JUDGE_STRICT=true 可升级为全维度 ≥4。"""
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
    if _env_flag("RESEARCH_REPORT_JUDGE_STRICT", False) and min(vals) < 4:
        return False
    if min(vals) < 3:
        return False
    for k in _REPORT_JUDGE_CRITICAL:
        try:
            if float(scores.get(k, 0)) < 4:
                return False
        except (TypeError, ValueError):
            return False
    return (sum(vals) / len(vals)) >= 4.0


def build_report_refine_prompt(question: str, gaps: list, depth: str,
                               target_language: str | None) -> str:
    """构造一次**定向** top-up 研究回合提示词：只补 judge 指出的 INSIGHT-CONTRACT 缺口
    （必要时搜索/取证补基率、历史类比、机制链、量化事实、反共识证据、来源归属），不重写整份报告。"""
    gap_lines = "\n".join(f"- {str(g)}" for g in (gaps or [])[:12])
    lang = f"\n用{target_language}书写工作笔记。" if target_language else ""
    return (
        "对【预测问题】的研究报告，一名评审按 INSIGHT CONTRACT 指出了以下**具体缺口**。只针对这些"
        "缺口做定向研究（必要时搜索/取证），补齐相应的参照类基率与历史类比、因→果机制链与二阶效应、"
        "带单位/日期/来源的量化事实、非共识/反证据、或缺失的来源归属，**不要**重写整份报告、不要偏离"
        "这些缺口。完成后把新发现以工作笔记形式给出，供随后重合成采纳。\n\n"
        f"{_agentic_delegation_block(chinese=True)}"
        f"=== 缺口清单 ===\n{gap_lines}\n\n=== 预测问题 ===\n{question}{lang}\n"
    )


def judge_research_report(report: str, question: str, target_language: str | None,
                          depth: str, model_name: str, plog: "ProgressLog") -> "dict | None":
    """对研究报告做一次无工具的 AI-judge 评审，返回记分牌 dict（解析失败/异常→None，pass-through）。"""
    try:
        from deerflow.models import create_chat_model
        from langchain_core.messages import HumanMessage

        # RQ-3: 复用 DEERFLOW_JUDGE_MODEL 路由（与 judge_dossier 同一批评家）；未设置 → model_name。
        judge_model = os.environ.get("DEERFLOW_JUDGE_MODEL", "").strip() or model_name
        model = create_chat_model(judge_model, thinking_enabled=False)
        target_words = "15,000–25,000 words" if depth == "deep" else "3,500–6,000 words"
        prompt = (
            build_report_judge_prompt(question, target_language, target_words,
                                      _dossier_source_signal(report or ""))
            + "\n=== 研究报告 ===\n" + (report or "")[:_JUDGE_INPUT_CAP]
        )
        resp = model.invoke([HumanMessage(content=prompt)])
        text = _message_text(getattr(resp, "content", resp))
        sc = extract_json_object(text)
        if isinstance(sc, dict):
            return sc
        plog.write("warn", "research-report judge: could not parse scorecard JSON")
        return None
    except Exception as e:  # noqa: BLE001 — judge 失败不阻断，回退发当前稿
        plog.write("warn", f"research-report judge failed ({type(e).__name__}: {e})")
        return None


def run_report_judge_refine(client, thread_id: str, question: str, depth: str,
                            target_language: str | None, model_name: str,
                            report: str, plog: "ProgressLog") -> str:
    """RQ-3: 对合成出的研究报告跑 judge→定向 top-up→重合成环（默认开、有界）。返回（可能改进的）报告。

    判不合格且有 gaps 时：在同一线程上跑一次定向 top-up 研究回合（补 judge 点名的缺口），再重合成；
    仅当新稿非空、非 LLM 错误、且**不短于**当前稿时才替换（绝不因重合成回退长度/内容）。
    最多 RESEARCH_REPORT_JUDGE_MAX_ROUNDS 轮；judge 解析失败/任何异常 → 发当前稿（degrade-safe）。"""
    if not _env_flag("RESEARCH_REPORT_JUDGE", True) or not (report or "").strip():
        return report
    try:
        max_rounds = max(0, int(os.environ.get("RESEARCH_REPORT_JUDGE_MAX_ROUNDS", "1") or "1"))
    except ValueError:
        max_rounds = 1
    for r in range(max_rounds):
        scorecard = judge_research_report(report, question, target_language, depth, model_name, plog)
        if scorecard is None:
            break
        plog.write("stage",
                   f"research-report judge round {r + 1}: verdict={scorecard.get('verdict')} "
                   f"scores={scorecard.get('scores')}")
        if report_passes(scorecard):
            plog.write("ok", f"research-report judge: PASS at round {r + 1}")
            break
        gaps = scorecard.get("gaps") or []
        if not gaps:
            break
        plog.write("stage", f"research-report refine round {r + 1}: addressing {len(gaps)} INSIGHT-CONTRACT gap(s)")
        try:
            refine_notes = run_streamed_turn(
                client,
                build_report_refine_prompt(question, gaps, depth, target_language),
                thread_id,
                int(os.environ.get("DEERFLOW_REPORT_REFINE_RECURSION_LIMIT", "360") or "360"),
                plog,
                f"research:report-refine-{r + 1}",
            )
            # WAVE9：top-up 笔记优先走一次有界小节级补丁（只强化 judge 点名的小节）；
            # 补丁不可用（None）→ 回退整份重合成（今日行为）。
            new_report = run_incremental_report_patch(question, report, refine_notes, target_language, model_name, plog, "judge-refine")
            if new_report is None:
                new_report = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
            # 只在新稿真正更充实时替换：非空 + 非 LLM 错误 + 长度不短于当前稿（与覆盖门同一"绝不回退"约定）。
            if (new_report.strip() and not looks_like_llm_error(new_report)
                    and len(new_report.strip()) >= len(report.strip())):
                report = new_report
                plog.write("ok", f"research-report refine round {r + 1}: adopted re-synthesized report ({len(report)} chars)")
            else:
                plog.write("warn", f"research-report refine round {r + 1}: re-synthesis not longer/valid; keeping current report")
        except Exception as e:  # noqa: BLE001 — refine 失败发当前稿
            plog.write("warn", f"research-report refine failed ({type(e).__name__}: {e}); shipping current report")
            break
    return report


# ===================== WAVE9: incremental report patch (top-up / judge-refine 共用) ==============
# 三角 top-up 与 judge-refine 此前都在 verify/top-up 回合后**整份**重跑 14 节多段合成
# （synthesize_from_thread）——每次 ~14 次分节调用 × 至多 890K 字符打包上下文（~3M tokens、
# 6–15 分钟），而实测 track_3 的整份重合成因「不比原稿长」被整体丢弃（15 分钟纯损耗）。
# 这里改为：verify/top-up 回合产出的**笔记**直接喂一次有界裸模型调用，产出「小节级补丁」
# （替换受影响小节 / 追加核验小节），确定性拼回原报告。受影响小节 > 上限（默认 4）、解析
# 失败、补丁净负（更短）或任何异常 → 返回 None，调用方回退整份重合成（今日行为）。
# 默认开（RESEARCH_INCREMENTAL_TOPUP）；上限 RESEARCH_TOPUP_PATCH_MAX_SECTIONS。

_H2_HEADING_RE = re.compile(r"^##\s+(\S.*)$")
_PATCH_BLOCK_RE = re.compile(
    r"<<<\s*(?:SECTION\s*:\s*(?P<title>[^>\n]+?)|(?P<append>APPEND))\s*>>>\s*\n?(?P<body>.*?)<<<\s*END\s*>>>",
    re.S,
)
_PATCH_NO_CHANGES_RE = re.compile(r"<<<\s*NO_CHANGES\s*>>>")


def _normalize_heading(heading: Any) -> str:
    """小节标题归一化（strip 井号/星号/反引号 + 空白折叠 + 小写）供补丁标题匹配。纯函数。"""
    return re.sub(r"\s+", " ", str(heading or "").strip().strip("#*` ").strip()).lower()


def list_report_section_titles(report: str) -> "list[str]":
    """列出报告的 H2（'## '）小节标题（按出现顺序，原文大小写）。纯函数。"""
    return [m.group(1).strip() for ln in (report or "").split("\n") if (m := _H2_HEADING_RE.match(ln))]


def parse_report_patch(text: str) -> "tuple[list[tuple[str, str]], list[str], bool] | None":
    """解析裸模型输出的补丁块（纯函数）。

    返回 (replacements[(标题, 新正文)], appends[整节 markdown], no_changes)；一个可解析块都
    没有 → None（调用方回退整份重合成）。约定格式：
        <<<SECTION: 既有小节标题>>> …新正文（不含 '## 标题' 行）… <<<END>>>
        <<<APPEND>>> …自带 '## 标题' 的新小节… <<<END>>>
        <<<NO_CHANGES>>>  —— 明确声明无需任何修改
    """
    if not (text or "").strip():
        return None
    t = strip_think(text)
    no_changes = bool(_PATCH_NO_CHANGES_RE.search(t))
    replacements: list[tuple[str, str]] = []
    appends: list[str] = []
    for m in _PATCH_BLOCK_RE.finditer(t):
        body = (m.group("body") or "").strip()
        if not body:
            continue
        title = m.group("title")
        if title:
            replacements.append((title.strip(), body))
        else:
            appends.append(body)
    if not replacements and not appends and not no_changes:
        return None
    return replacements, appends, no_changes


def apply_report_patch(report: str, replacements: "list[tuple[str, str]]",
                       appends: "list[str]") -> "tuple[str, int]":
    """把补丁确定性拼回报告（纯函数）。返回 (patched, 匹配替换的小节数)。

    按 H2（'## '，多段合成 stitch 的固定层级）切段；replacement 标题归一化匹配既有小节则
    整节替换正文（保留原 '## 标题' 行），匹配不到则**降级为追加**新小节（证据只增不减）；
    appends 原样追加文末。前言（首个 H2 之前）与未受影响小节逐字节保留。
    """
    lines = (report or "").split("\n")
    h2_idx = [i for i, ln in enumerate(lines) if _H2_HEADING_RE.match(ln)]
    rep_map: dict[str, str] = {}
    for title, body in replacements:
        rep_map.setdefault(_normalize_heading(title), body)  # 同题多块取首块
    segments: list[tuple[int, int, "str | None"]] = []
    if h2_idx:
        if h2_idx[0] > 0:
            segments.append((0, h2_idx[0], None))
        for k, s in enumerate(h2_idx):
            e = h2_idx[k + 1] if k + 1 < len(h2_idx) else len(lines)
            segments.append((s, e, _H2_HEADING_RE.match(lines[s]).group(1).strip()))
    else:
        segments.append((0, len(lines), None))
    matched = 0
    consumed: set[str] = set()
    out_parts: list[str] = []
    for s, e, title in segments:
        if title is not None:
            key = _normalize_heading(title)
            if key in rep_map and key not in consumed:
                consumed.add(key)
                matched += 1
                out_parts.append(lines[s] + "\n\n" + rep_map[key].strip())
                continue
        out_parts.append("\n".join(lines[s:e]).rstrip("\n"))
    for title, body in replacements:  # 匹配不到的 replacement 降级为追加（不丢补丁内容）
        key = _normalize_heading(title)
        if key not in consumed:
            consumed.add(key)
            out_parts.append(f"## {title.strip()}\n\n{body.strip()}")
    for body in appends:
        if body.strip():
            out_parts.append(body.strip())
    return "\n\n".join(p for p in out_parts if p.strip()), matched


def build_report_patch_prompt(question: str, section_titles: "list[str]", report: str,
                              notes: str, target_language: "str | None", kind: str,
                              max_sections: int) -> str:
    """构造一次有界补丁调用的提示词：现报告 + verify/top-up 笔记 → 小节级补丁块。"""
    lang = f"\nWrite all patched content in {target_language}." if target_language else ""
    titles_block = "\n".join(f"- {t}" for t in section_titles) or "- (report has no '## ' sections)"
    if kind == "triangulation":
        intent = (
            "A triangulation-verification pass just corroborated / refuted / qualified the "
            "report's single-origin load-bearing claims. Fold those findings into the report: "
            "update the affected claims' wording and status, and add the new corroborating or "
            "refuting sources (with URL and date)."
        )
    else:
        intent = (
            "An INSIGHT-CONTRACT judge flagged specific gaps, and a targeted top-up research "
            "pass gathered new material for them. Strengthen ONLY the affected sections with "
            "that new material (reference-class base rates, cause→effect mechanism chains, "
            "quantitative facts with units/dates, contrarian evidence, source attributions)."
        )
    return (
        "You are PATCHING an existing long research report — NOT rewriting it.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        f"CONTEXT: {intent}\n\n"
        "OUTPUT FORMAT — output ONLY patch blocks, nothing else (no preamble, no commentary):\n"
        "<<<SECTION: exact existing section title>>>\n"
        "...the FULL replacement body for that section (do NOT repeat the '## title' line)...\n"
        "<<<END>>>\n"
        "<<<APPEND>>>\n"
        "## New Section Title\n"
        "...body...\n"
        "<<<END>>>\n"
        "<<<NO_CHANGES>>>  — output this token alone if the new findings require no change.\n\n"
        "HARD RULES:\n"
        f"- Patch AT MOST {max_sections} sections total; pick ONLY those materially affected "
        "by the new findings below.\n"
        "- A replacement body must KEEP every existing fact, number, and source of that "
        "section and INTEGRATE the new material — never drop evidence, never shorten.\n"
        "- Base every new claim strictly on the VERIFICATION / TOP-UP NOTES below; never "
        "invent facts, numbers, or sources.\n"
        "- Use the EXACT section titles listed below when replacing."
        f"{lang}\n\n"
        f"=== EXISTING SECTION TITLES ===\n{titles_block}\n\n"
        f"=== VERIFICATION / TOP-UP NOTES (the new material) ===\n{notes}\n\n"
        f"=== CURRENT REPORT ===\n{report}"
    )


def run_incremental_report_patch(question: str, report: str, notes: str,
                                 target_language: "str | None", model_name: str,
                                 plog: "ProgressLog", kind: str) -> "str | None":
    """WAVE9：一次有界裸模型调用把 verify/top-up 笔记补进现报告，替代整份多段重合成。

    返回打好补丁的报告（NO_CHANGES 时原样返回，重合成整个跳过）；以下情况一律返回 None，
    由调用方回退 synthesize_from_thread 整份重合成（今日行为）：开关关（RESEARCH_INCREMENTAL_TOPUP
    =false）/ 报告或笔记为空 / 补丁解析失败 / 受影响小节数 > RESEARCH_TOPUP_PATCH_MAX_SECTIONS
    （默认 4）/ 补丁后更短（净负，与覆盖门同一「绝不回退」约定）/ 任何异常。
    """
    if not _env_flag("RESEARCH_INCREMENTAL_TOPUP", True):
        return None
    if not (report or "").strip() or not (notes or "").strip():
        return None
    try:
        _max_secs = max(1, int(os.environ.get("RESEARCH_TOPUP_PATCH_MAX_SECTIONS", "4") or "4"))
    except ValueError:
        _max_secs = 4
    try:
        titles = list_report_section_titles(report)
        # SCALE-4 同款路由：合成走 DEERFLOW_SYNTHESIS_MODEL（未设置 → 研究模型）。
        synth_model = os.environ.get("DEERFLOW_SYNTHESIS_MODEL", "").strip() or model_name
        prompt = build_report_patch_prompt(
            question, titles, (report or "")[:_JUDGE_INPUT_CAP], (notes or "")[:60000],
            target_language, kind, _max_secs,
        )
        raw = strip_think(_bare_synth_invoke(synth_model, prompt))
        parsed = parse_report_patch(raw)
        if parsed is None:
            plog.write("warn", f"incremental patch ({kind}): no parseable patch blocks; falling back to full re-synthesis")
            return None
        replacements, appends, no_changes = parsed
        if no_changes and not replacements and not appends:
            plog.write("ok", f"incremental patch ({kind}): model reports NO_CHANGES; keeping report as-is (full re-synthesis skipped)")
            return report
        affected = len({_normalize_heading(t) for t, _ in replacements}) + len(appends)
        if affected > _max_secs:
            plog.write("warn", f"incremental patch ({kind}): {affected} sections affected (> {_max_secs}); falling back to full re-synthesis")
            return None
        patched, matched = apply_report_patch(report, replacements, appends)
        if not patched.strip() or looks_like_llm_error(patched):
            plog.write("warn", f"incremental patch ({kind}): patched output empty/error-like; falling back to full re-synthesis")
            return None
        if len(patched.strip()) < len(report.strip()):
            plog.write("warn", f"incremental patch ({kind}): patched report shorter than current ({len(patched)} < {len(report)}); falling back to full re-synthesis")
            return None
        plog.write("ok", f"incremental patch ({kind}): replaced {matched} section(s), appended {affected - matched}; report {len(report.strip())} → {len(patched.strip())} chars")
        return patched
    except Exception as e:  # noqa: BLE001 — 补丁只做加法；任何失败回退整份重合成
        plog.write("warn", f"incremental patch ({kind}) failed ({type(e).__name__}: {e}); falling back to full re-synthesis")
        return None


# ===================== SCALE-5: triangulation top-up (single-origin claim verification) =========
# 抽取阶段的 triangulation audit 会标出「载重却只有单一来源」的声明。此前它只落 meta 告警、无人
# 跟进。这里把 top-10 单源载重声明作为**显式核验目标**跑一次专门 pass（找独立佐证/反证）再重合成，
# 强化最关键声明的三角验证。默认对 deep 开（RESEARCH_TRIANGULATION_TOPUP）；任何失败保留原报告。


def _claim_text(c: Any) -> str:
    """从三角审计条目里取声明文本（支持 dict 的 claim/text/statement 键或纯字符串）。"""
    if isinstance(c, dict):
        return str(c.get("claim") or c.get("text") or c.get("statement") or c.get("summary") or "").strip()
    return str(c or "").strip()


def build_triangulation_verification_prompt(question: str, claims: list,
                                            target_language: str | None) -> str:
    """SCALE-5: 把单源载重声明作为显式核验目标喂给一次专门 pass（找独立佐证/反证）。"""
    lang_line = f"\n\nWrite your pass notes in {target_language}." if target_language else ""
    lines = [f"- {t[:240]}" for t in (_claim_text(c) for c in list(claims or [])[:10]) if t]
    claim_block = "\n".join(lines) or "- (no parseable single-origin claims)"
    return (
        "TRIANGULATION VERIFICATION PASS — corroborate or refute the single-origin claims below.\n\n"
        f"RESEARCH BRIEF:\n{question}\n\n"
        "Each claim below is LOAD-BEARING but currently rests on a SINGLE source. For each, run "
        "focused searches and FETCH INDEPENDENT sources (a different outlet/author/primary "
        "document) that corroborate it, refute it, or materially qualify it. Record what you "
        "found and the resulting status (corroborated / contested / unverified).\n\n"
        f"SINGLE-ORIGIN LOAD-BEARING CLAIMS:\n{claim_block}\n\n"
        "HARD RULES:\n"
        "- Only record sources you ACTUALLY FETCHED, with their real URL and on-page date.\n"
        "- A second copy of the SAME wire story is NOT independent corroboration — seek a "
        "genuinely different origin.\n\n"
        "End with the same Markdown working-note headings as the deep passes "
        "(## Evidence gathered / ## Contradictions … / ## Gaps to carry into the next pass). "
        "Do NOT write the final report yet."
        f"{lang_line}"
    )


def run_triangulation_topup(client, thread_id: str, question: str, depth: str,
                            target_language: str | None, model_name: str, report: str,
                            flagged: list, plog: "ProgressLog") -> str:
    """SCALE-5: 跑一次三角验证 pass + 重合成；仅当新稿非空、非 LLM 错误、且不短于当前稿时替换。
    degrade-safe：无声明 / 任何异常 → 返回原报告。"""
    claims = [c for c in list(flagged or []) if _claim_text(c)][:10]
    if not claims or not (report or "").strip():
        return report
    plog.write("stage", f"triangulation top-up: verifying {len(claims)} single-origin load-bearing claim(s)")
    try:
        verify_notes = run_streamed_turn(
            client,
            build_triangulation_verification_prompt(question, claims, target_language),
            thread_id,
            int(os.environ.get("DEERFLOW_TRIANGULATION_RECURSION_LIMIT", "360") or "360"),
            plog,
            "research:triangulation-verify",
        )
        # WAVE9：verify 笔记优先走**一次有界**小节级补丁（替换/追加受影响小节），替代整份
        # 14 节多段重合成；补丁不可用（None）→ 回退整份重合成（今日行为）。
        new_report = run_incremental_report_patch(question, report, verify_notes, target_language, model_name, plog, "triangulation")
        if new_report is None:
            new_report = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
        if (new_report.strip() and not looks_like_llm_error(new_report)
                and len(new_report.strip()) >= len(report.strip())):
            plog.write("ok", f"triangulation top-up: adopted re-synthesized report ({len(new_report)} chars)")
            return new_report
        plog.write("warn", "triangulation top-up: re-synthesis not longer/valid; keeping current report")
    except Exception as e:  # noqa: BLE001 — 三角 top-up 只做加法，绝不破坏本轮
        plog.write("warn", f"triangulation top-up skipped (non-fatal): {type(e).__name__}: {e}")
    return report


def run_research_stage(client, question: str, depth: str, target_language: str | None, model_name: str, thread_id: str, plog: ProgressLog, *, resume_completed=None, out_dir=None) -> str:
    """Run the research stage.

    Quick/standard remain one DeerFlow turn. Deep is intentionally multi-pass:
    several scoped research turns share the same thread/checkpointer, then a
    tool-free synthesis turn writes the final dossier from all accumulated notes
    and fetched sources.

    ITEM-3 断点续跑：``out_dir`` 非空时每完成一个 pass 就把 research_checkpoint.json
    落盘（记录复用所需的 thread_id + 已完成 pass-id + gaps + 抓取数）。``resume_completed``
    非空时进入续跑模式——已在该集合里的 pass 跳过（其笔记已在复用线程的 checkpointer 里），
    只补跑未完成 pass，覆盖门 + 合成照常重跑。两者均缺省时逐字节不改今日行为。
    """
    preset = DEPTH_PRESETS[depth]
    # ITEM-3：续跑集合 + 断点记录器。resume_completed 空 → resume=False（should_run_pass 恒
    # True，逐字节不改行为）；out_dir 空或 RESEARCH_CHECKPOINT=false → ckpt 记录为 no-op。
    _resume_done = set(resume_completed or [])
    _resume = bool(_resume_done)
    ckpt = ResearchCheckpointer(out_dir, thread_id, depth, question, enabled=_checkpoint_enabled())
    ckpt.seed_completed(_resume_done)
    if depth != "deep":
        if should_run_pass("standard", _resume_done, _resume):
            text = run_streamed_turn(
                client,
                build_research_prompt(question, depth, target_language),
                thread_id,
                preset["recursion_limit"],
                plog,
                "research",
            )
            if text.strip():
                ckpt.record_pass("standard")
        else:
            # 续跑：唯一的 research pass 已完成 → 不重跑该轮，从复用线程重合成即可（便宜）。
            plog.write("resume", "跳过已完成 pass: standard（从复用线程重合成）")
            text = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
        # RES-11: RESEARCH_COVERAGE_GATE 文档上是通用默认开的门，但此前只在 deep 生效——
        # 默认深度 standard 完全没有来源下限。SCALE-2: RESEARCH_COVERAGE_GATE_STANDARD
        # 默认改为 true（原 false）——standard 也该有来源下限；=false 恢复旧的无门槛行为。
        # 开启时对 standard 跑有界 top-up + 重合成；任何失败保留原文。
        if (depth == "standard" and _env_flag("RESEARCH_COVERAGE_GATE", True)
                and _env_flag("RESEARCH_COVERAGE_GATE_STANDARD", True)):
            try:
                _min_src = _research_min_sources(depth)  # SCALE-2: standard 保持默认 20
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
    # SCALE-2: 开场默认 220→300（环境覆盖照旧生效）——开场负责铺源图/定 KIQ，预算与
    # 各 pass 的 ×1.5 扩容保持同一比例。
    opening_limit = int(os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "300"))
    if should_run_pass("deep-opening", _resume_done, _resume):
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
            ckpt.record_pass("deep-opening")
    else:
        # 续跑：开场 pass 已完成（其源图/KIQ 笔记已在复用线程里）；空串让下方 drift/fanout
        # 跳过（都 gated on opening.strip()），gap 线程由后续 pass 重新累积。
        plog.write("resume", "跳过已完成 pass: deep-opening")
        opening = ""

    # RQ-6: 开场后一个便宜的无工具核对——若开场笔记的核心年份与 brief 指定年份不符（如
    # brief 是 2026 midterm、开场却围着 2028 转），在进入 phase 2 前往线程注入一条纠偏消息
    # 并打 _RESEARCH_FLAGS。默认开；任何失败绝不破坏本轮（degrade-safe）。
    if _env_flag("RESEARCH_BRIEF_DRIFT_CHECK", True) and opening.strip():
        try:
            _drift = _detect_year_drift(question, opening)
            if _drift:
                plog.write("warn", f"brief-drift: opening notes centered on {_drift['notes_year']} but brief specifies {_drift['brief_years']}; injecting correction before phase 2")
                _correction_text = build_brief_correction_prompt(question, _drift, target_language)
                # WAVE9：纠偏文本是确定性生成的，注入线程消息即可锚定后续 pass——不必烧一个
                # 会调工具的 agent 回合（实测该回合 recursion_limit=8 却跑了 40 次工具调用、
                # budget exhausted）。注入失败 → 回退旧 agent-turn 路径（degrade-safe）。
                _injected = (_env_flag("RESEARCH_BRIEF_DRIFT_AS_MESSAGE", True)
                             and inject_thread_message(client, thread_id, _correction_text))
                if _injected:
                    plog.write("ok", "brief-drift: correction injected as a plain thread message (no agent turn)")
                else:
                    run_streamed_turn(
                        client,
                        _correction_text,
                        thread_id, 8, plog, "research:brief-drift-correction",
                    )
                _flag_research_degradation(
                    f"brief drift: opening centered on {_drift['notes_year']} vs brief {_drift['brief_years']}; injected correction"
                )
        except Exception as _bd_err:  # noqa: BLE001 — 纠偏是加法，绝不破坏本轮
            plog.write("warn", f"brief-drift check skipped (non-fatal): {_bd_err}")

    # R2-RES-9: carry unresolved gaps forward between deep passes so each pass is
    # steered to close earlier open questions (default on; degrade-safe — empty gap
    # list yields the original prompt). Seed from the opening pass's gap section.
    _gap_threading = _env_flag("RESEARCH_DEEP_GAP_THREADING", True)
    accumulated_gaps: list[str] = parse_gaps_from_notes(opening) if _gap_threading else []
    # PAR-1: PHASE PARALLELISM（默认对 deep 开启）。scope（第 1 pass，为后续铺缺口）仍在
    # 主线程顺序跑；随后 primary-evidence / actors-and-incentives / contradictions-and-risks
    # 这三个中间 pass 改为**并行** scoped worker（各自隔离 thread_id + 各自 phase 预算，复用
    # run_scoped_worker 机制），join 后把三者笔记吸收进主线程（uncut，上限 100000），合并它们
    # 携出的缺口，再在主线程顺序跑最后的 forecast-implications（看得到全部证据），最后照旧走
    # 覆盖门 + 合成。worker 之间无法在 flight 中互传缺口——故只在 join 后汇总缺口喂给收尾
    # pass。RESEARCH_PARALLEL_PHASES=false → 走下方逐字节一致的顺序循环；任一 worker 失败 →
    # 该 pass 回退为主线程上的一次顺序运行（degrade-safe）。
    _total_phases = len(DEEP_RESEARCH_PHASES)
    _parallel_ok = (
        _env_flag("RESEARCH_PARALLEL_PHASES", True)
        and _total_phases >= 3
        and len(DEEP_RESEARCH_PHASES[1:-1]) >= 1
    )

    # I-0-4: optional per-KIQ/per-actor fan-out (default off). Runs scoped parallel
    # sub-investigations off the opening's seed list, then absorbs the merged notes
    # into THIS thread so the contradiction + synthesis passes below see the breadth.
    # WAVE9 OVERLAP：fan-out worker（隔离 thread_id）与主线程的 deep-phase-1 scope 回合
    # 互不依赖——两者都只吃 opening。默认（RESEARCH_FANOUT_OVERLAP_SCOPE=true 且并行相位
    # 路径可用且 scope 本轮要跑）改为**非阻塞**启动 worker 池，scope 回合与之并发，scope
    # 结束后 join 并跑吸收回合（吸收本就是喂给 scope **之后**的相位，顺序语义不变）——
    # 实测省 11–15 分钟关键路径。overlap 不可用/关闭 → 走原阻塞路径（逐字节同今日）。
    _fanout_pending = None
    if _env_flag("RESEARCH_DEEP_FANOUT", False) and opening.strip():
        try:
            width = max(1, int(os.environ.get("RESEARCH_FANOUT_WIDTH", "4") or "4"))
            _overlap_ok = (
                _env_flag("RESEARCH_FANOUT_OVERLAP_SCOPE", True)
                and _parallel_ok
                and should_run_pass("deep-phase-1", _resume_done, _resume)
            )
            if _overlap_ok:
                _fanout_pending = start_deep_fanout(
                    client, opening, question, depth, target_language, model_name, thread_id, plog, width
                )
                if _fanout_pending is not None:
                    plog.write("stage", "deep fan-out: overlapping workers with the scope pass (join after scope)")
            if _fanout_pending is None:
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

    if _parallel_ok:
        import concurrent.futures as _cf

        _scope_phase = DEEP_RESEARCH_PHASES[0]
        _parallel_group = list(DEEP_RESEARCH_PHASES[1:-1])  # primary-evidence / actors / contradictions
        _final_phase = DEEP_RESEARCH_PHASES[-1]             # forecast-implications

        # (1) scope —— 顺序、主线程（与今日一致，其缺口为并行组铺路）。
        if should_run_pass("deep-phase-1", _resume_done, _resume):
            _scope_text = run_streamed_turn(
                client,
                build_deep_phase_prompt(
                    question, _scope_phase, 1, _total_phases, target_language,
                    prior_gaps=accumulated_gaps if _gap_threading else None,
                ),
                thread_id,
                _phase_budget(int(_scope_phase["recursion_limit"])),
                plog,
                f"research:deep-1-{_scope_phase['label']}",
            )
            if _scope_text.strip():
                reports.append(_scope_text)
                if _gap_threading:
                    accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(_scope_text))
                ckpt.record_pass("deep-phase-1", gaps=accumulated_gaps)
        else:
            plog.write("resume", "跳过已完成 pass: deep-phase-1（scope）")
            _scope_text = ""

        # WAVE9 OVERLAP：scope 回合已在 fan-out worker 飞行期间跑完 —— 此处 join worker 池、
        # 吸收合并笔记进主线程。吸收回合先于并行中间相位（吸收喂的本就是 LATER phases），
        # 与旧阻塞路径的下游可见性一致；任何失败只丢 fan-out 增量，绝不破坏本轮。
        if _fanout_pending is not None:
            try:
                fanout_notes = join_deep_fanout(_fanout_pending, plog)
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
            finally:
                _fanout_pending = None

        # (2) 中间三 pass —— 并行 scoped worker（隔离线程 + 各自 phase 预算）。种子缺口在派发
        # 时冻结（worker 无法互传缺口），全文经 _record_worker_notes 保留供最终合成折叠。
        _seed_gaps = list(accumulated_gaps) if _gap_threading else None

        def _run_phase_worker(phase_idx: int, phase: dict) -> str:
            _txt = run_scoped_worker(
                client, str(phase["label"]), question, thread_id, depth,
                target_language, model_name, plog, phase_idx,
                recursion_limit=_phase_budget(int(phase["recursion_limit"])),
                prompt=build_deep_phase_prompt(
                    question, phase, phase_idx, _total_phases, target_language,
                    prior_gaps=_seed_gaps,
                ),
                label=f"research:deep-{phase_idx}-{phase['label']}:parallel",
            )
            if _txt and _txt.strip():
                _record_worker_notes(f"阶段并行调查：{phase['label']}", _txt)
            return _txt

        # ITEM-3 续跑：已完成的中间相位不重派 worker（其笔记已在上一轮吸收进复用的主线程
        # checkpoint 里）。跳过的相位记入 _skipped_phases，视同成功（不走顺序补跑、不重复吸收）。
        _skipped_phases: set[int] = set()
        _to_run: list[tuple[int, dict]] = []
        for _i, _p in enumerate(_parallel_group, start=2):
            if should_run_pass(f"deep-phase-{_i}", _resume_done, _resume):
                _to_run.append((_i, _p))
            else:
                plog.write("resume", f"跳过已完成 pass: deep-phase-{_i}（{_p['label']}）")
                _skipped_phases.add(_i)

        plog.write("stage", f"deep: running {len(_to_run)} middle phases in parallel — " + ", ".join(p["label"] for _i, p in _to_run))
        _phase_results: dict[int, str] = {_i: "" for _i in _skipped_phases}
        _parallel_success: set[int] = set()
        if _to_run:
            try:
                with _cf.ThreadPoolExecutor(max_workers=len(_to_run)) as _ex:
                    _futs = {
                        _ex.submit(_run_phase_worker, _i, _p): _i
                        for _i, _p in _to_run
                    }
                    for _fut in _cf.as_completed(_futs):
                        _i = _futs[_fut]
                        try:
                            _res = _fut.result() or ""
                        except Exception as _exc:  # noqa: BLE001 — 单 worker 失败不拖垮其余
                            plog.write("warn", f"deep parallel phase {_i} failed: {_exc}; will run sequential fallback")
                            _res = ""
                        _phase_results[_i] = _res
                        if _res.strip():
                            _parallel_success.add(_i)
            except Exception as _exc:  # noqa: BLE001 — 执行器层异常 → 整组回退顺序
                plog.write("warn", f"deep parallel phases pool failed: {_exc}; falling back to sequential for the group")

        # (3) 任一 pass 并行未出笔记 → 主线程顺序补跑（其笔记本就落主线程 checkpoint，不再折叠）。
        # 续跑跳过的相位（_skipped_phases）不补跑。
        for _i, _phase in enumerate(_parallel_group, start=2):
            if _i in _parallel_success or _i in _skipped_phases:
                continue
            plog.write("warn", f"deep phase {_i} ({_phase['label']}): parallel worker empty; running sequential fallback on main thread")
            try:
                _fb = run_streamed_turn(
                    client,
                    build_deep_phase_prompt(
                        question, _phase, _i, _total_phases, target_language,
                        prior_gaps=accumulated_gaps if _gap_threading else None,
                    ),
                    thread_id,
                    _phase_budget(int(_phase["recursion_limit"])),
                    plog,
                    f"research:deep-{_i}-{_phase['label']}:fallback",
                )
            except Exception as _exc:  # noqa: BLE001 — 补跑失败该 pass 贡献为空
                plog.write("warn", f"deep phase {_i} sequential fallback failed: {_exc}")
                _fb = ""
            _phase_results[_i] = _fb
            # 顺序补跑直接写主线程 checkpoint → 立即记完成（其笔记无需再经吸收）。
            if _fb.strip():
                ckpt.record_pass(f"deep-phase-{_i}")

        # (4) 把并行成功的三 pass 笔记吸收进主线程（uncut 至 cap），让顺序收尾 pass 看得到。
        _merged_parallel = "\n\n---\n\n".join(
            f"## 阶段并行调查：{_phase['label']}\n\n{_phase_results[_i].strip()}"
            for _i, _phase in enumerate(_parallel_group, start=2)
            if _i in _parallel_success and _phase_results.get(_i, "").strip()
        )
        _absorbed_ok = False
        if _merged_parallel.strip():
            try:
                run_streamed_turn(
                    client,
                    build_fanout_absorption_prompt(question, _merged_parallel, target_language),
                    thread_id,
                    120,
                    plog,
                    "research:deep-parallel-phase-merge",
                )
                _absorbed_ok = True
            except Exception as _exc:  # noqa: BLE001 — 吸收是加法，绝不破坏本轮
                plog.write("warn", f"deep parallel-phase absorption skipped: {_exc}")

        # ITEM-3：并行相位的笔记跑在隔离线程（随机 uuid 后缀，不可复用），只有经上面的吸收
        # turn 才落进可复用的主线程 checkpoint。故仅在吸收成功后才把这些相位记为完成——保证
        # 续跑跳过它们时，其证据确已在主线程里（吸收前崩溃 → 未记 → 续跑重跑，无丢证据）。
        if _absorbed_ok:
            for _i in sorted(_parallel_success):
                if _phase_results.get(_i, "").strip():
                    ckpt.record_pass(f"deep-phase-{_i}")

        # (5) 按 pass 顺序并入 reports + 合并全部中间 pass 携出的缺口（join 后统一喂收尾 pass）。
        for _i in sorted(_phase_results):
            _txt = _phase_results[_i]
            if _txt.strip():
                reports.append(_txt)
                if _gap_threading:
                    accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(_txt))

        # (6) forecast-implications —— 顺序、主线程，看得到 scope + 三并行 pass 的全部证据与缺口。
        if should_run_pass(f"deep-phase-{_total_phases}", _resume_done, _resume):
            _final_text = run_streamed_turn(
                client,
                build_deep_phase_prompt(
                    question, _final_phase, _total_phases, _total_phases, target_language,
                    prior_gaps=accumulated_gaps if _gap_threading else None,
                ),
                thread_id,
                _phase_budget(int(_final_phase["recursion_limit"])),
                plog,
                f"research:deep-{_total_phases}-{_final_phase['label']}",
            )
            if _final_text.strip():
                reports.append(_final_text)
                if _gap_threading:
                    accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(_final_text))
                ckpt.record_pass(f"deep-phase-{_total_phases}", gaps=accumulated_gaps)
        else:
            plog.write("resume", f"跳过已完成 pass: deep-phase-{_total_phases}（{_final_phase['label']}）")
    else:
        for idx, phase in enumerate(DEEP_RESEARCH_PHASES, start=1):
            _seq_pass_id = f"deep-phase-{idx}"
            if not should_run_pass(_seq_pass_id, _resume_done, _resume):
                plog.write("resume", f"跳过已完成 pass: {_seq_pass_id}（{phase['label']}）")
                continue
            limit = _phase_budget(int(phase["recursion_limit"]))  # SCALE-2: 读取时应用倍率
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
                ckpt.record_pass(_seq_pass_id, gaps=accumulated_gaps)

    # #2 COVERAGE GATE — actionable, not just a warning. If too few DISTINCT sources were
    # actually fetched-and-read, run a bounded number of top-up passes that broaden
    # high-tier coverage (and FETCH the pages) BEFORE synthesis, so the dossier rests on a
    # wide evidence base rather than a handful. Uses the #1 live fetched-URL count. Default
    # on; degrade-safe (wrapped; never breaks the run; SOFT — only adds passes, never fails
    # the run, distinct from the orchestrator's hard RESEARCH_QUALITY_GATE). Bounded by
    # RESEARCH_COVERAGE_GATE_MAX_ROUNDS.
    _coverage_rounds_run = 0  # SCALE-5: 计入自适应 pass 预算的实际补充轮数
    if _env_flag("RESEARCH_COVERAGE_GATE", True):
        min_sources = _research_min_sources(depth)  # SCALE-2: deep 默认 20→45（env 显式设置则从 env）
        try:
            # SCALE-2: deep 档默认 2→4 轮 —— 45 源门槛下 2 轮 top-up 常常补不满就放行。
            max_topups = max(0, int(os.environ.get("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", "4") or "4"))
        except ValueError:
            max_topups = 4
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
                _coverage_rounds_run += 1
                if topup.strip():
                    reports.append(topup)
                    if _gap_threading:
                        accumulated_gaps = _merge_gaps(accumulated_gaps, parse_gaps_from_notes(topup))
            except Exception as _te:  # noqa: BLE001 — top-up is additive; never break the run
                plog.write("warn", f"coverage top-up pass skipped (non-fatal): {_te}")
                break
        plog.write("stage", f"coverage gate: {distinct_fetched_count()} distinct sources fetched (floor {min_sources})")

    # SCALE-5: 自适应收尾 pass —— 覆盖门（源数量）满足后，只要**上一轮笔记仍报出未决 gaps**
    # 且总 pass 数未触顶，就再跑一次**定向**收口调研（补 gaps，而非泛化拓源）。总 pass 上限
    # RESEARCH_MAX_ADAPTIVE_PASSES（WAVE9 默认 12→9：5 相位 + 开场固定占 6，有效收口预算
    # 6→3——旧 merge-only 缺口集按构造不收敛，实测每轮都跑满 6 轮预算、44 分钟纯尾巴；
    # 收口集合改为整体替换后 3 轮足够，env 显式设置照旧生效）。收敛条件：新 pass 不再报出
    # gaps → 停；或缺口集连续两轮零变化（平台期）→ 停。任何异常绝不破坏本轮（degrade-safe）。
    if _gap_threading and accumulated_gaps and _env_flag("RESEARCH_ADAPTIVE_PASSES", True):
        try:
            _max_adaptive_total = max(0, int(os.environ.get("RESEARCH_MAX_ADAPTIVE_PASSES", "9") or "9"))
        except ValueError:
            _max_adaptive_total = 9
        _budget = adaptive_passes_remaining(_coverage_rounds_run, _max_adaptive_total)  # 剩余自适应 pass 预算
        _adaptive_ran = 0
        while accumulated_gaps and _adaptive_ran < _budget:
            plog.write("warn", f"adaptive gap-closing (pass {_adaptive_ran + 1}/{_budget}): {len(accumulated_gaps)} unresolved gap(s); running one targeted closing pass")
            try:
                _gtxt = run_streamed_turn(
                    client,
                    build_gap_closing_prompt(question, accumulated_gaps, target_language),
                    thread_id,
                    int(os.environ.get("DEERFLOW_ADAPTIVE_PASS_RECURSION_LIMIT", "360") or "360"),
                    plog,
                    f"research:deep-adaptive-gap-{_adaptive_ran + 1}",
                )
            except Exception as _ae:  # noqa: BLE001 — 自适应 pass 只做加法，绝不破坏本轮
                plog.write("warn", f"adaptive gap-closing pass skipped (non-fatal): {_ae}")
                break
            _adaptive_ran += 1
            if not _gtxt.strip():
                break
            reports.append(_gtxt)
            _fresh = parse_gaps_from_notes(_gtxt)
            # WAVE9：收口提示词已要求「只列**仍未决**的 gaps」——缺口集改为整体替换（闭合即
            # 出清单，环才可能收敛），并加平台期检测（归一化后与上一轮零变化 → 停，别再烧
            # 同一批打不动的缺口）。RESEARCH_GAP_SET_REPLACE=false 恢复旧 merge-only 语义。
            accumulated_gaps, _plateau = advance_gap_set(
                accumulated_gaps, _fresh,
                replace=_env_flag("RESEARCH_GAP_SET_REPLACE", True),
            )
            if not _fresh:  # 本轮不再报出新 gaps → 视为收敛，停止
                plog.write("ok", f"adaptive gap-closing: converged after {_adaptive_ran} pass(es) (no fresh gaps surfaced)")
                break
            if _plateau:
                plog.write("ok", f"adaptive gap-closing: gap set unchanged after pass {_adaptive_ran} (plateau); stopping — remaining gaps deemed unclosable")
                break
        if _adaptive_ran and _adaptive_ran >= _budget:
            plog.write("stage", f"adaptive gap-closing: hit pass ceiling (total {_max_adaptive_total}); proceeding to synthesis")

    # ITEM-3：合成前刷新一次 checkpoint 的 gaps/抓取数（覆盖门 + 自适应轮跑完后的最新状态）。
    # 不新增 completed pass —— 合成本身很便宜、续跑照常重跑，无需被跳过。
    ckpt.update_progress(gaps=accumulated_gaps)

    synth = synthesize_from_thread(client, thread_id, question, target_language, model_name, plog, depth=depth)
    if synth.strip():
        # RQ-3: 合成出报告后跑 INSIGHT-CONTRACT judge→定向 top-up→重合成环（默认开、有界、pass-through）。
        synth = run_report_judge_refine(client, thread_id, question, depth, target_language, model_name, synth, plog)
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
    # ACTOR-CAST DISCIPLINE: the dossier cast is capped like the extraction cast.
    _cast_cap = _actor_cast_max()
    if _cast_cap > 0:
        cast_size_line = (
            f"Aim for roughly {min(8, _cast_cap)}–{_cast_cap} deeply-profiled cast members and "
            f"NEVER more than {_cast_cap} — any real forecast distills to a small cast of main "
            "actors, chosen strictly by causal role (whose decisions and actions move the "
            "outcome), not by how often a name appeared. When over the cap, cut the least "
            "causally-influential entries, never the profile depth."
        )
    else:
        cast_size_line = (
            "Aim for roughly 8–20 deeply-profiled cast members (up to ~35 for sprawling "
            "multi-party situations), chosen by causal role, not by how often a name appeared."
        )
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
        "tier 1 (principals); materially-affected stakeholders are tier 2. Media "
        "organizations, journalists, commentators, analysts, and pollsters that merely "
        "report on or discuss the situation are NEVER cast members — an outlet is an "
        f"actor ONLY if it itself moves the outcome. {cast_size_line}\n"
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
            # RQ-3: judge 输入上限 60000→200000 —— 6 万字符会把一份 15-25K 词的长卷宗从中腰截断，
            # judge 只看到前半就打分（evidence_grounding/ontology_readiness 被系统性低估）。
            + "\n=== 卷宗 ===\n" + (dossier or "")[:_JUDGE_INPUT_CAP]
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
    # 给 Track B 的研究回合一个合理的递归预算：deep 默认跟随 deep-opening 的预算（同为
    # 300，含其环境覆盖），否则用该 depth preset 的 recursion_limit。
    # SCALE-2: Track B 获得自己的旋钮 DEERFLOW_TRACKB_RECURSION_LIMIT —— 此前与开场共用
    # 一个 env，调开场必然连带调 Track B；未设置时行为 = 跟随开场（与现状一致）。
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
    if depth == "deep":
        research_limit = int(
            (os.environ.get("DEERFLOW_TRACKB_RECURSION_LIMIT", "") or "").strip()
            or os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "300")
        )
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
        # RQ-3: 默认 1→2 —— 单轮 refine 常常只补了 judge 点名 gaps 的一部分；第二轮让 judge
        # 复评并再补一次，卷宗质量对整条流水线是上游封顶项，值这一轮额外成本（仍有界）。
        max_rounds = max(0, int(os.environ.get("ACTOR_DOSSIER_JUDGE_MAX_ROUNDS", "2") or "2"))
    except ValueError:
        max_rounds = 2
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
# Prediction-market signals (Polymarket public Gamma API, keyless)
# ---------------------------------------------------------------------------
# 研究报告落盘后，用研究问题/hot_topics/头部 actor 名确定性派生几条短检索词，抓取
# 相关活跃预测市场的隐含概率，落 prediction_markets.json 并向 research_report.md
# 追加一个机器抓取的确定性 markdown 节——下游（合成/报告/预测抽取）把市场价格当
# **校准锚点**（非真值）。本 bridge 跑在 DeerFlow 自己的 venv 里，只用 stdlib
# urllib（镜像 backend/app/utils/prediction_markets.py 的同一批端点与规整规则）。
# 数据源是 Polymarket 官方公开 Gamma API——检索/浏览公开市场无需 API key、无需钱包。
# 全程 degrade-safe：关闭旗标 / 无结果 / 网络错误 → 一行日志静默跳过。

PREDICTION_MARKETS_FILENAME = "prediction_markets.json"
# PM-6: 通过相关性门的锚点市场的 CLOB 历史价时间线，落这里 {market_id: [{t,p}]}。
PRICE_HISTORY_FILENAME = "market_price_history.json"
_POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"
# PM-6: CLOB 官方公开端点（keyless）——历史价与重报价走不同宿主（Gamma=gamma-api / 历史价=clob）；
# 镜像 backend/app/utils/prediction_markets.py 的 CLOB_BASE_URL。
_CLOB_BASE_URL = "https://clob.polymarket.com"
_POLYMARKET_TRANSIENT = (429, 500, 502, 503, 504)

# 与 backend/app/utils/prediction_markets.py 保持一致的停用词/短语启发式（无 LLM）。
# PM-1: 追加一批「泛化名词」黑名单（outcome/cover/key/factors/impact/analysis 等）——它们
# 出现在几乎每个预测问题里却毫无检索区分度，作为短语头/尾只会稀释 polymarket 全文检索。
_PM_STOPWORDS = frozenset("""
a an the and or but of to in on for with by from at as is are was were be been being
will would could should shall may might must can do does did not no nor this that these
those it its their his her our your my we they you he she i who whom whose which what
when where why how whether if than then so such very just now here there per via about
into over under between among during before after above below up down out off again
further once more most other some any all both each few own same too s t don also
year years month months week weeks day days future likely impact effect effects report
research question forecast prediction predict analysis analyze scenario scenarios
outcome outcomes cover covers coverage key factor factors driver drivers overview
""".split())
# PM-1: 4 位年份（2026/2028）作为独立 token 保留——预测市场标题几乎都带年份，旧的
# `[A-Za-z]…` 起始类丢弃一切以数字打头的 token，把「2026 US House」里的年份整个吃掉。
_PM_TOKEN_RE = re.compile(r"(?:19|20)\d{2}|[A-Za-z][A-Za-z0-9\-]*|[一-鿿]{2,}")
# PM-1: 子句边界（逗号/分号/破折号/括号/问号句号等）强制断句，避免跨子句拼出无意义长
# 短语（"House control, Senate races" 不该合成 "House control Senate"）。
_PM_CLAUSE_SPLIT_RE = re.compile(r"[,;:.!?，。；：、（）()\[\]{}\"'`\n\r]+|\s[—–\-]\s|[—–]")


def _pm_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _pm_as_list(v: Any) -> list:
    """Polymarket 的 outcomes/outcomePrices 常是 JSON 串（'["Yes","No"]'）也可能已是 list。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _pm_yes_price(outcomes: Any, prices: Any) -> float | None:
    """从 outcomes/outcomePrices 取 "Yes" 对应的隐含概率；无法定位则 None。"""
    names = _pm_as_list(outcomes)
    px = _pm_as_list(prices)
    if not names or not px or len(names) != len(px):
        return None
    for i, name in enumerate(names):
        if str(name).strip().lower() == "yes":
            return _pm_float(px[i])
    return None


def _pm_salient_phrases(text: str, max_words: int = 3) -> list[str]:
    """从研究问题抽取显著短语：在**子句边界内**把连续非停用词 token 聚成 ≤max_words 词
    短语（确定性）。PM-1：子句边界强制断句（clause-boundary truncation），4 位年份保留，
    黑名单泛化名词随停用词一起断句。"""
    phrases: list[str] = []

    for clause in _PM_CLAUSE_SPLIT_RE.split(str(text or "")):
        cur: list[str] = []
        for tok in _PM_TOKEN_RE.findall(clause):
            if re.match(r"[一-鿿]", tok):
                cur.append(tok[:12])
                phrases.append(" ".join(cur))
                cur = []
                continue
            # 全大写缩略词（AI/EU/GDP）与 4 位年份即使短也保留；其余 <3 字符英文词按噪声丢弃。
            if tok.lower() in _PM_STOPWORDS or (len(tok) < 3 and not tok.isupper() and not tok.isdigit()):
                if cur:
                    phrases.append(" ".join(cur))
                    cur = []
                continue
            cur.append(tok)
            if len(cur) >= max_words:
                phrases.append(" ".join(cur))
                cur = []
        if cur:
            phrases.append(" ".join(cur))

    seen: set = set()
    out: list[str] = []
    for p in phrases:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _pm_derive_queries(question: str, hot_topics: list | None = None,
                       actor_names: list | None = None,
                       max_queries: int = 12, max_words: int = 5) -> list[str]:
    """确定性派生市场检索词（每条 ≤5 词，全文检索短词效果最好），去重限量。

    PM-1：上限 6→12，且为 actor 名保留 ≥2 个名额——旧实现里问题短语+热点常把 12 条名额
    占满、actor 名（往往是最锐利的市场检索词，如 'Mike Johnson'）被挤掉。做法：问题短语/
    热点先在「预留后的预算」内填，actor 名用完整预算兜底保证进入，最后再回填剩余名额。
    """
    out: list[str] = []
    seen: set = set()

    def _add(q: Any, budget: int) -> None:
        s = re.sub(r"\s+", " ", str(q or "")).strip(" \t\"'.,;:!?()[]{}")
        if not s:
            return
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words])
        if s.lower() in seen or len(out) >= budget:
            return
        seen.add(s.lower())
        out.append(s)

    actors = [str(n).strip() for n in (actor_names or []) if str(n or "").strip()]
    reserve = min(2, len(actors))                 # 为 actor 名预留 ≥2 个名额
    primary_budget = max(1, max_queries - reserve)  # 问题短语/热点先只填到预留线

    for ph in _pm_salient_phrases(question, max_words=3)[:6]:
        _add(ph, primary_budget)
    for t in (hot_topics or [])[:6]:
        _add(t, primary_budget)
    for n in actors[:6]:                          # actor 名用完整预算兜底
        _add(n, max_queries)
    for ph in _pm_salient_phrases(question, max_words=3):  # 回填剩余名额
        _add(ph, max_queries)
    for t in (hot_topics or []):
        _add(t, max_queries)
    return out[:max_queries]


def degrade_market_queries(queries: "list[str]") -> "list[tuple[str, list[str]]]":
    """PM-HZ（纯函数，可单测）：远期问题的检索词降级阶梯。

    2030 级远期检索词（'TSMC market share 2030'）常在 Polymarket 零命中——市场目录
    以近端事件为主。零命中时按两级放宽重试：
      1. 'year_stripped'：剥掉查询里的 4 位年份（'TSMC market share 2030' →
         'TSMC market share'），保留主题词。
      2. 'event_level'：进一步收敛到事件级短词（剥年份后的前 3 个词），把子结果
         检索拓宽为事件目录检索。
    每阶段去重、剔除与原始集合重复的词；空阶段不返回。降级词更易捞进无关市场，
    调用方必须以 LLM 相关性门为**硬前提**（fail-closed）消费本阶梯的命中。
    """
    year_re = re.compile(r"\b(?:19|20)\d{2}\b")

    def _clean(rows: "list[str]", exclude: "set[str]") -> "list[str]":
        out: list[str] = []
        seen: set[str] = set()
        for r in rows:
            s = re.sub(r"\s+", " ", str(r or "")).strip(" \t\"'.,;:!?()[]{}-–—")
            key = s.lower()
            if not s or key in seen or key in exclude:
                continue
            seen.add(key)
            out.append(s)
        return out

    orig = {str(q or "").strip().lower() for q in queries if str(q or "").strip()}
    stripped_all = [year_re.sub(" ", str(q or "")) for q in queries]
    stage1 = _clean(stripped_all, orig)
    # 事件级：在剥年份后的词上取前 3 个词（stage1 为空——即没词带年份——也照样收敛原词）。
    base = stage1 or _clean(stripped_all, set())
    stage2 = _clean([" ".join(q.split()[:3]) for q in base],
                    orig | {q.lower() for q in stage1})
    stages: list[tuple[str, list[str]]] = []
    if stage1:
        stages.append(("year_stripped", stage1))
    if stage2:
        stages.append(("event_level", stage2))
    return stages


def build_market_queries_prompt(question: str, hot_topics: list | None = None,
                                actor_names: list | None = None) -> str:
    """PM-1: LLM 主派生路径的提示词——从问题+热点+actor 名产出 10-16 条『市场标题形状』
    的短检索词（如 'House control 2026'、'Ohio Senate winner'）。只要一个 JSON 字符串数组。"""
    hot = ", ".join(str(t) for t in (hot_topics or []) if str(t or "").strip())[:600]
    actors = ", ".join(str(n) for n in (actor_names or []) if str(n or "").strip())[:600]
    ctx = ""
    if hot:
        ctx += f"\nHOT TOPICS: {hot}"
    if actors:
        ctx += f"\nKEY ACTORS: {actors}"
    return (
        "You generate short search queries for a prediction-market catalog (Polymarket). "
        "Given a forecasting question, output 10-16 queries SHAPED LIKE MARKET TITLES — "
        "the concrete, resolvable propositions a market would list (e.g. \"House control "
        "2026\", \"Ohio Senate winner\", \"Fed rate cut March\", \"Trump approval\"). Keep "
        "each 2-6 words, include the relevant YEAR when the question is time-bound, and "
        "cover the distinct sub-outcomes and named actors — not one generic restatement.\n\n"
        f"FORECASTING QUESTION:\n{question}{ctx}\n\n"
        "Output ONLY a JSON array of strings (no prose, no code fences), e.g. "
        '["House control 2026","Ohio Senate winner"].'
    )


def _parse_market_queries(raw: str, max_queries: int = 16, max_words: int = 6) -> list[str]:
    """PM-1: 从 LLM 回复里解析『市场标题形状』检索词。纯函数、可单测；解析失败返回 []
    （调用方回退确定性派生）。优先取首个 JSON 数组；否则按行拆分去掉编号/项目符号。"""
    text = str(raw or "").strip()
    if not text:
        return []
    items: list = []
    # 1) 首个 JSON 数组（可能被包在散文/围栏里）。
    m = re.search(r"\[[^\[\]]*\]", text, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                items = parsed
        except (ValueError, TypeError):
            items = []
    # 2) 回退：逐行清洗（去 markdown 项目符号/编号/引号）。
    if not items:
        for line in text.splitlines():
            s = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s*", "", line).strip().strip('",\'')
            if s and not s.startswith("[") and not s.startswith("{"):
                items.append(s)
    out: list[str] = []
    seen: set = set()
    for it in items:
        s = re.sub(r"\s+", " ", str(it or "")).strip(" \t\"'.,;:!?()[]{}")
        if not s:
            continue
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words])
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
        if len(out) >= max_queries:
            break
    return out


def derive_market_queries_llm(question: str, hot_topics: list | None,
                              actor_names: list | None, model_name: str,
                              plog: "ProgressLog") -> list[str]:
    """PM-1: LLM 主派生路径——一次无工具裸模型调用产出市场标题形状的检索词。
    复用 synthesize/extraction 的裸模型 helper（``_bare_synth_invoke``）。任何异常/空 →
    返回 []，调用方回退确定性 ``_pm_derive_queries``（degrade-safe）。"""
    try:
        prompt = build_market_queries_prompt(question, hot_topics, actor_names)
        raw = _bare_synth_invoke(model_name, prompt)
        queries = _parse_market_queries(raw)
        if queries:
            plog.write("ok", f"prediction markets: LLM derived {len(queries)} market-title queries")
        return queries
    except Exception as e:  # noqa: BLE001 — LLM 派生失败回退确定性
        plog.write("warn", f"prediction markets: LLM query derivation failed ({type(e).__name__}: {e}); using deterministic queries")
        return []


def _pm_min_relevance() -> float:
    """PM-1: LLM 相关性门槛（0-10），低于此分的候选市场被丢弃。默认 5；非法值回退 5。"""
    try:
        return float(os.environ.get("PM_MIN_RELEVANCE", "5") or "5")
    except ValueError:
        return 5.0


def _parse_relevance_scores(raw: str, market_ids: list[str]) -> dict[str, float]:
    """PM-1: 解析相关性打分批量调用的回复 → {market_id: score}。纯函数、可单测。
    支持 {"<id>": 7, ...} 与 [{"id":..,"score":..}, ...] 两种形状；解析失败返回 {}
    （调用方据此放行全部候选，degrade-safe）。只保留 market_ids 里的已知 id。"""
    text = str(raw or "").strip()
    if not text:
        return {}
    known = {str(mid) for mid in market_ids}
    scores: dict[str, float] = {}
    obj: Any = None
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            obj = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            f = _pm_float(v)
            if str(k) in known and f is not None:
                scores[str(k)] = f
    elif isinstance(obj, list):
        for row in obj:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or row.get("market_id") or "")
            f = _pm_float(row.get("score") if row.get("score") is not None else row.get("relevance"))
            if mid in known and f is not None:
                scores[mid] = f
    return scores


def _apply_relevance_gate(markets: list[dict], scores: dict[str, float],
                          min_relevance: float) -> list[dict]:
    """PM-1: 用相关性分给市场行标注 relevance_score、丢弃 <min_relevance、按 (relevance,
    volume) 降序重排。纯函数、可单测。scores 为空（LLM 门降级）→ 不丢任何行，仅按 volume。"""
    kept: list[dict] = []
    for m in markets:
        mid = str(m.get("market_id") or "")
        sc = scores.get(mid)
        row = dict(m)
        if sc is not None:
            row["relevance_score"] = sc
            if sc < min_relevance:
                continue
        kept.append(row)
    kept.sort(key=lambda r: (
        -(float(r["relevance_score"]) if r.get("relevance_score") is not None else 0.0),
        -(r.get("volume") or 0.0),
    ))
    return kept


def score_market_relevance(question: str, markets: list[dict], model_name: str,
                           plog: "ProgressLog") -> dict[str, float]:
    """PM-1: 一次批量无工具裸模型调用，为每个候选市场打 0-10 的话题相关性分。
    返回 {market_id: score}；任何异常/空 → {}（调用方放行全部候选，degrade-safe）。"""
    if not markets:
        return {}
    try:
        rows = "\n".join(
            f'- id={m.get("market_id")}: {str(m.get("question") or "")[:160]}'
            for m in markets
        )
        prompt = (
            "You are scoring how TOPICALLY RELEVANT each prediction market is to a "
            "forecasting question. Score each market 0-10 (10 = directly resolves/informs "
            "the question; 0 = unrelated). Judge topical fit only, not trading volume.\n\n"
            f"FORECASTING QUESTION:\n{question}\n\n"
            "CANDIDATE MARKETS:\n"
            f"{rows}\n\n"
            'Output ONLY a JSON object mapping each id to its integer score, e.g. '
            '{"12345": 8, "67890": 2}. Include every id.'
        )
        raw = _bare_synth_invoke(model_name, prompt)
        scores = _parse_relevance_scores(raw, [str(m.get("market_id") or "") for m in markets])
        if scores:
            plog.write("ok", f"prediction markets: LLM relevance-scored {len(scores)}/{len(markets)} markets")
        else:
            plog.write("warn", "prediction markets: relevance scores unparseable; keeping all candidates")
        return scores
    except Exception as e:  # noqa: BLE001 — 相关性门为可选增强
        plog.write("warn", f"prediction markets: relevance scoring failed ({type(e).__name__}: {e}); keeping all candidates")
        return {}


def _polymarket_get(path: str, params: dict, timeout: float = 15.0,
                    base_url: str | None = None) -> Any:
    """stdlib GET + JSON（keyless）。瞬时错误（网络/超时/5xx/429）重试一次；仍失败则抛（调用方兜底）。

    PM-6: 默认打 Gamma（_POLYMARKET_BASE_URL）；base_url 传 _CLOB_BASE_URL 即复用同一套重试逻辑
    打 CLOB 历史价端点（未传 → 与旧行为逐字节一致）。"""
    import urllib.error
    import urllib.parse
    import urllib.request
    url = (base_url or _POLYMARKET_BASE_URL) + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    last_err: Any = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in _POLYMARKET_TRANSIENT and attempt == 1:
                continue
            break  # 4xx 参数错误不重试
        except Exception as e:  # noqa: BLE001 — URLError/超时/JSON 解析 → 重试一次
            last_err = e
            continue
    raise RuntimeError(f"polymarket GET {path} failed: {last_err}")


def _pm_parse_price_history(data: Any, days: int = 90) -> list[dict]:
    """PM-6: 纯函数——把 CLOB /prices-history 的响应规整成 [{t,p}]（镜像 backend fetch_price_history）。

    响应形如 {"history":[{t,p},...]}（少数形态直接是点数组）。days>0 时客户端裁剪到最近 days 天；
    t 转 int、p 转 float 四舍五入 4 位；缺时间戳/价格的脏点跳过。任何非预期形状 → []。可离线单测。"""
    if isinstance(data, dict):
        hist = data.get("history")
    elif isinstance(data, list):
        hist = data
    else:
        hist = None
    if not isinstance(hist, list):
        return []
    cutoff: float | None = None
    try:
        d = int(days)
        if d > 0:
            import time as _t
            cutoff = _t.time() - d * 86400
    except (TypeError, ValueError):
        cutoff = None
    out: list[dict] = []
    for pt in hist:
        if not isinstance(pt, dict):
            continue
        tf = _pm_float(pt.get("t"))
        p = _pm_float(pt.get("p"))
        if tf is None or p is None:
            continue  # 缺时间戳/价格的点是脏数据，跳过
        if cutoff is not None and tf < cutoff:
            continue
        out.append({"t": int(tf), "p": round(p, 4)})
    return out


def _pm_fetch_price_history(clob_token_id: Any, interval: str = "1d",
                            days: int = 90, timeout: float = 15.0) -> list[dict]:
    """PM-6: 拉某 CLOB token 的历史价格序列（keyless，镜像 backend fetch_price_history）；
    返回 [{t,p}] 或 []（任何失败）。端点 GET https://clob.polymarket.com/prices-history
    ?market=<clobTokenId>&interval=<interval>。degrade-to-empty：空 token/网络/解析失败 → []（绝不抛）。"""
    token = str(clob_token_id or "").strip()
    if not token:
        return []
    params = {"market": token, "interval": str(interval or "1d").strip()}
    try:
        data = _polymarket_get("/prices-history", params, timeout=timeout, base_url=_CLOB_BASE_URL)
    except Exception:  # noqa: BLE001 — 历史价为可选增强，失败降级为空
        return []
    return _pm_parse_price_history(data, days)


def _pm_normalize_market(raw: Any, matched_query: str, min_volume: float,
                         event_title: str = "", event_slug: str = "") -> dict | None:
    """单条 Polymarket 市场规整化（镜像 backend 规则）；已关闭/无价/定盘价/低量 → None。"""
    if not isinstance(raw, dict):
        return None
    market_id = str(raw.get("id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not market_id or not question:
        return None
    # 已关闭/已判定 → 不配当锚点（active 旗标在已判定市场上仍为 True，不可靠）。
    if raw.get("closed") is True or str(raw.get("closed")).strip().lower() == "true":
        return None
    prob = _pm_yes_price(raw.get("outcomes"), raw.get("outcomePrices"))
    # 价格须严格落在 (0,1)：恰为 0/1 = 市场实质已定盘，作为校准锚点没有意义。
    if prob is None or not (0.0 < prob < 1.0):
        return None
    volume = _pm_float(raw.get("volume")) or 0.0
    liquidity = _pm_float(raw.get("liquidity")) or 0.0
    if volume < float(min_volume):
        return None
    row: dict[str, Any] = {
        "market_id": market_id,
        "exchange": "polymarket",
        "question": question,
        "implied_yes_prob": round(prob, 4),
        "volume": volume,
        "liquidity": liquidity,
        "event_title": event_title or str(raw.get("groupItemTitle") or "").strip(),
        "matched_query": matched_query,
    }
    # PM-1: 尽量携出可用的深链/时效/流动性信号（存在才写，缺失则降级为无该键）。
    slug = str(event_slug or "").strip()
    if slug:
        row["event_url"] = f"https://polymarket.com/event/{slug}"
    end_date = str(raw.get("endDate") or "").strip()
    if end_date:
        row["end_date"] = end_date
    odpc = _pm_float(raw.get("oneDayPriceChange"))
    if odpc is not None:
        row["one_day_price_change"] = round(odpc, 4)
    best_bid = _pm_float(raw.get("bestBid"))
    if best_bid is not None:
        row["best_bid"] = round(best_bid, 4)
    best_ask = _pm_float(raw.get("bestAsk"))
    if best_ask is not None:
        row["best_ask"] = round(best_ask, 4)
    # PM-6: CLOB token id（Yes/No 各一）——画历史价时间线（_pm_fetch_price_history）的入口；
    # Gamma 里是 JSON 串 '["0x..","0x.."]'，规整成 list 保留；缺失不造假（键不出现）。镜像 backend。
    clob_ids = [str(t).strip() for t in _pm_as_list(raw.get("clobTokenIds")) if str(t).strip()]
    if clob_ids:
        row["clob_token_ids"] = clob_ids
    return row


def _pm_cap_per_event(ranked: list[dict], max_per_event: int, max_total: int) -> list[dict]:
    """在已按 volume 降序的市场列表上，限制每个事件最多 max_per_event 条，再截到 max_total。
    保证多事件多样性（一个多结局事件的子市场阶梯不霸占全部名额）。<=0 视为不限制。"""
    if int(max_per_event) <= 0:
        return ranked[:max(0, int(max_total))]
    per_event: dict[str, int] = {}
    out: list[dict] = []
    for m in ranked:
        key = str(m.get("event_title") or "").strip() or m.get("market_id") or ""
        n = per_event.get(key, 0)
        if n >= int(max_per_event):
            continue
        per_event[key] = n + 1
        out.append(m)
    return out[:max(0, int(max_total))]


def _pm_snapshot(queries: list[str], per_query: int = 8, max_total: int = 20,
                 min_volume: float = 200, max_per_event: int = 3) -> list[dict]:
    """对一组检索词取市场快照：public-search 返回活跃事件，展开其市场，按 market_id 去重、
    过滤、按成交量降序，每个事件最多 max_per_event 条，最后限量。单个 query 失败只丢那一批。"""
    by_id: dict[str, dict] = {}
    for q in queries:
        q = str(q or "").strip()
        if not q:
            continue
        try:
            data = _polymarket_get("/public-search",
                                   {"q": q, "limit_per_type": per_query,
                                    "events_status": "active"})
        except Exception:  # noqa: BLE001 — 单 query 失败不影响其余
            continue
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            event_title = str(event.get("title") or "").strip()
            event_slug = str(event.get("slug") or "").strip()
            for raw in event.get("markets") or []:
                norm = _pm_normalize_market(raw, matched_query=q, min_volume=min_volume,
                                            event_title=event_title, event_slug=event_slug)
                if norm is not None and norm["market_id"] not in by_id:
                    by_id[norm["market_id"]] = norm
    ranked = sorted(by_id.values(), key=lambda m: -(m.get("volume") or 0.0))
    return _pm_cap_per_event(ranked, max_per_event, max_total)


def _pm_render_section(markets: list[dict], as_of: str) -> str:
    """确定性渲染追加到 research_report.md 的「预测市场信号」markdown 节。"""
    lines = [
        "## Prediction Market Signals",
        "",
        (f"> Machine-fetched from Polymarket (public Gamma API) as of {as_of}. "
         "Market-implied probabilities are **calibration anchors, not ground truth** — "
         "prices move continuously; mind freshness before relying on them."),
        "",
        "### Prediction Market Signals (Polymarket)",
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


def _pm_env_caps() -> "tuple[int, float, int]":
    """PM: 读取市场快照的三个上限（max_total / min_volume / max_per_event），非法值降级默认。
    initial 快照与最终刷新共用同一套读法，避免两处漂移。"""
    try:
        max_total = int(os.environ.get("PREDICTION_MARKETS_MAX",
                                       os.environ.get("ODDPOOL_MAX_MARKETS", "20")) or "20")
    except ValueError:
        max_total = 20
    try:
        min_volume = float(os.environ.get("PREDICTION_MARKETS_MIN_VOLUME", "200") or "200")
    except ValueError:
        min_volume = 200.0
    try:
        max_per_event = int(os.environ.get("PREDICTION_MARKETS_MAX_PER_EVENT", "3") or "3")
    except ValueError:
        max_per_event = 3
    return max_total, min_volume, max_per_event


def _pm_render_pricing_block(markets: list[dict], as_of: str, limit: int = 8) -> str:
    """PM-4: 一段紧凑的『当前市场定价』块，注入 pass-0 提示词让开场带着锚点搜。
    确定性、无 LLM。空市场 → 空串。"""
    if not markets:
        return ""
    lines = [
        f"CURRENT MARKET PRICING (Polymarket, machine-fetched as of {as_of}) — these are "
        "calibration anchors, NOT ground truth. For each, investigate WHY the market is "
        "priced this way and whether the evidence supports or contradicts it:",
    ]
    for m in markets[:max(1, int(limit))]:
        prob = _pm_float(m.get("implied_yes_prob"))
        vol = _pm_float(m.get("volume"))
        q = str(m.get("question") or "").replace("\n", " ").strip()[:140]
        pct = f"{prob * 100:.0f}%" if prob is not None else "—"
        vtxt = f", volume ${vol:,.0f}" if vol is not None else ""
        lines.append(f"- {q}: market prices YES at {pct}{vtxt}")
    return "\n".join(lines)


def _pm_resolve_queries(question: str, hot_topics: list | None, actor_names: list | None,
                        model_name: str, plog: "ProgressLog") -> list[str]:
    """PM-1: 市场检索词派生——LLM 主路径（市场标题形状）优先，任何异常/空回退确定性派生
    （degrade-safe）。PREDICTION_MARKETS_LLM_QUERIES=false 时直接走确定性。"""
    llm_q: list[str] = []
    if _env_flag("PREDICTION_MARKETS_LLM_QUERIES", True) and str(model_name or "").strip():
        llm_q = derive_market_queries_llm(question, hot_topics, actor_names, model_name, plog)
    if llm_q:
        return llm_q
    return _pm_derive_queries(question, hot_topics, actor_names)


def _pm_initial_snapshot(question: str, model_name: str, plog: "ProgressLog") -> list[dict]:
    """PM-4: 研究开跑前的初始快照——仅用『问题派生检索词』（此时还没有 actor/热点）。
    应用相关性门后返回市场行列表（供注入 pass-0 提示词 + 喂结构化抽取）。任何失败 → []。"""
    queries = _pm_resolve_queries(question, None, None, model_name, plog)
    if not queries:
        return []
    max_total, min_volume, max_per_event = _pm_env_caps()
    markets = _pm_snapshot(queries, max_total=max_total, min_volume=min_volume,
                           max_per_event=max_per_event)
    if not markets:
        plog.write("warn", f"prediction markets (pre-pass): no active markets (queries={queries})")
        return []
    scores = score_market_relevance(question, markets, model_name, plog)
    return _apply_relevance_gate(markets, scores, _pm_min_relevance())


def _collect_prediction_markets(out_dir: Path, question: str, report: str,
                                meta: dict, plog: "ProgressLog",
                                model_name: str = "claude") -> None:
    """研究报告落盘后抓取预测市场信号：落 prediction_markets.json + 追加报告节 + 注册进 meta。

    调用方包 try/except；本函数内部对「无 key/无结果」也只记一行日志（degrade-safe）。
    PM-4：这是「刷新」快照——用更好的 actor/热点/LLM 检索词重取，作为最终表格输出。
    """
    if not _env_flag("PREDICTION_MARKETS_ENABLED", True):
        plog.write("warn", "prediction markets skipped (PREDICTION_MARKETS_ENABLED=false)")
        return
    # hot_topics / 头部 actor 名 / 支撑配角来自已落盘的 actors.json（缺失/未抽取时仅用研究问题）。
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
                # INT-1: enforce_actor_cast 降级/截断出去的支撑配角（context_entities）也是
                # 高价值的市场检索词来源（往往是次级候选/相关方），并入 actor 名派生。
                ctx = obj.get("context_entities")
                if isinstance(ctx, list):
                    ctx_names = [str((e or {}).get("name") or "").strip()
                                 for e in ctx if isinstance(e, dict)]
                    actor_names += [n for n in ctx_names if n and n not in actor_names]
    except Exception:  # noqa: BLE001 — actors.json 只是查询词的可选增强
        pass
    queries = _pm_resolve_queries(question, hot_topics, actor_names, model_name, plog)
    if not queries:
        # PM-HZ: 无检索词也**始终落盘**显式空标记——下游（报告市场包/置信度理由）能
        # 陈述「无市场锚点」而非在静默缺文件与「阶段没跑」之间无法区分。
        payload = {"as_of": _utcnow(), "source": "polymarket", "queries": [],
                   "markets": [], "no_relevant_markets": True,
                   "reason": "no derivable queries"}
        _atomic_write_text(out_dir / PREDICTION_MARKETS_FILENAME,
                           json.dumps(payload, ensure_ascii=False, indent=2))
        meta["prediction_markets_count"] = 0
        plog.write("warn", f"prediction markets: no derivable queries; wrote {PREDICTION_MARKETS_FILENAME} no_relevant_markets marker")
        return
    max_total, min_volume, max_per_event = _pm_env_caps()
    markets = _pm_snapshot(queries, max_total=max_total, min_volume=min_volume,
                           max_per_event=max_per_event)
    if markets:
        # PM-1: LLM 相关性门——为每个候选市场打 0-10 分，丢弃 <PM_MIN_RELEVANCE，按 (relevance,
        # volume) 重排；打分失败 → 放行全部候选（仅按 volume，主路径语义不变）。
        _scores = score_market_relevance(question, markets, model_name, plog)
        markets = _apply_relevance_gate(markets, _scores, _pm_min_relevance())
        if not markets:
            plog.write("warn", "prediction markets: all candidates below relevance floor")
    else:
        plog.write("warn", f"prediction markets: no relevant active markets (queries={queries})")
    # PM-HZ: 远期降级阶梯——主检索词零命中/全被门挡时，剥年份→事件级宽词重试。降级词
    # 更易捞进无关市场，相关性门在此是**硬前提**（fail-closed：打分不可用 → 整阶段丢弃，
    # 与主路径的 fail-open 不同）；降级命中逐行打 horizon_degraded 标签留痕。
    degraded_stage = ""
    degraded_queries: list[str] = []
    if not markets and _env_flag("PREDICTION_MARKETS_HORIZON_RETRY", True):
        for _stage, _stage_queries in degrade_market_queries(queries):
            plog.write("warn", f"prediction markets: horizon-degradation retry ({_stage}) with {len(_stage_queries)} broadened queries")
            _cand = _pm_snapshot(_stage_queries, max_total=max_total, min_volume=min_volume,
                                 max_per_event=max_per_event)
            if not _cand:
                continue
            _sc = score_market_relevance(question, _cand, model_name, plog)
            if not _sc:
                plog.write("warn", f"prediction markets: relevance gate unavailable for degraded ({_stage}) candidates; discarding {len(_cand)} (fail-closed)")
                continue
            _kept = _apply_relevance_gate(_cand, _sc, _pm_min_relevance())
            if not _kept:
                plog.write("warn", f"prediction markets: degraded ({_stage}) candidates all below relevance floor")
                continue
            for _row in _kept:
                _row["horizon_degraded"] = _stage
            markets = _kept
            degraded_stage = _stage
            degraded_queries = _stage_queries
            plog.write("ok", f"prediction markets: horizon-degradation ({_stage}) matched {len(_kept)} relevance-gated market(s)")
            break
    as_of = _utcnow()
    payload = {"as_of": as_of, "source": "polymarket", "queries": queries, "markets": markets}
    if degraded_stage:
        payload["horizon_degraded"] = degraded_stage
        payload["degraded_queries"] = degraded_queries
    if not markets:
        # PM-HZ: 空结果也**始终落盘**——显式 no_relevant_markets 标记（含尝试过的检索词），
        # 报告节不追加（没有市场行可渲染）。
        payload["no_relevant_markets"] = True
        _atomic_write_text(out_dir / PREDICTION_MARKETS_FILENAME,
                           json.dumps(payload, ensure_ascii=False, indent=2))
        meta["prediction_markets_count"] = 0
        plog.write("warn", f"wrote {PREDICTION_MARKETS_FILENAME} (no_relevant_markets marker; no report section appended)")
        return
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
    # PM-6: 为通过相关性门的锚点市场抓 CLOB 历史价时间线（keyless），落 market_price_history.json。
    # degrade-safe：任何失败只记一行日志，绝不影响已落盘的市场快照/报告节。
    try:
        if _env_flag("PREDICTION_MARKETS_PRICE_HISTORY", True):
            _hist_n = _collect_market_price_history(out_dir, markets, plog)
            if _hist_n:
                meta["prediction_market_price_history_count"] = _hist_n
    except Exception as _ph_err:  # noqa: BLE001 — 历史价为可选增强
        plog.write("warn", f"market price history skipped (non-fatal): {_ph_err}")


def _collect_market_price_history(out_dir: Path, markets: list[dict],
                                  plog: "ProgressLog") -> int:
    """PM-6: 为每个（通过相关性门的）市场抓 CLOB 历史价时间线，写 {market_id: [{t,p}]}。

    每市场用第一个 CLOB token（约定俗成 Yes 腿）画线；cap 20 市场、90 天、1d 档（镜像 backend）。
    degrade-to-empty：无 clob token / 网络失败 → 该市场跳过；全部为空 → 不写文件。返回落盘的序列条数。"""
    try:
        cap = int(os.environ.get("PREDICTION_MARKETS_PRICE_HISTORY_MAX", "20") or "20")
    except ValueError:
        cap = 20
    try:
        days = int(os.environ.get("PREDICTION_MARKETS_PRICE_HISTORY_DAYS", "90") or "90")
    except ValueError:
        days = 90
    interval = (os.environ.get("PREDICTION_MARKETS_PRICE_HISTORY_INTERVAL", "1d") or "1d").strip()
    hist_map: dict[str, list] = {}
    for m in list(markets or [])[:max(0, cap)]:
        mid = str(m.get("market_id") or "").strip()
        clob_ids = m.get("clob_token_ids") or []
        if not mid or not clob_ids:
            continue
        series = _pm_fetch_price_history(str(clob_ids[0]), interval=interval, days=days)
        if series:
            hist_map[mid] = series
    if not hist_map:
        plog.write("warn", "market price history: no series fetched (no CLOB tokens or all empty)")
        return 0
    _atomic_write_text(out_dir / PRICE_HISTORY_FILENAME,
                       json.dumps(hist_map, ensure_ascii=False, indent=2))
    plog.write("ok", f"wrote {PRICE_HISTORY_FILENAME} ({len(hist_map)} market price series, "
                     f"≤{days}d @ {interval})")
    return len(hist_map)


# ---------------------------------------------------------------------------
# WAVE9-RQ4: 研究图表 —— forecast-visuals 技能捆绑的 plotly 渲染器（scripts/render.py）
# 的确定性调用步。结构化工件（actors/timeline/quantitative）落盘后直接子进程渲染
# charts/*.html + charts/*.png + charts.json，并把 PNG 以 Visual Annex 内嵌进
# research_report.md —— 不依赖 agent 记得跑技能（三次真实运行 0 张图的根因）。
# ---------------------------------------------------------------------------

CHARTS_MANIFEST_FILENAME = "charts.json"


def _charts_timeout() -> float:
    """WAVE9-RQ4: 渲染子进程超时（秒）。非法/非正值回退 180。"""
    try:
        v = float(os.environ.get("RESEARCH_CHARTS_TIMEOUT", "180") or "180")
    except ValueError:
        v = 180.0
    return v if v > 0 else 180.0


def _charts_python() -> str:
    """WAVE9-RQ4: 选择跑 render.py 的解释器——RESEARCH_CHARTS_PYTHON 显式覆盖 >
    backend/.venv（部署里带 plotly/kaleido 的解释器）> 当前 sys.executable。
    只做存在性检查；plotly/matplotlib 的导入可用性由 render.py 自检并降级。"""
    envp = (os.environ.get("RESEARCH_CHARTS_PYTHON", "") or "").strip()
    if envp and Path(envp).exists():
        return envp
    backend_py = Path(__file__).resolve().parents[1] / "backend" / ".venv" / "bin" / "python"
    if backend_py.exists():
        return str(backend_py)
    return sys.executable


def embed_chart_refs(report_text: str, charts: "list[dict]") -> str:
    """WAVE9-RQ4（纯函数）：把 charts.json 清单的图表以 '## Visual Annex' 节内嵌进报告 ——
    PNG 条目走 markdown 图片 ``![title](charts/x.png)`` + 交互版链接 + 斜体 caption；
    仅 HTML 的条目退化为交互版链接。正文已引用的 path 不重复；已有 Visual Annex 时只追加
    尚缺条目（不重复标题）。空清单/无有效条目 → 原样返回。"""
    rows = [c for c in (charts or [])
            if isinstance(c, dict) and str(c.get("path") or "").strip()]
    # The research agent may already have placed figures beside the relevant
    # evidence. Annex only genuinely missing paths; do not duplicate a correct
    # contextual embed merely because the deterministic renderer ran later.
    rows = [c for c in rows
            if f"]({str(c.get('path') or '').strip()})" not in (report_text or "")]
    if not rows:
        return report_text
    lines: list[str] = [] if "## Visual Annex" in (report_text or "") else ["## Visual Annex", ""]
    for c in rows:
        title = str(c.get("title") or "Chart").strip()
        path = str(c.get("path") or "").strip()
        caption = str(c.get("caption") or "").strip()
        html_path = str(c.get("html_path") or "").strip()
        lines.append(f"### {title}")
        lines.append("")
        if path.lower().endswith(".png"):
            lines.append(f"![{title}]({path})")
            if html_path:
                lines.append("")
                lines.append(f"[Interactive version]({html_path})")
        else:
            lines.append(f"[Interactive version]({path})")
        if caption:
            lines.append("")
            lines.append(f"_{caption}_")
        lines.append("")
    return (report_text or "").rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"


def _render_research_charts(out_dir: Path, meta: dict, plog: "ProgressLog") -> None:
    """WAVE9-RQ4: 确定性图表渲染步（best effort，调用方再包一层 try/except）。

    子进程跑 skills/forecast-visuals/scripts/render.py --dir <out_dir>：渲染器读
    actors.json / timeline.json / quantitative.json（缺哪个跳哪张，绝不造数据），写
    charts/*.html + charts/*.png + charts.json。随后把 PNG 内嵌进 research_report.md
    （读盘上最新版本——预测市场节可能已追加）。python/plotly 缺失、超时、渲染失败 →
    一行日志跳过，绝不影响已产出的研究契约（degrade-safe）。
    """
    if _research_charts_min() <= 0:
        return
    # GATE-W9: 双布局探测——bridge 源树是 skills/<name>/，部署树（deer-flow/）是
    # skills/public/<name>/（setup.sh / _sync_deerflow_bridge_if_stale 的部署语义）。
    _base = Path(__file__).resolve().parent
    _candidates = [
        _base / "skills" / "forecast-visuals" / "scripts" / "render.py",
        _base / "skills" / "public" / "forecast-visuals" / "scripts" / "render.py",
    ]
    render_py = next((p for p in _candidates if p.exists()), None)
    if render_py is None:
        plog.write("warn", f"research charts: bundled renderer missing (tried {', '.join(str(p) for p in _candidates)}); skipped")
        return
    import subprocess
    py = _charts_python()
    plog.write("stage", f"research charts: rendering via {py} {render_py.name} (out={out_dir})")
    try:
        proc = subprocess.run(
            [py, str(render_py), "--dir", str(out_dir)],
            capture_output=True, text=True, timeout=_charts_timeout(),
        )
    except subprocess.TimeoutExpired:
        plog.write("warn", f"research charts: render.py timed out after {_charts_timeout():.0f}s; skipped")
        return
    _out_tail = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[-1]
    if proc.returncode != 0:
        plog.write("warn", f"research charts: render.py exited {proc.returncode} ({_out_tail}); skipped")
        return
    manifest_path = out_dir / CHARTS_MANIFEST_FILENAME
    if not manifest_path.exists():
        plog.write("warn", "research charts: render.py produced no charts.json; skipped")
        return
    try:
        charts = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        plog.write("warn", f"research charts: charts.json unreadable ({e}); skipped")
        return
    if not isinstance(charts, list) or not charts:
        plog.write("warn", "research charts: charts.json empty; nothing to embed")
        return
    meta["charts_count"] = len(charts)
    if len(charts) < _research_charts_min():
        plog.write("warn", f"research charts: only {len(charts)}/{_research_charts_min()} required charts rendered (missing input artifacts?)")
    report_path = out_dir / REPORT_FILENAME
    if report_path.exists():
        try:
            cur = report_path.read_text(encoding="utf-8")
            new = embed_chart_refs(cur, charts)
            if new != cur:
                _atomic_write_text(report_path, new)
                meta["report_chars"] = len(new)
                plog.write("ok", f"research charts: embedded {len(charts)} chart(s) into {REPORT_FILENAME} (Visual Annex)")
        except OSError as e:
            plog.write("warn", f"research charts: could not embed into {REPORT_FILENAME} ({e}); charts remain on disk")
    plog.write("ok", f"wrote {CHARTS_MANIFEST_FILENAME} ({len(charts)} charts) + charts/ files")


# ---------------------------------------------------------------------------
# ITEM-14 EXTRACT-ONLY 打捞 —— 跳过全部研究，只从既存报告跑结构化抽取 + 预测市场
# ---------------------------------------------------------------------------


def run_extract_only(question: str, out_dir: Path, args, meta: dict, plog: "ProgressLog",
                     write_meta) -> int:
    """ITEM-14：跳过所有研究阶段，只对既存 research_report.md 跑结构化抽取 + 预测市场。

    前置：main() 已做过「报告存在且 ≥ _extract_only_min_chars()」的门（不满足直接非零退出）。
    此函数不构造 DeerFlow 研究客户端、不发起任何研究 turn——extract_structured_tool_free 与
    _collect_prediction_markets 各自用裸模型调用。用途：watchdog 超时打捞（研究报告已落盘、
    结构化档案还没跑）时，用一个有界子进程把 actors/sources/timeline/quantitative 补出来。

    抽取用的是与正常 Stage 2 相同的原语（extract_structured_tool_free / extract_json_object /
    enforce_actor_cast / reconcile_quantitative / source_tier_histogram / _collect_prediction_markets），
    但故意省去依赖「本 run 现场抓取」的增强（来源 grounding、research_quality 记分牌）——本进程没有
    发起过抓取，那些信号不适用。抽取失败为软失败（报告仍在，返回 0）；报告缺失/过小已在 main 拦截。
    """
    report_path = out_dir / REPORT_FILENAME
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    dossier_path = out_dir / ACTOR_DOSSIER_FILENAME
    dossier = dossier_path.read_text(encoding="utf-8") if dossier_path.exists() else ""
    meta["extract_only"] = True
    plog.write("stage", f"extract-only: 跳过研究，仅对既存 {REPORT_FILENAME} 跑结构化抽取 + 预测市场")

    if not args.no_actors:
        try:
            # 卷宗（若有）作为 actor 抽取「主」输入，报告作为附加上下文（与正常 Stage 2 一致）。
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
                plog.write("warn", "extract-only: 结构化 JSON 解析失败；actors.json/sources.json 跳过")
            else:
                sources = obj.pop("sources", None)
                try:
                    enforce_actor_cast(obj, meta, plog)
                except Exception as _cast_err:  # noqa: BLE001 — 阵容纪律是加法
                    plog.write("warn", f"extract-only: actor-cast discipline skipped (non-fatal): {_cast_err}")
                _atomic_write_text(out_dir / ACTORS_FILENAME, json.dumps(obj, ensure_ascii=False, indent=2))
                meta["actors_count"] = len(obj.get("actors", []) or [])
                meta["relationships_count"] = len(obj.get("relationships", []) or [])
                meta["has_situation_brief"] = bool(obj.get("situation_brief"))
                plog.write("ok", f"extract-only: wrote {ACTORS_FILENAME} ({meta['actors_count']} actors, {meta['relationships_count']} relationships)")
                key_events = obj.get("key_events")
                if isinstance(key_events, list) and key_events:
                    _atomic_write_text(out_dir / TIMELINE_FILENAME, json.dumps(key_events, ensure_ascii=False, indent=2))
                    meta["timeline_count"] = len(key_events)
                    plog.write("ok", f"extract-only: wrote {TIMELINE_FILENAME} ({len(key_events)} events)")
                quant = _clean_optional_rows(obj.get("quantitative_facts"))
                extra_contested: list = []
                if quant and _env_flag("RESEARCH_QUANT_RECONCILE", True):
                    try:
                        extra_contested, _unit_errors = reconcile_quantitative(quant)
                    except Exception:  # noqa: BLE001 — 数值对账是加法
                        extra_contested = []
                if quant:
                    _atomic_write_text(out_dir / QUANTITATIVE_FILENAME, json.dumps(quant, ensure_ascii=False, indent=2))
                    meta["quantitative_count"] = len(quant)
                    plog.write("ok", f"extract-only: wrote {QUANTITATIVE_FILENAME} ({len(quant)} facts)")
                contested = _clean_optional_rows(obj.get("contested_claims")) + extra_contested
                if contested:
                    _atomic_write_text(out_dir / CONTESTED_FILENAME, json.dumps(contested, ensure_ascii=False, indent=2))
                    meta["contested_count"] = len(contested)
                    plog.write("ok", f"extract-only: wrote {CONTESTED_FILENAME} ({len(contested)} contested claims)")
                if isinstance(sources, list) and sources:
                    _atomic_write_text(out_dir / SOURCES_FILENAME, json.dumps(sources, ensure_ascii=False, indent=2))
                    meta["sources_count"] = len(sources)
                    meta["source_tiers"] = source_tier_histogram(sources)
                    plog.write("ok", f"extract-only: wrote {SOURCES_FILENAME} ({len(sources)} sources; tiers={meta['source_tiers']})")
        except Exception as _e:  # noqa: BLE001 — 抽取绝不令打捞失败（报告仍在）
            plog.write("warn", f"extract-only: structured extraction failed (non-fatal): {_e}")

    # 预测市场（与正常 Stage 3 一致的可选锚点；失败一行日志跳过）。
    try:
        _collect_prediction_markets(out_dir, question, report, meta, plog, model_name=args.model)
        write_meta()
    except Exception as _pm_err:  # noqa: BLE001 — 市场信号为可选增强
        plog.write("warn", f"extract-only: prediction markets skipped (non-fatal): {_pm_err}")

    # WAVE9-RQ4: 打捞路径同样补渲染研究图表（结构化工件刚补出来；与正常 Stage 4 一致，
    # degrade-safe——渲染失败绝不令打捞失败）。
    try:
        _render_research_charts(out_dir, meta, plog)
        write_meta()
    except Exception as _ch_err:  # noqa: BLE001 — 图表为可选增强
        plog.write("warn", f"extract-only: research charts skipped (non-fatal): {_ch_err}")

    meta.update(status="completed", finished_at=_utcnow())
    write_meta()
    plog.write("done", "extract-only complete")
    plog.close()
    return 0


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
    # ITEM-3：续跑。既存 research_checkpoint.json 且 question_hash 匹配时复用其 thread_id、
    # 跳过已完成 pass，从下一 pass 续跑（否则全量重启并打标）。
    parser.add_argument("--resume", action="store_true", help="Resume research from research_checkpoint.json (reuse thread, skip completed passes).")
    # ITEM-14：只抽取。跳过所有研究，只对既存 research_report.md 跑结构化抽取 + 预测市场；
    # 报告缺失/过小 → 诚实非零退出。用于 watchdog 超时打捞已落盘但缺结构化档案的报告。
    parser.add_argument("--extract-only", action="store_true", dest="extract_only", help="Skip research; only run structured extraction + prediction markets against an existing research_report.md.")
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
    # AGENTIC-SEARCH: 依 --subagents 打开研究提示词里的「主动委派 scoped-researcher」指令块。
    # 必须在 _reset_fetched_sources()（其把该标志复位 False）之后设置。仅当同时开启 --subagents
    # 与 RESEARCH_AGENTIC_SEARCH（默认 true，在 _agentic_delegation_block 内二次门控）才注入指令。
    _set_agentic_delegation(bool(getattr(args, "subagents", False)))
    thread_id = args.thread_id or f"research-{uuid.uuid4().hex[:12]}"
    # ITEM-3 续跑：--resume 且 checkpoint 存在、question_hash 匹配、depth 一致 → 复用记录的
    # thread_id（LangGraph checkpointer 仍持有该线程全部笔记），把已完成 pass 传给研究阶段跳过。
    # 否则（缺失/stale/hash 不匹配）→ 全量重启并把原因记入 meta（打标）。--extract-only 不续跑。
    resume_completed: set[str] = set()
    resume_info: dict[str, Any] = {}
    if getattr(args, "resume", False) and not getattr(args, "extract_only", False) and _checkpoint_enabled():
        _ckpt = load_research_checkpoint(out_dir)
        _plan = plan_research_resume(_ckpt, question, args.depth)
        if _plan["resume"]:
            thread_id = _plan["thread_id"]
            resume_completed = set(_plan["completed_passes"])
            resume_info = {"resumed": True, "thread_id": thread_id,
                           "skipped_passes": sorted(resume_completed)}
            plog.write("resume", f"续跑研究：复用线程 {thread_id}，跳过 {len(resume_completed)} 个已完成 pass")
        else:
            resume_info = {"resumed": False, "reason": _plan["reason"]}
            plog.write("resume", f"--resume 但无法续跑（{_plan['reason']}）→ 全量重启")
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
    if resume_info:
        meta["resume"] = resume_info
    if args.depth == "deep":
        meta["deep_research_phases"] = [
            # SCALE-2: 与 run_research_stage 的实际读值保持一致（开场默认 300；各 pass
            # 经 _phase_budget 应用 RESEARCH_PHASE_BUDGET_MULT）。
            {"label": "deep-opening", "recursion_limit": int(os.environ.get("DEERFLOW_DEEP_OPENING_RECURSION_LIMIT", "300"))},
            *[
                {"label": str(phase["label"]), "recursion_limit": _phase_budget(int(phase["recursion_limit"]))}
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

    # ITEM-14 --extract-only 前置门（在凭据校验/客户端构造之前，故 argparse 路由可零 LLM 测）：
    # 既存 research_report.md 必须存在且 ≥ _extract_only_min_chars()，否则诚实非零退出——
    # 打捞的前提是「已有一份可抽取的报告」，无报告/残段抽不出有用结构。
    if getattr(args, "extract_only", False):
        _rp = out_dir / REPORT_FILENAME
        _existing = _rp.read_text(encoding="utf-8") if _rp.exists() else ""
        _min_chars = _extract_only_min_chars()
        if len(_existing.strip()) < _min_chars:
            return _preflight_fail(
                f"--extract-only 需要既存的 {REPORT_FILENAME}（≥{_min_chars} 字符）："
                f"当前 {len(_existing.strip())} 字符，无法抽取。",
                "extract-only: missing/too-small research_report.md",
            )

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
        # ITEM-14：只抽取路径不构造研究客户端、不发起研究 turn（抽取/市场各自用裸模型）。
        # 前置门已确保 research_report.md 存在且够长。
        if getattr(args, "extract_only", False):
            return run_extract_only(question, out_dir, args, meta, plog, write_meta)

        plog.write("init", f"importing DeerFlow client (model={args.model})")
        from deerflow.client import DeerFlowClient

        client = DeerFlowClient(
            config_path=args.config,
            model_name=args.model,
            thinking_enabled=True,
            subagent_enabled=args.subagents,
        )
        plog.write("init", "client ready; available skills will load on demand (deep-research)")

        # --- PM-4: 研究开跑前的初始市场快照（仅用问题派生检索词）---
        # 把一段紧凑「当前市场定价」块注入 pass-0 提示词，让开场就带着「市场把 X 定在
        # NN%——去查为什么」的锚点搜；同一批市场也在 Stage 2 喂给结构化抽取（INT-1）。
        # Degrade-safe：任何市场失败 → 研究照常无块进行。
        try:
            if _env_flag("PREDICTION_MARKETS_ENABLED", True) and _env_flag("PREDICTION_MARKETS_PREPASS", True):
                _init_markets = _pm_initial_snapshot(question, args.model, plog)
                if _init_markets:
                    _set_initial_pm_markets(_init_markets)
                    _set_market_pricing_block(_pm_render_pricing_block(_init_markets, _utcnow()))
                    plog.write("ok", f"pre-pass market snapshot: injected {len(_init_markets)} market prices into pass-0 prompt")
        except Exception as _pm_pre_err:  # noqa: BLE001 — 初始快照为可选锚点
            plog.write("warn", f"pre-pass market snapshot skipped (non-fatal): {_pm_pre_err}")

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
                    resume_completed=resume_completed,  # ITEM-3 续跑：跳过已完成 pass
                    out_dir=out_dir,                    # ITEM-3：每完成一 pass 落 checkpoint
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
                resume_completed=resume_completed,  # ITEM-3 续跑：跳过已完成 pass
                out_dir=out_dir,                    # ITEM-3：每完成一 pass 落 checkpoint
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
        # SCALE-2: 触发线按深度取值 —— deep 15000 / 其余 4000（见 _synthesis_trigger_chars）。
        if len(_stripped) < _synthesis_trigger_chars(args.depth) and not _is_content_block:
            plog.write("warn", f"research turn returned only {len(_stripped)} chars (budget exhausted or a provider error on the final write); synthesizing tool-free from gathered research")
            synth = synthesize_from_thread(client, thread_id, question, args.target_language, args.model, plog, depth=args.depth)
            if len(synth.strip()) > len(_stripped):
                report = synth

        # RES-1: 最短报告门。一个无错误哨兵的短桩（如 MiniMax 0-tool-call 的 47 字符回合）
        # 能同时穿过 looks_like_llm_error 与 orchestrator 的 LIVE 守卫（:809 只拒 <400 且带
        # 错误标记），下游会拿桩建图并报成功。低于门槛按「无报告」诚实失败。0 = 关闭（回退旧行为）。
        # SCALE-2: deep 档的有效下限抬到 2000 —— 400 字符对多-pass 深研交付物形同虚设；
        # 显式设置的 RESEARCH_MIN_REPORT_CHARS 对所有深度照旧生效（含 0=关闭）。
        _min_report_default = 2000 if args.depth == "deep" else 400
        _min_report_raw = (os.environ.get("RESEARCH_MIN_REPORT_CHARS", "") or "").strip()
        try:
            _min_report = max(0, int(_min_report_raw)) if _min_report_raw else _min_report_default
        except ValueError:
            _min_report = _min_report_default
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
        # WAVE9-RQ2: 落盘前确定性引注校验——[S<n>] 必须解析到参考条目（报告自带 References
        # 节或本轮钉住的 SOURCE INDEX），悬空记号剔除并计数；绝不阻断落盘（degrade-safe）。
        try:
            report = finalize_report_citations(report, plog)
        except Exception as _cit_err:  # noqa: BLE001 — 引注校验为可选增强
            plog.write("warn", f"citation finalize skipped (non-fatal): {_cit_err}")
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
                # INT-1: 把初始预测市场表附到抽取输入，让市场隐含概率能落进 situation_brief/
                # catalysts/hot_topics（抽取提示词已指示如何消费）。无初始快照 → 逐字节不变。
                if _INITIAL_PM_MARKETS:
                    try:
                        extraction_input = (
                            extraction_input + "\n\n---\n\n"
                            + _pm_render_section(_INITIAL_PM_MARKETS, _utcnow())
                        )
                    except Exception:  # noqa: BLE001 — 附加市场表是加法，失败保留原输入
                        pass
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
                    # ACTOR-CAST DISCIPLINE: demote media/observer rows to context and cap the
                    # main cast at ACTOR_CAST_MAX (top-ranked by tier/salience/influence),
                    # recording any cut in meta (actors_truncated_from). Best-effort: a
                    # malformed obj must never fail the run.
                    try:
                        enforce_actor_cast(obj, meta, plog)
                    except Exception as _cast_err:  # noqa: BLE001 — discipline is additive
                        plog.write("warn", f"actor-cast discipline skipped (non-fatal): {_cast_err}")
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
                                # SCALE-2: 与覆盖门共用同一深度感知下限（deep 默认 45），
                                # grounding 分量的分母随门槛一起缩放。
                                _min_src = _research_min_sources(args.depth)
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

        # --- SCALE-5: 三角验证 top-up（仅 deep，默认开）---
        # 抽取阶段 triangulation audit 标出的单源载重声明，作为显式核验目标跑一次专门 pass +
        # 重合成，强化最关键声明的独立佐证。改进的报告改写回 research_report.md（下游报告阶段读文件即得）。
        # degrade-safe：无标记声明 / 任何失败 → 保留已落盘报告，绝不影响已产出的研究契约。
        try:
            _flagged = meta.get("single_origin_loadbearing")
            if args.depth == "deep" and _flagged and _env_flag("RESEARCH_TRIANGULATION_TOPUP", True):
                _new_report = run_triangulation_topup(
                    client, thread_id, question, args.depth, args.target_language, args.model,
                    report, _flagged, plog)
                if (_new_report.strip() and _new_report != report
                        and len(_new_report.strip()) >= len(report.strip())):
                    report = unwrap_markdown_fence(_new_report)
                    # WAVE9-RQ2: top-up 补写可能引入新的悬空 [S<n>] 记号 → 重校验（幂等：
                    # 已有 References 节则按该节校验，不重复追加）。
                    report = finalize_report_citations(report, plog)
                    _atomic_write_text(out_dir / REPORT_FILENAME, report)
                    meta["report_chars"] = len(report)
                    meta["triangulation_topup_applied"] = True
                    write_meta()
                    plog.write("ok", f"triangulation top-up: rewrote {REPORT_FILENAME} ({len(report)} chars)")
        except Exception as _tt_err:  # noqa: BLE001 — 三角 top-up 为可选增强
            plog.write("warn", f"triangulation top-up skipped (non-fatal): {_tt_err}")

        # --- Stage 3: 预测市场信号（Polymarket 公开 Gamma API，keyless；best effort）---
        # 报告与结构化抽取都已落盘后再抓取（可复用 actors.json 的 hot_topics/actor 名派生
        # 检索词）。市场隐含概率作为下游报告/预测的「校准锚点」（非真值）。Degrade-safe：
        # 无结果/网络错误 → 一行日志跳过，绝不影响已产出的研究契约。
        try:
            _collect_prediction_markets(out_dir, question, report, meta, plog, model_name=args.model)
            write_meta()
        except Exception as _pm_err:  # noqa: BLE001 — 市场信号为可选增强
            plog.write("warn", f"prediction markets skipped (non-fatal): {_pm_err}")

        # --- Stage 4: 研究图表（WAVE9-RQ4；forecast-visuals/scripts/render.py，确定性）---
        # 结构化工件都已落盘后直接子进程渲染 actor 网络/时间线/定量 Top 指标图，并把 PNG
        # 内嵌进 research_report.md 的 Visual Annex。Degrade-safe：python/plotly 缺失或
        # 渲染失败 → 一行日志跳过，绝不影响已产出的研究契约。
        try:
            _render_research_charts(out_dir, meta, plog)
            write_meta()
        except Exception as _ch_err:  # noqa: BLE001 — 图表为可选增强
            plog.write("warn", f"research charts skipped (non-fatal): {_ch_err}")

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
