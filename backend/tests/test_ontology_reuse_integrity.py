"""Real orchestration must reject invalid ontology reuse without rewriting proof.

All external services and all storage are isolated by the shared state-machine
fixture. These cases cover the integrity prerequisite, not research freshness
or automatic regeneration of dependent stages.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_orchestrator_research_wiring import _exercise_prepare_run_resume, _po


_DOWNSTREAM_STAGES = (
    _po.STAGE_GRAPH, _po.STAGE_PREPARE, _po.STAGE_RUN, _po.STAGE_REPORT,
)


def _exercise_ontology_reuse(
    monkeypatch, tmp_path, *, mutation=None, registered=True,
    validation_enabled=True, legacy_project_only=False, late_replacement=False,
):
    captured = {"generator_calls": 0, "entered_stages": []}
    original_updater = _po.PipelineOrchestrator._make_stage_updater
    original_complete = _po.PipelineOrchestrator._complete_stage

    def before_run(state):
        monkeypatch.setattr(
            _po.Config, "PIPELINE_VALIDATE_ARTIFACTS", validation_enabled,
            raising=False,
        )
        project = _po.ProjectManager.get_project(state.project_id)
        ontology_path = Path(state.handoff_dir) / "ontology.json"
        captured["path"] = ontology_path
        captured["original_project_ontology"] = deepcopy(project.ontology)
        if not legacy_project_only:
            # Whitespace differs from the project object serialization; semantic
            # equality must not require identical serialization conventions.
            ontology_path.write_text(
                json.dumps(project.ontology, indent=4) + "\n", encoding="utf-8",
            )
        manifest = _po.PipelineManager.load_artifact_manifest(state.pipeline_id)
        if registered and not legacy_project_only:
            entry = _po._manifest_entry_for(
                "ontology", str(ontology_path), _po.STAGE_ONTOLOGY,
            )
            # A successful resume retains this registration and its provenance.
            entry["produced_at"] = "2026-01-01T00:00:00+00:00"
            entry["source_attempt"] = "original_ontology_attempt"
            manifest["ontology"] = entry
        if mutation == "tampered":
            ontology_path.write_text(
                ontology_path.read_text(encoding="utf-8").replace("Company", "Changed"),
                encoding="utf-8",
            )
        elif mutation == "missing_file":
            ontology_path.unlink()
        elif mutation == "project_mismatch":
            project.ontology = {"entity_types": [{"name": "DifferentProjectType"}]}
        elif mutation == "malformed_json":
            ontology_path.write_text('{"entity_types": [', encoding="utf-8")
            if registered:
                # Even matching file metadata cannot prove malformed JSON safe.
                manifest["ontology"] = _po._manifest_entry_for(
                    "ontology", str(ontology_path), _po.STAGE_ONTOLOGY,
                )
        elif mutation == "wrong_manifest_path":
            foreign_path = tmp_path / "foreign_ontology.json"
            foreign_path.write_bytes(ontology_path.read_bytes())
            manifest["ontology"]["path"] = str(foreign_path)
        elif mutation == "missing_sha":
            manifest["ontology"]["sha256"] = None
        elif mutation == "malformed_manifest":
            pass  # Corrupt only after publishing the normal fixture below.
        elif mutation is not None:
            raise AssertionError(f"Unknown test mutation: {mutation}")
        _po.PipelineManager.write_artifact_manifest(state.pipeline_id, manifest)
        manifest_path = Path(_po.PipelineManager.artifact_manifest_path(state.pipeline_id))
        if mutation == "malformed_manifest":
            manifest_path.write_bytes(b'{"ontology": {"sha256": "incomplete')
        captured["manifest_path"] = manifest_path
        captured["manifest_bytes_before_run"] = manifest_path.read_bytes()
        captured["original_entry"] = deepcopy(manifest.get("ontology"))
        captured["bytes_before_run"] = (
            ontology_path.read_bytes() if ontology_path.exists() else None
        )
        captured["project_before_run"] = deepcopy(project.ontology)
        captured["project"] = project

        class UnexpectedOntologyGenerator:
            def generate(self, **kwargs):
                captured["generator_calls"] += 1
                raise AssertionError("Integrity rejection must not regenerate ontology")

        def record_updater(self, pipeline_state, stage):
            captured["entered_stages"].append(stage)
            return original_updater(self, pipeline_state, stage)

        def complete_with_late_replacement(
            self, pipeline_state, stage, message="完成", *, reused=False,
        ):
            if stage == _po.STAGE_ONTOLOGY and reused and late_replacement:
                # The accepted project object remains unchanged. A later disk
                # replacement must not acquire a new, apparently valid proof.
                ontology_path.write_text(
                    '{"entity_types": [{"name": "LateReplacement"}]}',
                    encoding="utf-8",
                )
            return original_complete(
                self, pipeline_state, stage, message, reused=reused,
            )

        monkeypatch.setattr(_po, "OntologyGenerator", UnexpectedOntologyGenerator)
        monkeypatch.setattr(
            _po.PipelineOrchestrator, "_make_stage_updater", record_updater,
        )
        monkeypatch.setattr(
            _po.PipelineOrchestrator, "_complete_stage", complete_with_late_replacement,
        )

    result = _exercise_prepare_run_resume(
        monkeypatch, tmp_path, rebuild_prepare=False, before_run=before_run,
    )
    return result, captured


@pytest.mark.parametrize("mutation", [
    "tampered", "missing_file", "project_mismatch", "malformed_json",
    "wrong_manifest_path",
])
def test_invalid_cached_ontology_stops_real_run_before_downstream(
    monkeypatch, tmp_path, mutation,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, mutation=mutation,
    )

    assert result.state.status == "failed"
    assert result.state.current_stage == _po.STAGE_ONTOLOGY
    assert result.state.stages[_po.STAGE_ONTOLOGY].status == "failed"
    assert "ontology" in result.state.error.lower()
    assert captured["generator_calls"] == 0
    assert not set(_DOWNSTREAM_STAGES).intersection(captured["entered_stages"])
    assert result.start_calls == []
    assert result.summary_writes == []
    assert result.manager_calls == {"create": 0, "prepare": 0, "reseal": 0, "validate": 0}
    assert result.manifest["ontology"] == captured["original_entry"]
    assert captured["project"].ontology == captured["project_before_run"]
    assert (captured["path"].read_bytes() if captured["path"].exists() else None) == (
        captured["bytes_before_run"]
    )
    saved = json.loads(Path(_po.PipelineManager.state_path(
        result.state.pipeline_id,
    )).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["current_stage"] == _po.STAGE_ONTOLOGY
    assert saved["stages"][_po.STAGE_ONTOLOGY]["status"] == "failed"


@pytest.mark.parametrize("registered", [False, True])
def test_present_unregistered_ontology_still_requires_project_identity(
    monkeypatch, tmp_path, registered,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, mutation="project_mismatch", registered=registered,
    )
    assert result.state.status == "failed"
    assert result.state.current_stage == _po.STAGE_ONTOLOGY
    assert captured["generator_calls"] == 0
    assert not set(_DOWNSTREAM_STAGES).intersection(captured["entered_stages"])
    assert result.manifest.get("ontology") == captured["original_entry"]


def test_healthy_ontology_reuse_preserves_original_registration(monkeypatch, tmp_path):
    result, captured = _exercise_ontology_reuse(monkeypatch, tmp_path)

    assert result.state.status == "completed"
    assert captured["generator_calls"] == 0
    assert result.state.stages[_po.STAGE_ONTOLOGY].message == "本体已恢复"
    assert result.manifest["ontology"] == captured["original_entry"]
    assert captured["path"].read_bytes() == captured["bytes_before_run"]
    assert captured["project"].ontology == captured["original_project_ontology"]
    assert result.state.artifacts.get("ontology") == str(captured["path"])
    assert result.start_calls == []
    assert result.summary_writes == []


def test_malformed_manifest_is_preserved_before_research_bookkeeping(
    monkeypatch, tmp_path,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, mutation="malformed_manifest",
    )

    assert result.state.status == "failed"
    assert result.state.current_stage == _po.STAGE_ONTOLOGY
    assert result.state.stages[_po.STAGE_ONTOLOGY].status == "failed"
    assert "artifact_manifest_unreadable" in result.state.error
    assert captured["entered_stages"] == []
    assert captured["generator_calls"] == 0
    assert result.start_calls == []
    assert result.manager_calls == {"create": 0, "prepare": 0, "reseal": 0, "validate": 0}
    assert captured["manifest_path"].read_bytes() == captured["manifest_bytes_before_run"]
    assert captured["path"].read_bytes() == captured["bytes_before_run"]
    assert captured["project"].ontology == captured["original_project_ontology"]


def test_legacy_registration_without_sha_is_not_upgraded_during_reuse(
    monkeypatch, tmp_path,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, mutation="missing_sha",
    )

    assert result.state.status == "completed"
    assert captured["generator_calls"] == 0
    assert result.manifest["ontology"] == captured["original_entry"]
    assert result.manifest["ontology"]["sha256"] is None
    validation = result.state.options["ontology_reuse_validation"]
    assert validation["sha256_checked"] is False
    assert validation["size_checked"] is True
    assert validation["input_freshness"] == "unverified"
    assert captured["path"].read_bytes() == captured["bytes_before_run"]


def test_late_file_replacement_does_not_receive_a_new_reuse_registration(
    monkeypatch, tmp_path,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, late_replacement=True,
    )

    assert result.state.status == "completed"
    assert captured["generator_calls"] == 0
    assert result.manifest["ontology"] == captured["original_entry"]
    assert captured["path"].read_bytes() != captured["bytes_before_run"]
    assert captured["project"].ontology == captured["original_project_ontology"]
    assert _po.PipelineOrchestrator()._reuse_ok(result.state, _po.STAGE_ONTOLOGY) is False


def test_explicit_validation_opt_out_preserves_existing_reuse_behavior(
    monkeypatch, tmp_path,
):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, mutation="tampered", validation_enabled=False,
    )
    assert result.state.status == "completed"
    assert captured["generator_calls"] == 0
    assert result.state.stages[_po.STAGE_ONTOLOGY].message == "本体已恢复"
    assert result.start_calls == []
    assert captured["path"].read_bytes() == captured["bytes_before_run"]


def test_legacy_project_only_reuse_remains_available(monkeypatch, tmp_path):
    result, captured = _exercise_ontology_reuse(
        monkeypatch, tmp_path, legacy_project_only=True,
    )
    assert result.state.status == "completed"
    assert captured["generator_calls"] == 0
    assert result.state.stages[_po.STAGE_ONTOLOGY].message == "本体已恢复"
    assert not captured["path"].exists()
    assert "ontology" not in result.manifest
    assert result.start_calls == []


def _shared_handoff_ontology(monkeypatch, tmp_path):
    """A scenario child uses the base handoff and its existing registration."""
    monkeypatch.setattr(_po.Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(_po.Config, "PIPELINE_VALIDATE_ARTIFACTS", True)
    base_id, child_id = "pipe_ontology_base", "pipe_ontology_child"
    for pipeline_id in (base_id, child_id):
        _po.PipelineManager.ensure_dirs(pipeline_id)
    owner_handoff = Path(_po.PipelineManager.handoff_dir(base_id))
    owner_handoff.mkdir(parents=True, exist_ok=True)
    ontology_path = owner_handoff / "ontology.json"
    ontology = {"entity_types": [{"name": "Company"}], "edge_types": []}
    ontology_path.write_text(json.dumps(ontology, indent=2), encoding="utf-8")
    owner_entry = _po._manifest_entry_for(
        "ontology", str(ontology_path), _po.STAGE_ONTOLOGY,
    )
    _po.PipelineManager.write_artifact_manifest(base_id, {"ontology": owner_entry})
    _po.PipelineManager.write_artifact_manifest(child_id, {})
    state = _po.PipelineState(
        pipeline_id=child_id, prompt="Offline scenario", mode="full", status="running",
    )
    state.handoff_dir = str(owner_handoff)
    paths = [
        ontology_path,
        Path(_po.PipelineManager.artifact_manifest_path(base_id)),
        Path(_po.PipelineManager.artifact_manifest_path(child_id)),
    ]
    return state, ontology, owner_entry, paths


@pytest.mark.parametrize("child_registered", [False, True])
def test_shared_handoff_owner_hash_cannot_be_bypassed_by_child_manifest(
    monkeypatch, tmp_path, child_registered,
):
    state, ontology, _owner_entry, paths = _shared_handoff_ontology(monkeypatch, tmp_path)
    ontology_path, _owner_manifest, _child_manifest = paths
    # JSON semantics still match the project. The old owner's exact-byte proof
    # must reject this even if the child has no proof or a newer matching entry.
    ontology_path.write_bytes(ontology_path.read_bytes() + b"\n")
    if child_registered:
        child_entry = _po._manifest_entry_for(
            "ontology", str(ontology_path), _po.STAGE_ONTOLOGY,
        )
        _po.PipelineManager.write_artifact_manifest(
            state.pipeline_id, {"ontology": child_entry},
        )
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(RuntimeError, match="Ontology reuse rejected"):
        _po.PipelineOrchestrator()._require_ontology_reuse(state, ontology)

    assert state.current_stage == _po.STAGE_ONTOLOGY
    assert state.options["ontology_reuse_validation"]["status"] == "rejected"
    assert {path: path.read_bytes() for path in paths} == before


def test_shared_handoff_healthy_owner_proof_is_registered_and_read_only(
    monkeypatch, tmp_path,
):
    state, ontology, _owner_entry, paths = _shared_handoff_ontology(monkeypatch, tmp_path)
    before = {path: path.read_bytes() for path in paths}

    _po.PipelineOrchestrator()._require_ontology_reuse(state, ontology)

    validation = state.options["ontology_reuse_validation"]
    assert validation["status"] == "registered"
    assert validation["sha256_checked"] is True
    assert validation["size_checked"] is True
    assert validation["input_freshness"] == "unverified"
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("conflicting_manifest", ["owner", "child"])
def test_shared_handoff_checks_both_registration_paths(
    monkeypatch, tmp_path, conflicting_manifest,
):
    state, ontology, owner_entry, paths = _shared_handoff_ontology(monkeypatch, tmp_path)
    ontology_path, owner_manifest, child_manifest = paths
    _po.PipelineManager.write_artifact_manifest(
        state.pipeline_id, {"ontology": deepcopy(owner_entry)},
    )
    # Both initially prove the same bytes. A foreign path on either registration
    # must not be hidden by the other registration's otherwise valid proof.
    conflicting_path = owner_manifest if conflicting_manifest == "owner" else child_manifest
    foreign_ontology = tmp_path / "foreign_ontology.json"
    foreign_ontology.write_bytes(ontology_path.read_bytes())
    altered = json.loads(conflicting_path.read_text(encoding="utf-8"))
    altered["ontology"]["path"] = str(foreign_ontology)
    conflicting_path.write_text(json.dumps(altered), encoding="utf-8")
    before = {path: path.read_bytes() for path in [*paths, foreign_ontology]}

    with pytest.raises(RuntimeError, match="ontology_registration_path_mismatch"):
        _po.PipelineOrchestrator()._require_ontology_reuse(state, ontology)

    assert state.current_stage == _po.STAGE_ONTOLOGY
    assert {path: path.read_bytes() for path in [*paths, foreign_ontology]} == before
