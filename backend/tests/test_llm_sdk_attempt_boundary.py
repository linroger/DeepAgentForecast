"""Real OpenAI SDK and httpx transport, entirely offline physical-call checks."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI, RateLimitError

from app.config import Config
from app.utils import llm_client as lc
from app.utils import telemetry as tel


@pytest.fixture
def run(tmp_path, monkeypatch):
    previous = tel.get_run_context()
    rid, path = "pipe-sdk-boundary", tmp_path / "usage.sqlite3"
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, str(path), attempt_id="attempt-sdk")
    tel.set_run_context(rid, "report")
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", False)
    monkeypatch.setattr(Config, "LLM_TIERED_ROUTING", False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(lc, "_CB_STATE", {})
    monkeypatch.setattr(lc.time, "sleep", lambda _: None)
    yield SimpleNamespace(id=rid, path=path)
    tel.LLMMeter.reset(rid)
    tel.set_run_context(*previous)


def response(content="accepted", inputs=10, outputs=5):
    return {"id": "offline-response", "object": "chat.completion", "created": 1, "model": "offline-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": inputs, "completion_tokens": outputs, "total_tokens": inputs + outputs}}


@pytest.fixture
def client_factory(monkeypatch):
    opened = []

    def make(handler, sdk_retries=2):
        sdk = OpenAI(api_key="offline-fixture", base_url="https://offline.invalid/v1",
                     http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=sdk_retries)
        opened.append(sdk)
        monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *args: sdk))
        return lc.LLMClient(provider="openai", model="offline-model", api_key="offline-fixture"), sdk

    yield make
    for sdk in opened:
        sdk.close()


def operations(run):
    with sqlite3.connect(run.path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            "SELECT * FROM usage_operations WHERE source='llm_api_attempt' ORDER BY rowid")]


@pytest.mark.parametrize("native", [False, True])
def test_shared_sdk_retry_defaults_cannot_multiply_outer_attempts(run, client_factory, native):
    transmissions = []

    def transport(request):
        transmissions.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "offline rate limit", "type": "rate_limit"}})

    client, sdk = client_factory(transport, sdk_retries=2)
    with pytest.raises(RateLimitError):
        client.chat_with_tools([], []) if native else client.chat([])
    assert len(transmissions) == lc.MAX_RETRIES == 3
    assert sdk.max_retries == 2  # Request-local control does not mutate a shared client.
    rows = operations(run)
    assert len(rows) == 3
    assert len({row["operation_id"] for row in rows}) == 3
    assert all(json.loads(row["metadata_json"])["usage_class"] == "unknown" for row in rows)
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["calls"] == 3


def test_empty_response_and_success_are_both_settled_before_validation(run, client_factory):
    transmissions = []

    def transport(request):
        transmissions.append(request)
        body = response("" if len(transmissions) == 1 else "accepted",
                        100 if len(transmissions) == 1 else 50, 20 if len(transmissions) == 1 else 10)
        return httpx.Response(200, json=body)

    client, _ = client_factory(transport)
    assert client.chat([]) == "accepted"
    assert len(transmissions) == 2
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] == 180
    assert snap["total"]["calls"] == 2
    assert len(operations(run)) == 2


def test_budget_consumed_by_empty_response_prevents_retry(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10)
    transmissions = []

    def transport(request):
        transmissions.append(request)
        return httpx.Response(200, json=response("", 20, 5))

    client, _ = client_factory(transport)
    with pytest.raises(tel.BudgetExceeded):
        client.chat([], max_tokens=5)
    with pytest.raises(tel.BudgetExceeded):
        client.chat_with_tools([], [], max_tokens=5)
    assert len(transmissions) == 1
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 25


def test_sdk_construction_bounds_timeout_and_retries_without_http2(monkeypatch):
    captured = []
    monkeypatch.setattr(Config, "LLM_HTTP_TIMEOUT_S", 73.5, raising=False)
    monkeypatch.setattr(lc.LLMClient, "_build_http_client", staticmethod(lambda: None))
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: captured.append(kwargs) or object())
    lc.LLMClient._build_openai_client("openai", "offline-fixture", "https://offline.invalid/v1")
    assert captured[0]["max_retries"] == 0
    assert captured[0]["timeout"] == 73.5
    assert "http_client" not in captured[0]


def test_attempt_attribution_does_not_move_when_active_registry_changes(run, client_factory, monkeypatch):
    # A worker without a ContextVar initially infers the sole active pipeline.
    previous = tel.get_run_context()
    original_active = dict(tel._active_runs)
    tel._current_run.set(None)
    tel._current_stage.set(None)
    with tel._ACTIVE_LOCK:
        tel._active_runs.clear()
        tel._active_runs[run.id] = 1
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20)
    tel.LLMMeter.reset("unrelated-run")
    tel.LLMMeter.record("openai", "offline", 100, 0, 0, run_id="unrelated-run")

    def transport(request):
        with tel._ACTIVE_LOCK:
            tel._active_runs.clear()
            tel._active_runs["unrelated-run"] = 1
        return httpx.Response(200, json=response())

    try:
        client, _ = client_factory(transport)
        assert client.chat([], max_tokens=5) == "accepted"
        snap = tel.LLMMeter.cumulative_snapshot(run.id)
        assert snap["total"]["total_tokens"] == 15
        assert snap["fallback_attributed"]["total_tokens"] == 15
        rows = operations(run)
        assert len(rows) == 1 and rows[0]["run_id"] == run.id
    finally:
        tel.LLMMeter.reset("unrelated-run")
        with tel._ACTIVE_LOCK:
            tel._active_runs.clear()
            tel._active_runs.update(original_active)
        tel._current_run.set(previous[0])
        tel._current_stage.set(previous[1])
