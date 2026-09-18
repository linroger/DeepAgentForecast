# DeepResearchForecast: source atlas and migration record

This atlas describes the source baseline studied on 2026-09-19 and the approved migration underway on `codex/workflow-rearchitecture-2026-09-19`. It separates observed architecture, proposed changes, and verified implementation. A proposed optimization is not a measured speedup.

## Scope and coverage

The starting census covers **880 Python files and 351,306 lines**, including tests. All files were hashed and parsed successfully. Environment, cache, Git, and dependency-install directories were excluded. The original checkout is at `ec29ab1`; two untracked cleanup files are explicitly identified in the inventory and were not copied into the implementation branch. The requested native source tree is ignored vendor material, not tracked application source.

| Requested tree | Files | Lines | Production/support files | Test files |
|---|---:|---:|---:|---:|
| `backend` | 254 | 167,221 | 94 | 160 |
| `deerflow_bridge` | 25 | 29,076 | 19 | 6 |
| `drf2` | 16 | 3,139 | 16 | 0 |
| `deer-flow-2.0.0/backend` | 585 | 151,870 | 309 | 276 |

The explicitly named `packages/harness` directory is included within the native backend row; named individual files are counted once. [The file index](python-file-index.md) and [machine-readable inventory](python-inventory.json) record each file. [Static imports](import-edges.json) and [the orchestrator call index](orchestrator-call-index.json) are navigation aids, not complete runtime call graphs.

**Reading-depth limit:** this is a complete structural census and substantial behavioral review of the end-to-end workflow, not an exhaustive semantic read of all 351,306 lines. Every specialist lists actual depth by file. For example, all 16 `drf2` Python files were read in full, while the native backend has 14 full reads, 21 selected-function reviews, and 550 structural-only files. The bridge has seven full reads and 18 selected-function reviews. Do not infer verification from line counts or symbol enumeration.

## The system that actually runs

The live product is the Flask backend and its `PipelineOrchestrator`. A pipeline is durable state plus referenced project, graph, simulation, and report objects. An API request starts a background owner; expensive research and OASIS work execute across process boundaries. File-backed records and graph storage survive the process that created them. UI/task progress is a projection of that state.

The native DeerFlow harness supplies research agents, tools, models, skills, middleware, checkpoints and subagents. `deer-flow-2.0.0` is the vendor seed; `deer-flow/` is the default assembled runtime in the examined configuration. Tracked bridge modules and overlays are the maintained source of custom behavior. Native gateway completion is not completion of a DRF forecast.

`drf2` is a separate driver and MCP adapter path. It has useful abstractions, but its engine transports, job status, stage receipts and completion gates are not yet equivalent to the live coordinator. It must not silently become the authoritative runtime simply because its files are shorter.

## Stage and boundary map

| Stage | Main Python responsibilities | Inputs and durable outputs | Boundary that must survive migration |
|---|---|---|---|
| Admission and lifecycle | `api/research.py`; `PipelineManager`, `PipelineOrchestrator`; ASTRA `launch_intents.py` | User prompt/options → stable pipeline/attempt/task ownership, pinned policies, durable state | Retry the same launch without double execution; distinguish a dead owner from a slow one; keep cancellation/resume/fork identity. |
| Research | `deerflow_research.py`, `linear_research.py`, `cached_fetch.py`, `search_tools.py`, `research_budget.py`; native client/middleware/model/tool layers | Question and lane policy → report, actor dossier, actors, sources, timeline, quantitative/contested data, lane packs, receipts, manifest | Fetched evidence and derived summaries stay distinct; mode, question, lane, budget and checkpoint identity must match before reuse. |
| Ontology | `ontology_generator.py`; coordinator project/artifact publication | Report + dossier + actors + question → entity/edge schema and ontology artifact | Preserve late-document evidence; bind outputs to exact effective inputs; distinguish valid legacy state from verified current generation. |
| Graph | `graph_builder.py`; `graphiti_client` runtime/client/storage; entity reader/resolver/pruner; actor seed helpers | Ontology + structured actors + text episodes → graph, actor seed receipt, communities and priors | Do not lose acknowledged work or destroy evidence during resolution; retain temporal and source identity; distinguish uncertain writes from confirmed failures. |
| Preparation | `simulation_manager.py`, `simulation_config_generator.py`, `oasis_profile_generator.py`, `actor_context.py`, `actor_role_prompt.py` | Graph and research context → cast, profiles, actor context/role packs, final sealed config, calendar/scenario state | Give each actor only authorized knowledge; bind prepared outputs to current graph/research/overlay; seal after all intended config mutations. |
| Simulation | `simulation_runner.py`; parallel and single-platform launchers; OASIS/CAMEL adapters; decision and world-state helpers | Sealed prepared config → per-platform actions, run state, completion markers, summaries, trajectories and usage | Verify actual consumed context and both-platform completion; preserve enough state for equivalent continuation; keep simulated observations out of observed evidence by default. |
| Report and delivery | `report_agent.py`, `forecast_extractor.py`, `report_lint.py`, `report_visualizer.py`, report/API/SDK/market/evaluation modules | Research + graph + simulation diagnostics + scenario policy → audited report, structured forecast, figures, translations and exports | All entry points consume the same owned evidence; bind published bytes to the final audit; preserve source citation positions and uncertainty labels. |
| Cross-cutting accounting | LLM clients, telemetry, ASTRA usage ledger/reservations/child transport | Logical operation and physical attempt → durable actual/estimated/unknown usage, holds, settlement and status | Exactly-once replay semantics; no hidden SDK retries; do not call estimated coverage an invoice or a hard monetary cap. |

