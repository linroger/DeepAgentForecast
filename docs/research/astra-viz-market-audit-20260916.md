# Visualization and prediction-market integration audit

Audit time: 2026-09-16 UTC. Source baseline: `b2a2e10` in `/Users/rogerlin/.codex/worktrees/drf-astra-improvements`. Owner: `viz_market_audit`. This is a read-only source audit with synthetic offline reproduction. No application runtime files, original checkout files, saved runs, provider state, or public market APIs were changed or contacted. Only this external evidence directory was written. Root owns plans/UI edits concurrently.

## Recommended next user-visible slice

**Preserve market quote provenance and show quote freshness consistently from research through the final report and charts.** This is a demonstrated data-quality defect, not a cosmetic preference: the current forecast extractor overwrites the preserved research price with the refreshed price, discards the per-row refresh failure flag, and has no timestamp contract. The UI can consequently present a historical price without its failure state and label an incorrect number as the research price.

Use one additive, version-compatible market quote projection with explicit research price, research observation time, latest successful quote time, and refresh status. Old artifacts with no timestamp must say “time unavailable”; never manufacture a time from report generation. Retain the existing semantic-equivalence gate and market-influence audit. A refresh failure must retain its last known price and original time without acquiring a “live/current” label. Do not automatically re-fetch on every UI render. This slice improves interpretation and provenance without requiring extra provider/model calls.

Acceptance scenario: a synthetic market has research price 34%, then successfully refreshes to 41%, then fails its final refresh. Across snapshot → anchor → comparison → report markdown → API payload → Vue row and chart annotations, research price remains 34%, latest known price remains 41%, divergence from a 55% model remains +14 percentage points, and the failed refresh/last successful quote time remain visible. A second market refreshing successfully in the same batch must not hide the first market's failure. Numeric zero stays 0%; missing/invalid probabilities render unavailable.

## Current connected path

1. `deerflow_bridge/market_tools.py:170` normalizes tool-discovered markets and retains outcome names, prices, token IDs and deep links. `deerflow_bridge/deerflow_research.py:14632` duplicates the finalization normalizer; `:15100` begins snapshot finalization and `:15369` writes selected-market history. Relevance scoring and event caps already exist; new work must retain them.
2. `backend/app/services/pipeline_orchestrator.py:6318` unions track snapshots by `market_id`, retains track provenance, selects mutable fields from the freshest track, and caps relevant markets per event. `:6486` merges selected histories. `:12017` integrates these into the handoff, and research artifact specifications register the market files at `:9524`.
3. PREPARE consumers `simulation_config_generator.py:1336`, `:1776`, and `oasis_profile_generator.py:1401` read the matching simulation handoff and add bounded market priors. They do not supply evidence that the simulation becomes a calibrated forecaster.
4. `report_agent.py:2568` loads the handoff snapshot and re-quotes it; `:2721` refreshes again before binary extraction. `forecast_extractor.py:1685` builds canonical anchors and `:1918` builds comparisons. Semantic proposition matching, source-bound contract hashes, and audit of market-induced probability movement already exist and are valuable.
5. `report_agent.py:8086` gathers structured chart inputs and locates the research handoff; `report_visualizer.py:4703` loads price histories, `:4837` normalizes anchors, `:3125` produces interactive history, and `:2611` produces static history. The renderer uses deterministic Plotly/Matplotlib output, bounded chart density, CJK font checks, and source/interval distinctions; a broad visual rewrite would duplicate existing work.
6. `api/research.py:516` exposes the canonical market snapshot and status. `api/report.py:398` exposes structured forecasts behind the publication gate. `DossierViewer.vue:193` displays research-market cards; `ForecastReport.vue:172` shows market divergence; `BinaryForecastTable.vue:1` exposes complete binary rows and market links. `utils/vizManifest.js:43` folds safe PNG/HTML pairs; manifest/chart API tests verify containment and script sandboxing.
7. `backend/scripts/resolution_monitor.py:260` converts resolved market quotes into binary truth and Brier records, using `price_at_research` for the original market baseline. This makes preservation of the research quote consequential beyond visual labels.

Paths above are relative to the baseline worktree, not the original checkout.

## Confirmed findings and scope

### VM-01 — P1: research quote is overwritten and row-level staleness is dropped

- `forecast_extractor.py:1685–1733` `_build_market_anchor` unconditionally sets `price_at_research = implied_yes_prob`. A valid input `{implied_yes_prob:0.41, price_at_research:0.34}` becomes an anchor with both fields 0.41.
- The same projection omits `requote_failed`, observation time, and quote time. `build_market_comparison` at `:1918` copies the already damaged research price and has no freshness fields.
- `report_agent.py:2715` marks the whole pack stale only when **all** rows have failed re-quotes. A mixed batch can contain failed rows while the pack header says current pricing. `prediction_markets.py:583` renders no per-row failure flag. The final deterministic cross-check at `report_agent.py:1383` says “live” even for stale snapshots.
- `frontend/src/utils/binaryForecasts.js:114` and `BinaryForecastTable.vue:109` model/display research price but no freshness. `ForecastReport.vue:172` market tiles show neither timestamp nor market link/equivalence. `report_visualizer.py:2967`, `:3125`, and `:3186` chart prices without observation-time/status labels.
- **Executed reproduction:** `viz-market/reproduction-results.json`, key `research_quote`. 34% becomes 41%; failure and timestamp keys disappear. `ui-reproduction-results.json` confirms the Vue row projection also drops a supplied failure/time.
- **Benefit:** fixes a routine refresh path and preserves an honest market baseline in later calibration; no token savings claimed.

