# EXECPLAN_FOGLAMP — Build the Decision-Grade Forecasting Architecture

**Executor:** Claude Fable 5, or an equivalently capable coding agent

**Status:** Proposed living implementation plan

**Architecture source:** [`CODEX_FOGLAMP.md`](CODEX_FOGLAMP.md)

**Repository snapshot studied:** 2026-07-17, base commit `e58c928becc7f89036a7a2d9b0b5c3636b3f716a`, dirty worktree

**Primary implementation profile:** modular monolith; SQLite WAL metadata authority and filesystem content-addressed artifacts on a workstation; ports are designed for a later PostgreSQL/object-store adapter, which is not considered verified until its integration suite exists
**Rule:** complete one atomic subpackage/slice, its evidence, and one reviewable commit before starting another slice; finish all required slices and gates for a package before declaring that package complete

This is the implementation authority for the Foglamp architecture program. It is intentionally separate from the older root `EXECPLAN.md`, which describes an earlier enrichment program. Where they conflict, this plan wins for this program.

In particular, do **not** implement the older recommendations to:

- turn simulation feedback on against the shared observed graph;
- treat `influence_weight` as institutional outcome power;
- run scenario forks or multiple seeds against a mutable shared graph;
- interpret same-model redraw spread as calibrated uncertainty;
- let report prose remain the authority for probabilities; or
- make every new field fail-soft when an authoritative forecast would then become misleading.

The older plan remains useful historical context. It is not permission to preserve a newly confirmed unsafe behavior.

---

## 1. Purpose and observable end state

The program is complete only when the application produces a forecast as a durable, resolvable, auditable object whose entire lineage can be reproduced:

```text
ForecastCase
  → immutable QuestionSpec / TargetSpec / DecisionSpec / RunSpec
  → immutable evidence snapshot and influence-lineage map
  → optional, isolated, pre-registered experiments
  → exact ForecastEvidencePack + pre-forecast RunQualityAssessment
  → ForecastBundle with priors and revisions
  → external audit + PublicationSeal
  → deterministic Markdown / HTML / PDF / chart / translation projections
  → ForecastClaim / ResolutionEvent / ScoreRecord
  → outcome-blind PromotionDecision
```

The final system must prove all of the following:

1. observed evidence, inferences, assumptions, simulated events, forecasts, and resolved outcomes never silently merge;
2. one source family and every direct/derived descendant contribute through one influence cluster;
3. a social action cannot become an institutional action without an explicit, validated mapping and feasibility check;
4. simulation failure, silence, abstention, infeasibility, no change, and convergence remain different states;
5. forks/seeds cannot read or mutate one another's generated state;
6. the probability object exists before report prose and records exactly which inputs it saw or omitted;
7. every forecast target has one stable identity through publication, revision, monitoring, resolution, and scoring;
8. policies are promoted only by outcome-blind, paired, prospective evidence against the correct frozen baseline;
9. every external command is idempotent and every task is owned by one durable leased attempt;
10. latency, tokens, dollars, retries, and quality are attributable end to end.

This plan does not require one network service per logical plane. Build bounded modules and typed ports first. Split processes only after a measured scaling or isolation requirement appears.

---

## 2. Non-negotiable safety boundary

Before any implementation write, Claude Fable 5 MUST:

1. read `AGENTS.md` if present, then `handoff.md`, `PLANS.md`, this file, `CODEX_FOGLAMP.md`, and relevant recent commits;
2. run `pwd`, `git status --short`, and `git log --oneline -20`;
3. classify every dirty path as user-owned, prior-agent-owned, generated, or intended for this program; do not infer ownership from recency;
4. after the owner has resolved or separately committed every intended source change, select the resulting clean source commit as `S`; never create `S` by bundling unrelated dirty paths;
5. compute and record the SHA-256 of this plan and `CODEX_FOGLAMP.md`, create a temporary clean documentation worktree at `S`, and copy only the owner-approved documentation bytes into it;
6. in that clean worktree, use path-limited staging—never `git add .`—and require the staged-path allowlist and staged hashes to match the approved documents before creating documentation commit `D`; verify `parent(D)=S` and a clean status;
7. create the isolated `codex/` implementation branch/worktree from `D`, not `S`, and verify that the implementation worktree is clean and contains the recorded document hashes; and
8. stop before writing if ownership, `S`, `D`, their parent relationship, the staged-path allowlist, or either document hash cannot be established.

Every subpackage handoff and commit message body records `sourceCommit=S`, `documentationCommit=D`, `architectureDocHash`, and `execPlanHash`. If any of them changes, stop, re-review the delta, update the decision log, and explicitly adopt the new commits/hashes before continuing. Plan and architecture changes occur as separately reviewed documentation commits descended from the current implementation base; implementation resumes only from the adopted documentation commit. A dirty working tree is evidence to classify, not permission to fold changes into the program.

Runtime safety rules:

- Never restart a healthy in-flight pipeline.
- Never duplicate a `/run` POST.
- Never resume a superseded or read-only historical pipeline.
- Never change provider routing merely to make a test pass.
- Never launch a paid/networked end-to-end run without explicit authorization after deterministic and shadow gates pass.
- Never switch workflow, forecast, evidence, graph, or publication authority for an in-flight run.
- Never mutate historical artifact bytes during migration.
- Never delete legacy state until the explicit soak and restore gates in Work Package 17 pass.
- Preserve MiniMax/approved-fallback constraints recorded in the latest `handoff.md`; this architecture program does not broaden providers.

The present repository is heavily dirty. The documentation created by the current session is not authorization for an implementation agent to absorb, rewrite, stage, commit, or discard unrelated changes.

---

## 3. Mandatory codebase-reading route

Do not start by editing the largest module. Build an evidence map in this order and record current symbols/line anchors in `handoff.md`, because anchors in this plan will drift.

### 3.1 Entry, state, and authority

Read:

- `backend/app/api/research.py`
- `backend/app/api/sdk.py`
- `backend/app/services/pipeline_orchestrator.py`
- `backend/app/models/task.py`
- `backend/app/utils/atomic.py`
- `backend/app/config.py`

Answer:

- What creates a pipeline, task, thread, subprocess, and state file?
- Which object decides the next stage?
- What survives process death?
- Which commands have idempotency protection, and at what scope?
- What files/IDs are used as business identity?
- Which settings are pinned at intake, and which are re-read from global configuration later?

### 3.2 Research and evidence handoff

Read:

- `deerflow_bridge/deerflow_research.py`
- `deerflow_bridge/search_tools.py`
- `deerflow_bridge/cached_fetch.py`
- `deerflow_bridge/research_budget.py`
- `backend/app/services/requirement_spec.py`
- `_RESEARCH_CONTRACT_FILES`, manifest, and reuse logic in `pipeline_orchestrator.py`
- dossier edit/continue routes in `backend/app/api/research.py`

Trace exact producer → artifact → selector/truncation → consumer paths for:

- report prose;
- actors and actor-role contracts;
- sources and exact source spans;
- quantitative and contested claims;
- timeline and market snapshots;
- research quality/judge/penalty state; and
- human edits and resumed research generations.

### 3.3 Graph, identity, and preparation

Read:

- `backend/app/services/graph_builder.py`
- `backend/app/services/graphiti_client/runtime.py`
- `backend/app/services/zep_entity_reader.py`
- `backend/app/services/zep_graph_memory_updater.py`
- `backend/app/services/simulation_manager.py`
- `backend/app/utils/actors.py`
- `backend/app/services/actor_role_prompt.py`
- `backend/app/services/oasis_profile_generator.py`

Confirm:

- which typed ontology fields are actually consumed;
- where exact source/citation IDs disappear;
- where all graph nodes/edges are enumerated;
- how actor identities are matched or merged;
- how simulation feedback and interviews are written; and
- whether every graph read/write declares snapshot, namespace, run, scenario, and seed.

### 3.4 Simulation, decision, and WorldState

Read:

- `backend/app/services/simulation_config_generator.py`
- `backend/app/services/simulation_runner.py`
- `backend/app/services/decision_channel.py`
- `backend/app/services/worldstate.py`
- `backend/app/services/world_delta.py`
- `backend/scripts/run_parallel_simulation.py`
- `backend/scripts/run_twitter_simulation.py`
- `backend/scripts/run_reddit_simulation.py`

Construct a sequence diagram for:

```text
actor evidence/view
→ actor belief/context
→ social action
→ central decision elicitation
→ commitment weight
→ WorldState update
→ convergence
→ report signal pack
```

At every arrow, state what was discarded, inferred, defaulted, or conflated.

### 3.5 Forecast, report, publication, and evaluation

Read:

- `backend/app/services/forecast_extractor.py`
- `backend/app/services/report_agent.py`
- `backend/app/services/report_lint.py`
- `backend/app/services/report_visualizer.py`
- `backend/app/services/forecast_ledger.py`
- `backend/scripts/resolution_monitor.py`
- `backend/scripts/scheduled_rerun.py`
- `backend/app/services/backtest.py`
- `backend/scripts/golden_eval.py`
- `backend/tests/eval/golden_questions.json`

Trace:

- research probability direct path into the spine;
- research probability indirect path through WorldState;
- market direct and simulation-mediated paths;
- structured arguments supported by `derive_forecast_spine()` versus arguments passed by its production caller;
- bundle/audit/report/translation mutation boundaries;
- scenario and binary forecast persistence;
- manual resolution, market resolution, calibration, and rerun-diff stores; and
- every place that claims confidence, interval, convergence, or calibration.

### 3.6 Frontend and operator surfaces

Read current routes, API clients, report/dossier views, progress polling, downloads, and run controls under `frontend/src/`. Confirm how stable IDs, quality state, experiment validity, forecast revisions, resolution, and provenance must appear without breaking existing history views.

### 3.7 Baseline evidence to preserve

Select small, redacted fixtures representing:

- one completed pipeline;
- one failed pipeline;
- one stopped/resumed pipeline;
- one research-only/edit/continue path;
- one scenario fork;
- one translated/published report;
- one manually resolved report; and
- one market-monitored forecast.

Record exact source artifact hashes. Do not copy giant uploads into tests. Create minimal deterministic fixtures that preserve contracts and failure shapes.

---

## 4. Architecture decisions to ratify before runtime work

Work Package 0 creates ADRs. The recommended defaults are:

1. **Workflow authority:** transactional database state machine plus outbox. SQLite WAL is the workstation implementation; repositories remain PostgreSQL-compatible. Do not add Temporal now, and never allow a workflow engine and database state machine to both advance runs.
2. **Artifact authority:** content-addressed immutable bytes plus transactional `ArtifactRecord`; legacy paths remain compatibility projections until cutover.
3. **Forecast authority:** `ForecastBundle v2`, pinned per `RunSpec`; legacy `forecast.json` is a deterministic projection after cutover.
4. **Evidence authority:** source/evidence/claim records; dossier and graph are projections.
5. **Graph authority:** never authoritative for evidence; immutable observed projection plus isolated experiment overlays.
6. **Publication authority:** external `AuditRecord` and `PublicationSeal`; post-seal renderers are deterministic and network-free.
7. **Evaluation authority:** one transactional target/revision/resolution/score lifecycle.
8. **Migration:** shadow writes and comparisons first; authority choices are immutable per run.

If an ADR chooses differently, it must still satisfy every invariant and gate in this plan. Update this file and `CODEX_FOGLAMP.md` before implementation diverges.

---

## 5. Proposed required invariants

Work Package 0 must ratify these in ADRs. Once ratified, use these IDs in tests, reviews, and handoffs; before ratification, any disagreement is a blocking ADR decision, not an implicit exception.

