# GROK-RECOMMENDATIONS — Workflow Performance & Efficiency

**Audit date:** 2026-09-08 (Asia/Shanghai)  
**Audited revision:** `main` @ `6df6b37` (post loop-i1…i6)  
**Method:** GitHub API / MCP reads only (no local clone). Primary sources: `README.md`, `ARCHITECTURE.md`, `DEERFLOW_INTEGRATION.md`, `EXECPLAN.md`, `OPTIMIZATIONS.md`, `docs/RESEARCH_STAGE_OPTIMIZATION.md`, `docs/foglamp/current-shape-map.md`, `docs/adr/0001-workflow-authority.md`, `docs/adr/0002-forecast-evidence-publication-authority.md`, `.env.example`, `backend/app/config.py`, `backend/app/services/*`, `deerflow_bridge/*`, recent commits `3306af8`…`cf6a154`.

**Nature of this file:** Forward-looking performance/efficiency recommendations for the **product workflow** (one-prompt research→graph→sim→report). It does **not** replace `OPTIMIZATIONS.md` (historical stage catalog), `CODEX_RECOMMENDATIONS.md` (broader product/security audit), or Foglamp ADRs (authority migration). It **updates the priority order** for what still matters after containment loops i1–i6 landed.

---

## 1. Architecture map (how parts connect)

```
Browser (Vue 3 SPA, frontend/)  ──poll──▶  Flask :5001 (backend/run.py)
        │                                       │
        │  / → ResearchView (Step 0)            │  blueprints:
        │  /legacy → MiroFish wizard            │   /api/research  → PipelineOrchestrator
        │  /report|/simulation|/process …       │   /api/graph|/simulation|/report|/settings
        ▼                                       ▼
 uploads/pipelines/<id>/pipeline_state.json  +  TaskManager (in-memory)
        │
        ├─ STAGE research ──subprocess──▶ deer-flow/venv + deerflow_bridge/deerflow_research.py
        │     skills: deep-research, actor-ontology-research, prediction-markets, forecast-visuals
        │     handoff/: research_report.md, actor_dossier.md, actors.json, sources.json, …
        │
        ├─ STAGE ontology ──▶ OntologyGenerator (LLM)  [actors bias via additional_context]
        ├─ STAGE graph    ──▶ GraphBuilderService → graphiti_client shim → FalkorDB
        │     seed_actors + GRAPH_CAST_CHUNK_FILTER + chunk LLM extract + resolve/prune
        ├─ STAGE prepare  ──▶ OasisProfileGenerator + SimulationConfigGenerator
        │     actor-role/v1 contracts, calendars (SIM_TEMPORAL_MODE=calendar)
        ├─ STAGE run      ──▶ SimulationRunner → scripts/run_parallel_simulation.py (OASIS)
        │     Twitter ∥ Reddit, decision_channel + WorldState, IPC interviews
        └─ STAGE report   ──▶ ReportAgent + forecast_extractor + report_visualizer + publish gates
```

**Two venvs by design** (`DEERFLOW_INTEGRATION.md`): MiroFish (`backend/`) and DeerFlow (`deer-flow/`) never share packages. Research is Option-C subprocess + log/file handoff; OASIS is the same process pattern.

**Frontend connection:** loop-i1 made `/` land on Research UI; Flask also serves `frontend/dist` as SPA at `:5001`. Legacy MiroFish routes are lazy-loaded. Charts use shared plotlyjs directory mode (`REPORT_VIZ_PLOTLYJS_INLINE` rollback knob) after loop-i1; theme unified in loop-i6.

**Authority note (do not fight it for perf):** ADR 0001 pins workflow advancement to the legacy thread controller until WP16D; ADR 0002 / Foglamp map say research markdown and shared graph are *projections*, not final forecast authority. Perf work should prefer **budgets, reuse, and config knobs** over inventing a second control plane.

---

## 2. Workflow stage map — where cost/time concentrates

