# History, branch reuse, and recurring workflow packaging

**Status:** Complete read-only source study. **Window:** 2026-08-20–2026-09-19, Asia/Shanghai. **Observed:** 2026-09-19.

Only this report was written. No implementation, asset creation, automation changes, provider calls, service starts, saved-run mutation, commits, or publication occurred. The parent owns PLANS.md and the overall 880-Python-file inventory. This report does not claim full detailed review of those 880 files. Its complete comparison scope is the union of paths changed on either branch since their common ancestor, with additional targeted unchanged files and local assets listed below.

## Decision for the approval plan

**Keep `ec29ab1` as the target baseline and reconcile ASTRA's completed slices into it in dependency order. Do not replace the baseline with the older ASTRA branch. Do not regard a clean textual merge as contract integration.** ASTRA's usage, launch, compaction, access, ontology and graph-receipt safeguards already exist; rebuilding them would duplicate work. The target also contains a newer linear research route and September 18 GLM reliability changes that ASTRA lacks.

**Create no new workflow asset now.** Existing DRF forensics/recovery, HMS setup and paper-review playbooks cover the strongest recurring candidates. The ASTRA heartbeat already exists and is currently **PAUSED**. A narrow contract-tracing checklist is a possible extension to the existing research skill after this study, rather than a new architect agent or a second automation. It needs a stable, validated procedure and overlap review first.

### Compact packaging shortlist

Frequency counts below are observed lower bounds; separate commits or automated iterations are not falsely presented as independent user requests.

| Repeated workflow | Supporting evidence and dates | Frequency / confidence | Smallest form | Decision and value |
|---|---|---|---|---|
| Reconcile expensive/slow DRF runs, attempts and ensemble overlap | “Analyze ASTRA workflow performance”; Sept 7–8 accounting iterations; Sept 16 all-successful-run audit | Multiple dated iterations in one task; high | Reuse existing skill | `drf-run-cost-forensics` already defines scope, lineage, delta accounting, uncertainty and stop conditions. No duplicate. |
| Check restart/reuse identity before continuing a DRF pipeline | Same task, Sept 8 unresolved-operation recovery, Sept 13 ontology reuse, Sept 16 graph receipts | At least 3 distinct recovery-related slices; high for need, not evidence of 3 requested live resumes | Reuse existing recovery skill plus local contracts | Recovery audit already exists. July monitor history is outside this window and is not counted toward recurrence. No new monitor. |
| Trace data/context from producer through consumer and expose missing handoffs | DRF Sept 7, 11, 13 and current Sept 19 study; “Rearchitect HMS memory system” Sept 13–14; “Improve AI translation prompts” Sept 9 | At least 3 project tasks; high recurrence | Possible extension to `source-command-research-codebase` | Existing research and review workflows cover the general activity. A per-boundary identity/permissions/failure/replay worksheet could help, but do not package a broad architecture agent. Stabilize this study's minimal worksheet first. |
| Configure HMS memory provider, model and coding-agent MCP access | “Rearchitect HMS memory system”: Sept 13 CLI/MCP request; Sept 14 setup/model-selector follow-ups | Two related dated surfaces in one task; high recurrence likelihood | Reuse existing skill | `hms-agent-connectivity-setup` exists in memory skills and has source/test references, key-handling boundaries and TUI checks. Its restricted invocation flags do not mean it is absent. |
| Score/compare papers against a venue rubric | Two visible “论文评分” tasks dated Sept 7 and Sept 8; prior packaging summary records three requests across those dates | At least 2 primary task entries; third is memory-derived; high | Reuse existing skill | `paper-review-evidence` is present and addresses rubric/version/score-label evidence and missing supplements. No manuscripts need copying. |
| Improve Chinese explanations without wrong-sense/stale-cache delivery | “Improve AI translation prompts”, Sept 9 | One observed project task; medium recurrence likelihood | Skip new asset; reuse Swift review tools | Prompt, cache and async identity are project-specific. A second comparable application or completed reusable regression procedure is needed before extracting a distinct asset. |
| Direct localhost entry, launch uncertainty and UI readiness checks | ASTRA launch slice Sept 8 and workspace/readiness slice Sept 16 | Two related verified slices; high | Existing ASTRA automation and project tests | No second scheduled UI checker. Existing ownership, stopped browser policy and paused schedule must survive. |
| Academic figures, teaching visuals and narrative refinement | September task-title metadata includes figures, teaching images and narrative work | Multiple adjacent requests; only title-level evidence; low confidence in one common procedure | Skip | Image/PDF/document tools already exist. Similar subject matter is not a stable reusable input→procedure→output contract. |
| Finance, communication and personal administration | August 22–September 19 task-title inventory contains finance questions, letters, posting and troubleshooting | Broad activity, no confirmed repeated procedure; low | Skip | No deeper private content was needed. Do not infer trading advice, outbound messages, account actions or scheduled personal monitoring from adjacent titles. |

## Evidence order, privacy and coverage limits

