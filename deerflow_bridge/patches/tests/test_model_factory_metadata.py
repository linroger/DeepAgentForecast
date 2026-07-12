"""Regression tests for non-request model metadata in DeerFlow config."""

from __future__ import annotations

from deerflow.config.model_config import ModelConfig
from deerflow.models import factory


class _Config:
    def __init__(self, model_config: ModelConfig):
        self.models = [model_config]
        self._model_config = model_config

    def get_model_config(self, name: str):
        return self._model_config if name == self._model_config.name else None


def test_context_window_metadata_is_not_forwarded_to_provider(monkeypatch):
    """Budgeting metadata must never become an OpenAI request parameter."""
    captured = {}

    class FakeChatModel:
        model_fields = {}

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.callbacks = []

    model_config = ModelConfig(
        name="minimax",
        display_name="MiniMax",
        description=None,
        use="deerflow.models.patched_minimax:PatchedChatMiniMax",
        model="MiniMax-M3",
        context_window_tokens=1_000_000,
    )
    monkeypatch.setattr(factory, "resolve_class", lambda *_args: FakeChatModel)

    factory.create_chat_model(
        "minimax", app_config=_Config(model_config), attach_tracing=False)

    assert "context_window_tokens" not in captured
