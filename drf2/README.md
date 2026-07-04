# DRF-2 — DeepResearchForecast on the deer-flow 2.0 super-agent harness

**Status: scaffold, pre-cutover.** This tree is the new system described in
[`REDESIGN.md`](../REDESIGN.md). The legacy system (`backend/app`, `backend/scripts`,
`deerflow_bridge/`) remains the working pipeline; nothing here replaces it until DRF-2
passes the same deliverable gates on a live end-to-end run. Read the honesty section
(§5) before assuming anything in here is wired up.

## 1. What lives where

```
drf2/
  driver/                  # Pipeline Driver — stage state machine, health gates,
                           # artifact manifest, ensemble fan-out (built separately)
  engines/
    kg/                    # KG engine: MCP server wrapping graphiti_client + zep_tools
    simulation/            # Simulation engine: OASIS job service + MCP tools
  skills/
    custom/                # the 7 deer-flow 2.0 skills (this layout matches the
                           # harness loader, which scans <skills.path>/{public,custom})
      deep-research/           # research tradecraft (ported from deerflow_bridge)
      actor-ontology-research/ # actor-centric dossier + judge loop (ported)
      ontology-generation/     # ontology methodology distilled from ontology_generator.py
      kg-construction/         # when/how to call the kg_* MCP tools
      simulation-design/       # persona/world-brief/config methodology
      forecast-report/         # 3-part Bridgewater brief + calibration rubric
      prediction-markets/      # Polymarket anchors usage (keyless public API)
  config/
    config.yaml              # harness config: models, tools, 4 custom sub-agents
    extensions_config.json   # MCP registration for the two engines + skill enablement
    market_tools.py          # config-reflected Polymarket tool (prediction_market_search, keyless)
  README.md                  # this file
```

Capability → primitive mapping, and why each piece lands where it does, is in
`REDESIGN.md` §2. Legacy modules are reused **by import** (they are Flask-free);
only the orchestration/agentic shell is being replaced.

## 2. Prerequisites

- The vendored harness source at `deer-flow-2.0.0/` (or an upstream clone) with its
  own installed environment (`cd deer-flow-2.0.0/backend && make install`).
- The legacy backend venv at `backend/.venv` (used by the two engine processes).
- FalkorDB reachable (`FALKORDB_HOST` / `FALKORDB_PORT`) for the KG engine.
- Env vars: `MINIMAX_API_KEY` (MiniMax-M3 stages) and claude-cli credentials
  for `ClaudeChatModel` (it auto-loads `$ANTHROPIC_API_KEY` →
  `$CLAUDE_CODE_OAUTH_TOKEN` → `~/.claude/.credentials.json`).
  Prediction-market anchors use Polymarket's keyless public API — no key needed.
- `PYTHONPATH` for the harness process must include this repo root so the
  config-reflected tool `drf2.config.market_tools` resolves.

## 3. Running DRF-2

### 3.1 Start the engines (their own processes, not sandboxes)

The two engines are **stdio MCP servers**: the harness spawns them per
`extensions_config.json` (command = `backend/.venv/bin/python -m
drf2.engines.kg.server` / `-m drf2.engines.simulation.server`). You do not start
them by hand for stdio mode — but FalkorDB must already be up, the engines'
requirements installed (`backend/.venv/bin/pip install -r
drf2/engines/kg/requirements.txt -r drf2/engines/simulation/requirements.txt`),
and the env vars in `extensions_config.json` must resolve. If the engines are
deployed as HTTP MCP services instead, change each server entry to
`"type": "http"` with its `url`.

Tool surface (pinned by `backend/tests/test_drf2_skills_config.py` against the
engine sources): KG — `kg_add_episode`, `kg_search`, `kg_get_entities`,
`kg_get_edges`, `kg_causal_paths`, `kg_n_hop_subgraph`, `kg_trace_cascade`,
`kg_centrality_priors`; simulation — `sim_start`, `sim_status`, `sim_results`,
`sim_stop`, `sim_interview_agents`.

### 3.2 Start the harness with this config

```bash
cd deer-flow-2.0.0
export DEER_FLOW_CONFIG_PATH=/Users/rogerlin/Downloads/DeepResearchForecast/drf2/config/config.yaml
export DEER_FLOW_EXTENSIONS_CONFIG_PATH=/Users/rogerlin/Downloads/DeepResearchForecast/drf2/config/extensions_config.json
export PYTHONPATH=/Users/rogerlin/Downloads/DeepResearchForecast:$PYTHONPATH
make dev        # gateway :8001, UI :3000, nginx :2026
```

(Or copy the two config files to the deer-flow project root instead of exporting
the path vars. `config.yaml` pins `skills.path` to this repo's `drf2/skills` with
an absolute path — adjust it if the repo lives elsewhere.)

### 3.3 Drive a forecast

Two ways:

