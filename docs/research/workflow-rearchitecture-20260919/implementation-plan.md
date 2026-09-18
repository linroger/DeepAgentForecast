# DRF-RX-20260919: incremental workflow rearchitecture

**Status: approved by the user on 2026-09-19.** The user selected “Approve the incremental plan” in response to the explicit approval request. Source study and implementation now proceed one verified slice at a time. This plan does not claim that the entire project has been rearchitected or exhaustively behaviorally reviewed.

## Objective and scope

Improve the full research → ontology → graph → prepare → simulation → report workflow, including the Python modules in `backend`, `deerflow_bridge`, `drf2`, and the deployed DeerFlow backend/harness. Preserve existing forecast semantics, source provenance, actor access rules, compatible saved runs, and user-visible artifact links while reducing repeated work and making recovery explainable.

The dedicated branch is `codex/workflow-rearchitecture-2026-09-19`, initially based on main `ec29ab1`. The original dirty checkout remains untouched. Implementation must first reconcile the separate ASTRA improvement branch; neither branch is assumed to subsume the other.

The target remains a modular application with one durable pipeline owner. Stage engines have explicit inputs, outputs, cancellation, budget context, and completion receipts. Extract existing code behind compatibility facades before changing algorithms. `drf2` remains an optional integration surface until the complete production contract and acceptance suite are demonstrably equivalent. No new infrastructure dependency or wholesale framework migration is needed for the first slices.

## Invariants

1. Output integrity, input freshness, ownership, and quality are separate admission checks. A valid output hash cannot prove that its upstream inputs are current.
2. A stage is published only after required output bytes and its generation receipt are durable. A modern failed publication cannot be reclassified silently as permissive legacy state.
3. Regeneration never overwrites the only accepted generation. Persist an owned invalidation decision before downstream work; retain old artifacts for diagnosis and rollback.
4. Scenario forks may read a base generation but cannot overwrite its handoff, graph, project, context, or forecast. Regeneration uses an owned generation.
5. Research source receipts, summaries, actor knowledge, modeler-only information, simulated events, and forecast judgments remain distinct evidence classes.
6. Both enabled simulation platforms must consume the admitted actor/configuration generation and emit validated completion evidence. An exit code is not sufficient.
7. Every report entry point receives the same resolved research context. Reuse and ensemble caches include the input generation and effective policy.
8. A provider dispatch has an immutable operation identity. Retries, cache hits, partial responses, missing prices, and estimated usage remain explicit. No unsupported hard dollar-cap or invoice-accuracy claim.
9. Configurations that affect meaning are captured for the run or stage. Secrets are referenced through existing provider mechanisms and never copied into public manifests.
10. Each implementation slice has a focused failing scenario, passing result, independent review, and one coherent commit before the next slice.

## Ordered slices

