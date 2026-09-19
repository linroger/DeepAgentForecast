"""Offline acceptance checks for the standalone, durable research workspace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor

import pytest


BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"
sys.path.insert(0, str(BRIDGE))

import research_workspace as rw  # noqa: E402


IDENTITY = {
    "run_id": "run/../one", "lane": "global", "question": "What changed?",
    "model": "offline-model", "language": "en", "policy": {"version": 1},
}
LONG_TEXT = "Evidence 中文\n" * 8000 + "TAIL: 47 units; source receipt original-9"


@pytest.fixture(scope="module", autouse=True)
def workspace_source_receipts(record_testsuite_property):
    # The guarded launcher's tracked-file inventory omits these new files until
    # integration stages them. Bind this suite to their actual bytes as well.
    for path in (BRIDGE / "research_workspace.py", Path(__file__)):
        record_testsuite_property(
            "workspace_source_sha256:" + path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


@pytest.fixture
def workspace(tmp_path):
    return rw.ResearchWorkspace(tmp_path / "run", IDENTITY)


def child(code, *args):
    """Keep the inherited offline audit hook active in every subprocess."""
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]);\n" + code,
         str(BRIDGE), *map(str, args)],
        text=True, capture_output=True, timeout=20,
    )


def test_full_content_artifact_and_task_survive_fresh_process(workspace):
    ref = workspace.put_artifact(LONG_TEXT, "source/../../unsafe")
    assert set(ref) == {"id", "sha256", "bytes", "path"}
    assert ref["sha256"] == hashlib.sha256(LONG_TEXT.encode()).hexdigest()
    assert ref["bytes"] == len(LONG_TEXT.encode())
    assert workspace.read_artifact(ref) == LONG_TEXT
    inputs = {"question": LONG_TEXT, "context": [ref]}
    result = {"text": LONG_TEXT, "source": ref}
    workspace.save_task("../task/a", inputs, result)
    script = """
import json
from pathlib import Path
from research_workspace import ResearchWorkspace
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
result = w.load_task('../task/a', json.loads(sys.argv[4]))
assert result['text'].endswith('TAIL: 47 units; source receipt original-9')
assert w.read_artifact(result['source']) == result['text']
print(len(result['text']))
"""
    completed = child(script, workspace.root, json.dumps(IDENTITY), json.dumps(inputs))
    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip()) == len(LONG_TEXT)
    assert workspace.load_task("../task/a", inputs) == result
    assert not (workspace.root.parent / "task").exists()


@pytest.mark.parametrize("field,value", [
    ("run_id", "other"), ("lane", "actors"), ("question", "Different?"),
    ("model", "other-model"), ("language", "zh"), ("policy", {"version": 2}),
])
def test_semantic_identity_conflict_does_not_modify_existing_state(workspace, field, value):
    workspace.save_task("t", {}, {"text": "original"})
    before = {p.name: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    with pytest.raises(rw.ResearchWorkspaceError, match="identity"):
        rw.ResearchWorkspace(workspace.root, {**IDENTITY, field: value})
    after = {p.name: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    assert after == before


def test_process_attempt_is_excluded_and_identity_is_copied(tmp_path):
    identity = {**IDENTITY, "policy": {"version": 1}, "attempt_id": "first", "pid": 1}
    first = rw.ResearchWorkspace(tmp_path / "run", identity)
    identity["policy"]["version"] = 9
    first.save_task("t", {}, {"complete": True})
    second = rw.ResearchWorkspace(first.root, {**IDENTITY, "attempt_id": "next", "pid": 2})
    assert second.load_task("t", {}) == {"complete": True}
    assert second.snapshot()["identity"] == IDENTITY


def test_incomplete_task_binds_inputs_and_done_tasks_are_immutable(workspace):
    assert workspace.load_task("task", {"question": "one"}) is None
    with pytest.raises(rw.ResearchWorkspaceError, match="inputs"):
        workspace.load_task("task", {"question": "two"})
    workspace.save_task("task", {"question": "one"}, {"result": "first"})
    workspace.save_task("task", {"question": "one"}, {"result": "first"})
    with pytest.raises(rw.ResearchWorkspaceError, match="conflict"):
        workspace.save_task("task", {"question": "one"}, {"result": "different"})


def test_thread_writes_and_concurrent_initialization(tmp_path):
    root = tmp_path / "shared"

    def write(i):
        w = rw.ResearchWorkspace(root, IDENTITY)
        ref = w.put_artifact(f"full text {i}", "source")
        event_id = w.append_event("same-task", "tool_result", {"text": LONG_TEXT, "i": i})
        w.save_task(f"task/{i}", {"i": i}, {"artifact": ref})
        w.add_discovery("shared follow-up", f"task/{i}", [ref])
        return event_id

    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = list(pool.map(write, range(10)))
    w = rw.ResearchWorkspace(root, IDENTITY)
    assert len(set(ids)) == 10
    events = w.events("same-task")
    assert [e["id"] for e in events] == sorted(ids)
    assert all(e["payload"]["text"] == LONG_TEXT for e in events)
    assert len(w.discoveries()) == 1
    assert len(w.discoveries()[0]["evidence_refs"]) == 10
    assert len(w.snapshot()["tasks"]) == 10


def test_process_writes_share_sqlite_and_blob_store(workspace):
    code = """
