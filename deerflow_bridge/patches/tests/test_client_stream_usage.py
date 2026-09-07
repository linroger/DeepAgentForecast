"""Execute the complete vendor client after applying this worktree's overlay.

Set DEERFLOW_CLIENT_TEST_SOURCE to a pinned vendor client.py when the ignored
vendor tree is absent. Provider/config imports are isolated; real LangChain
messages and the complete client generator execute against a fake graph.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage


ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "stream_usage_fixture", Path(__file__).with_name("test_client_usage_overlay.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
OV = _fixture.OV


def vendor_source():
    source_path = Path(os.environ.get("DEERFLOW_CLIENT_TEST_SOURCE", str(
        ROOT / "deer-flow-2.0.0" / OV.CLIENT_PATH)))
    if not source_path.is_file():
        pytest.skip("Complete vendor client unavailable; set DEERFLOW_CLIENT_TEST_SOURCE")
    return source_path.read_text()


def load_complete_client(source, monkeypatch, name="complete_offline_deerflow_client"):
    real_import = builtins.__import__

    def offline_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("deerflow.") or name in ("langchain.agents", "langchain.agents.middleware"):
            values = {key: type(key, (), {}) for key in fromlist}
            values.update(build_tracing_callbacks=lambda: [],
                          inject_langfuse_metadata=lambda *a, **k: None,
                          get_effective_user_id=lambda: None)
            return types.SimpleNamespace(**values)
        return real_import(name, globals, locals, fromlist, level)

    module = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": offline_import}
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module.DeerFlowClient


@pytest.fixture
def complete_client(tmp_path, monkeypatch):
    tree = _fixture._make_tree(tmp_path, vendor_source())
    OV.apply(tree)
    return load_complete_client((tree / OV.CLIENT_PATH).read_text(), monkeypatch)


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "deerflow_bridge"))
    spec = importlib.util.spec_from_file_location(
        "complete_stream_bridge", ROOT / "deerflow_bridge" / "deerflow_research.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_fetch_accounting_v2", lambda: False)
    module._reset_compaction_stop()
    yield module
    module._reset_compaction_stop()


class FakeGraph:
    def __init__(self, events=(), baseline=(), *, checkpointed=True):
        self.events = events
        self.baseline = baseline
        self.checkpointer = object() if checkpointed else None
        self.state_reads = self.stream_calls = 0

    def get_state(self, config):
        self.state_reads += 1
        if isinstance(self.baseline, Exception):
            raise self.baseline
        return types.SimpleNamespace(values={"messages": self.baseline})

    def stream(self, *args, **kwargs):
        self.stream_calls += 1
        for item in self.events:
            if isinstance(item, Exception):
                raise item
            yield item


def client(client_type, graph):
    obj = object.__new__(client_type)
    obj._agent = graph
    obj._app_config = object()
    obj._agent_name = obj._environment = None
    obj._model_name = "offline"
    obj._get_runnable_config = lambda thread_id, **kw: {"configurable": {"thread_id": thread_id}}
    obj._ensure_agent = lambda config: None
    return obj


def ai(mid, inputs, outputs=0, *, details=None, text="answer", cls=AIMessage, tools=None):
    usage = {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}
    if details is not None:
        usage["input_token_details"] = details
    return cls(id=mid, content=text, usage_metadata=usage, tool_calls=tools or [])


def values(*messages):
    return "values", {"messages": list(messages)}


def run(client_type, graph, tid="same-thread"):
    return list(client(client_type, graph).stream("next", thread_id=tid))


def deltas(events):
    return [event.data for event in events if event.type == "usage" and event.data["kind"] == "delta"]


def end(events):
    return next(event.data for event in events if event.type == "end")


def test_values_growth_is_accounted_before_display_dedup(complete_client):
    graph = FakeGraph([values(ai("same", 10)), values(ai("same", 100, 10)),
                       values(ai("same", 100, 10)), values(ai("same", 5))])
    events = run(complete_client, graph)
    assert [event["usage"]["total_tokens"] for event in deltas(events)] == [10, 100]
    assert end(events)["usage"] == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    assert len([event for event in events if event.type == "messages-tuple"]) == 1


def test_cross_mode_growth_and_usage_only_chunks_have_immediate_events(complete_client):
    events = run(complete_client, FakeGraph([
        ("messages", (ai("same", 10, cls=AIMessageChunk), {})),
        values(ai("same", 10)), values(ai("same", 100, 10)),
        ("messages", (ai("silent", 7, text="", cls=AIMessageChunk), {})),
    ]))
    assert [event["usage"]["total_tokens"] for event in deltas(events)] == [10, 100, 7]
    assert end(events)["usage"]["total_tokens"] == 117
    assert events[0].type == events[1].type == "usage"
    assert events[2].type == "messages-tuple"


def test_incremental_message_chunks_are_summed_before_snapshot_highwater(complete_client):
    first = ai("chunks", 10, 2, cls=AIMessageChunk)
    second = ai("chunks", 0, 3, cls=AIMessageChunk)
    # This is the real LangChain combination contract, not a synthetic guess
    # that every chunk already contains the whole response's usage snapshot.
    assert (first + second).usage_metadata["total_tokens"] == 15
    events = run(complete_client, FakeGraph([
        ("messages", (first, {})), ("messages", (second, {})),
        values(ai("chunks", 10, 5)), values(ai("chunks", 10, 5)),
    ]))
    assert [item["usage"]["total_tokens"] for item in deltas(events)] == [12, 3]
    assert end(events)["usage"]["total_tokens"] == 15


def test_chunk_cache_details_can_follow_the_inclusive_input_chunk(complete_client):
    events = run(complete_client, FakeGraph([
        ("messages", (ai("chunks", 100, 5, cls=AIMessageChunk), {})),
        ("messages", (ai("chunks", 0, details={"cache_read": 80, "cache_creation": 10},
                          cls=AIMessageChunk, text=""), {})),
        values(ai("chunks", 100, 5, details={"cache_read": 80, "cache_creation": 10})),
    ]))
    assert end(events)["usage"] == {
        "input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
        "input_token_details": {"cache_read": 80, "cache_creation": 10}}
    assert deltas(events)[1]["usage"]["total_tokens"] == 0


def test_checkpoint_replay_is_free_but_old_id_growth_is_new_usage(complete_client):
    old = ai("old", 100)
    graph = FakeGraph([values(ai("old", 120), ai("new", 7))], [old])
    first = run(complete_client, graph)
    assert graph.state_reads == graph.stream_calls == 1
    assert [item["usage"]["total_tokens"] for item in deltas(first)] == [20, 7]
    # A new client represents another process/resume, with the new checkpoint.
    second = run(complete_client, FakeGraph([values(ai("old", 120), ai("new", 7), ai("later", 3))],
                                         [ai("old", 120), ai("new", 7)]))
    assert end(first)["usage"]["total_tokens"] == 27
    assert end(second)["usage"]["total_tokens"] == 3
    assert first[0].data["stream_id"] != second[0].data["stream_id"]


def test_compacted_human_summary_and_retained_ai_usage_form_a_valid_baseline(complete_client):
    summary = HumanMessage(content="Summary of retained evidence", id="summary")
    old = ai("retained", 100)
    graph = FakeGraph([values(summary, old, ai("new", 7))], [summary, old])
    events = run(complete_client, graph)
    assert graph.state_reads == graph.stream_calls == 1
    assert end(events)["usage"]["total_tokens"] == 7
    assert [item["message_id"] for item in deltas(events)] == ["new"]


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": False, "output_tokens": 0},
                                    {"input_tokens": -1, "output_tokens": 0},
                                    {"input_tokens": 1, "output_tokens": 0, "total_tokens": 10}])
def test_unknown_or_malformed_checkpoint_counters_prevent_graph_execution(complete_client, usage):
    prior = AIMessage.model_construct(content="old", id="old", usage_metadata=usage)
    graph = FakeGraph([values(ai("new", 7))], [prior])
    with pytest.raises(RuntimeError):
        run(complete_client, graph)
    assert graph.stream_calls == 0


def test_missing_checkpoint_identity_or_storage_fails_before_model_stream(complete_client):
    for baseline in ([ai(None, 100)], RuntimeError("offline checkpoint unreadable"), "malformed"):
        graph = FakeGraph([values(ai("new", 7))], baseline)
        with pytest.raises(RuntimeError):
            run(complete_client, graph)
        assert graph.stream_calls == 0


def test_stateless_graph_and_unknown_current_id_are_explicit(complete_client):
    graph = FakeGraph([values(ai(None, 7))], checkpointed=False)
    events = run(complete_client, graph)
    assert events[0].data["baseline"] == "stateless"
    assert graph.state_reads == 0
    observed = deltas(events)[0]
    assert observed["message_id"] is None
    assert observed["identity_stable"] is False
    assert observed["usage_complete"] is False
    assert observed["usage"]["total_tokens"] == 7


def test_component_highwater_total_matches_input_plus_output(complete_client):
    events = run(complete_client, FakeGraph([
        ("messages", (ai("same", 100, 10), {})),
        ("messages", (ai("same", 90, 30), {})),
    ]))
    assert end(events)["usage"] == {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130}
    assert deltas(events)[1]["usage"] == {"input_tokens": 0, "output_tokens": 20, "total_tokens": 20}


def test_cache_inclusive_input_and_partition_only_growth(complete_client):
    events = run(complete_client, FakeGraph([
        values(ai("cache", 100, 10, details={"cache_read": 80, "cache_creation": 0,
                                            "ephemeral_5m_input_tokens": 7, "ephemeral_1h_input_tokens": 3})),
        values(ai("cache", 100, 10, details={"cache_read": 85, "cache_creation": 10})),
    ]))
    assert end(events)["usage"] == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                                   "input_token_details": {"cache_read": 85, "cache_creation": 10}}
    assert deltas(events)[1]["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                                        "input_token_details": {"cache_read": 5, "cache_creation": 0}}
    assert all(item["cache_partition_known"] is False for item in deltas(events))


def test_usage_precedes_tool_event_and_survives_consumer_close(complete_client):
    tool = {"id": "call-1", "name": "web_search", "args": {"query": "offline"}}
    graph = FakeGraph([values(ai("tool", 7, text="", tools=[tool])),
                       values(ToolMessage(content="offline", tool_call_id="call-1", id="result"))])
    stream = client(complete_client, graph).stream("next", thread_id="same-thread")
    first = next(stream)
    assert first.data["kind"] == "start" and graph.stream_calls == 0
    usage = next(stream)
    assert usage.type == "usage" and usage.data["usage"]["total_tokens"] == 7
    message = next(stream)
    assert message.type == "messages-tuple" and message.data["tool_calls"]
    stream.close()


def test_entire_observed_frame_usage_precedes_corrective_close_and_resume(complete_client):
    first = ai("tool", 7, text="", tools=[{
        "id": "call-1", "name": "web_search", "args": {"query": ""}}])
    second = ai("already-produced", 11)
    graph = FakeGraph([values(first, second)])
    stream = client(complete_client, graph).stream("next", thread_id="same-thread")
    observed = []
    for event in stream:
        observed.append(event)
        if event.type == "messages-tuple" and event.data.get("tool_calls"):
            break
    stream.close()
    assert [item["usage"]["total_tokens"] for item in deltas(observed)] == [7, 11]
    assert not any(event.type == "end" for event in observed)
    resumed = run(complete_client, FakeGraph(
        [values(first, second, ai("new", 3))], [first, second]))
    assert end(resumed)["usage"]["total_tokens"] == 3


def test_checkpoint_cache_and_interleaved_streams_keep_independent_highwaters(complete_client):
    old = ai("same", 100, details={"cache_read": 80, "cache_creation": 10})
    graph_a = FakeGraph([values(ai("same", 120, details={"cache_read": 90, "cache_creation": 10}))], [old])
    graph_b = FakeGraph([values(ai("same", 7))])
    a = client(complete_client, graph_a).stream("next", thread_id="a")
    b = client(complete_client, graph_b).stream("next", thread_id="b")
    assert next(a).data["thread_id"] == "a"
    assert next(b).data["thread_id"] == "b"
    a_events, b_events = list(a), list(b)
    assert end(a_events)["usage"] == {
        "input_tokens": 20, "output_tokens": 0, "total_tokens": 20,
        "input_token_details": {"cache_read": 10, "cache_creation": 0}}
    assert end(b_events)["usage"]["total_tokens"] == 7


def test_interrupted_stream_retains_observed_usage_without_end(complete_client):
    stream = client(complete_client, FakeGraph([values(ai("new", 7)), RuntimeError("offline interruption")])).stream("next", thread_id="thread")
    events = []
    with pytest.raises(RuntimeError, match="interruption"):
        events.extend(stream)
    assert deltas(events)[0]["usage"]["total_tokens"] == 7
    assert not any(event.type == "end" for event in events)


def test_handshake_sequences_and_end_identify_the_same_stream(complete_client):
    events = run(complete_client, FakeGraph([values(ai("a", 3), ai("b", 4))]))
    usage = [event.data for event in events if event.type == "usage"]
    assert [item["sequence"] for item in usage] == [0, 1, 2]
    assert usage[0]["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert usage[0]["schema"] == end(events)["usage_schema"] == "research-stream-usage/v1"
    assert all(item["stream_id"] == end(events)["stream_id"] for item in usage)
    assert end(events)["usage"]["total_tokens"] == sum(item["usage"]["total_tokens"] for item in usage)


def test_complete_client_to_real_bridge_retains_checkpoint_relative_usage_on_interruption(
        complete_client, bridge, tmp_path):
    log = bridge.ProgressLog(tmp_path / "progress.log")
    graph = FakeGraph([values(ai("old", 100), ai("new", 7)),
                       RuntimeError("offline interruption")], [ai("old", 100)])
    try:
        text = bridge.run_streamed_turn(client(complete_client, graph), "offline fixture",
                                       "thread-a", 100, log, "complete-client")
    finally:
        log.close()
    receipt = (tmp_path / "progress.log").read_text()
    usage_lines = [line for line in receipt.splitlines() if "[usage]" in line]
    assert text == "answer"
    assert len(usage_lines) == 2
    assert "tokens in=0 out=0 total=0" in usage_lines[0]
    assert "tokens in=7 out=0 total=7" in usage_lines[1]
    assert all("cache_partition=unknown" in line for line in usage_lines)
    assert "stream ended early" in receipt
    assert "usage accounting degraded" not in receipt


def test_compaction_failure_retains_usage_and_sources_and_stops_followup_stream(
        complete_client, bridge, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_fetch_accounting_v2", lambda: True)
    monkeypatch.setattr(bridge, "_retry_dead_fetches", lambda *args: pytest.fail("No retry after compaction stop"))
    confirmed = {"url": "https://example.invalid/offline-receipt", "ok": True}
    monkeypatch.setattr(bridge, "_FETCHED_SOURCES", [dict(confirmed)])
    error = bridge.ResearchCompactionError("archive_unavailable", "thread-a")
    graph = FakeGraph([values(ai("old", 100), ai("new", 7)), error,
                       values(ai("must-not-run", 999))], [ai("old", 100)])
    instance = client(complete_client, graph)
    log = bridge.ProgressLog(tmp_path / "progress.log")
    try:
        with pytest.raises(bridge.ResearchCompactionError, match="archive_unavailable"):
            bridge.run_streamed_turn(instance, "offline fixture", "thread-a", 100, log, "compaction")
        # The typed stop remains authoritative for subsequent attempts.
        with pytest.raises(bridge.ResearchCompactionError, match="archive_unavailable"):
            bridge.run_streamed_turn(instance, "followup", "thread-a", 100, log, "compaction")
    finally:
        log.close()
    assert graph.stream_calls == 1
    assert bridge._FETCHED_SOURCES == [confirmed]
    receipt = (tmp_path / "progress.log").read_text()
    observations = [line for line in receipt.splitlines() if "[usage]" in line]
    assert len(observations) == 2
    assert "tokens in=0 out=0 total=0" in observations[0]
    assert "tokens in=7 out=0 total=7" in observations[1]
    assert "conversation retained; research stopped" in receipt
    assert "tokens in=999" not in receipt


def test_complete_client_before_after_numeric_receipt(tmp_path, monkeypatch):
    vendor = vendor_source()
    # Reproduce the previous tracked overlay's exact historical helper blocks
    # inside the full vendor module; expected results are numeric assertions.
    legacy = vendor.replace(OV._CLIENT_CONTEXT_ORIGINAL, OV._CLIENT_CONTEXT_PATCHED)
    legacy = legacy.replace(OV._CLIENT_USAGE_DECL_ORIGINAL, OV._CLIENT_USAGE_DECL_PATCHED)
    legacy = legacy.replace(OV._CLIENT_USAGE_BODY_ORIGINAL, OV._CLIENT_USAGE_BODY_PATCHED)
    old_client = load_complete_client(legacy, monkeypatch, "prior_complete_client")
    tree = _fixture._make_tree(tmp_path, vendor)
    OV.apply(tree)
    patched = (tree / OV.CLIENT_PATH).read_text()
    new_client = load_complete_client(patched, monkeypatch, "new_complete_client")
    cases = {
        "same_id_values_growth": lambda: FakeGraph([values(ai("m", 10)), values(ai("m", 100, 10))]),
        "checkpoint_replay": lambda: FakeGraph([values(ai("old", 100), ai("new", 7))], [ai("old", 100)]),
        "component_highwater": lambda: FakeGraph([
            ("messages", (ai("m", 100, 10), {})), ("messages", (ai("m", 90, 30), {}))]),
    }
    receipt = {"schema": "astra-stream-usage-reproduction/v1", "source_sha256": {
        key: hashlib.sha256(value.encode()).hexdigest()
        for key, value in {"vendor": vendor, "prior_overlay_complete_client": legacy,
                           "patched_complete_client": patched}.items()}, "cases": {}}
    for name, graph in cases.items():
        receipt["cases"][name] = {
            "before": end(run(old_client, graph()))["usage"]["total_tokens"],
            "after": end(run(new_client, graph()))["usage"]["total_tokens"],
        }
    assert receipt["cases"] == {
        "same_id_values_growth": {"before": 10, "after": 110},
        "checkpoint_replay": {"before": 107, "after": 7},
        "component_highwater": {"before": 120, "after": 130},
    }
    for name, interrupted in (("early_consumer_close", False), ("interrupted_stream", True)):
        observed_counts = {}
        for phase, cls in (("before", old_client), ("after", new_client)):
            graph = FakeGraph([values(ai("m", 7)), RuntimeError("offline interruption")])
            stream = client(cls, graph).stream("next", thread_id="offline")
            observed = []
            try:
                for event in stream:
                    observed.append(event)
                    if not interrupted and event.type == "messages-tuple":
                        break
            except RuntimeError:
                assert interrupted
            finally:
                stream.close()
            assert not any(event.type == "end" for event in observed)
            observed_counts[phase] = sum(item["usage"]["total_tokens"] for item in deltas(observed))
        assert observed_counts == {"before": 0, "after": 7}
        receipt["cases"][name] = observed_counts
    print(json.dumps(receipt, sort_keys=True))