| Slice | Concrete outcome | Main ownership | Dependencies and stopping check |
|---|---|---|---|
| RX-00 | Reconcile existing proven improvements into an agreed implementation baseline. | Git integration; ASTRA tests and evidence; current main linear-research changes. | Review actual conflicting hunks, preserve branch-specific behavior, rerun relevant offline scenarios. No blind wholesale overwrite. |
| RX-01 | A pure stage dependency and reuse planner returns explicit reuse/rebuild/reject/legacy decisions without running a pipeline. | New narrow `backend/app/services/pipeline_contracts.py` and `pipeline_reuse.py`; coordinator adapter; focused tests. | Same input snapshot gives the same result; no provider, graph mutation, subprocess, or saved-state write. Initially advisory only. |
| RX-02 | Bind ontology generation to effective research inputs and make its publication strict. | Ontology boundary in `pipeline_orchestrator.py`, artifact contract helpers, ontology tests. | Reuse accepts unchanged bytes and input identity; changed report/dossier/actors/prompt/policy invalidates; corrupt modern proof rejects; old unbound data is explicitly legacy. |
| RX-03 | Persist owned dependency invalidation before rebuilding graph, preparation, run, report, or ensemble. | Pipeline state/manifest helpers, project and simulation identity checks, fork handling. | A rebuilt graph cannot reuse old PREPARE; crash recovery resumes the same invalidation decision; empty new derived outputs cannot expose stale old files; base fork artifacts remain byte-identical. |
| RX-04 | Give research execution, evidence collection, synthesis, actor reception, and handoff publication distinct modules. | Tracked bridge modules and coordinator research-runner helpers. | Preserve CLI arguments, stdout protocol, cancellation, lane/budget/checkpoint identity, extraction artifacts and existing golden fixtures through mechanical extraction. |
| RX-05 | Make research resume/cache/concurrency operate on explicit task and evidence identities. | `linear_research.py`, `research_budget.py`, `cached_fetch.py`, research runner. | Replayed completed tasks make zero duplicate fixture calls; changed source/policy invalidates only the dependent task; denied or unreceipted evidence remains excluded. |
| RX-06 | Resume graph ingestion from acknowledged episodes and avoid demonstrably redundant extraction. | `graph_builder.py`, `graphiti_client/runtime.py`, graph receipt store and tests. | First reuse existing ASTRA receipt fix; then prove timeout/restart preserves successes, uncertain sends stay uncertain, and structured-ingestion changes preserve actor/readback/evidence coverage. |
| RX-07 | Centralize prepared and consumed simulation context; reuse canonical preprocessing across platforms and reports. | `simulation_manager.py`, config/profile/role modules, `run_parallel_simulation.py`, runner. | Exact actor identity, access denials and both-platform prompt attestations survive; seed and primary paths use the same completion/usage rules; seeded outputs stay semantically unchanged. |
| RX-08 | One source-aware report context resolver serves pipeline, ensemble, API generation and chat. | `report_agent.py`, `api/report.py`, coordinator report constructors; focused context tests. | Both main and fork runs deliver the same quantitative/contested/timeline/source/actor context to every entry point; malformed or wrong-owner inputs fail explicitly. |
| RX-09 | Reuse accepted report sections and publish complete report generations. | Report planning/generation, forecast extraction, visualization/translation/export boundaries. | Crash after a section reuses only an identical accepted section; dependency change invalidates it; final report, forecast, charts and citations belong to one published generation. |
| RX-10 | Consolidate provider accounting and admission policy, including atomic token/dollar holds where price coverage permits. | Existing ASTRA usage ledger, LLM client, native model overlays and child transport. | Concurrent sends cannot spend the same planned allowance; replay is idempotent; uncertain outcomes and unpriced routes stop or remain explicitly uncovered under the chosen policy. |
| RX-11 | Extract remaining stage adapters and add read-only diagnostics for source/reuse/budget decisions. | Coordinator façade, stage services, existing API/status projection; native adapter/deployment boundary. | Characterization suite shows unchanged lifecycle and artifacts; diagnostics trigger no execution; existing imports remain compatible. |
| RX-12 | Establish explicit `drf2` parity and deployment contracts before any cutover. | `drf2/driver`, MCP engine adapters, tracked overlay/package manifests. | Portable config, strict schemas, compatible stage gates, cancellation/recovery and full result parity; cutover remains a separate decision with rollback. |

This ordering is the default critical path. RX-08 can be implemented as a bounded independent slice after RX-00 when it does not depend on the new generation format. Do not implement several architectural slices simultaneously merely because their filenames differ; shared contracts must settle first.

## Proposed boundary contract

Add a small internal representation instead of replacing every existing JSON document immediately:

- `RunIdentity`: pipeline ID, attempt ID, base/fork owner, mode, and schema version.
- `ArtifactRef`: logical role, owner, generation ID, relative path under an allowed root, content digest, bytes, schema, and epistemic class.
- `StageInputSnapshot`: sorted upstream references plus a digest of the effective semantic policy. Include normalized prompt, relevant source/actor/ontology identities, graph identity, prepared config seal, scenario overlay, language, seed, and report policy only where they affect that stage.
- `StageDecision`: reuse, rebuild, reject, or legacy-unverified; explicit reasons and the affected downstream closure.
- `StageReceipt`: the input snapshot identity, required/optional outputs, completion and quality evidence, timestamps, and producer version. Write outputs before the receipt and make the receipt the publication boundary.

