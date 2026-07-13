# PLANS.md — Agentic Workflow Refinement Program

## 2026-07-13 LOOP-013 — complete research provenance in localhost and demos

### Intent

Before the next paid forecast run, make the initiating question and the complete
deep-research event history visible and auditable in both the live Vue workflow
and the static demo. Preserve the existing bounded tail endpoint for efficient
polling, add an explicit full-history snapshot contract, and make static export
use the same multi-track merge semantics instead of copying only the 33-line root
handoff summary.

### Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| O1 | The submitted prompt remains visible after launch and when reopening history | Localhost run view renders the prompt returned by `pipeline_state.json`; browser smoke verifies the exact text |
| O2 | Live logs never discard already-observed history | Initial full snapshot plus incremental tail merge preserves early and late events; frontend regression covers overlap and deduplication |
| O3 | Completed logs are exact and complete | The full-history API reports an exact total and includes root plus every valid `track_N` log without a silent cap |
| O4 | Static demos publish the complete research history | Exported `research_log.txt` is the canonical chronological multi-track merge, and metadata records exact line/source counts |
| O5 | The demo makes provenance obvious | The prompt is persistently visible and the deep-research panel reports full line/source coverage |
| Q1 | The slice is safe for a new run | Focused backend/frontend tests, complete frontend tests/build, backend quality gates, exporter replay, API smoke, and browser acceptance pass |

### Execution order

1. Characterize the current root-versus-track artifacts and API/UI truncation.
2. Introduce one reusable full-history merge alongside the existing bounded tail.
3. Wire initial/final snapshots plus incremental tail accumulation into localhost.
4. Export and render the exact merged history and prompt metadata in the demo.
5. Re-export the EV demo, run deterministic gates, then verify localhost and the
   static page in a real browser before release.

### Risk boundary

No paid research, graph, simulation, or report generation is required. Existing
run artifacts remain immutable; only deterministic reads and tracked demo output
are permitted. Full-history reads MUST fail explicitly on unsafe files or resource
limits rather than silently presenting a partial history as complete.

### Verified implementation outcome (2026-07-13T13:33:00Z)

- The live and static surfaces now show the canonical initiating prompt and all
  persisted research progress events from root, track, and preserved-attempt
  sources. User-facing labels explicitly distinguish this summarized event
  stream from a raw model transcript.
- Localhost performs one full hydration, cheap overlap-aware bounded tails, and
  one distinct terminal snapshot; failed/cancelled states are covered, and logs
  over 2,000 rows use deterministic paging without dropping data.
- Research lane reopen appends instead of overwriting prior events. Failed or
  cancelled global-synthesis logs are retained before staging cleanup.
- Independent review found and closed two final edge cases: concurrent-track
  late arrivals now merge chronologically without duplicating a bounded tail,
  and structured forecasts are exposed only when their exact bytes are bound
  by the current hard-passed final audit.
- The EV demo publishes 1,523 rows from four sources (33 root + 1,490 track),
  with exact boundary events and SHA-256 provenance. Selective export prevents
  unrelated report/chart regeneration; retained legacy logs that cannot be
  re-verified are explicitly labeled incomplete rather than presented as full.
- Backend 2,171/2,171, frontend 18/18, production build, smoke, environment,
  dependency, diff, deployed-bridge parity, API, and browser gates are green.
  Implementation commit `3ca713a` is pushed to both canonical `main` branches;
  GitHub Pages serves the expected log checksum and passes fresh public browser
  acceptance with zero console messages. No paid pipeline was launched.

## 2026-07-13 LOOP-012 — stage forensics and decision-relevant visual evidence

### Intent

Audit the three newest EV pipelines stage by stage, separate historical defects
that are already fixed from active workflow weaknesses, and implement one
cohesive improvement slice: make run observability truthful and make report
visuals answer forecast questions with sourced data. Internal actor-ranking
proxies such as “influence versus salience” MUST NOT occupy a default
customer-facing chart slot when actual market, technology, policy, regional,
simulation, or forecast data are available.

### Comparison set

- `pipe_91aaf91f6392`: latest completed and published EV run.
- `pipe_a362c6f3c49d`: immediately preceding cancelled pre-fix run.
- `pipe_1cf8b18d71a0`: immediately preceding failed global-synthesis run.

### Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| F1 | Every stage is reviewed against the same contract | Research, ontology, graph, prepare, run, and report evidence table with timing, errors, retries, inputs, outputs, health, and remaining risks |
| F2 | Active and historical failures are distinguished | Each finding names whether the current `main` code still reproduces it and points to the responsible code/test seam |
| V1 | Default visuals are reader-relevant | The influence-versus-salience bubble is removed from default report generation and replaced by sourced forecast-domain data when eligible |
| V2 | Charts preserve data meaning | Compatible units are normalized deliberately; incompatible units never share an axis; source and as-of metadata survive into hover/caption/manifest |
| V3 | Chart selection fails closed | No fabricated values, proxy-score substitution, silent empty chart, or “chart for chart’s sake”; skipped reasons remain explicit |
| S1 | The visualization skill teaches reusable judgment | `forecast-visuals` has a concise reader-question workflow plus a routed reference containing concrete time-series, regional, technology, cost, concentration, policy, uncertainty, and scenario examples |
| W1 | Confirmed workflow regressions are hardened | At least one high-confidence cross-run reliability/efficiency defect receives a bounded regression check and fix |
| A1 | The EV artifact replay proves the outcome | A temporary replay of `report_9147b3f6a0a9` produces the new chart set from the original structured artifacts without mutating the published source bundle |
| A2 | The reader-visible EV demo reflects the fix | Refresh the tracked `ev-2035` presentation from the verified replay, remove proxy-chart references/assets, update artifact hashes, and browser-check every new figure without changing the audited source report |
| Q1 | Release evidence is fresh | Focused tests, changed-file lint/compile, skill validation, frontend/build where affected, full backend suite, artifact smoke, and independent review pass before commit/push |

### Execution order

1. Parse the three pipeline states, progress logs, manifests, telemetry, and
   terminal artifacts into one stage-by-stage evidence matrix.
2. Trace every active anomaly to its producer/consumer code and rank it by
   correctness, frequency, user harm, cost, fix size, and verification cost.
3. Implement the smallest coherent workflow hardening and the data-first
   visualization contract. Preserve old helper APIs only where callers/tests
   require compatibility; stop emitting low-value charts by default.
4. Expand `forecast-visuals` using progressive disclosure: keep the mandatory
   decision process in `SKILL.md`, put domain/chart examples in one directly
   linked reference, and validate both the bridge and deployed copies.
5. Replay the completed EV artifacts into an isolated output directory,
   inspect the manifest/data bindings and rendered images, then run code review
   and the proportional repository gates.
6. Update the loop evidence and handoff, commit one cohesive change, and push
   only after all publication-independent gates pass.

### Authorization and risk boundary

The user explicitly authorized stage review and bounded workflow/visualization
changes. Deterministic offline replays are authorized. A new paid deep-research,
graph, simulation, or report run is not necessary for the first proof and will
not be launched unless deterministic evidence cannot validate the change.

### Independent-review amendment (2026-07-13T10:07:09Z)

The first frozen-diff review found no Critical issue and eight release-blocking
edge cases. Before LOOP-012 can close, publication MUST bind project, graph,
simulation, report, and available run identities; the exact final LLM-mutated
research report MUST receive an authoritative complete-scorecard judge; chart
families MUST prove provenance plus unit/denominator/definition/time
compatibility; revision lines MUST bind publisher, outlook family, target
horizon, and publication vintage; and recovery/temporal fallbacks MUST not
invent elapsed time, per-platform completion, or forecast semantics. Each seam
requires a targeted failing-before/passing-after regression, then the complete
gate and independent re-review are repeated.

### Review-correction disposition (2026-07-13T10:46:23Z)

All eight first-review findings are implemented and covered by focused tests.
The final immutable replay emits seven report figures and five dossier figure
pairs, with the reader-facing sequence led by published forecast revisions,
same-denominator benchmarks, declared probabilities, and exact-date milestones.
The tracked `ev-2035` demo contains no influence/salience or proxy
evidence-weight asset/reference; its 25-artifact hash manifest and 17 local
Markdown links validate. Full gates, independent re-review, browser acceptance,
commit, and remote verification remain before closure.

### Final adversarial-review disposition (2026-07-13T11:30:01Z)

