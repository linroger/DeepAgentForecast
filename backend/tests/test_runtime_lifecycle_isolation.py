"""Regression coverage for process-lifecycle isolation.

The Flask app factory owns destructive startup hooks in production.  A test
process must never run those hooks against the shared localhost uploads tree,
and a duplicate backend must not reclaim a simulator owned by the backend that
already holds the configured port.
"""

from __future__ import annotations

import socket

from app import create_app
from app.config import Config
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.simulation_runner import SimulationRunner


def test_app_factory_skips_destructive_lifecycle_in_test_process(monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("DRF_TEST_PROCESS", "1")
    monkeypatch.setattr(
        SimulationRunner,
        "register_cleanup",
        classmethod(lambda cls: calls.append("simulation-register")),
    )
    monkeypatch.setattr(
        SimulationRunner,
        "reconcile_orphans",
        classmethod(lambda cls: calls.append("simulation-reconcile")),
    )
    monkeypatch.setattr(
        PipelineOrchestrator,
        "register_cleanup",
        classmethod(lambda cls: calls.append("pipeline-register")),
    )
    monkeypatch.setattr(
        PipelineOrchestrator,
        "reconcile_orphans",
        classmethod(lambda cls: calls.append("pipeline-reconcile")),
    )

    app = create_app()

    assert app is not None
    assert calls == []


def test_app_factory_keeps_production_lifecycle_when_test_marker_absent(monkeypatch):
    calls: list[str] = []
    monkeypatch.delenv("DRF_TEST_PROCESS", raising=False)
    monkeypatch.setattr(
        SimulationRunner,
        "register_cleanup",
        classmethod(lambda cls: calls.append("simulation-register")),
    )
    monkeypatch.setattr(
        SimulationRunner,
        "reconcile_orphans",
        classmethod(lambda cls: calls.append("simulation-reconcile")),
    )
    monkeypatch.setattr(
        PipelineOrchestrator,
        "register_cleanup",
        classmethod(lambda cls: calls.append("pipeline-register")),
    )
    monkeypatch.setattr(
        PipelineOrchestrator,
        "reconcile_orphans",
        classmethod(lambda cls: calls.append("pipeline-reconcile")),
    )

    app = create_app()

    assert app is not None
    assert calls == [
        "simulation-register",
        "simulation-reconcile",
        "pipeline-reconcile",
        "pipeline-register",
    ]


class _OccupiedPort:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_simulation_orphan_reconcile_skips_when_backend_port_is_owned(
    tmp_path, monkeypatch,
):
    run_root = tmp_path / "simulations"
    run_root.mkdir()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(run_root))
    monkeypatch.setattr(Config, "SIMULATION_RECLAIM_PORT_PROBE", True, raising=False)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: _OccupiedPort(),
    )

    def _unexpected_kill(cls, state):
        raise AssertionError(f"must not reclaim {state.simulation_id}")

    monkeypatch.setattr(
        SimulationRunner,
        "_kill_orphan_simulation",
        classmethod(_unexpected_kill),
    )

    SimulationRunner.reconcile_orphans()

