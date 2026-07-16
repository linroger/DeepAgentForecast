"""Regression tests for the tracked embedded-subagent safety overlay.

Run with DeerFlow's own environment after applying the overlay, for example::

    python3 deerflow_bridge/patches/apply_subagent_overlays.py deer-flow
    deer-flow/backend/.venv/bin/python -m pytest \
      deerflow_bridge/patches/tests/test_subagent_runtime_contract.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from enum import Enum
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.subagents.config import SubagentConfig
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


class FakeSubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@pytest.fixture()
def real_executor_module():
    """Load the real executor despite DeerFlow's suite-level circular-import shim."""
    mocked_names = [
        "deerflow.agents",
        "deerflow.agents.thread_state",
        "deerflow.agents.middlewares",
        "deerflow.agents.middlewares.thread_data_middleware",
        "deerflow.sandbox",
        "deerflow.sandbox.middleware",
        "deerflow.sandbox.security",
        "deerflow.models",
        "deerflow.skills.storage",
    ]
    original_modules = {name: sys.modules.get(name) for name in mocked_names}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    sys.modules.pop("deerflow.subagents.executor", None)
    for name in mocked_names:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **_kwargs: SimpleNamespace(
        load_skills=lambda *, enabled_only: []
    )
    sys.modules["deerflow.skills.storage"] = storage_module
    module = importlib.import_module("deerflow.subagents.executor")
    try:
        yield module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        if original_executor is None:
            sys.modules.pop("deerflow.subagents.executor", None)
        else:
            sys.modules["deerflow.subagents.executor"] = original_executor


async def _no_sleep(_seconds: float) -> None:
    return None


def test_task_inherits_embedded_client_model_from_configurable(monkeypatch) -> None:
    """Tracing-off embedded runs MUST NOT fall back to the first provider."""
    config = SubagentConfig(
        name="scoped-researcher",
        description="Research one bounded question",
        model="inherit",
        timeout_seconds=10,
    )
    runtime = SimpleNamespace(
        state={},
        context={
            "thread_id": "thread-1",
            "app_config": SimpleNamespace(
                token_usage=SimpleNamespace(enabled=False),
            ),
        },
        config={
            "configurable": {"model_name": "minimax", "thread_id": "thread-1"},
            "metadata": {},
        },
    )
    captured: dict = {}
    tool_loader = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute_async(self, _prompt, task_id=None):
            return task_id

    completed = SimpleNamespace(
        status=FakeSubagentStatus.COMPLETED,
        result="done",
        error=None,
        ai_messages=[],
        token_usage_records=[],
        usage_reported=True,
    )
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(
        task_tool_module,
        "get_subagent_config",
        lambda _name, **_kwargs: config,
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_available_subagent_names",
        lambda **_kwargs: ["scoped-researcher"],
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: completed,
    )
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _task_id: None)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", tool_loader)

    output = asyncio.run(
        task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description="bounded research",
            prompt="Research one source.",
            subagent_type="scoped-researcher",
            tool_call_id="call-1",
        )
    )

    assert output == "Task Succeeded. Result: done"
    assert captured["parent_model"] == "minimax"
    tool_loader.assert_called_once_with(
        model_name="minimax",
        groups=None,
        subagent_enabled=False,
        app_config=runtime.context["app_config"],
    )


def test_provider_fallback_message_marks_subagent_failed(
    monkeypatch,
    real_executor_module,
) -> None:
    """A middleware fallback is an error boundary, never successful evidence."""
    config = SubagentConfig(
        name="scoped-researcher",
        description="Research one bounded question",
        model="inherit",
        timeout_seconds=10,
    )
    executor = real_executor_module.SubagentExecutor(
        config=config,
        tools=[],
        parent_model="minimax",
        trace_id="trace-1",
    )

    async def fake_initial_state(_task):
        return {}, [], None

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield {
                "messages": [
                    AIMessage(
                        content="The configured LLM provider rejected the request.",
                        additional_kwargs={
                            "deerflow_error_fallback": True,
                            "error_reason": "auth",
                        },
                    )
                ]
            }

    monkeypatch.setattr(executor, "_build_initial_state", fake_initial_state)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: FakeAgent())

    result = asyncio.run(executor._aexecute_under_lease("Research one source."))

    assert result.status is real_executor_module.SubagentStatus.FAILED
    assert result.error == "LLM provider fallback inside subagent (reason=auth)"
    assert result.result is None


