# Latest-three-run forensic evidence — 2026-07-10

**Captured (UTC):** 2026-07-10T17:03:43Z  
**Base revision:** `27b785a1831f343662d5bc5cea37e93ef308483c`  
**Worktree status fingerprint:** `sha256:9180fb91e5c13de8d84f1038fb4e406600fd4e5b823c1694bea67f566b872f4a`  
**Coordinator:** root agent  
**Method:** three independent, read-only forensic lanes followed by coordinator source/artifact verification

This is the durable **pre-backfill frozen snapshot** for `CODEX_LOOP_ENGINEERING.md`. It normalizes the agents' July 10 findings. Later explicitly backed-up repair passes rewrote live report paths; current policy-v3 disposition and hashes are appended below rather than silently replacing this capture.

## 1. Frozen evidence inventory

| Artifact | SHA-256 |
|---|---|
| `backend/uploads/pipelines/pipe_f23527f7d903/pipeline_state.json` | `d35ebcbe44d251b120b9d1d23546efca5935bf11d23782e2bc5f8aaa6fac1bbc` |
| `backend/uploads/pipelines/pipe_f23527f7d903/run.json` | `4c56590a6ddb5e1691cbfbe9229a331a16dfab1128979d76243f5e0858bea2be` |
| `backend/uploads/pipelines/pipe_f23527f7d903/run_telemetry.json` | `a952a3790fec998f1d64bf78a8b41f6189972cf0303a6250daf0f4a0f5c2f240` |
| `backend/uploads/pipelines/pipe_0f2bee7bd649/pipeline_state.json` | `49c2613e96e0b26ec9ec7f023f9105bcb706a0af2b5af86c89596f63e322a85a` |
| `backend/uploads/pipelines/pipe_0f2bee7bd649/run.json` | `7c6dcab2d1760fe4a594efb4dc5a63f01af5afae111f917a1c6c4508e1d35b23` |
| `backend/uploads/pipelines/pipe_a8986bffd918/pipeline_state.json` | `f36d15ac57d76ea6efc93bb92b7a66b2cc6a4b2ceee2331e18d21c17c0a633ec` |
| `backend/uploads/pipelines/pipe_a8986bffd918/run.json` | `6e62cee986ddb8b5d5306e7126b112397c75c7cebafb838047293bbd21453a8e` |
| `backend/uploads/simulations/sim_cbbacd1a27a9/run_state.json` | `554fc6a9670ee31961c0ed21651677f83a389a1b6fc9703ca9a23531896354a6` |
| `backend/uploads/simulations/sim_cbbacd1a27a9/run_summary.json` | `c8b52fb7eba2ead326aff63f967baa35b32cba650f931886c48001dc541fc7eb` |
| `backend/uploads/simulations/sim_cbbacd1a27a9/simulation.log` | `c96139d7e39ea3f02c9f5cae47ac8a0fb1a4b9b1e0f9bec7b47e0015184c9484` |
| `backend/uploads/reports/report_1b70ace5c9e8/forecast.json` | `8933065b1202fb04a85fadc6aa81416536442936cdc1ca948639ac69ab282e32` |
| `backend/uploads/reports/report_1b70ace5c9e8/full_report.md` | `73f69a138d570fd8d0503e8cc20b1cba8342b6a862d0032f6300b5a8abc35109` |
| `backend/uploads/reports/report_a03be154febc/forecast.json` | `508d9bedcc3358360ea80f3b5f7b329345759e31038b3fafeaca78be141f4ff6` |
| `backend/uploads/reports/report_a03be154febc/full_report.md` | `d99585825df32f0bf3bb2e2fbebf2858bc911f3839b81cb77c0ce9d52cd576c2` |

These hashes describe the July 10 capture only. Later transactional repairs updated live paths after complete backups, so the frozen rows are immutable evidence—not current-path fingerprints. Every later replay must record its own hashes and restore on failure.

## 2. Lane A — run and stage lifecycle

