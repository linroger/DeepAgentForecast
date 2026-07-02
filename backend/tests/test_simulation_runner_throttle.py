"""SIM-13: throttle run_state.json disk writes while keeping the in-memory state fresh."""

import app.services.simulation_runner as sr
from app.services.simulation_runner import RunnerStatus, SimulationRunState, SimulationRunner


def _patch_atomic(monkeypatch):
    """Count disk writes without touching the filesystem layout."""
    calls = {"n": 0}

    def _fake_write(path, data):
        calls["n"] += 1

    import app.utils.atomic as atomic
    monkeypatch.setattr(atomic, "write_json_atomic", _fake_write)
    monkeypatch.setattr(sr.os, "makedirs", lambda *a, **k: None)
    return calls


def test_interval_zero_writes_every_time(monkeypatch):
    """Default interval 0 → byte-identical with the old always-write behavior."""
    calls = _patch_atomic(monkeypatch)
    monkeypatch.setattr(sr.Config, "SIM_RUNSTATE_SAVE_INTERVAL", 0, raising=False)
    SimulationRunner._run_state_last_save.pop("sim-zero", None)
    st = SimulationRunState(simulation_id="sim-zero", runner_status=RunnerStatus.RUNNING)
    for _ in range(3):
        SimulationRunner._save_run_state(st)
    assert calls["n"] == 3
    assert SimulationRunner._run_states["sim-zero"] is st   # in-memory always fresh


def test_interval_throttles_but_memory_stays_fresh(monkeypatch):
    calls = _patch_atomic(monkeypatch)
    monkeypatch.setattr(sr.Config, "SIM_RUNSTATE_SAVE_INTERVAL", 100, raising=False)
    SimulationRunner._run_state_last_save.pop("sim-throttle", None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(sr.time, "monotonic", lambda: clock["t"])
    st = SimulationRunState(simulation_id="sim-throttle", runner_status=RunnerStatus.RUNNING)

    SimulationRunner._save_run_state(st)          # first write always lands
    st.current_round = 5
    SimulationRunner._save_run_state(st)          # within interval → throttled
    assert calls["n"] == 1
    assert SimulationRunner._run_states["sim-throttle"].current_round == 5  # memory fresh

    clock["t"] += 150.0                           # past the interval → writes again
    SimulationRunner._save_run_state(st)
    assert calls["n"] == 2


def test_terminal_status_always_forces_write(monkeypatch):
    calls = _patch_atomic(monkeypatch)
    monkeypatch.setattr(sr.Config, "SIM_RUNSTATE_SAVE_INTERVAL", 100, raising=False)
    SimulationRunner._run_state_last_save.pop("sim-term", None)
    monkeypatch.setattr(sr.time, "monotonic", lambda: 5000.0)
    st = SimulationRunState(simulation_id="sim-term", runner_status=RunnerStatus.RUNNING)
    SimulationRunner._save_run_state(st)          # first write
    st.runner_status = RunnerStatus.COMPLETED
    SimulationRunner._save_run_state(st)          # terminal → forced despite interval
    assert calls["n"] == 2