### VM-02 — P1: a missing token entry shifts the Yes/No mapping

- Three normalizers filter blank token IDs **before** looking up the original outcome index: `backend/app/utils/prediction_markets.py:550`, `deerflow_bridge/deerflow_research.py:14691`, `deerflow_bridge/market_tools.py:221`. `_pm_yes_leg_token` at `deerflow_research.py:15361` repeats this on legacy rows.
- **Executed reproduction:** outcomes `["Yes","No"]`, tokens `["","NO_TOKEN"]` results in `clob_yes_token_id="NO_TOKEN"` in both backend and finalization producers and in the legacy helper. This is the same wrong-leg history class that earlier reversed-order tests meant to prevent, now triggered by missing positional data.
- The tool normalizer is source-confirmed to use the same expression; its exact malformed-input case was not separately executed.
- **Acceptance:** preserve array positions, require an unambiguous Yes index and a nonblank token at that exact index, and fail closed when alignment is malformed. Include forward/reverse ordering, blank first/second token, null, mismatched cardinality, duplicate Yes names, and no explicit guess. Healthy forward/reverse cases must remain unchanged.
- **Benefit:** prevents a market-No series being mislabeled as Yes. No saved run is asserted to contain the malformed fixture.

### VM-03 — P1: resolution parsing accepts contradictory/invalid final prices as truth

- `_parse_resolution` in `prediction_markets.py:144` accepts the first outcome price ≥0.99, checks `closed`, but never requires finite probabilities in [0,1], a unique winner, or the opposite leg ≤0.01. `_RESOLVED_PRICE_LO` is defined but unused there. UMA status is returned diagnostically and ignored even when explicitly disputed.
- **Executed reproduction:** closed markets with `[0.995,0.995]`, `[1.2,-0.2]`, and `[0.995,0.005]` with `umaResolutionStatus="disputed"` each return `resolved=True` and `resolved_outcome="Yes"`.
- `resolution_monitor.py:275` then uses any non-null returned Yes price ≥0.5 as `y=1`. Thus contradictory or out-of-range data can enter the historical calibration ledger. This propagation is source-traced; no ledger writes were performed.
- **Acceptance:** reject contradictory, nonfinite and out-of-range data; require an unambiguous binary winner and compatible final settlement evidence. The exact production settlement-status policy needs the official API contract checked before implementation; this audit made no live/documentation network requests. Legacy no-status handling should be a documented compatibility policy, not silently assumed finality.

### VM-04 — P2: dashboard invents zero for missing values; probability views admit out-of-range values

- `ForecastReport.vue:758` uses `Number(v)` and accepts null, empty string, false and empty array as zero. `divergencePP` at `:825` has the same defect. `dashScenarios` accepts those as valid probabilities; `ensembleInfo` at `:801` similarly turns null agreement/spread into numeric zero.
- **Executed reproduction:** null/empty/false/[] each display `0%` and `0pp`; -0.2 and 1.2 display `-20%` and `120%`. The existing binary-table helper correctly rejects null/boolean/array values but also permits probabilities outside [0,1].
- `report_visualizer.py:2986` and `:3204` use permissive numeric parsing without a probability-range gate; axes clipped to [0,1] can hide an invalid point instead of explaining its absence.
- **Acceptance:** share strict finite probability coercion across cards/tables/charts, retaining real 0 and 1 while labeling missing/invalid values unavailable. Signed divergence has a separate [-1,1] domain. Do not coerce missing confidence/agreement to zero or claim “within band” when divergence is unavailable.
- **Benefit:** credible presentation of partial/legacy runs and API faults; this is a safe small companion to later report UI work, not evidence about current saved forecasts.

### VM-05 — P2: malformed history is silently converted into certainty, and infinity can discard a series

- `deerflow_research.py:14581` and `prediction_markets.py:420` parse history timestamps/probabilities but reject only NaN, not infinity; `int(float('inf'))` raises. Price bounds are not checked there.
- `report_visualizer.py:4875` clamps historical probabilities into [0,1]. An impossible -0.25/1.4 series becomes a valid-looking 0%/100% curve.
- **Executed reproduction:** `history_boundaries` records [-0.25,1.4] transformed into [0,1]; bridge history with an infinite timestamp raises `OverflowError`.
- **Acceptance:** reject invalid points, sort/deduplicate valid timestamps with deterministic precedence, require ≥2 distinct timestamps, keep true 0/1, and expose skipped/invalid-point counts. Preserve timeline UTC labels and show history observation horizon separately from latest quote time.

