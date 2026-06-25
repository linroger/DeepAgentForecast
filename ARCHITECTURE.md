# MiroFish — Architecture & How It Works

> *A Simple and Universal Swarm Intelligence Engine, Predicting Anything*
> 简洁通用的群体智能引擎，预测万物

> **⚠️ Scope of this document.** This page documents the **simulation-engine
> internals** (MiroFish/OASIS) — the graph → personas → simulation → report core
> that runs *after* research. It predates the one-prompt workflow and frames the
> system around the older simulation-first model (the legacy 5-step wizard). For
> the **current product** — the one-prompt **DeepAgentForecast** flow
> (research → ontology → graph → prepare → run → report) and its `/api/research`
> orchestration — see **[README.md](README.md)** and
> **[DEERFLOW_INTEGRATION.md](DEERFLOW_INTEGRATION.md)**. The pipeline-service
> descriptions below (§4) are still accurate; the entry seam and UI sections
> reflect the standalone simulation engine, not the unified dashboard.

MiroFish is an LLM-driven **multi-agent social simulation engine**. You feed it
seed material (a news report, a policy draft, financial signals, or even a
novel) plus a natural-language *prediction requirement*, and it (1) extracts a
knowledge graph of the real-world actors involved, (2) turns each actor into an
autonomous LLM agent with a persona and memory, (3) runs thousands of those
agents loose on two simulated social platforms (a Twitter-like "square" and a
Reddit-like "community"), and (4) reads the emergent behaviour back out as a
predictive report you can interrogate. It is, in effect, a digital wind-tunnel
for "what if" questions.

The simulation core is **OASIS** (Open Agent Social Interaction Simulations,
by CAMEL-AI); the long-term memory / knowledge graph runs **locally** on the
open-source **Graphiti** engine (`graphiti-core`) backed by an embedded
**FalkorDB** (`falkordblite` — no Docker, no server, no account, no API key);
the LLM brain is pluggable (local Claude/Codex CLI by default, or any
OpenAI-compatible API, or Kimi-for-coding).

> *Migration note.* The knowledge-graph layer was originally **Zep Cloud** (a
> paid SaaS). It now runs entirely on-device via **Graphiti** — the same
> open-source engine Zep Cloud was built on — behind a drop-in
> **Zep-compatible shim** (`backend/app/services/graphiti_client/`). The shim
> preserves the old Zep SDK surface so the pipeline code barely changed; see
> §4.3 for how the local graph actually works.

---

## 1. Top-level layout

```
DeepAgentForecast/
├── package.json            # npm scripts: dev = concurrently(backend, frontend)
├── .env / .env.example     # LLM_PROVIDER, GRAPH_BACKEND, GRAPHITI_* …  (no API key)
├── frontend/               # Vue 3 + Vite SPA  (port 3000)
│   └── src/{views,components,api,router,store}
└── backend/                # Flask API + pipeline + OASIS scripts (port 5001)
    ├── run.py              # Flask entry point
    ├── app/
    │   ├── __init__.py     # app factory, blueprint registration
    │   ├── config.py       # all configuration (env-driven)
    │   ├── api/            # HTTP routes  (graph / simulation / report)
    │   ├── models/         # Task (in-memory) + Project (file-backed)
    │   ├── services/       # the actual pipeline (18 modules)
    │   │   └── graphiti_client/   # Graphiti shim → embedded FalkorDB (Zep-SDK-compatible)
    │   └── utils/          # LLM clients, file parsing, graph paging, retry, logging
    └── scripts/            # OASIS subprocess runners (run as separate processes)
        ├── run_parallel_simulation.py   # Twitter + Reddit in one process
        ├── run_twitter_simulation.py
        ├── run_reddit_simulation.py
        └── action_logger.py             # JSONL action-log writer
```

**Run model.** `npm run dev` starts both halves with `concurrently`. The Flask
backend (`uv run python run.py`) listens on `:5001`; the Vite dev server on
`:3000` proxies `/api` → `:5001`. OASIS simulations are **not** run in-process —
the backend spawns them as detached child processes and communicates with them
through the filesystem.

---

## 2. The pipeline at a glance

The whole system is one linear pipeline with a feedback loop. Each box is a
backend service; arrows are the artifacts handed forward.