| Stage | Entrypoints | Dominant cost driver | Post-i1…i6 containment already in tree |
|---|---|---|---|
| **Research** | `PipelineOrchestrator` → `DeerFlowResearchRunner` → `deerflow_bridge/deerflow_research.py` | LLM input tokens (measured ~**80M / ~96% of metered spend**, 49:1 in:out on `pipe_f23527f7d903` — `docs/RESEARCH_STAGE_OPTIMIZATION.md`). Quadratic thread re-send × `RESEARCH_PARALLEL_TRACKS=3` × phases × fanout. | Tool budgets (`RESEARCH_BUDGET_*`), negative cache, source cache, global synthesis, spend flush on fail/cancel (loop-i4), Polymarket UA hardening (loop-i2) |
| **Ontology** | `ontology_generator.py` | One (or few) strong LLM calls; ~minutes | Actor-biased seeds (`ONTOLOGY_FROM_DOSSIER`) |
| **Graph** | `graph_builder.py` + `graphiti_client/{runtime,llm_adapter,client}.py` | Per-episode extract LLM + embeds; historically **hours** (forensic: 8h37m / 62% of one full run before cast filter) | `DEFAULT_CHUNK_SIZE=2500`, `GRAPH_BUILD_CONCURRENCY=4`, `GRAPH_CHUNK_SOURCE=dossier_only`, `GRAPH_CAST_CHUNK_FILTER`, `GRAPH_CHUNK_MAX_ATTEMPTS=2`, resolve+`GRAPH_MAX_ENTITIES=400`, telemetry attribution (loop-i2/i4) |
| **Prepare** | `oasis_profile_generator.py`, `simulation_config_generator.py` | Per-actor LLM personas + stepwise config | `ACTOR_CAST_MAX=20`, `PROFILE_ZEP_SKIP_WHEN_CONTEXT`, LLM cache |
| **Run (sim)** | `simulation_runner.py` + `scripts/run_parallel_simulation.py` | Agents × rounds × platforms LLM calls | Calendar mode, hollow-run gate, **`SIM_RESUME_REUSE_COMPLETED`** (loop-i5), sim meter persistence, outage halt (loop-i4) |
| **Report** | `report_agent.py`, `forecast_extractor.py`, `report_visualizer.py` | Section LLM + tools + purity/translation | `REPORT_SECTION_CONCURRENCY=6`, native tools, brief context, purity escalation cap (loop-i2), viz density gates (loop-i3), shared plotlyjs (loop-i1) |

**Rule of thumb after containment:** research is still the **token** whale; graph+sim are the **wall-clock** whales when research is bounded. Report is secondary unless purity/failover storms (now capped).

---

## 3. What already improved (do not re-propose as new)

Ground these as *done* on `main` so effort goes to residual gaps:

1. **Graph cast filter + attempt budget** — `GRAPH_CAST_CHUNK_FILTER=true`, `GRAPH_CHUNK_MAX_ATTEMPTS=2` (`config.py`, `graph_builder.py`; loop-i4).
2. **Run-level outage halt** — `LLM_OUTAGE_HALT_CONSECUTIVE=10` (loop-i4).
3. **Sim resume reuse + hollow-run failure** — `SIM_RESUME_REUSE_COMPLETED=true` (loop-i5).
4. **Telemetry attribution for worker threads** — enables `LLM_RUN_BUDGET_TOKENS` to actually trip from graph workers (loop-i2).
5. **Chart disk blowup mitigation** — plotlyjs directory mode; disk footprint report script (loop-i1/i6) — *cleanup still owner decision*.
6. **GAP-1 code path is wired** — `graphiti_client/llm_adapter.py` maps `ModelSize.small|medium → tier="fast"`. Speedup is still **inert until `LLM_FAST_MODEL` (and friends) point at a real non-reasoning model** (`.env.example` documents empty = no speedup).
7. **Many OPTIMIZATIONS.md quick wins are already defaults** — chunk 2500, graph concurrency 4, resolve on, report concurrency 6, dual-track on, parallel tracks 3, etc. (see `config.py` defaults vs `.env.example`).

---

## 4. Prioritized recommendations (P0 / P1 / P2)

Impact = expected token or wall-clock reduction on a full deep run. Effort = engineering + validation, not product politics.

### P0 — highest impact × effort (do / decide next)

