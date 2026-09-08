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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .usage_ledger import (
    METRICS, UsageLedger, UsageLedgerBudgetExceeded, UsageLedgerConflict, UsageLedgerStorageError, UsageLedgerUnresolvedError,
    empty_counter, normalize_counter,
)

# ---------------------------------------------------------------- run context
# Tag every LLM call with the run (pipeline/report id) and stage that issued it,
# so telemetry can be attributed without threading ids through every call.
_current_run: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_run_id", default=None)
_current_stage: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_stage", default=None)

_DEFAULT_BUCKET = "_global"

# FOG-TEL-1: 进程内「活跃 run」注册表（run_id → 登记时刻）。ThreadPoolExecutor 工作线程
# 不继承 contextvars——graphiti 抽取等池线程里的 LLM 调用会失去 run 归属而落 '_global'
# 桶（既不落任何 run 工件，也逃过 per-run 预算；2026-07 取证：graph 阶段 100+ 分钟持续
# 抽取在 telemetry.json 里 calls=0/tokens=0）。该注册表让 record()/check_budget() 在
# 「恰好一个 run 在飞」时把无归属调用回退归属到该 run（fallback attribution，另计
# fallback_attributed 计数器以示区分）；0 个或 ≥2 个活跃 run 时归属真正含糊，保持旧的
# '_global' 行为。登记：set_run_context(run_id) 时；注销：LLMMeter.reset(run_id)
# （管线终局路径），或本线程 set_run_context(None) 还原时注销该线程当前的 run
# （独立报告生成的 finally 路径）。
_ACTIVE_LOCK = threading.Lock()
_active_runs: Dict[str, float] = {}


def set_run_context(run_id: Optional[str], stage: Optional[str] = None) -> None:
    if run_id:
        with _ACTIVE_LOCK:
            _active_runs[run_id] = time.time()
    else:
        # 清空上下文视作「本线程归还其 run」：若无其它归属证据（LLMMeter.reset 才是
        # 权威终局），也把该 run 从活跃表摘除，避免独立报告等短生命周期 run 永久滞留、
        # 稀释单活跃 run 的回退归属。
        prev = _current_run.get()
        if prev:
            with _ACTIVE_LOCK:
                _active_runs.pop(prev, None)
    _current_run.set(run_id)
    _current_stage.set(stage)


def set_stage(stage: Optional[str]) -> None:
    _current_stage.set(stage)


def get_run_context() -> Tuple[Optional[str], Optional[str]]:
    return _current_run.get(), _current_stage.get()


def active_run_ids() -> List[str]:
    """FOG-TEL-1: run ids currently registered as active in this process."""
    with _ACTIVE_LOCK:
        return list(_active_runs)


def _sole_active_run() -> Optional[str]:
    """Return the single active run id, or None when zero / 2+ runs are active."""
    with _ACTIVE_LOCK:
        if len(_active_runs) == 1:
            return next(iter(_active_runs))
    return None


def resolve_run_attribution(run_id: Optional[str] = None,
                            stage: Optional[str] = None) -> Tuple[str, str, bool]:
    """Pin attribution once for a request spanning dispatch and settlement.

    The active-run registry can change while a provider is working. Returning
    the resolved bucket and inference flag lets every observation for the same
    physical attempt retain its original ownership without changing contextvars.
    The flag describes inferred run ownership, not provider failover.
    """
    rid = run_id or _current_run.get()
    inferred = False
    if not rid:
        rid = _sole_active_run()
        inferred = rid is not None
    rid = rid or _DEFAULT_BUCKET
    return rid, stage or _current_stage.get() or LLMMeter.default_stage(rid) or "_unstaged", inferred


def _clear_active_runs() -> None:
    """Test/maintenance helper: forget all active-run registrations."""
    with _ACTIVE_LOCK:
        _active_runs.clear()


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


# ITEM-18: per-provider $/Mtok 成本表可经 env 覆盖（Config.LLM_COST_PER_MTOK，JSON），形如
# {"openai": [5.0, 15.0], "myprovider": [0.5, 1.5]}（每百万 token 的 [输入, 输出] 美元价）。
# 解析后转成「每 1K token」并叠加在内建保守默认 _COST_PER_1K 之上（同名覆盖、新名新增）。env
# 未设/解析失败 → 空覆盖，estimate_cost 行为与历史完全一致（degrade-safe）。既不在覆盖表也不在
# 内建默认的未知 provider → (0,0)：只计 token，不臆测成本。按原始 env 串缓存，避免每次调用重解析。
_COST_OVERRIDE_CACHE: Dict[str, Dict[str, Tuple[float, float]]] = {}


