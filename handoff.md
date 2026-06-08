# Handoff — MiroFish × DeerFlow integration

**Last Updated (UTC):** 2026-06-08
**Status:** Code complete (Option C, all phases); live end-to-end run pending two env prerequisites.
**Current Focus:** verify a full prompt→prediction run after the DeerFlow venv finishes installing and a Python ≤3.12 MiroFish venv is in place.

## 1) Request
"Pull DeerFlow, understand both repos, integrate the deep-research workflow with
the MiroFish multi-agent prediction workflow into a unified interface: user submits
a prompt → deep-research agent gathers context → MiroFish builds a knowledge graph
and runs the prediction → all working on coding plans (Claude Code)."
Decisions made by user: **topology = subprocess (Option C)**, **scope = full build (all phases)**.

## 2) What exists now (all written + statically verified)
See `DEERFLOW_INTEGRATION.md` (design + §10 Build Status & Runbook) for full detail.
- DeerFlow `../deer-flow/config.yaml` (model `claude` = Claude Code OAuth) + `deerflow_research.py` (handoff contract producer).
- MiroFish `backend/app/services/pipeline_orchestrator.py`, `backend/app/api/research.py` (`/api/research/*`), config knobs in `config.py`, blueprint registered.
- Frontend `views/ResearchView.vue` (Step 0), `api/research.js`, `/research` route, Home.vue entry button.

## 3) Verification done
- ✅ Frontend `npm run build` (679 modules, exit 0).
- ✅ `python -m py_compile` on all new backend modules.
- ✅ `deerflow_research.py` pure-logic unit tests (JSON extraction incl. nested/escaped strings, prompt builders, depth presets) — PASS.
- ✅ Every MiroFish service signature the orchestrator calls verified against source.
- ✅ DeerFlow Claude-Code OAuth path confirmed in `claude_provider.py` + `credential_loader.py`.

## 4) Remaining work (env, not code)
1. **MiroFish venv is Python 3.13 → must be ≤3.12** (camel-ai/tiktoken won't build on 3.13):
   `cd backend && uv venv --python 3.12 && uv sync`.
2. **DeerFlow venv install** (`cd ../deer-flow/backend && uv sync`, slow markitdown[all] stack; `UV_HTTP_TIMEOUT=300`). In progress as of this writing.
3. **Claude Code logged in** (`~/.claude/.credentials.json` fresh).
4. Then run the headless smoke test (research only) and one full pipeline; capture evidence.

## 5) Smoke test (once DeerFlow venv ready)
```bash
cd ../deer-flow && backend/.venv/bin/python deerflow_research.py \
  --prompt "若某市全面放开网约车牌照，三个月内本地出租车司机群体舆情如何演变？" \
  --out-dir /tmp/handoff_test --depth quick
ls /tmp/handoff_test   # expect research_report.md + actors.json + sources.json + research_progress.log
```
Full pipeline: `cd MiroFish-0.1.2 && npm run dev` → http://localhost:3000 → "✦ 用一句话深度研究 → 预测".
