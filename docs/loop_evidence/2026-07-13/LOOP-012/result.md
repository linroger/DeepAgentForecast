# LOOP-012 — Stage forensics, recovery hardening, and data-first visuals

**Date:** 2026-07-13
**Scope:** Read-only comparison of the three newest EV pipelines, bounded code fixes, and isolated artifact replays.
**Completed run:** `pipe_91aaf91f6392` → `proj_0b164d997b9c` → `mirofish_204aa50069334f1b` → `sim_85b123350ee3` → `report_9147b3f6a0a9`
**Historical controls:** `pipe_a362c6f3c49d` (cancelled during pre-fix research) and `pipe_1cf8b18d71a0` (failed during pre-fix global synthesis).

## Outcome

The current search APIs were not the cause of the reported zero-source failure. The two historical runs failed before they performed a real search/fetch turn: their model turns returned 99-character provider text with zero tool calls and zero usage, so Tavily/Brave never had work to execute. The completed control run subsequently made 346 research tool calls, 730 network searches, and 419 network fetches and produced 34 canonical sources. That proves the current research/search path can work end to end.

The completed run nevertheless exposed seven active reliability or product-quality defects. This loop fixes all seven within the existing contracts:

1. Publication could bind a completed report to a hollow or mismatched simulation.
2. Graph fan-out permanently dropped transient 429 failures and did not classify common Chinese invalid-JSON errors for fallback.
3. An explicit research-judge `FAIL` could be overridden by high component scores, and the judge could see pre-finalized citation bytes.
4. Reused/retried stages accumulated idle downtime as apparent compute time.
5. Durable simulation state could remain at round zero after the authoritative run summary completed 19 rounds.
6. Graph executor calls could lose telemetry context and fall into the global bucket.
7. Reader-facing charts used fabricated or internal proxies instead of forecast-domain evidence.

The immutable published run/report bundle was not modified. Both report-level and research-skill visualizers were replayed into `/tmp` using copies of the original structured artifacts.

## Stage-by-stage comparison

| Stage | Completed run evidence | Historical comparison | Defect or risk | Implemented hardening |
|---|---|---|---|---|
| Research | Two of three requested lanes survived. The final run records 18,740,529 tokens, 346 tool results, 1,276 budget attempts, 730 searches, 419 fetches, 38 denied calls, 33 fetched sources and 34 canonical structured sources. Research quality is 0.864 and explicitly degraded for one outline fallback plus budget denials. | `pipe_a362c6f3c49d` produced repeated 99-character, zero-tool turns and 850-character empty evidence packs before cancellation. `pipe_1cf8b18d71a0` preserved two 892-byte evidence packs whose `sources.json` files were each `[]`, then failed source-count validation and both synthesis attempts. | The historical provider/factory path was already superseded, not a Tavily/Brave outage. In the successful run, `research_report_judge.json` says `verdict: FAIL`, while `handoff/meta.json` recorded `passed: true`; citations could also be finalized after judging. One lane timed out after 1,800 seconds and breadth remains expensive. | `report_passes()` now treats explicit `FAIL` as authoritative. Citation finalization now precedes the initial judge and every adopted re-judge. Regression tests prove the judge sees finalized bytes. Existing global budgets, immutable lane packs and no-full-rerun recovery remain intact. |
| Ontology | Completed, with 12 actors, 11 relationships, full incentives/worldview coverage for tier-1/2 actors, and all edges valenced. Twelve graph seed edges were generated. | Both historical controls stopped in research; ontology correctly remained pending and emitted no downstream artifacts. | No new ontology correctness failure reproduced. Its recorded 13,561-second wall time was mostly restored-stage idle time, not ontology compute. | Reused-stage completion preserves the original completed interval; retried stages reset start/finish timestamps before new work. This prevents restore/resume downtime from being charged to ontology. |
| Graph | Completed but honestly degraded: 14 of 44 chunks skipped (31.8%). The retained graph has 119 nodes, 238 edges, one component, 100% core-actor survival, and a successful prune postcondition. | Historical controls never entered graph. Local graph-ingestion diagnostics showed transient rate-limit losses dominated the skipped set, plus schema/JSON parse failures. | Concurrent ingestion counted transient 429s as final skips, retried independently, and did not recognize common Chinese invalid-JSON text. Blocking executor calls also lost context-local telemetry. | Parse/schema failures now use the existing fallback path. Rate-limited episodes are retained in input order, share one bounded cooldown, replay serially once, and are counted only after replay. `GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S` is documented and clamped to 0–60 seconds. Executor calls copy the current context. Persistent failures still surface as degraded; the all-failed hard guard remains. |
| Prepare | Completed and restored against `sim_85b123350ee3`; all actor-role artifacts and simulation config were present. | Historical controls never entered PREPARE. | The prior recovery sequence had already shown that a rebuilt PREPARE identity could be paired with an old RUN. Timing also counted the 11,842 seconds between the original start and later restore. | Existing identity checks remain. The generalized retry reset now clears stale start/finish/error/progress before genuine recomputation, while a valid reuse retains its original duration rather than manufacturing a new one. |
| Run | Authoritative `run_summary.json`: 12 agents, 19 rounds, 698 actions, 617 organic actions; both platforms report 228 LLM calls and zero errors. | Historical controls never entered RUN. | Durable `state.json` said `status=completed` but `current_round=0`, contradicting the authoritative 19-round summary. The terminal log also conflated seed round zero with executed simulation rounds. | Terminal orchestration reconciles durable state from authoritative run progress/platform results before saving. Logs now report `rounds_executed` and round-zero seed buckets separately. Publication also rejects zero/missing agents, rounds, total actions or organic actions. |
| Report | `report_9147b3f6a0a9` has 11/11 sections. The final read-only audit passes exact disk/memory hashes, five mutually exclusive scenarios summing to 1.0, 12 binary forecasts, and 90/91 resolved quantitative citations (0.989). Actual report telemetry is 605.43 seconds, 130 section LLM calls, and 2,386,683 tokens. | Historical controls never entered REPORT. Earlier attempts in the successful pipeline exposed two valid publication-gate failures before final repair: probability contradictions and 0.05 quantitative citation coverage. | Pipeline telemetry reported 11,606 seconds for REPORT and 81,493 seconds total because retry/reuse timestamps retained earlier attempts and idle gaps. Default visuals included influence/salience, a fake tornado, proxy evidence weights, fabricated binary confidence, and a false ensemble-spread claim. Numeric parsing also turned `247,226` into 247 and `1,808,511` into 1. | Retry timestamps reset; reused stages preserve truthful intervals. Publication now validates exact report/simulation/run-summary identity and non-hollow activity. The report visualizer emits comparable quantitative panels, forecast revisions, exact-date timelines, scenarios and binary probabilities; it omits the three proxy charts by default, never invents confidence/error bars, and keeps source/as-of/definition metadata. |