| ID | Invariant |
|---|---|
| I-01 | Every durable record has a stable ID and schema version. |
| I-02 | Registered artifact bytes are immutable; correction creates a new version. |
| I-03 | Every derived record names input IDs/hashes, producer version, policy version, and time. |
| I-04 | Paths are locations, never business identity. |
| I-05 | One logical task has at most one active leased attempt; stale fencing tokens cannot commit. |
| I-06 | Start, resume, fork, cancel, publish, edit, and resolve commands are idempotent. |
| I-07 | Workflow authority is singular and pinned for the life of a run. |
| I-08 | Probabilities live in `ForecastBundle`; prose and renderers cannot author or mutate them. |
| I-09 | Audit is external; the seal binds immutable candidate and audit hashes without circular mutation. |
| I-10 | Graph/vector/search are rebuildable projections with snapshot/watermark checks. |
| I-11 | Observed, inferred, assumed, simulated, forecast, and resolved records never silently merge. |
| I-12 | Every experiment write is run/simulation/scenario/seed scoped; unqualified reads fail closed. |
| I-13 | One source family and its descendants share one influence cluster. Transformation creates no independent weight. |
| I-14 | Social event, inferred intent, feasible action, mechanism, and modeled state are separate types. |
| I-15 | Missing authority/power is unknown; visibility never defaults to outcome power. |
| I-16 | Failure, silence, abstention, infeasibility, no change, and convergence are distinct. Invalid experiments cause no forecast update. |
| I-17 | Actor prompts expose only evidence permitted by their observation policy and availability time. |
| I-18 | Every forecast input is selected through a hash-bound evidence pack with selected/omitted/disposition lineage. |
| I-19 | Every model/tool invocation is attributable, budgeted, sensitivity-checked, and reproducible to declared limits. |
| I-20 | Every eligible target remains in the evaluation denominator through resolution or a recorded terminal reason. |
| I-21 | No production policy is promoted solely from in-sample, answer-bearing, or famous-outcome retrospective evaluation. |
| I-22 | Legacy API/history remains readable until a declared, tested cutover and soak period ends. |
| I-23 | Secrets never enter records, events, prompts, artifacts, telemetry, or exported plans. |

---

## 6. Living progress ledger

Update this table at the start and end of every implementation session. A checked item requires linked evidence in `handoff.md` and a successful commit.

| Work package | State | Commit | Evidence |
|---|---|---|---|
| 0. Baseline, characterization, and ADRs | Not started | — | — |
| 1. Immediate correctness containment | Not started | — | — |
| 2. Core domain and predictive-validity contracts | Not started | — | — |
| 3. Metadata and immutable artifact stores | Not started | — | — |
| 4. Stable identity, research revisions, and command idempotency | Not started | — | — |
| 5. Capability gateway and complete invocation telemetry | Not started | — | — |
| 6. Influence lineage, ForecastEvidencePack, and quality gate | Not started | — | — |
| 7. ForecastBundle v2 and bundle-first authority | Not started | — | — |
| 8. NarrativeIR, external audit, seal, and deterministic publication | Not started | — | — |
| 9. Evidence/claim ledger and research-generation authority | Not started | — | — |
| 10. Rebuildable graph, isolated overlays, and prepare fast path | Not started | — | — |
| 11. Actor observation/action contracts and valid decision semantics | Not started | — | — |
| 12. Optional experiment service, paired forks/seeds, and simulation efficiency | Not started | — | — |
| 13. Transactional forecast revision, resolution, and scoring | Not started | — | — |
| 14. Outcome-blind evaluation and policy promotion | Not started | — | — |
| 15. Adaptive research and report/cost optimization | Not started | — | — |
| 16. Durable workflow shadowing, activity extraction, and authority cutover | Not started | — | — |
| 17. Indexed reads, compatibility retirement, and final soak | Not started | — | — |

Allowed engineering states are `Not started`, `Characterizing`, `Red test`, `Implementing`, `Shadow`, `Blocked`, and `Implementation complete`. Long-horizon policy work additionally uses `Study collecting`, `Study analyzed`, `Policy rejected`, and `Policy promoted`. Never call a package simply `Complete` when its prospective study or authority cutover is still pending.

### 6.1 Mandatory dependency and authority order

Package numbers group related work; they are **not** the execution order. The following dependency table governs. A row may be implemented in shadow once its code prerequisites pass, but it cannot become authoritative until every authority prerequisite also passes.

| Slice | Code prerequisites | Authority prerequisites | Earliest allowed authority state |
|---|---|---|---|
| 0A fixtures/baseline manifest | clean frozen source/doc hashes | none | characterization only |
| 0B characterization suite | 0A | none | characterization only |
| 0C workflow ADR | 0A | explicit approval | design authority only |
| 0D evidence/forecast/publication ADR and current-shape map | 0A–0B | explicit approval | design authority only |
| 1 containment | 0A–0D | compatibility safety-policy pin for every existing run | new-run defaults only |
| 2 contracts | 0D | none | additive schemas only |
| 3 metadata/artifact stores | 2 | 0C–0D | shadow |
| 16A task/attempt/command/event/outbox store | 3A | 0C | shadow; no workflow advancement |
| 4 case identity, research revisions, admission idempotency | 3, 16A | 16A command uniqueness/outbox gates | new-command admission only |
| 5 capability gateway/telemetry | 3–4 | per-run policy pin | shadow |
| 9A minimal source/evidence/snapshot ledger | 2–5 | 0D | shadow evidence identity |
| 6 influence lineage/evidence packs/quality | 5, 9A | pack parity and influence-cluster gates | shadow, then pack authority; unpromoted simulation remains `no_update` |
| 7 bundle-first forecast | 6, 9A | Work Package 6 pack-authority gate, forecast parity, auditability, run pin | candidate bundle authority only; no new publication before 8 |
| 8 publication | 7 | audit/seal/parity gates | sealed deterministic publication |
| 9B claim-complete research-generation authority | 9A, 7–8 | evidence backfill/conformance gates | claim-complete new runs only |
| 10 graph projections/overlays | 9A–9B | snapshot/watermark/isolation gates | projection authority only |
| 11 actor/action semantics | 9A, 10 | observation/action conformance gates | experiment input authority only |
| 12 experiment service | 6, 10–11 | 16B–16C experiment activity durability plus an immutable 14D `PromotionDecision` | diagnostic/shadow until promoted |
| 13A claim/revision/resolution/score shadow service | 3, 7, 16A | publishability-qualified backfill | shadow |
| 14A outcome-blind harness/preregistration | 6–7, 9A, 13A | hidden-outcome/leakage gates | evaluation only |
| 15A research optimization | 5, 9A–9B | research parity, evidence coverage, cost gates | shadow |
| 15B report optimization | 7–9B | bundle/seal/audit parity and cost gates | shadow |
| 15C graph/prepare/simulation optimization | 10, 12, and 16B–16C for each extracted activity | registered Work Package 10/12 parity, durability, validity, and cost gates | shadow |
| 16B–16C worker/activity extraction | 16A, relevant domain package | kill/retry/fencing parity per activity | shadow |
| 16D workflow cutover | all activities required by selected path | chaos, restore, parity, no in-flight conversion | new-run workflow authority |
| 13B resolution/scoring authority | 13A, 16D | idempotency, compatibility, coverage gates | new claims only |
| 14B–14D prospective study and promotion | 14A, 12 shadow, 13B, 16D | preregistered sample/stopping gates and immutable 14D `PromotionDecision` | policy promoted per domain/version only |
| 15 authoritative optimization | immutable 14D `PromotionDecision` where forecast semantics change | package-specific exit gate plus an exact candidate/domain/version decision match | run-pinned promoted policy only |
| 17 retirement | all applicable authority and soak gates | backup/restore and compatibility soak | deletion one path at a time |

The critical ordering consequences are deliberate:

- a minimal `EvidenceSnapshot`/source/evidence identity exists before an authoritative `ForecastEvidencePack`;
- command admission may deduplicate early, but exactly-once business effects are not claimed until 16A uniqueness/outbox gates pass;
- actor, experiment, resolution, promotion, and optimization code may be built and exercised in shadow, but none bypasses durable workflow cutover or its own promotion gate; and
- Work Package 14 can reach `Implementation complete` while remaining `Study collecting` for months. `Policy promoted` is a separate, evidence-dependent state.

---

## 7. Per-work-package execution ritual

For every numbered package:

1. Re-read this package, its dependencies, latest `handoff.md`, and current code. Reverify all anchors.
2. Restate the exact user-visible or architectural behavior being changed.
3. Add the named failing-before or characterization test. Capture its expected failure.
4. Implement the smallest slice that makes the test pass.
5. Run focused tests immediately.
6. Run touched-file lint/compile and `git diff --check`.
7. Run relevant integration/replay and compare shadow artifacts/hashes.
8. Perform an independent review for every authority, schema, migration, or forecast-semantic change.
9. Update the progress ledger, decision log, surprises, metrics, rollback status, and `handoff.md`.
10. Commit only one atomic subpackage/slice. A package may require several commits; do not mix another package or an unrelated cleanup into the same commit.

“Likely files” is a discovery seed, not permission to edit all listed files. Before the red test, the slice manifest must replace it with the exact mandatory changed/new paths and explain any addition. If the necessary surface is materially larger than the slice, stop and split/re-plan rather than silently expanding scope.

Recommended commands, adjusted to the actual environment:

```bash
cd backend
.venv/bin/python -m pytest -q <focused tests>
uv run ruff check <touched Python files>
.venv/bin/python -m compileall -q app
uv sync --check

cd ../frontend
npm run test:unit
npm run build

cd ..
git diff --check
git status --short
```

Do not invoke `backend/.venv/bin/pytest`; this repository family has had a stale absolute shebang. A suite that prints `100%` but does not exit is a failure, not a pass: run with a bounded timeout and capture a thread/process dump before fixing lifecycle leakage.

### 7.1 Required evidence and independent-review artifact

Every slice creates `docs/foglamp/evidence/<slice-id>/` containing:

- `manifest.json`: source/doc hashes, branch, commit, dirty-state classification, requirements, changed paths, fixture/input/output hashes, and timestamps from an injected clock;
- `commands.jsonl`: exact command, cwd, start/end, exit code, bounded-timeout value, stdout/stderr artifact IDs, and network/paid-call policy;
- `before.json` and `after.json`: failing/characterized behavior and passing behavior in a schema owned by the slice;
- `reconciliation.json`: legacy/new counts, hashes, allowed differences, quarantine counts, and unexplained mismatches;
- `rollback.json`: run-pinned mode change, scope, preconditions, rehearsal result, and proof that no unsafe writer/dual authority is restored; and
- `review.json`: reviewer identity, plan/doc hashes, checklist version, findings (`blocking|important|minor|note`), disposition, and verdict. Any blocking or important finding prevents authority cutover.

Use `gtimeout --signal=TERM --kill-after=30s 15m ...` for focused local suites unless a package registers a different justified bound. On timeout, capture process tree plus Python fault stacks using the repository diagnostic script added in 0A; do not blindly retry. Network/model/provider calls are denied in all baseline, unit, migration-dry-run, and synthetic chaos tests.

### 7.2 Cross-cutting deterministic and security gates

These gates apply to every relevant slice:

1. Inject UTC clock, monotonic lease clock, UUID/ID source, and RNG. Test timezone offsets, DST boundaries, clock skew, lease expiry, evidence `availableAt`, deadline, and resolution ordering.
2. Maintain canonical hash vectors across two fresh Python processes for Unicode normalization, decimal/float policy, timestamps, omitted/default fields, ordering, and schema aliases. Reject NaN/Infinity and ambiguous local times.
3. Scan fixtures, databases, CAS, spool files, caches, logs, telemetry, prompts, and exported evidence for real/seeded secrets. A seeded secret canary must be detected; no raw secret value appears in the result artifact.
4. Test current and previous supported schema versions plus unknown-field behavior. External JSON remains `lowerCamelCase`; Python adapters remain `snake_case`.
5. For API/UI-affecting slices, run generated-client contract tests, `npm run test:unit`, `npm run build`, and a browser smoke covering current history plus the changed command/read path. Add and document a deterministic `test:e2e` script before relying on it as a gate.
6. Treat the current deployment as single-owner unless authentication exists. `tenantId` is optional metadata, not a security claim; cross-tenant tests become mandatory before multi-user deployment.
7. A PostgreSQL-compatible repository interface is a design goal, not a verified capability until a real PostgreSQL adapter and dialect/integration suite pass. Do not advertise it earlier.