import json
from pathlib import Path
from research_workspace import ResearchWorkspace
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
for n in range(8):
    ref = w.put_artifact('identical shared text', 'source')
    w.append_event('shared', 'observation', {'writer': sys.argv[4], 'n': n})
    w.save_task(sys.argv[4] + '/' + str(n), {'n': n}, {'ref': ref})
"""
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda i: child(code, workspace.root, json.dumps(IDENTITY), i), range(3),
        ))
    assert all(p.returncode == 0 for p in results), [p.stderr for p in results]
    assert len(workspace.events("shared")) == 24
    assert len(workspace.snapshot()["tasks"]) == 24


def test_crash_after_receipt_commit_does_not_mark_task_done(workspace):
    code = """
import json, os
from pathlib import Path
from research_workspace import ResearchWorkspace
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
original = w.put_artifact
def crash(text, kind):
    ref = original(text, kind)
    os._exit(73)
w.put_artifact = crash
w.save_task('task', {'version': 1}, {'text': 'complete before death'})
"""
    crashed = child(code, workspace.root, json.dumps(IDENTITY))
    assert crashed.returncode == 73
    reloaded = rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert reloaded.load_task("task", {"version": 1}) is None
    assert any("complete before death" in reloaded.read_artifact(ref)
               for ref in reloaded.snapshot()["artifacts"])
    reloaded.save_task("task", {"version": 1}, {"text": "complete before death"})
    assert reloaded.load_task("task", {"version": 1}) == {"text": "complete before death"}


def test_orphan_blob_is_safe_and_reused_after_receipt_failure(workspace, monkeypatch):
    original = workspace._record_artifact

    def fail(*args, **kwargs):
        raise OSError("simulated failure before artifact metadata commit")

    monkeypatch.setattr(workspace, "_record_artifact", fail)
    with pytest.raises((OSError, rw.ResearchWorkspaceError)):
        workspace.save_task("task", {}, {"text": "not completed"})
    blobs = list((workspace.root / "blobs").glob("*.txt"))
    assert len(blobs) == 1
    assert workspace.load_task("task", {}) is None
    monkeypatch.setattr(workspace, "_record_artifact", original)
    workspace.save_task("task", {}, {"text": "not completed"})
    assert list((workspace.root / "blobs").glob("*.txt")) == blobs


@pytest.mark.parametrize("crash_point", ["blob", "done"])
def test_hard_process_exit_at_storage_boundaries(workspace, crash_point):
    code = """
import json, os, sqlite3
from pathlib import Path
from research_workspace import ResearchWorkspace
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
if sys.argv[4] == 'blob':
    def crash_before_receipt(*args):
        os._exit(75)
    w._record_artifact = crash_before_receipt