| Finding | Runs/evidence | Current code path | Coverage and deterministic replay |
|---|---|---|---|
| Exit-zero is not completion evidence | `pipe_a8986bffd918`; `sim_cbbacd1a27a9` has 0/36 rounds, zero actions, no platform completion, disk refusal at 1.61 GB, yet persisted `runner_status=completed` | `SimulationRunner._monitor_simulation` | LOOP-001 adds a fake-process/preflight fixture: missing enabled-platform `simulation_end` must be `FAILED`; complete events remain `COMPLETED` |
| Simulation result and command process are separate lifecycles | Valid runs leave the child alive for report interviews after platform end events | `run_parallel_simulation.py` command-mode loop; `SimulationRunner._read_action_log`, `_monitor_simulation`, and `cleanup_all_simulations` | LOOP-002 replays cleanup followed by the real monitor classification and requires terminal status/timestamp/error to remain unchanged |
| Completed report can be orphaned into failed pipeline | `pipe_0f2bee7bd649` and `pipe_a8986bffd918` have complete primary reports but failed after restart during post-report work | `PipelineManager._salvage_completed_report`, `PipelineManager.reconcile_orphans`, `PipelineOrchestrator._run_seed_ensemble` | Wave 9 has salvage/checkpoint code; replay a copied completed report with interrupted ensemble and require completed/degraded, never false failed |
| State truth is split | Runner state, simulation-manager state, pipeline stages, summary health, and UI messages can disagree | `run_state.json`, `state.json`, `pipeline_state.json`, `run_summary.json` | Introduce one terminal-transition contract; every replay must compare all persisted views |
| Telemetry can disappear across process/thread boundaries | Graph shows zero attributed calls despite multi-hour work; crash/restart loses late stage flushes | `run_telemetry.json`, stage telemetry context, thread/subprocess handoff | Require every attempted graph/research/report call to have a stage or explicit `unattributed` owner and flush on terminal/reconcile |

## 3. Lane B — research, ontology, and graph

| Finding | Runs/evidence | Current code path | Coverage and deterministic replay |
|---|---|---|---|
| Gap loop failed to converge | Same 20 gaps repeated for six passes | `deerflow_bridge/deerflow_research.py::_merge_gaps` and `advance_gap_set` | Wave 9 code is present; replay recorded gap sets and require strict advance or an explicit no-progress stop |
| Search useful-result yield is poor | In `pipe_f23527f7d903`, 1,428 of 3,391 search operations returned no result within 4,974 total tool calls; 442 calls used `{}` arguments | research search wrapper/cache/breaker in `deerflow_bridge` | Count valid result-bearing calls, not calls issued; reject empty arguments before the tool boundary |
| Track citation IDs collide | Each track finalizes local `[S1]…`; merge does not globally remap them | research track finalization and dossier/report merge | Merge two fixtures where `[S1]` points to different URLs; output must have stable globally unique source identities and correct references |
| Source credit can outlive content validity | Nine truncated hosts were marked reachable | source finalization/reachability checks in `deerflow_research.py` | A URL receives credit only after canonical URL, successful reachability, and nonempty claim-bearing content checks |
| Graph work is slow and incompletely accounted | `pipe_f23527f7d903` graph took about 8.63 h; 278/466 chunks skipped; failure counts differ by 44; telemetry attributes zero calls | `GraphBuilder`, Graphiti runtime, graph stage health | One ledger row per chunk with exactly one final class: succeeded, skipped with typed reason, or failed after retries |
| Graph scope is not yet a hard key-actor cap | Wave 9 pruner defaults to 400 entities, but keeps core+two-hop and prunes only certain low-degree disconnected nodes | `backend/app/services/graph_pruner.py::prune_graph`; `GRAPH_MAX_ENTITIES`; `GraphBuilder.get_subgraph` | Seed key actors, related and unrelated components; persisted graph and default UI payload must both obey the declared cap and retain all protected actors |
| Ontology→actor handoff is semantically weak | Actors can retain generic types not present in the generated ontology, creating duplicate domain nodes | ontology artifact, `backend/app/utils/actors.py`, graph seed/ingest | Every actor type must map to a valid ontology type before ingestion; invalid mappings fail or use one explicit fallback |
| Actor dossier→simulation behavior was generic | Graph/persona selection could omit a researched actor or let a generic persona dilute its goals/incentives | `actors.json`, `actor_role_prompt.py`, `simulation_manager.py`, `oasis_profile_generator.py` | LOOP-011 now reconciles eligible missing actors, compiles distinct roles, seals exact OASIS runtime fields, and validates them at runner start; Graphiti type alignment remains a separate open issue |
| Pagination/LOD address symptoms but need final proof | Historical pagination duplicated edges and full SVG work lagged zoom/pan | `zep_paging.py`, graph runtime, graph API, `GraphPanel.vue` | Compare unique node/edge IDs across pages and record frame/render budget against the default constrained payload |