The final reviewer found six additional Important edge cases and no Critical
issue. All six are fixed and independently re-reviewed with no Critical or
Important defect remaining: strict publication no longer rebuilds a missing
bound graph; the project artifact and ontology are identity-checked; deep
research reuse requires a complete seven-dimension judge bound to the exact
untruncated prose prefix; incomplete final judging fails closed and late
mutations must pass without regressing a previously passing dimension; absent
legacy platform-enable fields remain unknown; and declared scenario intervals
are visually distinct from true ensemble spread. The exact release bytes pass
the complete backend suite, frontend 14/14 tests/build, dependency/environment,
compile, deployment-parity, skill, diff, static-demo, and immutable-replay
gates. Only staging, commit, push, and public deployment verification remain.

## 2026-07-11 LOOP-010/011 — publication integrity and ontology-connected roles

### New intent

Finish the professional-publication contract and connect the DeerFlow actor
ontology/dossier directly to multi-agent behavior. A report is customer-visible
only when its exact Markdown and structured-forecast bytes pass the current audit policy. Every dossier-backed actor selected into the cast
MUST receive and use a distinct runtime role prompt compiled from its
research-backed ontology fields; sparse entries receive an explicit bounded
fallback rather than a generic invented biography.

### Added acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| P1 | Partial, stale-audit, unsupported-citation, or policy-stale reports never publish | **Implemented:** detail/list/SDK withhold; exports 409; sections/interview locked; policy-v3 exact Markdown+forecast SHA fixtures pass |
| P2 | Research fan-in is immutable and fair | **Implemented:** every lane pack/source ledger has bytes+SHA; all declared lanes consumed fairly; postprocessing precedes manifest-last promotion |
| P3 | Structured forecast propositions and scenarios are coherent | **Implemented:** invalid membership/proposition/market/scenario/cardinality/range/critique/allocation contracts block publication |
| A1 | Every dossier-backed cast actor gets a tailored role contract | **Implemented:** graph omissions receive dossier stand-ins; one versioned contract/fingerprint per selected actor; all exclusions recorded |
| A2 | Runtime consumes the tailored prompt | **Implemented:** exact prompt reaches Reddit `persona`/Twitter `user_char`; full runtime field, profile file, cast, and roster hashes validate at runner start |
| A3 | Role context is safe and efficient | **Implemented:** persona generation receives one allowlisted JSON contract inside explicit untrusted-data markers; values, keys, names, types, and usernames are sanitized; bounded prompts preserve balanced markers plus actions/red-lines/safety; exact runtime/state seals fail closed |

### Current implementation order

- [x] Complete policy-v3 citation/scenario/proposition audits and a central publication barrier.
- [x] Seal research lane manifests and postprocessed research contracts.
- [x] Compile dossier actors into deterministic roles and validate exact OASIS runtime fields.
- [x] Replay the last three transactionally; all failed substantively, restored their bytes, and remain quarantined.
- [x] Run broad report, actor/runner/orchestrator, research/bridge, frontend unit/build, compile, and independent review gates.
- [ ] Obtain explicit authorization for a comparable paid/live research+graph+OASIS run and production `*_profiles_roles.json`; no synthetic cost/latency claim substitutes for it.
- [ ] Move multi-seed sensitivity aggregation before final report rendering in a future bounded slice; until then it remains an immutable sidecar and cannot mutate the sealed forecast.

No paid full run, deployment, commit, push, or destructive cleanup is authorized.

## 2026-07-11 expansion — DeerFlow 2.0 step-change wave

### Intent

Exploit the DeerFlow 2.0 super-agent harness more completely—research planning, subagents, data analysis, charts/diagrams, prediction markets, and export skills—while improving the entire forecast workflow's value per token, stage integration, and delivered report quality. The target is a material capability step change, not a larger version of the same unconstrained search loop.

### Evidence-based constraint

The last representative runs already prove that raw scale is not the missing ingredient: research consumed 2.6–5.6 hours and reached a reproducible high-water mark of 79.75M measured tokens in `pipe_f23527f7d903`. That run recorded 4,974 total tool calls, including 3,391 searches and 1,428 no-result searches, plus roughly 1,075 exact repeat instances. The historical 82.71M claim for `pipe_a8986bffd918` does not reconcile with its current 48,837,873-row sum. Graph construction took up to 8.6 hours. Several individual track reports were 179K–197K characters before multi-track merge. Therefore:

