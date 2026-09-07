"""Offline regressions for durable launch admission and ambiguous dispatch."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import Config
from app.services import launch_intents as li
from app.services import pipeline_orchestrator as po


KEY = "intent-20260908-00000001"
REQUEST = {
    "prompt": "Forecast the sample industry.", "mode": "full", "project_name": None,
    "depth": None, "max_rounds": None, "language": None, "model": None,
}


@pytest.fixture
def launches(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(po.PipelineOrchestrator, "_threads", {})
    monkeypatch.setattr(po.PipelineOrchestrator, "_cancel_events", {})
    started = []

    def fake_run(cls, state):
        started.append(state.pipeline_id)

    monkeypatch.setattr(po.PipelineOrchestrator, "_run", classmethod(fake_run))
    yield started
    for thread in po.PipelineOrchestrator._threads.values():
        thread.join(timeout=5)


def _start(key=KEY, request=None, **kwargs):
    return po.PipelineOrchestrator.start_idempotent(
        key, request or REQUEST, prompt=REQUEST["prompt"], **kwargs,
    )


def _join():
    for thread in po.PipelineOrchestrator._threads.values():
        thread.join(timeout=5)


@pytest.mark.parametrize("key", [None, "", "short", "x" * 129, "../" * 10, " a" * 20, 123])
def test_invalid_keys_rejected(key):
    with pytest.raises(ValueError):
        li.validate_intent_key(key)


@pytest.mark.parametrize("key", [KEY, "3f43cba8-c141-49d8-ae52-b23b98555817", "_" * 16, "A" * 128])
def test_valid_keys_preserved(key):
    assert li.validate_intent_key(key) == key


def test_identical_retry_has_one_dispatch_and_one_task(launches, monkeypatch):
    created_tasks = []
    original = po.TaskManager.create_task

    def create_task(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        created_tasks.append(result)
        return result

    monkeypatch.setattr(po.TaskManager, "create_task", create_task)
    first = _start()
    second = _start()
    _join()
    assert first["pipeline_id"] == second["pipeline_id"]
    assert first["task_id"] == second["task_id"] == created_tasks[0]
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["launch_status"] == second["launch_status"] == "dispatched"
    assert first["recovery_required"] is False
    assert launches == [first["pipeline_id"]]
    assert len(created_tasks) == 1


def test_new_key_intentionally_repeats(launches):
    first = _start()
    second = _start(key=KEY + "-repeat")
    _join()
    assert first["pipeline_id"] != second["pipeline_id"]
    assert len(launches) == 2


@pytest.mark.parametrize("field,value", [
    ("prompt", "Changed question"), ("mode", "research_only"), ("project_name", "Changed"),
    ("depth", "deep"), ("max_rounds", 10), ("language", ""), ("model", "other-model"),
])
def test_changed_request_conflicts(launches, field, value):
    first = _start()
    changed = {**REQUEST, field: value}
    with pytest.raises(li.LaunchIntentConflict) as caught:
        _start(request=changed)
    assert caught.value.pipeline_id == first["pipeline_id"]
    _join()
    assert len(launches) == 1


def test_canonical_object_key_order_does_not_conflict(launches):
    first = _start()
    second = _start(request=dict(reversed(list(REQUEST.items()))))
    assert second["pipeline_id"] == first["pipeline_id"]


def test_concurrent_threads_dispatch_once(launches):
    barrier = threading.Barrier(8)

    def run(_):
        barrier.wait(timeout=10)
        return _start()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(8)))
    _join()
    assert len({result["pipeline_id"] for result in results}) == 1
    assert sum(not result["replayed"] for result in results) == 1
    assert len(launches) == 1


def _process_start(root, marker, event, queue):
    """Spawn a fresh process; only a harmless file append substitutes for _run."""
    import os
    os.environ["DRF_TEST_PROCESS"] = "1"
    Config.PIPELINE_DATA_DIR = root

    def fake_run(cls, state):
        with open(marker, "a", encoding="utf-8") as handle:
            handle.write(state.pipeline_id + "\n")

    po.PipelineOrchestrator._run = classmethod(fake_run)
    event.wait(timeout=20)
    try:
        result = _start()
        _join()
        queue.put(result)
    except Exception as exc:
        queue.put({"exception": type(exc).__name__})


def test_separate_processes_share_one_pipeline_and_dispatch(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    event, queue = ctx.Event(), ctx.Queue()
    marker = tmp_path / "dispatches.txt"
    root = str(tmp_path / "pipelines")
    processes = [ctx.Process(target=_process_start, args=(root, str(marker), event, queue)) for _ in range(3)]
    for process in processes:
        process.start()
    event.set()
    try:
        results = [queue.get(timeout=45) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert not any("exception" in result for result in results), results
        ids = {result["pipeline_id"] for result in results}
        assert len(ids) == 1
        assert marker.read_text().splitlines() == list(ids)
        assert len(list((tmp_path / "pipelines").glob("pipe_*/pipeline_state.json"))) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()


def test_admission_storage_failure_dispatches_nothing(launches, monkeypatch):
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("offline storage fixture")

    monkeypatch.setattr(li.sqlite3, "connect", unavailable)
    with pytest.raises(li.LaunchIntentStorageError):
        _start()
    assert launches == []


class SimulatedCrash(BaseException):
    pass


def test_crash_after_admission_retains_identity_and_snapshot(launches, monkeypatch):
    def crash(cls, state):
        raise SimulatedCrash()

    with monkeypatch.context() as scope:
        scope.setattr(po.PipelineManager, "save", classmethod(crash))
        with pytest.raises(SimulatedCrash):
            _start()
    first_lookup = po.PipelineOrchestrator.lookup_launch_intent(KEY, REQUEST)
    replay = _start()
    assert first_lookup["pipeline_id"] == replay["pipeline_id"]
    assert replay["launch_status"] == "unavailable"
    assert replay["recovery_required"] is True
    assert launches == []
    record = li.LaunchIntentStore(Config.PIPELINE_DATA_DIR).lookup(KEY, REQUEST)
    assert record["initial_state"]["prompt"] == REQUEST["prompt"]
    assert record["initial_state"]["options"]["safety_policy_v1"]["origin"] == "admission"
    assert record["initial_state"]["task_id"] is None


def test_crash_after_state_before_dispatch_never_restarts(launches, monkeypatch):
    original = li.LaunchIntentStore.transition

    def crash(self, key, state, **kwargs):
        if state == "dispatching":
            raise SimulatedCrash()
        return original(self, key, state, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(li.LaunchIntentStore, "transition", crash)
        with pytest.raises(SimulatedCrash):
            _start()
    replay = _start()
    assert replay["replayed"] is True
    assert replay["recovery_required"] is True
    state = po.PipelineManager.load(replay["pipeline_id"])
    assert state["owner_pid"] is not None
    assert state["owner_boot_id"] == po._BOOT_ID
    assert state["heartbeat_at"] is not None
    assert launches == []


def test_thread_start_failure_is_durable_and_does_not_retry(launches, monkeypatch):
    def fail_start(self):
        raise RuntimeError("offline thread-start failure")

    with monkeypatch.context() as scope:
        scope.setattr(po.threading.Thread, "start", fail_start)
        failed = _start()
    replay = _start()
    assert failed["pipeline_id"] == replay["pipeline_id"]
    assert replay["launch_status"] == "failed"
    assert replay["status"] == "failed"
    assert replay["recovery_required"] is True
    assert launches == []
    assert replay["pipeline_id"] not in po.PipelineOrchestrator._threads


@pytest.mark.parametrize("recovered_status", ["running", "completed"])
def test_explicit_same_id_resume_preserves_original_dispatch_history(launches, monkeypatch, recovered_status):
    def fail_start(self):
        raise RuntimeError("offline thread-start failure")

    with monkeypatch.context() as scope:
        scope.setattr(po.threading.Thread, "start", fail_start)
        failed = _start()

    def recovered_run(cls, state):
        launches.append(state.pipeline_id)
        state.status = recovered_status
        po.PipelineManager.save(state)

    monkeypatch.setattr(po.PipelineOrchestrator, "_run", classmethod(recovered_run))
    po.PipelineOrchestrator.resume(failed["pipeline_id"])
    _join()
    replay = _start()
    assert replay["pipeline_id"] == failed["pipeline_id"]
    assert replay["status"] == recovered_status
    assert replay["launch_status"] == "failed"  # Original admission history is retained.
    assert replay["recovery_required"] is True  # Historical dispatch ambiguity is not rewritten.
    assert len(launches) == 1


def test_thread_start_ambiguous_acknowledgement_retains_worker(launches, monkeypatch):
    original_start = po.threading.Thread.start

    def ambiguous_start(self):
        original_start(self)
        raise RuntimeError("offline acknowledgement lost after thread start")

    with monkeypatch.context() as scope:
        scope.setattr(po.threading.Thread, "start", ambiguous_start)
        first = _start()
    _join()
    replay = _start()
    assert first["pipeline_id"] == replay["pipeline_id"]
    assert replay["status"] == "running"
    assert replay["launch_status"] == "dispatching"
    assert replay["recovery_required"] is True
    assert replay["pipeline_id"] in po.PipelineOrchestrator._threads
    assert len(launches) == 1


def test_dispatch_acknowledgement_storage_failure_does_not_fail_live_worker(launches, monkeypatch):
    original = li.LaunchIntentStore.transition

    def fail_ack(self, key, state, **kwargs):
        if state == "dispatched":
            raise li.LaunchIntentStorageError()
        return original(self, key, state, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(li.LaunchIntentStore, "transition", fail_ack)
        first = _start()
    _join()
    replay = _start()
    assert replay["pipeline_id"] == first["pipeline_id"]
    assert replay["launch_status"] == "dispatching"
    assert replay["status"] == "running"
    assert len(launches) == 1


def test_dispatch_admission_storage_failure_starts_nothing(launches, monkeypatch):
    original = li.LaunchIntentStore.transition

    def fail_dispatch(self, key, state, **kwargs):
        if state == "dispatching":
            raise li.LaunchIntentStorageError()
        return original(self, key, state, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(li.LaunchIntentStore, "transition", fail_dispatch)
        first = _start()
    replay = _start()
    assert replay["pipeline_id"] == first["pipeline_id"]
    assert replay["launch_status"] == "failed"
    assert replay["status"] == "failed"
    assert len(launches) == 0


def test_persistently_empty_ledger_fails_closed(launches):
    from pathlib import Path
    path = Path(Config.PIPELINE_DATA_DIR) / "launch_intents.sqlite3"
    path.parent.mkdir()
    path.touch()
    with pytest.raises(li.LaunchIntentStorageError):
        po.PipelineOrchestrator.lookup_launch_intent(KEY)
    assert launches == []


@pytest.mark.parametrize("damage", ["delete", "corrupt", "incompatible"])
def test_state_loss_never_turns_replay_into_new_launch(launches, damage):
    first = _start()
    _join()
    path = po.PipelineManager.state_path(first["pipeline_id"])
    if damage == "delete":
        po.PipelineManager.delete(first["pipeline_id"])
    elif damage == "corrupt":
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not-json")
    else:
        state = po.PipelineManager.load(first["pipeline_id"])
        state["schema_version"] = po.PIPELINE_SCHEMA_VERSION + 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
    replay = _start()
    assert replay["pipeline_id"] == first["pipeline_id"]
    assert replay["launch_status"] == "unavailable"
    assert replay["recovery_required"] is True
    assert len(launches) == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_replay_uses_persisted_current_status_without_task_registry(launches, monkeypatch, status):
    first = _start()
    _join()
    state = po.PipelineState.from_dict(po.PipelineManager.load(first["pipeline_id"]))
    state.status = status
    po.PipelineManager.save(state)
    monkeypatch.setattr(po.TaskManager(), "_tasks", {})
    replay = _start()
    assert replay["status"] == status
    assert replay["task_id"] == first["task_id"]
    assert len(launches) == 1


def test_retry_preserves_admission_policy_across_config_change(launches, monkeypatch):
    monkeypatch.setattr(Config, "DEERFLOW_RESEARCH_DEPTH", "standard")
    first = _start()
    state_before = po.PipelineManager.load(first["pipeline_id"])
    monkeypatch.setattr(Config, "DEERFLOW_RESEARCH_DEPTH", "deep")
    monkeypatch.setattr(Config, "DEERFLOW_DUAL_TRACK", False)
    second = _start()
    state_after = po.PipelineManager.load(second["pipeline_id"])
    assert state_before == state_after


def test_unknown_intent_lookup_is_read_only(launches):
    from pathlib import Path
    assert po.PipelineOrchestrator.lookup_launch_intent(KEY, REQUEST) is None
    assert not Path(Config.PIPELINE_DATA_DIR).exists()


def test_store_never_persists_raw_intent_key(launches):
    from pathlib import Path
    _start()
    for path in Path(Config.PIPELINE_DATA_DIR).glob("launch_intents.sqlite3*"):
        assert KEY.encode() not in path.read_bytes()


def test_abandon_unadmitted_key_creates_only_durable_tombstone(launches):
    from pathlib import Path
    first = po.PipelineOrchestrator.abandon_launch_intent(KEY)
    repeated = po.PipelineOrchestrator.abandon_launch_intent(KEY)
    assert first == {
        "pipeline_id": None, "task_id": None, "mode": None, "status": "cancelled",
        "launch_status": "abandoned", "replayed": False, "recovery_required": False,
    }
    assert repeated == {**first, "replayed": True}
    assert list(Path(Config.PIPELINE_DATA_DIR).glob("pipe_*")) == []
    assert launches == []


def test_late_requests_cannot_dispatch_an_abandoned_intent(launches):
    po.PipelineOrchestrator.abandon_launch_intent(KEY)
    for request in [REQUEST, {**REQUEST, "prompt": "A corrected question"}]:
        replay = _start(request=request)
        assert replay["launch_status"] == "abandoned"
        assert replay["pipeline_id"] is None
        assert po.PipelineOrchestrator.lookup_launch_intent(KEY, request)["launch_status"] == "abandoned"
    assert launches == []


def test_abandon_admitted_key_returns_existing_work_without_cancelling(launches):
    first = _start()
    _join()
    before = po.PipelineManager.load(first["pipeline_id"])
    abandoned = po.PipelineOrchestrator.abandon_launch_intent(KEY)
    assert abandoned["pipeline_id"] == first["pipeline_id"]
    assert abandoned["launch_status"] == "dispatched"
    assert abandoned["status"] == "running"
    assert before == po.PipelineManager.load(first["pipeline_id"])
    assert len(launches) == 1


@pytest.mark.parametrize("iteration", range(5))
def test_concurrent_abandon_and_start_have_one_atomic_winner(launches, iteration):
    key = KEY + f"-{iteration}"
    barrier = threading.Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return _start(key=key)

    def abandon():
        barrier.wait(timeout=10)
        return po.PipelineOrchestrator.abandon_launch_intent(key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future, abandon_future = pool.submit(run), pool.submit(abandon)
        admitted, abandoned = run_future.result(), abandon_future.result()
    _join()
    final = po.PipelineOrchestrator.lookup_launch_intent(key)
    if final["launch_status"] == "abandoned":
        assert admitted["pipeline_id"] is None
        assert abandoned["pipeline_id"] is None
        assert launches == []
    else:
        assert final["launch_status"] == "dispatched"
        assert admitted["pipeline_id"] == abandoned["pipeline_id"] == final["pipeline_id"]
        assert launches == [final["pipeline_id"]]


def test_base_exception_after_start_keeps_live_thread_registration(launches, monkeypatch):
    original_start = po.threading.Thread.start

    def interrupted_start(self):
        original_start(self)
        raise SimulatedCrash()

    with monkeypatch.context() as scope:
        scope.setattr(po.threading.Thread, "start", interrupted_start)
        with pytest.raises(SimulatedCrash):
            _start()
    replay = _start()
    _join()
    assert replay["launch_status"] == "dispatching"
    assert replay["pipeline_id"] in po.PipelineOrchestrator._threads
    assert launches == [replay["pipeline_id"]]


def test_delayed_old_thread_cannot_run_after_explicit_resume(launches, monkeypatch):
    delayed = []

    class PendingThread:
        ident = None

        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
            delayed.append(self)

        def start(self):
            # Models OS thread creation followed by interrupt before bootstrap
            # assigns ident. Its target is delivered later, after resume wins.
            raise SimulatedCrash()

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    with monkeypatch.context() as scope:
        scope.setattr(po.threading, "Thread", PendingThread)
        with pytest.raises(SimulatedCrash):
            _start()
    pid = po.PipelineOrchestrator.lookup_launch_intent(KEY)["pipeline_id"]
    assert po.PipelineOrchestrator._threads[pid] is delayed[0]
    po.PipelineOrchestrator.resume(pid)
    _join()
    delayed[0].target(*delayed[0].args)
    assert launches == [pid]