## 4. Lane C — simulation, report, visualization, and product

| Finding | Runs/evidence | Current code path | Coverage and deterministic replay |
|---|---|---|---|
| Hollow simulation fed authoritative report prose | `report_1b70ace5c9e8` followed zero-action `sim_cbbacd1a27a9` | runner terminal state, orchestrator health gate, report simulation inputs | LOOP-001 blocks this exact terminal class; a separate health replay must strip low-signal simulation evidence or mark research-only |
| Report describes the machinery instead of the outcome | Repeated “Simulation Agent”/dynamics narration and agent-action sections in latest reports | report prompts, signal pack, `report_lint.py` | Final report fixture must contain outcome/driver language and zero forbidden simulation-process narration outside a clearly labeled methodology note |
| Structured probabilities contradict | In `report_1b70ace5c9e8`, equivalent forecasts include 53% versus about 75–76%, and 10% versus about 19% | `_audit_numeric_consistency`, `_finalize_structured_forecast`, binary extraction/final mutation order | Join scenarios and binaries by explicit proposition ID; >1 point mismatch blocks publication and names both values |
| Marker presence is not semantic grounding | Numeric backfill inserted 111, 139, and 233 markers; narrow sources are reused broadly | `_finalize_citations`, citation backfill, forecast extractor grounding audit | An unrelated source sharing only a year/number must not be attached; each claim must retain a source-support decision |
| Visualization generation and delivery can diverge | Producer writes schema-v2 with HTML/PNG fallbacks; historical API/UI expected other shapes; report inputs omit several chart builders | `ReportVisualizer.build_all`, report chart collection, `/viz-manifest`, `ForecastReport.vue` | Generate a manifest, pass it through report→API→UI/PDF, and require every visible item to render or carry an explicit skip reason |
| Timeline and actor network were non-rendering text artifacts | Latest report chart folders contain `.mmd` files not rendered by delivery surfaces | Plotly replacements in visualizer/skill and chart manifest | No `.mmd` is referenced by final Markdown; Plotly HTML and static PNG fallback both exist and resolve |
| Progressive endpoints are unreachable from pipeline mode | Pipeline persists `report_id` only after blocking generation returns | report-stage orchestration and `/sections-partial` | With a blocking fake report agent, persist ID before generation and poll a completed partial section while generation is still blocked |
| Final audit is premature | Final visible/structured mutations historically occurred after quality audit | report assembly/finalization ordering | LOOP-010 policy v3 seals exact Markdown and forecast bytes, requires scenario/proposition/citation contracts, and prevents post-audit ensemble mutation of the primary forecast |
| Degraded completion is hidden | Completed/degraded pipeline is shown as ordinary done and cannot be resumed | `ResearchView.vue`, pipeline health options, resume guard | Completed+degraded fixture shows reasons and a retry/force-resume action |
| Resolution feedback is incomplete | `_year_end` uses the first year; ledger has scenario rows but no headline binary rows | `forecast_ledger.py::_year_end` and ledger append | Multi-year horizon resolves to its explicit final date; scenario and binary forecasts are both idempotently stored and scored |

## 5. Verification caveat and next evidence command

Focused Wave 9 and simulation tests pass, but the full backend suite is not green: it reaches printed 100% and remains alive during interpreter finalization. `/usr/bin/sample` identified an event-loop `kevent` thread and an `os.read` thread. The next iteration must first isolate the smallest hanging test module and save the sample/thread inventory under this evidence directory.

The authoritative simulation gate is:

```bash
cd backend && uv run python -m pytest -q \
  tests/test_audit_fixes_runner.py \
  tests/test_simulation_runner_throttle.py \
  tests/test_drf2_simulation.py
```

The bounded full-suite diagnostic protocol is defined in `CODEX_LOOP_ENGINEERING.md`; an elapsed timeout is a failure artifact, never a pass.

## 6. Resolution update — LOOP-003 through LOOP-011

The findings above are the frozen baseline. The following deterministic refinements were subsequently implemented and verified; detailed evidence is in each `LOOP-00X/result.md`:

