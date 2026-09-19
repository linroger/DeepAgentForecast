"""Offline regressions for CLI retries while physical callbacks outlive timeout.

The real main ownership wrapper, adapter, scheduler, multipart synthesis, caches
and execution leases run against temporary workspaces. Only native construction
and provider-independent setup are substituted. The first CLI payload selects
the real phase under test; the retry payload detects entry/output mutation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import nullcontext
import hashlib
import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "deerflow_bridge"
QUESTION = "Offline ownership review"
SOURCE_PATHS = [BRIDGE / name for name in (
    "deerflow_research.py", "agentic_bridge.py", "agentic_research.py",
    "research_invocation.py", "research_workspace.py", "research_synthesis.py",
)] + [Path(__file__)]


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCE_PATHS}
    for path, digest in before.items():
        record_testsuite_property("invocation_integration_source_sha256:" + path.name, digest)
    yield
    changed = [path.name for path, digest in before.items()
               if hashlib.sha256(path.read_bytes()).hexdigest() != digest]
    assert not changed, f"Concurrent source changes require a fresh run: {changed}"


class _CallbackGate:
    def __init__(self, expected):
        self.expected = expected
        self.started = 0
        self.guard = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def block(self, error):
        with self.guard:
            self.started += 1
            if self.started == self.expected:
                self.entered.set()
        assert self.release.wait(10), "test failed to release its native callback"
        raise error


@pytest.fixture
def execution(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(BRIDGE))
    from research_context import ContextPolicy

    for name in ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    for key, value in {
        "RESEARCH_ENGINE": "agentic",
        "RESEARCH_AGENTIC_CACHE_DIR": str(tmp_path / "configured-cache"),
        "RESEARCH_AGENTIC_PHASE_DEADLINE_S": "1",
        "RESEARCH_AGENTIC_CALL_TIMEOUT_S": "0.1",
        "RESEARCH_AGENTIC_MAX_FOLLOWUPS": "0",
        "RESEARCH_BUDGET_RUN_ID": "invocation-integration",
        "RESEARCH_BUDGET_DB": str(tmp_path / "budget.sqlite3"),
        "RESEARCH_BUDGET_TELEMETRY_PATH": str(tmp_path / "budget.json"),
        "RESEARCH_COMPACTION_DB": str(tmp_path / "compaction.sqlite3"),
        "RESEARCH_SOURCE_CACHE_DIR": str(tmp_path / "sources"),
        "PREDICTION_MARKETS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    name = "invocation_integration_cli"
    spec = importlib.util.spec_from_file_location(name, BRIDGE / "deerflow_research.py")
    dr = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, dr)
    spec.loader.exec_module(dr)
    adapter = importlib.import_module("agentic_bridge")
    archive = importlib.import_module("research_archive")
    invocation = importlib.import_module("research_invocation")
    workspace_module = importlib.import_module("research_workspace")
    dr._reset_compaction_stop()
    out = tmp_path / "output"
    out.mkdir()
    workspace, _ = adapter.prepare(out, QUESTION, "standard", "offline")
    monkeypatch.setattr(dr, "_research_budget", SimpleNamespace(
        subagent_call_lease=nullcontext, model_call_lease=lambda *_: nullcontext(),
    ))
    monkeypatch.setattr(sys, "argv", ["deerflow_research.py", "--out-dir", str(out)])
    futures, gates = [], []

    class RecordingExecutor(ThreadPoolExecutor):
        """Observe actual executor futures so teardown waits for real completion."""
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            futures.append(future)
            return future

    value = SimpleNamespace(
        dr=dr, adapter=adapter, invocation=invocation, workspace=workspace, out=out,
        workspace_error=workspace_module.ResearchWorkspaceError,
        log=SimpleNamespace(write=lambda *_: None), executor=RecordingExecutor,
        futures=futures, gates=gates,
    )
    try:
        yield value
    finally:
        for gate in gates:
            gate.release.set()
        _, pending = wait(futures, timeout=3)
        assert not pending, "native callbacks must drain before fixture teardown"
        archive.activate_workspace(None)
        dr._reset_compaction_stop()


def _child_main(out):
    """A fresh interpreter exercises flock and the actual CLI admission wrapper."""
    script = """
