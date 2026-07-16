# Foglamp Current-Shape Map

- **Work package:** WP0, slice 0D (`EXECPLAN_FOGLAMP.md` §8)
- **Companion ADRs:** `docs/adr/0001-workflow-authority.md`, `docs/adr/0002-forecast-evidence-publication-authority.md`
- **Code anchors verified as of:** commit `92e5348`, with WP1 containment in progress
- **Vocabulary:** `CODEX_FOGLAMP.md` §3.3 (exact producer → handoff → consumer map), §4.20 (context-loss matrix)

This document maps every current producer→consumer path to its migration disposition.
Line numbers are approximate anchors into the files at the stated commit; they identify
the mechanism, not a stable contract. Each subsystem gets one table with the same
columns: Producer, Artifact, Consumer, What is lost, Current authority, Migration
disposition (owning WP).

## 1. Research probabilities

Research prose is the de facto data bus: probabilities extracted from
`research_report.md` fan out into actor seeds, WorldState base rates, and the report
spine with no shared influence-cluster identity, so one research prior can be counted
multiple times.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| Research stage | `research_report.md` | `backend/app/utils/actors.py` `forecast_inputs_from_report_markdown` → `actors['forecast_inputs']` | Claim/source spans, as-of availability, independence clusters — only regex-recoverable numbers survive | Markdown prose | WP6 influence clusters; WP1 already defaults `simulationForecastEffect=diagnostic_only` |
| `backend/app/services/pipeline_orchestrator.py` `inject_forecast_inputs_from_report_markdown` (~2705), wired at research finalize (~8124) and PREPARE (~8648) | `actors['forecast_inputs']` | `world_state_seed_from_actors` → `simulation_config.json` `world_state_seed` (~8673) | Lineage from prior to seed; the same prior re-enters via a second path below | Mutable `simulation_config.json` | WP6 influence clusters |
| `simulation_config.json` `world_state_seed` | WorldState `base_rates` | Simulation rounds / decision channel | Provenance that base rates descend from the research prior (double-count risk) | In-memory WorldState | WP6 influence clusters |
| Same research prose | `forecast_inputs_block` | `derive_forecast_spine` (`backend/app/services/report_agent.py` ~2782) | Shared identity with the WorldState path — the forecast can absorb the same prior twice | Report-stage prompt assembly | WP6 influence clusters (I-13, I-18) |

## 2. Graph feedback

Simulation-derived text is written into the shared observed Zep graph, stripping
simulation provenance on the way in. WP1 slice 1B gates the orchestrator call sites
behind the pinned safety policy.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `backend/app/services/zep_graph_memory_updater.py` `AgentActivity.to_episode_text` (~34–61) | Episode text | `_send_batch_activities` → `client.graph.add` on the shared observed graph | Simulation provenance is stripped at serialization; simulated facts become indistinguishable from observed ones (violates I-11 target) | Shared mutable Zep graph | WP10 overlays; call sites gated by pinned safety policy (WP1 slice 1B, `_pinned_safety`) |
| `zep_graph_memory_updater.py` `_write_typed_edges` | Typed edges | Shared observed graph | Scope qualification (run/simulation/seed) | Env gate `SIM_TYPED_FEEDBACK_EDGES` | WP10 isolated experiment overlays (I-12) |
| `zep_graph_memory_updater.py` `write_interview_fact` | Interview facts | Shared observed graph | Same provenance stripping | Gated by `SIM_INTERVIEW_GRAPH_FEEDBACK` (WP1) | WP10 overlays |

## 3. Ensemble (multi-seed)

Seed reruns share one graph, so seeds are not independent draws and unscoped feedback
from one seed can reach another.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `pipeline_orchestrator._maybe_run_seed_ensemble` (~4889) | Per-seed prepare/run/report reruns; `seed_jobs = base_seed + k*7919`; `ENSEMBLE_SEED_CONCURRENCY` default 2 | Ensemble aggregation | Seed independence — every seed runs against the **same** `graph_id`, so cross-seed contamination is possible (I-12 target) | Shared graph + in-process scheduling | WP12 isolated experiments; WP1 defaults `N_FORECAST_SEEDS=1` |

## 4. Decision channel

