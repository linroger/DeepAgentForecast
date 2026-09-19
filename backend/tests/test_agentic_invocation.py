"""Offline CLI ownership acceptance, including real threads and POSIX flock."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"
sys.path.insert(0, str(BRIDGE))

import research_invocation as invocation  # noqa: E402
from research_compaction import (  # noqa: E402
    ResearchCompactionError, get_compaction_stop,
)
from research_workspace import ResearchWorkspace  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for path in (BRIDGE / "research_invocation.py", Path(__file__)):
        record_testsuite_property(
            "invocation_source_sha256:" + path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def child(code, *args):
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path[:0] = sys.argv[1:3];\n" + code,
         str(BRIDGE), str(BRIDGE.parent), *map(str, args)],
        text=True, capture_output=True, timeout=15,
    )


def contender(root, *, raw_lock=False):
    code = """
from pathlib import Path
from research_invocation import invocation_scope
from research_compaction import ResearchCompactionError
from research_workspace import ResearchWorkspace, ResearchWorkspaceError
import json
root = Path(sys.argv[3])
if sys.argv[4] == 'raw':
    owner = root.parent / (root.name + '.owner')
    identity = json.loads((owner / 'identity.json').read_text())['identity']
    scope = ResearchWorkspace(owner, identity).execution_lock()
else:
    scope = invocation_scope(root)
try:
    with scope:
        print('admitted')
except (ResearchCompactionError, ResearchWorkspaceError):
    print('blocked')