- remove arbitrary prose truncation only where it weakens evidence synthesis;
- do not remove cost, attempt, convergence, or context-safety controls;
- increase parallelism only when scopes are non-overlapping and the measured marginal evidence yield justifies another lane;
- prefer data products, structured evidence, and reusable visual artifacts over repeated prose passes;
- separate “initial report is too short” from “research is too shallow”: measure both before changing caps.

### Success criteria

| ID | Criterion | Acceptance evidence |
|---|---|---|
| D1 | DeerFlow 2.0 is mapped end to end | Harness runtime, skills, tools, subagent policy, pass graph, bridge sync, artifacts, and downstream consumers are documented with source references |
| D2 | The true time/token hotspots are quantified | Last-run phase/token/tool tables distinguish search, synthesis, extraction, graph, simulation, and report costs |
| D3 | Research scale becomes adaptive | Another lane/pass requires an uncovered question/evidence target and stops on low marginal yield or formal budget; no fixed fan-out increase by assertion |
| D4 | Strong initial synthesis is not arbitrarily clipped | Character/context caps are classified as model safety, tool transport, prompt budget, or output truncation; only unjustified output clipping is removed |
| D5 | Polymarket is a reliable stage artifact | Relevant market queries run through the configured tool, a typed artifact records found/empty/degraded status, and research plus final report consume it or state why unavailable |
| D6 | Harness skills are deliberately routed | Research plans can invoke prediction-market, data-analysis, chart, and other relevant skills; unused media/code skills are not injected indiscriminately |
| D7 | Visuals derive from research data | Structured datasets/claims/markets/actors/timeline feed the visualization manifest and final report with explicit source/placement metadata |
| D8 | PDF/export remains integrated | Final PDF uses the exact audited Markdown and safe static chart fallbacks; export failure is visible, never silent |
| D9 | Reusable skill changes are valid | Any new/updated skill has concise triggering metadata, no duplicated instructions, validation passes, and a safe forward test where practical |
| D10 | No false performance claim | Deterministic tests and artifact replays are separated from paid/live wall-time, token, and browser measurements |

### Parallel discovery wave

1. **Harness/capability lane:** inventory DeerFlow 2.0 runtime, skill loader/catalog, subagent limits, data-analysis/chart/PDF capabilities, bridge deployment, and currently unused high-value seams.
2. **Performance lane:** replay recent progress/telemetry to rank phase wall time, tokens, tool calls, repeat/no-result yield, context/output caps, and graph amplification.
3. **Polymarket/delivery lane:** trace question → market queries/tool → `prediction_markets.json` → research prose/structured forecast → report/visual/API/PDF for the latest two runs.
4. **Root integration lane:** verify every claim against live code, resolve conflicts, choose bounded implementation slices, and maintain the loop/recommendation/handoff records.

### Implementation order

1. Fix missing/ambiguous Polymarket artifact status and consumer wiring before adding more market calls.
2. Replace fixed research breadth with a coverage-plan/novelty-budget policy; preserve the shared hard budget as the outer safety invariant.
3. Strengthen initial synthesis by using structured intermediate evidence and section planning, not by deleting model/context safety caps globally.
4. Route selected DeerFlow skills into explicit data-product tasks and connect their artifacts to the existing visualization manifest.
5. Validate exact final report/PDF identity, citations, visual paths, and degraded states.
6. Promote defaults only after deterministic gates and, where necessary, an authorized comparable live run.

### Non-goals and authorization boundary

- No paid/full live run, deployment, commit, push, destructive cleanup, or global plugin installation is authorized.
- The cached Claude `ralph-loop` plugin is not registered as a callable Codex command. Its genuine-completion iteration rule is mirrored by this program; its Claude stop-hook setup script will not be run because that hook is not active in Codex and would create misleading local state.
- “Go all out” authorizes deep local analysis and bounded implementation, not uncontrolled external spend or removal of safety limits.

### LOOP-009 implementation outcome

