"""Tamper regressions for the PREPARE -> runner -> child config seal."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.simulation_manager import (
    SimulationManager,
    SimulationState,
    build_simulation_config_seal,
    validate_simulation_config_seal,
)
from scripts.run_parallel_simulation import validate_direct_child_config_seal


def _write_json(path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sealed_simulation(tmp_path):
    simulation_id = "sim_config_seal"
    config_path = tmp_path / "simulation_config.json"
    cast_path = tmp_path / "actor_cast_manifest.json"
    context_path = tmp_path / "actor_context_manifest.json"
    role_path = tmp_path / "reddit_profiles_roles.json"
    _write_json(config_path, {
        "simulation_id": simulation_id,
        "agent_configs": [{
            "agent_id": 0,
            "stance": "neutral",
            "actor_context_evidence_gap_audit": {
                "schema_version": "actor-config-evidence-gap-audit/v1",
                "actor_id": "actor_alpha",
                "evidence_gaps": {
                    "future_plans": [{
                        "reason": "No target date was found.",
                        "attempted_queries": ["Alpha target date filing"],
                        "receipt_ids": ["receipt_alpha_plan_1"],
                        "result_ids": ["result_alpha_plan_1"],
                        "attempt_count": 1,
                        "exhausted": True,
                    }],
                },
            },
        }],
    })
    _write_json(cast_path, {"selected_actor_count": 1})
    _write_json(context_path, {"schema_version": "actor-context-manifest/v1"})
    _write_json(role_path, {
        "role_contract_version": "actor-role/v2",
        "actor_context_required": True,
        "actor_role_count": 1,
    })
    config_sha, manifest_sha = build_simulation_config_seal(
        str(tmp_path),
        simulation_id=simulation_id,
        actor_cast_manifest_sha256=_sha(cast_path),
        actor_context_manifest_sha256=_sha(context_path),
        actor_role_manifest_sha256={"reddit": _sha(role_path)},
    )
    _write_json(tmp_path / "state.json", {
        "simulation_id": simulation_id,
        "actor_context_count": 1,
        "simulation_config_sha256": config_sha,
        "simulation_config_manifest_sha256": manifest_sha,
    })
    return config_path, config_sha, manifest_sha


def test_config_seal_validates_at_service_and_direct_child_boundaries(tmp_path):
    config_path, config_sha, manifest_sha = _sealed_simulation(tmp_path)

    service_manifest = validate_simulation_config_seal(
        str(tmp_path),
        expected_manifest_sha256=manifest_sha,
        expected_config_sha256=config_sha,
        expected_simulation_id="sim_config_seal",
        require=True,
    )
    child_manifest = validate_direct_child_config_seal(
        str(config_path), manifest_sha
    )

    assert service_manifest["simulation_config_sha256"] == config_sha
    assert child_manifest["manifest_sha256"] == manifest_sha


def test_authorized_post_prepare_mutation_is_resealed_before_child_load(
    tmp_path, monkeypatch
):
    config_path, old_config_sha, old_manifest_sha = _sealed_simulation(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["world_state_seed"] = {
        "scenarios": ["base", "upside"],
        "horizon_date": "2035-12-31",
    }
    _write_json(config_path, config)

    state = SimulationState(
        simulation_id="sim_config_seal",
        project_id="project",
        graph_id="graph",
    )
    state.actor_role_count = 1
    state.actor_context_count = 1
    state.actor_role_contract_version = "actor-role/v2"
    state.actor_context_contract_version = "actor-context/v1"
    state.actor_cast_manifest_sha256 = _sha(
        tmp_path / "actor_cast_manifest.json"
    )
    state.actor_context_manifest_sha256 = _sha(
        tmp_path / "actor_context_manifest.json"
    )
    state.actor_role_manifest_sha256 = {
        "reddit": _sha(tmp_path / "reddit_profiles_roles.json")
    }
    state.simulation_config_sha256 = old_config_sha
    state.simulation_config_manifest_sha256 = old_manifest_sha
    saved = []
    manager = SimulationManager()
    monkeypatch.setattr(manager, "_load_simulation_state", lambda _sid: state)
    monkeypatch.setattr(manager, "_get_simulation_dir", lambda _sid: str(tmp_path))
    monkeypatch.setattr(manager, "_save_simulation_state", saved.append)

    updated = manager.reseal_simulation_config("sim_config_seal")
    _write_json(tmp_path / "state.json", updated.to_dict())

    assert saved == [updated]
    assert updated.simulation_config_sha256 != old_config_sha
    assert updated.simulation_config_manifest_sha256 != old_manifest_sha
    service_manifest = validate_simulation_config_seal(
        str(tmp_path),
        expected_manifest_sha256=updated.simulation_config_manifest_sha256,
        expected_config_sha256=updated.simulation_config_sha256,
        expected_simulation_id="sim_config_seal",
        require=True,
    )
    child_manifest = validate_direct_child_config_seal(
        str(config_path), updated.simulation_config_manifest_sha256
    )
    assert service_manifest["simulation_config_sha256"] == (
        updated.simulation_config_sha256
    )
    assert child_manifest["manifest_sha256"] == (
        updated.simulation_config_manifest_sha256
    )


def test_completed_prepare_reuse_validates_existing_seal_without_rewriting(
    tmp_path, monkeypatch
):
    _config_path, config_sha, manifest_sha = _sealed_simulation(tmp_path)
    state = SimulationState(
        simulation_id="sim_config_seal",
        project_id="project",
        graph_id="graph",
    )
    state.actor_role_count = 1
    state.actor_context_count = 1
    state.actor_role_contract_version = "actor-role/v2"
    state.actor_context_contract_version = "actor-context/v1"
    state.simulation_config_sha256 = config_sha
    state.simulation_config_manifest_sha256 = manifest_sha
    manager = SimulationManager()
    monkeypatch.setattr(manager, "_load_simulation_state", lambda _sid: state)
    monkeypatch.setattr(manager, "_get_simulation_dir", lambda _sid: str(tmp_path))
    manifest_before = (tmp_path / "simulation_config_manifest.json").read_bytes()

    validated = manager.validate_prepared_simulation_config("sim_config_seal")

    assert validated["manifest_sha256"] == manifest_sha
    assert (tmp_path / "simulation_config_manifest.json").read_bytes() == manifest_before
    manifest = json.loads(manifest_before.decode("utf-8"))
    manifest["simulation_config_sha256"] = "0" * 64
    _write_json(tmp_path / "simulation_config_manifest.json", manifest)
    with pytest.raises(ValueError, match="manifest fingerprint mismatch"):
        manager.validate_prepared_simulation_config("sim_config_seal")


def test_completed_prepare_reuse_rediscovers_current_contract_after_state_downgrade(
    tmp_path, monkeypatch
):
    _sealed_simulation(tmp_path)
    downgraded = SimulationState(
        simulation_id="sim_config_seal",
        project_id="project",
        graph_id="graph",
    )
    manager = SimulationManager()
    monkeypatch.setattr(manager, "_load_simulation_state", lambda _sid: downgraded)
    monkeypatch.setattr(manager, "_get_simulation_dir", lambda _sid: str(tmp_path))

    with pytest.raises(ValueError, match="omits or changes discovered actor roles"):
        manager.validate_prepared_simulation_config("sim_config_seal")


def test_completed_prepare_reuse_keeps_unsealed_actor_role_v1_compatible(
    tmp_path, monkeypatch
):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    _write_json(legacy_dir / "simulation_config.json", {"agent_configs": []})
    _write_json(legacy_dir / "reddit_profiles_roles.json", {
        "schema_version": "actor-role-manifest/v2",
        "role_contract_version": "actor-role/v1",
        "actor_role_count": 1,
        "actor_context_required": False,
    })
    legacy = SimulationState(
        simulation_id="sim_legacy",
        project_id="project",
        graph_id="graph",
    )
    legacy.actor_role_count = 1
    legacy.actor_role_contract_version = "actor-role/v1"
    manager = SimulationManager()
    monkeypatch.setattr(manager, "_load_simulation_state", lambda _sid: legacy)
    monkeypatch.setattr(manager, "_get_simulation_dir", lambda _sid: str(legacy_dir))

    assert manager.validate_prepared_simulation_config("sim_legacy") == {}


def test_post_prepare_config_mutation_fails_before_child_load(tmp_path):
    config_path, config_sha, manifest_sha = _sealed_simulation(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agent_configs"][0]["stance"] = "opposing"
    _write_json(config_path, config)

    with pytest.raises(ValueError, match="simulation config fingerprint mismatch"):
        validate_simulation_config_seal(
            str(tmp_path),
            expected_manifest_sha256=manifest_sha,
            expected_config_sha256=config_sha,
            require=True,
        )
    with pytest.raises(ValueError, match="simulation config fingerprint mismatch"):
        validate_direct_child_config_seal(str(config_path), manifest_sha)


def test_child_load_rechecks_the_exact_bytes_after_seal_validation(tmp_path):
    config_path, _config_sha, manifest_sha = _sealed_simulation(tmp_path)
    from scripts.run_parallel_simulation import load_config

    manifest = validate_direct_child_config_seal(str(config_path), manifest_sha)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agent_configs"][0]["stance"] = "changed between check and use"
    _write_json(config_path, config)

    with pytest.raises(ValueError, match="changed after seal validation"):
        load_config(config_path, manifest["simulation_config_sha256"])


def test_config_seal_covers_typed_evidence_gap_audit_bytes(tmp_path):
    config_path, config_sha, manifest_sha = _sealed_simulation(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agent_configs"][0]["actor_context_evidence_gap_audit"][
        "evidence_gaps"
    ]["future_plans"][0]["attempted_queries"][0] = (
        "mutated query after PREPARE"
    )
    _write_json(config_path, config)

    with pytest.raises(ValueError, match="simulation config fingerprint mismatch"):
        validate_simulation_config_seal(
            str(tmp_path),
            expected_manifest_sha256=manifest_sha,
            expected_config_sha256=config_sha,
            expected_simulation_id="sim_config_seal",
            require=True,
        )
    with pytest.raises(ValueError, match="simulation config fingerprint mismatch"):
        validate_direct_child_config_seal(str(config_path), manifest_sha)


def test_current_role_cannot_downgrade_by_deleting_state_config_binding(tmp_path):
    config_path, _config_sha, manifest_sha = _sealed_simulation(tmp_path)
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("simulation_config_sha256")
    state.pop("simulation_config_manifest_sha256")
    _write_json(state_path, state)

    with pytest.raises(ValueError, match="state-bound simulation config"):
        validate_direct_child_config_seal(str(config_path), manifest_sha)


def test_unversioned_legacy_child_without_seal_remains_compatible(tmp_path):
    config_path = tmp_path / "legacy.json"
    _write_json(config_path, {"agent_configs": []})

    assert validate_direct_child_config_seal(str(config_path)) == {}
