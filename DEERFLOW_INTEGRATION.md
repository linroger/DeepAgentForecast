# MiroFish × DeerFlow — Integration Design

> **STATUS: IMPLEMENTED (Option C, Phases 0–4).** The end-to-end pipeline
> (prompt → research → ontology → graph → simulation → report) works, and the
> full Phase-4 fidelity loop is now wired: `actors.json` **biases the ontology**,
> its structured `role`/`stance`/`influence`/`memory` fields are **name-matched to
> graph entities and injected into persona generation** (`OasisProfileGenerator`),
> drive **per-agent `stance`/`influence_weight`** in `SimulationConfigGenerator`,
> and ground **`initial_posts`** (with `poster_name` targeting to the actual
> researched actor). `sources.json` provenance is surfaced in the UI. Pipelines
> are **cancellable** (`POST /api/research/<id>/cancel`), `POST /run` pre-flights
> the whole configuration, and the backend venv is pinned to Python 3.12
> (`backend/.python-version`). Shared helpers live in `backend/app/utils/actors.py`.
> Still open (deliberate): stage-aware resume/continue of failed or research-only
> pipelines, and the optional Gateway (Option B) migration.

**Goal:** one prompt in, a prediction out. The user types a natural-language
question; a **deep-research agent (DeerFlow 2.0)** gathers data and builds the
context; that context is handed to the **multi-agent prediction engine
(MiroFish)**, which builds a knowledge graph, runs a social simulation, and
returns a prediction report — all driven by a **coding-plan subscription
(Claude Code / Codex), no per-token API key.**

This document is the design + implementation plan. Both pieces live in this
single repo folder:

```
DeepResearchForecast/
├── backend/ + frontend/ ← prediction engine (see ARCHITECTURE.md)
├── deerflow_bridge/     ← overlay applied onto deer-flow/ by setup.sh
└── deer-flow/           ← DeerFlow 2.0 super-agent harness (auto-downloaded, gitignored)
```

---

## 1. The two systems in one paragraph each

**MiroFish** (see `ARCHITECTURE.md`) is a linear pipeline with a feedback loop:
seed text + a natural-language *prediction requirement* → LLM ontology → Zep
knowledge graph → typed entities become agents → personas + simulation config →
**OASIS** dual-platform (Twitter+Reddit) agent simulation → a ReAct **ReportAgent**
writes a prediction report → deep interaction (chat / interview live agents). Its
true inputs are just **(a) seed material describing the real-world situation and
its actors, and (b) a prediction question.** Today those arrive via
`POST /api/graph/ontology/generate` (multipart: `files` + `simulation_requirement`
+ optional `additional_context`), which creates a project, extracts text to
`extracted_text.txt`, and generates the ontology. LLM calls go through a
`claude-cli` subprocess bridge by default.

**DeerFlow 2.0** is a ground-up rewrite (shares no code with the v1 "deep
research" graph; v1 lives on the `main-1.x` branch). It is a **Claude-Code-style
super-agent harness** (`backend/packages/harness/deerflow/`, the `deerflow-harness`
package) built on `langchain.agents.create_agent` (LangGraph). A single **lead
agent** runs a tool-use loop with: `web_search` (DDG/Tavily/Exa/Serper/
Firecrawl/InfoQuest), `web_fetch` (Jina/Exa/Firecrawl), `image_search`, a
**sandbox** filesystem (`ls/read_file/glob/grep/write/edit/bash`), **sub-agents**
(`task` tool → parallel scoped workers), **MCP** tools, and **skills**
(Claude-Code `SKILL.md` files). It ships a first-class **`deep-research`** skill
plus `github-deep-research`, `systematic-literature-review`, `academic-paper-review`,
`consulting-analysis`, `data-analysis`, etc. It exposes two front doors:

- **Embedded Python client** — `deerflow.client.DeerFlowClient`: `chat(prompt,
  thread_id)` / `stream(...)`, `upload_files()`, `get_artifact()`. Runs the agent
  in-process, no server.
- **Gateway** — a FastAPI service (`backend/app/gateway/`) with routers `runs`,
  `threads`, `uploads`, `artifacts`, `agents`, `models`, `skills`, `memory`,
  `mcp`, `channels` (HTTP + SSE, LangGraph-server compatible).

---

## 2. Why these two fit together (the seam)

MiroFish is only as good as its **seed material**. Today a human uploads a PDF/
report. DeerFlow's entire job is to *produce* exactly that kind of material —
a thorough, multi-source, cited research dossier — from a single question.

> **DeerFlow's research output = MiroFish's seed input.**

So the pipeline composes cleanly with no architectural surgery:

```
            ┌────────────────────────── UNIFIED PIPELINE ──────────────────────────┐
 user prompt│                                                                       │ prediction
 ──────────▶│  DeerFlow lead agent (deep-research skill)                            │──────────▶
            │    web_search ▸ web_fetch ▸ sub-agents ▸ synthesize                   │  report +
            │      └─▶ research_report.md   (+ optional actors.json, timeline.json) │  live world
            │                       │                                               │
            │                       ▼                                               │
            │  MiroFish: seed = research_report.md ; requirement = user prompt      │
            │    ontology ▸ Zep graph ▸ personas ▸ OASIS sim ▸ ReportAgent          │
            └───────────────────────────────────────────────────────────────────────┘
```

The user's single prompt is reused twice **verbatim**: as the DeerFlow research
brief, and as MiroFish's `simulation_requirement` (the orchestrator passes
`state.prompt` unchanged to both — there is no sharpening/rewrite step in the code).

