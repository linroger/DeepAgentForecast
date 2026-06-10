# Handoff — MiroFish × DeerFlow integration

**Last Updated (UTC):** 2026-06-09T20:08:21Z
**Status:** Integration hardening, Zep 429 failure fix, and deeper DeerFlow multi-pass research mode complete; live full prompt→prediction rerun not launched because it spends live LLM/Zep/OASIS quota.
**Current Focus:** if continuing, run a controlled `depth=deep` research-only forecast to inspect report length/detail before spending on the full simulation/report stages.

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

---

## SESSION 2026-06-10 (5th pass) — Deep-research FORCED STOP starvation fix

**Symptom (user screenshot):** from pass 2 of deep research, every turn logged `[FORCED STOP] Tool web_search called 52/53/54 times — exceeded the per-tool safety limit`, degrading passes 2-7 to no-research summaries, with the notices leaking into the dossier.

**Root cause:** DeerFlow's `LoopDetectionMiddleware` keeps per-tool frequency counters scoped to the *thread* and never resets them between agent runs. The multi-pass protocol runs 6-8 turns on one thread; passes 0-1 legitimately burn ~50 web searches, after which the cumulative counter permanently force-stops `web_search`. (Upstream bug for ANY long thread, not just our protocol — the warning text itself says "without producing a final answer", contradicting cross-run accumulation.)

**Fix (commit `fccdde9`):**
1. Overlay patch `deerflow_bridge/patches/middlewares/loop_detection_middleware.py` — `before_agent`/`abefore_agent` reset the per-thread hash window + tool-frequency counters, so each run gets a fresh budget while in-run protection is unchanged. setup.sh applies it (new b2 step). All 65 upstream middleware tests pass + functional two-run regression (run1 hard-stops at its limit; run2 starts fresh; run2 still self-protects).
2. `deerflow_bridge/config.yaml`: `loop_detection.tool_freq_overrides` — web_search/web_fetch warn 60 / hard 100 (per-run). Synced to ../deer-flow (config verified to flow into middleware via from_config).
3. `deerflow_research.py` `strip_think` also strips `[FORCED STOP]`/`[LOOP DETECTED]` lines so harness notices never enter the dossier.
4. Regression checks in `test_deerflow_deep_research.py` (patch presence, deploy sync, marker stripping); troubleshooting rows in both READMEs.

**No backend restart needed** — research runs in a fresh subprocess per pipeline, so the patched middleware + config apply to the next run.
**NOTE for the cancelled semiconductor run:** its research stage completed UNDER the bug (passes 2+ starved), so Resume would reuse the degraded dossier. For full research quality, delete it and start a fresh run.
**Mishap & recovery:** first commit attempt accidentally landed in the ../deer-flow clone (cd in a chained command); `git reset --mixed 799bef6d` restored it to the pinned commit with the overlay back as working-tree files; push to upstream had been refused (403) so nothing leaked.

## SESSION 2026-06-10 (4th pass) — Full-workflow demo walkthroughs + bilingual site

**Request:** the live demos must show every workflow stage (deep research log, research brief, ontology, knowledge graph, forum, final report), and the site must be bilingual (EN/中文).

**Shipped:**
1. **`backend/scripts/export_demo_site_data.py`** — reproducible exporter dumping per run: `research_log.txt`, `dossier.md`, `actors.json`/`sources.json` (when present), `ontology.json` (entity/edge types + analysis summary from project.json), `graph.json` (Zep nodes+links, trimmed), `forum.json` (parsed from twitter/reddit actions.jsonl), `report.md` (placeholder-failure sections stripped generically), `meta.json` (prompt/rounds/personas from run_state + sim config).
2. **Zep graphs had to be REBUILT**: the original graphs 404 on the current Zep account (account/plan rotated — limit now 300/min, only 2 unrelated graphs existed). Exporter detects 404 → re-runs stage 3 on identical inputs (saved dossier + ontology, same chunking) → exports the result. us-ai-2030: 61 nodes/95 links; ev-2035: 74/100; russia-ukraine: 128 chunks (largest dossier).
3. **demo.html rewritten** as a 6-tab stage walkthrough: ① research console log (terminal style) ② dossier with actor cards (role/stance/influence) + cited sources (graceful no-actors fallback for the older Russia run) ③ ontology cards ④ interactive force-graph (force-graph CDN, hover tooltips with extracted facts) ⑤ forum feed (Twitter/Reddit switch, post/comment/like stats, avatar feed) ⑥ final report. Per-tab lazy fetch + cache; language switch re-renders the active tab.
4. **i18n**: `docs/i18n.js` (~70 keys, EN/中文), `data-i18n` attributes, `.lang-toggle` in both navs, localStorage persistence, browser-language default. index.html fully retrofitted.
5. Verified in Chrome throughout: all six tabs on multiple runs, zh/EN toggle on both pages, graph force-layout, no console errors. The user's own live run (semiconductor) was cancelled by them mid-session via the new cancel button — observed working.

