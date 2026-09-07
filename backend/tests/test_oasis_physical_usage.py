"""Actual CAMEL/SDK HTTP boundaries, with temporary ledgers and offline transports."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from camel.models import OpenAIModel
from openai import AsyncOpenAI, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import Config
from app.utils import llm_client as lc
from app.utils import oasis_llm as oasis
from app.utils import telemetry as tel
from app.utils.oasis_usage import instrument_oasis_model
from app.utils.usage_ledger import UsageLedger


@pytest.fixture
def run(tmp_path, monkeypatch):
    previous = tel.get_run_context()
    rid, path = "pipe-oasis-physical", tmp_path / "usage.sqlite3"
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, str(path), attempt_id="shared-parent-attempt")
    tel.set_run_context(rid, "run")
    for name, value in {"LLM_TELEMETRY_ENABLED": False, "LLM_CACHE_ENABLED": False,
                        "LLM_TIERED_ROUTING": False, "LLM_RUN_BUDGET_TOKENS": 0,
                        "LLM_RUN_BUDGET_USD": 0}.items():
        monkeypatch.setattr(Config, name, value)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setenv("SIM_LLM_FALLBACK", "true")
    monkeypatch.setattr(lc, "_CB_STATE", {})
    monkeypatch.setattr(oasis, "_record_llm_fallback", lambda *args: None)
    yield SimpleNamespace(id=rid, path=path)
    tel.LLMMeter.reset(rid)
    tel.set_run_context(*previous)


def response(content="accepted", usage="default", tools=None):
    result = {"id": "offline-response", "object": "chat.completion", "created": 1, "model": "offline-model",
              "choices": [{"index": 0, "finish_reason": "tool_calls" if tools else "stop",
                           "message": {"role": "assistant", "content": content, "tool_calls": tools}}]}
    if usage == "default":
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                 "prompt_tokens_details": {"cached_tokens": 4}}
    if usage is not None:
        result["usage"] = usage
    return result


@pytest.fixture
def models(monkeypatch):
    opened = []

    def make(handler, retries=2, *, instrument=True, provider="openai"):
        sync = OpenAI(api_key="offline", base_url="https://offline.invalid/v1", max_retries=retries,
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        async_sdk = AsyncOpenAI(api_key="offline", base_url="https://offline.invalid/v1", max_retries=retries,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(sync, "_sleep_for_retry", lambda **kwargs: None)

        async def no_sleep(**kwargs):
            pass

        monkeypatch.setattr(async_sdk, "_sleep_for_retry", no_sleep)
        opened.append((sync, async_sdk))
        model = object.__new__(OpenAIModel)
        model.model_type = "offline-model"
        model.model_config_dict = {}
        model._client, model._async_client = sync, async_sdk
        return instrument_oasis_model(model, provider) if instrument else model

    yield make
    for sync, async_sdk in opened:
        sync.close()
        asyncio.run(async_sdk.close())


def call(model, asynchronous=False, *, tools=None, parse=None):
    messages = [{"role": "user", "content": "offline prompt"}]
    if parse:
        method = model._arequest_parse if asynchronous else model._request_parse
        result = method(messages, parse, tools)
    else:
        method = model._arequest_chat_completion if asynchronous else model._request_chat_completion
        result = method(messages, tools)
    return asyncio.run(result) if asynchronous else result


def operations(run):
    with sqlite3.connect(run.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM usage_operations ORDER BY rowid").fetchall()
    return [{**dict(row), "counter": json.loads(row["counter_json"]),
             "metadata": json.loads(row["metadata_json"])} for row in rows]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["429", "timeout"])
def test_every_sdk_retry_has_marker_and_distinct_receipt(run, models, asynchronous, failure):
    sent = []

    def transport(request):
        current = operations(run)[-1]
        assert current["status"] == "in_flight"
        assert current["counter"]["calls"] == current["counter"]["total_tokens"] == 0
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("offline interruption", request=request)
            return httpx.Response(429, json={"error": {"message": "offline rate limit"}})
        return httpx.Response(200, json=response())

    model = models(transport)
    assert call(model, asynchronous).choices[0].message.content == "accepted"
    assert model._client.max_retries == model._async_client.max_retries == 2
    rows = operations(run)
    assert len(sent) == len(rows) == len({row["operation_id"] for row in rows}) == 2
    assert [row["status"] for row in rows] == ["unknown", "completed"]
    assert rows[0]["metadata"]["usage_class"] == "unknown"
    assert rows[1]["counter"]["cache_read_tokens"] == 4
    snapshot = tel.LLMMeter.cumulative_snapshot(run.id)
    assert (snapshot["total"]["calls"], snapshot["total"]["total_tokens"]) == (2, 15)
    assert snapshot["api_operation_state"]["in_flight"] == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_exhausted_http_retries_retain_unknown_calls(run, models, asynchronous):
    model = models(lambda request: httpx.Response(429, json={"error": {"message": "rate limit"}}))
    with pytest.raises(RateLimitError):
        call(model, asynchronous)
    rows = operations(run)
    assert len(rows) == 3
    assert all(row["status"] == "unknown" and row["counter"]["calls"] == 1 for row in rows)
    assert all(row["counter"]["total_tokens"] == 0 for row in rows)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["empty", "native", "parse_error"])
def test_usage_settles_before_content_and_structured_parsing(run, models, asynchronous, kind):
    tool = {"id": "call_1", "type": "function", "function": {"name": "act", "arguments": "{}"}}
    body = response("invalid json" if kind == "parse_error" else "", tools=[tool] if kind == "native" else None)
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    model = models(transport)
    if kind == "parse_error":
        class Result(BaseModel):
            answer: int
        with pytest.raises(ValidationError):
            call(model, asynchronous, parse=Result)
        assert requests[0]["response_format"]["type"] == "json_schema"
    else:
        tools = [{"type": "function", "function": {"name": "act", "parameters": {"type": "object"}}}]
        result = call(model, asynchronous, tools=tools if kind == "native" else None)
        if kind == "native":
            assert result.choices[0].message.tool_calls[0].function.name == "act"
            assert requests[0]["tools"] == tools
    assert len(operations(run)) == len(requests) == 1
    assert operations(run)[0]["status"] == "completed"
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 15


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("usage", ["garbage", {"prompt_tokens": True, "completion_tokens": 1},
                                   {"cache_read_input_tokens": 20}])
def test_malformed_usage_stops_sdk_retries_and_future_sends(run, models, asynchronous, usage):
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(200, json=response(usage=usage))

    model = oasis._wrap_openai_fallback_guard(models(transport), "openai")
    for _ in range(2):
        with pytest.raises(tel.UsageLedgerUnresolvedError):
            call(model, asynchronous)
    assert len(sent) == 1
    assert operations(run)[0]["status"] == "accounting_error"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_storage_failure_stays_sticky_without_sdk_retry(run, models, monkeypatch, asynchronous):
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(200, json=response())

    model = oasis._wrap_openai_fallback_guard(models(transport), "openai")

    def fail_write(*args, **kwargs):
        raise sqlite3.OperationalError("offline full disk")

    with monkeypatch.context() as scoped:
        scoped.setattr(UsageLedger, "_insert_delta", staticmethod(fail_write))
        with pytest.raises(tel.UsageLedgerStorageError):
            call(model, asynchronous)
    with pytest.raises(tel.UsageLedgerStorageError, match="failed during this attempt"):
        call(model, asynchronous)
    assert len(sent) == 1
    assert operations(run)[0]["status"] == "in_flight"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_budget_stop_survives_failing_response_cleanup(run, models, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10)
    sent = []

    def transport(request):
        sent.append(request)
        result = httpx.Response(200, json=response())

        def close():
            raise RuntimeError("offline close failure")

        async def aclose():
            raise RuntimeError("offline close failure")

        result.close, result.aclose = close, aclose
        return result

    model = oasis._wrap_openai_fallback_guard(models(transport), "openai")
    for _ in range(2):
        with pytest.raises(tel.BudgetExceeded):
            call(model, asynchronous)
    assert len(sent) == 1
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 15


@pytest.mark.parametrize("asynchronous", [False, True])
def test_streaming_rejected_before_transport_or_operation(run, models, asynchronous):
    sent = []
    model = models(lambda request: sent.append(request))
    model.model_config_dict["stream"] = True
    model = oasis._wrap_openai_fallback_guard(model, "openai")
    with pytest.raises(tel.UsageLedgerStorageError, match="non-streaming"):
        call(model, asynchronous)
    assert sent == operations(run) == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_request_and_failed_marker_never_send(run, models, monkeypatch, asynchronous):
    sent = []
    model = oasis._wrap_openai_fallback_guard(models(lambda request: sent.append(request)), "openai")
    model.model_type = ""
    with pytest.raises(tel.UsageLedgerStorageError, match="attribution"):
        call(model, asynchronous)
    assert operations(run) == []
    model.model_type = "offline-model"

    def fail_marker(*args, **kwargs):
        raise tel.UsageLedgerStorageError("offline marker unavailable")

    monkeypatch.setattr(UsageLedger, "record_snapshot", fail_marker)
    with pytest.raises(tel.UsageLedgerStorageError, match="marker unavailable"):
        call(model, asynchronous)
    assert sent == operations(run) == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_failed_response_usage_is_retained_and_routed_from_request(run, models, asynchronous):
    sent = []

    def transport(request):
        sent.append(request)
        body = response()
        body["model"] = "response-model-is-not-request-attribution"
        return httpx.Response(429 if len(sent) == 1 else 200, json=body)

    model = models(transport, provider="minimax")
    instrument_oasis_model(model, "minimax")  # Installation is idempotent.
    call(model, asynchronous)
    rows = operations(run)
    assert len(sent) == len(rows) == 2
    assert [row["status"] for row in rows] == ["unknown", "completed"]
    assert all(row["metadata"]["usage_class"] == "known" for row in rows)
    assert all(row["metadata"]["provider"] == "minimax" and row["metadata"]["model"] == "offline-model" for row in rows)
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 30


@pytest.mark.parametrize("asynchronous", [False, True])
def test_fallback_synthetic_completion_does_not_duplicate_real_calls(run, models, monkeypatch, asynchronous):
    direct = models(lambda request: httpx.Response(422, json={"error": {"message": "new_sensitive"}}), retries=0)
    fallback = models(lambda request: httpx.Response(200, json=response()), instrument=False)
    monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *args: fallback._client))
    model = oasis._wrap_openai_fallback_guard(direct, "openai")
    assert call(model, asynchronous).choices[0].message.content == "accepted"
    rows = operations(run)
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["unknown", "completed"]
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 15


@pytest.mark.parametrize("error_type", [tel.BudgetExceeded, tel.UsageLedgerStorageError])
def test_fallback_control_error_is_not_replaced_by_primary(run, models, monkeypatch, error_type):
    model = models(lambda request: httpx.Response(422, json={"error": {"message": "new_sensitive"}}), retries=0)
    monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *args: SimpleNamespace()))
    monkeypatch.setattr(lc.LLMClient, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(error_type("offline stop")))
    with pytest.raises(error_type, match="offline stop"):
        call(oasis._wrap_openai_fallback_guard(model, "openai"))
    assert len(operations(run)) == 1


def test_unbound_models_remain_untouched(run):
    tel.LLMMeter.reset(run.id)
    model = SimpleNamespace()
    assert instrument_oasis_model(model, "openai") is model
    assert vars(model) == {}


@pytest.mark.parametrize("boost_provider", [None, "minimax"])
def test_factory_instruments_replaced_clients_and_boost_route(run, models, monkeypatch, boost_provider):
    old, replacement = [], []
    model = models(lambda request: old.append(request), instrument=False)
    new = models(lambda request: replacement.append(request) or httpx.Response(200, json=response()), instrument=False)
    monkeypatch.setattr(oasis, "_create_openai_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(oasis, "_resolve_provider", lambda config: "kimi")
    monkeypatch.setattr(Config, "reasoning_extra_body", lambda: None)
    monkeypatch.setenv("LLM_BOOST_API_KEY", "offline")
    if boost_provider:
        monkeypatch.setenv("LLM_BOOST_PROVIDER", boost_provider)
    else:
        monkeypatch.delenv("LLM_BOOST_PROVIDER", raising=False)

    def replace(target):
        target._client, target._async_client = new._client, new._async_client

    monkeypatch.setattr(oasis, "_inject_coding_agent_ua", replace)
    assert call(oasis.create_oasis_model({}, use_boost=True)).choices[0].message.content == "accepted"
    assert old == [] and len(replacement) == 1
    assert operations(run)[0]["metadata"]["provider"] == (boost_provider or "unknown")


def test_missing_usage_is_labeled_estimate_and_does_not_invent_cache_partition(run, models):
    call(models(lambda request: httpx.Response(200, json=response(usage=None))))
    row = operations(run)[0]
    assert row["metadata"]["usage_class"] == "estimated"
    assert row["counter"]["total_tokens"] > 0
    assert row["metadata"]["cache_partition_known"] is False


@pytest.mark.parametrize("asynchronous", [False, True])
def test_interrupted_response_read_closes_stream_before_sdk_retry(run, models, asynchronous):
    closed, sent = [], []

    class BrokenSyncStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"usage":'
            raise httpx.ReadError("offline body interruption")

        def close(self):
            closed.append(True)

    class BrokenAsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"usage":'
            raise httpx.ReadError("offline body interruption")

        async def aclose(self):
            closed.append(True)

    def transport(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(200, stream=BrokenAsyncStream() if asynchronous else BrokenSyncStream())
        assert closed == [True]
        return httpx.Response(200, json=response())

    model = models(transport)
    # Request non-streaming chat output but defer HTTP body reads, as the SDK's
    # raw-response API can do. The accounting hook owns and closes that read.
    model.model_config_dict["extra_headers"] = {"X-Stainless-Raw-Response": "stream"}
    call(model, asynchronous)
    assert closed == [True] and len(sent) == 2
    assert [row["status"] for row in operations(run)] == ["unknown", "completed"]
