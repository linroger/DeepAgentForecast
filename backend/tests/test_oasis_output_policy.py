"""Native output policy through actual CAMEL factories and offline SDK requests."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import openai
import pytest
from camel.agents import ChatAgent
from camel.models import OpenAIModel
from camel.models import openai_model as camel_openai
from pydantic import BaseModel

from app.config import Config
from app.utils import oasis_llm as oasis
from app.utils import oasis_output_policy as policy
from app.utils import simulation_usage as simulation
from app.utils import telemetry as tel
from test_oasis_physical_usage import call, response


def output_policy(cap=32, parameter="max_tokens"):
    return {"schema": policy.SCHEMA, "max_output_tokens": cap, "parameter": parameter}


@pytest.fixture
def factory(monkeypatch):
    original_sync, original_async = openai.OpenAI, openai.AsyncOpenAI
    opened, requests, constructors = [], [], []
    harness = SimpleNamespace(requests=requests, constructors=constructors)
    harness.handler = lambda request: httpx.Response(200, json=response())

    def transport(request):
        requests.append({"body": json.loads(request.content), "host": request.url.host,
                         "user_agent": request.headers.get("user-agent")})
        return harness.handler(request)

    def make_sync(**kwargs):
        constructors.append((False, dict(kwargs)))
        supplied = kwargs.pop("http_client", None)
        if supplied is not None:
            supplied.close()
        sdk = original_sync(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        opened.append((sdk, False))
        return sdk

    def make_async(**kwargs):
        constructors.append((True, dict(kwargs)))
        sdk = original_async(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        opened.append((sdk, True))
        return sdk

    monkeypatch.setattr(openai, "OpenAI", make_sync)
    monkeypatch.setattr(openai, "AsyncOpenAI", make_async)
    monkeypatch.setattr(camel_openai, "OpenAI", make_sync)
    monkeypatch.setattr(camel_openai, "AsyncOpenAI", make_async)
    monkeypatch.setattr(camel_openai, "is_langfuse_available", lambda: False)
    monkeypatch.setattr(simulation, "current_output_policy", lambda: None)
    for name, value in {"OASIS_MAX_OUTPUT_TOKENS": 32, "OASIS_OUTPUT_TOKEN_PARAMETER": "max_tokens",
                        "LLM_RUN_BUDGET_TOKENS": 0, "LLM_RUN_BUDGET_USD": 0,
                        "LLM_CACHE_ENABLED": False, "LLM_TIERED_ROUTING": False,
                        "LLM_API_KEY": "offline", "LLM_MODEL_NAME": "gpt-4o-mini",
                        "LLM_BASE_URL": "https://primary.offline.invalid/v1"}.items():
        monkeypatch.setattr(Config, name, value)
    monkeypatch.setattr(Config, "reasoning_extra_body", lambda: None)
    for name, value in {"LLM_PROVIDER": "openai", "LLM_API_KEY": "offline",
                        "LLM_MODEL_NAME": "gpt-4o-mini", "LLM_BASE_URL": "https://primary.offline.invalid/v1",
                        "LLM_FALLBACK_PROVIDER": "", "SIM_LLM_FALLBACK": "true"}.items():
        monkeypatch.setenv(name, value)
    for name in ("LLM_BOOST_API_KEY", "LLM_BOOST_BASE_URL", "LLM_BOOST_MODEL_NAME", "LLM_BOOST_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(oasis, "_record_llm_fallback", lambda *args: None)
    yield harness
    for sdk, asynchronous in opened:
        asyncio.run(sdk.close()) if asynchronous else sdk.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("kind", ["chat", "tools", "parse"])
def test_actual_factory_caps_all_native_requests_without_changing_context(factory, monkeypatch, asynchronous, parameter, kind):
    monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", parameter)
    model = oasis.create_oasis_model({})
    assert isinstance(model, OpenAIModel)
    assert "max_tokens" not in model.model_config_dict and "max_completion_tokens" not in model.model_config_dict
    assert model.token_limit > 32
    initial_config = dict(model.model_config_dict)
    if kind == "parse":
        class Result(BaseModel):
            answer: int
        factory.handler = lambda request: httpx.Response(200, json=response('{"answer": 7}'))
        result = call(model, asynchronous, parse=Result)
        assert result.choices[0].message.parsed.answer == 7
    else:
        tools = [{"type": "function", "function": {"name": "act", "parameters": {"type": "object"}}}]
        assert call(model, asynchronous, tools=tools if kind == "tools" else None).choices[0].message.content == "accepted"
    body = factory.requests[-1]["body"]
    assert body[parameter] == 32
    assert ({"max_tokens", "max_completion_tokens"} & body.keys()) == {parameter}
    assert body["model"] == "gpt-4o-mini"
    assert model.model_config_dict == initial_config


@pytest.mark.parametrize("asynchronous", [False, True])
def test_default_zero_leaves_request_uncapped(factory, monkeypatch, asynchronous):
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 0)
    model = oasis.create_oasis_model({})
    call(model, asynchronous)
    assert not ({"max_tokens", "max_completion_tokens"} & factory.requests[-1]["body"].keys())
    assert model.token_limit > 32


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_output_cap_does_not_truncate_or_reject_actual_chatagent_memory(factory, monkeypatch, parameter):
    monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", parameter)
    model = oasis.create_oasis_model({})
    # A deterministic counter avoids tokenizer downloads while retaining the
    # actual ChatAgent memory/context construction used by OASIS SocialAgent.
    model._token_counter = SimpleNamespace(count_tokens_from_messages=lambda messages:
                                          sum(len(str(message.get("content", ""))) for message in messages))
    system = "Preserve the full offline role and evidence. " * 20
    assert len(system) > Config.OASIS_MAX_OUTPUT_TOKENS
    agent = ChatAgent(system_message=system, model=model, retry_attempts=1)
    result = agent.step("Choose an action.")
    assert result.msgs[0].content == "accepted"
    body = factory.requests[-1]["body"]
    assert body["messages"][0]["content"] == system
    assert body[parameter] == 32


@pytest.mark.parametrize("provider", ["openai", "minimax", "kimi"])
@pytest.mark.parametrize("boost", [False, True])
def test_provider_boost_and_kimi_replacement_keep_route_and_captured_policy(factory, monkeypatch, provider, boost):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    if boost:
        monkeypatch.setenv("LLM_BOOST_API_KEY", "offline-boost")
        monkeypatch.setenv("LLM_BOOST_BASE_URL", "https://boost.offline.invalid/v1")
        monkeypatch.setenv("LLM_BOOST_MODEL_NAME", "gpt-4o")
        monkeypatch.setenv("LLM_BOOST_PROVIDER", "openai")
    original_replace = oasis._inject_coding_agent_ua

    def replace_then_drift(model):
        original_replace(model)
        monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 999)

    monkeypatch.setattr(oasis, "_inject_coding_agent_ua", replace_then_drift)
    model = oasis.create_oasis_model({}, use_boost=boost)
    call(model)
    call(model, True)
    for request in factory.requests:
        assert request["body"]["max_tokens"] == 32
        assert request["body"]["model"] == ("gpt-4o" if boost else "gpt-4o-mini")
        assert request["host"] == ("boost.offline.invalid" if boost else "primary.offline.invalid")
        if provider == "kimi":
            assert request["user_agent"] == Config.LLM_USER_AGENT


def test_positive_bound_policy_and_pinned_zero_override_config_and_environment(factory, monkeypatch):
    monkeypatch.setenv("OASIS_MAX_OUTPUT_TOKENS", "999")
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 888)
    for cap in (5, 0):
        bound = output_policy(cap, "max_completion_tokens")
        monkeypatch.setattr(simulation, "current_output_policy", lambda bound=bound: dict(bound))
        model = oasis.create_oasis_model({})
        call(model)
        body = factory.requests[-1]["body"]
        if cap:
            assert body["max_completion_tokens"] == cap and "max_tokens" not in body
        else:
            assert not ({"max_tokens", "max_completion_tokens"} & body.keys())


def test_request_local_cap_override_does_not_mutate_caller_options(factory):
    model = oasis.create_oasis_model({})
    extra = {"max_tokens": 100, "max_completion_tokens": 200, "thinking": {"type": "disabled"}}
    kwargs = {"messages": [], "model": "gpt-4o-mini", "max_tokens": 1000,
              "max_completion_tokens": 2000, "extra_body": extra}
    model._client.chat.completions.create(**kwargs)
    body = factory.requests[-1]["body"]
    assert body["max_tokens"] == 32 and "max_completion_tokens" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == 1000 and extra["max_tokens"] == 100
    assert extra["max_completion_tokens"] == 200
    oasis._install_native_output_policy(model, output_policy())
    model._client.chat.completions.create(**kwargs)
    assert len(factory.requests) == 2
    with pytest.raises(tel.BudgetExceeded, match="another"):
        oasis._install_native_output_policy(model, output_policy(9))
    assert model._drf_native_output_policy == output_policy()


@pytest.mark.parametrize("provider", ["claude-cli", "codex-cli"])
def test_cli_factory_bypasses_native_output_policy(factory, monkeypatch, provider):
    sentinel = object()
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", True)
    monkeypatch.setattr(oasis, "CLIModel", lambda **kwargs: sentinel)
    assert oasis.create_oasis_model({}) is sentinel
    assert factory.constructors == factory.requests == []


@pytest.mark.parametrize("invalid", [output_policy(True), output_policy(-1), output_policy(1.5),
                                    output_policy(2**53 + 1), output_policy(10, "auto"),
                                    {**output_policy(), "schema": "unknown"},
                                    {**output_policy(), "extra": "ignored?"}])
def test_invalid_bound_policy_stops_before_factory_or_transport(factory, monkeypatch, invalid):
    monkeypatch.setattr(simulation, "current_output_policy", lambda: invalid)
    with pytest.raises(tel.BudgetExceeded, match="output policy"):
        oasis.create_oasis_model({})
    assert factory.constructors == factory.requests == []


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_fallback_preserves_numeric_cap_and_original_route(factory, monkeypatch, parameter):
    monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", parameter)
    model = oasis.create_oasis_model({})
    factory.handler = lambda request: httpx.Response(422, json={"error": {"message": "new_sensitive"}})
    fallback_calls = []

    def fallback_chat(**kwargs):
        fallback_calls.append(kwargs)
        return "fallback accepted"

    monkeypatch.setattr(oasis, "LLMClient", lambda provider: SimpleNamespace(chat=fallback_chat))
    assert call(model).choices[0].message.content == "fallback accepted"
    assert fallback_calls[0]["max_tokens"] == 32
    assert factory.requests[0]["body"][parameter] == 32


def test_policy_validation_returns_independent_value_and_config_validation_reports_errors(factory, monkeypatch):
    original = output_policy(2**53)
    validated = policy.validate_output_policy(original)
    validated["max_output_tokens"] = 1
    assert original["max_output_tokens"] == 2**53
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", True)
    assert any("OASIS_MAX_OUTPUT_TOKENS" in message for message in Config.validate())
    with pytest.raises(ValueError, match="OASIS_MAX_OUTPUT_TOKENS"):
        policy.configured_output_policy()


def test_code_defaults_are_zero_and_legacy_parameter_without_dotenv():
    program = """
import json, os
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ.pop('OASIS_MAX_OUTPUT_TOKENS', None)
os.environ.pop('OASIS_OUTPUT_TOKEN_PARAMETER', None)
from app.utils.oasis_output_policy import configured_output_policy
print(json.dumps(configured_output_policy()))
"""
    result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).parents[1],
                            env=dict(os.environ), capture_output=True, text=True, check=True)
    assert json.loads(result.stdout.splitlines()[-1]) == output_policy(0)