### 7.3 Identity, command, and retention conventions

- Random business IDs use typed UUIDv7/ULID-style namespaces; content identities use the versioned canonical hash. Never derive sensitive business identity from guessable content. Collision handling is fail-closed and audited.
- Client-retryable commands use a client-generated idempotency key. Server-generated correlation IDs aid tracing but cannot make a lost-response retry safe. Request hashing, key TTL, response retention, and expired-key behavior are versioned; conflicting payloads always fail.
- Enumerate command coverage before cutover: start, continue/resume, fork, cancel/stop, edit, publish/export/translate, resolve/appeal, rerun/monitor, simulation create/prepare/run, and destructive clean/delete operations. Settings mutation is a versioned policy command, never a hidden side effect.
- Raw sources, prompts, response caches, child spools, and exact locators inherit sensitivity, retention, legal-hold, and deletion policy. High-sensitivity or nondeterministic requests are never response-cached unless explicitly allowed.

### 7.4 Planned focused-suite entry points

Create and keep these stable entry points so later agents do not invent ad hoc commands. Each row runs through the bounded wrapper in §7.1.

| Slice | Focused suite/command after creation |
|---|---|
| 0A–0B | `cd backend && .venv/bin/python -m pytest -q tests/test_architecture_characterization.py` |
| 1 | `cd backend && .venv/bin/python -m pytest -q tests/test_foglamp_containment.py` |
| 2 | `cd backend && .venv/bin/python -m pytest -q tests/test_domain_contracts.py tests/test_predictive_validity_contracts.py` |
| 3A–3B | `cd backend && .venv/bin/python -m pytest -q tests/test_metadata_store.py tests/test_artifact_store.py tests/test_migrations.py` |
| 16A/4 | `cd backend && .venv/bin/python -m pytest -q tests/test_workflow_store.py tests/test_command_idempotency.py tests/test_research_generation.py` |
| 5 | `cd backend && .venv/bin/python -m pytest -q tests/test_capability_gateway.py tests/test_invocation_import.py` |
| 9A/6 | `cd backend && .venv/bin/python -m pytest -q tests/test_evidence_ledger.py tests/test_influence_lineage.py tests/test_forecast_evidence_pack.py tests/test_run_quality.py` |
| 7 | `cd backend && .venv/bin/python -m pytest -q tests/test_forecast_bundle_v2.py tests/test_bundle_first_report.py tests/test_forecast_bundle_backfill.py` |
| 8 | `cd backend && .venv/bin/python -m pytest -q tests/test_narrative_ir.py tests/test_publication_seal.py tests/test_deterministic_publication.py` |
| 9B | `cd backend && .venv/bin/python -m pytest -q tests/test_evidence_ledger.py tests/test_research_generation_authority.py tests/test_evidence_backfill.py` |
| 10 | `cd backend && .venv/bin/python -m pytest -q tests/test_graph_projection.py tests/test_graph_overlay_isolation.py tests/test_actor_projection.py` |
| 11 | `cd backend && .venv/bin/python -m pytest -q tests/test_actor_observation.py tests/test_actor_action_contract.py tests/test_decision_semantics.py` |
| 12 | `cd backend && .venv/bin/python -m pytest -q tests/test_experiment_service.py tests/test_experiment_isolation.py tests/test_experiment_validity.py` |
| 13A–13B | `cd backend && .venv/bin/python -m pytest -q tests/test_resolution_service.py tests/test_scoring_registry.py tests/test_resolution_backfill.py` |
| 14 | `cd backend && .venv/bin/python -m pytest -q tests/test_evaluation_isolation.py tests/test_promotion_study.py tests/test_promotion_decision.py` |
| 15 | `cd backend && .venv/bin/python -m pytest -q tests/test_research_policy.py tests/test_report_efficiency.py tests/test_performance_envelope.py` |
| 16B–16D | `cd backend && .venv/bin/python -m pytest -q tests/test_workflow_worker.py tests/test_workflow_chaos.py tests/test_workflow_cutover.py` |
| 17 | `cd backend && .venv/bin/python -m pytest -q tests/test_indexed_reads.py tests/test_compatibility_retirement.py tests/test_restore_soak.py` |

---

## 8. Work Package 0 — Baseline, characterization, and authority ADRs

**Purpose:** freeze current semantics and reproduce every critical failure before changing behavior.

Execute this package as four separately reviewable slices:

- **0A — Fixture inventory and baseline manifest.** Select the exact completed/failed/resumed/forked/resolved artifacts, copy only minimal contract-preserving fixtures, and emit `baseline_manifest.json` with source path, SHA-256, schema/shape, redaction result, expected semantic role, and why it is safe to retain. The baseline CLI must support `capture --source-root --fixture-root --manifest`, `verify --manifest`, and `compare --manifest --candidate-root`, return non-zero on mismatch, and emit deterministic JSON.
- **0B — Characterization suite.** Add tests that pass only when they faithfully describe current behavior. Pair every unsafe characterization with a named future-regression test and owner package. Expected failures use `xfail(strict=True, reason="WP<n>: ...")`; the implementing package must remove the mark, not leave permanent xfails.
- **0C — Workflow ADR.** Decide database state machine plus outbox versus an engine, define authority, transaction, timer, attempt, cancellation, and rollback semantics, and prohibit dual advancement.
- **0D — Evidence/forecast/publication ADR and current-shape map.** Decide canonical identity, hash/encryption semantics, source/evidence authority, bundle/audit/seal authority, external serialization rules, and map every current producer/consumer/path to its migration disposition.

0A MUST NOT assume that runtime artifacts exist inside the clean implementation worktree. Before capture, the owner designates an absolute, read-only `FOGLAMP_SOURCE_ARTIFACT_ROOT` containing only selected terminal/read-only run history. The baseline tool rejects a relative root, symlink escape, a selected healthy/in-flight run, and any pre-read/post-read inventory-hash change. It records an opaque source-root ID plus relative source locator, ownership classification, lifecycle state, source hash, fixture hash, redaction/transformation record, and secret-scan result; it never writes to the source or stores the raw absolute path in a committed manifest. If the original root cannot be held stable, first create an owner-approved immutable filesystem snapshot outside Git, verify its full inventory hash, mount it read-only, and capture only from that snapshot. No fixture is copied merely because it is reachable.

**Mandatory outputs**

- new `docs/adr/0001-workflow-authority.md`
- new `docs/adr/0002-forecast-evidence-publication-authority.md`
- new `docs/foglamp/current-shape-map.md`
- new `backend/tests/fixtures/foglamp_architecture/`
- new `backend/tests/fixtures/foglamp_architecture/baseline_manifest.json`
- new `backend/tests/test_architecture_characterization.py`
- new `backend/scripts/architecture_baseline.py`
- new `backend/scripts/process_diagnostics.py`
- `PLANS.md` and `handoff.md`

**Required characterization fixtures**

1. a research prior reaches the forecast directly and through WorldState but has no shared influence-cluster identity;
2. a market value reaches actors/world state and the forecast spine;
3. a social action is relabeled as an institutional commitment and report prompting can restate simulation output as real-world judgment while suppressing its simulation mechanics;
4. provider failure or zero valid commitments can decay into convergence;
5. `influence_weight` becomes outcome power;
6. fork/seed runs share graph state and unscoped feedback;
7. `quantitative_facts` and `base_distribution` are supported but absent from the production spine call;
8. the typed WorldState anchor is dormant while implicit signal text remains active;
9. human edit invalidates old research hashes without creating a new authoritative generation;
10. quality penalties are write-only and pipeline health arrives after forecast/report construction;
11. manual resolution, scenario ledger, binary claims, market ledger, and scheduled drift are disconnected;
12. `golden_questions.json` exposes labels/answers, the harness cannot enforce an as-of run, and `--to-ledger` rows are not excluded from production calibration/recalibration.

Characterization tests should pass against current behavior and use names such as `test_characterizes_shared_graph_feedback`; they are not fixes. For each, add a paired future-regression test marked with the work package that will turn it green.

Use exact paired names for the twelve rows: `shared_graph_feedback`, `market_descendant_reuse`, `social_to_institutional_laundering`, `failure_false_convergence`, `visibility_to_outcome_power`, `fork_seed_shared_mutation`, `structured_spine_omissions`, `dormant_typed_worldstate_anchor`, `edit_without_research_generation`, `write_only_quality_and_late_health`, `disconnected_resolution_lifecycle`, and `answer_bearing_golden_harness`. Each has `test_characterizes_<name>` and `test_wp_<owner>_prevents_<name>`; the latter is strict-xfail only until its owner package begins.

The fixture manifest stores both `sourceSha256` and `fixtureSha256`, plus byte-range redactions/transforms and a seeded-secret scan result. A minimized/redacted fixture never claims to retain the source byte hash.

**Exact package commands**

```bash
cd backend
: "${FOGLAMP_SOURCE_ARTIFACT_ROOT:?set an approved absolute read-only artifact root}"
.venv/bin/python scripts/architecture_baseline.py capture --source-root "$FOGLAMP_SOURCE_ARTIFACT_ROOT" --fixture-root tests/fixtures/foglamp_architecture --manifest tests/fixtures/foglamp_architecture/baseline_manifest.json
.venv/bin/python scripts/architecture_baseline.py verify --manifest tests/fixtures/foglamp_architecture/baseline_manifest.json
gtimeout --signal=TERM --kill-after=30s 15m .venv/bin/python -m pytest -q tests/test_architecture_characterization.py
uv run ruff check scripts/architecture_baseline.py scripts/process_diagnostics.py tests/test_architecture_characterization.py
```

**Baseline metrics**

Record `pipe_0e1b84d2682a` as a case study, not a universal benchmark:

- research: 3,509.0 s;
- graph: 3,238.2 s;
- prepare: 2,920.7 s;
- run: 670.9 s;
- report: 1,239.7 s;
- total: 11,638.7 s;
- report: 252 attributed calls, 2,679,956 input and 260,706 output tokens.

Also record telemetry blind spots rather than interpreting zero-attributed calls as zero calls.

**Exit gate**

- All twelve failure paths are reproducible offline with fixture/artifact hashes.
- ADRs select one workflow authority, one artifact/evidence authority, one forecast/publication authority, and per-run pinning rules.
- Current API, state, resume, publication, and resolution shapes are documented.
- `architecture_baseline.py verify` returns zero on the frozen fixture set and non-zero after a controlled byte/semantic mutation.
- Fixture secret scan is clean, wall-clock and random dependencies are replaced by injected clocks/seeds, and canonical hash vectors are identical across two fresh processes.
- Every `xfail` names its owning package and removal criterion; no unowned skip/xfail is accepted.
- No runtime behavior changes.

**Rollback:** documentation/tests only; revert the commit if the characterization is wrong.

---

## 9. Work Package 1 — Immediate correctness containment

**Purpose:** stop known overstatement and contamination while durable replacements are built.

Execute as 1A observed-graph/interview containment, 1B compatibility safety-policy pin, 1C round validity/convergence, 1D diagnostic simulation/provenance plus single-draw defaults, and 1E golden-ledger isolation. Each slice has its own red test and forward rollback; do not combine them merely because they share configuration.

**Likely files**

- `backend/app/config.py`
- `.env.example`
- `backend/app/services/decision_channel.py`
- `backend/app/services/worldstate.py`
- `backend/app/services/zep_graph_memory_updater.py`
- `backend/app/services/zep_tools.py`
- `backend/app/services/pipeline_orchestrator.py`
- `backend/app/services/report_agent.py`
- `backend/app/services/forecast_extractor.py`
- `backend/scripts/golden_eval.py`
- focused configuration/decision/report/eval tests

