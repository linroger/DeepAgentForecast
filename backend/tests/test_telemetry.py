"""Golden tests for LLM telemetry / cache / budget (EXECPLAN2 I-7-3, guards I-5-0/I-6-0/I-5-3)."""

import pytest

from app.utils import telemetry as T


def test_meter_accumulates_by_stage_and_model():
    T.LLMMeter.reset("r")
    T.LLMMeter.record("minimax", "MiniMax-M3", 1000, 500, 100.0, stage="RESEARCH", run_id="r")
    T.LLMMeter.record("minimax", "MiniMax-M3", 2000, 800, 50.0, stage="REPORT", run_id="r")
    T.LLMMeter.record("minimax", "MiniMax-M3", 0, 0, 0.0, cached=True, stage="REPORT", run_id="r")
    snap = T.LLMMeter.snapshot("r")
    assert snap["total"]["calls"] == 3
    assert snap["total"]["cached"] == 1
    assert snap["total"]["total_tokens"] == 4300
    assert set(snap["by_stage"]) == {"RESEARCH", "REPORT"}
    assert snap["total"]["cost_usd"] > 0


def test_cache_put_get_and_key_stability():
    k1 = T.LLMCache.key("p", "m", [{"role": "user", "content": "hi"}], 0.7, 100, None)
    k2 = T.LLMCache.key("p", "m", [{"role": "user", "content": "hi"}], 0.7, 100, None)
    assert k1 == k2
    T.LLMCache.put(k1, "resp")
    assert T.LLMCache.get(k1) == "resp"
    assert T.LLMCache.get("missing") is None


def test_budget_guard(monkeypatch):
    from app.config import Config
    T.LLMMeter.reset("b")
    T.LLMMeter.record("minimax", "MiniMax-M3", 100, 100, 1.0, run_id="b")
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 50, raising=False)
    with pytest.raises(T.BudgetExceeded):
        T.check_budget("b")
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0, raising=False)
    T.check_budget("b")  # unlimited -> no raise


def test_context_roundtrip():
    T.set_run_context("rid", "STAGE")
    assert T.get_run_context() == ("rid", "STAGE")
    T.set_stage("OTHER")
    assert T.get_run_context() == ("rid", "OTHER")


# ---------------------------------------------------------------- OBS-1 tests
def test_cost_is_estimated_flags_proxy_and_unknown():
    # Proxy/aggregator-fronted + unlisted providers are estimates; published ones aren't.
    assert T.cost_is_estimated("gemini") is True
    assert T.cost_is_estimated("proxy") is True
    assert T.cost_is_estimated("antigravity") is True
    assert T.cost_is_estimated("totally-unknown-provider") is True
    assert T.cost_is_estimated("openai") is False
    assert T.cost_is_estimated("minimax") is False
    assert T.cost_is_estimated("claude-cli") is False


def test_gemini_cost_entry_present_and_nonzero():
    # OBS-1: gemini gets an (estimated) non-zero per-1K rate so the USD line is plausible.
    cost = T.estimate_cost("gemini", 1000, 1000)
    assert cost > 0


def test_snapshot_cost_estimated_flag():
    # Authoritative provider -> not estimated.
    T.LLMMeter.reset("est-no")
    T.LLMMeter.record("minimax", "MiniMax-M3", 1000, 500, 10.0, run_id="est-no")
    assert T.LLMMeter.snapshot("est-no")["cost_estimated"] is False

    # Proxy/gemini provider with real token volume -> estimated.
    T.LLMMeter.reset("est-yes")
    T.LLMMeter.record("gemini", "gemini-3.5-flash", 1000, 500, 10.0, run_id="est-yes")
    assert T.LLMMeter.snapshot("est-yes")["cost_estimated"] is True

    # Empty run snapshot also carries the flag (False) without raising.
    assert T.LLMMeter.snapshot("never-seen")["cost_estimated"] is False


def test_status_snapshot_shape():
    T.LLMMeter.reset("st")
    T.LLMMeter.record("minimax", "MiniMax-M3", 100, 100, 5.0, stage="REPORT", run_id="st")
    s = T.LLMMeter.status_snapshot("st")
    assert set(s) == {"run_id", "total", "by_stage", "cost_estimated"}
    assert "by_model" not in s  # compact: per-model breakdown omitted
    assert s["by_stage"]["REPORT"]["calls"] == 1
    assert s["total"]["total_tokens"] == 200


# ---------------------------------------------------------------- R2-EXEC-6 tests
def test_http_client_disabled_by_default(monkeypatch):
    from app.config import Config
    from app.utils.llm_client import LLMClient
    # Unflagged / false -> None (keeps OpenAI SDK default client; degrade-safe).
    monkeypatch.setattr(Config, "LLM_HTTP2", False, raising=False)
    assert LLMClient._build_http_client() is None


def test_http_client_falls_back_to_http1_without_h2(monkeypatch):
    from app.config import Config
    from app.utils.llm_client import LLMClient
    monkeypatch.setattr(Config, "LLM_HTTP2", True, raising=False)
    monkeypatch.setattr(Config, "LLM_HTTP_KEEPALIVE", 128, raising=False)
    client = LLMClient._build_http_client()
    # With h2 absent, construction must still succeed (HTTP/1.1 fallback), not raise.
    assert client is not None
    try:
        # Generous read timeout so long generations aren't truncated at httpx's 5s default.
        assert client.timeout.read and client.timeout.read >= 120.0
    finally:
        client.close()
