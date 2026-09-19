"""Offline acceptance of the real parent routing and child launch boundaries.

The state machine and evidence-manifest producer run unchanged. Scripted research
children stop at synthesis admission: these tests establish routing, not final
publication quality or provider behavior. Deployment tests copy into tmp_path.
"""

from __future__ import annotations

from contextvars import Context
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest

from app.services import pipeline_orchestrator as po
from test_deerflow_bridge_sync_guard import _write
from test_loop010_global_synthesis import _write_shared_actor_artifacts
from test_research_spend_flush import FakeProc


ROOT = Path(__file__).resolve().parents[2]
POLICY = "mechanical-with-advisory/v1"
STOP = "offline acceptance reached synthesis admission"
MODULES = (
    "research_workspace.py", "research_context.py", "research_archive.py",
    "agentic_research.py", "agentic_bridge.py", "research_quality.py",
    "research_synthesis.py", "research_admission.py", "research_invocation.py",
)
BACKEND_MODULES = (
    "research_workspace.py", "research_context.py", "research_archive.py",
    "research_admission.py",
)


@pytest.fixture
def parent(monkeypatch, tmp_run_dir):
    """Reuse shared temporary-run/log/network fixtures; retain real state I/O."""
    root = Path(tmp_run_dir)
    monkeypatch.setattr(po.Config, "PIPELINE_DATA_DIR", str(root / "pipelines"))
    monkeypatch.setattr(po.Config, "UPLOAD_FOLDER", str(root / "uploads"))
    monkeypatch.setattr(po.Config, "DEERFLOW_MODEL", "offline-routing")
    monkeypatch.setattr(po.Config, "DEERFLOW_DUAL_TRACK", True)
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_MAX_EPOCHS", 3)
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _path: None)
    monkeypatch.setattr(po, "_register_outage_breaker", lambda _pid: None)
    monkeypatch.setattr(po, "TaskManager", lambda: SimpleNamespace())
    # Do not spawn housekeeping threads or inspect a real vendor deployment.
    monkeypatch.setattr(po.PipelineOrchestrator, "_start_heartbeat", lambda *_: None)
    monkeypatch.setattr(po.PipelineOrchestrator, "_write_run_manifest", lambda *_: None)
    return root


def _state(engine):
    state = po.PipelineOrchestrator._new_launch_state(
        "Forecast the documented outcome", mode="research_only",
        depth="deep", language="English", model="offline-routing",
    )
    if engine is None:
        state.options.pop("research_engine")
    else:
        state.options["research_engine"] = engine
    po.PipelineManager.ensure_dirs(state.pipeline_id)
    po.PipelineManager.save(state)
    return po.PipelineState.from_dict(po.PipelineManager.load(state.pipeline_id))


def _scripted_research(monkeypatch, *, fail_first_synthesis=False, source_body=None):
    calls = []

    def run(prompt, handoff_dir, **kwargs):
        folder = Path(handoff_dir)
        folder.mkdir(parents=True, exist_ok=True)
        calls.append({"prompt": prompt, "directory": folder, **kwargs})
        if not kwargs.get("evidence_only"):
            synthesis_calls = [c for c in calls if c.get("synthesis_manifest_path")]
            if fail_first_synthesis and len(synthesis_calls) == 1:
                raise RuntimeError("offline synthesis interruption")
            raise po.PipelineCancelled(STOP)
        evidence = "# Internal Evidence Lane Pack\n\n" + "Retained sourced evidence. " * 40
        sources = [{"url": f"https://example.gov/{folder.name}", "title": "Evidence", "tier": "S1"}]
        if source_body is not None:
            sources[0].update({
                "content": source_body,
                "content_sha256": hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
                "source_origin": "fetched",
                "fetch_receipt_id": "offline-source-receipt",
            })
        (folder / "evidence_pack.md").write_text(evidence, encoding="utf-8")
        (folder / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({
            "research_engine": "agentic-phases/v1" if kwargs["research_engine"] == "agentic" else "hybrid",
            "quality_policy": POLICY if kwargs["research_engine"] == "agentic" else "legacy",
        }), encoding="utf-8")
        dossier = _write_shared_actor_artifacts(folder) if folder.name == "track_1" else ""
        return {
            "report": evidence, "report_path": str(folder / "evidence_pack.md"),
            "actor_dossier": dossier, "actors": None, "sources": sources,
            "timeline": None, "exit_code": 0,
            "research_telemetry": {"wall_s": 1, "tokens_total": 0},
        }

    monkeypatch.setattr(po.DeerFlowResearchRunner, "run", staticmethod(run))
    return calls


