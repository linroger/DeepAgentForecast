# DeerFlow bridge

DeepResearchForecast's **deep-research stage** (stage 1 of the pipeline) is powered by
[DeerFlow](https://github.com/bytedance/deer-flow), a LangGraph-based research super‑agent.
DeerFlow runs in **its own Python environment** (its `langchain`/`langgraph` dependency tree is
isolated from this backend) and is invoked as a **subprocess** that writes a file‑based
"handoff contract" the backend consumes.

This folder is the **single source of truth** for that integration, so the whole bridge is
reproducible from this repo. `./setup.sh` (at the project root) clones DeerFlow and applies
everything here automatically — you normally never touch these files by hand.

| File | What it is | Where `setup.sh` puts it |
|---|---|---|
| `deerflow_research.py` | The bridge entry point. Runs a DeerFlow research turn for a prompt and writes `research_report.md`, `actors.json`, `sources.json`, `prediction_requirement.txt`, `meta.json`, `research_progress.log` into an output dir. | → **root** of the `deer-flow` checkout. |
| `patches/models/claude_provider.py` | `ClaudeChatModel` with **OAuth‑preference** (prefers a Claude Code OAuth credential over an ambient non‑OAuth `ANTHROPIC_API_KEY`, fixing stray‑key 401s) and a 0.5 thinking‑budget ratio. | → `deer-flow/backend/packages/harness/deerflow/models/` |
| `patches/models/credential_loader.py` | Adds a **macOS Keychain** credential source (`security find-generic-password -s "Claude Code-credentials"`) so the local `claude` OAuth token is found even when it isn't in `~/.claude/.credentials.json`. | → same `models/` dir |
| `patches/models/patched_minimax.py` | `PatchedChatMiniMax` — strips the per‑message `name` field that DeerFlow middlewares inject, fixing MiniMax `400 user name must be consistent`; keeps tools + reasoning **on**. | → same `models/` dir |
| `skills/deep-research/SKILL.md` | **Overhauled deep-research skill** (replaces the generic upstream one). Adds a source-quality framework — S1 primary/authoritative → S4 reject (SEO farms, aggregator slop, undated/anonymous pages) — 8 signal heuristics applied *before* fetching, circular-sourcing detection (ten echoes of one report = one source), mandatory triangulation of load-bearing claims, disconfirmation searches, a synthesis gate, and tool-budget discipline tuned to the per-run `web_search`/`web_fetch` limits. | → `deer-flow/skills/public/deep-research/SKILL.md` |
| `patches/middlewares/loop_detection_middleware.py` | Loop detection with **per‑run counter resets**. Upstream accumulates per‑tool call counts across *all* turns of a thread, so multi‑pass deep research permanently force‑stops `web_search` from pass 2 onward (`[FORCED STOP] Tool web_search called N times…`) once the cumulative count crosses the limit. The patch resets the budget at the start of each agent run — full in‑run loop protection stays intact. | → `deer-flow/backend/packages/harness/deerflow/agents/middlewares/` |
| `config.yaml` | A complete, ready‑to‑use DeerFlow config with active stanzas for **claude / minimax / deepseek / qwen / glm / codex / kimi**. All keys are `$VAR` references resolved from `.env` — **no secrets**. Bridge‑tuned: conversation **memory off** (no cross‑run fact contamination), **title generation off** (headless runs), summarization trigger raised to **120K tokens** (research keeps full source detail). | → `deer-flow/config.yaml` (only if absent; never clobbers an existing one — diff against this copy to pick up new stanzas/tuning). |
| `config.minimax.snippet.yaml` | Just the `minimax` model stanza, for pasting into an existing `deer-flow/config.yaml` by hand. | (manual merge helper) |

## Automated install (recommended)

From the project root:

```bash
./setup.sh
```

It downloads DeerFlow into the repo (`deer-flow/`, pinned to a known‑good commit, gitignored), drops the
research driver in, applies the three provider patches + the middleware patch, installs the
overhauled deep-research skill, installs `config.yaml` if there isn't one, and builds DeerFlow's
isolated venv. Re‑running is safe (idempotent). Overrides:

- `DEERFLOW_DIR` — where to put / find the checkout (default: `./deer-flow` in the repo root).
- `DEERFLOW_REPO` — clone URL (default: the upstream ByteDance repo).
- `DEERFLOW_REF` — commit/branch to pin (default: the commit these patches target; set
  `DEERFLOW_REF=main` to track upstream HEAD instead).

## Manual install (equivalent steps)

```bash
# 1. Clone DeerFlow into the repo root (the backend auto-detects ./deer-flow)
git clone https://github.com/bytedance/deer-flow deer-flow

# 2. Drop the bridge entry point in
cp deerflow_bridge/deerflow_research.py deer-flow/deerflow_research.py

# 3. Apply the provider + middleware patches and the overhauled skill
cp deerflow_bridge/patches/models/*.py \
   deer-flow/backend/packages/harness/deerflow/models/
cp deerflow_bridge/patches/middlewares/*.py \
   deer-flow/backend/packages/harness/deerflow/agents/middlewares/
cp deerflow_bridge/skills/deep-research/SKILL.md \
   deer-flow/skills/public/deep-research/SKILL.md

# 4. Install the ready-to-use config (keys come from .env via $VAR)
cp deerflow_bridge/config.yaml deer-flow/config.yaml

# 5. Build DeerFlow's isolated venv (Python >= 3.12; 3.13 recommended)
UV_PROJECT_ENVIRONMENT=deer-flow/backend/.venv uv sync --project deer-flow/backend --python 3.13
```

The backend finds DeerFlow via `DEERFLOW_DIR` (defaults to `./deer-flow` in the repo) and the
research model via `DEERFLOW_MODEL` (`claude | minimax | deepseek | qwen | glm | codex | kimi`). See the
main `README.md` and `DEERFLOW_INTEGRATION.md` for the full contract.

## Notable bridge hardening (in `deerflow_research.py`)

- **Pre-flight credential check for every model** — runs BEFORE the DeerFlow client/config is constructed: `claude` (OAuth token present/fresh), `codex` (`~/.codex/auth.json`), and each API model (`kimi`/`minimax`/`deepseek`/`qwen`/`glm` → its `$KEY` env var). Fails fast (exit 3) with the exact variable to set instead of an opaque traceback mid-research.
- **Provider-key env hygiene** — DeerFlow's config loader greedily resolves every `$VAR` in `config.yaml`, so one unset key used to crash even the default claude path on standalone runs; the bridge now presets empty defaults for all known provider key vars (MiroFish's backend does the same before spawning it).
- **LLM-error guard** — a degraded provider message (rate limit, `422 new_sensitive`, `400 bad_request`, connection error) is never mistaken for a real research report; the run fails fast instead of contaminating the pipeline.
- **Tool-free synthesis net** — if the research agent exhausts its step budget on tool calls before writing, *or* hits a provider **structural** error on the final write (e.g. MiniMax `400 user name must be consistent`), the report is synthesized directly from the gathered, thread-checkpointed research via a clean single-turn call. (Only a genuine content-moderation block is excluded, since re-sending the same content would just be blocked again.)
- **Multi-pass `deep` research** — `quick` and `standard` remain single-turn research runs, while `deep` now runs a staged protocol in one thread: opening source map → primary evidence sweep → actor/incentive analysis → contradiction/risk testing → forecast-input pass → tool-free long-form synthesis. This makes deep runs slower, but much more detailed and less likely to stop after a short generic dossier.
- **Longer, richer reports** — a higher synthesis trigger and a model‑aware context cap (up to ~900K chars for million‑token models: minimax / qwen / deepseek) with explicit length mandates in the research/synthesis prompts. Deep synthesis targets an 8,000–12,000 word evidence-backed dossier when the model can support it.
- **`<think>` stripping** for reasoning models (MiniMax‑M3 inlines its chain‑of‑thought).