## SESSION 2026-06-10 (later still) — Run management + live demo site

**Request:** Screenshots folder pointer (already covered — those raw originals were previously optimized into docs/media); add a cancel button (one already existed in the run header; added per-run controls to the history drawer); clean the failed runs; create a live demo site and link it from the GitHub repo description.

**Shipped (commit `87cc668`):**
1. **Run management.** `DELETE /api/research/<id>` (terminal-only, path-traversal-safe, refuses live runs under the lifecycle lock) and `POST /api/research/clean` (bulk failed+cancelled, never touches running/completed). History drawer got per-run **Cancel** (running) / **Delete** (terminal) buttons + a **Clear failed (N)** bulk button; deleting the active run resets the view. Regression checks added (delete/clean/traversal/live-protection) — suite now 8 checks, all pass.
2. **Cleaned this machine:** all 12 failed pipelines purged via the new clean path; the 4 completed runs kept.
3. **Live demo site** (GitHub Pages, `main:/docs`): `docs/index.html` (hero, run cards with verified stats from run_state.json, demo video, screenshots, pipeline overview, quickstart) + `docs/demo.html?run=<key>` (marked.js viewer with Forecast/Dossier tabs) + `docs/demos/{us-ai-2030,ev-2035,russia-ukraine}/{report,dossier}.md` — unedited artifacts from the 3 good completed runs (the 4th, pipe_8bd4981639ac, had a placeholder-failure report from the pre-fix era and was excluded; one failed section was trimmed from the us-ai-2030 demo copy). Verified locally in Chrome (render + tabs + no console errors) and live post-deploy.
4. **Repo metadata:** description + homepage now carry https://linroger.github.io/DeepResearchForecast/ ; READMEs link the live site and document the delete/clean endpoints (EN + zh-CN).

**Gotcha (this machine):** stale `GH_TOKEN` *and* `GITHUB_TOKEN` env vars shadow the valid gh keyring login → `env -u GH_TOKEN -u GITHUB_TOKEN gh …` and `gh auth switch -u linroger` were needed for the Pages/description API calls. git push itself was unaffected (osxkeychain).

## SESSION 2026-06-10 (later) — Resume hardening + fresh-clone audit + commit

**Request:** integrate the pieces, optimize, make setup/instructions accurate so a fresh clone runs seamlessly, and make the resume feature robust.

**Resume hardening (all regression-tested in `backend/scripts/test_pipeline_resume.py`):**
1. **Double-resume race fixed** — `PipelineOrchestrator._lifecycle_lock` serialises resume/cancel "load → check → save running → spawn thread"; resume() also rejects when a live `_threads` entry exists (persisted status can lag a crash). Before: two concurrent `POST /resume` both passed the status check and spawned two `_run` threads on the same pipeline.
2. **Run-stage restart no longer duplicates actions** — `SimulationRunner._rotate_stale_action_logs()` rotates `twitter|reddit/actions.jsonl` → `actions.prev.jsonl` before every `start_simulation` (the sim script recreates its DBs but the action logs are append-mode; a resumed run previously mixed old rounds into monitoring/feed/report analysis).
3. **Orphan cancel persists `cancelled`, not `failed`** — `PipelineManager.mark_failed(..., status=)` gained a terminal-status param; UI semantics now match user intent. Frontend already renders `cancelled` stages and offers Resume for both terminal states.
4. Dead-code error-marker branch removed from `_load_research_handoff`; prepare-stage self-heal (stage done but sim state missing) now logs loudly.
5. Resume documented in both READMEs (feature bullet, API table row, troubleshooting row).