```
 seed files ──TextProcessor──▶ clean text
     │
     ├─OntologyGenerator (LLM)──▶ ontology {10 entity types, 6-10 edge types}
     │
 text+ontology ─GraphBuilderService (shim→Graphiti)──▶ Local Graph  (graph_id≡group_id)
     │
 graph_id ─ZepEntityReader──▶ typed entities (the actors that become agents)
     │
     ├─OasisProfileGenerator (LLM + graph search)──▶ personas
     │     reddit_profiles.json  /  twitter_profiles.csv
     │
     └─SimulationConfigGenerator (LLM)──▶ simulation_config.json
            (time flow, per-agent activity, initial posts, platform weights)
     │
 SimulationManager ─writes all files──▶  sim dir is READY
     │
 SimulationRunner ─subprocess.Popen──▶ scripts/run_parallel_simulation.py
     │                                       │
     │                                   OASIS env.step() loop
     │                                       ▼
     │                              {twitter,reddit}/actions.jsonl  +  *_simulation.db
     │                                       │
     ├── monitor thread tails actions.jsonl ─┘──▶ run_state.json (progress)
     │            │
     │            └─(optional) ZepGraphMemoryUpdater ──▶ local graph   ← feedback loop
     │
 ReportAgent (ReAct + ZepToolsService) ──▶ markdown report (sections)
     │
 Deep interaction: chat with ReportAgent  |  interview live OASIS agents (file IPC)
```

This maps **exactly** to the 5-step wizard in the UI: Step 1 Graph Build →
Step 2 Env Setup → Step 3 Simulation → Step 4 Report → Step 5 Interaction.

---

## 3. Backend — Flask app skeleton

- **`run.py`** sets UTF-8 on Windows, validates `Config`, builds the app via the
  factory, and runs threaded on `0.0.0.0:5001`.
- **`app/__init__.py`** (`create_app`): enables permissive CORS on `/api/*`,
  disables ASCII-escaping of JSON (so Chinese renders directly), registers a
  request/response logging middleware, registers the blueprints under
  `/api/graph`, `/api/simulation`, `/api/report`, `/api/research`, and
  `/api/settings` (plus an optional `/api/v1` SDK surface when
  `Config.API_V1_ENABLED` is set), exposes `/health`, and calls
  `SimulationRunner.register_cleanup()` so all child simulation processes are
  killed on shutdown.
- **`config.py`** is the single configuration surface, driven entirely by `.env`.
  Key knobs: `LLM_PROVIDER` (default `claude-cli`); the local graph stack —
  `GRAPH_BACKEND` (`auto`|`falkordblite`|`kuzu`|`falkordb`), `GRAPHITI_DATA_DIR`,
  `GRAPHITI_EMBED_MODEL`/`GRAPHITI_EMBED_DIM`, `GRAPHITI_RERANKER`, and
  `FALKORDB_HOST`/`FALKORDB_PORT` (only used when pointing at an external
  FalkorDB); upload limits (50 MB; `pdf/md/txt/markdown`), chunking defaults
  (500/50), OASIS round defaults, the Twitter/Reddit action whitelists, and
  report-agent limits. **No `ZEP_API_KEY` is required** — the graph is local.
  The legacy `ZEP_*` retry/backoff knobs are retained but now tune transient
  *local* graph reads (no rate limits / 429s to absorb). `Config.validate()`
  enforces a known provider before the server starts.

### Async job pattern

Three operations are slow (graph build, env prepare, report generation). Each
follows the same shape: the route creates an in-memory `Task`, kicks off a
daemon `threading.Thread`, and returns a `task_id` immediately. The frontend
polls a `/status` endpoint. Because tasks live only in memory, every status
endpoint **also** accepts the durable entity id (`simulation_id` / `report_id`)
and falls back to checking on-disk completion artifacts — so progress survives a
backend restart even though the `Task` object doesn't.

### Models & persistence

| Model | Storage | Notes |
|---|---|---|
| `Task` / `TaskManager` | **in-memory singleton**, thread-safe | `PENDING→PROCESSING→COMPLETED/FAILED`, `progress` 0-100. Lost on restart. |
| `Project` / `ProjectManager` | **file-backed** under `uploads/projects/<id>/` | `project.json` + uploaded `files/` + `extracted_text.txt`. Status: `CREATED→ONTOLOGY_GENERATED→GRAPH_BUILDING→GRAPH_COMPLETED` (+`FAILED`). |
| Simulation state | files under `uploads/simulations/<id>/` | `state.json`, `run_state.json`, config, profiles, action logs, SQLite DBs. |
| Report | files under `uploads/reports/<id>/` | `outline.json`, `section_NN.md`, `full_report.md`, `agent_log.jsonl`, `console_log.txt`, `progress.json`, `meta.json`. |

