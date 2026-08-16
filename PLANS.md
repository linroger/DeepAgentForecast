# PLANS.md — Agentic Workflow Refinement Program

## 2026-08-17 LOOP-017 — measurable cost, reliable localhost, and forecast-quality refinement

### Status and approval gate

**Status:** Plan proposed; implementation by this session is not approved yet.

This is a complex, multi-surface program. Per the repository working agreement,
the implementation slices below MUST remain pending until the user approves this
plan. Read-only inspection, offline artifact analysis, and continuity updates are
allowed before approval. No paid pipeline, provider mutation, resume, restart,
publication, commit, or push is part of the approval request.

Concurrent changes already present in the dirty working tree are user-owned. They
MUST be reviewed and reconciled rather than reverted, duplicated, or attributed
to this session. During read-only validation, the external owner committed the
root-route/UI, Flask SPA serving, fallback-model/fail-fast, visualization-axis/
shared-Plotly, and pytest-log-isolation slice together as `3306af8`. That commit
is an evidence checkpoint, not feature acceptance: the real launcher and several
cross-component safety/accessibility scenarios remain red. Its changes are inputs
to Slices 1, 4, 7, and the Q1 gate respectively; each must be reviewed and
verified in its own feature rather than duplicated or treated as one accepted
localhost change.

### Intent

Make the six-stage DeepResearchForecast workflow measurable, faster, safer, and
more legible without weakening provenance, publication gates, or Foglamp's
diagnostic-only simulation boundary. The program has four linked outcomes:

1. `localhost` opens the DeepResearchForecast research workflow directly and
   survives the actual launcher/readiness/browser acceptance path.
2. Token and time accounting is exact enough to identify real waste by pipeline,
   attempt, stage, lane, phase, provider, model, and request rather than relying
   on double-counted or zero-filled summaries.
3. The highest-cost stages stop predictably on provider/time/budget exhaustion
   and avoid repeating work whose marginal evidence value is exhausted.
4. Forecast, market, graph, simulation, and report artifacts preserve explicit
   lineage into a professional, accessible UI and decision-relevant visuals.

### Evidence baseline and corrections

- Current runtime authority is `PipelineOrchestrator` plus
  `uploads/pipelines/<id>/pipeline_state.json`, executing
  `RESEARCH -> ONTOLOGY -> GRAPH -> PREPARE -> RUN -> REPORT`. Native DeerFlow
  Gateway and `drf2/` are not current authority.
- For completed reference pipeline `pipe_f23527f7d903`, the durably recorded
  main-run meter total/lower bound at `options.llm_telemetry.total` is
  **82,983,739 tokens**, of which the synthetic RESEARCH entry is
  **79,749,778 (96.1%)**. Adding `options.research_telemetry.tokens_total` to
  `options.llm_telemetry.total` produces **162,733,517**, but that double-counts
  the same research aggregate. Two additional ensemble reports add a known
  6,356,626 tokens, yielding an all-in durable lower bound of 89,340,365.
- The same run lasted 13h57m. GRAPH occupied 8h37m (61.8% elapsed). Its outer
  ten-chunk batch loop was sequential, so all twelve 900-second whole-batch
  deadlines were on the critical path: **10,800 seconds / 3 hours / 34.8% of
  GRAPH**. The caller accounted 278/466 chunks as skipped, including 120 chunks
  hidden by those all-or-nothing batch deadlines; some cancelled batches may
  still have produced partial durable writes that the caller could not retain.
- The denominator is still incomplete: current DeerFlow message-ID accounting
  drops later parent-plus-subagent usage snapshots, and RUN records calls/errors
  but not prompt/output tokens. A mechanically reproduced 10 -> 110 same-ID
  usage growth emitted only 10. Therefore neither 82.98M nor the reported 150M
  is a trustworthy true total until accounting is fixed.
- Failed RESEARCH lanes create a second, larger accounting hole. In
  `pipe_0f2bee7bd649`, retained logs contain **25,161,961** known attempted
  tokens while pipeline state reports only **6,110,831**; the failed Track 1 and
  Track 3 spend of **19,051,130 (75.7%)** is omitted because final aggregation
  sums successful lanes only. Current telemetry has no `usage_complete`,
  missing-call count, or lower-bound reason, and provider retries before a
  successful fallback are not measured.
- Historical graph zero-token telemetry predates the current ContextVar executor
  propagation fix. That historical record MUST NOT be presented as proof that
  current graph attribution is still broken; it needs a controlled validation.
- Historical adaptive research waste is real. In the reference run, late passes
  consumed **20,286,821 tokens (25.44% of RESEARCH)**, including six Track-1
  passes costing 15,817,428 tokens while its gap count stayed at 20 throughout.
  The 63 retained reference usage rows reconcile exactly to 79,749,778, but the
  historical topology had 24 legacy fanout workers, nine middle-phase workers,
  repeated merge/correction turns, and 4,974 emitted tool attempts. Current
  source already suppresses legacy fanout, limits each of three evidence lanes
  to three isolated middle phases, centralizes synthesis, bounds inherited notes
  to 60,000 characters, and permits at most one adaptive pass with plateau/
  zero-yield stopping. Its expected reduction is source-derived: no completed
  current-topology run yet proves the savings. Emitted tool attempts also MUST
  be distinguished from cache admission and real network calls before query
  retry waste is quantified.
- GRAPH historically re-extracted work already present in structured Stage-1
  artifacts: 25 curated actors, 196 normalized unique relationships, and 109
  aliases were seeded, then both the 356-chunk report and 110-chunk dossier were
  sent through prose extraction. The current `dossier_only` default would reduce
  that corpus from 466 to about 109 episodes, and current sealed
  `actor-intelligence/v1` structural writes are deterministic; neither change
  existed in the reference run. A controlled fixture must prove a v1
  `structured_only` or residual-only path preserves identities, relationships,
  receipts, and PREPARE cast coverage before prose extraction is removed.
- The concurrent UI tree now routes `/` to `ResearchView`, but the rebrand no
  longer matches `scripts/start.sh`'s `DeepAgentForecast` readiness signature.
  The launcher would roll back healthy services. The brand also sends users to
  `/legacy`, and Vite plus `start.sh` can open two browser tabs.
- The real launcher has now reproduced that failure: both services became
  healthy, the stale frontend signature timed out, and the launcher safely
  rolled both owned processes back. Focused launcher tests still false-green
  because their HTML fixture hard-codes the retired identity. Direct Flask SPA
  browser checks proved `/`, `/research`, refresh, history, and unknown-route
  behavior without a `/run` POST, but also proved that the brand enters
  `/legacy`; Settings has no dialog semantics, focus entry/restore, or Escape
  close; and preflight can display a false green state when its request fails.
