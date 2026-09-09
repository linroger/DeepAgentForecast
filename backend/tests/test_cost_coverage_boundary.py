"""Offline physical APIs reject missing historical prices without inventing spend."""

from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from app.config import Config
from app.utils import telemetry as tel
from test_api_cost_boundary import invoke as invoke, _rows
from test_llm_sdk_attempt_boundary import client_factory as client_factory, response, run as run
from test_oasis_physical_usage import models as models


@pytest.fixture(autouse=True)
def rates(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '')


def test_unpriced_history_blocks_later_priced_api_and_disable_recovers(run, invoke, monkeypatch):
    sent = []
    handler = lambda request: (sent.append(request), httpx.Response(200, json=response(inputs=1000, outputs=1000)))[1]
    invoke(handler, 'unknown')()
    before = _rows(run)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    with pytest.raises(tel.BudgetExceeded, match='price|coverage'):
        invoke(handler)()
    assert len(sent) == 1 and _rows(run) == before
    tel.LLMMeter.assert_accounting_available(run.id)
    coverage = tel.LLMMeter.cumulative_snapshot(run.id)['cost_coverage']
    assert coverage['unpriced_api_operations'] == 1
    assert coverage['price_coverage_complete'] is False
    # Later rates cannot reinterpret an immutable historical unpriced receipt.
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '{"unknown":[1,2],"openai":[0,0]}')
    with pytest.raises(tel.BudgetExceeded, match='price|coverage'):
        invoke(handler)()
    assert _rows(run) == before
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 0)
    invoke(handler)()
    assert len(sent) == 2
    assert _rows(run)[0]['metadata']['cost_quote'] == before[0]['metadata']['cost_quote']


@pytest.mark.parametrize('source,provider', [('llm_api_attempt', 'openai'), ('research_stream', 'claude-cli'), ('simulation_process', 'minimax')])
def test_unquoted_prior_observations_block_current_dollar_api(run, invoke, monkeypatch, source, provider):
    tel.LLMMeter.record_snapshot(source, 'historical', provider, 'old-model', 10, 5, 0,
                                run_id=run.id, stage='report', usage_source='known')
    before = tel.LLMMeter.cumulative_snapshot(run.id)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    sends = []
    with pytest.raises(tel.BudgetExceeded, match='price|coverage'):
        invoke(lambda request: sends.append(request))()
    assert sends == []
    after = tel.LLMMeter.cumulative_snapshot(run.id)
    assert after['total'] == before['total']
    assert after['cost_coverage'] == before['cost_coverage']
    tel.LLMMeter.assert_accounting_available(run.id)


@pytest.mark.parametrize('tokens', [0, 2000])
def test_opaque_legacy_baseline_blocks_even_when_recorded_cost_is_zero(run, invoke, monkeypatch, tokens):
    tel.LLMMeter.reset(run.id)
    run.path = run.path.with_name('legacy.sqlite3')
    tel.LLMMeter.attach_durable_run(run.id, str(run.path), 'legacy-owner', legacy_snapshot={
        'total': {'prompt_tokens': tokens, 'completion_tokens': 0, 'total_tokens': tokens,
                  'calls': 1 if tokens else 0, 'cost_usd': 0}})
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    sends = []
    with pytest.raises(tel.BudgetExceeded, match='price|coverage'):
        invoke(lambda request: sends.append(request))()
    assert sends == [] and _rows(run) == []
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap['cost_coverage']['legacy_baseline_present'] is True
    assert snap['total']['total_tokens'] == tokens


def test_real_local_cache_observation_does_not_create_a_price_gap(run, invoke, monkeypatch):
    tel.LLMMeter.record('openai', 'offline-model', 0, 0, 0, cached=True, run_id=run.id)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    sends = []
    invoke(lambda request: (sends.append(request), httpx.Response(200, json=response()))[1])()
    state = tel.LLMMeter.cumulative_snapshot(run.id)['cost_coverage']
    assert state['cache_only_operations'] == state['priced_api_operations'] == 1
    assert state['non_api_operations'] == 0 and state['price_coverage_complete'] is True
    assert len(sends) == 1


