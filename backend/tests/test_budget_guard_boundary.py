"""Provider admission uses narrow totals while recovery checks stay independent."""

import sqlite3

import httpx
import pytest

from app.config import Config
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerStorageError, UsageLedgerUnresolvedError
from test_llm_sdk_attempt_boundary import client_factory as client_factory, response, run as run
from test_oasis_physical_usage import call, models as models


def forbid_full_projection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A provider budget guard must not build a full usage projection")

    monkeypatch.setattr(UsageLedger, "snapshot", forbidden)
    monkeypatch.setattr(UsageLedger, "_api_operation_state", forbidden)


@pytest.mark.parametrize("kind", ["tokens", "cost", "both"])
@pytest.mark.parametrize("native_tools", [False, True])
def test_shared_api_stops_after_usage_crossing_without_full_projection(
        run, client_factory, monkeypatch, kind, native_tools):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 20 if kind != "cost" else 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 1 if kind != "tokens" else 0)
    # Five reported output tokens cost two fixture dollars; use the captured
    # rate contract rather than replacing the legacy estimate function.
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[0,400000]}')
    transmissions = []

    def transport(request):
        transmissions.append(request)
        return httpx.Response(200, json=response("", 20, 5))

    client, _ = client_factory(transport)
    forbid_full_projection(monkeypatch)
    def invoke():
        return client.chat_with_tools([], [], max_tokens=5) if native_tools else client.chat([], max_tokens=5)
    with pytest.raises(tel.BudgetExceeded):
        invoke()
    # The repeated call is denied before dispatch; neither a response retry nor
    # a later logical call should evade the same committed cumulative total.
    with pytest.raises(tel.BudgetExceeded):
        invoke()
    assert len(transmissions) == 1
    with sqlite3.connect(run.path) as connection:
        assert connection.execute(
            "SELECT SUM(calls),SUM(total_tokens),SUM(cost_usd) FROM usage_deltas WHERE run_id=?",
            (run.id,),
        ).fetchone() == (1, 25, 2)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["tokens", "cost"])
def test_native_sdk_stops_without_retry_or_full_projection(run, models, monkeypatch, asynchronous, kind):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 30 if kind == "tokens" else 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 1 if kind == "cost" else 0)
    monkeypatch.setattr(Config, "LLM_COST_PER_MTOK", '{"openai":[0,400000]}')
    transmissions = []

    def transport(request):
        transmissions.append(request)
        return httpx.Response(200, json=response("", 40, 5))

    model = models(transport, retries=3)
    model.model_config_dict = {"max_tokens": 5}
    forbid_full_projection(monkeypatch)
    with pytest.raises(tel.BudgetExceeded):
        call(model, asynchronous)
    with pytest.raises(tel.BudgetExceeded):
        call(model, asynchronous)
    assert len(transmissions) == 1


def test_unresolved_admission_is_not_weakened_by_narrow_budget_read(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 100)
    UsageLedger(str(run.path)).record_snapshot(
        run_id=run.id, attempt_id="different-owner", source="llm_api_attempt", operation_id="unfinished",
        metadata={"stage": "run", "provider": "openai", "model": "fixture", "usage_class": "unknown",
                  "billing_basis": "api"}, counters={}, status="in_flight")
    transmissions = []
    client, _ = client_factory(lambda request: transmissions.append(request))
    forbid_full_projection(monkeypatch)
    with pytest.raises(UsageLedgerUnresolvedError):
        client.chat([])
    assert transmissions == []


def test_missing_storage_stops_before_any_provider_work(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 100)
    transmissions = []
    client, _ = client_factory(lambda request: transmissions.append(request))
    run.path.unlink()
    with pytest.raises(UsageLedgerStorageError):
        client.chat([])
    assert transmissions == []
    assert not run.path.exists()
