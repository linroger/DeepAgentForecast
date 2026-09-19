"""Transferable CLI ownership across coordinator, actor and synthesis workers.

The parent resolves its configured cache root and wraps the whole CLI invocation
in ``invocation_scope(root)``. Every producer callback enters ``producer_scope``
before doing work, including callbacks running in native worker threads. The
owner's dedicated sibling workspace uses a constant identity so changed models,
questions or lanes cannot bypass a timed-out invocation's execution lease.

Exit closes admission without waiting. Each admitted producer owns a Future
until its finally block runs; the last completion releases the deferred lease
and only then clears the process registry. A stuck producer intentionally keeps
ownership until process exit. With no invocation, isolated/legacy producers are
unchanged. Callers must finish wrapping callbacks before starting an invocation;
already running unregistered work cannot retroactively acquire this protection.
"""
from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from types import ModuleType

SCHEMA_VERSION = "research-invocation/v1"


def _shared_state():
    # Bare/package imports can execute concurrently under different import
    # locks. Publish a fully initialized candidate with one atomic setdefault,
    # rather than creating a second active-owner slot or replacing an owner on
    # import/reload. Workers deliberately share this process-wide state instead
    # of thread-local/context-variable state.
    candidate = ModuleType("_drf_research_invocation_state_v1")
    candidate.guard = threading.Lock()
    candidate.import_guard = threading.Lock()
    candidate.active = None
    return sys.modules.setdefault(candidate.__name__, candidate)


_STATE = _shared_state()

# The compaction helper aliases its module names after initialization. Importing
# both names concurrently can expose its unfinished bare module through that
# alias. Serialize our dependency imports as well, without holding admission's
# short lock while importing modules or touching the filesystem.
with _STATE.import_guard:
    if __package__:
        from .research_compaction import ResearchCompactionError
        from .research_workspace import ResearchWorkspace
    else:
        from research_compaction import ResearchCompactionError
        from research_workspace import ResearchWorkspace


class _Owner:
    def __init__(self):
        self.accepting = False
        self.pending: set[Future] = set()
        self.exited = False
        self.finishing = False
        # This aggregate Future transfers the lease to all registered producers.
        # It completes only after execution_lock's context has exited, ensuring
        # its synchronous callback releases flock before the registry is reset.
        self.drained = Future()


def _finish_if_drained(owner):
    with _STATE.guard:
        if not owner.exited or owner.pending or owner.finishing:
            return
        owner.finishing = True
    # Future callbacks may acquire their own locks; never run them under the
    # admission lock. The lease callback runs synchronously before this returns.
    owner.drained.set_result(None)
    with _STATE.guard:
        if _STATE.active is owner:
            _STATE.active = None


@contextmanager
def invocation_scope(root):
    """Own one CLI invocation; reject overlap with a sanitized typed stop.

    ``root`` is the resolved cache path supplied by the parent, not a new run or
    per-lane directory. Its sibling ``<root.name>.owner`` is reserved exclusively
    for execution ownership. Contenders never change the existing owner's stop
    latch, producers or registry. Body exceptions propagate unchanged.
    """
    owner = _Owner()
    with _STATE.guard:
        if _STATE.active is not None:
            raise ResearchCompactionError("checkpoint_unavailable") from None
        # Reserve before filesystem work so a contender cannot steal ownership
        # during acquisition. Producers fail closed until the OS lease is held.
        _STATE.active = owner

    acquired = False
    try:
        try:
            cache_root = Path(root).expanduser().resolve()
            workspace = ResearchWorkspace(
                cache_root.parent / (cache_root.name + ".owner"),
                {"schema_version": SCHEMA_VERSION},
            )
            lock = workspace.execution_lock()
            lease = lock.__enter__()
            acquired = True
        except Exception:
            # Do not expose cache paths, storage diagnostics or provider text.
            # In particular, do not latch a contender's stop into the active run.
            raise ResearchCompactionError("checkpoint_unavailable") from None
    finally:
        if not acquired:
            with _STATE.guard:
                if _STATE.active is owner:
                    _STATE.active = None

    with _STATE.guard:
        owner.accepting = True
    try:
        yield
    finally:
        with _STATE.guard:
            owner.accepting = False
        try:
            lease.defer_release_until([owner.drained])
            lock.__exit__(None, None, None)
        except Exception:
            # An uncertain release remains reserved, never silently admitting a
            # retry. No cleanup path may clear a different invocation's owner.
            raise ResearchCompactionError("checkpoint_unavailable") from None
        with _STATE.guard:
            owner.exited = True
        _finish_if_drained(owner)


@contextmanager
def producer_scope():
    """Register before producer work, complete in finally; legacy use is a no-op.

    The short admission lock serializes registration with invocation exit. A
    producer either belongs to the still-open invocation or receives a typed
    stop before entering its body. Pending tokens cannot be cancelled by callers.
    """
    with _STATE.guard:
        owner = _STATE.active
        if owner is None:
            token = None
        else:
            if not owner.accepting:
                raise ResearchCompactionError("checkpoint_unavailable") from None
            token = Future()
            owner.pending.add(token)
    try:
        yield
    finally:
        if token is not None:
            token.set_result(None)
            with _STATE.guard:
                owner.pending.remove(token)
            _finish_if_drained(owner)


def submit_producer(executor, callback, *args, **kwargs):
    """Reserve the current owner before dispatch, including pre-entry delays.

    A worker cannot attach itself to a later invocation after a timeout. A
    cancelled queued future and a failed submission release the reservation;
    running futures retain it through callback completion.
    """
    with _STATE.guard:
        owner = _STATE.active
        if owner is not None and not owner.accepting:
            raise ResearchCompactionError("checkpoint_unavailable") from None
        token = Future() if owner is not None else None
        if token is not None:
            owner.pending.add(token)

    if owner is None:
        return executor.submit(callback, *args, **kwargs)

    def release(_future=None):
        if token is not None:
            token.set_result(None)
            with _STATE.guard:
                owner.pending.remove(token)
            _finish_if_drained(owner)

    def invoke():
        if owner is not None:
            with _STATE.guard:
                if _STATE.active is not owner or not owner.accepting:
                    raise ResearchCompactionError("checkpoint_unavailable") from None
        return callback(*args, **kwargs)

    try:
        future = executor.submit(invoke)
    except BaseException:
        release()
        raise
    future.add_done_callback(release)
    return future