Central batched elicitation compresses the roster and converts convergence heuristics
into outcomes. WP1 slices 1C (convergence policy) and the explicit-only power map are
containment, not the target contract.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `backend/app/services/decision_channel.py` `_build_round_decision_prompt` | Batched round decision prompt | LLM elicitation per round | Full actor context — roster compression via `_agent_meta_map` keeps only `name`/`stance`/`influence`/`gains_if`/`loses_if` | Prompt assembly code | WP11 action contracts (I-14, I-17) |
| `decision_channel.py` `_outcome_power_map` | Outcome power weights | Round outcome resolution | WP1 makes this explicit-only (no influence fallback); missing authority stays unknown rather than defaulting to visibility (I-15) | Config-supplied explicit map | WP11 action contracts |
| WorldState EWMA convergence + `CONVERGENCE_POLICY_V1` (WP1 1C) | Convergence signal | Stopping/round logic | Distinction between failure, silence, abstention, and genuine convergence — typed round statuses `committed`/`abstained`/`silent`/`failed`/`missing` now exist (WP1) but downstream semantics remain heuristic (I-16) | In-memory WorldState | WP11 action contracts |

## 5. Report

The report stage still owns semantic work that belongs to the forecast plane; WP1
relabels and gates the simulation-derived inputs, and two structured spine inputs are
supported but never passed.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `backend/app/services/report_agent.py` `_build_signal_pack` | Signal pack (diagnostic header, WP1) | Report prompt | Header marks it diagnostic, but the pack is still prose-shaped rather than a typed evidence pack | Prompt text | WP7 bundle-first authority (I-08, I-18) |
| `report_agent._world_state_block` | World-state block (`elicited_model_projection` label, WP1) | Report prompt | Label prevents restating simulation output as real-world judgment, but the block remains free text | Prompt text | WP7/WP8 |
| `report_agent._derive_and_pin_forecast_spine` | Forecast spine | Report + downstream forecast surface | `signal_pack` gated to `legacy_prompt` only (WP1); `base_distribution`/`quantitative_facts` are supported by `derive_forecast_spine` but **never passed** — dormant structured inputs | Spine derivation inside report stage | WP6/WP7 (dormant inputs activated via evidence packs and bundle) |
| `backend/app/services/forecast_extractor.py` `extract_binary_forecasts` | Extracted binary forecasts | Ledger / calibration | `sim_sensitive` gated to `legacy_prompt` (WP1); extraction can only recover what prose states | Regex/parse over report prose | WP7 `ForecastBundle v2` (extraction becomes a projection check) |

## 6. Ledger / evaluation

Production and evaluation rows share files with disconnected lifecycles; resolutions
never flip ledger rows.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `backend/app/services/forecast_ledger.py` | `ledger.jsonl` (production) + `_evaluation_ledger` (WP1 1E redirect for golden rows) | `calibration_summary` / `recalibration_param`; `is_production_calibration_row` excludes `golden`/`characterization_only`/`record_class=evaluation` | Single transactional lifecycle; exclusion is filter-based, not schema-enforced | Append-only JSONL files | WP13 transactional target/revision/resolution/score lifecycle (I-20) |
| Resolution flow | `resolutions.jsonl`, idempotent on (`report_id`, `forecast_id`, `market_id`) | Scoring / review | Never flips `ledger.jsonl` `resolved` flags — a **disconnected lifecycle**; targets can silently leave the denominator | Separate JSONL file | WP13 (I-20); promotion rules WP14 (I-21) |

## 7. Workflow

Process-local authority; see ADR 0001 for the ratified target.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| `pipeline_orchestrator.py` `PipelineOrchestrator._threads` / `_cancel_events` / `_lifecycle_lock` (~3967–3973) | In-process run advancement | Stage execution | Durable attempt history, fenced leases, survivable timers (I-05) | Process memory | WP16 durable store (16A shadow → 16D cutover) |
| `PipelineManager._state_locks` (~341) | Per-run state locking | State mutation | Cross-process safety; a second process cannot see the lock | Process memory | WP16 |
| Heartbeat/owner fingerprint (~6483) | Liveness/ownership signal | Resume/recovery logic | Fencing — a stale owner can still commit; fingerprint is advisory (I-05) | File/heartbeat convention | WP16 leases with fencing tokens |
| `backend/app/models/task.py` `TaskManager` (~54) | In-memory task singleton | API/status surfaces | All task state on process death | Process memory | WP16 durable task/attempt/event/outbox store |

## 8. Safety pin

WP1's compatibility pin is the bridge until immutable RunSpec exists.

| Producer | Artifact | Consumer | What is lost | Current authority | Migration disposition (owning WP) |
|---|---|---|---|---|---|
| Admission-time `capture_safety_policy_v1` | `state.options["safety_policy_v1"]`, re-pinned on resume (`origin=resume_reconstructed_safe`) | All WP1 safety gates (graph feedback, diagnostic simulation, convergence policy, ledger isolation) | Immutability — options live in mutable run state, so the pin is convention-enforced rather than schema-enforced | Mutable `state.options` captured at admission | WP4 migrates the pin into the immutable RunSpec (with `workflowAuthority` and authority modes per ADR 0001/0002) |
