# DeepAgentForecast — Architecture & Workflow Audit Report

## 1. Title and Audit Metadata

- **Date:** 2026-06-24
- **Repository Path:** `/Users/rogerlin/Downloads/DeepResearchForecast`
- **Audit Scope:** Complete codebase architecture, workflow mapping, test coverage evaluation, and identification of bugs, risks, and enhancement opportunities.
- **Methodology:** Static source code analysis and documentation review (`README.md`, `ARCHITECTURE.md`, `DEERFLOW_INTEGRATION.md`, `handoff.md`), followed by targeted inspection of core backend services, frontend Vue components, and DeerFlow integration scripts.
- **Note:** This report is based on static analysis; no live, cost-incurring pipeline tests or LLM workflows were executed during this documentation task to preserve resources. Unrelated pre-existing dirty files were intentionally untouched.

---

## 2. Executive Summary

DeepAgentForecast is an autonomous "one prompt → forecast" engine. From a single natural-language question, it orchestrates a complex multi-agent pipeline: autonomous deep web research (DeerFlow 2.0), temporal knowledge graph generation (Graphiti/FalkorDB), multi-agent population simulation (OASIS dual-platform), and tool-augmented forecast synthesis (ReAct ReportAgent).

**Overall Architecture:** The application consists of a Vue 3/Vite frontend and a Flask backend orchestrating detached Python subprocesses. It bridges two distinct AI stacks—DeerFlow (LangGraph) for research and MiroFish/OASIS for simulation—sharing data via a file-based handoff contract and a local Graphiti knowledge graph. The integration ensures strict environment isolation between the stacks.

**Strongest Design Choices:**
- **File-Based Handoff:** Isolating the LangGraph dependencies of DeerFlow from the CAMEL-AI dependencies of OASIS via subprocesses and a filesystem contract prevents severe dependency conflicts.
- **Local Knowledge Graph:** Migrating from Zep Cloud to an embedded Graphiti/FalkorDB local graph reduces latency, eliminates API key dependencies, and ensures data privacy.
- **Structured Persona Seeding:** Grounding simulation personas in researched real-world actors (`actors.json`) vastly improves simulation fidelity over purely generated LLM profiles.
- **Graceful Degradation:** The pipeline utilizes robust fail-open guards (e.g., tool-free synthesis nets, section-level degradation) to salvage runs that experience transient LLM or network failures.

**Highest-Priority Risks:**
- **State Scattering:** Pipeline state is distributed across multiple fragmented artifacts (`pipeline_state.json`, `run_state.json`, `progress.json`), risking synchronization drift during resume/cancellation operations.
- **Tool Registry Drift:** Inconsistencies between tool execution logic (e.g., `faction_brief`) and the ReAct tool schemas can lead to hallucinated or rejected tool calls by the ReportAgent.
- **Duplicate Graph Entities:** Parallel graph ingestion can produce duplicate entities if semantic name matching or entity resolution logic fails, degrading retrieval quality.

**Recommended Next Moves:**
- Hardening the ReAct ReportAgent by natively supporting OpenAI-compatible tool calling across all viable providers, reducing reliance on brittle ReAct prompts.
- Refactoring `SimulationRunner` to break down its bloated ~1700-line monolithic structure into focused domain services.
- Unifying state tracking into a single, ACID-compliant source of truth instead of disparate JSON files.

---

## 3. System Purpose and Product Model

**Goal:** Provide an end-to-end predictive analysis tool that simulates how social dynamics and real-world actors will react to or shape future events.

**Workflow Model:** "One-prompt workflow".
1. The user inputs a single predictive question (e.g., "Who wins the US AI race by 2030?").
2. The system fetches factual evidence, builds a semantic web of relationships, breathes life into the actors via LLM personas, and observes their interactions in a compressed, simulated timeframe (72 simulated hours on Twitter/Reddit).
3. An analyst agent (ReportAgent) reads the simulation logs and knowledge graph to produce a well-structured forecast report.

