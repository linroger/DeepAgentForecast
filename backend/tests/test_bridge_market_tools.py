"""Offline regression tests for the DeerFlow prediction-market tool delivery lane."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path


BRIDGE_FILE = Path(__file__).resolve().parents[2] / "deerflow_bridge" / "market_tools.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("loop009_market_tools", BRIDGE_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_identical_normalized_queries_use_one_snapshot_and_one_ledger_record(
    tmp_path, monkeypatch,
):
    module = _load_module()
    calls: list[list[str]] = []

    def fake_snapshot(queries, **_kwargs):
        calls.append(list(queries))
        return [{
            "market_id": "691340",
            "question": "AI bubble burst in 2026?",
            "implied_yes_prob": 0.1545,
            "volume": 2_310_000.0,
            "url": "https://polymarket.com/event/ai-bubble-burst-in-2026",
        }]

    monkeypatch.setattr(module, "snapshot_for_queries", fake_snapshot)
    monkeypatch.setenv("DEERFLOW_RUN_ARTIFACT_DIR", str(tmp_path))
    module._reset_market_query_cache()

    first = json.loads(module.prediction_market_search_impl(
        " AI bubble 2026,ai BUBBLE 2026, US recession 2026"))
    second = json.loads(module.prediction_market_search_impl(
        "US recession 2026, AI bubble 2026"))

    assert calls == [["AI bubble 2026", "US recession 2026"]]
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    ledger = (tmp_path / module.MARKET_CANDIDATES_FILENAME).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines()]
    assert len(rows) == 1
    assert rows[0]["markets"][0]["market_id"] == "691340"


def test_capture_is_disabled_outside_trusted_existing_artifact_dir(tmp_path, monkeypatch):
    module = _load_module()
    missing = tmp_path / "missing"
    monkeypatch.setenv("DEERFLOW_RUN_ARTIFACT_DIR", str(missing))
    module._capture_market_candidates(["x"], [{"market_id": "1"}])
    assert not missing.exists()


def test_snapshot_diagnostics_distinguish_provider_failure_from_empty(monkeypatch):
    module = _load_module()
    diagnostics: dict[str, int] = {}
    monkeypatch.setattr(module, "_http_get", lambda *_args, **_kwargs: None)

    assert module.snapshot_for_queries(["outage"], diagnostics=diagnostics) == []
    assert diagnostics == {
        "attempted_query_count": 1,
        "successful_query_count": 0,
        "transport_failure_count": 1,
        "raw_candidate_count": 0,
        "candidate_count": 0,
    }

    empty_diagnostics: dict[str, int] = {}
    monkeypatch.setattr(module, "_http_get", lambda *_args, **_kwargs: {"events": []})
    assert module.snapshot_for_queries(["empty"], diagnostics=empty_diagnostics) == []
    assert empty_diagnostics["successful_query_count"] == 1
    assert empty_diagnostics["transport_failure_count"] == 0

    for malformed in ({"error": "temporarily unavailable"}, ["unexpected"]):
        malformed_diagnostics: dict[str, int] = {}
        monkeypatch.setattr(
            module, "_http_get", lambda *_args, _value=malformed, **_kwargs: _value
        )
        assert module.snapshot_for_queries(
            ["malformed"], diagnostics=malformed_diagnostics
        ) == []
        assert malformed_diagnostics["successful_query_count"] == 0
        assert malformed_diagnostics["transport_failure_count"] == 1


def test_no_queries_has_explicit_not_attempted_status():
    module = _load_module()
    payload = json.loads(module.prediction_market_search_impl("  \n , "))
    assert payload["status"]["attempted"] is False
    assert payload["status"]["state"] == "no_queries"
    assert payload["status"]["empty_reason"] == "no_queries"


def test_duration_knobs_are_always_finite(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("PREDICTION_MARKETS_NEGATIVE_CACHE_TTL_SECONDS", "inf")
    monkeypatch.setenv("PREDICTION_MARKETS_SINGLEFLIGHT_WAIT_SECONDS", "999999")
    assert module._bounded_env_seconds(
        "PREDICTION_MARKETS_NEGATIVE_CACHE_TTL_SECONDS", 30.0, 3600.0
    ) == 30.0
    assert module._bounded_env_seconds(
        "PREDICTION_MARKETS_SINGLEFLIGHT_WAIT_SECONDS", 35.0, 120.0
    ) == 120.0


def test_verified_empty_is_structured_and_negative_cache_expires(monkeypatch):
    module = _load_module()
    calls = 0

    def fake_snapshot(queries, diagnostics, **_kwargs):
        nonlocal calls
        calls += 1
        diagnostics.update({
            "attempted_query_count": len(queries),
            "successful_query_count": len(queries),
            "transport_failure_count": 0,
            "raw_candidate_count": 0,
            "candidate_count": 0,
        })
        return []

    monkeypatch.setattr(module, "snapshot_for_queries", fake_snapshot)
    monkeypatch.setenv("PREDICTION_MARKETS_NEGATIVE_CACHE_TTL_SECONDS", "0.02")
    module._reset_market_query_cache()

    first = json.loads(module.prediction_market_search_impl("no equivalent contract"))
    cached = json.loads(module.prediction_market_search_impl("no equivalent contract"))
    time.sleep(0.03)
    refreshed = json.loads(module.prediction_market_search_impl("no equivalent contract"))

    assert calls == 2
    assert first["status"]["state"] == "verified_empty"
    assert first["status"]["successful_query_count"] == 1
    assert first["status"]["empty_reason"] == "no_equivalent_market"
    assert "No active, liquid markets matched" in first["note"]
    assert cached["cache_hit"] is True
    assert refreshed["cache_hit"] is False


def test_transport_outage_is_structured_and_never_cached(monkeypatch):
    module = _load_module()
    calls = 0

    def failed_snapshot(queries, diagnostics, **_kwargs):
        nonlocal calls
        calls += 1
        diagnostics.update({
            "attempted_query_count": len(queries),
            "successful_query_count": 0,
            "transport_failure_count": len(queries),
            "raw_candidate_count": 0,
            "candidate_count": 0,
        })
        return []

    monkeypatch.setattr(module, "snapshot_for_queries", failed_snapshot)
    module._reset_market_query_cache()

    first = json.loads(module.prediction_market_search_impl("provider outage"))
    retried = json.loads(module.prediction_market_search_impl("provider outage"))

    assert calls == 2
    assert first["cache_hit"] is False
    assert retried["cache_hit"] is False
    assert first["status"]["state"] == "transport_failure"
    assert first["status"]["transport_failure_count"] == 1
    assert "no absence conclusion" in first["note"]
    assert "No active, liquid markets matched" not in first["note"]


def test_partial_transport_result_is_not_cached(tmp_path, monkeypatch):
    module = _load_module()
    calls = 0

    def partial_snapshot(queries, diagnostics, **_kwargs):
        nonlocal calls
        calls += 1
        diagnostics.update({
            "attempted_query_count": len(queries),
            "successful_query_count": 1,
            "transport_failure_count": 1,
            "raw_candidate_count": 1,
            "candidate_count": 1,
        })
        return [{"market_id": "m1", "question": "Relevant?", "volume": 1000}]

    monkeypatch.setattr(module, "snapshot_for_queries", partial_snapshot)
    monkeypatch.setenv("DEERFLOW_RUN_ARTIFACT_DIR", str(tmp_path))
    module._reset_market_query_cache()

    first = json.loads(module.prediction_market_search_impl("one, two"))
    second = json.loads(module.prediction_market_search_impl("one, two"))

    assert calls == 2
    assert first["status"]["state"] == "partial_success"
    assert first["cache_hit"] is False and second["cache_hit"] is False
    records = [json.loads(row) for row in (
        tmp_path / module.MARKET_CANDIDATES_FILENAME
    ).read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["status"]["transport_failure_count"] == 1


def test_singleflight_waiter_timeout_is_unknown_not_verified_empty(monkeypatch):
    module = _load_module()
    owner_started = threading.Event()
    release_owner = threading.Event()
    owner_payload: list[dict] = []

    def slow_snapshot(queries, diagnostics, **_kwargs):
        owner_started.set()
        assert release_owner.wait(timeout=2)
        diagnostics.update({
            "attempted_query_count": len(queries),
            "successful_query_count": len(queries),
            "transport_failure_count": 0,
            "raw_candidate_count": 0,
            "candidate_count": 0,
        })
        return []

    monkeypatch.setattr(module, "snapshot_for_queries", slow_snapshot)
    monkeypatch.setenv("PREDICTION_MARKETS_SINGLEFLIGHT_WAIT_SECONDS", "0.01")
    module._reset_market_query_cache()

    owner = threading.Thread(target=lambda: owner_payload.append(json.loads(
        module.prediction_market_search_impl("same query")
    )))
    owner.start()
    assert owner_started.wait(timeout=1)
    waiter = json.loads(module.prediction_market_search_impl("same query"))
    release_owner.set()
    owner.join(timeout=2)

    assert not owner.is_alive()
    assert waiter["cache_hit"] is False
    assert waiter["singleflight_shared"] is True
    assert waiter["status"]["state"] == "inflight_timeout"
    assert waiter["status"]["inflight_timeout_count"] == 1
    assert "still in flight" in waiter["note"]
    assert "No active, liquid markets matched" not in waiter["note"]
    assert owner_payload[0]["status"]["state"] == "verified_empty"
