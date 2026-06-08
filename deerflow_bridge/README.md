# DeerFlow bridge

DeepResearchForecast's **deep-research stage** (stage 1 of the pipeline) is powered by
[DeerFlow](https://github.com/bytedance/deer-flow), a LangGraph-based research super‑agent.
DeerFlow runs in **its own Python environment** (its `langchain`/`langgraph` dependency tree is
isolated from this backend) and is invoked as a **subprocess** that writes a file‑based
"handoff contract" the backend consumes.

This folder contains the integration artifacts so the bridge is reproducible:

| File | What it is | Where it goes |
|---|---|---|
| `deerflow_research.py` | The bridge entry point. Runs a DeerFlow research turn for a prompt and writes `research_report.md`, `actors.json`, `sources.json`, `prediction_requirement.txt`, `meta.json`, `research_progress.log` into an output dir. | Copy to the **root of your `deer-flow` checkout**. |
| `config.minimax.snippet.yaml` | The `minimax` model stanza (MiniMax‑M3, OpenAI‑compatible). | Paste into `deer-flow/config.yaml` under `models:` (only needed if you use the `minimax` provider). |

## Install

```bash
# 1. Clone DeerFlow as a SIBLING of this project (the backend auto-detects ../deer-flow)
git clone https://github.com/bytedance/deer-flow ../deer-flow

# 2. Drop the bridge in
cp deerflow_bridge/deerflow_research.py ../deer-flow/deerflow_research.py

# 3. (Optional, for the minimax provider) add the model to DeerFlow's config
cat deerflow_bridge/config.minimax.snippet.yaml   # paste under models: in ../deer-flow/config.yaml

# 4. Build DeerFlow's isolated venv (Python >= 3.12; 3.13 recommended)
UV_PROJECT_ENVIRONMENT=../deer-flow/backend/.venv uv sync --project ../deer-flow/backend --python 3.13
```

The backend finds DeerFlow via `DEERFLOW_DIR` (defaults to the sibling `../deer-flow`) and the
research model via `DEERFLOW_MODEL` (`claude` or `minimax`). See the main `README.md` and
`DEERFLOW_INTEGRATION.md` for the full contract.

## Notable bridge hardening (in `deerflow_research.py`)

- **Pre-flight credential check** (fails fast with a clear message when a `claude` model has no valid OAuth/API key).
- **LLM-error guard** — a degraded provider message (rate limit, `422 new_sensitive`, `400 bad_request`, connection error) is never mistaken for a real research report; the run fails fast instead of contaminating the pipeline.
- **Tool-free synthesis net** — if the research agent exhausts its step budget on tool calls before writing, *or* hits a provider **structural** error on the final write (e.g. MiniMax `400 user name must be consistent`), the report is synthesized directly from the gathered, thread-checkpointed research via a clean single-turn call. (Only a genuine content-moderation block is excluded, since re-sending the same content would just be blocked again.)
- **`<think>` stripping** for reasoning models (MiniMax‑M3 inlines its chain‑of‑thought).