## Source-confirmed efficiency/design candidates (not measured production bugs)

1. **Verified-empty snapshots trigger redundant report retrieval.** `ReportAgent._load_prediction_markets` at `:2595` recognizes only nonempty rows as reusable. An existing canonical `{markets:[], status:{state:"verified_empty"}}` falls through to new query-derivation LLM work, sequential public searches and relevance scoring. Each separate/ensemble ReportAgent can repeat it. Distinguish a valid recent empty result from transport failure or missing/expired input, reuse it under an explicit freshness policy, and retain observed absence. Acceptance: recent verified-empty causes zero calls; failed/missing/expired state follows the designed recovery path; an explicit refresh remains possible. This path is source-traced, not a measured fraction of 150M tokens.
2. **History window misleading configuration.** Bridge defaults to `days=90` and API `interval="1d"` (`:15378–15395`); the backend method's own docstring at `:424` says interval is a lookback window, not a sample stride. A 90-day client cutoff cannot recover data omitted by a one-day server window. Confirm current official API semantics before changing requests; choose explicit horizon/fidelity, cap points, annotate actual earliest/latest history timestamps. No external request was made and no 90-day availability is claimed.
3. **History calls are serial.** Up to 20 selected markets are retrieved one by one (`deerflow_research.py:15386`). A small bounded parallel pool could reduce elapsed time while keeping the same request budget and deterministic merge; first measure actual selected-market counts, latency and rate limiting. It does not justify unbounded concurrency or materially explain a six-hour run by itself.
4. **Mixed snapshot as-of.** `merge_market_snapshots` retains row track provenance but returns the latest global `as_of`; `DossierViewer` renders that global time for every card. A union can contain older disjoint markets, so each displayed row should use the timestamp of its selected price, with a clearly labeled dataset assembly time separately.
5. **Provenance metadata disappears from chart gallery.** `normalizeVizGallery` retains only image/interactive path, caption and ID although server manifests include source and placement metadata; `ForecastReport` stores only the API's items and ignores structured skipped reasons. Extend only useful reader-facing metadata (evidence date, measured/projected distinction, units, interval provenance) and add an optional diagnostics view rather than exposing implementation details in the main flow. Avoid replacing existing working HTML/PNG safety and export behavior.
6. **Alternate report constructors still omit structured inputs.** Existing ASTRA-INTEGRATION-04 is directly relevant: `_collect_viz_artifacts` relies on constructor-provided quantitative/contested/graph priors for multiple families. Improve wiring before adding more chart builders. This is an existing issue, not a duplicate recommendation.

## Verification and limitations

- Existing backend gate: **243 passed in 3.59s** across prediction markets, bridge snapshots, resolution monitoring, report cross-checks, market evidence, market influence, chart quality, manifest API and axes. `viz-market/existing-tests.log`, `existing-market-viz/results.xml`, and `command-completed.json` retain the receipt. Network audit: zero attempts; changed Python/JS/Vue/YAML sources during the test: none.
- Frontend focused gate: **21 passed**, zero failures/skips, in 100.5ms: `binaryForecasts.test.mjs` and `vizManifest.test.mjs`; receipt `viz-market/frontend-tests.log`.
- New findings were reproduced without modifying permanent tests: `viz-market/reproduce.py` extracts the exact production function ASTs and records method source SHA-256/line anchors; `reproduce-ui.mjs` imports the actual pure helper and extracts actual Vue formatting functions. Output: `reproduction-results.json` and `ui-reproduction-results.json`. These are synthetic defect demonstrations, not full rendered UI acceptance.
- No live market freshness, production chart export/render quality, saved market occurrence, provider cost, trading behavior or forecast quality was tested. No performance gain is claimed. Root's separate run-cost agent owns saved-run token/time reconciliation.
- Existing tests passing does not close the reproduced gaps: they exercise reversed token order but not blank positional tokens, re-quote preservation in the client but not after extraction, stale-all batches but not complete per-row propagation, and valid/null probability formatting in the binary table but not the report dashboard.

## Suggested dependency order after the landing/UI-entry repair

1. VM-01 quote provenance through report output, with targeted visible status and source links; retain existing anchor equivalence and calibration restrictions.
2. VM-02 exact token-array alignment across all three normalizers and the history helper.
3. VM-03 strict resolution finality/validity before any future monitoring is allowed to write outcomes.
4. VM-04/VM-05 truthful probability and history visualization, with explicit missing values and a single strict display model.
5. Measure verified-empty report fallback and serial history latency before changing retrieval scheduling. Integrate the already-open alternate-report structured artifacts so charts see the same research across entry points.

Each step is independently reversible and can carry its own red/green tests, offline API fixture, UI check, comparative call count and local commit. The app is not fully integrated merely because these targeted suites passed.
