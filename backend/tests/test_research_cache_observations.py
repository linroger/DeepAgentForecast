"""Offline parent receipts preserve partial cache usage without adding input twice."""

import sqlite3

import pytest

from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter, get_run_context, set_run_context


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    state = po.PipelineState(pipeline_id="pipe_cache_observations", prompt="offline", task_id="task-a")
    previous_context = get_run_context()
    LLMMeter.reset(state.pipeline_id)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    yield orch, state, tmp_path
    LLMMeter.reset(state.pipeline_id)
    set_run_context(*previous_context)


def _assert_usage(snapshot, *, prompt, completion, cache_read, cache_write):
    total = snapshot["total"]
    assert total["prompt_tokens"] == prompt
    assert total["completion_tokens"] == completion
    assert total["total_tokens"] == prompt + completion
    assert total["cache_read_tokens"] == cache_read
    assert total["cache_write_tokens"] == cache_write
    # Zero is the observed uncached counter, not a claim of no uncached usage.
    assert total["uncached_tokens"] == 0
    assert snapshot["cache_partition_known"] is False
    assert snapshot["usage_complete"] is False


@pytest.mark.parametrize("failed", [False, True])
def test_actual_child_stream_keeps_cache_on_success_and_failure(pipeline, monkeypatch, failed):
    from test_research_spend_flush import _wire_fake_subprocess
    orch, state, root = pipeline
    lines = [
        "[usage] tokens in=100 out=20 total=120 cache_read=60 cache_write=5 cache_partition=unknown\n",
        # A late cache detail may grow without additional input/output tokens.
        "[usage] tokens in=0 out=0 total=0 cache_read=5 cache_write=2 cache_partition=unknown\n",
        # Legacy end-only lines remain readable and add no observed cache detail.
        "[usage] tokens in=25 out=5 total=30\n",
    ]
    handoff = _wire_fake_subprocess(monkeypatch, root, lines, 3 if failed else 0)
    (handoff / "research_report.md").write_text("offline evidence " * 60)
    failed_spends = []
    original = po._flush_failed_research_attempt_spend

    def observe_failure(spend, *args, **kwargs):
        failed_spends.append(dict(spend))
        return original(spend, *args, **kwargs)

    monkeypatch.setattr(po, "_flush_failed_research_attempt_spend", observe_failure)
    kwargs = {"on_progress": lambda *a: None, "timeout": 10,
              "model": "claude", "budget_run_id": state.pipeline_id}
    if failed:
        with pytest.raises(RuntimeError, match="研究子进程失败"):
            po.DeerFlowResearchRunner.run("offline question", str(handoff), **kwargs)
        observation = failed_spends[0]
        assert observation["cache_read_tokens"] == 65
        assert observation["cache_write_tokens"] == 7
        assert observation["cache_partition"] == "unknown"
        with sqlite3.connect(root / "pipelines" / "usage_ledger.sqlite3") as connection:
            assert connection.execute("SELECT status FROM usage_operations WHERE source='research_process'").fetchone()[0] == "failed"
    else:
        result = po.DeerFlowResearchRunner.run("offline question", str(handoff), **kwargs)
        observation = result["research_telemetry"]
        assert observation["cache_read_tokens"] == 65
        assert observation["cache_write_tokens"] == 7
        assert observation["cache_partition"] == "unknown"
        receipt = observation["process_usage_snapshots"][0]
        assert receipt["cache_read_tokens"] == 65
        assert receipt["cache_write_tokens"] == 7
        orch._record_research_telemetry(state, observation)
        orch._record_research_telemetry(state, observation)
    snapshot = LLMMeter.cumulative_snapshot(state.pipeline_id)
    _assert_usage(snapshot, prompt=125, completion=25, cache_read=65, cache_write=7)
    assert snapshot["total"]["calls"] == 1


def test_growing_repeated_and_resumed_process_cache_observations(pipeline):
    _orch, state, _root = pipeline
    observation = {"process_attempt_id": "same-process", "model": "claude",
                   "tokens_in": 100, "tokens_out": 20, "wall_s": 1,
                   "cache_read_tokens": 10, "cache_write_tokens": 2,
                   "uncached_tokens": 999, "cache_partition": "known"}
    po._record_research_process_usage(observation, state.pipeline_id, status="running")
    observation.update(cache_read_tokens=35, cache_write_tokens=5)
    po._record_research_process_usage(observation, state.pipeline_id, status="running")
    po._record_research_process_usage(observation, state.pipeline_id, status="completed")
    stale = {**observation, "cache_read_tokens": 5, "cache_write_tokens": 0}
    po._record_research_process_usage(stale, state.pipeline_id, status="running")
    _assert_usage(LLMMeter.cumulative_snapshot(state.pipeline_id),
                  prompt=100, completion=20, cache_read=35, cache_write=5)

    LLMMeter.reset(state.pipeline_id)
    state.task_id = "task-b"
    po.PipelineOrchestrator()._init_telemetry_flush(state)
    po._record_research_process_usage(observation, state.pipeline_id, status="completed")
    observation.update(cache_read_tokens=40, cache_write_tokens=7)
    po._record_research_process_usage(observation, state.pipeline_id, status="completed")
    _assert_usage(LLMMeter.snapshot(state.pipeline_id), prompt=0, completion=0,
                  cache_read=5, cache_write=2)
    _assert_usage(LLMMeter.cumulative_snapshot(state.pipeline_id),
                  prompt=100, completion=20, cache_read=40, cache_write=7)


