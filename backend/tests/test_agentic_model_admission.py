"""Offline durable prompt admission: no provider invocation or real credentials."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"
sys.path.insert(0, str(BRIDGE))

import research_admission as admission  # noqa: E402
import research_archive as archive  # noqa: E402
import research_compaction as compaction  # noqa: E402
from research_workspace import ResearchWorkspace  # noqa: E402

MESSAGES = [{"role": "user", "content": "private prompt 中文\r\n" * 20}]
IDENTITY = {"run": "admission-fixture", "model": "offline", "policy": "v1"}


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for path in (BRIDGE / "research_admission.py", Path(__file__)):
        record_testsuite_property("admission_source_sha256:" + path.name,
                                 hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def active(tmp_path, monkeypatch):
    compaction.reset_compaction_stop()
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.delenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", raising=False)
    workspace = ResearchWorkspace(tmp_path / "workspace", IDENTITY)
    archive.activate_workspace(workspace)
    yield workspace
    archive.activate_workspace(None)
    compaction.reset_compaction_stop()


def response(inputs=100, outputs=20, **extra):
    return SimpleNamespace(usage_metadata={"input_tokens": inputs, "output_tokens": outputs, **extra})


def test_reserve_before_send_settle_actual_cache_inclusive_once(active):
    before = active.snapshot()
    with admission.model_admission(MESSAGES) as ticket:
        reserved = admission.snapshot(active)
        assert reserved["limit_input_tokens"] == 4_000_000
        assert reserved["spent_input_tokens"] == 0
        assert reserved["held_input_tokens"] >= len(json.dumps(MESSAGES, ensure_ascii=False).encode())
        assert reserved["reserved_calls"] == 1
        ticket.settle(response(100, 20, input_token_details={"cache_read": 80, "cache_creation": 10}))
        ticket.settle(response(100, 20))
    final = admission.snapshot(active)
    assert final["spent_input_tokens"] == final["observed_input_tokens"] == 100
    assert final["observed_output_tokens"] == 20
    assert final["held_input_tokens"] == 0
    assert final["settled_calls"] == final["known_usage_calls"] == 1
    assert final["unknown_usage_calls"] == 0
    assert active.snapshot() == before


@pytest.mark.parametrize("wrapped", [False, True])
def test_model_response_and_ai_message_usage(active, wrapped):
    value = response(31, 7)
    if wrapped:
        value = SimpleNamespace(result=[value])
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(value)
    result = admission.snapshot(active)
    assert result["spent_input_tokens"] == 31
    assert result["observed_output_tokens"] == 7


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1, "output_tokens": 1},
    {"input_tokens": True, "output_tokens": 1}, {"input_tokens": float("nan"), "output_tokens": 1},
    {"input_tokens": float("inf"), "output_tokens": 1}, {"input_tokens": 1, "output_tokens": -1},
    {"input_tokens": 1, "output_tokens": False}, {"input_tokens": 0, "output_tokens": 0},
    {"input_tokens": "10", "output_tokens": 1}, {"input_tokens": 1.5, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 0},
    {"input_tokens": 1, "output_tokens": 1, "input_token_details": {"cache_read": -2}}])
def test_missing_or_invalid_usage_never_credits_budget(active, usage):
    with admission.model_admission(MESSAGES) as ticket:
        held = admission.snapshot(active)["held_input_tokens"]
        ticket.settle(SimpleNamespace(usage_metadata=usage))
    final = admission.snapshot(active)
    assert final["spent_input_tokens"] >= held > 0
    assert final["unknown_usage_calls"] == 1
    assert final["estimated_calls"] == 1
    assert final["coverage"] == "estimated_or_unknown"


def test_partial_usage_retains_large_observed_input(active):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(SimpleNamespace(usage_metadata={"input_tokens": 90000}))
    final = admission.snapshot(active)
    assert final["spent_input_tokens"] >= 90000
    assert final["unknown_output_calls"] == 1


def test_partial_model_response_does_not_claim_complete_output_coverage(active):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(SimpleNamespace(result=[response(50, 10), SimpleNamespace(usage_metadata=None)]))
    state = admission.snapshot(active)
    assert state["unknown_usage_calls"] == 1
    assert state["unknown_output_calls"] == 1
    assert state["coverage"] == "estimated_or_unknown"


def test_failed_response_with_partial_counters_remains_conservative(active):
    with admission.model_admission(MESSAGES) as ticket:
        held = admission.snapshot(active)["held_input_tokens"]
        ticket.settle(SimpleNamespace(status="failed", usage_metadata={"input_tokens": 1, "output_tokens": 0}))
    state = admission.snapshot(active)
    assert state["spent_input_tokens"] == held
    assert state["coverage"] == "estimated_or_unknown"
    assert state["calls"][0]["reason"] == "failed_response"


@pytest.mark.parametrize("details", [
    {"cache_read": 100000}, {"cache_creation": 100000}, {"cache_read": 1, "cache_creation": 1},
])
def test_contradictory_cache_partition_cannot_release_reservation(active, details):
    with admission.model_admission(MESSAGES) as ticket:
        held = admission.snapshot(active)["held_input_tokens"]
        ticket.settle(response(1, 1, total_tokens=2, input_token_details=details))
    state = admission.snapshot(active)
    assert state["spent_input_tokens"] >= held
    assert state["unknown_usage_calls"] == 1


def test_failed_nested_attempt_is_charged_before_retry(active):
    with admission.model_admission(MESSAGES) as outer:
        held = admission.snapshot(active)["held_input_tokens"]
        try:
            with admission.model_admission(MESSAGES) as failed:
                assert failed is outer
                raise RuntimeError("first provider send failed")
        except RuntimeError:
            pass
        after_failure = admission.snapshot(active)
        assert after_failure["spent_input_tokens"] == held
        assert after_failure["held_input_tokens"] == 0
        with admission.model_admission(MESSAGES) as retried:
            assert retried is not outer
            retried.settle(response(13, 2))
        outer.settle(response(13, 2))
    state = admission.snapshot(active)
    assert state["spent_input_tokens"] == held + 13
    assert state["settled_calls"] == 2
    assert state["estimated_calls"] == state["known_usage_calls"] == 1


@pytest.mark.parametrize("point", ["marker_synced", "database_open", "database_committed", "published"])
def test_first_initialization_crash_can_resume_without_resetting_usage(active, point):
    code = CHILD_SETUP + """
