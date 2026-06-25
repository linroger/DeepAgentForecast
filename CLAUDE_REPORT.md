# CLAUDE_REPORT — DeepAgentForecast / DeepResearchForecast Architecture, Workflow & Remediation Audit

## 1. Title and Audit Metadata

- **Audit date:** 2026-06-25
- **Repository path:** `/Users/rogerlin/Downloads/DeepResearchForecast`
- **Product name:** `DeepAgentForecast` (README/`package.json`); the checkout directory and some legacy code still read `DeepResearchForecast` / `MiroFish`.
- **Audit scope:** Full-stack static source audit — Vue 3 frontend, Flask backend/API, the six-stage pipeline orchestrator, the DeerFlow research bridge, the local Graphiti/FalkorDB knowledge-graph layer, the OASIS simulation layer, the ReportAgent, configuration/provider system, operational scripts, tests, and documentation drift.
- **Methodology:** This is an *independent re-audit*. Three prior audit perspectives were read for context but **independently re-verified against current source**:
  - `CODEX_REPORT.md` (prior attempt, 505 lines),
  - `GEMINI-PRO-_REPORT.md` (16 KB, doc-derived),
  - a fresh 22-agent multi-agent audit workflow run for this report (12 subsystem readers + 10 adversarial verification probes; 2.8 M sub-agent tokens).
  Every headline finding below was additionally confirmed by the author reading the cited source lines directly.
- **Static vs. executed:** The audit itself is **static analysis** (no paid/LLM end-to-end pipeline run was executed for auditing). The remediation phase (§14) **did** run the project's offline quality gates (compile, import smoke, ruff, env-drift, pytest, Vite build); their results are recorded there.
- **Pre-existing dirty files (left untouched):** `M backend/uv.lock`, `D frontend/src/assets/logo/MiroFish_logo_left.jpeg`, and the staged report files `CODEX_REPORT.md` / `GEMINI-PRO-_REPORT.md`.

---

## 2. Executive Summary

DeepAgentForecast is a **one-prompt forecasting engine**. A user types a single future-facing question into a Vue dashboard; the Flask backend launches a **durable six-stage pipeline** — `research → ontology → graph → prepare → run → report` — that performs autonomous web research, distills it into a local temporal knowledge graph, populates and runs a multi-agent OASIS social simulation (dual Twitter + Reddit), and synthesizes a sectioned, evidence-grounded forecast report. The frontend polls pipeline state and progressively unlocks the dossier, graph, simulation, and report tabs as artifacts appear.

**What the architecture gets right** is process and contract isolation. DeerFlow (LangGraph stack) runs in its own Python venv as a subprocess and communicates only through a file handoff contract; OASIS (CAMEL-AI stack) likewise runs as a tracked subprocess with action logs. This cleanly resolves otherwise-fatal transitive dependency conflicts between the two AI stacks. The knowledge graph runs **locally** on embedded FalkorDB behind a Zep-SDK-compatible shim, so there is no SaaS dependency and no required API key. Pipeline state is **file-backed, schema-versioned (v2), heartbeat-aware, and resume-oriented**, with orphan reconciliation on restart and non-idempotent guards on the frontend. The research-only mode plus *edit-and-continue* is a genuinely high-leverage human-in-the-loop checkpoint.

**The risks are integration- and product-boundary risks, not conceptual ones.** The highest-value confirmed defects all live in the report layer and a legacy simulation API:

- **Report tool-registry drift** — `faction_brief` is advertised and dispatchable but absent from the validation set and the native-tool schema generator, so it is unusable in native mode and dropped in the bare-JSON ReAct fallback. The root cause is a *static* `VALID_TOOL_NAMES` set that drifts from the *dynamic* live tool set.
- **Outline fallback breaks the report's own 5–8 section contract** (returns 3), and the success path never enforces the count.
- **Partial reports masquerade as complete** — when sections fail, the report is still `status="completed"`; the failure count lives only in a log line and telemetry, never surfaced as a first-class status/field to the API or UI.
- **Legacy `/profiles` cannot return Twitter personas** — Twitter is written as CSV but `get_profiles()` only reads JSON.
- **Implicit China/social-opinion defaults** for a product positioned as *general* forecasting — `ONTOLOGY_TEMPLATE='social_opinion'` by default, and the simulation time-config prompt is hard-wired to Beijing activity rhythms.

**Recommended direction:** keep the six-stage pipeline and process isolation; tighten the inter-stage contracts; fix the concrete report/profile bugs; make domain/locale assumptions explicit per run (degrade-safe, default-preserving); reconcile stale docs and legacy `Zep`/`MiroFish` naming. The §14 remediation implemented the confirmed bugs plus a set of degrade-safe improvements in a single file-disjoint multi-agent sweep; larger refactors (splitting the 2.5–3.8 K-line modules, unifying run state) are documented as roadmap rather than attempted autonomously.

---

## 3. System Purpose and Product Model

**User-facing goal.** Given one natural-language prediction question (e.g., *"Will product X reach mainstream adoption within 18 months?"* or *"Who leads the memory-semiconductor market by 2030?"*), produce an interactive, cited forecast report — backed by real web research, a temporal knowledge graph, and an emergent multi-agent simulation — without the user hosting any database or service.

**One-prompt workflow.** The dashboard takes a prompt + a few options and runs the whole pipeline behind a single live, stage-by-stage view (`frontend/src/views/ResearchView.vue`). The only credential needed is one LLM: a local `claude`/`codex` CLI login (zero keys) or an API key for one hosted provider (`openai`, `kimi`, `minimax`, `deepseek`, `qwen`, `glm`).

**Supported modes.**
- **Full pipeline:** prompt → research → ontology → graph → prepare → run → report.
- **Research-only:** stops after research, returning an editable dossier; the user can curate it and then *continue to full*.
- **Edit-and-continue:** `PUT /dossier` lets a human fix the research before the expensive stages.
- **What-if / scenario fork:** reuses research/ontology/graph and re-forks prepare/run/report with influence/stance overrides and injected events.

**Expected user journey.** Enter prompt → (preflight banner confirms credentials) → launch → watch the stage timeline → inspect the research dossier (actors, sources, timeline) → inspect the knowledge graph → watch the simulated Twitter/Reddit forum → read the final forecast → optionally fork a what-if scenario or resume/continue.

---

## 4. Repository Layout

