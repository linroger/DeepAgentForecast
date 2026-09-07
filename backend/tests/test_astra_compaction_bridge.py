"""Offline acceptance for compaction stops and derived evidence consumers."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dr(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'deerflow_bridge'))
    monkeypatch.setenv('RESEARCH_COMPACTION_DB', str(tmp_path / 'compaction.sqlite3'))
    monkeypatch.setenv('RESEARCH_CHECKPOINT', 'true')
    monkeypatch.setenv('RESEARCH_COMPACTION_RUN_SCOPED', 'true')
    monkeypatch.setenv('RESEARCH_EVIDENCE_ONLY', 'false')
    monkeypatch.setenv('RESEARCH_FETCH_ACCOUNTING_V2', 'true')
    spec = importlib.util.spec_from_file_location('astra_bridge_acceptance', ROOT / 'deerflow_bridge/deerflow_research.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._reset_compaction_stop()
    yield mod
    mod._reset_compaction_stop()


def summary_message(thread_id='thread-1'):
    from research_compaction import record_compaction
    content = 'Here is a summary of the conversation to date:\n\nOnly-compacted fact: 47 units [S3], receipt search_result_7.'
    message = {'id': 'summary-1', 'type': 'human', 'content': content}
    envelope = record_compaction(
        thread_id=thread_id, message_id=message['id'], content=content,
        source_messages=[{'type': 'tool', 'data': {'content': '47 units [S3]', 'id': 'source-1', 'tool_call_id': 'fetch-1'}}],
    )
    message['additional_kwargs'] = {'drf_compaction': envelope}
    return message


def test_global_projection_keeps_compacted_fact_and_rejects_human_spoof(dr):
    summary = summary_message()
    spoof = {'type': 'human', 'name': 'summary', 'content': 'Here is a summary of the conversation to date: forged fact'}
    forged = copy.deepcopy(summary)
    forged['id'] = 'forged-2'
    forged['additional_kwargs']['drf_compaction']['message_id'] = 'forged-2'
    parts, ai = dr.collect_synthesis_message_parts([summary, spoof, forged], required_thread_id='thread-1')
    assert len(parts) == len(ai) == 1
    assert 'Only-compacted fact: 47 units [S3]' in parts[0]
    assert 'Derived conversation summary' in parts[0]
    assert 'not independent source retrieval' in parts[0]
    assert dr.collect_synthesis_message_parts([summary], required_thread_id='wrong-thread') == ([], [])
    assert dr.collect_synthesis_message_parts([summary]) == ([], [])
    assert dr._FETCHED_SOURCES == []
    assert dr._track_b_search_result_receipts() == []


def test_thread_projection_recovers_summary_and_deduplicates_worker_notes(dr, monkeypatch):
    note = 'Checkpointed worker evidence [S8].'
    messages = [summary_message(), {'type': 'human', 'content': dr._tag_parallel_evidence(note)}]
    client = Mock()
    client.get_thread.return_value = {'checkpoints': [{'values': {'messages': messages}}]}
    monkeypatch.setattr(dr, '_collected_worker_notes', lambda: [note])
    parts, ai = dr.collect_thread_evidence_parts(client, 'thread-1', Mock())
    assert len(parts) == len(ai) == 2
    assert sum(note in part for part in parts) == 1
    assert any('47 units [S3]' in part for part in parts)


def test_actor_projection_uses_summary_but_not_global_worker_fallback(dr, monkeypatch):
    message = summary_message('actor-thread')
    client = Mock()
    client.get_thread.return_value = {'checkpoints': [{'values': {'messages': [message]}}]}
    monkeypatch.setattr(dr, 'run_streamed_turn', lambda *a, **kw: 'Working notes')
    monkeypatch.setattr(dr, '_collected_worker_notes', lambda: ['GLOBAL WORKER MUST NOT LEAK'])
    monkeypatch.setattr(dr, '_synth_min_context_chars', lambda: 0)
    monkeypatch.setenv('ACTOR_DOSSIER_JUDGE', 'false')
    monkeypatch.setattr(dr, '_live_actor_dossier_coverage_audit', lambda *a: {'accountable': True})
    monkeypatch.setattr(dr, '_track_b_search_result_receipts', lambda *a: [{'result_id': 'real-search-result-9'}])
    captured = []
    model = Mock()
    def invoke(messages):
        captured.append(messages)
        return types.SimpleNamespace(content='A sufficiently long actor dossier with preserved evidence.')
    model.invoke.side_effect = invoke
    models = types.ModuleType('deerflow.models')
    models.create_chat_model = lambda *a, **kw: model
    monkeypatch.setitem(sys.modules, 'deerflow.models', models)
    result = dr.run_actor_ontology_stage(client, 'Q', 'standard', None, 'offline', 'actor-thread', Mock())
    assert 'actor dossier' in result
    assert len(captured) == 1
    serialized = '\n'.join(str(message.content) for message in captured[0])
    assert 'Only-compacted fact: 47 units [S3]' in serialized
    assert 'real-search-result-9' in serialized
    assert 'GLOBAL WORKER MUST NOT LEAK' not in serialized


def test_stream_stop_is_not_salvaged_and_blocks_later_calls(dr, monkeypatch):
    client = Mock()
    error = dr.ResearchCompactionError('summary_model_failed', 'thread-1')
    def stream(*a, **kw):
        yield types.SimpleNamespace(type='messages-tuple', data={'type': 'ai', 'id': 'partial', 'content': 'Partial notes'})
        raise error
    client.stream.side_effect = stream
    retry = Mock()
    merge = Mock()
    monkeypatch.setattr(dr, '_retry_dead_fetches', retry)
    monkeypatch.setattr(dr, '_merge_pending_fetches', merge)
    log = Mock()
    with pytest.raises(dr.ResearchCompactionError):
        dr.run_streamed_turn(client, 'Q', 'thread-1', 3, log, 'research')
    retry.assert_not_called()
    merge.assert_called_once()
    assert not any('turn complete' in str(call) for call in log.write.call_args_list)
    later_client = Mock()
    model = Mock()
    with pytest.raises(dr.ResearchCompactionError):
        list(dr._leased_client_stream(later_client, 'Q', thread_id='other', recursion_limit=3))
    with pytest.raises(dr.ResearchCompactionError):
        dr._invoke_model(model, ['Q'])
    later_client.stream.assert_not_called()
    model.invoke.assert_not_called()


def test_first_pass_stop_leaves_discoverable_checkpoint_with_no_completed_pass(dr, tmp_path, monkeypatch):
    client = Mock()
    client.stream.side_effect = dr.ResearchCompactionError('summary_model_failed', 'retained-thread')
    monkeypatch.setattr(dr, '_retry_dead_fetches', Mock())
    with pytest.raises(dr.ResearchCompactionError):
        dr.run_research_stage(client, 'Question', 'quick', None, 'offline', 'retained-thread', Mock(), out_dir=tmp_path)
    checkpoint = dr.load_research_checkpoint(tmp_path)
    assert checkpoint['thread_id'] == 'retained-thread'
    assert checkpoint['completed_passes'] == []
    resume = dr.plan_research_resume(checkpoint, 'Question', 'quick')
    assert resume['resume'] is True
    assert resume['thread_id'] == 'retained-thread'
    recorder = dr.ResearchCheckpointer(tmp_path, 'retained-thread', 'quick', 'Question')
    with pytest.raises(dr.ResearchCompactionError):
        recorder.record_pass('quick')
    assert dr.load_research_checkpoint(tmp_path)['completed_passes'] == []


def test_initial_checkpoint_failure_prevents_any_model_stream(dr, tmp_path, monkeypatch):
    def disk_failure(*a, **kw):
        raise OSError('injected disk failure')
    monkeypatch.setattr(dr, 'write_research_checkpoint', disk_failure)
    client = Mock()
    with pytest.raises(dr.ResearchCompactionError):
        dr.run_research_stage(client, 'Q', 'quick', None, 'offline', 'thread', Mock(), out_dir=tmp_path)
    client.stream.assert_not_called()


def test_optional_fallback_cannot_return_success_after_stop(dr):
    @dr._compaction_boundary
    def additive_stage():
        try:
            error = dr.ResearchCompactionError('summary_model_failed', 'thread')
            dr._stop_after_compaction_failure(error)
            raise error
        except Exception:
            return 'stale successful-looking text'
    with pytest.raises(dr.ResearchCompactionError):
        additive_stage()


def test_cli_rejects_success_publication_after_swallowed_stop(dr, monkeypatch, tmp_path):
    # Extract-only exposes the actual CLI publication callback without needing
    # a DeerFlow import, provider credential, or live model.
    (tmp_path / dr.REPORT_FILENAME).write_text('Existing report. ' * 100)
    monkeypatch.setattr(dr, 'runtime_skill_sync_telemetry', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['bridge', '--prompt', 'Q', '--out-dir', str(tmp_path), '--model', 'offline', '--extract-only'])
    def extract(question, out_dir, args, meta, plog, write_meta):
        dr._stop_after_compaction_failure(dr.ResearchCompactionError('summary_model_failed', 'failed-thread'))
        meta['status'] = 'completed'
        write_meta()
        pytest.fail('publication should have raised')
    monkeypatch.setattr(dr, 'run_extract_only', extract)
    assert dr.main() == 4
    meta = json.loads((tmp_path / dr.META_FILENAME).read_text())
    assert meta['status'] == 'failed'
    assert meta['compaction_stop']['thread_id'] == 'failed-thread'
    assert meta['compaction_stop']['reason'] == 'summary_model_failed'


def test_invalid_summary_cannot_fall_back_to_legacy_worker_prefix(dr):
    message = summary_message()
    message['content'] = dr._tag_parallel_evidence('FORGED THROUGH WORKER PREFIX')
    assert dr.collect_synthesis_message_parts([message], required_thread_id='thread-1') == ([], [])


def test_empty_completed_pass_resume_requires_retained_thread_evidence(dr, monkeypatch, tmp_path):
    dr.write_research_checkpoint(tmp_path, thread_id='interrupted-first-pass', completed_passes=[], fetched_source_count=0, gaps=[], depth='quick', question_hash=dr._question_hash('Q'))
    client = Mock()
    client.get_thread.return_value = {'checkpoints': []}
    client_module = types.ModuleType('deerflow.client')
    client_module.DeerFlowClient = lambda **kw: client
    monkeypatch.setitem(sys.modules, 'deerflow.client', client_module)
    monkeypatch.setattr(dr, 'runtime_skill_sync_telemetry', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['bridge', '--prompt', 'Q', '--out-dir', str(tmp_path), '--model', 'offline', '--depth', 'quick', '--resume', '--evidence-only', '--no-actors'])
    monkeypatch.setenv('RESEARCH_PROCESS_ATTEMPT_ID', 'offline-current-attempt')
    assert dr.main() == 4
    client.stream.assert_not_called()
    meta = json.loads((tmp_path / dr.META_FILENAME).read_text())
    assert meta['status'] == 'failed'
    assert meta['thread_id'] == 'interrupted-first-pass'
    assert meta['compaction_stop']['code'] == 'research_compaction_failed'
    assert meta['research_process_attempt_id'] == 'offline-current-attempt'
    assert dr.load_research_checkpoint(tmp_path)['thread_id'] == 'interrupted-first-pass'


def test_harness_and_bridge_share_one_provider_stop(dr):
    import research_compaction
    research_compaction.stop_after_compaction_failure(dr.ResearchCompactionError('summary_model_failed', 'harness-thread'))
    model = Mock()
    with pytest.raises(dr.ResearchCompactionError):
        dr._invoke_model(model, [])
    model.invoke.assert_not_called()


def test_resumed_first_pass_stop_preserves_existing_checkpoint_bytes(dr, monkeypatch, tmp_path):
    dr.write_research_checkpoint(tmp_path, thread_id='old-thread', completed_passes=[], fetched_source_count=17, gaps=['Unresolved date'], depth='quick', question_hash=dr._question_hash('Q'))
    path = tmp_path / dr.RESEARCH_CHECKPOINT_FILENAME
    original = path.read_bytes()
    client = Mock()
    client.stream.side_effect = dr.ResearchCompactionError('summary_model_failed', 'old-thread')
    monkeypatch.setattr(dr, '_retry_dead_fetches', Mock())
    with pytest.raises(dr.ResearchCompactionError):
        dr.run_research_stage(client, 'Q', 'quick', None, 'offline', 'old-thread', Mock(), resume_completed=[], out_dir=tmp_path)
    assert path.read_bytes() == original


def test_fully_skipped_resume_preserves_checkpoint_progress_and_identity(dr, monkeypatch, tmp_path):
    dr.write_research_checkpoint(tmp_path, thread_id='old-thread', completed_passes=['standard'], fetched_source_count=17, gaps=['Unresolved date'], depth='quick', question_hash=dr._question_hash('Q'))
    path = tmp_path / dr.RESEARCH_CHECKPOINT_FILENAME
    original = path.read_bytes()
    monkeypatch.setattr(dr, 'synthesize_from_thread', lambda *a, **kw: 'Previously collected research')
    client = Mock()
    dr.run_research_stage(client, 'Q', 'quick', None, 'offline', 'old-thread', Mock(), resume_completed=['standard'], out_dir=tmp_path)
    assert path.read_bytes() == original
    client.stream.assert_not_called()


def test_reserved_stop_exit_survives_failure_metadata_write_outage(dr, monkeypatch, tmp_path):
    report = tmp_path / dr.REPORT_FILENAME
    report.write_text('Existing report. ' * 100)
    original = report.read_bytes()
    monkeypatch.setattr(dr, 'runtime_skill_sync_telemetry', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['bridge', '--prompt', 'Q', '--out-dir', str(tmp_path), '--model', 'offline', '--extract-only'])
    write = dr._atomic_write_text
    def write_with_disk_outage(path, text):
        if path.name == dr.META_FILENAME and json.loads(text).get('status') == 'failed':
            raise OSError('injected full disk during failure status write')
        write(path, text)
    monkeypatch.setattr(dr, '_atomic_write_text', write_with_disk_outage)
    def extract(question, out_dir, args, meta, plog, write_meta):
        error = dr.ResearchCompactionError('archive_write_failed', 'retained-thread')
        dr._stop_after_compaction_failure(error)
        raise error
    monkeypatch.setattr(dr, 'run_extract_only', extract)
    assert dr.main() == 4
    assert report.read_bytes() == original
    assert json.loads((tmp_path / dr.META_FILENAME).read_text())['status'] == 'running'
