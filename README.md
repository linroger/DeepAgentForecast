# DeepResearchForecast

**English** | [简体中文](README.zh-CN.md)

> **Type a single question. Get an interactive forecast.**
> DeepResearchForecast auto-researches the web, builds a high-fidelity parallel world, runs a multi-agent population simulation, and produces an interactive forecast report — all from one prompt.

DeepResearchForecast is an autonomous **"one prompt → forecast"** engine. You give it a question about the future; it researches the open web, distills what it learns into a temporal knowledge graph, populates a simulated society of LLM-driven personas, runs that society forward in time, and then synthesizes everything into a sectioned, evidence-grounded forecast report you can read and explore in your browser. The whole journey — research, graph, simulation, report — happens behind a single combined dashboard with a live, stage-by-stage view of the work in progress.

---

## Demo

🔗 **[Live demo site](https://linroger.github.io/DeepResearchForecast/)** (English + 中文) — walk through **every stage** of real end-to-end runs: the deep-research console log, the research dossier with actors & sources, the generated ontology, an interactive knowledge graph, the simulated Twitter/Reddit forum, and the final forecast (US AI race 2030, global EV industry 2035, Russia–Ukraine endgame).

One prompt — *"Who wins the US AI race by 2030?"* — taken from question to interactive forecast (research → knowledge graph → 40-round population simulation → report):

![Demo: one prompt to forecast](docs/media/demo-preview.gif)

▶ **[Watch the full demo video (47s, MP4)](docs/media/demo.mp4)**

### Screenshots

| | |
|---|---|
| ![Knowledge graph built from the research dossier](docs/media/01-pipeline-knowledge-graph.jpg) <br/>*Stage 4 — the temporal knowledge graph built from the research dossier* | ![Research dossier with cited sources](docs/media/02-research-dossier-sources.jpg) <br/>*The research dossier tab — every claim grounded in cited web sources* |
| ![Generated agent personas](docs/media/03-agent-personas.jpg) <br/>*Digital personas generated for each real-world actor (researched stance & influence)* | ![Live simulation console](docs/media/04-simulation-console.jpg) <br/>*The live simulation console streaming agent actions in real time* |
| ![Graph node details](docs/media/05-graph-node-details.jpg) <br/>*Inspecting a graph entity mid-simulation* | ![Simulated social feed](docs/media/06-simulation-feed.jpg) <br/>*The simulated Twitter/Reddit feed at round 20/40* |
| ![Simulated posts](docs/media/07-simulation-posts.jpg) <br/>*Emergent discussion threads between agent personas* | ![Agent detail panel](docs/media/08-simulation-agent-detail.jpg) <br/>*Post & agent detail panel at round 33/40 (88% through the pipeline)* |

---

## What it does

Given one natural-language prediction question (for example, *"Will product X reach mainstream adoption within 18 months?"*), DeepResearchForecast:

1. **Researches the web autonomously** — a deep-research super-agent performs multi-angle web search and full-text fetching, then writes a structured research dossier with the actors, sources, and the precise prediction requirement.
2. **Builds a parallel world** — the dossier is converted into a temporal knowledge graph, and one digital persona is generated per key entity in that graph.
3. **Simulates a population** — hundreds of LLM personas interact across a simulated Twitter and Reddit for a configurable number of rounds, producing emergent social dynamics around the question.
4. **Forecasts and reports** — a report agent retrieves from both the knowledge graph and the simulation and writes an interactive, sectioned forecast report.

Everything is observable in real time: a live research console, the rendered dossier, the knowledge graph, the simulated social feed, and the final forecast all live in one dashboard.

---

## Architecture overview

DeepResearchForecast chains **two engines** through a **shared temporal knowledge graph** and a **report agent**, all surfaced by a single Vue frontend.

| Component | Role |
|---|---|
| **DeerFlow** | A LangGraph-based deep-research "super agent": web search + full-text fetch, multi-angle research, writes a structured research dossier. Runs in its **own subprocess and isolated Python venv**. |
| **MiroFish / OASIS** | A multi-agent social-simulation engine (built on CAMEL-AI's OASIS). Spins up hundreds of LLM personas interacting on a simulated **Twitter + Reddit**. |
| **Zep Cloud** | A **temporal knowledge graph (GraphRAG)** that glues the two engines together. The dossier is ingested here; entities and relations are extracted server-side. |
| **ReportAgent** | A **ReAct loop** with an `insight_forge` tool that performs tool-augmented retrieval over the graph **and** the simulation, then writes the final forecast report. |
| **Frontend** | A Vue 3 + Vite single combined dashboard with a sticky 6-stage timeline and tabs for each phase. Bilingual (English + 中文). |

### The 6-stage pipeline

A **pipeline** is one prompt run. Each run flows through six stages:

```
            ┌────────────────────────────────────────────────────────────────────────┐
  one       │                          DeepResearchForecast                            │
 prompt ───▶│                                                                          │
            │  ┌───────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ ┌─────┐ ┌────────┐   │
            │  │ 1 RESEARCH│▶│2 ONTOLOGY│▶│3 GRAPH │▶│4 PREPARE│▶│5 RUN│▶│6 REPORT│   │
            │  └─────┬─────┘ └────┬─────┘ └───┬────┘ └────┬────┘ └──┬──┘ └───┬────┘   │
            │   DeerFlow      LLM derives   Zep KG     personas   OASIS    ReportAgent │
            │   (subprocess)  entity/edge   ingest +   + sim cfg  dual-    (ReAct +    │
            │   → dossier     types         extract    (env agent)platform insight_forge)│
            │        │            │            │           │         │          │       │
            │        └─ Zep Cloud temporal knowledge graph (GraphRAG) shared throughout ┘ │
            └────────────────────────────────────────────────────────────────────────┘
                                                                                  │
                                                                                  ▼
                                                                       interactive forecast
```

```mermaid
flowchart LR
    P([One prompt]) --> R

    subgraph DeerFlow["DeerFlow (subprocess + isolated venv)"]
        R["1. research<br/>web search + fetch<br/>writes handoff contract"]
    end

    R --> O["2. ontology<br/>LLM derives entity<br/>+ edge types"]
    O --> G["3. graph<br/>chunk + ingest into<br/>Zep temporal KG"]
    G --> PR["4. prepare<br/>digital personas +<br/>simulation config"]

    subgraph OASIS["MiroFish / OASIS"]
        RUN["5. run<br/>dual-platform<br/>Twitter + Reddit<br/>multi-agent sim, N rounds"]
    end

    PR --> RUN
    RUN --> REP["6. report<br/>ReportAgent (ReAct +<br/>insight_forge) over<br/>graph + simulation"]
    REP --> F([Interactive forecast report])

    Zep[("Zep Cloud<br/>temporal KG (GraphRAG)")]
    G -.-> Zep
    PR -.-> Zep
    REP -.-> Zep
```

**Stage by stage:**

1. **research** — DeerFlow runs in its own subprocess (its own Python venv), researches the web, and writes a **file-based handoff contract**:
   - `research_report.md` — the structured research dossier
   - `actors.json` — the key actors
   - `sources.json` — the cited sources
   - `prediction_requirement.txt` — the precise prediction question
   - `meta.json` — run metadata
   - `research_progress.log` — the live research console log
2. **ontology** — an LLM derives **entity types + edge types** from the dossier and the prediction question.
3. **graph** — the dossier is **chunked and ingested** into a Zep temporal knowledge graph (GraphRAG); entities and relations are extracted server-side.
4. **prepare** — **digital personas** (one per key graph entity) and the **simulation config** are generated; an "environment agent" sets the rounds and timing.
5. **run** — OASIS runs a **dual-platform (Twitter + Reddit)** multi-agent simulation for *N* rounds; personas post, comment, and like, and social dynamics emerge.
6. **report** — the **ReportAgent** (a ReAct loop with the `insight_forge` tool) retrieves from the graph + simulation and writes a **sectioned forecast report**.

---

## Features

- **One prompt → full forecast.** A single question drives the entire research → simulation → report pipeline end to end.
- **Autonomous deep research.** Multi-angle web search and full-text fetch, distilled into a structured dossier with actors and sources. At `deep` depth, DeerFlow runs a staged multi-pass protocol: source mapping, primary-evidence sweep, actor/incentive analysis, contradiction/risk testing, forecast-input synthesis, then final long-form synthesis.
- **Research-grounded personas.** The structured actor dossier (`actors.json`: role, stance, influence, memory per real-world actor) seeds the ontology, **the agent personas, the per-agent stance/influence config, and the simulation's initial posts** — agents start from researched facts, not LLM guesses.
- **Temporal knowledge graph (GraphRAG).** The research is ingested into Zep Cloud, where entities and relations are extracted and made queryable.
- **Multi-agent population simulation.** Hundreds of LLM personas interact on a simulated Twitter + Reddit; emergent dynamics inform the forecast.
- **Tool-augmented forecast synthesis.** A ReAct ReportAgent retrieves across both the graph and the simulation before writing.
- **Single combined dashboard.** Live log, dossier, knowledge graph, simulation feed, and forecast — all in one view with a sticky 6-stage timeline.
- **Runtime-switchable LLM providers.** Switch between local CLIs and hosted APIs from the Settings menu; the switch applies to new runs.
- **Cancellable runs.** A running pipeline can be aborted from the UI at any stage — the research subprocess group is killed and the OASIS simulation is stopped, so a cancelled run stops burning quota immediately.
- **Resumable runs.** A failed or cancelled pipeline can be resumed in place (**Resume** button, or `POST /api/research/<id>/resume`). Completed stages are reused — an already-written research dossier, ontology, knowledge graph, or finished simulation is never paid for twice; the pipeline restarts from the stage that broke.
- **Fail-fast preflight.** `npm run doctor` checks the whole environment in seconds, and `POST /research/run` validates keys/credentials/checkout before any spend.
- **Bilingual UI.** English + 中文, toggled from the Settings menu.
- **Run history.** A drawer lists past pipeline runs for quick review.
- **Resilient by design.** Error guards, a tool-free synthesis net, depth-aware research watchdog with report salvage, per-section graceful degradation, atomic state writes, and orphan reconciliation (including stranded research processes) across restarts.

---

## Requirements

| Requirement | Notes |
|---|---|
| **Node.js ≥ 20.19** | For the frontend (Vue 3 + Vite 7). |
| **Python 3.11 – 3.12** | For the backend — the `camel-ai`/`camel-oasis` simulation stack targets ≤ 3.12, so the venv is **pinned to 3.12** (`backend/.python-version` + `uv sync --python 3.12`). A 3.13/3.14 default interpreter would break the install. |
| **Python ≥ 3.12 (3.13 recommended)** | DeerFlow's deep-research engine runs in its **own, separate venv**. |
| **uv** | The Python package manager used for both venvs. Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git** | Required — `setup.sh` uses it to auto-download the DeerFlow research engine. |
| **Zep Cloud API key** | **Always required** (the free tier works). Get one at <https://app.getzep.com/>. |
| **An LLM** | By default the local `claude` or `codex` CLI (no API key). The OpenAI-compatible API providers (`openai`, `kimi`, `minimax`, `deepseek`, `qwen`, `glm`) need `LLM_API_KEY`. |

---

## Getting started

Three steps: **install → configure → run**. DeerFlow lives in a **sibling directory** named `deer-flow` so its LangChain/LangGraph dependencies stay isolated from the backend; it runs in its own venv at `../deer-flow/backend/.venv`.

### 1. Install

**Option A — `setup.sh` (recommended).** One script automates the entire installation:

```bash
./setup.sh
```

It checks prerequisites, scaffolds `.env` from `.env.example`, **auto-detects your model provider** (`claude` CLI → `claude-cli`, `codex` CLI → `codex-cli` + research on `codex`), prompts for your Zep key, installs the root + frontend npm deps, builds the backend venv (**pinned to Python 3.12**), then **downloads DeerFlow automatically**: it clones the sibling `../deer-flow` repo (from <https://github.com/bytedance/deer-flow>, pinned to a known-good commit) if absent, applies the **bridge overlay** from `deerflow_bridge/` (the `deerflow_research.py` driver, the `patches/models/*.py` patches, and `config.yaml`), and builds DeerFlow's isolated venv (Python 3.13). Re-running it is idempotent and safe.

Override the defaults via env vars if needed: `DEERFLOW_DIR` (location), `DEERFLOW_REPO` (clone URL), `DEERFLOW_REF` (pinned commit; set `=main` to track HEAD). These are read by `setup.sh` from the shell environment (they are not `.env` keys), e.g. `DEERFLOW_REF=main ./setup.sh`.

**Option B — manual setup** (the equivalent steps by hand):

```bash
# 1. Install Node deps (root + frontend) and the backend venv (Python 3.12 pinned)
npm run setup:all

# 2. Download the DeerFlow research engine (git required) as a SIBLING directory
git clone https://github.com/bytedance/deer-flow ../deer-flow

# 3. Apply the bridge overlay from deerflow_bridge/
cp deerflow_bridge/deerflow_research.py ../deer-flow/deerflow_research.py
cp deerflow_bridge/patches/models/*.py  ../deer-flow/backend/packages/harness/deerflow/models/
cp deerflow_bridge/config.yaml          ../deer-flow/config.yaml   # only if absent

# 4. Build DeerFlow's isolated research venv (Python ≥ 3.12; 3.13 recommended)
UV_PROJECT_ENVIRONMENT=../deer-flow/backend/.venv \
  uv sync --project ../deer-flow/backend --python 3.13
```

### 2. Configure

Set at minimum your **Zep key** (and an API key if you picked a hosted provider) in `.env` — see the [Configuration](#configuration-env) reference below. If you use the `claude` CLI, just make sure you are logged in (run `claude` once).

Then verify everything is wired up — **the doctor checks your whole environment in seconds** (tool versions, both venvs, the DeerFlow overlay, credentials for the providers you selected):

```bash
npm run doctor
```

Fix any ✗ items it reports and re-run until it prints `All checks passed`.

### 3. Run

```bash
npm run dev        # backend on :5001 + frontend on :3000
```

Open **<http://localhost:3000/research>**, type your question, and click **Run research + simulate + forecast**. The backend pre-flights your configuration at launch time — misconfiguration is reported in seconds, not after a 40-minute research run.

| Service | URL |
|---|---|
| Frontend (Vue 3 + Vite) | <http://localhost:3000> (proxies `/api` → `5001`) |
| Backend (Flask) | <http://localhost:5001> |

---

## Model providers

The LLM provider is **switchable at runtime** via the Settings menu (it can also be set in `.env`, or via the `/api/settings/llm` endpoint). A provider switch applies to **new runs**.

The **report + simulation** stages are driven by `LLM_PROVIDER` (8 values):

| Provider | Description | API key? |
|---|---|---|
| **`claude-cli`** *(default)* | Uses the local `claude` CLI / Claude Code subscription. | No key |
| **`codex-cli`** | Uses the local `codex` CLI / Codex (ChatGPT) subscription. | No key |
| **`openai`** | Any OpenAI-compatible API. | Needs `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` |
| **`kimi`** | Kimi-for-coding (`api.kimi.com/coding`; OpenAI-compatible + coding-agent User-Agent gateway). | Needs `LLM_API_KEY` |
| **`minimax`** | MiniMax-M3 (`https://api.minimaxi.com/v1`; a reasoning model). | Needs `LLM_API_KEY` |
| **`deepseek`** | DeepSeek (`https://api.deepseek.com/v1`, default model `deepseek-chat`; the **research** stage uses the flagship `deepseek-v4-pro`, million-token context). | Needs `LLM_API_KEY` |
| **`qwen`** | Qwen (`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`, default model `qwen-plus`; the **research** stage uses the flagship `qwen3.7-max`, million-token context). | Needs `LLM_API_KEY` |
| **`glm`** | GLM-4.6 (`https://api.z.ai/api/paas/v4`, model `glm-4.6`). | Needs `LLM_API_KEY` |

`openai`, `kimi`, `minimax`, `deepseek`, `qwen`, and `glm` are all OpenAI-compatible API providers needing `LLM_API_KEY`; `kimi`, `minimax`, `deepseek`, `qwen`, and `glm` ship sensible default `LLM_BASE_URL` / `LLM_MODEL_NAME`, so you only set `LLM_API_KEY`.

The **deep-research** stage is driven separately by `DEERFLOW_MODEL` (7 options):

| Research model | Notes |
|---|---|
| **`claude`** *(default)* | Uses Claude Code OAuth — no API key. `openai` maps to this stanza. |
| **`codex`** | Codex (ChatGPT) OAuth — no API key. Auto-selected when only the `codex` CLI is installed. |
| **`kimi`** | Kimi-for-coding. Needs `KIMI_API_KEY`. Auto-mirrored when you switch to the kimi provider in Settings. |
| **`minimax`** | Needs `MINIMAX_API_KEY`. |
| **`deepseek`** | Needs `DEEPSEEK_API_KEY`. |
| **`qwen`** | Needs `DASHSCOPE_API_KEY`. |
| **`glm`** | Needs `ZHIPUAI_API_KEY`. |

> **Note on the research stage:** the research stage is configured independently from `LLM_PROVIDER` via `DEERFLOW_MODEL`. Its per-provider key is mirrored for deer-flow (e.g. `KIMI_API_KEY`, `MINIMAX_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ZHIPUAI_API_KEY`) and is only needed when you actually run `DEERFLOW_MODEL` on that provider. The default `claude` uses Claude Code OAuth (no API key). Both `POST /api/research/run` and `deerflow_research.py` pre-flight the selected model's credentials and fail fast with an actionable message.

### How to switch

- **Settings menu** — open the Settings menu in the frontend and pick a provider (and, if needed, supply key / base URL / model). This is the easiest path.
- **`.env`** — set `LLM_PROVIDER` (and provider credentials) before starting. See the reference below.
- **API** — `POST /api/settings/llm` with `{provider, api_key?, base_url?, model?}` to switch at runtime. Read the current setting with `GET /api/settings/llm`.

---

## Configuration (`.env`)

Create a `.env` file at the project root (`setup.sh` scaffolds it from `.env.example`).

```bash
LLM_PROVIDER=claude-cli      # claude-cli | codex-cli | openai | kimi | minimax | deepseek | qwen | glm
ZEP_API_KEY=...              # required (free tier works)

# openai / kimi / minimax / deepseek / qwen / glm only:
LLM_API_KEY=...
LLM_BASE_URL=...             # kimi/minimax/deepseek/qwen/glm ship a sensible default
LLM_MODEL_NAME=...           # kimi/minimax/deepseek/qwen/glm ship a sensible default

# DeerFlow deep research (optional — all have sensible defaults):
DEERFLOW_DIR=...                 # path to the deer-flow sibling directory
DEERFLOW_PYTHON=...              # python interpreter for DeerFlow's venv (auto-detected)
DEERFLOW_MODEL=...               # claude | minimax | deepseek | qwen | glm | codex | kimi
DEERFLOW_RESEARCH_DEPTH=...      # quick | standard | deep
DEERFLOW_RESEARCH_LANGUAGE=...   # research output language (default Chinese)
DEERFLOW_RESEARCH_TIMEOUT=...    # research watchdog override; unset = depth-aware
                                 #   (quick 900s / standard 2400s / deep 10800s)

# DeerFlow per-provider keys (only when DEERFLOW_MODEL runs on that provider):
KIMI_API_KEY=...                 # DEERFLOW_MODEL=kimi
MINIMAX_API_KEY=...              # DEERFLOW_MODEL=minimax
DEEPSEEK_API_KEY=...             # DEERFLOW_MODEL=deepseek
DASHSCOPE_API_KEY=...            # DEERFLOW_MODEL=qwen
ZHIPUAI_API_KEY=...              # DEERFLOW_MODEL=glm

# Tuning (optional):
OASIS_SEMAPHORE=30               # concurrent LLM calls for API providers during simulation
OASIS_CLI_SEMAPHORE=3            # concurrent LLM calls for CLI providers
ZEP_MAX_RETRIES=4                 # Zep 429 / transient retry budget
ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS=90
FLASK_DEBUG=false                # dev only: exposes the Werkzeug debugger + reloader
```

> `DEERFLOW_REPO` / `DEERFLOW_REF` are **shell** env vars read by `setup.sh` at install time (e.g. `DEERFLOW_REF=main ./setup.sh`), not `.env` keys.

| Variable | Required | Purpose |
|---|---|---|
| `LLM_PROVIDER` | Yes | Selects the active provider. One of `claude-cli`, `codex-cli`, `openai`, `kimi`, `minimax`, `deepseek`, `qwen`, `glm`. |
| `ZEP_API_KEY` | **Always** | Zep Cloud API key for the temporal knowledge graph. |
| `LLM_API_KEY` | openai/kimi/minimax/deepseek/qwen/glm | API key for the hosted provider. |
| `LLM_BASE_URL` | openai/kimi/minimax/deepseek/qwen/glm | Base URL for the OpenAI-compatible endpoint (kimi/minimax/deepseek/qwen/glm default it). |
| `LLM_MODEL_NAME` | openai/kimi/minimax/deepseek/qwen/glm | Model name to request (kimi/minimax/deepseek/qwen/glm default it). |
| `DEERFLOW_DIR` | No | Location of the `deer-flow` sibling directory. |
| `DEERFLOW_PYTHON` | No | Python interpreter for DeerFlow's isolated venv (auto-detects `../deer-flow/backend/.venv`). |
| `DEERFLOW_MODEL` | No | Research model: `claude`, `minimax`, `deepseek`, `qwen`, `glm`, `codex`, or `kimi`. |
| `KIMI_API_KEY` | DEERFLOW_MODEL=kimi | DeerFlow research key when running Kimi-for-coding. |
| `MINIMAX_API_KEY` | DEERFLOW_MODEL=minimax | DeerFlow research key when running MiniMax. |
| `DEEPSEEK_API_KEY` | DEERFLOW_MODEL=deepseek | DeerFlow research key when running DeepSeek. |
| `DASHSCOPE_API_KEY` | DEERFLOW_MODEL=qwen | DeerFlow research key when running Qwen. |
| `ZHIPUAI_API_KEY` | DEERFLOW_MODEL=glm | DeerFlow research key when running GLM. |
| `DEERFLOW_RESEARCH_DEPTH` | No | Depth of the research stage: `quick` / `standard` / `deep`. `deep` runs multiple scoped research passes before final synthesis. |
| `DEERFLOW_RESEARCH_LANGUAGE` | No | Language of the research output. |
| `DEERFLOW_RESEARCH_TIMEOUT` | No | Research watchdog override (seconds). Unset = depth-aware: quick 900 / standard 2400 / deep 10800. If the report was already written when the watchdog fires, the run is salvaged instead of discarded. |
| `OASIS_SEMAPHORE` / `OASIS_CLI_SEMAPHORE` | No | Concurrent LLM-call cap during simulation (API providers / CLI providers). In dual-platform parallel runs each platform gets half, so the cap is the true global in-flight limit. |
| `ZEP_MAX_RETRIES` / `ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS` | No | Zep retry budget and maximum wait when the free tier returns 429 with `Retry-After`. Defaults are `4` and `90`. |
| `LLM_CLI_USE_API_KEY` | No | `claude-cli` strips a stray `ANTHROPIC_API_KEY` from the subprocess env by default (it would silently switch billing from your subscription to the API). Set `true` to keep it. |
| `FLASK_DEBUG` | No | Dev only (default `false`): enables the Werkzeug debugger + auto-reloader (the reloader kills in-flight pipelines). |

---

## API surface

The backend is a **Flask** app at `http://localhost:5001`. All endpoints are under `/api`.

### Research

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/research/run` | Start a pipeline. Body: `{prompt, mode(full\|research_only), depth(quick\|standard\|deep), max_rounds?, project_name?}` → `{pipeline_id}`. Pre-flights the whole configuration (Zep key, provider credentials, DeerFlow checkout) and returns an actionable `400` instead of failing mid-run. |
| `POST` | `/research/<id>/cancel` | **Cancel a running pipeline** — kills the research subprocess group / stops the OASIS simulation; other stages exit at the next checkpoint. |
| `POST` | `/research/<id>/resume` | **Resume a failed/cancelled pipeline** — reuses completed stages (research dossier, ontology, graph, finished simulation) and restarts from the stage that failed. Pre-flights the configuration first. |
| `DELETE` | `/research/<id>` | **Delete a finished run record** (including its handoff artifacts). Running pipelines must be cancelled first (`409`). |
| `POST` | `/research/clean` | **Bulk-delete failed/cancelled runs.** Body: `{statuses?: ["failed","cancelled"]}`; `running`/`completed` are never touched. |
| `GET` | `/research/status/<id>` | Current status of a pipeline run (terminal states: `completed` / `failed` / `cancelled`). |
| `GET` | `/research/list` | List pipeline runs. |
| `GET` | `/research/<id>/dossier` | The rendered research dossier. |
| `GET` | `/research/<id>/progress` | Live research progress (DeerFlow console). |

### Graph

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/graph/data/<graph_id>` | Knowledge graph data → `{nodes, edges}`. |

### Simulation

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/simulation/<sim_id>/profiles/realtime?platform=` | Real-time persona profiles (filterable by platform). |
| `GET` | `/simulation/<sim_id>/posts?platform=` | Simulated posts (filterable by platform). |
| `GET` | `/simulation/<sim_id>/agent-stats` | Aggregate agent statistics. |

### Report

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/report/<report_id>` | The forecast report → `{markdown_content, ...}`. |

### Settings

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/settings/llm` | Current LLM provider settings. |
| `POST` | `/settings/llm` | Switch provider at runtime. Body: `{provider, api_key?, base_url?, model?}`. Applies to **new runs**. |

---

## The combined frontend dashboard

The frontend is **Vue 3 + Vite** at `http://localhost:3000` (it proxies `/api` to the backend on `5001`). The main view is **`/research`** — a single combined dashboard containing:

- A **prompt input** with run parameters.
- A **sticky 6-stage timeline** tracking research → ontology → graph → prepare → run → report.
- A **run-history drawer** for past runs.
- A **Settings menu** (model provider + EN/中文 language toggle).

The dashboard's tabs:

| Tab | What it shows |
|---|---|
| **Live log** | The DeerFlow research console, streaming in real time. |
| **Dossier** | The rendered research dossier (markdown) plus actor cards and sources. |
| **Knowledge graph** | The temporal knowledge graph rendered as a d3 force graph. |
| **Simulation** | Personas plus the simulated Twitter/Reddit feed and live stats. |
| **Forecast** | The rendered, sectioned forecast report. |

The entire UI is **bilingual** (English + 中文).

---

## Notable engineering

- **File-based handoff contract over a subprocess.** The DeerFlow ↔ backend bridge is a file-based handoff contract executed in a subprocess, keeping DeerFlow's LangChain/LangGraph dependencies fully isolated from the backend.
- **Structured actor seeding end-to-end.** DeerFlow's `actors.json` (role / stance / influence / memory per researched actor) flows through every downstream stage: it biases the ontology, is matched by name to graph entities to ground each persona's stance and memory, drives per-agent `stance` / `influence_weight` in the simulation config, and lets initial posts be authored *by the actual researched actor* (`poster_name` matching) instead of a type-matched stand-in.
- **Research error guard.** An error guard prevents an LLM-error or degraded message from being mistaken for a real research report — it fails fast, so no contamination flows downstream.
- **Tool-free "synthesis net".** If the research agent exhausts its step budget on tool calls before writing, or hits a provider **structural** error on the final write, the report is synthesized directly from the gathered (checkpointed) research via a clean single-turn call.
- **Per-section graceful degradation.** In the ReportAgent, a single section's LLM error becomes a placeholder while the rest of the report still produces a partial result.
- **Robust state management.** Atomic state writes, process-group cleanup, and orphan reconciliation across restarts keep runs consistent.

---

## Project layout

```
DeepResearchForecast/
├── backend/                 # Flask API (port 5001) — pipeline orchestration,
│   │                        #   graph ingest, simulation, ReportAgent. uv-managed,
│   └── .python-version      #   pinned to Python 3.12 (camel-ai stack targets ≤3.12).
├── frontend/                # Vue 3 + Vite dashboard (port 3000), bilingual EN/中文.
├── deerflow_bridge/         # Bridge overlay applied onto the cloned deer-flow:
│   ├── deerflow_research.py #   research driver / entry point (→ ../deer-flow/ root).
│   ├── patches/models/      #   provider patches (claude OAuth fix, Keychain loader,
│   │                        #     patched_minimax → MiniMax "name" fix).
│   └── config.yaml          #   deer-flow model config (copied only if absent).
├── Screenshots -> docs/media/ # README screenshots + demo video/GIF.
├── docs/media/              # Canonical optimized media assets.
├── scripts/doctor.sh        # `npm run doctor` — environment health check.
├── setup.sh                 # Quick-start: downloads deer-flow, installs everything,
│                            #   applies the bridge overlay, auto-detects provider.
├── .env                     # LLM_PROVIDER, ZEP_API_KEY, provider + DeerFlow config.
└── package.json             # `setup:all`, `doctor` and `dev` scripts.

../deer-flow/                # SIBLING engine: LangGraph deep-research super agent
└── backend/.venv/           #   (auto-downloaded by setup.sh; isolated Python ≥3.12 venv).
```

> DeerFlow is a **sibling directory** to keep its dependency tree isolated from the backend. `setup.sh` clones it (git required), applies the `deerflow_bridge/` overlay (driver + `patches/models` + `config.yaml`), and builds its venv with:
> `UV_PROJECT_ENVIRONMENT=../deer-flow/backend/.venv uv sync --project ../deer-flow/backend --python 3.13`

---

## Troubleshooting

**First move: run `npm run doctor`.** It diagnoses the most common problems (missing venvs, wrong Python, missing overlay, placeholder keys, missing CLI logins) with the exact fix command for each.

| Symptom | Likely cause / fix |
|---|---|
| **`POST /research/run` returns a preflight error list** | That's the fail-fast check working — each bullet names the missing piece (Zep key, provider key, CLI login, DeerFlow checkout) and how to fix it. Nothing was spent. |
| **Missing / invalid / placeholder `ZEP_API_KEY`** | The Zep Cloud key is **always required** (graph stage), and the `.env.example` placeholder is rejected. Set a real `ZEP_API_KEY` in `.env`; the free tier works: <https://app.getzep.com/>. |
| **Report stage fails with `status_code: 429` / `Rate limit exceeded for FREE plan`** | Zep free-tier throttling. The app now parses `Retry-After`, waits before retrying, and reuses report graph snapshots to reduce duplicate node/edge reads. For very large runs, lower concurrency/depth or raise the retry knobs in `.env`. |
| **Backend install fails (camel-ai / tiktoken build errors)** | Your default Python is 3.13+. The backend venv must be on **3.11–3.12**: `( cd backend && uv sync --python 3.12 )` — `setup.sh` and `backend/.python-version` already pin this. |
| **Research stage runs on Claude even though I picked another provider** | The research stage is configured separately via `DEERFLOW_MODEL` (`claude` *(default)*, `minimax`, `deepseek`, `qwen`, `glm`, `codex`, `kimi`), not by `LLM_PROVIDER`. Only `openai` maps to the `claude` stanza. Set `DEERFLOW_MODEL` (and its key, e.g. `MINIMAX_API_KEY`) to run research on a different model. |
| **DeerFlow / research stage fails to start** | `setup.sh` clones the `deer-flow` sibling (git required) and applies the `deerflow_bridge/` overlay. Ensure its venv is built with Python ≥ 3.12 (3.13 recommended): `UV_PROJECT_ENVIRONMENT=../deer-flow/backend/.venv uv sync --project ../deer-flow/backend --python 3.13`. Re-running `setup.sh` is idempotent. Optionally set `DEERFLOW_DIR` / `DEERFLOW_PYTHON` (or `DEERFLOW_REPO` / `DEERFLOW_REF` for `setup.sh`). |
| **No API key but hosted provider selected** | `openai`, `kimi`, `minimax`, `deepseek`, `qwen`, and `glm` need `LLM_API_KEY` (with `LLM_BASE_URL` / `LLM_MODEL_NAME`; `kimi`/`minimax`/`deepseek`/`qwen`/`glm` default those). For no-key operation use `claude-cli` or `codex-cli`. |
| **`claude-cli` returns 401 / bills the API instead of my subscription** | A stray `ANTHROPIC_API_KEY` in your environment. It is stripped from the CLI subprocess automatically; run `claude` once to refresh the OAuth login. (Set `LLM_CLI_USE_API_KEY=true` if you *want* API-key billing.) |
| **Provider switch didn't take effect** | The runtime switch applies to **new runs** only. Start a fresh pipeline after switching. |
| **Frontend can't reach the API** | The frontend proxies `/api` → `5001`. Confirm the backend is running on port 5001 (`npm run dev` starts both). The UI shows a "Lost connection" banner if the backend stops responding mid-run. |
| **Research stage times out** | The watchdog is depth-aware (quick 900s / standard 2400s / deep 10800s). Deep mode intentionally runs multiple research passes, so it is slower; override with `DEERFLOW_RESEARCH_TIMEOUT` or reduce research `depth`. If the report was already written when the watchdog fired, the run salvages it and continues. |
| **Deep research logs `[FORCED STOP] Tool web_search called N times` from pass 2 onward** | Upstream DeerFlow accumulates per-tool call counts across all turns of a thread, starving later research passes. Re-run `./setup.sh` to apply the bridge middleware patch (per-run counter resets) and pick up the research-grade `web_search`/`web_fetch` limits in `deerflow_bridge/config.yaml` (if you keep your own `deer-flow/config.yaml`, merge the `loop_detection.tool_freq_overrides` stanza by hand). |
| **Need to stop a long run** | Click **Cancel** in the run header (or `POST /api/research/<id>/cancel`). Research/simulation subprocesses are terminated immediately. |
| **A run failed (or was cancelled) midway** | Click **Resume** in the run header (or `POST /api/research/<id>/resume`). Completed stages are reused — a finished research dossier, graph, or simulation is never re-run — and the pipeline restarts from the stage that broke. This also covers runs interrupted by a backend restart. |
| **A report section shows a placeholder** | Per-section graceful degradation: one section's LLM error becomes a placeholder while the rest of the report is still produced. Re-run if needed. |

---

## Acknowledgments

- **[OASIS](https://github.com/camel-ai/oasis)** (CAMEL-AI) powers the multi-agent social simulation engine — sincere thanks to the CAMEL-AI team for their open-source work.
- **[DeerFlow](https://github.com/bytedance/deer-flow)** (ByteDance) powers the deep-research stage.
- **[Zep Cloud](https://www.getzep.com/)** provides the temporal knowledge graph (GraphRAG).
- Built on **[MiroFish](https://github.com/666ghj/MiroFish)**, the original population-simulation prediction engine.

## License

[AGPL-3.0](LICENSE)