1. **Recent task evidence first:** `list_threads(limit=40)` exposed four current Codex tasks in the window, including the parent. Bounded recent-turn reads were requested for ASTRA, HMS and SwiftMandarin. The returned ASTRA page reports September 8 turns even though task metadata is updated through September 17; latest ASTRA implementation status therefore comes from Git/source/handoffs, not from assuming that page is the newest complete execution history. Returned messages and commands were used only for task/date/procedure evidence, not copied wholesale into this report.
2. **Memory second:** targeted registry ranges and the ASTRA/HMS rollout summaries below supplied pointers, not current truth. The September 13 ASTRA summary predates September 16 work. Swift memory is corroborative of the single September 9 task, not an additional occurrence.
3. **Chronicle:** no Chronicle capability was exposed in the enabled tool catalog. No Chronicle history was read and no off-Codex behavioral coverage is claimed. ChatGPT title metadata is discovery evidence only.
4. **Existing assets last:** relevant local SKILL.md files, custom-agent descriptions/instructions, and automation metadata were inspected. An archived-task inventory was added as a completeness check: five September HMS entries include a greeting and repeated architecture prompts, with no basis to count them as five completed workflows. Older July entries were excluded. The archive page had no further cursor.

No `.env`, credential store, provider profile, saved research prompt, manuscript, raw provider payload or private report body was read. Git comparison inventories include demo/artifact filenames structurally; that does not mean their contents were reviewed. No tests ran in this task: test names/source and prior verification receipts are evidence of existing coverage, not fresh passing gates.

## Branch lineage, conflicts and ownership

| Item | Verified state |
|---|---|
| Original source | `/Users/rogerlin/Downloads/DeepResearchForecast`, branch `main`, HEAD `ec29ab1d4b9a74ae9f0bea1b4644e4582a3a4d6f`; dirty frontend/document cleanup, progress changes and untracked cleanup/evidence files belong to other work. |
| Parent study worktree | `/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919`, branch `codex/workflow-rearchitecture-2026-09-19`, HEAD `ec29ab1d4b9a74ae9f0bea1b4644e4582a3a4d6f`. |
| ASTRA worktree | `/Users/rogerlin/.codex/worktrees/drf-astra-improvements`, branch `codex/astra-workflow-review-2026-09-07`, HEAD `019a7e4c67c1569b031316447aaab8298ed45618`; clean when inspected. |
| Common ancestor | `4be3ce43d3972c0d976efe7b8b97fcdc033c6e34`; `git rev-list --left-right --count ec29ab1...019a7e4` = **2 / 34**. |
| Baseline-only commits | `d75da29` (linear research v2 and GLM reliability), `ec29ab1` (demo gallery). Preserve both. |
| Changed-path scope | ASTRA: **202** paths; baseline: **20** paths; union **219**; shared paths **3**. |
| Shared paths | `backend/app/services/pipeline_orchestrator.py`, `backend/app/utils/llm_client.py`, `deerflow_bridge/deerflow_research.py`. |
| Text conflict preview | Read-only `git merge-tree --trivial-merge 4be3ce4 ec29ab1 019a7e4` emits one conflict hunk in the orchestrator and one in the LLM client. This is a three-tree preview, not a real merged checkout or semantic validation. Bridge changes overlap by file without a conflict marker in this preview. |
| Source ownership | Original dirty cleanup is separate from ASTRA UI changes. Never restore deleted legacy views or overwrite the new ResearchView/router by copying the whole tree. Reconcile target UI intent explicitly. |

**Concrete reconciliation seams.** Baseline `S/backend/app/services/pipeline_orchestrator.py:1416` deploys `linear_research.py`; ASTRA adds compaction/accounting helper deployment in the same area. Preserve both deployment sets. Baseline `S/backend/app/utils/llm_client.py:263` adds explicit request timeout configuration; ASTRA `A/backend/app/utils/llm_client.py:274` disables hidden SDK retries and `:442` records physical attempts. Preserve timeout and per-attempt accounting together, including injected client options. Baseline `S/deerflow_bridge/deerflow_research.py:16031` dispatches the linear route; ASTRA's compaction latch/typed receipt guards live in its other research path. Passing the old bridge suite cannot establish coverage of the new route.

**New route is a concrete integration gap, not proof that all durable evidence is lost.** `S/deerflow_bridge/linear_research.py:492` replaces old tool bodies in place with a notes-reference string, while the mini-agent records only a 200-character preview per tool in its working memory (`:567`–`:576`). This function does not produce or validate ASTRA's exact removed-message receipt. Other source-ledger artifacts may retain fetched content; those are distinct from an exact conversation-compaction receipt. `_Gateway.invoke` (`:165`–`:195`) retries broad exceptions, increments local prompt counters after successful calls, and enforces limits after the response. Do not represent ASTRA's durable backend API reservation or stop-latch coverage as automatically applying to this direct `model.invoke` route. Scope and design that extension separately after approval.

### Automation ownership and stale status

Live configuration: `/Users/rogerlin/.codex/automations/astra-workflow-improvement-loop/automation.toml:2` identifies the existing heartbeat; `:6` is **PAUSED**; `:8` targets `01a07c47-3e0a-7f00-bd84-951add1d8233` (“Analyze ASTRA workflow performance”). Its hourly schedule remains configured, and `:10` records update time **2026-09-17 12:14:56.613 UTC**. Its prompt (`:5`) owns work in the ASTRA worktree, requires checking active ownership, forbids duplicate slices/automations, and preserves the user's stopped-browser policy and offline restrictions. It was not changed or resumed.

`A/astra-improvement-state.json:3` still gives a September 9 update timestamp and `:4` says `active`, while `:538` names implementation commit `4935dc3` and `:797` points to `ASTRA-MARKET-02`. These fields have different freshness: use Git and per-slice receipts for implementation, automation.toml for schedule status, and the ledger for issue identities. Do not restart a loop merely to reconcile labels. The current parent study supersedes any assumption that a pending ASTRA slice is unowned.