| ID | Recommendation | Impact | Effort | Evidence | Notes |
|---|---|---|---|---|---|
| **P0-1** | **Turn on a real fast tier for graph extraction** — set `LLM_TIERED_ROUTING=true` (already default) **and** `LLM_FAST_MODEL` / `LLM_FAST_PROVIDER` / `LLM_FAST_BASE_URL` / `LLM_FAST_API_KEY` to a non-reasoning model | Largest remaining **per-call** graph latency lever | Config + 1 A/B run | `llm_adapter.py` tier mapping; `.env.example` GAP-1 note; `OPTIMIZATIONS.md` GAP-1 | Monitor schema-echo retries via `LLM_TELEMETRY_ENABLED`; keep edge-quality check vs strong-only baseline |
| **P0-2** | **Enable run budgets by default in operator profiles** — set `LLM_RUN_BUDGET_TOKENS` (e.g. 30M for experiments, higher for production deep) and optionally `LLM_RUN_BUDGET_USD` | Hard stops doomed spend; makes research experiments safe | Config only | Budget enforceable after loop-i2/i4; recommended in `docs/RESEARCH_STAGE_OPTIMIZATION.md` | Default remains `0` (unlimited) — intentional, but workstation operators should pin |
| **P0-3** | **Provider prompt caching spike for DeerFlow thread** — verify Anthropic/MiniMax/etc. prefix caching on the re-sent LangGraph thread; implement only where billing discounts apply | Potentially the largest **research token $** cut with no quality change | Small spike + bridge change in `deerflow_bridge/deerflow_research.py` / provider adapters | `docs/RESEARCH_STAGE_OPTIMIZATION.md` lever #1; no `cache_control` / prompt-cache hits found in indexed tree | Owner-gated only for quality-affecting levers; caching itself is quality-neutral |
| **P0-4** | **Audit live `.env` pins with `check_env_drift.py --pins`** (Wave 8) before tuning further | Prevents “defaults improved but workstation still on old concurrency” | Minutes | Wave 8 commit notes 14 divergent pins historically (e.g. `GRAPHITI_MAX_COROUTINES`) | Treat drift as a release checklist item |
| **P0-5** | **Wire `SIM_CONVERGENCE_STOP` into the calendar/decision-channel loop** (or delete the dead knob) | Bounds sim tail when WorldState EWMA is flat | Medium | `Config.SIM_CONVERGENCE_STOP` defaults `true`, but Python references outside `config.py` were **not found** via code search — knob is documented (`.env.example`, `OPTIMIZATIONS.md` SIM-1) yet appears **unwired** | Pair with existing `CONVERGENCE_POLICY_V1` / typed round validity (WP1) so silence ≠ convergence |

### P1 — strong follow-ons

| ID | Recommendation | Impact | Effort | Evidence |
|---|---|---|---|---|
| **P1-1** | **Inter-phase thread compaction** — lower lead-agent `trim_tokens_to_summarize` / tighten `RESEARCH_PRIOR_NOTES_CONTEXT_CHARS` so each phase starts from a compact brief | Breaks quadratic research input growth | Small + A/B | `docs/RESEARCH_STAGE_OPTIMIZATION.md` lever #2; `RESEARCH_PRIOR_NOTES_CONTEXT_CHARS=60000` already exists |
| **P1-2** | **Shared-evidence track seeding (3→2 or B/C seeded from A)** before cutting `RESEARCH_PARALLEL_TRACKS` | ~⅓ research spend if topology cut; shared-seed preserves angles better | Medium + product call | Same doc levers #3; `RESEARCH_PARALLEL_TRACKS=3`, `RESEARCH_GLOBAL_SYNTHESIS=true` already merge at end |
| **P1-3** | **Keep `RESEARCH_ALLOW_STACKED_FANOUT=false`** and treat any enable as a paid experiment only | Avoids return of ~80M-token nested breadth | Policy | `.env.example` explicitly cites ~80M nested runs |
| **P1-4** | **Latency profile for dual-track** — for speed runs: `DEERFLOW_DUAL_TRACK=false` or `ACTOR_DOSSIER_JUDGE_MAX_ROUNDS=0/1`; keep defaults for quality | Cuts research wall clock ~2× when Track B is on critical path | Config | `OPTIMIZATIONS.md` RESEARCH-2/3; defaults dual-track+judge on |
| **P1-5** | **Operator “speed / balanced / max-quality” preset packs** documented as named `.env` fragments (not more code) | Reduces mis-tuning across 70+ knobs | Docs | `.env.example` is comprehensive but overwhelming |
| **P1-6** | **Report/artifact retention policy** using `backend/scripts/disk_usage_report.py` findings (pre-i2 reports ~1.57GB of 1.9GB via inline plotly) | Disk I/O & backup cost | Ops policy | loop-i6 commit message; no auto-delete by design |
| **P1-7** | **Confirm `LLM_CACHE_ENABLED=true` survives across process restarts** (disk vs process-local) for prepare/persona reruns | Speeds resume/fork/ensemble | Small if currently process-local only | `.env.example` SIM-9/CACHE-1; verify implementation locality |

### P2 — larger bets / product-gated

