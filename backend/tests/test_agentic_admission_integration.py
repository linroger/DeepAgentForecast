"""Tracked native middleware + real admission ledger, with framework shells only.

The prescribed interpreter has no LangChain runtime. Tests import the complete
production overlay sources, replace only framework types and model handlers,
and exercise real ResearchWorkspace, admission, and compaction-stop helpers.
"""

from __future__ import annotations

import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys
import sqlite3
import threading
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "deerflow_bridge"
OVERLAYS = BRIDGE / "patches/middlewares"
SUMMARY_PROMPT = "Summarize the full evidence 中文\r\n" * 25
TOOLS = [{"type": "function", "function": {
    "name": "read_evidence", "description": "native schema details " * 80,
    "parameters": {"type": "object", "properties": {"artifact_id": {"type": "string"}}},
}}]


@dataclass
class Message:
    content: str
    type: str = "human"
    usage_metadata: dict | None = None

    @property
    def text(self):
        return self.content

    def model_dump(self, mode="json"):
        return {"type": self.type, "content": self.content}


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for path in (OVERLAYS / "model_concurrency_middleware.py", OVERLAYS / "summarization_middleware.py",
                 BRIDGE / "research_admission.py", Path(__file__)):
        record_testsuite_property("native_admission_sha256:" + path.name,
                                 hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def active(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(BRIDGE))
    admission = importlib.import_module("research_admission")
    archive = importlib.import_module("research_archive")
    compaction = importlib.import_module("research_compaction")
    workspace_type = importlib.import_module("research_workspace").ResearchWorkspace
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.delenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", raising=False)
    monkeypatch.setenv("RESEARCH_COMPACTION_RUN_SCOPED", "true")
    workspace = workspace_type(tmp_path / "workspace", {"run": "native-admission", "model": "offline"})
    archive.activate_workspace(workspace)
    compaction.reset_compaction_stop()
    yield SimpleNamespace(admission=admission, archive=archive, compaction=compaction, workspace=workspace)
    archive.activate_workspace(None)
    compaction.reset_compaction_stop()


@pytest.fixture
def native(active, monkeypatch):
    class Middleware:
        def __class_getitem__(cls, item):
            return cls

    shells = {
        "langchain.agents": {"AgentState": dict},
        "langchain.agents.middleware": {"AgentMiddleware": Middleware, "SummarizationMiddleware": Middleware},
        "langchain.agents.middleware.types": {"ModelCallResult": object, "ModelRequest": object, "ModelResponse": object},
        "langchain_core.messages": {
            "AIMessage": Message, "AnyMessage": Message, "HumanMessage": Message,
            "RemoveMessage": Message, "ToolMessage": Message,
            "get_buffer_string": lambda messages: "\n".join(m.content for m in messages),
            "messages_to_dict": lambda messages: [m.model_dump() for m in messages],
        },
        "langgraph.config": {"get_config": lambda: {}},
        "langgraph.constants": {"TAG_NOSTREAM": "nostream"},
        "langgraph.graph.message": {"REMOVE_ALL_MESSAGES": "remove_all"},
        "langgraph.runtime": {"Runtime": object},
        "deerflow.agents.middlewares.dynamic_context_middleware": {"is_dynamic_context_reminder": lambda message: False},
        "deerflow.agents.middlewares.tool_call_metadata": {"clone_ai_message_with_tool_calls": lambda m, *a, **k: m},
    }
    for name, attrs in shells.items():
        shell = ModuleType(name)
        shell.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, shell)

    def load(filename, name):
        spec = importlib.util.spec_from_file_location(name, OVERLAYS / filename)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    concurrency = load("model_concurrency_middleware.py", "deerflow.agents.middlewares.model_concurrency_middleware")
    summary = load("summarization_middleware.py", "_admission_summary_overlay")
    events = []
    hooks = SimpleNamespace(acquire=lambda: None)

    @contextmanager
    def lease(weight):
        assert weight == 1
        events.append("acquire")
        try:
            hooks.acquire()
            yield
        finally:
            events.append("release")

    @asynccontextmanager
    async def async_lease(weight):
        assert weight == 1
        events.append("acquire")
        try:
            hooks.acquire()
            await asyncio.sleep(0)
            yield
        finally:
            events.append("release")

    monkeypatch.setattr(concurrency, "_research_budget", SimpleNamespace(
        model_call_lease=lease, async_model_call_lease=async_lease,
    ))
    return SimpleNamespace(concurrency=concurrency, summary=summary, events=events, hooks=hooks)