## Already-existing improvements and contracts to retain

Here `A/` means the ASTRA worktree at `019a7e4`; `S/` means committed baseline `ec29ab1` in the source checkout. All numbered anchors were checked against those source trees. Historical validation is labelled separately from current source inspection.

| Existing slice / implementation commit | Producer → consumer contract and failure/recovery behavior | Concrete source and existing coverage |
|---|---|---|
| Compaction archive and typed derived evidence, `ccb88d2` | Exact removed message dictionaries, summary, thread/message identity and envelope commit before removal. Retries with identical identity/content are idempotent; mismatches or storage failure stop. Consumer reopens archive read-only and checks exact content; a summary is derived evidence, never fetched-source authority. Shared stop latch protects provider admission and parent fan-in. | `A/deerflow_bridge/research_compaction.py:145`, `:185`, `:218`; `A/deerflow_bridge/patches/middlewares/summarization_middleware.py:410`; `A/deerflow_bridge/patches/middlewares/model_concurrency_middleware.py:30`; bridge `deerflow_research.py:6888`, `:17224`. Tests: `test_research_compaction.py:116`, `:193`; `test_astra_compaction_bridge.py:102`, `:247`; `test_research_compaction_stop.py:15`. |
| Durable launch intent, `06dc8b9` | Immutable request hash and initial pipeline snapshot are reserved transactionally before dispatch. Same key/request returns same pipeline without dispatching again; changed request conflicts. Corrupt/unavailable store is not permission to launch. Crash after admission retains identity; recovery must explicitly distinguish admitted work from dispatched work. | `A/backend/app/services/launch_intents.py:117`, `:153`; `A/backend/app/api/research.py:113`, `:148`, `:185`. Tests: `test_launch_intents.py:63`, `:114`, `:149`, `:176`, `:190`, `:210`; API/launch-state/UI files inventoried below. |
| Durable usage and incremental observations, `8eaa71b`, `099bdbe` | Run/source/operation identity merges positive cumulative deltas once. Replays do not re-add full totals; attribution conflicts are errors. JSON telemetry remains a compatibility projection of durable cumulative accounting; unbound callers retain legacy attempt semantics. | `A/backend/app/utils/usage_ledger.py:438`–`:468`; `A/backend/app/utils/telemetry.py:713`. Existing `test_usage_ledger.py`, `test_durable_pipeline_usage.py`, `test_research_stream_usage.py` (structural test inventory only). Stream-specific behavior is supported by commit/ledger evidence; its entire patched client was not read here. |
| Physical attempts, unresolved recovery, detached child usage, `60925d1`, `d2b706c`, `af5d2a1` | Durable in-flight marker precedes actual SDK send; response usage settles before content validation. Transport uncertainty remains unknown, malformed usage becomes accounting error, restart cannot reinterpret either as zero spend. Child/shared-ledger and sidecar compatibility are existing dependent pieces, not optional when reusing this subsystem. | `A/backend/app/utils/llm_client.py:442`–`:529`; `usage_ledger.py:444`, `:472`. Existing tests: physical-attempt, SDK-boundary, unresolved-ledger/pipeline and simulation-shared-usage suites. Child implementation is structural scope in this review; retain its tests during reconciliation. |
| Scalar budget projections and token holds, `99eac2a`, `04c981b` | Planned prompt estimate plus explicit completion cap are held under the same operation identity; serialized admission compares spent+held+planned. Active policy cannot drift; aggregate observations cannot own API holds. Old schema remains usable without reservations, rather than being silently upgraded during reads. Unknown usage is not free capacity. | `A/backend/app/utils/usage_ledger.py:551`–`:598`, `:701`; `llm_client.py:462`, `:507`. Existing `test_token_reservations.py`, `test_api_token_reservations.py`, `test_budget_totals.py` (structural inventory). |
| Native output policy, `4de6d4f` | Opt-in output policy reaches native model construction and child launch, preserving uncapped defaults and same-simulation identity. This is not a blanket guarantee for all provider paths. | State/commit corroborated; `A/backend/app/utils/oasis_output_policy.py`, `oasis_llm.py`, `simulation_usage.py` and native-output tests are structural scope here. Sept 8 task summary reports 67 new cases and pinned launch policy; not freshly rerun. |
| Immutable price quotes and recorded coverage, `98ed4b7`, `f647bfb` | Provider/model-bound quotes cannot change during replay. Dollar-enabled covered dispatch requires current pricing plus complete recorded price history before writing markers/holds. Legacy opaque history, unquoted/unpriced/non-API usage remains explicit. Coverage is about recorded operations; exact usage and invoice accuracy are not asserted. | `A/backend/app/utils/usage_ledger.py:404`–`:443`, `:636`–`:677`; `llm_client.py:472`. `test_cost_coverage_boundary.py:21`, `:46`; API-cost and coverage-ledger/status suites. Monetary reservation is still missing. |
| Shared actor denial, `ef4f795` | Validated participating actor packs produce public shared rows. Literal `actor_knows=false` on a normalized claim in any pack overrides public duplicates across all packs, independent of order. Authorized actor-local knowledge and modeler evidence remain available; packs are not rewritten. | `A/backend/app/services/simulation_config_generator.py:1649`–`:1730`; negative test `test_actor_shared_access.py:144`–`:192` also verifies pack/seal equality and public positive controls. |
| Complete sanitation before sampling/chunking, `9ab6677` | Sanitize full document before ontology head/middle/tail sampling and graph chunking. Unicode/replacement expansion cannot silently erase safe tail evidence. Presentation bounds still apply at consumer boundaries. More retained graph evidence may increase calls; no speedup is implied. | `A/backend/app/services/ontology_generator.py:703`–`:720`; `pipeline_orchestrator.py:1269`–`:1303`; sanitizer `actor_role_prompt.py:133`. `test_ontology_context_transfer.py:28`, `:90`, `:117`. |
| Cached ontology integrity fence, `c3008bf` | Owner/current manifests, ontology bytes/schema and project ontology must agree on one byte snapshot. Reject before downstream work; preserve invalid artifacts for diagnosis. Reuse completion does not reread and bless a replacement. Explicit opt-out, missing legacy project-only artifacts and hashless metadata retain existing compatibility semantics; none proves input freshness. | `A/backend/app/services/pipeline_orchestrator.py:9805`–`:9910`, `:9046`, `:12238`, `:12644`. `test_ontology_reuse_integrity.py:133`–`:159`, `:210`, `:228`, `:243`, `:256`, `:298`. |
| Direct research workspace/readiness, `45a8da8` | Existing route/readiness/startup changes and tests must be considered alongside original dirty cleanup. Broad UI behavior was not independently re-executed or visually reviewed here. | `A/docs/research/astra-workspace-verification.json:1`; all 13 listed source hashes match current files. Historical evidence: 131 frontend passes, build exit 0; backend 3,957 passes, 1 known failure, 17 skips, 11 xfails. No production deployment proof. |
| Graph timeout acknowledgements, `4935dc3` | Per-call immutable snapshot partitions input indexes into acknowledged UUIDs, started/unacknowledged, and never started. Runtime confirms owned async cleanup before snapshot handoff; builder validates graph/count identity, accounts failures once and stops before further batches/publication when cleanup is unconfirmed. No automatic replay, durable restart checkpoint, provider cancellation or DB rollback guarantee. | `A/backend/app/services/graphiti_client/batch_receipts.py:1`–`:127`; runtime `:242`–`:277`, `:1106`–`:1136`; builder `graph_builder.py:2377`–`:2416`, `:2483`. `test_graph_batch_receipts.py:289`, `:308`, `:413`. |