---

## 4. The pipeline services (`backend/app/services/`)

All comments/prompts are Chinese; the system targets Chinese-language public-
opinion ("舆论") simulation. Everything routes LLM calls through one
`LLMClient` and knowledge-graph calls through the **Graphiti shim**
(`services/graphiti_client/`, which fronts a local Graphiti + FalkorDB engine
behind a Zep-SDK-compatible surface) plus paged helpers. The five graph-touching services (`GraphBuilderService`,
`ZepEntityReader`, `ZepGraphMemoryUpdater`, `OasisProfileGenerator`,
`ZepToolsService`) and `utils/zep_paging.py` kept their Zep-era names and
call shapes; only their import line changed
(`from zep_cloud…` → `from .graphiti_client…`).

### 4.1 TextProcessor + FileParser — ingestion
`FileParser` extracts text from PDFs (PyMuPDF/`fitz`) and md/txt (with
`charset_normalizer`→`chardet`→utf-8-replace fallback, so GBK Chinese files
survive). `TextProcessor` preprocesses (normalises whitespace) and chunks text
on sentence boundaries (default 500 chars, 50 overlap).

### 4.2 OntologyGenerator — *what kinds of actors exist?*
Sends the seed text + the prediction requirement to the LLM with a long system
prompt that demands the ontology describe **real, social-media-capable actors**
(people, companies, media, government, platforms) — explicitly *not* abstract
topics or stances. Hard constraints (graph schema limits, inherited from the
original Graphiti/Zep graph-schema contract): exactly **10 entity types**, the last two forced to be
`Person`/`Organization` fallbacks; 6-10 edge types; attribute names must avoid
reserved words and stay **primitive** (`Optional[str]`), because FalkorDB only
stores scalar node/edge properties. Output is a validated dict of
`entity_types` + `edge_types`.

### 4.3 GraphBuilderService — *build the knowledge graph (GraphRAG)*
This is where the local knowledge graph is created. Runs async (background
thread, progress via `TaskManager`). The service still calls the **Zep API
shape** (`graph.create` / `set_ontology` / `add_batch` / `search` …); the
`graphiti_client` shim translates each call onto Graphiti, which writes into the
embedded FalkorDB. Steps:
1. `graph.create` → a local graph keyed by `mirofish_<hex>`. The shim maps this
   `graph_id` **directly onto Graphiti's `group_id`** (also reused as the
   FalkorDB tenant/database name) and caches **one `Graphiti` instance per
   `graph_id`**.
2. `graph.set_ontology` — **dynamically synthesises Pydantic entity/edge classes**
   from the ontology dict (`type(name, (EntityModel,), …)`), remapping reserved
   attribute names. The shim caches this ontology and passes `entity_types` /
   `edge_types` **per `add_episode`** (Graphiti takes the schema at ingest time)
   rather than registering it server-side as Zep did.
3. Chunk text → `graph.add_batch` episodes (batches of 3). The shim routes each
   to Graphiti's `add_episode`, where the app's configured **`LLM_PROVIDER`**
   (via an `AppGraphitiLLMClient` adapter over the app's `LLMClient` — works even
   with the no-key CLI providers) performs entity/edge extraction and a local
   **sentence-transformers** model (`paraphrase-multilingual-MiniLM-L12-v2`,
   384-dim, via `LocalSentenceTransformerEmbedder`) computes embeddings.
4. **No episode polling.** `add_episode` is *synchronous on return* — extraction
   has already completed when the call returns — so the old "submit then poll
   `episode.processed` (≤ 600 s)" wait collapses to a fast no-op.
5. `get_graph_data` pages all nodes/edges (with temporal fields) for the UI; the
   shim wraps Graphiti nodes/edges back into the Zep object shape (`uuid_`+`uuid`,
   `fact`, `fact_type`, `valid_at`/`invalid_at`/`expired_at`/`created_at`,
   `labels`, `summary`, `attributes`, `episodes`). Bi-temporal facts are still
   produced — now computed by Graphiti locally rather than by a remote service.

