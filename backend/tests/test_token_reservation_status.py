"""Saved/restarted status distinguishes recorded usage from planned holds."""

import json
from pathlib import Path

from flask import Flask
import pytest

from app.api import research_bp
from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter, set_run_context
from test_unresolved_usage_pipeline import isolated as isolated


def _observe(state, status, source, tokens=0, reservation=None):
    return LLMMeter.record_snapshot(
        'llm_api_attempt', 'reserved-api', 'openai', 'fixture', tokens, 0, 0,
        run_id=state.pipeline_id, stage='report', calls=0 if status == 'in_flight' else 1,
        status=status, usage_source=source, token_reservation=reservation)


def _reserve(ledger, state):
    LLMMeter.attach_durable_run(state.pipeline_id, str(ledger.path), 'new-owner', existing_only=True)
    set_run_context(state.pipeline_id, 'report')
    _observe(state, 'in_flight', 'unknown', reservation={
        'token_limit': 1000, 'prompt_tokens_estimate': 100, 'completion_tokens_limit': 200,
        'estimator': 'utf8-json-quarter/v1'})


def _status(state):
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix='/api/research')
    result = app.test_client().get(f'/api/research/status/{state.pipeline_id}')
    assert result.status_code == 200
    return result.get_json()['data']['live']


@pytest.mark.parametrize('status,source,tokens', [('unknown', 'unknown', 0), ('completed', 'estimated', 80)])
def test_saved_and_restarted_status_subtracts_uncertain_hold(isolated, status, source, tokens):
    ledger, state, _ = isolated
    _reserve(ledger, state)
    _observe(state, status, source, tokens)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    orch._flush_run_telemetry(state)
    saved = json.loads(Path(orch._tel_path).read_text())
    hold = saved['token_reservation_state']
    assert hold == saved['usage_accounting']['token_reservation_state']
    assert hold['reserved_tokens'] == 300 - tokens
    LLMMeter.reset(state.pipeline_id)
    before = ledger.path.read_bytes()
    live = _status(state)
    assert live['spend_so_far']['tokens'] == tokens
    assert live['spend_so_far']['token_reservation_state'] == hold
    assert live['budget']['remaining_tokens'] == 700
    assert live['budget']['recorded_remaining_tokens'] == 1000 - tokens
    assert live['budget']['reserved_tokens'] == 300 - tokens
    assert live['budget']['remaining_basis'] == 'recorded_plus_planned_holds'
    assert live['budget']['usage_complete'] is False
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize('configured', [0, 2000])
def test_status_uses_pinned_policy_and_surfaces_configuration_mismatch(isolated, monkeypatch, configured):
    ledger, state, _ = isolated
    _reserve(ledger, state)
    _observe(state, 'unknown', 'unknown')
    monkeypatch.setattr(Config, 'LLM_RUN_BUDGET_TOKENS', configured)
    budget = _status(state)['budget']
    assert budget['limit_tokens'] == 1000
    assert budget['configured_limit_tokens'] == configured
    assert budget['remaining_tokens'] is None
    assert budget['unavailable_reason'] == 'reservation_policy_mismatch'


def test_precise_late_settlement_releases_headroom_without_replacing_recorded_usage(isolated):
    ledger, state, _ = isolated
    _reserve(ledger, state)
    _observe(state, 'completed', 'estimated', 80)
    assert _status(state)['budget']['remaining_tokens'] == 700
    _observe(state, 'completed', 'known', 100)
    live = _status(state)
    assert live['spend_so_far']['tokens'] == 100
    assert live['budget']['reserved_tokens'] == 0
    assert live['budget']['remaining_tokens'] == 900
