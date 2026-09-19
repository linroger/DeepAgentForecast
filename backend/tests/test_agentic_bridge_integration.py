"""Real CLI/adaptor/coordinator/archive acceptance with an offline native stream."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
BODY = '# Source release\n\n' + 'Full retained evidence paragraph.\n\n' * 800 + 'TAIL_SENTINEL 98765 revised capacity.\n'


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'deerflow_bridge'))
    spec = importlib.util.spec_from_file_location('agentic_cli_acceptance', ROOT / 'deerflow_bridge/deerflow_research.py')
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    for key, value in {
        'RESEARCH_ENGINE': 'agentic', 'MINIMAX_API_KEY': 'offline-unused',
        'DEERFLOW_DUAL_TRACK': 'false', 'PREDICTION_MARKETS_ENABLED': 'false',
        'RESEARCH_SOURCE_CACHE_DIR': str(tmp_path / 'source-cache'),
        'RESEARCH_BUDGET_DB': str(tmp_path / 'budget.sqlite3'),
        'RESEARCH_BUDGET_TELEMETRY_PATH': str(tmp_path / 'budget.json'),
        'RESEARCH_COMPACTION_DB': str(tmp_path / 'compaction.sqlite3'),
        'RESEARCH_AGENTIC_CACHE_DIR': str(tmp_path / 'workspace'),
        'RESEARCH_BUDGET_RUN_ID': 'acceptance', 'RESEARCH_BUDGET_LANE_ID': 'one',
        'RESEARCH_AGENTIC_MAX_FOLLOWUPS': '1', 'RESEARCH_CHECKPOINT_ENABLED': 'true',
    }.items():
        monkeypatch.setenv(key, value)
    dr._reset_compaction_stop()
    monkeypatch.setattr(dr, 'runtime_skill_sync_telemetry', lambda: {'runtime_verified': False, 'outcome': 'offline-fixture', 'skills': {}})
    adapter = importlib.import_module('agentic_bridge')
    archive = importlib.import_module('research_archive')
    # Only native framework/config construction is substituted; all scheduling,
    # stream interpretation, fetch caching, archive, receipts and CLI are real.
    monkeypatch.setattr(adapter, 'configure_client', lambda client, policy: None)
    yield dr, adapter, archive
    archive.activate_workspace(None)
    dr._reset_compaction_stop()


class OfflineClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(5)
        self.active = self.peak = self.calls = self.fetch_calls = 0
        self.threads = {}

    def stream(self, message, *, thread_id, **_):
        from cached_fetch import cached_fetch
        with self.lock:
            self.calls += 1
            ordinal = self.calls
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self.barrier.wait(timeout=15)
            url = 'https://example.test/release/' + thread_id
            call_id = 'fetch-' + thread_id
            yield SimpleNamespace(type='messages-tuple', data={'type': 'ai', 'id': 'request', 'content': '', 'tool_calls': [{'name': 'web_fetch', 'id': call_id, 'args': {'url': url}}]})

            async def produce(_url):
                with self.lock:
                    self.fetch_calls += 1
                return BODY

            body = asyncio.run(cached_fetch(url, produce))
            yield SimpleNamespace(type='messages-tuple', data={'type': 'tool', 'name': 'web_fetch', 'tool_call_id': call_id, 'content': body})
            discovery = 'DISCOVERED: How does revised capacity affect downside risk?\n' if ordinal == 1 else ''
            note = discovery + f'Evidence at {url}: TAIL_SENTINEL 98765. ' + 'Reported capacity remains uncertain. ' * 15
            yield SimpleNamespace(type='messages-tuple', data={'type': 'ai', 'id': 'answer', 'content': note})
            self.threads[thread_id] = {'checkpoints': [{'values': {'messages': [{'type': 'tool', 'name': 'web_fetch', 'content': body}, {'type': 'ai', 'content': note}]}}]}
            yield SimpleNamespace(type='end', data={})
        finally:
            with self.lock:
                self.active -= 1

    def get_thread(self, thread_id):
        return self.threads.get(thread_id, {})


def test_cli_five_agents_discovery_full_sources_and_exact_resume(bridge, monkeypatch, tmp_path):
    dr, _, archive = bridge
    client = OfflineClient()
    module = ModuleType('deerflow.client')
    module.DeerFlowClient = lambda **kwargs: client
    monkeypatch.setitem(sys.modules, 'deerflow', ModuleType('deerflow'))
    monkeypatch.setitem(sys.modules, 'deerflow.client', module)
    argv = ['deerflow_research.py', '--prompt', 'Forecast capacity', '--out-dir', str(tmp_path / 'out'), '--model', 'minimax', '--depth', 'standard', '--thread-id', 'fixed', '--evidence-only']
    monkeypatch.setattr(sys, 'argv', argv)
    assert dr.main() == 0
    assert client.calls == 30 and client.peak == 5 and client.fetch_calls == 30
    sources_path = tmp_path / 'out' / 'evidence_sources.json'
    if not sources_path.exists():
        sources_path = tmp_path / 'out' / dr.SOURCES_FILENAME
    sources = json.loads(sources_path.read_text())
    assert len(sources) == 30
    assert all(s['content'] == BODY and s['source_origin'] == 'fetched' for s in sources)
    assert archive.current_workspace().discoveries()
    before = (tmp_path / 'out' / dr.EVIDENCE_PACK_FILENAME).read_text()
    monkeypatch.setattr(sys, 'argv', argv + ['--resume'])
    assert dr.main() == 0
    assert client.calls == 30 and client.fetch_calls == 30
    assert 'TAIL_SENTINEL' in (tmp_path / 'out' / dr.EVIDENCE_PACK_FILENAME).read_text()
    assert (tmp_path / 'out' / dr.EVIDENCE_PACK_FILENAME).read_text() == before
    stats = json.loads((tmp_path / 'out' / 'agentic_research_stats.json').read_text())
    assert stats['reused_tasks'] == 30


def test_bare_model_real_admission_rejects_before_send(bridge, monkeypatch, tmp_path):
    dr, adapter, _ = bridge
    from research_admission import snapshot
    monkeypatch.setenv('RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS', '1')
    workspace, _ = adapter.prepare(tmp_path / 'out', 'Question', 'standard', 'fixture')
    calls = []
    with pytest.raises(dr.ResearchCompactionError):
        dr._invoke_model(SimpleNamespace(invoke=lambda messages: calls.append(messages)), ['too much input'])
    assert calls == []
    assert snapshot(workspace)['denied_calls'] == 1


def test_imported_full_source_survives_global_synthesis_namespace(bridge):
    import hashlib
    dr, _, _ = bridge
    source = {'url': 'https://example.test/full', 'source_origin': 'fetched', 'reachable': True,
              'content': BODY, 'content_sha256': hashlib.sha256(BODY.encode()).hexdigest(),
              'receipt_id': 'producer-receipt'}
    dr.seed_manifest_sources([source])
    assert dr.export_fetched_sources_for_manifest()[0]['content'] == BODY
    assert 'content' not in dr.export_fetched_sources_for_manifest(include_content=False)[0]
    merged, _ = dr.merge_fetched_into_sources([])
    assert merged[0]['content'] == BODY


@pytest.mark.parametrize('marker,expected', [('[S1]', 0), ('[S999]', 2)])
def test_cli_advisory_fail_cannot_veto_and_bad_citation_cannot_disappear(bridge, monkeypatch, tmp_path, marker, expected):
    import hashlib
    dr, _, _ = bridge
    client_module = ModuleType('deerflow.client')
    client_module.DeerFlowClient = lambda **kwargs: object()
    monkeypatch.setitem(sys.modules, 'deerflow', ModuleType('deerflow'))
    monkeypatch.setitem(sys.modules, 'deerflow.client', client_module)
    report = '# Research report\n\n## Findings\n\n' + ('Observed evidence remains uncertain ' + marker + '. ') * 20 + '\n\n## Limitations\n\nMore primary evidence may change this assessment.\n'
    source = {'url': 'https://example.test/full', 'source_origin': 'fetched', 'reachable': True,
              'content': BODY, 'content_sha256': hashlib.sha256(BODY.encode()).hexdigest(),
              'receipt_id': 'producer-receipt'}
    def researched(*args, **kwargs):
        dr.seed_manifest_sources([source])
        return report
    monkeypatch.setattr(dr, 'run_research_stage', researched)
    monkeypatch.setattr(dr, '_render_research_charts', lambda *args: {'passed': True})
    calls = []
    def critique(*args, **kwargs):
        calls.append(kwargs['label'])
        return SimpleNamespace(content=json.dumps({'status': 'FAIL', 'weaknesses': ['More detail might help.']})), 'minimax'
    monkeypatch.setattr(dr, '_invoke_tool_free_model', critique)
    monkeypatch.setattr(sys, 'argv', ['deerflow_research.py', '--prompt', 'Forecast capacity', '--out-dir', str(tmp_path / 'out'), '--model', 'minimax', '--no-actors', '--thread-id', 'fixed'])
    assert dr.main() == expected
    receipt = json.loads((tmp_path / 'out' / 'research_quality.json').read_text())
    assert receipt['passed'] is (expected == 0)
    assert len(calls) == 5
    persisted = (tmp_path / 'out' / dr.REPORT_FILENAME).read_text()
    assert marker in persisted


def test_real_multipart_reuses_successful_sections_after_failure(bridge, monkeypatch, tmp_path):
    from collections import Counter
    dr, adapter, _ = bridge
    adapter.prepare(tmp_path / 'out', 'Forecast capacity', 'standard', 'minimax')
    monkeypatch.setenv('RESEARCH_SYNTHESIS_MIN_WORDS', '0')
    monkeypatch.setenv('RESEARCH_SYNTHESIS_WORKERS', '5')
    monkeypatch.setenv('RESEARCH_MODEL_CONCURRENCY_GLOBAL', '5')
    monkeypatch.setattr(dr, '_inline_citations_enabled', lambda: False)
    monkeypatch.setattr(dr, '_dedup_shingles_enabled', lambda: False)
    calls = Counter()
    failing = [True]
    def invoke(_model, _prompt, _log=None, label='bare', *_args):
        calls[label] += 1
        if label == 'synthesis-outline':
            return json.dumps({'sections': [{'title': f'Topic {i}', 'scope': f'Capacity risk topic {i}', 'target_words': 1000, 'covers': [f'KIQ-{i}']} for i in range(5)]})
        if label.startswith('synthesis-section-1') and failing[0]:
            raise RuntimeError('offline interrupted section')
        if label == 'synthesis-summary':
            return '## Executive Summary\n\nObserved capacity remains uncertain.'
        return 'Observed capacity evidence, causal reasoning and competing explanations. ' * 50
    monkeypatch.setattr(dr, '_bare_synth_invoke', invoke)
    log = SimpleNamespace(write=lambda *_: None)
    args = ('Forecast capacity', None, 'standard', 'minimax', [BODY], ['Working note'], BODY, log)
    with pytest.raises(RuntimeError):
        dr.synthesize_multipart(*args)
    completed = {label: count for label, count in calls.items() if not label.startswith('synthesis-section-1')}
    assert len(completed) >= 5
    failing[0] = False
    dr._reset_compaction_stop()
    report = dr.synthesize_multipart(*args)
    assert 'Executive Summary' in report
    assert all(calls[label] == count for label, count in completed.items())
    before = dict(calls)
    assert dr.synthesize_multipart(*args) == report
    assert dict(calls) == before


def test_real_multipart_timeout_retains_execution_ownership(bridge, monkeypatch, tmp_path):
    dr, adapter, _ = bridge
    workspace, _ = adapter.prepare(tmp_path / 'out', 'Forecast capacity', 'standard', 'minimax')
    from research_workspace import ResearchWorkspaceError
    monkeypatch.setenv('RESEARCH_AGENTIC_CALL_TIMEOUT_S', '0.05')
    entered, release = threading.Event(), threading.Event()
    def invoke(*_args):
        entered.set()
        release.wait(5)
        return 'late outline'
    monkeypatch.setattr(dr, '_bare_synth_invoke', invoke)
    try:
        with pytest.raises(dr.ResearchCompactionError):
            dr.synthesize_multipart('Forecast capacity', None, 'standard', 'minimax', [BODY], [], BODY, SimpleNamespace(write=lambda *_: None))
        assert entered.is_set()
        with pytest.raises(ResearchWorkspaceError):
            with workspace.execution_lock():
                pass
    finally:
        release.set()


def test_auxiliary_actor_pass_recovers_without_native_checkpointer(bridge, tmp_path):
    dr, adapter, _ = bridge
    adapter.prepare(tmp_path / 'out', 'Question', 'standard', 'minimax')
    calls = []
    class Client:
        def stream(self, *args, **kwargs):
            calls.append(kwargs['thread_id'])
            yield SimpleNamespace(type='messages-tuple', data={'type': 'tool', 'name': 'read_evidence', 'content': BODY})
            yield SimpleNamespace(type='messages-tuple', data={'type': 'ai', 'id': 'answer', 'content': 'Actor findings. ' * 40})
        def get_thread(self, _):
            return {}
    client = Client()
    log = SimpleNamespace(write=lambda *_: None)
    args = (client, 'Find actor evidence', 'actor-fixed', 32, log, 'actor-ontology')
    first = dr.run_streamed_turn(*args)
    assert dr.run_streamed_turn(*args) == first
    assert calls == ['actor-fixed']
    assert BODY in adapter.actor_evidence('actor-fixed')


def test_stop_during_bare_admission_blocks_physical_send(bridge, monkeypatch, tmp_path):
    dr, adapter, _ = bridge
    import research_admission as admission
    adapter.prepare(tmp_path / 'out', 'Question', 'standard', 'minimax')
    reserve = admission._Ledger.reserve
    def stopped(ledger, *args):
        ticket = reserve(ledger, *args)
        dr._stop_after_compaction_failure(dr.ResearchCompactionError('archive_write_failed', 'sibling'))
        return ticket
    monkeypatch.setattr(admission._Ledger, 'reserve', stopped)
    calls = []
    with pytest.raises(dr.ResearchCompactionError) as failure:
        dr._invoke_model(SimpleNamespace(invoke=lambda *_: calls.append('sent')), ['prompt'])
    assert failure.value.reason == 'archive_write_failed'
    assert calls == []


def test_advisory_targets_only_named_section_in_real_cli(bridge, monkeypatch, tmp_path):
    import hashlib
    dr, _, _ = bridge
    client_module = ModuleType('deerflow.client')
    client_module.DeerFlowClient = lambda **kwargs: object()
    monkeypatch.setitem(sys.modules, 'deerflow', ModuleType('deerflow'))
    monkeypatch.setitem(sys.modules, 'deerflow.client', client_module)
    limits = '## Limitations\n\nThis unchanged limitation remains precise.\n'
    report = '# Research\n\n## Findings\n\n' + 'Initial uncertain evidence [S1]. ' * 20 + '\n\n' + limits
    source = {'url': 'https://example.test/release', 'source_origin': 'fetched', 'reachable': True,
              'content': BODY, 'content_sha256': hashlib.sha256(BODY.encode()).hexdigest()}
    def researched(*args, **kwargs):
        dr.seed_manifest_sources([source])
        return report
    monkeypatch.setattr(dr, 'run_research_stage', researched)
    monkeypatch.setattr(dr, '_render_research_charts', lambda *args: {'passed': True})
    labels = []
    def invoke(*args, **kwargs):
        labels.append(kwargs['label'])
        value = ({'section_heading': 'Findings', 'replacement': 'Revised capacity is 98765; persistence remains uncertain [S1]. ' * 12}
                 if kwargs['label'].startswith('section-repair-') else
                 {'status': 'FAIL', 'weaknesses': [{'section_heading': 'Findings', 'weakness': 'Include the revised observed figure.'}]})
        return SimpleNamespace(content=json.dumps(value)), 'minimax'
    monkeypatch.setattr(dr, '_invoke_tool_free_model', invoke)
    monkeypatch.setattr(sys, 'argv', ['deerflow_research.py', '--prompt', 'Forecast capacity', '--out-dir', str(tmp_path / 'out'), '--model', 'minimax', '--no-actors', '--thread-id', 'fixed'])
    assert dr.main() == 0
    final = (tmp_path / 'out' / dr.REPORT_FILENAME).read_text()
    assert final.endswith(limits) and 'Revised capacity is 98765' in final
    assert len(labels) == 6
    receipt = json.loads((tmp_path / 'out' / 'research_quality.json').read_text())
    assert receipt['passed'] is True
    assert any(r['status'] == 'applied' for r in receipt['advisory']['repairs'])