## Ranked root causes and disposition

| Priority | Finding | Current disposition | Regression evidence |
|---|---|---|---|
| P0 | Publication accepted a report without proving its simulation was the same completed, non-hollow run. | Fixed fail-closed with canonical ID/status/activity checks. The real degraded-graph EV run remains publishable because its simulation is substantive. | `test_export_demo_site_data.py` (23 focused tests) |
| P1 | Graph 429 and schema-parse failures were prematurely permanent under parallel fan-out. | Fixed with existing fallback plus one shared bounded serial replay and final-only skip accounting. | `test_wave9_kg.py` (38 tests) |
| P1 | Explicit research-judge `FAIL` was ignored when numerical scores were high. | Fixed; the explicit verdict is authoritative. | `test_bridge_prediction_markets.py` |
| P1 | Judge input could precede citation finalization. | Fixed at both initial and refined/global-synthesis judge boundaries. | Citation-finalization byte-order regression |
| P1 | Stage wall time represented recovery downtime instead of active work. | Fixed for both reuse and retry paths. | Orchestrator timing regressions, including preserved 42-second reuse |
| P1 | Durable terminal simulation state contradicted the completed summary. | Fixed by terminal reconciliation before state persistence. | Orchestrator simulation-state regression |
| P1 | Default visuals answered internal-ranking questions and altered evidence semantics. | Fixed with data-first selection, strict denominators, honest uncertainty and exact dates. | Visualizer, skill-renderer and artifact replay suites |
| P2 | Graph worker telemetry escaped into `_global`. | Fixed by copying `contextvars` into each blocking executor call. | `test_graph_optimizations.py` |

## Visualization contract and replay

The new default question is: **what decision-relevant forecast fact does this chart let the reader see faster than prose?** A chart is emitted only when it has real rows, comparable units/denominators, provenance, and honest uncertainty semantics.

### Report visualizer replay

Final isolated output: `/tmp/drf-loop012-ev-report-release.BpNRpU`

Generated from the immutable EV report/research/simulation inputs:

