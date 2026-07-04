# DRF-2: DeepResearchForecast rebuilt around the DeerFlow 2.0 super-agent harness

**Branch:** `deerflow2-redesign` · **Date:** 2026-07-03 · **Basis:** 7-agent deep-read of
`deer-flow-2.0.0/` (upstream `bytedance/deer-flow@2.0.x-dev`) + full inventory of the current
46K-LOC backend.

---

## 1. The verdict: hybrid rebuild — and why

**Does it make sense to fold mirofish, local Graphiti, ontology, the KG, and the multi-agent
simulation into DeerFlow 2.0? Partially — and the parts matter.**

DeerFlow 2.0 is a chat-first **super-agent harness** (lead agent + one layer of sub-agents +
skills + sandboxes + MCP client), not a workflow engine. The studies established hard facts
that dictate the split:

| Harness fact (evidence) | Consequence for DRF |
|---|---|
| Sub-agents are one-shot, `checkpointer=False`, default 30-min timeout, max **3 concurrent**, results returned as plain strings (`executor.py:360,858`; `contracts/subagent_status_contract.json`) | The 80-agent, 60–120-min OASIS simulation **cannot** be sub-agents or live inside a sub-agent turn |
| Sandbox `bash` has a 600s exec ceiling and 600s idle-destroy | The simulation cannot run as a skill script either |
| Memory = per-user JSON doc + LangGraph checkpointer, 2000-token prompt injection, no query API (`agents/memory/storage.py`) | Graphiti/FalkorDB must **not** be shoehorned in as the memory backend — it stays an external temporal KG |
| Harness is an excellent **MCP client** (OAuth, deferred `tool_search` for big catalogs, tool-output externalization) but has **no MCP server mode** | Our KG and sim engines are exposed **to** the harness as MCP servers; the harness is the consumer |
| Skills are prompt/methodology directories (+ scripts run via sandbox bash), enforced tool whitelists; `deerflow_bridge/skills/*` already proves our research methodology ports 1:1 | All **knowledge-shaped** stages (research, ontology method, sim design, forecast rubric) become skills |
| Provider layer ships `ClaudeChatModel` (claude-cli subscription auth!) and `PatchedChatMiniMax` (reasoning_split, name-field fix), per-sub-agent model routing | Our hand-rolled `llm_client.py` provider/failover mess is largely **subsumed** for agentic stages |
| Lead-agent graph is hardcoded; no custom graph without forking; runs are in-memory asyncio tasks, cancel-on-disconnect by default | Deterministic pipeline semantics (stage state machine, resume, health gates, ensemble fan-out) must live in **our** thin driver, not in the harness |
| Current services are already Flask-free (0 flask imports under `services/`, `utils/`, `scripts/`) | The capability core ports as-is; only the 4.2K-LOC orchestrator + Flask shell is replaced |

**So:** rebuild the *orchestration and agentic* layer on the harness; keep the *heavy engines*
(KG, simulation) as external services the harness calls via MCP; port *methodology* as skills;
preserve the *deterministic calibration/health machinery* in a thin driver. Nothing gets
shoehorned; each capability lands on the primitive that actually fits it.

## 2. Target architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  deer-flow 2.0 harness (gateway :8001, Next.js UI, IM channels)         │
│  lead agent ── task ──► custom sub-agents (config.yaml):                │
│     researcher · ontology-builder · sim-configurer · forecaster         │
│     (per-stage model routing: MiniMax-M3 cheap / claude-cli strong)     │
│  skills (/mnt/skills): deep-research · actor-ontology-research ·        │
│     ontology-generation · kg-construction · simulation-design ·         │
│     forecast-report · prediction-markets                                │
│  MCP client (extensions_config.json, deferred tool_search)              │
└──────────┬──────────────────────────────┬───────────────────────────────┘
           │ MCP (stdio/HTTP)             │ MCP (stdio/HTTP)
┌──────────▼─────────────┐   ┌────────────▼────────────────────────────────┐
│ KG engine (ours)       │   │ Simulation engine (ours)                    │
│ Graphiti + FalkorDB    │   │ OASIS job service: start_simulation,        │
│ add_episode, search,   │   │ sim_status, sim_results, interview_agents   │
│ causal_paths, n_hop,   │   │ (runs run_parallel_simulation.py in its own │
│ trace_cascade,         │   │ process; file contracts unchanged:          │
│ centrality, entities   │   │ simulation_config.json / actions.jsonl /    │
│ (wraps zep_tools —     │   │ run_summary.json)                           │
│ already tool-shaped)   │   │                                             │
└────────────────────────┘   └─────────────────────────────────────────────┘
           ▲                              ▲