| Area | Responsibility | Key paths |
|---|---|---|
| Root scripts / metadata | Dev entry points, interactive setup, health/smoke checks, CI | `package.json`, `setup.sh`, `scripts/doctor.sh`, `scripts/smoke.sh`, `.github/` |
| Frontend (current) | Vue 3/Vite one-prompt dashboard | `frontend/src/views/ResearchView.vue`, `frontend/src/components/research/*`, `frontend/src/api/*`, `frontend/src/components/GraphPanel.vue`, `frontend/src/i18n.js` |
| Frontend (legacy) | Pre-one-prompt 5-step UI, still routable | `frontend/src/components/Step1-5*.vue`, `frontend/src/views/{Home,MainView,Process,Simulation*,Report,Interaction}View.vue` |
| Flask app/API | App factory, auth/CORS gates, REST blueprints | `backend/app/__init__.py`, `backend/app/api/{research,graph,simulation,report,settings,sdk}.py` |
| Pipeline engine | Durable six-stage orchestration, resume/cancel/fork/continue, artifacts, telemetry | `backend/app/services/pipeline_orchestrator.py` |
| Research bridge | DeerFlow subprocess + file handoff contract + model patches | `deerflow_bridge/deerflow_research.py`, `deerflow_bridge/{config.yaml,patches/*,skills/*}`, synced runtime at `deer-flow/` |
| Knowledge graph | Local Graphiti runtime + Zep-compatible facade + graph build/ingest/resolve/feedback | `backend/app/services/graphiti_client/*`, `graph_builder.py`, `ontology_generator.py`, `text_processor.py`, `zep_entity_reader.py`, `zep_entity_resolver.py`, `zep_graph_memory_updater.py`, `utils/actors.py` |
| Simulation | Entity→persona→config prep, OASIS subprocess runner, IPC, dynamics | `simulation_manager.py`, `oasis_profile_generator.py`, `simulation_config_generator.py`, `simulation_runner.py`, `simulation_ipc.py`, `agent_dynamics.py`, `backend/scripts/run_parallel_simulation.py` |
| Reporting | ReAct/native-tool report agent, graph/sim tools, structured forecasts, ensemble/backtest | `report_agent.py`, `zep_tools.py`, `forecast_extractor.py`, `ensemble.py`, `backtest.py` |
| Config / providers / observability | Config surface, LLM client, OASIS LLM bridge, telemetry, budgets, security/atomic helpers | `backend/app/config.py`, `utils/{llm_client,oasis_llm,telemetry,token_budget,retry,security,atomic,dates,logger}.py` |
| Tests | Offline unit/eval/security/contract coverage | `backend/tests/*` |
| Docs / demos | Product docs (current + legacy), GitHub Pages demo site | `README.md`, `README.zh-CN.md`, `ARCHITECTURE.md`, `DEERFLOW_INTEGRATION.md`, `docs/*` |

---

## 5. Architecture Map

```
                         ┌──────────────────────────────────────────────┐
   one prompt  ──────────▶  Vue ResearchView (/research)  ── polls 2.5s ─┼──┐
                         └──────────────────────────────────────────────┘  │
                                          │ POST /api/research/run          │ GET status / artifacts
                                          ▼                                 │
                         ┌──────────────────────────────────────────────┐  │
                         │  Flask app factory (backend/app/__init__.py)  │◀─┘
                         │  auth gate · CORS allowlist · traceback strip │
                         │  blueprints: research/graph/simulation/report/│
                         │  settings (+ optional /api/v1 sdk)            │
                         └───────────────────────┬──────────────────────┘
                                                 ▼
                         ┌──────────────────────────────────────────────┐
                         │   PipelineOrchestrator (pipeline_state.json)  │
                         │   6 stages · schema v2 · heartbeat/owner-lease│
                         │   resume · cancel · continue · fork · manifest│
                         └───┬───────────┬────────────┬──────────┬───────┘
        STAGE research       │  ontology │   graph    │ prepare  │ run / report
                             ▼           ▼            ▼          ▼
   ┌─────────────────────────────┐  ┌──────────┐ ┌─────────────────────┐ ┌────────────────────────┐
   │ DeerFlow bridge (subprocess │  │ Ontology │ │ Graphiti runtime    │ │ SimulationRunner       │
   │ + isolated venv)            │  │ Generator│ │ (embedded FalkorDB) │ │ OASIS subprocess       │
   │ → handoff/ contract files   │  │          │ │ + graph_builder     │ │ run_parallel_sim.py    │
   └─────────────┬───────────────┘  └────┬─────┘ └──────────┬──────────┘ └───────────┬────────────┘
                 │ research_report.md          │ chunks/seed actors/communities      │ actions.jsonl
                 │ actors.json (situation_brief│ + bi-temporal episodes              │ run_state.json
                 │  + relationships[] + tiers) │                                     │ run_summary.json
                 │ sources.json · timeline.json│                                     │ (→ graph feedback)
                 └─────────────────────────────┴──────────────┬──────────────────────┘
                                                              ▼
                                            ┌────────────────────────────────────┐
                                            │ ReportAgent (ReAct or native tools) │
                                            │ ZepToolsService: insight_forge/      │
                                            │ panorama/quick/interview/simulation_ │
                                            │ outcomes/coalition_map/opinion_shift/│
                                            │ scenario_diff/(faction_brief)        │
                                            │ → sections + full_report.md          │
                                            │ → telemetry.json / forecast.json     │
                                            └────────────────────────────────────┘
```