def test_explicit_zero_price_remains_complete_without_inferred_free_provider(run, invoke, monkeypatch):
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '{"openai":[0,0]}')
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    invoke(lambda request: httpx.Response(200, json=response()))()
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap['total']['total_tokens'] == 15 and snap['total']['cost_usd'] == 0
    assert snap['cost_coverage']['priced_api_operations'] == 1
    assert snap['cost_coverage']['price_coverage_complete'] is True


def test_coverage_decision_is_captured_before_shared_sdk_options_mutate_config(run, client_factory, monkeypatch):
    tel.LLMMeter.record_snapshot('llm_api_attempt', 'historical', 'openai', 'old', 1, 1, 0, run_id=run.id)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    sent = []
    client, sdk = client_factory(lambda request: (sent.append(request), httpx.Response(200, json=response()))[1])
    original = sdk.with_options
    def change(**kwargs):
        monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 0)
        return original(**kwargs)
    monkeypatch.setattr(sdk, 'with_options', change)
    with pytest.raises(tel.BudgetExceeded, match='price|coverage'):
        client.chat([], max_tokens=10)
    assert sent == [] and len(_rows(run)) == 1
    tel.LLMMeter.assert_accounting_available(run.id)


def test_price_only_check_preserves_same_owner_priced_concurrency(run, client_factory, monkeypatch):
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    entered, release = threading.Event(), threading.Event()
    sent = []
    def transport(request):
        sent.append(request)
        if len(sent) == 1:
            entered.set()
            assert release.wait(5)
        return httpx.Response(200, json=response())
    client, _ = client_factory(transport)
    def invoke_request():
        tel.set_run_context(run.id, 'report')
        return client.chat([], max_tokens=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke_request)
        try:
            assert entered.wait(5)
            coverage = tel.LLMMeter.cumulative_snapshot(run.id)['cost_coverage']
            assert coverage['priced_api_operations'] == coverage['inexact_api_operations'] == 1
            assert coverage['price_coverage_complete'] is True
            assert pool.submit(invoke_request).result(timeout=5) == 'accepted'
        finally:
            release.set()
        assert first.result(timeout=5) == 'accepted'
    assert len(sent) == 2


@pytest.mark.parametrize('policy', ['dollar', 'token'])
def test_captured_admission_cannot_be_skipped_when_telemetry_is_disabled_and_binding_resets(
        run, client_factory, monkeypatch, policy):
    monkeypatch.setattr(Config, 'LLM_TELEMETRY_ENABLED', False)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1 if policy == 'dollar' else 0)
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_TOKENS', 100 if policy == 'token' else 0)
    tel.LLMMeter.record_snapshot('llm_api_attempt', 'history', 'openai', 'old', 1, 1, 0,
                                run_id=run.id, usage_source='known')
    before = _rows(run)
    sent = []
    client, sdk = client_factory(lambda request: (sent.append(request), httpx.Response(200, json=response()))[1])
    original = sdk.with_options
    def reset_binding(**kwargs):
        tel.LLMMeter.reset(run.id)
        return original(**kwargs)
    monkeypatch.setattr(sdk, 'with_options', reset_binding)
    with pytest.raises(tel.BudgetExceeded, match='durable|binding'):
        client._create_openai_completion(sdk, 'openai', 'offline-model',
                                        {'model': 'offline-model', 'messages': [], 'max_tokens': 10})
    assert sent == [] and _rows(run) == before
    assert run.id not in tel.LLMMeter._runs


def test_native_known_usage_on_failed_http_response_is_price_and_usage_covered(run, models, monkeypatch):
    from test_oasis_physical_usage import call
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    sends = []
    def handler(request):
        sends.append(request)
        return httpx.Response(429 if len(sends) == 1 else 200, json=response())
    model = models(handler, retries=1)
    assert call(model, False).choices[0].message.content == 'accepted'
    snap = tel.LLMMeter.cumulative_snapshot(run.id)
    assert snap['api_operation_state']['unknown'] == 1
    assert snap['cost_coverage']['inexact_api_operations'] == 0
    assert snap['cost_coverage']['priced_api_operations'] == 2
    assert snap['cost_coverage']['price_coverage_complete'] is True
    assert len(sends) == 2