- The local-serving contract has additional integration and security gaps:
  Vite binds on all interfaces while proxying into a loopback-trusted API,
  backend-port authority is split across the shell, `.env`, Python, and a
  literal Vite target, exact `/api` and missing assets fall through to SPA HTML,
  and a missing `dist` reports HTTP 200 success instead of an unavailable UI.
  The client prevents an immediate double-click, but a lost successful `/run`
  response plus manual retry can still create a second costly pipeline because
  the server has no stable launch-intent idempotency key.
- A concurrent owner repaired directory-mode Plotly delivery during validation:
  one shared on-disk bundle is injected at serve time while the opaque sandbox
  remains intact. Real Plotly HTTP replay and the focused regression set pass,
  so the earlier bundle-delivery P0 is retracted. Remaining hardening is to
  reject sibling bundle symlinks/oversize files and cover the complete research
  artifact path.
- The concurrent fallback-model guard is incomplete and unsafe across provider
  boundaries. Graphiti constructs its fallback outside the guarded resolver;
  absent fallback fields can inherit the primary model, base URL, or API key,
  including transmitting a primary credential to another endpoint. Generic
  request-specific HTTP 400s also poison the whole fallback tuple for 15
  minutes. Fallback configuration and deterministic-error classification must
  be centralized before this slice is accepted.
- Polymarket currently has a forecast-integrity bug: topical relevance alone
  injects a market into research, the scenario spine, every report chapter, and
  binary-generation instructions before contract equivalence is known. At the
  later anchor boundary, an exact/near match at confidence 0.49 can revise a
  forecast from 0.80 to 0.40, then be removed from final provenance while the
  changed probability and market rationale survive. A completed Optimus artifact
  confirms the broader leak: a 12.5% market became a major research/report anchor
  even though the canonical forecast retained zero accepted market anchors.
- Tool-normalized markets also omit CLOB IDs, bid/ask, and daily change, and a
  selected tool row suppresses deterministic refresh by default. History then
  assumes the first unlabeled CLOB token is `Yes`; a reversed `[No, Yes]` fixture
  selected the `NO_TOKEN` series and would label it P(Yes). Resolution polling
  accepts any nonempty anchor market ID without equivalence/binding provenance,
  and its read-before-append ledger admitted two concurrent rows for the same
  key, double-weighting Brier summaries.
- After the market audit completed, a concurrent uncommitted edit appeared in
  `backend/app/utils/prediction_markets.py` adding a browser-like Gamma user agent,
  jittered transient-error backoff, shorter timeout, and transport diagnostics.
  It is useful external input for transport reliability but does not change the
  pre-equivalence influence, schema/CLOB identity, or resolution-ledger defects;
  it MUST be reconciled rather than overwritten when Slice 6 begins.
- The shared Plotly delivery repair works for ordinary generated charts and
  preserves the opaque sandbox, but its sibling-bundle loader follows symlinks,
  has no chart-directory containment check, and reads without a size bound. An
  offline fixture inlined an outside file into served chart HTML. No saved
  post-`3306af8` report yet proves the complete current visual path in a browser.
- The reference REPORT stage is itself measurably prompt-heavy: 3,042,322 of
  3,168,162 report tokens (96.0%) were prompt tokens. Its 57 tool results contain
  1,569,434 raw characters; because the ReAct transcript resends prior results on
  later turns, those payloads account for an estimated 5,476,501 character-
  transmissions (~1.37M tokens at the repository's four-characters-per-token
  estimator). `insight_forge` and `panorama_search` contribute 88.7% of the raw
  result text. Current ReAct mode places their unbounded `to_text()` output into
  the next prompt; Panorama also serializes all 940 node names and up to 100
  facts without exact deduplication. In the saved run, exact duplicate lines make
  up 45.9% of Panorama result bytes. The existing retrieval cache/parallel
  helpers are not operational configuration: they read
  `Config.REPORT_RETRIEVAL_CACHE`, `REPORT_RETRIEVAL_PARALLEL`, and worker fields
  that `config.py` never defines, so environment settings cannot enable them.
- REPORT also lost 13m42s (24.6% of the stage) to a two-attempt forecast-spine
  JSON call that ultimately failed on an unescaped quote and was discarded. Its
  telemetry has only stage totals; concurrent section mode intentionally leaves
  `sections=[]`, so spine, outline, retrieval, reflection, grounding repair,
  language repair, and translation cannot be costed separately. The late repair
  path then spent about 6m33s raising citation coverage from 0.43 to 0.907, and
  the final report still closed degraded with five implausible headline stats.
- RUN's completed-result reuse is provenance-safe, but interrupted-run recovery
  is operationally disconnected: `SIM_CHECKPOINT=true` writes sealed round
  checkpoints by default while pipeline resume calls `start_simulation(...,
  resume=None)`; with the default `SIM_RESUME=false`, those checkpoints are not
  consumed and stale action logs are rotated before a fresh run. RUN additionally
  records 109 Twitter plus 112 Reddit model calls but no usage. It would need to
  average more than 360,858 tokens per call to overtake the recorded RESEARCH
  total, but exact RUN cost remains unknown until child-process usage is persisted.
- The historical three-seed ensemble spent another 6,356,626 known report tokens
  yet produced 11/12 unmatched scenario rows, each with support=1/3 and aggregate
  agreement 0.0. Current scenario-spine alignment addresses the naming defect and
  the default is safely back to one seed, but `_run_one_seed` still executes the
  complete PREPARE -> RUN -> prose REPORT path. Multi-seed mode MUST stay disabled
  until seed work is forecast-only and measured against a quality gain.

### Invariants

1. Every provider call receives one locally generated immutable logical
   `call_id`, which contributes to cumulative lineage exactly once. Provider
   request IDs and LangGraph message IDs are metadata, not the accounting key;
   growing cumulative snapshots for one message ID contribute only positive
   deltas to their owning logical call(s).
2. Budgets apply across process boundaries and resume attempts. A resume does
   not silently reset lineage spend, and estimated usage is labeled as estimated.
3. A healthy in-flight pipeline is never restarted or duplicated. No implementation
   test may issue a paid `/run` or provider call without separate explicit approval.
4. Forecast probabilities can change only through a retained, source-bound input
   whose equivalence, confidence, timestamp, and transformation remain visible.
5. Scenario simulation stays diagnostic-only unless a separately validated
   promotion contract authorizes an update.
6. Root, alias, refresh, history, and unknown-route navigation never duplicate a
   `/run` POST and never route the product brand to the legacy MiroFish surface.
7. Historical artifacts remain immutable; new policy/version metadata describes
   their limitations instead of rewriting them.

### Requirements -> acceptance checks