> **How the shim runs Graphiti.** Graphiti's API is `async`; the app is sync.
> The shim runs every Graphiti coroutine on a **dedicated background asyncio
> event loop** (a sync→async bridge), so callers keep their blocking interface.
> Listing maps to `EntityNode.get_by_group_ids` / `EntityEdge.get_by_group_ids`
> (cursor pagination preserved; an empty edge set raises
> `GroupsEdgesNotFoundError`, which the shim catches → `[]`). `graph.search`
> maps to `graphiti.search_()` with the `EDGE_HYBRID_SEARCH_RRF` /
> `NODE_HYBRID_SEARCH_RRF` recipes. Reranking is **RRF by default** (a
> `NoOpCrossEncoder` satisfies construction; the RRF recipes do the ranking with
> no extra cross-encoder LLM call); a local **BGE** cross-encoder is optional via
> `GRAPHITI_RERANKER=bge`.

### 4.4 ZepEntityReader — *which nodes become agents?*
`filter_defined_entities(graph_id)` keeps only nodes whose labels include a
custom ontology type (i.e. they matched something beyond the generic
`Entity`/`Node`), optionally enriching each with its incoming/outgoing edges and
neighbour summaries. This filtered set is the cast of the simulation.

### 4.5 OasisProfileGenerator — *give each actor a persona* (Step 2a)
For every entity it builds a context blob (attributes + related facts + a
**parallel secondary graph search** over edges & nodes for extra recall) and asks
the LLM to write an `OasisAgentProfile`: bio (~200 chars), a ~2000-char persona
(background, MBTI, posting behaviour, stance, and the actor's personal/
institutional *memory* of the event), plus demographics. Individuals vs.
institutions get different prompts. Generation is parallel
(`ThreadPoolExecutor`), order-preserving, with incremental save and rule-based
fallback on failure. Persisted in the exact formats OASIS demands:
- **Twitter** → CSV (`user_id,name,username,user_char,description`; `user_char`
  becomes the agent's system prompt).
- **Reddit** → JSON keyed by `user_id` (must match `initial_posts.poster_agent_id`).

### 4.6 SimulationConfigGenerator — *how does the world tick?* (Step 2b)
Generates every simulation parameter via the LLM in **stepwise** fashion (avoids
one giant fragile call):
1. **Time config** — 72 simulated hours, minutes-per-round, peak/off-peak hour
   buckets and activity multipliers tuned to a Chinese daily rhythm.
2. **Event config** — hot topics, narrative direction, and `initial_posts`
   (each tagged with a `poster_type`).
3. **Per-agent activity** (batched 15 at a time) — activity level, posts/comments
   per hour, active hours, stance, sentiment bias, influence weight; officials =
   low-activity/high-influence, media = all-day, individuals = evening-heavy.
4. **Initial post → agent assignment** — matches each seed post's `poster_type`
   to a concrete agent.
5. **Platform weights** — recency/popularity/relevance, viral threshold, echo-
   chamber strength.
Serialised to `simulation_config.json`.

### 4.7 SimulationManager — orchestrates Step 2
Owns simulation lifecycle metadata (`SimulationStatus`,
`created→preparing→ready→running→…`). `prepare_simulation` runs 4.4 → 4.5 → 4.6
and writes all files OASIS needs, ending in state `READY`. Stores everything
under `uploads/simulations/<sim_id>/`.

### 4.8 SimulationRunner — runs & monitors the OASIS subprocess (Step 3)
The heaviest service (~1700 lines), almost all classmethods over class-level
registries. `start_simulation`:
- Computes `total_rounds = total_hours*60/minutes_per_round` (optionally capped
  by `max_rounds`).
- Picks a script (`run_parallel`/`run_twitter`/`run_reddit`) and launches it via
  **`subprocess.Popen`** with `cwd=sim_dir`, UTF-8 env, stdout/stderr →
  `simulation.log`, and `start_new_session=True` (own process group, so the whole
  tree can be signalled).
- Spawns a **daemon monitor thread** that tails `twitter/actions.jsonl` and
  `reddit/actions.jsonl` every 2 s, parsing each JSONL record into the live
  `SimulationRunState` (`run_state.json`): `round_end` events advance the round
  counter and simulated hours; `simulation_end` marks platform completion;
  action records become `AgentAction`s. When the process exits it sets
  COMPLETED/FAILED (capturing the log tail on failure).
- If graph-memory updating is enabled, each parsed action is forwarded to the
  `ZepGraphMemoryUpdater` — **the feedback loop that grows the local graph during
  the run.**

It also exposes process control (cross-platform kill via `taskkill`/`killpg`),
analytics readers (`get_actions`, `get_timeline`, `get_agent_stats`, plus direct
SQLite reads of posts/comments), and the **interview API** (delegating to the
file-based IPC client).

### 4.9 SimulationIPC — talking to a running simulation
Because the OASIS process is detached, Flask communicates with it through a
**filesystem command/response protocol**. `SimulationIPCClient` (Flask side)
writes `<command_id>.json` into `<sim>/ipc_commands/` and polls
`<sim>/ipc_responses/` for the matching reply; `SimulationIPCServer` (script
side) polls for commands, executes them, writes responses, and maintains
`env_status.json` (`alive`/`stopped`). Commands: `interview`, `batch_interview`,
`close_env`. `platform=None` means interview both platforms.

### 4.10 ZepGraphMemoryUpdater — simulation → graph feedback
Converts each agent action into a natural-language Chinese sentence
(`<agent>: created a post about … / liked …`), buffers per platform (batch 5),
and `graph.add`s them back into the local graph (shim → Graphiti `add_episode`)
so the knowledge graph keeps evolving as the simulation unfolds (this is what
powers the "GraphRAG memory updating live" overlay in the UI). Runs on its own
worker thread with a registry manager.

### 4.11 ZepToolsService — the report agent's toolbox
The retrieval/interview toolkit. Foundation is `search_graph` — the shim's
`graph.search`, i.e. Graphiti hybrid search over the local FalkorDB using the
`*_HYBRID_SEARCH_RRF` recipes (RRF reranking by default, optional local BGE
cross-encoder); degrades to local keyword search on failure. On top sit the 4
high-level tools the report agent calls:
- **`insight_forge`** — multi-hop deep retrieval: LLM decomposes the question
  into sub-queries, searches each, pulls entity details, builds relationship
  chains.
- **`panorama_search`** — splits facts into active vs. historical/expired using
  temporal flags (good for timelines/evolution).
- **`quick_search`** — thin search wrapper.
- **`interview_agents`** — selects diverse relevant agents, generates questions,
  and runs *real OASIS interviews* via `SimulationRunner.interview_agents_batch`
  (600 s timeout, ≤6 agents — deliberate guards against the historical 180 s IPC
  timeout), then summarises.

### 4.12 ReportAgent + ReportManager — the predictive report (Step 4)
Generates a Chinese "future-prediction report" framed as a god's-eye preview of
the future. It is a **ReAct agent** (prompt-based tool calling, since the CLI
providers don't support native tool calling):
- `plan_outline` produces a 2-5 section JSON outline.
- For each section, `_generate_section_react` loops (≤5 iterations, **≥3 tool
  calls required**): the LLM emits either `<tool_call>{…}</tool_call>` or a Final
  Answer; the agent executes one tool per turn, feeds the observation back, and
  only accepts a Final Answer once enough evidence is gathered.
- **Contamination defence** (`_looks_contaminated`): rejects sections that leak
  Claude-Code system-prompt text, leftover tool-call framing, interview-timeout
  strings, or are too short — a direct mitigation for the known `claude-cli`
  prompt-leak failure mode. Failed sections get a visible placeholder rather
  than a silent "success."
- `chat()` is a lightweight 2-iteration ReAct over the finished report for
  interactive Q&A.
`ReportManager` persists outline/sections/progress and streams structured
`agent_log.jsonl` + plain `console_log.txt` so the UI can render the agent's
reasoning live.

---

## 5. The OASIS simulation process (`backend/scripts/`)

`run_parallel_simulation.py` is launched as an independent process. It:
1. Monkey-patches `open()` to default UTF-8 (works around OASIS reading files
   without an explicit encoding), silences OASIS's verbose loggers, loads `.env`.
2. Builds an **agent graph** per platform from the profiles
   (`generate_twitter_agent_graph` from CSV / `generate_reddit_agent_graph` from
   JSON), each agent wired to the LLM model and the platform's action whitelist.
3. `oasis.make(...)` creates the environment with a fresh SQLite DB
   (`<platform>_simulation.db`) and a concurrency semaphore (3 for CLI providers,
   30 for HTTP — because each CLI call is a subprocess).
4. `env.reset()`, then injects the `initial_posts` as `ManualAction`s (round 0).
5. **Round loop** (`for round_num in range(total_rounds)`): computes the
   simulated hour/day, picks active agents via `get_active_agents_for_round`
   (gated by each agent's `active_hours` × `activity_level`, scaled by peak/off-
   peak multipliers and a random target count), then issues
   `{agent: LLMAction()}` and `await env.step(actions)`. Each agent autonomously
   decides what to do (post, like, comment, repost, follow…). New DB rows are
   read back and written to `actions.jsonl` by `PlatformActionLogger`.
6. Twitter and Reddit run **concurrently** via `asyncio.gather`.
7. When the loop finishes the env is **not closed** — the process enters
   *wait-for-commands mode*, polling the IPC command dir so the user can
   interview agents (Step 5) against the post-simulation world. `close_env`
   (or a SIGTERM from the backend) finally tears it down.

`action_logger.py` defines the JSONL schema (round-start/round-end/action/
simulation-end event records) that the backend monitor thread parses back into
progress.

---

## 6. LLM provider abstraction

Two cooperating modules make every LLM call provider-agnostic. `Config.LLM_PROVIDER`
selects one of 8 providers (data-driven via `PROVIDER_META`): `claude-cli`
(default), `codex-cli`, `openai`, `kimi`, `minimax`, `deepseek`, `qwen`, `glm`.
The last six are OpenAI-compatible API providers needing `LLM_API_KEY`;
`kimi`/`minimax`/`deepseek`/`qwen`/`glm` ship sensible default `BASE_URL`/`MODEL_NAME`
so you only set `LLM_API_KEY`.

- **`utils/llm_client.py` — `LLMClient`** is used by all *pipeline* generation
  (ontology, profiles, config, report). Uniform interface: `chat(...)` and
  `chat_json(...)`.
  - **CLI providers** shell out: `subprocess.run(["claude","-p","--output-format","json", prompt], cwd="/tmp")`
    or `codex exec`. Multi-turn messages are flattened into one prompt. Runs in
    `/tmp` so the CLI doesn't pick up the repo's `CLAUDE.md`/context (leak
    mitigation). Wrapped in a 3-try exponential backoff; Claude error envelopes
    (rate limits) and empty output raise `RuntimeError` to trigger retry.
  - **OpenAI/Kimi** use the `openai` SDK. Kimi injects a coding-agent
    `User-Agent` header (`claude-cli/1.0.0`) — the Kimi-for-coding gateway
    authorises by UA — and disables hidden "thinking" by default so reasoning
    tokens don't exhaust the budget and return empty content.
- **`utils/oasis_llm.py`** bridges the same 8 providers into the CAMEL
  `ChatCompletion` shape OASIS expects. CLI providers are wrapped in a
  **`CLIModel`** (a fake `OpenAIModel` that calls `LLMClient` and synthesises a
  `ChatCompletion` with estimated token usage; it ignores `tools=` because CLI
  mode has no native tool calling). `get_oasis_semaphore` caps concurrency low
  (3) for CLI providers. There's an optional OpenAI "boost" dual-LLM path.

**Net effect:** by default MiroFish needs *no API key at all*. The LLM drives
your local Claude Code / Codex subscription, and the knowledge graph runs
locally on Graphiti + embedded FalkorDB — there is **no Zep account, no
`ZEP_API_KEY`, and no remote graph service**. Entity/edge extraction and
embeddings happen on-device (your `LLM_PROVIDER` for extraction, a local
sentence-transformers model for embeddings).

---

## 7. Frontend — Vue 3 SPA (`frontend/`)

- **Stack:** Vue 3 (Composition API, `<script setup>`), Vue Router 4 (history
  mode), Axios, D3 v7 (force-directed graph), Vite 7. No Vuex/Pinia — a tiny
  hand-rolled `reactive` store (`store/pendingUpload.js`) bridges the Home page's
  uploaded files to the first route. Chinese UI, monochrome + orange "terminal"
  aesthetic.
- **Dev proxy:** Vite on `:3000` proxies `/api` → `http://localhost:5001`.
- **API layer:** one Axios instance (`api/index.js`, baseURL `localhost:5001`,
  5-min timeout, response interceptor unwrapping `{success,data,error}`,
  retry-with-backoff helper) plus three domain modules (`graph.js`,
  `simulation.js`, `report.js`).
- **Routes / views** mirror the pipeline:

| Route | View | Step |
|---|---|---|
| `/` | `Home.vue` (+ `HistoryDatabase`) | upload + prompt |
| `/process/:projectId` | `MainView.vue` → `Step1GraphBuild` | 1 — graph build |
| `/simulation/:simulationId` | `SimulationView` → `Step2EnvSetup` | 2 — env setup |
| `/simulation/:simulationId/start` | `SimulationRunView` → `Step3Simulation` | 3 — run |
| `/report/:reportId` | `ReportView` → `Step4Report` | 4 — report |
| `/interaction/:reportId` | `InteractionView` → `Step5Interaction` | 5 — interact |

All step views share a shell: header, a `graph / split / workbench` view-mode
toggle, a step indicator, a left **`GraphPanel`** (D3 graph with zoom/pan, drag,
curved multi-edges, collapsed self-loops, type-colour legend, and a live
"GraphRAG memory updating" overlay), and the right step component.

- **Everything is poll-based** (no WebSockets). Each step auto-fires its backend
  job and runs tight `setInterval` loops: Step 1 polls task + graph data; Step 2
  streams profiles + config as they generate; Step 3 polls run-status + action
  detail and rebuilds a dual-platform action timeline; Step 4 incrementally
  pulls `agent-log` + `console-log` (via a `from_line` cursor) and renders the
  ReAct stream — planning → sections → tool calls → completion; Step 5 offers
  chat-with-report-agent and broadcast/individual agent interviews.

---

## 8. Cross-cutting concerns & notable design points

- **Concurrency model:** Flask is threaded; slow jobs run in daemon threads;
  profile generation and graph enrichment use thread pools; the graph shim runs
  Graphiti's async API on a dedicated background asyncio event loop (sync→async
  bridge); OASIS runs as detached OS subprocesses with their own asyncio loops.
  Three independent retry layers (LLMClient CLI backoff, OpenAI SDK retries, and
  the `ZEP_*`-tuned graph-paging backoff — now smoothing transient *local* graph
  reads rather than remote rate limits).
- **Resilience to LLM flakiness:** aggressive JSON repair (`_fix_truncated_json`,
  control-char scrubbing, regex extraction) across ontology/profile/config
  generation; empty completions raise to trigger retry rather than poisoning
  downstream parsing.
- **Known failure modes (defended in code):** (1) `claude-cli` leaking the
  Claude Code system prompt into report sections → `_looks_contaminated`
  rejection + `/tmp` cwd; (2) interview IPC timeouts → 600 s timeout / ≤6 agents;
  (3) Kimi 403 / empty content → coding-agent UA + thinking disabled.
- **Data durability:** Projects, simulations, and reports are durable on disk;
  `Task` objects are ephemeral (in-memory), so status endpoints defensively also
  resolve progress from on-disk artifacts.
- **Per-simulation directory** (`uploads/simulations/<sim_id>/`) is the integration
  point between the Flask backend and the OASIS subprocess: `simulation_config.json`,
  `*_profiles.{json,csv}`, `{twitter,reddit}/actions.jsonl`, `<platform>_simulation.db`,
  `run_state.json`, `env_status.json`, and the `ipc_commands/` + `ipc_responses/`
  mailboxes.

---

## 9. End-to-end summary

A user uploads a document and a question. MiroFish reads the document, decides
what kinds of real-world actors matter, builds a temporal knowledge graph of
them **locally** (Graphiti extracting entities/edges with your LLM provider and
embedding them on-device, stored in an embedded FalkorDB behind the
Graphiti shim), gives each actor an LLM persona and a memory of the event,
then sets thousands of them loose on two simulated social networks where they
post, argue, like, and follow autonomously over 72 simulated hours. Their
collective behaviour is logged action-by-action and continuously fed back into
the knowledge graph. A ReAct report agent then mines that post-simulation
graph — running deep multi-hop retrievals and even interviewing the agents
directly — to write a predictive report, which the user can finally interrogate
by chatting with the report agent or talking to any individual simulated
character. The entire workload — LLM **and** knowledge graph — can run on a
local Claude/Codex subscription with **no API key and no paid services.**
