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
| `config.yaml` | A complete, ready‑to‑use DeerFlow config with active stanzas for **claude / minimax / deepseek / qwen / glm / codex**. All keys are `$VAR` references resolved from `.env` — **no secrets**. | → `deer-flow/config.yaml` (only if absent; never clobbers an existing one). |
| `config.minimax.snippet.yaml` | Just the `minimax` model stanza, for pasting into an existing `deer-flow/config.yaml` by hand. | (manual merge helper) |

## Automated install (recommended)

From the project root:

```bash
./setup.sh
```

It clones DeerFlow as a **sibling** (`../deer-flow`, pinned to a known‑good commit), drops the
research driver in, applies the three provider patches, installs `config.yaml` if there isn't
one, and builds DeerFlow's isolated venv. Re‑running is safe (idempotent). Overrides:

- `DEERFLOW_DIR` — where to put / find the checkout (default: `../deer-flow`).
- `DEERFLOW_REPO` — clone URL (default: the upstream ByteDance repo).
- `DEERFLOW_REF` — commit/branch to pin (default: the commit these patches target; set
  `DEERFLOW_REF=main` to track upstream HEAD instead).

## Manual install (equivalent steps)

```bash
# 1. Clone DeerFlow as a SIBLING of this project (the backend auto-detects ../deer-flow)
git clone https://github.com/bytedance/deer-flow ../deer-flow

# 2. Drop the bridge entry point in
cp deerflow_bridge/deerflow_research.py ../deer-flow/deerflow_research.py

# 3. Apply the provider patches
cp deerflow_bridge/patches/models/*.py \
   ../deer-flow/backend/packages/harness/deerflow/models/

# 4. Install the ready-to-use config (keys come from .env via $VAR)
cp deerflow_bridge/config.yaml ../deer-flow/config.yaml

# 5. Build DeerFlow's isolated venv (Python >= 3.12; 3.13 recommended)
UV_PROJECT_ENVIRONMENT=../deer-flow/backend/.venv uv sync --project ../deer-flow/backend --python 3.13
```

The backend finds DeerFlow via `DEERFLOW_DIR` (defaults to the sibling `../deer-flow`) and the
research model via `DEERFLOW_MODEL` (`claude | minimax | deepseek | qwen | glm | codex`). See the
main `README.md` and `DEERFLOW_INTEGRATION.md` for the full contract.

## Notable bridge hardening (in `deerflow_research.py`)

- **Pre-flight credential check** (fails fast with a clear message when a `claude` model has no valid OAuth/API key).
- **LLM-error guard** — a degraded provider message (rate limit, `422 new_sensitive`, `400 bad_request`, connection error) is never mistaken for a real research report; the run fails fast instead of contaminating the pipeline.
- **Tool-free synthesis net** — if the research agent exhausts its step budget on tool calls before writing, *or* hits a provider **structural** error on the final write (e.g. MiniMax `400 user name must be consistent`), the report is synthesized directly from the gathered, thread-checkpointed research via a clean single-turn call. (Only a genuine content-moderation block is excluded, since re-sending the same content would just be blocked again.)
- **Longer, richer reports** — a higher synthesis trigger and a model‑aware context cap (up to ~900K chars for million‑token models: minimax / qwen / deepseek) with explicit length mandates in the research/synthesis prompts.
- **`<think>` stripping** for reasoning models (MiniMax‑M3 inlines its chain‑of‑thought).