| Baseline finding | Resolution now present | Remaining live proof |
|---|---|---|
| Graph sprawl and browser lag | Actor-centered 400-entity physical cap, 150-per-type bound, verified deletion postcondition, 400-node UI overview, deduplicated/orphan-free payload | Comparable graph wall time, database bytes, and browser frame timing |
| Repeated/empty research calls | Shared cross-process attempt/search/fetch budgets, negative-result TTL, formal budget ledger, compact canonical actor dossier | Cost/useful-result delta on a newly authorized live run |
| Track citation collisions | URL-identity global remap, one merged References ledger, unresolved-marker stripping | Semantic entailment remains conservative by design |
| Simulation-mechanics narration | Outcome-first prompt blocks, forecast-share scenario comparison, deterministic final lint, leakage-health gate | Generation path is guarded; current policy-v3 replay still finds two flags in `report_1c312b400d33`, so the legacy report remains withheld |
| Unsafe numeric citation invention | Unique numeric-plus-lexical repair plus source-specific material-number/text support, concentration, and unverifiable-ratio gates | This is bounded fail-closed support validation, not a claim of general semantic entailment |
| Visualization delivery divergence | One v2 manifest through report/API/UI/PDF, request-time containment, CSP sandboxing, stale-asset rejection | Browser E2E on a new report |
| Broken timeline/actor text artifacts | Plotly HTML + inspected PNG fallbacks, numbered timeline key, collision-planned key-actor labels, canonical scenario probabilities | Responsive/print observation across additional screen sizes |
| No live parallel-track console | Bounded chronological merge of `track_N/research_progress.log`; frontend revision polling and autoscroll | Observe in browser during one newly running pipeline |
| Percentage jumps to 90% | Phase-bounded per-track estimator plus monotonic equal-weight aggregate capped at 95 pre-merge | Last-three replay passes; new live observation remains |

Historical policy-v1 backfill produced 9, 8, and 9 chart sets; 283, 189, and 361 inline markers; and 38, 25, and 25 referenced sources. Those measurements are migration evidence only. Policy-v3 publication now withholds the reports, so they are not “delivered” artifacts.

### 6.1 Current policy-v3 report disposition (2026-07-11)

| Report | Current Markdown SHA-256 | Current status | Transactional replay finding |
|---|---|---|---|
| `report_a03be154febc` | `6c94d29e5ae01a667fcf6e0629bccc9d68763b09c1d8b33c7156bbfce2e19c8a` | Not publishable | Quantitative citation coverage 0.02 < 0.75 and two extreme-stat findings; `restored: true` |
| `report_1c312b400d33` | `b54be9f81a4aca28ed37ec12bafe2984c3282f1dcabdb65ddc8f84edd0a3be5e` | Not publishable; partial | Two internal-process/mechanics leakage flags; `restored: true` |
| `report_1b70ace5c9e8` | `fe533a40184837cdfe60334b56146bfe629fb36ae2949acfd8d7f81278f0514e` | Not publishable | One binary/scenario proposition contradiction; `restored: true` |

Pre-replay diagnostics explain why superficial marker counts were insufficient: `a03be` checked 266 markers with 178 unsupported, 85 unverifiable, and S2 used 50 times; `1c312` checked 157 with 106 unsupported, 47 unverifiable, and two leakage flags; `1b70` checked 359 with 272 unsupported, 84 unverifiable, S1 used 52 times, and S2 used 35 times. Stored legacy audits are schema v1/policy null and cannot satisfy policy v3.

### 6.2 LOOP-011 actor-role evidence

Behavioral roles derive from the DeerFlow `actors.json` dossier/ontology handoff, not Graphiti's `ontology.json` type schema. Persona generation consumes only the allowlisted `ActorRoleContract` encoded as JSON inside explicit untrusted-evidence markers; recursive guards cover dossier values and keys plus prompt-facing names, types, and usernames. Bounded prompts retain exactly one ordered evidence boundary with actions, red lines, and evidence inside it. Deterministic tests cover graph omissions, ambiguous and short names, sparse actors, Unicode/control-text attacks, imperative injections, prompt caps, distinct actor hashes, exact Reddit `persona` and Twitter `user_char`, profile/cast/roster/state fingerprints, invalid platforms, missing state, and paired profile/manifest tampering. The final independent 204-test seam matrix passed with no actionable P0/P1/P2 finding. No paid/live run was authorized, so a new production `*_profiles_roles.json` is deliberately not claimed.

The frozen `metrics.json` is preserved byte-for-byte as historical provenance. `metrics-corrections-2026-07-11.json` records that 4,974 was one run's total tool-call count rather than a cross-run search denominator, and that the historical 82.71M token value does not reconcile with the currently retained 48,837,873 telemetry-row sum.
