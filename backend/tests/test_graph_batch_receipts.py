"""Offline public graph batch timeout regressions (ASTRA-06a).

Production runtime, sync Future/Task bridge, graph lock, facade and GraphBuilder
are exercised. Only graph warm-up and core extraction/storage are faked. A
one-shot Future proxy expires after event barriers instead of a timing race;
it delegates cancellation and cleanup to the real Future and background loop.
These tests specify in-process receipt retention, not durable restart/replay.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timezone
import json
import threading
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services.graph_builder import GraphBuilderService
from app.services.graphiti_client.client import _GraphNamespace
from app.services.graphiti_client import runtime as rt_module
from app.services.graphiti_client.runtime import GraphitiRuntime


@pytest.fixture
def interrupted_batch(monkeypatch, record_property):
    """Exercise scripted storage outcomes on a fixture-owned background loop."""
    monkeypatch.setattr(Config, 'GRAPH_BUILD_CONCURRENCY', 2)
    monkeypatch.setattr(Config, 'GRAPHITI_REMOTE', False)
    monkeypatch.setattr(Config, 'GRAPH_CAST_CHUNK_FILTER', False)
    monkeypatch.setattr(Config, 'GRAPHITI_OP_TIMEOUT_S', 10)
    original_submit = asyncio.run_coroutine_threadsafe
    original_sleep = asyncio.sleep

    def exercise(chunks, *, acknowledged_body=None, unknown_write_body=None,
                 script=None, phase='gather', started_before_deadline=None,
                 batch_size=None, concurrency=2, submitted_chunks=None,
                 cast_terms=None, uncooperative_body=None, inner_timeout=False,
                 acknowledged_before_deadline=0):
        """Script values are per-body sequences of (return|error|block, value).

        The sequence fakes only provider/storage behavior, not receipt/retry logic.
        ``phase=None`` lets the real Future finish without a forced deadline.
        """
        loop = asyncio.new_event_loop()
        loop_ready = threading.Event()

        def serve_loop():
            asyncio.set_event_loop(loop)
            loop.call_soon(loop_ready.set)
            loop.run_forever()

        thread = threading.Thread(target=serve_loop, name='astra-graph-receipt-test', daemon=True)
        thread.start()
        assert loop_ready.wait(5), 'owned event loop did not start'
        runtime = GraphitiRuntime.__new__(GraphitiRuntime)
        runtime._loop = loop
        runtime._graphs = {}
        runtime._graph_locks = {}
        runtime._ingest_skip_reasons = {}
        monkeypatch.setattr(rt_module, 'get_runtime', lambda: runtime)
        monkeypatch.setattr(Config, 'GRAPH_BUILD_CONCURRENCY', concurrency)
        reference_time = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
        expected_submitted = list(chunks if submitted_chunks is None else submitted_chunks)
        target_started = started_before_deadline or len(expected_submitted)
        enough_started = threading.Event()
        phase_ready = threading.Event()
        cleanup_entered = threading.Event()
        enough_acknowledged = threading.Event()
        if not acknowledged_before_deadline:
            enough_acknowledged.set()
        first_returned = asyncio.Event()
        release_cleanup = asyncio.Event()
        release_final_accounting = threading.Event()
        calls, storage_events, cancelled, forced_deadlines, cancellation_requests = [], [], [], [], []
        active, attempts = set(), {}
        timeout_injected = False
        if phase == 'gather':
            phase_ready.set()

        async def ensure_graph(graph_id):
            assert graph_id == 'synthetic-receipt-graph'
            if inner_timeout:
                raise TimeoutError('synthetic inner operation timeout')
            return None

        async def core_ingest(graph_id, *, name, body, source_type,
                              source_description, reference_time,
                              record_skip_reason=True, attempt_budget=None):
            attempts[body] = attempts.get(body, 0) + 1
            attempt = attempts[body]
            call_id = len(calls)
            calls.append({
                'graph_id': graph_id, 'name': name, 'body': body, 'attempt': attempt,
                'source_type': source_type, 'source_description': source_description,
                'reference_time': reference_time.isoformat(), 'attempt_budget': attempt_budget,
            })
            active.add(call_id)
            if len(calls) >= target_started:
                enough_started.set()
            try:
                actions = (script or {}).get(body)
                if actions:
                    action, value = actions[min(attempt - 1, len(actions) - 1)]
                elif body == acknowledged_body:
                    action, value = 'return', 'uuid-fast'
                else:
                    action, value = 'block', None
                if action == 'return_after_another':
                    await first_returned.wait()
                    action = 'return'
                if action == 'return':
                    storage_events.append({'body': body, 'returned_uuid': value})
                    first_returned.set()
                    acknowledgements = sum(
                        isinstance(event.get('returned_uuid'), str)
                        and bool(event['returned_uuid'].strip()) for event in storage_events)
                    if acknowledgements >= acknowledged_before_deadline:
                        enough_acknowledged.set()
                    return value  # Production core returns a UUID string, never a dict.
                if action == 'cancel':
                    raise asyncio.CancelledError(value)
                if action == 'error':
                    error = RuntimeError(value)
                    error._graphiti_ingest_attempts = 1
                    raise error
                if body == unknown_write_body:
                    storage_events.append({'body': body, 'write_observed_without_ack': True})
                if phase == 'replay' and attempt > 1:
                    phase_ready.set()
                await asyncio.Event().wait()
                raise AssertionError('blocked episode was unexpectedly released')
            except asyncio.CancelledError:
                cancelled.append(body)
                if body == uncooperative_body:
                    cleanup_entered.set()
                    await release_cleanup.wait()
                raise
            finally:
                active.remove(call_id)

        async def controlled_cooldown(delay, result=None):
            if phase == 'cooldown':
                phase_ready.set()
                await asyncio.Event().wait()
            if phase == 'replay':
                return result
            return await original_sleep(delay, result=result)

        runtime._ensure_graph = ensure_graph
        runtime._add_episode_locked = core_ingest
        if phase == 'final_accounting':
            original_record_skip = runtime._record_skip_reason

            def pause_after_final_count(graph_id, reason):
                # Preserve the actual counter write, then model slow final logging.
                original_record_skip(graph_id, reason)
                if not phase_ready.is_set():
                    phase_ready.set()
                    assert release_final_accounting.wait(5), 'deadline never released final accounting'

            runtime._record_skip_reason = pause_after_final_count
        if phase in {'cooldown', 'replay'}:
            monkeypatch.setenv('GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S', '7.5')
            monkeypatch.setattr(rt_module.asyncio, 'sleep', controlled_cooldown)

        class ObservedFuture:
            def __init__(self, future, force_deadline):
                self.future = future
                self.force_deadline = force_deadline

            def result(self, timeout=None):
                if self.force_deadline:
                    self.force_deadline = False
                    assert enough_started.wait(5), 'deadline preceded intended episode starts'
                    assert phase_ready.wait(5), 'deadline preceded intended ingestion phase'
                    assert enough_acknowledged.wait(5), 'deadline preceded intended acknowledgements'
                    forced_deadlines.append({'timeout_argument': timeout})
                    raise concurrent.futures.TimeoutError()
                return self.future.result(timeout)

            def cancel(self):
                cancellation_requests.append(True)
                try:
                    return self.future.cancel()
                finally:
                    # Cancellation is requested before the paused counter/logger
                    # returns, so the real Future deadline has already won.
                    release_final_accounting.set()

            def __getattr__(self, name):
                return getattr(self.future, name)

        def submit_with_observed_deadline(coro, target_loop):
            nonlocal timeout_injected
            future = original_submit(coro, target_loop)
            if not timeout_injected:
                timeout_injected = True
                return ObservedFuture(future, phase is not None)
            return future

        monkeypatch.setattr(rt_module.asyncio, 'run_coroutine_threadsafe', submit_with_observed_deadline)
        submitted_batches, batch_errors = [], []
        namespace = _GraphNamespace(runtime)
        original_add_batch = namespace.add_batch

        def observe_add_batch(*args, **kwargs):
            submitted_batches.append([episode.data for episode in kwargs['episodes']])
            try:
                return original_add_batch(*args, **kwargs)
            except Exception as error:
                batch_errors.append({'type': type(error).__name__, 'message': str(error)})
                raise

        namespace.add_batch = observe_add_batch
        builder = GraphBuilderService.__new__(GraphBuilderService)
        builder.client = SimpleNamespace(graph=namespace)
        builder.task_manager = None
        builder.last_ingest_stats = None
        builder.last_actor_graph_seed_manifest = None
        if cast_terms is not None:
            monkeypatch.setattr(Config, 'GRAPH_CAST_CHUNK_FILTER', True)
            builder._cast_chunk_terms = {'synthetic-receipt-graph': cast_terms}
        returned, error_type, lock_observation = None, None, None
        try:
            try:
                returned = builder.add_text_batches(
                    'synthetic-receipt-graph', chunks, batch_size=batch_size or len(chunks),
                    reference_time=reference_time,
                )
            except Exception as error:
                error_type = type(error).__name__

            async def read_after_timeout():
                async with runtime._graph_lock('synthetic-receipt-graph'):
                    return {
                        'active_children_when_lock_reacquired': sorted(active),
                        'cancelled_children': sorted(cancelled),
                        'storage_events_at_read': list(storage_events),
                    }

            if uncooperative_body is None:
                # No cleanup barrier before this probe: old work must have quiesced.
                lock_observation = runtime.run(read_after_timeout(), timeout=5)
            observation = {
                'chunks': chunks, 'returned_uuids': returned, 'raised_type': error_type,
                'storage_events': list(storage_events), 'calls': list(calls),
                'ingest_stats': builder.last_ingest_stats, 'lock_observation': lock_observation,
                'forced_deadlines': forced_deadlines, 'cancellation_requests': len(cancellation_requests),
                'cleanup_entered': cleanup_entered.is_set(), 'active_at_return': sorted(active),
                'submitted_batches': submitted_batches, 'batch_errors': batch_errors,
            }
            record_property('graph_batch_observation', json.dumps(observation, sort_keys=True))
            assert len(forced_deadlines) == (phase is not None)
            assert all(call['reference_time'] == reference_time.isoformat() for call in calls)
            assert all(call['source_type'] == 'text' for call in calls)
            assert all(call['source_description'] == 'mirofish-text' for call in calls)
            if lock_observation is not None:
                assert lock_observation['active_children_when_lock_reacquired'] == []
                assert lock_observation['storage_events_at_read'] == storage_events
            return observation
        finally:
            release_final_accounting.set()
            # Only this fixture's tasks/thread are drained; never a live global runtime.
            loop.call_soon_threadsafe(release_cleanup.set)

            async def drain_owned_tasks():
                pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            original_submit(drain_owned_tasks(), loop).result(5)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)
            assert not thread.is_alive(), 'owned event-loop thread failed to stop'
            loop.close()

    return exercise


@pytest.mark.parametrize('chunks', [
    ['synthetic acknowledged fact', 'synthetic hung fact'],
    ['synthetic hung fact', 'synthetic acknowledged fact'],
], ids=['ack-first', 'ack-second'])
def test_public_batch_timeout_preserves_acknowledged_episode(interrupted_batch, chunks):
    observed = interrupted_batch(chunks, acknowledged_body='synthetic acknowledged fact')
    assert observed['storage_events'] == [
        {'body': 'synthetic acknowledged fact', 'returned_uuid': 'uuid-fast'}
    ]
    assert [call['body'] for call in observed['calls']] == chunks
    assert observed['lock_observation']['cancelled_children'] == ['synthetic hung fact']
    assert observed['returned_uuids'] == ['uuid-fast'], observed
    assert observed['raised_type'] is None
    assert observed['ingest_stats']['succeeded'] == 1
    assert observed['ingest_stats']['failed'] == 1
    receipt = observed['ingest_stats']['batch_timeouts'][0]
    ack_index = chunks.index('synthetic acknowledged fact')
    assert receipt['acknowledged'] == [{'input_index': ack_index, 'uuid': 'uuid-fast'}]
    assert receipt['started_unacknowledged'] == [1 - ack_index]
    assert receipt['never_started'] == []
    assert receipt['cleanup_confirmed'] is True


def test_unacknowledged_write_never_becomes_success_or_automatic_replay(interrupted_batch):
    observed = interrupted_batch(
        ['synthetic unknown write', 'synthetic blocked fact'],
        unknown_write_body='synthetic unknown write',
    )
    assert observed['storage_events'] == [
        {'body': 'synthetic unknown write', 'write_observed_without_ack': True}
    ]
    assert observed['returned_uuids'] is None
    assert observed['raised_type'] == 'ValueError'
    assert observed['ingest_stats']['succeeded'] == 0
    assert observed['ingest_stats']['failed'] == 2
    assert len(observed['calls']) == 2
    receipt = observed['ingest_stats']['batch_timeouts'][0]
    assert receipt['acknowledged'] == []
    assert receipt['started_unacknowledged'] == [0, 1]
    assert receipt['never_started'] == []


def test_queued_input_does_not_start_after_deadline(interrupted_batch):
    chunks = ['ack', 'hung-a', 'hung-b', 'queued']
    observed = interrupted_batch(chunks, acknowledged_body='ack', started_before_deadline=3)
    assert [call['body'] for call in observed['calls']] == chunks[:3]
    assert observed['lock_observation']['cancelled_children'] == ['hung-a', 'hung-b']
    assert observed['returned_uuids'] == ['uuid-fast'], observed
    assert observed['ingest_stats']['succeeded'] == 1
    assert observed['ingest_stats']['failed'] == 3
    receipt = observed['ingest_stats']['batch_timeouts'][0]
    assert receipt['started_unacknowledged'] == [1, 2]
    assert receipt['never_started'] == [3]
    assert observed['ingest_stats']['skip_reasons'] == {
        'batch_timeout_unacknowledged': 2, 'batch_timeout_not_started': 1}


@pytest.mark.parametrize('phase', ['cooldown', 'replay'])
def test_timeout_during_rate_limit_recovery_preserves_prior_receipt(interrupted_batch, phase):
    observed = interrupted_batch(['ack', 'limited'], acknowledged_body='ack', phase=phase,
        script={'limited': [('error', 'HTTP 429 rate limit'), ('block', None)]})
    assert [call['body'] for call in observed['calls']] == (
        ['ack', 'limited'] if phase == 'cooldown' else ['ack', 'limited', 'limited'])
    if phase == 'replay':
        assert observed['calls'][-1]['attempt_budget'] == 1
    assert observed['returned_uuids'] == ['uuid-fast'], observed
    assert observed['ingest_stats']['succeeded'] == 1
    assert observed['ingest_stats']['failed'] == 1
    reason = 'rate_limit' if phase == 'cooldown' else 'batch_timeout_unacknowledged'
    assert observed['ingest_stats']['skip_reasons'] == {reason: 1}


def test_acknowledged_replay_is_retained_before_next_replay_hangs(interrupted_batch):
    observed = interrupted_batch(['ack', 'recovered', 'hung'], acknowledged_body='ack',
        phase='replay', concurrency=3, script={
            'recovered': [('error', 'HTTP 429 rate limit'), ('return', 'uuid-recovered')],
            'hung': [('error', 'HTTP 429 rate limit'), ('block', None)],
        })
    assert [call['body'] for call in observed['calls']] == [
        'ack', 'recovered', 'hung', 'recovered', 'hung']
    assert observed['returned_uuids'] == ['uuid-fast', 'uuid-recovered'], observed
    assert observed['ingest_stats']['succeeded'] == 2
    assert observed['ingest_stats']['failed'] == 1


def test_batch_local_duplicate_names_do_not_drop_prior_receipts(interrupted_batch):
    observed = interrupted_batch(['same', 'hung', 'same', 'other'],
        batch_size=2, started_before_deadline=2, script={
            'same': [('return', 'uuid-first'), ('return', 'uuid-second')],
            'other': [('return', 'uuid-other')],
        })
    assert [call['name'] for call in observed['calls']] == ['chunk-0', 'chunk-1', 'chunk-0', 'chunk-1']
    assert observed['returned_uuids'] == ['uuid-first', 'uuid-second', 'uuid-other'], observed
    assert observed['ingest_stats']['succeeded'] == 3
    assert observed['ingest_stats']['failed'] == 1


@pytest.mark.parametrize('invalid_uuid', ['', None, {}, True])
def test_empty_uuid_is_not_a_commit_receipt(interrupted_batch, invalid_uuid):
    observed = interrupted_batch(['empty', 'hung'], script={'empty': [('return', invalid_uuid)]})
    assert observed['returned_uuids'] is None
    assert observed['raised_type'] == 'ValueError'
    assert observed['ingest_stats']['succeeded'] == 0
    assert len(observed['calls']) == 2


def test_cast_filter_denominator_and_exact_inputs_survive_partial_timeout(interrupted_batch):
    chunks = ['Alice acknowledged fact', 'unrelated weather', 'Alice hung fact']
    observed = interrupted_batch(chunks, acknowledged_body=chunks[0], cast_terms=['alice'],
        submitted_chunks=[chunks[0], chunks[2]])
    assert [call['body'] for call in observed['calls']] == [chunks[0], chunks[2]]
    assert observed['returned_uuids'] == ['uuid-fast'], observed
    stats = observed['ingest_stats']
    assert (stats['input_chunks'], stats['total'], stats['skipped_cast_filter']) == (3, 2, 1)
    assert stats['skip_reasons']['skipped_cast_filter'] == 1
    assert stats['skip_ratio'] == 0.5


def test_inner_timeout_does_not_trigger_outer_deadline_cancellation(interrupted_batch):
    observed = interrupted_batch(['a', 'b'], phase=None, inner_timeout=True)
    assert observed['calls'] == []
    assert observed['returned_uuids'] is None
    assert observed['cancellation_requests'] == 0, observed
    assert observed['batch_errors'] == [
        {'type': 'TimeoutError', 'message': 'synthetic inner operation timeout'}
    ]


def test_unconfirmed_cleanup_stops_before_next_batch(interrupted_batch):
    observed = interrupted_batch(['ack', 'stubborn', 'later-a', 'later-b'],
        acknowledged_body='ack', uncooperative_body='stubborn',
        batch_size=2, started_before_deadline=2,
        script={'later-a': [('return', 'uuid-later-a')], 'later-b': [('return', 'uuid-later-b')]})
    assert observed['cleanup_entered'] is True
    assert observed['raised_type'] == 'GraphBatchTimeout', observed
    assert [call['body'] for call in observed['calls']] == ['ack', 'stubborn'], observed
    assert observed['submitted_batches'] == [['ack', 'stubborn']]
    assert observed['returned_uuids'] is None
    assert observed['ingest_stats']['cleanup_confirmed'] is False
    assert observed['ingest_stats']['not_submitted'] == 2
    assert observed['ingest_stats']['succeeded'] == 1


@pytest.mark.parametrize('concurrency', [1, 2])
def test_healthy_batches_preserve_uuid_order_and_call_count(interrupted_batch, concurrency):
    observed = interrupted_batch(['first', 'second'], phase=None, concurrency=concurrency,
        script={'first': [('return', 'uuid-first')], 'second': [('return', 'uuid-second')]})
    assert observed['returned_uuids'] == ['uuid-first', 'uuid-second']
    assert observed['raised_type'] is None
    assert [call['body'] for call in observed['calls']] == ['first', 'second']
    assert observed['cancellation_requests'] == 0
    assert observed['ingest_stats']['failed'] == 0
    assert 'batch_timeouts' not in observed['ingest_stats']


def test_completion_order_does_not_change_input_order_of_receipts(interrupted_batch):
    observed = interrupted_batch(['first', 'second', 'hung'], concurrency=3,
        acknowledged_before_deadline=2, script={
            'first': [('return_after_another', 'uuid-first')],
            'second': [('return', 'uuid-second')],
        })
    assert [event['returned_uuid'] for event in observed['storage_events']] == [
        'uuid-second', 'uuid-first']
    assert observed['returned_uuids'] == ['uuid-first', 'uuid-second']
    assert observed['ingest_stats']['batch_timeouts'][0]['acknowledged'] == [
        {'input_index': 0, 'uuid': 'uuid-first'}, {'input_index': 1, 'uuid': 'uuid-second'}]


def test_known_failure_and_unknown_timeout_have_separate_skip_reasons(interrupted_batch):
    observed = interrupted_batch(['ack', 'filtered', 'hung'], concurrency=3,
        acknowledged_body='ack', script={'filtered': [('error', 'content filtered as sensitive')]})
    assert len(observed['calls']) == 3
    assert observed['returned_uuids'] == ['uuid-fast']
    assert observed['ingest_stats']['failed'] == 2
    assert observed['ingest_stats']['skip_reasons'] == {
        'content_filter': 1, 'batch_timeout_unacknowledged': 1}


@pytest.mark.parametrize('returned_cancellation', [False, True], ids=['ordinary', 'cancelled-child'])
def test_deadline_after_final_accounting_counts_each_failure_once(
        interrupted_batch, returned_cancellation):
    chunks = ['ack', 'filtered', 'no-uuid']
    script = {
        'filtered': [('error', 'content filtered as sensitive')],
        'no-uuid': [('return', None)],
    }
    if returned_cancellation:
        chunks.append('cancelled-child')
        # gather(return_exceptions=True) converts this child cancellation into
        # a returned CancelledError during the production final accounting pass.
        script['cancelled-child'] = [('cancel', 'synthetic child cancellation')]
    observed = interrupted_batch(chunks, acknowledged_body='ack',
        script=script, phase='final_accounting', concurrency=len(chunks))
    assert observed['returned_uuids'] == ['uuid-fast']
    assert observed['raised_type'] is None
    assert len(observed['calls']) == len(chunks)
    assert observed['ingest_stats']['succeeded'] == 1
    assert observed['ingest_stats']['failed'] == len(chunks) - 1
    expected_reasons = {'content_filter': 1, 'batch_timeout_unacknowledged': 1}
    if returned_cancellation:
        expected_reasons['other'] = 1
    assert observed['ingest_stats']['skip_reasons'] == expected_reasons, observed
    assert sum(observed['ingest_stats']['skip_reasons'].values()) == len(chunks) - 1