| ID | Requirement | Scenario-level acceptance check | Evidence |
|---|---|---|---|
| L1 | Reliable direct localhost entry | Start through the real launcher; load `/`, `/research`, refresh, back/forward, and an unknown path | One browser tab, ResearchView at `/`, readiness passes, no console/network errors, no duplicate `/run` |
| L2 | Professional and accessible workflow UI | Keyboard-only desktop/mobile pass over prompt, advanced controls, tabs, settings, history, confirmations, progress, graph, report, and exports | Focus order, dialog focus/escape/restore, ARIA/name checks, contrast and overflow screenshots |
| L3 | Durable and local-only launch boundary | Lose the first successful `/run` response, replay the same intent/body, replay the key with a different body, and probe exact `/api`, unknown non-GET APIs, missing assets, and missing `dist` through the real launcher | One loopback-only Vite listener and one opener; same intent returns one durable pipeline; changed payload is rejected; API/static/build failures are typed non-HTML/non-success responses |
| T1 | Exact Stage-1 usage | Replay same-ID 10 -> 110 usage, the 63-row reference, and a failed-lane fixture | Exactly 110 recorded; reference reconciles once to 79,749,778; all 25,161,961 failed/successful-lane tokens remain visible; incomplete inputs set a typed lower-bound reason |
| T2 | Cross-process lineage ledger | Offline fixtures emit research, graph, simulation, report, translation, retry, and resume events with local call IDs plus provider/message metadata | Unique logical-call sum equals lineage total; attempt/current/cumulative views are distinct |
| T3 | Enforceable budgets | Simulated subprocess crosses token/cost budget between calls; parallel workers race at the remaining-budget boundary | Reservation is atomic, aggregate overshoot is bounded by policy, the next unreserved call is rejected with a durable receipt, and completed work remains resumable |
| O1 | Truthful live observability | Poll an active fixture, a stalled fixture, and a completed fixture | UI/API show stage elapsed, ETA approximation, heartbeat age, stale state, token/cost lower bound, and remaining budget |
| F1 | Provider failover is credential- and failure-isolated | Route primary and fallback through distinct canary endpoints/credentials; inject a request-specific 400 and a proven deterministic configuration error through normal and Graphiti paths | Fallback uses only its explicit complete tuple or fails closed; no primary secret/base/model crosses providers; request-specific 400 does not cooldown a healthy provider; only the matching deterministic configuration signature is suppressed |
| G1 | Bounded graph failure tail | Run nine fast episodes plus one hanging episode, then a sealed-v1 structured-only fixture | Nine successes are retained, one typed timeout returns within budget, no lock/task leaks, next batch starts immediately, and structured identity/relationship/cast parity is preserved |
| R1 | Efficient research convergence | Replay historical constant-gap, duplicate-query, and failed-lane fixtures through current controls | At most one default adaptive pass, no merge-only gap growth, 60k inherited-context cap, exact-query retry suppression, emitted/admitted/network/result counts separated, quality contract retained |
| P1 | Efficient report evidence synthesis | Replay the saved oversized Panorama/InsightForge payloads plus malformed-spine and concurrent-section fixtures | Tool payloads are relevance-ranked, deduplicated, and bounded before entering prompts; every report operation has attributable time/tokens; malformed spine exits within its operation budget; forecast/citation quality does not regress |
| I1 | Cross-stage artifact authority | Exercise base pipeline, scenario fork, resume, and stale/tampered handoff fixtures | Every API resolves `state.handoff_dir`; hashes/attempt lineage match; stale or tampered reuse fails closed |
| M1 | Market influence is provenance-safe | Feed a topically related non-equivalent market plus confidence 0.49 and 0.50 matches through prepass, anchor, divergence, reconciliation, report, and publication | Non-equivalent/0.49 rows cannot enter probability instructions or call divergence and leave the forecast byte-identical; accepted match remains visible with exact reason and price |
| M2 | Selected markets support history | Select a tool market lacking CLOB fields, then batch-enrich by exact ID | One bounded enrichment, schema parity, CLOB history when available, explicit unavailable reason otherwise |
| M3 | Market outcome and resolution lifecycle are authoritative | Feed reversed outcome/token ordering plus repeated/concurrent unresolved, ambiguous, and resolved polling fixtures | History uses the token mapped to literal `Yes`; exactly one provenance-complete ledger row survives concurrent polling; only unambiguous resolution becomes calibration authority |
| V1 | Decision-relevant truthful visuals | Render binary, scenario, market, diagnostic simulation, revisions, timeline, and quantitative fixtures; serve legitimate, symlinked, non-regular, and oversize bundles | Correct chronology/units/uncertainty, readable labels, policy/source hashes, diagnostic simulation disclaimer and skip reasons; only contained regular size-bounded bundles are inlined |
| S1 | Simulation resume avoids repeated spend | Replay a completed-simulation/report-only resume and a partial run with a sealed round checkpoint under default configuration | Completed simulation is reused; pipeline resume explicitly elects sealed-checkpoint recovery for the partial run; it starts after the last sealed round; no completed round or provider call is duplicated |
| E1 | Multi-seed work has positive marginal value | Replay the historical 11/12 support=1 ensemble and a semantically aligned forecast-only fixture | Historical mismatch is detected; no full prose report is generated per seed; agreement/support reconcile by canonical resolution criteria; extra spend is emitted and mode remains off without a measured quality gain |
| Q1 | Scoped, reviewable delivery | Run focused tests per slice, then affected backend/frontend suites, build, launcher smoke, browser acceptance, lint/compile/diff checks | No new failures; known unrelated fixture remains separately identified; unrelated dirty files remain untouched |

### Dependency-ordered implementation slices

Only one slice may be in progress at a time. Every slice starts with a failing
regression or adopts and replays an already-authored external red/green pair,
then ends with its scenario acceptance check and updates `handoff.md` and
`agent-progress.txt`. A feature-list `passes` flag changes only after every slice
and acceptance step belonging to that feature is green; Slice 2 alone therefore
cannot pass `feature_usage_lineage_truth` before Slice 3 completes the ledger.

1. **Stabilize and accept the concurrent localhost/UI slice.** Reconcile the
   current uncommitted UI and SPA-serving changes; replace the brittle
   readiness-brand predicate with a stable application marker; keep the product
   brand on `/`; make `start.sh` the sole optional browser opener; bind Vite to
   loopback; and centralize the backend port/proxy authority. Make exact `/api`,
   non-GET unknown API routes, missing assets, and missing `dist` fail with the
   correct non-HTML/non-success response. Add a persisted launch-intent key so an
   ambiguous successful `/run` response cannot create a duplicate pipeline on
   manual retry. Repair preflight state ownership, modal/focus/keyboard/live-region
   semantics, contrast, responsive history controls, and documentation drift.
   Run the production build/unit tests and real launcher/browser matrix. Do not
   claim the external slice as this session's implementation, and do not sweep
   its unrelated fallback/viz/log changes into this feature.