def _run_to_boundary(state):
    # Production runs this on a fresh thread. Match its context isolation while
    # keeping the state machine synchronous and fully observable in this test.
    Context().run(po.PipelineOrchestrator._run, state)
    assert state.status == "cancelled", state.error
    assert state.error == STOP
    assert state.current_stage == po.STAGE_RESEARCH


@pytest.mark.parametrize(("configured", "engine"), [
    ("agentic", "agentic"), ("hybrid", "hybrid"), ("linear", "linear"),
    (" AGENTIC ", "agentic"),
])
def test_new_pipeline_pins_config_engine_before_environment_drift(parent, monkeypatch, configured, engine):
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", configured)
    monkeypatch.setenv("RESEARCH_ENGINE", "linear" if engine != "linear" else "agentic")
    state = po.PipelineOrchestrator._new_launch_state("Question", mode="research_only")
    assert state.options["research_engine"] == engine
    po.PipelineManager.ensure_dirs(state.pipeline_id)
    po.PipelineManager.save(state)

    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", "hybrid" if engine != "hybrid" else "agentic")
    restored = po.PipelineState.from_dict(po.PipelineManager.load(state.pipeline_id))
    assert restored.options["research_engine"] == engine


@pytest.mark.parametrize("engine", ["", "unknown-engine"])
def test_invalid_config_engine_rejected_before_allocating_pipeline_state(parent, monkeypatch, engine):
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", engine)

    def allocated_before_validation(*args, **kwargs):
        pytest.fail("PipelineState allocated before Config.RESEARCH_ENGINE validation")

    monkeypatch.setattr(po, "PipelineState", allocated_before_validation)
    with pytest.raises(ValueError, match="(?i)engine"):
        po.PipelineOrchestrator._new_launch_state("Question", mode="research_only")
    assert not (parent / "pipelines").exists()


@pytest.mark.parametrize("configured_tracks", [1, 3])
@pytest.mark.parametrize("global_synthesis", [False, True])
def test_actual_parent_agentic_selects_one_evidence_lane_and_global_synthesis(
    parent, monkeypatch, configured_tracks, global_synthesis,
):
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", "agentic")
    # Admission uses Config; later environment/default changes cannot reroute it.
    state = po.PipelineOrchestrator._new_launch_state(
        "Forecast the documented outcome", mode="research_only", depth="deep",
        language="English", model="offline-routing",
    )
    po.PipelineManager.ensure_dirs(state.pipeline_id)
    po.PipelineManager.save(state)
    state = po.PipelineState.from_dict(po.PipelineManager.load(state.pipeline_id))
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", "hybrid")
    monkeypatch.setenv("RESEARCH_ENGINE", "linear")
    monkeypatch.setattr(po.Config, "RESEARCH_PARALLEL_TRACKS", configured_tracks)
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SYNTHESIS", global_synthesis)
    calls = _scripted_research(monkeypatch)

    _run_to_boundary(state)

    assert len(calls) == 2
    evidence, synthesis = calls
    assert evidence["evidence_only"] is True
    assert evidence["directory"].name == "track_1"
    assert evidence["budget_lane_id"] == "outer-track-1"
    assert evidence["dual_track"] is True
    assert not synthesis.get("evidence_only", False)
    assert synthesis["budget_lane_id"] == "global-synthesis"
    assert synthesis["dual_track"] is False
    assert synthesis["subagents"] is False
    for call in calls:
        assert call["research_engine"] == "agentic"
        assert call["prompt"] == state.prompt
        assert call["agentic_cache_root"] == str(Path(po.PipelineManager._dir(state.pipeline_id)) / "agentic-cache")
        assert call["budget_run_id"] == state.pipeline_id
    assert evidence["budget_db_path"] == synthesis["budget_db_path"]
    assert evidence["budget_epoch"] == synthesis["budget_epoch"]
    manifest = json.loads(Path(synthesis["synthesis_manifest_path"]).read_text())
    assert manifest["pipeline_id"] == state.pipeline_id
    assert len(manifest["lanes"]) == 1
    assert manifest["lanes"][0]["path"] == "track_1/evidence_pack.md"
    assert manifest["actor_dossier"]["lane_index"] == 1
    assert manifest["actor_dossier"]["judge_policy"] == POLICY