**Supported Modes:**
- **Full Pipeline:** Prompt → Research → Graph → Simulate → Report.
- **Research Only:** Pauses after the deep-research stage, allowing the user to read, edit, and curate the dossier before proceeding to simulation.
- **What-If Scenarios:** Forks an existing pipeline at the simulation stage to inject alternative events or alter actor influence.

---

## 4. Repository Layout

| Path | Responsibility |
|---|---|
| `/frontend/` | Vue 3 + Vite SPA (port 3000). Interactive 6-stage timeline dashboard. |
| `/backend/` | Flask API (port 5001). Orchestrates pipelines, serves API endpoints, and manages the local Graphiti instance. Pinned to Python 3.12. |
| `/backend/app/api/` | HTTP Blueprint routes (graph, simulation, report, research, settings). |
| `/backend/app/services/` | Core pipeline orchestration modules (12+ services). |
| `/backend/app/services/graphiti_client/` | Zep-SDK compatible shim translating requests to the embedded Graphiti/FalkorDB local graph. |
| `/backend/scripts/` | Python scripts launched as detached subprocesses to run OASIS simulations (`run_parallel_simulation.py`, etc.). |
| `/deer-flow/` | DeerFlow 2.0 research engine. Gitignored, built into an isolated venv. |
| `/deerflow_bridge/` | Overlay applied to DeerFlow providing the `deerflow_research.py` driver and model patches. |

---

## 5. Architecture Map

- **Frontend:** Vue 3 Composition API using a custom `axios` wrapper for API communication. It utilizes a polling mechanism (no WebSockets) to fetch live progress logs and status updates from the backend.
- **Flask API Layer:** Manages routing and asynchronous job dispatch. Core operations use thread-safe in-memory `Task` objects, falling back to disk-backed state for crash survivability.
- **Pipeline Orchestrator:** (`PipelineOrchestrator`) Glues the stages together, handling cancellation (killing process groups), resumption (re-using completed stages), and status aggregation.
- **DeerFlow Research Bridge:** Operates as a detached subprocess executing `deerflow_research.py` inside its own Python 3.12 virtual environment. Passes results via a physical `handoff/` directory.
- **Graphiti / Local KG:** Embedded Graphiti engine running on `falkordblite`. The backend uses a Zep-compatible shim layer to communicate with Graphiti, supporting RRF reranking and synchronous batch ingestion.
- **OASIS Simulation:** Multi-agent CAMEL-AI OASIS simulation running concurrently on Twitter and Reddit environments. Powered by a daemon monitor thread that tails JSONL action logs.
- **ReportAgent:** A ReAct-based agent containing a `ZepToolsService` toolkit (`insight_forge`, `panorama_search`, `interview_agents`) to query both the KG and simulation state to draft the final report.

---

## 6. End-to-End Workflow

1. **Launch / Preflight (`POST /api/research/run`):** Validate LLM provider configurations, keys, and paths before starting. Creates a `Pipeline` artifact directory.
2. **Research Stage:** Flask spawns the DeerFlow subprocess. The agent searches the web, builds a dossier, and outputs `research_report.md`, `actors.json`, `timeline.json`, and `sources.json`.
3. **Ontology Stage:** The LLM reads the dossier and `actors.json` to generate an ontology consisting of exactly 10 entity types and 6-10 edge types.
4. **Graph Stage:** The text is chunked and ingested into Graphiti. Local sentence-transformers compute embeddings. 
5. **Prepare Stage:** The system creates personas for graph entities (`OasisProfileGenerator`) and generates the simulation configuration (`SimulationConfigGenerator`), establishing peak hours, echo chambers, and initial posts.
6. **Run Stage:** `run_parallel_simulation.py` is executed. Agents loop through actions. A monitor thread tails `actions.jsonl` to provide live updates and update the graph via `ZepGraphMemoryUpdater`.
7. **Report Stage:** `ReportAgent` uses ReAct or native tool calls to plan an outline and synthesize sections iteratively.
8. **Frontend:** The Vue app polls `/api/research/status/<id>` and specific endpoint logs, rendering progress incrementally.