def request():
    return SimpleNamespace(messages=[Message("user question")], tools=TOOLS,
                           system_message=Message("system instructions " * 100, type="system"))


def model_response(inputs=41, outputs=7):
    return Message("summary result", type="ai", usage_metadata={"input_tokens": inputs, "output_tokens": outputs})


def invoke(native, boundary, mode, provider, *, req=None, summary_tools=None):
    if boundary == "model":
        req = req or request()
        middleware = native.concurrency.ModelConcurrencyMiddleware()
        if mode == "sync":
            return middleware.wrap_model_call(req, lambda actual: provider(actual))
        async def handler(actual):
            await asyncio.sleep(0)
            return provider(actual)
        return asyncio.run(middleware.awrap_model_call(req, handler))

    middleware = native.summary.DeerFlowSummarizationMiddleware.__new__(native.summary.DeerFlowSummarizationMiddleware)
    middleware._build_summary_prompt = lambda messages: SUMMARY_PROMPT
    def sync(prompt, *, config):
        assert prompt == SUMMARY_PROMPT
        assert config == {"metadata": {"lc_source": "summarization"}}
        return provider(prompt)
    async def async_call(prompt, *, config):
        await asyncio.sleep(0)
        return sync(prompt, config=config)
    middleware._summary_model = SimpleNamespace(invoke=sync, ainvoke=async_call, kwargs={"tools": summary_tools})
    messages = [Message("full source evidence")]
    if mode == "sync":
        return middleware._create_summary(messages)
    return asyncio.run(middleware._acreate_summary(messages))


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_each_native_call_reserves_before_send_and_settles_result(active, native, boundary, mode):
    response = model_response()
    result = SimpleNamespace(result=[response]) if boundary == "model" else response
    def provider(actual):
        native.events.append("provider")
        state = active.admission.snapshot(active.workspace)
        assert state["reserved_calls"] == 1
        assert state["held_input_tokens"] > 0
        return result
    received = invoke(native, boundary, mode, provider)
    assert received is result if boundary == "model" else received == "summary result"
    state = active.admission.snapshot(active.workspace)
    assert state["spent_input_tokens"] == 41
    assert state["observed_output_tokens"] == 7
    assert state["held_input_tokens"] == 0
    assert state["settled_calls"] == 1
    assert native.events == ["acquire", "provider", "release"]


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_budget_denial_preserves_typed_halt_and_never_sends(active, native, monkeypatch, boundary, mode):
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "1")
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, boundary, mode, lambda _: pytest.fail("budget denied before native send"))
    assert caught.value.reason == "checkpoint_unavailable"
    assert active.compaction.get_compaction_stop() is not None
    assert active.admission.snapshot(active.workspace)["budget_denied_reason"] == "budget_denied"
    assert native.events == ["acquire", "release"]


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_completed_usage_is_settled_before_sibling_stop_check(active, native, boundary, mode):
    stop = active.compaction.ResearchCompactionError("archive_write_failed", "sibling")
    response = model_response(53, 11)
    def provider(_):
        active.compaction.stop_after_compaction_failure(stop)
        return SimpleNamespace(result=[response]) if boundary == "model" else response
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, boundary, mode, provider)
    assert caught.value.reason == "archive_write_failed"
    assert caught.value.thread_id == "sibling"
    state = active.admission.snapshot(active.workspace)
    assert state["spent_input_tokens"] == 53
    assert state["known_usage_calls"] == 1
    assert state["held_input_tokens"] == 0


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_native_failure_charges_estimate_and_releases_capacity(active, native, boundary, mode):
    held = []
    def provider(_):
        held.append(active.admission.snapshot(active.workspace)["held_input_tokens"])
        raise TimeoutError("private request detail")
    error_type = TimeoutError if boundary == "model" else active.compaction.ResearchCompactionError
    with pytest.raises(error_type) as caught:
        invoke(native, boundary, mode, provider)
    if boundary == "summary":
        assert caught.value.reason == "summary_model_failed"
        assert "private request detail" not in str(caught.value)
    state = active.admission.snapshot(active.workspace)
    assert state["spent_input_tokens"] == held[0] > 0
    assert state["estimated_calls"] == 1
    assert native.events == ["acquire", "release"]


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_native_missing_usage_remains_estimated(active, native, boundary, mode):
    value = Message("summary result", type="ai")
    invoke(native, boundary, mode, lambda _: value)
    state = active.admission.snapshot(active.workspace)
    assert state["spent_input_tokens"] > 0
    assert state["unknown_usage_calls"] == state["estimated_calls"] == 1
    assert state["known_usage_calls"] == 0


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_native_typed_stop_keeps_original_reason_and_origin(active, native, boundary, mode):
    stop = active.compaction.ResearchCompactionError("archive_write_failed", "producer-thread")
    def provider(_):
        raise stop
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, boundary, mode, provider)
    assert caught.value is stop
    state = active.admission.snapshot(active.workspace)
    assert state["spent_input_tokens"] > 0
    assert state["estimated_calls"] == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_model_admission_includes_system_message_and_tool_schemas(active, native, mode):
    req = request()
    expected = active.admission._estimate([req.system_message, *req.messages], req.tools)
    def provider(actual):
        assert actual is req
        row = active.admission.snapshot(active.workspace)["calls"][0]
        assert (row["input_sha256"], row["estimate_input_tokens"]) == expected
        return SimpleNamespace(result=[model_response()])
    invoke(native, "model", mode, provider, req=req)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_summarizer_bound_tools_are_reserved_before_send(active, native, monkeypatch, mode):
    without_tools = active.admission._estimate(SUMMARY_PROMPT, None)[1]
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(without_tools))
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, "summary", mode, lambda _: pytest.fail("bound schemas require allowance"), summary_tools=TOOLS)
    assert caught.value.reason == "checkpoint_unavailable"


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_parent_admission_wrapper_does_not_double_count(active, native, boundary, mode):
    with active.admission.model_admission([Message("outer estimate")]) as ticket:
        result = invoke(native, boundary, mode, lambda _: model_response())
        ticket.settle(model_response())
    state = active.admission.snapshot(active.workspace)
    assert state["settled_calls"] == 1
    assert state["spent_input_tokens"] == 41
    assert result is not None


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_stop_during_capacity_wait_neither_reserves_nor_sends(active, native, boundary, mode):
    def stop():
        active.compaction.stop_after_compaction_failure(active.compaction.ResearchCompactionError("summary_failed", "sibling"))
    native.hooks.acquire = stop
    with pytest.raises(active.compaction.ResearchCompactionError):
        invoke(native, boundary, mode, lambda _: pytest.fail("sibling stop prohibits send"))
    assert not (active.workspace.root / "model_usage.sqlite3").exists()
    assert native.events == ["acquire", "release"]


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_stop_during_durable_admission_prevents_send_and_cleans_ticket(active, native, monkeypatch, boundary, mode):
    reserve = active.admission._Ledger.reserve
    error = active.compaction.ResearchCompactionError("archive_write_failed", "sibling")
    def stop_after_reservation(ledger, *args):
        ticket = reserve(ledger, *args)
        active.compaction.stop_after_compaction_failure(error)
        return ticket
    monkeypatch.setattr(active.admission._Ledger, "reserve", stop_after_reservation)
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, boundary, mode, lambda _: pytest.fail("stop after admission must prohibit send"))
    assert caught.value.reason == error.reason
    assert caught.value.thread_id == "sibling"
    state = active.admission.snapshot(active.workspace)
    assert state["held_input_tokens"] == 0
    assert state["estimated_calls"] == 1
    assert native.events == ["acquire", "release"]


