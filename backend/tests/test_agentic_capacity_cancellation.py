"""Real SQLite capacity waits must not strand native async accounting workers."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
import hashlib
import importlib
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import test_agentic_admission_integration as fixtures

active = fixtures.active
native = fixtures.native
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for rel in ("deerflow_bridge/research_budget.py", "deerflow_bridge/research_admission.py",
                "deerflow_bridge/patches/middlewares/model_concurrency_middleware.py",
                "backend/tests/test_agentic_capacity_cancellation.py"):
        record_testsuite_property("capacity_sha256:" + rel, hashlib.sha256((ROOT / rel).read_bytes()).hexdigest())


@pytest.fixture
def capacity(active, native, monkeypatch, tmp_path):
    budget = importlib.import_module("research_budget")
    monkeypatch.setattr(native.concurrency, "_research_budget", budget)
    for key, value in {
        "RESEARCH_MODEL_LEASE_DB": str(tmp_path / "leases.sqlite3"),
        "RESEARCH_MODEL_CONCURRENCY_GLOBAL": "1", "RESEARCH_GLOBAL_SUBAGENT_CAP": "1",
        "RESEARCH_MODEL_LEASE_WAIT_SECONDS": "2", "RESEARCH_SUBAGENT_LEASE_WAIT_SECONDS": "2",
        "RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS": "12000000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RESEARCH_BUDGET_DB", raising=False)
    monkeypatch.delenv("RESEARCH_BUDGET_TELEMETRY_PATH", raising=False)
    return budget


async def until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def scopes(capacity, native, kind):
    if kind == "model":
        return capacity.model_call_lease, native.concurrency.async_provider_model_lease
    return capacity.subagent_call_lease, native.concurrency.async_subagent_lifecycle_lease


@pytest.mark.parametrize("kind", ["model", "subagent"])
def test_cancelled_waiter_stops_database_polling_before_return(capacity, native, monkeypatch, kind):
    sync_scope, async_scope = scopes(capacity, native, kind)
    tag = ContextVar("cancelled_capacity_waiter", default=False)
    polls, sent = [], []
    original = capacity._connect

    def connected(*args, **kwargs):
        if tag.get():
            polls.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(capacity, "_connect", connected)

    async def run():
        with sync_scope():
            async def waiter():
                tag.set(True)
                async with async_scope():
                    sent.append(True)
            task = asyncio.create_task(waiter())
            await until(lambda: polls)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            after_cancel = len(polls)
            await asyncio.sleep(0.4)
            assert len(polls) == after_cancel, "cancelled acquisition still polls in an abandoned worker"
            assert sent == []

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["model", "subagent"])
@pytest.mark.parametrize("cancel_waiters", [False, True])
def test_default_executor_remains_available_for_native_settlement(
    active, native, capacity, monkeypatch, kind, cancel_waiters, record_testsuite_property,
):
    """Regression from the exact 20-worker/5MB/1M-token reviewer reproduction.

    The number of pending lease waiters equals the actual default pool size.
    Each waiter must have attempted SQLite admission before the provider returns.
    Both live and cancelled capacity waiters must leave accounting able to run.
    """
    _, async_scope = scopes(capacity, native, kind)
    tag = ContextVar("capacity_waiter_id", default=None)
    polled, provider_calls = set(), []
    original = capacity._connect

    def connected(*args, **kwargs):
        if tag.get() is not None:
            polled.add(tag.get())
        return original(*args, **kwargs)

    monkeypatch.setattr(capacity, "_connect", connected)
    request = SimpleNamespace(messages=[{"role": "user", "content": "word " * 1_000_000}],
                              tools=[], system_message=None)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(_):
            provider_calls.append(True)
            entered.set()
            await release.wait()
            return {"type": "ai", "usage_metadata": {"input_tokens": 1_000_000,
                                                     "output_tokens": 1, "total_tokens": 1_000_001}}
        async def waiter(index):
            tag.set(index)
            async with async_scope():
                pass

        hold = capacity.subagent_call_lease() if kind == "subagent" else nullcontext()
        with hold:
            admitted = asyncio.create_task(native.concurrency.ModelConcurrencyMiddleware().awrap_model_call(request, handler))
            await asyncio.wait_for(entered.wait(), 3)
            workers = asyncio.get_running_loop()._default_executor._max_workers
            pending = [asyncio.create_task(waiter(i)) for i in range(workers)]
            try:
                await until(lambda: len(polled) == workers, timeout=1.5)
                if cancel_waiters:
                    for task in pending:
                        task.cancel()
                    outcomes = await asyncio.gather(*pending, return_exceptions=True)
                    assert all(isinstance(result, asyncio.CancelledError) for result in outcomes)
                release.set()
                await asyncio.wait_for(asyncio.shield(admitted), 0.75)
            finally:
                release.set()
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.gather(admitted, return_exceptions=True)
        return workers

    workers = asyncio.run(run())
    record_testsuite_property("default_executor_workers", workers)
    record_testsuite_property("prompt_bytes", 5_000_000)
    state = active.admission.snapshot(active.workspace)
    assert provider_calls == [True]
    assert state["spent_input_tokens"] == 1_000_000
    assert state["known_usage_calls"] == 1 and state["held_input_tokens"] == 0
    with sqlite3.connect(capacity._model_db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_leases").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM subagent_leases").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["model", "subagent"])
@pytest.mark.parametrize("stage", ["acquired", "release"])
def test_repeated_cancellation_drains_and_releases_race_winner(capacity, native, monkeypatch, kind, stage):
    _, async_scope = scopes(capacity, native, kind)
    entered, release = threading.Event(), threading.Event()
    sent = []
    target = "export_telemetry" if stage == "acquired" else f"_release_{kind}_lease"
    original = getattr(capacity, target)
    blocked_once = threading.Event()

    def blocked(*args, **kwargs):
        if not blocked_once.is_set():
            blocked_once.set()
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(capacity, target, blocked)

    async def run():
        async def caller():
            async with async_scope():
                sent.append(True)
        task = asyncio.create_task(caller())
        try:
            await until(entered.is_set)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done(), "cancellation abandoned a live acquired permit or release"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    asyncio.run(run())
    assert sent == ([True] if stage == "release" else [])
    with sqlite3.connect(capacity._model_db_path()) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {kind}_leases").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["model", "subagent"])
def test_async_retries_keep_original_deadline(capacity, native, monkeypatch, kind):
    sync_scope, async_scope = scopes(capacity, native, kind)
    monkeypatch.setenv(f"RESEARCH_{kind.upper()}_LEASE_WAIT_SECONDS", "1")

    async def run():
        with sync_scope():
            with pytest.raises(TimeoutError, match="timed out waiting 1s"):
                async with asyncio.timeout(2.5):
                    async with async_scope():
                        pytest.fail("saturated lease must time out without admission")
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["model", "subagent"])
def test_async_wait_metrics_and_weight_remain_one_acquisition(capacity, native, monkeypatch, kind):
    sync_scope, async_scope = scopes(capacity, native, kind)
    observed = threading.Event()
    original = capacity._wait_for_lease_capacity

    def waiting(*args):
        observed.set()
        return original(*args)

    monkeypatch.setattr(capacity, "_wait_for_lease_capacity", waiting)

    async def run():
        async def waiter():
            async with async_scope():
                with sqlite3.connect(capacity._model_db_path()) as conn:
                    assert conn.execute(f"SELECT COUNT(*) FROM {kind}_leases").fetchone()[0] == 1
        with sync_scope():
            task = asyncio.create_task(waiter())
            await until(observed.is_set)
        await task

    asyncio.run(run())
    with sqlite3.connect(capacity._model_db_path()) as conn:
        metrics = dict(conn.execute("SELECT name,value FROM counters WHERE scope='global' AND lane=''"))
        assert metrics[f"{kind}_lease_acquisitions"] == 2
        assert metrics[f"{kind}_lease_waits"] == 1
        assert metrics[f"{kind}_lease_wait_ms"] > 0
        assert conn.execute(f"SELECT COUNT(*) FROM {kind}_leases").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["model", "subagent"])
def test_async_ledger_unavailable_keeps_existing_degraded_semantics(capacity, native, monkeypatch, kind):
    _, async_scope = scopes(capacity, native, kind)

    def unavailable(*args):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(capacity, "_connect", unavailable)

    async def run():
        # Exercise the budget adapter directly because native wrappers discard
        # Admission while retaining its scope and stop checks.
        scope = capacity.async_model_call_lease if kind == "model" else capacity.async_subagent_call_lease
        async with scope() as admitted:
            assert admitted == capacity.Admission(True, "ledger_unavailable", True)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["model", "subagent"])
@pytest.mark.parametrize("outcome", ["provider", "integrity"])
def test_exceptional_cleanup_obeys_cancellation_and_integrity_precedence(active, capacity, native, monkeypatch, kind, outcome):
    _, async_scope = scopes(capacity, native, kind)
    entered, release = threading.Event(), threading.Event()
    original = getattr(capacity, f"_release_{kind}_lease")
    failure = (active.compaction.ResearchCompactionError("checkpoint_unavailable")
               if outcome == "integrity" else RuntimeError("provider failed"))

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(capacity, f"_release_{kind}_lease", blocked)

    async def run():
        async def caller():
            async with async_scope():
                raise failure
        task = asyncio.create_task(caller())
        try:
            await until(entered.is_set)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
            outcomes = await asyncio.gather(task, return_exceptions=True)
        if outcome == "integrity":
            assert outcomes == [failure]
        else:
            assert isinstance(outcomes[0], asyncio.CancelledError)
    asyncio.run(run())
    with sqlite3.connect(capacity._model_db_path()) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {kind}_leases").fetchone()[0] == 0