"""
    result = child(code, root, "raw" if raw_lock else "invocation")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@contextmanager
def held_producer(*, error=None):
    """Keep a real producer alive until the caller explicitly releases it."""
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def work():
        try:
            with invocation.producer_scope():
                entered.set()
                assert release.wait(10), "producer test did not release its worker"
                if error is not None:
                    raise error
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        assert entered.wait(3), errors
        yield release, finished
    finally:
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert errors == ([] if error is None else [error])


@pytest.mark.parametrize("error", [None, ValueError("body"), KeyboardInterrupt()])
def test_isolated_producer_is_noop_and_preserves_exception(error):
    if error is None:
        with invocation.producer_scope():
            with invocation.producer_scope():
                pass
    else:
        with pytest.raises(type(error)) as caught:
            with invocation.producer_scope():
                raise error
        assert caught.value is error


def test_sibling_owner_constant_identity_does_not_touch_research_root(tmp_path):
    root = tmp_path / "configured-cache"
    identity = None
    for _ in range(2):
        with invocation.invocation_scope(root):
            owner = root.parent / (root.name + ".owner")
            current = json.loads((owner / "identity.json").read_text())["identity"]
            assert current == {"schema_version": "research-invocation/v1"}
            assert identity is None or current == identity
            identity = current
            assert not root.exists()
            assert contender(root) == "blocked"
            assert contender(root, raw_lock=True) == "blocked"
            with invocation.producer_scope():
                pass
        assert contender(root) == "admitted"


@pytest.mark.parametrize("different_root", [False, True])
def test_contender_does_not_steal_or_clear_active_owner(tmp_path, different_root):
    root = tmp_path / "active"
    stop_before = get_compaction_stop()
    with invocation.invocation_scope(root):
        for _ in range(2):
            with pytest.raises(ResearchCompactionError):
                with invocation.invocation_scope(tmp_path / "other" if different_root else root):
                    pytest.fail("contender entered")
            with invocation.producer_scope():
                pass
        assert contender(root, raw_lock=True) == "blocked"
    assert not (tmp_path / "other.owner").exists()
    assert get_compaction_stop() == stop_before
    with invocation.invocation_scope(root):
        pass


@pytest.mark.parametrize("error", [ValueError("body"), KeyboardInterrupt()])
def test_invocation_body_exception_releases_and_preserves_exception(tmp_path, error):
    root = tmp_path / "run"
    with pytest.raises(type(error)) as caught:
        with invocation.invocation_scope(root):
            with invocation.producer_scope():
                raise error
    assert caught.value is error
    assert contender(root) == "admitted"
    with invocation.invocation_scope(root):
        pass


@pytest.mark.parametrize("body_error", [False, True])
def test_exit_defers_same_and_crossprocess_lease_until_all_producers_drain(tmp_path, body_error):
    root = tmp_path / "run"
    scope = invocation.invocation_scope(root)
    scope.__enter__()
    with held_producer() as (release_a, finished_a), held_producer() as (release_b, finished_b):
        start = time.monotonic()
        if body_error:
            assert not scope.__exit__(TimeoutError, TimeoutError("deadline"), None)
        else:
            scope.__exit__(None, None, None)
        assert time.monotonic() - start < 1
        assert not finished_a.is_set() and not finished_b.is_set()
        for _ in range(2):
            with pytest.raises(ResearchCompactionError):
                with invocation.invocation_scope(root):
                    pytest.fail("retry overlapped a producer")
            with pytest.raises(ResearchCompactionError):
                with invocation.producer_scope():
                    pytest.fail("late producer admitted")
        assert contender(root) == "blocked"
        assert contender(root, raw_lock=True) == "blocked"
        release_a.set()
        assert finished_a.wait(3)
        assert contender(root, raw_lock=True) == "blocked"
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(root):
                pytest.fail("last producer is still active")
        release_b.set()
        assert finished_b.wait(3)
        # Joining is deliberately unnecessary: completion releases both owners.
        with invocation.invocation_scope(root):
            assert contender(root, raw_lock=True) == "blocked"
    assert contender(root) == "admitted"


@pytest.mark.parametrize("error", [ValueError("producer"), KeyboardInterrupt()])
def test_exception_in_late_producer_completes_registration(tmp_path, error):
    root = tmp_path / "run"
    scope = invocation.invocation_scope(root)
    scope.__enter__()
    with held_producer(error=error) as (release, finished):
        scope.__exit__(None, None, None)
        release.set()
        assert finished.wait(3)
        with invocation.invocation_scope(root):
            pass
    assert contender(root, raw_lock=True) == "admitted"


def test_producer_registers_future_before_work_and_completes_finally(tmp_path):
    with invocation.invocation_scope(tmp_path / "run"):
        with invocation.producer_scope():
            owner = invocation._STATE.active
            pending = list(owner.pending)
            assert len(pending) == 1 and not pending[0].done()
        assert pending[0].done() and pending[0].result() is None
        assert not owner.pending


def test_parallel_producers_do_not_hold_registry_lock_during_work(tmp_path):
    with invocation.invocation_scope(tmp_path / "run"):
        with held_producer(), held_producer(), held_producer():
            with invocation.producer_scope():
                assert len(invocation._STATE.active.pending) == 4


@pytest.mark.parametrize("first", ["research_invocation", "deerflow_bridge.research_invocation"])
def test_bare_and_package_imports_share_owner_in_both_orders(tmp_path, first):
    result = child("""
import importlib
first = importlib.import_module(sys.argv[4])
second = importlib.import_module('deerflow_bridge.research_invocation'
    if sys.argv[4] == 'research_invocation' else 'research_invocation')
from research_compaction import ResearchCompactionError
assert first._STATE is second._STATE
scope = first.invocation_scope(sys.argv[3])
scope.__enter__()
producer = second.producer_scope()
producer.__enter__()
scope.__exit__(None, None, None)
try:
    with second.invocation_scope(sys.argv[3]):
        raise AssertionError('overlap')
except ResearchCompactionError:
    pass
try:
    with second.producer_scope():
        raise AssertionError('late admission')
except ResearchCompactionError:
    pass
producer.__exit__(None, None, None)
with second.invocation_scope(sys.argv[3]):
    pass