if sys.argv[5] == 'marker_synced':
    original_sync = os.fsync
    def crash_sync(fd):
        original_sync(fd)
        os._exit(76)
    os.fsync = crash_sync
elif sys.argv[5] == 'database_open':
    import sqlite3
    original_connect = sqlite3.connect
    def crash_connect(*args, **kwargs):
        original_connect(*args, **kwargs)
        os._exit(76)
    sqlite3.connect = crash_connect
elif sys.argv[5] == 'database_committed':
    original_create = a._Ledger._create
    def crash_create(self, *args, **kwargs):
        original_create(self, *args, **kwargs)
        os._exit(76)
    a._Ledger._create = crash_create
else:
    original_replace = os.replace
    def crash_replace(*args, **kwargs):
        original_replace(*args, **kwargs)
        os._exit(76)
    os.replace = crash_replace
with a.model_admission(messages):
    raise AssertionError('initialization crash must precede provider admission')
"""
    run = subprocess_call(code, active, json.dumps(MESSAGES), point)
    assert run.returncode == 76, run.stderr
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(41, 3))
    state = admission.snapshot(active)
    assert state["settled_calls"] == 1
    assert state["spent_input_tokens"] == 41


def test_lost_committed_ledger_is_never_recreated(active):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response())
    (active.root / "model_usage.sqlite3").unlink()
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            pytest.fail("committed ledger was lost, not unfinished")
    assert not (active.root / "model_usage.sqlite3").exists()


def test_legacy_committed_marker_keeps_usage_and_pending_marker_cannot_reset_it(active):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(20, 2))
    marker = active.root / ".model_usage.lock"
    header = marker.read_bytes().split(b"\n", 1)[0]
    marker.write_bytes(header)
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(30, 3))
    assert admission.snapshot(active)["spent_input_tokens"] == 50
    marker.write_bytes(header + b"\nP")
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            pytest.fail("a pending marker cannot erase committed calls")
    with sqlite3.connect(active.root / "model_usage.sqlite3") as conn:
        assert conn.execute("SELECT sum(charged_input_tokens) FROM calls").fetchone()[0] == 50


def test_overlapping_noncache_usage_details_are_not_summed(active):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(10, 1, input_token_details={"audio": 8, "text": 8, "cache_read": 5}))
    assert admission.snapshot(active)["spent_input_tokens"] == 10
    assert admission.snapshot(active)["known_usage_calls"] == 1


def test_actual_input_overrun_commits_usage_then_latches_stop(active, monkeypatch):
    limit = admission._estimate(MESSAGES, None)[1]
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(limit))
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES) as ticket:
            ticket.settle(response(limit + 100, 5))
    state = admission.snapshot(active)
    assert state["spent_input_tokens"] == limit + 100
    assert state["held_input_tokens"] == 0
    assert state["settled_calls"] == 1
    assert state["budget_denied_reason"] == "budget_denied"


def test_nested_tool_expansion_cannot_bypass_reserved_allowance(active, monkeypatch):
    limit = admission._estimate(MESSAGES, None)[1]
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(limit))
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            with admission.model_admission(MESSAGES, [{"description": "schema " * 100}]):
                pytest.fail("expanded native schema cannot send without allowance")
    state = admission.snapshot(active)
    assert state["spent_input_tokens"] == limit
    assert state["held_input_tokens"] == 0
    assert state["budget_denied_reason"] == "budget_denied"


@pytest.mark.parametrize("exit_kind", ["exception", "unsettled", "cancelled"])
def test_every_finally_settles_conservatively(active, exit_kind):
    try:
        with admission.model_admission(MESSAGES):
            held = admission.snapshot(active)["held_input_tokens"]
            if exit_kind == "exception":
                raise RuntimeError("fixture failure")
            if exit_kind == "cancelled":
                raise asyncio.CancelledError()
    except (RuntimeError, asyncio.CancelledError):
        pass
    final = admission.snapshot(active)
    assert final["spent_input_tokens"] == held
    assert final["held_input_tokens"] == 0
    assert final["unknown_usage_calls"] == 1


def test_nested_wrappers_share_one_ticket_and_extend_tool_reservation(active):
    tools = [{"type": "function", "function": {"name": "query", "description": "schema " * 500}}]
    with admission.model_admission(MESSAGES) as outer:
        initial = admission.snapshot(active)["held_input_tokens"]
        with admission.model_admission(MESSAGES, tools) as inner:
            assert inner is outer
            assert admission.snapshot(active)["held_input_tokens"] > initial
            inner.settle(SimpleNamespace(result=[response(211, 19)]))
        outer.settle(response(211, 19))
    final = admission.snapshot(active)
    assert final["settled_calls"] == 1
    assert final["spent_input_tokens"] == 211


def test_async_context_inherits_nested_ticket_but_peer_calls_are_independent(active):
    async def nested(ticket):
        with admission.model_admission(MESSAGES) as same:
            assert same is ticket
            same.settle(response(12, 3))
    async def outer():
        with admission.model_admission(MESSAGES) as ticket:
            await asyncio.create_task(nested(ticket))
    asyncio.run(outer())

    async def peer():
        with admission.model_admission(MESSAGES) as ticket:
            await asyncio.sleep(0)
            ticket.settle(response(20, 1))
    async def peers():
        await asyncio.gather(*(peer() for _ in range(5)))
    asyncio.run(peers())
    final = admission.snapshot(active)
    assert final["settled_calls"] == 6
    assert final["spent_input_tokens"] == 112


def test_budget_exhaustion_is_persisted_and_latched_before_send(active, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "1")
    with pytest.raises(compaction.ResearchCompactionError) as caught:
        with admission.model_admission(MESSAGES):
            pytest.fail("denied call cannot reach provider")
    assert caught.value.reason == "checkpoint_unavailable"
    assert compaction.get_compaction_stop() is not None
    state = admission.snapshot(active)
    assert state["budget_denied_reason"] == "budget_denied"
    assert state["held_input_tokens"] == state["spent_input_tokens"] == 0
    compaction.reset_compaction_stop()
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            pytest.fail("restart cannot clear durable denial")


def test_thread_race_cannot_reuse_last_planned_allowance(active, monkeypatch):
    estimate = admission._estimate(MESSAGES, None)[1]
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(estimate))
    barrier = threading.Barrier(5)
    release = threading.Event()
    entered = []

    def call(i):
        barrier.wait(timeout=5)
        try:
            with admission.model_admission(MESSAGES):
                entered.append(i)
                assert release.wait(timeout=5)
            return "admitted"
        except compaction.ResearchCompactionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(call, i) for i in range(5)]
        try:
            # At least one denial must settle before the admitted call releases.
            from concurrent.futures import wait, FIRST_COMPLETED
            done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
            assert done and any(f.result() == "denied" for f in done)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(entered) == results.count("admitted") == 1
    assert admission.snapshot(active)["spent_input_tokens"] == estimate


def subprocess_call(code, workspace, *args):
    return subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,sys.argv[1]);\n" + code,
                           str(BRIDGE), str(workspace.root), json.dumps(IDENTITY), *map(str, args)],
                          text=True, capture_output=True, timeout=20)


CHILD_SETUP = """
import json, os
from pathlib import Path
from types import SimpleNamespace
import research_admission as a
import research_archive as archive
from research_workspace import ResearchWorkspace
from research_compaction import ResearchCompactionError
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
archive.activate_workspace(w)
messages = json.loads(sys.argv[4])
"""


def test_process_race_and_hard_exit_keep_reservations_on_restart(active, monkeypatch):
    estimate = admission._estimate(MESSAGES, None)[1]
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(estimate))
    code = CHILD_SETUP + """