| Slice | Outcome | Proof |
|---|---|---|
| Harness concurrency | One breadth plane; fixed global subagent envelope distributed across outer tracks | 3 tracks × 3 workers under the default cap, instead of 3 × 5 plus legacy duplication |
| Context efficiency | 80K forecast-ledger summarization, 16K recent-token keep, 12K fetch externalization | YAML/runtime sync and focused config checks |
| Dossier depth | Removed final report ceilings; retained per-call/context/convergence safety limits | Deep skill, standard/deep synthesis prompts, outline parser tests |
| Market delivery | Persistent tool ledger + single-flight cache + canonical cross-track union/status/history + fail-closed report recovery | Producer, collector, orchestrator, report, API tests |
| Visual/data products | Market probability and source quality/freshness research figures; stable Plotly/PNG; table fallback; no Mermaid | 12 renderer tests, real HTML/PNG smoke, browser artifact check |
| Report hygiene | Forecast-first wording, internal telemetry stripping, circular market-contract forecast rejection | Lint/extractor/backfill tests and latest-report replay |
| Frontend integration | Market signals surfaced separately from exact model-vs-market matches; live log/progress fixes retained | API test, 14 frontend tests, production build |

No paid run, deployment, commit, or push was performed. LOOP-009 changes the control and delivery contracts; a comparable live run is the only honest way to quantify the resulting token/wall delta.

**Created (UTC):** 2026-07-10T14:46:09Z  
**Last updated (UTC):** 2026-07-11T11:49:46Z  
**Status:** LOOP-001 through LOOP-011 implemented in deterministic code; live paid performance/OASIS proof remains unauthorized  
**Current phase:** Deterministic scope complete; publication quarantine is active and live paid proof awaits explicit authorization  
**Current revision:** `6746de3c11d5a1a8dd62532acf1fc20266252c98` on `main` plus the active uncommitted loop refinements  
**Primary operational document:** `CODEX_LOOP_ENGINEERING.md`

## Intent

Establish and exercise a durable engineering loop that continuously studies real DeepResearchForecast runs, identifies the highest-leverage workflow defect or bottleneck, fixes exactly one bounded slice at a time, verifies the user-visible outcome, records the evidence, and repeats without losing provenance or destabilizing the pipeline.

## Constraints

- The working tree already contains a large, uncommitted Wave 9 implementation across research, graph, report, visualization, and frontend files. Those changes belong to an existing effort and MUST be preserved.
- No agent may edit the existing Wave 9 file set during the initial forensic wave. The first task is to understand and validate what is already present.
- Each implementation iteration MUST have one owner, one defect hypothesis, one rollback boundary, and one acceptance scenario.
- A passing unit suite is necessary but not sufficient. Each iteration MUST demonstrate a run- or artifact-level improvement tied to a real failure mode.
- The agent MUST not increase research breadth, graph size, simulation seeds, report length, or concurrency until cost and quality evidence justify it.
- No commit, push, deployment, or external tracker mutation is authorized by this request.

## Success criteria

| ID | Criterion | Evidence |
|---|---|---|
| L1 | A self-contained loop-engineering playbook exists | `CODEX_LOOP_ENGINEERING.md` describes state, roles, cadence, gates, metrics, artifacts, stop conditions, and recovery |
| L2 | The complete workflow is mapped end to end | Intake → research → ontology → graph → prepare → run → report → resolution, with contracts and observable handoffs |
| L3 | Latest-run evidence is independently re-audited | Parallel reports cover the latest completed and failed runs, their logs, telemetry, structured artifacts, and deliverables |
| L4 | Existing Wave 9 changes are classified before modification | Every dirty file is mapped to an intended fix, test, unresolved risk, and owning loop stage |
| L5 | One bounded iteration is selected and resolved | A failing scenario is reproduced, the smallest non-overlapping fix is applied, and the scenario passes |
| L6 | Quality gates are truthful | Correct interpreter, focused tests, full backend suite, frontend build, bridge-sync, lint status, and artifact smoke results are recorded without masking failures |
| L7 | The loop can continue safely | Handoff records next candidate, evidence, rollback point, and exact next verification command |

## Execution outcome (2026-07-10)

