"""HTTP acceptance checks use temporary state and a harmless worker, never providers."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import research as api
from app.config import Config
from app.services import launch_intents as li
from app.services.pipeline_orchestrator import PipelineManager, PipelineOrchestrator


KEY = 'http-launch-intent-20260908'
PAYLOAD = {'prompt': 'A deterministic sample question', 'mode': 'research_only'}


@pytest.fixture
def http_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'PIPELINE_DATA_DIR', str(tmp_path / 'pipelines'))
    monkeypatch.setattr(Config, 'DEERFLOW_RESEARCH_DEPTH', 'standard')
    monkeypatch.setattr(Config, 'SUPPORTED_DEERFLOW_MODELS', ['sample-model'])
    monkeypatch.setattr(PipelineOrchestrator, '_threads', {})
    monkeypatch.setattr(PipelineOrchestrator, '_cancel_events', {})
    calls = []
    monkeypatch.setattr(PipelineOrchestrator, '_run', classmethod(lambda cls, state: calls.append(state.pipeline_id)))
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: [])
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api.research_bp, url_prefix='/api/research')
    yield app, calls
    for thread in PipelineOrchestrator._threads.values():
        thread.join(timeout=5)


def post(app, payload=None, key=KEY):
    with app.test_client() as client:
        response = client.post('/api/research/run', json=PAYLOAD if payload is None else payload,
                               headers={'Idempotency-Key': key})
    # Admission returns before the harmless worker necessarily gets CPU time.
    # Count completed stub dispatches without changing production start timing.
    for thread in tuple(PipelineOrchestrator._threads.values()):
        if thread.ident is not None:
            thread.join(timeout=5)
    return response


def test_lost_response_recovered_by_read_only_lookup(http_launch, monkeypatch):
    app, calls = http_launch
    admitted = post(app).get_json()['data']

    def forbidden(*args, **kwargs):
        raise AssertionError('lookup must not dispatch or preflight')

    monkeypatch.setattr(PipelineOrchestrator, 'start_idempotent', forbidden)
    monkeypatch.setattr(api, 'preflight_pipeline', forbidden)
    with app.test_client() as client:
        response = client.get(f'/api/research/launch-intents/{KEY}')
    recovered = response.get_json()['data']
    assert response.status_code == 200
    assert recovered['pipeline_id'] == admitted['pipeline_id']
    assert recovered['task_id'] == admitted['task_id']
    assert recovered['replayed'] is True
    assert len(calls) == 1


def test_duplicate_after_provider_and_defaults_change_reuses_admission(http_launch, monkeypatch):
    app, calls = http_launch
    payload = {**PAYLOAD, 'model': 'sample-model'}
    first = post(app, payload).get_json()['data']
    monkeypatch.setattr(Config, 'DEERFLOW_RESEARCH_DEPTH', 'deep')
    monkeypatch.setattr(Config, 'SUPPORTED_DEERFLOW_MODELS', [])
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: ['Provider offline'])
    replay = post(app, payload)
    assert replay.status_code == 200
    assert replay.get_json()['data']['pipeline_id'] == first['pipeline_id']
    assert PipelineManager.load(first['pipeline_id'])['options']['depth'] == 'standard'
    assert len(calls) == 1


def test_aliases_and_normalized_request_have_same_identity(http_launch):
    app, _ = http_launch
    first = post(app, {**PAYLOAD, 'language': 'auto', 'max_rounds': '12', 'model': 'SAMPLE-MODEL'})
    replay = post(app, {**PAYLOAD, 'research_language': 'auto', 'max_rounds': 12, 'model': 'sample-model'})
    assert replay.status_code == 200
    assert replay.get_json()['data']['pipeline_id'] == first.get_json()['data']['pipeline_id']


def test_conflict_wins_over_new_preflight_and_model_validation(http_launch, monkeypatch):
    app, calls = http_launch
    first = post(app).get_json()['data']
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: ['Provider offline'])
    response = post(app, {**PAYLOAD, 'model': 'now-unsupported'})
    assert response.status_code == 409
    assert response.get_json()['code'] == 'launch_intent_conflict'
    assert response.get_json()['pipeline_id'] == first['pipeline_id']
    assert len(calls) == 1


def test_concurrent_http_requests_share_one_identity(http_launch):
    app, calls = http_launch
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda _: post(app), range(6)))
    assert all(response.status_code == 200 for response in responses), [
        (response.status_code, response.get_json()) for response in responses
    ]
    assert len({r.get_json()['data']['pipeline_id'] for r in responses}) == 1
    for thread in PipelineOrchestrator._threads.values():
        thread.join(timeout=5)
    assert len(calls) == 1


def test_unknown_lookup_does_not_admit(http_launch):
    app, calls = http_launch
    with app.test_client() as client:
        response = client.get(f'/api/research/launch-intents/{KEY}')
    assert response.status_code == 404
    assert response.get_json()['code'] == 'launch_intent_not_found'
    assert calls == []


@pytest.mark.parametrize('key', ['', 'short', 'x' * 129, 'invalid key' * 3])
def test_malformed_key_never_falls_back_to_legacy_launch(http_launch, key):
    app, calls = http_launch
    assert post(app, key=key).status_code == 400
    assert calls == []


@pytest.mark.parametrize('payload', [[], 'bad', {}, {'prompt': 12}, {'prompt': 'x', 'depth': []},
                                     {'prompt': 'x', 'max_rounds': True},
                                     {'prompt': 'x', 'max_rounds': 1.5},
                                     {'prompt': 'x', 'language': []}])
def test_invalid_inputs_rejected_before_admission(http_launch, payload):
    app, calls = http_launch
    assert post(app, payload).status_code == 400
    assert calls == []


def test_preflight_failure_does_not_consume_intent(http_launch, monkeypatch):
    app, calls = http_launch
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: ['Provider offline'])
    assert post(app).status_code == 400
    assert PipelineOrchestrator.lookup_launch_intent(KEY) is None
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: [])
    assert post(app).status_code == 200
    assert len(calls) == 1


def test_storage_failure_surfaces_retryable_service_error_without_start(http_launch, monkeypatch):
    app, calls = http_launch

    def unavailable(*args, **kwargs):
        raise li.LaunchIntentStorageError('Intent ledger unavailable')

    monkeypatch.setattr(PipelineOrchestrator, 'lookup_launch_intent', unavailable)
    assert post(app).status_code == 503
    with app.test_client() as client:
        response = client.get(f'/api/research/launch-intents/{KEY}')
    assert response.status_code == 503
    assert response.get_json()['code'] == 'launch_intent_unavailable'
    assert calls == []


def test_missing_pipeline_returns_retained_identity_without_resurrection(http_launch):
    app, calls = http_launch
    first = post(app).get_json()['data']
    from pathlib import Path
    state_path = Path(Config.PIPELINE_DATA_DIR) / first['pipeline_id'] / 'pipeline_state.json'
    state_path.unlink()
    replay = post(app)
    assert replay.status_code == 200
    data = replay.get_json()['data']
    assert data['pipeline_id'] == first['pipeline_id']
    assert data['recovery_required'] is True
    assert not state_path.exists()
    assert len(calls) == 1


def test_unkeyed_client_preserves_legacy_start_contract(http_launch, monkeypatch):
    app, _ = http_launch
    starts = []

    def legacy(**kwargs):
        starts.append(kwargs)
        return SimpleNamespace(pipeline_id='pipe_legacy', task_id='legacy', mode='research_only', status='running')

    monkeypatch.setattr(PipelineOrchestrator, 'start', legacy)
    with app.test_client() as client:
        response = client.post('/api/research/run', json=PAYLOAD)
    assert response.status_code == 200
    assert response.get_json()['data']['pipeline_id'] == 'pipe_legacy'
    assert len(starts) == 1


def test_rejected_request_can_be_retired_without_racing_late_submission(http_launch, monkeypatch):
    app, calls = http_launch
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: ['Provider offline'])
    assert post(app).status_code == 400
    with app.test_client() as client:
        response = client.post(f'/api/research/launch-intents/{KEY}/abandon')
    assert response.status_code == 200
    retired = response.get_json()['data']
    assert retired['launch_status'] == 'abandoned'
    assert retired['pipeline_id'] is None
    monkeypatch.setattr(api, 'preflight_pipeline', lambda **kwargs: [])
    late = post(app).get_json()['data']
    assert late['launch_status'] == 'abandoned'
    assert calls == []
    assert post(app, key=KEY + '-deliberate-new').get_json()['data']['pipeline_id']
    assert len(calls) == 1


def test_abandonment_preserves_already_admitted_identity(http_launch):
    app, calls = http_launch
    first = post(app).get_json()['data']
    with app.test_client() as client:
        response = client.post(f'/api/research/launch-intents/{KEY}/abandon')
    assert response.status_code == 200
    assert response.get_json()['data']['pipeline_id'] == first['pipeline_id']
    assert response.get_json()['data']['launch_status'] == 'dispatched'
    assert PipelineManager.load(first['pipeline_id'])['status'] == 'running'
    assert len(calls) == 1
