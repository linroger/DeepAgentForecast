"""Pricing gaps remain explicit in saved, restarted and public accounting."""

import json
from pathlib import Path

from flask import Flask
import pytest

from app.config import Config
from app.api import research_bp
from app.services import pipeline_orchestrator as po
from app.utils.api_cost import capture_cost_quote
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger
from test_unresolved_usage_pipeline import isolated as isolated
from test_simulation_shared_usage import launch as launch, _environment, _CHILD


def _record(ledger, state, provider='unknown', identity='priced-history'):
    tel.LLMMeter.attach_durable_run(state.pipeline_id, str(ledger.path), 'new-owner', existing_only=True)
    return tel.LLMMeter.record_snapshot('llm_api_attempt', identity, provider, 'fixture', 10, 5, 0,
        run_id=state.pipeline_id, stage='report', usage_source='known',
        cost_quote=capture_cost_quote(provider, 'fixture'))


def _status(state):
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix='/api/research')
    response = app.test_client().get(f'/api/research/status/{state.pipeline_id}')
    assert response.status_code == 200
    return response.get_json()['data']['live']


def test_saved_restarted_public_status_retains_unpriced_coverage(isolated, monkeypatch):
    ledger, state, _ = isolated
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '')
    _record(ledger, state)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    orch._flush_run_telemetry(state)
    saved = json.loads(Path(orch._tel_path).read_text())
    expected = saved['cost_coverage']
    assert expected == saved['usage_accounting']['cost_coverage']
    assert expected['unpriced_api_operations'] == 1 and expected['price_coverage_complete'] is False
    tel.LLMMeter.reset(state.pipeline_id)
    before = ledger.path.read_bytes()
    spend = _status(state)['spend_so_far']
    assert spend['cost_usd'] == 0 and spend['tokens'] == 15
    assert spend['cost_coverage'] == expected and spend['available'] is True
    assert ledger.path.read_bytes() == before
    assert not tel.LLMMeter.is_durable_run(state.pipeline_id)


def test_attempt_filter_does_not_hide_historical_price_gap_in_public_status(isolated, monkeypatch):
    ledger, state, _ = isolated
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '')
    _record(ledger, state)
    tel.LLMMeter.reset(state.pipeline_id)
    tel.LLMMeter.attach_durable_run(state.pipeline_id, str(ledger.path), 'later-owner', existing_only=True)
    assert tel.LLMMeter.snapshot(state.pipeline_id)['total']['total_tokens'] == 0
    assert tel.LLMMeter.snapshot(state.pipeline_id)['cost_coverage']['unpriced_api_operations'] == 1
    assert _status(state)['spend_so_far']['cost_coverage']['unpriced_api_operations'] == 1


def test_flush_uses_one_cumulative_price_coverage_view_after_interleaved_observation(isolated, monkeypatch):
    ledger, state, _ = isolated
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '')
    _record(ledger, state, provider='openai')
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    original = tel.LLMMeter.snapshot
    def interleave(run_id=None):
        before = original(run_id)
        assert before['cost_coverage']['price_coverage_complete'] is True
        tel.LLMMeter.record_snapshot('research_stream', 'late-history', 'claude-cli', 'fixture', 20, 0, 0,
            run_id=state.pipeline_id, stage='research', usage_source='known')
        return before
    monkeypatch.setattr(tel.LLMMeter, 'snapshot', interleave)
    orch._flush_run_telemetry(state)
    saved = json.loads(Path(orch._tel_path).read_text())
    assert saved['cost_coverage'] == saved['usage_accounting']['cost_coverage']
    assert saved['cost_coverage']['non_api_operations'] == 1
    assert saved['cost_coverage']['price_coverage_complete'] is False


@pytest.mark.parametrize('gap', [True, False])
def test_child_checks_shared_historical_prices_before_dollar_enabled_send(launch, monkeypatch, gap):
    import subprocess
    import sys
    monkeypatch.setattr(Config, 'LLM_COST_PER_MTOK', '')
    if gap:
        tel.LLMMeter.record_snapshot('llm_api_attempt', 'parent-history', 'unknown', 'fixture', 100, 0, 0,
            run_id=launch.state.pipeline_id, stage='report', usage_source='known',
            cost_quote=capture_cost_quote('unknown', 'fixture'))
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_USD', 1)
    launch.context = launch.orch._simulation_usage_context(launch.state, launch.sim_id)
    env = _environment(launch)
    env['LLM_COST_PER_MTOK'] = '{"openai":[1,2]}'
    # Capture only typed failure and mock transmission count from the actual
    # bootstrap/factory/shared-SDK child, never provider payloads.
    import re
    def capture_denial(match):
        indent = match.group(1)
        return (indent + 'try:\n' + indent + '    assert client.chat([]) == "accepted"\n'
                + indent + 'except tel.BudgetExceeded:\n'
                + indent + '    print(json.dumps({"denied": True, "provider_calls": len(seen)}))\n'
                + indent + '    sys.exit(29)')
    program = re.sub(r'(?m)^([ \t]*)assert client\.chat\(\[\]\) == "accepted"$', capture_denial, _CHILD)
    compile(program, '<cost-coverage-child>', 'exec')
    result = subprocess.run([sys.executable, '-c', program, str(launch.config), 'one'],
        env=env, cwd=launch.config.parent, capture_output=True, text=True, timeout=30)
    assert result.returncode == (29 if gap else 0), result.stderr + result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload['provider_calls'] == (0 if gap else 1)
    snap = UsageLedger(str(launch.ledger)).snapshot(launch.state.pipeline_id)
    assert snap['cost_coverage']['unpriced_api_operations'] == int(gap)
    assert snap['cost_coverage']['priced_api_operations'] == (0 if gap else 1)
    assert snap['total']['total_tokens'] == (100 if gap else 60)
