# LOOP-009 result — DeerFlow value per token and artifact delivery

## Hypothesis

The workflow was not failing because DeerFlow lacked capability. It was multiplying overlapping breadth work and then dropping high-value outputs at stage boundaries. The correct step change was to make parallel work gap-owned, compact durable evidence, and give markets/visuals one canonical delivery path.

## Baseline evidence

- `pipe_f23527f7d903`: research 2.631 h and about 79.75M measured tokens; graph 8.625 h; report 0.927 h. Research emitted 3,391 searches, 962 fetches, 1,428 no-results, and about 1,075 exact repeated query payloads.
- One six-gap adaptive tail in that run consumed about 44.1 minutes and 15.817M tokens, roughly 44% of that track.
- `pipe_a8986bffd918`: research about 82.71M measured tokens; graph 7.227 h; report 4.64 h/5.99M tokens.
- Across the representative three, graph used about 48.5% of active wall time, research 34.1%, and report 16%. Research is the measured token sink; graph is often the wall-clock sink but graph model calls remain unattributed.
- Relevant Polymarket markets were found inside agent turns (`691340` AI bubble at 15.45%; `609655` US recession at 10.5%) and then lost because the final collector started over. The reports had zero structured market anchors.

## Implemented contracts

1. One KIQ breadth plane is active by default. Three outer tracks share a default nine-worker envelope (three harness workers per track) rather than opening fifteen harness workers plus bridge fan-out.
2. Harness compaction starts at 80K tokens with a forecast evidence-ledger prompt, retains 16K recent tokens, externalizes web fetch output after 12K, and preserves only the three recent workflow skills.
3. `/deep-research` and `/actor-ontology-research` activate deterministically; scoped researchers can call `prediction_market_search`; bridge clients advertise only workflow-relevant skills.
4. Final dossier wording has a quality floor and no word/character ceiling. Per-call section, context, convergence, and network budgets remain safety controls.
5. Prediction-market tool responses append machine data to a 0600 JSONL ledger; normalized query payloads use a process-local single-flight cache; finalization unions refresh + in-loop candidates through one relevance gate.
6. Track merge unions disjoint markets, keeps the freshest quote and provenance, merges selected price histories, and preserves explicit empty/irrelevant/transport-failure status.
7. Report-time recovery uses LLM-derived market-title queries plus an explicit relevance score and fails closed on unscored rows; recovered data is persisted in the report bundle.
8. Dossier API/UI exposes market signals independently of exact forecast anchors.
9. Research visuals now include market-implied probabilities and source tier/freshness alongside actor, timeline, and quantitative charts. Plotly output has stable IDs and PNG/Matplotlib degradation; Mermaid is not a delivery fallback.
10. Customer reports strip internal Run Telemetry, rewrite residual internal method language, and reject forecasts that merely predict a market contract resolving YES/NO.

## Existing artifact repair

- The last three reports were regenerated offline after new timestamped backups: 9/8/9 visuals, 283/189/361 inline citation markers, 38/25/25 referenced sources, zero dangling citations, and zero residual mechanics flags.
- `report_a03be154febc` removed the circular `F6` Polymarket-contract outcome and now contains 12 valid binaries. Internal Run Telemetry was removed from both language variants; the telemetry artifacts remain separate.
- Browser inspection of the latest report found nine images, zero broken images, a Visual Annex, and no Run Telemetry heading.

## Verification

- Python compilation and YAML parsing passed.
- Focused market producer/collector/merge/report/API/lint/backfill/visual/bridge tests passed.
- Forecast renderer: 12 focused tests, Ruff clean, deterministic HTML, real 2400×1500 Plotly PNG/HTML smokes, and Matplotlib fallbacks.
- Updated `deep-research`, `prediction-markets`, and `forecast-visuals` skills each passed `quick_validate.py` and were synchronized into the active DeerFlow runtime.
- Frontend: 14/14 unit contracts and Vite production build (699 modules) passed; the pre-existing mixed static/dynamic `pendingUpload.js` warning remains.
- `git diff --check` passed at the pre-final checkpoint.

## Honest boundary

No paid research/graph/simulation/report run, deployment, commit, or push was performed. These checks prove contract correctness and repair existing artifacts; they do not prove a post-change wall-time/token delta. A comparable authorized run must capture research wall/tokens/useful-result yield, graph wall/calls/bytes, live progress cadence, market anchors, and final PDF/report identity before changing the production baseline.
