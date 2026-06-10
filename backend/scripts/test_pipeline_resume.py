#!/usr/bin/env python3
"""Regression checks for pipeline resume/cancel robustness.

Covers:
  * mark_failed() terminal-status parameter (failed vs cancelled)
  * resume() stage reset, resume_count bookkeeping, fresh task id
  * resume() rejection of running / completed pipelines
  * double-resume race: N concurrent resumes -> exactly one _run thread
  * cancel() on an orphan pipeline persists "cancelled" (not "failed")
  * _load_research_handoff() short-report guard + missing-actors tolerance
  * SimulationRunner._rotate_stale_action_logs() rotation semantics

Runs fully offline (no Zep / LLM / DeerFlow needed):
    cd backend && uv run python scripts/test_pipeline_resume.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config  # noqa: E402
from app.services.pipeline_orchestrator import (  # noqa: E402
    PipelineManager,
    PipelineOrchestrator,
    _load_research_handoff,
)
from app.services.simulation_runner import SimulationRunner  # noqa: E402


def _write_state(pipeline_id: str, **overrides) -> dict:
    """Persist a minimal pipeline_state.json and return it."""
    state = {
        "pipeline_id": pipeline_id,
        "prompt": "测试恢复",
        "mode": "full",
        "status": "failed",
        "global_progress": 55,
        "current_stage": "graph",
        "task_id": "task_old",
        "project_id": "proj_x",
        "graph_id": None,
        "simulation_id": None,
        "report_id": None,
        "handoff_dir": PipelineManager.handoff_dir(pipeline_id),
        "research_pid": 99999,
        "error": "boom",
        "created_at": "2026-06-10T00:00:00+00:00",
        "updated_at": "2026-06-10T00:10:00+00:00",
        "options": {"depth": "quick"},
        "stages": {
            "research": {"name": "research", "status": "completed", "progress": 100},
            "ontology": {"name": "ontology", "status": "completed", "progress": 100},
            "graph": {"name": "graph", "status": "failed", "progress": 40, "error": "boom"},
        },
    }
    state.update(overrides)
    PipelineManager.ensure_dirs(pipeline_id)
    with open(PipelineManager.state_path(pipeline_id), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return state


def test_mark_failed_status_param() -> None:
    _write_state("pipe_mark", status="running")
    assert PipelineManager.mark_failed("pipe_mark", "崩了")
    data = PipelineManager.load("pipe_mark")
    assert data["status"] == "failed"
    assert data["stages"]["graph"]["status"] == "failed"

    _write_state("pipe_mark2", status="running")
    assert PipelineManager.mark_failed("pipe_mark2", "已被用户取消", status="cancelled")
    data = PipelineManager.load("pipe_mark2")
    assert data["status"] == "cancelled"
    assert data["stages"]["graph"]["status"] == "cancelled"
    assert data["error"] == "已被用户取消"
    print("✓ mark_failed status param (failed / cancelled)")


def test_resume_resets_failed_stage(block_run) -> None:
    _write_state("pipe_resume1")
    state = PipelineOrchestrator.resume("pipe_resume1")
    try:
        assert state.status == "running"
        assert state.task_id != "task_old"
        assert state.research_pid is None
        assert state.options["resume_count"] == 1
        assert state.stages["graph"].status == "pending"
        assert state.stages["graph"].error is None
        # completed stages stay completed (they will be reused by _run)
        assert state.stages["research"].status == "completed"
        # all bands present even if the original state predates some stages
        for name in ("prepare", "run", "report"):
            assert name in state.stages
        persisted = PipelineManager.load("pipe_resume1")
        assert persisted["status"] == "running"
    finally:
        block_run.set()
    print("✓ resume() resets failed stage, bumps resume_count, persists running")


def test_resume_rejects_terminal_and_live(block_run) -> None:
    _write_state("pipe_done", status="completed")
    try:
        PipelineOrchestrator.resume("pipe_done")
        raise AssertionError("resume of completed pipeline must raise")
    except RuntimeError as e:
        assert "已完成" in str(e)

    _write_state("pipe_running", status="running")
    try:
        PipelineOrchestrator.resume("pipe_running")
        raise AssertionError("resume of running pipeline must raise")
    except RuntimeError as e:
        assert "仍在运行" in str(e)

    try:
        PipelineOrchestrator.resume("pipe_missing_xyz")
        raise AssertionError("resume of unknown pipeline must raise")
    except FileNotFoundError:
        pass
    print("✓ resume() rejects completed / running / missing pipelines")


def test_double_resume_race(block_run) -> None:
    _write_state("pipe_race", status="cancelled",
                 stages={"research": {"name": "research", "status": "completed"},
                         "ontology": {"name": "ontology", "status": "cancelled"}},
                 current_stage="ontology")

    results: list[str] = []
    lock = threading.Lock()

    def attempt():
        try:
            PipelineOrchestrator.resume("pipe_race")
            with lock:
                results.append("ok")
        except (RuntimeError, FileNotFoundError) as e:
            with lock:
                results.append(f"rej:{e}")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    try:
        ok = [r for r in results if r == "ok"]
        assert len(results) == 8, results
        assert len(ok) == 1, f"exactly one resume must win, got {results}"
    finally:
        block_run.set()
    print("✓ double-resume race: 8 concurrent resumes -> exactly 1 _run thread")


def test_cancel_orphan_persists_cancelled() -> None:
    _write_state("pipe_orphan", status="running", research_pid=None)
    # no live thread registered -> orphan path
    result = PipelineOrchestrator.cancel("pipe_orphan")
    assert result == {"ok": True, "status": "cancelled"}
    data = PipelineManager.load("pipe_orphan")
    assert data["status"] == "cancelled"
    assert data["stages"]["graph"]["status"] == "cancelled"
    # and a cancelled orphan is resumable again (frontend canResume covers it)
    assert data["status"] in ("failed", "cancelled")
    print("✓ cancel() on orphan persists status=cancelled (resumable)")


def test_load_research_handoff(tmp: str) -> None:
    handoff = os.path.join(tmp, "handoff_a")
    os.makedirs(handoff, exist_ok=True)
    with open(os.path.join(handoff, "research_report.md"), "w", encoding="utf-8") as f:
        f.write("LLM request failed")  # short error text masquerading as a report
    try:
        _load_research_handoff(handoff)
        raise AssertionError("short report must be rejected")
    except RuntimeError:
        pass

    handoff_b = os.path.join(tmp, "handoff_b")
    os.makedirs(handoff_b, exist_ok=True)
    with open(os.path.join(handoff_b, "research_report.md"), "w", encoding="utf-8") as f:
        f.write("研究正文。" * 200)  # >=400 chars, no actors.json / sources.json
    res = _load_research_handoff(handoff_b)
    assert res["resumed"] is True
    assert res["actors"] is None and res["sources"] is None
    assert len(res["report"]) >= 400
    print("✓ _load_research_handoff: short-error guard + missing-actors tolerance")


def test_rotate_stale_action_logs(tmp: str) -> None:
    sim_dir = os.path.join(tmp, "sim_x")
    for plat in ("twitter", "reddit"):
        os.makedirs(os.path.join(sim_dir, plat), exist_ok=True)
        with open(os.path.join(sim_dir, plat, "actions.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"round": 1, "action_type": "post"}\n')

    SimulationRunner._rotate_stale_action_logs(sim_dir)
    for plat in ("twitter", "reddit"):
        assert not os.path.exists(os.path.join(sim_dir, plat, "actions.jsonl"))
        prev = os.path.join(sim_dir, plat, "actions.prev.jsonl")
        assert os.path.exists(prev)

    # second rotation overwrites the previous backup instead of erroring
    with open(os.path.join(sim_dir, "twitter", "actions.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"round": 2}\n')
    SimulationRunner._rotate_stale_action_logs(sim_dir)
    with open(os.path.join(sim_dir, "twitter", "actions.prev.jsonl"), encoding="utf-8") as f:
        assert '"round": 2' in f.read()
    # missing dir / nothing to rotate is a no-op
    SimulationRunner._rotate_stale_action_logs(os.path.join(tmp, "sim_void"))
    print("✓ _rotate_stale_action_logs: rotate, overwrite-prev, no-op on empty")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mirofish_resume_test_")
    Config.PIPELINE_DATA_DIR = os.path.join(tmp, "pipelines")

    # Block every spawned _run thread on an event so resumes are observable
    # without touching Zep/LLM/DeerFlow; release in each test's finally.
    block_run = threading.Event()

    def fake_run(state):  # signature matches the (state,) thread args
        block_run.wait(timeout=30)

    original_run = PipelineOrchestrator._run
    PipelineOrchestrator._run = staticmethod(fake_run)
    try:
        test_mark_failed_status_param()
        test_resume_resets_failed_stage(block_run)
        test_resume_rejects_terminal_and_live(block_run)
        block_run.set()  # release thread from previous test before the race test
        for t in list(PipelineOrchestrator._threads.values()):
            t.join(timeout=5)
        block_run.clear()
        test_double_resume_race(block_run)
        for t in list(PipelineOrchestrator._threads.values()):
            t.join(timeout=5)
        test_cancel_orphan_persists_cancelled()
        test_load_research_handoff(tmp)
        test_rotate_stale_action_logs(tmp)
    finally:
        block_run.set()
        PipelineOrchestrator._run = original_run

    print("\nAll pipeline resume regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