| ID | Recommendation | Impact | Effort | Why wait |
|---|---|---|---|---|
| **P2-1** | Cut `RESEARCH_FANOUT_WIDTH` 8→5 and/or `RESEARCH_PHASE_BUDGET_MULT` only after P0-3 + P1-1 measured | Moderate research tokens | Config | Real coverage risk — owner sign-off (`RESEARCH_STAGE_OPTIMIZATION.md`) |
| **P2-2** | DeerFlow Gateway (Option B) for persistent research workers | Cold-start + ops isolation | Large | Correct long-term; Option C is intentional first cut (`DEERFLOW_INTEGRATION.md`) |
| **P2-3** | Route MiroFish report LLM through DeerFlow `ClaudeChatModel` (native tools) | Fewer ReAct/contamination retries | Large | Explicitly out of scope for first integration; T4.5 in EXECPLAN |
| **P2-4** | Isolated per-seed graph overlays before raising `N_FORECAST_SEEDS` | True uncertainty ensembles | Large (WP10/12) | WP1 correctly defaults seeds=1; shared `graph_id` contaminates |
| **P2-5** | Durable workflow store (ADR 0001 / WP16) | Resume/lease correctness, not raw LLM speed | Very large | Authority migration; do not dual-advance (I-07) |
| **P2-6** | Frontend further route-level splitting beyond loop-i1 lazy legacy | Smaller entry chunk | Small–medium | Already 514KB→356KB entry; diminishing returns vs research tokens |

---

## 5. Quick wins vs larger bets

### Quick wins (config / ops — hours)

1. Set **`LLM_FAST_MODEL`(+provider URL/key)** to a cheap non-reasoning model for graph/mechanical JSON.
2. Set **`LLM_RUN_BUDGET_TOKENS`** on experiment hosts (start ~30M per `RESEARCH_STAGE_OPTIMIZATION.md`).
3. Run **`check_env_drift.py --pins`** / `npm run doctor` and clear stale overrides.
4. For latency demos: `DEERFLOW_RESEARCH_DEPTH=standard` (or `quick`), optionally `DEERFLOW_DUAL_TRACK=false`, keep `RESEARCH_ALLOW_STACKED_FANOUT=false`.
5. Keep sim reuse on (`SIM_RESUME_REUSE_COMPLETED=true`) — already default; do not disable during quota incidents.
6. Schedule disk-usage review from `disk_usage_report.py`; archive old `uploads/reports/*` with inline-plotly HTML.

### Larger bets (days–weeks + product decision)

1. Prompt-caching + thread compaction in the DeerFlow bridge (research $).
2. Track topology / shared-evidence redesign (research diversity vs cost).
3. Actually wire convergence early-stop into OASIS/calendar loop.
4. Option B gateway; native-tool report path; Foglamp forecast-bundle authority (quality/architecture, secondary to token burn).

---

## 6. Existing knobs checklist (already documented — prefer these over new code)

Grouped from `.env.example` + `backend/app/config.py` defaults on `main`:

| Family | Key knobs | Default stance on `main` |
|---|---|---|
| **LLM** | `LLM_PROVIDER`, `LLM_TIERED_ROUTING`, `LLM_FAST_*`, `LLM_CACHE_ENABLED`, `LLM_RUN_BUDGET_TOKENS/USD`, `LLM_OUTAGE_HALT_CONSECUTIVE`, `LLM_TELEMETRY_ENABLED`, `LLM_FALLBACK_*` | Tiered on but fast model empty; budgets off (`0`); outage halt 10; cache on |
| **DeerFlow / research** | `DEERFLOW_MODEL`, `DEERFLOW_RESEARCH_DEPTH`, `DEERFLOW_DUAL_TRACK`, `DEERFLOW_SUBAGENTS`, `RESEARCH_PARALLEL_TRACKS`, `RESEARCH_FANOUT_WIDTH`, `RESEARCH_GLOBAL_SYNTHESIS`, `RESEARCH_ALLOW_STACKED_FANOUT`, `RESEARCH_BUDGET_*`, `RESEARCH_PRIOR_NOTES_CONTEXT_CHARS`, judge/refine round caps | Deep + dual-track + 3 tracks + fanout 8; stacked fanout **false**; budgets on |
| **Graph** | `DEFAULT_CHUNK_SIZE/OVERLAP`, `GRAPH_CHUNK_SOURCE`, `GRAPH_BUILD_CONCURRENCY`, `GRAPHITI_MAX_COROUTINES`, `GRAPH_LLM_EXECUTOR_WORKERS`, `GRAPH_RESOLVE_ENTITIES`, `GRAPH_MAX_ENTITIES`, `GRAPH_CAST_CHUNK_FILTER`, `GRAPH_CHUNK_MAX_ATTEMPTS`, `GRAPH_SEED_FROM_ACTORS`, `GRAPH_BUILD_COMMUNITIES` | dossier_only, 2500/250, concurrency 4, cast filter on, communities **off** |
| **Sim** | `SIM_TEMPORAL_MODE`, `OASIS_DEFAULT_MAX_ROUNDS`, `OASIS_MAX_AGENTS`, `ACTOR_CAST_MAX`, `SIM_RESUME_REUSE_COMPLETED`, `SIM_CONVERGENCE_STOP*`, `SIM_GRAPH_FEEDBACK`, `SIMULATION_FORECAST_EFFECT`, `N_FORECAST_SEEDS` | calendar; cast 20; resume reuse on; feedback **off**; diagnostic_only; seeds 1; *convergence stop likely unwired* |
| **Report** | `REPORT_SECTION_CONCURRENCY`, `REPORT_NATIVE_TOOLS`, `REPORT_SECTION_CONTEXT_MODE`, `REPORT_SIGNAL_PACK`, `REPORT_PURITY_ESCALATION_MAX`, `REPORT_VIZ_*`, publish/health gates | concurrency 6; brief; viz on; purity capped |