**Layer responsibilities.**
- **Frontend** (`ResearchView.vue` + `components/research/*`): one-prompt dashboard, 2.5 s polling, terminal-state handling, non-idempotent action guards, progressive artifact unlocking, settings/provider switching, history/cancel/resume/delete. Bilingual via an inline `L(zh,en)` helper (`frontend/src/i18n.js`).
- **Flask/API** (`backend/app/__init__.py` + `api/*`): app factory wires JSON-as-UTF8, logging with secret redaction, a CORS allowlist (`__init__.py:45-52`), a loopback-or-token auth gate (`:77-96`), traceback stripping outside DEBUG (`:107-121`), OASIS + pipeline orphan reconciliation at startup (`:54-71`), and five blueprints (plus an optional `/api/v1` SDK gated by `API_V1_ENABLED`).
- **Pipeline orchestrator** (`pipeline_orchestrator.py`, ~3 K lines): the durable state machine — stage constants/bands (`:68-82`), schema-versioned state with migrations (`:91+`), `start/cancel/resume/continue_to_full/fork`, heartbeat/owner-lease, `reconcile_orphans` (`:1300`), artifact manifests + `run.json`, dynamic cost-aware progress bands (`:1841+`).
- **DeerFlow research bridge** (`deerflow_research.py`, ~1.4 K lines): isolated-venv subprocess writing the handoff contract; tool-free synthesis + tool-free structured extraction fallbacks; deep fan-out; atomic writes.
- **Graphiti/local KG** (`graphiti_client/*`): asyncio-loop-owning runtime, per-graph-id client cache, episode/batch/concurrent ingest, `add_triplet`, bi-temporal `reference_time`, communities, entity-merge primitives, full search surface, and a rising-temperature retry adapter that handles MiniMax schema-echo failures.
- **OASIS simulation** (`simulation_*` + `run_parallel_simulation.py`): graph entities → capped persona set → dual-platform config (initial follows, scheduled events, echo chambers, recsys) → OASIS subprocess with action logs, run state, run summary, and optional graph feedback.
- **ReportAgent** (`report_agent.py`, ~3.8 K lines): outline planning, a tool registry over graph + simulation evidence, ReAct and native-tool section generation, concurrent sections, telemetry, and optional structured forecasts.
- **Persistence/artifacts:** file-backed under `backend/uploads/{projects,pipelines,reports}` + per-simulation dirs + the local FalkorDB on disk; all gitignored runtime data.
- **Config/provider system** (`config.py` + `llm_client.py` + `api/settings.py`): centralized flags/defaults, multi-provider LLM client with two-tier routing and native-tool support, `apply_provider()` writing `.env` under a lock.

---

## 6. End-to-End Workflow (prompt → forecast)

**Stage 0 — Launch & preflight.** `ResearchView.start()` → `POST /api/research/run` with `{prompt, mode, depth, max_rounds?, language?, model?}`. The backend validates prompt/mode/depth/language/model/max_rounds and calls `preflight_pipeline()` so missing credentials or a broken DeerFlow checkout fail *before* any subprocess starts (`backend/app/api/research.py`). The orchestrator mints a `pipeline_id`, writes `pipeline_state.json` + a `run.json` manifest, starts a heartbeat, and chooses `STAGE_BANDS` (full) or `RESEARCH_ONLY_BANDS` (`pipeline_orchestrator.py:1453+`).

**Stage 1 — Research.** If a valid `research_report.md` (≥ 400 chars) already exists (resume), it is reused; otherwise the orchestrator launches `DeerFlowResearchRunner.run()` as a subprocess in DeerFlow's isolated venv. The bridge runs multi-angle web search and writes the **handoff contract**: `research_report.md`, `prediction_requirement.txt`, best-effort `actors.json` (enriched: `situation_brief`, typed `relationships[]`, source tiers, quantitative facts, contested claims, forecast inputs), `sources.json`, `timeline.json`, `research_progress.log`, `meta.json` — all via atomic writes. Eager models that keep calling tools instead of emitting JSON are caught by a **tool-free structured-extraction** fallback (a bare model call with no tools bound).

**Stage 2 — Ontology.** A project is created/reused, the report is stored as extracted text, and `OntologyGenerator.generate()` derives entity/edge types from the report + prompt + actor-derived context. The active template defaults to `social_opinion` (exactly ~10 social-media-capable entity types); a `general_forecast` template exists and adapts to the actor distribution.

**Stage 3 — Graph build.** Reuse is validated (artifact manifest + zero-entity health check); otherwise a Graphiti graph is created, the dynamic ontology is set, researched actors/relationships are **seeded as typed edges**, the report is chunked and ingested as bi-temporal episodes (`reference_time = as_of`), communities are optionally built (Leiden), entity resolution optionally runs, and integrity metrics are recorded. Default ingest concurrency is 1 (safe).

**Stage 4 — Prepare.** `SimulationManager` reads graph entities, **caps agent count** (`OASIS_MAX_AGENTS=80`, preserving researched actors + high-influence/high-degree entities), generates OASIS personas (relationship-aware), writes `reddit_profiles.json` + `twitter_profiles.csv`, and generates `simulation_config.json` — initial follows from researched relationships + graph edges, scheduled events from the timeline, echo-chamber follows, platform configs, optional recsys wiring, and a time/activity config.

**Stage 5 — Run.** `SimulationRunner.start_simulation(platform='parallel')` launches `run_parallel_simulation.py` in a new process group (PID/PGID recorded), tails per-platform `actions.jsonl`, optionally truncates rounds, optionally enables sim→graph feedback, handles cancellation, waits for the feedback flush barrier, marks the stage complete, and writes `run_summary.json`. The dual loop runs Twitter + Reddit with influence-weighted activation, initial-follow injection, scheduled-event firing, and affective-state injection.

**Stage 6 — Report.** A `ReportAgent` is created with the graph id, simulation id, prompt, situation brief, actors, sources, research report, and optional scenario-diff context. It plans a 5–8 section outline, generates each section (ReAct or native tools) by querying the graph + simulation, assembles `full_report.md`, optionally extracts `forecast.json`, and records telemetry.

**Frontend rendering.** `ResearchView` polls `GET /api/research/status/<id>` every 2.5 s, fetches the research log while research is active, fetches the dossier once research completes, fetches graph data and simulation feed as those ids appear, loads the report when `report_id` is present, and stops polling on terminal states.

**Artifact/data flow (summary).** `prompt → handoff/ files → ontology.json → FalkorDB graph (graph_id ≈ group_id, prefixed mirofish_) → personas + simulation_config.json → actions.jsonl/run_state.json/run_summary.json (+ graph feedback) → report sections + full_report.md (+ telemetry.json/forecast.json)`. The orchestrator records pointers to these in `PipelineState.artifacts` and the run manifest.

---

## 7. Secondary Workflows

- **Research-only + edit-and-continue.** `mode=research_only` completes after research; `GET /dossier` returns report/actors/sources/timeline; `PUT /dossier` (atomic, gated to completed-research-only or pre-graph-failure) lets a human edit; `continue_to_full` re-enters ontology→report reusing the edited research.
- **Cancel / resume / delete / clean.** `cancel` sets a cancel event (cooperative cancel points + immediate research-subprocess-group kill + `stop_simulation`); orphans are marked `cancelled`. `resume` re-enters with stage-reuse guards (research ≥ 400 chars, graph zero-entity rebuild, `*_enabled` completion flags). `delete`/`clean_terminal` remove ended runs (running runs must be cancelled first).
- **Scenario fork (what-if).** Reuses research/ontology/graph; re-forks prepare/run/report under an overlay (influence/stance overrides, injected events, max rounds); the report exposes a `scenario_diff` tool and a forced comparison section when a base simulation exists.
- **Settings / provider switching.** The settings UI lists providers, maps them to DeerFlow models, live-tests connectivity (with SSRF guards), and `apply_provider()` writes `.env` under a lock. Changes apply to **new** runs only.
- **History.** `PipelineHistory.vue` lists runs and supports delete + bulk-clean of failed/cancelled runs.
- **Legacy/manual.** The Step1–5 components and `/api/simulation/*` manual routes (create/prepare/start/interview, `/profiles`, `/profiles/realtime`) remain from the pre-one-prompt era.