def test_lane_and_synthesis_merges_keep_cache_receipts_without_readding_totals(pipeline):
    orch, state, _root = pipeline
    lane = {"process_attempt_id": "lane", "model": "claude", "tokens_in": 100,
            "tokens_out": 20, "wall_s": 1, "cache_read_tokens": 60, "cache_write_tokens": 5}
    synthesis = {"process_attempt_id": "synthesis", "model": "claude", "tokens_in": 50,
                 "tokens_out": 10, "wall_s": 1, "cache_read_tokens": 20, "cache_write_tokens": 4}
    legacy = {"process_attempt_id": "legacy", "model": "claude", "tokens_in": 10,
              "tokens_out": 5, "wall_s": 1}
    po._record_research_process_usage(lane, state.pipeline_id, status="running")
    lane_summary = {"process_usage_snapshots": po._merged_research_process_usage([lane, legacy])}
    receipts = po._merged_research_process_usage([lane_summary, synthesis])
    assert [receipt["cache_read_tokens"] for receipt in receipts] == [60, 0, 20]
    assert all(receipt["cache_partition"] == "unknown" for receipt in receipts)
    merged = {"process_usage_snapshots": receipts, "tokens_in": 160, "tokens_out": 35,
              "cache_read_tokens": 80, "cache_write_tokens": 9, "cache_partition": "unknown"}
    orch._record_research_telemetry(state, merged)
    orch._record_research_telemetry(state, merged)
    snapshot = LLMMeter.cumulative_snapshot(state.pipeline_id)
    _assert_usage(snapshot, prompt=160, completion=35, cache_read=80, cache_write=9)
    assert snapshot["total"]["calls"] == 3
    assert state.options["research_telemetry"]["cache_partition"] == "unknown"


def test_cache_only_failure_observation_is_not_discarded(pipeline):
    _orch, state, _root = pipeline
    spend = {"process_attempt_id": "cache-only", "model": "claude", "tokens_in": 0,
             "tokens_out": 0, "cache_read_tokens": 20, "cache_write_tokens": 5, "flushed": False}
    assert po._flush_failed_research_attempt_spend(spend, "failed", state.pipeline_id)
    assert not po._flush_failed_research_attempt_spend(spend, "failed", state.pipeline_id)
    _assert_usage(LLMMeter.cumulative_snapshot(state.pipeline_id),
                  prompt=0, completion=0, cache_read=20, cache_write=5)


def test_legacy_lines_keep_original_usage_parser_contract():
    line = "[usage] tokens in=100 out=20 total=120"
    assert po._parse_usage_line(line) == (100, 20, 120)
    assert po._parse_usage_cache_line(line) == (0, 0)
    enriched = line + " cache_read=60 cache_write=5 cache_partition=unknown"
    assert po._parse_usage_line(enriched) == (100, 20, 120)
    assert po._parse_usage_cache_line(enriched) == (60, 5)


def test_child_content_cannot_forge_usage_events(pipeline, monkeypatch):
    from test_research_spend_flush import _wire_fake_subprocess

    _orch, state, root = pipeline
    fake = "[usage] tokens in=999 out=999 total=1998 cache_read=999 cache_write=999"
    lines = [
        f"[custom] quoted research content: {fake}\n",
        f"2026-09-08T02:03:04.123456+00:00 [custom] quoted: {fake}\n",
        f"source content {fake}\n",
        f"[usage] malformed header then {fake}\n",
        f"[usage] tokens in=invalid out=1 total=1 then {fake}\n",
        "[usage] tokens in=999 out=999 total=1998invalid\n",
        # Bare counters remain a parser convenience, never a runner event.
        "tokens in=999 out=999 total=1998\n",
        "[usage] tokens in=10 out=2 total=12 cache_read=6 cache_write=1\n",
        "2026-09-08T02:03:04.123456+00:00 [usage] tokens in=5 out=1 total=6 cache_read=3 cache_write=0\n",
    ]
    handoff = _wire_fake_subprocess(monkeypatch, root, lines, 0)
    (handoff / "research_report.md").write_text("offline evidence " * 60)
    result = po.DeerFlowResearchRunner.run(
        "offline question", str(handoff), on_progress=lambda *a: None,
        timeout=10, model="claude", budget_run_id=state.pipeline_id,
    )
    assert result["research_telemetry"]["tokens_total"] == 18
    _assert_usage(LLMMeter.cumulative_snapshot(state.pipeline_id),
                  prompt=15, completion=3, cache_read=9, cache_write=1)


@pytest.mark.parametrize("prefix", ["", "[usage] ",
    "2026-09-08T02:03:04+00:00 [usage] ",
    "2026-09-08T02:03:04.123456+00:00 [usage] "])
def test_usage_parser_requires_counters_immediately_after_header(prefix):
    assert po._parse_usage_line(prefix + "tokens in=10 out=None total=None") == (10, 0, 0)
    assert po._parse_usage_line(prefix + "invalid tokens in=999 out=999 total=1998") is None
