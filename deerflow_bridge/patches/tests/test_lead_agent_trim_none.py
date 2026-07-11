"""Construction regression for the tracked lead-agent summarization overlay.

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
                name="safe-model",
                display_name="safe-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="safe-model",
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