import importlib.util, sys
from pathlib import Path
bridge, out = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(bridge))
spec = importlib.util.spec_from_file_location('invocation_child_cli', bridge / 'deerflow_research.py')
dr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dr
spec.loader.exec_module(dr)
dr._main_impl = lambda: print('RETRY_PAYLOAD_ENTERED') or 0
sys.argv = ['deerflow_research.py', '--out-dir', out]
raise SystemExit(dr.main())
"""
    return subprocess.run(
        [sys.executable, "-c", script, str(BRIDGE), str(out)],
        text=True, capture_output=True, timeout=10,
    )


def _assert_retry_fenced(execution, monkeypatch, gate):
    dr, invocation, workspace = execution.dr, execution.invocation, execution.workspace
    assert gate.entered.is_set(), "deadline expired before every reproducing callback entered"
    assert len(execution.futures) == gate.expected
    assert all(not future.done() for future in execution.futures)
    with pytest.raises(execution.workspace_error):
        with workspace.execution_lock():
            pytest.fail("the original phase must retain its execution lease")

    meta = execution.out / "meta.json"
    original = b'{"existing_checkpoint":"must survive a contender"}\n'
    meta.write_bytes(original)
    calls = []

    def retry_payload():
        calls.append("entered")
        meta.write_bytes(b"contender changed metadata")
        dr._reset_compaction_stop()
        return 0

    monkeypatch.setattr(dr, "_main_impl", retry_payload)
    before_stop = dr.ResearchCompactionError("checkpoint_unavailable")
    dr._stop_after_compaction_failure(before_stop)
    for _ in range(2):
        assert dr.main() == 4, "a timed-out physical callback lost CLI ownership"
        assert not calls and meta.read_bytes() == original
        with pytest.raises(dr.ResearchCompactionError):
            with invocation.producer_scope():
                pytest.fail("late admission reopened before physical callbacks drained")
        with pytest.raises(dr.ResearchCompactionError):
            dr._raise_if_compaction_stopped()
    child = _child_main(execution.out)
    assert child.returncode == 4, child.stderr
    assert "RETRY_PAYLOAD_ENTERED" not in child.stdout
    assert "checkpoint_unavailable" in child.stderr
    assert str(workspace.root) not in child.stderr
    assert meta.read_bytes() == original
    # This must be the configured cache's owner, not a fallback under --out-dir.
    assert (workspace.root.parent / (workspace.root.name + ".owner")).is_dir()
    assert not (execution.out / "agentic.owner").exists()


def _assert_retry_after_drain(execution, gate):
    gate.release.set()
    _, pending = wait(execution.futures, timeout=3)
    assert not pending
    assert all(future.exception() is not None for future in execution.futures)
    # Future waiters can wake before the workspace's done callback closes flock.
    # Poll the public lease only during this bounded teardown interval.
    deadline = time.monotonic() + 3
    while True:
        try:
            with execution.workspace.execution_lock():
                break
        except execution.workspace_error:
            assert time.monotonic() < deadline, "phase lease did not release after drain"
            threading.Event().wait(0.005)
    assert execution.dr.main() == 0
    assert (execution.out / "meta.json").read_bytes() == b"contender changed metadata"
    child = _child_main(execution.out)
    assert child.returncode == 0, child.stderr
    assert "RETRY_PAYLOAD_ENTERED" in child.stdout


def test_coordinator_preparation_timeout_retains_cli_owner(execution, monkeypatch):
    """Five real phase workers block before run_streamed_turn has registered."""
    scheduler = importlib.import_module("agentic_research")
    monkeypatch.setattr(scheduler, "ThreadPoolExecutor", execution.executor)
    gate = _CallbackGate(5)
    execution.gates.append(gate)
    dr, adapter = execution.dr, execution.adapter
    monkeypatch.setattr(adapter, "_prepare_native_agent", lambda *_: gate.block(
        dr.ResearchCompactionError("checkpoint_unavailable"),
    ))
    monkeypatch.setattr(dr, "_main_impl", lambda: adapter.run_stage(
        dr, object(), QUESTION, "standard", None, "offline", "review-thread",
        execution.log, out_dir=execution.out,
    ))
    started = time.monotonic()
    assert dr.main() == 4
    assert time.monotonic() - started < 4, "CLI waited for timed-out workers"
    _assert_retry_fenced(execution, monkeypatch, gate)
    _assert_retry_after_drain(execution, gate)


def test_multipart_model_construction_timeout_retains_cli_owner(execution, monkeypatch):
    """The real submitted _bare_synth_invoke blocks before _invoke_model."""
    futures_module = importlib.import_module("concurrent.futures")
    monkeypatch.setattr(futures_module, "ThreadPoolExecutor", execution.executor)
    gate = _CallbackGate(1)
    execution.gates.append(gate)
    dr = execution.dr
    monkeypatch.setattr(dr, "_stage1_model_messages", lambda *_: ["offline fixture"])
    monkeypatch.setattr(dr, "_configured_model_fallback", lambda *_: None)
    monkeypatch.setattr(dr, "_build_tool_free_model", lambda *_: gate.block(
        dr.ResearchCompactionError("checkpoint_unavailable"),
    ))
    monkeypatch.setattr(dr, "_main_impl", lambda: dr.synthesize_multipart(
        QUESTION, None, "standard", "offline", ["Evidence body"], [],
        "Evidence body", execution.log,
    ))
    started = time.monotonic()
    assert dr.main() == 4
    assert time.monotonic() - started < 4, "CLI waited for timed-out model construction"
    _assert_retry_fenced(execution, monkeypatch, gate)
    _assert_retry_after_drain(execution, gate)