Test paths in this table without a directory prefix are under `A/backend/tests/`. A file listed as structural-only below is not upgraded to detailed review by this table.

### Historical verification versus fresh checks

The September 16 graph receipt record (`A/docs/research/astra-graph-receipt-verification.json:1`) reports 144 focused passes, 21 new permanent cases, 109 independent-review passes and full backend **3,978 passed / 1 failed / 17 skipped / 11 xfailed**, with zero recorded network events and 143 guarded Python processes. The known failure is `tests.test_drf2_skills_config.TestConfigYaml::test_skills_path_points_at_drf2_skills`. This is not an all-green gate. Its six source hashes were independently recomputed in this study and all match. Thirteen workspace/readiness source hashes also match the September 16 receipt. These hash checks confirm unchanged referenced source, not that the tests still run successfully today.

September 9 aggregate results produced by the unguarded multiprocessing launcher are explicitly invalid (`A/docs/handoff/astra-integration/handoff.md:55`). Future approval should require the guarded offline harness with fresh phase-specific receipts. Do not inherit an invalid aggregate or silently mark known failure/xfails as passing.

## Ranked, source-proven next work for PLANS.md

No item below is implemented here. Each can be a separate approval slice, with invariants and rollback/recovery stated before code edits.

| Rank | Narrow proposed work | Mechanism/evidence | Scenario acceptance and stopping condition |
|---|---|---|---|
| 1 — P1 integration prerequisite | Reconcile existing ASTRA safeguards into the ec29ab1 baseline in dependency order | Two textual conflict hunks; three shared files; newer route and timeout on baseline. Copying ASTRA HEAD wholesale loses newer main work. | Mock both research dispatch routes and API clients. Preserve baseline timeout, helper deployment and GLM/linear dispatch; same launch key produces one dispatch; retries have separate attempt IDs; legacy outputs remain compatible; actor-denial and ontology invalid-reuse scenarios stay intact. Stop when scoped merged changes and applicable offline suites pass with known baseline debt explicit. |
| 2 — P1 route-contract closure | Define linear-engine compaction/admission coverage explicitly | `S/deerflow_bridge/linear_research.py:492` mutates messages without the ASTRA receipt; `:165` admits before local post-response checks. This is a distinct path, not evidence that the old ASTRA repair failed. | Synthetic long tool result: retained source/receipt identity is recoverable after restart; archive write failure removes no evidence and permits no later dispatch. Concurrent near-cap calls and provider timeout must produce explicit known/unknown attempt outcomes. Keep current phase artifacts and caller output shapes. Choose receipt/admission semantics in the plan before implementation. |
| 3 — P1 existing gap | Bind ontology reuse to effective inputs before designing dependent invalidation (03b then 03c) | Existing guard explicitly reports `input_freshness=unverified` at `A/backend/app/services/pipeline_orchestrator.py:9818`; owner/child manifests only establish artifact consistency. | Identical bytes+policy reuse without rewriting proof; changed research/dossier/policy cannot reuse stale ontology. Fork ownership and ensemble dependents are enumerated. Interrupted invalidation preserves old artifacts and a durable recoverable state; no mixed-generation GRAPH/PREPARE/RUN/REPORT reuse. Legacy compatibility must be explicit. |
| 4 — P1 report context | Unify structured artifacts at alternate report entry points and chat, without swallowing internal constructor TypeError | `A/backend/app/api/report.py:191` passes four dossier fields; seed report `pipeline_orchestrator.py:8871` uses a reduced kwargs set and catches TypeError at `:8887`; `report_agent.py:10898` formats chat from requirement/report head/tools without constructor research context. | Place distinctive source-bound markers in structured research beyond the 40k report head. Primary, seed, direct API and chat paths retain relevant evidence and actor restrictions. Inject internal TypeError: surface it once; do not retry a reduced-context constructor. Old optional-field callers continue under a tested adapter. |
| 5 — P1 monetary admission | Pin run-wide price/cap policy and atomically reserve token+dollar estimates (04b5b3b2) | `A/backend/app/utils/usage_ledger.py:636` intentionally does not serialize priced work; token rejection commits policy at `:593`. Recorded price coverage is not remaining-dollar reservation. Existing record documents two mock calls consuming $1 against $0.75. | Barrier-synchronized requests compete for one allowance: reject second before physical send, leave no orphan hold when either token or dollar capacity rejects, keep unknown response liability visible, reject same-run policy drift, settle/replay idempotently. No billing-accuracy promise. |
| 6 — P2 durable graph recovery | Extend attempt-local graph receipts only after input/policy identity and ambiguous-write reconciliation are designed (06b; deadlines 06c later) | `A/backend/app/services/graphiti_client/batch_receipts.py:13` states acknowledgement is a returned UUID; unknown writes remain possible. Runtime `:1114` notes same-name DB uniqueness is not guaranteed. | Crash before/after DB write and receipt durability; replay acknowledged work zero times, do not blindly replay ambiguous work, reject different text/ontology/policy identity, preserve actor-seed/source reference time, stop safely on storage failure. Measure per-episode deadlines separately after this correctness boundary. |
| 7 — P2 continuity accuracy | Reconcile status ownership in existing records during a future authorized documentation slice | Automation PAUSED conflicts with top-level ledger active and stale updated_at. | Report scheduler state separately from program backlog; preserve target task/worktree and pause; do not resume or create automation. This study records the truth without editing another owner's files. |