**Red tests**

- default configuration cannot write activity, typed-edge, or end-of-simulation interview feedback to the observed graph;
- default new run uses one forecast seed and identity extremization;
- injected decision-provider failure yields `validity=invalid`, `forecastEffect=no_update`, and `converged=false`;
- all-abstention and zero-valid rounds are distinguishable from equilibrium;
- WorldState is labeled `elicited_model_projection` in forecast/report context;
- default simulation forecast policy is diagnostic/no-update, so `signal_pack` may inform analysis prose but cannot enter `derive_forecast_spine()` or adjust a probability;
- diagnostic simulation text cannot be rewritten as observed real-world evidence while its simulation provenance is hidden; and
- `golden_eval --to-ledger` is rejected for the production ledger or redirected to an isolated evaluation ledger, while production `calibration_summary()` and `recalibration_param()` exclude historical `golden`/`characterization_only` rows.

**Implementation**

1. Default `SIM_GRAPH_FEEDBACK=false` and `SIM_TYPED_FEEDBACK_EDGES=false` for observed graphs. Add a separate `SIM_INTERVIEW_GRAPH_FEEDBACK=false` gate or make `write_interview_fact()` obey the same observed-graph prohibition; keep interviews in run artifacts.
2. Default `N_FORECAST_SEEDS=1`; set `REPORT_SPINE_SELFCONSISTENCY_K=1`; set `ENSEMBLE_EXTREMIZE_A=1.0` unless a promoted policy ID overrides it.
3. Add typed round accounting and an explicit validity state without yet redesigning the action model.
4. Prevent invalid/inconclusive rounds from updating WorldState or converging.
5. Replace “authoritative/hard simulation evidence” wording with an elicited-model-projection label.
6. Add a run-pinned `simulationForecastEffect=diagnostic_only|validated_update|legacy_prompt` policy. New runs default `diagnostic_only`; omit simulation/WorldState and market-mediated simulation descendants from probability-generation inputs while preserving them for explicitly labeled analysis. `validated_update` is unavailable until Work Packages 6, 12, and 14 pass. `legacy_prompt` exists only for characterization fixtures.
7. Remove the report instruction that converts simulation output into unlabeled real-world judgment while suppressing simulation mechanics. Any retained diagnostic analysis must identify its simulated provenance and remain outside the probability/evidence authority path.
8. Mark the existing historical golden fixture as characterization-only. Reject production-ledger `--to-ledger` writes or route them to a separate evaluation ledger, and exclude legacy golden/characterization rows from production calibration and recalibration queries.
9. Before changing ambient defaults, persist a compatibility `safetyPolicyV1` snapshot in the existing `run.json`/`pipeline_state.json` path for every newly admitted run. A pre-change run may resume only with a reconstructable explicit effective policy **and** a safety review. If the prior policy is unknown or would write generated activity, typed edges, or interviews into the observed graph, pause for operator review; an explicitly authorized characterization run may use an isolated disposable graph, never the observed graph. Do not treat reconstruction of an unsafe policy as approval to resume it. Work Package 4 later migrates this temporary pin into immutable `RunSpec`.

For this containment slice, `converged=true` requires a versioned minimum successful-decision coverage, minimum effective eligible actor/authority mass, minimum count of valid state transitions, no unresolved provider/infrastructure error, and a stability threshold across only those valid transitions. The exact thresholds are frozen in a fixture policy before implementation; missing data yields `inconclusive`, not convergence.

**Exit gate**

- Forced-failure, all-abstention, no-action, and real-convergence fixtures are distinct.
- No default observed-graph feedback.
- No default simulation-derived probability movement; removing the diagnostic simulation text from the forecast-spine input leaves the committed distribution path unchanged because it is never admitted there.
- No current published artifact is mutated.
- Existing healthy in-flight runs are untouched. Restart/resume tests prove a pre-change run retains its captured compatibility policy and a new run receives the safe policy; no run silently changes semantics on service reload.
- Golden/characterization rows contribute zero rows to production calibration/recalibration, including historical mixed-ledger rows.

**Rollback:** move only newly admitted runs to a reviewed forward-compatible policy mode; do not use a Git revert or ambient `.env` change that restores unsafe defaults or changes existing runs. `legacy_prompt` is limited to deterministic characterization fixtures and never a production fallback.

---

## 10. Work Package 2 — Core domain and predictive-validity contracts

**Purpose:** create stable vocabulary and schemas without switching runtime authority.

Split into 2A record envelope/IDs/hash/time, 2B forecast and target specs, 2C evidence/influence/context contracts, 2D actor/action/checkpoint contracts, and 2E forecast-resolution-evaluation contracts.

**Likely files**

- new `backend/app/domain/__init__.py`
- new `backend/app/domain/ids.py`
- new `backend/app/domain/contracts.py`
- new `backend/app/domain/predictive_validity.py`
- extend `backend/app/services/requirement_spec.py`
- new `backend/tests/test_domain_contracts.py`
- new `backend/tests/test_predictive_validity_contracts.py`

**Contracts**

- `ForecastCase`, `QuestionSpec`, `DecisionSpec`, `TargetSpec`, immutable `RunSpec`;
- base record envelope and canonical hashing;
- `ResearchGeneration`, `EvidenceSnapshot`, `SourceSnapshot`, `EvidenceItem`, `Claim`, and `ClaimEvidenceLink`;
- `InfluenceLineage` with `originId`, `sourceFamilyId`, `influenceClusterId`, `availableAt`, and `epistemicType`;
- `InfluenceConsumption` with target/revision and influence-cluster/version identity, update-operation ID/type, consumed contribution, optional pre-registered contrast and independently identified incremental-information hash, and adjudication lineage;
- `ForecastEvidencePack` and `RunQualityAssessment`;
- `ActorObservationPolicy`, `ObservationEvent`, `BeliefState`, `ActorState`, `ActorActionContract`, `ActionProposal`, `FeasibilityResult`, `ExecutedAction`, and `ActionOntologyMapping`;
- `ExperimentPlan`, `SimulationCheckpoint`, typed `ExperimentResult`, and experiment validity;
- `ForecastDistribution` and `ForecastBundle` with target-owned probability semantics;
- `ForecastClaim`, `ForecastRevision`, `ResolutionEvent`, and `ScoreRecord`;
- `EvaluationCase`, `PromotionStudy`, and `PromotionDecision`.

Internal Python uses `snake_case`; canonical external JSON uses `lowerCamelCase`. Every schema declares aliases explicitly and serialization round-trip tests prove that an internal rename cannot silently alter the wire contract. Hashing uses one versioned canonical-JSON profile with fixed Unicode normalization, number/date encoding, map ordering, and forbidden NaN/Infinity; include cross-process golden vectors and injected clock/ID/random providers.

Target validators are explicit: binary support is `{false,true}`; categorical mass sums to one and obeys declared exclusivity/exhaustiveness; independent binaries never share a normalization constraint; continuous distributions declare units/support/CDF or quantiles and monotonicity; time-to-event declares origin, event, censoring, survival/hazard monotonicity; conditionals declare conditioning-event identity and undefined behavior when the condition fails; multi-horizon targets preserve coherent proposition and ordered horizons. Unknown schema major versions fail; compatible minor additions round-trip without semantic use; removed/renamed fields require a migration adapter.

`InfluenceConsumption` declares two mutually exclusive operation classes. An ordinary update owns the unique slot `(targetRevisionId, influenceClusterVersionId, ordinary)`. A contrast update is admissible only when its `contrastRegistrationId` was frozen before execution, names the same target and cluster lineage, defines exactly one update mapping, and binds an independently derived `incrementalInformationHash`. It must satisfy **two separate** uniqueness guards: `(targetRevisionId, influenceClusterVersionId, contrastRegistrationId)` prevents one registration from being consumed again under a different hash, and `(targetRevisionId, influenceClusterVersionId, incrementalInformationHash)` prevents the same incremental information from being consumed under another registration. A transaction must reject either duplicate, a contrast masquerading as ordinary evidence, or a second ordinary contribution from any descendant of the cluster.

**Red tests**

- invalid target/outcome/resolution contracts currently have no authoritative validator;
- mutable RunSpec changes hash;
- a source and its simulated descendant can currently be represented as independent;
- social event can be labeled institutional action without a mapping;
- missing outcome authority can default to visibility.

**Exit gate**

- Canonical JSON and hashes are deterministic.
- Binary, categorical, continuous, conditional, multi-horizon, and time-to-event targets validate correctly.
- Source-descendant fixture remains one influence cluster.
- Unknown authority/power fails quantitative eligibility.
- Current prompts can round-trip through an explicitly labeled `legacy_prompt_to_question_spec` adapter.
- No production API behavior changes.

**Rollback:** additive unused modules can be removed cleanly.

---

## 11. Work Package 3 — Transactional metadata and immutable artifact stores

Split this package into two commits if necessary: 3A metadata, then 3B artifacts.

### 3A — Metadata store

**Likely files**

- new `backend/app/persistence/metadata_store.py`
- new `backend/app/persistence/migration_runner.py`
- new `backend/app/persistence/migrations/0001_core.sql`
- new `backend/app/persistence/migrations/registry.json`
- new `backend/scripts/migrate_metadata.py`
- `backend/app/config.py`, `.env.example`
- new `backend/tests/test_metadata_store.py`
- new `backend/tests/test_migrations.py`

Use SQLite WAL, foreign keys, busy timeout, explicit migrations, and repository ports. Provide a migration CLI with `status`, `plan`, `apply`, `verify`, and `reserve`; it records checksum, applied time, source commit, and plan hash and refuses an edited applied migration, a duplicate/out-of-order allocation, or a database whose applied version is newer than the code knows. Initial modes: `off|shadow|authoritative`; default `shadow` only after tests pass.

Migration numbers follow **merge order**, not work-package numbers. `0001_core.sql` is the bootstrap migration. Because 16A is an early prerequisite for command admission, the next reviewed slice reserves `0002_workflow.sql` before any other post-core schema. Thereafter, immediately before a schema-bearing slice, the integration owner runs `migrate_metadata.py reserve --owner <slice-id> --slug <name>` on the current clean integration head. The command atomically assigns the next zero-padded number, creates the migration file, and appends `{number, ownerSlice, slug, filename, state}` to `registry.json`. A cancelled reservation remains tombstoned and its number is never reused. Parallel branches may design schemas, but only the integration owner may reserve a number; rebase before reservation and never insert a lower-numbered migration after a higher number has merged or been applied. Tests require every migration to have one registry owner, every registry allocation to have one file or tombstone, strict numeric order, immutable applied checksums, and no startup-time schema creation outside migrations. Thus Work Packages 4, 5, 9A, 6, 7, 8, 13A, 9B, 10, 11, 12, 14, and later schema-bearing slices receive numbers in their actual merge sequence rather than competing for a guessed `0003`.

**Gate:** a reproducible 10-process × 10-writes barrier test completes 100 contending transactions within the registered busy-timeout/SLO with no lost rows, duplicate IDs, partial event pairs, or unexplained lock failures; atomic record+event transaction; migrations are idempotent and refuse an edited/newer schema; online backup uses SQLite's backup API or `VACUUM INTO` with checkpoint verification rather than copying a live WAL database; restore reproduces migration checksums, row counts, hashes, foreign keys, and active-mode metadata.

### 3B — Content-addressed artifact store

**Likely files**

- new `backend/app/persistence/artifact_store.py`
- new `backend/app/domain/artifacts.py`
- new `backend/tests/test_artifact_store.py`