else:
    original = sqlite3.connect
    class CrashAfterCommit(sqlite3.Connection):
        def commit(self):
            super().commit()
            if self.execute("SELECT count(*) FROM tasks WHERE status='done'").fetchone()[0]:
                os._exit(75)
    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=CrashAfterCommit)
    sqlite3.connect = connect
w.save_task('task', {'version': 1}, {'text': 'fully durable'})
"""
    assert child(code, workspace.root, json.dumps(IDENTITY), crash_point).returncode == 75
    reloaded = rw.ResearchWorkspace(workspace.root, IDENTITY)
    result = reloaded.load_task("task", {"version": 1})
    if crash_point == "blob":
        assert result is None
        assert reloaded.snapshot()["artifacts"] == []
        assert len(list((workspace.root / "blobs").glob("*.txt"))) == 1
        reloaded.save_task("task", {"version": 1}, {"text": "fully durable"})
    else:
        assert result == {"text": "fully durable"}
    assert reloaded.load_task("task", {"version": 1}) == {"text": "fully durable"}


def test_frozen_phase_inputs_and_native_sources_restore_verbatim(workspace):
    block = workspace.put_artifact(LONG_TEXT, "phase_context")
    phase_key = "phase-inputs:initial"
    phase_identity = {"model": IDENTITY["model"], "policy": IDENTITY["policy"]}
    assert workspace.load_task(phase_key, phase_identity) is None
    workspace.save_task(phase_key, phase_identity, {"blocks": [block]})
    sources = [{"id": "native/source/1", "url": "https://example.test/source",
                "content": "unmodified\r\n" + LONG_TEXT, "tool_call_id": "fetch:7"}]
    workspace.save_task("worker", {"phase": "initial", "blocks": [block]},
                        {"text": LONG_TEXT, "sources": sources})
    reloaded = rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert reloaded.load_task(phase_key, phase_identity) == {"blocks": [block]}
    assert reloaded.load_task("worker", {"phase": "initial", "blocks": [block]})["sources"] == sources
    changed = reloaded.put_artifact("different context", "phase_context")
    with pytest.raises(rw.ResearchWorkspaceError, match="conflict"):
        reloaded.save_task(phase_key, phase_identity, {"blocks": [changed]})


@pytest.mark.parametrize("change", ["bytes", "sha256", "id", "path", "absolute", "symlink"])
def test_artifact_tampering_and_traversal_are_rejected(workspace, change, tmp_path):
    ref = workspace.put_artifact("original", "source")
    bad = dict(ref)
    if change == "bytes":
        bad["bytes"] += 1
    elif change in {"id", "sha256"}:
        bad[change] = "0" * 64
    elif change == "path":
        bad["path"] = "../secret.txt"
    elif change == "absolute":
        bad["path"] = str(workspace.root / ref["path"])
    else:
        destination = tmp_path / "outside.txt"
        destination.write_text("original")
        blob = workspace.root / ref["path"]
        blob.unlink()
        blob.symlink_to(destination)
    with pytest.raises(rw.ResearchWorkspaceError):
        workspace.read_artifact(bad)


def test_changed_blob_rejects_load_and_restart_without_reset(workspace):
    ref = workspace.put_artifact("original", "source")
    workspace.save_task("task", {}, {"ref": ref})
    (workspace.root / ref["path"]).write_text("tampered")
    with pytest.raises(rw.ResearchWorkspaceError):
        workspace.load_task("task", {})
    with pytest.raises(rw.ResearchWorkspaceError):
        rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert (workspace.root / ref["path"]).read_text() == "tampered"


@pytest.mark.parametrize("table,column,value", [
    ("tasks", "inputs_json", '{"other":1}'),
    ("tasks", "receipt_json", "{}"),
    ("events", "payload_json", '{"changed":true}'),
    ("discoveries", "question", "changed question"),
    ("metadata", "identity_json", "{}"),
])
def test_corrupt_records_fail_closed(workspace, table, column, value):
    workspace.save_task("task", {}, {"text": "result"})
    workspace.append_event("task", "observation", {"text": "original"})
    workspace.add_discovery("Follow up?", "task")
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute(f"UPDATE {table} SET {column} = ?", (value,))
    with pytest.raises(rw.ResearchWorkspaceError):
        rw.ResearchWorkspace(workspace.root, IDENTITY)


@pytest.mark.parametrize("damage", ["garbage", "empty", "missing", "missing_manifest"])
def test_broken_database_or_manifest_is_never_reinitialized(workspace, damage):
    db = workspace.root / "workspace.sqlite3"
    if damage == "missing":
        db.unlink()
    elif damage == "missing_manifest":
        (workspace.root / "identity.json").unlink()
    else:
        db.write_bytes(b"not a database" if damage == "garbage" else b"")
    with pytest.raises(rw.ResearchWorkspaceError):
        rw.ResearchWorkspace(workspace.root, IDENTITY)


def test_discoveries_survive_restart_with_full_question_and_status(workspace):
    ref = workspace.put_artifact(LONG_TEXT, "source")
    question = "Question? " + LONG_TEXT
    discovery = workspace.add_discovery(question, "../../task", [ref])
    assert discovery["status"] == "pending"
    assert workspace.add_discovery(question, "another-task", [ref])["id"] == discovery["id"]
    workspace.mark_discovery(discovery["id"], "admitted", "follow-up/1")
    reloaded = rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert reloaded.discoveries("pending") == []
    saved = reloaded.discoveries("admitted")[0]
    assert saved["question"] == question
    assert saved["origin_task"] == "../../task"
    assert saved["task_id"] == "follow-up/1"
    assert saved["evidence_refs"] == [ref]
    reloaded.mark_discovery(discovery["id"], "done")
    assert reloaded.discoveries("done")[0]["task_id"] == "follow-up/1"
    with pytest.raises(KeyError):
        reloaded.mark_discovery("unknown", "done")


def test_execution_lock_contends_across_processes_and_releases_on_crash(workspace):
    code = """
