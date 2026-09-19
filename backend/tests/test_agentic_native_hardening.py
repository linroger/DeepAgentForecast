"""Offline large-context and cancellation regressions for native overlays.

Framework shells come from the existing native fixtures; archive/admission
storage, middleware methods and stop propagation use production code.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import test_agentic_admission_integration as admission_fixtures
import test_agentic_evidence_archive as archive_fixtures

Message = admission_fixtures.Message
active = admission_fixtures.active
native = admission_fixtures.native
model_response = admission_fixtures.model_response
ToolMessage = archive_fixtures.Message
ToolConfig = archive_fixtures.ToolConfig
tool_native = archive_fixtures.native


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "deerflow_bridge/research_admission.py",
    "deerflow_bridge/patches/middlewares/model_concurrency_middleware.py",
    "deerflow_bridge/patches/middlewares/summarization_middleware.py",
    "deerflow_bridge/patches/middlewares/tool_output_budget_middleware.py",
    "backend/tests/test_agentic_native_hardening.py",
)


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for name in SOURCES:
        record_testsuite_property("native_hardening_sha256:" + name,
                                 hashlib.sha256((ROOT / name).read_bytes()).hexdigest())


def policy_workspace(active, value):
    workspace = type(active.workspace)(active.workspace.root.parent / "policy-workspace", {
        "run": "policy-fixture", "execution_policy": {"prompt_budget_tokens": value},
    })
    active.archive.activate_workspace(workspace)
    return workspace


@pytest.mark.parametrize("limit", [0, 12_000_000, 2**63 - 1])
def test_identity_limit_drives_uninitialized_snapshot_and_persisted_ledger(active, limit):
    workspace = policy_workspace(active, limit)
    # Read-only inspection must use its explicit workspace even without activation.
    active.archive.activate_workspace(None)
    before = active.admission.snapshot(workspace)
    assert before["limit_input_tokens"] == limit
    assert not (workspace.root / "model_usage.sqlite3").exists()
    active.archive.activate_workspace(workspace)
    messages = [Message("中文" * 700_000)]  # UTF-8 reserve exceeds the legacy 4M limit.
    with active.admission.model_admission(messages) as ticket:
        assert active.admission.snapshot(workspace)["held_input_tokens"] > 4_000_000
        ticket.settle(model_response(900_000, 13))
    after = active.admission.snapshot(workspace)
    assert after["limit_input_tokens"] == limit
    assert after["spent_input_tokens"] == 900_000
    assert after["reserved_calls"] == 0


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "12000000", None, 2**63])
def test_invalid_identity_budget_stops_before_provider_and_ledger_creation(active, invalid):
    workspace = policy_workspace(active, invalid)
    with pytest.raises(active.compaction.ResearchCompactionError):
        with active.admission.model_admission([Message("question")]):
            pytest.fail("invalid identity budget must prevent provider admission")
    assert not (workspace.root / "model_usage.sqlite3").exists()


def test_explicit_override_wins_and_existing_ledger_remains_pinned(active, monkeypatch):
    workspace = policy_workspace(active, "invalid-but-overridden")
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "12345")
    with active.admission.model_admission([Message("question")]) as ticket:
        ticket.settle(model_response(10, 2))
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "12346")
    with pytest.raises(active.compaction.ResearchCompactionError):
        with active.admission.model_admission([Message("question")]):
            pytest.fail("a changed environment must not reset the pinned ledger")
    assert active.admission.snapshot(workspace)["limit_input_tokens"] == 12345


@pytest.mark.parametrize("mode", ["fallback", "preview"])
@pytest.mark.parametrize("tail_chars", [0, 40, 200])
def test_sparse_newlines_cannot_expand_large_source_tail_budget(tool_native, mode, tail_chars):
    content = "head\n" + "x" * 600_000 + "\n" + "中" * 399_994 + "TAIL"
    if mode == "fallback":
        result = tool_native._build_fallback(content, tool_name="fetch", max_chars=1000,
                                            head_chars=100, tail_chars=tail_chars)
        assert len(result) <= 1000
    else:
        result = tool_native._build_preview(content, tool_name="fetch", virtual_path="/source.txt",
                                           head_chars=100, tail_chars=tail_chars)
        assert len(result) <= 100 + tail_chars + 300
    assert result.endswith("TAIL") == bool(tail_chars)


async def cancel_blocked(task, entered, release, effects):
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "cancellation returned while durable work was still writing"
        assert effects == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert effects == ["durable"]


def test_tool_cancellation_drains_durable_archive_before_return(active, tool_native, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    effects = []
    original = active.archive.archive_tool_result

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        result = original(*args, **kwargs)
        effects.append("durable")
        return result

    monkeypatch.setattr(active.archive, "archive_tool_result", blocked)

    async def run():
        async def handler(_):
            return ToolMessage("evidence" * 2000)
        request = SimpleNamespace(runtime=SimpleNamespace(state={}))
        task = asyncio.create_task(tool_native.ToolOutputBudgetMiddleware(ToolConfig()).awrap_tool_call(request, handler))
        await cancel_blocked(task, entered, release, effects)

    asyncio.run(run())


def test_summary_cancellation_drains_receipt_before_return(active, native, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    effects = []
    hooks = []
    middleware = native.summary.DeerFlowSummarizationMiddleware.__new__(native.summary.DeerFlowSummarizationMiddleware)
    middleware._ensure_message_ids = lambda messages: None
    middleware.token_counter = lambda messages: 900_000
    middleware._should_summarize = lambda messages, total: True
    middleware._determine_cutoff_index = lambda messages: 1
    middleware._partition_with_skill_rescue = lambda messages, cutoff: (messages[:cutoff], messages[cutoff:])
    middleware._fire_hooks = lambda *args: hooks.append("hook")

    async def summary(messages):
        return "valid summary"

    def record(*args):
        entered.set()
        assert release.wait(3)
        effects.append("durable")
        return [Message("recorded summary")]

    middleware._acreate_summary = summary
    middleware._record_summary = record

    async def run():
        task = asyncio.create_task(middleware.abefore_model(
            {"messages": [Message("original evidence"), Message("keep")]},
            SimpleNamespace(context={"thread_id": "summary-thread"}),
        ))
        await cancel_blocked(task, entered, release, effects)

    asyncio.run(run())
    assert hooks == [], "cancelled compaction must not publish memory hooks"


def test_async_historical_budgeting_runs_archive_io_off_event_loop(active, tool_native, monkeypatch):
    main_thread = threading.get_ident()
    original = active.archive.archive_tool_result
    calls = []

    def checked(*args, **kwargs):
        assert threading.get_ident() != main_thread, "historical archive blocked native event loop"
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(active.archive, "archive_tool_result", checked)

    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kwargs):
            return Request(kwargs["messages"])

    async def run():
        async def handler(request):
            assert "read_evidence" in request.messages[0].content
            assert len(request.messages[0].content) <= 4000
            return "accepted"
        return await tool_native.ToolOutputBudgetMiddleware(ToolConfig()).awrap_model_call(
            Request([ToolMessage("evidence" * 2000)]), handler,
        )

    assert asyncio.run(run()) == "accepted"
    assert calls == [1]


@pytest.mark.parametrize("boundary", ["summary", "tool"])
def test_event_loop_shutdown_drains_worker_before_owner_exits(native, tool_native, boundary):
    entered, release, owner_exited = threading.Event(), threading.Event(), threading.Event()
    exited_early, effects = [], []
    context = ContextVar("native_shutdown_context", default=None)

    def record(*args):
        assert context.get() == "retained"
        entered.set()
        assert release.wait(3)
        effects.append("durable")
        return [Message("receipt")]

    def observe():
        assert entered.wait(3)
        exited_early.append(owner_exited.wait(0.25))
        release.set()

    async def owner():
        context.set("retained")
        try:
            if boundary == "tool":
                await tool_native._run_budget_io(record)
            else:
                middleware = native.summary.DeerFlowSummarizationMiddleware.__new__(native.summary.DeerFlowSummarizationMiddleware)
                middleware._record_summary = record
                await middleware._arecord_summary("summary", [Message("original")], "thread")
        finally:
            owner_exited.set()

    async def main():
        task = asyncio.create_task(owner())
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        assert not task.done()
        # Returning makes asyncio.run cancel every Task, including any separately
        # created to_thread Task. The underlying receipt write must stay owned.

    observer = threading.Thread(target=observe)
    observer.start()
    try:
        asyncio.run(main())
    finally:
        release.set()
        observer.join(3)
    assert exited_early == [False]
    assert owner_exited.is_set()
    assert effects == ["durable"]