---

## 7. Secondary Workflows

- **Research-Only Mode:** Stops after step 2. Allows manual editing of the `research_report.md` (`PUT /api/research/<id>/dossier`) before hitting "Continue to Full Pipeline".
- **Cancel / Resume:** `POST /<id>/cancel` sends SIGTERM to process groups. `POST /<id>/resume` bypasses completed stages via presence checks on existing files (e.g., skips Graph build if entity count > 0).
- **What-If Forking:** `POST /api/research/<id>/scenario` duplicates the research and graph layers but restarts the prepare/run stages with new overlays (modified influence weights or injected events).
- **Settings / Provider Switching:** `POST /api/settings/llm` changes the provider for future runs. `LLM_PROVIDER` governs simulation/extraction, while `DEERFLOW_MODEL` governs deep research.

---

## 8. Operational and Developer Workflow

- **Setup:** `./setup.sh` handles end-to-end interactive setup, applying the bridge overlay and scaffolding venvs.
- **Preflight:** `npm run doctor` quickly validates environment hygiene, python versions, and LLM credentials.
- **Dev Server:** `npm run dev` uses `concurrently` to run Flask (`:5001`) and Vite (`:3000`).
- **Tests & Quality:** `npm run test` executes pytest suite. `npm run lint` uses `ruff`. `npm run check:env` prevents `.env.example` drift. `npm run smoke` provides offline smoke tests.

---

## 9. Strengths

- **Fault Tolerance:** Stage-aware resumption allows skipping expensive re-computations of Graph and Research artifacts if a simulation fails midway.
- **Subprocess Isolation:** Utilizing a bridge to run LangGraph and CAMEL-AI in separate Venvs cleanly resolves severe transitive dependency conflicts.
- **Local Embedded Graph:** Removing Zep Cloud in favor of local Graphiti drastically reduces third-party dependencies, mitigates remote API limits, and prevents lock-in.
- **LLM Agnosticism:** Extensive support for local CLIs (Claude, Codex) and multiple APIs (Kimi, Deepseek, Qwen, Minimax) without hardcoding tightly coupled integrations.

---

## 10. Findings, Issues, Bugs, and Risks

### [P1] Graph Concurrency Duplicate-Node Risk
- **Evidence:** `ARCHITECTURE.md` states "parallel ingest... `GRAPH_BUILD_CONCURRENCY`".
- **Impact:** Parallel extraction requests to Graphiti may cause race conditions when identifying existing nodes, resulting in duplicated canonical entities in FalkorDB.
- **Solution:** Enforce strict sequential ingestion (`GRAPH_BUILD_CONCURRENCY=1`) or implement a robust pre-extraction deduplication lock mechanism in the `graphiti_client`.

### [P1] Tool Registry Drift (`faction_brief`)
- **Evidence:** `handoff.md` notes "`faction_brief` report-tool registry drift".
- **Impact:** The `ReportAgent` may attempt to call `faction_brief` using an outdated JSON schema or missing argument, causing the LLM to hallucinate arguments or the tool call to fail completely.
- **Solution:** Align the prompt schemas in `VALID_TOOL_NAMES` and the native function signature in `ZepToolsService`. Ensure `_execute_tool` explicitly handles the `faction_brief` invocation accurately.

### [P2] Monolithic `SimulationRunner` Module Size
- **Evidence:** `ARCHITECTURE.md` mentions `SimulationRunner` is "~1700 lines".
- **Impact:** Extremely difficult to maintain, test, and debug. High cognitive load for developers modifying simulation orchestrations.
- **Solution:** Refactor `SimulationRunner` into separate classes for `ProcessLifecycle`, `ActionLogParser`, and `TelemetryAggregator`.