┌──────────┴──────────────────────────────┴───────────────────────────────┐
│ Pipeline Driver (ours, ~500 LOC replacing 4,200)                        │
│ drives the harness via embedded DeerFlowClient / Runs API against ONE   │
│ persistent thread; owns: stage state machine + schema-versioned resume, │
│ artifact manifest + hash-verified reuse, health gates (research quality │
│ floor, hollow-sim gate, binary conviction gate, deliverable validation),│
│ heartbeat/orphan reclaim, multi-seed ensemble fan-out + log-odds pool   │
│ — gates call engine endpoints DIRECTLY (deterministic), never via LLM   │
└──────────────────────────────────────────────────────────────────────────┘
```

### Capability → primitive mapping

| Current module | Destination | Rationale |
|---|---|---|
| `deerflow_bridge/skills/*` (research methodology) | **2.0 skills**, near-verbatim | Already proven; the 1.x subprocess bridge (`deerflow_research.py`, 3.7K LOC) retires once research runs as native lead-agent work |
| `ontology_generator` + `actors.py` | **skill** (methodology) + **config-reflected tools** (deterministic dossier algebra) | Prompt-and-tool work; `actors.py` is a pure function library |
| `graphiti_client/*`, `graph_builder`, `zep_entity_*`, `zep_tools` | **KG engine + MCP server** | `zep_tools` already returns `to_text()` tool payloads; heavy deps (FalkorDB, sentence-transformers) need a persistent process |
| `run_parallel_simulation.py`, `simulation_runner`, `agent_dynamics`, profile/config generators | **Simulation engine + MCP tools** | Multi-hour stateful compute; keeps world-brief injection, persona_design, checkpoint/resume |
| `report_agent` ReACT + `forecast_extractor` rubric | **forecast-report skill** + forecaster sub-agent; deterministic extractors as config-reflected tools | The methodology is prompt; the conviction gate/ledger is code |
| `prediction_markets` (Polymarket, keyless) | config-reflected **tool** + thin skill | Simple HTTP tool |
| `worldstate`, `decision_channel`, `ensemble`, `backtest`, `forecast_ledger` | **Driver-side deterministic modules** (unchanged) | Calibration math must not be LLM-mediated |
| `llm_client.py` provider/failover | **Harness model layer** for agentic stages (ClaudeChatModel / PatchedChatMiniMax); engines keep a slim client for their internal calls | Deletes the worst-duplicated subsystem |
| `pipeline_orchestrator.py` (4.2K), Flask API, `TaskManager` | **Replaced** by Pipeline Driver + harness Runs API | The studies' unanimous "replaceable shell" |

### What the harness does NOT give us for free (driver must keep)

Durable per-stage state with resume/fork; artifact manifests with hash-verified stage reuse;
crash-safe heartbeats + orphan reclaim (incl. cmdline-verified subprocess kills); cancellation
that pierces defensive except-layers; health gates; multi-seed ensemble fan-out with log-odds
pooling. Every one of these exists because of a documented real failure — they move into the
driver, not into prompts.

## 3. Repo layout (new, on this branch)

```
drf2/                        # the new system (grows here; legacy untouched until parity)
  driver/                    # Pipeline Driver (state machine, gates, manifest, ensemble)
  engines/
    kg/                      # MCP server wrapping graphiti_client + zep_tools
    simulation/              # job service + MCP tools wrapping the OASIS runner
  skills/                    # 2.0-format skills (7)
  config/
    config.yaml              # harness config: models (claude-cli, MiniMax-M3), custom_agents
    extensions_config.json   # MCP registrations for the two engines
  README.md                  # how to run DRF-2 end to end
```

Legacy `backend/` remains the working system during migration; `drf2/` reuses its modules by
import (services are Flask-free). Cut-over happens when DRF-2 passes the same deliverable
gates on a live run.

## 4. Cleanup (executed on this branch)

**Tracked deletions:** `backend/app/utils/retry.py` + `token_budget.py` (zero importers),
`backend/scripts/run_twitter_simulation.py` + `run_reddit_simulation.py` (superseded by
`run_parallel_simulation.py`; runner invokes only the parallel script), stale planning blobs
(`EXECPLAN*.md`, `CODEX_*.md`, `CLAUDE_ONTOLOGY.md`, `CLAUDE_REPORT.md` — 900KB+ of session
notes), `reference/` (13 tracked files, superseded by the vendored 2.0 source).
**Local-only junk (not tracked; listed for the operator):** `deer-flow/` (962MB regenerable),
`graphiti-0.29.2/` (23MB reference), `log/` (zero-byte debris), `SwiftMandarin/` (stray files
from an unrelated project), `logs/` (117MB transcripts — may contain secrets; operator decides).
**Config drift fixed:** `setup.sh` `DEERFLOW_VENDOR_DIR` default points at the removed
`deer-flow-2.0-m1-rc3`; repointed at `deer-flow-2.0.0` with upstream-clone fallback intact.

## 5. Migration sequencing

1. **This branch:** scaffold `drf2/` (engines' MCP servers + driver skeleton + skills + config),
   delete dead code, keep 584-test suite green (legacy untouched).
2. Live shakedown: run one forecast end-to-end through DRF-2 (research→ontology→KG→sim→report)
   against the same deliverable gates.
3. Parity reached → retire `pipeline_orchestrator.py`, Flask API routes, `deerflow_bridge/`
   subprocess plumbing, and the duplicated provider stack in a follow-up PR.