def _cost_overrides() -> Dict[str, Tuple[float, float]]:
    """ITEM-18：解析 Config.LLM_COST_PER_MTOK（$/Mtok JSON）为 {provider(lower): ($/1K in, out)}。"""
    try:
        from ..config import Config
        raw = (getattr(Config, "LLM_COST_PER_MTOK", "") or "").strip()
    except Exception:  # noqa: BLE001 — 配置不可用时退回无覆盖
        return {}
    if not raw:
        return {}
    cached = _COST_OVERRIDE_CACHE.get(raw)
    if cached is not None:
        return cached
    out: Dict[str, Tuple[float, float]] = {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for prov, pair in parsed.items():
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    try:
                        cin = float(pair[0]) / 1000.0   # $/Mtok → $/1K
                        cout = float(pair[1]) / 1000.0
                    except (TypeError, ValueError):
                        continue
                    out[str(prov).strip().lower()] = (cin, cout)
    except (ValueError, TypeError):
        out = {}
    _COST_OVERRIDE_CACHE[raw] = out
    return out


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    # ITEM-18: 先查 env 覆盖表（区分大小写命中 → 再退小写），未命中回退内建默认，仍无 → (0,0)。
    ov = _cost_overrides()
    if provider in ov:
        cin, cout = ov[provider]
    elif provider and provider.lower() in ov:
        cin, cout = ov[provider.lower()]
    else:
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

    def add_delta(self, delta: Dict[str, Any]) -> None:
        for key in ("calls", "cached", "prompt_tokens", "completion_tokens", "latency_ms", "cost_usd"):
            setattr(self, key, getattr(self, key) + delta[key])


@dataclass
class _RunMeter:
    total: _Counter = field(default_factory=_Counter)
    by_stage: Dict[str, _Counter] = field(default_factory=dict)
    by_model: Dict[str, _Counter] = field(default_factory=dict)
    # FOG-TEL-1: 无 run 上下文、经「单活跃 run」回退归属到本 run 的那部分（total 的子集，
    # 单独累计使推断归属与显式归属可区分/可审计）。
    fallback: _Counter = field(default_factory=_Counter)
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    operations: Dict[Tuple[str, str], dict] = field(default_factory=dict)
    quoted_cost_observed: bool = False


@dataclass(frozen=True)
class _DurableRun:
    ledger: UsageLedger
    attempt_id: str
    default_stage: Optional[str] = None
    operation_scope: Optional[str] = None


# TEL-1: '_global' 桶只该接住零星的无归属调用（reset 从不清它，跨 run 累积）。它一旦变大，
# 说明 run 上下文没传播进某个工作线程（ThreadPoolExecutor 不继承 ContextVar）——届时
# spend/预算按 run 归集全部失真。达到这些调用数时各告警一次，让归属泄漏可见。
_GLOBAL_BUCKET_WARN_AT = (20, 100, 500, 2000)


class LLMMeter:
    """Process-wide, thread-safe accumulation of LLM usage keyed by run id."""

    _lock = threading.Lock()
    _runs: Dict[str, _RunMeter] = {}
    _durable: Dict[str, _DurableRun] = {}
    _durable_failures: Dict[str, str] = {}
    _write_lock = threading.Lock()

    @classmethod
    def is_durable_run(cls, run_id: Optional[str] = None) -> bool:
        """Inspect the local binding without scanning or creating storage."""
        rid = run_id or _current_run.get() or _sole_active_run() or _DEFAULT_BUCKET
        with cls._lock:
            return rid in cls._durable

    @classmethod
    def durable_binding(cls, run_id: str) -> Optional[Dict[str, str]]:
        """Copy explicit storage ownership for a trusted subprocess launcher."""
        with cls._lock:
            binding = cls._durable.get(run_id)
            return ({"run_id": run_id, "ledger_path": str(binding.ledger.path),
                     "attempt_id": binding.attempt_id} if binding else None)

    @classmethod
    def default_stage(cls, run_id: str) -> Optional[str]:
        """Explicit process binding fallback for executor threads without context."""
        with cls._lock:
            binding = cls._durable.get(run_id)
            return binding.default_stage if binding else None

    @classmethod
    def _remember_accounting_failure(cls, run_id: str) -> None:
        # Keep no exception traceback: it can retain provider response bodies.
        with cls._lock:
            if run_id in cls._durable:
                cls._durable_failures[run_id] = "Durable accounting failed during this attempt"

    @classmethod
    def assert_accounting_available(cls, run_id: Optional[str] = None) -> None:
        """Stop new provider attempts after a durable accounting failure.

        A successful later read cannot prove that a failed usage observation
        was recovered. The stop persists until explicit reset/new attempt.
        Already-in-flight responses may still settle their observations.
        """
        cls._assert_accounting(run_id, require_settled=False)

    @classmethod
    def new_operation_id(cls, run_id: str) -> str:
        """Give child dispatches a durable launch scope without changing owner."""
        with cls._lock:
            binding = cls._durable.get(run_id)
            scope = binding.operation_scope if binding else None
        return f"{scope}:{uuid.uuid4().hex}" if scope else uuid.uuid4().hex

    @classmethod
    def assert_local_accounting_settled(cls, run_id: str) -> None:
        """Finish one launched child while allowing active sibling API work."""
        cls.assert_accounting_available(run_id)
        with cls._lock:
            binding = cls._durable.get(run_id)
        if binding is None:
            return
        if not binding.operation_scope:
            raise UsageLedgerConflict("Local completion requires an explicit operation scope")
        try:
            binding.ledger.assert_scope_settled(run_id, binding.attempt_id, binding.operation_scope)
        except UsageLedgerUnresolvedError:
            raise
        except UsageLedgerStorageError:
            cls._remember_accounting_failure(run_id)
            raise

    @classmethod
    def assert_accounting_settled(cls, run_id: Optional[str] = None) -> None:
        """Reject completion while an API call is in flight or has invalid usage."""
        cls._assert_accounting(run_id, require_settled=True)

    @classmethod
    def _assert_accounting(cls, run_id: Optional[str], *, require_settled: bool) -> None:
        rid = run_id or _current_run.get() or _sole_active_run() or _DEFAULT_BUCKET
        with cls._lock:
            binding = cls._durable.get(rid)
            failure = cls._durable_failures.get(rid)
        if binding is None:
            return
        if failure:
            raise UsageLedgerStorageError(failure)
        try:
            binding.ledger.assert_available(rid, current_attempt_id=binding.attempt_id,
                                            require_settled=require_settled)
        except UsageLedgerUnresolvedError:
            # A later precise settlement can release this policy stop. Unlike
            # a failed storage write, it must not poison the process-local latch.
            raise
        except UsageLedgerStorageError:
            cls._remember_accounting_failure(rid)
            raise

    @classmethod
    def attach_durable_run(cls, run_id: str, ledger_path: str,
                           attempt_id: Optional[str] = None,
                           legacy_snapshot: Optional[Dict[str, Any]] = None, *,
                           existing_only: bool = False,
                           default_stage: Optional[str] = None,
                           operation_scope: Optional[str] = None) -> str:
        """Bind explicitly before provider work; never infer a filesystem path.

        Reattaching the same binding is idempotent. A new attempt requires reset
        so two live owners cannot silently move each other's attribution.
        """
        ledger = UsageLedger(ledger_path)
        if operation_scope is not None and (not isinstance(operation_scope, str) or not operation_scope
                                           or len(operation_scope) > 128 or not operation_scope.isalnum()):
            raise ValueError("Invalid accounting operation scope")
        with cls._lock:
            existing = cls._durable.get(run_id)
            if existing is not None:
                if existing.ledger.path != ledger.path or (attempt_id is not None and attempt_id != existing.attempt_id):
                    raise UsageLedgerConflict("Run already has a different durable binding")
                if existing.operation_scope != operation_scope or existing.default_stage != default_stage:
                    raise UsageLedgerConflict("Run already has a different process accounting scope")
                return existing.attempt_id
            rm = cls._runs.get(run_id)
            if rm is not None and any(rm.total.as_dict().values()):
                raise UsageLedgerConflict("Attach durable accounting before recording usage")
            selected_attempt = attempt_id or uuid.uuid4().hex
            if not isinstance(selected_attempt, str) or not selected_attempt or len(selected_attempt) > 512:
                raise ValueError("Invalid accounting attempt identity")
            # Initialization happens while binding is locked, ensuring no local
            # record can slip into the memory bucket between import and attach.
            if existing_only:
                if legacy_snapshot is not None:
                    raise ValueError("Existing-only attachment cannot import a baseline")
                # Uses mode=ro; neither this read nor later writes create storage.
                ledger.snapshot(run_id)
            else:
                ledger.initialize(run_id, legacy_snapshot)
            cls._durable[run_id] = _DurableRun(ledger, selected_attempt, default_stage, operation_scope)
            return selected_attempt

    @classmethod
    def cumulative_snapshot(cls, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return all durable attempts, or None only for an unbound run."""
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            binding = cls._durable.get(rid)
        if binding is None:
            return None
        try:
            return binding.ledger.snapshot(rid, current_attempt_id=binding.attempt_id)
        except UsageLedgerStorageError:
            cls._remember_accounting_failure(rid)
            raise

    @classmethod
    def cumulative_budget_totals(cls, run_id: Optional[str] = None, *,
                                 include_cost: bool) -> Optional[Dict[str, Any]]:
        """Return narrow durable budget totals, or None only for an unbound run."""
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            binding = cls._durable.get(rid)
            failure = cls._durable_failures.get(rid)
        if binding is None:
            return None
        if failure:
            raise UsageLedgerStorageError(failure)
        try:
            return binding.ledger.budget_totals(rid, include_cost=include_cost)
        except UsageLedgerStorageError:
            cls._remember_accounting_failure(rid)
            raise

    @classmethod
    def record_snapshot(cls, source: str, operation_id: str, provider: str, model: str,
                        prompt_tokens: int, completion_tokens: int, latency_ms: float, *,
                        run_id: Optional[str] = None, stage: Optional[str] = None,
                        calls: int = 1, cached: int = 0, status: str = "completed",
                        usage_source: str = "unknown", billing_basis: Optional[str] = None,
                        cache_read_tokens: int = 0, cache_write_tokens: int = 0,
                        uncached_tokens: Optional[int] = None,
                        cost_usd: Optional[float] = None,
                        cost_quote: Optional[Dict[str, Any]] = None,
                        baseline_included: bool = False,
                        token_reservation: Optional[Dict[str, Any]] = None,
                        _fallback: bool = False) -> Dict[str, Any]:
        """Reconcile one stable cumulative observation, never an anonymous sum.

        A new attempt receives only positive growth discovered during that
        attempt. Initial ownership of an old operation remains unchanged. The
        explicit legacy seed flag suppresses only first-import credit.
        """
        rid = run_id or _current_run.get()
        fallback = _fallback
        if not rid:
            rid = _sole_active_run()
            fallback = rid is not None
        rid = rid or _DEFAULT_BUCKET
        stg = stage or _current_stage.get() or cls.default_stage(rid) or "_unstaged"
        with cls._lock:
            bound_at_validation = rid in cls._durable
        try:
            selected_quote = None
            if cost_quote is not None:
                from .api_cost import quote_cost, validate_cost_quote
                if source != "llm_api_attempt" or cost_usd is not None:
                    raise ValueError("An API cost quote cannot accompany another source or explicit cost")
                selected_quote = validate_cost_quote(cost_quote, provider=provider, model=model)
                observed_cost = quote_cost(selected_quote, prompt_tokens, completion_tokens)
            else:
                observed_cost = cost_usd if cost_usd is not None else (0.0 if cached else estimate_cost(provider, prompt_tokens, completion_tokens))
            usage_class = {"provider": "known", "reported": "known", "estimate": "estimated",
                           "missing": "unknown", "legacy": "unknown"}.get(usage_source, usage_source)
            if usage_class not in {"known", "estimated", "unknown", "mixed"}:
                raise ValueError("Invalid usage source")
            basis = billing_basis or ("subscription" if provider in {"claude-cli", "codex-cli"} else "api")
            counters = normalize_counter({
                "calls": calls, "cached": cached, "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens, "latency_ms": latency_ms,
                "cost_usd": observed_cost,
                "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens,
                "uncached_tokens": uncached_tokens if uncached_tokens is not None else 0,
            })
            metadata = {"stage": stg, "provider": provider, "model": model,
                        "usage_class": usage_class, "usage_source": usage_source,
                        "billing_basis": basis, "cost_estimated": cost_usd is None,
                        "fallback": fallback, "cache_partition_known": uncached_tokens is not None}
            if selected_quote is not None:
                metadata["cost_quote"] = selected_quote
        except (ValueError, TypeError, OverflowError) as exc:
            if not bound_at_validation:
                raise
            cls._remember_accounting_failure(rid)
            raise UsageLedgerStorageError("Invalid observation for durable accounting") from exc
        with cls._lock:
            binding = cls._durable.get(rid)
            if binding is None:
                if token_reservation is not None:
                    raise BudgetExceeded("Token reservation requires a durable run binding")
                if baseline_included:
                    raise UsageLedgerConflict("Legacy baseline seeding requires durable accounting")
                rm = cls._runs.setdefault(rid, _RunMeter())
                key = (source, operation_id)
                previous_record = rm.operations.get(key)
                previous = previous_record["counter"] if previous_record else empty_counter()
                if previous_record and any(previous_record["metadata"][k] != metadata[k] for k in ("stage", "provider", "model", "billing_basis", "fallback")):
                    raise UsageLedgerConflict("Usage operation identity has different attribution")
                if previous_record and previous_record["metadata"].get("cost_quote") != selected_quote:
                    raise UsageLedgerConflict("Usage operation identity has a different cost quote")
                highwater = {k: max(previous[k], counters[k]) for k in METRICS}
                highwater["total_tokens"] = highwater["prompt_tokens"] + highwater["completion_tokens"]
                if selected_quote is not None:
                    highwater["cost_usd"] = quote_cost(selected_quote, highwater["prompt_tokens"], highwater["completion_tokens"])
                delta = {k: highwater[k] - previous[k] for k in METRICS}
                rm.operations[key] = {"counter": highwater, "metadata": metadata}
                if selected_quote is not None and (highwater["calls"] or highwater["total_tokens"]):
                    rm.quoted_cost_observed = True
                rm.total.add_delta(delta)
                rm.by_stage.setdefault(stg, _Counter()).add_delta(delta)
                rm.by_model.setdefault(f"{provider}:{model}", _Counter()).add_delta(delta)
                if fallback:
                    rm.fallback.add_delta(delta)
                return delta
        try:
            return binding.ledger.record_snapshot(
                run_id=rid, attempt_id=binding.attempt_id, source=source, operation_id=operation_id,
                metadata=metadata, counters=counters, status=status, baseline_included=baseline_included,
                token_reservation=token_reservation)
        except UsageLedgerBudgetExceeded as exc:
            raise BudgetExceeded(str(exc)) from exc
        except UsageLedgerUnresolvedError:
            raise
        except (UsageLedgerStorageError, UsageLedgerConflict):
            cls._remember_accounting_failure(rid)
            raise
        except (ValueError, TypeError, OverflowError) as exc:
            cls._remember_accounting_failure(rid)
            raise UsageLedgerStorageError("Invalid attribution for durable accounting") from exc

    @classmethod
    def record(cls, provider: str, model: str, prompt_tokens: int, completion_tokens: int,
               latency_ms: float, *, cached: bool = False, stage: Optional[str] = None,
               run_id: Optional[str] = None) -> None:
        rid = run_id or _current_run.get()
        fallback = False
        if not rid:
            # FOG-TEL-1: 无 run 上下文（典型：ThreadPoolExecutor 工作线程未继承 contextvars）
            # 且进程内恰好只有一个 run 在飞 → 回退归属到该 run，另计 fallback 计数器。
            # 0 个或 ≥2 个活跃 run 时归属含糊，保持旧的 '_global' 行为。
            rid = _sole_active_run()
            fallback = rid is not None
        if not rid:
            rid = _DEFAULT_BUCKET
        stg = stage or _current_stage.get() or cls.default_stage(rid) or "_unstaged"
        warn_calls = 0
        first_fallback = False
        with cls._lock:
            binding = cls._durable.get(rid)
            if binding is None:
                cost = 0.0 if cached else estimate_cost(provider, prompt_tokens, completion_tokens)
                rm = cls._runs.setdefault(rid, _RunMeter())
                rm.total.add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
                rm.by_stage.setdefault(stg, _Counter()).add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
                rm.by_model.setdefault(f"{provider}:{model}", _Counter()).add(
                    prompt_tokens, completion_tokens, latency_ms, cost, cached)
                if fallback:
                    rm.fallback.add(prompt_tokens, completion_tokens, latency_ms, cost, cached)
                    first_fallback = rm.fallback.calls == 1
                if rid == _DEFAULT_BUCKET and rm.total.calls in _GLOBAL_BUCKET_WARN_AT:
                    warn_calls = rm.total.calls
        if binding is not None:
            cls.record_snapshot("llm_call", uuid.uuid4().hex, provider, model,
                                prompt_tokens, completion_tokens, latency_ms,
                                cached=int(cached), run_id=rid, stage=stg,
                                usage_source="known" if cached else "unknown", _fallback=fallback)
            return
        if first_fallback:
            import logging
            logging.getLogger("mirofish.telemetry").info(
                "LLM 计量缺 run 上下文，已回退归属到唯一活跃 run %s"
                "（快照另计 fallback_attributed；预算按该 run 生效）", rid)
        if warn_calls:
            import logging
            logging.getLogger("mirofish.telemetry").warning(
                "LLM 计量落入 '_global' 桶已达 %d 次调用——run 上下文未传播到某工作线程"
                "（per-run 预算/成本归集失真；检查线程池是否用 contextvars.copy_context 提交任务）",
                warn_calls,
            )

    @classmethod
    def snapshot(cls, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Per-run usage snapshot. Additive keys (existing keys keep their meaning):

        - ``fallback_attributed`` (FOG-TEL-1): the subset of ``total`` that was inferred
          onto this run via single-active-run fallback (no run contextvar on the calling
          thread); all zeros when attribution was always explicit.
        - ``unattributed_process`` (FOG-TEL-1): the process-wide '_global' bucket totals
          at snapshot time — spend whose attribution stayed ambiguous (zero or 2+ active
          runs). Cumulative for the process lifetime (per-run reset never clears it) and
          shared across concurrent runs; carried on every run snapshot so persisted
          artifacts (run_telemetry.json) can never hide unattributed spend. Omitted only
          when snapshotting the '_global' bucket itself (it would duplicate ``total``).
        """
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._lock:
            binding = cls._durable.get(rid)
            global_run = cls._runs.get(_DEFAULT_BUCKET)
            global_total = global_run.total.as_dict() if global_run else _Counter().as_dict()
        if binding is not None:
            try:
                out = binding.ledger.snapshot(rid, attempt_id=binding.attempt_id,
                                               current_attempt_id=binding.attempt_id)
            except UsageLedgerStorageError:
                cls._remember_accounting_failure(rid)
                raise
            if rid != _DEFAULT_BUCKET:
                out["unattributed_process"] = global_total
            return out
        with cls._lock:
            g = cls._runs.get(_DEFAULT_BUCKET)
            unattributed = g.total.as_dict() if g else _Counter().as_dict()
            rm = cls._runs.get(rid)
            if not rm:
                out: Dict[str, Any] = {
                    "run_id": rid, "total": _Counter().as_dict(), "by_stage": {},
                    "by_model": {}, "cost_estimated": False, "cost_basis": "api",
                    "fallback_attributed": _Counter().as_dict(),
                }
                if rid != _DEFAULT_BUCKET:
                    out["unattributed_process"] = unattributed
                return out
            by_model = {k: v.as_dict() for k, v in rm.by_model.items()}
            # OBS-1: flag whether the aggregate USD figure is authoritative. by_model keys
            # are "provider:model"; once any token volume comes from a proxy/aggregator-
            # fronted or unlisted provider (gemini/proxy/antigravity/unknown), the modelled
            # cost is an estimate rather than a billing-exact rate, so surface that to the
            # status payload. (Zero-token model entries can't taint the total.)
            cost_estimated = any(
                cost_is_estimated(k.split(":", 1)[0]) and v.get("total_tokens", 0) > 0
                for k, v in by_model.items()
            ) or rm.quoted_cost_observed
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
            out = {
                "run_id": rid,
                "total": rm.total.as_dict(),
                "by_stage": {k: v.as_dict() for k, v in rm.by_stage.items()},
                "by_model": by_model,
                "cost_estimated": cost_estimated,
                "cost_basis": cost_basis,
                "fallback_attributed": rm.fallback.as_dict(),
            }
            if rid != _DEFAULT_BUCKET:
                out["unattributed_process"] = unattributed
            return out

    @classmethod
    def status_snapshot(cls, run_id: Optional[str] = None) -> Dict[str, Any]:
        """OBS-1: compact telemetry block for embedding in a pipeline status payload.

        Returns total + per-stage latency/tokens/cost plus the ``cost_estimated`` flag,
        omitting the verbose per-model breakdown. Callers (status endpoint /
        orchestrator) can splice this under a ``llm_telemetry`` key. Cheap and lock-safe.
        """
        snap = cls.snapshot(run_id)
        result = {
            "run_id": snap["run_id"],
            "total": snap["total"],
            "by_stage": snap["by_stage"],
            "cost_estimated": snap["cost_estimated"],
        }
        if "api_operation_state" in snap:
            result["api_operation_state"] = snap["api_operation_state"]
        if "token_reservation_state" in snap:
            result["token_reservation_state"] = snap["token_reservation_state"]
        return result

    @classmethod
    def reset(cls, run_id: Optional[str] = None) -> None:
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        # FOG-TEL-1: reset 是 run 的权威终局（管线 finally 调用）——同时注销活跃登记，
        # 让后续单活跃 run 的回退归属不再考虑它。
        with _ACTIVE_LOCK:
            _active_runs.pop(rid, None)
        with cls._lock:
            cls._runs.pop(rid, None)
            cls._durable.pop(rid, None)
            cls._durable_failures.pop(rid, None)

    @classmethod
    def write_run_telemetry(cls, path: str, run_id: Optional[str] = None,
                            extra: Optional[Dict[str, Any]] = None) -> None:
        """Write a compatibility projection without adding one attempt twice.

        Durable runs use the ledger's cumulative projection. Unbound legacy
        callers retain a stable in-memory attempt ID and a fixed persisted base;
        repeated writes replace that attempt's snapshot instead of adding it.
        """
        import os as _os
        from .atomic import write_json_atomic
        rid = run_id or _current_run.get() or _DEFAULT_BUCKET
        with cls._write_lock:
            data = cls.snapshot(rid)
            with cls._lock:
                binding = cls._durable.get(rid)
                attempt_id = binding.attempt_id if binding else cls._runs.setdefault(rid, _RunMeter()).attempt_id
            previous = None
            if _os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    previous = json.load(f)
                if not isinstance(previous, dict):
                    raise UsageLedgerStorageError("Invalid previous telemetry projection")
            base = {}
            previous_attempt = None
            if previous:
                if previous.get("attempt_id") == attempt_id:
                    base = previous.get("attempt_base_total") or {}
                    previous_attempt = previous.get("previous_attempt")
                else:
                    base = previous.get("cumulative_total") or previous.get("total") or {}
                    previous_attempt = {"total": previous.get("total"),
                                        "report_id": previous.get("report_id"), "status": previous.get("status")}
            if extra:
                data.update(extra)
            data["attempt_id"] = attempt_id
            data["attempt_base_total"] = base
            if previous_attempt:
                data["previous_attempt"] = previous_attempt
            cumulative = cls.cumulative_snapshot(rid)
            if cumulative is not None:
                data["cumulative_total"] = cumulative["total"]
                data["cumulative_by_stage"] = cumulative["by_stage"]
                data["api_operation_state"] = cumulative["api_operation_state"]
                data["token_reservation_state"] = cumulative["token_reservation_state"]
            else:
                current = data.get("total") or {}
                data["cumulative_total"] = {key: round((base.get(key) or 0) + (current.get(key) or 0), 6)
                                            for key in METRICS}
            write_json_atomic(path, data)


# ---------------------------------------------------------------- budget guard
def check_budget(run_id: Optional[str] = None, *, token_limit: Optional[int] = None) -> None:
    """Raise :class:`BudgetExceeded` if the run is over its configured budget (I-5-3).

    Limits come from Config (0/unset = unlimited). A reserved API attempt passes
    its captured token limit after settlement so a mid-call config change cannot
    discard that attempt's policy. USD checks retain their configured behavior.
    """
    from ..config import Config
    max_tokens = token_limit if token_limit is not None else int(getattr(Config, "LLM_RUN_BUDGET_TOKENS", 0) or 0)
    max_cost = float(getattr(Config, "LLM_RUN_BUDGET_USD", 0) or 0)
    if max_tokens <= 0 and max_cost <= 0:
        return
    rid = run_id or _current_run.get()
    if not rid:
        # FOG-TEL-1: 与 record() 同一回退——无归属线程（如 graphiti 池线程）在唯一活跃
        # run 时按该 run 的预算执行，使失控的 graph 阶段能真正触发 LLM_RUN_BUDGET_TOKENS。
        # 0 个或 ≥2 个活跃 run 时保持旧语义（读 '_global' 桶；纯含糊花费不计入任何 run 预算）。
        rid = _sole_active_run() or _DEFAULT_BUCKET
    cumulative = LLMMeter.cumulative_budget_totals(rid, include_cost=max_cost > 0)
    snap = cumulative if cumulative is not None else LLMMeter.snapshot(rid)["total"]
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


# ---------------------------------------------------------------- ITEM-18: 阶段级遥测
# 把 LLMMeter 的 per-stage token/调用/成本，与编排器记录的每阶段墙钟时长（started_at→
# finished_at）合并成一份紧凑的「阶段 × 调用/tokens/成本/墙钟」视图，供落 telemetry.json、
# 折入 state.options（经 GET /status 暴露）、并确定性渲染进报告附录。纯函数、可测；计量缺失/
# 为空 → 静默产出（仅墙钟或全零骨架），绝不抛。
_PIPELINE_STAGE_ORDER = ("research", "ontology", "graph", "prepare", "run", "report")


def build_stage_telemetry(run_id: Optional[str],
                          stage_walls: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """ITEM-18：构建 {stage: {calls, input_tokens, output_tokens, est_cost_usd, wall_seconds}}。

    token/调用/成本取自 :meth:`LLMMeter.snapshot` 的 ``by_stage``（成本已按 estimate_cost 计入，
    含 LLM_COST_PER_MTOK 覆盖）；``wall_seconds`` 取自编排器传入的 ``stage_walls``（各阶段
    started_at→finished_at 墙钟差——反映整段阶段耗时，与纯 LLM 在飞延迟不同）。某阶段可能只在
    一侧出现（graph/prepare 常有墙钟但 0 LLM 调用；run 阶段 LLM 在子进程、计量归 0）——两侧取
    并集，缺失侧填 0。degrade-safe：snapshot 空 → 仅墙钟骨架。"""
    walls: Dict[str, float] = {}
    for k, v in (stage_walls or {}).items():
        if isinstance(v, (int, float)) and v >= 0:
            walls[str(k)] = float(v)
    snap = LLMMeter.snapshot(run_id)
    by_stage = snap.get("by_stage") or {}
    stages: Dict[str, Dict[str, Any]] = {}
    for name in set(by_stage.keys()) | set(walls.keys()):
        c = by_stage.get(name) or {}
        stages[name] = {
            "calls": int(c.get("calls", 0) or 0),
            "input_tokens": int(c.get("prompt_tokens", 0) or 0),
            "output_tokens": int(c.get("completion_tokens", 0) or 0),
            "est_cost_usd": round(float(c.get("cost_usd", 0.0) or 0.0), 6),
            "wall_seconds": round(walls.get(name, 0.0), 1),
        }
    total = {
        "calls": sum(s["calls"] for s in stages.values()),
        "input_tokens": sum(s["input_tokens"] for s in stages.values()),
        "output_tokens": sum(s["output_tokens"] for s in stages.values()),
        "est_cost_usd": round(sum(s["est_cost_usd"] for s in stages.values()), 6),
        "wall_seconds": round(sum(s["wall_seconds"] for s in stages.values()), 1),
    }
    out = {
        "run_id": snap.get("run_id") or run_id or _DEFAULT_BUCKET,
        "by_stage": stages,
        "total": total,
        "cost_estimated": bool(snap.get("cost_estimated")),
        "cost_basis": snap.get("cost_basis", "api"),
    }
    # FOG-TEL-1: 把进程级无归属桶透传进落盘的 telemetry.json（附加键，纯观测）。
    _up = snap.get("unattributed_process")
    if isinstance(_up, dict):
        out["unattributed_process"] = _up
    return out


def render_telemetry_appendix(stage_telemetry: Optional[Dict[str, Any]],
                              title: str = "Run Telemetry") -> str:
    """ITEM-18：把 stage_telemetry 渲染成确定性 Markdown「Run Telemetry」附录表（无 LLM）。

    列：Stage | Calls | Input tok | Output tok | Est. cost (USD) | Wall (s)。阶段按固定管线顺序
    （research→ontology→graph→prepare→run→report）排列，未知阶段按名称字典序附后；末行 TOTAL。
    成本为估算/非 API 计价基准时以脚注标注。空/无阶段 → 返回空串（调用方据此跳过追加）。"""
    if not isinstance(stage_telemetry, dict):
        return ""
    stages = stage_telemetry.get("by_stage") or {}
    if not stages:
        return ""
    known = [s for s in _PIPELINE_STAGE_ORDER if s in stages]
    extra = sorted(k for k in stages if k not in _PIPELINE_STAGE_ORDER)
    ordered = known + extra

    def _row(name: str, d: Dict[str, Any]) -> str:
        return (f"| {name} | {int(d.get('calls', 0) or 0)} | "
                f"{int(d.get('input_tokens', 0) or 0):,} | "
                f"{int(d.get('output_tokens', 0) or 0):,} | "
                f"{float(d.get('est_cost_usd', 0.0) or 0.0):.4f} | "
                f"{float(d.get('wall_seconds', 0.0) or 0.0):.1f} |")

    lines = [f"## {title}", "",
             "| Stage | Calls | Input tok | Output tok | Est. cost (USD) | Wall (s) |",
             "|---|---:|---:|---:|---:|---:|"]
    for name in ordered:
        lines.append(_row(name, stages.get(name) or {}))
    lines.append(_row("**TOTAL**", stage_telemetry.get("total") or {}))
    note = ("_Est. cost is a rough estimate from a per-provider $/Mtok table; "
            "treat as indicative, not billing-exact._"
            if stage_telemetry.get("cost_estimated") else
            "_Est. cost derived from a per-provider $/Mtok table._")
    basis = stage_telemetry.get("cost_basis")
    if basis and basis != "api":
        note = note.rstrip("_") + f" Cost basis: {basis}._"
    lines += ["", note, ""]
    return "\n".join(lines)


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
