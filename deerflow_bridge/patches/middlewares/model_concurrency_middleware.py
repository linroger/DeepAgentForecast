"""Cross-process admission at the exact provider model-call boundary.

The forecast orchestrator configures ``RESEARCH_MODEL_LEASE_DB`` and a global
capacity. Normal DeerFlow use without that environment remains a no-op. The
SQLite implementation lives in the stdlib-only bridge ``research_budget``
module so separate outer-track and pipeline processes share the same permits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager, nullcontext
import os
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse

try:
    import research_budget as _research_budget
except ImportError:  # Ordinary DeerFlow deployments do not need this control plane.
    _research_budget = None  # type: ignore[assignment]

try:
    import research_compaction as _research_compaction
except ImportError:  # Ordinary DeerFlow deployments have no research stop latch.
    _research_compaction = None  # type: ignore[assignment]


def _raise_if_compaction_stopped() -> None:
    if _research_compaction is not None:
        _research_compaction.raise_if_compaction_stopped()


def provider_model_admission(messages, tools=None):
    """Reserve one physical call's prompt budget without a legacy dependency.

    Keep this context inside the concurrency lease: queued callers must pass its
    stop check before reserving. Settle the response inside both contexts, before
    the lease's post-call sibling-stop check can interrupt the return path.
    """
    if os.environ.get("RESEARCH_ENGINE", "").strip().lower() != "agentic":
        return nullcontext(None)
    try:
        from research_admission import model_admission
    except ImportError:
        if _research_compaction is not None:
            error = _research_compaction.ResearchCompactionError("checkpoint_unavailable")
            _research_compaction.stop_after_compaction_failure(error)
            raise error from None
        raise RuntimeError("Agentic model admission is unavailable") from None
    return model_admission(messages, tools=tools)


@asynccontextmanager
async def async_provider_model_admission(messages, tools=None):
    """Keep durable admission and settlement off the native event loop."""
    if os.environ.get("RESEARCH_ENGINE", "").strip().lower() != "agentic":
        yield None
        return
    try:
        from research_admission import async_model_admission
    except ImportError:
        if _research_compaction is not None:
            error = _research_compaction.ResearchCompactionError("checkpoint_unavailable")
            _research_compaction.stop_after_compaction_failure(error)
            raise error from None
        raise RuntimeError("Agentic model admission is unavailable") from None
    async with async_model_admission(messages, tools=tools) as ticket:
        yield ticket


def _request_messages(request):
    """Include the system message carried separately by native ModelRequest."""
    messages = getattr(request, "messages", None)
    system = getattr(request, "system_message", None)
    return [system, *(messages or ())] if system is not None else messages


@contextmanager
def provider_model_lease():
    """Admit one provider call only while the research run remains healthy."""
    _raise_if_compaction_stopped()
    if _research_budget is None or not hasattr(_research_budget, "model_call_lease"):
        yield
        _raise_if_compaction_stopped()
        return
    with _research_budget.model_call_lease(1):
        # A sibling can stop during the capacity wait. Release the acquired
        # permit without entering the provider when that has happened.
        _raise_if_compaction_stopped()
        yield
        # Already-started calls may finish, but cannot conceal a sibling stop.
        _raise_if_compaction_stopped()


@asynccontextmanager
async def async_provider_model_lease():
    """Async exact-call permit without blocking the LangGraph event loop."""
    _raise_if_compaction_stopped()
    if _research_budget is None or not hasattr(
            _research_budget, "async_model_call_lease"):
        yield
        _raise_if_compaction_stopped()
        return
    async with _research_budget.async_model_call_lease(1):
        _raise_if_compaction_stopped()
        yield
        _raise_if_compaction_stopped()


@asynccontextmanager
async def async_subagent_lifecycle_lease():
    """Reserve one globally shared slot for a complete subagent execution."""
    if _research_budget is None or not hasattr(
            _research_budget, "async_subagent_call_lease"):
        yield
        return
    async with _research_budget.async_subagent_call_lease():
        yield


class ModelConcurrencyMiddleware(AgentMiddleware[AgentState]):
    """Apply one global lease only while the provider handler is in flight."""

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        with provider_model_lease():
            with provider_model_admission(_request_messages(request), getattr(request, "tools", None)) as ticket:
                response = handler(request)
                if ticket is not None:
                    ticket.settle(response)
                return response

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        async with async_provider_model_lease():
            async with async_provider_model_admission(_request_messages(request), getattr(request, "tools", None)) as ticket:
                response = await handler(request)
                if ticket is not None:
                    await ticket.settle(response)
                return response
