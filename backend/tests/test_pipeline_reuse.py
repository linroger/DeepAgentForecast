"""Pure RX01 acceptance scenarios; no production artifacts or service imports."""

import builtins
from dataclasses import FrozenInstanceError, replace
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType

import pytest


STAGES = ("research", "ontology", "graph", "prepare", "run", "report")
CLOSURES = {
    "research": STAGES,
    "ontology": STAGES[1:],
    "graph": STAGES[2:],
    "prepare": STAGES[3:],
    "run": STAGES[4:],
    "report": STAGES[5:],
}
SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"


@pytest.fixture(scope="module")
def core():
    # Load the two real source files without the services package's eager imports.
    package_name = "_rx01_pure_services"
    package = ModuleType(package_name)
    package.__path__ = [str(SERVICES)]
    modules_before = set(sys.modules)
    sys.modules[package_name] = package
    try:
        for name in ("pipeline_contracts", "pipeline_reuse"):
            qualified = f"{package_name}.{name}"
            spec = importlib.util.spec_from_file_location(qualified, SERVICES / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified] = module
            spec.loader.exec_module(module)
        assert sys.modules[f"{package_name}.pipeline_contracts"].STAGES == STAGES
        yield (
            sys.modules[f"{package_name}.pipeline_contracts"].StageObservation,
            sys.modules[f"{package_name}.pipeline_reuse"].plan_reuse,
        )
    finally:
        for name in set(sys.modules) - modules_before:
            if name == package_name or name.startswith(package_name + "."):
                sys.modules.pop(name, None)


@pytest.fixture
def complete(core):
    observation, _ = core
    return {
        stage: observation(
            status="completed",
            current_inputs={"semantic_policy": "a" * 64, "upstream": "b" * 64},
            recorded_inputs={"semantic_policy": "a" * 64, "upstream": "b" * 64},
            output_status="verified",
            owner_id="base-generation",
            recorded_owner_id="base-generation",
        )
        for stage in STAGES
    }


def rows(plan):
    return {row["stage"]: row for row in plan["stages"]}


def assert_closure(plan, source, decision):
    assert plan["affected_stages"] == (list(CLOSURES[source]) if decision in ("rebuild", "reject") else [])
    for stage, row in rows(plan).items():
        assert row["decision"] == (decision if stage in CLOSURES[source] else "reuse")
        assert row["affected_by"] == ([source] if stage in CLOSURES[source] else [])
        assert row["reasons"]


def test_complete_generation_is_advisory_reuse(core, complete):
    _, plan_reuse = core
    plan = plan_reuse(complete)
    assert plan["schema"] == "pipeline-reuse-plan/v1"
    assert plan["advisory"] is True
    assert plan["execution_authorized"] is False
    assert plan["mode"] == "full"
    assert [row["stage"] for row in plan["stages"]] == list(STAGES)
    assert all(row["decision"] == "reuse" for row in plan["stages"])
    assert plan["affected_stages"] == plan["changed_stages"] == []
    assert json.loads(json.dumps(plan)) == plan


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("change_kind", ("explicit", "input_hash", "input_key"))
def test_each_change_rebuilds_only_its_dependency_closure(core, complete, stage, change_kind):
    _, plan_reuse = core
    changed = ()
    if change_kind == "explicit":
        changed = (stage,)
    else:
        inputs = dict(complete[stage].current_inputs)
        if change_kind == "input_hash":
            inputs["semantic_policy"] = "c" * 64
        else:
            inputs["additional_semantic_policy"] = "c" * 64
        complete[stage] = replace(complete[stage], current_inputs=inputs)
    plan = plan_reuse(complete, changed)
    assert_closure(plan, stage, "rebuild")
    assert plan["changed_stages"] == list(changed)


def test_report_language_change_preserves_completed_simulation(core, complete):
    _, plan_reuse = core
    complete["report"] = replace(complete["report"], current_inputs={"language": "c" * 64})
    plan = plan_reuse(complete)
    assert_closure(plan, "report", "rebuild")
    assert rows(plan)["run"]["decision"] == "reuse"


def test_research_only_ignores_unselected_changes_and_active_stages(core, complete):
    _, plan_reuse = core
    complete["run"] = replace(complete["run"], status="running")
    plan = plan_reuse(complete, ["report", "ontology"], mode="research_only")
    assert [row["stage"] for row in plan["stages"]] == ["research"]
    assert rows(plan)["research"]["decision"] == "reuse"
    assert plan["affected_stages"] == []
    assert plan["changed_stages"] == ["ontology", "report"]
    changed = plan_reuse(complete, ["research"], mode="research_only")
    assert changed["affected_stages"] == ["research"]