The existing ASTRA next issue is `ASTRA-MARKET-02`, not a newly invented backlog: `A/astra-improvement-state.json:759` and `A/docs/research/astra-viz-market-audit-20260916.md:36` record blank token IDs shifting Yes/No positions. This report has not independently re-read all market normalizers, so it is **prior source-audit evidence**, not a fresh source-proven market verdict. The parent market specialist should decide its current rank. Other carried backlog: report checkpoints (05), WorldState/calendar resume (07), synchronous decision waits (08), repeated graph reads (09), checkpoint/context selection (10), cache economics (11), report tool bounds (12), polling (13), visualization transport (14), ensemble forecast/report separation (15), CI/frontend acceptance (16), dependency-aware retention (17), JSON reparsing (18), remaining port/routing backlog (19), and stable stage interfaces/continuity scope (20). Do not mark an entire parent item complete because one child slice is verified.

## Existing asset verification and potential extension

| Asset | Current inspected coverage | Reuse decision |
|---|---|---|
| `/Users/rogerlin/.codex/skills/drf-run-cost-forensics/SKILL.md:10`–`:50` | Run/attempt/ensemble identity, overlap, cache/billing distinctions, source hashes, read-only bounds, acceptance and stop condition | Adequate for repeated cost/time studies. |
| `/Users/rogerlin/.codex/memories/skills/deepresearchforecast-recovery-audit/SKILL.md:14`–`:113` | Canonical/superseded runs, state/checkpoint/progress, no automatic resume | Existing memory-hosted playbook. Its legacy `.env` read suggestion is superseded by this task's explicit no-secrets rule; no credentials were inspected. A future extension could modernize provider-neutral safe metadata references, without a new skill. |
| `/Users/rogerlin/.codex/memories/skills/hms-agent-connectivity-setup/SKILL.md:15`–`:92` | Provider/client/TUI routing, scoped profile overrides, key privacy, serialized transport and offline verification | Adequate. No new install/setup agent. |
| `/Users/rogerlin/.codex/skills/paper-review-evidence/SKILL.md:10`–`:38` | Exact paper/version, verified rubric, source-linked judgments, no external submission, clear stop | Adequate for the observed review requests. |
| `/Users/rogerlin/.agents/skills/source-command-research-codebase/SKILL.md:34`–`:161` | Source-first decomposition, parallel read-only research, concrete file references and historical context | Reuse for source mapping. Candidate extension: optional single-boundary worksheet with producer output, consumer validation, immutable identity, permission projection, failure/replay behavior, test evidence, and honest per-file depth. Do not redefine the skill as automatic implementation. |
| `/Users/rogerlin/.codex/skills/code-review/SKILL.md` | Verification before completion, independent review and scoped evidence | Reuse; generic completion discipline is already packaged. |
| `/Users/rogerlin/.codex/agents/project-architect.toml:1`, `code-reviewer.toml:1`, `context-manager.toml:1` | Existing names are real, but first two are specialized to macOS architecture and Cookbook notebook review respectively; context manager is continuity-oriented | Do not assume these names already implement a language-neutral provenance auditor. Equally, no stable repeated bounded delegation output proves a new agent is preferable to a checklist extension. No agents created. |
| `/Users/rogerlin/.codex/automations/astra-workflow-improvement-loop/automation.toml:2`–`:10` | Existing paused heartbeat, exact owner, output/notification/permission boundaries | Do not duplicate, resume or repoint. |