2. **Make Stage-1 usage delta-correct.** Replace boolean per-message-ID
   de-duplication with cumulative positive-delta accounting; preserve streamed
   lane/phase/model usage, including rows from failed, cancelled, and timed-out
   lanes. Persist completeness, missing-call, measured/estimated, and lower-bound
   semantics. This is the first cost optimization because every later percentage
   and budget depends on it.
3. **Introduce an immutable cross-process request ledger.** Define the additive
   logical-`call_id` record contract and instrument RESEARCH/Graphiti/OASIS/REPORT
   plus retries and resumes; retain provider request and LangGraph message IDs as
   metadata. Preserve lower-bound/estimated labels where providers omit usage.
4. **Connect live health, spend, and budgets.** Review the external fallback and
   dual-outage edits in this feature. Centralize fallback-provider resolution so
   Graphiti and normal calls cannot inherit a primary model, base URL, or
   credential across providers; scope cooldowns to proven deterministic
   configuration signatures rather than every HTTP 400. Expose existing
   heartbeat/ETA calculations plus ledger snapshots through status; atomically
   reserve budget before calls across subprocesses; present a compact stage-cost
   panel in the UI.
5. **Repair shared-handoff API resolution.** Centralize `state.handoff_dir`
   resolution for dossier, translation, PDF, editing, progress, visualization,
   and scenario-fork routes, with tamper and reuse regressions.
6. **Repair Polymarket forecast integrity.** Gate confidence/equivalence before
   any probability/scenario instruction and before divergence; strengthen
   rationale binding; align all normalizers; and enrich only selected market IDs
   for CLOB/history/provenance with a typed unavailable reason. Map CLOB tokens by
   the literal `Yes` outcome. Require equivalence/confidence/binding hashes in the
   resolution ledger and make concurrent polling atomically idempotent and
   fail-closed. Do not repeat broad searches when the exact selected ID is known.
7. **Improve forecast presentation and visualization truthfulness.** Revalidate
   the external axis/shared-Plotly and serve-time-inlining edits as one delivery
   contract, including research-artifact HTTP delivery, bundle containment,
   non-symlink/size checks, and browser execution under the opaque sandbox. Add
   a first-class sortable binary table; surface market equivalence/freshness and
   visualization skip reasons; label simulation trajectories diagnostic-only;
   version manifests; fix chronology, truncation, whitespace, uncertainty, and
   responsive/accessibility defects against real saved artifacts.
8. **Bound graph failure and redundant extraction.** Benchmark current chunk,
   retry, resolution, and community-detection defaults first. Replace the
   all-or-nothing outer batch deadline with per-episode deadlines that retain
   completed episode UUIDs and cancel only pending tasks. Add one logical
   retry/wall-budget ledger across adapter, Graphiti, schema-reroll, fallback,
   and rate-limit replay layers. For sealed `actor-intelligence/v1` only, gate a
   structured-only or deterministic-residual path behind a flag after parity
   fixtures pass. Preserve a signed degradation receipt and make graph telemetry
   monotonic across flush/reuse boundaries; do not raise static concurrency.
9. **Validate and tune research marginal yield.** Compare current convergence
   controls to historical fixtures; deduplicate exact normalized searches, stop
   terminal market transport retries, compact invariant history, and continue a
   pass only for a named KIQ/source/contradiction/forecast-input gain. Persist
   emitted tool attempts, admitted calls, cache hits, network calls, results, and
   negative-cache decisions separately so savings are not inferred from prompt
   attempts alone. Do not reduce the three evidence angles without a measured
   marginal-yield fixture.
10. **Eliminate avoidable report/simulation repetition.** Prove report-only
    resume reuses a completed simulation and make pipeline resume explicitly
    consume a compatible sealed checkpoint for an interrupted RUN under default
    settings, without duplicate completed rounds or calls. Persist child-process
    prompt/output usage from the same OASIS request wrapper that already records
    calls/errors. Add report operation telemetry for spine, graph preload,
    outline, each concurrent section/tool, reflection, grounding repair,
    three-part synthesis, language repair, and translation. Shape ReAct and
    native-tool results through one deterministic, tool-specific relevance/
    deduplication/budget boundary; do not blind-slice after serializing an entire
    graph. Put the two-attempt structured spine behind an operation deadline and
    schema/repair contract so malformed output cannot consume two 600-second HTTP
    windows. Replace fixed minimum tool-call counts with an evidence-sufficiency
    gate only if replay proves citation/forecast quality is preserved. If
    ensembles are re-enabled, produce forecast-only seed outputs rather than full
    PREPARE -> RUN -> prose reports, and reconcile by canonical resolution
    criteria. Preserve `N_FORECAST_SEEDS=1` until forecast-only seeds pass.
11. **Controlled end-to-end comparison (separate approval).** Only after offline
    gates pass, request authorization for one fresh controlled run. Compare
    reconciled measured tokens plus explicitly labeled estimates/lower bounds,
    stage time, evidence quality, judge outcomes, market coverage, artifact
    completeness, and visuals against the historical baselines. A paid run is
    not implied by approval of slices 1-10; an incomplete provider denominator
    MUST NOT be called exact.

### Risks, rollback, and stopping rules

- A semantic optimization that improves tokens/time but degrades source coverage,
  judge quality, provenance, or publication integrity MUST be rolled back.
- Provider outages, budget exhaustion, or graph degradation MUST stop with typed,
  resumable state; they MUST NOT trigger unbounded retries or a duplicate run.
- UI refactors MUST remain separable from workflow semantics and preserve the
  non-retried `/run` POST safeguard.
- If a concurrent owner changes an in-scope file, pause that slice, re-baseline,
  and reconcile rather than overwrite.
- No completion claim is valid from a historical replay alone. Historical replay
  proves regressions; a later approved controlled run validates external behavior.

### Immediate decision requested

Approve LOOP-017 as written, or specify changes. On approval, implementation
begins with Slice 1 only: stabilize and browser-validate the current localhost/UI
entry work. Slice 2 begins only after Slice 1 is green and documented.

## 2026-07-13 LOOP-014 — Mandarin report and PDF release integrity

### Intent

Before the next forecast runs, prove that a publication-valid English report
can be translated into Mandarin, viewed and exported from the live UI, and
downloaded as both Markdown and a genuinely readable Mandarin PDF. Audit the
runtime skills activated by this path, tighten only instructions with a
reproduced ambiguity, inefficiency, or failure mode, and keep source/deployed
copies byte-identical. Make `npm start` stream service output and durable stage
marks, then launch the user's humanoid-robotics, grid-storage, and AI-compute
prompts as three independent parallel pipelines and monitor each to publication.

### Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| M1 | Mandarin generation is bound to the selected published report | API and UI scenario return the selected report's Mandarin artifact; stale, failed, unaudited, or mismatched artifacts fail closed |
| M2 | Mandarin viewing and Markdown export work end to end | Real browser switches to Mandarin and downloads UTF-8 Markdown with the expected report identity and Chinese text |
| M3 | Mandarin PDF export is a real, legible document | Export returns `%PDF` bytes with the correct content disposition; text extraction finds Chinese text; all rendered pages are inspected for missing glyphs, clipping, overlap, and broken layout |
| M4 | English/Mandarin state remains coherent | Switching reports or language cannot show or export a previous report's translation; loading/error/availability states are explicit |
| S1 | Every runtime skill used by this path is reviewed | Activation evidence maps runtime stage to skill; frontmatter, core workflow, failure behavior, resource routing, and source/deployed parity are checked |
| S2 | Skill optimization is evidence-backed and validated | Any edited skill is concise, imperative, linked to required resources, validated with the repository and skill-creator validators, and forward-tested where practical |
| O1 | Startup output is attached and stage-aware | `npm start` streams both service logs and concise durable `▶/✓/✕` stage transitions, repairs stale/misbound PID files, returns nonzero on readiness failure, and retains an explicit detached mode |
| R1 | Three requested forecasts run independently in parallel | Three distinct pipeline IDs preserve the exact humanoid-robotics, grid-storage, and AI-compute prompts and begin without serial launch coupling |
| R2 | Every new run completes end to end | Each pipeline has healthy heartbeats, real model/search/fetch activity, nonzero canonical evidence, terminal research/ontology/graph/prepare/run/report artifacts, and a publication-valid audited report |
| Q1 | The repository is safe for the next run | Focused backend/frontend tests, production build, backend suite, dependency/environment/diff checks, API smoke, PDF render QA, and browser acceptance pass |

### Execution order

1. Characterize report publication, translation persistence, language switching,
   Markdown export, PDF generation, font embedding, and error contracts.
2. Inventory the exact runtime skills activated by translation/export and check
   authoritative versus deployed copies before editing anything.
3. Add failing-before regressions for every reproduced defect, implement the
   smallest coherent repair, and optimize only implicated skill instructions.
4. Exercise the selected EV report through API and real-browser scenarios;
   inspect extracted PDF text and every rendered page.
5. Exercise `npm start` against healthy, stale-PID, attached-stream, detached,
   signal, and readiness-failure paths; prove stage transitions from durable state.
6. Freeze the implementation diff, run proportional repository gates and an
   independent review, then launch all three exact prompts together.
7. Monitor and repair only reproducible in-scope regressions until all three
   pipelines pass their terminal publication gates and artifacts are verified.
8. Update continuity evidence, commit, push both canonical `main` remotes, and
   verify a clean state.

### Authorization and risk boundary

The user explicitly authorized Mandarin/export verification, skill optimization,
startup observability changes, and three new full parallel runs. Existing audited
report bytes MUST remain immutable unless the translation contract explicitly
creates a new language sidecar. The three supplied prompts MUST be preserved
verbatim and MUST remain separate sessions. Healthy in-flight pipelines MUST NOT
be restarted; failed/stalled work may be retried only after evidence identifies
and validates an in-scope workflow correction.

### Recovery amendment (2026-07-15T17:07:00Z)

The current grid-storage successor is `pipe_0e1b84d2682a`. Its synthesis-only
recovery reused 292 sealed sources and produced an exact-judge-passed 19,943-word
dossier; it is now healthy in graph construction and MUST NOT be restarted or
resumed again. Terminal acceptance additionally requires a non-fabricated
simulation-contribution audit because the pre-fix process image failed to parse
an aggregate `summing to 100%` scenario heading. The deterministic parser fix is
covered by the exact live format and will apply after the next safe process
start. A missing world-state trajectory in this in-flight attempt must be
reported honestly and must never be mislabeled as a simulation signal.

### Provider-routing amendment (2026-07-15T17:50:11Z)

Grid graph ingestion exposed MiniMax `429/2062` throttling while the fallback
provider field was blank. Quotio itself is healthy and returned `READY` from a
low-effort live probe. Backend fallback now uses a fallback-only `antigravity`
identity over Quotio, never a Claude/Gemini CLI. Fallback clients retain their
resolved Quotio model even when primary fast/strong tiering is enabled; an
exact live `_try_fallback` call proves `antigravity` plus
`gemini-3-flash-preview` returns `READY`. The repair remains unloaded until the
healthy current graph reaches a safe terminal state; no mid-run restart is
permitted. The next service load MUST re-probe MiniMax primary, Quotio fallback,
and the exact model/reasoning tuple before recovering the remaining pipelines.

### Characterization amendment (2026-07-13T14:05:00Z)

The EV run did attempt translation; the isolated audit rejected it and correctly
removed the candidate, which made the conditional UI toggle disappear. The
repair slice now includes: correct numeric-token parsing, deterministic canonical
References handling, one bounded integrity/language retry for affected chunks,
an observable publication-gated on-demand translation task, report-bound
frontend caches/requests, and an explicit Markdown download beside PDF. These
are one cohesive Mandarin-delivery contract and will be validated against the
existing failed EV artifact before any new full forecast run.

### Scope amendment (2026-07-13T15:20:00Z)

The user added two release requirements. First, `scripts/start.sh` now defaults
to attached log/stage streaming while services remain durable; `--detach`
preserves non-attached operation. Second, after implementation gates pass, three
new full sessions will run in parallel: global humanoid/general-purpose robots
to 2035, global grid-scale storage to 2040, and global AI-compute/data-center
power infrastructure to 2035. Each requires independent heartbeat, evidence,
simulation, report-audit, visualization, and terminal-artifact verification.

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

## LOOP-015 — Deep-research cost, resilience, and sealed-contract recovery (2026-07-15)

### Task brief

The newest pipeline, `pipe_750d99882585`, spent roughly four hours in research, produced an under-length and judge-failing report, and then failed a 158 ms resume with `promoted research contract failed checksum validation`. The user explicitly authorized diagnosis, implementation, and safe continuation. The preserved evidence lanes MUST be reused; a healthy evidence stage MUST NOT be rerun merely to repair global synthesis or finalization.

### Reproduced causes and invariants

1. **Sealed report invariant:** `research_report_judge.json` binds the exact LLM-prose prefix. Post-judge citation lint changed seven citation variants in memory, and `_finalize_research_contract` attempted to promote those changed bytes with the old judge hash. The manifest checksums themselves were correct; the exception text was misleading.
2. **Network resilience invariant:** the run had no configured Tavily/Brave/Exa/Firecrawl/InfoQuest fetch fallback. Anonymous Jina was the sole fetch provider. The shared epoch admitted 450 fetch network calls; 317 failures were recorded, dominated by connect timeouts, without a provider-wide circuit breaker.
3. **Token-efficiency invariant:** 14,738,617 research tokens were reported. Adaptive gap closing consumed 7,720,291 (52.4%). Later turns replayed growing LangGraph histories (individual inputs exceeded 1.7M tokens), while full checkpoint/tool history was preferred over compact phase reports for the final evidence pack.
4. **Quality invariant:** deep output was approximately 7–8K words against the judge's 15K floor. Multipart outline parsing failed and fell back to a single completion. A longer refine candidate was discarded after another FAIL. The quality score ignored `research_report_judge.json`, so an explicit report FAIL was masked by an inflated 0.862 aggregate.
5. **Optional-market invariant:** unavailable prediction-market transport consumed about 17 minutes across pre-pass and final retries. A run-scoped transport failure MUST suppress redundant optional retries.

