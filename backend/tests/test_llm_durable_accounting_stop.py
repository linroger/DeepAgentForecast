"""A failed usage write must not become permission for another provider call."""

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils import llm_client as lc
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerStorageError


@pytest.fixture
def bound_run(tmp_path, monkeypatch):
    rid = "pipe-ledger-stop"
    path = tmp_path / "usage.sqlite3"
    previous = tel.get_run_context()
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, str(path))
    tel.set_run_context(rid, "report")
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setattr(lc, "_CB_STATE", {})
    monkeypatch.setattr(lc, "_FB_AUTH_UNAVAILABLE_UNTIL", {})
    monkeypatch.setattr(lc, "_retry_delay", lambda *_: 0)
    yield rid, path
    tel.LLMMeter.reset(rid)
    tel.set_run_context(*previous)


def _response():
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                           choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))])


def _client(create):
    client = lc.LLMClient(provider="claude-cli", model="fixture")
    client.provider = "minimax"
    client._openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client


def _full_disk(*args, **kwargs):
    raise sqlite3.OperationalError("simulated full disk")


def test_native_deleted_ledger_failure_escapes_and_blocks_react(bound_run, monkeypatch):
    _rid, path = bound_run
    calls = []

    def create(**kwargs):
        calls.append("native")
        path.unlink()
        return _response()

    client = _client(create)
    monkeypatch.setattr(client, "_chat_openai", lambda *a, **k: calls.append("react") or "ok")
    with pytest.raises(UsageLedgerStorageError):
        client.chat_with_tools([], [])
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert calls == ["native"]


def test_readable_database_after_failed_native_write_does_not_clear_stop(bound_run, monkeypatch):
    rid, path = bound_run
    calls = []
    client = _client(lambda **k: calls.append("native") or _response())
    with monkeypatch.context() as scoped:
        scoped.setattr(UsageLedger, "_insert_delta", staticmethod(_full_disk))
        with pytest.raises(UsageLedgerStorageError):
            client.chat_with_tools([], [])
    assert UsageLedger(str(path)).snapshot(rid)["total"]["calls"] == 0
    # The database is readable and writable again, but the missing observation
    # remains unresolved. Both a fresh client and another method must stop.
    client2 = _client(lambda **k: calls.append("new-client") or _response())
    with pytest.raises(UsageLedgerStorageError, match="failed during this attempt"):
        client2.chat_with_tools([], [])
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert calls == ["native"]


@pytest.mark.parametrize("native", [False, True])
def test_missing_ledger_before_request_admits_no_provider_call(bound_run, monkeypatch, native):
    _, path = bound_run
    calls = []
    client = _client(lambda **k: calls.append("native") or _response())
    monkeypatch.setattr(client, "_chat_openai", lambda *a, **k: calls.append("chat") or "ok")
    path.unlink()
    with pytest.raises(UsageLedgerStorageError):
        client.chat_with_tools([], []) if native else client.chat([])
    assert calls == []
    assert not Path(path).exists()


def test_chat_write_failure_escapes_without_fallback_or_retry(bound_run, monkeypatch):
    calls = []
    client = _client(lambda **k: calls.append("chat") or _response())
    monkeypatch.setattr(client, "_try_fallback", lambda *a, **k: calls.append("fallback") or "fallback")
    monkeypatch.setattr(UsageLedger, "_insert_delta", staticmethod(_full_disk))
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert calls == ["chat"]


def test_fallback_write_failure_preserves_accounting_error_and_stops(bound_run, monkeypatch):
    calls = []
    client = _client(lambda **k: _response())

    def primary(*args, **kwargs):
        calls.append("primary")
        raise ValueError("primary rejected request")

    monkeypatch.setattr(client, "_chat_openai", primary)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "codex-cli")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fixture-fallback")
    monkeypatch.setattr(lc.LLMClient, "_chat_codex_cli", lambda *a, **k: calls.append("fallback") or "ok")
    monkeypatch.setattr(UsageLedger, "_insert_delta", staticmethod(_full_disk))
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert calls == ["primary", "fallback"]


@pytest.mark.parametrize("native", [False, True])
def test_accounting_error_inside_provider_adapter_is_never_retried(bound_run, monkeypatch, native):
    calls = []

    def fail(*args, **kwargs):
        calls.append("attempt")
        raise UsageLedgerStorageError("adapter accounting failed")

    client = _client(fail)
    monkeypatch.setattr(client, "_chat_openai", fail)
    monkeypatch.setattr(client, "_try_fallback", lambda *a, **k: calls.append("fallback") or "ok")
    with pytest.raises(UsageLedgerStorageError):
        client.chat_with_tools([], []) if native else client.chat([])
    assert calls == ["attempt"]


def test_retry_boundary_rechecks_storage_after_provider_failure(bound_run, monkeypatch):
    _, path = bound_run
    calls = []
    client = _client(lambda **k: _response())

    def fail(*args, **kwargs):
        calls.append("attempt")
        path.unlink()
        raise RuntimeError("transient provider failure")

    monkeypatch.setattr(client, "_chat_openai", fail)
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert calls == ["attempt"]


@pytest.mark.parametrize("invalid", [False, "", None, -1, float("nan")])
def test_invalid_bound_observation_is_typed_and_stops_next_provider(bound_run, invalid):
    rid, path = bound_run
    with pytest.raises(UsageLedgerStorageError, match="Invalid observation"):
        tel.LLMMeter.record_snapshot("fixture", "invalid", "minimax", "m", invalid, 5, 1.0,
                                    run_id=rid, stage="report")
    calls = []
    client = _client(lambda **k: calls.append("native") or _response())
    with pytest.raises(UsageLedgerStorageError, match="failed during this attempt"):
        client.chat_with_tools([], [])
    assert calls == []
    assert UsageLedger(str(path)).snapshot(rid)["total"]["calls"] == 0


def test_invalid_bound_identity_is_typed_and_stops_next_provider(bound_run):
    rid, _ = bound_run
    with pytest.raises(UsageLedgerStorageError, match="Invalid attribution"):
        tel.LLMMeter.record_snapshot("fixture", "", "minimax", "m", 10, 5, 1.0,
                                    run_id=rid, stage="report")
    with pytest.raises(UsageLedgerStorageError):
        tel.LLMMeter.assert_accounting_available(rid)