---

## 3. The data contract (DeerFlow → MiroFish)

Keep the hand-off a **plain set of files in a directory** — this matches
MiroFish's existing file-based conventions (projects, simulations, IPC mailboxes)
and keeps the two venvs decoupled. The research stage writes a `handoff/` folder:

| File | Producer | Consumer in MiroFish | Required |
|---|---|---|---|
| `research_report.md` | DeerFlow (final synthesis) | seed document → `extracted_text` | **yes** |
| `prediction_requirement.txt` | `deerflow_research.py` (the user prompt, verbatim) | informational only — the live `simulation_requirement` is the in-memory `state.prompt`, never re-read from this file | **yes** |
| `sources.json` | DeerFlow (cited URLs + titles) | provenance panel in UI; optional extra context | no |
| `actors.json` | DeerFlow (structured extraction) | **pre-seeds the ontology** + `initial_posts` | recommended |
| `timeline.json` | DeerFlow | `panorama_search` / event config seeding | optional |

**`actors.json` is the highest-leverage optional output.** MiroFish's
`OntologyGenerator` already insists the ontology describe *real, social-media-capable
actors* (people, companies, media, government, platforms) — not abstract topics.
If DeerFlow is asked (via a custom skill / a structured-output sub-agent) to emit:

```jsonc
{
  "central_question": "...",
  "as_of_date": "2026-06-08",
  "actors": [
    {"name": "...", "type": "Person|Organization|Media|Government|Platform",
     "role": "...", "stance": "...", "influence": "high|med|low",
     "memory": "what this actor knows/believes about the event"}
  ],
  "key_events": [{"date": "...", "event": "..."}],
  "hot_topics": ["..."]
}
```

…then MiroFish can (a) bias its ontology toward those entity types, (b) seed
`initial_posts` and per-agent `stance`/`influence` in `SimulationConfigGenerator`
with real data instead of LLM guesses, and (c) skip re-discovering the cast. This
turns DeerFlow from "a fancier file upload" into a genuine fidelity upgrade.

> **Implementation status:** (a), (b) and (c) are all **wired**. (a)
> `PipelineOrchestrator` passes `_actors_to_context(actors)` as
> `additional_context` into `OntologyGenerator.generate`, biasing the ontology
> toward the researched actors. (b) `prepare_simulation(..., actors=...)` threads
> the structured archive through both generators: `OasisProfileGenerator` matches
> each graph entity to its actor row by normalized name (`app/utils/actors.py`)
> and injects role/stance/influence/memory into the persona prompt as
> authoritative researched evidence; `SimulationConfigGenerator` adds an
> actors/key_events/hot_topics digest to its context, grounds per-agent
> `stance`/`sentiment_bias`/`influence_weight` in the researched profile
> (high≈2.5-3.0 / medium≈1.5-2.0 / low≈0.8-1.2, with a deterministic
> influence override on the rule-based fallback path), and asks the event-config
> LLM to author `initial_posts` as the actual researched actors (`poster_name`
> is matched by name to the right agent before falling back to type aliases).
> (c) holds via (a)+(b).

