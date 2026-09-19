"""An old judge failure cannot silently turn resume into paid fresh research."""
from contextvars import Context
import json
from pathlib import Path

import pytest

from test_agentic_parent_routing import parent, _state  # noqa: F401
from app.services import pipeline_orchestrator as po


@pytest.mark.parametrize('engine', [None, 'hybrid', 'agentic'])
@pytest.mark.parametrize('failure', ['judge', 'gate', 'integrity', 'actor'])
@pytest.mark.parametrize('report_state', ['full', 'short', 'missing'])
@pytest.mark.usefixtures('parent')
def test_rejected_report_without_evidence_manifest_never_replays_research(monkeypatch, engine, failure, report_state):
    state = _state(engine)
    folder = Path(state.handoff_dir)
    folder.mkdir(parents=True, exist_ok=True)
    report = '# Expensive retained research\n\n' + 'Documented findings and uncertainty. ' * 60
    if report_state != 'missing':
        (folder / 'research_report.md').write_text(report if report_state == 'full' else '# Partial')
    if failure == 'judge':
        (folder / 'research_report_judge.json').write_text(json.dumps({'verdict': 'FAIL', 'scores': {'overall': 4.57}}))
    elif failure == 'gate':
        (folder / 'meta.json').write_text(json.dumps({'research_report_quality_gate': {'passed': False}}))
    elif failure == 'actor':
        (folder / 'actor_dossier_judge.json').write_text(json.dumps({'verdict': 'FAIL'}))
    else:
        (folder / po._RESEARCH_CONTRACT_FILENAME).write_text('{}')
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    calls = []
    monkeypatch.setattr(po.DeerFlowResearchRunner, 'run', staticmethod(lambda *a, **k: calls.append('paid-child')))
    monkeypatch.setattr(po.PipelineOrchestrator, '_run_parallel_research_tracks', lambda *a, **k: calls.append('lanes'))
    monkeypatch.setattr(po.PipelineOrchestrator, '_run_research_synthesis_recovery', lambda *a, **k: calls.append('synthesis'))
    for _ in range(2):
        Context().run(po.PipelineOrchestrator._run, state)
        assert state.status == 'failed', state.error
        assert 'automatic full research replay is blocked' in state.error
        assert state.options['research_recovery_stop']['automatic_research_replay'] is False
        assert calls == []
        assert all((folder / name).read_bytes() == raw for name, raw in before.items())


@pytest.mark.usefixtures('parent')
def test_modern_advisory_fail_alone_does_not_block_incomplete_research(monkeypatch):
    state = _state('agentic')
    folder = Path(state.handoff_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'meta.json').write_text(json.dumps({'research_engine': 'agentic-phases/v1', 'quality_policy': 'mechanical-with-advisory/v1'}))
    (folder / 'actor_dossier_judge.json').write_text(json.dumps({'verdict': 'FAIL'}))
    (folder / 'research_report_judge.json').write_text(json.dumps({'verdict': 'FAIL'}))
    calls = []
    def reached(*args):
        calls.append('resume')
        raise po.PipelineCancelled('advisory did not veto')
    monkeypatch.setattr(po.PipelineOrchestrator, '_run_parallel_research_tracks', reached)
    Context().run(po.PipelineOrchestrator._run, state)
    assert state.status == 'cancelled' and calls == ['resume']
    assert 'research_recovery_stop' not in state.options