**Fresh-clone fixes (from a very-thorough audit agent):**
- README media links switched `Screenshots/…` → `docs/media/…` (GitHub does not follow symlinks when rendering images; raw URLs for symlinked files return the link target text). The `Screenshots -> docs/media` symlink stays for local convenience.
- `DEERFLOW_INTEGRATION.md` absolute `/Users/rogerlin/...` path → generic `<parent-dir>/`.
- README.zh-CN.md had a stray closing code fence and was missing 致谢/许可证 sections its own TOC linked to (plus the cancel/resume/timeout troubleshooting rows) — fixed/mirrored from EN.
- setup.sh stanza-count comments corrected (7 providers incl. kimi).
- **Everything committed** — previously `backend/app/utils/actors.py`, `zep_rate_limit.py`, `scripts/doctor.sh`, `backend/.python-version`, and all of `docs/media/` were untracked, so a clone would ImportError on backend start, fail `npm run doctor`, and show broken README images.

**Evidence:** `test_pipeline_resume.py` 7 checks pass (incl. 8-thread double-resume race → exactly 1 winner); `test_zep_rate_limit.py` pass; py_compile on all touched files; `bash -n` setup.sh+doctor.sh; bridge copies byte-identical to ../deer-flow; `npm run doctor` all green; frontend `npm run build` exit 0; README link/media checker — none missing.

**Still deferred (unchanged):** report-section parallelization + claude-cli `--resume`, SSE streaming, live kimi research test, full live E2E with actors seeding, Windows `os.killpg` portability.

## SESSION 2026-06-10 — Deep integration tightening + out-of-box + performance + README media

**Request:** orchestrate MiroFish×DeerFlow closer together, make it work out of the box, optimize performance, update READMEs with the Screenshots/ media and a setup guide.

**Shipped (all uncommitted on main; full details in ../.remember/remember.md):**
1. **actors.json end-to-end seeding** — new `app/utils/actors.py`; `prepare_simulation(..., actors=)` threads researched role/stance/influence/memory into persona prompts, per-agent stance/influence config, and initial_posts (poster_name → exact agent). Closes the documented Phase-4 gap; DEERFLOW_INTEGRATION.md updated.
2. **Cancellable pipelines** — `POST /api/research/<id>/cancel` + UI button; `PipelineCancelled(BaseException)` punches through fault-tolerance layers; research proc group killed ≤1s, OASIS stopped; `cancelled` terminal state end-to-end.
3. **Out-of-box** — backend Python pinned 3.12 (`.python-version` + setup.sh + npm script); `POST /run` preflight + Zep-placeholder rejection; bridge presets provider-key env vars + per-model credential preflight (exit 3, actionable); codex-only machines → DEERFLOW_MODEL=codex; node ≥20.19 gate; `npm run doctor` health check (live-verified green); FLASK_DEBUG default off; depth-aware research watchdog (900/2400/5400) with report salvage; research_pid persisted + orphan kill.
4. **Performance** — LLM retry covers HTTP providers; chat_json stack-based JSON repair + cooled retry; claude-cli via stdin + ANTHROPIC_API_KEY strip; OASIS semaphore halved per platform in parallel runs (true global cap); graph batch 3→10; persona concurrency 8(HTTP)/3(CLI); deerflow memory/title off + summarization 32k→120k; frontend log-poll stops after research.
5. **Docs/media** — docs/media/ (8 optimized screenshots + demo.mp4 + 4.3MB GIF teaser + poster); README.md restructured (Demo, 3-step Getting started w/ doctor, fixed ../deer-flow paths, 7 research models incl. new kimi stanza, cancel endpoint, acknowledgments); README.zh-CN.md mirrored; README-EN.md → pointer stub.