### [P2] Implicit Social Opinion / China Timezone Defaults
- **Evidence:** `ARCHITECTURE.md` mentions "peak/off-peak hour buckets and activity multipliers tuned to a Chinese daily rhythm."
- **Impact:** When researching non-Chinese contexts (e.g., US elections, European policies), the simulation dynamics will unrealistically map to Beijing timezone behaviors.
- **Solution:** Dynamically adjust the peak/off-peak hour buckets based on the geographic context derived from `actors.json` or the initial user prompt.

### [P2] Partial Report Status Ambiguity & Outline Fallback
- **Evidence:** `handoff.md` mentions "report-outline fallback returning only three sections despite a 5-8 section contract".
- **Impact:** The report layout becomes stunted if the LLM plan parser fails. If a single section fails, the placeholder renders, but the run still reports `completed`, masking partial failures.
- **Solution:** Provide a structured warning in the telemetry/status API indicating `sections_failed`. Adjust the outline prompt to enforce a strict minimum section count or use a JSON-schema constrained generation mode for planning.

### [P3] Legacy Zep Naming
- **Evidence:** Files and classes (`ZepEntityReader`, `ZepToolsService`, `ZepGraphMemoryUpdater`) still carry "Zep" names despite migrating entirely to Graphiti.
- **Impact:** Developer confusion; false assumption of external network calls.
- **Solution:** Rename modules and classes to `GraphEntityReader`, `GraphToolsService`, etc., to reflect the localized architecture.

---

## 11. Refinements and Enhancements

- **Performance:** Streamline ReportAgent ReAct iterations. For providers supporting native tools, completely bypass the ReAct prompt loops to cut latency and token usage by ~40%.
- **UX/Product:** Implement deep-linking directly to specific artifacts via UI tabs. Add an explicitly modeled "Simulation Fast-Forward" visualizer summarizing actions over time rather than a raw feed.
- **Observability:** Centralize logs using the newly implemented telemetry system (`utils/telemetry.py`), providing an aggregate token/cost dashboard across the 6 pipeline stages in the UI settings panel.

---

## 12. Proposed Implementation Roadmap

1. **Immediate Bug Fixes (Next 1-2 weeks):**
   - Fix `faction_brief` tool schema drift in `ReportAgent`.
   - Update `SimulationRunner` timezone constants to use dynamic offsets.
   - Patch outline generation prompts to ensure minimum section adherence.
2. **Contract & State Hardening (Weeks 2-4):**
   - Implement single-source-of-truth state tracking (consolidate pipeline state, simulation state, and run state).
   - Address Graphiti parallel ingest concurrency locks.
3. **Product Alignment (Weeks 4-6):**
   - Rename Zep legacy namespaces.
   - Fully implement native tool calling API integration for all supported APIs to accelerate Report generation.
4. **Longer-term Architecture (Weeks 6+):**
   - Break down `SimulationRunner` into micro-services.
   - Enable mid-run OASIS pause/resume.

---

## 13. Suggested Quality Gates

To ensure regressions are not introduced, the following validation commands must be executed based on the scope of changes:

- **Documentation-only changes:** 
  `git diff --check` and `npx prettier --check "**/*.md"`
- **Backend changes:** 
  `npm run lint` (ruff) and `npm run test` (pytest). Ensure backend API runs locally.
- **Frontend changes:** 
  `npm run build` and UI component smoke tests.
- **Full-pipeline LLM-costly Validation:**
  Must be executed sparingly. Use `scripts/smoke.sh` for offline verification. When live validation is necessary, use `mode=research_only` with `depth=quick` to limit token spend.

---

## 14. Final Assessment

DeepAgentForecast achieves a highly ambitious goal by effectively synthesizing complex, disparate AI toolchains into a resilient pipeline. Its isolation of the DeerFlow and CAMEL-AI environments via a robust file-based handoff contract is an excellent architectural decision. The migration to a local Graphiti instance further stabilizes the application. The primary technical debt lies in the monolithic nature of the `SimulationRunner` and lingering state fragmentation. Addressing the highlighted tool registry drift and graph deduplication risks will solidify the platform's reliability for production-grade predictive simulations.