Stage plaintext bytes to a temp file, validate and canonicalize where the media contract permits, compute protected `contentHash=SHA-256(canonical plaintext)` within one tenant/security domain, encrypt with a tenant-scoped envelope key, fsync, atomically place under a tenant-keyed HMAC or ciphertext-digest physical key, then transactionally register both identities. The current single-owner deployment may use an optional tenant field, but must not claim cross-tenant isolation until authentication/authorization/key-boundary tests exist. Reject traversal, symlink escapes, unauthorized existence probes, and cross-domain deduplication.

After rename/place but before metadata commit, a crash may leave an unregistered final object. A CAS reconciler lists these objects into quarantine, proves that no registered reference exists, and deletes only after a retention window; directory metadata is fsynced after rename on filesystems that support it.

**Gate:** same authorized plaintext deduplicates only inside its security domain; plaintext-hash and ciphertext/object-key vectors are deterministic under their declared rules; unauthorized callers cannot test another domain's content existence; mismatches fail closed; interrupted put registers nothing; registered bytes cannot be overwritten; reference counts/legal holds are transactional; crypto-shred removes recoverability; orphan temp and unregistered-final objects are observable, quarantined, and safely reconciled.

**Rollback:** turn shadow mode off. Preserve sidecar databases/artifacts for audit; never auto-delete them.

---

## 12. Work Package 4 — Stable identity, research revisions, and command idempotency

Split into 4A case/spec/run identity, 4B command admission/client idempotency, and 4C research-generation edit/continue semantics.

**Likely files**

- new `backend/app/services/case_service.py`
- new `backend/app/services/research_generation_service.py`
- modify `backend/app/api/research.py`, `backend/app/api/sdk.py`
- modify `backend/app/services/pipeline_orchestrator.py`
- modify `backend/app/__init__.py`, `backend/app/config.py`
- modify generated/API clients and frontend command callers so a logical retry reuses one client-generated key
- new `backend/tests/test_command_idempotency.py`
- new `backend/tests/test_research_generation.py`
- update resume/edit/continue tests

**Implementation**

1. Persist case, typed specs, immutable RunSpec, and Run before legacy launch. RunSpec pins workflow authority, evidence/forecast/publication modes, `simulationForecastEffect`, graph/interview feedback, seed/spine policy, command-admission policy, research-generation ID, capability/provider/tool policy, resolution policy, actor/action mapping versions, renderer/localization versions, and budget/deadline.
2. Add `case_id`, `run_id`, `run_spec_id`, `research_generation_id`, and `active_attempt_id` to the next pure `PipelineState` migration; retain `pipeline_id` as a compatibility alias.
3. Accept a **client-generated** `Idempotency-Key`. Uniqueness is `(owner_or_tenant, command_type, key)` plus canonical request hash in the 16A command store. Same key/same payload returns the same admitted command/response; same key/different payload returns 409. Freeze key TTL and response retention by command class; an expired key never silently aliases a new payload. A server-generated correlation ID is returned for tracing only. This proves admission deduplication, not exactly-once downstream effects; those require outbox and activity fencing gates.
4. Make dossier edits create a new generation, hashes, manifest, editor provenance, and explicit downstream invalidation. Never overwrite the sealed parent generation.
5. Pin continuation to one generation ID.
6. New runs receive explicit lineage references; reverse directory scans are legacy-only diagnostics.

**Red tests**

- 100 concurrent identical start/resume/fork/cancel/edit admissions currently can create more than one command across processes;
- edit then continue currently has no new sealed research identity;
- same idempotency key with a different body is not rejected consistently.

**Exit gate**

- 100 duplicate admissions produce one durable command and stable response; the end-to-end one-business-effect claim remains gated by Work Package 16 activity tests.
- Conflicting payload gets 409.
- Edit → new generation → continue preserves the approved bytes and invalidates exactly the declared downstream stages.
- Legacy clients without a supplied key remain one-shot compatible and receive a generated correlation ID plus an explicit non-retry-safe diagnostic; all first-party clients are migrated before command cutover.
- Retry-aware first-party clients generate and persist the key before the first request; a lost response followed by retry returns the original command. Contract tests cover every external command enumerated in §7.3.

**Rollback:** metadata remains shadow; route only new commands back to legacy. Never make an in-flight run forget its pinned identity.

---

## 13. Work Package 5 — Capability gateway and complete invocation telemetry

Split into 5A parent gateway and 5B child-process spool/import.

**Likely files**

- new `backend/app/domain/invocations.py`
- new `backend/app/services/capability_gateway.py`
- new `backend/app/persistence/invocation_store.py`
- modify `backend/app/utils/llm_client.py`, `backend/app/utils/telemetry.py`
- modify graph LLM adapters
- new `deerflow_bridge/invocation_spool.py`
- new `backend/app/services/invocation_importer.py`
- modify research and simulation child entrypoints
- focused gateway, telemetry, budget, and import tests

**Implementation**

- Capability requests declare task class, schema, sensitivity, cost/latency cap, provider/tool allowlist, reasoning/fallback policy, and deterministic-cache eligibility.
- Persist one logical invocation and all attempts, including fallback, validation, tokens/cost, latency, cache, error taxonomy, and exact input/output artifact IDs.
- Parent passes case/run/task/attempt/correlation IDs to children. Each child writes one checksummed canonical-JSON event per `O_EXCL` temp-file + fsync + atomic rename into an attempt-scoped spool directory; the parent imports by event ID idempotently, quarantines malformed/truncated records, and acknowledges by immutable import receipt. Do not depend on multi-process writes to one JSONL frame.
- Use persistent exact-response cache keys containing owner/security scope, provider/model, prompt/template, parameters, schema, policy, and deterministic eligibility. Cache payloads inherit sensitivity, encryption, retention, legal hold, and deletion; high-sensitivity or nondeterministic requests default ineligible.
- Typed RunSpecs must have finite budgets. Legacy unbounded behavior is labeled `legacy-unbounded`, never silently inherited.

**Exit gate**

- Restart loses or duplicates no invocation events.
- Concurrent calls never fall into a global/unattributed bucket.
- Simulation, research, graph, and report spend appear by task.
- At least 99.9% invocation attribution over a preregistered ≥10,000 synthetic invocation/attempt/event denominator, with every missing event classified; uninstrumented external tools are explicit gaps, never removed from the denominator.
- Billable API reconciliation target is less than 2% variance when provider invoice/export data exists; until then this gate is `awaiting external evidence`, not falsely passed or package-blocking.
- Provider behavior is unchanged in shadow mode: exact normalized request, attempt order, fallback selection, response schema, and error class match the frozen fixture policy.

**Rollback:** disable gateway authority but keep the append-only invocation evidence.

---

## 14. Work Package 6 — Influence lineage, ForecastEvidencePack, and pre-forecast quality gate

Split into 6A lineage/consumption, 6B lane-specific pack construction, 6C quality gate, and 6D market-role/blind-lane policy.

**Likely files**

- new `backend/app/services/influence_lineage.py`
- new `backend/app/persistence/influence_consumption_store.py`
- new `backend/app/services/forecast_evidence_pack.py`
- new `backend/app/services/run_quality.py`
- modify `backend/app/services/pipeline_orchestrator.py`
- modify `backend/app/services/report_agent.py`
- modify `backend/app/services/forecast_extractor.py`
- modify market handoff code
- new focused lineage/evidence-pack/quality tests

**Implementation**

1. Assign source family and influence cluster to evidence, summaries, embeddings, graph projections, research forecasts, market observations, simulation seeds/trajectories, and report restatements. Market observations from the same source family retain distinct quote/capture IDs and timestamps while sharing the correlated family lineage.
2. Build one deterministic pack **per forecaster/context-policy/target lane** before probabilistic judgment. It binds the frozen `EvidenceSnapshot`, records the complete eligible universe, selected and omitted IDs with reason, exact ordered rendered context, transformation/template versions, byte/token spans, truncation/budget decision, market role, experiment validity, and final context hash. Pack overlap metrics—not agent names—measure lane independence.
3. Compute `RunQualityAssessment` after research and after simulation, before forecast construction. Consume research penalties, simulation health, LLM degradation, organic-ratio warnings, decision coverage, and convergence validity.
4. Pass supported structured `quantitative_facts` and explicit experiment fields through the pack rather than relying on accidental prose context. The legacy/research-derived WorldState `base_distribution` remains recorded as diagnostic/no-update and MUST NOT be passed into the auto-anchoring extractor or move a committed distribution. Only a lineage-deduplicated experimental contrast with a registered update mapping may adjust a forecast, and that path remains unavailable until Work Packages 12 and 14 promote it.
5. Require the forecast builder to acknowledge every penalty/warning with block, caveat, or explicit no-effect decision. A numeric quality-based adjustment is itself a forecast policy and remains disabled until outcome-blind promotion.
6. Enforce one market role—`prior`, `feature`, `comparator`, or `resolution_source`—per observation/target, and keep a market-blind lane.

Source-family assignment starts deterministic (canonical publisher/origin, syndicated-content fingerprint, URL/content provenance, and explicit transformation parent). Ambiguous merge/split cases enter a review queue; adjudication creates a new lineage version and never rewrites old consumption records. Persist each `InfluenceConsumption` transactionally with its update operation, consumed contribution, optional contrast registration and incremental-information hash, and adjudication lineage. An ordinary update claims the single ordinary slot for `(targetRevisionId, influenceClusterVersionId)`. A contrast may be consumed only when the registration was frozen before execution, matches the target and cluster lineage, defines exactly one promoted update mapping, and identifies incremental information independently. Enforce separate unique keys on target/revision + cluster/version + registration ID and on target/revision + cluster/version + incremental-information hash, so neither a registration nor the same information can be replayed through a new alias. Duplicate ordinary, registration, or incremental-information keys fail deterministically. A bare non-null contrast ID is never an escape hatch.

**Red tests**

- direct research prior plus WorldState descendant currently appears as two paths;
- removing a duplicated descendant can change the aggregate despite no new contrast;
- quantitative facts and typed base distribution are silently absent;
- quality penalties/warnings can be ignored before report generation;
- market observation can enter multiple roles;
- two forecasters can appear independent while receiving the same rendered context; and
- passing the dormant legacy `base_distribution` would activate an unvalidated anchor.

**Exit gate**

- 100% of forecast-affecting inputs have influence lineage.
- Duplicate descendants do not alter the aggregate unless a registered experimental contrast changes.
- Every forecast revision can enumerate its exact `InfluenceConsumption` records; ordinary and registered-contrast slot uniqueness survives retry, concurrent admission, and replay.
- Enabled-but-omitted inputs fail unless the pack gives a valid reason.
- Invalid/inconclusive simulation produces `no_update`.
- Legacy/research-derived `base_distribution` stays diagnostic and cannot activate extractor anchoring; only a promoted contrast mapping can move probability.
- Pack and quality hashes appear in the forecast candidate and invocation lineage.

**Rollback:** run-pinned `forecast_context=legacy|pack_shadow|pack_authoritative`; never change a run midstream.

---

## 15. Work Package 7 — ForecastBundle v2 and bundle-first authority

Split into 7A schema/dual-write and 7B authority.

**Likely files**

- new `backend/app/domain/forecast_bundle.py`
- new `backend/app/services/forecast_bundle_builder.py`
- new `backend/scripts/backfill_forecast_bundles.py`
- modify `backend/app/services/forecast_extractor.py`
- modify `backend/app/services/report_agent.py`
- modify report/SDK APIs
- new `backend/tests/test_forecast_bundle_v2.py`
- new `backend/tests/test_bundle_first_report.py`

**7A implementation**

- Store TargetSpecs, target-owned committed `ForecastDistribution` records, independent component estimates, per-target priors, per-target ordered revisions, uncertainty diagnostics, resolution specs, pack/quality/evidence/experiment IDs, market role, monitoring, and provenance-completeness profile. Scenario sets and binary collections carry only target references and semantic metadata; they never duplicate probabilities. An immutable bundle may name `supersedes`; reverse `supersededBy` links are catalog projections and never mutate the predecessor.
- Build from current spine in dual-write mode and deterministically project legacy `forecast.json`.
- Historical backfill is allowed only from hash-valid forecast+audit artifacts and remains `legacy-artifact`; never invent claim-level provenance.