- `scenario_probabilities`
- `binary_forecast_dotplot`
- `timeline_lanes`
- `actor_network`
- `source_mix_sunburst`
- `quantitative_claims`
- `forecast_revisions`

Explicitly skipped:

- `actor_influence_salience` — internal proxy, not reader-facing evidence
- `driver_tornado` — no actual sensitivity analysis
- `contested_claims` — proxy evidence weight, not a forecast outcome

Checks performed:

- Grouped integers parse as `247226.0` and `1808511.0`, not 247 and 1.
- Quantitative panels contain one explicit denominator each and distinguish reported circles from published forecast/target diamonds.
- BNEF's US 2030 EV-share vintages render as the actual 48% → 27% → 17% revision path.
- Month-end dates and quarter windows retain their source meaning.
- Scenario titles claim ensemble spread only when real intervals exist.
- Binary charts show declared probabilities and only show confidence when the source declares it.

### `forecast-visuals` skill replay

Final isolated output: `/tmp/drf-loop012-ev-skill-release.a7l4Xe`

Generated five eligible products: actor relationship network, 25-event timeline, three-panel comparable forecast benchmarks, forecast revisions, and source quality/freshness. Prediction-market probability was correctly skipped because this run had no matched market rows. Every static PNG and interactive HTML exists. Visual inspection confirmed:

- all 25 timeline events appear in the numbered key;
- six deterministic layout lanes avoid dense-marker collisions without modifying dates;
- the quantitative legend no longer collides with panel titles;
- the forecast-revision chart presents the sourced vintage sequence directly;
- actor size never comes from arbitrary numbers embedded in prose;
- source freshness uses one run-level anchor instead of a different implicit clock per row.

The rewritten skill requires reader-question selection, actual rows, denominator/unit comparability, provenance, and honest uncertainty. `references/chart-patterns.md` supplies concrete patterns for regional adoption, battery costs, forecast revisions, policy timelines, scenarios/binaries, concentration, consumer structure, technology routes, supply-demand, and genuine sensitivity/uncertainty. It explicitly rejects chart quotas, influence-vs-salience bubbles, fake tornadoes, mixed-denominator axes, and invented error bars.

## Efficiency and observability effects

- Transient graph pressure now incurs at most one shared cooldown and one serial retry per failed episode, rather than immediate permanent loss or unconstrained parallel retry amplification.
- Reused stages report their original work interval; true retries begin a fresh interval. This removes hours of recovery idle time from stage-cost attribution.
- Terminal state is reconciled once rather than forcing later consumers to infer whether `state.json` or `run_summary.json` is authoritative.
- Publication fails before copying static artifacts when IDs, terminal states or activity prove the run is hollow.
- Visual generation fails closed per chart and records why a chart was skipped; it does not spend chart slots on manufactured proxies.

## Independent-review corrections

The first frozen-diff review found no Critical issue and identified eight release-blocking edge cases. All eight were reproduced and corrected before the final replay:

1. Publication now binds project, graph, simulation, report, pipeline, and available run identities instead of accepting a report/simulation pair in isolation.
2. Every final LLM-authored research-prose mutation is citation-finalized and judged again. The deterministic market/chart appendix is attached only after the judged prose has been sealed.
3. A judge result is admissible only when it contains exactly the seven required dimensions, each with a finite score in the closed interval 0–5. Truncated or mismatched judge input cannot pass.
4. Quantitative comparison panels require a common source, parseable as-of provenance, metric, unit, denominator, definition, and time basis. Rows that do not prove comparability are skipped rather than placed on a shared axis.
5. Forecast-revision lines require one publisher/outlook family, fixed target horizon, metric, unit, denominator/definition, and distinct publication vintages.
6. Reused stages with only one trustworthy endpoint collapse to zero duration instead of manufacturing elapsed time.
7. Aggregate round counts no longer imply that every enabled platform completed; platform status remains `unknown` without platform-specific terminal evidence.
8. A future-dated reported actual cannot be reclassified as a projection by a negative staleness calculation.

The HTML renderer also normalizes generated Plotly line-ending spaces. This makes self-contained interactive artifacts pass repository hygiene checks without changing their rendered behavior; a direct browser probe confirmed a live Plotly graph root, SVG content, stable chart ID, and an empty error console.

### Final adversarial pass

A second independent pass found six Important edge cases and no Critical issue. The release was reopened and all six were corrected:

