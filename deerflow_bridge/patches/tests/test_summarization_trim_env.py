"""Env-knob regressions for inter-phase thread compaction (token lever 2).

Run with DeerFlow's environment:
``deer-flow/backend/.venv/bin/python -m pytest -q
deerflow_bridge/patches/tests/test_summarization_trim_env.py``.

RESEARCH_TRIM_TOKENS_TO_SUMMARIZE must be a pure A/B override at the single
DeerFlowSummarizationMiddleware construction site: unset preserves today's
behavior exactly (config.yaml ships ``trim_tokens_to_summarize: null`` → None →
summarize the complete discarded segment), set is honored. The import resolves
the DEPLOYED middleware copy (patches/tests/conftest.py puts
deer-flow/backend/packages/harness first on sys.path), so a forgotten re-sync of
patches/middlewares fails these tests too.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from deerflow.agents.middlewares import summarization_middleware as sm


def _middleware(**kwargs):
    model = MagicMock()
    model.config = {}
    model.with_config.return_value = model
    return sm.DeerFlowSummarizationMiddleware(
        model=model,
        trigger=("tokens", 80_000),
        keep=("tokens", 16_000),
        token_counter=lambda messages: 0,
        **kwargs,
    )


def test_unset_env_preserves_explicit_null(monkeypatch):
    """Today's default: config null flows through as None (full-segment summary)."""
    monkeypatch.delenv(sm.TRIM_TOKENS_ENV, raising=False)
    mw = _middleware(trim_tokens_to_summarize=None)
    assert mw.trim_tokens_to_summarize is None


def test_unset_env_preserves_configured_integer(monkeypatch):
    monkeypatch.delenv(sm.TRIM_TOKENS_ENV, raising=False)
    mw = _middleware(trim_tokens_to_summarize=12_345)
    assert mw.trim_tokens_to_summarize == 12_345


def test_unset_env_preserves_langchain_default_when_kwarg_omitted(monkeypatch):
    """No env → nothing injected: an omitted kwarg still gets LangChain's default."""
    from langchain.agents.middleware.summarization import _DEFAULT_TRIM_TOKEN_LIMIT

    monkeypatch.delenv(sm.TRIM_TOKENS_ENV, raising=False)
    mw = _middleware()
    assert mw.trim_tokens_to_summarize == _DEFAULT_TRIM_TOKEN_LIMIT


def test_env_integer_overrides_config_null(monkeypatch):
    monkeypatch.setenv(sm.TRIM_TOKENS_ENV, "24000")
    mw = _middleware(trim_tokens_to_summarize=None)
    assert mw.trim_tokens_to_summarize == 24_000


def test_env_integer_overrides_configured_integer(monkeypatch):
    monkeypatch.setenv(sm.TRIM_TOKENS_ENV, "24000")
    mw = _middleware(trim_tokens_to_summarize=4_000)
    assert mw.trim_tokens_to_summarize == 24_000


def test_env_none_sentinels_force_full_segment(monkeypatch):
    for sentinel in ("none", "NULL", " Full "):
        monkeypatch.setenv(sm.TRIM_TOKENS_ENV, sentinel)
        mw = _middleware(trim_tokens_to_summarize=8_000)
        assert mw.trim_tokens_to_summarize is None, sentinel


def test_invalid_and_nonpositive_env_values_are_ignored(monkeypatch):
    for bad in ("abc", "-5", "0", "1.5"):
        monkeypatch.setenv(sm.TRIM_TOKENS_ENV, bad)
        mw = _middleware(trim_tokens_to_summarize=None)
        assert mw.trim_tokens_to_summarize is None, bad
        mw = _middleware(trim_tokens_to_summarize=8_000)
        assert mw.trim_tokens_to_summarize == 8_000, bad


def test_blank_env_counts_as_unset(monkeypatch):
    monkeypatch.setenv(sm.TRIM_TOKENS_ENV, "   ")
    assert sm._resolve_trim_tokens_override() == (False, None)
    monkeypatch.delenv(sm.TRIM_TOKENS_ENV, raising=False)
    assert sm._resolve_trim_tokens_override() == (False, None)
