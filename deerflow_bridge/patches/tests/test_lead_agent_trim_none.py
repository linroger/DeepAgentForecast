"""Construction regressions for the tracked lead-agent summarization overlay.

Run with DeerFlow's environment:
``deer-flow/backend/.venv/bin/python -m pytest -q
deerflow_bridge/patches/tests/test_lead_agent_trim_none.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.config.app_config import AppConfig
from deerflow.config.memory_config import MemoryConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.summarization_config import SummarizationConfig


def _app_config() -> AppConfig:
    return AppConfig(
        models=[
            ModelConfig(
                name="default-model",
                display_name="default-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="default-model",
                supports_thinking=False,
                supports_vision=False,
            ),
            ModelConfig(
                name="active-model",
                display_name="active-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="active-model",
                supports_thinking=False,
                supports_vision=False,
            ),
            ModelConfig(
                name="summary-model",
                display_name="summary-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="summary-model",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(
            use="deerflow.sandbox.local:LocalSandboxProvider"
        ),
    )


def test_factory_forwards_explicit_null_trim(monkeypatch):
    """YAML null must reach LangChain as None, not disappear into its 4K default."""

    app_config = _app_config()
    app_config.summarization = SummarizationConfig(
        enabled=True,
        trim_tokens_to_summarize=None,
    )
    app_config.memory = MemoryConfig(enabled=False)
    fake_model = MagicMock()
    fake_model.with_config.return_value = fake_model
    monkeypatch.setattr(
        lead_agent_module, "create_chat_model", lambda **kwargs: fake_model
    )
    monkeypatch.setattr(
        lead_agent_module,
        "DeerFlowSummarizationMiddleware",
        lambda **kwargs: kwargs,
    )

    middleware = lead_agent_module._create_summarization_middleware(
        app_config=app_config
    )

    assert "trim_tokens_to_summarize" in middleware
    assert middleware["trim_tokens_to_summarize"] is None


def test_null_summary_model_inherits_active_run_model(monkeypatch):
    """A MiniMax run must not silently summarize through the first (Claude) model."""

    app_config = _app_config()
    app_config.summarization = SummarizationConfig(
        enabled=True,
        model_name=None,
    )
    app_config.memory = MemoryConfig(enabled=False)
    fake_model = MagicMock()
    fake_model.with_config.return_value = fake_model
    calls: list[dict] = []

    def fake_create_chat_model(**kwargs):
        calls.append(kwargs)
        return fake_model

    monkeypatch.setattr(
        lead_agent_module, "create_chat_model", fake_create_chat_model
    )
    monkeypatch.setattr(
        lead_agent_module,
        "DeerFlowSummarizationMiddleware",
        lambda **kwargs: kwargs,
    )

    lead_agent_module._create_summarization_middleware(
        app_config=app_config,
        model_name="active-model",
    )

    assert calls[0]["name"] == "active-model"


def test_explicit_summary_model_overrides_active_run_model(monkeypatch):
    """A configured dedicated summarizer remains authoritative."""

    app_config = _app_config()
    app_config.summarization = SummarizationConfig(
        enabled=True,
        model_name="summary-model",
    )
    app_config.memory = MemoryConfig(enabled=False)
    fake_model = MagicMock()
    fake_model.with_config.return_value = fake_model
    calls: list[dict] = []

    def fake_create_chat_model(**kwargs):
        calls.append(kwargs)
        return fake_model

    monkeypatch.setattr(
        lead_agent_module, "create_chat_model", fake_create_chat_model
    )
    monkeypatch.setattr(
        lead_agent_module,
        "DeerFlowSummarizationMiddleware",
        lambda **kwargs: kwargs,
    )

    lead_agent_module._create_summarization_middleware(
        app_config=app_config,
        model_name="active-model",
    )

    assert calls[0]["name"] == "summary-model"


def test_build_middlewares_forwards_runtime_model_to_summary_factory(monkeypatch):
    """Keep the factory call-site wired when the upstream lead agent changes."""

    app_config = _app_config()
    app_config.summarization = SummarizationConfig(enabled=True)
    app_config.memory = MemoryConfig(enabled=False)
    captured: dict[str, object] = {}

    def fake_summary_factory(*, app_config, model_name):
        captured["app_config"] = app_config
        captured["model_name"] = model_name
        return None

    monkeypatch.setattr(
        lead_agent_module,
        "build_lead_runtime_middlewares",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        lead_agent_module,
        "_create_summarization_middleware",
        fake_summary_factory,
    )

    lead_agent_module.build_middlewares(
        {
            "configurable": {
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        },
        model_name="active-model",
        app_config=app_config,
    )

    assert captured == {
        "app_config": app_config,
        "model_name": "active-model",
    }