**Evidence:** py_compile + import tests in real 3.12 venv; npm run build exit 0; doctor all-green; 5-scenario seeding/cancel smoke suite; 7-case JSON-repair suite; mock-CLI suite (stdin 600KB prompt, env strip, retry exhaustion); 63-agent audit workflow (50 confirmed findings drove the plan) + 5-dimension adversarial review workflow over the final diff.

**Next:** apply review-workflow findings → commit; deferred: stage-aware resume/continue, report-section parallelization + claude-cli --resume, SSE streaming, live kimi research test, full E2E with seeding.

## SESSION 2026-06-09 — Follow-up hardening + README media path + responsive smoke

**Request continuation:** read through the MiroFish and DeerFlow workflows, orchestrate them closer together, improve out-of-box behavior/performance/coherence, update English and Mandarin READMEs, and link the images/videos in the Screenshots folder.

**Changes made in this pass:**
1. Added a top-level `Screenshots -> docs/media` symlink so the optimized checked-in demo GIF, MP4, poster, and screenshots are reachable from the exact folder name requested without duplicating large binary assets.
2. Updated `README.md` and `README.zh-CN.md` media links to use `Screenshots/...`, and documented the `Screenshots -> docs/media` path in the project structure.
3. Fixed stale setup/docs text: `setup.sh` now documents that codex-only machines set `DEERFLOW_MODEL=codex`; `deerflow_bridge/README.md` includes `kimi` in the research model list; `DEERFLOW_INTEGRATION.md` says 7 research model options; Mandarin troubleshooting now says Node.js >=20.19 and includes `kimi`.
4. Tightened `ResearchView.vue` responsive header CSS so mobile widths no longer crowd the brand/stage/actions. Also removed negative letter spacing from the main title to keep typography consistent with the project UI constraints.

**Evidence captured:**
- `backend/.venv/bin/python -m py_compile backend/app/services/pipeline_orchestrator.py backend/app/api/research.py backend/app/config.py backend/app/utils/actors.py deerflow_bridge/deerflow_research.py ../deer-flow/deerflow_research.py` — pass.
- README link checker over `README.md`, `README.zh-CN.md`, `README-EN.md`, and `deerflow_bridge/README.md` — all local links/media paths resolve; `Screenshots` symlink resolves to `docs/media`.
- `npm run doctor` — pass: Node 22.22.3, uv 0.7.13, backend Python 3.12.6 imports OK, DeerFlow checkout/overlay/venv OK, `.env`/Zep/MiniMax credentials detected, 0 warnings.
- `npm run build` — pass: 696 modules transformed, build exit 0. Vite still reports the pre-existing non-fatal chunking warning for `frontend/src/store/pendingUpload.js` being both dynamically and statically imported.
- Browser smoke on `http://localhost:3000/research` with the Codex in-app browser: page title matched, first meaningful screen rendered, console errors/warnings were empty, language toggle worked, prompt textarea accepted input, desktop screenshot captured.
- Mobile browser smoke at 390x844 after CSS patch: brand and header controls fit without the prior crowding, prompt area remains visible, console errors/warnings were empty, screenshot captured.

**Remaining work:**
- Full live E2E (`prompt -> research -> graph -> simulation -> report`) was not launched in this pass because it intentionally consumes live provider quota and can take many minutes. The environment is doctor-green and ready for that controlled run.
- Optional improvements still deferred from the previous session: stage-aware resume/continue, report-section parallelization, `claude-cli --resume`, SSE streaming, and a live Kimi research test.

## SESSION 2026-06-09 — Most recent run failure diagnosis and Zep rate-limit fix

**Request continuation:** diagnose why the most recent run failed with `status_code: 429` / `Rate limit exceeded for FREE plan`, and fix the issue.

