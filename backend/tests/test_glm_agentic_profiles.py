"""Resolved-model defaults, pinned recovery, CLI defaults and physical budgets."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_FRAME = {'schema': 'research-scenario-frame/v1', 'horizon': '2030',
                  'scenarios': [{'id': f'SC{i}', 'name': f'Case {i}', 'probability': weight}
                                for i, weight in enumerate([40, 30, 20, 10], 1)]}


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'deerflow_bridge'))
    profiles = importlib.import_module('research_profiles')
    context = importlib.import_module('research_context')
    adapter = importlib.import_module('agentic_bridge')
    archive = importlib.import_module('research_archive')
    for name in context.ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    for key in profiles.execution_defaults():
        monkeypatch.delenv('RESEARCH_AGENTIC_' + key.upper(), raising=False)
    for name in ('RESEARCH_BUDGET_RUN_ID', 'RESEARCH_AGENTIC_CACHE_DIR', 'RESEARCH_ENGINE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('RESEARCH_ENGINE', 'agentic')
    spec = importlib.util.spec_from_file_location('glm_profile_bridge', ROOT / 'deerflow_bridge/deerflow_research.py')
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    dr._reset_compaction_stop()
    yield SimpleNamespace(p=profiles, c=context, a=adapter, archive=archive, dr=dr)
    archive.activate_workspace(None)
    dr._reset_compaction_stop()


@pytest.mark.parametrize('model,actual,large', [('glm', None, True), ('glm-5.3', None, True), ('custom', 'glm-5.3', True), ('glm', 'glm-4.6', False), ('glm-5.3-flash', None, False), ('claude', None, False), (None, None, False)])
def test_profile_tracks_actual_model_without_expanding_others(modules, model, actual, large):
    policy = modules.c.ContextPolicy.from_env(model_name=model, model_id=actual)
    assert policy.context_window_tokens == (1048576 if large else 128000)
    assert policy.working_tokens == (262144 if large else 64000)
    assert policy.retrieval_tokens == (32768 if large else 8000)
    execution = modules.p.execution_policy(model, actual)
    assert execution['prompt_budget_tokens'] == (12000000 if large else 4000000)
    assert execution['task_steps'] == (24 if large else 12)


def test_explicit_caps_override_but_cannot_exceed_model_window(modules, monkeypatch):
    monkeypatch.setenv('RESEARCH_AGENTIC_WORKING_TOKENS', '500000')
    assert modules.c.ContextPolicy.from_env('glm').working_tokens == 500000
    monkeypatch.setenv('RESEARCH_AGENTIC_CONTEXT_WINDOW_TOKENS', '2000000')
    with pytest.raises(ValueError, match='capacity'):
        modules.c.ContextPolicy.from_env('glm')


@pytest.mark.parametrize('value', ['0.05', '1.5', '600'])
def test_finite_fractional_deadlines_are_supported_and_pinned(modules, monkeypatch, tmp_path, value):
    monkeypatch.setenv('RESEARCH_AGENTIC_CALL_TIMEOUT_S', value)
    ws, _ = modules.a.prepare(tmp_path, 'Question', 'deep', 'glm', model_id='glm-5.3')
    assert modules.p.workspace_execution_policy(ws)['call_timeout_s'] == float(value)
    assert modules.a.prepare(tmp_path, 'Question', 'deep', 'glm', model_id='glm-5.3')[0].identity == ws.identity


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '0'])
def test_invalid_deadlines_fail_before_workspace_creation(modules, monkeypatch, tmp_path, value):
    monkeypatch.setenv('RESEARCH_AGENTIC_CALL_TIMEOUT_S', value)
    with pytest.raises(ValueError):
        modules.a.prepare(tmp_path / 'output', 'Q', 'deep', 'glm')
    assert not (tmp_path / 'output' / 'agentic').exists()


def test_new_glm_workspace_policy_reaches_recall_and_bare_output(modules, tmp_path):
    ws, policy = modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    assert ws.identity['model_profile'] == 'glm-5.3-1m/v1'
    assert modules.c.ContextPolicy.from_workspace(ws) == policy
    assert modules.a.execution_setting('TASK_STEPS', 12) == 24
    assert modules.a.execution_setting('COMPACTION_KEEP_TOKENS', 16000) == 65536
    assert modules.dr._effective_model_output_tokens('glm', 1800) == 5896
    assert modules.dr._effective_model_output_tokens('claude', 1800) == 1800
    assert modules.dr._synthesis_section_context_cap(5, 500000) == 196608


def test_existing_v1_workspace_keeps_old_context_and_identity(modules, tmp_path):
    from research_workspace import ResearchWorkspace
    import hashlib
    identity = {'schema': modules.a.ENGINE, 'question_sha256': hashlib.sha256(b'Q').hexdigest(), 'depth': 'deep', 'model': 'glm', 'language': 'auto', 'run_id': 'standalone', 'lane_id': 'evidence', 'context_policy': modules.c.ContextPolicy().to_dict()}
    ResearchWorkspace(tmp_path / 'agentic', identity)
    ws, policy = modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    assert ws.identity == identity
    assert policy.working_tokens == 64000
    assert modules.a.execution_setting('TASK_STEPS', 12) == 12


def test_resume_rejects_policy_and_actual_model_drift(modules, monkeypatch, tmp_path):
    modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    with pytest.raises(ValueError, match='model changed'):
        modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-4.6')
    monkeypatch.setenv('RESEARCH_AGENTIC_WORKING_TOKENS', '300000')
    with pytest.raises(ValueError, match='conflicts'):
        modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')


@pytest.mark.parametrize('args,saved,expected', [([], None, 'agentic'), (['--resume'], None, 'hybrid'), (['--resume'], 'agentic-phases/v1', 'agentic'), (['--engine', 'linear'], None, 'linear')])
def test_cli_selects_fresh_default_and_preserves_legacy_recovery(modules, monkeypatch, tmp_path, args, saved, expected):
    monkeypatch.delenv('RESEARCH_ENGINE')
    if saved:
        (tmp_path / 'meta.json').write_text(json.dumps({'research_engine': saved}))
    monkeypatch.setattr(sys, 'argv', ['bridge', '--out-dir', str(tmp_path), *args])
    import os
    monkeypatch.setattr(modules.dr, '_main_impl', lambda: os.environ['RESEARCH_ENGINE'])
    assert modules.dr.main() == expected


def test_cli_invalid_resume_metadata_is_a_clean_preflight_error(modules, monkeypatch, tmp_path):
    monkeypatch.delenv('RESEARCH_ENGINE')
    (tmp_path / 'meta.json').write_text('[]')
    monkeypatch.setattr(sys, 'argv', ['bridge', '--out-dir', str(tmp_path), '--resume'])
    monkeypatch.setattr(modules.dr, '_main_impl', lambda: pytest.fail('invalid metadata admitted'))
    assert modules.dr.main() == 3


def test_cached_sections_do_not_spend_physical_call_allowance(modules, monkeypatch, tmp_path):
    dr = modules.dr
    modules.a.prepare(tmp_path, 'Q', 'standard', 'glm', model_id='glm-5.3')
    monkeypatch.setenv('RESEARCH_SYNTHESIS_MIN_WORDS', '0')
    monkeypatch.setattr(dr, '_inline_citations_enabled', lambda: False)
    monkeypatch.setattr(dr, '_dedup_shingles_enabled', lambda: False)
    calls = []
    def invoke(_model, _prompt, _plog, label, *_args):
        calls.append(label)
        if label.startswith('synthesis-scenario-frame'):
            return json.dumps(SCENARIO_FRAME)
        if label == 'synthesis-outline':
            return json.dumps({'sections': [{'title': f'Topic {i}', 'scope': 'capacity', 'covers': [str(i)], 'target_words': 1000} for i in range(5)]})
        return 'Evidence remains uncertain and should be verified. ' * 60
    monkeypatch.setattr(dr, '_bare_synth_invoke', invoke)
    args = ('Q', None, 'standard', 'glm', ['Capacity evidence. ' * 300], [], 'Capacity evidence. ' * 300, SimpleNamespace(write=lambda *_: None))
    first = dr.synthesize_multipart(*args)
    assert first and calls
    monkeypatch.setattr(dr.SynthesisExecutionBudget, 'reserve', lambda *_: pytest.fail('cache hit charged physical budget'))
    assert dr.synthesize_multipart(*args) == first


def test_legacy_routing_override_is_pinned_and_drift_is_rejected(modules, monkeypatch, tmp_path):
    monkeypatch.setenv('SYNTHESIS_SECTION_CONTEXT_CHARS', '200000')
    modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    assert modules.dr._synthesis_section_context_cap(5, 500000) == 200000
    monkeypatch.setenv('SYNTHESIS_SECTION_CONTEXT_CHARS', '60000')
    assert modules.dr._synthesis_section_context_cap(5, 500000) == 200000
    with pytest.raises(ValueError, match='conflicts'):
        modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')


def test_synthesis_input_cap_uses_pinned_model_window(modules, monkeypatch, tmp_path):
    ws, policy = modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    cap = modules.dr._synthesis_context_cap('glm', '中文😀')
    assert cap * 4 + policy.reserved_output_tokens + policy.prompt_overhead_tokens + policy.safety_margin_tokens <= policy.context_window_tokens
    monkeypatch.setenv('SYNTHESIS_CONTEXT_WINDOW_TOKENS', '4000000')
    assert modules.dr._synthesis_context_cap('glm', '中文😀') == cap


def test_alias_to_same_physical_glm_gets_same_reasoning_allowance(modules, monkeypatch, tmp_path):
    from types import ModuleType
    modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    package = ModuleType('deerflow')
    config = ModuleType('deerflow.config')
    config.get_app_config = lambda: SimpleNamespace(get_model_config=lambda name: SimpleNamespace(model='glm-5.3' if name == 'alias' else 'glm-4.6'))
    monkeypatch.setitem(sys.modules, 'deerflow', package)
    monkeypatch.setitem(sys.modules, 'deerflow.config', config)
    assert modules.dr._effective_model_output_tokens('alias', 1800) == 5896
    assert modules.dr._effective_model_output_tokens('not-glm', 1800) == 1800


def test_modern_extract_only_stops_before_contract_mutation(modules, monkeypatch, tmp_path):
    meta = tmp_path / 'meta.json'
    meta.write_text(json.dumps({'research_engine': 'agentic-phases/v1', 'quality_policy': 'mechanical-with-advisory/v1'}))
    before = meta.read_bytes()
    monkeypatch.delenv('RESEARCH_ENGINE')
    monkeypatch.setattr(sys, 'argv', ['bridge', '--out-dir', str(tmp_path), '--extract-only'])
    monkeypatch.setattr(modules.dr, '_main_impl', lambda: pytest.fail('unmetered extraction admitted'))
    assert modules.dr.main() == 3
    assert meta.read_bytes() == before


def test_alternate_smaller_model_gets_own_pinned_cap_and_preflight(modules, monkeypatch, tmp_path):
    profiles = {'glm': {'model_id': 'glm-5.3', 'context_window_tokens': 1048576, 'max_output_tokens': 65536},
                'small': {'model_id': 'small-model', 'context_window_tokens': 128000, 'max_output_tokens': 16000}}
    modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3', model_profiles=profiles)
    cap = modules.dr._synthesis_context_cap('small', 'ASCII evidence')
    assert cap + 16000 < 128000
    assert modules.dr._synthesis_section_context_cap(5, 1000) <= 1000
    monkeypatch.setattr(modules.dr, '_build_tool_free_model', lambda *_: pytest.fail('oversized request constructed model'))
    with pytest.raises(ValueError, match='capacity'):
        modules.dr._invoke_tool_free_model('small', ['x' * 128000], max_output_tokens=1000, plog=None, label='small')
    assert modules.dr._effective_model_output_tokens('small', 1800) == 1800
    changed = {**profiles, 'small': {**profiles['small'], 'context_window_tokens': 64000}}
    with pytest.raises(ValueError, match='envelopes changed'):
        modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3', model_profiles=changed)


def test_reasoning_headroom_is_not_false_truncation(modules, monkeypatch, tmp_path):
    modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    response = SimpleNamespace(content='A complete section.', response_metadata={}, usage_metadata={'input_tokens': 100, 'output_tokens': 5000})
    monkeypatch.setattr(modules.dr, '_invoke_tool_free_model', lambda *_, **__: (response, 'glm'))
    assert modules.dr._bare_synth_invoke('glm', 'Prompt', None, 'section', 1800, True) == 'A complete section.'


def test_backend_profile_loader_does_not_require_repository_on_syspath():
    import os
    import subprocess
    code = '''import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path = [str(root / 'backend')] + [p for p in sys.path if Path(p or '.').resolve() != root]
from app.services.pipeline_orchestrator import _research_profile_module
assert _research_profile_module().select_engine() == 'agentic'
print('ok')
'''
    result = subprocess.run([sys.executable, '-c', code, str(ROOT)], cwd=ROOT / 'backend', env={**os.environ, 'DRF_TEST_PROCESS': '1'}, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith('ok')


def test_critic_model_identity_is_persisted_and_reused_without_calls(modules, monkeypatch, tmp_path):
    ws, _ = modules.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    ref = ws.put_artifact(json.dumps(SCENARIO_FRAME), 'scenario_frame')
    ws.append_event('synthesis-scenario-frame', 'canonical_frame', {'ref': ref})
    monkeypatch.setenv('DEERFLOW_JUDGE_MODEL', 'critic-alias')
    seen = []
    def invoke(model, *args, **kwargs):
        seen.append(model)
        return SimpleNamespace(content=json.dumps({'status': 'FAIL', 'weaknesses': []}), response_metadata={'model_name': 'observed-critic-model'}), 'actual-served-alias'
    monkeypatch.setattr(modules.dr, '_invoke_tool_free_model', invoke)
    report = '# Report\n\n## Findings\n\n' + 'Supported but uncertain evidence [S1]. ' * 20
    sources = [{'url': 'https://example.test/source'}]
    args = (modules.dr, ws, report, sources, {}, 'glm', SimpleNamespace(write=lambda *_: None))
    _, result = modules.a.review_and_repair(*args)
    assert seen == ['critic-alias'] * 5
    records = result['model_provenance']
    assert len(records) == 5
    assert all(r['requested_model_alias'] == 'critic-alias' and r['served_model_alias'] == 'actual-served-alias' and r['provider_response_model'] == 'observed-critic-model' for r in records)
    _, reused = modules.a.review_and_repair(*args)
    assert reused['model_provenance'] == records and len(seen) == 5


def test_small_primary_model_never_inherits_128k_or_glm_defaults(modules, monkeypatch, tmp_path):
    profiles = {'small': {'model_id': 'small-32k', 'context_window_tokens': 32768, 'max_output_tokens': 4096}}
    _, policy = modules.a.prepare(tmp_path, 'Q', 'deep', 'small', model_id='small-32k', model_profiles=profiles)
    assert policy.context_window_tokens == 32768 and policy.working_tokens == 16384
    assert policy.working_tokens + policy.reserved_output_tokens + policy.prompt_overhead_tokens + policy.safety_margin_tokens <= 32768
    monkeypatch.setenv('RESEARCH_AGENTIC_CONTEXT_WINDOW_TOKENS', '1048576')
    with pytest.raises(ValueError, match='configured model capacity'):
        modules.c.ContextPolicy.from_env('small', context_limit=32768)