### Acceptance checks

| Requirement | Acceptance check | Expected evidence |
|---|---|---|
| Preserve judged bytes | Finalize a judge-bound contract after a lint proposal that would mutate citations | Promotion remains valid and the sealed report bytes are unchanged |
| Actionable contract failures | Tamper each representative artifact/binding | Diagnostic names the failing file, size/hash, optional set, or judge-prose binding |
| Bound Jina outage cost | Simulate concurrent transport failures | Shared circuit opens after the configured threshold; later calls fail over or fail fast without spending Jina timeout/network slots |
| Preserve subagent provenance | Record successful fetches from isolated lanes and export the shared ledger | Lane/global source union contains exact successful URLs with stable content hashes |
| Stop context replay | Exercise adaptive planning/evidence rendering | Adaptive workers use isolated threads/compact inputs, stop on no source gain or exhausted tools, and default to one bounded gap round |
| Produce deep-length reports | Force malformed outline output | Deterministic multipart outline remains active; deep minimum and judge floor both equal 15K words |
| Fail honestly on poor report | Feed an explicit report-judge FAIL | Aggregate quality cannot remain healthy/completed; preserved evidence is eligible for synthesis-only recovery |
| Avoid duplicate optional-market waits | Simulate all-query transport failure in pre-pass | Final collection writes an explicit transport marker without another degradation retry ladder |
| Recover newest run safely | Resume `pipe_750d99882585` after focused gates | Evidence lanes are reused, global synthesis/finalization reruns only as needed, and terminal artifacts pass manifest/judge/report checks |

### Implementation order

1. Repair sealed-contract/lint handling and add diagnostic validation plus focused tests.
2. Add shared fetch provenance and provider circuit/fallback behavior with multiprocess-safe tests.
3. Isolate late research turns, compact evidence assembly, bound adaptive passes, and stop on exhausted/no-gain work.
4. Harden multipart outline fallback, align length floors, include the real report judge in quality, and make judge FAIL synthesis-recoverable.
5. Add prediction-market transport short-circuiting.
6. Sync the runtime bridge/skills, run focused and proportional gates, restart the backend once, and use the existing pipeline's safe resume path exactly once.

### 2026-07-15 execution delta

- The first synthesis-only recovery was mathematically unable to pass: independent section writers generated 37K/43K words while the exact-byte judge cap was 200K characters. The implementation now enforces one aggregate 15K–22K-word budget, bounded per-call outputs, and a 600K-character judge envelope.
- Synthesis-only recovery reuses an unexhausted tool-budget epoch at the global epoch cap; it does not buy or consume another search/fetch epoch.
- MiniMax quota `429/2056` is now a typed provider outage. MiniMax retries first; only then may tool-free calls fail over to Antigravity through Quotio (`gemini-3-flash-preview`, low reasoning). A process-local circuit prevents every parallel section from repeating the same doomed primary wait.
- If both providers are unavailable, recovery stops after one attempt, preserves evidence/attempt diagnostics, and advertises a safe-resume blocker instead of zero-byte report or generic quality failure.
- External blocker: Quotio has the correct model alias and endpoint, but its sole active Antigravity account is disabled upstream. Do not restore archived accounts to evade provider enforcement. Resume only after MiniMax resets or the user restores an authorized Quotio account.
- The old grid-storage manifest is structurally valid but source-empty despite preserved real fetch activity. It is not publication-eligible; recover provenance deterministically or run only a bounded missing-source lane before synthesis.

### Rollback boundary

All edits remain scoped to the research bridge, research budget/fetch tools, orchestrator contract/recovery path, directly associated tests/config/docs, and runtime mirrors. Existing user changes and unrelated stages remain untouched. No new `/run` POST, commit, push, or site publication is part of this slice.

## LOOP-016 — Execution-envelope, artifact-integrity, and renderer-parity closure (2026-07-15)

### Objective and stopping conditions

Close the four independent-review blockers before spending another paid model or simulation run. Completion requires: one shared multipart output-token ledger covering outline, section attempts, retries, expansions, and summary; oversized/deep-empty synthesis cannot reach judge/extraction through concatenated notes; no legacy streamed actor JSON can be promoted; every report-facing quantitative renderer uses target-period precedence, explicit-range midpoint semantics, and external provenance for forecast rows; and fallback clients retain their Quotio endpoint/model under both strong and fast tiers.

### Execution order

1. Add deterministic regressions for each blocker and implement the smallest fail-closed fixes.
2. Sync the canonical bridge and runtime skill bundle to `deer-flow`, then prove byte/hash parity.
3. Run focused bridge, extraction, visualizer, fallback, lifecycle, and scenario suites plus Ruff/compile/diff hygiene.
4. Preflight MiniMax and Quotio-Antigravity, verify the grid dossier parses exactly four canonical scenarios, and resume `pipe_0e1b84d2682a` once through the existing `/resume` path.
5. Monitor every stage and terminal artifact; only after grid publication passes should the humanoid and AI-compute pipelines be recovered.

### Safety invariants

- A terminal or unhealthy pipeline is never converted into a new `/run`; preserved evidence and the existing resume contract are used.
- Test discovery/app construction MUST NOT reconcile or terminate production processes.
- A forecast chart point without an externally reachable `http(s)` source URL remains report-table evidence only; it is not rendered as a published forecast trajectory.
- A full canonical A/B/C/D scenario partition is authoritative. Any second full partition with different weights is a deterministic research-judge failure.

### 2026-07-15T19:01Z provider-capacity checkpoint

- The grid run passed the pre-resume code gates and advanced to RUN, but MiniMax exhausted Token Plan capacity and the only authorized fallback, Quotio Antigravity, subsequently exhausted/cooldowned its credentials.
- The simulator was deliberately stopped only after both config-bound platform checkpoints were verified. This prevents quota hammering and protects output quality without replaying research or completed simulation rounds.
- Current checkpoint floor is Twitter round 18/29; Reddit is round 23/29. A safe resume MUST use the existing pipeline and `SIM_RESUME`; no new `/run` is permitted.
- The active automation now probes providers first. It MUST remain idle while both are unavailable, issue at most one resume after a health pass, and verify checkpoint continuation before considering the recovery successful.