**Diagnosis:**
1. The most recent pipeline artifact is `backend/uploads/pipelines/pipe_6f1098df6989/pipeline_state.json`. It reached `global_progress=92`, completed research, ontology, graph, prepare, and run stages, then failed in the report stage at `2026-06-09T19:40:15Z`.
2. The matching report artifact is `backend/uploads/reports/report_da694b500ebd`. Its `console_log.txt` shows report planning performed one graph semantic search, fetched graph statistics via all nodes/all edges, then called all nodes again while building simulation context.
3. Zep Cloud returned the free-plan throttle on that duplicate/high-frequency report-planning read: headers included `retry-after: 44`, `x-ratelimit-limit: 5`, `x-ratelimit-remaining: 0`, `status_code: 429`, and body `Rate limit exceeded for FREE plan`.
4. The previous retry path used generic short exponential backoff and the paging helper did not recognize 429 throttles, so the provider-specific retry window was ignored and the exception escaped as a hard report failure.

**Changes made in this pass:**
1. Added `backend/app/utils/zep_rate_limit.py` to recognize Zep 429/free-plan exceptions and parse `Retry-After` / `x-ratelimit-reset`.
2. Updated `ZepToolsService._call_with_retry()` and `backend/app/utils/zep_paging.py` to wait according to the Zep retry window, with tunable caps from `Config`.
3. Added per-`ZepToolsService` node/edge collection caches so one report run reuses its graph snapshot instead of repeatedly reading the same graph pages.
4. Refactored `get_simulation_context()` to compute graph statistics and entity lists from the same node/edge snapshot, removing the duplicate all-nodes read that triggered the observed report failure.
5. Added regression script `backend/scripts/test_zep_rate_limit.py` covering the exact failed 429 header shape, retry delay calculation, `_call_with_retry()` behavior, and graph snapshot reuse.
6. Documented the Zep retry knobs and 429 troubleshooting entry in `.env.example`, `README.md`, and `README.zh-CN.md`.

**Evidence captured:**
- `backend/.venv/bin/python -m py_compile backend/app/config.py backend/app/services/zep_tools.py backend/app/utils/zep_paging.py backend/app/utils/zep_rate_limit.py backend/scripts/test_zep_rate_limit.py` — pass.
- `(cd backend && .venv/bin/python scripts/test_zep_rate_limit.py)` — pass. It verifies the exact failed `retry-after: 44` payload schedules a 45-second sleep (44 + 1 second buffer), then succeeds on retry; it also verifies `get_simulation_context()` + `get_graph_statistics()` only fetch nodes/edges once via cache.
- README local link/media checker over `README.md`, `README.zh-CN.md`, `README-EN.md`, and `deerflow_bridge/README.md` — pass.
- `npm run doctor` — pass: Node 22.22.3, uv 0.7.13, git 2.51.2, backend venv Python 3.12.6 imports OK, DeerFlow checkout/overlay/venv OK, `.env`/Zep/MiniMax credentials detected, 0 warnings.

**Remaining work:**
- A live rerun was not launched here to avoid spending another full LLM/Zep/OASIS run. The original failure was after simulation completion, so a targeted future improvement could add “resume report from existing pipeline/report inputs” to avoid repeating research and simulation when only the report stage needs retrying.

## SESSION 2026-06-09 — DeerFlow deep research made longer and more robust

**Request continuation:** make the DeerFlow workflow more detailed, robust, and longer because the deep research output was too short and not detailed enough.

**Changes made in this pass:**
1. Reworked `deerflow_bridge/deerflow_research.py` so `depth=deep` is no longer a single large agent turn. It now runs a staged multi-pass protocol in one DeerFlow thread:
   - opening source map,
   - primary-evidence sweep,
   - actor/incentive analysis,
   - contradiction/risk testing,
   - forecast-input pass,
   - tool-free long-form synthesis from all checkpointed evidence.