**What needs more evidence:** validate a proposed boundary worksheet on one DRF boundary and one HMS/Swift boundary, then compare it with the research/review playbooks for redundant instructions. A useful extension should produce a compact contract table and negative scenario list without reading private artifacts, running providers, altering state, or claiming full source coverage from a symbol scan. This is an evaluation criterion for later packaging, not authorization or a claim that an asset is missing today.

## Per-file comparison scope and honest depth

The table below exhaustively lists **219 changed paths** in the union of common-ancestor→ASTRA and common-ancestor→ec29ab1. It is a comparison inventory, not a whole-project code review. “Structural only” means Git path/change metadata, symbol/test-name lookup, or receipt/hash inspection without sufficient body reading to claim detailed review. “Selected functions” means the cited functions/sections were read; the remaining file was not. “Full detailed read” is reserved for the complete 127-line batch receipt helper. Baseline-only demo bodies and images were not inspected. Shared-path labels describe branch edits, not successful merge validation.

| Path | Branch scope | Review depth | What was inspected |
|---|---|---|---|
| `.env.example` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `.gitignore` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `ASTRA-RECOMMENDATIONS.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `PLANS.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `README.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `agent-progress.txt` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `astra-improvement-state.json` | ASTRA | selected functions | program metadata, all recommendation IDs/statuses, open issue entries and latest commit |
| `backend/app/api/research.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/config.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/services/actor_role_prompt.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/services/graph_builder.py` | ASTRA | selected functions | 2377–2421 timeout consumer; accounting/stop symbols through 2484 |
| `backend/app/services/graphiti_client/batch_receipts.py` | ASTRA | full detailed read | 1–127; identity partition, acknowledgements, collector and exception |
| `backend/app/services/graphiti_client/client.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/services/graphiti_client/runtime.py` | ASTRA | selected functions | 112–171 configuration; 242–280 sync timeout; 340–420 driver setup; 1106–1136 batch handoff |
| `backend/app/services/launch_intents.py` | ASTRA | selected functions | 117–189 lookup and transactional reserve; remaining definitions structural |
| `backend/app/services/oasis_profile_generator.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/services/ontology_generator.py` | ASTRA | selected functions | 695–730 sanitation and bounded sampling |
| `backend/app/services/pipeline_orchestrator.py` | both | selected functions | A:1269–1303, 8871–8890, 9012–9056, 9805–9905; reuse call sites; S helper-sync/preflight diff |
| `backend/app/services/report_agent.py` | baseline | selected functions | 10860–10928 chat prompt construction; reduced baseline diff structural |
| `backend/app/services/report_lint.py` | baseline | structural only | Git changed-path inventory; no full content claim |
| `backend/app/services/simulation_config_generator.py` | ASTRA | selected functions | 1649–1730 validated public shared-row selection |
| `backend/app/services/simulation_runner.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/api_budget.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/api_cost.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/llm_client.py` | both | selected functions | A:442–529 physical send/settlement; S:255–285 timeout construction |
| `backend/app/utils/oasis_llm.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/oasis_output_policy.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/oasis_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/simulation_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/app/utils/telemetry.py` | ASTRA | selected functions | 690–735 compatibility projection and reset |
| `backend/app/utils/usage_ledger.py` | ASTRA | selected functions | 398–492, 551–600, 636–680; schema/reservation/budget symbol inventory |
| `backend/run.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_api_attempts.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_api_cost_quotes.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_api_reservations.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_budget_guard.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_cost_coverage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_native_output_policy.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_simulation_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_unresolved_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/benchmark_usage_ledger.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/run_parallel_simulation.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/run_reddit_simulation.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/scripts/run_twitter_simulation.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/fixtures/astra_launch_qa_server.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_actor_shared_access.py` | ASTRA | selected functions | 144–192 duplicate-denial and positive-control scenario |
| `backend/tests/test_api_cost_boundary.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_api_cost_child.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_api_cost_ledger.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_api_token_reservations.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_astra_compaction_bridge.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_audit_fixes_infra.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_budget_guard_boundary.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_budget_totals.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_cost_coverage_boundary.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_cost_coverage_ledger.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_cost_coverage_status.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_deerflow_bridge_sync_guard.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_durable_pipeline_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_durable_telemetry.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_graph_batch_receipts.py` | ASTRA | selected functions | 289–326 and 413–429; remaining test names and source hash |
| `backend/tests/test_graph_research_boundary.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_launch_intent_api.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_launch_intents.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_launcher_backend_port.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_llm_durable_accounting_stop.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_llm_physical_attempts.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_llm_sdk_attempt_boundary.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_loop007_research_budget.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_loop010_global_synthesis.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_native_output_launch.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_oasis_output_policy.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_oasis_physical_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_ontology_context_transfer.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_ontology_reuse_integrity.py` | ASTRA | selected functions | 133–163 rejection behavior; remaining scenario names |
| `backend/tests/test_orchestrator_research_wiring.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_persona_meter_context.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_research_cache_observations.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_research_compaction.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_research_compaction_stop.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_research_stream_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_sim_meter_persistence.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_simulation_shared_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_start_script_progress.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_status_live_block.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_token_reservation_status.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_token_reservations.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_unresolved_usage_ledger.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_unresolved_usage_pipeline.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_usage_completion_gate.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_usage_ledger.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `backend/tests/test_utils_security.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/README.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/config.yaml` | baseline | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/deerflow_research.py` | both | selected functions | baseline-vs-ancestor judge/extract-only changes; linear dispatcher and ASTRA receipt/stop consumer locations |
| `deerflow_bridge/linear_research.py` | baseline | selected functions | 133–208 gateway, 492–590 compaction and mini-agent consumers |
| `deerflow_bridge/patches/apply_subagent_overlays.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/middlewares/model_concurrency_middleware.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/middlewares/summarization_middleware.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/tests/test_client_stream_usage.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/tests/test_client_usage_overlay.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/tests/test_provider_compaction_stop.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/patches/tests/test_summarization_failure_contract.py` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `deerflow_bridge/research_compaction.py` | ASTRA | selected functions | 145–287 producer transaction, exact receipt validation, import identity; stop symbols |
| `deerflow_bridge/search_tools.py` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demo.html` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/actors.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/dossier.md` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/forum.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/graph.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/meta.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/ontology.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/report.md` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/research_log.txt` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/demos/datacenter-2030/sources.json` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/handoff/astra-experience/handoff.md` | ASTRA | selected functions | tail entries 35–79 including UI, receipts and next-slice ownership |
| `docs/handoff/astra-integration/handoff.md` | ASTRA | selected functions | continuity sections 1–66; later ontology repair entries |
| `docs/handoff/astra-optimization/handoff.md` | ASTRA | selected functions | latest continuation entries; long history not fully reviewed |
| `docs/handoff/astra-recommendations/handoff.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/i18n.js` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/index.html` | baseline | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-attempt-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-attempt-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-attempt-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-cost-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-cost-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-cost-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-api-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-budget-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-budget-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-budget-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-budget-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-compaction-benchmark.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-compaction-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-cost-coverage-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-cost-coverage-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-cost-coverage-verification.json` | ASTRA | selected functions | scope, gates, limits and remaining-dollar-concurrency evidence |
| `docs/research/astra-cost-coverage-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-accounting-race.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-review.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-review.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-scenarios.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-graph-receipt-verification.json` | ASTRA | selected functions | scope, counts, limits and all six source hashes independently verified |
| `docs/research/astra-graph-receipt-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-integration-access-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-integration-access-review.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-integration-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-integration-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-integration-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-iteration-04-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch-benchmark.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch/astra-launch-mobile.png` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch/astra-launch-recovered.png` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-launch/astra-launch-uncertain-new.png` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-monetary-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-native-cap-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-native-cap-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-native-cap-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-native-cap-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-context-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-context-review.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-context-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-context-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-historical-metadata.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-integrity.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-review.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-reuse-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-ontology-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-research-stream-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-research-stream-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-reservation-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-reservation-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-reservation-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-reservation-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-simulation-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-simulation-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-simulation-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-simulation-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-successful-runs-audit-20260916-validation.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-successful-runs-audit-20260916.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-successful-runs-audit-20260916.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-unresolved-comparison.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-unresolved-run-evidence.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-unresolved-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-unresolved-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-usage-benchmark.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-usage-browser.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-usage-verification.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-usage-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/research/astra-viz-market-audit-20260916.md` | ASTRA | structural only | headings, findings/acceptance source pointers; normalizer implementations not reviewed |
| `docs/research/astra-workspace-verification.json` | ASTRA | selected functions | scope, counts, limits and all thirteen source hashes independently verified |
| `docs/research/astra-workspace-verification.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `docs/workflows/drf-run-cost-forensics/SKILL.md` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `feature_list.json` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/devServerConfig.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/api/research.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/components/research/RunVitals.vue` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/router/index.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/utils/launchIntent.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/utils/preflightState.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/utils/runVitals.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/src/views/ResearchView.vue` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/devServerConfig.test.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/launchIntent.test.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/manual/astra-launch.playwright.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/manual/astra-usage.playwright.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/manual/astra-workspace.playwright.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/preflightState.test.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/runVitals.test.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/tests/runVitalsRender.test.mjs` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `frontend/vite.config.js` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `scripts/start.sh` | ASTRA | structural only | Git changed-path inventory; no full content claim |
| `setup.sh` | ASTRA | structural only | Git changed-path inventory; no full content claim |