""", tmp_path / "run", first)
    assert result.returncode == 0, result.stderr


def test_acquisition_failure_is_sanitized_and_can_retry(tmp_path, monkeypatch):
    root = tmp_path / "private-secret-location"
    real_workspace = invocation.ResearchWorkspace

    def fail(*args):
        raise OSError("secret-key-and-private-filesystem")

    monkeypatch.setattr(invocation, "ResearchWorkspace", fail)
    with pytest.raises(ResearchCompactionError) as caught:
        with invocation.invocation_scope(root):
            pytest.fail("broken acquisition admitted")
    public_trace = "".join(traceback.format_exception(caught.value))
    assert "secret-key-and-private-filesystem" not in public_trace
    assert "private-secret-location" not in str(caught.value)
    assert caught.value.reason == "checkpoint_unavailable"
    monkeypatch.setattr(invocation, "ResearchWorkspace", real_workspace)
    with invocation.invocation_scope(root):
        pass


def test_interrupted_acquisition_clears_only_its_reservation(tmp_path, monkeypatch):
    real_workspace = invocation.ResearchWorkspace
    interruption = KeyboardInterrupt()

    def fail(*args):
        raise interruption

    monkeypatch.setattr(invocation, "ResearchWorkspace", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        with invocation.invocation_scope(tmp_path / "run"):
            pytest.fail("interrupted acquisition entered")
    assert caught.value is interruption
    monkeypatch.setattr(invocation, "ResearchWorkspace", real_workspace)
    with invocation.invocation_scope(tmp_path / "run"):
        pass


def test_external_owner_blocks_invocation_without_leaving_registry_reservation(tmp_path):
    root = tmp_path / "run"
    workspace = ResearchWorkspace(
        tmp_path / "run.owner", {"schema_version": "research-invocation/v1"},
    )
    with workspace.execution_lock():
        with pytest.raises(ResearchCompactionError) as caught:
            with invocation.invocation_scope(root):
                pytest.fail("external owner bypassed")
        assert caught.value.reason == "checkpoint_unavailable"
        assert "workspace execution lock" not in "".join(traceback.format_exception(caught.value))
        assert invocation._STATE.active is None
        assert contender(root) == "blocked"
    with invocation.invocation_scope(root):
        pass


def test_wrong_owner_identity_fails_closed_without_rewriting_store(tmp_path):
    root = tmp_path / "run"
    owner = root.parent / (root.name + ".owner")
    workspace = ResearchWorkspace(owner, {"schema_version": "foreign-owner"})
    before = (owner / "identity.json").read_bytes()
    with pytest.raises(ResearchCompactionError):
        with invocation.invocation_scope(root):
            pytest.fail("foreign owner adopted")
    assert (owner / "identity.json").read_bytes() == before
    assert workspace.identity == {"schema_version": "foreign-owner"}
    with invocation.invocation_scope(tmp_path / "different"):
        pass


def test_acquiring_owner_rejects_producers_and_contenders(tmp_path, monkeypatch):
    real_workspace = invocation.ResearchWorkspace
    constructing, proceed, entered = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def delayed(*args):
        constructing.set()
        assert proceed.wait(3)
        return real_workspace(*args)

    def run():
        try:
            with invocation.invocation_scope(tmp_path / "run"):
                entered.set()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(invocation, "ResearchWorkspace", delayed)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert constructing.wait(3)
        with pytest.raises(ResearchCompactionError):
            with invocation.producer_scope():
                pytest.fail("producer ran before lock acquisition")
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(tmp_path / "other"):
                pytest.fail("contender stole acquisition")
    finally:
        proceed.set()
        thread.join(3)
    assert not thread.is_alive() and not errors and entered.is_set()


def test_exit_and_producer_admission_race_is_atomic(tmp_path):
    for _ in range(20):
        scope = invocation.invocation_scope(tmp_path / "run")
        scope.__enter__()
        anchor = invocation.producer_scope()
        anchor.__enter__()
        barrier, decided, release = threading.Barrier(2), threading.Event(), threading.Event()
        outcomes = []

        def race(barrier=barrier, decided=decided, release=release, outcomes=outcomes):
            try:
                barrier.wait(timeout=3)
                with invocation.producer_scope():
                    outcomes.append("admitted")
                    decided.set()
                    assert release.wait(3)
            except ResearchCompactionError:
                outcomes.append("closed")
                decided.set()

        thread = threading.Thread(target=race, daemon=True)
        thread.start()
        try:
            barrier.wait(timeout=3)
            scope.__exit__(None, None, None)
            assert decided.wait(3)
            assert outcomes in (["admitted"], ["closed"])
            with pytest.raises(ResearchCompactionError):
                with invocation.producer_scope():
                    pytest.fail("admission reopened")
            with pytest.raises(ResearchCompactionError):
                with invocation.invocation_scope(tmp_path / "run"):
                    pytest.fail("ownership cleared during exit")
        finally:
            release.set()
            thread.join(3)
            anchor.__exit__(None, None, None)
        assert not thread.is_alive()
        with invocation.invocation_scope(tmp_path / "run"):
            pass


def test_last_producer_finishing_during_lock_exit_cannot_clear_registry(tmp_path, monkeypatch):
    """Force completion between closing admission and lease-context teardown."""
    from research_workspace import ExecutionLease

    root = tmp_path / "run"
    scope = invocation.invocation_scope(root)
    scope.__enter__()
    token = invocation.producer_scope()
    token.__enter__()
    exiting, proceed = threading.Event(), threading.Event()
    real_exit = ExecutionLease._context_exited
    errors = []

    def delayed_exit(lease):
        exiting.set()
        assert proceed.wait(3)
        real_exit(lease)

    def close():
        try:
            scope.__exit__(None, None, None)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(ExecutionLease, "_context_exited", delayed_exit)
    thread = threading.Thread(target=close, daemon=True)
    thread.start()
    try:
        assert exiting.wait(3)
        token.__exit__(None, None, None)
        assert invocation._STATE.active is not None
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(root):
                pytest.fail("registry cleared before lease exit")
        with pytest.raises(ResearchCompactionError):
            with invocation.producer_scope():
                pytest.fail("admission reopened before lease exit")
        assert contender(root, raw_lock=True) == "blocked"
    finally:
        proceed.set()
        thread.join(3)
    assert not thread.is_alive() and not errors
    with invocation.invocation_scope(root):
        pass


def test_simultaneous_bare_and_package_imports_share_single_registry(tmp_path):
    for _ in range(4):
        result = child("""
