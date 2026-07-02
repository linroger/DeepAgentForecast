# NEXTSTEPS.md — A First-Principles Roadmap for DeepAgentForecast

> Written the way the last round of improvements should have been proposed: from first principles about *what this product is for*, grounded in what the code actually does today, and aimed at the changes that most improve the **forecast** — not the ones that are easiest to see. This continues the thread you have been pulling (the actor ontology, the actor/relations skill, its integration with ontology generation, and the knowledge-graph layer) and extends the same logic to the rest of the machine.

---

## 1. First principles: what is this product, really?

Strip away the engines and the dashboard. The deliverable is a **forecast**: given a question about a future outcome, produce a **probability distribution over a falsifiable, dated result**, with confidence that is *earned* — from the spread of replicate runs and, eventually, from a track record of how past forecasts resolved. Everything else — DeerFlow, the knowledge graph, OASIS, the report agent — is a *means* to that end.

Judged against that definition, the single most important finding is uncomfortable and liberating at once:

> **The product currently ships as a *simulation viewer*, not a forecaster — and its forecasting machinery is already built, just unwired.** Closing the gap to a real forecaster is overwhelmingly an act of **wiring parts that already exist**, not inventing new modeling primitives.

The north star: **DeepAgentForecast should become a calibrated generative model of the outcome variable** — one prompt in, `P(outcome)` over a dated, falsifiable target out, with confidence empirically grounded in run-to-run variance and resolved-forecast history.

Three seams are severed end-to-end between today's app and that north star:

1. **The simulation measures voice, not the outcome.** OASIS produces post-counts; `compute_emergent_metrics` derives `final_stance_share` from who posted in the last round (`run_parallel_simulation.py:2088-2092`). There is no modeled **world-state** (vote %, market share, capacity) initialized from base rates and evolved. The rich behavioral DNA we just added (incentives, resources, risk tolerance) only flavors *post tone* — agents **argue but never act**. The forecaster is then left to make the unmodeled leap from "who talked most" to a number.
2. **Uncertainty is destroyed, not surfaced.** The live pipeline launches **one** stochastic simulation and reports that single draw as "the forecast." The multi-seed aggregator that turns `n=1` into a distribution (`ensemble.aggregate_forecasts`) exists, is tested, and is pointed at the *wrong axis* (different questions in `batch_runs.py`, not different seeds of the same question). You cannot state `P(outcome)` or a credible interval from one sample.
3. **The forecast is a post-hoc side-car, and the loop never closes.** The structured probabilistic forecast (`forecast_extractor.py`) is extracted *from finished prose* written with zero probabilistic discipline, and it is **default-OFF**. Every forecast emits machine-checkable `resolution_criteria` + dated indicators, and `backtest.py` can score Brier/calibration — but **nothing ever reads resolved outcomes back**. The system records how it did and never learns from it. It is calibration-*capable* but never calibrat-*ed*.

The rest of this document is the plan to suture those seams, then to raise the ceiling.

---

## 2. The five structural gaps (the cross-cutting themes)

Every specific recommendation below is an instance of one of these:

| # | Theme | One-line diagnosis |
|---|---|---|
| G1 | **Dormant forecasting machinery** | The highest-value capability already exists in code but is default-OFF or reachable only from offline scripts (`forecast_extractor`, `ensemble`, `backtest`, `agent_dynamics`, `zep_entity_resolver`, `build_communities`, the whole bi-temporal `as_of` stack). |
| G2 | **Discourse-proxy vs outcome-model** | The sim simulates *talk*; the forecast is about *outcomes*. There is no modeled outcome variable for the sim to converge on, so "equilibrium" is undefined and behavioral DNA can't drive decisions. |
| G3 | **Uncertainty collapse** | `n=1` reported as the expectation; the correct aggregator is wired to the wrong axis. No `P(outcome)`, no interval. |
| G4 | **No feedback loop** | Resolution criteria and a Brier scorer exist, but nothing accumulates resolved outcomes and no forecast-time code conditions on past accuracy. Confidence is self-asserted, never earned. |
| G5 | **Two sources of truth (cast + ontology)** | The dual-track design makes two casts that are merely concatenated; the ontology is then re-derived from prose even though `actors.json` already carries archetype/tier/role-class and valenced relation types. Two classifications of one reality can silently desync. |
| G6 | **A fact store that can't reason structurally or about the future** | The KG is consumed as a flat RAG index: no causal-edge vocabulary, 1-hop traversal only, computed centrality discarded, and `as_of` is past-only — horizon queries silently collapse to the timeless backbone. |