2. Increased standard-depth guidance from roughly 10-20 searches to roughly 14-28 searches and a 3,500-6,000 word target.
3. Set deep synthesis expectations to an 8,000-12,000 word evidence-backed dossier when the model can support it, including actor tables, dated timelines, quantitative evidence, scenarios, winners/losers, leading indicators, and contested-claim/evidence-quality sections.
4. Expanded deep structured extraction: `actors.json` now asks for 10-35 named actors on deep runs and gets an 80-step extraction budget instead of 40.
5. Synced the installed sibling overlay at `../deer-flow/deerflow_research.py` so the current runtime uses the new multi-pass protocol immediately.
6. Increased the depth-aware research watchdog for `deep` from 5400s to 10800s in `pipeline_orchestrator.py`; quick and standard remain 900s and 2400s.
7. Updated `.env.example`, `README.md`, `README.zh-CN.md`, `deerflow_bridge/README.md`, and `DEERFLOW_INTEGRATION.md` to document multi-pass deep mode and the new deep timeout.
8. Added `backend/scripts/test_deerflow_deep_research.py`, an offline regression test that verifies the bridge source and installed DeerFlow copy are synced, the deep prompt contract mentions multi-pass/no-final-report-yet behavior, and the runner schedules the six deep research turns in order.

**Evidence captured:**
- `backend/.venv/bin/python -m py_compile deerflow_bridge/deerflow_research.py ../deer-flow/deerflow_research.py backend/app/services/pipeline_orchestrator.py backend/scripts/test_deerflow_deep_research.py` — pass.
- `backend/.venv/bin/python backend/scripts/test_deerflow_deep_research.py` — pass.
- README local link/media checker over `README.md`, `README.zh-CN.md`, `README-EN.md`, and `deerflow_bridge/README.md` — pass.
- `git diff --check` — pass.
- `npm run doctor` — pass: Node 22.22.3, uv 0.7.13, git 2.51.2, backend Python 3.12.6 imports OK, DeerFlow checkout/overlay/venv OK, `.env`/Zep/MiniMax credentials detected, 0 warnings.

**Remaining work:**
- A live `depth=deep` research-only run should be done next to inspect actual report length, source diversity, and actor extraction quality. This pass intentionally used offline verification to avoid consuming live web/model quota.

## SESSION 2026-06-10 — Live full deep semiconductor forecast workflow

**Request continuation:** run and monitor an end-to-end deep research forecast workflow for the prompt: “请研判至 2030 年全球半导体行业及上下游产业链的发展趋势，分析技术演进、行业环境带来的影响，评估存储、高带宽内存、逻辑芯片、代工等细分领域及各大头部企业的发展前景。参考企业：台积电、三星、英特尔、超威、英伟达、高通、苹果、谷歌、华为、中芯国际、SK 海力士、美光、平头哥、长鑫存储、闪迪、博通、美满。”

**Pipeline launched / observed:**
- Pipeline id: `pipe_f8844f93a738`.
- Created at: `2026-06-10T01:14:05.191353+00:00`.
- Mode/options: `mode=full`, `depth=deep`, project name `研究预测 pipe_f8844f93a738`.
- Runtime provider at launch: `LLM_PROVIDER=minimax`, `DEERFLOW_MODEL=minimax`.
- Artifact directory: `backend/uploads/pipelines/pipe_f8844f93a738/`.