---

## 8. Operational and Developer Workflow

- **Setup:** `./setup.sh` (interactive) scaffolds the two venvs, seeds DeerFlow from the vendored RC, applies the bridge overlay, and live-tests the chosen provider key. `npm run setup:all` covers node + backend (`uv sync --python 3.12`).
- **Dev server:** `npm run dev` runs Flask (`:5001`) and Vite (`:3000`) via `concurrently`.
- **Doctor/preflight:** `npm run doctor` (`scripts/doctor.sh`) validates tooling, venvs, the DeerFlow overlay, env, the local graph backend, and provider credentials; `backend/scripts/preflight.py --json` is the unified programmatic preflight (also `GET /api/research/preflight`).
- **Smoke:** `npm run smoke` (`scripts/smoke.sh`) exercises inter-stage contracts offline, with optional live graph/OASIS legs.
- **Gates:** `npm run test` (pytest), `npm run lint` (ruff), `npm run check:env` (`check_env_drift.py --strict`, enforcing `.env.example ↔ Config` parity), `npm run build` (Vite). GitHub Actions CI wires the cheap gates.
- **Environment assumptions:** Python ≥ 3.12 (`backend/pyproject.toml`), Node ≥ 20.19, an embedded FalkorDB + a ~470 MB multilingual sentence-transformers model downloaded on first graph build, and a DeerFlow venv (~900 MB) assembled into `deer-flow/` (gitignored).

---

## 9. Strengths (worth preserving)

1. **Process isolation via file handoff.** DeerFlow (LangGraph) and OASIS (CAMEL-AI) run out-of-process in separate venvs; the only coupling is a documented file contract. This avoids fatal dependency contamination and keeps the Flask app stable.
2. **Local-first knowledge graph.** Embedded Graphiti/FalkorDB removes SaaS lock-in, API-key requirements, and remote rate limits, and keeps GraphRAG reproducible offline.
3. **Durable, resume-oriented state.** Schema-versioned `pipeline_state.json`, manifests, heartbeats, owner-lease, and orphan reconciliation directly target the cost of long LLM/OASIS jobs; stage reuse avoids re-paying for completed work.
4. **Honest lifecycle on restart.** `reconcile_orphans` (both pipeline and OASIS) identity-checks PID/PGID before killing, and marks stranded `running` records `failed/cancelled` so the frontend stops polling phantoms.
5. **Human-in-the-loop seam.** Research-only + edit-and-continue is a high-leverage quality checkpoint before the expensive stages.
6. **Defensive frontend.** Non-idempotent actions deliberately opt out of retry wrappers (`frontend/src/api/research.js`); components guard malformed data, stale responses, and transient disconnects.
7. **Security hygiene for a local app.** Loopback-or-token auth gate, single-chokepoint traceback stripping, secret redaction in logs, SSRF-guarded settings tests, default-localhost CORS, atomic writes.
8. **Observability + offline testing.** Per-run LLM telemetry (tokens/cost/latency by stage/model), an env-drift gate, and an offline `FakeLLMClient` pytest suite are the right baseline for a slow, expensive end-to-end system.

---

## 10. Findings, Issues, Bugs & Risks

Severity key: **P1** = correctness bug with user-visible impact; **P2** = reliability/quality risk or product-alignment gap; **P3** = polish/maintainability. "Confirmed" = author verified the exact source lines; "risk/smell/tradeoff" distinguished where relevant. Items marked **[FIXED §14]** were remediated this session.

### P1 — Confirmed bugs

**F1. Report tool-registry drift (`faction_brief`).** *Confirmed.* **[FIXED §14]**
- **Evidence:** `faction_brief` is defined in `_define_tools()` when `Config.GRAPH_COMMUNITY_RETRIEVAL` is true (`report_agent.py:1491`) and dispatchable in `_execute_tool()` (`:1584`), but it is **absent** from the static `VALID_TOOL_NAMES` set (`:1640-1641`). `_to_openai_tool_schemas()` iterates `VALID_TOOL_NAMES` (`:1932`), so it never emits a native schema for `faction_brief`. `_is_valid_tool_call()` (`:1690-1701`) gates only the *bare-JSON* fallback path of `_parse_tool_calls` (the XML `<tool_call>` path at `:1655` is unvalidated).
- **Impact:** When community retrieval is enabled, `faction_brief` is advertised in the system prompt (`_get_tools_description` iterates the live `self.tools`, `:1703-1711`) but (a) in native-tool mode it is missing from the `tools=` schema → the model cannot call it, and (b) in ReAct mode a bare-JSON call is silently dropped. The report degrades to behavior-log `coalition_map` instead of graph-native community evidence. Gated behind `GRAPH_COMMUNITY_RETRIEVAL` (default = `GRAPH_BUILD_COMMUNITIES` = false), so impact is scoped to users who enable communities.
- **Root cause:** a *static* validation/schema source that drifts from the *dynamic* tool set. The same drift silently affects the legacy redirect tools (`search_graph`, `get_graph_statistics`, etc.), which are dispatchable but not in `VALID_TOOL_NAMES`.
- **Fix:** derive valid names dynamically from `self.tools.keys()` ∪ a single legacy-alias set; make `_to_openai_tool_schemas` iterate the live tool set. (Implemented §14.)

**F2. Outline fallback violates the 5–8 section contract.** *Confirmed.* **[FIXED §14]**
- **Evidence:** the planning prompt requires "最少5个章节，最多8个章节" (`report_agent.py:669, 686`), but the `except` fallback returns exactly **3** sections (`:1827-1834`), and the success path (`:1805-1817`) accepts whatever the LLM returns with no clamp.
- **Impact:** on planning failure the report degrades to a 3-section shape that violates its own quality rules; the UI builds its table of contents from these headings. The success path can likewise under/over-shoot.
- **Fix:** 5-section fallback + clamp/pad the success path to 5–8. (Implemented §14.)