async def async_invoke(native, boundary, provider):
    if boundary == "model":
        async def handler(req):
            return provider(req)
        return await native.concurrency.ModelConcurrencyMiddleware().awrap_model_call(request(), handler)
    middleware = native.summary.DeerFlowSummarizationMiddleware.__new__(native.summary.DeerFlowSummarizationMiddleware)
    middleware._build_summary_prompt = lambda messages: SUMMARY_PROMPT
    async def handler(prompt, *, config):
        return provider(prompt)
    middleware._summary_model = SimpleNamespace(ainvoke=handler, kwargs={})
    return await middleware._acreate_summary([Message("evidence")])


@pytest.mark.parametrize("boundary", ["model", "summary"])
def test_async_ledger_contention_keeps_heartbeat_responsive(active, native, boundary):
    with active.admission.model_admission(["prime ledger"]) as ticket:
        ticket.settle(model_response())
    holding, release, released = threading.Event(), threading.Event(), threading.Event()
    def hold_writer():
        conn = sqlite3.connect(active.workspace.root / "model_usage.sqlite3")
        try:
            conn.execute("BEGIN IMMEDIATE")
            holding.set()
            release.wait(timeout=2)  # Bounds the failing implementation's deadlock.
            conn.rollback()
        finally:
            conn.close()
            released.set()
    ticks = []
    async def heartbeat():
        await asyncio.sleep(0)
        while not released.is_set():
            ticks.append(1)
            if len(ticks) >= 5:
                release.set()
            await asyncio.sleep(0)
    async def run():
        await asyncio.gather(async_invoke(native, boundary, lambda _: model_response()), heartbeat())
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_writer)
        assert holding.wait(timeout=2)
        try:
            asyncio.run(run())
        finally:
            release.set()
            holder.result(timeout=3)
    assert len(ticks) >= 5, "SQLite admission blocked the native event loop"


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("stage", ["admission", "settlement"])
def test_async_cancellation_drains_accounting_without_moving_context_tokens(active, native, monkeypatch, boundary, stage):
    entered, release = threading.Event(), threading.Event()
    sends, restored = [], []
    target = active.admission._Ledger if stage == "admission" else active.admission._Ticket
    name = "reserve" if stage == "admission" else "settle"
    original = getattr(target, name)
    def delayed(self, *args):
        entered.set()
        assert release.wait(timeout=3), "cancellation test must release accounting worker"
        return original(self, *args)
    monkeypatch.setattr(target, name, delayed)
    def provider(_):
        sends.append(1)
        return model_response()
    async def call():
        try:
            await async_invoke(native, boundary, provider)
        finally:
            restored.append(active.admission._CURRENT.get())
    async def run():
        task = asyncio.create_task(call())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "cancellation must drain the accounting worker"
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "repeated cancellation must not abandon accounting"
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    state = active.admission.snapshot(active.workspace)
    assert state["held_input_tokens"] == 0
    assert state["settled_calls"] == 1
    assert restored == [None]
    assert native.events == ["acquire", "release"]
    if stage == "admission":
        assert sends == []
        assert state["estimated_calls"] == 1
    else:
        assert sends == [1]
        assert state["spent_input_tokens"] == 41
        assert state["known_usage_calls"] == 1


