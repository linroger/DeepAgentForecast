"""Offline exact-provider admission checks for a failed research compaction.

The tracked overlay is imported directly; the assembled DeerFlow runtime and
its provider configuration are never used. Fake permits model a sibling failure
while waiting for capacity and an already-running call finishing after failure.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def admission(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "deerflow_bridge"))
    source = root / "deerflow_bridge/patches/middlewares/model_concurrency_middleware.py"
    spec = importlib.util.spec_from_file_location("_astra_provider_admission", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    # Inject a deterministic sibling stop independently of SQLite, credentials,
    # and the exact shared-latch implementation. The integration test below
    # additionally exercises the real compaction module's latch.
    from research_compaction import ResearchCompactionError

    stopped = []

    def check():
        if stopped:
            raise stopped[0]

    def stop():
        stopped.append(ResearchCompactionError("summary_failed", "sibling-thread"))

    monkeypatch.setattr(
        module, "_research_compaction",
        SimpleNamespace(raise_if_compaction_stopped=check), raising=False,
    )
    return module, stop, ResearchCompactionError


def _fake_budget(events, *, on_acquire=lambda: None):
    @contextmanager
    def lease(weight):
        assert weight == 1
        events.append("wait")
        on_acquire()
        try:
            yield
        finally:
            events.append("release")

    @asynccontextmanager
    async def async_lease(weight):
        assert weight == 1
        events.append("wait")
        await asyncio.sleep(0)
        on_acquire()
        try:
            yield
        finally:
            events.append("release")

    return SimpleNamespace(model_call_lease=lease, async_model_call_lease=async_lease)


def _call(module, mode, handler):
    middleware = module.ModelConcurrencyMiddleware()
    if mode == "sync":
        return middleware.wrap_model_call(None, lambda request: handler())

    async def async_handler(request):
        await asyncio.sleep(0)
        return handler()

    return asyncio.run(middleware.awrap_model_call(None, async_handler))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("budget_enabled", [False, True])
def test_stopped_run_cannot_wait_or_call_provider(admission, monkeypatch, mode, budget_enabled):
    module, stop, error = admission
    events = []
    monkeypatch.setattr(module, "_research_budget", _fake_budget(events) if budget_enabled else None)
    stop()

    with pytest.raises(error):
        _call(module, mode, lambda: events.append("provider"))

    assert events == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_stop_while_waiting_releases_permit_without_calling_provider(admission, monkeypatch, mode):
    module, stop, error = admission
    events = []
    monkeypatch.setattr(module, "_research_budget", _fake_budget(events, on_acquire=stop))

    with pytest.raises(error):
        _call(module, mode, lambda: events.append("provider"))

    assert events == ["wait", "release"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("budget_enabled", [False, True])
def test_inflight_completion_cannot_hide_sibling_stop_or_start_another_call(admission, monkeypatch, mode, budget_enabled):
    module, stop, error = admission
    events = []
    monkeypatch.setattr(module, "_research_budget", _fake_budget(events) if budget_enabled else None)

    def provider():
        events.append("provider")
        stop()
        return "completed while sibling failed"

    with pytest.raises(error):
        _call(module, mode, provider)
    with pytest.raises(error):
        _call(module, mode, provider)

    assert events == (["wait", "provider", "release"] if budget_enabled else ["provider"])


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("budget_enabled", [False, True])
def test_healthy_run_preserves_result_and_releases_permit(admission, monkeypatch, mode, budget_enabled):
    module, _stop, _error = admission
    events = []
    monkeypatch.setattr(module, "_research_budget", _fake_budget(events) if budget_enabled else None)

    def provider():
        events.append("provider")
        return "healthy result"

    assert _call(module, mode, provider) == "healthy result"
    assert events == (["wait", "provider", "release"] if budget_enabled else ["provider"])


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_ordinary_deerflow_without_compaction_module_still_runs(admission, monkeypatch, mode):
    module, _stop, _error = admission
    monkeypatch.setattr(module, "_research_compaction", None)
    monkeypatch.setattr(module, "_research_budget", None)
    assert _call(module, mode, lambda: "ordinary deployment") == "ordinary deployment"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_real_shared_latch_stops_provider_and_reset_admits_a_new_run(admission, monkeypatch, mode):
    import research_compaction

    module, _stop, error = admission
    events = []
    monkeypatch.setattr(module, "_research_compaction", research_compaction)
    monkeypatch.setattr(module, "_research_budget", _fake_budget(events))
    research_compaction.reset_compaction_stop()
    try:
        research_compaction.stop_after_compaction_failure(error("summary_failed", "failed-thread"))
        with pytest.raises(error) as caught:
            _call(module, mode, lambda: events.append("provider"))
        assert caught.value.code == "research_compaction_failed"
        assert caught.value.thread_id == "failed-thread"
        assert events == []

        # Reset represents explicit top-level run admission, not a worker retry.
        research_compaction.reset_compaction_stop()
        assert _call(module, mode, lambda: "new run") == "new run"
        assert events == ["wait", "release"]
    finally:
        research_compaction.reset_compaction_stop()
