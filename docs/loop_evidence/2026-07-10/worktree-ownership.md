# Worktree ownership snapshot — 2026-07-10

**Captured (UTC):** 2026-07-10T17:03:43Z  
**Base revision:** `27b785a1831f343662d5bc5cea37e93ef308483c`  
**Status fingerprint at capture:** `sha256:9180fb91e5c13de8d84f1038fb4e406600fd4e5b823c1694bea67f566b872f4a`

This map prevents overlapping edits in a shared dirty worktree. “External Wave 9 owner” means this loop may inspect and test the file but MUST NOT edit it until ownership is explicitly re-inventoried. The snapshot is point-in-time; a changed fingerprint requires a fresh diff before implementation.

## Root-agent owned in this program

| Files | Purpose |
|---|---|
| `backend/app/services/simulation_runner.py` | LOOP-001 and LOOP-002 simulation/process lifecycle fixes |
| `backend/tests/test_audit_fixes_runner.py` | Deterministic lifecycle regressions |
| `backend/app/services/graph_pruner.py` | LOOP-003 actor-centered physical hard cap and post-delete verification; ownership transferred after the graph Wave 9 file was stable since 2026-07-10T20:05:41 local |
| `backend/tests/test_wave9_kg.py` | LOOP-003 1,000-node, core-overflow, low-core-coverage, partial-delete, and failed-verification regressions |
| `CODEX_LOOP_ENGINEERING.md`, `PLANS.md` | Loop control plane and approved execution plan |
| `docs/loop_evidence/2026-07-10/*` | Hashed forensic evidence, metrics, ownership, and commands |
| `.learnings/ERRORS.md` | Tooling/verification failure record |
| `handoff.md` | Shared continuity entry; update only the current program section |

## Existing documentation from the earlier codebase audit

| Files | Ownership rule |
|---|---|
| `CODEX_RECOMMENDATIONS.md` | Preserve; use as candidate backlog, not as proof of current implementation |

## External Wave 9 — research and bridge

- `deerflow_bridge/config.yaml`
- `deerflow_bridge/deerflow_research.py`
- `deerflow_bridge/patches/models/claude_provider.py`
- `deerflow_bridge/search_tools.py`
- `deerflow_bridge/skills/forecast-visuals/SKILL.md`
- `deerflow_bridge/skills/forecast-visuals/scripts/render.py`

Intent: convergence, retry/recovery, source/citation quality, research-output hygiene, skill wiring, and Plotly rendering.

## External Wave 9 — graph and ontology

- `backend/app/api/graph.py`
- `backend/app/services/graph_builder.py`
- `backend/app/services/graphiti_client/runtime.py`
- `backend/app/utils/actors.py`
- `backend/app/utils/zep_paging.py`
- `backend/tests/test_audit_fixes_graph.py`
- `frontend/src/api/graph.js`
- `frontend/src/components/GraphPanel.vue`

Intent: pagination correctness, graph scope/pruning/GC, ingestion hardening, actor/type handling, API payloads, layout/LOD, and interaction performance.

LOOP-003 adds narrowly scoped shared-file hunks to `backend/app/config.py`, `.env.example`, `backend/app/services/pipeline_orchestrator.py`, and `backend/tests/test_audit_fixes_infra.py` so the destructive hard-cap semantics are documented and failed postconditions surface as degraded pipeline health. Other Wave 9 content in those files remains externally owned.

## External Wave 9 — pipeline, ensemble, and recovery

- `backend/app/config.py`
- `backend/app/services/ensemble.py`
- `backend/app/services/pipeline_orchestrator.py`
- `backend/tests/test_audit_fixes_infra.py`
- `backend/tests/test_wave9_orchestrator.py`
- `scripts/salvage_orphaned_pipelines.py`
- `.env.example`

Intent: stage configuration, post-report checkpoint/heartbeat, orphan salvage, semantic ensemble alignment, telemetry/health, and operational controls.

## External Wave 9 — report, citations, visualization, and product delivery

- `backend/app/services/forecast_extractor.py`
- `backend/app/services/report_agent.py`
- `backend/app/services/report_lint.py`
- `backend/app/services/report_visualizer.py`
- `backend/requirements.txt`
- `backend/tests/test_report_visualizer.py`
- `backend/tests/test_report_viz_and_pdf.py`
- `backend/tests/test_wave9_citations.py`
- `backend/tests/test_wave9_report_focus.py`
- `backend/tests/test_wave9_visualizer.py`
- `frontend/src/api/report.js`
- `frontend/src/components/research/DossierViewer.vue`
- `frontend/src/components/research/ForecastReport.vue`
- `frontend/src/utils/markdown.js`

Intent: outcome-focused report prose, anti-junk lint, citation finalization, Plotly chart generation/fallbacks, PDF/report integration, progressive display, and readable citations.

## External Wave 9 — research validation tests

- `backend/tests/test_wave9_research_quality.py`
- `backend/tests/test_wave9_research_speed.py`

Intent: convergence, cache/retry behavior, source quality, and speed/quality controls.

## Re-inventory rule

Before touching any external file:

1. capture a new `git status --porcelain=v1 -uall` hash;
2. compare file mtime and diff against this snapshot;
3. confirm no concurrent agent/process owns it;
4. update this file with the ownership transfer and iteration ID;
5. add a failing deterministic boundary test before implementation.
