"""The reuse diagnostic reads temporary artifacts and never dispatches work."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from flask import Flask

from app.api import research as api
from app.config import Config
from app.services.pipeline_orchestrator import PipelineManager, PipelineOrchestrator


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    root = tmp_path / "pipelines"
    root.mkdir()
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(root))
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path / "simulations"))
    from app.services.report_agent import ReportManager
    from app.services.simulation_runner import SimulationRunner
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path / "simulations"))
    pid = "pipe_reuse_inspection"
    home = root / pid
    handoff = home / "handoff"
    handoff.mkdir(parents=True)
    state = {
        "pipeline_id": pid, "schema_version": 2, "prompt": "private fixture question",
        "status": "completed", "mode": "full", "handoff_dir": str(handoff),
        "stages": {stage: {"name": stage, "status": "completed"}
                   for stage in ("research", "ontology", "graph", "prepare", "run", "report")},
    }
    (home / "pipeline_state.json").write_text(json.dumps(state))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("inspection must not mutate state or execute work")

    for name in ("save", "write_artifact_manifest", "ensure_dirs"):
        monkeypatch.setattr(PipelineManager, name, forbidden)
    for name in ("start", "resume", "_run", "fork"):
        monkeypatch.setattr(PipelineOrchestrator, name, forbidden)
    monkeypatch.setattr(api, "preflight_pipeline", forbidden)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api.research_bp, url_prefix="/api/research")
    return app.test_client(), root, home, handoff, state


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _decisions(response):
    assert response.status_code == 200, response.get_json()
    body = response.get_json()["data"]
    assert body["advisory"] is True
    assert body["execution_authorized"] is False
    return body, {row["stage"]: row["decision"] for row in body["stages"]}


def test_existing_completed_run_is_not_invented_as_verified_reuse(diagnostic):
    client, root, home, _, _ = diagnostic
    before = _snapshot(root)
    body, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan"))
    assert set(decisions.values()) == {"legacy_unverified"}
    assert "private fixture question" not in json.dumps(body)
    assert _snapshot(root) == before


def test_hypothetical_graph_change_explains_downstream_closure(diagnostic):
    client, root, home, _, _ = diagnostic
    before = _snapshot(root)
    body, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan?changed=graph"))
    assert body["affected_stages"] == ["graph", "prepare", "run", "report"]
    assert all(decisions[s] == "rebuild" for s in body["affected_stages"])
    assert decisions["research"] == decisions["ontology"] == "legacy_unverified"
    assert _snapshot(root) == before


@pytest.mark.parametrize("condition", ["healthy", "tampered", "missing", "wrong_path", "malformed"])
def test_registered_ontology_integrity_is_observed_without_reregistering(diagnostic, condition):
    client, root, home, handoff, _ = diagnostic
    path = handoff / "ontology.json"
    data = b'{"entity_types":[],"edge_types":[]}'
    path.write_bytes(data)
    manifest = {"ontology": {"path": str(path), "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest(), "stage": "ontology"}}
    if condition == "tampered":
        path.write_bytes(data + b" ")
    elif condition == "missing":
        path.unlink()
    elif condition == "wrong_path":
        manifest["ontology"]["path"] = str(root / "another_owner" / "ontology.json")
    manifest_path = handoff / "manifest.json"
    manifest_path.write_text("{" if condition == "malformed" else json.dumps(manifest))
    before = _snapshot(root)
    _, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan"))
    expected = "legacy_unverified" if condition == "healthy" else "rebuild" if condition == "missing" else "reject"
    assert decisions["ontology"] == expected
    if condition not in {"healthy"}:
        assert decisions["prepare"] == decisions["run"] == decisions["report"] == expected
    assert _snapshot(root) == before


def test_external_symlink_is_rejected_without_reading_its_target(diagnostic, monkeypatch, tmp_path):
    client, root, home, handoff, _ = diagnostic
    external = tmp_path / "outside-root.json"
    external.write_text("do not read this file")
    artifact = handoff / "ontology.json"
    artifact.symlink_to(external)
    (handoff / "manifest.json").write_text(json.dumps({"ontology": {"path": str(artifact)}}))
    original_open = Path.open

    def guard(path, *args, **kwargs):
        if path.resolve() == external.resolve():
            pytest.fail("outside-root artifact was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    _, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan"))
    assert decisions["ontology"] == "reject"


@pytest.mark.parametrize("query", ["unknown", "graph,,run", "graph%2F..", "graph&changed=unknown"])
def test_invalid_change_request_is_rejected(diagnostic, query):
    client, _, home, _, _ = diagnostic
    assert client.get(f"/api/research/{home.name}/reuse-plan?changed={query}").status_code == 400


def test_missing_and_future_schema_have_existing_api_semantics(diagnostic):
    client, _, home, _, state = diagnostic
    assert client.get("/api/research/pipe_missing/reuse-plan").status_code == 404
    state["schema_version"] = 999
    (home / "pipeline_state.json").write_text(json.dumps(state))
    assert client.get(f"/api/research/{home.name}/reuse-plan").status_code == 409


def test_report_only_change_preserves_upstream_work(diagnostic):
    client, root, home, _, _ = diagnostic
    before = _snapshot(root)
    body, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan?changed=report"))
    assert body["affected_stages"] == ["report"]
    assert decisions["run"] == "legacy_unverified"
    assert _snapshot(root) == before


def test_fork_reads_shared_owner_bytes_and_keeps_base_unchanged(diagnostic):
    client, root, home, handoff, state = diagnostic
    base_handoff = root / "pipe_base_owner" / "handoff"
    base_handoff.mkdir(parents=True)
    path = base_handoff / "ontology.json"
    data = b'{"entity_types":[],"edge_types":[]}'
    path.write_bytes(data)
    state["handoff_dir"] = str(base_handoff)
    state["options"] = {"base_pipeline_id": "pipe_base_owner"}
    (home / "pipeline_state.json").write_text(json.dumps(state))
    (handoff / "manifest.json").write_text(json.dumps({"ontology": {
        "path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }}))
    before = _snapshot(root)
    body, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan?changed=prepare"))
    assert body["affected_stages"] == ["prepare", "run", "report"]
    assert decisions["ontology"] == "legacy_unverified"
    ontology = next(row for row in body["stages"] if row["stage"] == "ontology")
    assert ontology["artifact_checks"][0]["status"] == "verified"
    assert _snapshot(root) == before


@pytest.mark.parametrize("mode,status", [("research_only", "completed"), ("full", "running")])
def test_unselected_and_active_stage_artifacts_are_not_opened(diagnostic, monkeypatch, mode, status):
    client, _, home, handoff, state = diagnostic
    state["mode"] = mode
    state["stages"]["ontology"]["status"] = status
    (home / "pipeline_state.json").write_text(json.dumps(state))
    path = handoff / "ontology.json"
    path.write_text("private active artifact")
    (handoff / "manifest.json").write_text(json.dumps({"ontology": {"path": str(path)}}))
    original_open = Path.open

    def guard(target, *args, **kwargs):
        if target == path:
            pytest.fail("inactive-scope or active artifact must not be opened")
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    _, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan"))
    if mode == "research_only":
        assert decisions == {"research": "legacy_unverified"}
    else:
        assert decisions["ontology"] == decisions["report"] == "reject"


@pytest.mark.parametrize("kind", ["duplicate_keys", "broken_link", "directory", "fifo"])
def test_present_invalid_manifest_is_never_downgraded_to_legacy(diagnostic, monkeypatch, kind):
    client, _, home, handoff, _ = diagnostic
    path = handoff / "manifest.json"
    if kind == "duplicate_keys":
        path.write_text('{"ontology":{},"ontology":{}}')
    elif kind == "broken_link":
        path.symlink_to(handoff / "missing-manifest.json")
    elif kind == "directory":
        path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO validation requires POSIX")
        os.mkfifo(path)
        original_open = Path.open

        def guard(target, *args, **kwargs):
            if target == path:
                pytest.fail("FIFO must never be opened")
            return original_open(target, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guard)
    _, decisions = _decisions(client.get(f"/api/research/{home.name}/reuse-plan"))
    assert set(decisions.values()) == {"reject"}


def test_state_identity_and_malformed_version_are_explicit_conflicts(diagnostic):
    client, _, home, _, state = diagnostic
    state["pipeline_id"] = "pipe_another_identity"
    (home / "pipeline_state.json").write_text(json.dumps(state))
    assert client.get(f"/api/research/{home.name}/reuse-plan").status_code == 409
    state["pipeline_id"] = home.name
    state["schema_version"] = 2
    state["stages"] = ["invalid-stage-container"]
    (home / "pipeline_state.json").write_text(json.dumps(state))
    assert client.get(f"/api/research/{home.name}/reuse-plan").status_code == 409
    state["pipeline_id"] = home.name
    state["schema_version"] = "not-a-version"
    (home / "pipeline_state.json").write_text(json.dumps(state))
    assert client.get(f"/api/research/{home.name}/reuse-plan").status_code == 409