> The minimum viable contract is just `research_report.md` +
> `prediction_requirement.txt`. `actors.json` is a fast-follow that materially
> improves simulation fidelity.

---

## 4. Integration topology — three options, one recommendation

The hard constraint driving this choice: **dependency isolation.** MiroFish pins
`camel-oasis==0.2.5`, `camel-ai==0.2.78`, `zep-cloud==3.13.0` (Python ≥3.11,≤3.12).
DeerFlow needs Python ≥3.12 with the full `langchain`/`langgraph`/`anthropic`/
`fastapi` stack. `camel-ai` drags heavy transitive pins (pydantic, openai,
tokenizers, …) that fight the langchain stack. **They must not share one venv.**

### Option A — Embedded `DeerFlowClient` inside MiroFish's Flask process
Import `deerflow` directly. *Rejected as primary:* forces one venv → dependency
hell with camel-ai. Only viable if MiroFish's OASIS layer is later spun into its
own subprocess venv too.

### Option B — DeerFlow Gateway as a sidecar service (HTTP + SSE) ✅ *recommended for production*
Run DeerFlow's FastAPI gateway as a second backend (its own venv/container).
MiroFish's orchestrator calls it over HTTP:
- `POST /threads` → create a thread,
- `POST /runs` (or `assistants_compat`) with the research prompt → stream SSE
  progress (reuse MiroFish's existing poll/stream UI plumbing),
- `GET /artifacts/...` → pull `research_report.md` + `actors.json`.

**Pros:** total isolation, independent scaling/restart, DeerFlow's own web UI
stays usable, language-agnostic. **Cons:** two services to run; need auth between
them (gateway has `internal_auth`/`csrf`); slightly more ops. This mirrors how
MiroFish already treats OASIS as an out-of-process engine.

### Option C — Thin subprocess entry into DeerFlow's venv ✅ *recommended for the first cut*
A small script `deerflow_research.py` living in DeerFlow's repo, run via its venv:

```bash
cd deer-flow && uv run python deerflow_research.py \
    --prompt-file <handoff>/prediction_requirement.txt \
    --out-dir <handoff>/
```

Internally it uses `DeerFlowClient(model_name="claude", subagent_enabled=True)`,
streams events to a log MiroFish tails, and writes `research_report.md` (+
`actors.json`) to `--out-dir`. MiroFish launches it with `subprocess.Popen` and a
**monitor thread** — *exactly the pattern `SimulationRunner` already uses for the
OASIS process*, including progress tailing and process-group kill.

**Pros:** zero new long-running service; reuses MiroFish's proven detached-process
+ file-IPC machinery; trivial venv isolation; easy to ship first. **Cons:** cold
start per run; streaming is via log-tail rather than native SSE.

**Recommendation:** **build Option C first** (fastest correct path, reuses
existing infra, isolates deps), and keep the code factored so swapping in
**Option B** later is a transport change, not a redesign. Both write the same
`handoff/` contract from §3, so the MiroFish side is identical.

---

## 5. Claude Code plan unification (the "coding plan" requirement)

This is the cleanest part. **DeerFlow 2.0 natively consumes the Claude Code
subscription** — no API key, native tool calling:

- `deerflow/models/claude_provider.py::ClaudeChatModel` auto-loads OAuth creds via
  `credential_loader.py` from, in order: `$CLAUDE_CODE_OAUTH_TOKEN` /
  `$ANTHROPIC_AUTH_TOKEN`, a file-descriptor handoff, `$CLAUDE_CODE_CREDENTIALS_PATH`,
  then **`~/.claude/.credentials.json`** (the exact file the `claude` CLI writes).
- It detects `sk-ant-oat…` tokens, switches to `Authorization: Bearer`, and adds
  `anthropic-beta: oauth-2025-04-20,claude-code-20250219,interleaved-thinking-…`
  plus the billing-header system block and `metadata.user_id` — i.e. it
  impersonates the Claude Code CLI to bill the **Max/Pro plan** instead of the API.
- Codex plan works the same way via `openai_codex_provider.py` + `~/.codex/auth.json`.

So configure DeerFlow's `config.yaml` with one model entry:

```yaml
models:
  - name: claude
    display_name: Claude (Code plan)
    use: deerflow.models.claude_provider:ClaudeChatModel
    model: claude-sonnet-4-6        # or opus
    max_tokens: 16384
    supports_thinking: true
```

**Both engines then ride the same subscription:**
- DeerFlow → `ClaudeChatModel` (OAuth, direct Anthropic API, **keeps native tool
  calling** — important for the deep-research tool loop).
- MiroFish → its existing `claude-cli` provider (subprocess bridge), which already
  uses the same `~/.claude` credentials.

> **Optional later win:** MiroFish's `claude-cli` bridge loses native tool calling
> (that's why its ReportAgent is a hand-rolled ReAct prompt loop, with the
> documented contamination-defence hacks). DeerFlow's `ClaudeChatModel` is a
> drop-in `BaseChatModel` with *real* tool calling on the same plan. A future
> refactor could route MiroFish's LLM calls through `ClaudeChatModel` too,
> retiring the fragile CLI bridge — but that is **out of scope** for the first
> integration and should be a separate effort.

One subscription, one credentials file, both engines. Requirement satisfied.

### 5.1 Alternative code plan — MiniMax (`minimax`)

A second coding-plan is wired in as a drop-in alternative to Claude (useful in mainland
China, where `api.minimaxi.com` is reachable and the plan is a flat-rate "code plan"):

- **MiroFish** → `minimax` provider (`LLM_PROVIDER=minimax`). OpenAI-compatible
  (`https://api.minimaxi.com/v1`, model `MiniMax-M3`), Bearer auth with the `sk-cp-…`
  code-plan key in `LLM_API_KEY`. `MiniMax-M3` is a reasoning model, so reasoning is
  disabled by default (`LLM_MINIMAX_DISABLE_THINKING=true` → `extra_body.thinking.type=disabled`)
  for stable JSON output; `_clean_content` also strips any inline `<think>`. No User-Agent
  gating (unlike Kimi). Both the `LLMClient` path (ontology/personas/report) and the OASIS
  CAMEL path are wired.
- **DeerFlow** → the `minimax` model in `config.yaml` (`deerflow.models.patched_minimax:PatchedChatMiniMax`,
  `MiniMax-M3`, key from `$MINIMAX_API_KEY`). `PatchedChatMiniMax` strips the
  middleware-injected per-message `name` field that otherwise triggers MiniMax's
  **"400 user name must be consistent"** error — tools and reasoning stay **ON**.
  Select it with `DEERFLOW_MODEL=minimax` (or `deerflow_research.py --model minimax`).
  `deerflow_research.py` strips any residual `<think>` from the report.

To ride MiniMax on **both** engines: set `LLM_PROVIDER=minimax`, `LLM_API_KEY=<sk-cp-…>`,
`MINIMAX_API_KEY=<sk-cp-…>`, `DEERFLOW_MODEL=minimax`. (Validated live: MiroFish `LLMClient`
chat/chat_json, the OASIS `model.run()`, and a real ontology generation all succeed on
MiniMax-M3. DeerFlow research over MiniMax uses **streaming**, which works on a normal host
but is blocked inside hardened/no-egress sandboxes that break long-lived SSE sockets.)

### 5.2 The full `DEERFLOW_MODEL` set (7 options)

`DEERFLOW_MODEL` selects the deep-research stage model (the `deer-flow/config.yaml`
model stanza). Six values are wired:

| `DEERFLOW_MODEL` | Provider / model | Base URL | Key env (deer-flow) |
|---|---|---|---|
| `claude` *(default)* | Claude Code OAuth (`ClaudeChatModel`) | direct Anthropic API | none (`~/.claude/.credentials.json`) |
| `minimax` | `PatchedChatMiniMax`, `MiniMax-M3` | `https://api.minimaxi.com/v1` | `MINIMAX_API_KEY` |
| `deepseek` | DeepSeek V4, `deepseek-v4-pro` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `qwen` | Qwen3.7-Max, `qwen3.7-max` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `glm` | GLM-4.6, `glm-4.6` | `https://api.z.ai/api/paas/v4` | `ZHIPUAI_API_KEY` |
| `codex` | Codex (ChatGPT) OAuth | direct OpenAI API | none (`~/.codex/auth.json`) |

`claude` and `codex` ride a coding-plan subscription (no API key). The other four
are OpenAI-compatible API stanzas and need only their per-provider `$KEY` env set
(base URL / model name ship as sensible defaults in `config.yaml`). Select any of
them with `DEERFLOW_MODEL=<value>` (or `deerflow_research.py --model <value>`).
Mirror the matching MiroFish `LLM_PROVIDER` + key envs when you want **both** the
research and report/simulation stages on the same provider.

---

## 6. The unified interface

### 6.1 Backend — a new orchestration blueprint
Add `backend/app/api/research.py` (blueprint `research_bp`, mounted at
`/api/research`) to MiroFish, plus a `PipelineOrchestrator` service. It reuses the
**existing async Task + daemon-thread + poll pattern** (`TaskManager`) and the
existing graph/sim/report services — the orchestrator just chains them:

```
POST /api/research/run        body: { prompt, mode?, depth?, max_rounds?, project_name? }
  → creates Task + a "pipeline" record under uploads/pipelines/<id>/
  → stage 1 RESEARCH:   launch DeerFlow (Option C subprocess), tail progress,
                        produce handoff/ (research_report.md, actors.json, …)
  → stage 2 SEED:       create MiroFish project; seed extracted_text from
                        research_report.md; simulation_requirement from prompt;
                        (if actors.json) pass as additional_context / ontology hints
  → stage 3 GRAPH:      call existing ontology+build path  (Step 1)
  → stage 4 ENV+SIM:    SimulationManager.prepare + SimulationRunner.start (Step 2-3)
  → stage 5 REPORT:     ReportManager (Step 4)
GET  /api/research/status/<id>   → unified progress across all 5 stages
```

Each stage maps onto endpoints/services that already exist; the orchestrator is
mostly glue + a `pipeline_state.json` that aggregates the sub-progress (research %,
graph %, sim round x/N, report section y/Z). A `mode` flag lets advanced users
**stop after research** (review/edit the dossier) before committing to a
simulation — recommended, because a sim is expensive and the dossier is worth a
human glance.

### 6.2 Frontend — one new wizard step ("Step 0: Research")
MiroFish's UI is a 5-step Vue wizard. Add a **Step 0** in front:

1. A single prompt box ("What do you want to predict?") + depth selector
   (quick / standard / deep) + optional file drops (passed through to DeerFlow as
   `upload_files`).
2. A live **research console** streaming DeerFlow's tool calls / sources (reuse the
   same SSE/poll + log-rendering components Step 4's ReportAgent already uses).
3. On completion: show the **dossier** (`research_report.md`) and **sources** with
   an **Edit & Continue** button. Editing writes back to `extracted_text` before
   graph build — keeps the human in the loop.
4. "Continue" flows straight into the existing Step 1→4 wizard, pre-filled.

This is purely additive: existing "upload your own PDF" flow stays as an alternate
entry. Power users can still skip research entirely.

---

## 7. Phased implementation plan

**Phase 0 — Provisioning (½ day).** `./setup.sh` now does this automatically: it
downloads the pinned `deer-flow/` into the repo, applies the `deerflow_bridge/` overlay
(`deerflow_research.py` + `patches/models/*.py` + `config.yaml` with the single
`ClaudeChatModel` entry from §5), and builds its isolated `uv` venv. DDG `web_search`
works key-free (Tavily/Exa if a key is available). Verify with
`DeerFlowClient(model_name="claude").chat("hello")`. Confirm
`~/.claude/.credentials.json` is fresh (`claude` logged in).

**Phase 1 — Research subprocess (Option C) + contract (1–2 days).**
Write `deer-flow/deerflow_research.py`: takes a prompt + out-dir, runs
`DeerFlowClient` with the `deep-research` skill, writes `research_report.md` and a
streaming `research_progress.log`. Add a structured pass (a sub-agent or a second
`chat()` with a JSON schema) to emit `actors.json` + `sources.json`. **Acceptance:**
one command turns a prompt into a populated `handoff/` dir using the Claude plan.

**Phase 2 — MiroFish orchestrator (2–3 days).** Add `research_bp` +
`PipelineOrchestrator`. `SimulationRunner`'s launch+monitor code is the template
for spawning the research subprocess and tailing progress. Wire stage 2→5 to the
existing services. **Acceptance:** `POST /api/research/run` with a prompt yields,
end to end, a prediction report — no manual file upload.

**Phase 3 — Unified UI (2–3 days).** Add Step 0 (prompt + research console +
dossier review) and a unified progress bar. **Acceptance:** the whole thing runs
from the browser with one prompt; user can stop-after-research, edit, and continue.

**Phase 4 — Fidelity & polish (done).** `actors.json` pre-seeds the **ontology**
(via `additional_context`); the structured `stance`/`influence`/`memory` fields
seed **personas** (name-matched per entity) and **per-agent simulation config**;
**`initial_posts`** are grounded in researched actor stances with `poster_name`
targeting; the "research depth" knob (quick/standard/deep) is live, and `deep`
uses a multi-pass research protocol with a depth-aware watchdog; `sources.json` provenance is surfaced in the dossier panel;
pipelines are cancellable and pre-flighted. **Still open (optional):**
stage-aware resume/continue of failed or research-only pipelines; migrate to
Gateway (Option B) for concurrency; route MiroFish LLM calls through
`ClaudeChatModel`.

---

## 8. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Dependency conflict if co-installed | high | high | **Separate venvs** (Option C/B); never `pip install deerflow` into MiroFish's env |
| Claude OAuth token expiry mid-run | med | med | `credential_loader` checks `expiresAt`; surface a clear "run `claude` to refresh" error; pre-flight check in Phase 0 |
| DeerFlow rate limits on the plan (long tool loops) | med | med | `ClaudeChatModel` already retries w/ backoff; cap research depth; allow stop-after-research |
| Research report not in the actor-centric shape MiroFish wants | med | med | Drive DeerFlow with a tailored skill/prompt; rely on `actors.json` structured pass; MiroFish's OntologyGenerator already re-normalizes toward actors |
| Cost/latency of full pipeline | high | med | Stage gating (stop-after-research), depth knob, OASIS round cap (pass `max_rounds` in the `/run` body to truncate; absent that the sim uses `OASIS_DEFAULT_MAX_ROUNDS`, default 10); show per-stage progress so long runs are legible |
| Chinese-vs-English mismatch (MiroFish prompts/sim are zh-targeted) | med | med | Have DeerFlow research in the target language, or add a translate/normalize step before seeding; set the research skill's output language |
| Two creds files drift (claude vs codex) | low | low | Standardize on Claude plan for both; document the single `~/.claude/.credentials.json` dependency |

---

## 9. Open decisions (need your call)

1. **Topology:** start with **Option C (subprocess)** as recommended, or go
   straight to **Option B (gateway sidecar)**? (C is faster to a working demo and
   reuses MiroFish's existing process machinery; B is the cleaner long-term target.)
2. **Scope now:** do you want me to **build the Phase 1 walking skeleton**
   (`deerflow_research.py` + the handoff contract, runnable today against your
   Claude plan), or keep this as design only for now?
3. **Stop-after-research gate:** include the human-in-the-loop dossier review step
   (recommended), or fully auto-run prompt→prediction with no pause?
4. **`actors.json` fidelity pass:** include it in the first build (better sims,
   a bit more work), or ship report-only seeding first and add it in Phase 4?

---

## Appendix — key source references

**DeerFlow (`deer-flow/`)**
- Embedded client: `backend/packages/harness/deerflow/client.py` (`DeerFlowClient.chat/stream/upload_files/get_artifact`)
- Claude plan auth: `…/models/claude_provider.py`, `…/models/credential_loader.py`
- Model factory / config: `…/models/factory.py`, `…/config/model_config.py`, `config.example.yaml` (`models:`, `tools:`)
- Agent + tools: `…/agents/lead_agent/{agent,prompt}.py`, `…/tools/tools.py`, `…/subagents/executor.py`
- Skills: `skills/public/deep-research/SKILL.md` (+ `github-deep-research`, `systematic-literature-review`, …)
- Gateway (Option B): `backend/app/gateway/routers/{runs,threads,uploads,artifacts}.py`

**MiroFish (repo root)** — see `ARCHITECTURE.md`
- Entry seam: `backend/app/api/graph.py::generate_ontology` (`/api/graph/ontology/generate`)
- Process+monitor template for Option C: `backend/app/services/simulation_runner.py`
- Pipeline services: `backend/app/services/*` (ontology, graph_builder, profile, sim config, report)
- LLM bridge (same Claude creds): `backend/app/services/llm_client.py`, `backend/scripts/*oasis_llm*`

---

## 10. Build Status & Runbook (Option C — implemented)

### What was built

**DeerFlow side (`deer-flow/`)** — *auto-provisioned by `./setup.sh`* (shallow-clones
`deer-flow/` into the repo from `https://github.com/bytedance/deer-flow`, trimmed to
runtime essentials — backend/, skills/, config.yaml — and pinned to a
known-good commit, then applies the **bridge overlay** from `deerflow_bridge/`:
`deerflow_research.py` → repo root; `patches/models/*.py` → the harness `deerflow/models/`
dir (`claude_provider.py` = OAuth-preference 401 fix, `credential_loader.py` = macOS
Keychain source, `patched_minimax.py` = the name-strip fix); `config.yaml` → only if
absent, never clobbering an existing one — then builds its isolated `uv` venv on Python
3.13). `git` is a prerequisite; overridable via `DEERFLOW_DIR` / `DEERFLOW_REPO` /
`DEERFLOW_REF` (set `DEERFLOW_REF=main` to track HEAD). Re-running `setup.sh` is
idempotent. So the whole integration is reproducible from a single `./setup.sh`.
- `config.yaml` — created from the template with one **active model `claude`**
  (`ClaudeChatModel`, Claude Code OAuth from `~/.claude/.credentials.json`,
  `supports_thinking`, native tool calling). DDG `web_search` + Jina `web_fetch`
  are active by default (DDG needs no key).
- `deerflow_research.py` — the subprocess bridge. `--prompt/--prompt-file
  --out-dir --model --depth {quick,standard,deep} --target-language --subagents`.
  Runs `DeerFlowClient.stream()` with the deep-research methodology, tees a
  tail-able `research_progress.log`, writes the **handoff contract**:
  `research_report.md` (required) + `prediction_requirement.txt` +
  `actors.json` + `sources.json` (best effort). Exit 0 = report produced.
  `depth=deep` is intentionally multi-pass in one thread: opening source map,
  primary-evidence sweep, actor/incentive analysis, contradiction/risk testing,
  forecast-input pass, then tool-free long-form synthesis from the accumulated
  checkpointed research.
- Its own venv (`backend/.venv`) via `uv sync` — dependency-isolated from MiroFish.

**MiroFish side (`backend/`)**
- `app/config.py` — new `DEERFLOW_*` knobs with operational defaults:
  `DEERFLOW_DIR` (auto = `./deer-flow` in the repo), `DEERFLOW_PYTHON` (auto-detect
  `deer-flow/backend/.venv` → `uv run`), `DEERFLOW_MODEL` (`claude`; one of
  `claude | minimax | deepseek | qwen | glm | codex | kimi`, see §5.2),
  `DEERFLOW_RESEARCH_DEPTH` (`standard`; quick/standard/deep),
  `DEERFLOW_RESEARCH_LANGUAGE` (`Chinese`), `DEERFLOW_RESEARCH_TIMEOUT` (unset by
  default; depth-aware budgets are quick 900s / standard 2400s / deep 10800s),
  `DEERFLOW_SUBAGENTS` (`false`) +
  `PIPELINE_DATA_DIR` (hardcoded `uploads/pipelines/`, not an env var). All have
  defaults — none are required to run. Mirrored in `.env.example`.
- `app/services/pipeline_orchestrator.py` — `PipelineOrchestrator` (daemon-thread,
  `SimulationRunner`-style subprocess monitor for research) chaining
  research → ontology → graph → prepare → run → report against the **verified**
  existing service signatures; `PipelineManager` (file-backed
  `uploads/pipelines/<id>/pipeline_state.json`); `DeerFlowResearchRunner`
  (locates DeerFlow venv: explicit `DEERFLOW_PYTHON` → `.venv` → `uv run` fallback).
  Global progress is weighted across the 6 stages; `mode="research_only"` stops
  after the dossier.
- `app/api/research.py` (`research_bp`) — `POST /run`, `GET /status/<id>`,
  `GET /list`, `GET /<id>/dossier`, `GET /<id>/progress`. Registered in
  `app/api/__init__.py` and `app/__init__.py` under `/api/research`.

**Frontend (`frontend/src/`)**
- `api/research.js` — client for the 5 endpoints.
- `views/ResearchView.vue` — Step 0 page: prompt + mode (full / research-only) +
  depth + optional max-rounds; live global progress bar, 6 stage chips, a research
  console (polls `/progress`), and a dossier panel (actors table + report). On full
  completion → "查看预测报告" routes to the existing `/report/:reportId`.
- `router/index.js` — new `/research` route.
- `views/Home.vue` — a dashed-orange button "✦ 没有资料？用一句话深度研究 → 预测"
  routing to `/research`.

### Verification status
- ✅ Frontend `npm run build` passes (679 modules, exit 0).
- ✅ All new backend modules pass `python -m py_compile`.
- ✅ Every MiroFish service signature the orchestrator calls was confirmed against
  source (ontology/graph/sim/report/task).
- ✅ DeerFlow `ClaudeChatModel` / `credential_loader` confirmed to read
  `~/.claude/.credentials.json` (Claude Code plan, native tool calling).
- ⏳ Live end-to-end run pending two environment items below.

### Prerequisites to run (environment, not code)
1. **MiroFish venv must be Python ≤3.12.** The current `backend/.venv`
   is **Python 3.13**, on which `camel-ai`/`tiktoken` fail to build (PyO3 ≤3.12).
   Recreate it: `cd backend && uv venv --python 3.12 && uv sync` (matches the
   README's "Python ≥3.11,≤3.12"). This is unrelated to the integration — the base
   MiroFish app needs it too.
2. **DeerFlow repo + venv installed**: `./setup.sh` downloads the pinned `deer-flow/`,
   applies the `deerflow_bridge/` overlay, and builds the venv automatically (heavy
   langchain/markitdown stack; use `UV_HTTP_TIMEOUT=300` on slow links). `git` is a
   prerequisite. To do it by hand instead: `cd deer-flow/backend && uv sync`.
3. **Claude Code logged in**: `~/.claude/.credentials.json` must hold a fresh
   `sk-ant-oat…` token (run `claude` once if expired). The bridge now pre-flights
   this and exits 3 with a clear message if the credential is missing/expired,
   instead of failing opaquely deep inside the research stream.

> **Run from source.** This feature shells out to
> `<DEERFLOW_DIR>/deerflow_research.py` in DeerFlow's **own** venv, so both venvs
> must exist on the host — `./setup.sh` builds them; `npm run dev` starts everything.

### How to run
```bash
# 0) one-time: ensure both venvs (see prerequisites) and DeerFlow config.yaml exist
# 1) start MiroFish (backend :5001 + frontend :3000)
npm run dev
# 2) open http://localhost:3000 → click "✦ 用一句话深度研究 → 预测"
#    enter a question → watch research console → prediction report at the end
```
Or headless, just the research stage (fastest smoke test, only needs DeerFlow venv):
```bash
cd deer-flow && backend/.venv/bin/python deerflow_research.py \
    --prompt "若某市全面放开网约车牌照，三个月内本地出租车司机群体舆情如何演变？" \
    --out-dir /tmp/handoff_test --depth quick
ls /tmp/handoff_test     # research_report.md, actors.json, sources.json, research_progress.log
```

### Known follow-ups
- Recreate the MiroFish venv on 3.12, then run one full pipeline to capture
  end-to-end evidence (the only remaining acceptance gap).
- `DEERFLOW_PYTHON` can be set explicitly in `.env` if the auto-detected venv path
  differs from `deer-flow/backend/.venv/bin/python`.
- Optional: stream research events via SSE instead of log-tail (Option B gateway)
  if concurrency grows.
