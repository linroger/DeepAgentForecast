"""Offline regression tests for the LOOP-007 cross-process research budget."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "deerflow_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

import cached_fetch as cf  # noqa: E402
import research_budget as rb  # noqa: E402
import search_tools as st  # noqa: E402

from app.services import pipeline_orchestrator as po  # noqa: E402


def _set_budget_env(
    monkeypatch,
    tmp_path,
    *,
    attempts=100,
    search_global=100,
    search_lane=100,
    fetch_global=100,
    fetch_lane=100,
):
    monkeypatch.setenv("RESEARCH_BUDGET_DB", str(tmp_path / "budget.sqlite3"))
    monkeypatch.setenv(
        "RESEARCH_BUDGET_TELEMETRY_PATH", str(tmp_path / "research_budget.json")
    )
    monkeypatch.setenv("RESEARCH_BUDGET_LANE_ID", "lane-a")
    monkeypatch.setenv("RESEARCH_BUDGET_ATTEMPTS_GLOBAL", str(attempts))
    monkeypatch.setenv("RESEARCH_BUDGET_SEARCH_GLOBAL", str(search_global))
    monkeypatch.setenv("RESEARCH_BUDGET_SEARCH_LANE", str(search_lane))
    monkeypatch.setenv("RESEARCH_BUDGET_FETCH_GLOBAL", str(fetch_global))
    monkeypatch.setenv("RESEARCH_BUDGET_FETCH_LANE", str(fetch_lane))
    monkeypatch.setenv("RESEARCH_NEGATIVE_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("RESEARCH_NEGATIVE_CACHE_RETRIES", "1")


def _multiprocess_admit_worker(db_path, lane, count, queue):
    os.environ["RESEARCH_BUDGET_DB"] = db_path
    os.environ["RESEARCH_BUDGET_LANE_ID"] = lane
    os.environ["RESEARCH_BUDGET_ATTEMPTS_GLOBAL"] = "1000"
    os.environ["RESEARCH_BUDGET_SEARCH_GLOBAL"] = "17"
    os.environ["RESEARCH_BUDGET_SEARCH_LANE"] = "100"
    os.environ.pop("RESEARCH_BUDGET_TELEMETRY_PATH", None)
    allowed = sum(rb.admit_network("search").allowed for _ in range(count))
    queue.put(allowed)


def _multiprocess_model_lease_worker(db_path, queue):
    os.environ.pop("RESEARCH_BUDGET_DB", None)
    os.environ["RESEARCH_MODEL_LEASE_DB"] = db_path
    os.environ["RESEARCH_MODEL_CONCURRENCY_GLOBAL"] = "1"
    os.environ["RESEARCH_MODEL_LEASE_WAIT_SECONDS"] = "10"
    with rb.model_call_lease(1):
        started = time.monotonic()
        time.sleep(0.2)
        queue.put((started, time.monotonic()))


def _multiprocess_subagent_lease_worker(db_path, queue):
    os.environ.pop("RESEARCH_BUDGET_DB", None)
    os.environ["RESEARCH_MODEL_LEASE_DB"] = db_path
    os.environ["RESEARCH_GLOBAL_SUBAGENT_CAP"] = "1"
    os.environ["RESEARCH_SUBAGENT_LEASE_WAIT_SECONDS"] = "10"
    with rb.subagent_call_lease():
        started = time.monotonic()
        time.sleep(0.2)
        queue.put((started, time.monotonic()))


def _multiprocess_request_claim_worker(
        db_path, lane, start_event, release_event, queue):
    os.environ["RESEARCH_BUDGET_DB"] = db_path
    os.environ["RESEARCH_BUDGET_LANE_ID"] = lane
    os.environ.pop("RESEARCH_BUDGET_TELEMETRY_PATH", None)
    start_event.wait(10)
    token = rb.claim_request("fetch", "https://example.test/global-exact")
    queue.put(bool(token))
    if token:
        release_event.wait(10)
        rb.release_request(token)


def test_multiprocess_global_cap_is_atomic(tmp_path):
    """Four real processes racing for 40 slots can atomically obtain only 17."""
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_multiprocess_admit_worker,
            args=(str(tmp_path / "shared.sqlite3"), f"lane-{i}", 10, queue),
        )
        for i in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert sum(queue.get(timeout=2) for _ in processes) == 17


def test_model_lease_is_shared_across_independent_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    db_path = str(tmp_path / "application-model-leases.sqlite3")
    processes = [
        ctx.Process(target=_multiprocess_model_lease_worker, args=(db_path, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    intervals = sorted(queue.get(timeout=2) for _ in processes)
    assert intervals[1][0] >= intervals[0][1] - 0.01


def test_subagent_lease_is_shared_across_independent_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    db_path = str(tmp_path / "application-subagent-leases.sqlite3")
    processes = [
        ctx.Process(
            target=_multiprocess_subagent_lease_worker,
            args=(db_path, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    intervals = sorted(queue.get(timeout=2) for _ in processes)
    assert intervals[1][0] >= intervals[0][1] - 0.01


def test_exact_request_claim_is_global_across_outer_lanes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    start_event = ctx.Event()
    release_event = ctx.Event()
    db_path = str(tmp_path / "global-request-claims.sqlite3")
    processes = [
        ctx.Process(
            target=_multiprocess_request_claim_worker,
            args=(db_path, f"outer-lane-{i}", start_event, release_event, queue),
        )
        for i in range(4)
    ]
    for process in processes:
        process.start()
    start_event.set()
    ownership = [queue.get(timeout=10) for _ in processes]
    release_event.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    assert ownership.count(True) == 1
    assert ownership.count(False) == 3


def test_weighted_model_lease_serializes_calls_above_global_cap(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "3")
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_WAIT_SECONDS", "5")
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_TTL_SECONDS", "15")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with rb.model_call_lease(2):
            first_entered.set()
            assert release_first.wait(3)

    def second():
        with rb.model_call_lease(2):
            second_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    assert first_entered.wait(2)
    t2.start()
    time.sleep(0.25)
    assert not second_entered.is_set()
    release_first.set()
    t1.join(3)
    t2.join(3)
    assert not t1.is_alive() and not t2.is_alive()
    assert second_entered.is_set()

    telemetry = rb.export_telemetry(force=True)
    assert telemetry["limits"]["model_concurrency_global"] == 3
    assert telemetry["model_concurrency"]["peak_weight"] == 2
    assert (
        telemetry["lease_application_lifetime"]["global"]
        ["model_lease_waits"] == 1
    )


def test_model_lease_remains_active_when_tool_budget_is_disabled(
        monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_BUDGET_DB", raising=False)
    monkeypatch.delenv("RESEARCH_BUDGET_TELEMETRY_PATH", raising=False)
    model_db = tmp_path / "model-leases.sqlite3"
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(model_db))
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "1")

    assert rb.enabled() is False
    assert rb.model_leases_enabled() is True
    with rb.model_call_lease(1):
        with sqlite3.connect(model_db) as conn:
            assert conn.execute(
                "SELECT COALESCE(SUM(weight), 0) FROM model_leases"
            ).fetchone()[0] == 1
    with sqlite3.connect(model_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_leases").fetchone()[0] == 0


def test_model_lease_rejects_weight_larger_than_capacity(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(tmp_path / "leases.sqlite3"))
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "2")

    with pytest.raises(ValueError, match="exceeds global capacity"):
        with rb.model_call_lease(5):
            raise AssertionError("oversized lease must not enter")


def test_async_model_lease_uses_same_capacity_and_releases(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(tmp_path / "leases.sqlite3"))
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "1")

    async def run():
        async with rb.async_model_call_lease(1):
            await asyncio.sleep(0)

    asyncio.run(run())
    with sqlite3.connect(tmp_path / "leases.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_leases").fetchone()[0] == 0


def test_expired_model_lease_is_reclaimed(monkeypatch, tmp_path):
    model_db = tmp_path / "leases.sqlite3"
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(model_db))
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "1")
    with rb._connect(str(model_db)) as conn:
        conn.execute(
            """INSERT INTO model_leases(
                   token, lane, pid, thread_id, weight, acquired_at, expires_at
               ) VALUES ('dead', 'old', 1, 1, 1, 0, 0)"""
        )

    with rb.model_call_lease(1):
        pass

    with sqlite3.connect(model_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_leases").fetchone()[0] == 0


def test_subagent_lifecycle_cap_waits_and_releases(monkeypatch, tmp_path):
    model_db = tmp_path / "leases.sqlite3"
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(model_db))
    monkeypatch.setenv("RESEARCH_GLOBAL_SUBAGENT_CAP", "1")
    monkeypatch.setenv("RESEARCH_SUBAGENT_LEASE_WAIT_SECONDS", "5")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with rb.subagent_call_lease():
            first_entered.set()
            assert release_first.wait(3)

    def second():
        with rb.subagent_call_lease():
            second_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    assert first_entered.wait(2)
    t2.start()
    time.sleep(0.25)
    assert not second_entered.is_set()
    release_first.set()
    t1.join(3)
    t2.join(3)
    assert not t1.is_alive() and not t2.is_alive()
    assert second_entered.is_set()
    with sqlite3.connect(model_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM subagent_leases"
        ).fetchone()[0] == 0


def test_distinct_tool_and_application_ledgers_merge_without_misattribution(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    model_db = tmp_path / "application-leases.sqlite3"
    monkeypatch.setenv("RESEARCH_MODEL_LEASE_DB", str(model_db))
    monkeypatch.setenv("RESEARCH_MODEL_CONCURRENCY_GLOBAL", "2")
    monkeypatch.setenv("RESEARCH_GLOBAL_SUBAGENT_CAP", "2")

    assert rb.admit_attempt("search").allowed
    with rb.model_call_lease():
        with rb.subagent_call_lease():
            telemetry = rb.export_telemetry(force=True)

    assert telemetry["global"]["attempts"] == 1
    assert "model_lease_acquisitions" not in telemetry["global"]
    application = telemetry["lease_application_lifetime"]["global"]
    assert application["model_lease_acquisitions"] == 1
    assert application["subagent_lease_acquisitions"] == 1
    assert telemetry["model_concurrency"]["scope"] == "application"
    assert telemetry["model_concurrency"]["active_weight"] == 1
    assert telemetry["subagent_concurrency"]["active_leases"] == 1


def test_research_runner_forwards_configured_global_subagent_cap(
        monkeypatch, tmp_path):
    deerflow_dir = tmp_path / "deer-flow"
    deerflow_dir.mkdir()
    (deerflow_dir / "deerflow_research.py").write_text(
        "# test entrypoint\n", encoding="utf-8")
    handoff_dir = tmp_path / "handoff"
    handoff_dir.mkdir()
    (handoff_dir / "research_report.md").write_text(
        "research evidence " * 40, encoding="utf-8")
    captured = {}

    class CompletedProcess:
        pid = 4321
        stdout = []

        @staticmethod
        def poll():
            return 0

        @staticmethod
        def wait(timeout=None):
            return 0

    def fake_popen(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return CompletedProcess()

    monkeypatch.setattr(po.Config, "DEERFLOW_DIR", str(deerflow_dir))
    monkeypatch.setattr(po.Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SUBAGENT_CAP", 2)
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _path: None)
    monkeypatch.setattr(po.subprocess, "Popen", fake_popen)

    po.DeerFlowResearchRunner.run(
        "forecast question",
        str(handoff_dir),
        on_progress=lambda _pct, _message: None,
        timeout=10,
    )

    assert captured["env"]["RESEARCH_GLOBAL_SUBAGENT_CAP"] == "2"


def test_lane_and_global_caps_are_both_enforced(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path, search_global=3, search_lane=2)
    assert rb.admit_network("search").allowed
    assert rb.admit_network("search").allowed
    lane_denial = rb.admit_network("search")
    assert not lane_denial.allowed and lane_denial.reason == "search_lane"

    monkeypatch.setenv("RESEARCH_BUDGET_LANE_ID", "lane-b")
    assert rb.admit_network("search").allowed
    global_denial = rb.admit_network("search")
    assert not global_denial.allowed and global_denial.reason == "search_global"


def test_attempt_cap_is_shared_across_tool_kinds(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path, attempts=2)
    assert rb.admit_attempt("search").allowed
    assert rb.admit_attempt("fetch").allowed
    denied = rb.admit_attempt("search")
    assert not denied.allowed and denied.reason == "attempts_global"


def test_negative_cache_allows_exactly_one_retry_then_expires(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    key = "ddg\nexact query\n10"
    rb.record_negative("search", key)
    assert rb.negative_suppressed("search", key) is False  # claims sole retry
    assert rb.negative_suppressed("search", key) is True

    with sqlite3.connect(str(tmp_path / "budget.sqlite3")) as conn:
        conn.execute("UPDATE negative_results SET last_failure=last_failure-1000")
    assert rb.negative_suppressed("search", key) is False


def test_search_cache_hit_spends_attempt_not_network(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path, search_global=0, search_lane=0)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", "6")
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_DIR", str(tmp_path / "search-cache"))
    calls = {"delegate": 0}

    class _Tool:
        @staticmethod
        def func(query, max_results=10):
            calls["delegate"] += 1
            return json.dumps({"results": [{"title": "network"}]})

    monkeypatch.setattr(
        st, "_load_search_module", lambda _provider: type("M", (), {"web_search_tool": _Tool})()
    )
    query = "cached query"
    cache_path = st._search_cache_path(
        st._search_cache_root(), st._search_cache_key("ddg", query, 10)
    )
    cached = json.dumps({"results": [{"title": "cached"}]})
    st._write_search_cache(cache_path, "ddg", query, cached)

    assert st.web_search_impl(query, 10) == cached
    assert calls["delegate"] == 0
    telemetry = rb.export_telemetry(force=True)
    assert telemetry["global"]["search_attempts"] == 1
    assert telemetry["global"].get("search_network", 0) == 0

    denied = json.loads(st.web_search_impl("uncached query", 10))
    assert denied["error"] == "research_budget_exhausted"
    assert calls["delegate"] == 0


def test_search_exact_no_result_gets_one_delegate_retry(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", "0")
    calls = {"delegate": 0}

    class _Tool:
        @staticmethod
        def func(query, max_results=10):
            calls["delegate"] += 1
            return '{"results": []}'

    monkeypatch.setattr(
        st, "_load_search_module", lambda _provider: type("M", (), {"web_search_tool": _Tool})()
    )
    assert json.loads(st.web_search_impl("empty", 10))["results"] == []
    assert json.loads(st.web_search_impl("empty", 10))["results"] == []
    third = json.loads(st.web_search_impl("empty", 10))
    assert third["error"] == "research_negative_cache_suppressed"
    assert calls["delegate"] == 2


def test_fetch_cache_hit_and_denial_never_invoke_delegate(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path, fetch_global=0, fetch_lane=0)
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "72")
    cached_url = "https://example.test/cached"
    cached_body = "C" * 500
    cf._write_cache(cf._cache_path(cf._cache_root(), cached_url), cached_url, cached_body)
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        return "N" * 500

    assert asyncio.run(cf.cached_fetch(cached_url, delegate)) == cached_body
    denied = json.loads(asyncio.run(cf.cached_fetch("https://example.test/new", delegate)))
    assert denied["error"] == "research_budget_exhausted"
    assert calls["delegate"] == 0


def test_fetch_dead_result_gets_one_delegate_retry(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "0")
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        return "Error: exact dead fetch"

    url = "https://example.test/dead"
    assert asyncio.run(cf.cached_fetch(url, delegate)).startswith("Error:")
    assert asyncio.run(cf.cached_fetch(url, delegate)).startswith("Error:")
    third = json.loads(asyncio.run(cf.cached_fetch(url, delegate)))
    assert third["error"] == "research_negative_cache_suppressed"
    assert calls["delegate"] == 2


def test_successful_fetch_provenance_survives_isolated_lanes(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    url = "https://example.test/primary-evidence"
    body = "# Primary evidence\n\n" + "auditable source content " * 30

    rb.record_fetched_source(url, body, provider="exa")
    lane_a = rb.list_fetched_sources()
    monkeypatch.setenv("RESEARCH_BUDGET_LANE_ID", "lane-b")
    rb.record_fetched_source(url, body, provider="exa")
    lane_b = rb.list_fetched_sources()
    all_lanes = rb.list_fetched_sources("*")

    assert [row["url"] for row in lane_a] == [url]
    assert [row["url"] for row in lane_b] == [url]
    assert len(all_lanes) == 2
    assert all(row["content_sha256"] for row in all_lanes)
    assert all(row["provider"] == "exa" for row in all_lanes)
    assert all(row["receipt_id"] for row in all_lanes)
    telemetry = rb.export_telemetry(force=True)
    assert telemetry["fetched_source_provenance"] == {
        "distinct_urls": 1,
        "lane_rows": 2,
    }


def test_shared_provider_circuit_opens_and_success_resets(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_PROVIDER_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("RESEARCH_PROVIDER_COOLDOWN_SECONDS", "60")

    assert rb.record_provider_transport_failure("jina", "ConnectTimeout") is False
    assert rb.provider_circuit_open("jina") is False
    assert rb.record_provider_transport_failure("jina", "ReadTimeout") is True
    assert rb.provider_circuit_open("jina") is True

    rb.record_provider_success("jina")
    assert rb.provider_circuit_open("jina") is False
    telemetry = rb.export_telemetry(force=True)
    assert telemetry["provider_health"]["jina"][
        "total_transport_failures"
    ] == 2
    assert telemetry["provider_health"]["jina"]["circuit_open"] is False


def test_jina_transport_failure_falls_back_and_open_circuit_skips_primary(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_PROVIDER_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("EXA_API_KEY", "test-key-not-used")
    calls = {"jina": 0, "exa": 0}

    async def failed_jina(_url):
        calls["jina"] += 1
        return "Error: Request to Jina API failed: ConnectTimeout"

    async def exa(_url):
        calls["exa"] += 1
        return "# Exa evidence\n\n" + "fallback source content " * 30

    monkeypatch.setattr(cf, "_jina_delegate_fetch", failed_jina)
    monkeypatch.setattr(cf, "_exa_fetch", exa)

    first = asyncio.run(cf._resilient_fetch("https://example.test/a"))
    second = asyncio.run(cf._resilient_fetch("https://example.test/b"))

    assert first.startswith("# Exa evidence")
    assert second.startswith("# Exa evidence")
    assert calls == {"jina": 1, "exa": 2}
    assert rb.provider_circuit_open("jina") is True


def test_failover_counts_each_physical_provider_request(monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key-not-used")
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))

    async def failed_jina(_url):
        return "Error: Request to Jina API failed: ConnectTimeout"

    async def exa(_url):
        return "# Exa evidence\n\n" + "fallback source content " * 30

    monkeypatch.setattr(cf, "_jina_delegate_fetch", failed_jina)
    monkeypatch.setattr(cf, "_exa_fetch", exa)
    result = asyncio.run(cf.cached_fetch("https://example.test/failover", cf._resilient_fetch))

    assert result.startswith("# Exa evidence")
    telemetry = rb.export_telemetry(force=True)
    assert telemetry["global"]["fetch_network"] == 2
    receipt = rb.list_fetched_sources()[0]
    assert receipt["provider"] == "exa"
    assert receipt["receipt_id"]


def test_access_denied_body_is_not_positive_evidence():
    assert cf._is_cacheable("Access denied. " + "blocked " * 80) is False


def test_repeated_successful_fetch_returns_compact_artifact_reference(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_COMPACT_REPEAT_RESULTS", "true")
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "72")
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        return "full source evidence " * 30

    url = "https://example.test/repeat"
    first = asyncio.run(cf.cached_fetch(url, delegate))
    second = json.loads(asyncio.run(cf.cached_fetch(url, delegate)))
    revisited = asyncio.run(
        cf.cached_fetch(url, delegate, revisit_reason="verify changed date"))

    assert first.startswith("full source evidence")
    assert second["status"] == "already_available"
    assert second["artifact_id"].startswith("fetch:")
    assert revisited == first
    assert calls["delegate"] == 1


def test_concurrent_identical_fetches_singleflight_to_one_delegate(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "72")
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        await asyncio.sleep(0.1)
        return "concurrent full source evidence " * 30

    async def run():
        return await asyncio.gather(
            cf.cached_fetch("https://example.test/singleflight", delegate),
            cf.cached_fetch("https://example.test/singleflight", delegate),
        )

    first, second = asyncio.run(run())
    payloads = [first, second]
    assert calls["delegate"] == 1
    assert sum(text.startswith("concurrent full source") for text in payloads) == 2
    assert first == second


def test_stale_fetch_cache_ignores_positive_compaction_ledger(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_COMPACT_REPEAT_RESULTS", "true")
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "1")
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        return ("first source evidence " if calls["delegate"] == 1
                else "refreshed source evidence ") * 30

    url = "https://example.test/stale-positive"
    first = asyncio.run(cf.cached_fetch(url, delegate))
    cache_path = cf._cache_path(cf._cache_root(), url)
    payload = json.loads(open(cache_path, encoding="utf-8").read())
    payload["fetched_at"] = time.time() - 2 * 3600
    open(cache_path, "w", encoding="utf-8").write(json.dumps(payload))
    refreshed = asyncio.run(cf.cached_fetch(url, delegate))

    assert first.startswith("first source")
    assert refreshed.startswith("refreshed source")
    assert calls["delegate"] == 2


def test_concurrent_dead_fetches_share_one_retry_then_suppress(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "fetch-cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "72")
    calls = {"delegate": 0}

    async def delegate(_url):
        calls["delegate"] += 1
        await asyncio.sleep(0.05)
        return "Error: exact dead fetch"

    async def run():
        return await asyncio.gather(*[
            cf.cached_fetch("https://example.test/dead-singleflight", delegate)
            for _ in range(3)
        ])

    payloads = asyncio.run(run())
    parsed = [json.loads(value) for value in payloads if value.startswith("{")]
    assert calls["delegate"] == 2
    assert len([value for value in payloads if value.startswith("Error:")]) == 2
    assert [item["error"] for item in parsed] == [
        "research_negative_cache_suppressed"]


def test_repeated_successful_search_returns_compact_artifact_reference(
        monkeypatch, tmp_path):
    _set_budget_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEARCH_COMPACT_REPEAT_RESULTS", "true")
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_DIR", str(tmp_path / "search-cache"))
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", "1")
    calls = {"delegate": 0}

    class Tool:
        @staticmethod
        def func(query, max_results=10):
            calls["delegate"] += 1
            return json.dumps({"results": [{"title": query, "url": "https://x.test"}]})

    monkeypatch.setattr(
        st,
        "_load_search_module",
        lambda _provider: type("M", (), {"web_search_tool": Tool})(),
    )
    first = json.loads(st.web_search_impl("distinct query", 10))
    second = json.loads(st.web_search_impl("distinct query", 10))
    revisited = json.loads(st.web_search_impl(
        "distinct query", 10, revisit_reason="refresh ranking"))

    assert first["results"]
    assert second["status"] == "already_available"
    assert second["artifact_id"].startswith("search:")
    assert revisited == first
    assert calls["delegate"] == 1


def test_ledger_failure_fails_open_and_marks_telemetry_degraded(monkeypatch, tmp_path):
    bad_db = tmp_path / "is-a-directory"
    bad_db.mkdir()
    monkeypatch.setenv("RESEARCH_BUDGET_DB", str(bad_db))
    monkeypatch.setenv(
        "RESEARCH_BUDGET_TELEMETRY_PATH", str(tmp_path / "research_budget.json")
    )
    admission = rb.admit_network("search")
    assert admission.allowed and admission.degraded
    telemetry = json.loads((tmp_path / "research_budget.json").read_text())
    assert telemetry["degraded"] is True
    assert telemetry["degradation"]


def test_orchestrator_injects_shared_paths_lane_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_ENABLED", True, raising=False)
    env = {}
    po._configure_research_budget_env(
        env,
        str(tmp_path / "track_2"),
        budget_db_path=str(tmp_path / "shared.sqlite3"),
        budget_lane_id="outer-track-2",
        budget_telemetry_path=str(tmp_path / "research_budget.json"),
        budget_run_id="pipe-123",
    )
    assert env["RESEARCH_BUDGET_DB"] == str(tmp_path / "shared.sqlite3")
    assert env["RESEARCH_BUDGET_TELEMETRY_PATH"] == str(
        tmp_path / "research_budget.json"
    )
    assert env["RESEARCH_BUDGET_LANE_ID"] == "outer-track-2"
    assert env["RESEARCH_BUDGET_RUN_ID"] == "pipe-123"
    assert env["RESEARCH_BUDGET_ATTEMPTS_GLOBAL"] == "1800"
    assert env["RESEARCH_BUDGET_SEARCH_GLOBAL"] == "900"
    assert env["RESEARCH_BUDGET_SEARCH_LANE"] == "360"
    assert env["RESEARCH_BUDGET_FETCH_GLOBAL"] == "450"
    assert env["RESEARCH_BUDGET_FETCH_LANE"] == "180"
    assert env["RESEARCH_NEGATIVE_CACHE_TTL_SECONDS"] == "600"
    assert env["RESEARCH_NEGATIVE_CACHE_RETRIES"] == "1"


def test_research_budget_epoch_is_stable_per_task_and_fresh_per_resume(
        monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_MAX_EPOCHS", 3, raising=False)
    saves = []
    monkeypatch.setattr(
        po.PipelineManager,
        "save",
        classmethod(lambda cls, state: saves.append(state.to_dict())),
    )
    state = po.PipelineState(
        pipeline_id="pipe-epoch",
        task_id="task-a",
        prompt="forecast",
        handoff_dir=str(tmp_path),
    )

    first = po._activate_research_budget_epoch(state, str(tmp_path))
    same_task = po._activate_research_budget_epoch(state, str(tmp_path))
    assert same_task == first
    assert Path(first[0]).parent.name.startswith("epoch-001-task-a")

    state.task_id = "task-b"
    second = po._activate_research_budget_epoch(state, str(tmp_path))
    assert second != first
    assert Path(second[0]).parent.name.startswith("epoch-002-task-b")
    history = state.options["research_budget_epochs"]
    assert [row["task_id"] for row in history] == ["task-a", "task-b"]
    assert saves


def test_research_budget_epoch_cap_prevents_unbounded_resume_spend(
        monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_MAX_EPOCHS", 1, raising=False)
    monkeypatch.setattr(
        po.PipelineManager, "save", classmethod(lambda cls, state: None))
    state = po.PipelineState(
        pipeline_id="pipe-capped",
        task_id="task-a",
        prompt="forecast",
        handoff_dir=str(tmp_path),
    )
    po._activate_research_budget_epoch(state, str(tmp_path))
    state.task_id = "task-b"

    with pytest.raises(RuntimeError, match="budget epoch limit"):
        po._activate_research_budget_epoch(state, str(tmp_path))


def test_synthesis_recovery_reuses_unexhausted_epoch_at_cap(
        monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_MAX_EPOCHS", 1, raising=False)
    saves = []
    monkeypatch.setattr(
        po.PipelineManager,
        "save",
        classmethod(lambda cls, state: saves.append(state.to_dict())),
    )
    state = po.PipelineState(
        pipeline_id="pipe-synthesis-reuse",
        task_id="task-a",
        prompt="forecast",
        handoff_dir=str(tmp_path),
    )
    first = po._activate_research_budget_epoch(state, str(tmp_path))
    state.options["synthesis_recovery"] = {
        "attempted": True,
        "passed": False,
    }
    state.task_id = "task-b"
    Path(first[1]).write_text(json.dumps({
        "epoch": first[2],
        "limits": {
            "attempts_global": 10,
            "search_global": 8,
            "fetch_global": 4,
        },
        "global": {"attempts": 2, "search_network": 1, "fetch_network": 1},
    }), encoding="utf-8")

    reused = po._synthesis_recovery_budget_epoch(state, str(tmp_path))

    assert reused == first
    assert len(state.options["research_budget_epochs"]) == 1
    assert state.options["synthesis_recovery_budget_reused"]["epoch"] == first[2]
    assert saves


def test_synthesis_recovery_opens_fresh_epoch_when_tool_ledger_exhausted(
        monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_MAX_EPOCHS", 2, raising=False)
    monkeypatch.setattr(
        po.PipelineManager, "save", classmethod(lambda cls, state: None))
    state = po.PipelineState(
        pipeline_id="pipe-synthesis-fresh",
        task_id="task-a",
        prompt="forecast",
        handoff_dir=str(tmp_path),
    )
    first = po._activate_research_budget_epoch(state, str(tmp_path))
    state.options["synthesis_recovery"] = {
        "attempted": True,
        "passed": False,
    }
    state.task_id = "task-b"
    Path(first[1]).write_text(json.dumps({
        "epoch": first[2],
        "limits": {
            "attempts_global": 10,
            "search_global": 8,
            "fetch_global": 4,
        },
        "global": {"attempts": 10, "search_network": 1, "fetch_network": 1},
    }), encoding="utf-8")

    fresh = po._synthesis_recovery_budget_epoch(state, str(tmp_path))

    assert fresh != first
    assert fresh[2] == "task-b"
    assert len(state.options["research_budget_epochs"]) == 2


def test_budget_snapshot_exhaustion_is_epoch_bound(tmp_path):
    path = tmp_path / "research_budget.json"
    path.write_text(json.dumps({
        "epoch": "task-a",
        "limits": {
            "attempts_global": 10,
            "search_global": 8,
            "fetch_global": 4,
        },
        "global": {"attempts": 10, "search_network": 6, "fetch_network": 3},
    }), encoding="utf-8")

    assert po._research_budget_exhaustion(str(path), "task-a") == "attempts_global"
    assert po._research_budget_exhaustion(str(path), "task-b") is None


def test_orchestrator_disable_removes_inherited_budget_env(monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_ENABLED", False, raising=False)
    env = dict.fromkeys(po._RESEARCH_BUDGET_ENV_KEYS, "stale")
    po._configure_research_budget_env(env, str(tmp_path))
    assert not any(key in env for key in po._RESEARCH_BUDGET_ENV_KEYS)


def test_research_budget_telemetry_is_a_stage_artifact(tmp_path):
    state = po.PipelineState(
        pipeline_id="pipe-budget", prompt="x", handoff_dir=str(tmp_path)
    )
    specs = dict(po.PipelineOrchestrator._stage_artifact_specs(state, po.STAGE_RESEARCH))
    assert specs["research_budget"] == str(tmp_path / "research_budget.json")


def test_budget_denial_surfaces_as_research_quality_degradation(monkeypatch, tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({
        "research_quality": {"score": 0.9, "degraded": False},
    }), encoding="utf-8")
    (tmp_path / "research_budget.json").write_text(json.dumps({
        "degraded": False,
        "limits": {"search_global": 2},
        "global": {"attempts": 4, "search_network": 2, "denied_search_global": 2},
    }), encoding="utf-8")
    state = po.PipelineState(
        pipeline_id="pipe-budget-surface", prompt="x", handoff_dir=str(tmp_path))
    monkeypatch.setattr(po.PipelineManager, "save", classmethod(lambda cls, value: None))

    po.PipelineOrchestrator._surface_research_quality(None, state, str(tmp_path))

    assert state.options["research_budget"]["denials"] == 2
    assert state.options["research_quality"]["degraded"] is True
    assert "budget denied" in state.options["research_quality"]["degradation"][-1]
    persisted = json.loads((tmp_path / "meta.json").read_text())
    assert persisted["research_quality"]["degraded"] is True
