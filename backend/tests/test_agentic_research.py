"""Offline scheduler acceptance: real barriers, durable restart, and safe stops."""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from deerflow_bridge.agentic_research import AgenticResearchHalt, PHASES, run_research


QUESTION = "How will regional water limits affect chip capacity by 2030?"


@pytest.fixture(scope="module", autouse=True)
def scheduler_source_receipts(record_testsuite_property):
    bridge = Path(__file__).resolve().parents[2] / "deerflow_bridge"
    for path in (bridge / "agentic_research.py", Path(__file__),
                 bridge / "research_workspace.py", bridge / "research_context.py"):
        record_testsuite_property(
            "scheduler_source_sha256:" + path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def answer(task, *, discoveries=()):
    return {
        "text": f"Full evidence for {task['id']}: tail number 42 [S1]",
        "evidence": [f"evidence:{task['id']}", "Shared raw block [S1]"],
        "sources": [{"id": "S1", "url": "https://example.test/document"}],
        "discoveries": list(discoveries),
    }


@pytest.fixture
def workspace_factory(tmp_path):
    from deerflow_bridge.research_workspace import ResearchWorkspace

    def make():
        return ResearchWorkspace(tmp_path / "workspace", identity={
            "question": QUESTION, "model": "offline-native-fixture", "version": 1,
        })

    return make


@pytest.fixture
def policy():
    from deerflow_bridge.research_context import ContextPolicy

    return ContextPolicy()


def run(ws, policy, worker, **kwargs):
    return run_research(
        ws, question=QUESTION, depth="deep", language="en", worker=worker,
        context_policy=policy, **kwargs,
    )


@pytest.mark.parametrize("workers", [5, 9])
def test_every_phase_has_five_distinct_scoped_workers_and_peak_five(workspace_factory, policy, workers):
    ws = workspace_factory()
    gate = threading.Barrier(5, timeout=5)
    lock = threading.Lock()
    seen = []
    active = peak = 0

    def worker(task, context, on_event):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(task)
        try:
            gate.wait()
            assert QUESTION in task["goal"]
            assert isinstance(context, str)
            return answer(task)
        finally:
            with lock:
                active -= 1

    result = run(ws, policy, worker, workers=workers)
    assert active == 0 and peak == 5
    assert len(seen) == len(PHASES) * 5 == 25
    assert len({t["id"] for t in seen}) == 25
    by_phase = collections.defaultdict(list)
    for task in seen:
        by_phase[task["phase"]].append(task)
    assert len(by_phase) == 5
    for tasks in by_phase.values():
        assert {t["focus"] for t in tasks} == {
            "scope", "primary-evidence", "actors-and-incentives",
            "contradictions-and-risks", "forecast-implications",
        }
        assert len({t["goal"] for t in tasks}) == 5
    assert result["stats"]["completed_tasks"] == 25
    for task in seen:
        assert answer(task)["text"] in result["text"]
        assert f"evidence:{task['id']}" in result["evidence"]
    assert "Shared raw block [S1]" in result["evidence"]


def test_finished_run_reuses_all_tasks_and_frozen_contexts(workspace_factory, policy):
    calls = []

    def worker(task, context, on_event):
        calls.append((task["id"], context))
        return answer(task)

    first = run(workspace_factory(), policy, worker, workers=1)
    second = run(workspace_factory(), policy, worker, workers=1)
    assert len(calls) == 25
    assert second["text"] == first["text"]
    assert second["evidence"] == first["evidence"]
    assert second["evidence_refs"] == first["evidence_refs"]
    assert second["sources"] == first["sources"]
    assert second["stats"]["reused_tasks"] == 25


def test_failure_reuses_saved_tasks_and_stops_queued_work(workspace_factory, policy):
    calls = []

    def worker(task, context, on_event):
        calls.append(task["id"])
        if len(calls) == 2:
            raise RuntimeError("Bearer secret-value never persist this")
        return answer(task)

    ws = workspace_factory()
    with pytest.raises(AgenticResearchHalt) as halted:
        run(ws, policy, worker, workers=1)
    assert len(calls) == 2
    assert halted.value.workspace is ws
    assert "secret-value" not in str(halted.value)
    resumed = []

    def recover(task, context, on_event):
        resumed.append(task["id"])
        return answer(task)

    result = run(workspace_factory(), policy, recover, workers=1)
    assert calls[0] not in resumed
    assert calls[1] in resumed
    assert len(resumed) == 24
    assert result["stats"]["reused_tasks"] == 1
    assert "secret-value" not in json.dumps(ws.snapshot())


def test_discovery_is_durable_before_worker_finishes_and_is_dispatched(workspace_factory, policy):
    ws = workspace_factory()
    emitted = threading.Event()
    release = threading.Event()
    discovery = "Which water permit expires before the capacity expansion?"
    seen = []
    lock = threading.Lock()

    def worker(task, context, on_event):
        with lock:
            seen.append(task)
            first = len(seen) == 1
        if first:
            on_event("discovery", {"question": discovery, "reason": "Permit timing gap"})
            emitted.set()
            assert release.wait(5)
        return answer(task)

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, ws, policy, worker)
        try:
            assert emitted.wait(5)
            assert discovery in json.dumps(workspace_factory().discoveries())
            assert not future.done()
        finally:
            release.set()
        result = future.result(timeout=10)
    followups = [t for t in seen if t["question"] == discovery]
    assert len(followups) == 5
    assert len({t["id"] for t in seen}) == len(seen)
    assert result["stats"]["followup_questions"] == 1