Use canonical serialization and existing SHA-256/atomic-write utilities. Do not hash secrets, wall-clock progress, temporary paths, or irrelevant UI fields into semantic identity. Include policy/schema versions so intentional behavior changes invalidate the correct results. Distinguish exact byte identity from normalized semantic equivalence explicitly; never silently substitute one for the other.

Legacy reading remains supported by a dedicated adapter. It returns `legacy-unverified` where freshness cannot be proven and follows a documented user-visible reuse policy. It must never manufacture producer proof from whatever bytes happen to be present.

## First implementation unit after approval

**RX-00 followed by the advisory portion of RX-01.** First agree and verify a baseline that contains the current main research engine plus applicable ASTRA safety/recovery work. Then implement only the pure stage dependency model and its read-only decision function. Runtime execution remains unchanged in this first unit.

Acceptance scenarios:

1. A complete valid generation returns reuse for every stage and performs zero external operations.
2. Changed research report, actor dossier, actor claims, or source bindings selects the appropriate ontology/graph/preparation/run/report closure.
3. Changed ontology invalidates graph and descendants; a report-language-only change does not rerun research or simulation.
4. Changed scenario overlay invalidates the fork's preparation and descendants while preserving the base generation.
5. Missing output, malformed receipt, owner mismatch, wrong graph/config binding, and corrupt digest produce explicit rejection or rebuild reasons according to the contract.
6. A legacy run with no modern receipt is reported as legacy-unverified, never modern verified reuse.
7. Planning is deterministic and leaves source fixtures, saved-run paths, graph stores, subprocess registries and provider mocks untouched.

RX-02/RX-03 activate selected planner decisions only after their invalidation/publication scenarios pass. The planner's existence alone is not completion of the rearchitecture.

## Verification and performance evidence

Use fixture-bound offline tests with a guarded Python entry point on macOS so multiprocessing cannot re-enter pytest. Select tests from the actual changed source tree; never test the original checkout accidentally through an absolute import path. Temporary run roots and network-denial hooks cover subprocesses as well as the parent. Preserve known pre-existing failures and xfails with exact scope and timestamps.

For every slice, capture before/after observable results: provider/tool dispatch count, source and claim identities retained, accepted/rejected stage entries, raw artifact hashes, recovery decisions, and relevant wall-time measurements. Measure performance only on the same deterministic fixture and environment. Synthetic local improvements are not production savings or forecast accuracy.

Broader gates run once after a coherent integrated change. UI builds are needed only for UI changes or a modified API contract consumed by the UI. Live provider runs, service deployments, and production cutover are separate acceptance activities and are not implied by offline results.

## Rollout and rollback

Keep existing public function/class imports through compatibility façades. Introduce additive schemas and explicit version checks. First observe the proposed reuse decision beside the existing path on fixtures; then activate one boundary at a time. Preserve old generation directories and record ownership before changing pointers. Never delete the only accepted output during a migration.

Each slice is revertible independently until a documented schema transition; after a schema transition the old binary must reject newer state rather than rewrite it. Deployment parity checks operate against temporary copies first. Do not run the coordinator's mutating deployment-sync helper against the user's active DeerFlow tree during tests.

## Decisions included in approval

Approval accepts incremental migration on this new branch, one state owner, backward-compatible readers, explicit artifact/input contracts, the above invariants, offline-first verification, and one implemented slice at a time. It authorizes the source changes described here but does not authorize paid runs, service restarts, saved-run mutation, deployment, or immediate `drf2` cutover.

The existing project feature list remains canonical and is not reordered or marked passing by this planning work. New acceptance detail lives in the rearchitecture issue ledger until an approved implementation explicitly extends the harness.
