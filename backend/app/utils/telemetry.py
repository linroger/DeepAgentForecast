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
    # CLI providers are subscription-based -> treat as 0 marginal cost
    "claude-cli": (0.0, 0.0),
    "codex-cli": (0.0, 0.0),
}


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    cin, cout = _COST_PER_1K.get(provider, (0.0, 0.0))
    return (prompt_tokens / 1000.0) * cin + (completion_tokens / 1000.0) * cout


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
        with cls._lock:
            rm = cls._runs.setdefault(rid, _RunMeter())
            rm.total.add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
            rm.by_stage.setdefault(stg, _Counter()).add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
            rm.by_model.setdefault(f"{provider}:{model}", _Counter()).add(
                prompt_tokens, completion_tokens, latency_ms, cost, cached)

    @classmethod
    def snapshot(cls, run_id: Optional[str] = None) -> Dict[str, Any]:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            rm = cls._runs.get(rid)
            if not rm:
                return {"run_id": rid, "total": _Counter().as_dict(), "by_stage": {}, "by_model": {}}
            return {
                "run_id": rid,
                "total": rm.total.as_dict(),
                "by_stage": {k: v.as_dict() for k, v in rm.by_stage.items()},
                "by_model": {k: v.as_dict() for k, v in rm.by_model.items()},
            }

    @classmethod
    def reset(cls, run_id: Optional[str] = None) -> None:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            cls._runs.pop(rid, None)

    @classmethod
    def write_run_telemetry(cls, path: str, run_id: Optional[str] = None,
                            extra: Optional[Dict[str, Any]] = None) -> None:
        """Persist a run's telemetry to ``path`` atomically (I-5-1)."""
        from .atomic import write_json_atomic
        data = cls.snapshot(run_id)
        if extra:
            data.update(extra)
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