def test_all_evidence_tools_budget_denied_marks_subagent_failed(
    monkeypatch,
    real_executor_module,
) -> None:
    """A normal graph return is not success when every evidence tool was denied."""
    config = SubagentConfig(
        name="scoped-researcher",
        description="Research one bounded question",
        model="inherit",
        timeout_seconds=10,
    )
    executor = real_executor_module.SubagentExecutor(
        config=config,
        tools=[],
        parent_model="minimax",
        trace_id="trace-budget",
    )

    async def fake_initial_state(_task):
        return {}, [], None

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield {
                "messages": [
                    ToolMessage(
                        content=(
                            '{"error":"research_budget_exhausted",'
                            '"tool":"web_search","budget":"attempts_global",'
                            '"request":"official source","results":[]}'
                        ),
                        name="web_search",
                        tool_call_id="search-1",
                    ),
                    ToolMessage(
                        content=(
                            '{"error":"research_budget_exhausted",'
                            '"tool":"web_fetch","budget":"attempts_global",'
                            '"request":"https://example.gov/report","results":[]}'
                        ),
                        name="web_fetch",
                        tool_call_id="fetch-1",
                    ),
                    AIMessage(
                        content=(
                            "## Research status: BLOCKED\n\n"
                            "No admissible evidence could be gathered."
                        )
                    ),
                ]
            }

    monkeypatch.setattr(executor, "_build_initial_state", fake_initial_state)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: FakeAgent())

    result = asyncio.run(executor._aexecute_under_lease("Research one source."))

    assert result.status is real_executor_module.SubagentStatus.FAILED
    assert result.error == (
        "Subagent evidence tools exhausted before any usable result"
    )
    assert result.result is None


def test_successful_evidence_survives_later_budget_denial(
    monkeypatch,
    real_executor_module,
) -> None:
    """A late denial degrades but must not discard independently fetched evidence."""
    config = SubagentConfig(
        name="scoped-researcher",
        description="Research one bounded question",
        model="inherit",
        timeout_seconds=10,
    )
    executor = real_executor_module.SubagentExecutor(
        config=config,
        tools=[],
        parent_model="minimax",
        trace_id="trace-partial",
    )

    async def fake_initial_state(_task):
        return {}, [], None

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield {
                "messages": [
                    ToolMessage(
                        content=(
                            '{"results":[{"title":"Official report",'
                            '"url":"https://example.gov/report"}]}'
                        ),
                        name="web_search",
                        tool_call_id="search-ok",
                    ),
                    ToolMessage(
                        content=(
                            '{"error":"research_budget_exhausted",'
                            '"tool":"web_fetch","budget":"attempts_global",'
                            '"request":"https://example.gov/second","results":[]}'
                        ),
                        name="web_fetch",
                        tool_call_id="fetch-denied",
                    ),
                    AIMessage(
                        content=(
                            "Official evidence: https://example.gov/report reports "
                            "a measured deployment baseline."
                        )
                    ),
                ]
            }

    monkeypatch.setattr(executor, "_build_initial_state", fake_initial_state)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: FakeAgent())

    result = asyncio.run(executor._aexecute_under_lease("Research one source."))

    assert result.status is real_executor_module.SubagentStatus.COMPLETED
    assert "Official evidence" in (result.result or "")


def test_exact_blocked_outcome_contract_marks_subagent_failed(
    monkeypatch,
    real_executor_module,
) -> None:
    """Non-tool specialists can emit one exact typed blocked envelope."""
    config = SubagentConfig(
        name="scoped-researcher",
        description="Research one bounded question",
        model="inherit",
        timeout_seconds=10,
    )
    executor = real_executor_module.SubagentExecutor(
        config=config,
        tools=[],
        parent_model="minimax",
        trace_id="trace-blocked",
    )

    async def fake_initial_state(_task):
        return {}, [], None

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield {
                "messages": [
                    AIMessage(
                        content=(
                            "SUBAGENT_OUTCOME: BLOCKED code=no_usable_evidence\n"
                            "The required source artifact was unavailable."
                        )
                    )
                ]
            }

    monkeypatch.setattr(executor, "_build_initial_state", fake_initial_state)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: FakeAgent())

    result = asyncio.run(executor._aexecute_under_lease("Research one source."))

    assert result.status is real_executor_module.SubagentStatus.FAILED
    assert result.error == "Subagent reported blocked outcome (code=no_usable_evidence)"