**Initial monitoring evidence captured at `2026-06-10T01:18:24Z`:**
- API status endpoint reports `status=running`, `current_stage=research`, `global_progress=27`.
- Research substage reports `status=running`, `progress=90`, message from active web-search result: `edge AI inference chip market 2026 2030 NPU smartphone PC`.
- Progress log confirms the new multi-pass protocol is active: `deep: starting multi-pass research protocol (6 research turns + final synthesis)`.
- Current progress log has 69 lines, including searches for HBM, CoWoS, TSMC N2/A16, Intel 18A, Samsung foundry, Nvidia roadmap, ASML EUV/High-NA, CXMT, YMTC, Huawei Ascend, CHIPS/export controls, Qualcomm/Apple custom silicon, Broadcom/Marvell custom ASICs, SanDisk/Western Digital NAND, Google TPU, AMD MI roadmap, WSTS/Gartner forecasts, SK Hynix HBM4, and Micron HBM.
- Follow-up probe at `2026-06-10T01:23:23Z`: API still reports `status=running`, `current_stage=research`, `global_progress=27`, research substage `progress=90`. The log grew to 46,674 bytes and the latest status message was `web_search( query=ASML 2025 Q4 earnings China revenue 20% bookings EU )`. No `research_report.md`, `actors.json`, or `sources.json` had been written yet.
- A Codex thread heartbeat automation named `monitor-semiconductor-forecast-pipeline` was created at `2026-06-10T01:23:37Z` to re-check the pipeline every 10 minutes, report progress, update this handoff, and relaunch via `codex-cli` if MiniMax fails with the same provider-content signature.
- Heartbeat probe at `2026-06-10T01:33:45Z`: API still reports `status=running`, `current_stage=research`, `global_progress=27`, research substage `progress=90`. The progress log grew to 144,780 bytes and the latest API message was an ASML Q4/China revenue search result. The tail shows active source work across HBM, Huawei Ascend, SanDisk/WDC spinoff, ASML annual/Q4 material, TrendForce foundry share, Qualcomm, SK Hynix HBM4, DRAM/NAND pricing, Intel 18A/IFS, AMD data-center revenue, China equipment localization/export controls, Apple silicon, Marvell custom AI, SIA/global semiconductor revenue, TSMC Arizona, and CoWoS capacity. No terminal error and no `research_report.md`, `actors.json`, or `sources.json` yet, so no corrective relaunch is needed.
- Heartbeat probe at `2026-06-10T01:43:44Z`: API still reports `status=running`, `current_stage=research`, `global_progress=27`, research substage `progress=90`, with no pipeline error. The log shows `research:deep-5-forecast-implications` completed at `2026-06-10T01:35:36Z` after 68 tool calls and 957,998 total provider tokens, then entered `synthesize: writing report (tool-free) from 243224 chars of gathered research` at `2026-06-10T01:35:37Z`. The DeerFlow child process is still alive, so this is an in-flight synthesis call, not a stopped pipeline. No `research_report.md`, `actors.json`, or `sources.json` had been written yet. Continue monitoring; if MiniMax returns `output new_sensitive (1027)` during this synthesis, relaunch with `codex-cli`.
- Heartbeat probe at `2026-06-10T01:53:48Z`: API still reports `status=running`, `current_stage=research`, `global_progress=27`, with no pipeline error and the DeerFlow child process still alive. `research_report.md` now exists at 43,854 bytes, and `meta.json` reports `report_chars=32189`, so the tool-free synthesis succeeded instead of hitting the prior MiniMax `output new_sensitive (1027)` failure. `actors.json` and `sources.json` are not written yet, so DeerFlow is likely still completing post-report structured extraction / finalization before the pipeline can advance to ontology/graph. Continue monitoring; no corrective relaunch is needed unless the remaining research finalization fails.
- Heartbeat probe at `2026-06-10T02:03:49Z`: pipeline `pipe_f8844f93a738` is now terminal `failed` in the research stage with error `DeerFlow 研究超时（>2400s）`. This was **not** the prior MiniMax `output new_sensitive (1027)` content-filter failure, and `research_report.md` was successfully produced at 43,854 bytes (`meta.json` reports `report_chars=32189`). However, `actors.json` and `sources.json` were never written, so the orchestrator did not accept the research stage as complete and did not proceed to ontology/graph/simulation/report. The apparent root cause is that the live backend enforced a 2400-second research timeout despite the intended deep watchdog being 10800 seconds, likely because the running backend process has stale code/config or the deep timeout did not propagate to this launched process.

**Risk / decision note:**
- The currently running workflow is using MiniMax. The prior semiconductor run `pipe_21363a69567e` did not hang; it failed after research tool use because MiniMax returned a moderation/provider error: `LLM request failed: output new_sensitive (1027)`.
- If `pipe_f8844f93a738` fails with the same MiniMax content-filter signature, the next corrective action is to switch provider to `codex-cli` via `POST /api/settings/llm` and relaunch the same `mode=full`, `depth=deep` workflow, rather than repeating a MiniMax run.

**Monitoring commands:**
```bash
curl -sS http://localhost:5001/api/research/status/pipe_f8844f93a738 | python3 -m json.tool
curl -sS http://localhost:5001/api/research/pipe_f8844f93a738/progress | python3 -m json.tool
tail -f backend/uploads/pipelines/pipe_f8844f93a738/handoff/research_progress.log
```

