"""Regression checks for synthesis producer ownership and workspace error identity."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "deerflow_bridge"
sys.path[:0] = [str(ROOT), str(BRIDGE)]

from deerflow_bridge import research_invocation as invocation  # noqa: E402
from deerflow_bridge import research_synthesis as synthesis  # noqa: E402
from deerflow_bridge.research_compaction import (  # noqa: E402
    ResearchCompactionError,
    reset_compaction_stop,
)
from deerflow_bridge.research_context import ContextPolicy  # noqa: E402
from deerflow_bridge.research_workspace import (  # noqa: E402
    ResearchWorkspace,
    ResearchWorkspaceError,
)


@pytest.fixture(autouse=True)
def isolated_policy_and_stop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_compaction_stop()
    for name in ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    yield
    reset_compaction_stop()


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property) -> None:
    for path in (
        BRIDGE / "research_invocation.py",
        BRIDGE / "research_synthesis.py",
        BRIDGE / "research_workspace.py",
        Path(__file__),
    ):
        record_testsuite_property(
            "synthesis_review_source_sha256:" + path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize("phase", ["advisory", "repair"])
def test_timeout_retains_invocation_before_callback_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Pause after the fence check, where the old code had no producer token."""
    workspace = ResearchWorkspace(tmp_path / "synthesis", {"model": "offline-review"})
    paused, release = threading.Event(), threading.Event()
    executors: list[ThreadPoolExecutor] = []
    invoked: list[str] = []
    original_producer = synthesis.producer_scope

    @contextmanager
    def delayed_registration() -> Iterator[None]:
        paused.set()
        assert release.wait(5), "test did not release the paused producer"
        with original_producer():
            yield

    class DeadlineAfterRegistrationPause(synthesis._Fence):
        def check(self) -> None:
            # Expire only after the worker reaches the exact race boundary;
            # setup I/O speed must not determine whether the test exercises it.
            if paused.is_set():
                self.deadline = 0
            super().check()

    class TrackedExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            executors.append(self)

    monkeypatch.setattr(synthesis, "producer_scope", delayed_registration)
    monkeypatch.setattr(synthesis, "_Fence", DeadlineAfterRegistrationPause)
    monkeypatch.setattr(synthesis, "ThreadPoolExecutor", TrackedExecutor)
    report = "# Report\n\n## Capacity\n\nOld claim [S1].\n\n## Risk\n\nUnchanged.\n"
    sources = [{"id": "S1", "text": "Verified capacity is 42."}]
    advisory = {
        "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
        "reviews": [{"weaknesses": [{"section_heading": "Capacity", "weakness": "Check capacity"}]}],
    }

    def invoke(task: dict) -> dict:
        invoked.append(task["label"])
        return {"section_heading": "Capacity", "replacement": "Verified capacity 42 [S1]."}

    def validate(candidate: str, records: list) -> dict:
        return {"passed": True, "errors": []}

    try:
        with invocation.invocation_scope(workspace.root):
            with pytest.raises(synthesis.ResearchSynthesisTimeout):
                if phase == "advisory":
                    synthesis.advisory_reviews(workspace, report, sources, invoke, workers=1, timeout_s=30)
                else:
                    synthesis.targeted_repairs(
                        workspace,
                        report,
                        sources,
                        advisory,
                        invoke,
                        validate,
                        workers=1,
                        timeout_s=30,
                    )
            assert paused.is_set() and not invoked
            before = workspace.snapshot()

        # The controller has returned and exited the invocation. A worker which
        # has not yet entered producer_scope must still prevent a new attempt.
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(workspace.root):
                pass
        owner_workspace = ResearchWorkspace(
            workspace.root.with_name(workspace.root.name + ".owner"),
            {"schema_version": invocation.SCHEMA_VERSION},
        )
        with pytest.raises(ResearchWorkspaceError):
            with owner_workspace.execution_lock():
                pass
    finally:
        release.set()
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)

    assert not invoked, "expired callback entered its body after invocation exit"
    assert workspace.snapshot() == before, "late worker changed the durable workspace"
    with invocation.invocation_scope(workspace.root):
        pass
    with workspace.execution_lock():
        pass


@pytest.mark.parametrize("order", ["bare-first", "package-first", "concurrent"])
def test_cold_import_workspace_errors_remain_control_failures(tmp_path: Path, order: str) -> None:
    """Fresh interpreters prevent a warm module cache from hiding split types."""
    script = r"""
import importlib
from pathlib import Path
import socket
import sys
import threading
import time

def no_network(*args, **kwargs):
    raise AssertionError("regression tests must remain offline")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
sys.path[:0] = sys.argv[1:3]
root, order = Path(sys.argv[3]), sys.argv[4]
names = ["research_workspace", "deerflow_bridge.research_workspace"]
modules, failures = {}, []
if order == "concurrent":
    barrier = threading.Barrier(2)
    def load(name):
        try:
            barrier.wait(3)
            modules[name] = importlib.import_module(name)
        except BaseException as exc:
            failures.append(exc)
    threads = [threading.Thread(target=load, args=(name,), daemon=True) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads), "cold import deadlocked"
    assert not failures, failures
else:
    for name in names if order == "bare-first" else reversed(names):
        modules[name] = importlib.import_module(name)
bare, package = (modules[name] for name in names)
assert bare.ResearchWorkspaceError is package.ResearchWorkspaceError, "split workspace errors"
assert bare.ResearchArtifactNotFoundError is package.ResearchArtifactNotFoundError
assert issubclass(bare.ResearchArtifactNotFoundError, package.ResearchWorkspaceError)

from deerflow_bridge import research_synthesis as synthesis
assert synthesis.ResearchWorkspaceError is bare.ResearchWorkspaceError
for index, module in enumerate((bare, package)):
    workspace = module.ResearchWorkspace(root / str(index), {"model": "offline"})
    fence = synthesis._Fence(time.monotonic() + 5)
    def invoke(task):
        # Obtain the real module's typed storage failure, rather than replacing
        # the callback with an artificial exception class or workspace double.
        workspace.read_artifact({})
    try:
        synthesis._call_review(invoke, {"label": "evidence-and-citations"}, fence)
    except package.ResearchWorkspaceError as exc:
        assert fence.error is exc and fence.closed
    else:
        raise AssertionError("workspace failure was downgraded to reviewer unavailability")
print("shared workspace errors propagate and abort")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(ROOT), str(BRIDGE), str(tmp_path), order],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "shared workspace errors propagate and abort"