- **Chat (exploratory):** open `http://localhost:2026`, enable subagents, and ask
  the lead agent to run the pipeline. The four stages are the custom sub-agents in
  `config.yaml` (`researcher` → `ontology-builder` → `sim-configurer` →
  `forecaster`), each with per-stage model routing, skill whitelists, tool
  whitelists, and raised timeouts (research 2700s, sim-config 900s, forecast 2700s).
  You can also activate a skill directly with `/deep-research <question>` etc.
- **Driver (deterministic pipeline):** the Pipeline Driver in `drf2/driver/` runs
  the stage state machine against ONE persistent thread via the embedded
  `DeerFlowClient` / Runs API, and calls the engine tools **directly** for the
  health gates (research quality floor, hollow-sim gate, binary conviction gate)
  — gates are never LLM-mediated. See `drf2/driver/` for its entry point and the
  stage/gate contract once that component lands.

### 3.4 The intended stage flow

1. **researcher** (claude-sonnet, 45 min budget) — `deep-research` +
   `actor-ontology-research` skills → `research_report.md`, `actor_dossier.md`
   (+ optional Prediction Market Signals via `prediction_market_search`).
2. **ontology-builder** (minimax-m3) — `ontology-generation` + `kg-construction`
   skills → writes `ontology.json` (the driver applies it to the graph), ingests
   chunked episodes (`kg_add_episode`), verifies resolution/causal paths.
3. **sim-configurer** (minimax-m3, 15 min budget) — `simulation-design` skill →
   `simulation_config.json` → `sim_start` (returns a sim_id; the 60–120 min OASIS
   run is a detached job inside the simulation engine, **never** a sub-agent turn).
4. **forecaster** (claude-sonnet, 45 min budget) — `forecast-report` +
   `prediction-markets` skills, reading `sim_results` / `sim_interview_agents` →
   `full_report.md` (3-part brief, ≥10 calibrated binaries).

## 4. Verifying this scaffold offline

```bash
cd backend
.venv/bin/python -m pytest tests/test_drf2_skills_config.py -v
```

The test parses every `drf2/skills/**/SKILL.md` with the **actual** deer-flow 2.0
parser/validator (path-injected from `deer-flow-2.0.0/`; skipped cleanly if that
tree is absent), validates `config.yaml` structure + `$ENV` placeholders +
sub-agent wiring, validates `extensions_config.json`, and unit-tests the Polymarket
tool's pure logic with mocked fetches. No network, no LLM, no engines needed.

### Verified import paths

Smoke-verified (quality gate, 2026-07-03) from the repo root
(`/Users/rogerlin/Downloads/DeepResearchForecast`) using the backend venv:

```bash
cd /Users/rogerlin/Downloads/DeepResearchForecast
PYTHONPATH=/Users/rogerlin/Downloads/DeepResearchForecast:/Users/rogerlin/Downloads/DeepResearchForecast/backend \
  backend/.venv/bin/python -c "import drf2.driver.cli, drf2.engines.kg.server, drf2.engines.simulation.server"
```

Resolution notes:
- All three modules import cleanly with **only the repo root** on `PYTHONPATH`
  (`PYTHONPATH=<repo-root>`); none of them requires `backend/` on the path at
  import time. When run with `python -c` from the repo root, even a bare
  invocation works because the cwd is added to `sys.path` automatically.
- Keep `<repo-root>:backend` on `PYTHONPATH` anyway (as in
  `extensions_config.json`) so engine code that reaches into `app.*` at
  runtime resolves the same way it does under pytest.

## 5. Current status — honesty section

Done in this tree:
- 7 skills in genuine 2.0 format (frontmatter passes the upstream validator;
  methodology ported/distilled from the proven legacy prompts and rubrics).
- Harness `config.yaml` (schema-compatible with upstream `config_version: 14`),
  `extensions_config.json`, and the `prediction_market_search` config-reflected tool.

**Not yet true / TODO for cutover:**
- Engine entry points and MCP tool names are reconciled against the engine
  implementations as of this commit and pinned by drift-guard tests
  (`TestEngineToolNameDrift`) — if the engines rename tools, the tests go red
  first; re-sync the skills' `allowed-tools` and the sub-agents' `tools` lists.
- Engine env contracts (FalkorDB/Graphiti/LLM keys) in `extensions_config.json`
  are a best-guess passthrough; finalize against the engines' real config surface
  (see `drf2/engines/*/README.md`).
- Absolute paths in `config.yaml` (skills.path) and `extensions_config.json`
  (venv python, PYTHONPATH) are pinned to this machine's checkout; parametrize or
  re-point at deployment.
- No live end-to-end run has been performed through the harness; the deliverable
  gates (research floor, hollow-sim, conviction) live in the driver and are not
  exercised by the offline tests.
- The legacy pipeline remains authoritative until parity (REDESIGN.md §5).