**7B implementation**

- Freeze the candidate bundle before narrative generation.
- Give report sections a bounded bundle view.
- Prose may explain but cannot author/mutate probabilities, targets, dates, or resolution criteria.
- Remove prose reverse-extraction as an authority for new runs; retain it only as an explicit legacy recovery adapter.

**Exit gate**

- Probability/outcome-space validation is exact for every target type.
- Independent binaries are never normalized together.
- Every scored probability/density/quantile/hazard exists in exactly one target-owned distribution authority; legacy JSON/report fields are deterministic projections.
- Changing prose percentages cannot change bundle hash.
- Generation refuses publication without a valid bundle.
- Every revision has prior plus evidence/experiment IDs or an explicit no-adjustment record.
- Legacy fixture probabilities remain semantically equivalent under dual-write.
- Backfill `--dry-run/--apply/--resume` manifests reconcile exact source forecast/final-audit hashes, imported/quarantined counts, and deterministic bundle hashes.
- At least ten representative publishable shadow cases across normal, retry, translated, forked, and legacy-artifact profiles show zero unexplained target/probability/resolution mismatch before `bundle_v2` authority is allowed for new runs.

**Rollback:** `forecast_authority=legacy|bundle_v2` is immutable in RunSpec; both artifacts persist during soak.

---

## 16. Work Package 8 — NarrativeIR, external audit, seal, and deterministic publication

Split into three commits: 8A IR, 8B audit/seal, 8C deterministic rendering/localization.

**Likely files**

- new `backend/app/domain/publication.py`
- new `backend/app/publication/narrative_ir.py`
- new `backend/app/publication/audit.py`
- new `backend/app/publication/seal.py`
- new `backend/app/publication/renderers.py`
- modify `report_agent.py`, `report_visualizer.py`, report API/export/translation paths
- IR/seal/deterministic-publication tests

**Implementation**

- `NarrativeIR` contains typed sections, claims, numbers, evidence refs, headings, and localizable semantic fields bound to a candidate bundle hash.
- `AuditRecord` references immutable bundle/IR hashes and never writes into the candidate.
- `PublicationSeal` binds accepted bundle, IR/localization, audit, approver, and policy hashes.
- Localize typed semantic fields before audit. One seal names the exact accepted locale candidates; adding a locale later creates a new localization candidate, audit, and superseding publication seal without changing the forecast bundle. After seal, Markdown/HTML/PDF/charts/API are deterministic and network-free.
- Install an OS/process-level network deny policy and capability gateway denial after seal, not only a monkeypatch. Pin renderer/browser/font packages, locale/timezone, and canonical PDF metadata so stable-hash claims are testable.

**Exit gate**

- Candidate or audit mutation invalidates the seal.
- Rejected candidate cannot seal.
- All formats expose the same accepted hashes and semantically identical propositions, probabilities, dates, and criteria.
- Repeated renderer output hashes are stable for fixed inputs/version.
- Current visual/PDF/bilingual gates pass.

**Rollback:** legacy publication remains read authority until parity; sealed candidate bytes never change regardless of renderer.

---

## 17. Work Package 9 — Evidence/claim ledger and research-generation authority

Split into **9A minimal identity/snapshot ledger** before Work Package 6 and **9B claim-complete research-generation authority** after bundle/publication shadow parity.

**Likely files**

- new `backend/app/domain/evidence.py`
- new `backend/app/services/evidence_importer.py`
- new `deerflow_bridge/evidence_ledger.py`
- new `backend/scripts/backfill_evidence_ledger.py`
- modify fetch/search/research writers
- modify research edit/continue and manifest paths
- evidence-ledger and research-contract tests

**Implementation**

- **9A:** persist immutable `SourceSnapshot`, `EvidenceSnapshot`, `EvidenceItem`, source-family/influence identity, time/geography/entity scope, trust/injection flags, and exact locators sufficient to freeze an eligible universe and build lane-specific packs. Keep this shadow; do not claim full claim completeness.
- **9B:** persist `Claim`, support/refute/context links, contradiction state, and authoritative research-generation projections.
- Research Markdown, actors, sources, quantitative, contested, and timeline files become projections of a sealed `ResearchGeneration` where supported.
- Human edit creates a new generation and projection artifacts, never an in-place mutation.
- Preserve raw source text as untrusted data; it cannot become workflow instruction.
- Do not fabricate spans or claims for historical runs; keep `legacy-artifact` profile.

Work Package 4 owns generation identity, command semantics, sealing, and downstream invalidation. Work Package 9 owns source/evidence/claim content and projection generation inside that identity. “Promoted claim” means a schema-valid claim admitted to a sealed `EvidenceSnapshot` after support/locator/as-of/injection gates; ordinary extracted text remains candidate evidence. Raw snapshots and locators inherit source license, sensitivity, access, retention, legal hold, and deletion policy.

**Exit gate**

- Every new promoted claim links to exact source snapshot and locator.
- As-of leakage, unsupported claim, and prompt-injection fixtures fail closed.
- Source independence is explicit.
- New claim-complete bundles trace every material adjustment to exact claim/evidence IDs.
- Current dossier/UI remains compatible as a projection.
- Backfill dry-run/apply/resume manifests preserve source and fixture hashes, report exact imported/quarantined counts, and never upgrade a historical `legacy-artifact` row to claim-complete.

**Rollback:** ledger remains shadow and current dossier path remains readable.

---

## 18. Work Package 10 — Rebuildable graph, isolated overlays, and prepare fast path

Split into 10A deterministic observed projection, 10B overlay/read gates, 10C actor projection fast path.

**Likely files**

- new `backend/app/services/projections/graph_projection.py`
- new `backend/app/services/actor_projection.py`
- new `backend/scripts/rebuild_projections.py`
- modify `graph_builder.py`, graph runtime/client, `pipeline_orchestrator.py`
- modify `zep_entity_reader.py`, `zep_graph_memory_updater.py`, `simulation_manager.py`
- graph projection/watermark/overlay/prepare tests

**10A implementation**

- Upsert typed entity/identity/relationship/claim/causal records by stable ID.
- LLM-extract only residual unstructured evidence.
- Content-hash episode ledger prevents repeated extraction; dedupe/prune before paid work.
- Build under a new graph/projection ID and swap catalog pointer only after equivalence/watermark passes.

**10B implementation**

- Observed projection is immutable for a sealed snapshot.
- Each experiment gets an overlay namespace keyed by run/simulation/scenario/seed.
- Every write includes epistemic type and lineage. Every read declares snapshot, allowed overlays, and minimum watermark.
- Unqualified graph query fails closed.

**10C implementation**

- Build versioned `actor_projection.json` from sealed actors, relationships, identity records, ontology, graph watermark, and input hashes.
- Normal PREPARE constructs the cast from this projection and performs only targeted selected-actor enrichment.
- Full all-node/all-edge enumeration becomes an explicit repair fallback.

`rebuild_projections.py graph --snapshot-id ... --new-projection-id ...` is the only normal rebuild/repair command. Equivalence compares canonical domain IDs, typed properties, epistemic status, valid/recorded time, evidence lineage, and adjacency after excluding only explicitly registered backend-generated IDs/timestamps. Every exclusion is versioned; unexplained differences block pointer swap.

**Red tests**

- unchanged inputs currently re-extract;
- one seed can affect another shared graph;
- stale graph ID can feed downstream work;
- monkeypatched `get_all_nodes/get_all_edges` breaks normal prepare.

**Exit gate**

- Observed projection is byte/record-equivalent before and after experiments.
- Seed/fork contamination fixture is impossible.
- Unchanged input produces zero residual extraction.
- Duplicate canonical IDs are zero and skipped chunks below 5% for benchmark fixture.
- Normal prepare makes zero all-node/all-edge calls and preserves cast/role hashes.
- Initial performance targets: graph P95 ≤900 s for ≤20 actors/≤100 material claims; prepare P95 <300 s and actor projection load <2 s.

**Rollback:** new graph IDs and actor projection remain shadow; old graph pointer is untouched until cutover.

---

## 19. Work Package 11 — Actor observation/action contracts and valid decision semantics

Split into 11A observation/belief boundaries, 11B action mapping and feasibility, and 11C executed-action environment transition plus round accounting.

**Likely files**

- new `backend/app/services/actor_decision_engine.py`
- modify `actor_role_prompt.py`, `oasis_profile_generator.py`
- modify `simulation_config_generator.py`, `decision_channel.py`, `worldstate.py`
- modify platform runner prompts/input construction
- actor-observation/action/decision/world-state tests

**Implementation**

1. Compile every outcome-relevant actor into an `ActorObservationPolicy` and `ActorActionContract`.
2. Separate attention, agenda-setting, legal authority, jurisdiction, resource capacity, implementation capacity, veto, coalition, mobilization, network brokerage, and information access.
3. Generate each actor prompt only from allowed evidence available at that simulated time.
4. Actors emit structured communication and material action proposals directly; a central model may validate/interpret but not invent hidden authority.
5. Promote executor/social events through a versioned `ActionOntologyMapping` with preconditions and validation evidence.
6. Run feasibility checks for authority, budget/resources, quorum, dependency, jurisdiction, delay, and effect mechanism.
7. Environment transition consumes only `ExecutedAction` plus exogenous events.
8. Account for attempted, valid, abstained, invalid, timed-out, and failed actions every round.

An actor is “outcome-relevant” only when a named TargetSpec mechanism allows one of its typed action classes to affect target state, or when it can veto/enable a required dependency; this registry is frozen in the ExperimentPlan. The central model may extract a proposed field only with source/actor-state IDs and confidence. Missing authority, jurisdiction, resources, or dependency evidence remains unknown and makes the material action quantitatively ineligible rather than guessed from role prose.

**Red tests**

- private information leaks through the common world brief;
- influential but powerless commentator moves an institutional outcome;
- low-visibility authorized actor cannot act;
- social post becomes order/vote/allocation without mapping;
- silent powerful actor disappears from the active roster;
- provider failure/all-abstention becomes convergence.

**Exit gate**

- 100% prompt snapshots pass observation allowlists.
- 100% actors eligible to move outcomes have complete action contracts.
- Missing authority excludes quantitative effect but preserves qualitative hypothesis generation.
- Synthetic commentator/authorized-actor fixtures behave correctly.
- Failed/invalid rounds are no-update and never hard evidence/convergence.
- Every outcome transition links to an executed action and mechanism.

**Rollback:** keep the legacy executor as an explicit diagnostic-only experiment implementation; never mix decision semantics inside one experiment.

---

## 20. Work Package 12 — Optional experiment service, paired forks/seeds, and simulation efficiency

Split into 12A contracts/service, 12B paired isolation/validity, 12C efficiency/adaptive sampling.

**Likely files**

- new `backend/app/domain/experiments.py`
- new `backend/app/services/experiment_service.py`
- modify simulation manager/runner, `pipeline_orchestrator.py`, `ensemble.py`
- modify `backend/scripts/run_parallel_simulation.py`, `backend/scripts/run_twitter_simulation.py`, `backend/scripts/run_reddit_simulation.py`, and the decision engine
- experiment, fork, seed, no-op/placebo, efficiency tests

**Implementation**

- `ExperimentPlan` names uncertainty, hypothesis, treatment/control, immutable inputs, parameters, seed policy, stopping rule, declared estimand, update rule, and validity limits.
- Skipping simulation is a successful workflow outcome.
- Paired forks use one declared intervention, isolated overlays/state, and common random numbers where available.
- Results store seed-level effects/sensitivity and validity; absolute WorldState shares do not corroborate their seed prior.
- Extra seeds run experiment + structured estimate only, not full report; aggregate before one report.
- Start with one seed. Add seeds sequentially only while the estimand remains unstable enough to justify cost.
- Compute one platform-neutral actor belief/action state, then render platform variants deterministically where appropriate.
- Coalesce quiet periods and advance around material events; cache only semantically safe persona/event/feed states.