---

## 3. The honest baseline — what already exists (do not re-build these)

It would be dishonest (and wasteful) to propose work that is already done. Two categories:

**Already shipped and ON (this session's work):** entity tiering/archetypes + `simulation_tier` so reporters/outlets/concepts are graph context not agents (`SIM_TIER_ELIGIBILITY` default on); behavioral-DNA actors extracted and rendered into personas (`PERSONA_BEHAVIORAL_DNA` on); valenced/directional relations feeding the follow graph + sentiment (`SIM_VALENCED_RELATIONS` on); salience-ranked agent selection (`SIM_SALIENCE_RANKING` on); concurrent dual-track research (`DEERFLOW_DUAL_TRACK` on); `forecast_inputs` (base_rates/drivers/indicators/scenarios) extracted and rendered into report background.

**Built but DORMANT (the gold — mostly off-by-default or unwired):**
- `forecast_extractor.py` — `extract_structured_forecast` (scenarios+probabilities normalized to ~1, resolution criteria, key uncertainties, confidence), `self_critique_forecast` (red-teams overconfidence/base-rate neglect), `audit_citation_grounding`. **Invoked only when `REPORT_STRUCTURED_FORECAST` is on (default OFF).**
- `ensemble.aggregate_forecasts` — mean/stdev/min/max/support + agreement across runs. **Correct, tested; wired only to offline scripts and the wrong axis.**
- `backtest.py` — multi-class Brier + log-loss + calibration report (per-bin hit-rate + ECE). **Correct, pure; `calibration_report` has zero server callers.**
- The **bi-temporal `as_of` stack** — write paths stamp `valid_at`, `runtime._to_search_filters` compiles the point-in-time predicate, `zep_tools.as_of_search` / `insight_forge(as_of=)` plumb it down. **The substrate is complete; the report agent's tool schema never exposes `as_of`.**
- `agent_dynamics.py` — per-round mood/opinion-strength/fatigue with bounded learning rates. **Fully implemented; `SIM_AGENT_DYNAMICS` default OFF.**
- `build_communities` (Leiden) + `faction_brief`; `zep_entity_resolver.py` (name + cosine ≥0.88 merge); `SIM_SEED` determinism. **All built, all gated off by default.**

The implication is the roadmap's center of gravity: **wire and invert before you build.**

---

## 4. The roadmap

Each item: **leverage / effort**, the first-principles *why*, the grounded *gap* (with file refs), the concrete *first step*, and *depends-on*. Ordered into four phases (§5 explains the ordering). Leverage ∈ {transformational, high, medium}; effort ∈ {S, M, L, XL}.

### Phase 0 — Establish the outcome & uncertainty substrate (wire finished code)

**P0-1. Make the structured probabilistic forecast the first-class deliverable, derived *before* prose.** — *transformational / L*
Today probabilities are reverse-engineered from finished narrative written with no probabilistic discipline (`report_agent.py:2917-2938`, default OFF); the default deliverable is 5–8 prose sections with no required numbers. Deriving the forecast *spine* first (scenarios + probabilities summing to 1 + resolution criteria, from the signal pack + `forecast_inputs`) forces MECE discipline and makes every prose claim accountable to a falsifiable target. **This is the single change that converts a simulation viewer into a forecaster, and the substrate every other forecast improvement plugs into.**
*First step:* add a `_derive_forecast_spine()` stage between outline and section generation that calls a variant of `extract_structured_forecast` seeded by `_build_signal_pack` + `forecast_inputs_block(self.actors)` (not finished prose); flip `REPORT_STRUCTURED_FORECAST` default → True; persist `forecast.json` early; inject the spine's scenarios+probabilities into `SECTION_SYSTEM_PROMPT` so each section defends its assigned numbers; keep `audit_citation_grounding` as the final gate. *Depends:* none.

**P0-2. Thread `as_of` through the ReportAgent's `insight_forge` tool (unwire one dead parameter).** — *high / S*
The entire bi-temporal retrieval stack is implemented and correct, yet `insight_forge`'s tool schema exposes only `{query, report_context}` (`report_agent.py:1583-1586`) and the dispatch never passes `as_of`. The forecaster always sees a **time-flattened snapshot** and cannot evidence "stance X drifted between T0 and the horizon." Capability already paid for; one parameter to wire.
*First step:* add `as_of` to the `insight_forge` parameters in `_define_tools`, pass `parameters.get('as_of')` into `zep_tools.insight_forge(...)` at dispatch (~`:1664`), document it for the report LLM, and add a trajectory-diff convenience (seed-time vs final-round `as_of`). *Depends:* none.

**P0-3. Automated same-question N-seed ensemble in the live pipeline — the variance *is* the uncertainty.** — *transformational / M*
An LLM-driven sim is a stochastic generator; one run is a single draw, yet the pipeline launches exactly one subprocess and reports it as the answer. The aggregation math already exists (`ensemble.aggregate_forecasts`, `ensemble.py:34-97`) — it is merely pointed at the wrong axis. Pointing it at the **seed axis** turns a point estimate into honest intervals and exposes instability as low agreement → low confidence.
*First step:* add `N_FORECAST_SEEDS` (default 1 = byte-identical degrade); after PREPARE, fork K sim+report runs over the *same* graph with distinct `SIM_SEED`; collect each `forecast.json`; call `aggregate_forecasts`; write `ensemble_forecast.json` as canonical; map `ensemble.agreement` → report headline confidence. *Depends:* strongest after P0-1 (comparable structured forecasts per seed).

**P0-4. Inject `forecast_inputs` into the simulation + turn on intra-agent dynamics.** — *high / S*
The base_rates/drivers/scenarios the dual-track research painstakingly extracted are rendered **only in the final report** (`report_agent.py:1165`) — grep confirms zero sim-side usage — so agents free-associate instead of reasoning against analytic anchors. Separately, `agent_dynamics.py`'s own docstring says that without intra-agent state a multi-round sim is "N independent one-shot polls repeated T times — no escalation, no bandwagon, no fatigue" — exactly the cascade dynamics by which discourse moves an outcome — and it is default-OFF.
*First step:* inject `forecast_inputs_block(actors)` into config-gen context and a shared per-round situation note; flip `SIM_AGENT_DYNAMICS` on (or auto-enable when rounds > threshold). *Depends:* independent; amplifies P1-1.

### Phase 1 — Make the simulation a model of the OUTCOME, not of chatter

**P1-1. Add a decision/commitment channel so agents ACT on incentives and evolve an outcome world-state.** — *transformational / L*  · **the architectural keystone**
The forecast outcome is the aggregate of consequential **decisions** (allocate, commit, vote, switch supplier), not of posts. The action space today is 100% expressive (`run_parallel_simulation.py:202-268`: CREATE_POST/LIKE/REPOST…, no DECIDE/COMMIT); `final_stance_share` is voice-share. Without a modeled outcome variable there is nothing for the sim to converge *on*. This is the biggest lever from discourse-proxy to genuine outcome-prediction — and it reuses behavioral DNA + `forecast_inputs` that already exist.
*First step:* in the round loop (~`:2323-2393`), after each active agent's `astep()`, call a lightweight structured-output step prompted with the agent's behavioral DNA + in-feed content + `forecast_inputs` option set, returning `{commitment, magnitude, confidence, rationale}`; persist `decisions.jsonl`; initialize a `WorldState` from `forecast_inputs.base_rates` and update it each round by **resource-weighted** commitments; persist `world_state_trajectory.json` and read the final world-state as the outcome (replacing `final_stance_share`). *Depends:* P0-4; feeds P0-3 a real outcome to aggregate.

**P1-2. Map simulation rounds to the forecast horizon and stage research-derived catalysts on the timeline.** — *high / M*
`_generate_time_config` caps the sim at 1–7 compressed social-media *days* (`simulation_config_generator.py:991-1064`) while questions ask about multi-*year* outcomes, with no rounds→calendar mapping — "round 40" says nothing about 2027 vs 2028, and the multi-year dynamics that actually decide outcomes are absent. `key_events` are already scheduled; extend the same mechanism to `forecast_inputs.indicators` with a horizon map.
*First step:* add a horizon model to `TimeSimulationConfig` (parse the question horizon, each round = horizon/total_rounds), schedule `indicators_to_schedule` at mapped rounds via `fire_scheduled_events`, stamp each round's outcome readout with its mapped date. *Depends:* P1-1.

**P1-3. Add a silent-majority / lurker population so the outcome isn't dominated by the loudest agents.** — *high / M*
Every metric is computed only over agents who **posted** (`run_parallel_simulation.py:1765-1793`), so the decisive populations in most forecasts — non-tweeting voters, customers, capital allocators — are structurally invisible, biasing toward the loudest and most extreme (the Twitter-is-not-real-life error). A forecast must weight by **decision power, not decibels.**
*First step:* instantiate a lightweight audience from existing tier-3 context entities that only READ and emit a private decision-channel commitment (never post); weight the outcome by audience commitments + resource-weighted actor decisions; interim, add influence/resource weighting to `final_stance_share`. *Depends:* P1-1 (best with the decision channel).

**P1-4. Convergence/equilibrium detection and early-stop on the *outcome* (not chatter volume).** — *medium / S*
Without a convergence notion you cannot distinguish a settled forecast from a transient, and you burn compute on rounds that don't change the answer; fast-vs-fragile convergence is itself a calibration signal. The sim runs fixed `total_rounds` regardless, with no stability metric.
*First step:* track the world-state per round; compute an EWMA of `|Δoutcome|`; early-stop when `Δ < ε` for W rounds (respecting `max_rounds`); record time-to-convergence + oscillation; report "converged at round R (stable)" vs "non-convergent (low confidence)." *Depends:* P1-1.

### Phase 2 — Calibrate, ground, and close the loop

**P2-1. Reconcile outside-view base rates with sim-derived probabilities (explicit anchor-and-adjust) + enable self-critique.** — *high / M*
Good forecasting is "start from the reference class, adjust for case specifics, justify the delta." Base rates are pinned into the prompt as suggestion *text* only (`report_agent.py:1165`); the final probabilities never reconcile against them, so the sim can drift to overconfident inside-view numbers (base-rate neglect) — the exact failure `self_critique_forecast` was written to catch but which is double-gated off (`REPORT_FORECAST_SELF_CRITIQUE` default False).
*First step:* extend the forecast schema with per-scenario `base_rate_anchor / simulation_signal / adjustment_rationale`; pass `extract_forecast_inputs(self.actors)` into `extract_structured_forecast` so the model sees the reference class at extraction time; flip `REPORT_FORECAST_SELF_CRITIQUE` on with a red-team that checks final-vs-anchor deltas; add soft shrinkage toward the base rate when inter-seed agreement (P0-3) is low. *Depends:* P0-1, P0-3.

**P2-2. Promote watch-indicators + resolution criteria into a mandatory falsifiable "how we'll know we were right" section.** — *high / S*
A forecast without explicit, dated, observable indicators cannot be tracked or scored — the difference between "rates may rise" and "if metric X exceeds Y by date Z, scenario A is confirmed." `indicators_to_schedule` (`actors.py:1371-1383`) exists with `date_or_trigger` fields built for verification, but no production caller threads it into the report's resolution criteria.
*First step:* add a mandatory final outline section that renders, per scenario, `resolution_criteria` + dated indicators, binding each indicator to the scenario it discriminates; persist the mapping into `forecast.json` for the resolution scheduler. *Depends:* P0-1.

**P2-3. Probability-coherence + grounding publish-gate.** — *medium / M*
A calibrated forecaster must refuse to publish incoherent or ungrounded probability sets — or loudly degrade their stated confidence. `audit_citation_grounding` is a regex proxy with no enforcement (a 20%-cited report publishes identically), and there is no coherence gate ensuring a status-quo fallback or that 0.99-on-one-scenario is justified.
*First step:* after forecast derivation, require citation coverage ≥ threshold for scenario-feeding quantitative claims (else auto-demote confidence to "low"); enforce a residual/status-quo scenario; reject degenerate entropy unless agreement is high; where ensemble exists, force `confidence = f(agreement)`; emit `forecast.json.quality` so the UI can badge low-confidence/ungrounded forecasts. *Depends:* P0-1, P0-3.

**P2-4. Close the calibration loop: a forecast ledger + resolution scheduler feeding historical Brier into confidence.** — *high / XL*  · **this is what finally makes confidence *earned***
A forecaster that never learns whether its 70%s happen 70% of the time is calibration-*capable*, not calibrated. `backtest.py` is correct but practically unreachable; nothing accumulates resolved forecasts. Every forecast already emits machine-checkable `resolution_criteria` + dated indicators.
*First step:* on every `forecast.json` write, append to a ledger (jsonl/sqlite) keyed by horizon/resolution date; extend `scripts/scheduled_rerun.py` to detect forecasts whose horizon/indicator dates have passed and queue them; add an optional LLM-judge resolver checking `resolution_criteria` against fresh research; periodically run `backtest.calibration_report` over the ledger and surface `mean_brier`/`calibration_error` in new reports' `confidence_rationale`. *Depends:* P0-1, P2-2.

### Phase 3 — Deepen the seed and the graph (raise the ceiling)

This is the lineage you have been driving — the actor ontology, the actor/relations skill, its integration with ontology generation, and the knowledge graph — taken to its logical next steps. It is *correctly last*: it raises the ceiling on a system that, after Phases 0–2, is already a calibrated outcome forecaster rather than a simulation viewer.

#### 3a. The research → actor → ontology seed

**P3-1. Wire the AI-judge → refine loop the actor-ontology SKILL already specifies (the dossier ships unverified).** — *transformational / L*
The whole pipeline's accuracy is capped by the actor dossier, and `SKILL.md §6–§8` fully specify a multipass workflow + an 8-dimension AI-judge gate (PASS bar, up to 3 refine rounds) — yet `run_actor_ontology_stage` runs exactly **one** research turn + **one** synthesis turn and ships the first draft (grep confirms zero judge/refine/rubric code in `deerflow_research.py`). It commits the exact failure the skill names as forbidden: *shipping the first draft.* A judge that catches a missing pivotal principal, a flat unvalenced network, or an outlet mis-cast as an actor — and drives one surgical refinement — directly raises cast correctness and relationship completeness, the dimensions everything downstream consumes.
*First step:* add `build_judge_prompt(dossier, question)` emitting structured JSON (8-dim 0–5 + verdict + targeted gap-list); after synthesis, run the judge and, if FAIL and `rounds < JUDGE_MAX_ROUNDS`, run one tool-bearing refinement turn seeded *only* with the gap-list, then re-synthesize/re-judge; gate behind `ACTOR_DOSSIER_JUDGE` (OFF → ON); persist the scorecard to `meta.json`. *Depends:* self-contained.

**P3-2. Cross-track cast reconciliation into one canonical, audited set before extraction.** — *high / M*
The dual-track design deliberately produces two casts that today are merely concatenated and handed to one extraction LLM, which silently arbitrates overlaps with no audit. The only real resolver (`zep_entity_resolver`) runs on graph nodes post-ingestion and is default-OFF — too late and too coarse to fix a cast-level divergence ("Nvidia" vs "NVIDIA Corp" with a different role-class). Divergence splits centrality, spawns duplicate personas, and corrupts the salience ranking the agent cap relies on.
*First step:* after extraction, run a reconciliation pass on `actors[]` reusing `normalize_name` + the bidirectional-containment/alias logic in `match_actor`/`zep_entity_resolver` to cluster duplicate rows, merge into one canonical row (richer profile wins, union aliases, flag conflicts), and write `cast_reconciliation.json`; gate behind `CAST_RECONCILE` (default ON — it only tightens the cast). *Depends:* shares helpers with `zep_entity_resolver`; precedes P3-3.

**P3-3. Derive the ontology FROM the dossier's archetypes/relation-families instead of a separate LLM re-derivation.** — *high / L*
A single source of truth prevents silent schema/instance desync. The dossier+extraction already assign per-entity archetype/`simulation_tier`/role-class and a typed/valenced `relationships[]` set, but `OntologyGenerator.generate` re-derives entity/edge types from raw prose (`pipeline_orchestrator.py:2550-2553`) — a redundant classification that can **diverge** from what actors were tagged with, silently degrading typed graph retrieval and the typed follow graph. `_normalize_rich_schema` even back-fills archetype/family/valence by string-matching edge *names* — re-deriving what the dossier already computed.
*First step:* add `ontology_from_actors(actors)` in `actors.py` projecting realized archetypes → `entity_types` and realized `relationships[].type` → `edge_types` (reusing `REL_EDGE_NAME` / `_EDGE_FAMILY_VALENCE`); make `OntologyGenerator.generate` accept it as a **seed/constraint** so the LLM pass keeps every realized relation family and may add ≤2 domain types; gate behind `ONTOLOGY_FROM_DOSSIER` (OFF). *Depends:* strengthened by P3-1.

**P3-4. Computable dossier coverage gate on the contract's load-bearing fields.** — *medium / M*
`actors.py` makes every contract field optional and silently degrades — correct for robustness, but a dossier that omitted incentives for every actor, or emitted an edge-less network, flows through indistinguishable from a rich one, building the forecast on a hollow seed with no signal.
*First step:* add `dossier_coverage(actors)` returning `{pct_actors_with_incentives, pct_tier1_2_with_worldview, pct_edges_valenced, edges_per_actor, salience_basis_present}`; compute after extraction → `meta.json`; surface in `_surface_research_quality`; behind `RESEARCH_COVERAGE_GATE` (OFF), trigger a refine round (via P3-1) or attach a confidence penalty that widens uncertainty bands. *Depends:* most useful once P3-1 can act on a FAIL.

#### 3b. The knowledge graph as a causal, temporal, structural model

**P3-5. Add a first-class CAUSAL/MECHANISM edge family (CAUSES/ENABLES/CONSTRAINS/TRIGGERS) with sign + lag + strength.** — *high / L*  · **prerequisite for the rest of 3b**
Forecasting outcomes is reasoning over **transmission mechanisms**, not who-knows-whom. `_EDGE_FAMILY_VALENCE` is entirely relational/economic (ALLY_OF/OPPOSES/SUPPLIES/DEPENDS_ON); a grep for CAUSES/TRIGGERS in `app/` is empty. `DEPENDS_ON` is the nearest mechanism edge but is treated as a static relation, not a propagation channel. Causal edges turn the KG from an index into a propagation model the report can trace and the sim can seed shocks along — and `graph_builder` already folds sign/strength/grade into the fact string, so no schema migration is forced.
*First step:* add a causal family to `_EDGE_FAMILY_VALENCE` and to both ontology templates' edge guidance (CAUSES/ENABLES/CONSTRAINS/TRIGGERS/ACCELERATES with required attrs `{sign, lag, strength, basis}`); have the actor-ontology dossier emit causal relationships; raise `MAX_EDGE_TYPES` so causal edges aren't crowded out by decorative relational ones. *Depends:* enables P3-6, P3-8.

**P3-6. Add a multi-hop causal-path / chokepoint traversal tool over the graph.** — *high / L*
A forecast needs structural reasoning — "trace the cascade; which node, if it moves, flips the outcome?" The runtime offers only 1-hop `get_node_edges` + embedding rerank; there is no reachability/path primitive, and `_get_graph_info` computes components/degree then **discards** them.
*First step:* add `n_hop_subgraph(center, max_hops, edge_types)` and `causal_paths(source, target, edge_types)` to `runtime.py` (FalkorDB variable-length Cypher); surface via `zep_tools` as a report tool `trace_cascade`; chokepoint = highest betweenness on the causal subgraph between catalyst and outcome. *Depends:* P3-5.

**P3-7. Derive graph-structural priors (centrality/betweenness) and feed them into salience + report weighting.** — *medium / M*
A high-betweenness actor is more pivotal than its raw mention count — a cheap, well-grounded influence prior. `_get_graph_info` already computes degree/top-hubs then discards them; salience-ranked selection ignores topology.
*First step:* persist the degree/component stats; add eigenvector/betweenness on the causal+relational subgraph; expose a `graph_priors` dict (node→centrality); blend into `salience_score` and faction/agent selection; surface a `centrality` field the report can read. *Depends:* stronger after P3-5; works on the relational subgraph alone.

**P3-8. Forward-project edge validity for genuine "as of <horizon>" future queries.** — *high / XL*
A forecast horizon is in the **future**, where no edges have `valid_at`, so every `as_of` query is backward-looking and a horizon query silently collapses to the timeless backbone. To reason "as of 2030" the graph needs **projected** edges — the difference between a temporal fact *store* and a temporal *model*. An edge with lag L from an observed T0 event projects an effect edge at T0+L.
*First step:* add a projection pass writing future edges (`valid_at=horizon`, `projected=true`, confidence) derived from scenario/driver extraction and causal-edge lag; tag them so `as_of_search` at the horizon returns projected structure separated from observed facts; never let projected edges contaminate observed-fact grounding retrieval. *Depends:* P3-5, P0-2.

**P3-9. Turn on Leiden communities + `faction_brief` by default and make factions a temporal forecast input.** — *medium / M*
Coalition structure — and especially how it *shifts* over the temporal axis — is a strong forecast signal (a defecting bloc flips an election or a standard war). `build_communities` + `faction_brief` exist but default OFF, so the report falls back to an action-log heuristic and can never observe coalition formation/fracture.
*First step:* default-enable the community path; let `faction_brief` optionally take an `as_of` for a round-0-vs-final faction diff; cross-reference community membership with relation valence to label factions cooperating vs colliding. *Depends:* P0-2 (for the temporal diff).

---

## 5. Sequencing and why this order

The ordering is itself a first-principles claim: **wire the finished machinery that surfaces uncertainty and defines the outcome first; make confidence earned and the loop closed second; only then spend L/XL effort deepening the seed and the causal graph.** Each phase makes the next *measurable*.

- **Phase 0** establishes the two objects everything consumes: a structured forecast spine (P0-1) and run-to-run variance as honest uncertainty (P0-3), plus the cheap activations (P0-2, P0-4). After Phase 0 the app *states a probability with an interval* instead of narrating one path.
- **Phase 1** makes the simulation a model of the **outcome** (P1-1 is the keystone — give the sim something to converge on), then maps it to real time (P1-2), weights it by decision power (P1-3), and knows when it has settled (P1-4). After Phase 1, "who posted most" has become "P(outcome) over a dated trajectory."
- **Phase 2** makes the stated confidence **defensible** (anchor-and-adjust, mandatory resolution criteria, a publish-gate) and **closes the write-only loop** (P2-4) so the system finally learns whether it was right.
- **Phase 3** raises the ceiling: a verified, reconciled, single-source-of-truth seed (3a) and a causal/temporal/structural graph (3b). These are last not because they are unimportant — they are the deepest — but because they compound a system that is already calibrated rather than polishing a simulation viewer.

A reasonable first sprint, in order: **P0-1 → P0-2 → P0-3 → P0-4**, then **P1-1**. That sequence alone moves the product from "simulation viewer" to "calibrated forecaster" using almost entirely code that is already written.

---

## 6. The meta-lesson (why these weren't proposed earlier)

The improvements that mattered most this whole time were never *code-health* observations a linter or a diff-review surfaces — they were **first-principles questions about what a forecast is**: *Should a cited reporter be a simulated actor? Should a rival follow you the same way a partner does? Should one stochastic run be reported as the answer? Should the forecast be derived before the prose, or reverse-engineered from it?* Those questions only get asked when you start from "what is the deliverable and what would make it true," not from "what does the code currently do."

The governing principle for everything above: **the system's loss function should be the calibration of its forecasts against resolved reality** — and almost every high-leverage change is just removing a place where the current code quietly optimizes for something else (a readable narrative, a single dramatic run, a searchable fact) instead of that.