try:
    with a.model_admission(messages):
        os._exit(73)
except ResearchCompactionError:
    sys.exit(74)
"""
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: subprocess_call(code, active, json.dumps(MESSAGES)), range(5)))
    assert [p.returncode for p in results].count(73) == 1, [p.stderr for p in results]
    assert [p.returncode for p in results].count(74) == 4
    state = admission.snapshot(active)
    assert state["held_input_tokens"] == estimate
    assert state["reserved_calls"] == 1
    assert state["remaining_input_tokens"] == 0


def test_settled_usage_survives_restart_and_budget_cannot_reset(active, monkeypatch):
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(451, 27))
    code = CHILD_SETUP + """
state = a.snapshot(w)
assert state['spent_input_tokens'] == 451
assert state['observed_output_tokens'] == 27
with a.model_admission(messages) as ticket:
    ticket.settle(SimpleNamespace(usage_metadata={'input_tokens': 49, 'output_tokens': 3}))
"""
    run = subprocess_call(code, active, json.dumps(MESSAGES))
    assert run.returncode == 0, run.stderr
    assert admission.snapshot(active)["spent_input_tokens"] == 500
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "0")
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            pytest.fail("configuration change cannot reset spent allowance")


def test_unlimited_is_explicit_but_still_accounted(active, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "0")
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response(9_000_000, 3))
    state = admission.snapshot(active)
    assert state["limit_input_tokens"] == 0
    assert state["remaining_input_tokens"] is None
    assert state["spent_input_tokens"] == 9_000_000


@pytest.mark.parametrize("engine,activate", [("legacy", True), ("linear-v2", True), ("", True), ("agentic", False)])
def test_disabled_admission_is_noop_even_for_nonserializable_inputs(active, monkeypatch, engine, activate):
    monkeypatch.setenv("RESEARCH_ENGINE", engine)
    monkeypatch.setenv("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", "invalid")
    if not activate:
        archive.activate_workspace(None)
    with admission.model_admission(object(), tools=object()) as ticket:
        ticket.settle(object())
    assert not (active.root / "model_usage.sqlite3").exists()


def test_base_tool_schema_is_fully_estimated_and_only_hashes_are_stored(active):
    class Schema:
        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"query": {"type": "string", "description": "secret-schema " * 200}}}
    class Tool:
        name = "secret-tool"
        description = "secret-description"
        args_schema = Schema
    small = admission._estimate(MESSAGES, None)[1]
    with admission.model_admission(MESSAGES, [Tool()]):
        assert admission.snapshot(active)["held_input_tokens"] > small + 2500
    raw = (active.root / "model_usage.sqlite3").read_bytes()
    assert b"private prompt" not in raw
    assert b"secret-schema" not in raw
    assert b"secret-tool" not in raw
    assert b"secret-description" not in raw


def test_corrupt_ledger_is_not_reset(active):
    with admission.model_admission(MESSAGES):
        pass
    db = active.root / "model_usage.sqlite3"
    db.write_bytes(b"corrupt ledger")
    with pytest.raises(compaction.ResearchCompactionError):
        with admission.model_admission(MESSAGES):
            pytest.fail("corrupt allowance must not be reset")
    assert db.read_bytes() == b"corrupt ledger"


def test_wal_full_connections_are_separate_and_closed(active, monkeypatch):
    connections, settings = [], []
    original = sqlite3.connect
    class Observed(sqlite3.Connection):
        def close(self):
            settings.append((self.execute("PRAGMA journal_mode").fetchone()[0],
                             self.execute("PRAGMA synchronous").fetchone()[0]))
            super().close()
    def traced(*args, **kwargs):
        conn = original(*args, **kwargs, factory=Observed)
        connections.append(conn)
        return conn
    monkeypatch.setattr(admission.sqlite3, "connect", traced)
    with admission.model_admission(MESSAGES) as ticket:
        ticket.settle(response())
    admission.snapshot(active)
    assert len(connections) >= 3
    assert len(settings) == len(connections)
    assert all(item == ("wal", 2) for item in settings)
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_package_and_standalone_import_share_nested_context(active):
    packaged = importlib.import_module("deerflow_bridge.research_admission")
    assert packaged is admission
    with admission.model_admission(MESSAGES) as first:
        with packaged.model_admission(MESSAGES) as second:
            assert first is second
            second.settle(response())
    assert admission.snapshot(active)["settled_calls"] == 1