Before executing an experiment, freeze numerical tolerances for no-op effect, placebo alpha, known-effect direction/minimum recovery, JS divergence, rank stability, seed schedule, and sequential stopping in the `ExperimentPlan`. If the executor exposes controllable randomness, paired arms MUST use common random numbers; otherwise the plan records why not and increases uncertainty rather than claiming pairing. Validate all gates first with deterministic synthetic engines (no effect, known positive/negative effect, provider failure, silence, abstention, delayed action, cross-arm write attempt) before any paid OASIS run.

**Exit gate**

- No-op fork paired effect is within declared numerical tolerance.
- Placebo false-positive rate is within pre-registered alpha.
- Known-effect synthetic cases recover direction and minimum magnitude.
- One arm cannot affect another.
- Invalid result causes no update.
- No full report per seed.
- Simulation model calls fall at least 50% while outcome distribution remains within pre-registered JS-divergence/rank-stability tolerance; P95 target <8 minutes for the representative fixture.
- The eight-minute claim uses the registered benchmark envelope and named fixture/plan hash; it is not transferable to a different actor count, horizon, provider, output contract, or concurrency setting.

**Rollback:** experiment policy selects the legacy executor as diagnostic-only; new records preserve comparison evidence.

---

## 21. Work Package 13 — Transactional forecast revision, resolution, and scoring

**Likely files**

- new `backend/app/domain/resolution.py`
- new `backend/app/services/resolution_service.py`
- new `backend/scripts/backfill_resolution_ledger.py`
- new resolution migration whose prefix is allocated by `migrate_metadata.py reserve --owner WP13A --slug resolution` on the current integration head; never hard-code `0003`
- modify `forecast_ledger.py`, `backtest.py`, SDK resolve API, resolution monitor, scheduled rerun
- resolution/calibration/rerun tests

### 13A — Shadow lifecycle and qualified import

- One stable `ForecastClaim` identity for scenario and independent binary targets.
- Append-only `ForecastRevision`, immutable `ResolutionEvent`, and `ScoreRecord`.
- Target-type-compatible Brier/log/spherical/CRPS/survival scoring adapters.
- Ambiguous, invalid, cancelled, superseded, withdrawn, unresolvable, appeal, and adjudication states.
- Manual resolution and market resolution go through one idempotent service; JSONL and per-report files become exports/projections.
- Scheduled rerun diff compares every target type by stable claim ID.
- Production calibration/recalibration queries exclude evaluation, golden, characterization, draft, rejected-attempt, and unverifiable cohorts by enforced record type—not caller convention.

The scoring registry permits: binary/categorical → Brier, log, spherical; continuous distributions/quantiles → CRPS or predeclared quantile score; time-to-event → predeclared censored survival/Brier/log score; conditional targets → score only under the resolved conditioning contract while retaining all cases in coverage; multi-horizon → one stable claim/distribution per horizon plus an aggregate declared before outcomes. Incompatible target/rule pairs fail closed.

Resolution permissions are explicit: automated resolvers may propose only from allowlisted sources; authorized adjudicators accept/reject/mark ambiguous; appeals create superseding events; corrections never rewrite the prior event. Conflicting market/official sources enter an adjudication queue. Pending, ambiguous, appealed, cancelled, withdrawn, superseded, invalid, and unresolvable targets remain in the all-target denominator with their terminal/temporary reason; only score eligibility differs.

**Backfill**

- Import scenario ledger, binary forecasts, `resolved.json`, `price_track.jsonl`, and market resolutions idempotently only when a row binds to an exact forecast hash and successful publishability/final-audit record.
- Deduplicate by stable `ForecastClaim`/revision identity. Quarantine pre-final-audit drafts, rejected attempts, duplicate appends, and rows whose publication evidence cannot be verified; never let them enter production calibration.
- Unknown target semantics remain unscored; never guess.
- Preserve original forecast hashes.
- `backfill_resolution_ledger.py --dry-run|--apply|--resume --manifest ...` records source/export hashes, publishability evidence, claim/revision mapping, imported/quarantined counts, and score eligibility.

**13A gate**

- Shadow records reconcile to qualified compatibility inputs with explicit quarantine counts and zero invented semantics.
- Duplicate resolve requests produce one shadow resolution/score.
- No database record advances workflow, changes a published forecast, or becomes production calibration authority.

### 13B — Resolution/scoring authority after workflow cutover

After 16D passes, route manual and market resolution commands through the durable command/outbox/activity path for newly eligible claims. Keep compatibility JSONL/per-report files as deterministic exports and pin `resolutionPolicyVersion` in RunSpec.

**13B gate**

- Duplicate resolve produces one resolution/score.
- Incompatible scoring rule is rejected.
- Appeal creates a superseding event without changing original forecast/outcome evidence.
- Scenario and binary targets appear in diff, monitoring, resolution, score, and calibration cohorts.
- All eligible target states contribute to coverage denominators.

**Rollback:** before 16D, the service and imports remain shadow while compatibility files retain read authority. After 16D and the 13B cutover gate, use a forward run-pinned compatibility projection for old consumers; never Git-revert into dual writers or restore draft rows to calibration.

---

## 22. Work Package 14 — Outcome-blind evaluation and policy promotion

Split into 14A isolated case/outcome infrastructure, 14B paired study runner/statistics, 14C immutable promotion service, and 14D prospective collection/decision. Only 14D can end in `Policy promoted`.

**Likely files**

- new `backend/app/evaluation/case_registry.py`
- new `backend/app/evaluation/promotion.py`
- new hidden outcome-store adapter with strict access boundary
- refactor `backend/scripts/golden_eval.py`
- split/replace outcome-bearing eval fixtures
- evaluation leakage, split, paired-score, coverage, and promotion tests

**Implementation**

1. Keep current historical golden cases for characterization only.
2. Create sealed `EvaluationCase` registry with frozen as-of source/market bundles, event-family clusters, model/tool policy, and hashes.
3. Store outcomes physically separately under credentials and filesystem/object-store permissions unavailable to inference workers. Define the threat model: prompt/model/tool code, inference process, cache, logs, filenames, metadata, and frozen bundles cannot read outcome bytes or answer-derived features; only the post-inference evaluator receives both forecast and outcome IDs.
4. Run inference in a network-denied sandbox whose only readable source mount is the frozen bundle. Add a randomly generated sealed outcome-token capability canary: any successful inference-side access, filename/metadata leak, or output reproduction fails the run closed.
5. Group-split event families and near-duplicates before tuning.
6. Run matched arms on identical snapshots:
   - `R`: research only;
   - `RM`: research + market;
   - `RS`: research + blinded isolated simulation;
   - `RMS`: research + market + blinded isolated simulation.
7. Report proper loss, market/base-rate skill, market main effect, simulation main effect, interaction, coverage, cost, latency, and sensitivity.
8. Include every eligible target in the denominator. Report unresolved bounds or pre-registered missingness adjustment.
9. Store characterization/golden scores in an isolated evaluation store. Production calibration, recalibration, and promotion inputs reject that cohort even if historical mixed-ledger exports are imported.

**Promotion statistics**

Define `Δ = mean(loss_candidate − loss_control)` on paired targets; lower is better. Before outcomes, freeze primary score, control, event-family clusters, minimum detectable effect, sample/stopping rule, eligible segments, non-inferiority margins, multiplicity, coverage, cost/latency caps, statistical library/version, numeric precision, and bootstrap RNG seed. Target at least 80% power at family-wise `α=0.05`. Use a 10,000-resample event-family-cluster bootstrap unless an ADR justifies another method.

Promotion requires:

- upper bound of the two-sided 95% CI for primary `Δ` < 0;
- every safety-critical segment within its non-inferiority margin;
- resolution/adjudication coverage at the versioned target, initially recommended ≥90%;
- candidate/control coverage difference within the versioned limit, initially recommended ≤5 percentage points;
- cost/latency caps pass; and
- zero blocking leakage or influence-cluster audit findings.

Sequential looks use alpha spending. Multiple candidates use family-wise or FDR control. Failure of any gate leaves the policy shadow/diagnostic-only.

**Simulation promotion addendum**

Promote only per domain and update-mapping version after prospective R→RS and, where markets are permitted, RM→RMS improvement passes plus seed/actor/parameter/time/source-family sensitivity and structural-validity gates. A cross-domain aggregate win cannot promote a failing domain.

**Exit gate**

- Outcome store is inaccessible during inference.
- Leak canary fails closed.
- Preregistration and snapshot hashes precede outcomes.
- PromotionDecision is immutable and reproducible.
- Existing answer-bearing golden set cannot promote production.
- Passing these code gates yields `Implementation complete`, not `Policy promoted`. The package remains `Study collecting` until its preregistered prospective stopping rule is met; analysis may then yield `Policy rejected` or a domain/version-specific `Policy promoted` decision.

**Rollback:** candidate remains shadow; evaluation evidence is append-only.

---

## 23. Work Package 15 — Adaptive research and report/cost optimization

This package optimizes after lineage and authority are measurable. Never trade away predictive-validity gates for speed.

### 15A — Active research/value-of-information controller

**Likely files:** new `deerflow_bridge/research_policy.py`; modify research budget/search/cache/synthesis and orchestrator; focused policy/fanout/synthesis tests.

- Normalize query, URL, and content fingerprints across lanes/processes.
- Persist positive and negative cache results.
- Rank gaps by expected claim/uncertainty change per cost.
- Stop after bounded consecutive no-new-source/no-gap-shrink passes.
- Synthesize structured evidence packets capped by policy rather than replaying huge raw contexts.

Targets: normalized repeated query <2%; repeated no-result <5%; at least one verified source per three searches; synthesis input ≤150k characters; adaptive tail ≤20% of run spend; research token P95 ≤15M for the named substantial-case fixture whose question, target count, evidence-gap count, source-policy, provider/model, and output hashes are fixed in the benchmark manifest.

### 15B — Report generation

- Generate one forecast/evidence/citation/NarrativeIR before sections.
- Bound evidence per section and render deterministic tables/charts/references.
- Repair only changed invalid spans.
- Add forecast draws only when measured instability and decision value justify them.

Targets: report P95 <600 s, ≤80 model calls, <750k prompt tokens, with equal or better semantic/audit gates.

### 15C — Graph/prepare/simulation targets

Re-run Work Packages 10 and 12 performance harnesses. Do not claim improvement without distributional/semantic parity and full invocation attribution.

**Benchmark envelope**

Every performance claim pins machine model/CPU/RAM, OS/runtime/dependency lock, provider/model and reasoning policy, fixture and input/output hashes, cache state, network mode, concurrency, worker count, budgets, output-quality gates, warmup policy, iteration count, and percentile window. Run at least one cold-cache and one warm-cache lane; publish every sample and use enough iterations for the stated percentile to be meaningful. A faster result that changes the evidence snapshot, output contract, provider, quality gate, or concurrency is a different experiment, not a speedup.

**Exit gate**

- 15A meets query/no-result/source-yield/context/tail-spend targets on the frozen benchmark without lower evidence coverage or worse paired score.
- 15B meets report latency/call/token targets with identical bundle/seal semantics and non-inferior audit results.
- 15C meets the previously registered graph/prepare/simulation targets with full invocation attribution and declared distributional/semantic parity.
- Each candidate has a run-pinned `legacy|shadow|authoritative` mode, forward rollback, cost guard, and independent review.
- Any candidate that can change a probability remains shadow until Work Package 14 promotes that exact policy/domain version; cost-only wins cannot waive predictive gates.

