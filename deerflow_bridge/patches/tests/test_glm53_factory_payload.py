"""Real LangChain/OpenAI serialization through a local HTTP mock, never a provider.

Run using the native DeerFlow interpreter and the guarded offline pytest runner.
The overlay is applied only to a temporary copy of the installed native factory.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path

import httpx
import pytest
import yaml
from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.config.model_config import ModelConfig
from deerflow.models import factory as installed_factory
from deerflow.models.assistant_payload_replay import restore_assistant_payloads
from deerflow.models.patched_deepseek import PatchedChatDeepSeek


ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "deerflow_bridge/patches/apply_model_factory_overlays.py"
CONFIG = ROOT / "deerflow_bridge/config.yaml"
REASONING = "Exact reasoning 中文\r\nwith spacing  retained."
TOOL = {"type": "function", "function": {"name": "read_evidence", "description": "Read archived evidence",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def factory(tmp_path, record_testsuite_property):
    overlay = load_module(OVERLAY, "glm53_factory_overlay")
    target = tmp_path / overlay.MODEL_FACTORY_PATH
    target.parent.mkdir(parents=True)
    source = Path(inspect.getfile(installed_factory)).read_text()
    target.write_text(source)
    overlay.apply(tmp_path)
    assert overlay.apply(tmp_path) == "already_applied"
    for path in (OVERLAY, CONFIG, Path(__file__), Path(inspect.getfile(installed_factory)),
                 Path(inspect.getfile(PatchedChatDeepSeek)), Path(inspect.getfile(restore_assistant_payloads)), target):
        record_testsuite_property("source_sha256:" + str(path), hashlib.sha256(path.read_bytes()).hexdigest())
    return load_module(target, "glm53_test_factory")


class Config:
    def __init__(self, model):
        self.models = [model]

    def get_model_config(self, name):
        return self.models[0] if self.models[0].name == name else None


def settings(alias="glm", model_id="glm-5.3", **updates):
    values = {
        "name": alias, "display_name": "Fixture", "description": None,
        "use": "langchain_openai:ChatOpenAI", "model": model_id,
        "api_key": "offline-fixture", "base_url": "https://glm-offline.invalid/v4",
        "max_tokens": 65536, "max_retries": 0, "context_window_tokens": 1048576,
        "supports_thinking": True,
        # Stale configs intentionally exercise migration at the factory boundary.
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
        "when_thinking_disabled": {"extra_body": {"thinking": {"type": "disabled"}}},
    }
    values.update(updates)
    return ModelConfig(**values)


@pytest.fixture
def wire():
    captured = []

    def handle(request):
        assert request.url.host == "glm-offline.invalid", "adapter changed the configured provider endpoint"
        payload = json.loads(request.content)
        captured.append(payload)
        message = {"role": "assistant", "content": "", "reasoning_content": REASONING,
                   "tool_calls": [{"id": "call-1", "type": "function",
                                   "function": {"name": "read_evidence", "arguments": '{"id":"fixture"}'}}]}
        base = {"id": "offline", "created": 1, "model": payload["model"]}
        if payload.get("stream"):
            chunks = [
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": REASONING[:13]}, "finish_reason": None}]},
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"reasoning_content": REASONING[13:], "tool_calls": [{"index": 0, **message["tool_calls"][0]}]}, "finish_reason": None}]},
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
            body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
        return httpx.Response(200, json={**base, "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 23, "completion_tokens": 7, "total_tokens": 30}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        yield captured, {"http_client": client, "http_async_client": async_client}
        asyncio.run(async_client.aclose())


@pytest.mark.parametrize("alias", ["glm", "custom-investigator"])
@pytest.mark.parametrize("thinking,effort,expected", [
    (False, None, "low"), (False, "max", "low"), (True, None, "max"),
    (True, "low", "low"), (True, "high", "high"), (True, "max", "max"),
    (True, "medium", "max"),
])
def test_actual_serialized_glm_contract_independent_of_alias(factory, wire, alias, thinking, effort, expected):
    captured, clients = wire
    config = settings(alias)
    before = config.model_dump()
    kwargs = {**clients, **({"reasoning_effort": effort} if effort else {})}
    model = factory.create_chat_model(alias, thinking_enabled=thinking, app_config=Config(config), attach_tracing=False, **kwargs)
    model.invoke("offline question")
    assert captured[0]["thinking"]["type"] == "enabled"
    assert captured[0]["reasoning_effort"] == expected
    assert captured[0]["max_tokens"] == 65536
    assert "context_window_tokens" not in captured[0]
    assert config.model_dump() == before


def test_explicit_physical_output_cap_and_body_cannot_disable_glm(factory, wire):
    captured, clients = wire
    model = factory.create_chat_model(
        "glm", thinking_enabled=False, app_config=Config(settings()), attach_tracing=False,
        max_tokens=12288, extra_body={"thinking": {"type": "disabled", "clear_thinking": False}, "reasoning_effort": "none"},
        **clients,
    )
    model.invoke("bounded synthesis")
    assert captured[0]["max_tokens"] == 12288
    assert captured[0]["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert captured[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize("mode", ["invoke", "stream", "ainvoke", "astream"])
def test_provider_reasoning_survives_tool_round_trip_exactly(factory, wire, mode):
    captured, clients = wire
    model = factory.create_chat_model("glm", thinking_enabled=True, app_config=Config(settings()), attach_tracing=False, **clients)
    bound = model.bind_tools([TOOL])
    original = HumanMessage("question")
    if mode == "invoke":
        result = bound.invoke([original])
    elif mode == "stream":
        chunks = list(bound.stream([original]))
        result = sum(chunks[1:], chunks[0])
    else:
        async def run():
            if mode == "ainvoke":
                return await bound.ainvoke([original])
            chunks = [chunk async for chunk in bound.astream([original])]
            return sum(chunks[1:], chunks[0])
        result = asyncio.run(run())
    assert result.additional_kwargs["reasoning_content"] == REASONING
    bound.invoke([original, result, ToolMessage("archived body", tool_call_id="call-1")])
    assistant = captured[-1]["messages"][1]
    assert assistant["reasoning_content"] == REASONING
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert captured[-1]["tools"] == [TOOL]


def test_non_glm_model_keeps_disable_semantics_and_constructor(factory, wire):
    captured, clients = wire
    config = settings(alias="glm", model_id="glm-4.6")
    model = factory.create_chat_model("glm", thinking_enabled=False, app_config=Config(config), attach_tracing=False, **clients)
    model.invoke("legacy question")
    assert type(model).__name__ == "ChatOpenAI"
    assert captured[0]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in captured[0]


def test_tracked_glm_stanza_declares_actual_capabilities():
    stanza = next(model for model in yaml.safe_load(CONFIG.read_text())["models"] if model["name"] == "glm")
    assert stanza["model"] == "glm-5.3"
    assert stanza["display_name"] == "GLM-5.3 (Z.ai)"
    assert stanza["context_window_tokens"] == 1048576
    assert stanza["max_tokens"] == 65536
    assert stanza["when_thinking_disabled"]["extra_body"]["thinking"]["type"] == "enabled"
    assert stanza["when_thinking_disabled"]["reasoning_effort"] == "low"


@pytest.mark.parametrize("location", ["config", "body", "caller"])
def test_explicit_research_effort_retained_from_supported_config_shapes(factory, wire, location):
    captured, clients = wire
    updates, kwargs = {}, {}
    if location == "config":
        updates["reasoning_effort"] = "high"
    elif location == "body":
        updates["when_thinking_enabled"] = {"extra_body": {"reasoning_effort": "high"}}
    else:
        updates["reasoning_effort"] = "low"
        kwargs["reasoning_effort"] = "high"
    model = factory.create_chat_model("glm", thinking_enabled=True, app_config=Config(settings(**updates)),
                                      attach_tracing=False, **clients, **kwargs)
    model.invoke("research")
    assert captured[0]["reasoning_effort"] == "high"
    assert captured[0]["thinking"]["type"] == "enabled"


@pytest.mark.parametrize("effort", ["low", "high"])
@pytest.mark.parametrize("location", ["config", "body"])
def test_native_none_effort_preserves_explicit_model_policy(factory, wire, effort, location):
    captured, clients = wire
    updates = {"reasoning_effort": effort} if location == "config" else {
        "when_thinking_enabled": {"extra_body": {"reasoning_effort": effort}},
    }
    model = factory.create_chat_model(
        "glm", thinking_enabled=True, reasoning_effort=None,
        app_config=Config(settings(**updates)), attach_tracing=False, **clients,
    )
    model.invoke("native lead-agent question")
    assert captured[0]["reasoning_effort"] == effort


@pytest.mark.parametrize("thinking", [False, True])
def test_other_tracked_model_constructor_settings_match_original(factory, monkeypatch, thinking):
    class FakeChatModel:
        model_fields = {}

        def __init__(self, **kwargs):
            self.settings = kwargs
            self.callbacks = []

    monkeypatch.setattr(installed_factory, "resolve_class", lambda *args: FakeChatModel)
    monkeypatch.setattr(factory, "resolve_class", lambda *args: FakeChatModel)
    for row in yaml.safe_load(CONFIG.read_text())["models"]:
        if row["model"].lower() == "glm-5.3":
            continue
        config = Config(ModelConfig(**row))
        args = {"app_config": config, "thinking_enabled": thinking, "attach_tracing": False}
        original = installed_factory.create_chat_model(row["name"], **args)
        updated = factory.create_chat_model(row["name"], **args)
        assert original.settings == updated.settings, row["name"]


@pytest.mark.parametrize("metadata_applied", [False, True])
def test_overlay_upgrades_pristine_and_metadata_only_factories_atomically(tmp_path, metadata_applied):
    overlay = load_module(OVERLAY, "glm53_overlay_upgrade")
    source = Path(inspect.getfile(installed_factory)).read_text()
    if not metadata_applied:
        source = source.replace(overlay._METADATA_SAFE_EXCLUDE_TAIL, overlay._ORIGINAL_EXCLUDE_TAIL)
    target = tmp_path / overlay.MODEL_FACTORY_PATH
    target.parent.mkdir(parents=True)
    target.write_text(source)
    assert overlay.apply(tmp_path) == "applied"
    result = target.read_bytes()
    assert overlay.apply(tmp_path) == "already_applied"
    assert target.read_bytes() == result
    assert target.read_text().count("    is_glm_53 = ") == 1


def test_overlay_missing_factory_anchor_does_not_partially_write(tmp_path):
    overlay = load_module(OVERLAY, "glm53_overlay_drift")
    target = tmp_path / overlay.MODEL_FACTORY_PATH
    target.parent.mkdir(parents=True)
    source = Path(inspect.getfile(installed_factory)).read_text().replace(overlay._MODEL_CONSTRUCTION, "    return None\n")
    target.write_text(source)
    with pytest.raises(RuntimeError, match="context drifted"):
        overlay.apply(tmp_path)
    assert target.read_text() == source