\*Treat `SIM_CONVERGENCE_STOP` as “declared intent” until a consumer in `decision_channel.py` / runner / scripts is verified.

---

## 7. Explicit non-goals / what not to change without product decisions

1. **Do not re-enable `SIM_GRAPH_FEEDBACK` / typed interview feedback** until WP10 isolated overlays exist (WP1 / I-11; epistemic contamination).
2. **Do not raise `N_FORECAST_SEEDS` > 1** without per-seed graph isolation (shared `graph_id` → fake ensemble; Foglamp map §3).
3. **Do not set `RESEARCH_ALLOW_STACKED_FANOUT=true`** for production cost control.
4. **Do not turn `GRAPH_BUILD_COMMUNITIES=true`** until upstream label-propagation convergence is fixed (known event-loop wedge).
5. **Do not treat simulation output as forecast authority** — keep `SIMULATION_FORECAST_EFFECT=diagnostic_only` until WP6/12/14 promotion (ADR 0002 / I-08).
6. **Do not dual-advance workflows** (Temporal + DB, or shadow store + legacy writes as authority) — ADR 0001 I-07.
7. **Do not auto-delete historical reports/charts** without an owner retention decision (loop-i6 explicitly left cleanup to humans).
8. **Do not refactor product code in the name of this doc** — recommendations only; prefer knob flips and measured experiments first.
9. **Do not cut research track count or fanout** solely from this audit — those change source diversity; follow the experiment protocol in `docs/RESEARCH_STAGE_OPTIMIZATION.md`.
10. **Do not expand actor cast (`ACTOR_CAST_MAX`) “for realism”** without accepting linear sim LLM cost growth.

---

## 8. Suggested measurement protocol (one page)

1. Pick one historical question / sealed handoff; enable `LLM_TELEMETRY_ENABLED=true`.
2. Baseline: one research-only run with current defaults + `LLM_RUN_BUDGET_TOKENS=30000000`.
3. Layer changes one at a time: (a) `LLM_FAST_MODEL` on a graph-inclusive run, (b) prompt-caching spike, (c) compaction, (d) optional shared-track seed.
4. Compare: total tokens, wall clock, `research_budget.json`, dossier quality gates, `pipeline_health`, graph node/edge counts after prune, publish-gate pass.
5. Reject any change that fails dossier/publication contracts even if cheaper.

---

## 9. Relationship to sibling docs

| Doc | Use for |
|---|---|
| `OPTIMIZATIONS.md` | Exhaustive stage-by-stage catalog with file:line evidence (many items already defaulted) |
| `docs/RESEARCH_STAGE_OPTIMIZATION.md` | Owner-gated research token levers + experiment protocol |
| `CODEX_RECOMMENDATIONS.md` | Security, contracts, publication integrity, broader roadmap |
| `EXECPLAN.md` / `EXECPLAN_FOGLAMP.md` | Structural fidelity & Foglamp authority program |
| `docs/adr/0001`, `0002`, `docs/foglamp/current-shape-map.md` | What must not be “optimized away” |
| **This file** | Post-i6 **priority order** for workflow perf/efficiency |

---

## 10. TL;DR

Research still burns the tokens; graph/sim still burn the clock. Contaminating shortcuts (graph feedback, stacked fanout, multi-seed on one graph) are correctly disabled — keep them disabled. The next honest wins are: **configure a real fast LLM tier**, **turn budgets on**, **spike prompt caching + compaction**, **wire or remove dead convergence stop**, and **stop fighting improved defaults with stale `.env` pins**.
