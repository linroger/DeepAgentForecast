"""DRF-2 Pipeline Driver — deterministic stage machine replacing pipeline_orchestrator's
ESSENTIAL semantics (REDESIGN.md §2: state machine + resume, artifact manifest, health
gates, multi-seed ensemble). The agentic work runs in the deer-flow 2.0 harness; this
driver only sequences it and verifies artifacts deterministically (never via LLM).
"""

from .state import DriverState, StageRecord, StateStore, STAGES, SCHEMA_VERSION
from .manifest import ArtifactManifest, sha256_file
from .gates import (
    GateResult,
    research_gate,
    hollow_sim_gate,
    binary_conviction_gate,
    deliverable_gate,
)
from .ensemble import aggregate_ensemble, fan_out
from .harness_client import (
    HarnessError,
    RunResult,
    RunsApiHarness,
    DryRunHarness,
    DrySimEngine,
    HttpSimEngine,
    poll_simulation,
)
from .pipeline import PipelineDriver, STAGE_SKILLS, build_stage_prompt

__all__ = [
    "DriverState", "StageRecord", "StateStore", "STAGES", "SCHEMA_VERSION",
    "ArtifactManifest", "sha256_file",
    "GateResult", "research_gate", "hollow_sim_gate", "binary_conviction_gate", "deliverable_gate",
    "aggregate_ensemble", "fan_out",
    "HarnessError", "RunResult", "RunsApiHarness", "DryRunHarness",
    "DrySimEngine", "HttpSimEngine", "poll_simulation",
    "PipelineDriver", "STAGE_SKILLS", "build_stage_prompt",
]