| Criterion | Result | Evidence/status |
|---|---|---|
| L1 | Met | `CODEX_LOOP_ENGINEERING.md` now defines the state machine, evidence schema, scorecard, ranking model, verification ladder, ownership rules, stop/recovery rules, and iteration register |
| L2 | Met | Intake through resolution is mapped with current code/artifacts and a common `StageResult` boundary |
| L3 | Met | Three parallel read-only lanes independently examined run/stage, research/graph, and simulation/report/product evidence for the latest representative runs |
| L4 | Met | `docs/loop_evidence/2026-07-10/worktree-ownership.md` maps every dirty file at capture to the root loop, earlier audit, or one of the external Wave 9 lanes; active edits remain quarantined |
| L5 | Exceeded | LOOP-001 fixes false exit-zero simulation completion; LOOP-002 separately preserves genuine terminal results during shutdown cleanup |
| L6 | Partially met and truthfully blocked | Focused runner gates pass, but the full backend suite reaches printed 100% and hangs in interpreter shutdown; it is explicitly not recorded as green |
| L7 | Met | Exact bounded commands and per-iteration result records are in `CODEX_LOOP_ENGINEERING.md` and `docs/loop_evidence/2026-07-10/LOOP-003/result.md`; no nonexistent full-pytest log is claimed |

### Refinement addendum (completed 2026-07-11 Asia/Shanghai)

The ownership gate changed when Wave 9 was committed as `6746de3`. The program re-inventoried that base and completed six additional bounded iterations:

| Iteration | Delivered outcome | Evidence |
|---|---|---|
| LOOP-003 | Actor-centered physical graph cap plus bounded/deduplicated UI overview | `docs/loop_evidence/2026-07-10/LOOP-003/result.md` |
| LOOP-004 | Outcome-first reports, global track citations, conservative citation repair, compact unified opening | `LOOP-004/result.md` |
| LOOP-005 | One visualization manifest through generation, API, frontend, PDF, and safe asset serving | `LOOP-005/result.md` |
| LOOP-006 | Readable Plotly/static charts; canonical scenario distribution; no delivered Mermaid artifacts | `LOOP-006/result.md` |
| LOOP-007 | Shared research budgets/negative cache and compact canonical actor dossier | `LOOP-007/result.md` |
| LOOP-008 | Live merged parallel-track logs and phase-bounded monotonic percentage | `LOOP-008/result.md` |

Historical policy-v1 repair produced 9/8/9 visualization sets and useful migration evidence. Policy-v3 transactional replay now correctly blocks all three: citation coverage/extreme statistics, mechanics leakage, and proposition inconsistency respectively. Each failed replay restored every touched byte and wrote a failure artifact; none of these reports or derivatives is currently deliverable. No paid research, graph, simulation, or report generation was launched.

### Current gate addendum

This addendum is historical. The current root gates are: 536 report/forecast/citation tests; 178 actor/runner/orchestrator/publication tests; 384 research/bridge tests; an independent 204-test actor/ontology seam review; 14/14 frontend Node contracts; and a 699-module Vite build. Counts overlap and are not summed into a fake global total.

- Focused LOOP-008 backend gate: 209 tests passed after independent review fixes.
- Frontend pure unit contracts: 14 tests passed; Vite production build passed.
- Visualization focused gate: 133 tests passed before the final scenario/timeline/actor additions; final focused subsets pass after those additions.
- Research budget gate: 97 focused tests passed, including multiprocess races.
- Combined report/citation set: 73 focused tests; broader combined research/report set: 132.
- Full backend suite remains **not green**: the prior unbounded attempt printed 100% but hung in interpreter teardown. It was not rerun without a bounded isolation harness.
- No commit/push/deployment was performed.

### Gate record

| Gate | Command/result | Status |
|---|---|---|
| Focused simulation lifecycle | `cd backend && uv run python -m pytest -q tests/test_audit_fixes_runner.py tests/test_simulation_runner_throttle.py tests/test_drf2_simulation.py` — 81 collected tests, 100% | Pass |
| Structural compile | `cd backend && uv run python -m compileall -q app/services/simulation_runner.py` | Pass |
| Scoped diff hygiene | `cd backend && git diff --check -- app/services/simulation_runner.py tests/test_audit_fixes_runner.py` | Pass |
| Independent review | Two blocking race findings were fixed; final re-review found no critical, important, or minor issue | Pass |
| Full backend | `cd backend && uv run python -m pytest -q` printed 100% but did not exit; sampled in `Py_FinalizeEx` with live `kevent` and `os.read` threads | Blocked/fail, not green |
| Frontend build | Historical row: the parallel lane's result was stale at the time | Superseded by the current 14/14 unit pass and 699-module production build recorded above |
| Bridge sync | Historical row: no stable final hash had yet been captured | Superseded by the current byte-identical actor-skill hash, successful runtime sync, and 10/10 sync-guard pass |
| Lint | Not rerun across the externally owned Wave 9 diff | Unverified; no false-green claim |
| Latest-run artifact replay | LOOP-001 uses a frozen offline shape from `sim_cbbacd1a27a9`; original artifact hashes are in the evidence vault | Pass for runner boundary; orchestrator downstream effect remains inferred |