1. Strict publication fails closed if the exact bound graph is unavailable; it never rebuilds and substitutes a nondeterministic graph under the old report/simulation audit.
2. `projects/{project_id}/project.json` is required and its exact project/graph identity is checked both at the gate and immediately before ontology export.
3. Required deep-research reuse validates the complete seven-dimension scorecard, exact untruncated judge input, exact judged-prose prefix, and consistent judge metadata. The historical pre-fix EV handoff now correctly returns `False` from the reuse validator because its explicit `FAIL` was recorded as passed and had no prose identity.
4. An incomplete required final judge now fails the research process. A late refinement or triangulation candidate can replace the current report only if it passes and does not lower any dimension of an already passing scorecard.
5. Legacy run-state files that omit platform-enable fields load them as unknown, allowing terminal reconciliation to use the durable simulation state's enable flags instead of reporting an enabled lane as disabled.
6. Canonical `p_low`/`p_high` values render as a declared uncertainty interval; only matched ensemble `min`/`max` values render as ensemble spread. Static and interactive charts use distinct labels, colors, markers, legends, and hover text.

Targeted re-review of those six seams found no remaining Critical or Important defect.

## Reader-visible demo refresh

The tracked `docs/demos/ev-2035` presentation now contains seven report PNGs and five dossier chart families, each with static PNG and self-contained HTML where applicable. Published report order is forecast-first: revision history, comparable quantitative benchmarks, declared scenario/binary probabilities, and exact-date milestones. Actor topology and source quality remain available as context and methodology, not as substitutes for forecast outcomes.

- Both `actor_influence_salience.png` and `contested_claims.png` were removed from the published bundle.
- `forecast_revisions.png` plus its interactive counterpart were added.
- The dossier exposes all 25 exact timeline events and the strict comparison contract.
- `meta.json` records 25 published artifacts; every SHA-256 matches.
- All 17 local Markdown asset links resolve.
- Browser checks loaded all seven report images and all five dossier images at natural width 2400, opened the interactive forecast-revision chart, and produced no warning/error console entries.

## Remaining risks (not concealed)

1. One research lane can still dominate wall time until the 1,800-second task timeout. Adaptive lane quorum/cancellation is a larger research-scheduler change and should be measured on a separately authorized live run.
2. Historical report retries do not retain complete section-level telemetry for every abandoned attempt. The latest report telemetry is trustworthy, while cumulative attempt attribution is still coarse.
3. Graph ingestion remains best-effort after one bounded replay. Persistent provider pressure can still produce a degraded graph; the health gate continues to expose the exact skipped ratio rather than claiming full success.
4. The publication and visualization fixes are proven deterministically against the completed EV artifacts. A new paid research/graph/simulation run was neither necessary nor launched.

## Verification ledger

The final frozen implementation passed the following gates:

- Visualization/research combined regression: 185 tests passed.
- Graph/publication/context focused regression: 28 tests passed.
- Reuse/retry timing regression: 3 tests passed.
- Final timeline layout regressions: 4 tests passed.
- Complete backend pytest suite: passed at 100% with exit code 0; only the known shutdown-time closed-stream logging noise appeared after pytest completed.
- Frontend tests: 14/14 passed.
- Frontend production build: passed; only the established Vite static/dynamic `pendingUpload.js` advisory remains.
- `uv sync --check`: passed and proposed no environment changes after Plotly, Kaleido, and Matplotlib were declared and locked as production visualization dependencies.
- `npm run check:env`: passed.
- Python compilation, source/deployed bridge parity, skill quick validation (`Skill is valid!`), and `git diff --check`: passed.
- Changed-file Ruff findings improved from the 95-finding inherited baseline to 85; no new lint debt was introduced. Repository-wide legacy findings remain outside this bounded slice.
- Real EV research-skill replay: five valid chart products, no fabricated market chart.
- Report-level isolated replay: seven valid products, all three proxy charts explicitly skipped.
- Static demo validation: 25/25 artifact hashes and 17/17 local Markdown links passed.
- Immutable input proof: all 86 sealed source report/handoff files remained byte-identical through every replay.
- Final post-review report replay: seven products at `/var/folders/sh/z3hsvy7526b8xcjjphdq8hs00000gn/T/drf-loop012-ev-report-final.uo0uggzj`; all 40 files in the source report directory remained byte-identical.
- Independent final re-review: no Critical or Important defect remained in the six scoped release blockers.

No new paid research, graph, simulation, or report run was launched. The deterministic replay proves the corrected consumers against the successful completed EV run while preserving its audited source artifacts.