**F3. Completed reports hide failed sections.** *Confirmed.* **[FIXED §14]**
- **Evidence:** a section-level exception writes `SECTION_FAILURE_PLACEHOLDER` and appends the title to `failed_section_titles` (`report_agent.py:2641, 2669-2671`), but `report.status` is unconditionally set to `ReportStatus.COMPLETED` (`:2728`). The failure is only a log warning (`:2758-2763`) and a `telemetry.totals.failed_sections` count (`:2785`, present only when telemetry is enabled). No `partial`/`failed_sections` field is exposed on the report payload (`Report.to_dict`, `:548`), and `api/report.py` returns `report.to_dict()` verbatim.
- **Impact:** a user sees a "completed" report silently containing placeholder sections; the frontend has nothing to surface a warning from.
- **Fix:** add `failed_sections`/`partial` to `Report.to_dict()` (round-tripped through `report.json`) and a UI banner; keep `status="completed"` to avoid breaking terminal-state detection. (Implemented §14.)

**F4. Legacy `/profiles` cannot return Twitter personas.** *Confirmed.* **[FIXED §14]**
- **Evidence:** prepare writes Reddit personas as `reddit_profiles.json` (`simulation_manager.py:367, 399`) and Twitter personas as `twitter_profiles.csv` (`:370, 407`), but `get_profiles()` unconditionally reads `{platform}_profiles.json` (`:524`). `get_profiles("twitter")` looks for a non-existent `twitter_profiles.json` and returns `[]`. The `GET /api/simulation/<id>/profiles` route depends on this; only the newer `/profiles/realtime` route parses CSV (`api/simulation.py:1073-1075`).
- **Impact:** legacy/manual workflows show no Twitter personas even though the CSV exists.
- **Fix:** make `get_profiles()` parse `twitter_profiles.csv` for Twitter, mirroring the realtime parser's dict shape. (Implemented §14.)

### P2 — Reliability & product-alignment

**F5. Implicit social-opinion / China-centric defaults for a general-forecasting product.** *Confirmed (mix of configurable default + hardcoded prompt).* **[PARTIALLY ADDRESSED §14]**
- **Evidence:** `Config.ONTOLOGY_TEMPLATE` defaults to `social_opinion` (`config.py:134`). More than a default: the simulation time-config is hard-wired to Beijing rhythms — `CHINA_TIMEZONE_CONFIG` (`simulation_config_generator.py:38-53`), `TimeSimulationConfig` "基于中国人作息习惯" (`:96-120`), and the **LLM prompt itself** asserts "用户群体为中国人，需符合北京时间作息习惯" (`:804-812`), "时间配置需符合中国人作息习惯" (`:841`), and "时间符合中国人作息" (`:1175`), regardless of the forecast's actual geography.
- **Impact:** US/EU/global or non-social forecasts inherit a China social rhythm and an opinion-actor ontology that may overfit "actors who can post" rather than the entities that matter for causal forecasting.
- **Fix:** introduce an explicit `SIM_ACTIVITY_PROFILE` (default `china_social`, byte-identical) with `us_business`/`global_market` presets, and an opt-in `ONTOLOGY_AUTO_SELECT` classifier — both degrade-safe with current behavior as the default. (Implemented §14; making them the *default* is a product decision left to the user.)

**F6. Graph-ingest concurrency can duplicate entities; mitigation is decoupled.** *Confirmed risk (default-safe).* **[GUARDED §14]**
- **Evidence:** `add_episodes_concurrent()` documents that concurrency > 1 can create duplicate same-name nodes (read-before-commit entity resolution, no DB uniqueness constraint) (`graphiti_client/runtime.py`). `GRAPH_BUILD_CONCURRENCY` defaults to 1 (`config.py:505`, safe). Entity resolution (`zep_entity_resolver`, gated by `GRAPH_RESOLVE_ENTITIES`, default off) is the mitigation but is *not* coupled to concurrency.
- **Impact:** users chasing faster builds can silently degrade entity quality and downstream personas, with no signal.
- **Fix:** when concurrency > 1, run best-effort duplicate-name detection, record a `graph_duplicate_name_groups` metric, and warn (or auto-resolve) — default path (concurrency 1) unchanged. (Implemented §14.)

**F7. Dossier edit accepts empty/garbage research.** *Confirmed.* **[FIXED §14]**
- **Evidence:** `PUT /dossier` atomically writes any string `report` if state allows editing (`api/research.py:356-402`); no minimum-quality guard. The orchestrator otherwise treats a `research_report.md` ≥ 400 chars as reusable.
- **Impact:** a user can save an empty/whitespace report and silently produce a degenerate ontology/graph/simulation.
- **Fix:** reject report bodies below a `MIN_DOSSIER_CHARS` threshold (400); keep actors/sources optional. (Implemented §14.)

**F8. Provider switching is global; runtime caches clients.** *Confirmed tradeoff (correct for in-flight runs, surprising across restarts).*
- **Evidence:** `apply_provider()` rewrites `.env` globally under a lock; the Graphiti runtime caches one LLM/embedder per graph id after first construction (`graphiti_client/runtime.py`). The orchestrator records provider info into manifests at stage boundaries. Settings say switches apply to new runs only, and `ResearchView.onProviderChanged()` deliberately does not refresh active runs.
- **Impact:** generally correct, but a cached graph runtime can keep an older client for subsequent graph work after a settings change until restart. Provider config is not snapshotted per-run as an immutable object.
- **Proposed solution:** construct an immutable `ProviderConfig` snapshot at `POST /run` and thread it through the pipeline; add `GraphitiRuntime.reset_clients()` after settings changes or include provider identity in the cache key. (Documented; not auto-implemented — touches shared modules and warrants review.)

**F9. Active-pipeline restore trusts `localStorage` and skips preflight.** *Confirmed (workflow-surfaced).*
- **Evidence:** on mount, `ResearchView` reads `mirofish_active_pipeline` from `localStorage` and calls `beginPipeline(saved)` with no existence check (`ResearchView.vue:523-528`); cleanup depends on the first poll returning exactly HTTP 404 (`:464-469`). Non-404 errors are treated as transient connection loss (`:471-477`).
- **Impact:** after a run was deleted elsewhere, the user briefly sees a phantom "Running" pipeline; a backend 500 for a stale id keeps the spinner alive indefinitely. The storage key also still carries the legacy `mirofish_` brand prefix.
- **Proposed solution:** validate the stored id with one status call on mount; clear the key and fall back to preflight on any 4xx; rename the key. (Documented.)