**Rollback:** `RESEARCH_POLICY=legacy|voi_shadow|voi_authoritative` and equivalent run-pinned modes; hard budget ledger remains authoritative during shadow.

---

## 24. Work Package 16 — Durable workflow shadowing, activity extraction, and authority cutover

Split this into many commits. Do not combine the database state machine, all stage extractions, and cutover.

### 16A — Task/attempt/event/outbox shadow store

**Likely files**

- new `backend/app/workflow/contracts.py`
- new `backend/app/workflow/store.py`
- new migration `backend/app/persistence/migrations/0002_workflow.sql`
- modify `models/task.py`, `pipeline_orchestrator.py`
- workflow store/outbox/lease tests

Tables include Task, TaskAttempt, lease/fencing, command, event, cancellation, deadline, budget, review hold, and outbox. Transition + outbox commit atomically. Legacy controller still advances runs while parity is measured.

The ADR freezes lease duration, heartbeat cadence, monotonic-clock/skew behavior, reclamation grace, outbox poll/backoff/dead-letter policy, queue priority/backpressure, maximum attempts, cancellation propagation SLO, and rolling schema-version compatibility. Workers may hold transient execution/lease state in memory but no **authoritative** workflow state.

**Gate:** cross-process lease exclusivity, expired-lease reclamation, stale-fence rejection, atomic transition/event, durable cancellation, and prior attempt preservation.

### 16B — Stateless worker/activity envelope

Add worker and activity ports. A worker leases a task, loads registered inputs, heartbeats, executes, registers outputs, and commits with its fencing token. It owns no workflow state in memory.

**Gate:** SIGKILL at pre-start, running, output-written, and pre-commit leaves a recoverable task; restart recovery <60 seconds; cancellation reaches subprocess/model/tool work within declared SLO.

### 16C — Extract one stage per commit

Fixed order:

1. research activity;
2. ontology activity;
3. graph projection activity;
4. prepare activity;
5. experiment/run activity;
6. forecast-bundle activity;
7. narrative/audit/publication activities.

For every activity, add a test that delivers it twice, kills between output write and commit, and submits a stale fencing token. It must use declared artifact IDs only, return the same successful result for the same idempotency key, preserve prior attempts, and never advance the next stage itself.

### 16D — Authority cutover for new runs

API emits commands; DB/outbox/workers advance new runs. File state becomes a read projection. Move schedule, retry, cancel, review-hold, deadline, and budget semantics.

Update backend status APIs, generated clients, and frontend hydration/event handling in the same slice. A browser acceptance flow creates a new safe fixture run, observes task/attempt history, retries one command with the same key, cancels a disposable task, restarts the service, and verifies the same durable state—without touching any healthy pipeline or making network/model calls.

**Cutover gate**

- ten representative shadow replays named by fixture/manifest hash, spanning normal, deterministic failure, retry, kill/restart, cancel/resume, fork, publication/localization, and resolution, with zero unexplained transition/artifact mismatch;
- 100 duplicate-command chaos test;
- kill at every transition;
- stale worker, two-worker, partial-artifact, cancel/resume, and restart tests;
- one durable state surface drives API/UI;
- no in-flight legacy run is converted.
- backup/restore covers SQLite metadata/migration journal, CAS registrations and bytes, compatibility projections, graph pointer/watermark, pending outbox rows, leases/tasks/timers, and run-pinned policy modes; recovery resumes from the same authority without duplicate effects.

**Rollback:** drain new queue; DB-authority runs remain on DB; route only newly created runs back to legacy. Never let legacy resume a DB-authority run.

---

## 25. Work Package 17 — Indexed reads, compatibility retirement, and final soak

### 17A — Indexed read paths

Replace hot-path directory scans and polling with metadata queries/events. Cover pipeline/report lists, by-simulation lookup, market handoff, artifact discovery, and status hydration. A deep filesystem scan remains an explicit repair command.

**Gate:** 10,000-run list/status P95 <50 ms; zero O(N) full deserializations in normal path; artifact scan I/O <1% wall time; deterministic index rebuild matches catalog.

### 17B — Compatibility deletion

Delete one proven-dead path per commit:

- reverse identity scans;
- process-local workflow authority;
- legacy forecast semantic extraction;
- duplicate manifests/state;
- report-owned probability mutation;
- unscoped graph feedback;
- obsolete JSONL write authority.

Split giant modules only after ports are stable. Do not mix behavior rewrite and module movement.

**Soak gate before deletion**

- at least two releases or twenty representative new runs;
- includes normal, failure, kill/restart, cancel/resume, fork, translation, and resolution paths;
- zero unexplained shadow mismatch;
- backup/restore drill succeeds;
- all current-history compatibility views pass;
- independent architecture review finds no critical or important open issue.

A “release” means a deployed version with immutable source/policy hashes and at least one completed representative case; the soak still requires the full scenario matrix and cannot be satisfied by two empty releases. The independent review uses §7.1 `review.json`; any `blocking` or `important` finding keeps deletion disabled.

**Rollback:** every deletion has an independently tested forward compatibility switch or adapter restoration. Do not use a Git revert that reactivates an unsafe writer or creates dual authority; immutable records/artifacts remain compatible.

---

## 26. Migration and backfill protocol

Every migration script MUST:

1. default to `--dry-run`;
2. print deterministic input/output counts and hashes;
3. support checkpoint/resume;
4. be idempotent;
5. write no secrets or raw sensitive payloads to logs;
6. make a verified metadata backup (SQLite backup API or `VACUUM INTO`, never a raw copy of a live WAL database) plus referenced-file hash manifest before apply; and
7. re-run validation and reconciliation after apply.

Rules:

- Copy, validate, canonical-plaintext hash, encrypt, and verify bytes into the tenant/security-domain CAS, then register protected content and physical object identities. Never mutate originals or expose a cross-domain plaintext-hash oracle.
- Never infer missing source spans, attempts, target semantics, influence clusters, or outcomes.
- Historical forecast bundles remain `legacy-artifact` unless exact claim lineage truly exists.
- Graph migration builds a new projection ID and swaps a pointer after equivalence/watermark gates.
- Workflow cutover applies only to new RunSpecs.
- Every authority mode is pinned in RunSpec, not re-read from ambient configuration mid-run.
- Preserve JSONL and file artifacts as exports until compatibility retirement.

Recommended run-pinned modes:

```text
metadataMode
artifactCatalogMode
evidenceAuthority
graphProjectionVersion
forecastAuthority
publicationAuthority
workflowAuthority
commandAuthority
researchGenerationId
simulationForecastEffect
graphFeedbackPolicy
interviewFeedbackPolicy
forecastContextPolicy
actorObservationPolicyVersion
actionOntologyMappingVersion
experimentExecutorVersion
invocationPolicyVersion
resolutionPolicyVersion
evaluationPolicyVersion
rendererVersion
localizationVersion
```

---

## 27. Program-wide fitness dashboard

| Area | Target |
|---|---|
| Contract/lineage | 100% task inputs/outputs registered; 100% forecast inputs selected/omitted with disposition; 100% revisions carry influence lineage |
| Graph isolation | Zero cross-seed/fork reads/writes; observed projection unchanged by experiments; stale watermark always rejected |
| Forecast semantics | No probability authored from prose; invalid experiment always no-update; stable claim ID across full lifecycle |
| Prepare | P95 <300 s; zero all-node/all-edge enumeration in normal path |
| Graph | P95 <900 s for representative scale; <5% skipped; zero duplicate canonical IDs |
| Report | P95 <600 s; ≤80 calls; <750k prompt tokens; no audit regression |
| Research | Repeat query <2%; repeated no-result <5%; ≥1 verified source/3 searches; synthesis ≤150k chars; token P95 ≤15M on the named substantial-case fixture |
| Simulation | ≥50% model-call reduction within pre-registered distribution tolerance; P95 <8 min fixture |
| Workflow | Exactly one effect under 100 duplicate requests; restart recovery <60 s; zero stale-fence commits |
| Telemetry | ≥99.9% invocation attribution; billable invoice variance <2%; no silently unbounded typed run |
| Publication | Zero post-seal model/tool/network calls; all formats carry the same accepted hashes |
| Evaluation | Outcome inaccessible during inference; leak canary blocks; paired prospective promotion gate enforced |
| Resolution | Transactionally idempotent; original forecast immutable; all-target denominator and coverage explicit |
| Indexed reads | 10k run list/status P95 <50 ms; normal path has zero full-tree scans |

Targets are initial architecture SLOs. Change them only through a versioned benchmark/ADR, never by weakening a failing test ad hoc.

---

## 28. Global stop conditions

Stop the current package and mark it `Blocked` if any of these occurs:

- dirty-worktree ownership is ambiguous;
- the current code contradicts this plan and the consequence is not understood;
- migration dry-run and apply counts/hashes differ unexpectedly;
- a shadow semantic/artifact/state comparison fails;
- an artifact declared required by the current authoritative/new-path package is unregistered, or its required projection watermark is stale (legacy undeclared artifacts outside that slice remain migration findings, not automatic global blockers);
- duplicate/lease/fencing/kill tests fail;
- a backup cannot be restored;
- a candidate-visible evaluation input contains outcome text;
- simulation arms share generated state;
- an invalid experiment still changes a forecast;
- the task would require changing authority for an in-flight run;
- the only proposed validation is a paid run that has not been explicitly authorized; or
- provider/budget health fails an approved paid gate.

Do not work around these conditions with a fallback, fail-soft default, or manual file edit. Diagnose, record evidence, and resolve the invariant first.

---

## 29. Decision log

Append decisions; do not rewrite history.

| Date | Decision | Rationale | Consequence |
|---|---|---|---|
| 2026-07-17 | Recommend database workflow authority with SQLite WAL workstation implementation | Fits the local-first modular monolith and avoids dual workflow authorities; repository ports preserve a PostgreSQL path | Work Package 0 must ratify or replace this in an ADR before runtime work |
| 2026-07-17 | Keep files/JSONL as compatibility projections during migration | Existing recovery/history/UI depend on them | Authority switches require shadow parity and run-pinned modes |
| 2026-07-17 | Disable shared simulation graph feedback before building overlays | Current untagged writes can contaminate observed evidence and other seeds/forks | Generated activity remains in run artifacts until safe overlays exist |
| 2026-07-17 | Treat simulation as diagnostic until prospective incremental skill is proved | Current research-prior reuse, social-action laundering, and evaluation leakage make quantitative authority unearned | Experiment results can generate hypotheses but default to no probability movement |

---

## 30. Surprises and discoveries

Append implementation discoveries with evidence. Seed entries:

- The measured local critical path is dominated by research, graph, and prepare rather than the simulation loop.
- The explicit WorldState `base_distribution` anchor is configured on but dormant in the current production spine call; implicit signal-pack influence remains active.
- Ensemble aggregation is a post-seal sidecar and does not currently rewrite the authoritative published forecast, despite older comments implying otherwise.
- The existing golden evaluator scores outputs but does not execute or enforce the as-of pipeline, and its fixture contains outcome-bearing text.
- The actor-role contract is richer than the metadata used by the current decision channel.

---

## 31. Required end-of-package handoff

For every completed package, append to `handoff.md`:

- request/package and exact scope;
- base commit, branch/worktree, and dirty-state ownership;
- requirements → acceptance evidence table;
- failing-before test and observed failure;
- files changed and why;
- schema/migration/authority decision;
- focused/full tests with exact command and exit code;
- shadow parity hashes and performance measurements;
- rollback flag/procedure and whether exercised;
- independent review findings/disposition;
- residual risks and next uncompleted package; and
- explicit statement that no healthy pipeline was restarted and no unauthorized paid run was launched.

The program is not complete because the code compiles or a report renders. It is complete only when the final soak demonstrates that the system's probabilities are lineage-complete, experiments are isolated and valid, publication is immutable, resolution closes the loop, predictive policies pass outcome-blind prospective gates, and the old authorities can be removed without losing history or recovery.