def test_fork_can_preserve_base_owners_and_rebuild_own_overlay(core, complete):
    _, plan_reuse = core
    for stage in ("prepare", "run", "report"):
        complete[stage] = replace(
            complete[stage], owner_id="fork-generation", recorded_owner_id="fork-generation"
        )
    before = dict(complete)
    plan = plan_reuse(complete, ["prepare"])
    assert_closure(plan, "prepare", "rebuild")
    assert complete == before
    assert complete["research"].owner_id == "base-generation"
    assert complete["prepare"].owner_id == "fork-generation"


@pytest.mark.parametrize("stage", STAGES)
def test_legacy_uncertainty_propagates_without_proven_reuse(core, complete, stage):
    _, plan_reuse = core
    complete[stage] = replace(complete[stage], recorded_inputs=None, recorded_owner_id=None)
    assert_closure(plan_reuse(complete), stage, "legacy_unverified")


@pytest.mark.parametrize("output_status", ("verified", "unverified"))
def test_legacy_adapter_observations_never_invent_proof(core, output_status):
    observation, plan_reuse = core
    legacy = {
        stage: observation(status="completed", owner_id="current-pipeline", output_status=output_status)
        for stage in STAGES
    }
    plan = plan_reuse(legacy)
    assert all(row["decision"] == "legacy_unverified" for row in plan["stages"])
    assert plan["affected_stages"] == []


def test_legacy_graph_change_preview_preserves_upstream_uncertainty(core):
    observation, plan_reuse = core
    legacy = {stage: observation(status="completed", owner_id="current-pipeline") for stage in STAGES}
    plan = plan_reuse(legacy, ["graph"])
    assert plan["affected_stages"] == ["graph", "prepare", "run", "report"]
    for stage in ("research", "ontology"):
        assert rows(plan)[stage]["decision"] == "legacy_unverified"
    for stage in CLOSURES["graph"]:
        assert rows(plan)[stage]["decision"] == "rebuild"


@pytest.mark.parametrize("status", ("pending", "failed", "cancelled"))
@pytest.mark.parametrize("stage", STAGES)
def test_unfinished_stages_rebuild_descendants(core, complete, status, stage):
    _, plan_reuse = core
    complete[stage] = replace(complete[stage], status=status)
    assert_closure(plan_reuse(complete), stage, "rebuild")


def test_omitted_observations_are_pending(core, complete):
    _, plan_reuse = core
    del complete["run"]
    assert_closure(plan_reuse(complete), "run", "rebuild")
    empty = plan_reuse({})
    assert all(row["decision"] == "rebuild" for row in empty["stages"])


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("output_status,decision", (("missing", "rebuild"), ("invalid", "reject")))
def test_missing_or_corrupt_outputs_propagate(core, complete, stage, output_status, decision):
    _, plan_reuse = core
    complete[stage] = replace(complete[stage], output_status=output_status)
    assert_closure(plan_reuse(complete), stage, decision)


@pytest.mark.parametrize("change", (
    {"errors": ("output_digest_mismatch",)},
    {"owner_id": "different-owner"},
    {"owner_id": None},
    {"recorded_owner_id": None},
    {"owner_id": "  "},
    {"recorded_owner_id": ""},
    {"recorded_inputs": {}},
    {"current_inputs": {}},
    {"current_inputs": None},
    {"recorded_inputs": {"policy": ""}},
    {"current_inputs": {" ": "a" * 64}},
    {"output_status": "unverified"},
))
def test_malformed_modern_or_invalid_observations_reject_even_when_changed(core, complete, change):
    _, plan_reuse = core
    complete["graph"] = replace(complete["graph"], **change)
    assert_closure(plan_reuse(complete, ["graph"]), "graph", "reject")


def test_running_stage_is_explicitly_active_and_rejected(core, complete):
    _, plan_reuse = core
    complete["run"] = replace(complete["run"], status="running")
    plan = plan_reuse(complete, ["run"])
    assert_closure(plan, "run", "reject")
    assert "stage_active" in rows(plan)["run"]["reasons"]


def test_legacy_never_hides_invalid_output_or_missing_output(core):
    observation, plan_reuse = core
    for output_status, expected in (("invalid", "reject"), ("missing", "rebuild")):
        plan = plan_reuse(
            {"research": observation(status="completed", output_status=output_status)},
            mode="research_only",
        )
        assert rows(plan)["research"]["decision"] == expected


def test_reject_beats_upstream_rebuild_and_retains_all_causes(core, complete):
    _, plan_reuse = core
    complete["graph"] = replace(complete["graph"], errors=("corrupt",))
    plan = plan_reuse(complete, ["ontology", "report"])
    assert rows(plan)["research"]["decision"] == "reuse"
    assert rows(plan)["ontology"]["decision"] == "rebuild"
    for stage in ("graph", "prepare", "run", "report"):
        assert rows(plan)[stage]["decision"] == "reject"
    assert rows(plan)["report"]["affected_by"] == ["ontology", "graph", "report"]