def test_nested_async_admission_shares_raw_ticket_and_restores_loop_context(active):
    async def run():
        assert active.admission._CURRENT.get() is None
        async with active.admission.async_model_admission([Message("outer")]) as outer:
            raw = active.admission._CURRENT.get()
            async with active.admission.async_model_admission([Message("inner")], TOOLS) as inner:
                assert active.admission._CURRENT.get() is raw
                assert inner.op_id == outer.op_id
                await inner.settle(model_response())
            assert active.admission._CURRENT.get() is raw
            await outer.settle(model_response())
        assert active.admission._CURRENT.get() is None
    asyncio.run(run())
    state = active.admission.snapshot(active.workspace)
    assert state["settled_calls"] == 1
    assert state["spent_input_tokens"] == 41


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("outcome", ["provider_error", "normal_exit", "typed_stop"])
def test_cancellation_during_context_exit_drains_and_obeys_error_precedence(active, native, monkeypatch, boundary, outcome):
    exiting, release = threading.Event(), threading.Event()
    restored = []
    close = active.admission._Ticket.close
    stop = active.compaction.ResearchCompactionError("archive_write_failed", "producer")
    def delayed_close(ticket, failed):
        exiting.set()
        assert release.wait(timeout=3)
        return close(ticket, failed)
    monkeypatch.setattr(active.admission._Ticket, "close", delayed_close)
    def provider(_):
        if outcome == "provider_error":
            raise TimeoutError("provider error before cancellation")
        if outcome == "typed_stop":
            raise stop
        return model_response()
    async def call():
        try:
            await async_invoke(native, boundary, provider)
        finally:
            restored.append((active.admission._CURRENT.get(), active.admission._DEPTH.get()))
    async def run():
        task = asyncio.create_task(call())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(exiting.wait), timeout=2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert native.events == ["acquire"]
        finally:
            release.set()
        expected = active.compaction.ResearchCompactionError if outcome == "typed_stop" else asyncio.CancelledError
        with pytest.raises(expected) as caught:
            await task
        if outcome == "typed_stop":
            assert caught.value is stop
        else:
            assert task.cancelled()
    asyncio.run(run())
    state = active.admission.snapshot(active.workspace)
    assert state["held_input_tokens"] == 0
    assert state["settled_calls"] == 1
    assert state["estimated_calls"] == (outcome != "normal_exit")
    assert restored == [(None, 0)]
    assert native.events == ["acquire", "release"]