### Additional files outside the changed-path union

| File | Review depth | Scope |
|---|---|---|
| `S/handoff.md` | Selected sections | Opening baseline continuity, prior-loop context; not the complete long history. |
| `A/backend/app/api/report.py` | Selected functions | 178–218 direct report constructor; chat constructor location. |
| `A/backend/app/services/zep_tools.py` | Structural only | Two 100,000-action retrieval call sites found; coalition semantics not independently reviewed. |
| `A/backend/tests/test_research_compaction.py` | Structural only unless listed in the union | Test-name inventory for restart, spoofed metadata and identity checks. |
| Local research/review/forensics/paper skill files in the asset table | Full detailed read for forensics and paper; selected sections for research/review | Existing coverage, invocation boundaries and stop conditions; no execution. |
| Memory recovery and HMS setup SKILL.md files in the asset table | Full detailed read | Existing playbooks and restrictions; no provider setup or recovery invoked. |
| `~/.codex/agents/project-architect.toml` | Selected sections | 1–145 role scope/requirements; not a platform-API verification. |
| `~/.codex/agents/code-reviewer.toml` | Selected sections | 1–100 role specialization/checklist; not adopted as task instructions. |
| `~/.codex/agents/context-manager.toml` | Selected sections | Opening role and continuity guidance. |
| `~/.codex/agents/{git-workflow-manager,knowledge-synthesizer,quant-analyst,risk-manager,swiftui-master}.toml` | Structural only | Existing filename inventory only; no assertion about detailed capabilities. |
| `~/.claude/agents/*.md` | Structural only | Filename inventory only; no new role created from an assumed absence. |
| ASTRA automation.toml | Full detailed read | 10 lines: identity, prompt, pause, schedule, owner and timestamps. |
| Memory registry and two rollout summaries below | Selected sections | Task-local history and asset pointers, with current verification where cheap. |

