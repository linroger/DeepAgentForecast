"""Pure, deterministic dependency diagnostics for six pipeline stages.

This module never reads artifacts, runs providers, or changes pipeline state.
Even a ``reuse`` decision is conditional on caller-supplied observations and is
neither authenticity proof nor execution authorization. RX01's legacy adapter
cannot produce modern reuse by checking output bytes alone.
"""

from collections.abc import Iterable, Mapping

from .pipeline_contracts import STAGES, STAGE_DEPENDENCIES, StageObservation


_MODES = frozenset({"full", "research_only"})
_SEVERITY = {"reuse": 0, "legacy_unverified": 1, "rebuild": 2, "reject": 3}


def _validate_stage(stage: str) -> None:
    if not isinstance(stage, str):
        raise TypeError("stage names must be strings")
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")


def _complete_inputs(inputs: Mapping[str, str] | None) -> bool:
    return bool(inputs) and all(key.strip() and digest.strip() for key, digest in inputs.items())


def _local_decision(observation: StageObservation, changed: bool) -> tuple[str, list[str]]:
    """Reject invalid evidence first; only a complete modern record can prove equality."""
    rejected = [f"observation_error:{error}" for error in sorted(set(observation.errors))]
    if observation.output_status == "invalid":
        rejected.append("outputs_invalid")
    if observation.status == "running":
        rejected.append("stage_active")

    modern = observation.recorded_inputs is not None
    if modern:
        if not _complete_inputs(observation.recorded_inputs):
            rejected.append("recorded_inputs_incomplete")
        if not _complete_inputs(observation.current_inputs):
            rejected.append("current_inputs_incomplete")
        if not observation.owner_id or not observation.owner_id.strip():
            rejected.append("owner_missing")
        if not observation.recorded_owner_id or not observation.recorded_owner_id.strip():
            rejected.append("recorded_owner_missing")
        if (
            observation.owner_id
            and observation.recorded_owner_id
            and observation.owner_id != observation.recorded_owner_id
        ):
            rejected.append("owner_mismatch")
        if observation.status == "completed" and observation.output_status == "unverified":
            rejected.append("modern_outputs_unverified")
    if rejected:
        return "reject", rejected

    rebuild = []
    if changed:
        rebuild.append("explicit_change")
    if observation.status in ("pending", "failed", "cancelled"):
        rebuild.append(f"stage_{observation.status}")
    if observation.output_status == "missing":
        rebuild.append("outputs_missing")
    if modern and observation.current_inputs != observation.recorded_inputs:
        rebuild.append("inputs_changed")
    if rebuild:
        return "rebuild", rebuild

    # No recorded snapshot means no input-freshness proof, even when the adapter
    # checked the output bytes and supplied the current pipeline's owner ID.
    if not modern:
        return "legacy_unverified", ["recorded_inputs_absent"]
    return "reuse", ["completed_inputs_owner_and_outputs_match"]


def plan_reuse(
    observations: Mapping[str, StageObservation],
    changed: Iterable[str] = (),
    *,
    mode: str = "full",
) -> dict:
    """Return JSON-safe advisory diagnostics without changing any input or state.

    Omitted observations default to pending. All supplied arguments are validated,
    including stages excluded by ``research_only``. That mode selects research
    alone; changes to other valid stages do not affect it. ``changed_stages`` is
    the canonical, deduplicated list of explicit change seeds, including any
    unselected seeds. Input mismatches are also detected without explicit seeds.

    Rejection propagates through dependencies and dominates rebuilding, which
    dominates legacy uncertainty. Uncertainty cannot become proven downstream
    reuse. ``affected_stages`` lists selected rebuild/reject stages, leaving
    unchanged legacy uncertainty in the stage diagnostics. ``affected_by`` gives
    each stage's local and inherited root causes in canonical stage order.
    Reasons are stable codes, with dependency names or caller-supplied safe error
    codes after a colon. Each returned list/dict is detached from input state.
    """
    if not isinstance(mode, str):
        raise TypeError("mode must be a string")
    if mode not in _MODES:
        raise ValueError(f"unknown mode: {mode!r}")
    if not isinstance(observations, Mapping):
        raise TypeError("observations must be a mapping of stages to StageObservation")
    snapshot = dict(observations)
    for stage, observation in snapshot.items():
        _validate_stage(stage)
        if not isinstance(observation, StageObservation):
            raise TypeError(f"observation for {stage!r} must be a StageObservation")
    if isinstance(changed, (str, bytes, bytearray, Mapping)) or not isinstance(changed, Iterable):
        raise TypeError("changed must be an iterable of stage names, not a string or mapping")
    change_set = set()
    for stage in changed:
        _validate_stage(stage)
        change_set.add(stage)

    selected = STAGES if mode == "full" else STAGES[:1]
    decisions = {}
    causes = {}
    results = []
    for stage in selected:
        observation = snapshot.get(stage, StageObservation())
        decision, reasons = _local_decision(observation, stage in change_set)
        affected_by = {stage} if decision != "reuse" else set()
        dependencies = STAGE_DEPENDENCIES[stage]
        uncertain_dependencies = [dep for dep in dependencies if decisions[dep] != "reuse"]
        if uncertain_dependencies and decision == "reuse":
            # Do not retain an affirmative local-reuse reason when dependencies
            # have made this generation ineligible for proven reuse.
            reasons = []
        for dependency in uncertain_dependencies:
            inherited = decisions[dependency]
            if _SEVERITY[inherited] > _SEVERITY[decision]:
                decision = inherited
            reasons.append(f"dependency_{inherited}:{dependency}")
            affected_by.update(causes[dependency])
        decisions[stage] = decision
        causes[stage] = affected_by
        results.append({
            "stage": stage,
            "decision": decision,
            "reasons": reasons,
            "affected_by": [cause for cause in STAGES if cause in affected_by],
        })

    return {
        "schema": "pipeline-reuse-plan/v1",
        "advisory": True,
        "execution_authorized": False,
        "mode": mode,
        "stages": results,
        "affected_stages": [stage for stage in selected if decisions[stage] in ("rebuild", "reject")],
        "changed_stages": [stage for stage in STAGES if stage in change_set],
    }
