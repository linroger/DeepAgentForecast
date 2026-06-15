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