## Memory references for parent synthesis

These are memory citations, not claims that every historical test was rerun. IDs are rollout/task IDs.

| Memory source and exact lines | Use | Rollout ID |
|---|---|---|
| `MEMORY.md:58–126` | ASTRA authority, prior verified/pending slices, offline harness caution, automation recheck pointers | `01a07c47-3e0a-7f00-bd84-951add1d8233` |
| `rollout_summaries/2026-09-07T14-30-38-3w4z-deepresearchforecast_astra_iterative_integration_repairs.md:138–153` | Prior packaging result: paper skill exists; three historical review requests; unresolved integration list | `01a07c47-3e0a-7f00-bd84-951add1d8233` |
| `rollout_summaries/2026-09-13T11-30-58-ZSrW-hms_memory_cli_mcp_provider_tui_setup.md:43–103` | HMS CLI/MCP and TUI procedure, dates/context and historical evidence | `01a09a88-e7d7-7f80-9014-5c242b2789b4` |
| `MEMORY.md:128–175` | Swift prompt/cache integration evidence and single-task scope | `01a0868e-f6ec-7620-80af-4ad289070800` |
| `skills/deepresearchforecast-recovery-audit/SKILL.md:14–113` | Existing recovery asset; no duplication | `019f672e-1e6d-71d1-8349-1ae902683ef6` (registry association; July, outside recurrence window) |
| `skills/hms-agent-connectivity-setup/SKILL.md:15–92` | Existing setup asset; no duplication | `01a09a88-e7d7-7f80-9014-5c242b2789b4` |

## Validation, recovery notes and handoff

Fresh checks in this study: exact branch heads/merge-base/divergence; clean ASTRA status; source-vs-baseline changed-path intersection; read-only three-tree text-conflict preview; local automation pause/ownership; installed asset existence/content; 19/19 referenced source hashes; report scope and anchor validation. No runtime tests or provider calls were made.

Two discovery details were corrected without writes outside this file: report tooling lives under services (`zep_tools.py`), not an assumed tools directory; and the first merge-preview decoder encountered binary image content, so the preview was re-read in bytes and only conflict path/count metadata was emitted. No binary/history bodies are reproduced here. Neither issue indicates a product defect.

**Created/extended:** this report only; no skills, custom agents or automations. **Deliberately skipped:** duplicates of forensics/recovery/HMS/paper tools; another ASTRA monitor; speculative general-purpose architect; title-derived financial, communication or personal automations. **Needs more evidence:** a narrow cross-boundary worksheet, a second reusable Swift cache workflow, and any non-Codex recurrent workflow.

**Suggested parent plan prompt:** “On ec29ab1, propose dependency-ordered reconciliation of existing ASTRA commits while preserving the new linear research route and GLM timeout/reliability fixes. Include both text-conflict resolutions, explicit linear-route compaction/admission acceptance, actor-denial and ontology-owner invariants, legacy behavior, graph uncertainty and offline harness limitations. Keep the paused ASTRA heartbeat unchanged. Seek PLANS.md approval before implementation; do not create workflow assets from this study.”

**Updates:** Initial brief created at intake; task/source/memory/asset evidence added incrementally; final report records branch reconciliation, current pause status, completed-vs-pending contracts, shortlist and exhaustive changed-path scope. Parent owns broader findings and implementation approval.

### Final coordination update — 2026-09-19

The user approved the parent plan after this source study. The parent reports that RX00 has merged the actual ASTRA baseline into the new worktree and is now testing, with exactly the two anticipated syntactic conflicts: the synchronized helper-module list and timeout/retry client construction. This is parent-reported integration progress, not independently verified merged behavior by this reviewer. All `A/` anchors and source-hash checks above describe ASTRA at `019a7e4`; all `S/` baseline anchors describe `ec29ab1`. The comparison inventory remains the historical 219-path union and is not a post-merge diff. No additional history exploration or branch mutation was performed during finalization.

**Explicit remaining gaps:** no fresh test execution; no review of the parent's actual conflict resolution or RX01 advisory planner; no full detailed read of the 880 Python files; child/native usage implementation and most test bodies are structural scope; no independent current market-normalizer verdict; no Chronicle data; title-only writing/personal workflows cannot establish repeatable procedures. The independent integration reviewer should verify the two resolutions and the linear-route boundary separately. Existing assets remain sufficient for the high-confidence repeated workflows; no speculative assets are proposed for creation now.
