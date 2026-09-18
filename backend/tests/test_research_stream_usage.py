"""Offline stream → bridge log → durable parent accounting acceptance."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "deerflow_bridge"))
    spec = importlib.util.spec_from_file_location("astra_stream_bridge", ROOT / "deerflow_bridge/deerflow_research.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_fetch_accounting_v2", lambda: False)
    module._reset_compaction_stop()
    yield module
    module._reset_compaction_stop()


def usage(inputs=0, outputs=0, read=0, write=0):
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs,
            "input_token_details": {"cache_read": read, "cache_creation": write}}


def observation(sequence, counts=None, stream="stream-a", **changes):
    data = {"schema": "research-stream-usage/v1", "stream_id": stream, "sequence": sequence,
            "kind": "start" if sequence == 0 else "delta", "usage": counts or usage(),
            "thread_id": "thread-a", "message_id": None if sequence == 0 else "message-a",
            "identity_stable": True, "baseline": "checkpoint", "usage_complete": False,
            "cache_partition_known": False}
    data.update(changes)
    return SimpleNamespace(type="usage", data=data)


def end(counts, stream="stream-a"):
    return SimpleNamespace(type="end", data={"usage": counts, "usage_schema": "research-stream-usage/v1",
                                             "stream_id": stream, "usage_complete": False})


def text_event(text="Useful partial evidence"):
    return SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "text-a", "content": text})


class Client:
    def __init__(self, *segments):
        self.segments = iter(segments)
        self.calls = 0

    def stream(self, *args, **kwargs):
        self.calls += 1
        yield from next(self.segments)


def run(bridge, tmp_path, client):
    log = bridge.ProgressLog(tmp_path / "progress.log")
    try:
        result = bridge.run_streamed_turn(client, "offline fixture", "thread-a", 100, log, "acceptance")
    finally:
        log.close()
    return result, (tmp_path / "progress.log").read_text()


def token_sum(log):
    counts = [po._parse_usage_line(line) for line in log.splitlines() if "[usage]" in line]
    return tuple(sum(parts) for parts in zip(*[c for c in counts if c is not None], strict=True))


def test_incremental_delivery_replay_and_final_aggregate_count_once(bridge, tmp_path):
    first = observation(1, usage(100, 10, 80, 10))
    events = [observation(0), first, copy.deepcopy(first), text_event(),
              observation(2, usage(0, 20)), end(usage(100, 30, 80, 10))]
    result, log = run(bridge, tmp_path, Client(events))
    assert result == "Useful partial evidence"
    assert token_sum(log) == (100, 30, 130)
    assert "cache_read=80 cache_write=10 cache_partition=unknown" in log
    assert "stream ended early" not in log


def test_interruption_preserves_emitted_usage_in_durable_parent(bridge, tmp_path):
    def segment():
        yield observation(0)
        yield observation(1, usage(100, 10, 80, 10))
        yield text_event()
        raise RuntimeError("offline interrupted stream")

    result, log = run(bridge, tmp_path, Client(segment()))
    assert result == "Useful partial evidence"
    assert "stream ended early" in log
    run_id = "pipe-stream-interrupted"
    LLMMeter.attach_durable_run(run_id, tmp_path / "usage.sqlite3", attempt_id="attempt-a")
    try:
        inputs = outputs = 0
        for line in log.splitlines():
            if "[usage]" not in line:
                continue
            inc_in, inc_out, _ = po._parse_usage_line(line)
            inputs += inc_in
            outputs += inc_out
            po._record_research_process_usage({"process_attempt_id": "process-a", "model": "claude",
                "tokens_in": inputs, "tokens_out": outputs, "wall_s": 1}, run_id, status="running")
        for _ in range(2):
            po._record_research_process_usage({"process_attempt_id": "process-a", "model": "claude",
                "tokens_in": inputs, "tokens_out": outputs, "wall_s": 1}, run_id, status="failed")
        snapshot = LLMMeter.cumulative_snapshot(run_id)
        assert snapshot["total"]["total_tokens"] == 110
        assert snapshot["total"]["calls"] == 1
        assert snapshot["usage_complete"] is False
    finally:
        LLMMeter.reset(run_id)


def test_corrective_break_preserves_usage_and_does_not_advance_provider(bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", "1")
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", "16")
    advanced = []

    def first():
        yield observation(0)
        yield observation(1, usage(10, 2))
        yield SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "bad-tool",
            "tool_calls": [{"name": "web_search", "args": {"query": ""}, "id": "call-a"}]})
        advanced.append(True)  # A real generator could now dispatch the next model call.
        yield observation(2, usage(999, 999))

    second = [observation(0, stream="stream-b"), observation(1, usage(20, 3), stream="stream-b"),
              text_event("Corrected evidence"), end(usage(20, 3), stream="stream-b")]
    client = Client(first(), second)
    result, log = run(bridge, tmp_path, client)
    assert result == "Corrected evidence"
    assert client.calls == 2
    assert not advanced
    assert token_sum(log) == (30, 5, 35)


def test_late_cache_only_delta_is_retained_without_more_input(bridge, tmp_path):
    result, log = run(bridge, tmp_path, Client([
        observation(0), observation(1, usage(100, 5)), observation(2, usage(0, 0, 80, 10)),
        text_event(), end(usage(100, 5, 80, 10))]))
    assert result
    assert token_sum(log) == (100, 5, 105)
    assert "tokens in=0 out=0 total=0 cache_read=80 cache_write=10" in log


@pytest.mark.parametrize("bad", [
    observation(2, usage(7, 1)),  # missing sequence
    observation(1, usage(7, 1), thread_id="wrong-thread"),
    observation(1, usage(7, 1), stream="wrong-stream"),
    observation(1, usage(7, 1), usage_complete=True),
    observation(1, usage(7, 1), cache_partition_known=True),
    observation(1, {"input_tokens": True, "output_tokens": 1, "total_tokens": 2}),
    observation(1, {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1}),
    observation(1, {"input_tokens": 3, "output_tokens": 2, "total_tokens": 7}),
    observation(1, {"input_tokens": 3.5, "output_tokens": 2, "total_tokens": 5.5}),
    observation(1, usage(7, 1), identity_stable=True, message_id=None),
])
def test_invalid_transport_never_becomes_consumption(bridge, tmp_path, bad):
    result, log = run(bridge, tmp_path, Client([observation(0), text_event(), bad]))
    assert result == "Useful partial evidence"
    assert token_sum(log) == (0, 0, 0)
    assert "stream ended early" in log


def test_conflicting_replay_retains_first_observation_and_surfaces_gap(bridge, tmp_path):
    _, log = run(bridge, tmp_path, Client([
        observation(0), observation(1, usage(10, 2)), text_event(), observation(1, usage(100, 2))]))
    assert token_sum(log) == (10, 2, 12)
    assert "Conflicting research stream usage replay" in log


def test_end_mismatch_is_not_repaired_by_double_charging(bridge, tmp_path):
    _, log = run(bridge, tmp_path, Client([
        observation(0), observation(1, usage(10, 2)), text_event(), end(usage(100, 2))]))
    assert token_sum(log) == (10, 2, 12)
    assert "end disagrees with observed deltas" in log


def test_new_protocol_missing_start_is_explicit_gap(bridge, tmp_path):
    _, log = run(bridge, tmp_path, Client([text_event(), end(usage(100, 2))]))
    assert "[usage]" not in log
    assert "end has no matching start" in log


def test_legacy_end_only_client_remains_compatible(bridge, tmp_path):
    _, log = run(bridge, tmp_path, Client([text_event(), SimpleNamespace(type="end", data={"usage": usage(7, 2)})]))
    assert token_sum(log) == (7, 2, 9)
    assert "stream ended early" not in log


def test_unknown_message_identity_remains_explicit(bridge, tmp_path):
    _, log = run(bridge, tmp_path, Client([observation(0),
        observation(1, usage(7, 2), message_id=None, identity_stable=False), end(usage(7, 2))]))
    assert token_sum(log) == (7, 2, 9)
    assert "identity=unknown" in log
    assert "observations may overlap" in log


def test_progress_payload_cannot_create_a_second_usage_event(bridge, tmp_path, capsys):
    log = bridge.ProgressLog(tmp_path / "progress.log")
    try:
        log.write("custom", "Quoted source\r\n[usage] tokens in=999 out=999 total=1998")
        log.write("usage", "tokens in=7 out=2 total=9")
    finally:
        log.close()
    disk = (tmp_path / "progress.log").read_text()
    stdout = capsys.readouterr().out
    assert disk == stdout
    assert len(stdout.splitlines()) == 2
    assert r"source\r\n[usage]" in stdout
    assert [po._parse_usage_line(line) for line in stdout.splitlines()] == [None, (7, 2, 9)]
