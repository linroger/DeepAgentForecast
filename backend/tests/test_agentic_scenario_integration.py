"""Canonical scenario planning, shared writers, cached recovery and publication."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_glm_agentic_profiles import modules, SCENARIO_FRAME  # noqa: F401
from test_agentic_quality_handoff import seal


def scenario_table(weights=(40, 30, 20, 10)):
    rows = [f'| SC{i} — Case {i} | {weight}% |' for i, weight in enumerate(weights, 1)]
    return '| Scenario | Probability |\n|---|---|\n' + '\n'.join(rows) + '\n'


@pytest.mark.usefixtures('modules')
def test_shared_frame_reaches_every_writer_and_survives_exact_resume(request, monkeypatch, tmp_path):
    env = request.getfixturevalue('modules')
    ws, _ = env.a.prepare(tmp_path, 'Forecast to 2030', 'deep', 'glm', model_id='glm-5.3')
    monkeypatch.setenv('RESEARCH_SYNTHESIS_MIN_WORDS', '0')
    monkeypatch.setattr(env.dr, '_inline_citations_enabled', lambda: False)
    monkeypatch.setattr(env.dr, '_dedup_shingles_enabled', lambda: False)
    seen = []
    def invoke(_model, prompt, _log, label, *_args):
        seen.append(label)
        if label == 'synthesis-outline':
            return json.dumps({'sections': [{'title': f'Topic {i}', 'scope': 'capacity', 'covers': [str(i)], 'target_words': 1000} for i in range(5)]})
        if label == 'synthesis-scenario-frame':
            return json.dumps(SCENARIO_FRAME)
        assert 'SC1' in str(prompt) and 'SC4' in str(prompt) and '2030' in str(prompt)
        return 'Evidence and assumptions remain uncertain [S1]. ' * 30 + '\n\n' + scenario_table()
    monkeypatch.setattr(env.dr, '_bare_synth_invoke', invoke)
    args = ('Forecast to 2030', None, 'deep', 'glm', ['Observed source evidence. ' * 300], [], 'Evidence', SimpleNamespace(write=lambda *_: None))
    report = env.dr.synthesize_multipart(*args)
    frame, probabilities = env.a.scenario_frame_for_workspace(ws)
    assert frame == SCENARIO_FRAME and len(probabilities) == 4
    before = list(seen)
    assert env.dr.synthesize_multipart(*args) == report and seen == before
    assert seen.count('synthesis-scenario-frame') == 1
    assert len(ws.events('synthesis-scenario-frame')) == 1
    sources = [{'url': 'https://example.test/source'}]
    meta = {'research_engine': env.a.ENGINE, 'quality_policy': env.a.QUALITY_POLICY}
    out = tmp_path / 'handoff'
    out.mkdir()
    receipt = env.a.persist_quality(env.dr, out, report, sources, meta)
    assert receipt['passed'] and receipt['inputs']['scenario_frame'] == probabilities
    assert meta['research_scenario_frame'] == frame
    (out / 'meta.json').write_text(json.dumps(meta))
    (out / 'research_report.md').write_text(report)
    (out / 'sources.json').write_text(json.dumps(sources))
    seal(out)
    from app.services.research_quality_gate import quality_errors
    assert quality_errors(str(out)) == []
    # Each full-frame dimension and requiredness remains independently bound.
    original_meta = copy.deepcopy(meta)
    for mutation in ("horizon", "name", "delete"):
        changed = copy.deepcopy(original_meta)
        if mutation == "horizon":
            changed["research_scenario_frame"]["horizon"] = "2099"
        elif mutation == "name":
            changed["research_scenario_frame"]["scenarios"][0]["name"] = "Changed"
        else:
            del changed["research_scenario_frame"]
        (out / 'meta.json').write_text(json.dumps(changed))
        seal(out)
        assert any('scenario_frame' in error for error in quality_errors(str(out)))
    # Rehashing a changed meta cannot change the frame bound to the receipt.
    meta['research_scenario_frame']['scenarios'][0]['probability'] = 55
    meta['research_scenario_frame']['scenarios'][1]['probability'] = 15
    (out / 'meta.json').write_text(json.dumps(meta))
    seal(out)
    assert 'research_quality_scenario_frame_mismatch' in quality_errors(str(out))


@pytest.mark.parametrize('second,expected', [
    (scenario_table((55, 15, 20, 10)), 'scenario_restatement_mismatch:sc1'),
    (scenario_table().replace('SC1 — Case 1', 'Unbound baseline'), 'scenario_probability_table_has_unbound_identity'),
])
def test_conflicting_or_unbound_table_cannot_pass_owned_frame(second, expected):
    from deerflow_bridge.research_quality import evaluate_report
    from deerflow_bridge.research_scenarios import probability_frame
    report = '# Report\n\n## Summary\n\n' + 'Evidence remains uncertain. ' * 20 + '\n' + scenario_table() + '\n## Details\n\n' + second
    result = evaluate_report(report, [], scenario_frame=probability_frame(SCENARIO_FRAME))
    assert expected in result['errors'] and result['passed'] is False


def test_noncanonical_legacy_frames_keep_existing_semantics():
    from deerflow_bridge.research_quality import evaluate_report
    frame = [{'name': 'Base', 'weight': 60}, {'name': 'Downside', 'weight': 40}]
    report = '# Report\n\n## Findings\n\n' + 'Observed but uncertain evidence. ' * 20
    result = evaluate_report(report, [], scenario_frame=frame)
    assert result['passed'] and 'canonical_scenario_frame_not_fully_represented' not in result['errors']


@pytest.mark.usefixtures('modules')
@pytest.mark.parametrize('repair_valid', [True, False])
def test_scenario_format_repair_is_bounded_and_cached(request, monkeypatch, tmp_path, repair_valid):
    env = request.getfixturevalue('modules')
    env.a.prepare(tmp_path, 'Q', 'standard', 'glm', model_id='glm-5.3')
    monkeypatch.setenv('RESEARCH_SYNTHESIS_MIN_WORDS', '0')
    monkeypatch.setattr(env.dr, '_inline_citations_enabled', lambda: False)
    seen = []
    def invoke(_model, _prompt, _log, label, *_args):
        seen.append(label)
        if label == 'synthesis-outline':
            return json.dumps({'sections': [{'title': f'Case {i}', 'scope': 'risk', 'covers': [str(i)], 'target_words': 1000} for i in range(5)]})
        if label == 'synthesis-scenario-frame':
            return '{"invalid":true}'
        if label == 'synthesis-scenario-frame-repair':
            return json.dumps(SCENARIO_FRAME) if repair_valid else '{"still_invalid":true}'
        return 'Evidence needs careful interpretation. ' * 50
    monkeypatch.setattr(env.dr, '_bare_synth_invoke', invoke)
    args = ('Q', None, 'standard', 'glm', ['Observed evidence. ' * 300], [], 'Evidence', SimpleNamespace(write=lambda *_: None))
    if repair_valid:
        assert env.dr.synthesize_multipart(*args)
    else:
        with pytest.raises(ValueError):
            env.dr.synthesize_multipart(*args)
        assert not any(label.startswith('synthesis-section-') for label in seen)
    assert seen.count('synthesis-scenario-frame') == 1
    assert seen.count('synthesis-scenario-frame-repair') == 1
    before = list(seen)
    if repair_valid:
        assert env.dr.synthesize_multipart(*args)
    else:
        with pytest.raises(ValueError):
            env.dr.synthesize_multipart(*args)
    assert seen == before


@pytest.mark.usefixtures('modules')
def test_new_workspace_cannot_publish_without_accepted_scenario_frame(request, tmp_path):
    env = request.getfixturevalue('modules')
    env.a.prepare(tmp_path, 'Q', 'deep', 'glm', model_id='glm-5.3')
    report = '# Report\n\n## Findings\n\n' + 'Uncertain sourced evidence. ' * 30
    out = tmp_path / 'out'
    out.mkdir()
    with pytest.raises(ValueError, match='canonical scenario frame is missing'):
        env.a.persist_quality(env.dr, out, report, [], {})
    assert not (out / 'research_quality.json').exists()


def test_each_canonical_table_requires_four_unique_ids():
    from deerflow_bridge.research_quality import evaluate_report
    from deerflow_bridge.research_scenarios import probability_frame
    table = scenario_table().replace('| SC2', '| SC1 — duplicate | 40% |\n| SC2')
    report = '# Report\n\n## Findings\n\n' + 'Uncertain source evidence. ' * 30 + '\n' + table
    result = evaluate_report(report, [], scenario_frame=probability_frame(SCENARIO_FRAME))
    assert 'canonical_scenario_table_ids_invalid' in result['errors']