## 2026-07-21 — DeerFlow 2.0 architecture atlas, tldraw map, and bilingual README

### Intent

Replace the documentation emphasis on the original DeerFlow execution model with a source-backed account of the checked-in DeerFlow 2.0 super-agent architecture and DeepAgentForecast's DeerFlow-2-facing integration. Produce a new tldraw-native diagram and a detailed report without reusing the Foglamp map or presenting proposed Foglamp components. Update the English and Simplified Chinese root READMEs in parity while preserving the distinction between the currently assembled DeerFlow 2.0 research runtime and the pre-cutover `drf2` orchestration scaffold.

### Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| D1 | DeerFlow 2.0 is reconstructed from current source | Report traces entrypoints, lead-agent graph, middleware, subagents, skills, tools, sandbox, memory, MCP, model routing, state/checkpoint flow, streaming, and frontend/API surfaces with valid `file:line` anchors |
| D2 | DeepAgentForecast integration is explicit | Report distinguishes the optional local-only `deer-flow-2.0.0` source drop, assembled `deer-flow/`, tracked `deerflow_bridge`, and pre-cutover `drf2`, and maps exact inputs, outputs, transports, and authority boundaries |
| V1 | A new tldraw-native diagram exists | Editable `.tldr` scene plus SVG and PNG renders cover DeerFlow 2.0 internals and DRF integration; all shapes/connectors are valid and the full-resolution render passes visual inspection |
| R1 | English README reflects DeerFlow 2.0 | Architecture overview, diagrams, workflow, project layout, status, and documentation links describe DeerFlow 2.0 primitives without claiming an unverified DRF-2 cutover |
| R2 | Mandarin README is semantically equivalent | The same architecture/status content appears in natural Simplified Chinese with matching links and diagram references |
| Q1 | Documentation is reproducible and scoped | Markdown renders, local links and source anchors resolve, bilingual structural parity checks pass, tldraw artifacts validate, relevant tests/builds pass if README-visible tooling is affected, and unrelated dirty paths remain untouched |

### Execution order

1. Inventory the repository and read the root READMEs in full, then map DeerFlow 2.0 core runtime, integration seams, and README/status contracts in parallel.
2. Confirm the official tldraw file/export path, install only the required tooling in an isolated temporary directory, and create a reproducible scene generator.
3. Reconcile every delegated claim against current source and write a standalone DeerFlow 2.0 architecture report with explicit runtime-status labels.
4. Generate the `.tldr` canvas and SVG/PNG renders, inspect the result at full resolution, and revise layout/labels until readable.
5. Rewrite the English and Mandarin README architecture sections in parity, linking the new report and diagram while keeping setup/runtime statements source-accurate.
6. Validate source anchors, Markdown, links, bilingual parity, scene schema, renders, and scoped diffs; update `handoff.md` with exact evidence.

### Authorization and risk boundary

The user's explicit request authorizes downloading and installing tldraw tooling and editing documentation/diagram artifacts. It does not authorize launching, stopping, resuming, or replacing a forecast pipeline; changing provider credentials; mutating the generated `deer-flow/` runtime; committing/pushing; or publishing externally. Existing untracked `deerflow_bridge/.cache/`, `docs/architecture/`, and `docs/research/` paths are preserved. This written plan is the approved execution plan for the requested end-to-end documentation task.

### Scope correction — 2026-07-20T20:19:02Z

The user clarified that the primary deliverable MUST map the **entire DeepResearchForecast workflow**, with DeerFlow 2 represented as the Stage-1 research subsystem, rather than making DeerFlow 2 itself the top-level system. Original DeerFlow remains out of scope. The corrected critical path is:

1. Re-audit the existing whole-system atlas, dataflow inventory, LLM-call inventory, frontend/API routes, six-stage orchestrator, persistence surfaces, and downstream publication paths against current source.
2. Reconcile the full workflow as user/API → pipeline control → DeerFlow 2 Stage 1 → validated research handoff → ontology → temporal KG → persona/simulation preparation → OASIS run → ReportAgent/forecast extraction → visualization, translation, PDF, and resolution/monitoring outputs.
3. Produce a new whole-system tldraw canvas with the DeerFlow 2 model/tool/subagent loop nested inside Stage 1 and with every stable producer, transport, receiver, persisted artifact, LLM call family, and control/recovery path represented or indexed.
4. Update the whole-system report and machine inventories, then make both root READMEs lead with the whole DeepResearchForecast architecture while linking the DeerFlow 2 report/canvas as a subsystem deep dive.
5. Validate current-source anchors, routes, call-family coverage, Markdown links/headings, bilingual parity, tldraw structure/renders, visual legibility, scoped regressions, and worktree hygiene; obtain an independent settled-tree review before completion.

### Completion outcome — 2026-07-20T22:36:50Z

The corrected whole-system scope is implemented. `docs/architecture/DEEPRESEARCHFORECAST_SYSTEM_ATLAS.md` now follows the complete six-stage product from user/API admission through terminal completion and post-run feedback, with the embedded DeerFlow 2 model/tool/subagent loop expanded inside Stage 1 and DeerFlow 1.x excluded. The machine companions contain 90 material flows, all 101 Flask interfaces, 78 lookup records that normalize to a 99-family model census, and the deeper 41-call/65-interface DeerFlow 2 inventories.

The new primary tldraw canvas is reproducibly generated and exported as editable `.tldr`, SVG, and PNG. Its final structure has 197 shapes, 88 arrows, and 176 endpoint bindings at 7,080 × 4,766 pixels. The English and Mandarin READMEs now lead with the complete DeepResearchForecast architecture, distinguish live/default, conditional, manual/compatibility, and pre-cutover paths, and link the DeerFlow 2 subsystem deep dive without presenting the original DeerFlow architecture as current.

Validation covered JSON/count/source-anchor integrity, exact route coverage, local Markdown links and GFM parsing, bilingual architecture-link parity, excluded-scope scanning, both tldraw validators and dependency audits, original-resolution visual QA, 165 focused backend contract tests, all 31 frontend unit tests, the production frontend build, and `git diff --check`. No runtime, provider, pipeline, credential, generated checkout, publication, commit, or push was changed or invoked.

The final independent settled-tree, artifact, and native-resolution visual audits are clean. They reconfirmed the corrected Stage-5 failure corridor, Stage-6 publication/admin/multi-seed routing, render freshness, exact inventory/source/link reconciliation, dependency health, and removal of all task-generated browser traces and helper processes.

## 2026-07-22 — provenance-bound actor intelligence from research to simulation

### Intent

Make actor realism a first-class cross-stage contract. The workflow MUST deeply research every actor that can enter the simulation cast, carry actor-specific history and forward-looking evidence through the sealed research handoff, include decision-relevant plans and incentives in the unified dossier, compile a distinct safe role for each actor, and prove that the exact bounded context reaches the OASIS-consumed persona. “More detail” MUST not mean uncited biography, a shared generic prompt, hidden fabrication, unbounded token growth, or a new prompt-injection path.

### Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| AI1 | Every simulation-relevant actor has one canonical intelligence profile | Stable actor ID/aliases plus history, incentives, values, capabilities, motivations, preferences, alliances, competitors, plans, actions, investments, constraints, vulnerabilities, decision rules, and uncertainty/evidence gaps |
| AI2 | Actor intelligence is deeply researched and source-bound | Actor-specific searches/fetches or equivalent scoped evidence passes persist source IDs, as-of dates, confidence, contradictions, and coverage; no material claim is anonymous |
| AI3 | The unified deep-research report uses the actor research | Report sections explicitly analyze actor plans, incentives, investments, likely actions, conflicts, dependencies, and forecast implications; structured artifacts and prose share actor IDs |
| AI4 | Research reuse is exact | A versioned actor-intelligence artifact and content hashes are bound by the research manifest; stale, partial, mismatched, or tampered profiles cannot be silently reused |
| AI5 | Prepare compiles relevant context safely | `ActorRoleContract` receives allowlisted declarative fields plus a bounded actor-specific/global-context selection; sparse evidence remains explicit and imperative/model-control text is rejected |
| AI6 | The simulation consumes the intended context | The exact distinct prompt reaches Reddit `persona` and Twitter `user_char`; role/profile/cast/roster/runtime fingerprints validate at runner start |
| AI7 | Scale and failure behavior are explicit | Actor count, per-actor evidence/context budgets, concurrency, retry, source diversity, sparse fallback, and terminal failure semantics are deterministic and observable |
| AI8 | Cross-stage regression proof is green | Fresh, reuse, sparse, adversarial, multi-actor, and tamper scenarios pass focused tests; bridge/deployed skill parity and relevant broader suites pass |

### Execution order

1. Trace the current producer/consumer contract from DeerFlow actor discovery and report synthesis through manifest sealing, graph/cast merge, role compilation, profile generation, prepare outputs, and OASIS runtime fields.
2. Identify all first-principles gaps: identity, evidence/provenance, recency, contradictions, coverage, boundedness, distinctness, trust boundaries, stage reuse, observability, and failure semantics.
3. Write failing characterization/regression scenarios for the chosen versioned contract before implementation.
4. Implement one cohesive actor-intelligence artifact and its research/report integration, then extend `actor-role/v1` or version it only where the consumer contract requires new fields.
5. Bind hashes through manifests and prepare/runner validation; synchronize tracked DeerFlow skill/runtime overlays when their contract changes.
6. Run focused tests after each slice, then cross-stage actor/research/orchestrator/runner gates, compile/lint, bridge parity, and diff review. Do not launch a paid research or OASIS run without separate authorization.

### Initial assumptions and risks

- The existing fail-closed actor-role seam is the consumer foundation and SHOULD be extended rather than replaced.
- The current default global-synthesis topology normally omits Track B, so actor enrichment must not rely on a branch that is absent in default runs.
- Researching actors one-by-one can multiply model/search cost. The implementation must use a deterministic roster, shared evidence registry/cache, bounded per-actor passes, concurrency limits, and explicit coverage rather than unconstrained fan-out.
- The full research report is too large and contains untrusted prose. OASIS actors should receive an actor-specific evidence contract plus a deterministic relevant global-context digest, never raw unbounded report bytes.
- No live paid pipeline, provider mutation, publication, generated-runtime mutation, commit, or push is authorized by this implementation plan.

### Stage-1 semantic-provenance amendment — 2026-07-21T22:02:52Z

The first producer-to-runner implementation and its 410-test integration gate
proved structural delivery but did not yet prove that the *meaning* of every
actor claim was supported by the cited receipt. The release gate is therefore
expanded before documentation closure:

1. Cast admission MUST use an exact persisted tier and stable semantic identity;
   ambiguous tier strings, untiered retained rows, homonym collapse, and alias
   collisions fail closed.
2. Actor claims and relationships MUST carry a supporting source span or quote
   bound to the fetched receipt and content hash. Merely naming any fetched URL
   is not evidence that the URL supports the claim. Plans, actions, and
   investments additionally require explicit temporal and status semantics.
3. The dossier ledger and extracted actors MUST be claim-bound, not merely
   roster-bound. Stable per-actor/per-dimension claim projections, relationships,
   source ledgers, and question/run/checkpoint lineage participate in sealing and
   resume validation.
4. Evidence gaps MUST record bounded research attempts and receipts; Track-A
   receipts cannot silently ground Track-B actor research.
5. Every Stage-1 reinjection of tool, model, dossier, report, or judge-gap text
   MUST cross a whole-document sanitizer and an explicit non-executable evidence
   boundary before model use. Actor-judge PASS MUST attest the exact complete
   input and a strict finite scorecard.
6. Global synthesis MUST route bounded per-actor blocks to a dedicated cast-wide
   owner and deterministically verify that every Tier-1/2 actor and each
   decision-critical behavior family appears in the final report with citations.

These additions are not optional hardening. They close reproduced paths that
could otherwise produce a polished but semantically ungrounded actor simulation.
No full-suite, README, inventory, or final-audit result counts as acceptance
evidence until the corresponding adversarial regressions pass.

### Completion outcome — 2026-07-22T02:01:06Z

All actor-intelligence acceptance criteria are implemented and verified. The
settled path is one shared baseline Track-B actor research plane with exact
tiering/semantic identity, seventeen source-bound dimensions, typed exhausted
gaps, behavior-family coverage, claim/relationship/lineage seals, and a
cast-wide report owner. ONTOLOGY and GRAPH preserve only canonical structural
identity plus admitted claims; PREPARE creates sealed actor-context/v1 packs,
deterministically compiles role-only actor-role/v2 behavior, derives current-v1
configuration without legacy flat overrides, and admits only explicit-public
shared world evidence. The final config/cast/context/platform-role closure is
resealed after authorized outer-PREPARE mutations, revalidated on reuse and at
runner admission, and the direct child hashes the exact bytes it parses.

The documentation suite now describes the entire DeepResearchForecast system,
with DeerFlow 2 as the deep Stage-1 subsystem: 95 material flows, all 101 Flask
routes, 100 normalized model-call families, 42 DeerFlow 2 call families, and 68
DeerFlow 2 interfaces. Official tldraw validation reports 198/89/178 whole-system
shapes/arrows/bindings and 150/66/132 DeerFlow 2 shapes/arrows/bindings. The
final backend collection is 2,815 tests; the feature gate passes with 2,803
passed, 11 expected xfails, and the one unchanged actor-unrelated language-purity
fixture deselected. No paid research/OASIS run, provider mutation, publication,
generated-runtime edit, commit, or push occurred.