@pytest.mark.parametrize("engine", ["legacy", "linear-v2", ""])
@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_legacy_calls_need_no_admission_module_or_ledger(active, native, monkeypatch, engine, boundary, mode):
    monkeypatch.setenv("RESEARCH_ENGINE", engine)
    original_import = builtins.__import__
    def no_admission(name, *args, **kwargs):
        if name == "research_admission":
            raise AssertionError("legacy native deployment must not import agentic helper")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_admission)
    assert invoke(native, boundary, mode, lambda _: model_response()) is not None
    assert not (active.workspace.root / "model_usage.sqlite3").exists()


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_agentic_flag_without_active_workspace_is_noop(active, native, boundary, mode):
    active.archive.activate_workspace(None)
    assert invoke(native, boundary, mode, lambda _: model_response()) is not None
    assert not (active.workspace.root / "model_usage.sqlite3").exists()


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_legacy_summary_accepts_existing_concurrency_overlay_without_new_helper(active, native, monkeypatch, mode):
    monkeypatch.setenv("RESEARCH_ENGINE", "legacy")
    # Existing standalone summarizer deployment fixtures copy this overlay alone
    # and retain the previously installed concurrency helper.
    monkeypatch.delattr(native.concurrency, "provider_model_admission", raising=False)
    assert invoke(native, "summary", mode, lambda _: model_response()) == "summary result"
    assert not (active.workspace.root / "model_usage.sqlite3").exists()


@pytest.mark.parametrize("boundary", ["model", "summary"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_agentic_missing_helper_is_latched_stop_not_legacy_fallback(active, native, monkeypatch, boundary, mode):
    original_import = builtins.__import__
    def missing_admission(name, *args, **kwargs):
        if name == "research_admission":
            raise ImportError("private installation path")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing_admission)
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        invoke(native, boundary, mode, lambda _: pytest.fail("missing admission cannot send"))
    assert caught.value.reason == "checkpoint_unavailable"
    assert "private installation path" not in str(caught.value)
    assert active.compaction.get_compaction_stop() is not None


def test_summary_prompt_preserves_existing_typed_stop(native, active):
    middleware = native.summary.DeerFlowSummarizationMiddleware.__new__(native.summary.DeerFlowSummarizationMiddleware)
    error = active.compaction.ResearchCompactionError("checkpoint_unavailable", "original")
    def stop(_):
        raise error
    middleware._build_summary_prompt = stop
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        middleware._required_summary_prompt([Message("evidence")])
    assert caught.value is error


def test_summary_response_validation_preserves_existing_typed_stop(native, active):
    error = active.compaction.ResearchCompactionError("archive_conflict", "original")
    class StoppedResponse:
        @property
        def text(self):
            raise error
    with pytest.raises(active.compaction.ResearchCompactionError) as caught:
        native.summary.DeerFlowSummarizationMiddleware._validated_summary_response(StoppedResponse())
    assert caught.value is error