def test_snapshot_copies_and_freezes_both_mappings(core):
    observation, plan_reuse = core
    current = {"policy": "a" * 64}
    recorded = dict(current)
    obs = observation("completed", current, recorded, "verified", (), "base", "base")
    current["policy"] = "b" * 64
    recorded.clear()
    assert dict(obs.current_inputs) == dict(obs.recorded_inputs) == {"policy": "a" * 64}
    for snapshot in (obs.current_inputs, obs.recorded_inputs):
        with pytest.raises(TypeError):
            snapshot["policy"] = "c" * 64
    with pytest.raises(FrozenInstanceError):
        obs.status = "failed"
    assert not hasattr(obs, "__dict__")
    assert rows(plan_reuse({"research": obs}, mode="research_only"))["research"]["decision"] == "reuse"


@pytest.mark.parametrize("kwargs,error", (
    ({"status": "done"}, ValueError),
    ({"status": []}, TypeError),
    ({"output_status": "ok"}, ValueError),
    ({"output_status": None}, TypeError),
    ({"current_inputs": []}, TypeError),
    ({"recorded_inputs": [("policy", "a")]}, TypeError),
    ({"current_inputs": {1: "a"}}, TypeError),
    ({"recorded_inputs": {"policy": []}}, TypeError),
    ({"owner_id": 42}, TypeError),
    ({"recorded_owner_id": False}, TypeError),
    ({"errors": ["corrupt"]}, TypeError),
    ({"errors": (1,)}, TypeError),
    ({"errors": ("",)}, ValueError),
))
def test_invalid_observation_argument_shapes_are_explicit(core, kwargs, error):
    observation, _ = core
    with pytest.raises(error):
        observation(**kwargs)


@pytest.mark.parametrize("observations,changed,mode,error", (
    (None, (), "full", TypeError),
    ([], (), "full", TypeError),
    ({"unknown": None}, (), "full", ValueError),
    ({1: None}, (), "full", TypeError),
    ({"research": {}}, (), "full", TypeError),
    ({}, "research", "full", TypeError),
    ({}, {"research": True}, "full", TypeError),
    ({}, None, "full", TypeError),
    ({}, 4, "full", TypeError),
    ({}, [None], "full", TypeError),
    ({}, ["unknown"], "full", ValueError),
    ({}, (), "unknown", ValueError),
    ({}, (), None, TypeError),
    ({}, (), [], TypeError),
    ({"unknown": None}, (), "research_only", ValueError),
    ({}, ["unknown"], "research_only", ValueError),
    ({"run": {}}, (), "research_only", TypeError),
))
def test_invalid_plan_argument_shapes_are_explicit(core, observations, changed, mode, error):
    _, plan_reuse = core
    with pytest.raises(error):
        plan_reuse(observations, changed, mode=mode)


def test_deterministic_order_and_detached_results(core, complete):
    _, plan_reuse = core
    baseline = plan_reuse(complete, ("report", "ontology", "report"))
    reverse = {
        stage: replace(
            obs,
            current_inputs=dict(reversed(list(obs.current_inputs.items()))),
            recorded_inputs=dict(reversed(list(obs.recorded_inputs.items()))),
        )
        for stage, obs in reversed(list(complete.items()))
    }
    assert json.dumps(plan_reuse(reverse, iter(("ontology", "report")))) == json.dumps(baseline)
    assert baseline["changed_stages"] == ["ontology", "report"]
    baseline["stages"][0]["reasons"].append("caller edit")
    assert "caller edit" not in rows(plan_reuse(complete))["research"]["reasons"]


def test_planning_and_snapshot_construction_have_no_io_or_state_mutation(core, complete, monkeypatch):
    observation, plan_reuse = core
    before = dict(complete)
    current = {"policy": "a" * 64}
    recorded = dict(current)
    environment = dict(os.environ)
    changed = ["report"]

    def forbidden(*args, **kwargs):
        raise AssertionError("pure planner attempted an external operation")

    with monkeypatch.context() as patch:
        for obj, name in (
            (builtins, "open"), (io, "open"), (os, "open"), (os, "stat"),
            (os, "listdir"), (os, "scandir"), (os, "system"), (os, "mkdir"),
            (os, "remove"), (os, "rename"), (os, "putenv"),
            (subprocess, "Popen"), (socket, "socket"), (socket, "getaddrinfo"),
        ):
            patch.setattr(obj, name, forbidden)
        obs = observation("completed", current, recorded, "verified", (), "base", "base")
        plan = plan_reuse(complete, changed)
        research_plan = plan_reuse({"research": obs}, mode="research_only")
    assert_closure(plan, "report", "rebuild")
    assert rows(research_plan)["research"]["decision"] == "reuse"
    assert complete == before
    assert current == recorded == {"policy": "a" * 64}
    assert changed == ["report"]
    assert dict(os.environ) == environment