import json, os
from pathlib import Path
from research_workspace import ResearchWorkspace, ResearchWorkspaceError
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
try:
    with w.execution_lock():
        if sys.argv[4] == 'crash':
            os._exit(73)
except ResearchWorkspaceError as exc:
    assert 'lock' in str(exc)
    sys.exit(74)
"""
    with workspace.execution_lock():
        contender = child(code, workspace.root, json.dumps(IDENTITY), "normal")
        assert contender.returncode == 74, contender.stderr
        with pytest.raises(rw.ResearchWorkspaceError, match="lock"):
            with workspace.execution_lock():
                pytest.fail("nested execution must not enter")
    crashed = child(code, workspace.root, json.dumps(IDENTITY), "crash")
    assert crashed.returncode == 73, crashed.stderr
    assert child(code, workspace.root, json.dumps(IDENTITY), "normal").returncode == 0
    with workspace.execution_lock():
        workspace.save_task("still-usable", {}, {"done": True})


def test_execution_lease_defers_without_waiting_and_blocks_reentry(workspace):
    running, other = Future(), Future()
    running.set_running_or_notify_cancel()
    with workspace.execution_lock() as lease:
        lease.defer_release_until([running, other, running])
        # A different thread can use the storage while the lease is held.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(workspace.save_task, "worker", {}, {"complete": True}).result(timeout=2)
    # Context exit must not wait for either Future. Even a second instance in
    # this process cannot bypass the lease retained by the unfinished callback.
    another = rw.ResearchWorkspace(workspace.root, IDENTITY)
    with pytest.raises(rw.ResearchWorkspaceError, match="lock"):
        with another.execution_lock():
            pytest.fail("must retain ownership")
    other.cancel()
    with pytest.raises(rw.ResearchWorkspaceError, match="lock"):
        with workspace.execution_lock():
            pytest.fail("one worker still running")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(running.set_result, None).result(timeout=2)
    with workspace.execution_lock():
        assert workspace.load_task("worker", {}) == {"complete": True}


def test_execution_lease_releases_once_after_error_and_finished_futures(workspace):
    completed = Future()
    completed.set_result(None)
    with pytest.raises(ValueError, match="caller failed"):
        with workspace.execution_lock() as lease:
            lease.defer_release_until([completed])
            lease.defer_release_until([])
            raise ValueError("caller failed")
    with workspace.execution_lock() as next_lease:
        assert next_lease is not lease
        with pytest.raises(rw.ResearchWorkspaceError, match="closed"):
            lease.defer_release_until([Future()])


def test_deferred_lease_blocks_other_process_after_context_exit(workspace):
    pending = Future()
    with workspace.execution_lock() as lease:
        lease.defer_release_until([pending])
    code = """
