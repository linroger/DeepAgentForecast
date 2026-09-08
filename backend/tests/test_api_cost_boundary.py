"""Real offline SDK calls retain an immutable price estimate and explicit gaps."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.api_cost import capture_cost_quote, quote_cost, validate_cost_quote
from test_llm_sdk_attempt_boundary import client_factory as client_factory, response, run as run
from test_oasis_physical_usage import call, models as models


@pytest.fixture(autouse=True)
def rates(monkeypatch):
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", "")


@pytest.fixture(params=["shared", "native_sync", "native_async"])
def invoke(run, client_factory, models, request):
    def build(handler, provider="openai"):
        if request.param == "shared":
            client, sdk = client_factory(handler)
            return lambda: client._create_openai_completion(sdk, provider, "offline-model", {
                "model": "offline-model", "messages": [{"role": "user", "content": "offline"}]})
        model = models(handler, retries=2, provider=provider)
        return lambda: call(model, request.param == "native_async")
    return build


def _rows(run):
    with sqlite3.connect(run.path) as conn:
        conn.row_factory = sqlite3.Row
        return [{**dict(row), "metadata": json.loads(row["metadata_json"]),
                 "counter": json.loads(row["counter_json"])}
                for row in conn.execute("SELECT * FROM usage_operations WHERE source='llm_api_attempt'")]


@pytest.mark.parametrize("raw", ["{bad", "[]", '{"openai":[-1,2]}', '{"openai":[true,2]}',
                               '{"openai":[NaN,2]}', '{"openai":[1,Infinity]}',
                               '{"openai":[1,2,3]}', '{"openai":["1",2]}',
                               '{"openai":[1,2],"openai":[3,4]}',
                               '{"OpenAI":[1,2],"openai":[3,4]}', '{"openai:":[1,2]}'])
def test_invalid_price_fails_before_transmission_and_recovers_without_reset(run, invoke, monkeypatch, raw):
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", raw)
    sent = []
    dispatch = invoke(lambda request: (sent.append(request), httpx.Response(200, json=response()))[1])
    with pytest.raises(tel.BudgetExceeded, match="price"):
        dispatch()
    assert sent == [] and _rows(run) == []
    tel.LLMMeter.assert_accounting_available(run.id)
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[1,2]}')
    assert dispatch().choices[0].message.content == "accepted"
    assert len(sent) == 1


@pytest.mark.parametrize("provider", ["unknown", "proxy", "antigravity"])
def test_missing_price_stops_dollar_enabled_work_but_explicit_zero_is_priced(run, invoke, monkeypatch, provider):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 1)
    sent = []
    dispatch = invoke(lambda request: (sent.append(request), httpx.Response(200, json=response()))[1], provider)
    with pytest.raises(tel.BudgetExceeded, match="no price"):
        dispatch()
    assert sent == [] and _rows(run) == []
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", json.dumps({provider: [0, 0]}))
    dispatch()
    quote = _rows(run)[0]["metadata"]["cost_quote"]
    assert quote["source"] == "configured_provider" and quote["input_usd_per_1k"] == 0
    assert _rows(run)[0]["counter"]["cost_usd"] == 0
    assert len(sent) == 1


def test_unpriced_disabled_budget_retains_unknown_rates_and_positive_usage(run, invoke):
    invoke(lambda request: httpx.Response(200, json=response()), "unknown")()
    row = _rows(run)[0]
    assert row["counter"]["total_tokens"] == 15 and row["counter"]["cost_usd"] == 0
    assert row["metadata"]["cost_quote"]["source"] == "unpriced"
    assert row["metadata"]["cost_quote"]["input_usd_per_1k"] is None
    assert row["metadata"]["cost_estimated"] is True


def test_rate_drift_during_response_and_restarted_replay_cannot_reprice_usage(run, invoke, monkeypatch):
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[1,2]}')
    def transport(request):
        monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[2,4]}')
        body = response(inputs=1000, outputs=1000)
        body["usage"]["prompt_tokens_details"] = {"cached_tokens": 100}
        return httpx.Response(200, json=body)
    invoke(transport)()
    row = _rows(run)[0]
    assert row["counter"]["cost_usd"] == pytest.approx(0.003)
    assert row["counter"]["cache_read_tokens"] == 100
    assert row["metadata"]["cache_partition_known"] is False
    assert row["metadata"]["cost_estimated"] is True
    quote = row["metadata"]["cost_quote"]
    tel.LLMMeter.reset(run.id)
    tel.LLMMeter.attach_durable_run(run.id, str(run.path), row["owner_attempt_id"], existing_only=True)
    tel.set_run_context(run.id, "report")
    delta = tel.LLMMeter.record_snapshot("llm_api_attempt", row["operation_id"], "openai", "offline-model",
        1000, 1000, row["counter"]["latency_ms"], run_id=run.id, stage=row["metadata"]["stage"],
        usage_source="known", cache_read_tokens=100, cost_quote=quote)
    assert delta["cost_usd"] == delta["total_tokens"] == delta["calls"] == 0
    snapshot = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snapshot["total"]["cost_usd"] == 0.003
    assert snapshot["cost_estimated"] is True


def test_model_specific_prices_precede_provider_estimate_and_preserve_model_case(run, monkeypatch):
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK",
                        '{"OpenAI":[5,10],"openai:Model-A":[1,2],"OPENAI:Model-B":[3,4]}')
    first = capture_cost_quote("openai", "Model-A")
    second = capture_cost_quote("openai", "Model-B")
    assert quote_cost(first, 1000, 1000) == pytest.approx(0.003)
    assert quote_cost(second, 1000, 1000) == pytest.approx(0.007)
    fallback = capture_cost_quote("openai", "model-a")
    assert fallback["source"] == "configured_provider"
    assert quote_cost(fallback, 1000, 1000) == pytest.approx(0.015)
    assert first["rate_key"] == "openai:Model-A"
    copied = validate_cost_quote(first)
    copied["input_usd_per_1k"] = 0
    assert first["input_usd_per_1k"] == 0.001


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), -1, True])
def test_invalid_dollar_cap_cannot_disable_price_admission(run, monkeypatch, limit):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", limit)
    with pytest.raises(tel.BudgetExceeded, match="budget"):
        capture_cost_quote("openai", "offline-model")


@pytest.mark.parametrize("payload", [{"model": "other", "messages": []},
                                  {"model": "offline-model", "messages": [], "extra_body": {"model": "other"}}])
def test_shared_quote_rejects_model_mismatch_before_dispatch(run, client_factory, payload):
    sent = []
    client, sdk = client_factory(lambda request: sent.append(request))
    with pytest.raises(tel.BudgetExceeded, match="model"):
        client._create_openai_completion(sdk, "openai", "offline-model", payload)
    assert sent == [] and _rows(run) == []


def test_shared_model_attribution_snapshot_ignores_caller_mutation_before_send(run, client_factory, monkeypatch):
    sent = []
    client, sdk = client_factory(lambda request: (sent.append(json.loads(request.content)),
                                                 httpx.Response(200, json=response()))[1])
    payload = {"model": "offline-model", "messages": [], "extra_body": {"thinking": {"type": "disabled"}}}
    original = sdk.with_options
    def mutate(**kwargs):
        payload["model"] = "different"
        payload["extra_body"]["model"] = "different"
        return original(**kwargs)
    monkeypatch.setattr(sdk, "with_options", mutate)
    client._create_openai_completion(sdk, "openai", "offline-model", payload)
    assert sent[0]["model"] == "offline-model"
    assert _rows(run)[0]["metadata"]["cost_quote"]["model"] == "offline-model"


def test_actual_shared_plain_and_tools_record_distinct_model_quotes(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai:offline-model":[1,2],"openai:second-model":[3,4]}')
    client, _ = client_factory(lambda request: httpx.Response(200, json=response(inputs=1000, outputs=1000)))
    assert client.chat([]) == "accepted"
    client.model = "second-model"
    assert client.chat_with_tools([], [])["content"] == "accepted"
    quotes = [row["metadata"]["cost_quote"] for row in _rows(run)]
    assert {quote["rate_key"] for quote in quotes} == {"openai:offline-model", "openai:second-model"}