def test_parent_merged_manifest_preserves_full_source_body_and_receipt(parent, monkeypatch):
    state = _state("agentic")
    body = ("Archived source paragraph with Unicode 中文.\r\n" * 2500
            + "TAIL-EVIDENCE-AFTER-PROMPT-LIMIT\r\n")
    calls = _scripted_research(monkeypatch, source_body=body)

    _run_to_boundary(state)

    assert len(calls) == 2
    lane_sources = json.loads((calls[0]["directory"] / "sources.json").read_text())
    manifest_path = Path(calls[1]["synthesis_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["sources"] == lane_sources
    assert manifest["sources"][0]["content"] == body
    assert manifest["sources"][0]["content_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert manifest["sources"][0]["fetch_receipt_id"] == "offline-source-receipt"
    source_bytes = json.dumps(manifest["sources"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert manifest["sources_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    lane_bytes = (calls[0]["directory"] / "sources.json").read_bytes()
    assert manifest["lanes"][0]["sources_sha256"] == hashlib.sha256(lane_bytes).hexdigest()
    assert manifest["lanes"][0]["sources_bytes"] == len(lane_bytes)


@pytest.mark.parametrize("engine", [None, "hybrid"], ids=["legacy-missing", "pinned-hybrid"])
@pytest.mark.parametrize("configured_tracks", [1, 3])
def test_actual_parent_legacy_remains_hybrid_despite_agentic_default(
    parent, monkeypatch, engine, configured_tracks,
):
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.setattr(po.Config, "RESEARCH_PARALLEL_TRACKS", configured_tracks)
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SYNTHESIS", True)
    state = _state(engine)
    calls = _scripted_research(monkeypatch)

    _run_to_boundary(state)

    assert all(call["research_engine"] == "hybrid" for call in calls)
    evidence = [call for call in calls if call.get("evidence_only")]
    if configured_tracks == 1:
        assert len(calls) == 1
        assert evidence == []
        assert not calls[0].get("synthesis_manifest_path")
    else:
        assert len(evidence) == 3
        assert len(calls) == 4
        manifest = json.loads(Path(calls[-1]["synthesis_manifest_path"]).read_text())
        assert len(manifest["lanes"]) == 3
        assert "judge_policy" not in manifest["actor_dossier"]
    restored = po.PipelineState.from_dict(po.PipelineManager.load(state.pipeline_id))
    assert restored.options.get("research_engine") == engine


def test_parent_retry_and_synthesis_recovery_keep_pipeline_cache_root(parent, monkeypatch):
    monkeypatch.setattr(po.Config, "RESEARCH_PARALLEL_TRACKS", 3)
    state = _state("agentic")
    calls = _scripted_research(monkeypatch, fail_first_synthesis=True)
    _run_to_boundary(state)
    assert len(calls) == 3
    assert sum(bool(c.get("evidence_only")) for c in calls) == 1
    manifest_path = calls[-1]["synthesis_manifest_path"]
    manifest_before = Path(manifest_path).read_bytes()

    # The real parent detects the retained manifest and performs synthesis only.
    restored = po.PipelineState.from_dict(po.PipelineManager.load(state.pipeline_id))
    _run_to_boundary(restored)
    assert len(calls) == 4
    assert calls[-1]["budget_lane_id"] == "global-synthesis-recovery"
    assert calls[-1]["synthesis_manifest_path"] == manifest_path
    assert Path(manifest_path).read_bytes() == manifest_before
    assert all(c["research_engine"] == "agentic" for c in calls)
    assert len({c["agentic_cache_root"] for c in calls}) == 1
    assert len({c["directory"] for c in calls[1:]}) == 3
    assert (Path(calls[0]["agentic_cache_root"]).parent == Path(po.PipelineManager._dir(state.pipeline_id)))


@pytest.fixture
def child_launches(parent, monkeypatch):
    deployed = parent / "deer-flow"
    deployed.mkdir()
    (deployed / "deerflow_research.py").write_text("# offline child\n")
    monkeypatch.setattr(po.Config, "DEERFLOW_DIR", str(deployed))
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SUBAGENT_CAP", 19)
    monkeypatch.setattr(po.Config, "RESEARCH_MODEL_LEASE_DB", "", raising=False)
    launches = []

    def popen(command, **kwargs):
        launches.append({"command": command, "env": kwargs["env"]})
        out = Path(command[command.index("--out-dir") + 1])
        name = "evidence_pack.md" if "--evidence-only" in command else "research_report.md"
        (out / name).write_text("# Offline child\n\n" + "Sourced evidence. " * 40)
        return FakeProc([], 0)

    monkeypatch.setattr(po.subprocess, "Popen", popen)
    return launches


@pytest.mark.parametrize("budget_enabled", [False, True])
def test_actual_runner_forces_five_slots_shared_lease_and_stable_cache_namespaces(
    parent, monkeypatch, child_launches, budget_enabled,
):
    monkeypatch.setattr(po.Config, "RESEARCH_BUDGET_ENABLED", budget_enabled)
    monkeypatch.setenv("RESEARCH_ENGINE", "hybrid")
    monkeypatch.setenv("RESEARCH_AGENTIC_CACHE_DIR", str(parent / "ambient-cache"))
    for name in ("RESEARCH_GLOBAL_SUBAGENT_CAP", "RESEARCH_MODEL_CONCURRENCY_GLOBAL",
                 "RESEARCH_SYNTHESIS_WORKERS", "RESEARCH_AGENTIC_WORKERS"):
        monkeypatch.setenv(name, "37")
    manifest = parent / "evidence_synthesis_manifest.json"
    manifest.write_text("{}")  # FakeProc never parses child input.
    cache_a = parent / "pipelines" / "pipe-a" / "agentic-cache"
    cache_b = parent / "pipelines" / "pipe-b" / "agentic-cache"
    scenarios = [
        ("pipe-a", "track_1", True, cache_a),
        ("pipe-a", ".global-synthesis-attempt-1", False, cache_a),
        ("pipe-a", ".global-synthesis-attempt-2", False, cache_a),
        ("pipe-a", ".synthesis-recovery-attempt-1", False, cache_a),
        ("pipe-b", "track_1", True, cache_b),
    ]
    for pipeline, directory, evidence, cache in scenarios:
        po.DeerFlowResearchRunner.run(
            "Offline question", str(parent / pipeline / directory),
            on_progress=lambda *_: None, timeout=10, research_engine="agentic",
            agentic_cache_root=str(cache), evidence_only=evidence, dual_track=False,
            synthesis_manifest_path=None if evidence else str(manifest),
            budget_run_id=pipeline, model_concurrency_global=23,
        )
    assert len(child_launches) == len(scenarios)
    for launch, (pipeline, _, evidence, cache) in zip(child_launches, scenarios, strict=True):
        env = launch["env"]
        assert env["RESEARCH_ENGINE"] == "agentic"
        for name in ("RESEARCH_GLOBAL_SUBAGENT_CAP", "RESEARCH_MODEL_CONCURRENCY_GLOBAL",
                     "RESEARCH_SYNTHESIS_WORKERS", "RESEARCH_AGENTIC_WORKERS"):
            assert env[name] == "5", name
        assert env["RESEARCH_AGENTIC_CACHE_DIR"] == str(cache / ("evidence" if evidence else "synthesis"))
        if budget_enabled:
            assert env["RESEARCH_BUDGET_RUN_ID"] == pipeline
        assert env["RESEARCH_MODEL_LEASE_DB"] == str(parent / "uploads" / "research_model_leases.sqlite3")
    namespaces = [launch["env"]["RESEARCH_AGENTIC_CACHE_DIR"] for launch in child_launches]
    assert namespaces[1] == namespaces[2] == namespaces[3]
    assert namespaces[0] != namespaces[1]
    assert namespaces[0] != namespaces[4]


def test_actual_runner_respects_explicit_legacy_engine(parent, monkeypatch, child_launches):
    monkeypatch.setattr(po.Config, "RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    po.DeerFlowResearchRunner.run(
        "Legacy question", str(parent / "legacy"), on_progress=lambda *_: None,
        timeout=10, research_engine="hybrid", dual_track=False, model_concurrency_global=4,
    )
    env = child_launches[0]["env"]
    assert env["RESEARCH_ENGINE"] == "hybrid"
    assert env["RESEARCH_MODEL_CONCURRENCY_GLOBAL"] == "4"
    assert env["RESEARCH_GLOBAL_SUBAGENT_CAP"] == "19"


@pytest.mark.parametrize("stale", [False, True], ids=["missing", "stale"])
def test_setup_and_runtime_sync_deploy_same_agentic_modules_including_admission(
    tmp_path, monkeypatch, stale,
):
    bridge = tmp_path / "deerflow_bridge"
    runtime = tmp_path / "runtime-deer-flow"
    setup = tmp_path / "setup-deer-flow"
    _write(bridge / "deerflow_research.py", "# source bridge\n")
    _write(runtime / "deerflow_research.py", "# deployed bridge\n")
    for deployed in (runtime, setup):
        (deployed / "backend").mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        _write(bridge / name, (ROOT / "deerflow_bridge" / name).read_text())
        if stale:
            for deployed in (runtime, setup):
                _write(deployed / name, "# stale copy\n")
                if name in BACKEND_MODULES:
                    _write(deployed / "backend" / name, "# stale native copy\n")
    monkeypatch.setattr(po, "__file__", str(tmp_path / "backend" / "app" / "services" / "pipeline_orchestrator.py"))
    po._sync_deerflow_bridge_if_stale(str(runtime))

    # Execute only setup's copy loop, never setup's dependency/service commands.
    setup_source = (ROOT / "setup.sh").read_text()
    copy_loop = re.search(r"(?ms)^  for _tool_mod in .*?^  done\s*$", setup_source)
    assert copy_loop, "setup must retain an identifiable bridge-module deployment loop"
    result = subprocess.run(
        ["bash", "-e", "-c", "ok() { :; }\n" + copy_loop.group(0)],
        env={**os.environ, "BRIDGE_DIR": str(bridge), "DEERFLOW_DIR": str(setup)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    destinations = [Path(name) for name in MODULES]
    destinations.extend(Path("backend") / name for name in BACKEND_MODULES)
    for relative in destinations:
        expected = (bridge / relative.name).read_bytes()
        assert (runtime / relative).is_file(), f"runtime missing {relative}"
        assert (setup / relative).is_file(), f"setup missing {relative}"
        assert (runtime / relative).read_bytes() == expected
        assert (setup / relative).read_bytes() == expected
    mtimes = {relative: (runtime / relative).stat().st_mtime_ns for relative in destinations}
    po._sync_deerflow_bridge_if_stale(str(runtime))
    assert mtimes == {relative: (runtime / relative).stat().st_mtime_ns for relative in destinations}