import json
from pathlib import Path
from research_workspace import ResearchWorkspace, ResearchWorkspaceError
w = ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
try:
    with w.execution_lock():
        pass
except ResearchWorkspaceError:
    sys.exit(74)
"""
    assert child(code, workspace.root, json.dumps(IDENTITY)).returncode == 74
    pending.set_exception(RuntimeError("worker finished with error"))
    assert child(code, workspace.root, json.dumps(IDENTITY)).returncode == 0


def test_verbatim_crlf_and_public_identity_copy(workspace):
    text = "line one\r\nline two\r\n\r\n尾部\r"
    assert workspace.read_artifact(workspace.put_artifact(text, "source")) == text
    identity = workspace.identity
    identity["policy"]["version"] = 99
    assert workspace.identity == IDENTITY


def test_connections_use_wal_full_and_close_on_success_and_failure(tmp_path, monkeypatch):
    connections = []
    settings = []
    connect = sqlite3.connect

    class Observed(sqlite3.Connection):
        def close(self):
            settings.append((self.execute("PRAGMA journal_mode").fetchone()[0],
                             self.execute("PRAGMA synchronous").fetchone()[0]))
            super().close()

    def traced(*args, **kwargs):
        conn = connect(*args, **kwargs, factory=Observed)
        connections.append(conn)
        return conn

    monkeypatch.setattr(rw.sqlite3, "connect", traced)
    w = rw.ResearchWorkspace(tmp_path / "workspace", IDENTITY)
    w.save_task("task", {}, {"text": "result"})
    w.snapshot()
    with pytest.raises(rw.ResearchWorkspaceError):
        w.load_task("task", {"changed": True})
    assert len(connections) > 4
    assert len(settings) == len(connections)
    assert all(setting == ("wal", 2) for setting in settings)
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


@pytest.mark.parametrize("invalid", [{1: "nonstring-key"}, {"tuple": (1, 2)}, {"n": float("nan")}])
def test_non_lossless_json_is_rejected_before_any_workspace_write(tmp_path, invalid):
    root = tmp_path / "invalid"
    with pytest.raises((rw.ResearchWorkspaceError, TypeError, ValueError)):
        rw.ResearchWorkspace(root, {**IDENTITY, "policy": invalid})
    assert not root.exists()


@pytest.mark.parametrize("field", ["inputs", "result", "event"])
def test_nested_reference_in_reference_metadata_must_be_registered(workspace, field):
    valid = workspace.put_artifact("registered", "source")
    missing_hash = hashlib.sha256(b"unregistered").hexdigest()
    missing = {"id": missing_hash, "sha256": missing_hash, "bytes": 12,
               "path": f"blobs/{missing_hash}.txt"}
    value = {"evidence": {**valid, "provenance": {"missing": missing}}}
    with pytest.raises(rw.ResearchWorkspaceError, match="receipt missing"):
        if field == "inputs":
            workspace.load_task("task", value)
        elif field == "result":
            workspace.save_task("task", {}, value)
        else:
            workspace.append_event("task", "observation", value)
    assert not any(task["status"] == "done" for task in workspace.snapshot()["tasks"])


@pytest.mark.parametrize("point", [
    "manifest_temp", "manifest", "database_open", "database_committed", "publication",
])
def test_interrupted_initialization_recovers_only_unpublished_store(tmp_path, point):
    root = tmp_path / "new-workspace"
    code = """