def test_deadline_returns_without_waiting_for_stuck_callbacks(workspace_factory, policy):
    release = threading.Event()
    entered = threading.Event()

    def worker(task, context, on_event):
        entered.set()
        release.wait(5)
        return answer(task)

    started = time.monotonic()
    try:
        with pytest.raises(AgenticResearchHalt) as caught:
            run(workspace_factory(), policy, worker, phase_deadline_s=0.15)
        assert entered.is_set()
        assert time.monotonic() - started < 1.5
        assert caught.value.reason == "phase_deadline"
    finally:
        release.set()


def test_changed_question_is_rejected_before_callback(workspace_factory, policy):
    run(workspace_factory(), policy, lambda t, c, e: answer(t))
    calls = []
    with pytest.raises(AgenticResearchHalt):
        run_research(
            workspace_factory(), question="Changed question", depth="deep", language="en",
            worker=lambda *args: calls.append(args), context_policy=policy,
        )
    assert calls == []


def test_policy_change_is_rejected_before_callback(workspace_factory, policy):
    ws = workspace_factory()
    run(ws, policy, lambda t, c, e: answer(t))
    calls = []
    changed = replace(policy, working_tokens=policy.working_tokens - 1)
    with pytest.raises(AgenticResearchHalt):
        run(ws, changed, lambda *args: calls.append(args))
    assert calls == []


def test_tampered_later_output_fails_before_any_native_callback(workspace_factory, policy):
    ws = workspace_factory()
    result = run(ws, policy, lambda t, c, e: answer(t))
    victim = result["evidence_refs"][-1]
    (ws.root / victim["path"]).write_text("Damaged archived source", encoding="utf-8")
    calls = []
    with pytest.raises(AgenticResearchHalt):
        run(ws, policy, lambda *args: calls.append(args))
    assert calls == []


def test_partial_phase_restart_keeps_identical_context_after_later_outputs(workspace_factory, policy):
    ws = workspace_factory()
    before = {}
    failed_id = None

    def worker(task, context, on_event):
        nonlocal failed_id
        before[task["id"]] = context
        if len(before) == 9:
            failed_id = task["id"]
            raise RuntimeError("interrupt the second phase")
        return answer(task)

    with pytest.raises(AgenticResearchHalt):
        run(ws, policy, worker, workers=1)
    after = {}

    def resume(task, context, on_event):
        after[task["id"]] = context
        return answer(task)

    result = run(workspace_factory(), policy, resume, workers=1)
    assert after[failed_id] == before[failed_id]
    assert set(before) & set(after) == {failed_id}
    assert result["stats"]["reused_tasks"] == 8