## SESSION 2026-06-10 — Increase DeerFlow timeout after deep run timeout

**Request continuation:** increase the timeout after `pipe_f8844f93a738` failed at the research stage with `DeerFlow 研究超时（>2400s）`.

**Changes made:**
1. Set the live `.env` override `DEERFLOW_RESEARCH_TIMEOUT=14400` so future DeerFlow research subprocesses get a 4-hour watchdog budget. This gives deep multi-pass research plus post-report `actors.json` / `sources.json` extraction more room than the prior 2400-second runtime ceiling.
2. Changed `backend/app/config.py` fallback `DEERFLOW_RESEARCH_TIMEOUT` from `2400` to `10800`, matching the documented deep-mode default and preventing accidental 2400-second fallback when the environment variable is absent.
3. Restarted the backend process on port 5001 so the new environment and code are active immediately. The new process reports `Config.DEERFLOW_RESEARCH_TIMEOUT=14400`.

**Evidence captured:**
- `curl http://localhost:5001/api/settings/llm` — backend responded successfully after restart; provider remains `minimax`, DeerFlow model remains `minimax`.
- `cd backend && uv run python - <<'PY' ... Config.DEERFLOW_RESEARCH_TIMEOUT ... PY` — printed `DEERFLOW_RESEARCH_TIMEOUT=14400`, `DEERFLOW_RESEARCH_DEPTH=standard`, `DEERFLOW_MODEL=minimax`, and the configured DeerFlow Python path.
- `backend/.venv/bin/python -m py_compile backend/app/config.py backend/app/services/pipeline_orchestrator.py backend/scripts/test_deerflow_deep_research.py` — pass.
- `backend/.venv/bin/python backend/scripts/test_deerflow_deep_research.py` — pass.
- `npm run doctor` — pass with 0 warnings.
- `git diff --check` — pass.

**Remaining work:**
- The failed pipeline `pipe_f8844f93a738` produced a usable research report but did not produce `actors.json`/`sources.json` and cannot continue from the API as-is. A fresh full deep rerun should now have enough watchdog budget to complete the research stage; a future resume/salvage feature could reuse the existing `research_report.md` rather than spending another full research pass.

## SESSION 2026-06-10 — Resume failed research pipelines

**Request continuation:** add a resume button to continue failed runs.

**Changes made:**
1. Added `POST /api/research/<pipeline_id>/resume`.
2. Added `PipelineOrchestrator.resume()`, which keeps the same pipeline id, assigns a fresh task id, clears the terminal error, and restarts the background runner.
3. Added file-backed state rehydration (`PipelineState.from_dict`, `StageState.from_dict`) so failed/cancelled persisted pipeline state can become a runnable in-memory state again.
4. Updated the runner to reuse existing artifacts during resume:
   - `research_report.md` present and valid → skip DeerFlow and continue to ontology even if `actors.json` / `sources.json` are missing.
   - existing ontology/project, completed graph, completed simulation prep, and completed simulation run can be reused when present.
   - missing or failed stages are regenerated from the first recoverable boundary.
5. Added frontend `resumePipeline()` API helper and a `继续 / Resume` button in the failed/cancelled run header. The button disables while resuming and then normal polling takes over.
6. Restarted the backend so `/resume` is live on port 5001.

**Evidence captured:**
- `backend/.venv/bin/python -m py_compile backend/app/api/research.py backend/app/services/pipeline_orchestrator.py backend/app/config.py` — pass.
- `backend/.venv/bin/python backend/scripts/test_deerflow_deep_research.py` — pass.
- `npm run build` — pass (pre-existing Vite chunking warning only).
- `POST /api/research/pipe_doesnotexist/resume` — route responds with expected `{success:false,error:"管线不存在"}` without starting a run.
- `GET /api/settings/llm` — backend responds after restart; provider remains `minimax`.
- `git diff --check` — pass.

**Remaining work:**
- The user can now open/select `pipe_f8844f93a738` and press `继续`. That will reuse its already-written `research_report.md` and proceed into ontology/graph instead of rerunning the expensive DeerFlow research pass.