import importlib, threading
barrier = threading.Barrier(2)
modules, failures = [], []
def load(name):
    try:
        barrier.wait(timeout=3)
        modules.append(importlib.import_module(name))
    except BaseException as exc:
        failures.append(exc)
threads = [threading.Thread(target=load, args=(name,)) for name in
           ('research_invocation', 'deerflow_bridge.research_invocation')]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(3)
assert len(modules) == 2 and not failures, failures
a, b = modules
assert a._STATE is b._STATE
assert a.ResearchCompactionError is b.ResearchCompactionError
with a.invocation_scope(sys.argv[3]):
    with b.producer_scope():
        assert len(a._STATE.active.pending) == 1
    try:
        with b.invocation_scope(sys.argv[3]):
            raise AssertionError('duplicate invocation')
    except a.ResearchCompactionError:
        pass
""", tmp_path / "run")
        assert result.returncode == 0, result.stderr


def test_reloaded_module_cannot_reset_or_clear_another_owner(tmp_path):
    with invocation.invocation_scope(tmp_path / "run"):
        old_owner = invocation._STATE.active
        importlib.reload(invocation)
        assert invocation._STATE.active is old_owner
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(tmp_path / "other"):
                pytest.fail("reload stole owner")
        with invocation.producer_scope():
            pass
    with invocation.invocation_scope(tmp_path / "run"):
        invocation._finish_if_drained(old_owner)
        assert invocation._STATE.active is not old_owner
        with pytest.raises(ResearchCompactionError):
            with invocation.invocation_scope(tmp_path / "other"):
                pytest.fail("old completion cleared current owner")
