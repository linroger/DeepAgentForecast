"""Central LLM call meter, run-correlation context, content-addressed cache, and
an optional per-run token/cost/time budget guard.

EXECPLAN2: I-5-0 (central meter), I-5-2 (run/stage correlation via contextvars),
I-6-6 (per-phase call/token/latency rollup), I-6-0 (content-addressed cache),
I-5-3 (budget guard). All optional-degrade: telemetry is cheap and on by default;
the cache and budget guard are off unless explicitly configured.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- run context
# Tag every LLM call with the run (pipeline/report id) and stage that issued it,
# so telemetry can be attributed without threading ids through every call.
_current_run: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_run_id", default=None)
_current_stage: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_stage", default=None)

_DEFAULT_BUCKET = "_global"


def set_run_context(run_id: Optional[str], stage: Optional[str] = None) -> None:
    _current_run.set(run_id)
    _current_stage.set(stage)


def set_stage(stage: Optional[str]) -> None:
    _current_stage.set(stage)


def get_run_context() -> Tuple[Optional[str], Optional[str]]:
    return _current_run.get(), _current_stage.get()


class BudgetExceeded(RuntimeError):
    """Raised when a run exceeds its configured token/cost budget."""


# ---------------------------------------------------------------- cost model
# Rough USD per 1K tokens (input, output). Unknown providers -> 0 (cost stays 0,
# token/latency accounting still works). Update as pricing changes.
_COST_PER_1K: Dict[str, Tuple[float, float]] = {
    "openai": (0.0050, 0.0150),
    "deepseek": (0.00027, 0.0011),
    "qwen": (0.0004, 0.0012),
    "glm": (0.0006, 0.0022),
    "minimax": (0.0003, 0.0011),
    "kimi": (0.0006, 0.0022),
    # OBS-1: proxy/aggregator-fronted models (e.g. a vibeproxy/antigravity stanza
    # fronting a gemini reasoning model through the OpenAI-compatible path). These
    # rates are NOT authoritative published prices — they are rough estimates so the
    # USD line is non-zero/plausible; flagged via _ESTIMATED_COST_PROVIDERS below.
    "gemini": (0.0003, 0.0025),
    "proxy": (0.0, 0.0),
    "antigravity": (0.0, 0.0),
    # CLI providers are subscription-based -> treat as 0 marginal cost
    "claude-cli": (0.0, 0.0),
    "codex-cli": (0.0, 0.0),
}

# OBS-1: providers whose per-1K pricing is a rough estimate (proxy/aggregator-fronted)
# rather than an authoritative published rate. Any provider absent from _COST_PER_1K is
# likewise treated as estimated (cost defaults to 0 but the figure is not billing-exact).
# Surfaced as ``cost_estimated`` in snapshots so status payloads don't present the USD
# total as authoritative.
_ESTIMATED_COST_PROVIDERS = frozenset({"gemini", "proxy", "antigravity"})


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    cin, cout = _COST_PER_1K.get(provider, (0.0, 0.0))
    return (prompt_tokens / 1000.0) * cin + (completion_tokens / 1000.0) * cout


def cost_is_estimated(provider: str) -> bool:
    """OBS-1: True when ``provider``'s USD cost is a rough estimate (proxy/aggregator
    fronted) or unknown (absent from the cost table), rather than an authoritative rate."""
    return provider in _ESTIMATED_COST_PROVIDERS or provider not in _COST_PER_1K


# ---------------------------------------------------------------- meter
@dataclass
class _Counter:
    calls: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0

    def add(self, prompt_tokens: int, completion_tokens: int, latency_ms: float,
            cost_usd: float, cached: bool) -> None:
        self.calls += 1
        if cached:
            self.cached += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.latency_ms += latency_ms
        self.cost_usd += cost_usd

    def as_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "cached": self.cached,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class _RunMeter:
    total: _Counter = field(default_factory=_Counter)
    by_stage: Dict[str, _Counter] = field(default_factory=dict)
    by_model: Dict[str, _Counter] = field(default_factory=dict)


# TEL-1: '_global' 桶只该接住零星的无归属调用（reset 从不清它，跨 run 累积）。它一旦变大，
# 说明 run 上下文没传播进某个工作线程（ThreadPoolExecutor 不继承 ContextVar）——届时
# spend/预算按 run 归集全部失真。达到这些调用数时各告警一次，让归属泄漏可见。
_GLOBAL_BUCKET_WARN_AT = (20, 100, 500, 2000)


class LLMMeter:
    """Process-wide, thread-safe accumulation of LLM usage keyed by run id."""

    _lock = threading.Lock()
    _runs: Dict[str, _RunMeter] = {}

    @classmethod
    def record(cls, provider: str, model: str, prompt_tokens: int, completion_tokens: int,
               latency_ms: float, *, cached: bool = False, stage: Optional[str] = None,
               run_id: Optional[str] = None) -> None:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        stg = stage or _current_stage.get() or "_unstaged"
        cost = 0.0 if cached else estimate_cost(provider, prompt_tokens, completion_tokens)
        warn_calls = 0
        with cls._lock:
            rm = cls._runs.setdefault(rid, _RunMeter())
            rm.total.add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
            rm.by_stage.setdefault(stg, _Counter()).add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
            rm.by_model.setdefault(f"{provider}:{model}", _Counter()).add(
                prompt_tokens, completion_tokens, latency_ms, cost, cached)
            if rid == _DEFAULT_BUCKET and rm.total.calls in _GLOBAL_BUCKET_WARN_AT:
                warn_calls = rm.total.calls
        if warn_calls:
            import logging
            logging.getLogger("mirofish.telemetry").warning(
                "LLM 计量落入 '_global' 桶已达 %d 次调用——run 上下文未传播到某工作线程"
                "（per-run 预算/成本归集失真；检查线程池是否用 contextvars.copy_context 提交任务）",
                warn_calls,
            )

    @classmethod
    def snapshot(cls, run_id: Optional[str] = None) -> Dict[str, Any]:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            rm = cls._runs.get(rid)
            if not rm:
                return {"run_id": rid, "total": _Counter().as_dict(), "by_stage": {},
                        "by_model": {}, "cost_estimated": False, "cost_basis": "api"}
            by_model = {k: v.as_dict() for k, v in rm.by_model.items()}
            # OBS-1: flag whether the aggregate USD figure is authoritative. by_model keys
            # are "provider:model"; once any token volume comes from a proxy/aggregator-
            # fronted or unlisted provider (gemini/proxy/antigravity/unknown), the modelled
            # cost is an estimate rather than a billing-exact rate, so surface that to the
            # status payload. (Zero-token model entries can't taint the total.)
            cost_estimated = any(
                cost_is_estimated(k.split(":", 1)[0]) and v.get("total_tokens", 0) > 0
                for k, v in by_model.items()
            )
            # XRUN-8: CLI 订阅提供方的 $0 不是「免费」而是「订阅内边际成本 0」。显式标注计价
            # 基准，避免 ~940K token 的报告 run 在成本审计里显得凭空免费。
            _sub = {"claude-cli", "codex-cli"}
            _vol_providers = {k.split(":", 1)[0] for k, v in by_model.items()
                              if v.get("total_tokens", 0) > 0}
            if not _vol_providers:
                cost_basis = "api"
            elif _vol_providers <= _sub:
                cost_basis = "subscription"
            elif _vol_providers & _sub:
                cost_basis = "mixed"
            else:
                cost_basis = "api"
            return {
                "run_id": rid,
                "total": rm.total.as_dict(),
                "by_stage": {k: v.as_dict() for k, v in rm.by_stage.items()},
                "by_model": by_model,
                "cost_estimated": cost_estimated,
                "cost_basis": cost_basis,
            }

    @classmethod
    def status_snapshot(cls, run_id: Optional[str] = None) -> Dict[str, Any]:
        """OBS-1: compact telemetry block for embedding in a pipeline status payload.

        Returns total + per-stage latency/tokens/cost plus the ``cost_estimated`` flag,
        omitting the verbose per-model breakdown. Callers (status endpoint /
        orchestrator) can splice this under a ``llm_telemetry`` key. Cheap and lock-safe.
        """
        snap = cls.snapshot(run_id)
        return {
            "run_id": snap["run_id"],
            "total": snap["total"],
            "by_stage": snap["by_stage"],
            "cost_estimated": snap["cost_estimated"],
        }

    @classmethod
    def reset(cls, run_id: Optional[str] = None) -> None:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            cls._runs.pop(rid, None)

    @classmethod
    def write_run_telemetry(cls, path: str, run_id: Optional[str] = None,
                            extra: Optional[Dict[str, Any]] = None) -> None:
        """Persist a run's telemetry to ``path`` atomically (I-5-1).

        XRUN-8: resume/re-report 会对同一管线写多次；此前直接覆盖会让上一 attempt 的成本
        （以及它指向的 report_id）凭空消失，跨 run 的 token 审计对不上账。改为合并：保留上一
        attempt 的 total/report_id 摘要（previous_attempt），并滚动累计 cumulative_total，
        使文件既反映「本 attempt」又反映「整条管线」的真实开销。首写行为不变。
        """
        import os as _os
        from .atomic import write_json_atomic
        data = cls.snapshot(run_id)
        if extra:
            data.update(extra)
        try:
            if _os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                if isinstance(prev, dict) and (prev.get("total") or {}).get("calls"):
                    data["previous_attempt"] = {
                        "total": prev.get("total"),
                        "report_id": prev.get("report_id"),
                        "status": prev.get("status"),
                    }
                    base = prev.get("cumulative_total") or prev.get("total") or {}
                    cur = data.get("total") or {}
                    cum: Dict[str, Any] = {}
                    for k in ("calls", "cached", "prompt_tokens", "completion_tokens",
                              "total_tokens", "latency_ms", "cost_usd"):
                        try:
                            cum[k] = round((base.get(k) or 0) + (cur.get(k) or 0), 6)
                        except TypeError:
                            continue
                    data["cumulative_total"] = cum
        except Exception:  # noqa: BLE001 — 合并是观测增益，失败退回单 attempt 覆盖写
            pass
        write_json_atomic(path, data)


# ---------------------------------------------------------------- budget guard
def check_budget(run_id: Optional[str] = None) -> None:
    """Raise :class:`BudgetExceeded` if the run is over its configured budget (I-5-3).

    Limits come from Config (0/unset = unlimited). Cheap; called after each LLM
    call so a runaway run aborts instead of silently burning the whole budget.
    """
    from ..config import Config
    max_tokens = int(getattr(Config, "LLM_RUN_BUDGET_TOKENS", 0) or 0)
    max_cost = float(getattr(Config, "LLM_RUN_BUDGET_USD", 0) or 0)
    if max_tokens <= 0 and max_cost <= 0:
        return
    snap = LLMMeter.snapshot(run_id)["total"]
    if max_tokens > 0 and snap["total_tokens"] > max_tokens:
        raise BudgetExceeded(
            f"run exceeded token budget: {snap['total_tokens']} > {max_tokens}")
    if max_cost > 0 and snap["cost_usd"] > max_cost:
        raise BudgetExceeded(
            f"run exceeded cost budget: ${snap['cost_usd']:.4f} > ${max_cost:.4f}")


# ---------------------------------------------------------------- LLM cache
def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) when a provider returns no usage."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class LLMCache:
    """Content-addressed in-memory cache of identical chat() calls (I-6-0).

    Keyed by (provider, model, messages, temperature, max_tokens, response_format).
    Off unless ``Config.LLM_CACHE_ENABLED``. In-memory only (bounded) — identical
    decomposition/extraction calls across a pipeline return instantly and free.
    """

    _lock = threading.Lock()
    _store: "Dict[str, str]" = {}
    _order: List[str] = []
    _max_entries = 2048

    @classmethod
    def key(cls, provider: str, model: str, messages: Any, temperature: float,
            max_tokens: int, response_format: Any) -> str:
        payload = json.dumps(
            [provider, model, messages, temperature, max_tokens, response_format],
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def get(cls, key: str) -> Optional[str]:
        with cls._lock:
            return cls._store.get(key)

    @classmethod
    def put(cls, key: str, value: str) -> None:
        with cls._lock:
            if key not in cls._store:
                cls._order.append(key)
                if len(cls._order) > cls._max_entries:
                    evict = cls._order.pop(0)
                    cls._store.pop(evict, None)
            cls._store[key] = value
