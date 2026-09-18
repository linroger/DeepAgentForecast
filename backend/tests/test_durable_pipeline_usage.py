"""Offline pipeline scenarios for durable process snapshot reconciliation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter, get_run_context, set_run_context
from app.utils.usage_ledger import UsageLedgerStorageError


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(po.SimulationRunner, "RUN_STATE_DIR", str(tmp_path / "simulations"))
    state = po.PipelineState(pipeline_id="pipe_durable_integration", prompt="offline fixture", task_id="task-a")
    LLMMeter.reset(state.pipeline_id)
    previous_context = get_run_context()
    orch = po.PipelineOrchestrator()
    yield orch, state, tmp_path
    LLMMeter.reset(state.pipeline_id)
    set_run_context(*previous_context)


def write_sim(root, sim_id="sim-primary", token="child-a", pt=100, ct=20, calls=2, model="m"):
    path = root / "simulations" / sim_id / "sim_llm_telemetry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meter_run_token": token, "provider": "minimax", "model": model,
                   "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt+ct,
                   "calls": calls, "wall_s": 1, "by_source": {}}
    path.write_text(json.dumps(payload))
    return payload


def test_growth_replay_stale_and_resume(pipeline):
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    write_sim(root)
    orch._record_sim_run_telemetry(state, "sim-primary")
    orch._record_sim_run_telemetry(state, "sim-primary")
    write_sim(root, pt=150, ct=30, calls=3, model="new-dominant-model")
    orch._record_sim_run_telemetry(state, "sim-primary")
    write_sim(root, pt=90, ct=10, calls=1)
    orch._record_sim_run_telemetry(state, "sim-primary")
    orch._flush_run_telemetry(state)
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 180
    LLMMeter.reset(state.pipeline_id)
    state.task_id = "task-b"
    resumed = po.PipelineOrchestrator()
    resumed._init_telemetry_flush(state)
    write_sim(root, pt=200, ct=40, calls=4)
    resumed._record_sim_run_telemetry(state, "sim-primary")
    assert LLMMeter.snapshot(state.pipeline_id)["total"]["total_tokens"] == 60
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 240
    resumed._flush_run_telemetry(state)
    resumed._flush_run_telemetry(state)
    saved = json.loads(Path(resumed._tel_path).read_text())
    assert saved["total"]["total_tokens"] == 60
    assert saved["cumulative_total"]["total_tokens"] == 240
    assert saved["usage_accounting"]["usage_complete"] is False


def test_state_projection_failure_cannot_lose_or_duplicate_credit(pipeline, monkeypatch):
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    write_sim(root)
    with monkeypatch.context() as patch:
        patch.setattr(po.PipelineManager, "save", lambda *a: (_ for _ in ()).throw(OSError("cache failure")))
        orch._record_sim_run_telemetry(state, "sim-primary")
    orch._record_sim_run_telemetry(state, "sim-primary")
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 120


def test_legacy_baseline_seed_accepts_only_child_growth(pipeline):
    orch, state, root = pipeline
    payload = write_sim(root)
    state.options["sim_llm_telemetry"] = payload
    state.options["sim_llm_telemetry_recorded"] = {"simulation_id": "sim-primary", "meter_run_token": "child-a"}
    directory = Path(po.PipelineManager._dir(state.pipeline_id))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run_telemetry.json").write_text(json.dumps({"total": {
        "calls": 5, "prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000}}))
    orch._init_telemetry_flush(state)
    write_sim(root, pt=150, ct=30, calls=3)
    orch._record_sim_run_telemetry(state, "sim-primary")
    orch._record_sim_run_telemetry(state, "sim-primary")
    snap = LLMMeter.cumulative_snapshot(state.pipeline_id)
    assert snap["total"]["total_tokens"] == 1060
    assert snap["legacy_baseline_ambiguous"] is True
    assert LLMMeter.snapshot(state.pipeline_id)["total"]["total_tokens"] == 60


def test_missing_identity_is_a_gap_not_a_changing_file_hash(pipeline):
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    write_sim(root, token="")
    orch._record_sim_run_telemetry(state, "sim-primary")
    write_sim(root, token="", pt=200)
    orch._record_sim_run_telemetry(state, "sim-primary")
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 0
    assert state.options["simulation_usage_gap"] == "missing_meter_run_token"


def test_research_lane_stream_and_merged_summary_are_same_operations(pipeline):
    orch, state, _ = pipeline
    orch._init_telemetry_flush(state)
    a = {"process_attempt_id": "lane-a", "model": "claude", "tokens_in": 100, "tokens_out": 20, "wall_s": 1}
    b = {"process_attempt_id": "synthesis", "model": "codex", "tokens_in": 50, "tokens_out": 10, "wall_s": 1}
    po._record_research_process_usage(a, state.pipeline_id, status="running")
    a["tokens_out"] = 40
    observations = po._merged_research_process_usage([a, b])
    merged = {"tokens_in": 150, "tokens_out": 50, "process_usage_snapshots": observations}
    orch._record_research_telemetry(state, merged)
    orch._record_research_telemetry(state, merged)
    snap = LLMMeter.cumulative_snapshot(state.pipeline_id)
    assert snap["total"]["total_tokens"] == 200
    assert snap["total"]["calls"] == 2
    assert snap["usage_by_class"]["unknown"]["total_tokens"] == 200


def test_primary_and_ensemble_children_all_reconcile(pipeline):
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    for sim in ("primary", "seed-1", "seed-2"):
        write_sim(root, sim_id=sim)
        orch._record_sim_run_telemetry(state, sim)
        orch._record_sim_run_telemetry(state, sim)
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 360


@pytest.mark.parametrize("failed", [False, True])
def test_seed_runner_imports_success_and_failure(pipeline, monkeypatch, failed):
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    manager = SimpleNamespace(create_simulation=lambda *a, **kw: SimpleNamespace(simulation_id="seed-1"),
                              prepare_simulation=lambda **kw: None)
    monkeypatch.setattr(po, "SimulationManager", lambda: manager)
    def start_child(**kwargs):
        from app.utils.simulation_usage import authority
        context = kwargs["usage_context"]
        # A newly launched child now records physical usage directly. Its JSON
        # is a diagnostic projection, including when the runner later fails.
        for index in range(2):
            LLMMeter.record_snapshot("llm_api_attempt", f"{context['launch_token']}:{index}",
                                     "minimax", "m", 50, 10, 1,
                                     run_id=state.pipeline_id, stage="run", usage_source="known")
        payload = write_sim(root, sim_id="seed-1", token=context["launch_token"])
        payload.update(simulation_id="seed-1", usage_authority=authority(context))
        (root / "simulations/seed-1/sim_llm_telemetry.json").write_text(json.dumps(payload))

    monkeypatch.setattr(po.SimulationRunner, "start_simulation", start_child)
    monkeypatch.setattr(po.SimulationRunner, "get_run_state", lambda *a: SimpleNamespace(
        current_round=1, runner_status=po.RunnerStatus.FAILED if failed else po.RunnerStatus.COMPLETED))
    monkeypatch.setattr(po.SimulationRunner, "write_run_summary", lambda *a: None)
    monkeypatch.setattr(orch, "_pinned_safety", lambda *a: False)
    monkeypatch.setattr(po, "ReportAgent", lambda **kw: SimpleNamespace(generate_report=lambda **kw: None))
    monkeypatch.setattr(orch, "_read_report_forecast", lambda *a: None)
    args = (state, SimpleNamespace(project_id="p"), "graph", None, {}, "text")
    if failed:
        with pytest.raises(RuntimeError, match="集成种子模拟"):
            orch._run_one_seed(*args, seed=1, max_rounds=1)
    else:
        assert orch._run_one_seed(*args, seed=1, max_rounds=1)[0] == "seed-1"
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 120


@pytest.mark.parametrize("cache", ["corrupt", "durable", "foreign"])
def test_missing_ledger_cannot_reset_historical_spend_to_zero(pipeline, cache):
    orch, state, _ = pipeline
    directory = Path(po.PipelineManager._dir(state.pipeline_id))
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": "different-pipeline", "total": {"prompt_tokens": 100}} if cache == "foreign" else {"durable": True}
    (directory / "run_telemetry.json").write_text("{" if cache == "corrupt" else json.dumps(payload))
    with pytest.raises(UsageLedgerStorageError):
        orch._init_telemetry_flush(state)
    assert LLMMeter.cumulative_snapshot(state.pipeline_id) is None
    before = (directory / "run_telemetry.json").read_bytes()
    orch._flush_run_telemetry(state, final=True)
    assert (directory / "run_telemetry.json").read_bytes() == before
    with pytest.raises(UsageLedgerStorageError):
        po.PipelineOrchestrator()._init_telemetry_flush(state)


@pytest.mark.parametrize("failed", [False, True])
def test_actual_research_stream_and_final_import_reconcile(pipeline, monkeypatch, failed):
    from test_research_spend_flush import _wire_fake_subprocess, USAGE_LINES
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    handoff = _wire_fake_subprocess(monkeypatch, root, USAGE_LINES, 3 if failed else 0)
    (handoff / "research_report.md").write_text("offline evidence " * 60)
    kwargs = {"on_progress": lambda *a: None, "timeout": 10, "model": "claude", "budget_run_id": state.pipeline_id}
    if failed:
        with pytest.raises(RuntimeError, match="研究子进程失败"):
            po.DeerFlowResearchRunner.run("offline question", str(handoff), **kwargs)
    else:
        result = po.DeerFlowResearchRunner.run("offline question", str(handoff), **kwargs)
        assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 28000
        orch._record_research_telemetry(state, result["research_telemetry"])
        orch._record_research_telemetry(state, result["research_telemetry"])
    snap = LLMMeter.cumulative_snapshot(state.pipeline_id)
    assert snap["total"]["calls"] == 1
    assert snap["total"]["total_tokens"] == 28000


def test_research_storage_failure_stops_before_child_launch(pipeline, monkeypatch):
    from test_research_spend_flush import _wire_fake_subprocess, USAGE_LINES
    orch, state, root = pipeline
    orch._init_telemetry_flush(state)
    handoff = _wire_fake_subprocess(monkeypatch, root, USAGE_LINES, 0)
    (root / "pipelines" / "usage_ledger.sqlite3").unlink()
    launches = []
    monkeypatch.setattr(po.subprocess, "Popen", lambda *a, **kw: launches.append(a))
    with pytest.raises(UsageLedgerStorageError):
        po.DeerFlowResearchRunner.run("offline", str(handoff), on_progress=lambda *a: None,
                                     timeout=10, budget_run_id=state.pipeline_id)
    assert launches == []


def test_legacy_child_seed_survives_crash_before_first_import(pipeline):
    orch, state, root = pipeline
    summary = write_sim(root)
    state.options.update(sim_llm_telemetry=summary, sim_llm_telemetry_recorded={
        "simulation_id": "sim-primary", "meter_run_token": "child-a"})
    directory = Path(po.PipelineManager._dir(state.pipeline_id))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run_telemetry.json").write_text(json.dumps({"total": {
        "calls": 2, "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}))
    orch._init_telemetry_flush(state)
    orch._flush_run_telemetry(state)
    LLMMeter.reset(state.pipeline_id)
    state.task_id = "task-b"
    resumed = po.PipelineOrchestrator()
    resumed._init_telemetry_flush(state)
    resumed._record_sim_run_telemetry(state, "sim-primary")
    assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 120
    assert LLMMeter.snapshot(state.pipeline_id)["total"]["total_tokens"] == 0


def test_pipeline_init_failure_preserves_both_prior_artifacts(pipeline, monkeypatch):
    orch, state, root = pipeline
    directory = Path(po.PipelineManager._dir(state.pipeline_id))
    directory.mkdir(parents=True, exist_ok=True)
    raw = b"{invalid prior usage"
    (directory / "run_telemetry.json").write_bytes(raw)
    (directory / "telemetry.json").write_text('{"historical": true}')
    monkeypatch.setattr(po.PipelineOrchestrator, "_start_heartbeat", lambda *a: None)
    monkeypatch.setattr(po.PipelineOrchestrator, "_write_run_manifest", lambda *a: None)
    monkeypatch.setattr(po, "_register_outage_breaker", lambda *a: None)
    po.PipelineOrchestrator._run(state)
    assert state.status == "failed"
    assert (directory / "run_telemetry.json").read_bytes() == raw
    assert json.loads((directory / "telemetry.json").read_text()) == {"historical": True}
    assert not LLMMeter.is_durable_run(state.pipeline_id)
    with pytest.raises(UsageLedgerStorageError):
        po.PipelineOrchestrator()._init_telemetry_flush(state)


def test_concurrent_fresh_pipeline_init_waits_for_uncommitted_schema(pipeline, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.utils.usage_ledger import UsageLedger
    _, _, root = pipeline
    entered = threading.Event()
    release = threading.Event()
    second_at_initializer = threading.Event()
    original = UsageLedger._schema
    original_attach = LLMMeter.attach_durable_run

    def observe_attach(cls, run_id, *args, **kwargs):
        if run_id == "pipe_cold_1":
            second_at_initializer.set()
        return original_attach(run_id, *args, **kwargs)

    def delayed_schema(conn):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        original(conn)

    monkeypatch.setattr(UsageLedger, "_schema", staticmethod(delayed_schema))
    monkeypatch.setattr(LLMMeter, "attach_durable_run", classmethod(observe_attach))
    states = [po.PipelineState(pipeline_id=f"pipe_cold_{i}", prompt="offline") for i in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(po.PipelineOrchestrator()._init_telemetry_flush, states[0])
            assert entered.wait(5)
            second = pool.submit(po.PipelineOrchestrator()._init_telemetry_flush, states[1])
            # Both callers reach the transactional initializer while the first
            # schema is uncommitted. The former speculative read failed earlier.
            assert second_at_initializer.wait(5)
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
        for state in states:
            assert LLMMeter.cumulative_snapshot(state.pipeline_id)["total"]["total_tokens"] == 0
    finally:
        release.set()
        for state in states:
            LLMMeter.reset(state.pipeline_id)