def test_process_crash_reuses_saved_task_and_midturn_discovery(workspace_factory, policy):
    ws = workspace_factory()
    script = """
import json, os, sys
from pathlib import Path
from deerflow_bridge.agentic_research import run_research
from deerflow_bridge.research_workspace import ResearchWorkspace
from deerflow_bridge.research_context import ContextPolicy
ws = ResearchWorkspace(Path(sys.argv[1]), json.loads(sys.argv[2]))
calls = []
def worker(task, context, on_event):
    calls.append(task['id'])
    if len(calls) == 2:
        on_event('discovery', {'question': 'Discovery committed before process crash'})
        os._exit(74)
    return {'text': 'Durable precrash full text', 'evidence': ['precrash block'],
            'sources': [], 'discoveries': []}
run_research(ws, question=sys.argv[3], depth='deep', language='en', worker=worker,
             context_policy=ContextPolicy(), workers=1)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(ws.root), json.dumps(ws.identity), QUESTION],
        capture_output=True, timeout=10,
    )
    assert crashed.returncode == 74
    persisted = workspace_factory().snapshot()
    completed = [t["task_id"] for t in persisted["tasks"]
                 if t["task_id"].startswith("ar-") and t["status"] == "done"]
    assert len(completed) == 1
    assert len(persisted["discoveries"]) == 1
    calls = []

    def worker(task, context, on_event):
        calls.append(task["id"])
        return answer(task)

    result = run(workspace_factory(), policy, worker)
    assert completed[0] not in calls
    assert len(calls) == 29
    assert result["stats"]["reused_tasks"] == 1
    assert result["stats"]["followup_questions"] == 1
    assert "Durable precrash full text" in result["text"]
    assert "precrash block" in result["evidence"]


def test_discovery_survives_failed_turn_and_restart_without_repeated_admission(workspace_factory, policy):
    ws = workspace_factory()
    discovery = "When does the regional water permit expire?"

    def crash(task, context, on_event):
        on_event("discovery", {"question": discovery})
        raise RuntimeError("lost response after durable discovery")

    with pytest.raises(AgenticResearchHalt):
        run(ws, policy, crash, workers=1)
    persisted = workspace_factory().discoveries()
    assert [d["question"] for d in persisted] == [discovery]
    called = []

    def recover(task, context, on_event):
        called.append(task)
        return answer(task, discoveries=[discovery] if task["question"] == QUESTION else [])

    first = run(workspace_factory(), policy, recover, workers=1)
    second = run(workspace_factory(), policy, recover, workers=1)
    assert len(called) == 30
    assert len([t for t in called if t["question"] == discovery]) == 5
    assert first["discoveries"] == second["discoveries"]
    assert len(second["discoveries"]) == 1
    assert second["stats"]["reused_tasks"] == 30


def test_adaptive_rounds_have_stable_ids_and_a_durable_total_cap(workspace_factory, policy):
    called = []

    def worker(task, context, on_event):
        called.append(task)
        if task["focus"] == "scope":
            level = 0 if task["question"] == QUESTION else int(task["question"].split()[-1])
            return answer(task, discoveries=[f"Permit investigation level {level + 1}"])
        return answer(task)

    result = run(workspace_factory(), policy, worker, max_followups=2, max_discovery_rounds=3)
    assert len(called) == 35
    assert result["stats"]["followup_questions"] == 2
    assert result["stats"]["discovery_rounds"] == 2
    assert len(result["stats"]["deferred_discoveries"]) == 1
    assert len({t["id"] for t in called}) == len(called)
    rerun = run(workspace_factory(), policy, worker, max_followups=2, max_discovery_rounds=3)
    assert len(called) == 35
    assert rerun["stats"]["reused_tasks"] == 35


def test_discovery_round_limit_leaves_explicit_pending_questions(workspace_factory, policy):
    def worker(task, context, on_event):
        proposal = "Initial follow-up" if task["question"] == QUESTION else "Second follow-up"
        return answer(task, discoveries=[proposal])

    result = run(workspace_factory(), policy, worker, max_followups=5, max_discovery_rounds=1)
    assert result["stats"]["completed_tasks"] == 30
    assert result["stats"]["followup_questions"] == 1
    assert result["stats"]["discovery_rounds"] == 1
    assert len(result["stats"]["deferred_discoveries"]) == 1


def test_timeout_fences_late_callbacks_and_holds_os_lease_until_drained(workspace_factory, policy):
    ws = workspace_factory()
    release = threading.Event()
    finished = threading.Event()
    late_rejected = threading.Event()

    def stuck(task, context, on_event):
        try:
            assert release.wait(5)
            try:
                on_event("discovery", {"question": "Abandoned late discovery"})
            except AgenticResearchHalt:
                late_rejected.set()
            return answer(task)
        finally:
            finished.set()

    try:
        with pytest.raises(AgenticResearchHalt):
            run(ws, policy, stuck, workers=1, phase_deadline_s=0.15)
        called = []
        with pytest.raises(AgenticResearchHalt):
            run(workspace_factory(), policy, lambda *args: called.append(args))
        assert called == []
        # A fresh process must see the retained OS lease, independently of the
        # workspace's same-process ownership registry.
        check_lock = subprocess.run([
            sys.executable, "-c",
            "import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDWR)\n"
            "try:\n fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n sys.exit(0)\nsys.exit(7)",
            str(ws.root / ".execution.lock"),
        ], capture_output=True, timeout=5)
        assert check_lock.returncode == 0
    finally:
        release.set()
        assert finished.wait(5)
    assert late_rejected.is_set()
    # Future completion follows the callback's final line. Retry only lease
    # acquisition, never run another worker until the old lease is closed.
    from deerflow_bridge.research_workspace import ResearchWorkspaceError

    deadline = time.monotonic() + 5
    while True:
        try:
            with ws.execution_lock():
                break
        except ResearchWorkspaceError:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert ws.discoveries() == []
    assert not any(t["status"] == "done" and t["task_id"].startswith("ar-") for t in ws.snapshot()["tasks"])
    result = run(workspace_factory(), policy, lambda t, c, e: answer(t))
    assert result["stats"]["executed_tasks"] == 25


def test_event_control_failure_cannot_be_swallowed_by_native_worker(workspace_factory, policy):
    ws = workspace_factory()
    calls = []

    def emit(kind, payload):
        if kind == "tool_progress":
            raise RuntimeError("Authorization: Bearer secret-value")

    def worker(task, context, on_event):
        calls.append(task)
        try:
            on_event("tool_progress", {"count": 1})
        except Exception:
            pass  # Native broad salvage must not reopen coordinator admission.
        return answer(task)

    with pytest.raises(AgenticResearchHalt):
        run(ws, policy, worker, emit=emit, workers=1)
    assert len(calls) == 1
    snapshot = ws.snapshot()
    assert "secret-value" not in json.dumps(snapshot)
    assert not any(t["task_id"].startswith("ar-") and t["status"] == "done" for t in snapshot["tasks"])


def test_native_compaction_halt_stops_queued_siblings_without_waiting(workspace_factory, policy):
    from deerflow_bridge.research_compaction import ResearchCompactionError

    ws = workspace_factory()
    gate = threading.Barrier(2, timeout=5)
    release = threading.Event()
    exited = threading.Event()
    called = []
    lock = threading.Lock()

    def worker(task, context, on_event):
        with lock:
            called.append(task["id"])
            index = len(called)
        on_event("tool_progress", {"observed": index})
        gate.wait()
        if index == 1:
            raise ResearchCompactionError("archive_write_failed")
        try:
            assert release.wait(5)
            return answer(task)
        finally:
            exited.set()

    started = time.monotonic()
    try:
        with pytest.raises(AgenticResearchHalt):
            run(ws, policy, worker, workers=2)
        assert time.monotonic() - started < 2
        assert len(called) == 2  # Three queued siblings never enter native code.
        assert all(any(e["kind"] == "tool_progress" for e in ws.events(task_id)) for task_id in called)
        assert not any(e["kind"] == "research_complete" for e in ws.events("agentic-run"))
    finally:
        release.set()
        assert exited.wait(5)


def test_completed_task_callbacks_are_closed_during_later_phases_and_after_success(workspace_factory, policy):
    ws = workspace_factory()
    callbacks = []

    def worker(task, context, on_event):
        if task["phase"] != PHASES[0]:
            with pytest.raises(AgenticResearchHalt):
                callbacks[0]("discovery", {"question": "Arrived after originating task closed"})
        callbacks.append(on_event)
        return answer(task)

    run(ws, policy, worker, workers=1)
    before = ws.snapshot()
    with pytest.raises(AgenticResearchHalt):
        callbacks[-1]("discovery", {"question": "Arrived after successful return"})
    assert ws.snapshot() == before


def test_failure_closes_admission_without_waiting_for_persistence_mutex(workspace_factory, policy, monkeypatch):
    ws = workspace_factory()
    gate = threading.Barrier(2, timeout=5)
    persisting = threading.Event()
    release = threading.Event()
    called = []
    lock = threading.Lock()
    put = ws.put_artifact

    def blocked_put(text, kind):
        if kind == "worker_result":
            persisting.set()
            assert release.wait(5)
        return put(text, kind)

    monkeypatch.setattr(ws, "put_artifact", blocked_put)

    def worker(task, context, on_event):
        with lock:
            called.append(task["id"])
            index = len(called)
        gate.wait()
        if index == 2:
            assert persisting.wait(5)
            raise RuntimeError("Sibling fails while persistence mutex is held")
        return answer(task)

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, ws, policy, worker, workers=2)
        try:
            assert persisting.wait(5)
            with pytest.raises(AgenticResearchHalt):
                future.result(timeout=1)
            assert len(called) == 2
        finally:
            release.set()


def test_lease_contender_cannot_mutate_owner_diagnostics(workspace_factory, policy):
    ws = workspace_factory()
    with ws.execution_lock():
        before = ws.snapshot()
        with pytest.raises(AgenticResearchHalt):
            run(ws, policy, lambda t, c, e: answer(t))
        assert ws.snapshot() == before


def test_persisted_event_diagnostics_redact_exception_text(workspace_factory, policy):
    ws = workspace_factory()

    def worker(task, context, on_event):
        on_event("tool_error", {"message": "Bearer secret-value"})
        on_event("tool_progress", {"nested": {"exception": ValueError("Bearer secret-value")}})
        return answer(task)

    run(ws, policy, worker)
    assert "secret-value" not in json.dumps(ws.snapshot())


def test_task_done_only_follows_durable_result_and_failed_save_stops_queue(workspace_factory, policy, monkeypatch):
    ws = workspace_factory()
    original = ws.save_task
    called = []

    def save(task_id, inputs, result):
        if task_id.startswith("ar-") and len(called) == 2:
            raise OSError("private path secret-value")
        return original(task_id, inputs, result)

    monkeypatch.setattr(ws, "save_task", save)

    def worker(task, context, on_event):
        called.append(task["id"])
        return answer(task)

    with pytest.raises(AgenticResearchHalt):
        run(ws, policy, worker, workers=1)
    assert len(called) == 2
    assert "task_done" in [e["kind"] for e in ws.events(called[0])]
    assert "task_done" not in [e["kind"] for e in ws.events(called[1])]
    assert "secret-value" not in json.dumps(ws.snapshot())


def test_selected_ranges_address_exact_archived_blocks_and_sources_are_unchanged(workspace_factory, policy):
    ws = workspace_factory()
    source = {"id": "S1", "url": "https://example.test/source", "source_origin": "native_observed",
              "receipt": {"observed": True, "raw": "Original\r\nsource"}}
    long_text = "Unrelated context. " * 6000 + "\n\nContradiction: permit expires 2029; capacity 42 units.\r\n"
    small = replace(policy, working_tokens=12000)

    def worker(task, context, on_event):
        assert len(context.encode("utf-8")) <= small.working_tokens
        result = answer(task)
        result["evidence"] = [long_text]
        result["sources"] = [source]
        return result

    result = run(ws, small, worker)
    assert result["sources"] == [source] * 25
    assert result["evidence"] == [long_text] * 25
    snapshots = [t["result"] for t in ws.snapshot()["tasks"] if t["task_id"].startswith("agentic-plan-")]
    selections = 0
    for snapshot in snapshots:
        for item in snapshot["tasks"]:
            by_id = {ref["id"]: ref for ref in item["task"]["context_refs"]}
            for selected in item["selection"]["selected_refs"]:
                original = ws.read_artifact(by_id[selected["ref"]])
                assert original[selected["start"]:selected["end"]] in item["selection"]["text"]
                selections += 1
    assert selections > 0
    # Long tail is independently retrievable from the full real artifact.
    assert any(ws.read_artifact(ref) == long_text for ref in result["evidence_refs"])


@pytest.mark.parametrize("kwargs", [
    {"workers": 0}, {"workers": True}, {"phase_deadline_s": 0},
    {"phase_deadline_s": float("nan")}, {"phase_deadline_s": float("inf")},
    {"max_followups": -1}, {"max_discovery_rounds": True},
])
def test_invalid_request_never_calls_worker(workspace_factory, policy, kwargs):
    calls = []
    with pytest.raises(AgenticResearchHalt) as caught:
        run(workspace_factory(), policy, lambda *args: calls.append(args), **kwargs)
    assert caught.value.reason == "invalid_request"
    assert calls == []