## Execution plan

### Phase 0 — Establish the control plane

1. Preserve and inventory the dirty worktree.
2. Create the loop-engineering document and this plan.
3. Update `handoff.md` with the active program, constraints, and evidence table.
4. Record the current repository, test, runtime-artifact, and configuration baseline.

### Phase 1 — Parallel forensic wave

Run three read-only lanes:

1. **Run/stage forensics:** latest pipeline states, logs, telemetry, failure transitions, resume/reconciliation, cost, and wall-time.
2. **Research/ontology/graph forensics:** dossier quality, evidence duplication, ontology handoff, graph scope, ingestion failures, entity/community quality, database growth, and viewer payload.
3. **Simulation/report/product forensics:** persona/run integrity, outcome signal, report accuracy/provenance/language, citations, visuals, progressive delivery, and UI state.

The root agent independently maps the end-to-end workflow and inspects the Wave 9 diff/test coverage.

### Phase 2 — Synthesis and decision gate

1. Merge duplicate findings into a ranked candidate register.
2. Score each candidate on user harm, correctness/security, frequency, runtime/cost impact, confidence, fix size, overlap risk, and verification cost.
3. Exclude issues already fully addressed by Wave 9 unless their new implementation fails validation.
4. Select exactly one first iteration using the highest score and lowest safe blast radius.
5. Record the selected hypothesis, expected effect, rollback boundary, and acceptance check in both loop document and handoff.

### Phase 3 — One-feature implementation loop

For the selected slice:

1. Reproduce the defect or characterize the baseline.
2. Add or identify a regression check that fails for the defect.
3. Implement the smallest cohesive fix without touching unrelated dirty files.
4. Run the focused check, relevant integration checks, and scenario acceptance.
5. Review the diff and obtain an independent code review.
6. Run the proportional final gates.
7. Update loop metrics, decision register, handoff, and next-candidate queue.

### Phase 4 — Repeat or stop

Repeat Phase 3 only when:

- the worktree remains understood and recoverable;
- the previous slice is verified;
- no concurrent owner is editing the next slice;
- the next candidate has fresh evidence and an objective acceptance check.

Stop and report rather than guessing when a candidate requires production credentials, a live paid run, user product judgment, or expansion into a concurrently owned file set.

## Verification portfolio

- **Static:** source-reference checks, schema validation, compilation, lint status, bridge drift/hash checks.
- **Focused:** defect-specific unit/contract tests with a failure-before/pass-after narrative.
- **Integration:** stage boundary tests using persisted artifacts from representative runs.
- **Scenario:** replay/smoke against latest run artifacts; no destructive mutation of original artifacts.
- **System:** correct-interpreter backend suite, frontend build, application import, setup/doctor where relevant.
- **Outcome:** compare cost, wall-time, coverage, graph size, report-quality/provenance, or user-visible state against the recorded baseline.

## Initial risks

- A separate Wave 9 implementation may still be active; overlapping edits could corrupt or misattribute work.
- Latest run artifacts are large and may contain partially repaired outputs; analysis must distinguish original run output from post-run regeneration.
- Re-running full research or graph stages can incur hours and material API cost; use artifact replays and focused smokes before any paid live run.
- Root `npm test` is `cd backend && uv run pytest`; the loop prefers the explicit `cd backend && uv run python -m pytest`. The current risk is not relocatability but the full suite's interpreter-shutdown hang and concurrent Wave 9 drift.

## Approval and change control

The user explicitly requested that the agent design the loop, run parallel agents, resolve findings, and keep iterating. This authorizes the plan's read-only forensic wave and bounded, reversible fixes inside the stated scope. Any deployment, paid live run, destructive cleanup, commit/push, or change that overlaps an active external owner requires a separate decision.
