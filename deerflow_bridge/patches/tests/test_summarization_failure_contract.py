"""Offline acceptance checks that execute the tracked summarization overlay.

Run with DeerFlow's Python environment. The test fixture copies and imports the
overlay under test explicitly, so an older assembled runtime cannot make an
unshipped change appear to pass. Dependency packages come from that environment;
the application checkout and its active checkpoint databases are never changed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage, messages_to_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.constants import TAG_NOSTREAM


@pytest.fixture
def sm(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    source = root / "deerflow_bridge/patches/middlewares/summarization_middleware.py"
    assembled = tmp_path / "harness/deerflow/agents/middlewares/summarization_middleware.py"
    assembled.parent.mkdir(parents=True)
    shutil.copyfile(source, assembled)
    monkeypatch.syspath_prepend(str(root / "deerflow_bridge"))
    name = "_astra_tracked_summarization"
    spec = importlib.util.spec_from_file_location(name, assembled)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert Path(module.__file__).read_bytes() == source.read_bytes()
    monkeypatch.setenv("RESEARCH_COMPACTION_DB", str(tmp_path / "compaction.sqlite3"))
    monkeypatch.setenv("RESEARCH_COMPACTION_RUN_SCOPED", "true")
    from research_compaction import reset_compaction_stop
    reset_compaction_stop()
    try:
        yield module
    finally:
        reset_compaction_stop()


def _messages():
    return [
        HumanMessage(content="Find the published quantity", id="request"),
        AIMessage(content="", id="fetch-call", tool_calls=[{
            "name": "web_fetch", "args": {"url": "https://example.test/source"}, "id": "fetch-1",
        }]),
        ToolMessage(content="Published quantity: 23 units; receipt R1", tool_call_id="fetch-1", id="receipt"),
        HumanMessage(content="Keep the source receipt", id="followup"),
        AIMessage(content="The source is ready", id="last-answer"),
    ]


def _middleware(sm, *, hook=None, model=None, preserve_recent_skill_count=0, **kwargs):
    if model is None:
        model = MagicMock()
        model.config = {"tags": ["middleware:summarize"]}
        model.with_config.return_value = model
        model.invoke.return_value = SimpleNamespace(text="23 units; receipt R1")
        model.ainvoke = AsyncMock(return_value=SimpleNamespace(text="23 units; receipt R1"))
    return sm.DeerFlowSummarizationMiddleware(
        model=model, trigger=("messages", 5), keep=("messages", 2), token_counter=len,
        before_summarization=[hook] if hook is not None else [],
        preserve_recent_skill_count=preserve_recent_skill_count, trim_tokens_to_summarize=None, **kwargs,
    )


def _run(middleware, messages, mode, runtime=None):
    runtime = runtime or SimpleNamespace(context={"thread_id": "thread-1"})
    if mode == "async":
        return asyncio.run(middleware.abefore_model({"messages": messages}, runtime))
    return middleware.before_model({"messages": messages}, runtime)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "empty", "malformed", "missing_text", "no_prompt"])
def test_failed_compaction_preserves_evidence_and_has_no_side_effects(sm, monkeypatch, mode, failure):
    events = []
    middleware = _middleware(sm, hook=events.append)
    archive = MagicMock()
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    model_call = middleware._summary_model.ainvoke if mode == "async" else middleware._summary_model.invoke
    if failure == "timeout":
        model_call.side_effect = TimeoutError("private provider detail must not become evidence")
    elif failure == "rate_limit":
        model_call.side_effect = RuntimeError("429 private provider detail")
    elif failure == "empty":
        model_call.return_value = SimpleNamespace(text=" \n ")
    elif failure == "malformed":
        model_call.return_value = SimpleNamespace(text={"unexpected": "object"})
    elif failure == "missing_text":
        model_call.return_value = object()
    else:
        monkeypatch.setattr(middleware, "_trim_messages_for_summary", lambda messages: [])
    messages = _messages()
    before = messages_to_dict(messages)

    with pytest.raises(RuntimeError) as caught:
        _run(middleware, messages, mode)

    assert "private provider detail" not in str(caught.value)
    assert caught.value.code == "research_compaction_failed"
    assert caught.value.thread_id == "thread-1"
    from research_compaction import get_compaction_stop
    stop = get_compaction_stop()
    assert stop.reason == caught.value.reason
    assert stop.thread_id == caught.value.thread_id
    assert messages_to_dict(messages) == before
    assert events == []
    archive.assert_not_called()
    assert model_call.call_count == (0 if failure == "no_prompt" else 1)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_receipt_failure_preserves_messages_and_does_not_fire_hooks(sm, monkeypatch, mode):
    events = []
    middleware = _middleware(sm, hook=events.append)
    archive = MagicMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    messages = _messages()
    before = messages_to_dict(messages)
    with pytest.raises(RuntimeError):
        _run(middleware, messages, mode)
    assert messages_to_dict(messages) == before
    assert events == []
    assert archive.call_count == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_sibling_stop_keeps_its_origin_and_reason(sm, monkeypatch, mode):
    from research_compaction import ResearchCompactionError, get_compaction_stop
    archive = MagicMock()
    monkeypatch.setattr(sm, "record_compaction", archive)
    middleware = _middleware(sm)
    model_call = middleware._summary_model.ainvoke if mode == "async" else middleware._summary_model.invoke
    model_call.side_effect = ResearchCompactionError("archive_write_failed", "other-thread")
    with pytest.raises(ResearchCompactionError) as caught:
        _run(middleware, _messages(), mode)
    assert caught.value.reason == "archive_write_failed"
    assert caught.value.thread_id == "other-thread"
    assert get_compaction_stop().thread_id == "other-thread"
    archive.assert_not_called()


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("ownership", ["native", "bridge_flag", "bridge_attempt"])
def test_native_failure_is_thread_local_and_bridge_failure_stops_its_run(sm, monkeypatch, mode, ownership):
    from research_compaction import ResearchCompactionError, get_compaction_stop
    monkeypatch.delenv("RESEARCH_COMPACTION_RUN_SCOPED", raising=False)
    monkeypatch.delenv("RESEARCH_PROCESS_ATTEMPT_ID", raising=False)
    if ownership == "bridge_flag":
        monkeypatch.setenv("RESEARCH_COMPACTION_RUN_SCOPED", "true")
    elif ownership == "bridge_attempt":
        monkeypatch.setenv("RESEARCH_PROCESS_ATTEMPT_ID", "attempt-1")
    middleware = _middleware(sm)
    model_call = middleware._summary_model.ainvoke if mode == "async" else middleware._summary_model.invoke
    model_call.side_effect = TimeoutError("offline native summary failure")
    with pytest.raises(ResearchCompactionError):
        _run(middleware, _messages(), mode)
    stop = get_compaction_stop()
    if ownership != "native":
        assert stop.reason == "summary_model_failed"
        assert stop.thread_id == "thread-1"
    else:
        assert stop is None
        other = _middleware(sm)
        result = _run(other, _messages(), mode, SimpleNamespace(context={"thread_id": "unrelated-thread"}))
        assert isinstance(result["messages"][0], RemoveMessage)
        assert get_compaction_stop() is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_native_gateway_uses_paths_archive_without_mutating_environment(sm, monkeypatch, tmp_path, mode):
    for name in (
        "RESEARCH_COMPACTION_DB", "RESEARCH_BUDGET_DB",
        "RESEARCH_COMPACTION_RUN_SCOPED", "RESEARCH_PROCESS_ATTEMPT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    base = tmp_path / "native-deerflow"
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: SimpleNamespace(base_dir=base))
    original_env = dict(os.environ)
    messages = _messages()
    result = _run(_middleware(sm), messages, mode)
    archive = base / "research_compaction.sqlite3"
    assert archive.is_file()
    with sqlite3.connect(archive) as conn:
        row = conn.execute("SELECT thread_id, content, source_messages_json FROM compaction_receipts").fetchone()
    assert row[0] == "thread-1"
    assert row[1] == result["messages"][1].content
    assert json.loads(row[2]) == messages_to_dict(messages[:3])
    assert dict(os.environ) == original_env


def test_native_backend_imports_local_helper_in_fresh_process(tmp_path):
    root = Path(__file__).resolve().parents[3]
    backend = tmp_path / "deer-flow/backend"
    overlay = backend / "packages/harness/deerflow/agents/middlewares/summarization_middleware.py"
    overlay.parent.mkdir(parents=True)
    shutil.copyfile(root / "deerflow_bridge/patches/middlewares/summarization_middleware.py", overlay)
    shutil.copyfile(root / "deerflow_bridge/research_compaction.py", backend / "research_compaction.py")
    script = '''
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Only the existing harness import-cycle shim is needed, not gateway startup.
executor = MagicMock()
executor.SubagentExecutor = executor.SubagentResult = executor.SubagentStatus = MagicMock
executor.MAX_CONCURRENT_SUBAGENTS = 3
sys.modules["deerflow.subagents.executor"] = executor
assert "research_compaction" not in sys.modules
overlay = Path("packages/harness/deerflow/agents/middlewares/summarization_middleware.py")
spec = importlib.util.spec_from_file_location("native_overlay_under_test", overlay)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
import research_compaction as contract
assert Path(contract.__file__).resolve() == Path("research_compaction.py").resolve()
assert not any(path.endswith("/deerflow_bridge") for path in sys.path)

import deerflow.config.paths as paths
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage
paths.get_paths = lambda: SimpleNamespace(base_dir=Path.cwd() / ".native-data")
original_env = dict(os.environ)
model = MagicMock()
model.config = {}
model.with_config.return_value = model
model.invoke.return_value = SimpleNamespace(text="23 units; receipt R1")
middleware = module.DeerFlowSummarizationMiddleware(
    model=model, trigger=("messages", 4), keep=("messages", 2),
    token_counter=len, preserve_recent_skill_count=0, trim_tokens_to_summarize=None,
)
messages = [HumanMessage(content="source fact", id="a"), AIMessage(content="23 units; receipt R1", id="b"),
            HumanMessage(content="continue", id="c"), AIMessage(content="next", id="d")]
result = middleware.before_model({"messages": messages}, SimpleNamespace(context={"thread_id": "native-success"}))
assert isinstance(result["messages"][0], RemoveMessage)
assert (Path(".native-data") / "research_compaction.sqlite3").is_file()
model.invoke.side_effect = TimeoutError("offline failure")
try:
    middleware.before_model({"messages": messages}, SimpleNamespace(context={"thread_id": "native-failure"}))
except contract.ResearchCompactionError:
    pass
else:
    raise AssertionError("native failure did not remain typed")
assert contract.get_compaction_stop() is None
assert dict(os.environ) == original_env
print("native backend import, durable fallback, and thread-local failure passed")
'''
    env = dict(os.environ)
    for name in (
        "RESEARCH_COMPACTION_DB", "RESEARCH_BUDGET_DB",
        "RESEARCH_COMPACTION_RUN_SCOPED", "RESEARCH_PROCESS_ATTEMPT_ID",
    ):
        env.pop(name, None)
    env["PYTHONPATH"] = "."
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=backend, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "thread-local failure passed" in result.stdout


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_success_archives_original_evidence_before_optional_hooks(sm, monkeypatch, mode):
    order = []
    archive_calls = []

    def record(**kwargs):
        order.append("archive")
        archive_calls.append(kwargs)
        return {"schema": "test-contract", "receipt_id": "receipt-1"}

    def hook(event):
        order.append("hook")
        assert event.thread_id == "thread-1"
        raise RuntimeError("optional memory hook unavailable")

    monkeypatch.setattr(sm, "record_compaction", record, raising=False)
    middleware = _middleware(sm, hook=hook)
    original = _messages()
    output = _run(middleware, original, mode)["messages"]
    assert order == ["archive", "hook"]
    assert isinstance(output[0], RemoveMessage)
    assert output[1].name == "summary"
    assert output[1].id
    assert output[1].additional_kwargs["drf_compaction"]["receipt_id"] == "receipt-1"
    assert output[2:] == original[-2:]
    assert archive_calls == [{
        "thread_id": "thread-1", "message_id": output[1].id,
        "content": output[1].content, "source_messages": messages_to_dict(original[:3]),
    }]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_missing_thread_fails_before_spending_or_archiving(sm, monkeypatch, mode):
    archive = MagicMock()
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    monkeypatch.setattr(sm, "get_config", lambda: {})
    middleware = _middleware(sm)
    with pytest.raises(RuntimeError):
        _run(middleware, _messages(), mode, SimpleNamespace(context={}))
    middleware._summary_model.invoke.assert_not_called()
    middleware._summary_model.ainvoke.assert_not_called()
    archive.assert_not_called()


def test_runtime_config_thread_fallback_and_summary_helper_compatibility(sm, monkeypatch):
    monkeypatch.setattr(sm, "get_config", lambda: {"configurable": {"thread_id": "config-thread"}})
    archive = MagicMock(return_value={"receipt_id": "receipt-1"})
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    middleware = _middleware(sm)
    result = _run(middleware, _messages(), "sync", SimpleNamespace(context={}))
    assert archive.call_args.kwargs["thread_id"] == "config-thread"
    assert result["messages"][1].name == "summary"
    assert middleware._build_new_messages("Short but valid")[0].content.endswith("Short but valid")


def test_dynamic_context_and_model_tag_contracts_survive(sm, monkeypatch):
    monkeypatch.setattr(sm, "record_compaction", lambda **kwargs: {"receipt_id": "receipt-1"}, raising=False)
    from deerflow.agents.middlewares.dynamic_context_middleware import _DYNAMIC_CONTEXT_REMINDER_KEY
    reminder = HumanMessage(
        content="current date", id="date", additional_kwargs={_DYNAMIC_CONTEXT_REMINDER_KEY: True},
    )
    middleware = _middleware(sm)
    raw_model = middleware.model
    messages = [reminder, *_messages()]
    result = _run(middleware, messages, "sync")["messages"]
    assert reminder in result[2:]
    assert middleware.model is raw_model
    tags = raw_model.with_config.call_args.kwargs["tags"]
    assert tags == ["middleware:summarize", TAG_NOSTREAM]


def test_archive_preserves_unsplit_skill_and_source_calls(sm, monkeypatch):
    archive = MagicMock(return_value={"receipt_id": "receipt-1"})
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    mixed_call = AIMessage(content="Need both sources", id="mixed", tool_calls=[
        {"name": "read_file", "args": {"path": "/mnt/skills/research/SKILL.md"}, "id": "skill"},
        {"name": "web_fetch", "args": {"url": "https://example.test/source"}, "id": "fetch"},
    ])
    skill = ToolMessage(content="Skill procedure", id="skill-result", tool_call_id="skill")
    source = ToolMessage(content="23 units; receipt R1", id="source-result", tool_call_id="fetch")
    messages = [HumanMessage(content="Investigate", id="first"), mixed_call, skill, source, *_messages()[-2:]]
    original_prefix = messages_to_dict(messages[:-2])
    middleware = _middleware(sm, preserve_recent_skill_count=1)
    emitted = _run(middleware, messages, "sync")["messages"]
    assert archive.call_args.kwargs["source_messages"] == original_prefix
    assert skill in emitted[2:]
    rescued_ai = next(message for message in emitted[2:] if isinstance(message, AIMessage) and message.tool_calls)
    assert [call["id"] for call in rescued_ai.tool_calls] == ["skill"]
    assert messages_to_dict(messages[:-2]) == original_prefix


class _StaticModel(BaseChatModel):
    calls: int = 0
    fail: bool = False

    @property
    def _llm_type(self):
        return "astra-offline-static"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.fail:
            raise TimeoutError("offline simulated summary timeout")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="23 units; receipt R1"))])


def test_sqlite_checkpoint_reopens_and_resumes_after_compaction_failure(sm, monkeypatch, tmp_path):
    archive_path = tmp_path / "compaction.sqlite3"
    monkeypatch.setenv("RESEARCH_COMPACTION_DB", str(archive_path))
    archive = MagicMock(wraps=sm.record_compaction)
    monkeypatch.setattr(sm, "record_compaction", archive, raising=False)
    summary_model = _StaticModel(fail=True)
    lead_model = _StaticModel()
    config = {"configurable": {"thread_id": "sqlite-thread"}}
    db_path = str(tmp_path / "checkpoints.sqlite")
    messages = _messages()

    with SqliteSaver.from_conn_string(db_path) as saver:
        agent = create_agent(
            model=lead_model, tools=[], middleware=[_middleware(sm, model=summary_model)], checkpointer=saver,
        )
        with pytest.raises(RuntimeError):
            agent.invoke({"messages": messages}, config=config)
        assert lead_model.calls == 0
        assert summary_model.calls == 1
        assert messages_to_dict(agent.get_state(config).values["messages"]) == messages_to_dict(messages)
        archive.assert_not_called()

    # Explicit attempt admission permits a same-thread retry; sibling calls in
    # the failed attempt must continue observing the shared stop until this point.
    from research_compaction import reset_compaction_stop
    reset_compaction_stop()
    summary_model.fail = False
    with SqliteSaver.from_conn_string(db_path) as saver:
        resumed = create_agent(
            model=lead_model, tools=[], middleware=[_middleware(sm, model=summary_model)], checkpointer=saver,
        )
        output = resumed.invoke(None, config=config)
        assert lead_model.calls == 1
        assert summary_model.calls == 2
        summary = next(message for message in output["messages"] if message.name == "summary")
        assert archive.call_count == 1
    from research_compaction import validate_compaction
    from deerflow.client import DeerFlowClient
    assert validate_compaction(summary.model_dump(), "sqlite-thread")
    serialized = DeerFlowClient._serialize_message(summary)
    assert validate_compaction(serialized, "sqlite-thread")
    assert serialized["additional_kwargs"] == summary.additional_kwargs
    with sqlite3.connect(archive_path) as conn:
        source_json = conn.execute("SELECT source_messages_json FROM compaction_receipts").fetchone()[0]
    assert json.loads(source_json) == messages_to_dict(messages[:3])