**F10. State is durable but scattered across roots/managers.** *Confirmed smell.*
- **Evidence:** pipeline state/handoff dirs, project files, per-simulation dirs, run-state files, report folders, the graph DB, and artifact manifests are all separate; there is no single queryable run index beyond manager scans.
- **Impact:** cleanup, debugging, migration, and history get harder as runs accumulate; partial delete can orphan graphs/reports/simulations.
- **Proposed solution:** a small SQLite run index (or a single `runs/<pipeline_id>/` root with pointers), a delete dry-run manifest, and a `doctor --runs` orphan/stale-DB checker. (Roadmap.)

**F11. Oversized modules raise change risk.** *Confirmed tradeoff.*
- **Evidence:** `report_agent.py` (3,847 LOC), `pipeline_orchestrator.py` (2,993), `backend/scripts/run_parallel_simulation.py` (2,893), `api/simulation.py` (2,719), `zep_tools.py` (2,541), `simulation_runner.py` (2,124); frontend `Step4Report.vue` (5,150).
- **Impact:** understandable after close reading but expensive to change safely; the F1 tool drift is a direct symptom of a sprawling module duplicating a registry across four sites.
- **Proposed solution:** extract stage modules from the orchestrator and a `tool_registry/parsing/outline/section/assembler/telemetry` split from the report agent — *with tests around the seams first*. (Roadmap; deliberately not attempted in the autonomous sweep.)

### P3 — Polish & maintainability

**F12. Pervasive legacy `Zep`/`MiroFish` naming.** *Confirmed.* **[DOCS RECONCILED §14]**
- **Evidence:** 28 backend files reference `MiroFish`; the five graph services keep `Zep`-era names (`zep_entity_reader.py`, `zep_graph_memory_updater.py`, `zep_tools.py`, …); graph IDs are literally prefixed `mirofish_`; `config.py` keeps a `ZEP_API_KEY` sentinel for old guards; `simulation_manager` logs "connecting to Zep graph". `ARCHITECTURE.md` is titled "MiroFish — Architecture"; `DEERFLOW_INTEGRATION.md:15` cites `resume()` at `pipeline_orchestrator.py:937-995`, but the actual `resume()` is at **line 1593**.
- **Impact:** onboarding confusion (engineers hunt for a Zep account that no longer exists) and stale doc references.
- **Fix:** reconcile docs + line refs now; treat the code rename as a separate, test-guarded refactor. (Docs fixed §14; code rename = roadmap.)

**F13. GraphPanel mixes hardcoded Chinese/English.** *Confirmed.* **[FIXED §14]**
- **Evidence:** `GraphPanel.vue` imports the inline `L()` helper (`:246`) and uses it in places (`:227`) but leaves many display strings single-language.
- **Fix:** wrap the static display strings in `L(zh,en)`, conservatively (no identifiers/keys). (Implemented §14.)

**F14. Stale operational instruction.** *Confirmed.* **[FIXED §14]**
- **Evidence:** `SimulationManager.get_run_instructions()` tells users to `conda activate MiroFish` (`simulation_manager.py:559`) — there is no such conda env in the current uv-based setup.
- **Fix:** update to the current workflow. (Implemented §14.)

**F15. Dossier tab can unlock with an empty report.** *Confirmed (workflow-surfaced).*
- **Evidence:** backend sets `has_report = (report is not None)` (`api/research.py:354`), so an empty file yields `has_report:true`; `ResearchView` gates the tab on `has_report` and auto-switches to it, while `DossierViewer.hasReport` requires non-empty content → the tab opens to an empty panel.
- **Impact:** a "completed" research run auto-navigates to an empty-looking dossier. Mild confusion.
- **Proposed solution:** make `has_report` require non-empty stripped content, or compute `researchDone` from non-empty report. (Documented; small, low-risk follow-up.)

**F16. Structured forecasts are valuable but off by default and underexposed.** *Confirmed tradeoff.*
- **Evidence:** `forecast_extractor.py` produces scenario probabilities, drivers, indicators, and a citation audit, but `ReportAgent` writes `forecast.json` only when `REPORT_STRUCTURED_FORECAST` is enabled (`config.py:122`, default false).
- **Proposed solution:** enable for `full` mode once cost/coverage are acceptable; add a Forecast subpanel (probability table, resolution criteria, citation coverage) and an eval that checks probability sums and date formats. (Roadmap.)

### Non-issues / refuted

- **Generated files polluting source — mostly refuted.** `frontend/src/logs`, `frontend/src/api/logs`, `backend/uploads`, top-level `logs/`, and `*.jsonl` are all covered by `.gitignore` (`logs/`, `*.jsonl`, `backend/uploads/`) and are **not tracked**; they are runtime clutter inside source dirs (minor housekeeping) rather than committed artifacts. The only tracked "data" is `docs/demos/*` (78 files), which are intentional GitHub-Pages demo fixtures.
- **Cancellation dishonesty — refuted.** `cancel()` (`pipeline_orchestrator.py:1509`) is honest: it sets a cancel event consumed at cancel points, kills the research subprocess group immediately, stops OASIS via `stop_simulation`, and marks orphans `cancelled`. The only nuance is *latency*: a cancel takes effect at the next cancel point (e.g., a mid-flight LLM call finishes first). Mid-run OASIS resume is honestly deferred (stage-level only) due to non-idempotent upstream CAMEL/OASIS table creation — documented, not silently no-op.
- **Non-idempotent endpoints retried — refuted.** The frontend retry wrapper deliberately excludes `run`/`start`/`prepare`/`create` (`frontend/src/api/research.js`, `simulation.js`); GETs are retried, expensive POSTs are not. (A residual double-click window exists client-side; see F9-adjacent note — server-side single-flight is a possible hardening, not a current bug.)

---

## 11. Refinements and Enhancements

**Performance / cost.**
- Preflight **budget/ETA band** from depth + provider + `max_rounds` + `OASIS_MAX_AGENTS` to prevent accidental expensive runs.
- Default `REPORT_SECTION_CONTEXT_MODE=brief` above an outline-length threshold to avoid O(N²) context growth.
- Native tool calling (`REPORT_NATIVE_TOOLS`, default off) becomes lower-risk to enable once the F1 registry is unified; cuts ReAct parsing failures and tokens for tool-reliable providers.
- Section-aware graph chunking that preserves source/actor boundaries for long reports.