import json, os, sqlite3
from pathlib import Path
from research_workspace import ResearchWorkspace
point = sys.argv[4]
if point == 'manifest_temp':
    original_link = os.link
    def crash_link(source, destination, *args, **kwargs):
        if Path(destination).name == 'identity.json':
            os._exit(76)
        return original_link(source, destination, *args, **kwargs)
    os.link = crash_link
elif point == 'manifest':
    original_publish = ResearchWorkspace._publish
    def crash_publish(self, destination, data):
        original_publish(self, destination, data)
        if destination.name == 'identity.json':
            os._exit(76)
    ResearchWorkspace._publish = crash_publish
elif point == 'database_open':
    original_connect = sqlite3.connect
    def crash_connect(*args, **kwargs):
        original_connect(*args, **kwargs)
        os._exit(76)
    sqlite3.connect = crash_connect
elif point == 'database_committed':
    original_initialize = ResearchWorkspace._initialize_database
    def crash_initialize(self):
        original_initialize(self)
        os._exit(76)
    ResearchWorkspace._initialize_database = crash_initialize
else:
    original_rename = os.rename
    def crash_rename(source, destination, *args, **kwargs):
        original_rename(source, destination, *args, **kwargs)
        os._exit(76)
    os.rename = crash_rename
ResearchWorkspace(Path(sys.argv[2]), json.loads(sys.argv[3]))
"""
    crashed = child(code, root, json.dumps(IDENTITY), point)
    assert crashed.returncode == 76, crashed.stderr
    if point != "manifest_temp":
        with pytest.raises(rw.ResearchWorkspaceError, match="identity"):
            rw.ResearchWorkspace(root, {**IDENTITY, "question": "changed question"})
    resumed = rw.ResearchWorkspace(root, IDENTITY)
    assert resumed.snapshot()["tasks"] == []
    resumed.save_task("after-recovery", {}, {"text": "durable"})
    assert rw.ResearchWorkspace(root, IDENTITY).load_task("after-recovery", {}) == {"text": "durable"}


def test_committed_empty_workspace_corruption_cannot_use_bootstrap_recovery(workspace):
    (workspace.root / "workspace.sqlite3").write_bytes(b"damaged after initialization")
    with pytest.raises(rw.ResearchWorkspaceError):
        rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert (workspace.root / "workspace.sqlite3").read_bytes() == b"damaged after initialization"


@pytest.mark.parametrize("method", ["lookup_artifact", "lookup_ref"])
def test_artifact_id_lookup_is_scoped_and_hash_verified(workspace, tmp_path, method):
    ref = workspace.put_artifact("verbatim\r\nbody", "fetched_source")
    lookup = getattr(workspace, method)
    assert lookup(ref["id"]) == ref
    foreign = rw.ResearchWorkspace(tmp_path / "foreign", {"run": "other"})
    with pytest.raises(rw.ResearchWorkspaceError):
        getattr(foreign, method)(ref["id"])
    for value in ("../identity.json", str(workspace.root / ref["path"]), "0" * 64):
        with pytest.raises(rw.ResearchWorkspaceError):
            lookup(value)
    (workspace.root / ref["path"]).write_bytes(b"changed")
    with pytest.raises(rw.ResearchWorkspaceError, match="hash"):
        lookup(ref["id"])


@pytest.mark.parametrize("method", ["lookup_artifact", "lookup_ref"])
def test_unknown_id_has_typed_registry_absence(workspace, method):
    assert issubclass(rw.ResearchArtifactNotFoundError, rw.ResearchWorkspaceError)
    with pytest.raises(rw.ResearchArtifactNotFoundError, match="artifact receipt missing"):
        getattr(workspace, method)("0" * 64)
    with pytest.raises(rw.ResearchWorkspaceError) as invalid:
        getattr(workspace, method)("../identity.json")
    assert not isinstance(invalid.value, rw.ResearchArtifactNotFoundError)


@pytest.mark.parametrize("damage", ["missing_blob", "changed_blob", "record", "database", "identity"])
def test_managed_corruption_is_not_unknown_artifact(workspace, damage):
    ref = workspace.put_artifact("original body", "source")
    if damage == "missing_blob":
        (workspace.root / ref["path"]).unlink()
    elif damage == "changed_blob":
        (workspace.root / ref["path"]).write_bytes(b"changed")
    elif damage == "record":
        with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
            conn.execute("UPDATE artifacts SET bytes=bytes+1")
    elif damage == "database":
        (workspace.root / "workspace.sqlite3").unlink()
    else:
        (workspace.root / "identity.json").write_text("{}")
    with pytest.raises(rw.ResearchWorkspaceError) as corrupt:
        workspace.lookup_ref(ref["id"])
    assert not isinstance(corrupt.value, rw.ResearchArtifactNotFoundError)


def test_missing_receipt_in_existing_task_is_corruption_not_caller_lookup(workspace):
    ref = workspace.put_artifact("durable evidence", "source")
    workspace.save_task("task", {}, {"ref": ref})
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("DELETE FROM artifacts WHERE id=?", (ref["id"],))
    for read in (lambda: workspace.read_artifact(ref), lambda: workspace.load_task("task", {}),
                 workspace.snapshot):
        with pytest.raises(rw.ResearchWorkspaceError, match="receipt missing") as corrupt:
            read()
        assert not isinstance(corrupt.value, rw.ResearchArtifactNotFoundError)


def test_source_events_leave_task_admission_and_completion_unchanged(workspace):
    url = "https://example.test/original"
    task_id = "source:" + hashlib.sha256(url.encode()).hexdigest()
    ref = workspace.put_artifact("source\r\n" + LONG_TEXT, "fetched_source")
    payload = {"url": url, "ref": ref, "provenance": {"tool_call_id": "fetch-7"}}
    workspace.append_event(task_id, "native_source", payload)
    assert workspace.snapshot()["tasks"] == []
    workspace.load_task(task_id, {"task": "already pending"})
    workspace.append_event(task_id, "native_source", payload)
    assert workspace.snapshot()["tasks"][0]["status"] == "pending"
    workspace.save_task(task_id, {"task": "already pending"}, {"text": "complete"})
    workspace.append_event(task_id, "native_source", payload)
    assert workspace.load_task(task_id, {"task": "already pending"}) == {"text": "complete"}
    assert all(event["payload"] == payload for event in workspace.events(task_id))


def test_events_by_kind_preserves_rows_order_and_source_metadata(workspace):
    ref = workspace.put_artifact(LONG_TEXT, "cached_source")
    first = workspace.append_event("source:one", "native_source", {
        "ref": ref, "source_origin": "cache", "provider": "cache", "text": LONG_TEXT,
    })
    workspace.append_event("source:one", "diagnostic", {"text": "unrelated"})
    second = workspace.append_event("source:two", "native_source", {
        "source_origin": "search_snippet", "text": "kind is not a provenance claim",
    })
    workspace.load_task("pending", {})
    workspace.save_task("done", {}, {"text": "existing result"})
    before = workspace.snapshot()
    rows = workspace.events_by_kind("native_source")
    assert [row["id"] for row in rows] == [first, second]
    assert rows == [row for row in before["events"] if row["kind"] == "native_source"]
    assert workspace.events_by_kind("missing-kind") == []
    assert workspace.snapshot() == before


def test_events_by_kind_reads_only_matching_events_and_refs(workspace, monkeypatch):
    selected = workspace.put_artifact("selected full body", "source")
    unrelated = workspace.put_artifact("unrelated completed evidence", "source")
    workspace.save_task("unrelated-task", {}, {"ref": unrelated})
    workspace.append_event("unrelated-task", "diagnostic", {"ref": unrelated})
    event_id = workspace.append_event("source:one", "native_source", {"ref": selected})
    (workspace.root / unrelated["path"]).write_text("unrelated damage")
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("UPDATE events SET payload_json='damaged' WHERE kind='diagnostic'")
    seen = []
    original_read = workspace._read_bytes

    def track(path):
        seen.append(path)
        return original_read(path)

    def forbidden(*args, **kwargs):
        pytest.fail("kind lookup must not inspect full snapshots or task results")

    monkeypatch.setattr(workspace, "_read_bytes", track)
    monkeypatch.setattr(workspace, "snapshot", forbidden)
    monkeypatch.setattr(workspace, "_task", forbidden)
    assert workspace.events_by_kind("native_source") == [{
        "id": event_id, "task_id": "source:one", "kind": "native_source", "payload": {"ref": selected},
    }]
    assert set(seen) == {workspace.root / "identity.json", workspace.root / selected["path"]}
    with pytest.raises(rw.ResearchWorkspaceError, match="event record"):
        workspace.events_by_kind("diagnostic")
    (workspace.root / selected["path"]).write_text("selected damage")
    with pytest.raises(rw.ResearchWorkspaceError, match="hash"):
        workspace.events_by_kind("native_source")


def test_events_by_kind_index_migrates_existing_database_without_record_changes(workspace):
    ref = workspace.put_artifact(LONG_TEXT, "source")
    workspace.append_event("source:first", "native_source", {"ref": ref})
    workspace.append_event("other", "tool_result", {"text": "old event"})
    workspace.load_task("pending", {"phase": 1})
    workspace.save_task("done", {}, {"text": "saved"})
    discovery = workspace.add_discovery("Question?", "done", [ref])
    workspace.mark_discovery(discovery["id"], "deferred")
    before = workspace.snapshot()
    tables = ("metadata", "artifacts", "tasks", "events", "discoveries", "sqlite_sequence")
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("DROP INDEX IF EXISTS events_by_kind")
        records = {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
    manifest = (workspace.root / "identity.json").read_bytes()
    reopened = rw.ResearchWorkspace(workspace.root, IDENTITY)
    assert reopened.snapshot() == before
    assert reopened.events_by_kind("native_source") == [before["events"][0]]
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert [row[2] for row in conn.execute("PRAGMA index_info(events_by_kind)")] == ["kind", "id"]
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM events WHERE kind=? ORDER BY id",
                            ("native_source",)).fetchall()
        assert any("SEARCH" in row[3] and "events_by_kind" in row[3] for row in plan)
        assert {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables} == records
    assert (workspace.root / "identity.json").read_bytes() == manifest


@pytest.mark.parametrize("kind", ["", "   ", None, 1])
def test_events_by_kind_rejects_invalid_kind(workspace, kind):
    with pytest.raises(rw.ResearchWorkspaceError, match="event kind"):
        workspace.events_by_kind(kind)


def test_kind_index_migration_cannot_modify_wrong_identity_workspace(workspace):
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("DROP INDEX IF EXISTS events_by_kind")
    before = (workspace.root / "workspace.sqlite3").read_bytes()
    with pytest.raises(rw.ResearchWorkspaceError, match="identity"):
        rw.ResearchWorkspace(workspace.root, {**IDENTITY, "model": "changed"})
    assert (workspace.root / "workspace.sqlite3").read_bytes() == before


def test_module_imports_standalone_without_app_or_bridge_imports(tmp_path):
    code = """
import importlib.abc
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name == 'app' or name.startswith(('app.', 'backend', 'deerflow_bridge')):
            raise AssertionError('forbidden import: ' + name)
sys.meta_path.insert(0, Guard())
from research_workspace import ResearchWorkspace
from pathlib import Path
w = ResearchWorkspace(Path(sys.argv[2]), {'run': 'standalone'})
assert w.read_artifact(w.put_artifact('hello', 'text')) == 'hello'
"""
    result = child(code, tmp_path / "standalone")
    assert result.returncode == 0, result.stderr
