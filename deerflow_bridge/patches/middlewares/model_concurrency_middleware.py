"""Cross-process admission at the exact provider model-call boundary.

The forecast orchestrator configures ``RESEARCH_MODEL_LEASE_DB`` and a global
capacity. Normal DeerFlow use without that environment remains a no-op. The
SQLite implementation lives in the stdlib-only bridge ``research_budget``
module so separate outer-track and pipeline processes share the same permits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse

try:
    import research_budget as _research_budget
except ImportError:  # Ordinary DeerFlow deployments do not need this control plane.
    _research_budget = None  # type: ignore[assignment]


@contextmanager
def provider_model_lease():
    """Reserve exactly one provider call, or no-op outside forecast runs."""
    if _research_budget is None or not hasattr(_research_budget, "model_call_lease"):
        yield
        return
    with _research_budget.model_call_lease(1):
        yield


@asynccontextmanager
async def async_provider_model_lease():
    """Async exact-call permit without blocking the LangGraph event loop."""
    if _research_budget is None or not hasattr(
            _research_budget, "async_model_call_lease"):
        yield
        return
    async with _research_budget.async_model_call_lease(1):
        yield


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
            return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        async with async_provider_model_lease():
            return await handler(request)