See [orchestration](orchestration.md) for concrete stage entry and lifecycle anchors, [research](research-bridge.md) for hybrid/linear mode differences, [ontology and graph](ontology-graph.md), [simulation](simulation.md), [report/platform](report-platform.md), and [native harness/DRF-2](native-drf2.md) for implementation and test details.

## What the review changes about the approach

The three largest application modules contain nearly 44,000 lines, but file size is a symptom. The main architectural problem is inconsistent producer/consumer contracts across alternate routes and retries. A mechanical split alone would move those inconsistencies into more files.

The review found concrete examples: current actor roster hash semantics differ between producer and consumer; the new linear engine does not honor all hybrid modes and provenance obligations; graph rebuild can leave preparation eligible for reuse; optional derived files may retain old values after an empty new result; seed and manual reports omit rich research context; chat does not consume all constructed evidence blocks; and single-platform simulation launchers have weaker safeguards than the maintained parallel path.

Some apparently missing features already exist on ASTRA: durable compaction receipts and stop propagation, actor shared-access denial, full sanitation before ontology sampling, cached ontology integrity, graph timeout acknowledgement preservation, launch idempotency, and much of durable accounting. [The history/reuse report](history-reuse.md) verifies their branch locations and limits. Integrate and test these before inventing replacements.

The chosen target therefore has one durable coordinator, versioned input snapshots, stage receipts and dependency decisions, cohesive stage adapters, and shared report/simulation context assembly. Planned diagnostics will explain why reuse is allowed, stale, invalid or unverified. Strict publication and fork-safe invalidation precede automatic regeneration. The [approved implementation plan](implementation-plan.md) contains acceptance scenarios and rollback boundaries for each slice.

## Current implementation versus proposal

RX-00 is verified offline. The branch combines current main and ASTRA while preserving both the linear helper deployment and compaction safeguards, and both request timeout and zero hidden SDK retries. The integration also repairs current actor-roster hash compatibility, restores strict extraction provenance, rejects unsupported linear modes before provider admission, preserves typed stops after bounded actor-worker timeouts, and makes DRF2 skill lookup portable. The OASIS prompt-consumption fixture uses the installed offline StubModel while still exercising real profile/prompt consumers.

Final RX-00 acceptance: **4,016 backend tests passed, zero failures, 12 skips and 11 existing xfails**; **131 frontend tests passed and the production build passed**; `init.sh` passed. The guarded backend run recorded zero network attempts across 143 Python processes and no source drift. Independent review closed the late-worker regression. Identical Ruff checks against both parent source trees found **zero introduced violations**; 107 inherited findings remain visible in the comparison ledger.

The first failing gates and their corrections remain recorded; they are not substituted for final evidence. See [RX-00 verification](rx00-verification.json), [independent review](rx00-review.md), and [continuity record](../../handoff/workflow-rearchitecture-20260919/handoff.md). This evidence is local/offline and does not establish live provider behavior, production performance or full rearchitecture completion. RX-01 and later work remain separate slices.

## Repeated workflow packaging

The requested 30-day review found strong repetition in DRF forensics/recovery, source-boundary analysis, HMS setup, and paper assessment. Existing skills and the paused ASTRA automation already cover the high-confidence candidates. **No new skill, custom agent or automation is justified by the current evidence.** The dated compact shortlist is in [history-reuse.md](history-reuse.md).

A narrow producer/consumer audit worksheet may merit extending the existing research skill after it works on a DRF boundary and another project. Writing, communications, finance and personal administration had insufficient procedure-level evidence; similar task titles alone do not establish a reusable workflow. No automation was resumed or duplicated.