**Reliability.**
- A single shared artifact-parser module for profiles/dossier/run-summary so the CSV-vs-JSON split (F4) cannot recur across endpoints.
- A richer status taxonomy surfaced to the UI: `completed` vs `partial_completed` vs `failed_recoverable/terminal` vs `cancelled` (F3 adds the `partial` flag as a first step).
- Run archive/export bundle (state + handoff + report + run summary + telemetry + graph stats) for debugging and demos.
- Golden contract tests around the exact API payload shapes the frontend consumes (the audit workflow enumerated them; see §13).

**UX / product.**
- A scenario-builder view (influence sliders, stance dropdowns, injected events) instead of hand-crafted JSON overlays.
- Research-quality panel (source tiers, contested claims, quantitative facts, coverage), graph-quality panel (component count, duplicate-name warnings, communities, merges), and forecast-calibration panel (scenario probabilities, indicators, resolution criteria).
- Locale/domain presets ("global market", "US politics", "China social opinion", "technology adoption") mapping to ontology template + activity profile + platform weights (F5 lays the groundwork).

**Observability.**
- A token/cost dashboard aggregating the existing per-stage telemetry across the six stages.
- Surface "outline fallback used" / `failed_sections` / `graph_duplicate_name_groups` in run metadata and UI.

**Security / maintainability.**
- Immutable per-run `ProviderConfig` snapshot (F8).
- Code-level `Zep`/`MiroFish` rename behind tests (F11/F12).
- Server-side single-flight for `run`/`continue`/`resume` POSTs.

---

## 12. Proposed Implementation Roadmap

**Phase A — concrete bug fixes (this session, §14):** F1 tool registry, F2 outline fallback, F3 partial status, F4 Twitter profiles, F7 dossier validation, F13 GraphPanel i18n, F14 stale instruction, F12 doc reconciliation; plus degrade-safe groundwork F5 (locale/ontology flags) and F6 (concurrency dedup guard).

**Phase B — contract & state hardening (next):** shared artifact-parser module; per-run immutable `ProviderConfig` + runtime cache reset (F8); graph duplicate-name auto-resolution when concurrency > 1; richer status taxonomy + UI surfacing; golden API-contract tests; F9/F15 frontend restore + `has_report` fixes.

**Phase C — product alignment:** make `general_forecast` / locale presets the default (or auto-select) once validated; first-class research-quality, graph-quality, and forecast panels; enable structured forecasts for `full` mode (F16); finish doc/naming reconciliation.

**Phase D — longer-term architecture:** split the oversized orchestrator/report/simulation modules behind tests (F11); a single SQLite run index (F10); the code-level `Zep`/`MiroFish` rename; (upstream-blocked) mid-run OASIS resume.

**Order rationale:** Phase A removes user-visible correctness defects with localized, low-blast-radius edits. Phase B makes the inter-stage contracts and run lifecycle robust so later refactors are safe. Phase C aligns defaults with the broad-forecasting product. Phase D is the high-risk structural work that should follow the contract/test scaffolding from B.

---

## 13. Suggested Quality Gates

- **Doc-only changes:** `git diff --check`; quick `rg` for stale paths/line refs.
- **Backend changes:** `npm run check:env` · `npm run lint` · `npm run test` · `npm run smoke` · targeted pytest for the touched service.
- **Frontend changes:** `npm run build` · manual `/research` launch-state + history + settings check.
- **Full-pipeline changes:** start with `mode=research_only` (cheap dossier), then a capped `full` run (`max_rounds` 1–3); capture `pipeline_state.json`, handoff dir, graph stats, `run_state.json`/`run_summary.json`, report folder, and UI screenshots.
- **Live/LLM-costly validation:** sparingly; prefer `scripts/smoke.sh` offline; when live, use `depth=quick` + small `max_rounds` to bound spend.

---

## 14. Implementation Log — Fixes Applied This Session

The remediation ran as a **file-disjoint multi-agent sweep** (10 parallel implementation agents, each owning a non-overlapping set of files, then a verification agent running the project gates). File-disjoint ownership — not git worktrees — was used deliberately: the project's own hard-won rule is that the large shared modules (`pipeline_orchestrator.py`, `report_agent.py`) must not be mutated in parallel, so each file had exactly one owner and two cross-file contracts were pre-agreed (`partial-report` fields; `config-flags` names/defaults). All behavior changes are **degrade-safe**: the default/unflagged path is byte-identical to before.

### 14.1 Changes by finding

