"""Actual offline SDK dispatches compete for durable planned token allowance."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.config import Config
from app.utils import telemetry as tel
from test_llm_sdk_attempt_boundary import client_factory as client_factory, response, run as run
from test_oasis_physical_usage import call, models as models
from app.utils.api_budget import plan_token_reservation


def test_concurrent_same_owner_calls_cannot_spend_same_remainder(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20)
    entered, release = threading.Event(), threading.Event()
    sent = []

    def transport(request):
        sent.append(request)
        if len(sent) == 1:
            entered.set()
            assert release.wait(5)
        return httpx.Response(200, json=response(inputs=3, outputs=2))

    client, _ = client_factory(transport)

    def invoke():
        tel.set_run_context(run.id, "report")
        return client.chat([], max_tokens=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        try:
            assert entered.wait(5)
            second = pool.submit(invoke)
            with pytest.raises(tel.BudgetExceeded):
                second.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5) == "accepted"
    assert len(sent) == 1
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 5


def test_unknown_retry_holds_use_allowance_without_fabricating_tokens(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 28)
    sent = []

    def transport(request):
        sent.append(request)
        raise httpx.ReadTimeout("offline response lost", request=request)

    client, _ = client_factory(transport)
    with pytest.raises(tel.BudgetExceeded):
        client.chat([], max_tokens=10)
    assert len(sent) == 2
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] == 0
    assert snap["total"]["calls"] == snap["api_operation_state"]["unknown"] == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_unknown_retries_hold_output_allowance(run, models, monkeypatch, asynchronous):
    body = {"messages": [{"role": "user", "content": "offline prompt"}], "max_tokens": 10}
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    planned = plan_token_reservation(body, run.id)
    allowance = planned["prompt_tokens_estimate"] + planned["completion_tokens_limit"]
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", allowance * 2)
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(429, json={"error": {"message": "offline rate limit"}})

    model = models(transport, retries=3)
    model.model_config_dict = {"max_tokens": 10}
    with pytest.raises(tel.BudgetExceeded):
        call(model, asynchronous)
    assert len(sent) == 2
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] == 0
    assert snap["token_reservation_state"]["reserved_tokens"] == 2 * allowance


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_uncapped_request_is_rejected_without_changing_request_defaults(
        run, models, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10000)
    sent = []
    model = models(lambda request: sent.append(request), retries=3)
    with pytest.raises(tel.BudgetExceeded, match="output limit"):
        call(model, asynchronous)
    assert sent == [] and model.model_config_dict == {}
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["calls"] == 0


@pytest.mark.parametrize("native_tools", [False, True])
def test_missing_usage_retains_allowance_after_success(run, client_factory, monkeypatch, native_tools):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 30 if native_tools else 20)
    sent = []

    def transport(request):
        sent.append(request)
        body = response()
        body.pop("usage")
        return httpx.Response(200, json=body)

    client, _ = client_factory(transport)
    def invoke():
        return client.chat_with_tools([], [], max_tokens=10) if native_tools else client.chat([], max_tokens=10)
    invoke()
    with pytest.raises(tel.BudgetExceeded):
        invoke()
    assert len(sent) == 1
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] > 0
    assert snap["token_reservation_state"]["reserved_tokens"] > 0


def test_known_empty_response_releases_allowance_before_logical_retry(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20)
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(200, json=response("" if len(sent) == 1 else "accepted", 3, 2))

    client, _ = client_factory(transport)
    assert client.chat([], max_tokens=10) == "accepted"
    assert len(sent) == 2
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] == 10
    assert snap["token_reservation_state"]["reserved_tokens"] == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_known_error_usage_releases_allowance(run, models, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(429 if len(sent) == 1 else 200, json=response(inputs=3, outputs=2))

    model = models(transport, retries=3)
    model.model_config_dict = {"max_completion_tokens": 10}
    assert call(model, asynchronous).choices[0].message.content == "accepted"
    assert len(sent) == 2
    assert tel.LLMMeter.cumulative_snapshot(run.id)["token_reservation_state"]["reserved_tokens"] == 0


@pytest.mark.parametrize("patch", [
    {"max_tokens": None}, {"max_tokens": True}, {"max_tokens": -1},
    {"max_tokens": 1.5}, {"max_tokens": 2**53 + 1}, {"max_completion_tokens": 10},
    {"n": 2}, {"n": True}, {"stream": True}, {"modalities": ["text", "audio"]},
    {"n": 1.0}, {"web_search_options": {}}, {"prediction": {"type": "content", "content": "large"}},
    {"extra_body": {"max_tokens": 10000}}, {"extra_body": {"messages": []}},
    {"tools": [{"type": "web_search"}]},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "offline"}}]}]},
    {"messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}]},
])
def test_unplannable_request_never_reaches_sdk(run, client_factory, monkeypatch, patch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10000)
    sent = []
    client, sdk = client_factory(lambda request: sent.append(request))
    body = {"messages": [], "model": "offline-model", "max_tokens": 10, **patch}
    with pytest.raises(tel.BudgetExceeded):
        client._create_openai_completion(sdk, "openai", "offline-model", body)
    assert sent == []
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["calls"] == 0


def test_planned_nested_request_cannot_mutate_during_admission(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    messages = [{"role": "user", "content": "original"}]
    sent = []
    original_record = tel.LLMMeter.record_snapshot

    def record(*args, **kwargs):
        if kwargs.get("status") == "in_flight":
            messages[0]["content"] = "mutated" * 1000
        return original_record(*args, **kwargs)

    monkeypatch.setattr(tel.LLMMeter, "record_snapshot", record)
    client, _ = client_factory(lambda request: sent.append(json.loads(request.content)) or
                                httpx.Response(200, json=response()))
    assert client.chat(messages, max_tokens=10) == "accepted"
    assert messages[0]["content"].startswith("mutated")
    assert sent[0]["messages"] == [{"role": "user", "content": "original"}]


def test_text_tool_schema_estimate_counts_all_input_and_persists_only_numbers(run, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 10000)
    inputs = {"messages": [{"role": "user", "content": [{"type": "text", "text": "中文 fixture"}]}],
              "tools": [{"type": "function", "function": {"name": "test", "description": "x" * 400,
                                                         "parameters": {"type": "object"}}}],
              "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": {}}},
              "tool_choice": "auto"}
    plan = plan_token_reservation({**inputs, "max_completion_tokens": 10}, run.id)
    size = len(json.dumps(inputs, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert plan == {"token_limit": 10000, "prompt_tokens_estimate": (size + 3) // 4,
                    "completion_tokens_limit": 10, "estimator": "utf8-json-quarter/v1"}
    assert "fixture" not in json.dumps(plan)


def test_disabled_token_budget_does_not_validate_or_serialize_payload(run, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 1)
    assert plan_token_reservation({"messages": object()}, run.id) is None


@pytest.mark.parametrize("changed_limit", [0, 1000])
def test_response_still_checks_captured_cap_after_configuration_changes(
        run, client_factory, monkeypatch, changed_limit):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20)
    sent = []

    def transport(request):
        sent.append(request)
        monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", changed_limit)
        return httpx.Response(200, json=response(inputs=20, outputs=5))

    client, _ = client_factory(transport)
    with pytest.raises(tel.BudgetExceeded, match="25 > 20"):
        client.chat([], max_tokens=10)
    assert len(sent) == 1
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 25


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_settlement_checks_captured_cap_after_configuration_changes(
        run, models, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 30)

    def transport(request):
        monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
        return httpx.Response(200, json=response(inputs=40, outputs=5))

    model = models(transport)
    model.model_config_dict = {"max_tokens": 5}
    with pytest.raises(tel.BudgetExceeded, match="45 > 30"):
        call(model, asynchronous)
    assert tel.LLMMeter.cumulative_snapshot(run.id)["total"]["total_tokens"] == 45


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_schema_parse_failure_releases_known_usage_hold(run, models, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    class Result(BaseModel):
        answer: int
    model = models(lambda request: httpx.Response(200, json=response("invalid json", 3, 2)))
    model.model_config_dict = {"max_tokens": 10}
    with pytest.raises(ValidationError):
        call(model, asynchronous, parse=Result)
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap["total"]["total_tokens"] == 5
    assert snap["token_reservation_state"]["reserved_tokens"] == 0


def test_unsupported_cap_and_cyclic_input_do_not_poison_storage(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 2**53 + 1)
    client, _ = client_factory(lambda request: httpx.Response(200, json=response(inputs=3, outputs=2)))
    with pytest.raises(tel.BudgetExceeded, match="supported range"):
        client.chat([], max_tokens=10)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20)
    cyclic = {"role": "user", "content": "text"}
    cyclic["extension"] = cyclic
    with pytest.raises(tel.BudgetExceeded, match="finite JSON"):
        client.chat([cyclic], max_tokens=10)
    assert client.chat([], max_tokens=10) == "accepted"