| Finding | Fix | File(s) | Default-preserving? |
|---|---|---|---|
| **F1** tool-registry drift | Replaced the static `VALID_TOOL_NAMES` with a dynamic `_valid_tool_names()` = `set(self.tools.keys()) ∪ _LEGACY_TOOL_ALIASES`; `_is_valid_tool_call` validates against it; `_to_openai_tool_schemas` now iterates `sorted(self.tools.keys())` so `faction_brief`/`scenario_diff` are exposed natively exactly when defined | `report_agent.py` (~1654-1673, 1726, 1962-1982) | Yes — with `faction_brief` off, the native schema list is byte-identical (verified: same 7-tool sorted list) |
| **F2** outline fallback | Fallback now returns **5** sections (was 3); the success path **clamps to 5-8** (pads from fallback titles if <5, truncates if >8, logs both) | `report_agent.py` (~1058-1068, 1853-1879, 1893-1900) | Yes — clamp is a no-op for any LLM outline already in [5,8]; only the error path changes |
| **F3** partial-report status | `Report` gains `failed_sections: List[str]`; `to_dict()` emits `failed_sections` + `partial`; set at COMPLETED; round-tripped through `report.json` in `ReportManager.get_report`. Frontend renders a warning banner | `report_agent.py` (~538-571, 2756-2760, 3744-3748), `ForecastReport.vue` (computed `failedSections`/`isPartial` + scoped `.partial-warning` banner) | Yes — `status` stays `"completed"`; old reports without the key reconstruct to `[]`/`false` (verified round-trip) |
| **F4** Twitter profiles | `get_profiles()` is now platform-aware: `twitter` reads `twitter_profiles.csv` via `csv.DictReader` (mirrors the realtime parser's dict shape); reddit JSON path unchanged | `simulation_manager.py` (`import csv` L8; get_profiles ~518-549) | Yes — default `platform='reddit'` path byte-identical; the broken `twitter`→`[]` case is now fixed |
| **F7** dossier validation | `PUT /dossier` rejects (HTTP 400) reports whose stripped length < `MIN_DOSSIER_CHARS=400`, before the atomic write; actors/sources stay optional | `api/research.py` (const ~34-36; guard ~386-398) | Yes — valid edits (≥400 chars) still pass unchanged |
| **F5** locale/ontology (degrade-safe groundwork) | New `SIM_ACTIVITY_PROFILE` (default `china_social` = verbatim current behavior; `us_business`/`global_market` presets) wired through the time-config prompt/defaults; new `ONTOLOGY_AUTO_SELECT` (default false) with a deterministic bilingual classifier choosing `general_forecast` vs `social_opinion` | `config.py` (L137, L535), `.env.example`, `simulation_config_generator.py` (`ACTIVITY_PROFILES` + `get_activity_profile`), `ontology_generator.py` (`SOCIAL_OPINION_KEYWORDS` + `_auto_select_template`) | Yes — both default to current behavior; making them the *default* is a deferred product decision |
| **F6** graph concurrency guard | In the GRAPH rebuild branch, when effective `GRAPH_BUILD_CONCURRENCY>1`, a best-effort duplicate-name detection groups entity nodes by NFKC-normalized name, records the count, and warns; concurrency==1 path untouched | `pipeline_orchestrator.py` (after the `GRAPH_RESOLVE_ENTITIES` block, ~2664-2696) | Yes — only runs in the opt-in concurrency>1 path |
| **F13** GraphPanel i18n | Wrapped 21 static display strings in the existing inline `L('zh','en')` helper (title, buttons/tooltips, overlays, legend, detail labels); no identifiers/keys touched | `GraphPanel.vue` | N/A (cosmetic; logic untouched) |
| **F14** stale instruction | `get_run_instructions()` text updated from `conda activate MiroFish` to the current uv workflow; dict keys/structure unchanged | `simulation_manager.py` (~577-585) | Yes — only the human-readable string changed |
| **F12** doc drift | `DEERFLOW_INTEGRATION.md` resume line ref `937-995`→`1593-1655` (both occurrences); `ARCHITECTURE.md` scope banner pointing to README for the current workflow + corrected service count (12→18) and blueprint list | `DEERFLOW_INTEGRATION.md`, `ARCHITECTURE.md` | N/A (docs) |
| (env-drift) | Documented the pre-existing-undocumented `GRAPH_COMPONENT_WARN_RATIO=0.5` so `.env.example` ↔ `Config` parity is restored | `.env.example` | N/A |

**Files changed (13):** `report_agent.py`, `simulation_manager.py`, `api/research.py`, `config.py`, `.env.example`, `simulation_config_generator.py`, `ontology_generator.py`, `pipeline_orchestrator.py`, `ForecastReport.vue`, `GraphPanel.vue`, `ARCHITECTURE.md`, `DEERFLOW_INTEGRATION.md` (`README.md` was already current and was not touched). No file outside the audit-fix scope was modified.

### 14.2 Verification (offline gates)

| Gate | Result |
|---|---|
| Backend import smoke (`create_app()`) | **PASS** |
| Backend `compileall app` | **PASS** |
| `check_env_drift.py --strict` | **PASS** (after documenting `GRAPH_COMPONENT_WARN_RATIO`) |
| `pytest` (offline suite) | **PASS** — 91 passed |
| `git diff --check` (whitespace) | **PASS** |
| Frontend build of the two edited `.vue` files | **PASS** (validated with the asset temporarily restored) |
| `ruff check app` | **54 errors — PRE-EXISTING, not a regression** (identical 54 at `HEAD` and after the changes, in files no agent touched) |
| `npm run build` (full) | **FAILS — PRE-EXISTING**, blocked by the user's working-tree deletion of `frontend/src/assets/logo/MiroFish_logo_left.jpeg`, which legacy `Home.vue` still imports |

**Two pre-existing failures were confirmed *not* caused by this work and were left for the user to decide:**
- **Ruff (54 errors).** A clean `HEAD` worktree reports the *same* 54 errors (F541 f-strings-without-placeholders, B904 raise-without-from, B905 zip-without-strict, etc.), almost all in files outside the fix set (`api/simulation.py`, `oasis_profile_generator.py`, `simulation_runner.py`, `backtest.py`, `forecast_extractor.py`, `graph_builder.py`, `oasis_llm.py`, `llm_client.py`). The fixes added **zero** new lint errors. Fixing 54 pre-existing lint issues across unrelated files was deliberately left out of scope; `ruff check app --fix` clears 39 of them mechanically if desired.
- **Frontend build.** Blocked solely by the pre-existing `D frontend/src/assets/logo/MiroFish_logo_left.jpeg` (a user change preserved per the audit constraints). `Home.vue:42` still imports that asset. The build passes once the asset is present; the two `.vue` files edited here compile cleanly. Recommended resolution (the user's call, since they deleted it): either restore the asset or update `Home.vue` to reference an existing logo — not done here to avoid reverting a deliberate user deletion.

### 14.3 Not auto-implemented (deferred to roadmap, by design)

The following were intentionally **not** attempted in an autonomous parallel sweep because they are high-blast-radius or require a product decision; see §11–12:
- Splitting the oversized modules (F11) and the code-level `Zep`/`MiroFish` rename (F12) — need a test scaffold first.
- Per-run immutable `ProviderConfig` snapshot + runtime cache reset (F8) — touches shared LLM/runtime modules.
- Making `general_forecast`/locale presets the *default* (F5) and enabling structured forecasts by default (F16) — product decisions.
- Frontend restore-on-mount hardening + `has_report` empty-file fix (F9/F15) and a single SQLite run index (F10).

---

## 15. Final Assessment

DeepAgentForecast is architecturally ambitious but coherent: a real durable, resumable pipeline rather than a monolithic request, with disciplined process isolation, a local-first graph, and a progressive dashboard. Its weaknesses are concentrated and addressable — a report tool-registry that drifts, an outline fallback and partial-status path that hide failures, a legacy profile-format gap, and product defaults that still assume a China social-opinion world.

The right next moves are narrow and contract-focused: land the confirmed-bug fixes (done in §14), make the inter-stage contracts and run lifecycle robust, surface quality signals (failed sections, graph duplicates, structured forecasts) to users, and align domain/locale defaults with the broad-forecasting product — *before* taking on the larger module-splitting and state-unification work. The engine is strong; the leverage now is in visibility and contracts, not more engines.
