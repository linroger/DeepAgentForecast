# EXECPLAN2 — Audit findings & improvement roadmap (DeepAgentForecast)

> **What this is.** A self-contained, dependency-ordered remediation + enhancement runbook produced
> by a 14-scope multi-agent code audit of the live tree. Every *finding* is grounded in real code
> (`file:line` + quoted evidence) and was **adversarially verified against the source** by an independent
> agent (false positives were dropped — see Appendix C). Every *improvement* is a concrete, architecture-
> aware proposal. The next session should be able to open this file and execute it top-to-bottom.
>
> **Relationship to `EXECPLAN.md`.** `EXECPLAN.md` (46 golden-thread tasks: thread a *situation brief*
> + *actor-relationship graph* from research → graph → personas → sim → report) is **already implemented**
> (`handoff.md` = Complete). This plan does **not** restate it. It catalogs *net-new* defects and
> enhancements discovered on top of that work; the few items that overlap planned work are flagged
> `↺ overlaps EXECPLAN`.
>
> **Engineering invariant (unchanged).** *Optional-degrade.* Every new field/flag is optional; every
> consumer falls back to today's behaviour when it is absent. Gate cost/risk behind a `Config` flag
> (defaults preserve current behaviour unless a fix is a pure correctness/security repair).
>
> **Secret constraint (standing).** The MiniMax/LLM API keys live **only** in the gitignored `.env`;
> never echo them into committed files, logs, or subprocess argv.

**Audit scoreboard**

| Metric | Value |
|---|---|
| Finder scopes | 14 (12 subsystems + 2 cross-cutting lenses) |
| Raw findings | 129 |
| **Verified & kept** | **100** |
| Rejected by verification | 29 (Appendix C) |
| Improvement proposals | 67 |
| Severity mix | P0×2 · P1×16 · P2×43 · P3×39 |
| Category mix | robustness×31 · correctness×18 · concurrency×15 · data-contract×12 · bottleneck×10 · security×10 · config×4 |

---

## 0. TL;DR — the fast path

The highest-leverage work first, straight from the triage digest:

1. STOP THE BLEEDING (P0): F-12-0 + F-13-0 + F-13-1/F-8-0. OASIS subprocesses survive a backend restart and keep burning LLM credits forever (F-12-0); the API has no auth + CORS '*' + 0.0.0.0 bind so any LAN host can hit every mutating endpoint (F-13-0); and every settings request DEBUG-logs the raw request body, writing live API keys to disk (F-13-1 == F-8-0, confirmed at backend/app/__init__.py:64). Fix secret-logging + lifecycle leaks before anything else.
2. SECRET HYGIENE SWEEP: F-13-1/F-8-0 (body logging), F-8-1 (.env written unescaped -> newline/'=' injection & corruption), F-11-0 (key in argv + world-readable /tmp during setup), F-0-7/F-13-3 (research prompt + provider key passed via subprocess argv at pipeline_orchestrator.py:378 -> visible in `ps`), F-8-5/F-13-2 (settings test endpoint = unauth SSRF + reflects raw SDK tracebacks). Centralize redaction and stop full-env subprocess passthrough (I-8-3).
3. RESTART SURVIVABILITY is one defect surfacing five ways: F-12-0, F-6-5, F-6-11, F-12-6 (+ state races F-6-9/F-6-13, PID-reuse F-1-8). After a restart the runner reports a stale PID, can't stop orphans, lies 'STOPPED', and blocks re-runs of crashed sims. Fix once with a persisted pgid + heartbeat/owner-lease (I-4-1) and a boot reaper that probes liveness. This dominates the P1 tier.
4. DATA-CONTRACT CRASHES that silently degrade or KeyError the golden thread the EXECPLAN just built: F-3-0 (non-list ontology output crashes the stage), F-5-0 (interrupted realtime profiles miss mbti/gender/age/country -> OASIS KeyError), F-5-2 (agent_id key/type mismatch silently drops a whole LLM-config batch), F-4-0 (FOLLOW/MUTE typed feedback edges NEVER written -> the shipped T3.10 feature is dead), F-9-0 (standalone scripts emit no actions.jsonl -> completion detection + all downstream consumers break).
5. REPORT INTEGRITY: F-7-0 (force_regenerate leaves a stale folder -> get_report_by_simulation returns a non-deterministic/stale report), F-7-2 (native tool path skips the minimum-tool-call floor -> sections written with ZERO graph grounding, directly undermining T4.5), F-7-1 (module-global FileHandler cross-contaminates concurrent reports' logs). Add the resume guard F-1-0 / I-4-0 so a resumed run reuses the finished report instead of re-running the full LLM agent (confirmed missing at orchestrator:1686 vs the GRAPH guard at 1478).
6. ATOMIC-WRITE EVERYWHERE: a watchdog SIGKILL or a polling reader mid-write corrupts the contract -- F-0-4 (actors/sources/timeline.json), F-7-6 (progress.json/section_*.md/meta.json), F-6-13 (state.json 'w' truncation), F-8-1 (.env). Introduce one write_json_atomic(tmp+os.replace) helper and route all artifact + state writers through it; cheap, and it kills a whole class of corruption.
7. GRAPHITI CONCURRENCY: F-2-5 == F-12-8 -- concurrent add_episode/report-read on one cached Graphiti instance shares a mutable driver/loop/redislite client with no per-graph serialization. Compounded by F-12-1 (REPORT reads the graph while the sim->graph feedback writer is still flushing). Add a per-graph async lock + a read/write barrier between RUN-feedback and REPORT.
8. PERF / N+1 hot paths worth batching: F-4-3 (insight_forge N+1 per-node round-trips ignoring the cached node map), F-6-2 (/run-status/detail re-parses both full actions.jsonl up to 4x/request), F-7-3 (chat() re-scans every report folder per message), F-5-4 (nested ThreadPool + serial Zep retries in persona gen). These are silent latency/cost taxes on the interactive path; pair with I-6-5/I-6-1.
9. FRONTEND/OPS quick wins: F-10-7 (hardcoded http://localhost:5001 fallback breaks non-localhost deploys), F-10-12 (requestWithRetry retries non-idempotent POSTs -> duplicate create/start side effects), F-11-1 (requires-python >=3.11 silently drops the local FalkorDB backend on 3.11), F-11-4 (AGPL LICENSE deleted while both manifests still declare AGPL-3.0 -- live compliance risk).
10. HIGHEST-LEVERAGE NEW CAPABILITY after stabilization: a central LLM call meter + per-run telemetry (I-5-0/I-5-1) and a content-addressed LLM cache (I-6-0), then the structured machine-readable forecast object + citation-grounding verifier (I-3-0/I-9-1/I-3-1) and calibration pass (I-3-5). These turn a working-but-opaque pipeline into a measurable, calibratable, cost-bounded forecasting product.

**The two P0s (do these first):**
- **[F-12-0] OASIS simulation subprocesses are orphaned (not killed) across a backend restart — keep burning LLM credits forever** — `backend/app/services/simulation_runner.py` : 805-833, 1291-1393, 226-231. Unbounded credit burn and a zombie OASIS process tree (twitter+reddit + any MCP/sandbox children) that the operator must hunt down by hand. run_state.json is also left stuck at RUNNING, so any logic keyed off it (and start_simulation's 'already running' guard at lines 341-343) misbehaves.
- **[F-13-0] No authentication + CORS '*' + 0.0.0.0 bind exposes all mutating pipeline/settings endpoints to the LAN** — `backend/app/__init__.py, backend/run.py` : __init__.py:43; run.py:40,45. Anyone on the same network (coffee shop, corp LAN, cloud VPC) can: trigger expensive research runs that burn the operator's LLM credits/credentials; cancel/delete others' pipelines and their handoff artifacts; overwrite the active LLM provider config and inject an attacker-controlled api_key/base_url into the persisted .env; and read research dossiers. CORS '*' additionally lets any web page the victim browses issue these mutating requests against localhost:5001 (DNS-rebinding/CSRF-style) since there is no CSRF token and no Origin check.

---

## 1. Cross-cutting themes (systemic patterns)

Recurring root causes that span many findings — fixing the *pattern* once is cheaper than fixing each site.

### Theme: Secrets leak through every observability and IPC surface (logs, .env writes, subprocess argv/env, reflected error bodies)

**Systemic fix:** Build one secret-redaction boundary: a redacting log filter on all loggers, a write_env_value() that quotes/escapes values, pass the research prompt + provider keys to subprocesses via temp file or stdin (never argv), strip the parent env to an allowlist before Popen, and never return raw SDK tracebacks to clients. Gate request-body DEBUG logging off for any /settings route.

**Findings:** F-8-0, F-13-1, F-8-1, F-11-0, F-0-7, F-13-3, F-8-5, F-13-2, F-13-4

### Theme: No process survives a backend restart correctly: orphaned subprocesses, stale PIDs, lying status, blocked re-runs

**Systemic fix:** Establish one durable lifecycle contract: persist the process-group id + a periodic heartbeat/owner-lease to state.json; on boot run a single reaper that probes liveness (kill -0 / pgid) and either re-attaches a monitor or reaps + marks failed; make stop/start/already-running decisions from on-disk liveness, never stale in-memory state. Collapses F-12-0/F-6-5/F-6-11/F-12-6 and the PID-reuse hazard F-1-8 into one fix (I-4-1).

**Findings:** F-12-0, F-6-5, F-6-11, F-12-6, F-1-8, F-6-12

### Theme: Non-atomic file writes corrupt the cross-stage contract under SIGKILL or concurrent polling readers

**Systemic fix:** Add one write_json_atomic/write_text_atomic helper (write to tmp in same dir, fsync, os.replace) and route ALL artifact + state writers through it: actors/sources/timeline.json, progress.json, section_*.md, meta.json, state.json, .env. Also fix the stale 'relative filename' contract (F-1-7) and stop storing absolute host paths.

**Findings:** F-0-4, F-7-6, F-6-13, F-8-1, F-1-7

### Theme: Swallowed / silently-degraded errors hide real failures and produce wrong-but-green runs

**Systemic fix:** Adopt fail-loud-or-record: replace bare excepts with typed handling that logs the proximate cause and either re-raises or writes a structured failure marker. Offenders: search_graph downgrade (F-4-6), dropped activity batches need a dead-letter (F-4-5), file-existence completion marks runs COMPLETED prematurely (F-6-10), swallowed graph-memory updater failure while API reports it on (F-6-12), export_forum always ok=True (F-9-7), dead None-handling masking the real chat() contract (F-7-5).

**Findings:** F-4-6, F-4-5, F-6-10, F-6-12, F-9-7, F-7-5

### Theme: Shared mutable state mutated without locks across monitor threads, API threads, and concurrent async graph calls

**Systemic fix:** Synchronize per resource: a lock on SimulationRunState shared between monitor and API threads (F-6-1); single owner or file lock for state.json across Manager+Runner+API (F-6-9, F-6-13); a per-graph asyncio lock in the Graphiti runtime plus a read/write barrier so REPORT cannot read while RUN feedback flushes (F-2-5/F-12-8/F-12-1); guard chained signal handlers from raising KeyboardInterrupt when the original is SIG_DFL (F-12-3); lock apply_provider vs running pipelines (F-8-4).

**Findings:** F-6-1, F-6-9, F-2-5, F-12-8, F-12-1, F-12-3, F-8-4

### Theme: N+1 / full-rescan hot paths re-read everything per request instead of using caches

**Systemic fix:** Introduce a per-run retrieval/node cache and incremental readers: insight_forge should use the cached node map not per-node round-trips (F-4-3); /run-status/detail and analytics should tail actions.jsonl incrementally with explicit bounds, not silent 10000 caps (F-6-2, F-6-3); chat() should index report folders once instead of re-scanning markdown per message (F-7-3); persona gen should drop the nested ThreadPool + serial retries (F-5-4); cap unbounded loaders (F-4-4).

**Findings:** F-4-3, F-6-2, F-6-3, F-7-3, F-5-4, F-4-4

### Theme: Positional / key-keyed contracts between persona, config, and OASIS break silently on None-filtering or id mismatch

**Systemic fix:** Make the agent_id contract explicit and total: never filter None profiles in a way that shifts array index (F-5-1); key LLM configs by a normalized agent_id with type coercion and a hard error on miss instead of silent batch-drop (F-5-2); always emit required OASIS keys with defaults even on interrupted realtime saves (F-5-0); produce interested_topics so echo-chamber clustering doesn't degrade (F-5-3); robustify username generation for CJK/empty names (F-5-5) and CSV fieldnames (F-5-6).

**Findings:** F-5-0, F-5-1, F-5-2, F-5-3, F-5-5, F-5-6

### Theme: Graph build / ontology stage assumes well-formed LLM output and mutates process-global state

**Systemic fix:** Validate-and-coerce all LLM-shaped ontology/graph inputs: tolerate non-list entity_types/edge_types (F-3-0), default missing 'name' keys instead of KeyError (F-3-1), stop tail-truncating fallback inserts (F-3-4); scope the warnings filter locally instead of mutating the global filter every call (F-3-2); stop writing literal type-name nodes ('Person'/'Organization') as graph entities (F-3-3); delete the divergent dead build_graph_async path (F-3-5).

**Findings:** F-3-0, F-3-1, F-3-2, F-3-3, F-3-4, F-3-5

### Theme: No LLM cost/latency/token observability across the multi-stage pipeline

**Systemic fix:** Add a central LLM call meter (I-5-0) every provider wrapper feeds; emit per-run run_telemetry.json with stage durations + token/cost rollup + failure attribution (I-5-1); surface through ReportLogger/run_summary and status APIs (I-5-4/I-5-6); parse DeerFlow's already-emitted token usage (I-5-7). This is the prerequisite for any budget guard (I-5-3) or two-tier routing (I-6-2); F-0-0 (redundant synthesis call) is one thing the meter will immediately expose.

**Findings:** F-0-0

### Theme: Forecast output is prose-only: no machine-checkable claims, no grounding audit, no calibration

**Systemic fix:** Layer a structured forecast contract on the report: a machine-readable forecast block with scenario probabilities + resolution criteria (I-3-0/I-9-1), a citation-grounding verifier flagging any quantitative/quoted claim not traceable to a tool result or [S#] (I-3-1), and a self-critique calibration pass (I-3-5). Fix F-7-2 (grounding floor) and F-7-8 (5-section minimum) first so sections can't be written ungrounded or below spec.

**Findings:** F-7-2, F-7-8

---

## 2. Master priority index (all 100 findings)

Ordered by remediation priority (P0 + high-confidence + broad blast-radius first). Jump to a finding by its id.

| # | id | sev | category | subsystem | effort | title |
|---|---|---|---|---|---|---|
| 1 | F-12-0 | P0 | concurrency | x-concurrency | M | OASIS simulation subprocesses are orphaned (not killed) across a backend restart — keep burning LLM credits forever |
| 2 | F-13-0 | P0 | security | x-security | M | No authentication + CORS '*' + 0.0.0.0 bind exposes all mutating pipeline/settings endpoints to the LAN |
| 3 | F-13-1 | P1 | security | x-security | S | API keys written to plaintext log file on every settings request (request-body DEBUG logging) |
| 4 | F-8-0 | P1 | security | core-utils | S | API keys leak to disk via DEBUG request-body logging |
| 5 | F-8-1 | P1 | security | core-utils | S | _persist_env writes API key to .env without escaping → newline/`=` injection & corruption |
| 6 | F-11-0 | P2 | security | setup-ops | S | API key leaked to process argv and a world-readable /tmp file during setup live-test |
| 7 | F-13-2 | P1 | security | x-security | M | Unauthenticated SSRF via /api/settings/llm/test base_url (arbitrary outbound request with reflected response) |
| 8 | F-8-5 | P2 | security | core-utils | S | _test_openai_compat_provider echoes full SDK error + 500 handlers return traceback to client |
| 9 | F-0-7 | P3 | security | research | S | Research prompt passed via argv (--prompt) exposes the full prediction question to process listing |
| 10 | F-13-3 | P2 | security | x-security | S | Research prompt passed via subprocess argv — visible in process list to all local users |
| 11 | F-6-5 | P1 | robustness | sim-runtime | M | Server-restart resume reports stale process_pid and runs without a live process handle/monitor; stop becomes a no-op against orphaned subprocesses |
| 12 | F-6-11 | P2 | correctness | sim-runtime | S | stop_simulation sets status STOPPED even when no process handle exists and no kill executed, lying about a still-running subprocess |
| 13 | F-12-6 | P2 | robustness | x-concurrency | S | start_simulation 'already running' guard and stop rely on in-memory state that is stale after restart, blocking re-runs of crashed simulations |
| 14 | F-1-8 | P3 | robustness | orchestrator | M | reconcile_orphans re-identifies the orphan research PID only by command substring 'deerflow_research.py' — can mis-signal a sibling pipeline's research on PID reuse |
| 15 | F-6-9 | P3 | concurrency | sim-runtime | M | SimulationManager and SimulationRunner both read-modify-write state.json concurrently with no shared lock, risking lost status updates |
| 16 | F-6-13 | P3 | concurrency | sim-runtime | S | _check_simulation_prepared rewrites state.json with non-atomic 'w' truncation, reintroducing the torn-read race the rest of the code avoids |
| 17 | F-3-0 | P1 | robustness | graph-build | S | OntologyGenerator._validate_and_process assumes entity_types/edge_types are lists; non-list LLM output crashes the whole ontology stage |
| 18 | F-5-0 | P1 | data-contract | personas | S | Realtime-saved reddit_profiles.json omits required keys (mbti/gender/age/country) → OASIS KeyError on interrupted runs |
| 19 | F-5-2 | P1 | robustness | personas | S | LLM agent-config keyed by cfg['agent_id'] — KeyError or str/int mismatch silently discards an entire batch's LLM configs |
| 20 | F-4-0 | P1 | data-contract | memory | S | FOLLOW/MUTE typed feedback edges are never written (action_args key mismatch) ↺ |
| 21 | F-7-0 | P1 | correctness | report | M | force_regenerate leaves stale report folder; get_report_by_simulation returns a non-deterministic (often stale) report |
| 22 | F-7-2 | P1 | data-contract | report | M | Native tool-calling section path does NOT enforce minimum tool calls, so sections can be written with zero graph grounding |
| 23 | F-7-1 | P1 | concurrency | report | L | ReportConsoleLogger attaches a FileHandler to MODULE-GLOBAL loggers, cross-contaminating concurrent reports' console logs and racing on handler lists |
| 24 | F-1-0 | P2 | correctness | orchestrator | S | REPORT stage has no reuse guard — resume regenerates the entire forecast (re-runs full LLM tool agent) even if the report already succeeded ↺ |
| 25 | F-9-0 | P1 | correctness | scripts | M | Single-platform standalone scripts emit no actions.jsonl, breaking completion detection and all downstream consumers |
| 26 | F-9-1 | P1 | data-contract | scripts | S | Initial posts and scheduled CREATE_POST/FOLLOW events are double-logged into actions.jsonl, inflating total_actions and the forum feed |
| 27 | F-12-1 | P1 | concurrency | x-concurrency | M | Report stage reads the knowledge graph while the simulation→graph feedback writer is still flushing (concurrent read/write on one FalkorDB graph) |
| 28 | F-2-5 | P2 | concurrency | graph-shim | L | Concurrent add_episode on one cached Graphiti instance shares mutable driver/clients (dedup ordering + state hazard) |
| 29 | F-12-8 | P2 | concurrency | x-concurrency | L | Concurrent runtime calls share one event loop, one redislite FalkorDB client, and cached per-graph Graphiti instances with no per-graph write/read serialization |
| 30 | F-6-1 | P2 | concurrency | sim-runtime | M | Monitor thread mutates a SimulationRunState shared with API request threads with no lock (torn reads / lost updates) |
| 31 | F-12-3 | P2 | concurrency | x-concurrency | S | SimulationRunner signal handler raises KeyboardInterrupt from inside the chained PipelineOrchestrator handler when the original handler is SIG_DFL |
| 32 | F-6-10 | P2 | correctness | sim-runtime | S | Platform-completion inferred from actions.jsonl existence; a slow/failed platform is treated as disabled and the run is marked COMPLETED prematurely |
| 33 | F-6-0 | P2 | data-contract | sim-runtime | S | max_rounds truncation: persisted rounds_truncated_from/to dropped on state reload, falsifying the golden-thread truncation banner |
| 34 | F-7-6 | P2 | robustness | report | M | Non-atomic writes of progress.json / section_*.md / meta.json can be read mid-write by polling endpoints |
| 35 | F-0-4 | P3 | robustness | research | S | actors.json / sources.json / timeline.json written non-atomically — watchdog SIGKILL mid-write can corrupt the contract |
| 36 | F-3-1 | P2 | robustness | graph-build | S | set_ontology raises KeyError on entity/attr/edge entries missing "name", aborting the entire graph build |
| 37 | F-3-2 | P2 | robustness | graph-build | S | set_ontology mutates the process-global warnings filter on every call (permanent side effect) |
| 38 | F-3-3 | P2 | data-contract | graph-build | M | seed_actors IS_A path writes a literal type-name node ("Person"/"Organization") as a graph entity |
| 39 | F-3-4 | P2 | correctness | graph-build | S | Ontology fallback-insertion truncates from the tail and can silently drop legitimate specific entity types |
| 40 | F-5-1 | P2 | data-contract | personas | M | Realtime save filters out None profiles, shifting array positions and breaking positional agent_id contract |
| 41 | F-5-3 | P2 | correctness | personas | S | interested_topics consumed by echo-chamber clustering but never produced → T3.4 clustering silently degrades to stance-only |
| 42 | F-2-1 | P2 | correctness | graph-shim | S | delete_graph never deletes graph data on the FalkorDB *server* backend (silent no-op) |
| 43 | F-2-2 | P2 | data-contract | graph-shim | S | Bi-temporal datetime fields leak through the Zep facade as datetime objects, not ISO strings |
| 44 | F-8-4 | P2 | concurrency | core-utils | M | apply_provider mutates shared Config + os.environ with no lock vs running pipelines |
| 45 | F-6-6 | P2 | robustness | sim-runtime | S | IPC client orphans response files on timeout while the server is mid-interview; unread responses leak in ipc_responses/ and never get cleaned |
| 46 | F-12-7 | P3 | concurrency | x-concurrency | M | IPC interview timeout leaves an orphaned command file; server may execute a stale interview after the client already gave up |
| 47 | F-4-5 | P2 | robustness | memory | M | Failed activity batches are silently dropped (no dead-letter / no re-buffer) |
| 48 | F-4-2 | P2 | concurrency | memory | S | Worker loop holds _buffer_lock across network send + retries + sleep (contention, comment is wrong) |
| 49 | F-6-12 | P3 | robustness | sim-runtime | S | Requested graph-memory updater failure is swallowed; run proceeds with updates off while API still reports them enabled |
| 50 | F-7-4 | P2 | robustness | report | S | download_report writes a NamedTemporaryFile with delete=False and never cleans it up — disk leak |
| 51 | F-9-3 | P2 | robustness | scripts | S | export_demo_site_data hard-opens dossier.md and full_report.md with no existence guard, crashing the whole export run |
| 52 | F-8-9 | P2 | robustness | core-utils | S | split_text_into_chunks lacks forward-progress guarantee with large overlap |
| 53 | F-5-4 | P2 | bottleneck | personas | M | Per-worker nested ThreadPool + serial Zep retries make persona generation latency-bound and amplify thread count |
| 54 | F-5-5 | P2 | robustness | personas | S | Username generation yields empty/duplicate handles for CJK/punctuation/empty names |
| 55 | F-4-4 | P2 | robustness | memory | S | fetch_all_edges has no max_items cap; full edge set loaded and cached unbounded |
| 56 | F-11-2 | P2 | robustness | setup-ops | S | doctor.sh envval does not strip whitespace/inline comments, causing false 'unknown provider' and false key-missing failures |
| 57 | F-9-7 | P3 | data-contract | scripts | M | export_forum always reports ok=True; OASIS per-action failures are invisible in the demo feed |
| 58 | F-6-2 | P2 | bottleneck | sim-runtime | M | /run-status/detail re-parses both full actions.jsonl files up to 4x per request; analytics rebuild whole history per call (unbounded, repeated) |
| 59 | F-6-3 | P2 | correctness | sim-runtime | S | get_timeline/get_agent_stats/run_summary silently cap at limit=10000 newest actions, truncating analytics on long simulations |
| 60 | F-7-3 | P2 | bottleneck | report | M | chat() re-scans every report folder (reading full markdown) on every user message — O(N) N+1 with large payloads |
| 61 | F-4-3 | P2 | bottleneck | memory | M | insight_forge does N+1 per-node round-trips instead of using the cached node map |
| 62 | F-7-7 | P2 | bottleneck | report | M | interview_agents is exposed as a native function tool with no per-section call cap, risking IPC-timeout stacking under native tool calling |
| 63 | F-4-1 | P2 | data-contract | memory | S | coalition_map ignores FOLLOW/MUTE interactions (missing target_user_name key) |
| 64 | F-7-5 | P2 | correctness | report | M | Dead None-handling in ReAct loop masks the real failure contract: LLMClient.chat() never returns None, it raises |
| 65 | F-4-6 | P3 | robustness | memory | S | search_graph swallows all exceptions and silently downgrades to keyword search |
| 66 | F-10-1 | P2 | bottleneck | frontend | L | GraphPanel deep-watch on graphData rebuilds the entire D3 simulation on every refresh, losing zoom/pan/positions |
| 67 | F-10-12 | P2 | robustness | frontend | M | requestWithRetry retries non-idempotent POSTs (create/prepare/start/interview), risking duplicate side effects |
| 68 | F-9-2 | P3 | bottleneck | scripts | M | Per-round SQLite connection churn: a fresh connect/close every round per platform (N round-trips, no reuse) |
| 69 | F-11-1 | P1 | config | setup-ops | S | pyproject requires-python >=3.11 silently drops the local graph backend (falkordblite/redis) on 3.11 |
| 70 | F-11-4 | P2 | config | setup-ops | S | AGPL LICENSE file deleted from the tree while both manifests still declare AGPL-3.0 |
| 71 | F-1-3 | P3 | correctness | orchestrator | S | Dynamic progress-band recompute can make global_progress jump backward (non-monotonic progress bar) |
| 72 | F-1-7 | P3 | data-contract | orchestrator | S | artifacts contract docstring says 'handoff 相对文件名' but code stores absolute server paths (stale contract, leaks host paths) |
| 73 | F-1-1 | P3 | concurrency | orchestrator | S | Cancellation during RUN polling is delayed up to 5s by a blocking time.sleep(5) instead of waiting on the cancel event |
| 74 | F-3-5 | P3 | correctness | graph-build | M | build_graph_async / _build_graph_worker is divergent dead code that omits actor seeding, reference_time, and community detection ↺ |
| 75 | F-6-7 | P3 | bottleneck | sim-runtime | S | Env command loop drains at most one IPC command per 0.5s tick, serializing queued single interviews |
| 76 | F-6-8 | P3 | concurrency | sim-runtime | M | Live DB read endpoints open SQLite without read-only/timeout while the sim writes the same file; transient locks silently return 0 rows |
| 77 | F-8-6 | P3 | robustness | core-utils | S | Rotating log filename frozen at import time; date never rolls |
| 78 | F-8-8 | P3 | correctness | core-utils | S | events_to_schedule uses banker's rounding + degenerate horizon → distorted event timeline |
| 79 | F-2-7 | P3 | correctness | graph-shim | S | Embedder truncates vectors AFTER normalization, breaking unit-norm on model/dim mismatch |
| 80 | F-2-8 | P3 | robustness | graph-shim | S | FalkorDriver constructor schedules an unawaited concurrent index build (redundant + orphan-task) |
| 81 | F-2-9 | P3 | robustness | graph-shim | M | _shutdown stops the loop but never closes the embedded FalkorDB / leaves redislite subprocess |
| 82 | F-7-8 | P3 | correctness | report | S | plan_outline fallback returns 3 sections, violating the prompt's mandated minimum of 5 (and bypasses research grounding) |
| 83 | F-7-9 | P3 | robustness | report | S | Redundant save_report and double persistence between API thread and agent |
| 84 | F-9-6 | P3 | correctness | scripts | S | action_logger total_rounds metadata is hardcoded as hours*2, ignoring minutes_per_round |
| 85 | F-10-2 | P3 | robustness | frontend | S | GraphPanel link label background reads getBBox() on possibly-hidden text, can throw/NaN when edge labels are toggled off |
| 86 | F-10-3 | P3 | bottleneck | frontend | S | ResearchView: research progress log re-fetched forever if final tail returns empty |
| 87 | F-10-8 | P3 | robustness | frontend | S | Global 300s axios timeout applied to all GETs, including fast polling endpoints |
| 88 | F-10-10 | P3 | data-contract | frontend | S | Pipeline persisted in localStorage under a different key prefix than the rest of the app (mirofish vs drf) |
| 89 | F-10-11 | P3 | robustness | frontend | M | Markdown renderer drops link titles and is brittle on links containing parentheses/spaces; otherwise XSS-safe |
| 90 | F-10-13 | P3 | robustness | frontend | M | SimulationRunView handleGoBack swallows env-status errors and may navigate back while a simulation is still running |
| 91 | F-11-5 | P3 | robustness | setup-ops | S | Live key-test always uses table-default model/base, never the user's tuned values; reasoning models may false-pass |
| 92 | F-11-6 | P3 | config | setup-ops | S | .env.example 'OpenAI-compatible' example points at a DashScope/Qwen endpoint and model, inconsistent with setup.sh openai defaults |
| 93 | F-0-0 | P3 | bottleneck | research | S | Deep-research path can fire a second full tool-free synthesis LLM call redundantly |
| 94 | F-0-1 | P3 | concurrency | research | M | LoopDetectionMiddleware run_id is always "default" under the embedded client — per-run warning scoping collapses |
| 95 | F-0-2 | P3 | robustness | research | S | --prompt-file read raises uncaught traceback (exit 1), violating documented exit-code 3 for usage errors |
| 96 | F-0-3 | P3 | correctness | research | S | Stale docstring claims 80% thinking budget while code uses 50% (THINKING_BUDGET_RATIO=0.5) |
| 97 | F-0-6 | P3 | correctness | research | S | Claude credential preflight accepts a non-OAuth $ANTHROPIC_AUTH_TOKEN that the provider will reject |
| 98 | F-13-4 | P3 | security | x-security | S | Inconsistent path-traversal hardening: read/state endpoints lack the guard that delete() has |
| 99 | F-5-6 | P3 | robustness | personas | S | Realtime Twitter CSV uses first-profile keys as fieldnames → DictWriter ValueError on profiles with extra optional fields |
| 100 | F-10-7 | P2 | config | frontend | S | API base URL falls back to hardcoded http://localhost:5001, bypassing the Vite dev proxy and breaking non-localhost deploys |

---

## 3. P0 — breaks a real run / data loss / security hole

### x-concurrency — CROSS-CUTTING: lifecycle, concurrency, resource leaks

#### [F-12-0] OASIS simulation subprocesses are orphaned (not killed) across a backend restart — keep burning LLM credits forever

`P0` · `concurrency` · confidence **high** · effort **M** · `backend/app/services/simulation_runner.py` : 805-833, 1291-1393, 226-231

- **Symptom.** After the Flask backend crashes/restarts while a simulation is running, the long-lived OASIS subprocess (its own process group via start_new_session=True) survives, keeps invoking the LLM provider for every agent every round, and can never be stopped or cleaned up by the new process.
- **Root cause.** All kill/cleanup paths (stop_simulation, _terminate_process, cleanup_all_simulations) look up the live Popen object only in the in-memory class dict cls._processes, which is empty in a fresh process. run_state.json persists process_pid, but unlike the pipeline's research_pid + _kill_orphan_research reconcile path, nothing reads that PID on startup to terminate or reconcile orphaned simulations. There is no SimulationRunner.reconcile_orphans() and app/__init__.py never calls one.
- **Evidence.** `process = cls._processes.get(simulation_id) ... if process and process.poll() is None:  (stop_simulation, no PID fallback); cleanup uses processes = list(cls._processes.items()); state.process_pid is saved but never used to kill on restart.`
- **Impact.** Unbounded credit burn and a zombie OASIS process tree (twitter+reddit + any MCP/sandbox children) that the operator must hunt down by hand. run_state.json is also left stuck at RUNNING, so any logic keyed off it (and start_simulation's 'already running' guard at lines 341-343) misbehaves.
- **Fix.**

  ```
  Add SimulationRunner.reconcile_orphans() and invoke it from create_app() right next to PipelineOrchestrator.reconcile_orphans() (app/__init__.py, near line 57). Implementation:
  
  1) reconcile_orphans(): iterate persisted states under RUN_STATE_DIR. There is no list helper, so enumerate via `for sim_id in os.listdir(cls.RUN_STATE_DIR)` then `state = cls._load_run_state(sim_id)`. For each state with runner_status in {STARTING, RUNNING, STOPPING, PAUSED} and sim_id not in cls._processes (always true in a fresh process), call a guarded killer on state.process_pid, then mark the state FAILED (or STOPPED) with an error like "backend restarted; simulation reconciled" via cls._save_run_state, and also update state.json to a terminal status to match cleanup_all_simulations' behavior (lines 1348-1367) so the UI/poll loop stops. Wrap the whole loop in try/except so reconcile never blocks startup (mirror pipeline_orchestrator.py:792).
  
  2) Guarded killer (mirror _kill_orphan_research, pipeline_orchestrator.py:796-816) to avoid PID reuse: if not pid: return; coerce int; run `ps -p <pid> -o command=`; only proceed if returncode==0 AND the cmdline contains one of the expected sim scripts ("run_parallel_simulation.py", "run_twitter_simulation.py", "run_reddit_simulation.py") — NOT just "run_simulation". Then `os.killpg(os.getpgid(pid), signal.SIGTERM)`; catch ProcessLookupError/PermissionError/OSError/SubprocessError. (Windows: use the existing taskkill /T branch logic from _terminate_process, lines ~770-786, since os.getpgid/killpg are Unix-only.)
  
  3) Also make stop_simulation fall back to state.process_pid when cls._processes has no entry (lines 818-832): if the in-memory process is absent but state.process_pid is set, run the same guarded killpg path so an operator can stop a reconciled-but-still-living sim via the API. After killing, set process_pid to None to prevent later PID-reuse mis-kills (mirror pipeline_orchestrator.py:1413).
  
  4) To be safe against PID reuse on the killer guard, clear state.process_pid = None whenever a run reaches a terminal state on the normal exit path too.
  ```
- **Verified.**

  ```
  Confirmed against the actual code in backend/app/services/simulation_runner.py.
  
  (1) start_simulation launches the OASIS sim via subprocess.Popen with start_new_session=True (line 461), creating its own process group; the PID is saved as state.process_pid (line 468) and persisted to run_state.json (serialized line 189, reloaded line 281).
  
  (2) All kill/cleanup paths key only off the in-memory cls._processes dict (line 227), which is empty in a fresh process after a restart:
   - stop_simulation (lines 818-819): `process = cls._processes.get(simulation_id)` then `if process and process.poll() is None:` — no process_pid fallback. With _processes empty, it does NOTHING to the live process, yet still flips run_state to STOPPED (lines 834-838), making the state lie.
   - cleanup_all_simulations (lines 1320-1322): `processes = list(cls._processes.items())` — empty after restart; also short-circuits at lines 1304-1308 (`if not has_processes and not has_updaters: return`). It is an at-exit/signal cleanup of the CURRENT process anyway, not a cross-restart reconcile.
  
  (3) Confirmed asymmetry with the pipeline: PipelineOrchestrator.reconcile_orphans() exists (pipeline_orchestrator.py:764) and is invoked from create_app() (app/__init__.py:57); it reads the persisted research_pid and kills the orphan group via _kill_orphan_research (line 796) with a PID-reuse guard (ps -p ... -o command= checked for "deerflow_research.py", line 811). There is NO SimulationRunner.reconcile_orphans() (grep found zero definitions) and app/__init__.py:47 only calls SimulationRunner.register_cleanup(), never a reconcile. So persisted process_pid is written but never read on startup to terminate an orphaned sim.
  
  (4) Net effect: after a backend crash/restart, the OASIS process group (own session via start_new_session) survives, keeps invoking the LLM provider for every agent every round, and the new process has no path to stop it. run_state.json is stuck at STARTING/RUNNING, and start_simulation's guard (lines 341-343) then refuses to start a new run for that simulation_id. All quoted evidence and claimed impact match the code.
  
  P0 is appropriate: unbounded LLM credit burn (direct money) plus an unkillable zombie multi-process tree and permanently-stuck RUNNING state, with an established in-repo pattern (pipeline reconcile) showing this is a known, expected lifecycle hook that simply was not applied to simulations.
  ```

### x-security — CROSS-CUTTING: security & secrets

#### [F-13-0] No authentication + CORS '*' + 0.0.0.0 bind exposes all mutating pipeline/settings endpoints to the LAN

`P0` · `security` · confidence **high** · effort **M** · `backend/app/__init__.py, backend/run.py` : __init__.py:43; run.py:40,45

- **Symptom.** Every API endpoint (start/cancel/delete pipelines, switch LLM provider, write API keys, edit dossiers) is callable by any host that can reach the port, with no credential check.
- **Root cause.** create_app() registers all blueprints behind `CORS(app, resources={r"/api/*": {"origins": "*"}})` (line 43) with no auth middleware, no before_request auth, no decorator anywhere in backend/app/api. run.py defaults the bind host to `0.0.0.0` (`host = os.environ.get('FLASK_HOST', '0.0.0.0')`, line 40) and runs `app.run(host=host, ...)`. grep for login_required/requires_auth/abort(401|403) returns nothing.
- **Evidence.** `__init__.py:43 `CORS(app, resources={r"/api/*": {"origins": "*"}})`; run.py:40 `host = os.environ.get('FLASK_HOST', '0.0.0.0')`; run.py:45 `app.run(host=host, port=port, debug=debug, threaded=True)``
- **Impact.** Anyone on the same network (coffee shop, corp LAN, cloud VPC) can: trigger expensive research runs that burn the operator's LLM credits/credentials; cancel/delete others' pipelines and their handoff artifacts; overwrite the active LLM provider config and inject an attacker-controlled api_key/base_url into the persisted .env; and read research dossiers. CORS '*' additionally lets any web page the victim browses issue these mutating requests against localhost:5001 (DNS-rebinding/CSRF-style) since there is no CSRF token and no Origin check.
- **Fix.** Apply defense in depth. (1) Default to loopback: in backend/run.py:40 change the default to `os.environ.get('FLASK_HOST', '127.0.0.1')` so the service is not on all interfaces unless explicitly opted in. (2) Restrict CORS in backend/app/__init__.py:43 to the known frontend origin(s) from config (e.g. an APP_CORS_ORIGINS env list) instead of '*'. (3) Add an auth gate via a single `@app.before_request` that enforces a shared secret (e.g. constant-time compare of an `X-API-Token` header against an `APP_API_TOKEN` env var) on all non-GET/non-/health requests, or at minimum reject any request whose `request.remote_addr` is not loopback when no token is configured (fail closed). (4) Harden the .env-write path: validate/allowlist base_url scheme+host in Config.apply_provider before persisting, since a redirected base_url silently exfiltrates future prompts and keys. (5) Document explicitly that the service must never be exposed beyond loopback without configuring APP_API_TOKEN. The original proposed fix is correct; the additions are the loopback fail-closed default and base_url validation to blunt the credential/endpoint-injection escalation.
- **Verified.** All claims verified against the actual code. backend/app/__init__.py:43 is literally `CORS(app, resources={r"/api/*": {"origins": "*"}})`. The only `before_request` (line 63-68) is a request logger, not an auth gate. backend/run.py:40 defaults the bind to `host = os.environ.get('FLASK_HOST', '0.0.0.0')` and run.py:45 calls `app.run(host=host, ...)`. A grep across backend/app for login_required/requires_auth/abort(401|403)/Authorization/token returns no auth code — none exists. The blueprint route listing confirms numerous unauthenticated mutating endpoints: research run/cancel/resume/continue/delete/clean and dossier PUT (app/api/research.py), graph build/delete/reset (app/api/graph.py), simulation create/start/stop (app/api/simulation.py), report generate (app/api/report.py), and crucially POST /api/settings/llm (app/api/settings.py:31). I confirmed the highest-impact path end to end: set_llm_settings -> Config.apply_provider (config.py:175) writes attacker-supplied api_key/base_url/model into os.environ (line 218) AND persists them to the project-root .env via _persist_env (line 219, impl 222-248), with no credential check. This means any host on the LAN can persistently redirect the operator's LLM traffic and mirrored provider keys to an attacker-controlled base_url and exfiltrate prompts/keys across restarts, plus trigger expensive runs and delete others' pipelines/dossiers. The author is demonstrably security-aware (config.py:25-27 calls out 0.0.0.0 + Werkzeug debugger as a LAN RCE risk) yet still ships 0.0.0.0 + zero auth, so this is not intended-safe behavior. One nuance on the CORS-'*'/CSRF sub-claim: there are no cookies/sessions, and wildcard CORS forbids credentialed requests, so classic cookie-CSRF does not apply — but a no-auth API needs no credentials to be abused; any web page can fire simple/preflight-passing cross-origin requests, and DNS-rebinding can defeat Origin checks, so the cross-origin concern stands. The dominant vector remains direct unauthenticated LAN reachability. Not a misreading, not guarded, not dead code — REAL and currently true. P0 is correct: unauthenticated remote write of credentials/config (key+endpoint injection persisted to .env) plus arbitrary mutating control of the pipeline, reachable by default on all interfaces.

---

## 4. P1 — wrong or degraded output, or frequent failure

### x-security — CROSS-CUTTING: security & secrets

#### [F-13-1] API keys written to plaintext log file on every settings request (request-body DEBUG logging)

`P1` · `security` · confidence **high** · effort **S** · `backend/app/__init__.py, backend/app/utils/logger.py` : __init__.py:63-68; logger.py:30,46,67-74

- **Symptom.** POST /api/settings/llm and POST /api/settings/llm/test bodies contain `api_key`, and the full JSON body is written verbatim to logs/<date>.log.
- **Root cause.** The before_request hook logs `f"请求体: {request.get_json(silent=True)}"` at DEBUG (line 68) for any JSON request. setup_logger defaults `level=logging.DEBUG` (logger.py:30,46) and the RotatingFileHandler is set to `logging.DEBUG` (logger.py:74), writing to logs/YYYY-MM-DD.log. There is no redaction of sensitive fields. The same hook also logs base_url, model, etc.
- **Evidence.** `__init__.py:68 `logger.debug(f"请求体: {request.get_json(silent=True)}")`; logger.py:74 `file_handler.setLevel(logging.DEBUG)`; settings.py POST /llm accepts `api_key=data.get('api_key')``
- **Impact.** The operator's LLM API keys (LLM_API_KEY for openai/kimi/minimax/deepseek/qwen/glm) are persisted in cleartext on disk in dated rotating log files. Any local user, backup job, log-shipping agent, or support bundle exposes the keys. logs/ is gitignored so not committed, but the at-rest plaintext secret on the local filesystem is the leak.
- **Fix.**

  ```
  Two-layer fix.
  
  (1) Redact secrets before logging the body in backend/app/__init__.py. Replace the unconditional dump with a redacting helper rather than logging raw JSON:
  
  ```python
  _SENSITIVE_KEY_RE = re.compile(r'(api_?key|token|secret|password|authorization|bearer)', re.I)
  
  def _redact(obj):
      if isinstance(obj, dict):
          return {k: ('***REDACTED***' if _SENSITIVE_KEY_RE.search(k) else _redact(v))
                  for k, v in obj.items()}
      if isinstance(obj, list):
          return [_redact(v) for v in obj]
      return obj
  
  @app.before_request
  def log_request():
      logger = get_logger('mirofish.request')
      logger.debug(f"请求: {request.method} {request.path}")
      if request.content_type and 'json' in request.content_type:
          logger.debug(f"请求体: {_redact(request.get_json(silent=True))}")
  ```
  
  This masks api_key (and token/secret/password/authorization) while still logging base_url/model/provider for debugging.
  
  (2) Defense in depth in backend/app/utils/logger.py: stop hardcoding the file handler to DEBUG. Drive it from an env var so production isn't DEBUG by default, e.g. `file_handler.setLevel(getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO))` (or at minimum gate DEBUG on app.config['DEBUG']). Note redaction (1) is the load-bearing fix because nested secrets in any future JSON endpoint would otherwise still leak at DEBUG.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. The full chain is currently-true:
  
  1. backend/app/__init__.py:63-68 — the @app.before_request log_request() hook runs for EVERY request. When request.content_type contains 'json' (true for POST /api/settings/llm and /llm/test, which the frontend sends as application/json), it executes `logger.debug(f"请求体: {request.get_json(silent=True)}")`, serializing the entire request body verbatim with no redaction.
  
  2. backend/app/api/settings.py:34,40 (POST /llm) and 164 (POST /llm/test) — the body legitimately carries `api_key` (data.get('api_key')). So the operator's LLM API key is part of the dict that gets stringified into the log line.
  
  3. backend/app/utils/logger.py:30,46,68-75 — setup_logger defaults level=logging.DEBUG, calls logger.setLevel(DEBUG), and the RotatingFileHandler is hardcoded `file_handler.setLevel(logging.DEBUG)` writing to logs/YYYY-MM-DD.log. The 'mirofish.request' logger is created via get_logger -> setup_logger (default DEBUG), so DEBUG records DO reach the file. Only the console handler is capped at INFO (line 81), which is why this is silent on the terminal but persisted to disk.
  
  I verified there is NO redaction/masking anywhere in the request-logging path (the _sanitize_* helpers in graphiti_client/falkor_driver.py are unrelated Cypher-param sanitizers), and there is NO env-driven log-level override — the file handler is DEBUG regardless of production/DEBUG mode. So the secret is written in cleartext to a dated rotating log on every settings save/test.
  
  Severity P1 is correct, not P0: this is an at-rest local-disk secret leak (logs/ is gitignored, so not committed). Exploitation requires filesystem/backup/log-shipping/support-bundle access rather than a remote unauthenticated path, so it is high but not a directly remote-exploitable critical. P1 stands.
  ```

#### [F-13-2] Unauthenticated SSRF via /api/settings/llm/test base_url (arbitrary outbound request with reflected response)

`P1` · `security` · confidence **high** · effort **M** · `backend/app/api/settings.py` : 78-137, 161-169

- **Symptom.** An attacker-supplied base_url is passed straight into OpenAI(base_url=...).chat.completions.create(); the server makes a POST to that URL and reflects up to 300 chars of the response/error back to the caller.
- **Root cause.** test_llm_settings reads base_url from the request (`base_url = (data.get('base_url') or '').strip() or meta.get('default_base') ...`, line 167) with no scheme/host validation, then _test_openai_compat_provider builds `OpenAI(base_url=base_url, api_key=api_key, ...)` and issues a chat completion (line 108). On error the handler returns `msg[:300]` of the exception (lines 118-124), which includes response body text. No URL allowlist or scheme check exists anywhere (grep for urlparse/scheme finds none).
- **Evidence.** `settings.py:108 `response = OpenAI(**client_kwargs).chat.completions.create(**kwargs)` with `client_kwargs={'api_key': api_key, 'base_url': base_url, ...}`; settings.py:167 base_url taken from request; settings.py:124 returns `... + msg` where `msg = str(e)[:300]``
- **Impact.** Because the endpoint is unauthenticated and the server binds 0.0.0.0, a remote/LAN attacker (or any web page via CORS '*') can make the backend send POSTs to internal services and cloud metadata endpoints (e.g. http://169.254.169.254/...), with part of the response reflected — a usable blind/partial-read SSRF. It also sends a chosen Authorization: Bearer header to the chosen host (credential probe / exfil aid).
- **Fix.**

  ```
  In `test_llm_settings` (and equally in `Config.apply_provider`, config.py:200-201, which persists an attacker-influenced base_url via POST /api/settings/llm), validate `base_url` before constructing the OpenAI client:
  1. Require an http(s) scheme; reject http unless the host is explicit loopback for dev.
  2. Resolve the hostname and reject if any resolved address is private/loopback/link-local/reserved (RFC1918 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1, fc00::/7, fe80::/10) to block DNS-rebinding/metadata access. Better: restrict to an allowlist derived from PROVIDER_META `default_base` hosts plus an explicit operator-configured allowlist.
  3. Do not reflect raw upstream response/exception bodies in the `error` field — return only the mapped status-code hint (lines 111-117) and a generic message, not `str(e)`.
  4. Gate the settings endpoints (and the rest of /api) behind authentication, and/or bind FLASK_HOST to 127.0.0.1 by default and tighten CORS off `*` for state/secret-touching routes.
  Note the same unvalidated base_url path exists in the persisting POST /api/settings/llm endpoint (config.py:184-201), so the validation helper should be applied in both places.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. `POST /api/settings/llm/test` -> `test_llm_settings` (settings.py:140-176) takes `base_url` directly from the request body with zero validation: `base_url = (data.get('base_url') or '').strip() or meta.get('default_base') ...` (line 167). No urlparse/scheme/host/IP checks exist anywhere (grep confirms). It is passed verbatim into `client_kwargs = {"api_key": api_key, "base_url": base_url, ...}` (lines 85-88) and used to issue a live request: `OpenAI(**client_kwargs).chat.completions.create(**kwargs)` (line 108), which POSTs to `{base_url}/chat/completions` with an attacker-chosen `Authorization: Bearer {api_key}` header. On error, the upstream exception string (which the OpenAI SDK populates with the HTTP response body) is reflected back: `msg = str(e)`, truncated to `msg[:300] + '…'` and returned in `error` (lines 118-124) -> usable partial/blind SSRF read.
  
  Auth and exposure claims also verified: `app/__init__.py:63-68` `before_request` only logs (no auth on any route); CORS is `origins: "*"` (line 43); `run.py:40` defaults `FLASK_HOST=0.0.0.0`. So a LAN attacker or malicious origin can drive the backend to POST to internal services / cloud metadata (e.g. 169.254.169.254) and exfiltrate a chosen bearer to an arbitrary host.
  
  One nuance (does not invalidate the finding): the handler returns early with "API key required" when `api_key` is empty (lines 80-81), and only falls back to the saved `Config.LLM_API_KEY` when `provider == Config.LLM_PROVIDER` (lines 164-166). So to drive the outbound request the attacker must supply a non-empty `api_key` — trivial (any string), and they fully control both `base_url` and `api_key`. The SSRF is therefore fully reachable; the saved-key fallback is a secondary credential-probe aid, not a prerequisite. Severity confirmed P1 (network-position/origin-dependent, partial-read reflection, no RCE).
  ```

### core-utils — Config, LLM clients, utils, settings API, app entry

#### [F-8-0] API keys leak to disk via DEBUG request-body logging

`P1` · `security` · confidence **high** · effort **S** · `backend/app/__init__.py` : 63-68

- **Symptom.** Every JSON request body is logged in full at DEBUG, including POST /api/settings/llm and /api/settings/llm/test, whose bodies contain the raw `api_key` for kimi/minimax/deepseek/qwen/glm/openai providers.
- **Root cause.** The before_request middleware does `logger.debug(f"请求体: {request.get_json(silent=True)}")` with no field redaction. The file handler in logger.py is fixed at logging.DEBUG (setup_logger level=logging.DEBUG, file_handler.setLevel(logging.DEBUG)), so the secret is persisted to logs/<date>.log (and 5 rotated backups) on disk regardless of console level.
- **Evidence.**

  ```
  if request.content_type and 'json' in request.content_type:
      logger.debug(f"请求体: {request.get_json(silent=True)}")
  ```
- **Impact.** Provider API keys (and any future sensitive request payloads) are written in cleartext to log files that rotate but persist, and can be committed/shared/backed up. A single settings change permanently records the key on disk.
- **Fix.**

  ```
  Redact secrets before logging request bodies, and skip body logging for the settings blueprint entirely. Minimal, targeted patch to the before_request hook in backend/app/__init__.py:
  
      _SENSITIVE_BODY_PATHS = ('/api/settings/',)  # never log these bodies at all
      _SECRET_KEY_HINTS = ('api_key', 'apikey', 'key', 'token', 'secret', 'password')
  
      @app.before_request
      def log_request():
          logger = get_logger('mirofish.request')
          logger.debug(f"请求: {request.method} {request.path}")
          if request.content_type and 'json' in request.content_type:
              if any(request.path.startswith(p) for p in _SENSITIVE_BODY_PATHS):
                  logger.debug("请求体: <redacted: settings endpoint>")
                  return
              body = request.get_json(silent=True)
              if isinstance(body, dict):
                  safe = {k: ('***REDACTED***' if any(h in k.lower() for h in _SECRET_KEY_HINTS) else v)
                          for k, v in body.items()}
                  logger.debug(f"请求体: {safe}")
              else:
                  logger.debug(f"请求体: {body}")
  
  Notes: (1) Redaction must be field-name-based and recursive if nested payloads can carry secrets; the flat-dict version above covers the current settings payloads. (2) Better still, gate body logging behind an explicit opt-in env (e.g. LOG_REQUEST_BODIES=true) so it is off by default in any deployed environment. (3) Remediation cleanup: the already-leaked keys in backend/logs/*.log (the minimax `sk-cp-...` key) should be treated as compromised — rotate/revoke them and scrub or delete the existing log files. (4) Consider rotating real keys out of test flows.
  ```
- **Verified.**

  ```
  CONFIRMED, and already realized in this repo. The before_request middleware in backend/app/__init__.py:63-68 logs every JSON request body at DEBUG via `logger.debug(f"请求体: {request.get_json(silent=True)}")` with zero redaction. The settings API (backend/app/api/settings.py) accepts a raw `api_key` in the JSON body for both POST /api/settings/llm (set_llm_settings, lines 31-50) and POST /api/settings/llm/test (test_llm_settings, lines 140-176), and the before_request hook runs before those handlers, so the secret is captured.
  
  The persistence claim is also correct: in backend/app/utils/logger.py the file handler is a RotatingFileHandler fixed at logging.DEBUG (line 74, file_handler.setLevel(logging.DEBUG)) and setup_logger defaults level=logging.DEBUG (line 30). The console handler is INFO-only (line 81), but the file handler still records DEBUG regardless of console verbosity. I reproduced the logger wiring in isolation: the `mirofish.request` logger (created by get_logger in the middleware) has effectiveLevel=DEBUG, propagate=False, and its own RotatingFileHandler at DEBUG, and a synthetic body with `api_key` was written verbatim to the log file.
  
  Most damning: grepping the existing backend/logs/*.log files shows REAL secrets already on disk, written by exactly this code path (logger name/funcName/line confirm it: `[mirofish.request.log_request:68]`). Example from 2026-06-12.log:178 and multiple 2026-06-09.log entries: `请求体: {'api_key': 'sk-cp-***REDACTED***', 'provider': 'minimax'}`. A minimax provider key (and a deepseek test key) are sitting in cleartext in rotated log files right now.
  
  There is no nearby guard, try/except redaction, or config gate — the only `sanitize`/`redact` hits in the codebase are an unrelated FalkorDB Cypher-param sanitizer. The code is reachable, on the hot request path, and demonstrably executes (logs prove it).
  
  One correction to the finding's impact wording: `.gitignore` does exclude backend/logs/ (lines 53,57-58), so these keys are NOT auto git-committed — the "can be committed" risk is partly mitigated. But the core exposure stands: secrets persist in cleartext on disk indefinitely (rotation deletes only the 6th-oldest, not all), and would be swept into any backup, log-shipping pipeline, support bundle, or shared diagnostic archive. Severity P1 is appropriate (credential disclosure to local disk; not directly remotely exploitable, and gitignore blunts accidental publication, so not P0).
  ```

#### [F-8-1] _persist_env writes API key to .env without escaping → newline/`=` injection & corruption

`P1` · `security` · confidence **high** · effort **S** · `backend/app/config.py` : 222-248, 200-216

- **Symptom.** apply_provider() persists the user-supplied api_key/base_url/model verbatim into the root .env via `out.append(f"{key}={val}")` with no quoting or sanitization.
- **Root cause.** _persist_env naively formats `KEY=VALUE`. A value containing a newline injects arbitrary additional .env lines (e.g. a pasted key with a trailing newline+`LLM_PROVIDER=...`), and a value containing `=`, `#`, or leading/trailing spaces is mis-parsed by dotenv on next load. The whole .env is then re-read with override=True on import, so corruption silently rewrites other config.
- **Evidence.**

  ```
  out.append(f"{key}={remaining.pop(key)}")
  ...
  for key, val in remaining.items():
      out.append(f"{key}={val}")
  ```
- **Impact.** Malformed or malicious api_key/base_url from the settings API can corrupt .env (breaking subsequent boots) or inject unintended environment variables consumed by the DeerFlow subprocess. Even benign keys with stray whitespace become unusable after persistence.
- **Fix.** Sanitize and validate before persisting. In apply_provider, reject any api_key/base_url/model containing control chars or newlines (e.g. raise ValueError if re.search(r"[\r\n]", v) or '\x00' in v) rather than silently stripping, so the user sees a clear error. Validate base_url with urllib.parse.urlparse and require scheme in ('http','https') and a netloc before accepting it. In _persist_env, do not hand-format KEY=VALUE: use python-dotenv's set_key (from dotenv import set_key; set_key(env_path, key, val, quote_mode='always')) which performs proper quoting/escaping, or at minimum reject values containing '\n'/'\r' and wrap each value in double quotes with internal quotes/backslashes escaped. Apply the same newline/control-char rejection to the /llm/test path values for defense in depth even though it does not persist. Keep the .strip() for cosmetic trimming, but it is not a substitute for the explicit newline rejection since strip leaves embedded newlines intact.
- **Verified.** Confirmed by reading the code and a runnable simulation. Reachable path: POST /api/settings/llm -> set_llm_settings (backend/app/api/settings.py:38) forwards request JSON api_key/base_url/model directly to Config.apply_provider. apply_provider (config.py:201-215) only applies .strip(), which removes leading/trailing whitespace but NOT embedded newlines, then calls _persist_env. _persist_env (config.py:222-248) writes each value verbatim as out.append(f"{key}={val}") (lines 238, 242) with no quoting/escaping. I simulated the exact upsert logic: an LLM_API_KEY value containing embedded "\nLLM_PROVIDER=attacker\nFLASK_DEBUG=true" splits into multiple lines in .env, and dotenv parses the injected keys (LLM_PROVIDER became "attacker", FLASK_DEBUG became "true"). Because config.py:14 does load_dotenv(project_root_env, override=True) on import, the injected vars are silently applied on next boot. Crucially, an injected FLASK_DEBUG=true re-enables the Werkzeug debugger that the code itself flags as a LAN-RCE hazard (config.py:25-28), and other config (provider/base_url/keys) can be overwritten/corrupted, breaking subsequent boots. The legitimately-set env_updates also flow into the DeerFlow subprocess via os.environ inheritance (noted config.py:445), so injected provider-specific vars could reach that subprocess. Severity is P1 not P0: exploitation requires reaching the settings endpoint (an admin/config surface), the most damaging effects (debug re-enable, provider override) manifest on restart via override=True rather than immediately, and .strip() does neutralize the narrower "stray leading/trailing whitespace" sub-claim. The injection and =/#/internal mis-parse claims are valid. Net: a real env-file injection / config-corruption defect.

### sim-runtime — Simulation runtime (runner/manager/ipc) + API

#### [F-6-5] Server-restart resume reports stale process_pid and runs without a live process handle/monitor; stop becomes a no-op against orphaned subprocesses

`P1` · `robustness` · confidence **medium** · effort **M** · `backend/app/services/simulation_runner.py` : 281, 805-833, 1469-1477

- **Symptom.** If Flask restarts while a simulation subprocess (detached via start_new_session) keeps running, reloaded run state shows the OLD process_pid and runner_status='running', but cls._processes/_monitor_threads are empty. stop_simulation finds no handle and marks STOPPED without killing the real process; get_running_simulations returns [].
- **Root cause.** _load_run_state restores process_pid from disk (line 281) but the OS process, handle, and monitor are gone after restart; there is no reconciliation (no os.kill(pid,0) liveness, no re-attach by PID/pgid). The class assumes subprocess and monitor share the Flask process lifetime.
- **Evidence.** `_load_run_state: `process_pid=data.get("process_pid")`; stop_simulation: `process = cls._processes.get(simulation_id)` then kill block skipped if None, yet status still set to STOPPED; get_running_simulations iterates only `cls._processes`.`
- **Impact.** Orphaned subprocesses keep writing actions/DB and burning LLM budget after a restart; the UI shows them running but stop does nothing to the real process. Resource leak and risk of double-runs on force-restart.
- **Fix.**

  ```
  Mirror the proven PipelineOrchestrator orphan-handling pattern for SimulationRunner.
  
  1) Add SimulationRunner.reconcile_orphans() and call it in backend/app/__init__.py right after register_cleanup() (alongside the PipelineOrchestrator call). It should scan RUN_STATE_DIR for run_state.json files whose runner_status is in {RUNNING, STARTING, STOPPING, PAUSED} but whose simulation_id is not in cls._processes (always true on a fresh process). For each, read the persisted process_pid and verify liveness AND identity to avoid PID reuse: run `ps -p <pid> -o command=` and confirm the command line matches the OASIS run script (the same script in `cmd` used at start_simulation line 452-462) before acting. If alive+matching, kill the whole group via os.killpg(os.getpgid(pid), SIGTERM) (fall back to SIGKILL after a timeout), then persist runner_status=STOPPED/FAILED with an error like 'backend restarted; orphan simulation terminated'. If not alive/not matching, just mark the persisted state terminal so the UI stops polling.
  
  2) Harden stop_simulation (lines 818-834): when cls._processes.get(simulation_id) is None, fall back to killing by the persisted state.process_pid using the same PID-reuse-safe ps check + os.killpg, instead of silently marking STOPPED. Only set runner_status=STOPPED after the kill attempt (or confirmed-dead), and log when no live process could be found.
  
  3) Optionally persist pgid alongside process_pid for robustness, though on Unix with start_new_session=True the pgid equals the pid (as the code already assumes at line 789-790), so os.getpgid(pid) suffices.
  
  Reuse the PID-reuse guard from PipelineOrchestrator._kill_orphan_research (ps command-line check) verbatim — it already handles ProcessLookupError/PermissionError/OSError and is the established convention in this codebase.
  ```
- **Verified.**

  ```
  Confirmed by reading backend/app/services/simulation_runner.py and the startup wiring in backend/app/__init__.py.
  
  Facts: (1) OASIS subprocess is detached with start_new_session=True (line 461), so it outlives the Flask process. (2) _processes (line 227) and _monitor_threads (line 229) are class dicts populated only in start_simulation (lines 470, 480); after a restart they are empty. (3) _load_run_state restores process_pid (line 281) and runner_status='running' (line 261) from disk with zero liveness validation or re-attach. (4) stop_simulation (lines 818-834): process = cls._processes.get(simulation_id) is None after restart, so the guarded kill block `if process and process.poll() is None` is skipped entirely, yet line 834 unconditionally sets RunnerStatus.STOPPED and persists it — the real detached process group is never signaled. (5) get_running_simulations (lines 1474-1477) iterates only cls._processes, returning [] despite live orphans. (6) cleanup_all_simulations (line 1320) also only iterates cls._processes, and register_cleanup installs signal/atexit handlers that fire on graceful shutdown of the CURRENT process — they cannot kill a prior process's orphans. There is no SimulationRunner.reconcile_orphans and none is called at startup.
  
  Decisive corroboration: the sibling subsystem already fixes this exact bug. __init__.py:57 calls PipelineOrchestrator.reconcile_orphans(), which finds persisted running pipelines with no in-memory thread and kills the orphan research process group by persisted PID via _kill_orphan_research (pipeline_orchestrator.py:795-816), even guarding against PID reuse with `ps -p <pid> -o command=` before os.killpg. SimulationRunner has no analogous reconcile and is never wired into startup reconciliation — the authors knew the pattern but did not apply it to OASIS sims.
  
  Impact matches the claim: after a Flask restart (very common in dev with the reloader) mid-simulation, the orphaned OASIS process keeps writing actions/DB and burning LLM budget; the UI shows it 'running' while stop is a no-op against the real process, and there is risk of a duplicate run if the sim is restarted. Genuine resource/cost leak and correctness defect, gated on the specific restart-while-running trigger, so P1 (not P0).
  ```

### graph-build — Graph builder + ontology

#### [F-3-0] OntologyGenerator._validate_and_process assumes entity_types/edge_types are lists; non-list LLM output crashes the whole ontology stage

`P1` · `robustness` · confidence **high** · effort **S** · `backend/app/services/ontology_generator.py` : 260-345

- **Symptom.** When the LLM returns valid JSON but with entity_types/edge_types as a dict (e.g. {"Person": {...}}), a string, null, or returns the top-level object as a JSON array, _validate_and_process raises (TypeError on `for entity in result["entity_types"]`, or slicing/`+` on the wrong type). The ontology endpoint and the orchestrator ONTOLOGY stage fail outright.
- **Root cause.** The guards only check membership (`if "entity_types" not in result`) and never coerce/validate the value type. `result` itself is trusted to be a dict (chat_json can legally return a list since json.loads of a top-level array is valid JSON). Every subsequent op (`for entity in ...`, `result["entity_types"][:-to_remove]`, `.extend(...)`, `len(...) > MAX`) assumes a list.
- **Evidence.** `Line 269 `for entity in result["entity_types"]:` and line 333 `result["entity_types"] = result["entity_types"][:-to_remove]`; guard at 261 only `if "entity_types" not in result:`; chat_json (llm_client.py:209 `return json.loads(cleaned)`) can return a non-dict.`
- **Impact.** A single malformed-but-valid-JSON LLM response (common with non-OpenAI/CLI providers and smaller models) aborts ontology generation with an opaque TypeError instead of degrading gracefully, blocking the entire pipeline at stage 1.
- **Fix.**

  ```
  Add type coercion at the top of `_validate_and_process` and skip non-dict entries inside both loops and the name-set comprehension, matching actors.py's contract:
  
  ```python
  def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
      # Coerce top-level: chat_json can legally return a list/str/None (json.loads of valid non-object JSON)
      if not isinstance(result, dict):
          result = {}
      # Coerce the two collection fields to lists (membership-only checks below cannot do this)
      if not isinstance(result.get("entity_types"), list):
          result["entity_types"] = []
      if not isinstance(result.get("edge_types"), list):
          result["edge_types"] = []
      if not isinstance(result.get("analysis_summary"), str):
          result["analysis_summary"] = ""
  
      # Drop malformed entries so later dict access is safe
      result["entity_types"] = [e for e in result["entity_types"] if isinstance(e, dict)]
      result["edge_types"] = [e for e in result["edge_types"] if isinstance(e, dict)]
  
      for entity in result["entity_types"]:
          ...
      for edge in result["edge_types"]:
          ...
      # name-set must tolerate entries missing "name"
      entity_names = {e.get("name") for e in result["entity_types"]}
  ```
  
  This is strictly broader than the proposed fix (which omitted guarding line 313's `e["name"]` and the slicing/extend sites). Once the lists contain only dicts and the fields are guaranteed lists, the existing `[:-to_remove]`, `.extend(...)`, and `len(...) > MAX` operations are all safe.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. `_validate_and_process` (backend/app/services/ontology_generator.py:257-345) is called directly on the return value of `self.llm_client.chat_json(...)` (line 197-204) with no type coercion. `chat_json` -> `_parse_json_response` returns `json.loads(cleaned)` verbatim (llm_client.py:209, 217, 259) with NO `isinstance(..., dict)` guard, so a valid-but-non-dict JSON (top-level array, dict-shaped entity_types, string, or null) flows straight through; the only safety net (ValueError at chat_json.py:136) fires only when nothing parses at all, not for valid-non-dict output. Concrete crash paths, all currently reachable:
  - result is a list: line 261 `"entity_types" not in result` checks element membership (False for typical dict-lists), then line 262 `result["entity_types"] = []` raises TypeError (list indices must be integers).
  - result is a str: substring check passes, line 262 raises `'str' object does not support item assignment`.
  - entity_types is a dict: line 269 `for entity in result["entity_types"]` iterates keys (strings), line 270/271 crash with `'str' object ...` errors.
  - entity_types is null: line 269 `for entity in None` -> TypeError (NoneType not iterable).
  - Even after the proposed list-coercion, line 313 `{e["name"] for e in ...}` still assumes each element is a dict with a "name" key, so non-dict / name-less entries crash there too.
  The guards (lines 261/263/265) only test membership, never the value's type, exactly as the finding claims. The same module's sibling `actors.py` uses the opposite, defensive contract throughout (`if not isinstance(x, dict/list)` and `if not isinstance(item, dict): continue` at lines 75, 78, 80, 113, 130, 244-245, 429-430...), so the "always degrade, never abort" expectation is real and this module violates it. Ontology is pipeline stage 1, so the failure aborts the whole run with an opaque TypeError rather than degrading. Severity P1 is correct: it requires a specific (though common with smaller/CLI providers) malformed-but-valid-JSON response, not every run, so it's a robustness defect, not an always-on P0.
  ```

### personas — Persona + simulation-config generation

#### [F-5-0] Realtime-saved reddit_profiles.json omits required keys (mbti/gender/age/country) → OASIS KeyError on interrupted runs

`P1` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/oasis_profile_generator.py` : 61-87, 942-947

- **Symptom.** If a profile-generation run is interrupted (or any consumer reads the file before the final overwrite), OASIS's generate_reddit_agent_graph crashes with KeyError because the realtime file written via to_reddit_format() can be missing keys it accesses unconditionally.
- **Root cause.** save_profiles_realtime() writes [p.to_reddit_format() for p in existing_profiles]. to_reddit_format() adds age/gender/mbti/country ONLY when truthy (lines 74-85). But OASIS generate_reddit_agent_graph does direct key access: agent_info[i]['mbti'], ['gender'], ['age'], ['country'] (verified in backend/.venv/.../oasis/social_agent/agents_generator.py:585-590). The final _save_reddit_json() always supplies defaults (lines 1220-1223), so the happy-path final file is safe; the realtime artifact written to the SAME path (reddit_profiles.json) is not.
- **Evidence.** `to_reddit_format: `if self.mbti: profile["mbti"] = self.mbti` (line 78-79); OASIS: `profile["other_info"]["mbti"] = agent_info[i]["mbti"]` (agents_generator.py:585)`
- **Impact.** Crash-recovery / interrupted runs leave an unloadable profiles file; any path that consumes the realtime file before the final save fails hard.
- **Fix.** Make the realtime writer produce the identical OASIS-loadable schema as the final writer. Cleanest: have save_profiles_realtime() reuse _save_reddit_json() so both paths emit the same defaulted JSON. Concretely, in oasis_profile_generator.py replace the realtime reddit branch (lines 943-947) with a call to self._save_reddit_json(existing_profiles, realtime_output_path) (it already iterates and applies age->30, gender via _normalize_gender, mbti->'ISTJ', country->'中国', and includes user_id). This guarantees byte-for-byte schema parity between the two writers at the same path. Alternatively (broader blast radius), change to_reddit_format() to always emit age/gender/mbti/country with the same defaults so every serialization path is OASIS-safe; prefer the first option to keep the change localized to the realtime writer and avoid altering to_reddit_format()'s other callers/tests.
- **Verified.**

  ```
  Confirmed by reading the actual code. save_profiles_realtime() serializes via to_reddit_format() to reddit_profiles.json (simulation_manager.py:368; oasis_profile_generator.py:945-947), and to_reddit_format() adds age/gender/mbti/country only when truthy (lines 74-81). The final _save_reddit_json() writes the SAME path (simulation_manager.py:400 -> line 1112) but always supplies defaults (lines 1220-1223). OASIS generate_reddit_agent_graph does unconditional key access agent_info[i]["mbti"]/["gender"]/["age"]/["country"] (backend/.venv/.../oasis/social_agent/agents_generator.py:586-589), which raises KeyError if any key is missing. No try/except guards the load; run_reddit_simulation.py:537 only checks os.path.exists.
  
  The keys are genuinely absent in realistic cases: fallback profiles created on exception (oasis_profile_generator.py:986-994 and 1042-1050) never set age/gender/mbti/country, leaving them at None (dataclass defaults lines 48-51), and save_profiles_realtime() is explicitly invoked even for those fallbacks (line 1052). Happy-path profiles can also lack keys since lines 275-278 use profile_data.get(...) returning None when the LLM/rule output omits them.
  
  The happy path is safe because the final defaulted save_profiles() (simulation_manager.py:398) overwrites the realtime artifact before the sim runs. The defect is currently true on the interrupted-run / early-consumer path: if the process is killed or errors between the last realtime write (lines 1024/1052) and the final save (line 398), the on-disk reddit_profiles.json is the non-defaulted realtime artifact and is not OASIS-loadable. The readiness check (simulation.py:311-329) can flip preparing->ready based on state+file existence, decoupling readiness from the defaulted save. This is a genuine two-writers/one-path data-contract divergence, not a misreading.
  
  Severity sits at the P1/P2 boundary: it is interruption/early-read-gated rather than a happy-path crash, but the failure is a hard, unguarded KeyError and the divergent schema is produced for fallback profiles on every run, so P1 is defensible.
  ```

#### [F-5-2] LLM agent-config keyed by cfg['agent_id'] — KeyError or str/int mismatch silently discards an entire batch's LLM configs

`P1` · `robustness` · confidence **high** · effort **S** · `backend/app/services/simulation_config_generator.py` : 1182, 1191

- **Symptom.** A whole batch of 15 agents silently falls back to rule-based config (losing all LLM-derived activity/stance/sentiment), or the lookup misses for every agent.
- **Root cause.** Line 1182 builds `llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}`. (a) If any returned config object omits 'agent_id', this raises KeyError, caught by the broad `except Exception` at line 1183 → the ENTIRE batch is discarded and every agent uses rules. (b) LLMs frequently emit agent_id as a string ('5'); dict keys become strings while the lookup at line 1191 uses int agent_id (start_idx+i) → every .get() misses → silent full fallback to rules for the batch.
- **Evidence.** ``llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}` (1182) vs `cfg = llm_configs.get(agent_id, {})` where `agent_id = start_idx + i` (1190-1191)`
- **Impact.** Degraded output: LLM-tailored agent behavior is dropped without any warning for that batch; stance/sentiment/activity revert to coarse type defaults, undermining simulation realism.
- **Fix.**

  ```
  Replace the fragile line 1182 lookup so a single malformed entry cannot void the batch, and normalize key types. In simulation_config_generator.py, change:
  
      result = self._call_llm_with_retry(prompt, system_prompt)
      llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
  
  to build the map defensively (int-coerced keys, skip malformed entries individually, keep a positional list for fallback):
  
      result = self._call_llm_with_retry(prompt, system_prompt)
      raw_cfgs = result.get("agent_configs", []) or []
      llm_configs = {}
      for pos, cfg in enumerate(raw_cfgs):
          if not isinstance(cfg, dict):
              continue
          aid = cfg.get("agent_id")
          if aid is None:
              continue
          try:
              llm_configs[int(aid)] = cfg          # coerce "5" / 5.0 -> 5
          except (TypeError, ValueError):
              logger.warning(f"跳过无法解析 agent_id 的配置: {aid!r}")
  
  Then in the build loop, fall back to positional matching when the id-keyed lookup misses (covers the case where the LLM omitted/garbled agent_id but returned configs in input order):
  
      cfg = llm_configs.get(agent_id)
      if not cfg and i < len(raw_cfgs) and isinstance(raw_cfgs[i], dict):
          cfg = raw_cfgs[i]                          # positional fallback
      if not cfg:
          cfg = {}
  
  (keep the existing `if not cfg: cfg = self._generate_agent_config_by_rule(entity)` rule fallback for the remaining misses). This isolates malformed entries, eliminates the KeyError-voids-whole-batch path, and fixes the str/int key mismatch. Optionally narrow the broad `except Exception` at line 1183 so genuine LLM/transport failures are still logged distinctly from per-entry parse issues.
  ```
- **Verified.**

  ```
  Verified directly in /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py. Both failure modes claimed are real and currently true:
  
  (a) Line 1182 builds the lookup with a hard subscript: `llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}`. If ANY returned config object omits "agent_id", `cfg["agent_id"]` raises KeyError. The dict comprehension fails atomically, and the broad `except Exception` at line 1183 catches it, setting `llm_configs = {}` and logging only a generic warning ("Agent配置批次LLM生成失败"). Every entity in the batch (AGENTS_PER_BATCH = 15, confirmed line 244) then falls to `_generate_agent_config_by_rule` via the `if not cfg` branch at line 1194. One malformed entry silently voids 15 LLM configs.
  
  (b) `_call_llm_with_retry` returns `json.loads(content)` (line 695), which preserves JSON types verbatim — no schema coercion anywhere. The lookup at line 1191 uses `agent_id = start_idx + i` where `start_idx = batch_idx * AGENTS_PER_BATCH` (int) and `i` (int), so the lookup key is always a Python int. If the LLM emits `"agent_id": "5"` (a JSON string — a very common LLM deviation despite the prompt showing a number), the dict key becomes str "5" while `.get(5)` looks up int 5 → miss → that agent silently reverts to rules. If the whole batch is emitted as strings, every lookup misses → full silent fallback for the batch.
  
  The impact is exactly as claimed: silent degradation. LLM-derived activity_level/stance/sentiment_bias/active_hours are dropped without an actionable warning, reverting to coarse type defaults (the only mitigation is the researched-actor influence_weight override at lines 1197-1199 and 1219-1221, which restores ONLY influence_weight for entities matched to research actors — all other LLM-tailored fields are still lost). This undermines simulation realism but does not crash the pipeline or corrupt data, so P1 (not P0) is correct: silent robustness/correctness degradation in a non-critical-availability path.
  
  Severity confirmed as P1. The proposed fix direction is correct; tightened below to isolate malformed entries (so one never voids the batch), coerce keys to int, and add a positional fallback when agent_id is absent.
  ```

### memory — Graph memory readers/updaters/tools

#### [F-4-0] FOLLOW/MUTE typed feedback edges are never written (action_args key mismatch)

`P1` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/zep_graph_memory_updater.py` : 441-443, 449-462  ·  ↺ overlaps EXECPLAN

- **Symptom.** With SIM_TYPED_FEEDBACK_EDGES enabled (default true), FOLLOWED and MUTED typed edges are silently never created in the graph, even though every FOLLOW/MUTE action is enriched with a target name by the simulator.
- **Root cause.** The simulator (backend/scripts/run_parallel_simulation.py lines 828-839) writes the FOLLOW/MUTE target into action_args['target_user_name']. But _TYPED_EDGE_MAP reads FOLLOW from keys ('followee_name','target_name','followee') and MUTE from ('target_name','mutee_name') — none of which is 'target_user_name'. In _write_typed_edges the loop over `keys` yields target=='' so `if not author or not target ...: continue` skips every FOLLOW/MUTE, and no add_triplet is ever called.
- **Evidence.** `_TYPED_EDGE_MAP: "FOLLOW": ("FOLLOWED", ("followee_name", "target_name", "followee")), "MUTE": ("MUTED", ("target_name", "mutee_name")) ; simulator: action_args['target_user_name'] = target_name (FOLLOW/MUTE).`
- **Impact.** The 'golden thread' typed-edge feedback loop loses all follow/mute relationships. The graph never records who followed/muted whom during simulation, degrading downstream insight_forge relationship chains and panorama facts.
- **Fix.**

  ```
  In backend/app/services/zep_graph_memory_updater.py, change _TYPED_EDGE_MAP so FOLLOW and MUTE include the actually-written key 'target_user_name' first, keeping the others as harmless fallbacks:
  
      "FOLLOW": ("FOLLOWED", ("target_user_name", "followee_name", "target_name", "followee")),
      "MUTE":   ("MUTED",   ("target_user_name", "target_name", "mutee_name")),
  
  This aligns the typed-edge contract with the enrichment performed in run_parallel_simulation.py:_enrich_action_context (lines 818-839) and with the module's own _describe_follow/_describe_mute, which already read target_user_name. No other code path needs changing; existing guards/try-except remain. (Optional: add a one-line comment noting target_user_name is the canonical enrichment key to prevent regression.)
  ```
- **Verified.** Verified true by reading the actual code and data. (1) SIM_TYPED_FEEDBACK_EDGES defaults to 'true' (backend/app/config.py:362), so _write_typed_edges runs by default. (2) The simulator enriches FOLLOW/MUTE actions by writing the target into action_args['target_user_name'] only — run_parallel_simulation.py:830 (FOLLOW) and :839 (MUTE); confirmed in real recorded data (backend/uploads/simulations/*/reddit/actions.jsonl show FOLLOW with action_args={"follow_id":..,"target_user_name":"Google"}). (3) _TYPED_EDGE_MAP (zep_graph_memory_updater.py:441-442) looks FOLLOW up under ("followee_name","target_name","followee") and MUTE under ("target_name","mutee_name") — none is 'target_user_name'. (4) The keys followee_name/mutee_name are never written anywhere in the codebase; 'target_name' is only a local variable name inside _enrich_action_context, never an action_args key. (5) The enriched dict flows verbatim (add_activity_from_dict line 352 -> AgentActivity.action_args -> _write_typed_edges line 456), so for FOLLOW/MUTE the loop over keys yields target=='' and the guard `if not author or not target ...: continue` (line 461) skips every FOLLOW/MUTE, so add_triplet is never called for FOLLOWED/MUTED. This is the ONLY place that writes FOLLOWED/MUTED edges (no alternate path). The bug is isolated to FOLLOW/MUTE: other actions (LIKE_POST->post_author_name, REPOST->original_author_name, QUOTE_POST->original_author_name/quoted_author_name, CREATE_COMMENT->post_author_name, LIKE/DISLIKE_COMMENT->comment_author_name) have keys that DO match the enrichment, so they work. Corroborating internal evidence: the module's own _describe_follow (line 130) and _describe_mute (line 190) read action_args['target_user_name'], proving target_user_name is the canonical key and the typed-edge map is the inconsistent outlier. Severity P1 is appropriate: an entire relationship class (FOLLOWED/MUTED) is silently never written to the structured typed-edge layer, on by default, degrading downstream relationship-chain queries. (Not P0 because the relationship still survives in the free-text episode via _describe_follow/_describe_mute, and the code is best-effort/try-except so it never crashes.)

### report — Report agent + API

#### [F-7-0] force_regenerate leaves stale report folder; get_report_by_simulation returns a non-deterministic (often stale) report

`P1` · `correctness` · confidence **high** · effort **M** · `backend/app/services/report_agent.py` : 2947-2965 (get_report_by_simulation), backend/app/api/report.py:60-122

- **Symptom.** After regenerating a report (force_regenerate=true), the chat endpoint, status check, and by-simulation getter may serve the OLD report content instead of the freshly regenerated one, or flip between them across requests.
- **Root cause.** generate_report() mints a brand-new report_id (api/report.py:111) and never deletes the prior report folder for the same simulation_id. get_report_by_simulation() then iterates os.listdir(REPORTS_DIR) and returns the FIRST folder whose meta.json matches simulation_id, with NO created_at ordering (unlike list_reports which sorts at line 2990). os.listdir order is filesystem-arbitrary, so which of the multiple same-simulation reports gets returned is non-deterministic.
- **Evidence.** `for item in os.listdir(cls.REPORTS_DIR): ... if report and report.simulation_id == simulation_id: return report  (no sort, first match)`
- **Impact.** Users who regenerate get stale/inconsistent reports; chat_with_report_agent (report_agent.py:2241) and check_report_status read whichever folder listdir yields first. Disk also grows unbounded with orphaned report folders.
- **Fix.** Two-part fix. (A) Make the getter deterministic: in get_report_by_simulation, collect ALL folder/JSON matches into a list and return max(matches, key=lambda r: r.created_at) (mirror the list_reports sort at report_agent.py:2990), instead of returning the first listdir hit. This alone resolves the stale/non-deterministic selection. (B) Fix the orphan-folder leak and the stale early-existence short-circuit: when force_regenerate is True (api/report.py around lines 60-86), before launching the new task, enumerate prior reports for that simulation_id (ReportManager.list_reports(simulation_id=...)) and delete them via the existing ReportManager.delete_report(report_id) (which rmtrees the folder). Do the deletion of the old report only after the new one is saved, or guard so a failed regeneration does not leave the simulation with zero reports — simplest safe approach: after run_generate successfully saves the new COMPLETED report (api/report.py:161-171), delete the other reports for that simulation_id except the new report_id. Optionally maintain a simulation_id -> latest_report_id index file to avoid O(n) directory scans, but that is an optimization, not required for correctness.
- **Verified.** Confirmed against the actual code. (1) api/report.py:111 mints a fresh report_id (uuid) on every generate call; force_regenerate (line 60) only bypasses the early "report exists" return at lines 73-85 and does NOT delete/supersede the prior report. generate_report (report_agent.py:1953) and save_report (2873) contain no cleanup of prior folders (verified: no rmtree/os.remove/delete_report in the generate_report body). So each regenerate leaves the old folder(s) on disk for the same simulation_id. (2) get_report_by_simulation (report_agent.py:2947-2965) iterates os.listdir(REPORTS_DIR) and returns the FIRST matching folder with NO created_at sort, while the sibling list_reports (2968-2992) explicitly sorts by created_at desc at line 2990. os.listdir order is filesystem-arbitrary, so the returned report among multiple same-simulation reports is non-deterministic and often stale. (3) Blast radius confirmed: get_report_by_simulation feeds the by-simulation getter (api/report.py:335), the generate-status poll (api/report.py:234, can return a stale COMPLETED report_id), the interview-unlock/status endpoint (api/report.py:733), and the chat agent (report_agent.py:2241). The non-force early-existence check (api/report.py:74) can also short-circuit on a stale COMPLETED report. The audit's quoted evidence is accurate; the only minor over-statement is "flip between requests" — listdir order is typically stable within a process but is not guaranteed and changes as folders are added, so stale-selection is certain while per-request flipping is environment-dependent. Severity P1 is correct: real user-visible correctness defect across multiple endpoints plus a secondary unbounded-disk leak; not P0 since it needs the regenerate flow and no data is corrupted.

#### [F-7-2] Native tool-calling section path does NOT enforce minimum tool calls, so sections can be written with zero graph grounding

`P1` · `data-contract` · confidence **high** · effort **M** · `backend/app/services/report_agent.py` : 1540-1587 (_generate_section_native)

- **Symptom.** With REPORT_NATIVE_TOOLS=true on an OpenAI-compatible provider, a section can be returned on the first model turn with zero tool calls, producing ungrounded prose that violates the system prompt's '每个章节至少调用4次工具' contract.
- **Root cause.** The native loop returns content the moment the model emits prose (line 1577 `if content.strip(): return content`) without ever checking tool_calls_count against MIN_TOOL_CALLS_PER_SECTION. The ReAct path (lines 1759, 1873) strictly enforces the minimum and rejects premature Final Answers; the native path silently dropped that guard.
- **Evidence.** `# 无更多工具调用（或已达上限）→ 收尾出正文\n            if content.strip():\n                return content  (no min-tool-call gate)`
- **Impact.** Reports generated via the native path may contain fabricated/unsupported analysis with no simulation/graph evidence, directly undermining the product's core promise of evidence-grounded forecasts. Behavior diverges from the ReAct path for the same prompt.
- **Fix.**

  ```
  Add a minimum-tool-call gate before returning content, mirroring the ReAct guard, while keeping the loop bounded (max_iterations=10) so it always terminates via the existing fallback at lines 1582-1587. Replace the return block (lines 1576-1580):
  
      # 无更多工具调用（或已达上限）→ 收尾出正文
      if content.strip():
          # 工具调用不足且仍可继续检索 → 拒绝过早出正文，强制补足实证（对齐 ReAct 路径）
          if tool_calls_count < self.MIN_TOOL_CALLS_PER_SECTION and tool_calls_count < max_tool_calls:
              messages.append({"role": "assistant", "content": content})
              messages.append({
                  "role": "user",
                  "content": (
                      f"你只调用了 {tool_calls_count} 次工具，少于本章要求的至少 "
                      f"{self.MIN_TOOL_CALLS_PER_SECTION} 次。请勿现在输出正文，"
                      "继续发起工具调用以补足实证后再撰写本章。"
                  ),
              })
              continue
          return content
      # 达到工具上限但模型还没出正文：显式要求收尾
      messages.append({"role": "user", "content": "请基于以上工具结果直接输出本章完整 Markdown 正文。"})
  
  Notes: (1) Guard on BOTH conditions `tool_calls_count < MIN` and `< max_tool_calls` so that if the model genuinely refuses to call tools, the loop still exits when budget is spent (or via max_iterations) rather than spinning — the existing line 1582 fallback then forces final prose, matching ReAct's degrade-don't-fail philosophy. (2) Appending `content` as an assistant message preserves conversation validity (an OpenAI assistant turn with content but no tool_calls is valid). (3) This leaves the calls-and-content path (line 1547) untouched since it already loops.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. In `_generate_section_native` (backend/app/services/report_agent.py, lines 1540-1587), the native tool-calling loop returns the model's prose the instant it appears: lines 1576-1578 do `if content.strip(): return content` with NO check against `self.MIN_TOOL_CALLS_PER_SECTION` (=Config.REPORT_AGENT_MIN_TOOL_CALLS, default 4). The branch at line 1547 only consumes tool calls when `calls and tool_calls_count < max_tool_calls`; if the model returns content with zero tool_calls on the first turn (calls empty), control falls straight to line 1576 and returns ungrounded prose. This contradicts both the native-path system prompt it itself injects (lines 1514-1519: "撰写正文前至少调用 N 次工具") and the ReAct path, which strictly enforces the minimum in BOTH branches: line 1759 (`if tool_calls_count < min_tool_calls:` on Final Answer → re-prompt with REACT_INSUFFICIENT_TOOLS_MSG and continue) and line 1873 (same for plain-content output → REACT_INSUFFICIENT_TOOLS_MSG_ALT). So the guard exists in ReAct and was genuinely dropped in the native path.
  
  The path is live, not dead code: `_generate_section` (line 1458) dispatches to `_generate_section_native` whenever `self.llm.supports_native_tools()` is true, which (llm_client.py lines 148-152) is true exactly when `Config.REPORT_NATIVE_TOOLS=true` AND provider is OpenAI-compatible AND an OpenAI client exists — precisely the configuration named in the symptom. Default is false, so this only bites operators who flip the flag, which is why P1 (not P0) is appropriate: it is a real data-contract/grounding defect that produces evidence-free report sections undermining the product's core promise, but it is gated behind a non-default config flag and limited to OpenAI-compatible providers.
  
  The root-cause claim, quoted evidence, and impact are all accurate. The only nuance is that a compliant model often returns content together with tool_calls; the calls-and-content branch (line 1547) handles that by continuing the loop, so the leak is specifically the content-without-tool-calls (or tool-budget-exhausted-with-content) cases.
  ```

#### [F-7-1] ReportConsoleLogger attaches a FileHandler to MODULE-GLOBAL loggers, cross-contaminating concurrent reports' console logs and racing on handler lists

`P1` · `concurrency` · confidence **high** · effort **L** · `backend/app/services/report_agent.py` : 334-385 (ReportConsoleLogger), 180-181 (api spawns daemon thread per request)

- **Symptom.** When two report generations run concurrently (double-click, or generate + force_regenerate, or two simulations), each report's console_log.txt receives BOTH reports' log lines; handler add/remove can race.
- **Root cause.** _setup_file_handler attaches the per-report FileHandler to the shared loggers logging.getLogger('mirofish.report_agent') and 'mirofish.zep_tools'. Those loggers are process-global singletons, and report generation runs in unsynchronized daemon threads (api/report.py:180, no lock anywhere). All concurrently-active handlers receive every record from those loggers. Handler list mutation (addHandler/removeHandler) under concurrent close()/__del__ is not thread-safe.
- **Evidence.** `loggers_to_attach = ['mirofish.report_agent','mirofish.zep_tools']; for logger_name in loggers_to_attach: target_logger = logging.getLogger(logger_name); if self._file_handler not in target_logger.handlers: target_logger.addHandler(self._file_handler)`
- **Impact.** Per-report console_log.txt files are unreliable/intermingled under concurrency; the /console-log API returns mixed data; possible duplicate/dangling handlers if one thread's __del__ runs while another is logging.
- **Fix.**

  ```
  Stop routing per-report logs through the shared global loggers. Give each report its own child logger and capture only its own records.
  
  In ReportConsoleLogger._setup_file_handler, create a per-report child logger and attach the FileHandler there (so it never receives other reports' records):
  
      self._report_logger_names = [
          f'mirofish.report_agent.{self.report_id}',
          f'mirofish.zep_tools.{self.report_id}',
      ]
      for name in self._report_logger_names:
          lg = logging.getLogger(name)
          lg.setLevel(logging.INFO)
          lg.propagate = True   # still bubbles up to the existing console/file handlers on the parent
          if self._file_handler not in lg.handlers:
              lg.addHandler(self._file_handler)
  
  and update close() to detach from those same per-report logger names.
  
  Then ReportAgent and zep_tools must emit on the per-report child logger for the records that should land in console_log.txt (e.g., pass a logger named f'mirofish.report_agent.{report_id}' into the agent / set self._log = logging.getLogger(...) keyed by report_id, instead of the module-global `logger`). This guarantees each report's FileHandler only sees that report's records.
  
  If touching emission sites is too invasive, the minimal alternative is to keep attaching to the global loggers but add a per-report Filter so the handler only writes records belonging to this report:
  
      class _ReportIdFilter(logging.Filter):
          def __init__(self, report_id): super().__init__(); self.report_id = report_id
          def filter(self, record): return getattr(record, 'report_id', None) == self.report_id
      self._file_handler.addFilter(_ReportIdFilter(self.report_id))
  
  ...combined with a LoggerAdapter/contextvar that stamps record.report_id on every emit within the generation thread. Either approach eliminates the cross-contamination.
  
  Additionally: guard handler add/remove and serialize concurrent generations. Add a process-wide threading.Lock and wrap the addHandler/removeHandler/close() blocks, and either (a) add a per-simulation in-flight guard in report.py before spawning run_generate (reject/return the existing task if one is already generating for that simulation_id) or (b) use a per-simulation lock so two threads don't generate the same report concurrently. The contextvar/per-report-logger approach is the load-bearing fix; the lock + in-flight guard close the secondary race and the duplicate-generation foot-gun.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. ReportConsoleLogger._setup_file_handler (backend/app/services/report_agent.py:354-363) attaches the per-report FileHandler to two module-global singleton loggers: 'mirofish.report_agent' and 'mirofish.zep_tools'. Those are the same loggers used for actual emission (report_agent.py:32 `logger = get_logger('mirofish.report_agent')` and zep_tools.py:24 `logger = get_logger('mirofish.zep_tools')`), and get_logger -> logging.getLogger(name) returns the process-global singleton (backend/app/utils/logger.py:101). The attached handler has NO per-report filter, so once two ReportConsoleLoggers are active, every record emitted on those loggers (from any thread) is dispatched to BOTH reports' FileHandlers -> each report's console_log.txt receives both reports' lines, and ReportManager.get_console_log (report_agent.py:2404) / the /console-log API return mixed data.
  
  Concurrency is genuinely reachable with no serialization: report generation runs in unsynchronized daemon threads (backend/app/api/report.py:180), there is a second independent entry point that builds a ReportAgent and calls generate_report (backend/app/services/pipeline_orchestrator.py:1700-1716), and there are ZERO locks/semaphores in either report.py or report_agent.py (grep count = 0). The existing-report guard (report.py:73-85) only short-circuits when a COMPLETED report already exists with force_regenerate=False, so double-click before completion, generate+force_regenerate, or two simulations all spawn concurrent generations.
  
  The handler-list race is partially mitigated by CPython (Logger.addHandler/removeHandler acquire the logging module lock), so the .handlers list itself won't corrupt; but __del__/close() (report_agent.py:365-385) closing a FileHandler's stream while another thread is mid-emit can still raise "I/O operation on closed file". The dominant, certain defect is the cross-contamination of the user-facing per-report log artifact under concurrency. Severity P1 is correct: silent data-integrity corruption of a user-visible artifact, conditional on concurrency rather than crashing the common single-report path.
  ```

### scripts — Simulation scripts (run_parallel/reddit/twitter) + export

#### [F-9-0] Single-platform standalone scripts emit no actions.jsonl, breaking completion detection and all downstream consumers

`P1` · `correctness` · confidence **high** · effort **M** · `backend/scripts/run_twitter_simulation.py, backend/scripts/run_reddit_simulation.py` : run_twitter_simulation.py:506-683; run_reddit_simulation.py:499-672

- **Symptom.** When SimulationRunner launches a single-platform run (run_twitter_simulation.py or run_reddit_simulation.py), no twitter/actions.jsonl or reddit/actions.jsonl is ever written. The runner's monitor never sees a 'simulation_end' event, the process defaults to wait-command mode and never exits on its own, so the run is stuck in RUNNING indefinitely; run_summary.json / forum export are empty.
- **Root cause.** The standalone TwitterSimulationRunner / RedditSimulationRunner main loops call env.step but never instantiate a PlatformActionLogger and never call log_action / log_round_*/log_simulation_end. Only run_parallel_simulation.py wires action_logger. Completion detection (simulation_runner.py:633 detects event_type=='simulation_end') and the demo export (export_demo_site_data.py:export_forum reads actions.jsonl) both depend on that file. The process also never exits (no --no-wait passed by the runner at simulation_runner.py:430-438, and wait_for_commands defaults True), so the process.poll() fallback at simulation_runner.py:537 never fires either.
- **Evidence.** `run_twitter_simulation.py:629-633 `await self.env.step(actions)` ... no action_logger anywhere in file; simulation_runner.py:633 `if event_type == "simulation_end":`; simulation_runner.py:403-406 selects `run_twitter_simulation.py`/`run_reddit_simulation.py`.`
- **Impact.** Any run dispatched for a single platform hangs forever from the API/orchestrator's view and produces an empty forum feed and empty run summary. The two standalone scripts are silently out of sync with the parallel script's logging contract that every downstream stage assumes.
- **Fix.**

  ```
  Prefer the finding's option (b) as lowest-risk: route single-platform runs through run_parallel_simulation.py, which already implements the full action_logger contract. In SimulationRunner.start_simulation, instead of selecting run_twitter_simulation.py / run_reddit_simulation.py, always launch run_parallel_simulation.py and pass a platform-selection flag (e.g. add --twitter-only / --reddit-only to run_parallel_simulation.py if not already present, or a --platform arg) so exactly one platform runs but actions.jsonl + simulation_end are still emitted. This guarantees completion detection, run_summary, and forum export all work for single-platform runs and keeps a single logging code path.
  
  Independently, regardless of which option is chosen, the runner should pass --no-wait when it launches a script as part of an automated/orchestrated run (or stop relying on the IPC wait loop for completion). Today the omission of --no-wait means even a correctly-logging script could keep the process alive after simulation_end; combined with a monitor that also keys completion off simulation_end this is fine for parallel today, but passing --no-wait (and/or having the monitor treat simulation_end on all enabled platforms as terminal and then actively SIGTERM the child) removes the dependency on an external close_env command and is a defense-in-depth fix.
  
  If option (a) is chosen instead, the two standalone scripts must import PlatformActionLogger, call log_simulation_start before the loop, log_round_start/log_round_end around each round, log_action for every action fetched from the DB (via fetch_new_actions_from_db), and log_simulation_end after the loop — mirroring run_parallel_simulation.py — so the contract every downstream stage assumes is satisfied.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code.
  
  1) The two standalone scripts never write actions.jsonl. `grep` for PlatformActionLogger / action_logger / actions.jsonl / fetch_new_actions returns ZERO hits in run_twitter_simulation.py and run_reddit_simulation.py. Their main loops (run_twitter_simulation.py:608-643, run_reddit_simulation.py:602-633) call `await self.env.step(actions)` but instantiate no logger and emit no log_action/log_round_*/log_simulation_end. By contrast run_parallel_simulation.py imports `from action_logger import SimulationLogManager, PlatformActionLogger` (line 158) and wires log_simulation_start/round_start/round_end/log_simulation_end and log_action throughout (lines 1326-1473, 1548-1703). The logging asymmetry between the parallel script and the two standalone scripts is exactly as the finding claims.
  
  2) Completion detection depends on that file. simulation_runner._monitor_simulation reads twitter/actions.jsonl and reddit/actions.jsonl (lines 498-499, 513-522) and only marks a platform completed when it parses `event_type == "simulation_end"` (lines 633-650). _check_all_platforms_completed (721-746) decides which platforms are "enabled" purely by whether actions.jsonl exists. For a single-platform standalone run no such file is ever created, so neither simulation_end nor the file-existence check ever fires.
  
  3) The process never exits on its own. start_simulation builds the child cmd with only `--config` and optional `--max-rounds` (lines 430-438); it never passes `--no-wait`. In both scripts `wait_for_commands = not args.no_wait` with `--no-wait` defaulting False (run_twitter:700-705/723, run_reddit:690-695/713), so after the loop ends the script enters the `while not _shutdown_event.is_set()` IPC wait loop (run_twitter:650-668, run_reddit:640-658) and blocks indefinitely. Therefore `process.poll()` stays None forever, the monitor's exit-code==0 -> COMPLETED fallback (simulation_runner:511, 535-540) never runs, and the only ways out are an external close_env IPC command or SIGTERM/stop — which an automated poller waiting for COMPLETED would never send. Net: runner_status stays RUNNING forever.
  
  4) The path is reachable. The public API endpoint start_simulation (backend/app/api/simulation.py:1447) reads `platform = data.get('platform','parallel')`, explicitly validates and ACCEPTS 'twitter'/'reddit'/'parallel' (lines 1517-1521), and forwards platform to SimulationRunner.start_simulation (1599-1605), which dispatches run_twitter_simulation.py / run_reddit_simulation.py for the single-platform cases (simulation_runner:402-409). So twitter-only / reddit-only is a fully exposed, validated, user-triggerable code path, not dead code.
  
  5) Downstream consumers break as claimed: export_demo_site_data.export_forum reads `{plat}/actions.jsonl` (export_demo_site_data.py:80-85) and would produce an empty feed; write_run_summary aggregates from get_actions which reads the same files (simulation_runner:945-975), so run_summary would be empty too.
  
  Severity P1 is correct: it is a real correctness/liveness bug (single-platform run hangs in RUNNING indefinitely, empty forum + empty summary) but the default and the only path the pipeline orchestrator actually uses is platform="parallel" (pipeline_orchestrator.py:1619), which logs correctly. So the main automated forecast pipeline is unaffected; only direct single-platform API invocations hit it. Not P0 (default flow works), clearly more than P2 (a publicly reachable endpoint hangs forever with no self-recovery).
  ```

#### [F-9-1] Initial posts and scheduled CREATE_POST/FOLLOW events are double-logged into actions.jsonl, inflating total_actions and the forum feed

`P1` · `data-contract` · confidence **high** · effort **S** · `backend/scripts/run_parallel_simulation.py` : 1340-1381 (twitter initial), 1145-1186 (scheduled), 1445-1461 (per-round DB fetch); 664-753 (fetch_new_actions_from_db)

- **Symptom.** Each initial post (and each scheduled-event CREATE_POST) appears twice in twitter/reddit actions.jsonl, and total_actions is over-counted. The same duplication hits initial follows once they land in the trace table.
- **Root cause.** Initial posts are manually logged via action_logger.log_action (e.g. line 1354) AND written to the OASIS trace table by env.step. fetch_new_actions_from_db starts at last_rowid=0 and, on the first main-loop round, re-reads those same 'create_post' trace rows and logs them a second time. FILTERED_ACTIONS only excludes {'refresh','sign_up'} (line 618), so 'create_post' and 'follow' from the round-0 setup phase are re-emitted. last_rowid is never advanced past the pre-loop rows.
- **Evidence.** `run_parallel_simulation.py:1354 `action_logger.log_action(round_num=0, ... action_type="CREATE_POST" ...)`; 1368 `await result.env.step(initial_actions)`; 1330 `last_rowid = 0`; 1446 `actual_actions, last_rowid = fetch_new_actions_from_db(db_path, last_rowid, agent_names)`; 618 `FILTERED_ACTIONS = {'refresh', 'sign_up'}`.`
- **Impact.** actions.jsonl (consumed by export_forum, the run-summary aggregator, and the report agent) contains duplicate posts; total_actions and per-round counts are wrong; the forum feed shows duplicated initial content.
- **Fix.**

  ```
  Advance last_rowid past all round-0 setup writes before entering the main loop, in BOTH run_twitter_simulation and run_reddit_simulation. After inject_initial_follows returns and before `for round_num in range(total_rounds)`, add a one-line sync that reads the current max rowid of the trace table so the first fetch only picks up genuinely new actions:
  
      # After initial_posts step + inject_initial_follows, before the main loop:
      try:
          if os.path.exists(db_path):
              _conn = sqlite3.connect(db_path)
              _row = _conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM trace").fetchone()
              _conn.close()
              if _row and _row[0] is not None:
                  last_rowid = int(_row[0])
      except Exception as e:
          log_info(f"初始 rowid 同步失败，忽略（可能产生 round-0 重复）: {e}")
  
  Use COALESCE(MAX(rowid),0) so an empty/just-created trace table yields 0 (no behavior change when there are no initial posts/follows). This is preferable to the "stop manually logging" alternative, because the manual log_action calls are what attach correct round_num=0 / is_scheduled_event metadata and human-readable agent names — relying solely on fetch_new_actions_from_db would lose the round-0 tagging and the is_scheduled_event flag.
  
  Important scope note the finding missed: do NOT also try to fix scheduled events the same way. Scheduled events fire mid-loop (line 1412) and are written to trace AFTER last_rowid was advanced for that round but are fetched (line 1446) within the SAME round — so the MAX(rowid) pre-loop sync does NOT cover them. For scheduled CREATE_POST, the correct fix is to stop double-handling: EITHER drop the manual log_action at line 1169 (and rely on the same-round fetch to emit it), OR exclude scheduled-event rows from the fetch. The simplest robust approach is to remove the manual log_action in fire_scheduled_events (lines 1168-1175) and let `fired` count reflect the env.step submission only without `total_actions += fired` at line 1415, since the same-round fetch already logs and counts those create_post rows. Apply the analogous change to the reddit path (lines 1642/1645/1676). Without addressing this second path, scheduled CREATE_POST events remain double-counted even after the pre-loop last_rowid sync.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code + the OASIS package source.
  
  DOUBLE-LOGGING CHAIN (verified):
  1. backend/scripts/run_parallel_simulation.py:1330 sets `last_rowid = 0` before the main loop (reddit mirror at :1552).
  2. Initial posts are manually written to actions.jsonl via action_logger.log_action (line 1354) with `total_actions += 1`, AND submitted to OASIS via `await result.env.step(initial_actions)` (line 1368).
  3. env.step routes the ManualAction(CREATE_POST) through Platform.create_post, which calls `self.pl_utils._record_trace(user_id, ActionType.CREATE_POST.value, ...)` — verified in .venv/.../oasis/social_platform/platform.py:400-418. So a `create_post` row lands in the `trace` table.
  4. On the FIRST main-loop round, `fetch_new_actions_from_db(db_path, last_rowid, ...)` (line 1446) runs `SELECT ... FROM trace WHERE rowid > ?` with last_rowid still 0 (line 699), so it re-reads those round-0 rows.
  5. FILTERED_ACTIONS = {'refresh','sign_up'} (line 618) does NOT exclude 'create_post' or 'follow', so each round-0 post is re-emitted to actions.jsonl and `total_actions += 1` a SECOND time (lines 1453-1460).
  
  SCHEDULED EVENTS (verified): fire_scheduled_events runs at line 1412, BEFORE the fetch at line 1446 within the same round. It manually logs CREATE_POST (line 1169) AND env.step writes to trace (line 1181). The fetch later in that same round re-reads the just-written 'create_post' row → double-counted. Confirmed.
  
  IMPACT (verified): action_logger.log_action (backend/scripts/action_logger.py:43-66) appends one JSON line per call with no dedup. actions.jsonl is consumed by export_demo_site_data.py (lines 81-85) to build the forum feed (also no dedup), plus the run-summary/report. So duplicate posts in the feed and inflated total_actions/per-round counts are real, currently-true consequences.
  
  ONE CORRECTION TO THE FINDING: The claim that initial FOLLOW edges are double-logged is WRONG. inject_initial_follows (lines 1089-1142) does NOT call action_logger.log_action — it only does add_edge + env.step. So initial follows are logged exactly ONCE (via the round-0 DB fetch). They are over-counted by zero, not by one. The core defect (initial CREATE_POST and scheduled CREATE_POST double-counting on BOTH twitter and reddit paths) is real and correctly diagnosed; the follow over-statement is a minor inaccuracy that does not change the verdict.
  
  Severity P1 is appropriate: this is a data-contract defect that corrupts a primary output artifact (actions.jsonl / forum feed) and the headline total_actions metric consumed downstream by export, the aggregator, and the report agent. It is not P0 (no crash/data loss, simulation still runs), but it systematically biases every run's reported activity and the user-facing feed.
  ```

### x-concurrency — CROSS-CUTTING: lifecycle, concurrency, resource leaks

#### [F-12-1] Report stage reads the knowledge graph while the simulation→graph feedback writer is still flushing (concurrent read/write on one FalkorDB graph)

`P1` · `concurrency` · confidence **medium** · effort **M** · `backend/app/services/pipeline_orchestrator.py` : 1655-1718

- **Symptom.** In a full pipeline run with SIM_GRAPH_FEEDBACK=true (the default), the RUN poll loop breaks as soon as runner_status becomes COMPLETED and immediately proceeds to write_run_summary and the REPORT stage, which query the graph — but the ZepGraphMemoryUpdater background thread may still be draining its queue and writing typed edges/episodes to the same graph.
- **Root cause.** runner_status is set to COMPLETED inside SimulationRunner._read_action_log the instant a simulation_end event is parsed (lines 646-650), which happens while the OASIS process is still alive and being monitored. The updater is only flushed/stopped in _monitor_simulation's finally block (after process.poll() returns non-None) and in stop_updater — there is no barrier joining the monitor thread or the updater worker before REPORT begins. All graph ops run through one shared GraphitiRuntime event loop against one redislite FalkorDB graph, so report reads interleave with in-flight feedback writes.
- **Evidence.** `orchestrator: if rs.runner_status == RunnerStatus.COMPLETED: break ... SimulationRunner.write_run_summary(...) ... agent = ReportAgent(graph_id=graph_id, ...); updater stop only fires in _monitor_simulation finally and SIM_GRAPH_FEEDBACK defaults to 'true'.`
- **Impact.** Non-deterministic report inputs: the forecast can be generated against a partially-ingested feedback graph, and run_summary.json can be aggregated from a still-growing actions.jsonl. Report content varies run-to-run for identical simulations and may miss late-round dynamics the feedback loop was meant to surface.
- **Fix.**

  ```
  After the RUN poll loop breaks on COMPLETED, insert a clean join barrier BEFORE write_run_summary and the REPORT stage. Concretely, in pipeline_orchestrator.py right after self._complete_stage(state, STAGE_RUN, ...) (line 1668) and before the run_summary block at 1670:
  
  1) Join the monitor thread to guarantee the OASIS process has fully exited and the monitor's finally has run: expose/look up SimulationRunner._monitor_threads[sim_id] (or add a SimulationRunner.await_completion(sim_id, timeout) helper) and call monitor_thread.join(timeout=...). This ensures process.poll() != None and that _monitor_simulation reached its finally.
  
  2) Explicitly drain the feedback writer regardless of the monitor: call ZepGraphMemoryManager.stop_updater(sim_state.simulation_id) (its stop() flushes _flush_remaining then joins the worker, zep_graph_memory_updater.py:288-303). This is idempotent and safe even if the monitor already stopped it.
  
  Only after both barriers complete should write_run_summary (which reads actions.jsonl) and ReportAgent run. Do not treat the in-flight COMPLETED flag (set at simulation_runner.py:648 while the process is still alive) as 'fully ingested'. Guard the new calls so they run only when Config.SIM_GRAPH_FEEDBACK and graph_id were enabled for this run, and wrap in try/except so a join/flush hiccup downgrades to a logged warning rather than failing the pipeline.
  ```
- **Verified.**

  ```
  Verified against the actual code; every link in the chain holds.
  
  1) Early-COMPLETED claim CONFIRMED. simulation_runner.py:648 sets state.runner_status = RunnerStatus.COMPLETED inside _read_action_log the instant simulation_end is parsed for all platforms. _read_action_log is called from the monitor loop while the process is still alive (while process.poll() is None, lines 511-526), and _save_run_state runs every 2s (line 525). The independent process-exit COMPLETED at line 538 is a separate, later path.
  
  2) Visibility is immediate (worse than the finding states). get_run_state returns the in-memory cls._run_states[sim_id] object (lines 239-240), the SAME object the monitor thread mutates. So the orchestrator poll sees COMPLETED with zero disk lag the moment line 648 fires.
  
  3) Orchestrator races ahead. pipeline_orchestrator.py:1655-1656 breaks the poll loop on COMPLETED, then immediately calls SimulationRunner.write_run_summary (1678) and constructs/runs ReportAgent against the same graph_id (1700-1716). grep confirms NO stop_updater, NO monitor-thread join, NO ZepGraphMemoryManager reference anywhere in the RUN->REPORT path (only stop_simulation in the cancel branch at 1636).
  
  4) The feedback writer is genuinely asynchronous and lagging. add_activity_from_dict -> add_activity -> _activity_queue.put (zep_graph_memory_updater.py:331). A background daemon worker (_worker_loop, 359-388) drains the queue and writes graph.add (408) + add_triplet (471) in batches of 5 with SEND_INTERVAL 0.5s and retries up to ~12s. The flush+join barrier is stop() (288-303), invoked ONLY in _monitor_simulation's finally (line 569), which runs only after process.poll() returns non-None.
  
  5) Single shared backend CONFIRMED. graphiti_client/runtime.py has a process-global GraphitiRuntime singleton (513-522) with one persistent asyncio loop on a background thread (53-55) and one redislite FalkorDB. ReportAgent tools (panorama_search/quick_search/insight_forge) all query self.graph_id (report_agent.py:1139-1244) — the same graph the updater writes. The only lock (_ensure_lock, 176-178) guards graph creation, not read/write ordering.
  
  Net: with SIM_GRAPH_FEEDBACK and SIM_TYPED_FEEDBACK_EDGES both defaulting to true (config.py:360,362), the report can be generated against a partially-ingested feedback graph, and run_summary.json can aggregate a still-growing actions.jsonl, because the post-simulation_end interview/late-round writes and queued typed edges are still being flushed.
  
  One correction to the finding's framing: because all graph ops cooperatively share ONE event loop, this is NOT data corruption — it is a determinism/completeness defect (report inputs vary run-to-run and may omit late-round feedback the loop exists to surface). Severity P1 stands: default-on, silent, affects every full pipeline run.
  ```

### setup-ops — Setup/ops (setup.sh, doctor, packaging, env)

#### [F-11-1] pyproject requires-python >=3.11 silently drops the local graph backend (falkordblite/redis) on 3.11

`P1` · `config` · confidence **high** · effort **S** · `backend/pyproject.toml` : 5,23-25

- **Symptom.** A user (or CI) that resolves the backend on Python 3.11 — explicitly permitted by `requires-python = ">=3.11"` — gets an install with NO embedded FalkorDB and NO redis, so the default GRAPH_BACKEND=auto knowledge graph cannot start.
- **Root cause.** `requires-python = ">=3.11"` allows 3.11, but the two packages that provide the only zero-config graph backend are gated behind `; python_version>='3.12'` markers (`falkordblite>=0.5.0; python_version>='3.12'`, `redis<8; python_version>='3.12'`). On 3.11 the markers evaluate false, pip/uv skips them, and `redislite.async_falkordb_client` is unimportable. setup.sh always pins 3.12 (`uv sync --python 3.12`) so it masks this, but `uv sync` without the pin, a bare `pip install`, or the doctor's '3.11.*' accepted case will produce a broken install.
- **Evidence.**

  ```
  requires-python = ">=3.11"
  ...
      "falkordblite>=0.5.0; python_version>='3.12'",
      "redis<8; python_version>='3.12'",
  ```
- **Impact.** The graph ingestion stage (Phase 2) fails at runtime on a 3.11 environment that the manifest advertises as supported; doctor would also flag 'no local graph backend' even though the user followed `requires-python`.
- **Fix.** Bump the contract to match reality: in backend/pyproject.toml set `requires-python = ">=3.12"` (the falkordblite wheels are cp312+ only and the markers already require 3.12, so 3.11 is never actually installable into a working state). After bumping, the `; python_version>='3.12'` markers on falkordblite/redis become redundant and can be simplified to plain `"falkordblite>=0.5.0"` and `"redis<8"`, making the dependency set unconditional and self-consistent. Then tighten scripts/doctor.sh: change line 59 from `3.11.*|3.12.*) ok ...` so 3.11 is no longer accepted, e.g. `3.12.*) ok "backend/.venv on Python $BE_VER" ;;` and move `3.11.*` into the warn/bad branch advising `uv sync --python 3.12`. Re-run `uv lock`/`uv sync` to regenerate the lock against the new requires-python. (If 3.11 support is genuinely desired instead, a real 3.11-compatible backend — e.g. adding `kuzu` as an unconditional dependency — would be required, but no 3.11 falkordblite wheel exists, so bumping to >=3.12 is the correct, lowest-risk fix.)
- **Verified.**

  ```
  Confirmed from the actual code. backend/pyproject.toml:5 declares `requires-python = ">=3.11"`, but the only zero-config graph backend packages are gated behind 3.12: line 24 `falkordblite>=0.5.0; python_version>='3.12'` and line 25 `redis<8; python_version>='3.12'`. backend/uv.lock encodes the same markers (`marker = "python_full_version >= '3.12'"` at lines 1337/1348) and ships ONLY cp312/cp313/cp314 wheels for falkordblite (lines 557-565) — there is no 3.11 wheel at all, so 3.11 is genuinely unsupported by the embedded backend.
  
  The "kuzu fallback" the finding dismisses is in fact NOT a fallback on 3.11: grep shows kuzu is never a declared dependency in pyproject.toml or uv.lock. So on a 3.11 resolve there is neither falkordblite nor kuzu, and the only remaining option (external FalkorDB via FALKORDB_HOST) defeats the advertised zero-config/no-Docker default.
  
  The runtime failure is concrete and hard: backend/app/services/graphiti_client/runtime.py:_resolve_backend() (lines 86-98) raises RuntimeError("No local graph backend available...") when GRAPH_BACKEND=auto and neither module imports. pipeline_orchestrator.py:648-662 likewise errors out. So Phase 2 graph ingestion crashes on a manifest-permitted 3.11 install.
  
  The doctor masking claim also checks out: doctor.sh:59 `3.11.*|3.12.*) ok` accepts 3.11 as a valid backend Python, while the local-backend probe at lines 108-112 would then print "no local graph backend installed" — a self-contradicting result on a 3.11 env. setup.sh pins 3.12 (lines 463, 625) and backend/.python-version is `3.12`, which masks this on the blessed path — but a bare `pip install`, a `uv sync` on a machine where 3.12 isn't available, or any downstream consumer trusting `requires-python` gets a broken install. The contract and the dependency set genuinely disagree.
  
  Not a misreading, not guarded, not dead code, not intended. Severity P1 is justified: it is a published-contract violation that crashes a core pipeline stage; the only mitigation is the setup script, which is not the contract. (P2 would be the floor given the setup.sh pin reduces real-world incidence, but the manifest is objectively wrong.)
  ```

---

## 5. P2 — inefficiency / fragility under load or on edges

### setup-ops — Setup/ops (setup.sh, doctor, packaging, env)

#### [F-11-0] API key leaked to process argv and a world-readable /tmp file during setup live-test

`P2` · `security` · confidence **high** · effort **S** · `setup.sh` : 389-416

- **Symptom.** When a user pastes an API key in the interactive picker, setup.sh runs a curl test that places the secret on curl's command line and writes the provider response to a predictable /tmp path.
- **Root cause.** The key is passed as `-H "Authorization: Bearer $LLM_API_KEY_INPUT"` directly in curl's argv (visible to any local user via `ps`/`/proc/<pid>/cmdline` for the up-to-25s request window), and the response body is written to `/tmp/setup_llm_test.$$` (predictable name, default permissions in a shared /tmp). Evidence: line 397 `curl -sS -o /tmp/setup_llm_test.$$ ...` and line 399 `-H "Authorization: Bearer $LLM_API_KEY_INPUT"`.
- **Evidence.**

  ```
  HTTP_CODE="$(curl -sS -o /tmp/setup_llm_test.$$ -w '%{http_code}' --max-time 25 \
      "${PROVIDER_BASE[$CHOSEN_IDX]}/chat/completions" \
      -H "Authorization: Bearer $LLM_API_KEY_INPUT" \
  ```
- **Impact.** On multi-user / shared hosts the freshly-entered API key is exposed in the process table during the test, and an attacker can pre-create/symlink the predictable /tmp file. Defeats the point of the silent (read -rs) key prompt.
- **Fix.**

  ```
  Keep the secret off argv and create the temp file safely. Replace the curl invocation (lines 397-403) so the Authorization header is fed via a curl config file on stdin and the response goes to a mktemp 0600 file:
  
    tmp="$(mktemp "${TMPDIR:-/tmp}/setup_llm_test.XXXXXX")"
    chmod 600 "$tmp" 2>/dev/null || true
    HTTP_CODE="$(curl -sS -o "$tmp" -w '%{http_code}' --max-time 25 \
      "${PROVIDER_BASE[$CHOSEN_IDX]}/chat/completions" \
      -H "Content-Type: application/json" \
      ${UA_HEADER[@]+"${UA_HEADER[@]}"} \
      --config - \
      -d "{\"model\":\"${PROVIDER_MODEL[$CHOSEN_IDX]}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}" \
      2>/dev/null <<EOF || printf '000'
  header = "Authorization: Bearer ${LLM_API_KEY_INPUT}"
  EOF
  )"
    ...
    rm -f "$tmp" 2>/dev/null || true
  
  Notes: mktemp creates the file atomically with O_EXCL under the caller's umask (the chmod 600 makes perms explicit and defeats the predictable-name symlink/pre-creation overwrite). Feeding the header via `--config -` (heredoc on stdin) keeps the token out of /proc/<pid>/cmdline and ps. The proposed `-H @-` variant is wrong (it does not exist as written and would conflict with -d on stdin); `--config -` is the correct mechanism. The existing `rm -f` cleanup and `unset LLM_API_KEY_INPUT` (lines 414-415) should be retained, switching the rm to "$tmp". This mirrors the safe mktemp pattern already used at line 307.
  ```
- **Verified.**

  ```
  Confirmed against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/setup.sh (lines 389-416). Two distinct, currently-true defects exist with no nearby mitigation:
  
  1) argv key leak (REAL): line 399 passes `-H "Authorization: Bearer $LLM_API_KEY_INPUT"` directly on curl's command line. On Linux, /proc/<pid>/cmdline is world-readable, so any local user can read the bearer token during the up-to-25s request window (--max-time 25, line 397). This is the genuine key-exposure vector and it defeats the silent `read -rs` prompt's intent on shared hosts.
  
  2) predictable /tmp file (REAL): line 397 writes to `/tmp/setup_llm_test.$$` — a guessable name in a shared, sticky /tmp. The script runs `set -euo pipefail` with no `noclobber`/`set -C` (verified via grep), so `curl -o` will follow a pre-planted symlink at that path and overwrite a victim-owned target (TOCTOU/symlink-overwrite). The same file also gets the provider response with default umask perms.
  
  Corrections to the finding: the response body written to /tmp does NOT contain the API key — it is the model's "ping" chat-completion. So "the freshly-entered API key is exposed in [the /tmp] file" is overstated; the argv path is the key-leak vector, while the /tmp path is a symlink-overwrite / minor info-disclosure vector. Both are genuine. Notably the file already uses the correct safe pattern elsewhere (mktemp at line 307 for the .env temp), so the fix is consistent with existing style and low-risk.
  
  Severity downgraded P1 -> P2: exploitation requires a multi-user/shared host with a co-located local attacker, the script is interactive-setup-only, the exposure window is short (<=25s), and the primary documented target is a macOS dev box where /proc does not exist and ps truncates the long curl line. It is a legitimate hardening defect but not remotely exploitable nor impactful in the default single-user case.
  ```

#### [F-11-2] doctor.sh envval does not strip whitespace/inline comments, causing false 'unknown provider' and false key-missing failures

`P2` · `robustness` · confidence **high** · effort **S** · `scripts/doctor.sh` : 34-37,115-130

- **Symptom.** A `.env` line such as `LLM_PROVIDER=claude-cli   # my provider` (trailing inline comment or trailing whitespace) makes doctor report 'Unknown LLM_PROVIDER' and can make API-key checks report the key as missing, even though the app runs fine.
- **Root cause.** `envval()` does `grep ... | cut -d= -f2- | tr -d '"'` — it strips quotes only, never leading/trailing whitespace or trailing ` # comment`. python-dotenv (used by the backend) DOES strip a space-prefixed inline comment and surrounding whitespace, so the runtime value is correct while doctor's parsed value still carries the comment/space, so the `case "$PROVIDER" in claude-cli)` exact match falls through to the catch-all warning, and `case "$KEY" in ""|...)` mis-evaluates.
- **Evidence.**

  ```
  envval() {
    [ -f "$ROOT_DIR/.env" ] || { echo ""; return; }
    grep -E "^[[:space:]]*$1=" "$ROOT_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"' || true
  }
  ```
- **Impact.** doctor (the documented 'is my environment ready' gate, exit code 1 blocks the user) emits false-negative blocking errors that contradict a working setup, eroding trust in the health check.
- **Fix.**

  ```
  Make envval mirror python-dotenv semantics by trimming a space-prefixed inline comment and surrounding whitespace, appended after the existing quote-strip (preserves backward compat). In scripts/doctor.sh lines 34-37:
  
  envval() {
    [ -f "$ROOT_DIR/.env" ] || { echo ""; return; }
    grep -E "^[[:space:]]*$1=" "$ROOT_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"' \
      | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//' || true
  }
  
  This was verified to produce: `claude-cli   # my provider` -> `claude-cli`; ` claude-cli ` -> `claude-cli`; `sk-abc123   # key` -> `sk-abc123`; while preserving `sk-abc#notcomment` (no space before #) — exactly matching python-dotenv. Note: ordering tr -d '"' before the sed strip is fine because doctor's existing behavior already removes all quotes; the only added behavior is comment/whitespace trimming. Optionally apply the same trim in setup.sh's value reads (lines 209, 324-325, 345-346) for full consistency, though setup.sh's tr -d '[:space:]' already covers its narrower comparison need.
  ```
- **Verified.**

  ```
  Confirmed by reading scripts/doctor.sh and reproducing the behavior. envval() at lines 34-37 does `grep -E "^[[:space:]]*$1=" .env | head -n1 | cut -d= -f2- | tr -d '"'` — it strips quotes only, never trailing inline ` # comment` or surrounding whitespace.
  
  Reproduction (real shell): a `.env` line `LLM_PROVIDER=claude-cli   # my provider` parses to the literal string `claude-cli   # my provider`. The `case "$PROVIDER" in claude-cli)` exact match at line 116-117 therefore falls through to the catch-all at line 129, emitting `warn "Unknown LLM_PROVIDER ..."`. (Note: this specific case is a warning, not a hard FAILURE, so it does not itself flip exit code to 1.)
  
  The key-missing false-negative is also real and IS blocking: the OpenAI-compatible branch at lines 124-128 reads `KEY="$(envval LLM_API_KEY)"`; the no-key/placeholder match is on `""|your_api_key|your_api_key_here`. A value like `your_api_key_here   # placeholder` would NOT match the placeholder cases (so that exact false-negative is avoided), but more importantly the df_key_check helper at lines 133-139 and the LLM_API_KEY branch test `[ -n "$V" ]` would treat a real-but-comment-bearing value as "set" — which is the opposite direction. The truly blocking false-negative path is the DEERFLOW_MODEL `case "$DF_MODEL"` at line 140 and the LLM_PROVIDER `case` for codex-cli: an inline comment on `LLM_PROVIDER` or `DEERFLOW_MODEL` makes the exact-match `case` fall to the `*) warn` arm (warning), while a comment on a value compared via these `case`/`[ -n ]` guards can mis-route. The most clearly blocking instance: any provider/model selector value carrying a comment misroutes the case statement.
  
  Verified the runtime divergence claim: backend/app/config.py uses `from dotenv import load_dotenv` (python-dotenv >=1.0.0 in pyproject.toml). Empirically, python-dotenv's dotenv_values strips space-prefixed inline comments and surrounding whitespace: `LLM_PROVIDER=claude-cli   # my provider` -> `'claude-cli'`, `B=val # comment` -> `'val'`, `C= spaced ` -> `'spaced'`, while preserving non-space-prefixed `#` (`A=sk-abc#notcomment` -> kept). So the app runs fine on `claude-cli` while doctor reports it as unknown — a genuine false-negative that contradicts a working setup.
  
  Also confirmed the consistency claim: setup.sh (lines 209, 324-325, 345-346) reads the same values with `tr -d '[:space:]'`, which strips whitespace (handling the trailing-space case) but NOT a `# comment`, so doctor.sh is the laxer of the two. Neither matches python-dotenv exactly; the proposed sed fix does.
  
  Severity P2 is correct: it requires the user to add an inline comment / trailing whitespace to a value line (not the default — .env.example keeps comments on their own lines), but the file is comment-heavy so this is plausible, and the failure is a confidence-eroding false alarm in the documented "is my environment ready" gate rather than a functional break.
  ```

#### [F-11-4] AGPL LICENSE file deleted from the tree while both manifests still declare AGPL-3.0

`P2` · `config` · confidence **high** · effort **S** · `.gitignore` : n/a (working tree)

- **Symptom.** `git status` shows `D LICENSE` (the LICENSE file is removed from the working tree and staged for deletion), yet `package.json` declares `"license": "AGPL-3.0"` and `backend/pyproject.toml` declares `license = { text = "AGPL-3.0" }`.
- **Root cause.** The LICENSE text file was deleted (confirmed: `LICENSE absent from working tree`, last touched in commit 27e712f) but the license metadata in both package manifests was left in place. AGPL-3.0 is a copyleft license whose terms require the license text to accompany distribution.
- **Evidence.** `git status: 'D LICENSE'; package.json: "license": "AGPL-3.0"; pyproject.toml: license = { text = "AGPL-3.0" }`
- **Impact.** Distributing/packaging the project (npm publish, wheel build) advertises AGPL-3.0 with no accompanying license text — a licensing-compliance defect and a contradiction in the repo's own metadata.
- **Fix.** Restore the GNU AGPL-3.0 license text at the repo root as LICENSE (the canonical full text), which satisfies both manifests and the README link. If AGPL-3.0 was not actually intended, instead change package.json ("license"), backend/pyproject.toml (license field), and README.md:516 to the license actually intended and add its text file. Either way, fix the README.md:516 dead link [AGPL-3.0](LICENSE) so it resolves. Verify with: ls LICENSE && grep -n license package.json backend/pyproject.toml.
- **Verified.** Confirmed by reading the actual tree. The root LICENSE file is gone from the working tree (ls: No such file or directory) AND from the committed HEAD tree (git cat-file -t HEAD:LICENSE -> 'fatal: path LICENSE does not exist in HEAD'). The audit's gitStatus snapshot showed a staged deletion (D LICENSE); it has since been committed (HEAD is 96c3018, two commits past 27e712f which removed it). Meanwhile both manifests still declare AGPL-3.0 at HEAD: package.json:21 ("license": "AGPL-3.0") and backend/pyproject.toml:6 (license = { text = "AGPL-3.0" }). The only LICENSE files remaining are vendored upstream subtrees (deer-flow/, graphiti-0.29.2/, deer-flow-2.0-m1-rc3/), not the project's own. The finding is accurate and slightly understated: README.md:516 also links [AGPL-3.0](LICENSE) to the now-missing file, a dead link. This is a genuine, currently-true licensing-metadata contradiction: AGPL-3.0 is copyleft and requires the license text to accompany distribution, so npm publish / wheel builds would advertise AGPL-3.0 with no license text. Not a runtime/security defect, so P2 is correct.

### core-utils — Config, LLM clients, utils, settings API, app entry

#### [F-8-5] _test_openai_compat_provider echoes full SDK error + 500 handlers return traceback to client

`P2` · `security` · confidence **medium** · effort **S** · `backend/app/api/settings.py` : 108-125, 49-50, 174-176

- **Symptom.** Connectivity-test failures return the raw str(e) of the OpenAI SDK exception to the client (truncated to 300 chars), and the 500 handlers in this file return full traceback.format_exc() in the JSON response.
- **Root cause.** Exception messages from the SDK/HTTP layer can contain the request URL, internal endpoints, or other detail; traceback returns server file paths and stack internals. These are surfaced verbatim to the frontend/network, and CORS is wide open (origins '*' in __init__.py:43).
- **Evidence.** `return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500`
- **Impact.** Information disclosure to any client able to reach the settings API, including internal base URLs and server filesystem paths via traceback.
- **Fix.** In the two 500 handlers (settings.py:48-50 and 174-176), stop returning the traceback to the client. Log full detail server-side (the handlers already call logger.error(..., exc_info=True)) and return only a generic message, e.g. `return jsonify({"success": False, "error": "Internal server error"}), 500` (optionally with a correlation id for log lookup). In `_test_openai_compat_provider` (settings.py:109-125), do not echo `str(e)`; return only the status-mapped `hint` from the existing `hints` dict, falling back to a generic message like "connection test failed" when status is unrecognized — log the raw `str(e)` server-side instead. Tighten CORS at backend/app/__init__.py:43 to an explicit allowlist of trusted origins (e.g. the local frontend origin) rather than "*", at minimum for /api/settings. Because the same traceback-in-JSON pattern exists across research.py, graph.py, simulation.py, and report.py, apply the same sanitization there (a shared error-response helper or a Flask errorhandler that strips tracebacks when DEBUG is False is the cleanest approach). Independently, consider binding to 127.0.0.1 by default instead of 0.0.0.0 and adding a minimal auth token, since the unauthenticated 0.0.0.0 exposure is what turns this info-disclosure into a real reachability concern.
- **Verified.** Confirmed verbatim against the code. backend/app/api/settings.py lines 50 and 176 both return `jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500`, leaking full server tracebacks (filesystem paths, stack internals) to the client. Lines 109-124 in `_test_openai_compat_provider` return `str(e)` of the OpenAI SDK exception (truncated to 300 chars) in the error field, which can embed the configured base_url/endpoint and HTTP detail. CORS is wide open at backend/app/__init__.py:43 (`origins: "*"` for `/api/*`). Aggravating, also verified: the server binds to 0.0.0.0 by default (backend/run.py:40,45), there is NO authentication anywhere on the API (grep for Authorization/login_required/abort(401|403)/token guards returned nothing), and the traceback leak is NOT gated by debug mode — Config.DEBUG defaults to False (config.py:28) yet the code manually serializes traceback.format_exc() into JSON regardless, so it leaks even in production posture. This is genuine, currently-true information disclosure reachable by any unauthenticated client (including any-origin browsers via open CORS, and same-LAN clients via 0.0.0.0). Severity is correctly P2: the leaked data is paths/code-structure/error strings, not credentials, and there is no RCE (interactive debugger is off by default), so it is recon-grade hardening rather than a critical/directly-exploitable flaw. Note the same traceback-in-JSON pattern is codebase-wide (research.py, graph.py, simulation.py, report.py), so the fix should not be scoped only to settings.py.

#### [F-8-4] apply_provider mutates shared Config + os.environ with no lock vs running pipelines

`P2` · `concurrency` · confidence **medium** · effort **M** · `backend/app/config.py` : 174-220

- **Symptom.** POST /api/settings/llm calls Config.apply_provider, which rewrites class attributes (LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, DEERFLOW_MODEL) and os.environ at runtime while pipeline/report threads read these same attributes (e.g. pipeline_orchestrator.py:666-683, 1580).
- **Root cause.** Provider switching is intentionally live, but the mutation isn't synchronized with the orchestrator's _lifecycle_lock or with in-flight stage reads. A report stage that reads Config.LLM_PROVIDER, then Config.LLM_API_KEY, can observe a torn mix from two providers if a switch lands between the reads (e.g. new provider id with old/absent key), producing auth errors mid-run.
- **Evidence.**

  ```
  cls.LLM_PROVIDER = provider
  ...
  cls.LLM_API_KEY = _key
  ...
  for k, v in env_updates.items():
      os.environ[k] = v
  ```
- **Impact.** A settings change during an active run can corrupt that run's provider/key/base_url combination, causing mid-pipeline LLM failures. The doc comment claims switches only affect 'newly started' pipelines, but nothing actually isolates running ones from the shared mutable Config.
- **Fix.** Make provider config a single atomic snapshot rather than five independently-mutated fields. Best: at pipeline start, capture the provider tuple (provider, api_key, base_url, model, deerflow_model) into PipelineState and pass it explicitly into every LLMClient/generator constructor for that run, so stages never read live Config. This also fixes the DeerFlow subprocess env-inheritance race. Minimal alternative: store the LLM config as one immutable tuple/dataclass on Config behind a lock — apply_provider builds the new tuple and assigns it in one atomic reference swap (Python attribute rebinding is atomic), and all readers grab the single reference once (cfg = Config.LLM; cfg.provider, cfg.api_key, ...) instead of reading four separate class attributes. Either approach removes the torn-read window without needing per-read locking. At absolute minimum, correct the misleading doc comment at config.py:176 and have apply_provider plus all readers go through one read/write lock so provider+key are always consistent.
- **Verified.**

  ```
  Verified against the actual code. The mechanics in the finding are correct:
  
  1. Pipelines run as in-process daemon threads (pipeline_orchestrator.py:914, 1058, 1123, 1192 — threading.Thread, daemon=True), so they share Config class attributes and os.environ with the Flask request thread handling POST /api/settings/llm.
  2. Config.apply_provider (config.py:191-218) rewrites LLM_PROVIDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, DEERFLOW_MODEL and os.environ with NO lock. It writes them in a different order than readers read them (provider at 191, base_url at 201, model at 202, key at 205, env loop at 217-218).
  3. Readers read the same attributes in a non-atomic sequence inside constructors invoked DURING a run: llm_client.py:50-55 and, most clearly, oasis_profile_generator.py:190-193 (PREPARE stage) and simulation_config_generator.py:260-263. graphiti_client/llm_adapter.py:57-59 and oasis_llm.py:175-177 do the same.
  4. The orchestrator's _lifecycle_lock (config_orchestrator line 759) guards only the _threads/_cancel_events registry, never Config — confirming nothing synchronizes the mutation against in-flight reads. preflight (pipeline_orchestrator.py:666-668) and run-time checks (1580) also read provider then key separately, so even validation can observe a torn pair.
  
  So a settings switch landing within a client/generator constructor's read window can pair a new provider id with an old/absent key (or wrong base_url/model), causing mid-run auth/endpoint failures. The doc comment at config.py:176 explicitly promises switches only affect "newly started" pipelines, which is false — running pipelines that construct a new client mid-run are not isolated from the shared mutable Config.
  
  Caveats keeping this at P2 (not higher): the GIL makes each individual attribute read/write atomic, so the bug is only the multi-field tear; once an LLMClient is built it snapshots values into instance fields, so steady-state calls are immune — the window is narrow (a few constructor lines) and only opens during a deliberate, infrequent admin switch coinciding with a concurrent run. Real defect, low probability, but silent and hard to diagnose, and the doc comment overstates safety.
  ```

#### [F-8-9] split_text_into_chunks lacks forward-progress guarantee with large overlap

`P2` · `robustness` · confidence **medium** · effort **S** · `backend/app/utils/file_parser.py` : 169-188

- **Symptom.** The loop advances start = end - overlap. If sentence-boundary search shrinks `end` close to `start` (end can be as low as start + chunk_size*0.3 + sep_len), then end-overlap can be <= the previous start when overlap is large relative to the boundary-shortened step, causing re-processing of the same region or near-zero progress.
- **Root cause.** No invariant ensures forward progress: the next start is derived purely from the (possibly shortened) end minus overlap, with no floor like max(prev_start+1, ...). Config exposes DEFAULT_CHUNK_OVERLAP and callers (text_processor.split_text) pass overlap through, so non-default values can hit this.
- **Evidence.** `start = end - overlap if end < len(text) else len(text)`
- **Impact.** With aggressive overlap or short chunk sizes the chunker can produce heavily duplicated chunks or very slow progress over large dossiers, inflating downstream LLM/graph ingestion cost.
- **Fix.**

  ```
  Enforce a forward-progress floor and clamp overlap. The audit's `step = max(1, (end - start) - overlap)` is correct and necessary; clamping overlap alone is NOT sufficient (overlap=40 < chunk_size=100 still loops). Also keep the final-chunk handling. Replace lines 167-188:
  
      overlap = max(0, min(overlap, chunk_size - 1))  # clamp: keep overlap strictly below chunk_size
      chunks = []
      start = 0
      while start < len(text):
          end = start + chunk_size
          if end < len(text):
              for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                  last_sep = text[start:end].rfind(sep)
                  if last_sep != -1 and last_sep > chunk_size * 0.3:
                      end = start + last_sep + len(sep)
                      break
          chunk = text[start:end].strip()
          if chunk:
              chunks.append(chunk)
          if end >= len(text):
              break
          step = max(1, (end - start) - overlap)  # guarantee >= 1 char of forward progress
          start += step
      return chunks
  
  Verified: terminates for chunk_size=500/overlap=200, chunk_size=100/overlap=100, and defaults, with no duplicate-chunk explosion. Additionally (defense in depth), validate chunk_size >= 1 and 0 <= chunk_overlap < chunk_size at the API boundary in graph.py:340-341 and reject otherwise, so callers get a clear 400 rather than relying solely on the clamp.
  ```
- **Verified.**

  ```
  CONFIRMED REAL via runtime reproduction. In backend/app/utils/file_parser.py:169-188, the loop advances `start = end - overlap` with no forward-progress floor. The sentence-boundary search can shrink the per-iteration step to as little as `chunk_size*0.3 + len(sep)`. When `overlap >= step`, the next `start` is <= the current `start`, so the loop never advances and runs forever, emitting unbounded duplicated chunks.
  
  The audit understated the impact: this is not merely "near-zero progress" — it is a true infinite loop. Reproductions (matching the function's exact logic):
  - chunk_size=500, overlap=200 (Chinese text, '。' separators landing just above the 0.3 floor): looped past 500,000 iterations producing ~500,000 chunks before I aborted it.
  - chunk_size=100, overlap=100: `start` goes negative (-1) and stays there → permanent hang.
  - chunk_size=100, overlap=40 (overlap < chunk_size): still infinite-loops, generating 200,000 duplicate chunks. This proves the audit's note that clamping `overlap < chunk_size` ALONE is insufficient — the floor on step is the real fix.
  - Defaults (chunk_size=500, overlap=50) are safe: terminates in 40 iterations.
  
  Reachability is confirmed: backend/app/api/graph.py:340-341 reads `chunk_size`/`chunk_overlap` directly from untrusted request JSON with zero validation, then passes them to TextProcessor.split_text → split_text_into_chunks (text_processor.py:34). project.py:49-50 and models default these to 500/50 but a caller can override with any integers (e.g. overlap >= chunk_size, or a moderate overlap like 200 that exceeds the boundary-shortened step). So a single malformed/aggressive request hangs the background graph-build worker thread indefinitely and balloons LLM/graph ingestion cost.
  
  Severity P2 rather than P3: an infinite loop / worker hang triggerable from request input is more than a robustness nit, but it requires non-default parameters and the build runs in a background thread (graph_builder.py spawns it), so it is a denial-of-progress / resource issue rather than a crash of the main process — hence P2, not P1.
  ```

### x-security — CROSS-CUTTING: security & secrets

#### [F-13-3] Research prompt passed via subprocess argv — visible in process list to all local users

`P2` · `security` · confidence **high** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 376-382

- **Symptom.** The DeerFlow research subprocess is launched with `--prompt <prompt>` on argv, so the full user prompt appears in `ps aux` / /proc/<pid>/cmdline for the duration of the (potentially multi-hour) research run.
- **Root cause.** DeerFlowResearchRunner.run builds `cmd = ... + [script, '--prompt', prompt, '--out-dir', handoff_dir, ...]` (lines 376-382) and Popen's it. The bridge already supports `--prompt-file` (deerflow_research.py:757) which would avoid argv exposure, and the project's own llm_client._chat_claude_cli explicitly feeds prompts via stdin specifically because 'argv 会把 prompt 暴露在进程列表里' (argv exposes the prompt in the process list) — the orchestrator does not follow that precedent.
- **Evidence.** `pipeline_orchestrator.py:378 `"--prompt", prompt,`; deerflow_research.py:757 `src.add_argument("--prompt-file", ...)`; llm_client.py comment: 'argv 会把 prompt 暴露在进程列表里'`
- **Impact.** Any unprivileged local user on the host can read the forecasting prompts (which may contain confidential business questions or sensitive subject matter) of every running pipeline. Inconsistent with the codebase's own stated argv-avoidance policy. Long deep runs keep the prompt visible for the full multi-hour budget.
- **Fix.**

  ```
  In DeerFlowResearchRunner.run (pipeline_orchestrator.py ~376-382), stop passing the prompt on argv. Write it to a file inside handoff_dir and pass --prompt-file instead. Because the bridge declares --prompt / --prompt-file as a mutually-exclusive group, you must remove the --prompt entry entirely (not merely add --prompt-file).
  
  Example:
      os.makedirs(handoff_dir, exist_ok=True)
      prompt_path = os.path.join(handoff_dir, "prediction_requirement.txt")
      with open(prompt_path, "w", encoding="utf-8") as fh:
          fh.write(prompt)
      cmd = _detect_deerflow_python(deerflow_dir) + [
          script,
          "--prompt-file", prompt_path,
          "--out-dir", handoff_dir,
          "--model", model or Config.DEERFLOW_MODEL,
          "--depth", depth or Config.DEERFLOW_RESEARCH_DEPTH,
      ]
  
  Notes:
  - Write the prompt file with restrictive permissions if the handoff_dir is on a shared host (e.g. os.open(..., 0o600) or os.chmod), since the goal is to keep the prompt off the process list AND off world-readable disk; otherwise you trade argv exposure for filesystem exposure. handoff_dir already contains the report/handoff artifacts, so directory perms should be set accordingly.
  - Do not rely on the bridge's own REQUIREMENT_FILENAME write (deerflow_research.py:781) to source the prompt, because that write happens after argv parsing inside the child; the orchestrator must write the file itself before spawning.
  - This also incidentally fixes a latent E2BIG (ARG_MAX) risk for very long prompts, matching the rationale already documented in llm_client.py:383-384.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. In /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py:376-382, DeerFlowResearchRunner.run builds cmd with "--prompt", prompt and then subprocess.Popen's it (line 398). The prompt is the full user research/prediction question, so it appears in `ps aux` and /proc/<pid>/cmdline for the entire run. The run is long-lived: it blocks until the child exits, with a watchdog budget scaled per depth (deep = multi-round protocol), so the exposure persists for the full budget.
  
  The safer alternative already exists and is fully wired: deerflow_research.py:755-757 defines a mutually-exclusive group with --prompt-file, and lines 769-770 read it via Path(args.prompt_file).read_text(encoding="utf-8"). So no bridge change is needed.
  
  The codebase has an explicit, contradicting precedent: llm_client.py:383-384 deliberately feeds the prompt via stdin instead of argv, with the comment that "argv 会把 prompt 暴露在进程列表里" (argv exposes the prompt in the process list) and to avoid E2BIG. The orchestrator does not follow this precedent for the DeerFlow subprocess.
  
  Severity P2 is correct: this is local-only information disclosure requiring an unprivileged local account on the host; no remote vector, auth bypass, or privilege escalation. Real and cleanly fixable, with an in-repo policy that already says not to do this — so not P3. Confidence high: every quoted line was verified directly.
  ```

### sim-runtime — Simulation runtime (runner/manager/ipc) + API

#### [F-6-11] stop_simulation sets status STOPPED even when no process handle exists and no kill executed, lying about a still-running subprocess

`P2` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/simulation_runner.py` : 817-838

- **Symptom.** If cls._processes.get(simulation_id) is None (after restart, or after the monitor popped it while the OS process lingers), the entire terminate block is skipped, yet the code unconditionally sets runner_status=STOPPED and twitter/reddit_running=False and returns success.
- **Root cause.** The transition to STOPPED is decoupled from whether the process was actually terminated. The guard 'if process and process.poll() is None' protects the kill, but the STOPPED assignment runs regardless of whether a kill happened.
- **Evidence.** ``process = cls._processes.get(simulation_id); if process and process.poll() is None: ...terminate...` then unconditionally `state.runner_status = RunnerStatus.STOPPED ... return state`.`
- **Impact.** API reports the simulation stopped while a detached subprocess keeps running and writing actions; combined with the resume defect this guarantees orphaned post-restart runs are 'stopped' on paper only.
- **Fix.** In stop_simulation, decouple the STOPPED transition from blind success and use the persisted pid as a fallback. Concretely: track whether termination actually occurred. If process and process.poll() is None, terminate as today and mark terminated=True. Else if process is None but state.process_pid is set, attempt to kill the orphaned detached process group by stored pid: on Unix, try pgid = os.getpgid(state.process_pid); os.killpg(pgid, SIGTERM) then SIGKILL after a grace period (wrap in try/except ProcessLookupError/PermissionError); on Windows use taskkill /F /T /PID. After attempting, verify with os.kill(pid, 0): if it raises ProcessLookupError the process is gone (mark terminated=True); if it still responds, do NOT report STOPPED. Only set runner_status=STOPPED (with twitter/reddit_running=False, completed_at) when termination is confirmed or the process was already dead; otherwise set FAILED (or a dedicated UNKNOWN) with state.error explaining that the process handle was lost and the lingering pid could not be confirmed killed, so the caller is not lied to. Note that os.getpgid/killpg by a stale pid risks pid-reuse killing an unrelated process group; mitigate by storing and re-validating the process group / start time, or at minimum guard the killpg path and document the residual risk. Mirror the same logic in cleanup_all_simulations if it can run after a restart.
- **Verified.** Verified against backend/app/services/simulation_runner.py. The defect is real via the restart scenario. (1) Subprocesses are launched with start_new_session=True (line 461), so they survive a backend parent restart. (2) cls._processes (line 227) is a class-level in-memory dict, lost on restart; there is NO startup reconciliation, re-attach, or pause/restore that rebuilds it (grep confirms none exist). (3) run_state.json persists runner_status and process_pid to disk (lines 189, 314) and _load_run_state reloads runner_status=RUNNING verbatim without downgrading it (line 261). (4) In stop_simulation: the guard at line 811 passes because status is RUNNING from disk; cls._processes.get(simulation_id) at line 818 returns None; the entire terminate block (819-832) is skipped; then lines 834-838 unconditionally set runner_status=STOPPED, twitter/reddit_running=False, completed_at, and return success. So the API reports STOPPED while the detached subprocess keeps running and writing to actions.jsonl. (5) process_pid is dead data for termination: it is written and reloaded but NEVER used to kill anything — every kill path (_terminate_process, cleanup_all_simulations) uses the live in-memory process.pid. One sub-claim in the finding is inaccurate: the 'monitor popped _processes while the OS process lingers' path is NOT real, because _monitor_simulation only pops _processes (line 576) after process.poll() returns non-None, i.e. after the process has already exited. But the restart scenario alone fully substantiates the defect, root cause, and impact. P2 is correct: triggering requires a backend restart mid-run (not the common path), but the consequence is orphaned subprocesses corrupting data while the UI/API falsely reports STOPPED — a correctness + resource-leak bug, above P3 cosmetic, below P1 happy-path failure.

#### [F-6-1] Monitor thread mutates a SimulationRunState shared with API request threads with no lock (torn reads / lost updates)

`P2` · `concurrency` · confidence **medium** · effort **M** · `backend/app/services/simulation_runner.py` : 236-246, 493-526, 151-162

- **Symptom.** While a simulation runs, /run-status and /run-status/detail can return inconsistent counters (e.g. progress_percent from a half-updated current_round/total_rounds), and recent_actions can be observed mid-mutation.
- **Root cause.** get_run_state() returns the same cached SimulationRunState object in cls._run_states. The daemon _monitor_simulation thread mutates it continuously (add_action, current_round, twitter_*), while Flask request threads call to_dict()/to_detail_dict() and _save_run_state() writes it. No lock guards this shared mutable state; add_action does list insert+slice (153-155) that is not atomic vs a concurrent serialization.
- **Evidence.** ``_run_states: Dict[str, SimulationRunState] = {}`; get_run_state returns `cls._run_states[simulation_id]`; monitor loops `cls._save_run_state(state)` / `state.add_action(action)` with `self.recent_actions.insert(0, action)` then slicing, no lock.`
- **Impact.** Intermittent wrong numbers in the live dashboard and occasional list-mutation/index errors when a request serializes recent_actions exactly as the monitor inserts. Under load this is a flaky-failure source for the polling UI.
- **Fix.** Add a per-simulation threading.Lock (or a single class-level RLock guarding _run_states) and hold it around the writer (add_action, the current_round/twitter_*/count updates in _read_action_log, and _save_run_state) and around the reader serialization (to_dict / to_detail_dict). Simplest robust form: have to_dict() build its return dict under the lock so the snapshot of all scalar counters is internally consistent; the monitor also takes the lock when mutating. This is enough to fix the only real exposure (inconsistent scalar counters). The 'copy recent_actions before serializing' suggestion is unnecessary for the current code — no request thread iterates the in-memory recent_actions, and CPython does not raise on that race — but copying it inside to_detail_dict() is a cheap, harmless hardening if to_detail_dict ever becomes request-reachable. Do NOT bother locking _processes/_run_states dict membership for this finding; that is a separate concern (dict get/set are GIL-atomic and not the source of the torn-counter symptom).
- **Verified.**

  ```
  CONFIRMED as a real unsynchronized-shared-state race, but the claimed impact is overstated, so severity is P2 not P1.
  
  Confirmed from code: there is no lock anywhere in backend/app/services/simulation_runner.py (grep finds only threading.Thread, no Lock/RLock). _run_states caches one SimulationRunState per id and get_run_state returns that same object (lines 239-246). The daemon _monitor_simulation thread mutates it continuously with no synchronization: add_action (151-162), current_round / twitter_*/counts (653-690), and _save_run_state (525). Flask runs threaded=True (backend/run.py:45), so /run-status (run_state.to_dict(), simulation.py:1746) and /run-status/detail (simulation.py:1837) run on separate request threads concurrently with the monitor. So torn/inconsistent reads of the scalar counters are genuinely possible: progress_percent in to_dict() is derived from current_round/total_rounds while the monitor is updating those plus the per-platform counts and simulated_hours, so a poller can see mutually inconsistent numbers.
  
  Where the finding is WRONG / overstated:
  1. The 'recent_actions can be observed mid-mutation' / 'occasional list-mutation/index errors when a request serializes recent_actions' claim does NOT hold. The detail endpoint does NOT serialize the in-memory shared recent_actions; it reloads actions from disk via get_all_actions (simulation.py:1830,1843). The only in-memory iteration of recent_actions (to_detail_dict, line 197) executes on the monitor thread itself inside _save_run_state, with no concurrent reader. I empirically verified that a CPython list-comprehension running concurrently with insert(0,...) + slice-reassign produces 0 exceptions (GIL makes each step atomic) — it yields an inconsistent snapshot, never an IndexError. So no crashes / flaky failures from this path.
  2. len(run_state.rounds) at simulation.py:1841 is harmless: grep shows rounds is never appended to or reassigned anywhere — it is always the empty init list.
  
  Net real defect: torn reads of plain int/bool scalars in to_dict(). Because attribute reads are GIL-atomic and values are ints/bools (no corruption, no crash, self-corrects on the next 2s poll), the user-visible effect is at worst a one-frame-stale/slightly-inconsistent number on a polling dashboard — cosmetic, not a flaky-failure or data-integrity source. Hence P2.
  ```

#### [F-6-10] Platform-completion inferred from actions.jsonl existence; a slow/failed platform is treated as disabled and the run is marked COMPLETED prematurely

`P2` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/simulation_runner.py` : 721-746, 632-650

- **Symptom.** In parallel mode, if Twitter writes simulation_end before Reddit has created reddit/actions.jsonl (Reddit slow to start or crashed before first write), reddit_enabled=os.path.exists(reddit_log) is False, so _check_all_platforms_completed returns True and the run flips to COMPLETED while Reddit is pending or dead.
- **Root cause.** Platform enablement is derived from a side-effect file rather than the authoritative SimulationState.enable_twitter/enable_reddit or the launch platform arg. actions.jsonl existence is false during startup and on early failure.
- **Evidence.** ``twitter_enabled = os.path.exists(twitter_log); reddit_enabled = os.path.exists(reddit_log); ... return twitter_enabled or reddit_enabled``
- **Impact.** Run can be reported COMPLETED with only one platform's data; the report agent then forecasts on half the intended simulation, and a Reddit-side crash is masked as success.
- **Fix.**

  ```
  Make completion checking use the authoritative enablement source instead of file existence, and require both enabled AND simulation_end emitted. In this codebase the launch-time flags are state.twitter_running/state.reddit_running (set in start_simulation from the platform arg), but note those get cleared to False on completion (lines 555-556, 636/640), so add dedicated persisted enablement fields (e.g. state.twitter_enabled/state.reddit_enabled set once at launch and serialized in to_dict/from_dict), or mirror SimulationState.enable_twitter/enable_reddit into the run state. Then:
  
  def _check_all_platforms_completed(cls, state) -> bool:
      twitter_enabled = state.twitter_enabled
      reddit_enabled = state.reddit_enabled
      if twitter_enabled and not state.twitter_completed:
          return False
      if reddit_enabled and not state.reddit_completed:
          return False
      return twitter_enabled or reddit_enabled
  
  Do NOT key enablement off os.path.exists(actions.jsonl), which is false during startup and on early failure. Additionally, harden the monitor so a platform that was enabled but never emitted simulation_end and whose subprocess exited is treated as FAILED rather than silently dropped: in _monitor_simulation after the process exits, if exit_code == 0 but an enabled platform has *_completed == False, set runner_status = FAILED with a clear error instead of COMPLETED (currently lines 537-540 mark COMPLETED on exit_code==0 regardless), so a Reddit crash before its first log write surfaces rather than producing a half-simulation forecast.
  ```
- **Verified.**

  ```
  Confirmed real by reading the full chain. _check_all_platforms_completed (simulation_runner.py:721-746) derives platform enablement from os.path.exists(actions.jsonl) (lines 736-737), ignoring the authoritative launch-time flags state.twitter_running/state.reddit_running, which start_simulation sets from the platform arg (lines 402-411; both True in parallel mode).
  
  The actions.jsonl file is created lazily: PlatformActionLogger.__init__ only does os.makedirs(log_dir) (action_logger.py:41); the file appears only on the first log write. For each platform that first write is log_simulation_start, which in run_parallel_simulation.py occurs only AFTER awaited setup — Reddit: create_model, generate_reddit_agent_graph, oasis.make, await env.reset() (lines 1512-1549). Both platforms run concurrently via asyncio.gather in a single subprocess (line 1807). Twitter's last write is log_simulation_end (line 1473), so by the time Twitter emits simulation_end its actions.jsonl exists; Reddit's may not yet exist (still in setup, or returned early at line 1517 when reddit_profiles.json is missing, or crashed before line 1549).
  
  In that window the monitor's mid-run path (_read_action_log handling simulation_end, lines 632-650) computes reddit_enabled=False, so _check_all_platforms_completed returns True and flips runner_status=COMPLETED while process.poll() is None and Reddit is still running or dead. The impact is concrete and not theoretical: the pipeline orchestrator wait loop breaks the instant rs.runner_status == RunnerStatus.COMPLETED (pipeline_orchestrator.py:1655-1656) with no check that the subprocess exited or that all enabled platforms finished, then proceeds to write_run_summary and the REPORT/forecast stage on Twitter-only data, masking the Reddit failure as success.
  
  There is a separate, correct process-exit completion path (lines 537-540) that only fires after both gathered coroutines finish, but it does not prevent the premature mid-run flip described above. The root-cause claim and quoted evidence are accurate. P2 is right: it needs a race or a specific Reddit-side failure rather than the happy path, but when triggered it silently corrupts the forecast and hides a platform crash (data-integrity defect, not a crash).
  ```

#### [F-6-0] max_rounds truncation: persisted rounds_truncated_from/to dropped on state reload, falsifying the golden-thread truncation banner

`P2` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/simulation_runner.py` : 259-282, 190-191

- **Symptom.** After a Flask restart (or any time run_state is served from disk rather than the in-memory object), a run that was truncated by max_rounds reports rounds_truncated_from=None / rounds_truncated_to=None even though run_state.json on disk contains the real values.
- **Root cause.** SimulationRunState.to_dict() writes rounds_truncated_from/rounds_truncated_to (lines 190-191) and _save_run_state persists them, but _load_run_state() (lines 259-282) reconstructs SimulationRunState WITHOUT passing rounds_truncated_from/rounds_truncated_to; they default back to None on every reload from file.
- **Evidence.** `constructor in _load_run_state ends at `process_pid=data.get("process_pid"),)` with no rounds_truncated_* keys, while to_dict() emits `"rounds_truncated_from": self.rounds_truncated_from, "rounds_truncated_to": self.rounds_truncated_to,``
- **Impact.** The EXECPLAN golden-thread feature that marks a forecast as 'shortened' so UI/report can disclose it is silently lost across process boundaries. The history endpoint and any report generated after a restart see an un-truncated run. This is a correctness/disclosure defect in the very feature EXECPLAN claims is implemented.
- **Fix.**

  ```
  In _load_run_state(), add the two missing fields to the SimulationRunState(...) constructor call, mirroring total_simulation_hours (line 265):
  
      process_pid=data.get("process_pid"),
      rounds_truncated_from=data.get("rounds_truncated_from"),
      rounds_truncated_to=data.get("rounds_truncated_to"),
  )
  
  Using data.get(...) (no default arg) correctly yields None for older state files that predate the field, matching the dataclass default. This restores the serialize/deserialize symmetry so to_dict()/to_detail_dict() report truthful truncation values after a restart. (Note: to fully realize the EXECPLAN "surfaced to UI/report" goal, a separate change is still needed to actually consume these fields in the frontend/report, but that is out of scope for this persistence fix.)
  ```
- **Verified.**

  ```
  Confirmed by reading the code. SimulationRunState defines rounds_truncated_from/rounds_truncated_to as constructor fields defaulting to None (lines 148-149). to_dict() serializes them (lines 190-191) and _save_run_state() persists via to_detail_dict()->to_dict() (line 311), so run_state.json on disk carries the real truncation values when max_rounds<total (set at lines 364-381). But _load_run_state() (lines 259-282) reconstructs SimulationRunState WITHOUT passing rounds_truncated_from/to; the constructor call ends at process_pid=data.get("process_pid") (line 281). No later code in _load_run_state restores them (only recent_actions are restored, lines 284-297). So both fields silently reset to None on any reload from disk. The reload path is live and reachable: get_run_state() (lines 236-246) falls back to _load_run_state() whenever simulation_id is absent from the in-memory _run_states cache (e.g., after a Flask restart). Net effect: a truncated run that survives a process restart reports rounds_truncated_from=None/rounds_truncated_to=None even though disk JSON holds the true values, and to_dict()/to_detail_dict() (the API/history serialization) then misreports an un-truncated run. The root-cause claim and quoted evidence are accurate, and the proposed fix is correct.
  
  Severity correction to P2 (down from P1): the finding's claimed live impact is a falsified UI/report truncation banner, but an exact-name grep across the whole repo (frontend .vue, report_agent.py, API) shows the only references to rounds_truncated_from/to are in simulation_runner.py and EXECPLAN docs. No current downstream consumer reads these fields, so there is no presently-rendered banner to falsify; the EXECPLAN "surfaced to UI/report" wiring is not yet implemented. The defect is a genuine, currently-true data-contract/persistence bug (serialize-but-not-deserialize asymmetry that drops persisted state on reload), but its user-visible blast radius today is latent rather than active, warranting P2.
  ```

#### [F-6-6] IPC client orphans response files on timeout while the server is mid-interview; unread responses leak in ipc_responses/ and never get cleaned

`P2` · `robustness` · confidence **medium** · effort **S** · `backend/app/services/simulation_ipc.py` : 157-187

- **Symptom.** When send_command times out it deletes only the command file (181-185). But the server may already be awaiting env.step() for that command; when it finishes it writes responses/<command_id>.json. Since command_id is a fresh UUID per call, that orphan response is never read and never deleted.
- **Root cause.** Client cleanup on timeout removes the request but cannot recall in-flight server work; the script's send_response writes the response unconditionally after the long LLM interview, and nothing garbage-collects responses whose client already gave up.
- **Evidence.** `client timeout branch does `os.remove(command_file)` then `raise TimeoutError` (no response cleanup); the script's send_response always writes `response_file` regardless of whether the client is still waiting.`
- **Impact.** Slow disk leak in ipc_responses/ for every interview exceeding the timeout (interviews are LLM-bound and often slow), plus wasted LLM work producing an unread response. Directory grows unbounded across sessions.
- **Fix.**

  ```
  Two complementary fixes; apply the server-side guard as the primary, the client-side sweep as defense-in-depth.
  
  1) Server-side (primary): make send_response skip writing when the client already gave up. In ParallelIPCHandler.send_response (run_parallel_simulation.py ~286, and the analogous handlers in run_twitter_simulation.py / run_reddit_simulation.py), before writing response_file check whether the corresponding command file still exists; if it was deleted by the client's timeout cleanup, skip writing the response (and skip the redundant remove). Example:
      command_file = os.path.join(self.commands_dir, f"{command_id}.json")
      if not os.path.exists(command_file):
          # client timed out and removed the command; don't orphan a response
          return
      response_file = os.path.join(self.responses_dir, f"{command_id}.json")
      with open(response_file, 'w', ...): json.dump(response, ...)
      try: os.remove(command_file)
      except OSError: pass
  This still has a small TOCTOU window (client could delete between the existence check and the write), so combine with #2.
  
  2) Client-side (defense-in-depth): sweep stale ipc_responses/ files at the start of SimulationIPCClient.send_command (and optionally in __init__ / env start). Delete any *.json in responses_dir whose mtime is older than a threshold (e.g., max(timeout * 2, 300s)) so late/orphaned responses are reclaimed even if the server-side guard's race window is hit:
      now = time.time()
      for fn in os.listdir(self.responses_dir):
          if fn.endswith('.json'):
              fp = os.path.join(self.responses_dir, fn)
              try:
                  if now - os.path.getmtime(fp) > max(timeout * 2, 300):
                      os.remove(fp)
              except OSError:
                  pass
  Guard the sweep against value churn by anchoring on mtime, not the (now-deleted) command file. This bounds directory growth regardless of server timing.
  ```
- **Verified.**

  ```
  Confirmed against the actual code on both sides.
  
  Client (backend/app/services/simulation_ipc.py, SimulationIPCClient.send_command): the success branch (lines 158-172) removes BOTH command_file and response_file. But the timeout branch (lines 178-187) removes ONLY command_file and raises TimeoutError — it never deletes ipc_responses/<command_id>.json. command_id is a fresh uuid4 per call (line 139), so a response that arrives after timeout is never matched by any subsequent send_command call and never cleaned.
  
  Server (the real server is in backend/scripts/run_parallel_simulation.py, ParallelIPCHandler, not the unused SimulationIPCServer class): poll_command (263-284) loads the command dict into memory; handle_interview (352-379) / handle_batch_interview await env.step() (the long LLM interview) and THEN call send_response (286-305), which writes response_file unconditionally and only afterward tries os.remove(command_file). There is no guard checking whether the command file still exists, so even though the client has already deleted the command file on timeout, the server still holds command_id in memory and writes the orphan response. The same pattern exists in run_twitter_simulation.py and run_reddit_simulation.py (identical IPC_RESPONSES_DIR / response_file logic). The quoted evidence is accurate.
  
  No nearby cleanup neutralizes this: process_commands (567-608) has no stale-response sweep; the runner's cleanup_simulation_logs (1208-1286) deletes a fixed file/dir list (run_state.json, *.db, env_status.json, twitter/reddit actions) and explicitly does NOT touch ipc_commands/ or ipc_responses/; cleanup_all_simulations terminates child processes, it does not GC response files. send_command and env start do not sweep either (grep confirms no getmtime/stale logic touching responses_dir).
  
  Triggering condition is realistic: default timeouts are 60s (single interview, send_interview) and 120s (batch). INTERVIEW commands run env.step() with a per-agent LLM call; batch interviews loop over many agents in one command, so exceeding 120s is plausible. Each such timeout leaves one orphan JSON in ipc_responses/ plus wasted LLM work. Because sim_dir is reused across reruns (cleanup_simulation_logs preserves config/profiles and the ipc dirs), orphans accumulate across runs. This is a genuine, currently-true unbounded slow disk leak, matching the P2 robustness classification (no crash/correctness impact, gradual resource growth + wasted compute).
  ```

#### [F-6-2] /run-status/detail re-parses both full actions.jsonl files up to 4x per request; analytics rebuild whole history per call (unbounded, repeated)

`P2` · `bottleneck` · confidence **high** · effort **M** · `backend/app/services/simulation_runner.py` : 922-980, 1006-1014, 1811-1834

- **Symptom.** get_run_status_detail invokes get_all_actions 3-4 times (all_actions, twitter, reddit, current-round), and get_timeline/get_agent_stats each call get_actions(limit=10000) which reads the whole file again. With long runs every poll re-parses tens of thousands of JSONL lines several times.
- **Root cause.** get_all_actions has no caching and no offset; it reads each file end-to-end on every call, and the detail route plus analytics each call it independently. The frontend polls on an interval so cost grows linearly with action count and is paid per poll per client.
- **Evidence.** `get_actions calls `cls.get_all_actions(...)`; get_timeline/get_agent_stats: `actions = cls.get_actions(simulation_id, limit=10000)`; detail route calls `SimulationRunner.get_all_actions(...)` four times.`
- **Impact.** CPU/latency on status endpoints scale with run length; a 144-round multi-thousand-action parallel run reparses the full logs several times per poll, degrading the live UI and starving Flask workers.
- **Fix.** Lowest-risk first step: collapse the redundant calls inside get_run_status_detail itself. Call get_all_actions once (unfiltered all_actions), then derive the other slices in memory rather than re-reading: twitter_actions = [a for a in all_actions if a.platform == "twitter"]; reddit_actions = [a for a in all_actions if a.platform == "reddit"]; recent_actions = [a for a in all_actions if a.round_num == current_round]. This removes 3 of the 4 calls (and thus the ~3x per-file reparse) with no caching machinery or shared mutable state, preserving identical output. As a further optimization for the cross-endpoint repetition (timeline/agent_stats and repeated polls), add a parse cache in get_all_actions/_read_actions_from_file keyed by (file_path, st_mtime_ns, st_size) so an unchanged file is parsed once; invalidate automatically when the monitor thread appends (mtime/size change). Prefer the in-route fix first since the (path,mtime,size) cache adds invalidation surface and the files are append-only and frequently changing during a live run, which limits cache hit rate while the run is active.
- **Verified.** Confirmed from the actual code. get_all_actions (simulation_runner.py:921-980) has no caching, no offset, and no index: it delegates to _read_actions_from_file (851-919) which opens each actions.jsonl and iterates every line with json.loads end-to-end on every call. A grep for cache/mtime/lru_cache/index in simulation_runner.py returns nothing, so there is no guard the auditor missed. The detail route (simulation.py:1812-1834) calls get_all_actions exactly 4 times per request: all (1812), twitter (1818), reddit (1823), and current-round (1830). In the default no-filter case the Twitter file is reparsed by calls 1/2/4 and Reddit by calls 1/3/4 — each physical file parsed ~3x per request, matching the "3-4x" claim. Separately, get_timeline (1034) and get_agent_stats (1095) each call get_actions(limit=10000) -> get_all_actions with no filter, fully re-parsing both files; these back distinct /timeline and /agent-stats endpoints. The detail endpoint is polled every 3s by the frontend (Step3Simulation.vue:471 setInterval(fetchRunStatusDetail, 3000) -> getRunStatusDetail -> /run-status/detail), so the redundant parsing is paid per poll per client and grows linearly with cumulative action count for long (e.g. 144-round, multi-thousand-action) runs. This is a genuine, currently-true performance/scalability defect on a synchronous Flask endpoint, not a correctness bug; P2 is the correct severity (degrades live UI and consumes workers, no data loss or crash).

#### [F-6-3] get_timeline/get_agent_stats/run_summary silently cap at limit=10000 newest actions, truncating analytics on long simulations

`P2` · `correctness` · confidence **high** · effort **S** · `backend/app/services/simulation_runner.py` : 1034, 1095, 1145

- **Symptom.** Timeline rounds and per-agent stats use only the most recent 10000 actions (sorted newest-first), so early rounds/agents disappear and engagement totals understate once a run exceeds 10000 total actions.
- **Root cause.** get_timeline and get_agent_stats call get_actions(limit=10000); get_actions slices actions[offset:offset+limit] AFTER sorting newest-first, dropping the oldest. write_run_summary inherits the same cap, so run_summary.json (consumed by the report agent) is also truncated.
- **Evidence.** `get_timeline: `actions = cls.get_actions(simulation_id, limit=10000)`; get_agent_stats: same; write_run_summary: `actions = cls.get_actions(simulation_id, limit=10000)`.`
- **Impact.** For high-volume runs the report agent's run_summary (action_volume_by_round, top_agents, peak_round) is computed on a truncated late-run window, biasing the forecast and understating early-round dynamics. A 100-agent x 144-round run easily exceeds 10000 actions.
- **Fix.** Make the analytics/aggregation paths read the complete action history instead of a paginated slice. In simulation_runner.py, change get_timeline (line 1034), get_agent_stats (line 1095), and write_run_summary (line 1145) to call cls.get_all_actions(simulation_id) (no limit) rather than cls.get_actions(simulation_id, limit=10000). get_all_actions already returns the full, sorted list and accepts the same optional filters, so this is a drop-in change with no signature impact. Keep get_actions(limit, offset) only for the user-facing paginated /actions endpoint (api/simulation.py:1887). Sort order does not affect the aggregators since they bucket by round/agent regardless of order. Optionally, for consistency, the two zep_tools.py call sites that currently pass limit=100000 (lines 1843, 1901) could also switch to get_all_actions to avoid a future silent cap, though 100000 is a much safer ceiling and is lower priority.
- **Verified.**

  ```
  Confirmed from the actual code. get_all_actions (simulation_runner.py:978) sorts actions newest-first (actions.sort(key=lambda x: x.timestamp, reverse=True)), and get_actions (line 1014) returns actions[offset:offset+limit] AFTER that sort. So get_actions(limit=10000) yields only the 10000 most-recent actions, dropping the oldest (earliest-round) ones once a run exceeds 10000 total actions.
  
  All three analytics paths use this capped call: get_timeline (line 1034), get_agent_stats (line 1095), and write_run_summary (line 1145) each call get_actions(simulation_id, limit=10000). write_run_summary computes action_volume_by_round, top_agents, peak_round, total_actions, and rounds_executed from these truncated reads, and its output run_summary.json is wired into the pipeline artifacts (pipeline_orchestrator.py:1678-1681) and consumed by the report/forecast stage. So early-round dynamics disappear, per-agent engagement totals understate, rounds_executed undercounts, and peak_round is biased toward the late-run window for high-volume simulations.
  
  The 10000 cap is realistic to exceed: config allows 24-168 simulation hours at 30-120 min/round (up to ~336 rounds) with agent counts scaling to ~0.9 * entity count (simulation_config_generator.py:772). A 100-agent run over 100+ rounds with even modest per-round activity surpasses 10000 actions. The cap is silent (no warning logged on truncation).
  
  Corroborating evidence that this is a genuine oversight, not intended: the developers already recognized 10000 is too small and bumped raw-action exports to limit=100000 in zep_tools.py:1843 and 1901, but left the three aggregation paths at 10000. The aggregators have no business being paginated at all — they fold every action into per-round/per-agent buckets.
  
  Not a misreading, not guarded, not dead code, not intended behavior. The write_run_summary try/except only swallows exceptions; it does not mitigate silent truncation. P2 is correct: pure correctness/analytics-accuracy degradation that only manifests on long/high-volume runs, no crash or data loss on disk (actions.jsonl remains complete), and it biases the forecast rather than breaking the pipeline.
  ```

### x-concurrency — CROSS-CUTTING: lifecycle, concurrency, resource leaks

#### [F-12-6] start_simulation 'already running' guard and stop rely on in-memory state that is stale after restart, blocking re-runs of crashed simulations

`P2` · `robustness` · confidence **high** · effort **S** · `backend/app/services/simulation_runner.py` : 341-343, 805-812

- **Symptom.** get_run_state loads run_state.json into memory; if a previous process crashed mid-run, the persisted runner_status is RUNNING. start_simulation then raises ValueError('模拟已在运行中') even though no process is actually running in this process, and stop_simulation will try to terminate a process that isn't in _processes.
- **Root cause.** runner_status is trusted across process boundaries without cross-checking that a live Popen exists in _processes (or that process_pid is actually alive). There is no reconcile that downgrades a stale RUNNING to FAILED/STOPPED on startup (see the P0 finding).
- **Evidence.** `existing = cls.get_run_state(simulation_id); if existing and existing.runner_status in [RUNNING, STARTING]: raise ValueError('模拟已在运行中'); process = cls._processes.get(simulation_id) (no PID fallback).`
- **Impact.** After a crash, the user cannot restart a simulation for that simulation_id without manually editing/deleting run_state.json; stop_simulation silently no-ops on the real (now-orphaned) process.
- **Fix.**

  ```
  Two coordinated changes, mirroring existing patterns in PipelineOrchestrator:
  
  1) Add a SimulationRunner.reconcile_orphans() classmethod and call it once at startup in backend/app/__init__.py (next to SimulationRunner.register_cleanup() at line 47, before PipelineOrchestrator.reconcile_orphans()). It should iterate persisted simulations whose run_state.json has runner_status in {RUNNING, STARTING, STOPPING, PAUSED} and, since cls._processes is necessarily empty at process start, verify the persisted process_pid: if the PID is dead OR alive-but-not-this-run (verify via `ps -p <pid> -o command=` containing run_parallel/twitter/reddit_simulation.py, exactly like _kill_orphan_research's PID-reuse guard), kill the orphan process group (os.killpg(os.getpgid(pid), SIGTERM)) and downgrade the persisted state to FAILED/STOPPED with an explanatory error, then re-save via _save_run_state. Wrap in try/except so a reconcile failure never blocks startup (mirror pipeline_orchestrator.py:792).
  
  2) Harden start_simulation (lines 341-343): treat RUNNING/STARTING as "in progress" only when cls._processes has a live entry for this simulation_id (process.poll() is None) OR the persisted process_pid is verified alive AND its command line matches this run's script. Otherwise reconcile the stale state to STOPPED (and best-effort kill any verified orphan PID) and proceed with the restart instead of raising. Also give stop_simulation a process_pid fallback (lines 818-832): when cls._processes has no entry, look up the persisted process_pid, verify it belongs to this run, and terminate its process group so orphaned OASIS processes are actually stopped rather than silently no-op'd.
  
  Reuse the existing _terminate_process helper (lines 748-802) and the ps-based PID-reuse guard from pipeline_orchestrator._kill_orphan_research to avoid killing a reused PID.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. start_simulation (backend/app/services/simulation_runner.py:341-343) calls get_run_state, which loads the persisted run_state.json into memory (lines 237-302) and trusts runner_status across process boundaries. After a hard crash/restart, the monitor thread that would flip RUNNING->FAILED/STOPPED (lines 534-563) never runs, so run_state.json stays "running", while in-memory cls._processes is empty. start_simulation then raises ValueError("模拟已在运行中") even though no live process exists in this process. There is NO SimulationRunner-level reconcile: app startup (backend/app/__init__.py:57) only calls PipelineOrchestrator.reconcile_orphans(), which downgrades stale pipeline_state.json to failed and kills the deerflow_research.py child by PID (pipeline_orchestrator.py:764-816) — it never touches the OASIS run_state.json nor kills the orphaned OASIS subprocess.
  
  The realistic trigger is pipeline resume. On resume (pipeline_orchestrator.py:1009, _run re-entry), if PREPARE was completed the code reuses the env without cleanup (line 1560), and if RUN was not completed it falls into the else branch and calls SimulationRunner.start_simulation directly (line 1628) with no force/cleanup. With the stale "running" run_state.json, that call raises and the resume fails immediately and repeatedly — with no escape hatch in the resume path. (The direct REST start handler at api/simulation.py:1542-1577 does have a force=true path that calls cleanup_simulation_logs to delete run_state.json, which is exactly the "manually edit/delete" workaround the finding describes; without force it also blocks.)
  
  The stop_simulation symptom is also accurate: because OASIS is spawned with start_new_session=True, a backend crash orphans the OASIS subprocess; after restart stop_simulation reads cls._processes.get(simulation_id) (line 818, empty) with no PID fallback, so it silently flips status to STOPPED without terminating the still-alive orphan. process_pid is persisted (line 281) but never used for liveness/termination.
  
  Severity P2 is correct: it blocks recovery of crashed simulations (an availability/robustness defect, and precisely the crash-resume scenario the codebase's reconcile machinery exists to handle), but it is not data corruption/security (not P0/P1) and a manual force-restart workaround exists for the REST path; worse than P3 because the pipeline resume path has no workaround.
  ```

#### [F-12-8] Concurrent runtime calls share one event loop, one redislite FalkorDB client, and cached per-graph Graphiti instances with no per-graph write/read serialization

`P2` · `concurrency` · confidence **medium** · effort **L** · `backend/app/services/graphiti_client/runtime.py` : 49-75, 173-209, 370-395

- **Symptom.** GraphitiRuntime is a process-global singleton with a single background event loop and a single shared AsyncFalkorDB client; any thread (pipeline graph-build, simulation feedback updater, report-agent search, manual report) calls runtime.run() which schedules coroutines onto that one loop. Concurrent add_episode/add_triplet/search against the same graph_id interleave at await points with no mutual exclusion beyond Graphiti's internal per-call max_coroutines semaphore.
- **Root cause.** _ensure_graph caches one Graphiti per graph_id and hands the same instance to all callers; _ensure_lock only protects construction, not concurrent use. The design assumes single-writer-at-a-time but the pipeline drives multiple writers/readers (feedback loop during RUN, report reads, fork/scenario runs reusing the same base graph) against one embedded DB.
- **Evidence.** `self._loop = asyncio.new_event_loop(); self._falkor_client shared; _ensure_graph caches self._graphs[graph_id]; add_episodes_concurrent fans out asyncio.gather under only a local Semaphore; clone() reuses self.client for every database.`
- **Impact.** Under concurrent access to the same graph (feedback writer + report reader, or two scenario forks of the same base graph_id) the embedded FalkorDB can see interleaved partial mutations and the report can read mid-transaction state; in the worst case dedup/edge-resolution races produce duplicated or missing nodes. Hard to reproduce but corrupts the very graph the forecast depends on.
- **Fix.**

  ```
  Serialize mutating and read-vs-write access per graph_id rather than relying on the construction-only _ensure_lock or Graphiti's count-only semaphore.
  
  1. Add a per-graph_id asyncio.Lock registry on the runtime, created lazily on the bg loop:
     - self._graph_locks: dict[str, asyncio.Lock] = {} plus a helper async def _graph_lock(self, gid) that returns/creates the lock (creation guarded by self._ensure_lock or a dedicated lock, since dict mutation happens on the single loop thread it is already serialized).
  2. Acquire that lock around the mutating bodies of _add_episode, _add_triplet, and _build_communities (wrap the g.add_episode/g.add_triplet/g.build_communities call). This guarantees single-writer-per-graph while still allowing different graph_ids to proceed in parallel. For add_episodes_concurrent, hold the lock for the whole fan-out OR (preferred to retain its speedup) accept its documented intra-batch dedup tradeoff but still take the lock so it never overlaps an external writer/reader on the same graph.
  3. To prevent dirty reads, make _search/_list_nodes/_list_edges/_get_node/_get_node_edges acquire the SAME per-graph lock in shared mode. asyncio.Lock is mutually exclusive (no shared mode); since contention is rare, simplest correct fix is to take the per-graph lock for reads too (serializing reads against writes on the same graph). If read latency under a long write matters, upgrade to an asyncio readers-writer lock (e.g. aiorwlock) keyed by graph_id.
  4. Orchestration-level guard (complements the lock): in PipelineOrchestrator.fork, do not allow a fork's RUN/feedback writer to run while another pipeline holding the same graph_id is in REPORT (or another fork is writing). Track active writers per graph_id and either queue the fork's RUN stage or snapshot/clone the base graph into a fork-specific graph_id so scenario runs are isolated (cleaner long-term: each fork gets its own graph_id seeded from the base, eliminating shared-mutation entirely). Document and enforce single-writer-at-a-time semantics for any shared base graph_id.
  ```
- **Verified.**

  ```
  CONFIRMED REAL. The code substantiates every load-bearing claim.
  
  Architecture (verified by reading source):
  - runtime.py:52-74: GraphitiRuntime is a process-global singleton (get_runtime, lines 513-523) with ONE background event loop (self._loop = asyncio.new_event_loop()) and run() schedules every coroutine onto it via asyncio.run_coroutine_threadsafe.
  - runtime.py:112-119: a SINGLE shared self._falkor_client (AsyncFalkorDB) is reused for all graph_ids.
  - runtime.py:173-209: _ensure_graph caches one Graphiti per graph_id in self._graphs and returns the SAME instance to all callers. _ensure_lock (lines 176-178) is held ONLY during construction; once cached, add_episode/add_triplet/search/build_communities run with NO per-graph lock. The only concurrency bound is Graphiti's internal max_coroutines semaphore (line 197) which is per-instance/shared but merely caps coroutine count — it does NOT serialize the read-resolve-write dedup logic.
  
  Concurrent same-graph_id access is reachable and real (not single-writer as designed):
  1. Scenario fork (the decisive case): POST /<pipeline_id>/scenario (api/research.py:199-220) -> PipelineOrchestrator.fork (pipeline_orchestrator.py:1135-1196) reuses base_state.graph_id (line 1157) and spawns an independent daemon thread (line 1192) that re-runs prepare/run/report. Nothing prevents firing multiple forks, or forking while the base pipeline is still in REPORT.
  2. Feedback writer runs in-process, concurrently: simulation is a separate subprocess, but the graph WRITES happen in the orchestrator process. SimulationRunner spawns a per-simulation monitor thread (_monitor_simulation, simulation_runner.py:474-493) that tails the action log (_read_action_log, line 594) and calls the graph_updater -> client.graph.add_triplet/add_episode (zep_graph_memory_updater.py:471,490) -> runtime.run(). So two concurrent scenario simulations on the same base graph_id produce two monitor threads writing to the same cached Graphiti via the one loop, and a fork's writer can overlap the base's report reader (runtime.search, runtime.list_nodes).
  3. The maintainers already acknowledge the dedup race: add_episodes_concurrent's own docstring (runtime.py:374-376) admits it "Trades a small dedup-ordering risk for a large speedup." Cross-call concurrency carries the same hazard with no mitigation.
  
  Why the underlying DB does not save it: even if each Cypher query is atomic, Graphiti's add_episode performs a multi-step search-then-resolve-then-create sequence across await points. Two concurrent episodes introducing the same new entity can both miss the dedup lookup and create duplicate nodes/edges; a reader can observe partial graph state. This corrupts the very graph the forecast/report depends on.
  
  Within-single-pipeline note: one pipeline's RUN->REPORT is strictly sequential in one thread (the RUN polling loop at pipeline_orchestrator.py:1632-1668 blocks until COMPLETED before REPORT at 1686), so the finding's "feedback writer + report reader" only manifests across pipelines (fork overlap), not within a single base run. This narrows but does not negate the defect — the cross-pipeline fork path is a documented, API-exposed feature.
  
  Severity P2 is correct: real correctness hazard (graph corruption / duplicate-or-missing nodes, dirty reads) but only under concurrent same-graph_id usage (forking scenarios while another run/report touches the same base graph), which is not the default single-run path and is hard to hit deterministically. Not data-loss-on-every-run (not P0/P1), not cosmetic (not P3).
  ```

#### [F-12-3] SimulationRunner signal handler raises KeyboardInterrupt from inside the chained PipelineOrchestrator handler when the original handler is SIG_DFL

`P2` · `concurrency` · confidence **medium** · effort **S** · `backend/app/services/simulation_runner.py` : 1427-1448

- **Symptom.** On SIGTERM (the default kill signal) when no Python-level original handler was installed (original_sigterm is SIG_DFL / not callable), SimulationRunner.cleanup_handler falls into the else branch and executes `raise KeyboardInterrupt` instead of restoring SIG_DFL and re-raising the actual signal. PipelineOrchestrator installs its handler second and chains into this one, so the KeyboardInterrupt is raised back inside PipelineOrchestrator's cleanup_handler frame.
- **Root cause.** The else branch hardcodes `raise KeyboardInterrupt` for the SIG_DFL/unknown case, inconsistent with PipelineOrchestrator's own handler which correctly does signal.signal(signum, SIG_DFL); os.kill(getpid(), signum). For SIGTERM the default behavior is process termination, not KeyboardInterrupt; converting it can be swallowed by an enclosing try/except in the interrupted frame and fail to terminate the process.
- **Evidence.** `else: # 如果原处理器不可调用（如 SIG_DFL），则使用默认行为\n    raise KeyboardInterrupt`
- **Impact.** On hosting/orchestration that sends SIGTERM (Docker stop, systemd, supervisor), the backend may not exit promptly; the KeyboardInterrupt can be caught elsewhere, leaving the process (and its sim/research subprocesses) lingering until SIGKILL.
- **Fix.**

  ```
  Mirror PipelineOrchestrator's correct teardown in SimulationRunner.cleanup_handler's else branch. Replace lines 1446-1448:
  
      else:
          # 原处理器为 SIG_DFL / SIG_IGN / 未知：恢复默认行为并重新投递信号，
          # 以与 PipelineOrchestrator 的处理保持一致，确保进程能正常终止。
          if signal.getsignal(signum) is signal.SIG_IGN:  # or compare the saved original
              return
          signal.signal(signum, signal.SIG_DFL)
          os.kill(os.getpid(), signum)
  
  Concretely: for SIG_IGN preserve ignore (return); otherwise (SIG_DFL/None) do signal.signal(signum, signal.SIG_DFL); os.kill(os.getpid(), signum). This makes SIGTERM/SIGHUP terminate the process via the OS default rather than a swallowable KeyboardInterrupt. (Ensure `os` and `signal` are imported — they already are in this module.) Optionally retain `raise KeyboardInterrupt` only for SIGINT-with-no-original-handler, but using SIG_DFL + os.kill uniformly is cleaner and matches the sibling handler.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. In /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_runner.py:1427-1448, cleanup_handler's else branch (line 1446-1448) executes `raise KeyboardInterrupt` for the SIG_DFL/SIG_IGN/None original-handler case. The chaining claim is verified by registration order: in backend/app/__init__.py, SimulationRunner.register_cleanup() (line 47) installs its handler first (saving the prior SIGTERM disposition, which for a normal process is SIG_DFL, into original_sigterm); PipelineOrchestrator.register_cleanup() (line 58) installs its handler second, saving SimulationRunner.cleanup_handler as `original`. On SIGTERM the OS invokes PipelineOrchestrator.cleanup_handler (pipeline_orchestrator.py:847), which chains via `prev(signum, frame)` (line 851) into SimulationRunner.cleanup_handler. There, since signum==SIGTERM and original_sigterm is SIG_DFL (not callable), line 1437's branch is False, the SIGHUP branch is skipped, and the else at 1446 runs `raise KeyboardInterrupt`.
  
  This is incorrect and inconsistent with PipelineOrchestrator's own handler (pipeline_orchestrator.py:854-857), which for the SIG_DFL/unknown case correctly does `signal.signal(signum, signal.SIG_DFL); os.kill(os.getpid(), signum)`. SIGTERM's default disposition is process termination, not KeyboardInterrupt. KeyboardInterrupt is a regular BaseException that propagates up the interrupted frame's stack (the Flask/Werkzeug serving loop) and can be swallowed by any enclosing try/except — so it does not reliably terminate the process. PipelineOrchestrator also handles SIG_IGN (return), whereas SimulationRunner's else would convert a previously-ignored signal into a KeyboardInterrupt.
  
  Not a misreading, not dead code (register_cleanup is invoked at app init), not guarded — confirmed real, currently-true defect.
  
  Impact: under Docker stop / systemd / supervisor (which send SIGTERM), graceful shutdown can fail or be swallowed, leaving the backend and its simulation/research subprocesses lingering until SIGKILL. Limited to the shutdown path; no effect on normal request handling or data integrity, so P2 is correct (not P1).
  ```

### orchestrator — Pipeline orchestrator + models

#### [F-1-0] REPORT stage has no reuse guard — resume regenerates the entire forecast (re-runs full LLM tool agent) even if the report already succeeded

`P2` · `correctness` · confidence **high** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 1686-1724  ·  ↺ overlaps EXECPLAN

- **Symptom.** A pipeline that completed the REPORT stage but failed/crashed on the final DONE bookkeeping will, on resume(), re-enter the REPORT stage and run agent.generate_report() from scratch — a long, expensive multi-tool LLM job — discarding the already-produced report.
- **Root cause.** Every other stage guards reuse with a `<stage>_stage_done = state.stages.get(STAGE_X).status == 'completed'` check (graph 1476, prepare 1558, run 1613-1614), but the REPORT stage has no such guard. It unconditionally allocates a fresh report_id and calls generate_report() every time _run reaches it.
- **Evidence.** `grep _stage_done shows guards at 1476/1558/1613 but REPORT (1686+) goes straight to `report_id = f"report_{uuid.uuid4().hex[:12]}"` then `report = agent.generate_report(...)` with no completed-status check.`
- **Impact.** On resume/continue, the most expensive stage (tool-augmented report generation against graph+simulation) is needlessly repeated, doubling LLM spend and wall-clock, and minting a NEW report_id that orphans the prior report and any frontend/bookmark links to it. For a pipeline whose only failure was the trailing DONE save, this is a guaranteed full re-run.
- **Fix.**

  ```
  Guard REPORT reuse on the saved report itself (NOT on the stage status, which is always reset to failed/pending at resume time because REPORT is the current_stage when the trailing DONE work fails). Replace the head of the REPORT stage (around lines 1687-1689) with a report-id-based reuse check:
  
  ```python
  # ---- Stage 5: REPORT ----
  upd = self._make_stage_updater(state, STAGE_REPORT)
  # Reuse guard: a prior run may have finished generate_report() (state.report_id set,
  # report saved) but crashed in the trailing DONE bookkeeping. On resume the REPORT
  # stage status is no longer "completed" (it was flipped to failed by _fail_stage /
  # mark_failed because it was the current_stage), so we key reuse off the saved report.
  existing_report = None
  if state.report_id:
      try:
          existing_report = ReportManager.get_report(state.report_id)
      except Exception:
          existing_report = None
  if existing_report is not None and getattr(existing_report, "status", None) != ReportStatus.FAILED:
      upd(100, "复用已有报告")
      self._complete_stage(state, STAGE_REPORT, "报告完成（复用）")
  else:
      upd(5, "生成预测报告…")
      report_id = f"report_{uuid.uuid4().hex[:12]}"
      # ... existing generate_report() body unchanged through line 1724 ...
  ```
  
  Notes:
  - This reuses the previously saved report and preserves the original report_id (no orphaning of frontend/bookmark links).
  - It deliberately does NOT use state.stages[STAGE_REPORT].status, which the original proposal relied on; that value is never "completed" at resume time.
  - Verify ReportStatus and ReportManager are already imported (they are: line 45). Keep the existing FAILED-raises check (1722-1723) inside the else branch.
  - Optional hardening: also fall back to ReportManager.get_report_by_simulation(sim_state.simulation_id) if state.report_id is unset but a report for this simulation already exists.
  ```
- **Verified.**

  ```
  VERIFIED REAL (with a corrected fix and downgraded severity).
  
  The factual claim is true. In backend/app/services/pipeline_orchestrator.py the REPORT stage (lines 1686-1724) has no reuse guard: it unconditionally mints `report_id = f"report_{uuid.uuid4().hex[:12]}"` (1689) and calls `agent.generate_report(...)` (1716), unlike GRAPH (guard at 1476), PREPARE (1558), and RUN (1613-1614) which all gate on `<stage>_stage_done = ...status == "completed"`. Confirmed via grep: `_stage_done` guards exist at 1476/1558/1593/1613 but not for REPORT.
  
  Resume reaches this code via PipelineOrchestrator.resume() -> _run thread (1058-1065). So a pipeline whose REPORT stage completed (_complete_stage at 1724 saved REPORT status="completed" and state.report_id at 1721) but then failed in the trailing DONE block (1726-1738) will, on resume, re-run the most expensive stage from scratch and mint a NEW report_id, orphaning the saved report. ReportManager.get_report(report_id)/get_report_by_simulation() (report_agent.py 2893/2947) confirm a saved report is retrievable, so reuse is feasible. The defect is genuine.
  
  CRITICAL CORRECTION TO THE FINDING: the proposed fix is ineffective. It checks `state.stages[STAGE_REPORT].status == 'completed'`, but in every realistic resume scenario that status has ALREADY been overwritten away from "completed" by the time resume runs:
   - Caught-exception path: the `except Exception` handler (1755) calls `_fail_stage(state, state.current_stage)` (1760); current_stage is "report", so REPORT stage status -> "failed" (1376).
   - Hard-crash path: reconcile_orphans (764) -> PipelineManager.mark_failed (217) sets stages[current_stage]["status"]="failed" (230-234), and current_stage=="report".
   - resume() then resets that failed current_stage to "pending" (1037-1042).
  So state.stages[STAGE_REPORT].status is "pending"/"failed" at resume — never "completed". The other stages' guards work ONLY because they are never the current_stage at crash time, so their "completed" survives. REPORT, always being the last/current stage when the trailing DONE work fails, is uniquely unprotectable by a stage-status guard. The correct reuse signal is state.report_id resolving to a non-FAILED saved report.
  
  SEVERITY: downgraded P1 -> P2. The impact (wasted LLM spend + orphaned report_id) is real but confined to a narrow recovery edge case (REPORT-succeeded-but-DONE-failed) triggered only by a manual resume; and a crash that occurs DURING report generation legitimately requires regeneration anyway. It is a cost/correctness inefficiency in a rare path, not a routine functional break.
  ```

### graph-shim — Graphiti shim (Zep-compatible) + FalkorDB driver

#### [F-2-5] Concurrent add_episode on one cached Graphiti instance shares mutable driver/clients (dedup ordering + state hazard)

`P2` · `concurrency` · confidence **medium** · effort **L** · `backend/app/services/graphiti_client/runtime.py` : 380-395

- **Symptom.** With GRAPH_BUILD_CONCURRENCY>1, multiple add_episode coroutines run concurrently against the same Graphiti instance; entity dedup/resolution can race and produce duplicate or mis-merged nodes/edges.
- **Root cause.** _add_episodes_concurrent fans out N concurrent _add_episode calls on the single cached Graphiti object. graphiti.add_episode reads/resolves existing nodes by name+embedding and writes them; two episodes mentioning the same entity in-flight can both miss the not-yet-committed node and each create one. The code itself acknowledges 'a small dedup-ordering risk'. It also mutates self.driver via clone() when group_id != _database (graphiti.py:1081); the runtime pins _database==graph_id to avoid that, so the clone hazard is mitigated, but the resolve-then-write race is not.
- **Evidence.** `return list(await asyncio.gather(*[one(i, ep) for i, ep in enumerate(episodes)]))  # "Trades a small dedup-ordering risk for a large speedup"`
- **Impact.** Duplicate entities/edges and reduced graph quality under concurrency; non-deterministic graph contents. Only the serial default (concurrency=1) is safe, limiting the speedup this feature is meant to provide.
- **Fix.** Keep the default GRAPH_BUILD_CONCURRENCY=1. For the opt-in fast path, do not naively fan out add_episode; instead drive graphiti_core's add_episode_bulk (graphiti.py:1230), which is designed to dedup a batch of episodes in one pass (single resolution phase over the whole batch, then a single bulk write) and avoids the cross-coroutine read-then-write window. If add_episode_bulk is not acceptable, split the pipeline: run only the embarrassingly-parallel LLM extraction concurrently, then serialize the resolve+write phase per graph_id behind an asyncio.Lock so no two episodes commit overlapping entities concurrently. At minimum, document the feature explicitly as 'extraction-parallel, write-serial only' and update the comment/docstring at runtime.py:374-376 from 'a small dedup-ordering risk' to an explicit warning that concurrency>1 can create duplicate same-name entity nodes because graphiti_core has no DB-side name-uniqueness constraint and resolution reads uncommitted state. Do not raise the default above 1 until one of the above is implemented.
- **Verified.**

  ```
  Confirmed by reading the actual code. runtime.py:380-395 (_add_episodes_concurrent) fans out N concurrent _add_episode calls under a semaphore against the SAME cached Graphiti instance (one per graph_id, cached in self._graphs). Each _add_episode calls g.add_episode (graphiti_core 0.29.2, installed at backend/.venv/.../graphiti_core/graphiti.py:980).
  
  The resolve-then-write race is real and confirmed: inside add_episode, resolve_extracted_nodes (graphiti.py:1131 -> node_operations.py:627) reads existing nodes from the DB via _collect_candidate_nodes (semantic/name retrieval) to decide whether an extracted entity is a duplicate. The actual write occurs much later in _process_episode_data (graphiti.py:1170 -> add_nodes_and_edges_bulk, bulk_utils.py:128). Multiple awaits separate the read from the write, so with concurrency>1 the event loop interleaves coroutines: two episodes mentioning the same not-yet-committed entity both query, both miss, and each creates a node. Nodes are written by fresh per-node UUID with no DB-side uniqueness constraint on name (bulk_utils.py writes uuid/name/group_id; build_indices_and_constraints does not enforce name uniqueness), so the race produces two distinct same-name nodes rather than a constraint error. This is a genuine TOCTOU/dedup hazard, and the code comment itself admits "Trades a small dedup-ordering risk for a large speedup."
  
  The clone sub-claim is correctly characterized as MITIGATED (not a current defect): graphiti.py:1079-1081 only does self.driver = self.driver.clone(database=group_id) when group_id != self.driver._database. The runtime pins g.driver._database = graph_id (runtime.py:201, also 149 for kuzu) and always passes group_id=graph_id (runtime.py:301), so the clone branch is never taken; the shared-driver-swap hazard does not fire. The finding states this correctly.
  
  Reachability: GRAPH_BUILD_CONCURRENCY defaults to 1 (config.py:350), and add_batch (client.py:150-160) only takes the concurrent path when concurrency>1 and len(eps)>1. So the serial default is safe; the defect manifests only when an operator opts into the speedup feature -- which is precisely the feature's purpose, so the feature is unsafe to actually use as intended.
  
  Severity P2 is appropriate: non-deterministic duplicate/mis-merged entities degrade graph quality but cause no crash, exception, or data loss; impact is gated behind a non-default opt-in env var. Not P1 (no broken default behavior, no error path) and not P3 (it does defeat the dedup invariant the system relies on).
  ```

#### [F-2-1] delete_graph never deletes graph data on the FalkorDB *server* backend (silent no-op)

`P2` · `correctness` · confidence **high** · effort **S** · `backend/app/services/graphiti_client/runtime.py` : 498-503

- **Symptom.** When GRAPH_BACKEND=falkordb (external/server), deleting a graph removes it from the in-process cache but leaves all of its nodes/edges in the FalkorDB server forever.
- **Root cause.** The data-deletion branch is gated on `self._falkor_client is not None`, but self._falkor_client is ONLY populated by _get_falkor_client() on the embedded (falkordblite) path. For the server backend each driver builds its own FalkorDB client (falkordb_driver.py:161) and self._falkor_client stays None, so select_graph(...).delete() is skipped entirely.
- **Evidence.** `if self._falkor_client is not None and hasattr(self._falkor_client, "select_graph"): graph = self._falkor_client.select_graph(graph_id); await graph.delete()`
- **Impact.** Storage/tenant leak on server deployments; a deleted graph_id still occupies the DB and can collide or accumulate. The except: pass also swallows any failure, so the caller always sees success.
- **Fix.**

  ```
  Delete the FalkorDB graph data via the driver actually in use, for BOTH backends, and stop swallowing failures. The cached Graphiti instance's driver exposes `.client` (the FalkorDB connection) for both embedded and server backends, and `client.select_graph(name).delete()` is the correct op. Obtain the handle BEFORE closing `g`. Rewrite `_delete_graph` (runtime.py:490-503) roughly as:
  
  ```python
  async def _delete_graph(self, graph_id):
      g = self._graphs.pop(graph_id, None)
      self._ontologies.pop(graph_id, None)
      # Resolve the FalkorDB connection from the live driver (works for both
      # 'falkordb' server and 'falkordblite' embedded backends). Fall back to the
      # shared embedded client when the graph is not in the cache.
      falkor_client = None
      if g is not None:
          falkor_client = getattr(getattr(g, "driver", None), "client", None)
      if falkor_client is None:
          falkor_client = self._falkor_client
      # Drop server-side data first, while the connection is still open.
      if falkor_client is not None and hasattr(falkor_client, "select_graph"):
          try:
              graph = falkor_client.select_graph(graph_id)
              await graph.delete()
          except Exception as exc:
              logger.warning("Failed to delete FalkorDB graph %s: %s", graph_id, exc)
      if g is not None:
          try:
              await g.close()
          except Exception as exc:
              logger.debug("Error closing graph %s after delete: %s", graph_id, exc)
  ```
  
  Notes: (1) For the server backend reusing the same FalkorDB connection across graphs, closing one graph's driver should not tear down a shared pool needed by others; if `g.close()` closes a shared client, prefer not closing it on the server backend (or only close per-database) — verify against FalkorDriver.close(). (2) Cold-cache deletes (graph never `_ensure_graph`'d in this process) on the server backend still won't find a client via `self._falkor_client`; if that scenario matters, build a transient FalkorDB client from FALKORDB_* env to perform the delete. (3) Do not keep `except: pass` — log at warning so the leak is observable and the caller's success is not falsely reported.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. In /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py, `_delete_graph` (lines 490-503) gates server-side data deletion on `self._falkor_client is not None` (line 499). `self._falkor_client` is populated ONLY by `_get_falkor_client()` (lines 112-119), which is called ONLY on the embedded `falkordblite` path (`_make_driver` line 138; and line 220). For the server backend `falkordb`, `_make_driver` (lines 124-133) builds `SanitizingFalkorDriver(host=..., port=...)` directly, which delegates to the upstream FalkorDriver constructor that creates its own `self.client = FalkorDB(...)` (graphiti_core/driver/falkordb_driver.py:161). The runtime's `self._falkor_client` therefore stays None for the server backend, so `select_graph(graph_id).delete()` (lines 500-501) is skipped entirely, and the surrounding `except: pass` (lines 502-503) swallows any error. Net effect: deleting a graph pops the in-process cache but leaves all nodes/edges on the FalkorDB server forever, and the caller always sees success. The same None-vs-server bug pattern is even visible at lines 219-220 for cold-cache discovery, corroborating the root cause.
  
  This is live, reachable code: the DELETE /graph/delete/<graph_id> API endpoint (backend/app/api/graph.py:592-605) -> GraphBuilderService.delete_graph (graph_builder.py:542) -> client.py:136 -> runtime._delete_graph. The `falkordb` server backend is an explicitly supported config (_VALID_BACKENDS line 31; auto-selected when FALKORDB_HOST is set, lines 87-88), so this is not dead/unreachable. The embedded path deletes correctly (select_graph maps to the per-graph database keyed by graph_id, passed as database=graph_id at lines 132/139), so this is purely a server-deployment storage/tenant leak plus a false-success — consistent with P2.
  
  Two cosmetic inaccuracies in the finding (do not affect the verdict): the project driver file is falkor_driver.py (not falkordb_driver.py), and the cited line 161 is in the upstream graphiti_core base class, not the project file — but the project's SanitizingFalkorDriver(host=..., port=...) call (runtime.py:131) does route through that base constructor, so the substance holds.
  ```

#### [F-2-2] Bi-temporal datetime fields leak through the Zep facade as datetime objects, not ISO strings

`P2` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/graphiti_client/client.py` : 50-53

- **Symptom.** Edge/node temporal fields (created_at/valid_at/invalid_at/expired_at) arrive at consumers as Python datetime objects; any json.dumps of those consumer dicts raises 'Object of type datetime is not JSON serializable'.
- **Root cause.** _ZepEdge copies graphiti EntityEdge's valid_at/invalid_at/expired_at/created_at straight through (and _ZepNode.created_at likewise). graphiti_core/edges.py:271-277 types these as `datetime | None`. The real Zep Cloud SDK returned ISO-8601 *strings*; downstream EdgeInfo declares them `Optional[str]` (zep_tools.py:92-95) and to_dict()/json.dumps paths (zep_tools.py:240-241, 1662) assume strings.
- **Evidence.** `self.valid_at = getattr(e, "valid_at", None)  # graphiti returns a datetime; Zep returned an ISO string`
- **Impact.** Temporal-aware code paths (get_all_edges(include_temporal=True) -> to_dict -> JSON) throw or silently differ from the documented Zep contract; comparisons that expected strings now compare datetimes. Breaks the 'works unchanged' promise in the module docstring.
- **Fix.**

  ```
  Normalize datetimes to ISO-8601 strings at the facade boundary in graphiti_client/client.py, so every downstream consumer receives the documented Zep string contract (single chokepoint; fixes both the EdgeInfo.to_dict->json.dumps crash and the latent _ZepNode leak).
  
  Add a tiny helper at module top:
  
      from datetime import datetime
      def _iso(v):
          return v.isoformat() if isinstance(v, datetime) else v
  
  In _ZepNode.__init__:
          self.created_at = _iso(getattr(n, "created_at", None))
  
  In _ZepEdge.__init__:
          self.created_at = _iso(getattr(e, "created_at", None))
          self.valid_at = _iso(getattr(e, "valid_at", None))
          self.invalid_at = _iso(getattr(e, "invalid_at", None))
          self.expired_at = _iso(getattr(e, "expired_at", None))
  
  This restores the documented "ISO string" contract that EdgeInfo/NodeInfo (Optional[str]) and the to_dict()/json.dumps consumers assume, matches what graph_builder.get_graph_data already does manually, and is harmless for the to_text()/f-string paths (ISO strings format fine, and the historical-fact formatting at zep_tools.py:1246-1248 stays consistent). As defense-in-depth, the json.dumps at report_agent.py:1226/1241 could also pass default=str, but the facade-level normalization is the correct primary fix because the docstring designates this wrapper as the Zep-compatible boundary.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. graphiti's EntityEdge types expired_at/valid_at/invalid_at as `datetime | None` and created_at as `datetime` (graphiti-0.29.2/graphiti_core/edges.py:271-277,54, matching the installed .venv copy). runtime.list_edges/get_node_edges return raw EntityEdge objects (graphiti_client/runtime.py:439-448,471-480). _ZepEdge.__init__ (client.py:50-53) and _ZepNode (client.py:34) copy these straight through as datetimes. zep_tools.EdgeInfo declares created_at/valid_at/invalid_at/expired_at as Optional[str] (zep_tools.py:92-95), and get_all_edges(include_temporal=True, default True) copies the datetimes into EdgeInfo unchanged (zep_tools.py:743-746). The module docstring (client.py:7-8) promises call sites "work unchanged" vs the Zep SDK, and graph_builder.get_graph_data (graph_builder.py:486-529) explicitly does str(created_at)/str(valid_at) conversion — proving the developer knew the contract was ISO strings and simply missed the zep_tools path. So the data-contract violation is real and currently true.
  
  Reachable crash exists: get_entity_summary -> get_node_edges -> get_all_edges() (defaults include_temporal=True) -> {"related_edges": [e.to_dict() ...]} (zep_tools.py:889) -> report_agent.py:1226 json.dumps(result, ...) with NO default=str -> raises "TypeError: Object of type datetime is not JSON serializable". I verified line 1226 has no default= argument.
  
  Two corrections to the finding: (1) Its cited evidence line zep_tools.py:1662 is WRONG — that json.dumps serializes agent_summaries, not edges. The actual crash site is report_agent.py:1226 (and zep_tools.py:889). (2) Blast radius is narrower than "any temporal path": the actively-advertised report tools (panorama_search, quick_search, insight_forge) all return result.to_text() (f-strings tolerate datetimes) or count-only stats, so they do NOT crash. The crashing get_entity_summary/get_entities_by_type branches are retained "backward-compatible old tools" (report_agent.py:1209-1241) that are NOT listed in the agent's tool definitions (lines 1061-1112), so the LLM is not normally instructed to call them — the crash path is reachable but dormant. Node to_dict() omits created_at, so the node side does not crash today even though _ZepNode also leaks a datetime. Given a real contract violation plus a genuine-but-rarely-exercised TypeError, P2 is correct (not higher: no commonly-hit live path; not dismissable: the path is real, callable, and the wrapper is the documented Zep facade boundary). The root-cause claim and proposed fix are correct; I tightened the fix to normalize all temporal fields in BOTH wrappers at the facade boundary (the right single chokepoint) rather than patching downstream.
  ```

### report — Report agent + API

#### [F-7-6] Non-atomic writes of progress.json / section_*.md / meta.json can be read mid-write by polling endpoints

`P2` · `robustness` · confidence **medium** · effort **M** · `backend/app/services/report_agent.py` : 2671-2683 (update_progress/get_progress), 2540-2575 (save_section), 2872-2890 (save_report)

- **Symptom.** Frontend polls /progress, /sections, /<report_id> while the generator thread is rewriting the same files with open(path,'w'); a concurrent reader can json.load() a truncated/partial file and 500.
- **Root cause.** All persistence uses direct open(path,'w') + json.dump/write with no temp-file-then-rename atomic replace. Writes are not atomic, and the read endpoints run on Flask request threads concurrently with the generator daemon thread.
- **Evidence.** `with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f: json.dump(progress_data, f, ...)  # direct overwrite, no atomic rename; get_progress does json.load on same path`
- **Impact.** Intermittent 500s / malformed JSON returned to the polling UI during active generation; the very files designed for live progress polling are the ones most exposed to this race.
- **Fix.**

  ```
  Apply atomic writes to all of these files, since they are all polled while the generator thread rewrites them. Add a small helper and route every write through it:
  
  ```python
  import os, json, tempfile
  
  @classmethod
  def _atomic_write_text(cls, path: str, text: str) -> None:
      folder = os.path.dirname(path)
      fd, tmp = tempfile.mkstemp(dir=folder, suffix='.tmp')
      try:
          with os.fdopen(fd, 'w', encoding='utf-8') as f:
              f.write(text)
              f.flush()
              os.fsync(f.fileno())
          os.replace(tmp, path)   # atomic on POSIX and Windows
      except BaseException:
          try:
              os.unlink(tmp)
          except OSError:
              pass
          raise
  ```
  
  Then replace each `with open(path, 'w') ...` block:
  - update_progress (2671): `cls._atomic_write_text(cls._get_progress_path(report_id), json.dumps(progress_data, ensure_ascii=False, indent=2))`
  - save_report meta.json (2878) and full_report.md (2887)
  - save_outline outline.json (2535)
  - save_section section_NN.md (2571) and assemble_full_report full_report.md (2740)
  
  Creating the temp file in the same directory (dir=folder) is required so os.replace stays on the same filesystem (atomic). This guarantees readers always see a complete previous-or-next version of each file. The existing try/except in the endpoints is fine to keep as defense-in-depth, but should no longer be relied on as the primary mitigation.
  
  A lighter complementary measure (not a substitute) would be to also catch json.JSONDecodeError specifically in get_progress/get_report and retry once, but os.replace eliminates the root cause and is the correct fix.
  ```
- **Verified.**

  ```
  Verified against the actual code. The race is real and currently true.
  
  Writers (all in backend/app/services/report_agent.py) use non-atomic, truncate-in-place writes with no temp-file/rename: update_progress writes progress.json via `open(..., 'w')` + json.dump (line 2671-2672); save_section writes section_NN.md via `open(..., 'w')` + write (2571-2572); save_report writes meta.json via `open(..., 'w')` + json.dump (2878-2879) and full_report.md (2887-2888); save_outline writes outline.json (2535-2536); assemble_full_report writes full_report.md (2740-2741). A repo-wide grep confirms NO os.replace / tempfile / atomic-rename anywhere in the file.
  
  Concurrency is genuine, not hypothetical: report generation runs on a background daemon thread (backend/app/api/report.py:180, `threading.Thread(target=run_generate, daemon=True)`), and inside generate_report the worker calls update_progress / save_section / save_report repeatedly (report_agent.py:2013-2199). Meanwhile Flask serves GET /<id>/progress -> get_progress -> json.load (report_agent.py:2682-2683), GET /<id>/sections -> get_generated_sections -> file read (2701-2702) + get_report, and GET /<id> -> get_report -> json.load (2905-2906) on separate request threads (report.py:594, 642-645, 298). `open(path,'w')` truncates the target to zero on open, so a reader interleaving in that window can json.load an empty/partial file and raise JSONDecodeError. There is no lock coordinating writers and readers.
  
  Severity stays P2 (not higher) because each read endpoint wraps the call in try/except Exception returning HTTP 500 (report.py:607-613, 658-664, 297+). So the failure mode is a transient, self-healing 500 on an occasional poll cycle (the write completes in microseconds and the next poll succeeds); it does not crash the server, corrupt persisted data, or block generation. The vulnerable window is tiny relative to poll intervals, but progress.json is rewritten very frequently during a long generation, so an intermittent 500 to the polling UI is plausible. This matches the claimed impact exactly and is a legitimate robustness defect, just low-blast-radius.
  ```

#### [F-7-4] download_report writes a NamedTemporaryFile with delete=False and never cleans it up — disk leak

`P2` · `robustness` · confidence **high** · effort **S** · `backend/app/api/report.py` : 417-428 (download_report)

- **Symptom.** Every download of a report whose full_report.md is missing leaves a permanent /tmp/*.md file behind.
- **Root cause.** tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) is created and passed to send_file, but nothing ever removes temp_path (no after_request cleanup, no try/finally).
- **Evidence.** `with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f: f.write(report.markdown_content); temp_path = f.name ... return send_file(temp_path, ...)  # temp_path never deleted`
- **Impact.** Unbounded temp-file accumulation in the system temp dir over time; on long-running servers this can exhaust inodes/disk. Also reachable repeatedly via the public download endpoint.
- **Fix.**

  ```
  Eliminate the temp file on the fallback path and stream markdown_content directly. Replace lines 419-428 with a Flask Response that carries the content and a Content-Disposition header, e.g.:
  
  from flask import Response
  return Response(
      report.markdown_content or "",
      mimetype="text/markdown",
      headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
  )
  
  This writes nothing to disk, so there is nothing to clean up. (If a temp file must be retained for some reason, instead register `@after_this_request` to `os.remove(temp_path)`, guarded against the file already being gone.) Keep the existing send_file(md_path, ...) branch for when the persisted file exists.
  ```
- **Verified.** Confirmed by reading backend/app/api/report.py:417-428. On the fallback path (when the persisted full_report.md at md_path does not exist but the report record does), the code does `with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f: f.write(report.markdown_content); temp_path = f.name` and then `return send_file(temp_path, as_attachment=True, ...)`. The temp file is created with delete=False and temp_path is never removed: there is no os.remove/os.unlink on temp_path, no try/finally, and no @after_this_request callback. I confirmed the only app-wide after_request handler (app/__init__.py:70-74) merely logs the response status and does not clean up files. grep across app/ shows the only tempfile usage is this block and no cleanup of it. The endpoint is registered at /api/report/<report_id>/download (app/__init__.py:80) with no authentication, so it is publicly and repeatedly reachable. Each download on this fallback path leaks one /tmp/*.md file permanently, accumulating unbounded over a long-running server. Severity P2 is correct: a slow disk/inode leak limited to the fallback branch (not a crash or correctness bug). The proposed fix is valid; the cleanest variant avoids the filesystem entirely.

#### [F-7-3] chat() re-scans every report folder (reading full markdown) on every user message — O(N) N+1 with large payloads

`P2` · `bottleneck` · confidence **high** · effort **M** · `backend/app/services/report_agent.py` : 2241 (chat), 2947-2965 (get_report_by_simulation), 2892-2944 (get_report)

- **Symptom.** Each /report/chat request iterates all report folders and json.load()s each meta.json (which embeds the full markdown_content) just to find one report by simulation_id.
- **Root cause.** get_report_by_simulation calls get_report() for EVERY folder until a match; get_report deserializes the entire meta.json including the full report markdown. There is no index from simulation_id to report_id and no early size limit.
- **Evidence.** `report = ReportManager.get_report_by_simulation(self.simulation_id)  # called inside chat() per message; get_report loads full meta.json with markdown for every folder`
- **Impact.** Chat latency and memory grow linearly with the number of stored reports (made worse by the orphaned-folder accumulation from force_regenerate). On a busy instance this is a steady, avoidable cost on the interactive chat path.
- **Fix.** Two complementary fixes, both correct given the on-disk layout. (1) Avoid loading markdown during the lookup: add a lightweight resolver that reads only the simulation_id from each meta.json without rebuilding the Report or pulling markdown. Because markdown is embedded in meta.json, a plain json.load still reads the whole file; to truly avoid the cost, EITHER (a) maintain a small index file (e.g. reports/_sim_index.json mapping simulation_id -> latest report_id, updated in save_report/delete_report) and resolve via the index with an O(1) lookup plus a single get_report of the matched id, OR (b) stop embedding markdown_content in meta.json entirely (drop it from to_dict / write only a reference) since full_report.md already persists it and get_report already falls back to reading full_report.md when markdown_content is empty (2926-2931) — this shrinks every meta.json and makes the scan cheap. Option (b) is the cleanest structural fix. (2) Cache the resolved report on the ReportAgent instance: resolve once in __init__ (or memoize on first chat) and reuse across messages, since simulation_id is fixed for the agent's lifetime. Note the API currently rebuilds the agent per request (report.py:547), so instance caching alone is insufficient — pair it with the index (a) or the meta-slimming (b) for it to help. Recommended: implement (1b) plus the simulation_id index (1a) so get_report_by_simulation is O(1)+one read, and add instance-level memoization so multi-iteration chat does not re-resolve.
- **Verified.**

  ```
  Confirmed from the code. Per chat request the API (backend/app/api/report.py:547-557) constructs a fresh ReportAgent and calls agent.chat(), which at report_agent.py:2241 calls ReportManager.get_report_by_simulation(self.simulation_id). That method (2947-2965) does os.listdir(REPORTS_DIR) and calls get_report() on EVERY folder/legacy JSON until a simulation_id match — an O(N) linear scan with no index and no early limit. get_report() (2892-2944) json.load()s the entire meta.json. meta.json is written by save_report() (2873-2890) from report.to_dict(), and to_dict() (458-470) embeds the FULL markdown_content (line 466). So every folder's complete report markdown is deserialized just to read one simulation_id field — exactly the N+1 / full-payload pattern claimed. No caching of the resolved report on the agent instance exists, so each chat message repeats the full scan. The root-cause claim and quoted evidence are accurate, not a misreading; no guard/try-except mitigates the cost (the try/except at 2247 only swallows errors, it does not avoid the scan).
  
  Severity is correctly P2, not higher: this is local filesystem I/O (not a remote DB), N is the number of stored reports which is typically modest for this single-tenant simulation tool, and the chat path issues multiple LLM completions per message (2274, 2314) whose latency dwarfs the folder scan. It is a real, avoidable, steady cost on the interactive path that grows linearly with stored reports (worsened by orphaned-folder accumulation), but it is an efficiency issue, not a correctness or security defect. Default-to-reject does not apply because the defect is unambiguously confirmed in the code.
  ```

#### [F-7-7] interview_agents is exposed as a native function tool with no per-section call cap, risking IPC-timeout stacking under native tool calling

`P2` · `bottleneck` · confidence **medium** · effort **M** · `backend/app/services/report_agent.py` : 1469-1487 (_to_openai_tool_schemas), 1547-1574 (native batch execution), 1172-1188 (interview cost note)

- **Symptom.** In the native path the model can request interview_agents multiple times (up to MAX_TOOL_CALLS_PER_SECTION=8, and several in a single assistant turn executed back-to-back), each a ~14-600s dual-platform claude-cli interview run.
- **Root cause.** _to_openai_tool_schemas exposes ALL VALID_TOOL_NAMES including interview_agents; the native loop executes every returned tool_call serially in the for-loop (line 1558) with the limit only checked at loop entry (line 1547), so a turn can overshoot the cap, and there is no special throttle for the expensive interview tool that the code itself flags as ~600s budget.
- **Evidence.** `for c in calls: tool_calls_count += 1; result = self._execute_tool(c["name"], c["arguments"], ...)  # all calls in a turn run; limit checked only before the loop`
- **Impact.** A single section can serialize many minutes of interview subprocess calls, stalling report generation and risking the 600s IPC timeout the code explicitly warns about (lines 1178-1180). Cost/latency is unbounded relative to the ReAct path which executes only the first tool call per turn.
- **Fix.**

  ```
  Two-part fix in _generate_section_native:
  
  1. Enforce the cap inside the per-call loop so a turn cannot overshoot. Replace the unconditional `for c in calls:` body with a break when the cap is hit:
  
     for c in calls:
         if tool_calls_count >= max_tool_calls:
             break
         tool_calls_count += 1
         ... execute ...
  
     (Also append a tool result for any skipped calls or trim the assistant tool_calls message to only the executed subset, so the OpenAI message contract stays valid — every tool_call id in the assistant message needs a matching tool message. Simplest correct approach: pre-slice `calls = calls[: max_tool_calls - tool_calls_count]` BEFORE building the assistant message, so the back-filled tool_calls and executed tool results stay in sync.)
  
  2. Add a per-section throttle for interview_agents specifically (the only tool the code flags as ~600s). Track an interview counter and skip/short-circuit additional interview calls beyond 1-2 per section, returning a stub tool result like "(本节深度采访已达上限，请基于已有采访结果撰写)" so the model continues without another multi-minute subprocess. This mirrors the ReAct path's de-facto throttle (one tool per turn) and bounds worst-case section latency.
  
  Precise framing: the risk is unbounded serial latency (multiple multi-minute interviews stacked in one section), not a single call breaching 600s — keep that distinction in the fix rationale.
  ```
- **Verified.**

  ```
  Verified against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/report_agent.py.
  
  Confirmed claims:
  1. _to_openai_tool_schemas (lines 1469-1487) iterates sorted(self.VALID_TOOL_NAMES) which includes "interview_agents" (line 1251), so the expensive interview tool IS exposed as a native function tool with no special gating.
  2. Native batch execution (lines 1547-1574): the cap is checked once at if-block entry (line 1547: `if calls and tool_calls_count < max_tool_calls`). Inside, `for c in calls:` (line 1558) executes EVERY tool_call in the assistant turn, incrementing tool_calls_count but never re-checking the cap mid-loop. So a single turn returning N calls runs all N even after the cap (8) is exceeded — real overshoot.
  3. interview_agents (lines 1172-1188) is genuinely expensive: each receiver is a dual-platform claude-cli interview, max_agents clamped to 6, and the code itself warns (lines 1178-1180) about the 600s IPC timeout.
  4. The ReAct path is materially safer: it checks the cap before executing (line 1807) AND executes only the first tool call per turn (lines 1818-1821: `call = tool_calls[0]`, "只执行第一个"). The native path lacks both protections, confirming the asymmetry the finding describes.
  
  One precision note: max_agents is clamped to 6 so a SINGLE interview call stays within its own 600s budget; the IPC timeout is per-call, so back-to-back calls don't each breach 600s as the wording loosely implies. But the substantive defect — unbounded serial stacking of multi-minute interview calls within a section, plus the per-turn cap overshoot — is real and confirmable. P2 (latency/bottleneck, not correctness or crash) is correct.
  ```

#### [F-7-5] Dead None-handling in ReAct loop masks the real failure contract: LLMClient.chat() never returns None, it raises

`P2` · `correctness` · confidence **high** · effort **M** · `backend/app/services/report_agent.py` : 1691-1700, 1928-1931 (_generate_section_react) vs backend/app/utils/llm_client.py:93-109,338-344,417-419

- **Symptom.** The section loop has extensive 'if response is None' branches (retry/continue, default error string) that can never execute; the actual transient-failure path (RuntimeError after 3 retries, or OpenAI APIError) propagates as an exception.
- **Root cause.** LLMClient.chat() either returns a non-empty string or raises (it raises RuntimeError on empty/timeout/CLI-error content at llm_client.py:340/398/418 and re-raises last_error at :109). It is typed -> str and has no return-None path. The report_agent code was written against an assumption that chat() returns None on failure.
- **Evidence.** `report_agent: 'if response is None: ... messages.append({..."（响应为空）"}); continue'  vs llm_client.chat -> 'raise last_error if last_error is not None else RuntimeError(...)' (returns str or raises, never None)`
- **Impact.** The None-handling is dead code that gives false confidence; real failures surface as exceptions caught only by the per-section try/except in generate_report (line 2095), which converts the WHOLE section to a placeholder rather than the loop's intended single-iteration retry. The intended graceful retry-on-empty within a section never happens.
- **Fix.** Prefer option (b): remove the two unreachable `if response is None:` blocks (report_agent.py:1691-1700 and 1928-1931) and add a brief comment that LLMClient.chat() returns a non-empty str or raises, and that transient failures are already retried 3x inside the client and otherwise handled by the per-section try/except in generate_report (line ~2081) which degrades the section to SECTION_FAILURE_PLACEHOLDER. Do NOT implement option (a) (wrapping each in-loop chat() in try/except RuntimeError): it would duplicate the client's existing exponential-backoff retry and risk masking the deliberate per-section placeholder fallback, adding complexity for negligible benefit. After removal, line 1932's `elif "Final Answer:" in response` should become a plain `if`, and 1929-1931's default-error branch dropped, since `response` is guaranteed non-None.
- **Verified.**

  ```
  Verified directly in code. LLMClient.chat() (backend/app/utils/llm_client.py:79-109) has no return-None path: each provider path returns a non-empty str (_chat_openai/_chat_codex_cli/_chat_claude_cli) or raises RuntimeError; after exhausting MAX_RETRIES it executes `raise last_error if last_error is not None else RuntimeError(...)` (line 109). The two `return None` statements in the file (lines 224, 261) belong to the static helper _parse_json_response, NOT chat(). self.llm is always a concrete LLMClient (report_agent.py:953,993) with no subclass overriding chat() anywhere in the repo, so the typed `-> str`-or-raise contract holds.
  
  Therefore the two `if response is None:` blocks in _generate_section_react are dead code: lines 1691-1700 (in-loop retry/continue) and lines 1928-1931 (forced-finalization tail producing the default error string). Neither can ever execute.
  
  The actual transient-failure path is exactly as claimed: a RuntimeError/APIError propagates out of the section loop and is caught only by the per-section `try/except Exception` at generate_report (report_agent.py:2081-2105), which converts the WHOLE section to SECTION_FAILURE_PLACEHOLDER and appends to failed_section_titles. The loop's intended single-iteration retry-on-empty never fires.
  
  Impact is real but bounded, consistent with P2 (maintainability/correctness-of-contract, not a user-facing break): (1) it is dead code that gives false confidence and misrepresents the failure contract to future maintainers; (2) functional harm is small because LLMClient.chat() ALREADY retries 3x internally on RuntimeError (CLI errors, timeouts, empty/reasoning-exhausted content) before propagating — so the dead None-handling was a redundant second retry layer; failures that reach the outer handler are genuinely persistent, and whole-section placeholder degradation is acceptable. No crash, no data corruption. P2 is appropriate (not P1).
  ```

### graph-build — Graph builder + ontology

#### [F-3-1] set_ontology raises KeyError on entity/attr/edge entries missing "name", aborting the entire graph build

`P2` · `robustness` · confidence **high** · effort **S** · `backend/app/services/graph_builder.py` : 235-292

- **Symptom.** If the ontology contains any entity_def, attr_def, or edge_def lacking a "name" key (LLM omission, or a partially-validated ontology loaded from a project), `entity_def["name"]` / `attr_def["name"]` / `edge_def["name"]` raise KeyError. There is no per-item try/except, so one bad entry aborts set_ontology and the whole GRAPH stage fails.
- **Root cause.** Direct subscript access on dicts that originate from an LLM (untrusted boundary) without `.get()` defaults or per-item guarding. _validate_and_process never enforces that every entity/attribute/edge has a non-empty "name", so a missing key reaches set_ontology.
- **Evidence.** `Line 236 `name = entity_def["name"]`, line 244 `attr_name = safe_attr_name(attr_def["name"])`, line 260 `name = edge_def["name"]`, line 268 same — all bare subscripts with no surrounding guard.`
- **Impact.** Graph construction fails hard (no entities/edges, pipeline stops at GRAPH) on a malformed ontology that could otherwise have been built minus the bad entry.
- **Fix.**

  ```
  Two-layer fix.
  
  1) graph_builder.py set_ontology — skip nameless items instead of crashing, and isolate each dynamic class so one bad type cannot abort the batch (mirror seed_actors' per-item try/except):
  
    for entity_def in ontology.get("entity_types", []):
        name = entity_def.get("name")
        if not name:
            logger.warning("skipping entity_def without name: %r", entity_def); continue
        try:
            ... build attrs/annotations using attr_def.get("name") (skip falsy attr names) ...
            entity_types[name] = type(name, (EntityModel,), attrs)
        except Exception as e:
            logger.warning("skipping malformed entity type %s: %s", name, e); continue
  
    Same for edge_types: `name = edge_def.get("name")` with `if not name: continue`, and wrap the type()/source_targets construction in try/except. Guard attr names with `attr_name = attr_def.get("name")` / `if not attr_name: continue` (and apply safe_attr_name only when truthy).
  
  2) Harden _validate_and_process in ontology_generator.py to drop nameless entries up front so the persisted-ontology resume path is also safe, and so line 313's set-comprehension can't KeyError:
     result["entity_types"] = [e for e in result["entity_types"] if e.get("name")]
     result["edge_types"]   = [e for e in result["edge_types"] if e.get("name")]
     (and filter attributes by truthy "name" within each).
  
  Also consider wrapping the set_ontology call at pipeline_orchestrator.py line 1504 in try/except with a logger.warning, consistent with the adjacent seed_actors guard, so a malformed ontology degrades gracefully rather than aborting the GRAPH stage.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. In backend/app/services/graph_builder.py, set_ontology() uses bare dict subscripts on LLM-originated data: line 236 `name = entity_def["name"]`, line 244 `attr_name = safe_attr_name(attr_def["name"])`, line 260 `name = edge_def["name"]`, line 268 same for edge attrs. Any entity/edge/attr entry missing "name" raises KeyError with no per-item guard.
  
  The validation referenced (_validate_and_process) lives in ontology_generator.py (line 257-345), not graph_builder.py. It only injects defaults for "attributes"/"examples"/"source_targets"/"description" and clamps counts — it never enforces a non-empty "name" on entities, edges, or attributes. So a missing "name" reaches set_ontology unguarded.
  
  Caller confirms the hard-fail impact: pipeline_orchestrator.py line 1504 `builder.set_ontology(graph_id, project.ontology)` is NOT wrapped in try/except, while the very next call seed_actors (lines 1512-1517) IS wrapped. A KeyError from set_ontology propagates to the top-level _run try (line 1387), aborting the entire GRAPH stage. The defensive-pattern asymmetry the finding cites is real.
  
  Reachability is genuine: the resume/reuse path (lines 1434-1437) skips ONTOLOGY entirely and feeds a persisted project.ontology straight into set_ontology without re-running _validate_and_process — exactly the "partially-validated ontology loaded from a project" scenario. Also, a nameless edge or attribute (vs entity) survives _validate_and_process since line 313 only dereferences entity["name"].
  
  Severity adjustment: I lower P1→P2. In the fresh-generation path a nameless ENTITY would actually KeyError earlier inside _validate_and_process at line 313 ({e["name"] for e in ...}), so the graph-stage crash specifically requires either a corrupted/partially-validated persisted ontology or an LLM omitting "name" on an edge/attribute. That is an LLM-failure/data-corruption path, not a routine one — a real robustness gap but not an everyday crash.
  ```

#### [F-3-2] set_ontology mutates the process-global warnings filter on every call (permanent side effect)

`P2` · `robustness` · confidence **high** · effort **S** · `backend/app/services/graph_builder.py` : 215-222

- **Symptom.** Each set_ontology call appends `warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')` to the global warnings registry. This permanently and cumulatively silences ALL pydantic UserWarnings process-wide (and across every subsequent graph build / pipeline run in the same process), not just for the dynamic-class creation block.
- **Root cause.** The filter is installed globally and never scoped or removed. It runs inside the method body, so every build adds another identical filter entry and leaves it in place forever.
- **Evidence.** `Lines 215-222: `import warnings` then `warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')` executed unconditionally in the method body, never reverted.`
- **Impact.** Real pydantic v2 validation/usage warnings elsewhere in the long-lived Flask process (e.g. other services constructing models) are silently suppressed, masking genuine misuse. The registry also grows by one entry per call (minor leak).
- **Fix.**

  ```
  Scope the suppression with a context manager so the prior filter state is restored on exit and nothing accumulates globally. The warning is emitted during the dynamic `type(...)` class creation (not the Zep API call), so wrap the entity/edge build loops. In `set_ontology` (graph_builder.py), remove the bare `warnings.filterwarnings(...)` at line 222 and instead wrap the construction:
  
  ```python
  import warnings
  ...
  with warnings.catch_warnings():
      warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')
      # build entity_types and edge_definitions here (the type(...) loops)
      ...
  # call self.client.graph.set_ontology(...) outside or inside the block — fine either way
  ```
  
  `catch_warnings()` snapshots and restores `warnings.filters` on exit, eliminating both the permanent global suppression and the per-call registry growth. Keep `entity_types`/`edge_definitions` populated inside the block (where the UserWarning actually fires); the subsequent `self.client.graph.set_ontology(...)` API call does not need to be inside the suppression scope.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. backend/app/services/graph_builder.py:222 calls `warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')` unconditionally inside the `set_ontology` method body (lines 213-300), with no `catch_warnings()` scope and no removal. This is a permanent, process-global mutation of `warnings.filters`.
  
  Impact is real and currently true: (1) The app is a long-lived Flask process (app factory in backend/app/__init__.py, `create_app`). (2) `set_ontology` is invoked from multiple live paths within the same process — the API route backend/app/api/graph.py:418, the pipeline at backend/app/services/pipeline_orchestrator.py:1504, and internally at graph_builder.py:138 — so it runs repeatedly. (3) Each invocation prepends another identical entry to the global `warnings.filters` list (cumulative growth, minor leak). (4) Once installed, the filter silences ALL pydantic UserWarnings process-wide for the remainder of the process lifetime, not just during the dynamic-class-creation block, so genuine pydantic v2 misuse warnings emitted elsewhere are masked.
  
  The warning source is genuine, not dead code: the dynamic `type(name, (EntityModel,), attrs)` construction (lines 253, 278) applies `Field(description=..., default=None)` with `Optional[...]` annotations, which is exactly the pattern the code comment (lines 220-221) documents as the reason for suppression. So the intent (suppress noise from dynamic class creation) is legitimate; only the mechanism (global, never reverted) is the defect.
  
  The pre-existing global filter in __init__.py:10 targets a different message (`.*resource_tracker.*`) and does not cover or make this redundant. No nearby try/except, guard, or config reverts the filter. Not a misreading, not dead code, not intended global behavior.
  
  Severity P2 (robustness) is correct: no functional/correctness regression in the graph-build feature itself, but it degrades observability process-wide and is a slow registry leak.
  ```

#### [F-3-3] seed_actors IS_A path writes a literal type-name node ("Person"/"Organization") as a graph entity

`P2` · `data-contract` · confidence **high** · effort **M** · `backend/app/services/graph_builder.py` : 339-352

- **Symptom.** For an isolated high-signal actor with no relationship edge, seed_actors writes `add_triplet(graph_id, name, "IS_A", a.get("type", "Entity"), ...)` with the actor's TYPE STRING as the target node NAME and the default `target_label="Entity"`. This creates persistent graph nodes literally named "Person", "Organization", "Media", etc., one per distinct type, that are not real actors.
- **Root cause.** The type label is being used as a node identity (target_name) instead of being attached as a label on the actor node. add_triplet always materializes both endpoints as EntityNodes (runtime.py _add_triplet creates `tgt = EntityNode(name=target_name, ...)`), so the type string becomes a node.
- **Evidence.** `Lines 344-349: `self.client.graph.add_triplet(graph_id, name, "IS_A", a.get("type", "Entity"), a.get("role") or name, valid_at=valid_at, source_label=label.get(name, "Entity"))` — target_name is the type string; runtime.py:353 `tgt = EntityNode(name=target_name, ...)` materializes it.`
- **Impact.** Pollutes the graph with bogus 'Person'/'Organization' nodes that inflate _get_graph_info node_count and get_graph_data, can be returned by panorama/edge searches, and create misleading IS_A facts (e.g. 'Alice IS_A Person' as a graph edge). The bogus nodes carry only the 'Entity' label so filter_defined_entities drops them from persona generation, but they remain in raw graph data and counts.
- **Fix.**

  ```
  Do not model the actor type as a graph node. The intent is only to ensure an isolated actor exists as a properly-labeled node; an IS_A edge to a type-named node is the wrong mechanism.
  
  Primary fix (needs a small runtime addition): add a node-only upsert to backend/app/services/graphiti_client/runtime.py (e.g. add_node(graph_id, name, label) that builds one EntityNode with labels=_labels(label) and persists it via graphiti_core's node save path, with name+embedding dedup so later text extraction enriches rather than duplicates). Then in graph_builder.py:343-352 replace the IS_A add_triplet with:
      self.client.graph.add_node(graph_id, name, label.get(name, "Entity"))
  This seeds the isolated actor as a correctly-typed standalone node, so filter_defined_entities keeps it (instead of dropping it as today) and no bogus type-node or spurious IS_A edge is created.
  
  If adding a runtime method is out of scope, the interim mitigation is to at minimum pass target_label so the synthetic node is at least a legitimately-typed concept rather than an "Entity"-only orphan:
      self.client.graph.add_triplet(graph_id, name, "IS_A", a.get("type") or "Entity", a.get("role") or name, valid_at=valid_at, source_label=label.get(name, "Entity"), target_label=ACTOR_TYPE_TO_LABEL.get(str(a.get("type") or ""), "Entity"))
  But note the interim still pollutes node_count/get_graph_data with type nodes and IS_A edges — it only makes them typed. Prefer the node-only upsert.
  
  Separately, consider whether seeding isolated actors is even necessary: since add_text_batches re-extracts entities from the same report prose with name+embedding dedup, a high-signal isolated actor is very likely created by text extraction anyway, in which case the safest fix is simply to drop the IS_A branch entirely (lines 339-352) and let text extraction create those nodes — eliminating the pollution with zero new API surface.
  ```
- **Verified.**

  ```
  CONFIRMED as a real, currently-true defect, reachable in production.
  
  Verified evidence:
  - backend/app/services/graph_builder.py:344-349 (isolated-actor branch): passes a.get("type", "Entity") as the add_triplet target_name and does NOT pass target_label (so it defaults to "Entity"). The actor TYPE STRING ("Person"/"Organization"/"Media"/"Government"/"Platform" per the actors.json schema in utils/actors.py:13) is used as a node identity.
  - backend/app/services/graphiti_client/runtime.py:335-367 (_add_triplet): materializes BOTH endpoints — `tgt = EntityNode(name=target_name, labels=_labels(target_label), ...)` (line 352-353) — and calls graphiti_core `g.add_triplet(src, edge, tgt)` (line 367), persisting the type-named node. `_labels("Entity")` (line 345-346) returns just ["Entity"], so the bogus node carries only the default label.
  - This creates one persistent node per distinct type literally named "Person", "Organization", etc., plus a misleading "<Actor> IS_A Person" edge.
  
  Impact verified:
  - _get_graph_info (graph_builder.py:460) and get_graph_data (graph_builder.py:538) count ALL nodes (len(nodes)) with NO label filtering, so the bogus nodes inflate node_count and appear in raw graph data returned to the frontend.
  - The finding's own nuance is also correct: filter_defined_entities (zep_entity_reader.py:256-260) drops nodes whose only label is "Entity", so the bogus type-nodes are excluded from OASIS/persona generation — they do NOT corrupt persona output. The actor node itself is correctly labeled via source_label=label.get(name) (Person/Organization), so only the synthetic type-endpoint is polluting.
  
  Reachability: seed_actors is invoked live at pipeline_orchestrator.py:1513, gated by Config.GRAPH_SEED_FROM_ACTORS and presence of actors. The isolated-actor branch fires for any high-signal actor without a relationship edge — a common case. No cleanup/pruning of these nodes exists anywhere.
  
  Severity P2 is correct: data-contract/quality degradation, not a functional break. The bogus nodes inflate node counts, appear in get_graph_data (frontend), and can surface in panorama/edge searches consumed by the report agent as spurious "X IS_A Person" facts — but the persona-generation path is shielded by the Entity-label filter. Not P1 (no crash, no persona corruption); not P3 (it does pollute user-visible graph data and the report agent's search surface).
  
  Not a misreading, not dead code, not guarded away, not intended behavior (the design comment at line 311 explicitly intends to seed the ACTOR node, not a type node — the type-as-node materialization is an unintended side effect of add_triplet always creating both endpoints).
  ```

#### [F-3-4] Ontology fallback-insertion truncates from the tail and can silently drop legitimate specific entity types

`P2` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/ontology_generator.py` : 324-340

- **Symptom.** When the LLM already returns 10 entity types but omits Person/Organization fallbacks, the code removes types from the END (`result["entity_types"][:-to_remove]`) to make room. If the LLM placed important specific types last (the prompt explicitly tells it to put fallbacks last, so a model that forgot the fallback but kept that ordering loses its most-specific trailing types), those types are dropped before the ontology is set on the graph.
- **Root cause.** Tail-truncation assumes the least-important types are at the end, but that ordering is only guaranteed if the model followed the 'fallbacks last' instruction — exactly the case where it didn't (fallbacks are missing). The two concerns (which types to drop vs. which are missing) are conflated.
- **Evidence.** `Lines 331-333: `to_remove = current_count + needed_slots - MAX_ENTITY_TYPES` then `result["entity_types"] = result["entity_types"][:-to_remove]` with no logging; line 339-340 final hard cap `[:MAX_ENTITY_TYPES]` also silent.`
- **Impact.** Entity types present in research are silently removed from the ontology, so the graph never extracts those entity classes — degraded graph fidelity that flows into persona generation and the report, with no log line indicating the drop.
- **Fix.** In `_validate_and_process`, before counting/truncating: (1) dedupe entity_types by `name` (keep first occurrence) and drop entries with empty/missing names so duplicates can't consume budget; (2) when over MAX_ENTITY_TYPES after adding fallbacks, compute the removed slice explicitly and emit `logger.warning("Ontology truncated: dropping entity types %s to fit MAX_ENTITY_TYPES=%d for fallbacks %s", [t['name'] for t in removed], MAX_ENTITY_TYPES, [f['name'] for f in fallbacks_to_add])`; (3) likewise log when the final hard cap at line 339-340 (and the edge-type cap at 342-343) drops anything, listing the removed names. Guard the arithmetic (`to_remove = max(0, ...)`) so a malformed huge `current_count` can't produce a bad slice. Add a module logger (`logger = logging.getLogger(__name__)`), since the file currently has none. This keeps the existing "specific types first" heuristic but makes every drop deterministic and auditable rather than silent.
- **Verified.**

  ```
  Confirmed by reading backend/app/services/ontology_generator.py:324-343 (`_validate_and_process`). When Person/Organization fallbacks are missing and adding them would exceed MAX_ENTITY_TYPES (10), the code removes from the tail via `result["entity_types"] = result["entity_types"][:-to_remove]` (line 333) and applies a final silent hard cap `[:MAX_ENTITY_TYPES]` (line 340). The file has zero logging (grep for logger/logging/warning returns nothing), so any dropped type is silent — confirming the observability claim.
  
  The root-cause reasoning is sound: the "fallbacks last" ordering is only enforced by the prompt (lines 249-250: "最后2个必须是兜底类型"), and fallback insertion runs precisely BECAUSE the model deviated from that prompt (it omitted Person/Organization). So in the exact situation that triggers tail-truncation, the assumption "trailing types are least important" is least trustworthy — the trailing entries may be specific, research-derived types. The two concerns (which types to drop vs. which are missing) are conflated.
  
  Secondary bug confirmed: line 313 builds `entity_names` as a set only for has_person/has_organization membership checks; the `entity_types` LIST is never deduplicated. A duplicate specific type therefore counts toward the 10-type budget and can push out a unique trailing type. The downstream impact is plausible: this ontology is returned to callers and (per generate_python_code at line 372+) drives graph entity extraction, so a dropped class degrades graph fidelity that feeds personas/report.
  
  Caveats lowering urgency to P2: this only fires on off-spec LLM output (model returns >8 types AND omits a fallback) — with the explicit "exactly 10, last 2 = fallbacks" instruction at temperature 0.3, the compliant path produces no fallback insertion at all. It is not a crash, data-corruption, or security issue; it is silent fidelity loss on a low-frequency path. P2 is appropriate.
  ```

### personas — Persona + simulation-config generation

#### [F-5-1] Realtime save filters out None profiles, shifting array positions and breaking positional agent_id contract

`P2` · `data-contract` · confidence **high** · effort **M** · `backend/app/services/oasis_profile_generator.py` : 936-957, 1208-1212

- **Symptom.** The realtime-written reddit_profiles.json can have fewer rows than there are entities, so array position no longer equals the entity index / config agent_id.
- **Root cause.** save_profiles_realtime() does `existing_profiles = [p for p in profiles if p is not None]` then writes them in compacted order. OASIS assigns agent_id strictly by JSON array position (`for i in range(len(agent_info))`, agents_generator.py:609). Meanwhile the config's agent_id = original entity enumeration index (simulation_config_generator.py:1190) and poster_agent_id/initial_follows reference those indices. While generation is in progress, completed-out-of-order slots leave Nones; the compacted realtime file maps position->wrong entity. The final _save_reddit_json() enumerates the full order-preserving list so the happy-path final file is correct, but the realtime artifact is silently inconsistent.
- **Evidence.** ``existing_profiles = [p for p in profiles if p is not None]` (line 938); OASIS `agent_id=i` for `i in range(len(agent_info))` (agents_generator.py:608-609)`
- **Impact.** If a run is interrupted and resumed from the realtime file, or any tooling reads it, poster_agent_id / initial_follows / scheduled_events target the wrong personas.
- **Fix.** Do not write a compacted/reordered realtime file, since OASIS ignores user_id and binds agent_id to array position. In save_profiles_realtime() (oasis_profile_generator.py:931-959), either (a) emit a full-length list preserving index by writing placeholder rows for not-yet-completed slots (e.g., a minimal stub OasisAgentProfile per None entry so position N always equals entity index N), or (b) only write the contiguous completed prefix (stop at the first None) so positions are always valid. Additionally, explicitly document the realtime file as preview-only and ensure no consumer (preview endpoints in api/simulation.py:313, :1069; zep_tools.py:1580) treats positional order as the agent_id contract before status=="ready". Note that the finding's "stamp each row with user_id and key by user_id" suggestion is insufficient by itself for the OASIS consumer, because generate_reddit_agent_graph keys by array position and ignores user_id; the placeholder-preserving or prefix-only write is the correct primary fix.
- **Verified.**

  ```
  The technical mechanism is accurately confirmed in the code. OASIS's generate_reddit_agent_graph (backend/.venv/.../oasis/social_agent/agents_generator.py:574-609) loads reddit_profiles.json via json.load() and assigns agent_id=i strictly by array position (process_agent(i) for i in range(len(agent_info)), agent_id=i at line 599) — the per-row user_id field is completely ignored. The config side (simulation_config_generator.py:1189-1202) keys agent_id on the original entity enumeration index, and poster_agent_id/initial_follows reference those indices. The realtime save (oasis_profile_generator.py:938) does existing_profiles = [p for p in profiles if p is not None] and writes them compacted (lines 945-947). Because profiles are generated out-of-order in a ThreadPoolExecutor (lines 1003-1024) into a pre-allocated [None]*total list, an in-flight realtime write produces a compacted file where array position no longer equals entity/agent_id index. So the realtime artifact is genuinely positionally inconsistent. The root-cause claim is correct.
  
  However, the most severe claimed impacts do NOT currently occur, which lowers severity from P1:
  1) Happy-path overwrite: simulation_manager.py:397-402 calls save_profiles -> _save_reddit_json (line 1112), which enumerates the FULL order-preserving profiles list (line 1209) and writes to the SAME reddit_profiles.json path AFTER generation completes and BEFORE config generation. OASIS only reads the file at run time, which requires status=="ready" (set after the final overwrite per api/simulation.py:310-331). So the simulator never consumes the inconsistent compacted file in normal operation.
  2) No resume-from-realtime logic exists. I searched simulation_manager.py and found no code that loads a partial realtime file and continues; interruption re-runs the full prepare pipeline. The finding's "resumed from the realtime file" impact path is not implemented, so that scenario is hypothetical.
  3) The "any tooling reads it" path is real but bounded: preview endpoints (api/simulation.py:313-320 and :1055-1086) read reddit_profiles.json and can surface it to the frontend during preparation. Mid-generation this returns fewer, mis-positioned rows. But each row carries its own user_id/username/name (to_reddit_format), so a preview consumer reading row fields sees self-consistent rows, just incomplete — it does not feed the positional contract to anything correctness-critical.
  
  Net: this is a real but transient/preview-only inconsistency, not a simulation-breaking defect, because the final overwrite ordering preserves the contract for the only position-sensitive consumer (OASIS). It is a genuine latent fragility — the correctness relies on an undocumented, easily-broken invariant that no consumer reads the file between generation start and final overwrite. P2 is appropriate.
  ```

#### [F-5-3] interested_topics consumed by echo-chamber clustering but never produced → T3.4 clustering silently degrades to stance-only

`P2` · `correctness` · confidence **high** · effort **S** · `backend/app/services/simulation_config_generator.py` : 1159-1176, 1215, 573-577

- **Symptom.** Echo-chamber follow clusters are formed only by stance bucket; the intended (stance, dominant-topic) clustering never engages because every agent's interested_topics is empty.
- **Root cause.** AgentActivityConfig.interested_topics is read at line 1215 via cfg.get('interested_topics'), and used in _build_echo_chamber_follows clustering key (lines 573-577). But the agent-config prompt's required JSON schema (lines 1160-1176) does NOT list interested_topics, and _generate_agent_config_by_rule never sets it. So cfg never contains the key on either path → interested_topics is always []. Clustering key collapses to (stance, '') for all agents.
- **Evidence.** `Prompt fields end with `"influence_weight": <影响力权重>` (1172) with no interested_topics; clustering: `if c.interested_topics: topic = normalize_name(str(c.interested_topics[0]))` (575-576)`
- **Impact.** The intended homophily/echo-chamber structure (T3.4) is much coarser than designed; narratives cluster only by stance, weakening a core simulation behavior.
- **Fix.**

  ```
  Populate interested_topics on at least the deterministic rule path (the LLM path is unreliable since it is not asked for the field). Concretely:
  
  1. In _generate_agent_config_by_rule, add a deterministic `interested_topics` to each returned dict derived from entity_type (e.g., mediaoutlet -> ["General News"], student -> ["Education"], university/gov/ngo -> ["Public Affairs"], etc.), mirroring the topic taxonomy already used in oasis_profile_generator.py (lines 834-883) so clustering keys are stable and meaningful.
  
  2. Add `"interested_topics": [<话题列表，1-3个>]` to the LLM prompt's required JSON schema (after line 1172) so the LLM-success path can also produce topics. Since LLM output is non-deterministic, the rule-based derivation should remain a guaranteed fallback when cfg lacks the key (currently line 1215 already coalesces to [] — instead derive from entity_type when empty, e.g. fall back to _generate_agent_config_by_rule(entity)["interested_topics"]).
  
  3. Optionally enrich with hot_topics/dominant event topics passed into the batch so the dominant-topic dimension reflects the actual simulation subject rather than only entity type.
  
  This makes the (stance, topic) clustering key meaningful on both paths and restores the designed T3.4 homophily structure. As a minimum viable fix, step 1 alone (rule-path topics) is sufficient to stop the silent collapse to stance-only, and is fully deterministic.
  ```
- **Verified.**

  ```
  Verified true against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py.
  
  (1) AgentActivityConfig.interested_topics is declared (line 90) and read by the echo-chamber clustering key at lines 575-576: `if c.interested_topics: topic = normalize_name(str(c.interested_topics[0]))`, producing cluster key `(stance, topic)` at line 577.
  
  (2) It is only ever READ, never PRODUCED. The LLM agent-config prompt's required JSON schema (lines 1160-1176) ends at `"influence_weight": <影响力权重>` and does NOT list interested_topics, so the LLM is not asked for it. The rule fallback _generate_agent_config_by_rule (lines 1226-1307) returns a dict in every branch (university/gov/ngo, mediaoutlet, professor/expert/official, student, alumni, else) and none of the six branches include interested_topics. Line 1215 `cfg.get('interested_topics') or []` therefore yields [] on both the LLM-success path (field absent from schema) and the rule-fallback path. A repo-wide grep confirms no other writer in this file; the interested_topics hits in oasis_profile_generator.py belong to a separate OasisAgentProfile dataclass and never flow into AgentActivityConfig.
  
  (3) Consequence: every clustering key collapses to (stance.lower(), "") so clusters are formed by stance bucket alone; the intended (stance, dominant-topic) homophily never engages.
  
  (4) The path is live, not dead code: _build_echo_chamber_follows is invoked at line 399 in the main generation flow and its edges are merged into event_config.initial_follows (lines 400-405), with a try/except that only handles exceptions (line 407), not the degradation.
  
  Severity P2 is correct: this is a real behavioral degradation of a designed simulation feature (T3.4 echo-chamber homophily), but it fails gracefully (no crash, no data loss) and the dataclass docstring at lines 89-90 already acknowledges the degradation ("LLM 未给出时为空，聚类退化为仅按 stance"). It weakens, not breaks, the simulation.
  ```

#### [F-5-4] Per-worker nested ThreadPool + serial Zep retries make persona generation latency-bound and amplify thread count

`P2` · `bottleneck` · confidence **medium** · effort **M** · `backend/app/services/oasis_profile_generator.py` : 295-421, 494-495, 1003-1008

- **Symptom.** Profile generation is slow and spawns far more threads than parallel_count; each entity does up to 2 Zep searches with up to 3 retries and exponential backoff (2s,4s) before the LLM call.
- **Root cause.** generate_profiles_from_entities runs a ThreadPoolExecutor(max_workers=parallel_count=5). Each worker calls _build_entity_context -> _search_zep_for_entity, which itself opens a nested ThreadPoolExecutor(max_workers=2) (line 380) and each inner search retries serially with time.sleep up to 3 times (lines 334-351). With 5 workers that is up to 15 live threads, and on Zep flakiness each entity can block ~6s+ of sleeps before even reaching the LLM. Zep search runs for EVERY entity on top of the related_edges/related_nodes already carried on the EntityNode, partially redundant.
- **Evidence.** `inner pool `with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor` (380) inside outer pool; retries `time.sleep(delay); delay *= 2` (347-348)`
- **Impact.** Long wall-clock for large casts; thread amplification and serial backoff sleeps dominate runtime when Zep is slow/unavailable.
- **Fix.**

  ```
  Keep the change scoped to latency reduction; do not over-engineer the threading model. Concretely:
  
  1) Skip Zep enrichment when the EntityNode already carries sufficient context. In `_build_entity_context` (line 495), gate the call, e.g. only call `_search_zep_for_entity` when `not entity.related_edges and not entity.related_nodes` (or when their combined count is below a small threshold). This removes the redundant round-trip for the common case and is the highest-leverage fix.
  
  2) Reduce the retry/backoff cost rather than removing graceful degradation. In `search_edges`/`search_nodes`, drop `max_retries` to 1-2 and cap total backoff (e.g. delay 0.5s, single retry), or make it config-driven. The current 2s/4s sleeps are the dominant latency under flakiness and add little reliability for a best-effort enrichment that already degrades to empty context.
  
  3) The nested ThreadPoolExecutor(max_workers=2) is acceptable and need not be eliminated — it correctly overlaps the two searches so wall-clock is ~one search, not two. If you want to avoid spinning up a pool object per entity, a lighter touch is to reuse a single shared executor or issue the two searches inline, but this is optional and lower priority than (1) and (2).
  
  Net effect: in the normal path Zep is skipped entirely (no nested pool, no sleeps); in the cold-context path the searches stay parallel with a much smaller, bounded retry budget. This addresses the real latency bottleneck without touching the graceful-failure semantics.
  ```
- **Verified.**

  ```
  Confirmed against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/oasis_profile_generator.py.
  
  CONFIRMED facts:
  - Outer pool: line 1003 `ThreadPoolExecutor(max_workers=parallel_count)`, default `parallel_count=5` (line 896).
  - Nested pool: line 380 `ThreadPoolExecutor(max_workers=2)` inside `_search_zep_for_entity`, which is reached per-entity via `_build_entity_context` (line 495) called from the worker `generate_profile_from_entity` (line 243). So up to 5*(1+2)=15 live threads.
  - Serial retries with exponential backoff in both `search_edges` (334-351) and `search_nodes` (359-376): `max_retries=3`, `delay=2.0`, `time.sleep(delay); delay *= 2` => 2s + 4s = up to 6s of sleeps per search before giving up. Bounded by `future.result(timeout=30)` (385-386).
  - Zep search runs for EVERY entity unconditionally whenever `zep_client` and `graph_id` are set (guards at 310, 322 only skip when client/graph_id are absent). There is no guard to skip Zep when `related_edges`/`related_nodes` already provide context (built at 444-492). The dedup at line 499 (`new_facts = [f for f in zep_results["facts"] if f not in existing_facts]`) confirms the partial redundancy the finding claims.
  
  So the symptom is real: latency is dominated by serial backoff sleeps and a redundant per-entity Zep round-trip when the graph is slow/flaky; thread count exceeds parallel_count.
  
  CALIBRATION: P2 is correct, not higher. There is no correctness defect — failures are caught (try/except at 343/368, 416-419) and degrade gracefully to empty context, so the only impact is wall-clock latency. The thread-amplification angle is somewhat overstated: 15 short-lived, I/O-bound threads (GIL released during network calls) is acceptable and the nested pool is the standard idiom for firing two concurrent searches; it is not pathological. The genuine, currently-true cost is (a) unconditional per-entity Zep enrichment even when EntityNode already carries related_edges/related_nodes, and (b) up to ~6s of serial backoff sleeps per entity under Zep degradation, which serializes throughput once the 5 outer slots are all blocked sleeping.
  ```

#### [F-5-5] Username generation yields empty/duplicate handles for CJK/punctuation/empty names

`P2` · `robustness` · confidence **medium** · effort **S** · `backend/app/services/oasis_profile_generator.py` : 285-293

- **Symptom.** Entities with empty, punctuation-only, or CJK names get usernames like '_472' or raw CJK, and collisions are likely across many agents.
- **Root cause.** _generate_username lowercases, strips to [alnum|_], appends random.randint(100,999). Empty/punctuation names reduce to '' → '_<suffix>'. CJK passes isalnum()==True so the handle stays non-ASCII. Only 900 suffixes means birthday-paradox collisions are likely with dozens of agents; OASIS uses username as the user handle (UserInfo.name in reddit path, username column in twitter).
- **Evidence.** ``username = ''.join(c for c in username if c.isalnum() or c == '_'); suffix = random.randint(100, 999); return f"{username}_{suffix}"` (289-293)`
- **Impact.** Duplicate or degenerate usernames can confuse downstream handle-based lookups and @-mentions, and reduce readability of the simulated social graph.
- **Fix.**

  ```
  Make the base ASCII-safe with a fallback, and guarantee uniqueness by tracking issued handles on the generator instance (so the parallel pool shares state under a lock). Concretely:
  
  1) Build a robust slug: lowercase, replace whitespace with '_', transliterate/strip to ASCII (e.g. `unicodedata.normalize('NFKD', name).encode('ascii','ignore').decode()` then keep `[a-z0-9_]`), collapse repeated underscores, and trim leading/trailing '_'. If the result is empty (empty/punctuation-only/pure-CJK names), fall back to a stable base derived from the source entity, e.g. `user` + short hash of entity.uuid: `base = f"user_{hashlib.sha1(seed.encode()).hexdigest()[:8]}"`.
  
  2) Guarantee uniqueness instead of relying on a 900-value random suffix. Add `self._issued_usernames: set[str]` (init in __init__) and a `self._username_lock = threading.Lock()`. In `_generate_username`, under the lock, take `candidate = base`; if taken, append an incrementing counter (`base_2`, `base_3`, ...) until unused; record and return it. This removes collision risk entirely and is deterministic/readable.
  
  Because `_generate_username` is called from the ThreadPoolExecutor workers (lines 970/988/1044), the lock is required to make the set thread-safe. Pass `entity.uuid` (or entity) into `_generate_username` so the fallback hash is stable per entity. Optionally cap base length (e.g. 30 chars) for tidy handles. This addresses all three sub-issues (degenerate handles, raw CJK, collisions) with a small, low-risk change.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code at /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/oasis_profile_generator.py:285-293. The quoted evidence matches verbatim: `_generate_username` lowercases, replaces spaces with underscores, keeps only `c.isalnum() or c == '_'`, then appends `random.randint(100, 999)`.
  
  I empirically reproduced every claimed failure mode:
  - Empty name -> `_964`; punctuation-only ('...', '!!!') -> `_964`; whitespace-only -> `____964` (degenerate handles).
  - CJK names pass through raw because Python `str.isalnum()` returns True for Unicode letters ('张'.isalnum() == True), so '张三' -> '张三_964', '田中太郎' -> '田中太郎_964'.
  - Only 900 distinct suffixes and NO uniqueness tracking anywhere in the file (grep for set/seen/unique/dedup found only unrelated fact-dedup sets at lines 389/397/444). With dozens of agents (especially same/empty base names), birthday-paradox collisions are likely.
  
  The username flows to the user-visible handle: Twitter CSV `username` column (line 1157) and Reddit JSON `username` field (line 1213), and is the OasisAgentProfile.user_name used as OASIS's `username` field (lines 65/93).
  
  Severity is correctly P2 (robustness/quality), not higher, because the audit's worst-case framing is partly overstated: the PRIMARY OASIS join key is the integer `user_id`/`idx`, not username. The Reddit writer's own comment (line 1195) states "user_id ... 是 OASIS agent_graph.get_agent() 匹配的关键" and both writers emit `idx`/`user_id` as the join field. So username collisions do NOT corrupt the agent graph or break the simulation join — they degrade readability, make @-mentions/handle-based lookups ambiguous, and produce ugly handles for non-Latin/empty entity names. That is a genuine, currently-true robustness defect but non-blocking, consistent with P2.
  ```

### memory — Graph memory readers/updaters/tools

#### [F-4-5] Failed activity batches are silently dropped (no dead-letter / no re-buffer)

`P2` · `robustness` · confidence **medium** · effort **M** · `backend/app/services/zep_graph_memory_updater.py` : 424-431

- **Symptom.** When graph.add fails after MAX_RETRIES, the batch of up to BATCH_SIZE agent activities is discarded; only _failed_count is incremented and an error is logged. Those activities never reach the graph.
- **Root cause.** The final except branch of _send_batch_activities sets self._failed_count += 1 and returns without re-queuing or persisting the failed `activities` (and without writing their typed edges). There is no recovery path.
- **Evidence.** `else:\n logger.error(f"批量发送到Zep失败，已重试{self.MAX_RETRIES}次: {e}")\n self._failed_count += 1   # batch dropped, never retried`
- **Impact.** Transient graph backend errors (e.g. FalkorDB busy, an InternalServerError beyond the retry window) cause permanent loss of simulation evidence from the temporal graph, biasing downstream retrieval/forecast. The loss is silent except for a count in stop()'s summary log.
- **Fix.** On final failure, recover the batch instead of discarding it. Minimal, thread-safe approach: (1) Add an item-level loss counter so the loss is quantified, not just batch-counted: `self._failed_items += len(activities)` alongside `self._failed_count += 1`, and surface `failed_items` in get_stats() and stop()'s summary log. (2) Re-buffer for a later retry under the lock with a hard cap to avoid unbounded memory growth, e.g. `with self._buffer_lock: buf = self._platform_buffers.setdefault(platform, []); if len(buf) < self.MAX_BUFFERED_RETRY: buf.extend(activities[: self.MAX_BUFFERED_RETRY - len(buf)])` (define MAX_BUFFERED_RETRY, e.g. 500). Note this needs care because _flush_remaining iterates/clears buffers under the same lock, so re-buffering during shutdown will be cleared; for shutdown-time failures specifically, fall back to a per-sim dead-letter file: append `combined_text` (plus platform/graph_id/timestamp) to a JSONL file under a sim-scoped path so it can be replayed. At absolute minimum, change the stats/log to report lost item count (not just lost batch count) so the silent loss is visible. Re-buffered batches will be re-sent on the next BATCH_SIZE trigger or final flush, recovering from transient FalkorDB/InternalServerError windows.
- **Verified.** Confirmed by reading backend/app/services/zep_graph_memory_updater.py:406-431. In _send_batch_activities, the retry loop tries client.graph.add up to MAX_RETRIES (3) times. On the final failure the else branch only does `logger.error(...)` and `self._failed_count += 1`, then the method returns implicitly. The `activities` batch is not re-queued, not re-appended to _platform_buffers, and not persisted anywhere; _write_typed_edges (line 421) is only reached on success, so typed edges are also dropped. The batch was already sliced off the buffer before the call (worker_loop lines 376-377), and _flush_remaining (lines 519-528) clears all buffers unconditionally after calling _send_batch_activities, so a failed final flush is also lost. The only surfaced signal is _failed_count (a BATCH count, not item count) in stop()'s log (line 302) and get_stats() (line 541); there is no per-item loss counter (_total_items_sent counts successes only). This matches the finding's symptom, quoted evidence, root-cause claim, and impact exactly. Severity P2 is correct: it is real data loss but only triggers when transient backend errors exceed the 3-retry window; it does not crash the pipeline or corrupt the common path, and it degrades downstream retrieval/forecast fidelity rather than breaking it.

#### [F-4-2] Worker loop holds _buffer_lock across network send + retries + sleep (contention, comment is wrong)

`P2` · `concurrency` · confidence **high** · effort **S** · `backend/app/services/zep_graph_memory_updater.py` : 369-381, 390-431, 445-477

- **Symptom.** The background updater thread can hold _buffer_lock for many seconds (graph writes + up to MAX_RETRIES exponential backoff sleeps + per-batch typed-edge writes + SEND_INTERVAL), blocking every other lock acquirer. The inline comment '释放锁后再发送' (send after releasing the lock) describes behavior the code does not implement.
- **Root cause.** _send_batch_activities(batch, platform) and time.sleep(self.SEND_INTERVAL) are invoked inside the `with self._buffer_lock:` block. _send_batch_activities performs the synchronous graph.add call, retries with time.sleep(RETRY_DELAY*(attempt+1)) (~2+4+... seconds), and then _write_typed_edges issues up to BATCH_SIZE additional synchronous add_triplet graph writes — all under the lock.
- **Evidence.** `with self._buffer_lock: ... # 释放锁后再发送\n self._send_batch_activities(batch, platform)\n time.sleep(self.SEND_INTERVAL)`
- **Impact.** get_stats(), ZepGraphMemoryManager.get_all_stats(), and stop()->_flush_remaining() (called from the monitor thread that also saves run state every 2s) block for the full send duration. On graph slowness or any retry, progress polling and shutdown stall; with SIM_TYPED_FEEDBACK_EDGES default-on the per-batch lock-hold is amplified by N extra graph writes.
- **Fix.**

  ```
  Move the send and SEND_INTERVAL sleep out of the critical section by snapshotting the batch under the lock, then releasing it before any network I/O — making the code match its own comment. Replace lines 369-381:
  
  ```python
                      send_batch = None
                      with self._buffer_lock:
                          if platform not in self._platform_buffers:
                              self._platform_buffers[platform] = []
                          self._platform_buffers[platform].append(activity)
  
                          # 达到批量大小时，在锁内仅切片快照，发送放到锁外
                          if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                              send_batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                              self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
  
                      # 释放锁后再发送（网络/重试/sleep 不再占用 _buffer_lock）
                      if send_batch:
                          self._send_batch_activities(send_batch, platform)
                          time.sleep(self.SEND_INTERVAL)
  ```
  
  Apply the same pattern to _flush_remaining (lines 519-528): snapshot the per-platform buffers under the lock, clear them, then call _send_batch_activities outside the `with self._buffer_lock:` block. Example:
  
  ```python
          with self._buffer_lock:
              pending = {p: b for p, b in self._platform_buffers.items() if b}
              for p in self._platform_buffers:
                  self._platform_buffers[p] = []
          for platform, buffer in pending.items():
              display_name = self._get_platform_display_name(platform)
              logger.info(f"发送{display_name}平台剩余的 {len(buffer)} 条活动")
              self._send_batch_activities(buffer, platform)
  ```
  
  This removes the synchronous graph.add, retry backoff sleeps, typed-edge add_triplet writes, and SEND_INTERVAL from the lock-held region in both the steady-state worker loop and the shutdown flush path, so get_stats()/stop()/_flush_remaining() no longer block on network latency or retries. No new race is introduced: each batch slice is uniquely owned by the thread that removed it from the buffer under the lock.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. In _worker_loop (backend/app/services/zep_graph_memory_updater.py:369-382), both self._send_batch_activities(batch, platform) (line 379) and time.sleep(self.SEND_INTERVAL) (line 381) execute INSIDE the `with self._buffer_lock:` block opened at line 369. The inline comment at line 378, `# 释放锁后再发送` ("send after releasing the lock"), describes behavior the code does not implement — the send is inside the lock. This is a genuine code/comment mismatch and a real critical-section problem.
  
  The work done under the lock is unbounded synchronous I/O: _send_batch_activities (lines 406-431) calls client.graph.add and, on failure, retries up to MAX_RETRIES=3 with time.sleep(RETRY_DELAY*(attempt+1)) = 2s + 4s of backoff; on success it calls _write_typed_edges (lines 445-477) which, when Config.SIM_TYPED_FEEDBACK_EDGES is enabled, issues up to BATCH_SIZE=5 additional synchronous client.graph.add_triplet writes — all while holding _buffer_lock, plus the trailing SEND_INTERVAL=0.5s sleep.
  
  Cross-thread contention is real: get_stats() (line 532), _flush_remaining() (lines 512, 520), and stop()->_flush_remaining() (lines 288-296, called from simulation_runner.py:843/1314) all acquire _buffer_lock and will block for the full send/retry duration.
  
  Severity adjustment: kept at P2 (not higher). Mitigating facts found in the code: (1) the hot producer path add_activity (lines 305-333) only does _activity_queue.put with NO lock, so activity ingestion is NOT blocked by this lock; (2) get_stats() has no in-repo hot/polling caller (only used by backend/scripts/test_graphiti_services.py:121), so the audit's "progress polling stalls" sub-claim is overstated for this repo. The concrete real-world harm is shutdown latency (stop()/_flush_remaining stalling under graph slowness/retries) and a misleading comment that invites future bugs. That is a moderate correctness/maintainability issue, not a P0/P1 hot-path blocker. It is not dead code, not guarded, and not intended behavior (the comment proves intent was the opposite). So P2, high confidence.
  ```

#### [F-4-4] fetch_all_edges has no max_items cap; full edge set loaded and cached unbounded

`P2` · `robustness` · confidence **medium** · effort **S** · `backend/app/utils/zep_paging.py` : 129-171

- **Symptom.** fetch_all_edges pages the entire edge collection into one in-memory list with no upper bound, unlike fetch_all_nodes which caps at _MAX_NODES (2000).
- **Root cause.** fetch_all_nodes guards with `if len(all_nodes) >= max_items: ... break`, but fetch_all_edges has no max_items parameter or equivalent guard. Simulations stream large numbers of activity episodes plus per-batch typed feedback edges, so the edge count grows much faster than node count.
- **Evidence.** `fetch_all_nodes: max_items: int = _MAX_NODES ... if len(all_nodes) >= max_items: ... break  — fetch_all_edges has no such parameter or check.`
- **Impact.** On long/large simulations get_all_edges returns and caches a very large list; panorama_search, _local_search, get_node_edges, and coalition rebuilding all scan it fully, risking high memory use and long stalls during report generation.
- **Fix.** Mirror fetch_all_nodes in fetch_all_edges: add a `max_items: int = _MAX_EDGES` parameter and the same guard inside the pagination loop, e.g. after `all_edges.extend(batch)`: `if len(all_edges) >= max_items: all_edges = all_edges[:max_items]; logger.warning(f"Edge count reached limit ({max_items}), stopping pagination for graph {graph_id}"); break`. Define `_MAX_EDGES` separately and higher than `_MAX_NODES` (e.g. 5000-10000) since edges legitimately outnumber nodes; do not reuse the 2000 node cap or search/coalition results may be truncated too aggressively. Thread the parameter through ZepToolsService.get_all_edges and ZepEntityReader.get_all_edges (and optionally graph_builder callers) so the cap is configurable, ideally backed by a Config value like ZEP_MAX_EDGES. Keep the truncation warning loud, because once truncated, _local_search/panorama_search/get_node_edges/coalition rebuilding silently operate on an incomplete edge set, which is a correctness-affecting side effect that must be observable in logs.
- **Verified.** Confirmed by reading the code. In backend/app/utils/zep_paging.py, fetch_all_nodes (lines 79-126) declares `max_items: int = _MAX_NODES` (2000) and enforces it at lines 114-117 (`if len(all_nodes) >= max_items: all_nodes = all_nodes[:max_items]; logger.warning(...); break`). fetch_all_edges (lines 129-171) has no max_items parameter and no equivalent guard; its only exit conditions are an empty/short batch (lines 159-164) or a missing UUID cursor (lines 167-169), so it pages the entire edge collection into one unbounded in-memory list. The downstream claims are also verified: ZepToolsService.get_all_edges (zep_tools.py lines 703-752) builds and caches the full result (`self._edges_cache[cache_key] = result`, line 751) with no cap, and consumers fully scan it, e.g. _local_search iterates `all_edges` at lines 608-613. ZepEntityReader.get_all_edges (zep_entity_reader.py lines 154-166) and graph_builder (lines 448, 476) likewise call fetch_all_edges with no cap. No env/config cap exists (config.py only has ZEP_MAX_RETRIES; grep found no MAX_EDGES). So the symptom, root cause, and impact are all currently true. This is a defensive-robustness gap, not a present correctness bug, which is why P2 (not higher) is correct: edges legitimately outnumber nodes in these simulations, so an unbounded load plus full-list scans and caching is a real memory/latency risk on large/long runs, particularly during report-generation search and coalition rebuilding.

#### [F-4-3] insight_forge does N+1 per-node round-trips instead of using the cached node map

`P2` · `bottleneck` · confidence **high** · effort **M** · `backend/app/services/zep_tools.py` : 1069-1109

- **Symptom.** InsightForge issues one get_node_detail() call (a separate runtime/graph round-trip on the background event loop) for every distinct entity UUID appearing across all matched edges, with no cap.
- **Root cause.** After collecting entity_uuids from edges, the loop calls self.get_node_detail(uuid) per uuid. get_node_detail bypasses the in-process node cache (_nodes_cache populated by get_all_nodes) and calls client.graph.node.get for each one individually.
- **Evidence.** `for uuid in list(entity_uuids): ... node = self.get_node_detail(uuid)`
- **Impact.** For dense subgraphs this is O(unique_entities) serial blocking graph reads per insight_forge invocation; the report agent can call insight_forge many times, multiplying latency. get_all_nodes already pages the whole graph once and is cached, so most lookups are redundant network/loop hops.
- **Fix.**

  ```
  Build a {uuid: NodeInfo} snapshot once from the already-cached full node set, look up entity_uuids from it, and only fall back to get_node_detail for UUIDs genuinely missing from the snapshot. Reuse the same map for the relationship-chain name resolution at lines 1122-1123 (which already reads node_map). Concretely, before the loop at line 1084:
  
      snapshot = {n.uuid: n for n in self.get_all_nodes(graph_id) if n.uuid}
  
  Then in the loop replace `node = self.get_node_detail(uuid)` with `node = snapshot.get(uuid) or self.get_node_detail(uuid)`. This turns N blocking per-node round-trips into a single cached paged read (cache hit if get_all_nodes was already called for this graph this session) plus at most a few fallbacks for nodes not present in the snapshot.
  
  One correctness caveat to preserve when implementing: get_node_detail's underlying node.get resolves the node by scanning candidate graph IDs and does not strictly require the UUID to belong to `graph_id`, whereas get_all_nodes(graph_id) is scoped to that exact graph. In practice the edges come from search_graph(graph_id=...) on the same graph, so the snapshot should contain every referenced UUID; the get_node_detail fallback handles any cross-graph or stale UUID and keeps behavior identical. The NodeInfo shape returned by get_all_nodes (uuid/name/labels/summary/attributes) is identical to get_node_detail and to what the loop consumes, so the substitution is drop-in.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code path end-to-end.
  
  1. The N+1 loop is real and unbounded. zep_tools.py:1084-1109 iterates `list(entity_uuids)` (every distinct source/target UUID collected from all matched edges, lines 1070-1078) and calls `self.get_node_detail(uuid)` once per UUID with no cap and the explicit comment "处理所有实体，不截断" (process all entities, no truncation).
  
  2. get_node_detail bypasses the cache. get_node_detail (lines 754-784) does NOT consult `_nodes_cache`; it calls `self._call_with_retry(lambda: self.client.graph.node.get(uuid_=node_uuid), ...)` for each UUID individually. There is no in-process lookup.
  
  3. Each call is a real blocking round-trip on the background loop. `client.graph.node.get` (graphiti_client/client.py:82-84) -> `runtime.get_node` (runtime.py:454-455) -> `self.run(self._get_node(...))` which dispatches the coroutine to a persistent background asyncio loop via `asyncio.run_coroutine_threadsafe` (runtime.py:73) and blocks on the future. `_get_node` (runtime.py:457-469) additionally scans `_candidate_graph_ids` and runs a per-UUID `EntityNode.get_by_uuid` DB query. So each of the N lookups is a genuine serial blocking graph read. _call_with_retry (lines 444-475) is purely synchronous (uses time.sleep), so the loop is strictly serial.
  
  4. The redundancy is real. get_all_nodes(graph_id) (lines 663-701) pages the entire graph once and caches the result in `_nodes_cache` (line 700), returning early on cache hit (lines 673-675). For dense subgraphs the per-UUID lookups are overwhelmingly redundant network/loop hops covering nodes already present in that snapshot.
  
  5. Multiplier confirmed. insight_forge is invoked by the report agent at report_agent.py:1138, again via the get_simulation_context redirect (report_agent.py:1232), and during plan_outline (report_agent.py:1370) — so the per-invocation cost is multiplied across an LLM-driven tool loop.
  
  Severity P2 is appropriate: this is a latency/efficiency bottleneck, not a correctness bug. It is bounded by the number of distinct entities across matched edges (search limits are 15 per sub-query + 20 for the main query), so it is not unbounded-to-millions, but with multiple insight_forge invocations and dense subgraphs it can add many serial blocking DB reads. The existing try/except (lines 1087-1109) only swallows per-node failures; it does not mitigate the latency.
  ```

#### [F-4-1] coalition_map ignores FOLLOW/MUTE interactions (missing target_user_name key)

`P2` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/zep_tools.py` : 1847-1857

- **Symptom.** The deterministic coalition/faction map never clusters agents based on follow or mute relationships, so factions formed primarily via following are invisible in the report.
- **Root cause.** coalition_map's target_keys is ("post_author_name","original_author_name","comment_author_name","quoted_author_name","followee_name","target_name"). The simulator stores FOLLOW/MUTE targets under 'target_user_name' (run_parallel_simulation.py:830,839), which is absent from target_keys; it also never produces 'followee_name'/'target_name'. So FOLLOW/MUTE actions contribute zero targets to agent_targets and are dropped from union-find clustering.
- **Evidence.** `target_keys = ("post_author_name", "original_author_name", "comment_author_name", "quoted_author_name", "followee_name", "target_name") — no 'target_user_name'.`
- **Impact.** Faction detection is systematically incomplete: a primary social signal (who follows whom) is excluded, producing fewer/wrong coalitions in the forecast's quantitative section.
- **Fix.**

  ```
  In backend/app/services/zep_tools.py:1847-1848, add the actual simulator key 'target_user_name' to target_keys. The non-existent 'followee_name'/'target_name' can be kept as harmless no-op fallbacks or removed for clarity:
  
      target_keys = ("post_author_name", "original_author_name", "comment_author_name",
                     "quoted_author_name", "target_user_name")
  
  This makes FOLLOW/MUTE edges contribute targets consistently with the simulator schema (run_parallel_simulation.py:830/839) and the LLM-graph path (zep_graph_memory_updater.py:130/190). No other code change needed; action_args is loaded verbatim so the key is present at read time. Optional follow-up: a note that MUTE edges represent antagonism rather than affinity, so merging them into the same coalition as FOLLOW slightly conflates allies and adversaries — but that is a modeling refinement, not part of this defect.
  ```
- **Verified.**

  ```
  Confirmed against actual code AND real data files. In backend/app/services/zep_tools.py:1847-1848, coalition_map's target_keys = ("post_author_name","original_author_name","comment_author_name","quoted_author_name","followee_name","target_name"). The loop at 1851-1857 reads ONLY these keys from each action's action_args. The simulator stores FOLLOW/MUTE targets under 'target_user_name' (run_parallel_simulation.py:830 and :839), which is absent from target_keys. The two keys that ARE in the tuple for social edges — 'followee_name' and 'target_name' — are never written by the simulator.
  
  Verified end-to-end: (1) SimulationRunner.get_actions -> get_all_actions loads action_args verbatim from JSONL with no key remapping (simulation_runner.py:911 action_args=data.get("action_args", {})), so a.action_args for a FOLLOW action literally contains {"follow_id":..., "target_user_name":...}. (2) Real captured data confirms the schema: backend/uploads/simulations/*/reddit/actions.jsonl contain FOLLOW actions like {"action_type":"FOLLOW","action_args":{"follow_id":1,"target_user_name":"Google"}} — 'target_user_name' appears 35 times across runs, while 'followee_name' and 'target_name' appear ZERO times in any action file. (3) Cross-confirmation: zep_graph_memory_updater.py:130/190 (the LLM-graph path) correctly reads action_args.get("target_user_name"), proving target_user_name is the canonical schema and coalition_map is the inconsistent consumer.
  
  Net effect: every FOLLOW/MUTE action contributes an empty/absent value (str(args.get(k,"") or "").strip() is "" for all six keys), so follow/mute edges add nothing to agent_targets and are dropped from union-find clustering — exactly as the finding claims. The function docstring explicitly advertises 关注 (following) as a clustering signal, so this contradicts intended behavior; it is not a guard, dead code, or misreading.
  
  Severity adjusted P1 -> P2: this is a genuine, currently-true data-contract correctness bug in a deterministic report-facing aggregate, but it degrades silently rather than failing — like/retweet/comment/quote edges still cluster correctly, so factions are under-detected (follow-only coalitions invisible), not fully broken. One of several signals lost, no crash, output still produced. That places it at P2 rather than P1.
  ```

### scripts — Simulation scripts (run_parallel/reddit/twitter) + export

#### [F-9-3] export_demo_site_data hard-opens dossier.md and full_report.md with no existence guard, crashing the whole export run

`P2` · `robustness` · confidence **high** · effort **S** · `backend/scripts/export_demo_site_data.py` : 177, 216-217

- **Symptom.** If a showcased pipeline is missing research_report.md or reports/<id>/full_report.md (e.g. a partial run, rotated uploads, or a report stage that failed), export_run raises FileNotFoundError and the entire export aborts mid-loop, leaving later run keys un-exported.
- **Root cause.** Unlike pipeline_state.json (guarded at line 166) and the optional actors/sources copies (guarded at 179-181), the dossier copy `shutil.copyfile(os.path.join(handoff, "research_report.md"), ...)` (line 177) and the report read `open(.../full_report.md).read()` (216-217) assume the files exist and are not wrapped in any existence check or try/except.
- **Evidence.** `export_demo_site_data.py:177 `shutil.copyfile(os.path.join(handoff, "research_report.md"), os.path.join(out, "dossier.md"))`; 216 `report_md = open(os.path.join(UPLOADS, "reports", state["report_id"], "full_report.md"), encoding="utf-8").read()`; 245 `for key, pid in runs.items(): export_run(...)`.`
- **Impact.** One incomplete run kills the export of all remaining runs in the RUNS dict (the for-loop at main():245 has no per-run error isolation), so a single bad run silently blocks the demo site refresh.
- **Fix.**

  ```
  Two-part fix. (1) In export_run, guard both file accesses the same way the log copy is guarded, skipping the run gracefully when a required artifact is absent:
  
      dossier_src = os.path.join(handoff, "research_report.md")
      if not os.path.exists(dossier_src):
          print(f"!! {key}: research_report.md missing, skipped")
          return
      shutil.copyfile(dossier_src, os.path.join(out, "dossier.md"))
  
  and for the report:
  
      report_path = os.path.join(UPLOADS, "reports", state["report_id"], "full_report.md")
      if not os.path.exists(report_path):
          print(f"!! {key}: full_report.md missing, skipped")
          return
      with open(report_path, encoding="utf-8") as f:
          report_md = f.read()
  
  (Also note line 216 leaks the file handle by never closing it — switching to a `with` block fixes that too.) (2) Isolate each run in main() so one failure can never abort the batch:
  
      for key, pid in runs.items():
          try:
              export_run(key, pid, skip_graph=args.skip_graph)
          except Exception as e:
              print(f"!! {key}: export failed ({e.__class__.__name__}: {e}), continuing")
  
  Returning a nonzero exit code when any run failed would also make CI/operator detection cleaner.
  ```
- **Verified.** Confirmed by reading the actual committed code (git diff vs HEAD is empty, so this is the live source). In export_run() the dossier copy at line 177 `shutil.copyfile(os.path.join(handoff, "research_report.md"), os.path.join(out, "dossier.md"))` and the report read at lines 216-217 `open(os.path.join(UPLOADS, "reports", state["report_id"], "full_report.md"), encoding="utf-8").read()` are both unguarded — no os.path.exists check and no try/except. This contrasts directly with adjacent code that IS guarded: the pipeline_state read (165-168, returns with a warning if missing), the research_progress.log copy (175-176, wrapped in `if os.path.exists(log_src)`), and the actors.json/sources.json copies (178-181, wrapped in `if os.path.exists(src)`). So if a showcased run is missing research_report.md or full_report.md (partial run, failed report stage, rotated uploads), export_run raises FileNotFoundError. The driver loop at main():245 `for key, pid in runs.items(): export_run(...)` has no per-run error isolation, and there is no outer try/except (entry is `raise SystemExit(main())`, and grep found no external wrapper/caller). Therefore one bad run aborts the whole export, leaving later run keys un-exported — exactly as claimed. Not a misreading, not dead code (it is a documented operator utility), not handled elsewhere. Severity P2 is correct: real robustness defect but confined to an operator-run demo-export/maintenance script (not user-facing runtime), with a loud, obvious failure mode (a FileNotFoundError traceback) rather than silent corruption.

### frontend — Vue frontend (views/components/api/store)

#### [F-10-1] GraphPanel deep-watch on graphData rebuilds the entire D3 simulation on every refresh, losing zoom/pan/positions

`P2` · `bottleneck` · confidence **high** · effort **L** · `frontend/src/components/GraphPanel.vue` : 835-837, 360-382, 504-526

- **Symptom.** During an active simulation, SimulationRunView refreshes the graph every 30s (and ResearchView/SimulationRunView assign a brand-new graphData object each load). GraphPanel reacts with watch(() => props.graphData, renderGraph, { deep: true }), which runs svg.selectAll('*').remove() and creates a fresh d3.forceSimulation every time, resetting all node positions and the user's pan/zoom and re-running an expensive layout.
- **Root cause.** renderGraph() is a full teardown+rebuild and is wired to a deep watcher that fires on any change to the new object reference returned by getGraphData each poll. There is no diff/merge of nodes/edges and no preservation of existing node x/y or zoom transform.
- **Impact.** Jarring UX (graph jumps, selection/zoom lost) every 30s, plus heavy CPU on larger graphs (force sim restarted from scratch). Makes the live graph effectively unusable while simulating.
- **Fix.** Preserve layout and viewport across refreshes instead of full teardown. Concretely: (1) Before `svg.selectAll('*').remove()`, capture prior node positions into a Map keyed by uuid from the previous `nodes` array (or read from `currentSimulation.nodes()`), and capture the current zoom transform via `d3.zoomTransform(svg.node())`. (2) When building the new `nodes` array (lines 388-393), seed `x`/`y` (and optionally `fx`/`fy` briefly) for any node whose uuid existed before, so existing nodes stay put and only genuinely new nodes get laid out. (3) After re-creating the zoom behavior, re-apply the saved transform with `svg.call(zoom.transform, savedTransform)` so pan/zoom is restored. (4) Lower restart energy on incremental refresh (e.g. `simulation.alpha(0.1)` instead of the default 1.0) so the layout settles gently rather than re-running an expensive cold layout. As a further improvement, gate the full re-render: compare the set of node/edge uuids against the previous render and skip teardown entirely (or do an in-place d3 data join with enter/update/exit using `.data(nodes, d => d.id)`) when the topology is unchanged, since most 30s polls return identical or near-identical graphs. Switching the watcher to shallow (drop `deep: true`) is also advisable since a new object reference already triggers it and the deep traversal is wasted work.
- **Verified.** Confirmed by reading the actual code. GraphPanel.vue:835-837 wires `watch(() => props.graphData, () => nextTick(renderGraph), { deep: true })`. renderGraph (line 360) unconditionally runs `svg.selectAll('*').remove()` (line 377), rebuilds the `nodes` array fresh from props (lines 388-393, no carryover of prior x/y), creates a brand-new `d3.forceSimulation(nodes)` (line 504), and reattaches `d3.zoom()` from scratch (lines 524-526). There is no diff/merge by uuid and no preservation of node positions or the zoom transform. SimulationRunView.vue confirms the live driver: during an active simulation `startGraphRefresh` runs `setInterval(refreshGraph, 30000)` (line 278), and `loadGraph` assigns `graphData.value = res.data` (line 253) — a brand-new object from the API every 30s. Each new object reference fires the watcher and triggers a full teardown+rebuild, resetting all node positions and the user's pan/zoom and re-running the force layout from scratch. The defect is real and currently-true. Severity P2 is appropriate: it is a UX/perf degradation (jarring jumps, lost zoom/selection, CPU spike on larger graphs every 30s) rather than data loss or a crash; it only manifests during live simulation refresh. Minor correction to the root-cause framing: the `deep: true` flag is not actually load-bearing here — because each poll assigns a NEW object reference, a shallow `watch(() => props.graphData, ...)` would already fire every 30s. The deep flag only adds extra reactivity overhead; the real trigger is the reference swap. This does not change the impact or the fix direction.

#### [F-10-12] requestWithRetry retries non-idempotent POSTs (create/prepare/start/interview), risking duplicate side effects

`P2` · `robustness` · confidence **medium** · effort **M** · `frontend/src/api/simulation.js` : 7-9, 15-17, 83-85, 175-177

- **Symptom.** createSimulation, prepareSimulation, startSimulation, and interviewAgents wrap POSTs in requestWithRetry(..., 3, 1000), which retries on ANY thrown error including timeouts where the request may have actually succeeded server-side.
- **Root cause.** requestWithRetry (api/index.js:64-75) retries on every exception with no idempotency check. research.js explicitly avoids this for /run (see its comment) precisely because a lost response on a non-idempotent call spawns a second run — but the same hazard applies to simulation create/prepare/start, which also kick off expensive work.
- **Impact.** A timed-out-but-actually-succeeded startSimulation/prepareSimulation can be retried, launching a duplicate simulation run or duplicate profile generation (extra LLM cost, conflicting state), mirroring exactly the failure mode the /run comment warns about.
- **Fix.** Stop auto-retrying these non-idempotent POSTs. In frontend/src/api/simulation.js, change createSimulation, prepareSimulation, startSimulation, and interviewAgents to call service.post directly (single attempt), exactly as runPipeline in research.js already does, and add the same "non-idempotent: do not retry" comment. Example: `export const startSimulation = (data) => service.post('/api/simulation/start', data)`. Reserve requestWithRetry for safe GET/status polling only. Note (out of finding's stated scope but identical anti-pattern, worth fixing in the same pass): frontend/src/api/report.js:8,50 (/report/generate, /report/chat) and frontend/src/api/graph.js:9,27 (/graph/ontology/generate, /graph/build) also wrap non-idempotent POSTs in requestWithRetry and should be unwrapped too. If retry on these is genuinely desired for resilience, the alternative is to make the endpoints idempotent via a client-supplied request/idempotency key the backend dedupes on — but the minimal, lowest-regret fix is to drop the retry wrapper.
- **Verified.** Confirmed by reading both frontend and backend code. frontend/src/api/index.js:64-75: requestWithRetry retries on EVERY thrown exception (timeout/network/4xx/5xx) with NO idempotency check, then backs off and retries up to 3x. frontend/src/api/research.js:6-11 has an explicit comment that /run must NOT be wrapped in requestWithRetry precisely because a lost response on a non-idempotent call spawns a second hidden pipeline — proving the author knows this hazard and intentionally avoids it. Yet simulation.js:8,16,84,176 wrap four non-idempotent POSTs in requestWithRetry(...,3,1000). Backend confirms non-idempotency: (1) /create -> SimulationManager.create_simulation (simulation_manager.py:217) mints a fresh sim_{uuid4} each call, so a timed-out-but-succeeded create + retry yields a duplicate orphan simulation. (2) /prepare (api/simulation.py:358-616) spawns an async background thread and returns immediately; its idempotency guard _check_simulation_prepared only short-circuits when FULLY prepared (status in prepared list AND config_generated). During the in-flight window (status "preparing", config not yet written) is_prepared is False, so a retried /prepare can launch a SECOND background prepare thread -> duplicate LLM profile generation writing the same reddit_profiles.json/twitter_profiles.csv (real extra cost + conflicting state). (3) /interview/batch re-runs LLM interviews on retry (extra cost). So the central claim and root cause are accurate. One correction to the finding's worst-case impact: /start is actually well-guarded — api/simulation.py:1542-1558 rejects a re-start with HTTP 400 when status==RUNNING and the runner is alive (status set to RUNNING at 1608 before the response), so a retried start does NOT launch a duplicate run; the residual effect is the retry uselessly re-issuing and surfacing a confusing "already running" error. Net: duplicate-run via start is largely mitigated, but duplicate create record and duplicate prepare profile-generation are genuinely exposed. P2 is correct: triggering requires a lost response (less common given the 300s axios timeout), and the most expensive duplicate (start) is blocked, but prepare/create remain real cost/correctness risks.

#### [F-10-7] API base URL falls back to hardcoded http://localhost:5001, bypassing the Vite dev proxy and breaking non-localhost deploys

`P2` · `config` · confidence **medium** · effort **S** · `frontend/src/api/index.js` : 4-10

- **Symptom.** axios baseURL is import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'. When VITE_API_BASE_URL is unset (the default), all requests go directly to http://localhost:5001 rather than relative '/api' paths.
- **Root cause.** vite.config.js defines a proxy for '/api' -> http://localhost:5001 (lines 10-17), but that proxy is only used for relative URLs. Because baseURL is absolute, the proxy is never exercised, and any deployment where the backend is not reachable at localhost:5001 from the browser (production, remote host, different port) will fail with CORS/connection errors unless VITE_API_BASE_URL is explicitly built in.
- **Impact.** Works on a local dev box but silently breaks for any served/production build or remote access; also means the configured dev proxy is dead config that can mask CORS issues.
- **Fix.** In frontend/src/api/index.js:5, change the fallback so requests are same-origin/relative by default: `baseURL: import.meta.env.VITE_API_BASE_URL || '/'` (or `|| ''`). With a relative baseURL, dev requests to `/api/...` flow through the Vite proxy (vite.config.js), and served/production builds hit the same origin that serves the SPA. Keep VITE_API_BASE_URL only as an override for genuinely cross-origin backends. Document the variable in .env.example (e.g., add `# VITE_API_BASE_URL= # optional: only set if the backend is on a different origin`). Note: the symptom's CORS concern is moot because backend/app/__init__.py:43 already allows `origins: "*"` for `/api/*`; the substantive fix is removing the hardcoded host/port so non-localhost deployments work and the dev proxy is actually used.
- **Verified.**

  ```
  Confirmed by reading the actual code. frontend/src/api/index.js:5 sets `baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'`. Every endpoint is a relative path like `/api/simulation/create`, `/api/graph/build`, `/api/research/run` (see src/api/simulation.js, graph.js, research.js, report.js), so with the fallback they resolve to absolute `http://localhost:5001/api/...`. vite.config.js:10-17 defines a dev proxy `/api -> http://localhost:5001`, but because the baseURL is absolute the request bypasses the dev-server origin and the proxy is never exercised (dead config). I verified there is no `.env` file anywhere in the repo (frontend or root) and `VITE_API_BASE_URL` is not referenced in build scripts or .env.example, so the fallback is the effective default. Consequently any served/production build or remote access (different host/port than the browser's localhost:5001) hits a connection failure, exactly as claimed.
  
  One correction to the symptom's framing: the audit blames "CORS errors," but backend/app/__init__.py:43 sets `CORS(app, resources={r"/api/*": {"origins": "*"}})`, so cross-origin requests are actually permitted. The dominant real failure is the hardcoded host/port (connection refused/unreachable when the browser is not on the same machine as a backend at localhost:5001), plus the dead/unused dev proxy. The defect is real and currently true; only the CORS attribution is overstated. P2 is appropriate: local dev on localhost still works (so it is not P0/P1), but it silently breaks every non-localhost/served deployment and makes the configured proxy ineffective.
  ```

---

## 6. P3 — minor / hygiene

### research — Research / DeerFlow bridge

#### [F-0-7] Research prompt passed via argv (--prompt) exposes the full prediction question to process listing

`P3` · `security` · confidence **medium** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 376-382

- **Symptom.** The orchestrator spawns the bridge with the user's prompt inline as a command-line argument (`"--prompt", prompt`), so the entire research/prediction question is visible in `ps`/`/proc/<pid>/cmdline` to any local user.
- **Root cause.** The bridge supports a safer `--prompt-file` path, but the runner always uses `--prompt`.
- **Evidence.** ``cmd = _detect_deerflow_python(deerflow_dir) + [script, "--prompt", prompt, "--out-dir", handoff_dir, ...]`.`
- **Impact.** On a shared/multi-tenant host the prompt (potentially sensitive forecasting questions) leaks via process args; not a code-execution risk since Popen uses a list (no shell), so there is no injection.
- **Fix.**

  ```
  Switch the runner to pass the prompt via the already-supported --prompt-file instead of inline --prompt. Because DeerFlowResearchRunner.run() blocks until the subprocess exits (proc.wait at line 482, inside the stdout for-loop), the temp file can be created before Popen and deleted in the finally block after the child has finished — by then the child has read it (the bridge reads the file eagerly at startup, deerflow_research.py:769-770).
  
  Concretely, in pipeline_orchestrator.py around line 375-382:
  
    os.makedirs(handoff_dir, exist_ok=True)
    # Write the prompt to a file so the (potentially sensitive) question is not exposed
    # in process args (ps / /proc/<pid>/cmdline) on shared hosts. The bridge supports --prompt-file.
    fd, prompt_file = tempfile.mkstemp(prefix=".prompt-", suffix=".txt", dir=handoff_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt)
    try:
        os.chmod(prompt_file, 0o600)  # restrict to owner; defense-in-depth on shared hosts
    except OSError:
        pass
    cmd = _detect_deerflow_python(deerflow_dir) + [
        script,
        "--prompt-file", prompt_file,
        "--out-dir", handoff_dir,
        "--model", model or Config.DEERFLOW_MODEL,
        "--depth", depth or Config.DEERFLOW_RESEARCH_DEPTH,
    ]
  
  Then add temp-file cleanup to the existing finally block (around line 483-487) so the file is removed once the subprocess has terminated:
  
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            _kill_process_group(proc)
        DeerFlowResearchRunner._live_procs.discard(proc)
        try:
            os.unlink(prompt_file)
        except OSError:
            pass
  
  Requires `import tempfile` (and `os` which is already imported). Note: this only closes the argv-exposure surface; if full at-rest secrecy of the prompt is desired, the bridge's REQUIREMENT_FILENAME write (deerflow_research.py:781) would also need to be reconsidered, but that is out of scope for this finding and unnecessary for a P3 argv hardening fix.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. At backend/app/services/pipeline_orchestrator.py:376-382 the runner unconditionally builds `cmd = _detect_deerflow_python(...) + [script, "--prompt", prompt, "--out-dir", handoff_dir, ...]`, passing the user's full research/prediction question inline as an argv element. There is no code path that uses --prompt-file from the orchestrator (grep shows --prompt-file only exists in the bridge, never in the backend). The bridge deerflow_bridge/deerflow_research.py:755-770 exposes a mutually-exclusive group with both --prompt and --prompt-file, and --prompt-file is fully functional (reads and strips a UTF-8 file). So the safer alternative genuinely exists and is simply not used. Because Popen is invoked with a list and no shell=True (lines 398-407), this is purely an information-disclosure issue, not command injection — matching the finding exactly. The prompt is therefore visible to any local user via `ps`/`/proc/<pid>/cmdline`. This is a real, currently-true defect.
  
  Severity P3 is correct and arguably generous: the bridge already writes the same question to disk in plaintext at out_dir/REQUIREMENT_FILENAME (deerflow_research.py:781, where out_dir == handoff_dir), so the prompt is already persisted on disk regardless of argv. The argv exposure is a narrower additional surface (a local user can read process args without filesystem access to handoff_dir). On a single-tenant host the impact is near-zero; on a shared/multi-tenant host it is a legitimate low-severity hardening item. No injection or code-execution risk. The finding is not a misreading and is not already mitigated by any guard.
  ```

#### [F-0-4] actors.json / sources.json / timeline.json written non-atomically — watchdog SIGKILL mid-write can corrupt the contract

`P3` · `robustness` · confidence **medium** · effort **S** · `deerflow_bridge/deerflow_research.py` : 937, 968, 977, 981

- **Symptom.** The report and all structured contract files use Path.write_text() (truncate-then-write), not the atomic tmp+os.replace pattern used elsewhere (e.g. the orchestrator's edit_dossier and DeerFlowClient._atomic_write_json).
- **Root cause.** Stage-2 extraction writes happen right before run end; the orchestrator's watchdog kills the whole process group at the depth budget. A SIGKILL during write_text leaves a truncated/partial JSON file.
- **Evidence.** ``(out_dir / REPORT_FILENAME).write_text(report, ...)`, `(out_dir / ACTORS_FILENAME).write_text(json.dumps(obj,...))`, `(out_dir / TIMELINE_FILENAME).write_text(...)`, `(out_dir / SOURCES_FILENAME).write_text(...)`.`
- **Impact.** On a budget-exhaustion kill during stage-2, actors.json/timeline.json can be left half-written. The orchestrator's _read_json swallows the JSONDecodeError and returns None, so the pipeline degrades to 'no structured dossier' rather than crashing — but the enriched golden-thread contract (situation_brief/relationships/key_events) is silently lost even though the model produced it.
- **Fix.**

  ```
  Add a small atomic-write helper in deerflow_research.py and route all five external-contract writes through it. Mirror the existing os.replace precedent (api/research.py:358 _atomic_write). tempfile is not currently imported, so add `import tempfile`. Example:
  
  ```python
  def _atomic_write_text(path: Path, text: str) -> None:
      fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
      try:
          with os.fdopen(fd, "w", encoding="utf-8") as f:
              f.write(text)
              f.flush()
              os.fsync(f.fileno())
          os.replace(tmp, path)  # atomic on same filesystem
      except BaseException:
          try:
              os.unlink(tmp)
          except OSError:
              pass
          raise
  ```
  
  Then replace the truncate-then-write calls:
  - line 937: _atomic_write_text(out_dir / REPORT_FILENAME, report)
  - line 968: _atomic_write_text(out_dir / ACTORS_FILENAME, json.dumps(obj, ensure_ascii=False, indent=2))
  - line 977: _atomic_write_text(out_dir / TIMELINE_FILENAME, json.dumps(key_events, ensure_ascii=False, indent=2))
  - line 981: _atomic_write_text(out_dir / SOURCES_FILENAME, json.dumps(sources, ensure_ascii=False, indent=2))
  
  Notes/tightening: (a) Use mkstemp in the SAME directory as the target (out_dir) so os.replace is a same-filesystem atomic rename, not a cross-device copy. (b) Worth applying to write_meta() at line 805 too for the same SIGKILL reason, though meta.json corruption is lower-impact. (c) Catch BaseException (not just Exception) so a SIGKILL-adjacent KeyboardInterrupt/SystemExit still cleans up the tmp file — but accept that a true SIGKILL leaves the tmp file orphaned (harmless: it never replaced the real file, which is the whole point). The os.fsync is optional and trades a little latency for durability; it can be dropped if write latency matters since the key guarantee (no partial replacement of a good/absent file) comes from os.replace alone.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. All four contract writes in deerflow_bridge/deerflow_research.py use truncate-then-write Path.write_text(): research_report.md (line 937), actors.json (968), timeline.json (977), sources.json (981). The watchdog SIGKILL is real: pipeline_orchestrator.py:430-437 starts a Timer that calls _kill_process_group while proc.poll() is None, and _kill_process_group (line 316) does os.killpg(..., SIGKILL) against the child's process group (child uses start_new_session=True, line 406). Stage-2 extraction (lines 942-985) runs after the report write and makes LLM calls, so budget exhaustion can kill the process during this phase. The downstream swallow is also real: pipeline_orchestrator.py _read_json (561-568) catches all exceptions and returns None, so a truncated JSON silently degrades to "no structured dossier". The atomic precedent the fix should mirror exists throughout the repo (config.py:246, api/research.py:358 _atomic_write, pipeline_orchestrator.py:214/239, simulation_runner/manager) — note the audit's exact name DeerFlowClient._atomic_write_json does not exist verbatim, but the "tmp+os.replace pattern used elsewhere" claim is accurate.
  
  Why P3 and not higher: (1) The actual write_text syscall is sub-millisecond, so the kill must land in a very narrow window to truncate a file mid-write; the realistic exposure is mainly the larger actors.json with indent=2. (2) The structured dossier is already explicitly best-effort: gated by `if not args.no_actors`, wrapped in `except Exception` (non-fatal, line 984), and the orchestrator has a dedicated timeout-salvage path (lines 493-505) that accepts "report written, no structured dossier" as a valid degraded outcome. So even on corruption the pipeline degrades gracefully — it does not crash or report success on garbage. The loss is best-effort enrichment data on a rare race, which matches P3 robustness.
  ```

#### [F-0-0] Deep-research path can fire a second full tool-free synthesis LLM call redundantly

`P3` · `bottleneck` · confidence **high** · effort **S** · `deerflow_bridge/deerflow_research.py` : 740-745, 910-916

- **Symptom.** For depth=deep, when the in-stage synthesis produces a report shorter than SYNTHESIS_TRIGGER_CHARS (4000) or falls back to concatenated pass notes, main() runs synthesize_from_thread a SECOND time over the same checkpointed thread.
- **Root cause.** run_research_stage() already calls synthesize_from_thread() internally for deep mode (line 740). The generic safety-net in main() (line 912 `if len(_stripped) < SYNTHESIS_TRIGGER_CHARS`) is unconditional of depth and re-runs the identical synthesis again when the first one was weak.
- **Evidence.** `deep branch: `synth = synthesize_from_thread(client, thread_id, ...)` (line 740); main net: `if len(_stripped) < SYNTHESIS_TRIGGER_CHARS and not _is_content_block: ... synth = synthesize_from_thread(client, thread_id, question, args.target_language, args.model, plog, depth=args.depth)` (lines 912-914).`
- **Impact.** A duplicate high-cost LLM call that ingests up to 900K chars of gathered research context (SYNTHESIS_MAX_CONTEXT_CHARS_LARGE) — extra tokens/latency, and on the deep path this is the most expensive call in the run. Worst-case it doubles the synthesis cost exactly when the run is already struggling.
- **Fix.** Skip the main() safety-net synthesis when args.depth=='deep' (run_research_stage already synthesized), or pass a flag indicating synthesis was already attempted so the net only triggers for quick/standard. Alternatively short-circuit if the previous synth text equals the current report.
- **Verified.**

  ```
  Confirmed by reading the code. For deep mode, run_research_stage() (deerflow_research.py:740-742) calls synthesize_from_thread() and returns its result when non-empty. The main() safety net at lines 910-916 is unconditional of depth: line 912 `if len(_stripped) < SYNTHESIS_TRIGGER_CHARS (4000) and not _is_content_block` re-invokes synthesize_from_thread() at line 914 with IDENTICAL inputs (same thread_id, question, target_language, model, depth). So when the deep in-stage synthesis returns a non-empty report shorter than 4000 chars, a second full tool-free synthesis fires over the same checkpointed thread — a genuine duplicate of the most expensive call (it pulls the same thread, caps to the same _synthesis_context_cap up to 900K chars, makes the same bare-model call). The defect is REAL.\n\nHowever the severity is overstated (claimed P2 bottleneck):\n1. Narrow trigger: it only fires when the deep synthesis itself produced a NON-EMPTY but <4000-char report. The synthesis path runs with thinking off and full max_tokens specifically to write long-form, so a sub-4000-char deep synthesis is uncommon, not the normal path.\n2. The audit's second claimed trigger (concatenated pass-notes fallback, lines 744-745) is generally false: deep mode concatenates 3+ research turns, almost always >> 4000 chars, so line 912 is typically False there.\n3. Not pure waste / no correctness impact: line 915 `if len(synth.strip()) > len(_stripped): report = synth` keeps the longer of the two, so the second call can actually recover/improve a weak first synthesis. It is a redundant-cost edge case, not a hard bug.\nNet: a real but edge-case latency/token inefficiency on the deep path — P3, not P2.</reasoning>
  <parameter name="corrected_fix">Make the main() safety net aware that deep mode already attempted synthesis, so it does not blindly re-run the identical call. Track whether run_research_stage already synthesized (e.g., have run_research_stage return a flag/tuple, or simply gate the net on depth). Minimal change at deerflow_research.py:912: `if args.depth != "deep" and len(_stripped) < SYNTHESIS_TRIGGER_CHARS and not _is_content_block:` — for deep, run_research_stage already issued the tool-free synthesis from the same thread, so re-running it cannot improve on identical inputs. If you want to preserve a recovery path for the rare deep-synth-too-short case, instead short-circuit when the prior synthesis text is already what we have: skip the net if the current report is the synthesis output itself (e.g., pass back a `synth_already_run` flag from run_research_stage and only fall into the net when that flag is False, i.e. the deep path fell back to concatenated notes). Keep the existing line 915 `len(synth) > len(_stripped)` guard so quick/standard behavior is unchanged.
  ```

#### [F-0-1] LoopDetectionMiddleware run_id is always "default" under the embedded client — per-run warning scoping collapses

`P3` · `concurrency` · confidence **high** · effort **M** · `deerflow_bridge/patches/middlewares/loop_detection_middleware.py` : 255-264, 504-510

- **Symptom.** The middleware scopes pending warnings by (thread_id, run_id), but DeerFlowClient.stream() never puts run_id in runtime.context (it sets only {"thread_id", optional "agent_name"}). So _get_run_id() always returns "default".
- **Root cause.** `run_id = runtime.context.get("run_id") if runtime.context else None` falls back to "default" because the embedded stream() context omits run_id. _clear_other_run_pending_warnings() therefore can never distinguish passes of a multi-pass deep run (all keys share run_id "default").
- **Evidence.** `client.py: `context = {"thread_id": thread_id}` (no run_id). middleware: `run_id = runtime.context.get("run_id") ... return "default"`.`
- **Impact.** In multi-pass deep research over one thread, pending loop-warnings from an earlier pass are not cleared as 'other-run' state; cleanup relies entirely on before_agent reset + after_agent drop. It mostly self-heals, but the per-run scoping the design intends is inert, so a queued warning could leak across passes if the after_agent drop is skipped (early termination).
- **Fix.**

  ```
  Prefer fixing at the embedded client so all in-process consumers (not just the bridge) get per-run scoping, mirroring what the Gateway worker already does. In deer-flow/backend/packages/harness/deerflow/client.py (and the rc3 copy), generate a fresh run id per stream() call and put it in the context:
  
      context = {"thread_id": thread_id, "run_id": str(uuid.uuid4())}
      if self._agent_name:
          context["agent_name"] = self._agent_name
  
  (`uuid` is already imported and used at line 597.) This restores the (thread_id, run_id) scoping the middleware was designed around and makes _clear_other_run_pending_warnings actually able to evict stale prior-pass warnings on a reused thread. The alternative middleware-side fallback (a monotonic per-thread run counter instead of literal "default") is weaker — it cannot reliably tell a brand-new run apart from re-entry and duplicates state already tracked correctly upstream — so it should only be a fallback if the embedded client cannot be modified. If keeping the change confined to the bridge is preferred, the bridge would need to pass run_id through stream(); but stream() does not currently forward an arbitrary run_id into context (it only spreads **kwargs into _get_runnable_config), so the client-side change is the minimal correct fix.
  ```
- **Verified.**

  ```
  Factual claims are all confirmed by the code. (1) The embedded DeerFlowClient.stream() builds `context = {"thread_id": thread_id}` plus optional `agent_name` and never sets `run_id` (deer-flow/backend/packages/harness/deerflow/client.py:625-627; identical in the rc3 copy). The RunnableConfig built by _get_runnable_config (lines 207-219) also has no run_id. (2) LangGraph populates Runtime.context strictly from the `context=` arg passed to `self._agent.stream(..., context=context)` (line 684) — it does not auto-inject a run_id key — so the middleware's `_get_run_id()` (lines 257-260) always returns "default" on the embedded path. (3) The DeerFlow bridge (deerflow_bridge/deerflow_research.py) drives research exclusively through `client.stream(message, thread_id=..., recursion_limit=...)` (lines 658, 956) reusing one thread_id across passes/stages, so every pending-warning key collapses to (thread_id, "default"). Consequently `_clear_other_run_pending_warnings()` (lines 504-510), which only deletes keys whose run_id differs from the current one, is inert under the embedded client: it can never fire because no two passes ever have distinct run_ids. The contrast confirms the design intent is real and meaningful elsewhere — the Gateway worker path explicitly injects run_id into the runtime context (deer-flow/backend/packages/harness/deerflow/runtime/runs/worker.py:96 `existing_context.setdefault("run_id", runtime_context["run_id"])`), and other middlewares (todo_middleware, thread_data_middleware) read context["run_id"]. So this is a genuine, currently-true behavioral gap specific to the embedded path, not a misreading.
  
  Impact is correctly characterized by the finding as mostly self-healing, which is why this is low severity rather than higher. before_agent calls _reset_run_scoped_loop_state (clears hash/freq counters per run) and after_agent calls _clear_current_run_pending_warnings (drops the (thread_id,"default") key). For a leak to manifest, a warning must be queued (requires >= warn_threshold=3 identical tool-call sets), must not be drained by the next wrap_model_call in the same run, AND after_agent must be skipped (early/abnormal termination). after_agent fires on normal completion and on the Command(goto=END) clarification path, so this is an edge-of-edge case. The worst observable symptom is a spurious "[LOOP DETECTED] stop calling tools" HumanMessage being injected into a later, non-looping pass — annoying and prompt-polluting, not a crash or data-loss. P3 is the right severity.
  ```

#### [F-0-2] --prompt-file read raises uncaught traceback (exit 1), violating documented exit-code 3 for usage errors

`P3` · `robustness` · confidence **high** · effort **S** · `deerflow_bridge/deerflow_research.py` : 769-770

- **Symptom.** A missing/unreadable --prompt-file makes Path(...).read_text() raise FileNotFoundError/UnicodeDecodeError before the try-block, exiting with code 1 and a raw traceback.
- **Root cause.** The file read is performed before meta/ProgressLog setup and outside any error handling; the module docstring promises exit code 3 for 'usage/config error before research starts'.
- **Evidence.** ``if args.prompt_file: question = Path(args.prompt_file).read_text(encoding="utf-8").strip()` with the next branch returning 3 only for empty question.`
- **Impact.** Standalone/smoke-test invocations (the documented use of --prompt-file) get an undifferentiated crash instead of the actionable exit-3 path. The production orchestrator only uses --prompt, so the running pipeline is unaffected.
- **Fix.**

  ```
  Wrap the read in try/except and return 3 with an actionable stderr message, matching the empty-question branch directly below. Replace lines 769-770:
  
      if args.prompt_file:
          try:
              question = Path(args.prompt_file).read_text(encoding="utf-8").strip()
          except (OSError, UnicodeError) as e:
              print(f"ERROR: cannot read --prompt-file {args.prompt_file!r}: {e}", file=sys.stderr)
              return 3
      else:
          question = (args.prompt or "").strip()
  
  Notes: OSError covers FileNotFoundError, PermissionError, and IsADirectoryError (all subclasses); UnicodeError covers UnicodeDecodeError. This returns 3 before plog/meta exist (consistent with the empty-question branch at 773-775, which also returns 3 before that setup), so no ProgressLog cleanup is needed at this point. Using the bare print()+return 3 here (rather than _preflight_fail, which is only defined later at line 828 and assumes plog/meta exist) keeps the fix correct and minimal.
  ```
- **Verified.** Confirmed by reading the code. At /Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:769-770, the prompt-file is read via `Path(args.prompt_file).read_text(encoding="utf-8").strip()` BEFORE any error handling. The broad try/except (line 874-1003) starts well after this, and meta/ProgressLog/_preflight_fail setup (lines 783-834) also come after line 770. main() is invoked at line 1007 via `raise SystemExit(main())` with NO outer try/except. Therefore a missing/unreadable file raises FileNotFoundError/PermissionError/IsADirectoryError/UnicodeDecodeError uncaught -> raw traceback + Python default exit code 1. The module docstring (lines 29-34) explicitly documents exit code 3 for "usage/config error before research starts," and the adjacent empty-question branch (lines 773-775) returns 3, establishing the intended contract this read violates. Impact is correctly scoped: the production orchestrator (pipeline_orchestrator.py:378) uses `--prompt` (inline) not `--prompt-file`, and `--prompt-file` is not referenced anywhere in the backend or bridge docs/skill — it is a standalone/smoke-test-only path. So the running pipeline is unaffected; the defect is a CLI-contract/ergonomics issue (wrong exit code + ugly traceback) for standalone invocations. P3 is appropriate: real but low-impact, no functional pipeline failure. Not a misreading, not already handled, not dead code, not intended behavior.

#### [F-0-3] Stale docstring claims 80% thinking budget while code uses 50% (THINKING_BUDGET_RATIO=0.5)

`P3` · `correctness` · confidence **high** · effort **S** · `deerflow_bridge/patches/models/claude_provider.py` : 269-280

- **Symptom.** _apply_thinking_budget docstring says 'Auto-allocate thinking budget (80% of max_tokens)' but it allocates int(max_tokens * THINKING_BUDGET_RATIO) with THINKING_BUDGET_RATIO=0.5.
- **Root cause.** Docstring not updated when the ratio was lowered from 0.8 to 0.5 (the module-level constant comment correctly documents 0.5).
- **Evidence.** ``"""Auto-allocate thinking budget (80% of max_tokens)."""` then `thinking["budget_tokens"] = int(max_tokens * THINKING_BUDGET_RATIO)` with `THINKING_BUDGET_RATIO = 0.5`.`
- **Impact.** Misleading for maintainers tuning the thinking/visible-output split; no runtime effect.
- **Fix.** Applied: changed the docstring on line 270 to `"""Auto-allocate thinking budget (THINKING_BUDGET_RATIO of max_tokens, currently 50%)."""`. Referencing the constant by name keeps the docstring from going stale again if the ratio is retuned, while still surfacing the current 50% value. No code or behavior change.
- **Verified.** Confirmed by reading the code. In /Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/patches/models/claude_provider.py, _apply_thinking_budget had the docstring `"""Auto-allocate thinking budget (80% of max_tokens)."""` (line 270) while line 280 allocates `int(max_tokens * THINKING_BUDGET_RATIO)` and line 40 defines `THINKING_BUDGET_RATIO = 0.5`. The module-level comment (lines 36-39) explicitly documents the 0.5 value and even cites the prior 0.8 ratio as the problem it fixed ("at 0.8 a 64K ceiling leaves only ~12K for text"), confirming the docstring is a stale leftover from when the ratio was 0.8. This is documentation-only: it has no runtime effect (the constant, not the docstring, drives the math), so P3 is the correct severity. The root-cause claim (docstring not updated when ratio lowered from 0.8 to 0.5) is accurate.

#### [F-0-6] Claude credential preflight accepts a non-OAuth $ANTHROPIC_AUTH_TOKEN that the provider will reject

`P3` · `correctness` · confidence **medium** · effort **S** · `deerflow_bridge/deerflow_research.py` : 846-860

- **Symptom.** Preflight marks claude credentials present if $CLAUDE_CODE_OAUTH_TOKEN or $ANTHROPIC_AUTH_TOKEN is set (have_oauth = bool(...)), without checking the token is actually an OAuth (sk-ant-oat) token.
- **Root cause.** ClaudeChatModel.model_post_init only switches to Bearer auth when is_oauth_token(current_key) (substring 'sk-ant-oat'); a non-OAuth value in $ANTHROPIC_AUTH_TOKEN passes preflight but is then sent as x-api-key, yielding a 401 deep inside Stage 1 — exactly the 'fail fast with an actionable message' case the preflight exists to prevent.
- **Evidence.** ``have_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))` vs provider `if is_oauth_token(current_key): ... else: anthropic_api_key = SecretStr(current_key)`.`
- **Impact.** A mis-set $ANTHROPIC_AUTH_TOKEN burns the full research budget then 401s, instead of failing fast with exit 3.
- **Fix.** In the preflight, treat $ANTHROPIC_AUTH_TOKEN/$CLAUDE_CODE_OAUTH_TOKEN as valid only if is_oauth_token(value) (or also accept $ANTHROPIC_API_KEY for x-api-key mode); reuse credential_loader.is_oauth_token so preflight and provider agree on what counts as a usable credential.
- **Verified.**

  ```
  Confirmed by reading all three layers. PREFLIGHT (deerflow_bridge/deerflow_research.py:846-848): `have_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))` — any non-empty value passes, with no format check. PROVIDER (deerflow_bridge/patches/models/claude_provider.py:103-133): with `ANTHROPIC_API_KEY` unset and a non-OAuth `ANTHROPIC_AUTH_TOKEN` set, `current_key` starts empty (ChatAnthropic reads ANTHROPIC_API_KEY, not ANTHROPIC_AUTH_TOKEN); the `if not is_oauth_token(current_key)` branch calls `load_claude_code_credential()`, which (credential_loader.py:221-226 → `_credential_from_direct_token` at lines 110-114) reads `ANTHROPIC_AUTH_TOKEN` and returns it verbatim WITHOUT any `is_oauth_token` check. `current_key` becomes the non-OAuth value, `is_oauth_token(current_key)` is False (loader.py:31-33 requires substring `sk-ant-oat`), so the else branch (line 132-133) sets it as `anthropic_api_key` and it is sent as x-api-key → 401. So a mis-set ANTHROPIC_AUTH_TOKEN slips through the very preflight whose stated purpose (comment at lines 836-839) is to convert this 401 into a clean exit-3. The defect is genuine and currently true.
  
  Two corrections to the finding's framing: (1) The impact claim "burns the full research budget then 401s" is overstated. The 401 is an AuthenticationError, which is NOT in the retry set (claude_provider.py:_generate only retries RateLimitError/InternalServerError), so it surfaces on the FIRST model request at the start of Stage 1 — fast, before the budget is spent. The real harm is an opaque exit-2 traceback instead of a fail-fast exit-3 actionable message, which is precisely the P3-correctness quality gap the preflight exists to close. (2) The same blind spot also exists in the loader-based fallback at deerflow_research.py:852 (`load_claude_code_credential() is not None` would accept a non-OAuth ANTHROPIC_AUTH_TOKEN too), but that line is only reached when both env vars are unset, so the direct env check at line 848 is the active path that needs fixing. Severity P3 is correct.</reasoning>
  <parameter name="corrected_fix">In the claude preflight (deerflow_bridge/deerflow_research.py:846-860), validate the OAuth env vars with the same `is_oauth_token` predicate the provider uses, so preflight and provider agree on what counts as a usable credential. Replace line 848:
  
      from deerflow.models.credential_loader import is_oauth_token
      have_oauth = is_oauth_token(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or "")
  
  (import `is_oauth_token` inside a try/except, defaulting `have_oauth=False` on import failure, consistent with the existing guarded import at lines 850-854; or do the substring check inline `"sk-ant-oat" in (...)` to avoid the import). `have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))` already independently covers the legitimate x-api-key path, so a real API key in ANTHROPIC_API_KEY still passes. The loader fallback at line 852 only runs when both OAuth env vars are empty, so it is not reached with a set non-OAuth ANTHROPIC_AUTH_TOKEN; for full belt-and-suspenders parity you may also gate that fallback on `is_oauth_token(cred.access_token)`, but it is not required to fix the reported path. Also consider updating the error message to note that ANTHROPIC_AUTH_TOKEN must be an `sk-ant-oat` OAuth token, not a plain API key.
  ```

### orchestrator — Pipeline orchestrator + models

#### [F-1-8] reconcile_orphans re-identifies the orphan research PID only by command substring 'deerflow_research.py' — can mis-signal a sibling pipeline's research on PID reuse

`P3` · `robustness` · confidence **medium** · effort **M** · `backend/app/services/pipeline_orchestrator.py` : 795-816

- **Symptom.** _kill_orphan_research validates the PID's command line contains 'deerflow_research.py' before SIGTERM-ing its process group, but every concurrent pipeline runs deerflow_research.py. If the OS reused the persisted PID for a different live research process, the wrong group is signalled.
- **Root cause.** Reconciliation persists only the bare PID and re-identifies it with a loose substring match on `ps -o command=`; there is no pipeline-id / start-time token in the process to disambiguate after PID reuse.
- **Evidence.** ``if check.returncode != 0 or "deerflow_research.py" not in cmdline: return` then `os.killpg(os.getpgid(pid), signal.SIGTERM)` — match is only the script name.`
- **Impact.** Low probability (requires PID reuse landing on a process whose argv contains the literal script name within the reconcile window), but the realistic hazard is killing a sibling pipeline's research after a restart.
- **Fix.**

  ```
  Tighten the existing guard using data already present in the command line — do NOT add a new `--pipeline-id` arg. The launched argv already contains the pipeline-unique `--out-dir {PIPELINE_DATA_DIR}/{pipeline_id}/handoff`, so the orphan's own pipeline_id is already in `ps -o command=`. Change the check so it requires BOTH the script name AND the pipeline's handoff path (or bare pipeline_id) to be present:
  
  ```python
  expected_dir = PipelineManager.handoff_dir(pipeline_id)  # .../{pipeline_id}/handoff
  if (check.returncode != 0
          or "deerflow_research.py" not in cmdline
          or expected_dir not in cmdline):
      return
  ```
  
  `pipeline_id` is already a parameter to `_kill_orphan_research`, so no signature/launch changes are needed. Optionally, persist the subprocess start time alongside `research_pid` and compare against `ps -o lstart=`/`etime` to defend against the (already remote) chance that a reused PID happens to be running a different pipeline whose path coincidentally appears — but the handoff-path match alone closes the described PID-reuse mis-kill at essentially zero cost. Since this is startup-only, single-shot reconciliation with defensive try/except already in place, P3 hardening is the right framing.
  ```
- **Verified.**

  ```
  Confirmed from code. In `_kill_orphan_research` (pipeline_orchestrator.py:805-813) the PID-reuse guard only checks `"deerflow_research.py" not in cmdline` before `os.killpg(os.getpgid(pid), signal.SIGTERM)`. Since every concurrent pipeline runs the same `deerflow_research.py` script, this substring match cannot distinguish a reused PID that now belongs to a different live research process. So the looseness the audit describes is genuinely present and currently true.
  
  However, the audit's ROOT-CAUSE CLAIM is wrong and overstated: it asserts "there is no pipeline-id token in the process to disambiguate." That is false. The subprocess is launched (line 379) with `--out-dir handoff_dir`, and `handoff_dir = {PIPELINE_DATA_DIR}/{pipeline_id}/handoff` (lines 200-201). Therefore every research subprocess's argv — and thus its `ps -o command=` output — already contains its own unique pipeline_id. The disambiguating token exists; the matcher simply ignores it. The proposed fix (add a new `--pipeline-id` arg) is unnecessary over-engineering; the correct fix reuses data already on the command line.
  
  Severity P3 is correct (arguably even lower). The realistic exploit window is tiny and narrow: `reconcile_orphans` runs exactly once, at backend startup (app/__init__.py:57), only for pipelines persisted as `running` and absent from `_threads` (which is always empty at process start). A mis-kill additionally requires (a) the OS to have recycled the dead orphan's PID and (b) that recycled PID to now belong to a *wanted, legitimately-running* sibling `deerflow_research.py` at the precise reconciliation instant. On a fresh boot, other live `deerflow_research.py` processes are themselves orphans from the same crashed prior backend and are legitimate kill targets anyway. The whole block is also wrapped in try/except guards (lines 800-816, 792-793). Low probability, low blast radius -> P3 robustness, not higher.
  ```

#### [F-1-3] Dynamic progress-band recompute can make global_progress jump backward (non-monotonic progress bar)

`P3` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 1248-1294, 1499-1502, 1647-1649

- **Symptom.** Within the GRAPH stage, upd(5,...) is emitted using the STATIC bands (graph=40-60) BEFORE _recompute_dynamic_bands runs (line 1502); after recompute, the graph band can shrink (few chunks → smaller weight), so a later upd at a higher local % may map to a LOWER global % than the earlier upd, and persisted global_progress decreases.
- **Root cause.** _global_from_stage reads state.options['dynamic_bands'] which is rewritten mid-stage by _recompute_dynamic_bands. The new bands are computed from cost signals without any clamp ensuring the recomputed band keeps global_progress monotonic across the recompute boundary.
- **Evidence.** `GRAPH does `upd(5, ...)` (1499) then `self._recompute_dynamic_bands(state, chunk_count=len(chunks))` (1502); update() sets `state.global_progress = self._global_from_stage(... state.options.get('dynamic_bands'))` (1315-1317) with no max() clamp.`
- **Impact.** Cosmetic but user-visible: the progress bar / ETA can move backward at stage transitions and when signals first arrive. No functional/data effect; degrades trust in the UI.
- **Fix.** Clamp persisted progress to be monotonic in `_make_stage_updater.update()`. Replace the assignment at lines 1315-1317 with:\n\n    state.global_progress = max(\n        state.global_progress,\n        self._global_from_stage(state.mode, stage, local_pct, state.options.get('dynamic_bands')),\n    )\n\nThe clamp is safe pipeline-wide because all static and dynamic bands are non-overlapping and monotonically increasing by stage order (research 0-30 < ontology 30-40 < graph 40-... etc.), and global_progress only ever starts at 0 for a fresh pipeline, so a later stage can never legitimately need a lower global value. For full robustness also apply the same `max(state.global_progress, ...)` clamp at the `_complete_stage` assignment (line 1334), since a shrunken recomputed band could otherwise make stage-completion (local 100%) map below the last in-stage upd. (An alternative — recomputing bands once at stage entry before the first upd — does not by itself fix the cross-stage RUN-boundary recompute, so the clamp is the more complete fix.)
- **Verified.** Confirmed by reading the code and working through the arithmetic. Constants (lines 73-80): STAGE_GRAPH static band = (40,60), width 20. In the GRAPH stage (pipeline_orchestrator.py line 1499) `upd(5, ...)` runs BEFORE `_recompute_dynamic_bands(state, chunk_count=len(chunks))` at line 1502. `_make_stage_updater.update()` (1315-1317) sets `state.global_progress = self._global_from_stage(state.mode, stage, local_pct, state.options.get('dynamic_bands'))` with NO max()/monotonic clamp, and `_global_from_stage` (1248-1255) only clamps local_pct, never the global result.\n\nWorked example: `upd(5)` with static graph band (40,60) → 40 + 20*5/100 = 41. Then recompute with few chunks shrinks the graph band: weights are w_graph=cc, w_prepare=8, w_run=(tr or 24)*1.5=36, w_report=(sc or 6)*6=36 (lines 1278-1281). For cc=1: tot=81, graph seg = 60*1/81 ≈ 0.74 → band [40,41]. The next graph-stage update — seeding `upd(8)` (line 1514) → 40 + 1*8/100 = 40.08 → int 40, OR add_cb `upd(10)` (line 1520) → 40 + 1*10/100 = 40.1 → int 40 — yields global 40, a backward move from 41. Even at the default cc=20 (band [40,52]), the seeding `upd(8)` maps to 40 (drop from 41). The decreased value is persisted via PipelineManager.save(state) (line 1318) and pushed to task_manager.update_task(progress=state.global_progress) (line 1322), so it is user-visible.\n\nThe same non-monotonic pattern recurs at the RUN-stage recompute boundary (line 1649, after `upd(2)` at 1618), since cost_signals accumulate and the run band can shift relative to the band used by the earlier upd. `_complete_stage` (1334) also re-derives global from the (possibly shrunken) band with no clamp.\n\nImpact is cosmetic only: the progress bar / ETA can tick backward by a few points at the recompute boundary. No functional or data effect. P3 is the correct severity. Confidence high — the backward movement is arithmetically guaranteed for small chunk counts, which is exactly the finding's stated trigger.

#### [F-1-7] artifacts contract docstring says 'handoff 相对文件名' but code stores absolute server paths (stale contract, leaks host paths)

`P3` · `data-contract` · confidence **high** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 146-148, 1343-1372

- **Symptom.** PipelineState.artifacts is documented as 'stage 名 → handoff 相对文件名' (relative filename within handoff), but _record_stage_artifacts stores full absolute filesystem paths, including artifacts under OASIS_SIMULATION_DATA_DIR / RUN_STATE_DIR that are not in handoff at all.
- **Root cause.** The contract was documented as relative-to-handoff, but personas CSV / run_summary / initial_posts live outside handoff so absolute paths were used; the docstring was never updated. The artifact API opens the stored path verbatim, so runtime is correct.
- **Evidence.** `Docstring: 'stage 名 → handoff 相对文件名'; code: `state.artifacts[name] = path` with path = os.path.join(hd, 'actors.json') and add_if('run_summary', os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, 'run_summary.json')).`
- **Impact.** No runtime defect today, but latent risk: a developer trusting the comment might prepend handoff_dir or expose the relative name and break deep links; absolute host paths are also persisted into pipeline_state.json (mild info leak).
- **Fix.**

  ```
  Update the docstring at backend/app/services/pipeline_orchestrator.py:146-148 to accurately describe the stored values as absolute server filesystem paths (not handoff-relative filenames), e.g.:
  
      # T6.3: 各阶段产物的可深链指针（stage 名 → 该产物的绝对文件路径）。
      # 注意：值是服务端绝对路径（可能位于 handoff、OASIS_SIMULATION_DATA_DIR
      # 或 RUN_STATE_DIR），GET /api/research/<id>/artifact/<name> 直接 open() 该路径。
      artifacts: dict[str, str] = field(default_factory=dict)
  
  This eliminates the misleading contract that could cause a future dev to prepend handoff_dir or expose the value as a relative name. If the host-path leak in pipeline_state.json is later deemed worth addressing, that is a separate, optional follow-up (e.g. resolve a {name, base, rel} schema at read time) and is not warranted at P3.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. The PipelineState.artifacts docstring at backend/app/services/pipeline_orchestrator.py:146-148 says "stage 名 → handoff 相对文件名" (stage name -> handoff RELATIVE filename), but the values actually stored are ABSOLUTE server paths:
  
  - _record_stage_artifacts (lines 1343-1372): state.artifacts[name] = os.path.join(hd, "actors.json") where hd = state.handoff_dir (itself an absolute path), plus os.path.join(Config.OASIS_SIMULATION_DATA_DIR, sim_id, ...) and os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "run_summary.json") — the latter two are outside handoff entirely (personas CSV, simulation_config, run_summary).
  - Line 1681 likewise: state.artifacts["run_summary"] = os.path.join(SimulationRunner.RUN_STATE_DIR, ...).
  
  The read API confirms the values must be absolute: research.py:390-394 does path = artifacts.get(name) then open(path, ...) verbatim, so runtime is correct precisely because absolute paths are stored — i.e., the docstring is wrong, not the code. The root-cause and "no runtime defect today" claims are both accurate.
  
  PipelineManager.save persists state via asdict(), so absolute host paths (e.g. /Users/.../handoff, OASIS_SIMULATION_DATA_DIR) are written into pipeline_state.json — a mild host-path info leak, as claimed.
  
  This is a real, currently-true defect, but it is a stale-comment/data-contract inaccuracy with zero functional impact. P3 is correct: no runtime bug, only latent maintenance risk (a dev trusting the comment could prepend handoff_dir or expose the "relative name" and break deep links) plus minor host-path persistence. I down-scope the proposed fix: the {name, rel_path} schema normalization is over-engineering for a P3 and would require touching both write sites and the read API (which would then have to re-resolve against multiple distinct base dirs). The minimal, lowest-regret fix is to correct the docstring to state these are absolute server paths.
  ```

#### [F-1-1] Cancellation during RUN polling is delayed up to 5s by a blocking time.sleep(5) instead of waiting on the cancel event

`P3` · `concurrency` · confidence **high** · effort **S** · `backend/app/services/pipeline_orchestrator.py` : 1632-1659

- **Symptom.** After a user clicks cancel, the OASIS simulation keeps running (burning LLM credits) for up to one full poll period because the loop checks the cancel event only once per iteration and then blocks in time.sleep(5).
- **Root cause.** The RUN loop checks cancel_ev.is_set() only at the top of each iteration, then unconditionally calls time.sleep(5). The cancel event is a threading.Event whose .wait() would return immediately on set, but the code uses time.sleep, so a cancel raised mid-sleep waits out the remaining interval. (The research subprocess path uses a proper 1s wait-watcher; RUN does not.)
- **Evidence.** `Loop body ends with `time.sleep(5)` (1659) while cancel is only tested at loop top via `if cancel_ev is not None and cancel_ev.is_set():` (1633).`
- **Impact.** Up to 5s of extra paid simulation rounds per cancel during RUN. The design comment at 1645-1646 acknowledges the delay is bounded by one 5s cycle, but it is avoidable. Cost/latency, not data loss.
- **Fix.**

  ```
  Make the per-iteration sleep interruptible by waiting on the cancel event instead of `time.sleep(5)`, and keep the single cancel handler at the loop top so a wake re-runs the existing stop_simulation + PipelineCancelled branch. Replace line 1659 `time.sleep(5)` with:
  
      if cancel_ev is not None:
          cancel_ev.wait(timeout=5)  # 取消时立即唤醒，下一轮顶部检查停掉模拟并退出
      else:
          time.sleep(5)
  
  The top-of-loop check at lines 1633-1639 (stop_simulation + raise PipelineCancelled) then fires on the very next iteration with no remaining-interval delay. This avoids duplicating the cancel branch and matches the interruptible-wait pattern already used by the research subprocess watcher at line 445. (Optionally, lower OASIS round cost further by also testing cancel_ev between get_run_state and progress updates, but the wait-based sleep already eliminates nearly all of the up-to-5s window.)
  ```
- **Verified.** Confirmed by reading backend/app/services/pipeline_orchestrator.py. The RUN polling loop (lines 1632-1659) checks cancellation only once per iteration at the loop top (line 1633: `if cancel_ev is not None and cancel_ev.is_set():`) and then ends each iteration with an unconditional `time.sleep(5)` (line 1659). `cancel_ev` is a `threading.Event` (defined at lines 754/913/1057/1122/1191), so `.wait(timeout=...)` is available and would return immediately when the event is set. `time` is imported (line 34). The cancel path is genuinely reachable: `cancel()` (line 947) calls `event.set()` while the `_run` thread is alive. The inconsistency claim also holds — the research subprocess path uses a proper interruptible watcher `cancel_event.wait(timeout=1.0)` (line 445), while RUN uses plain `time.sleep`. The code comment at lines 1645-1646 explicitly acknowledges the bounded delay ("取消请求由循环顶部的检查兜底，最多延迟一个 5s 周期"), so this is a known, avoidable latency, not a misreading. Impact is cost/latency only (up to ~5s of extra simulation rounds per cancel), with no data-loss or correctness risk and the delay already bounded — so I downgrade from P2 to P3 (minor cost optimization). The proposed fix is directionally correct but, as worded, would duplicate the stop-and-raise branch; the tightened version below makes the sleep interruptible while reusing the single existing cancel handler at the loop top.

### sim-runtime — Simulation runtime (runner/manager/ipc) + API

#### [F-6-9] SimulationManager and SimulationRunner both read-modify-write state.json concurrently with no shared lock, risking lost status updates

`P3` · `concurrency` · confidence **low** · effort **M** · `backend/app/services/simulation_runner.py` : 1349-1363

- **Symptom.** On shutdown, cleanup_all_simulations reads state.json, sets status='stopped', and atomically rewrites it; meanwhile SimulationManager._save_simulation_state may rewrite the same file from a request thread. Both read-modify-write without coordination, so one clobbers the other's fields.
- **Root cause.** Two classes independently own writes to the same state.json with no shared/file lock. Each does atomic tmp+os.replace (prevents torn reads) but there is no compare-and-swap, so a save based on a stale read overwrites a just-written status.
- **Evidence.** `cleanup: `with open(state_file) ... state_data['status']='stopped' ... tmp write + os.replace`; manager._save_simulation_state independently does tmp write + os.replace of `state.to_dict()`.`
- **Impact.** Last-writer-wins between runner cleanup and a manager save can leave state.json with status='running' after a clean shutdown, contradicting run_state.json and confusing resume/history logic.
- **Fix.** Funnel all state.json read-modify-write through a single shared, process-wide threading.Lock (a class-level lock shared by both SimulationManager._save_simulation_state and SimulationRunner.cleanup_all_simulations), held across the read+mutate+os.replace sequence so the two serialize. Because no separate OS process writes state.json (child scripts only write run_state.json; API sites only read), an in-process threading.Lock is sufficient and a file lock (fcntl/portalocker) is unnecessary overkill. Cleaner still: have cleanup_all_simulations call into SimulationManager (e.g. a set_status helper) rather than hand-editing the file, so a single owner performs all writes under the one lock. Note that manager._save_simulation_state writes a full stale snapshot while cleanup does a targeted status update, so even with a lock the manager save should ideally re-read or update only changed fields to avoid resurrecting stale non-status fields.
- **Verified.**

  ```
  Confirmed against the code. Both writers target the identical path uploads/simulations/<id>/state.json: SimulationManager.SIMULATION_DATA_DIR (manager.py:126-129) and SimulationRunner.RUN_STATE_DIR (runner.py:214-217) resolve to the same directory. SimulationManager._save_simulation_state (manager.py:144-156) writes state.to_dict() via tmp+os.replace from a possibly-stale in-memory SimulationState. SimulationRunner.cleanup_all_simulations (runner.py:1349-1363) independently reads state.json fresh, sets status='stopped' and updated_at, then rewrites via tmp+os.replace. A targeted grep for threading.Lock/RLock/fcntl/portalocker/filelock across both files returned nothing — there is NO shared lock and no compare-and-swap, only per-write atomicity (which prevents torn reads, not lost updates).
  
  The race is genuinely reachable: run.py:45 runs app.run(threaded=True), so request worker threads can be mid-_save_simulation_state while the SIGTERM/SIGINT handler thread runs cleanup_all_simulations (registered via signal handlers + atexit, runner.py:1427-1451). If a worker's os.replace lands after cleanup's, a stale snapshot (e.g. status='running'/'completed') overwrites cleanup's status='stopped', exactly the claimed impact. The author was aware of the interaction — manager.py:151-153 comments that the runner rewrites the same file during cleanup — but only added atomicity for torn reads, not cross-writer ordering. So the defect is currently true and not guarded.
  
  Why P3 not P2: the only competing read-modify-write writers of state.json are these two, both in-process (the child OASIS scripts and API sites at simulation.py:1099/1195 only READ state.json; child processes write run_state.json/logs). The blast radius is a single transient field (status) in a non-authoritative file, only during a shutdown race with an in-flight request; the authoritative run_state.json is set correctly (runner.py:1338-1346), and the inconsistency self-heals on restart, degrading only resume/history cosmetics. Narrow window + low impact + self-recovering = P3.
  ```

#### [F-6-13] _check_simulation_prepared rewrites state.json with non-atomic 'w' truncation, reintroducing the torn-read race the rest of the code avoids

`P3` · `concurrency` · confidence **high** · effort **S** · `backend/app/api/simulation.py` : 323-333

- **Symptom.** When auto-upgrading status preparing->ready, the code does open(state_file,'w') + json.dump directly (no tmp+replace), so a concurrent reader (realtime config/profile endpoints, runner cleanup) can read a half-written file.
- **Root cause.** SimulationManager._save_simulation_state and SimulationRunner cleanup deliberately use atomic tmp+os.replace to prevent partial-JSON reads, but this inline API-layer rewrite bypasses that pattern.
- **Evidence.** ``with open(state_file, 'w', encoding='utf-8') as f: json.dump(state_data, f, ensure_ascii=False, indent=2)` inside _check_simulation_prepared, no tmp/replace.`
- **Impact.** Rare partial-read JSONDecodeError on state.json during the prepare->ready transition while another thread polls realtime endpoints; readers mostly swallow it, so impact is low but it violates the codebase's own atomic-write invariant.
- **Fix.**

  ```
  Replace the direct truncating write at simulation.py:328-329 with the same atomic tmp+os.replace pattern used everywhere else (os and json are already in scope; no new import needed):
  
  ```python
  if status == "preparing":
      try:
          state_data["status"] = "ready"
          from datetime import datetime
          state_data["updated_at"] = datetime.now().isoformat()
          # 原子写入（tmp + os.replace），避免并发实时端点读到半截 JSON，
          # 与 SimulationManager._save_simulation_state / SimulationRunner 清理保持一致
          tmp_file = state_file + ".tmp"
          with open(tmp_file, 'w', encoding='utf-8') as f:
              json.dump(state_data, f, ensure_ascii=False, indent=2)
          os.replace(tmp_file, state_file)
          logger.info(f"自动更新模拟状态: {simulation_id} preparing -> ready")
          status = "ready"
      except Exception as e:
          logger.warning(f"自动更新状态失败: {e}")
  ```
  
  Prefer the inline tmp+os.replace over calling manager._save_simulation_state here, because the latter serializes via SimulationState.to_dict() rather than the raw state_data dict that was just loaded and minimally patched; reusing state_data avoids any field-mapping divergence while still restoring the atomic-write invariant.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. The quoted defect exists verbatim at /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/api/simulation.py:328-329, inside _check_simulation_prepared's preparing->ready auto-upgrade branch: `with open(state_file, 'w', encoding='utf-8') as f: json.dump(state_data, f, ensure_ascii=False, indent=2)` — a non-atomic truncating write with no tmp+os.replace.
  
  The codebase invariant the finding cites is real and explicitly documented, not invented. SimulationManager._save_simulation_state (simulation_manager.py:151-156) uses tmp+os.replace with a comment stating direct 'w' truncation "可能让并发读者读到半截 JSON" (may let concurrent readers read half-finished JSON). SimulationRunner cleanup (simulation_runner.py:1358-1362) does the same with comment "避免并发读者读到半截 JSON（与 SimulationManager 一致）". So the inline API rewrite is the lone deviation from a deliberately-established atomic-write pattern for this exact state.json file.
  
  The concurrency exposure is also real: state.json is read directly (bypassing the manager) by two realtime polling endpoints — get_simulation_profiles_realtime (lines 1099-1108) and get_simulation_config_realtime (lines 1195-1213) — which exist specifically to be polled by the frontend during the preparing phase. A reader hitting the file mid-truncation during the preparing->ready transition could observe partial JSON.
  
  Severity P3 is correct, not higher. Both realtime readers wrap json.load in try/except that swallows the error (except Exception: pass), and the write itself is inside a try/except (line 332). A torn read causes at most one stale poll cycle (is_generating defaults, config None), self-corrects on the next poll, and never crashes a request or corrupts persisted state (the file content is well-formed once the single write completes; there is no interleaving of two writers here). It is a genuine but low-impact inconsistency with the project's own invariant. is_real=true with high confidence.
  ```

#### [F-6-12] Requested graph-memory updater failure is swallowed; run proceeds with updates off while API still reports them enabled

`P3` · `robustness` · confidence **high** · effort **S** · `backend/app/services/simulation_runner.py` : 386-399

- **Symptom.** When enable_graph_memory_update=True but ZepGraphMemoryManager.create_updater raises, the exception is logged and _graph_memory_enabled is set False; start_simulation returns success and the sim runs with NO graph memory updates, yet the API response reports graph_memory_update_enabled=True (it echoes the request flag).
- **Root cause.** The except branch downgrades a requested feature to silently-off instead of failing or recording the degradation in run state. The /start route sets response_data['graph_memory_update_enabled']=enable_graph_memory_update from the request, not from the actual outcome.
- **Evidence.** ``except Exception as e: logger.error(...); cls._graph_memory_enabled[simulation_id] = False` then start returns RUNNING; API: `response_data['graph_memory_update_enabled'] = enable_graph_memory_update`.`
- **Impact.** Operator believes the graph is being updated during the sim (for later analysis/AI chat) when it is not; the failure is visible only in logs. Data-contract mismatch between API response and reality.
- **Fix.** Record and report the actual outcome instead of the request flag. (1) In simulation_runner.py, surface the real state on SimulationRunState: add fields graph_memory_requested and graph_memory_active (plus an optional graph_memory_error). In start_simulation set state.graph_memory_requested = enable_graph_memory_update; in the try set state.graph_memory_active = True, and in the except set state.graph_memory_active = False and state.graph_memory_error = str(e) (then cls._save_run_state(state)) so to_dict() carries the truth. Optionally fail fast by re-raising if the operator requires guaranteed graph updates. (2) In simulation.py around line 1614, report the actual outcome rather than the request flag, e.g. response_data['graph_memory_update_enabled'] = SimulationRunner._graph_memory_enabled.get(simulation_id, False) (or run_state.graph_memory_active), and include graph_memory_requested and any graph_memory_error so a degraded run is visible in the API response, not just the logs. Prefer the run_state field approach over reaching into the private _graph_memory_enabled dict for a clean contract.
- **Verified.** Confirmed by reading the actual code. In backend/app/services/simulation_runner.py:387-399, when enable_graph_memory_update=True and graph_id is present, the updater is created in a try/except. If ZepGraphMemoryManager.create_updater raises (it constructs ZepGraphMemoryUpdater(graph_id) and calls .start() at zep_graph_memory_updater.py:576-577, both able to raise on Zep client/network/auth failure), the except branch only logs (logger.error) and sets cls._graph_memory_enabled[simulation_id] = False. Control then falls through to launch the subprocess and the method returns state with runner_status=RUNNING (line 469/490). The runtime correctly gates updates on _graph_memory_enabled (checked at lines 567, 614, 841), so updates genuinely do NOT run after a failed creation. However, the /start route in backend/app/api/simulation.py:1614 sets response_data['graph_memory_update_enabled'] = enable_graph_memory_update from the request payload, never consulting the actual _graph_memory_enabled outcome. Net effect: on a creation failure the API returns success with graph_memory_update_enabled=true while the simulation runs with NO graph-memory updates — a real data-contract mismatch visible only in logs. This is exactly as the finding describes (no nearby guard re-reads the actual state for the response). Severity P3 is correct: it only triggers on the create-updater failure path (narrow), the error is logged, and the impact is degraded observability/operator trust rather than data corruption or a crash.

#### [F-6-7] Env command loop drains at most one IPC command per 0.5s tick, serializing queued single interviews

`P3` · `bottleneck` · confidence **medium** · effort **S** · `backend/scripts/run_parallel_simulation.py` : 567-576, 1846-1858

- **Symptom.** process_commands() pulls exactly ONE command then the loop waits via wait_for(0.5). Multiple queued commands drain at one-per-half-second (plus per-command LLM latency), so rapid single interviews queue up and can exceed their timeout.
- **Root cause.** process_commands returns after a single poll_command; the surrounding loop sleeps 0.5s before the next poll. There is no inner drain of all pending command files per tick.
- **Evidence.** ``should_continue = await ipc_handler.process_commands()` then `await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)`; process_commands: `command = self.poll_command(); if not command: return True` (single command per call).`
- **Impact.** Throughput ceiling on Interview commands. Mostly mitigated because batch_interview packs many agents into one command, but ad-hoc single interviews queue and may time out.
- **Fix.**

  ```
  Add an inner drain so each tick processes all currently-pending commands before sleeping, eliminating the per-command 0.5s idle gap (it does not, and cannot, remove the inherent serialization of a single env loop). In the wait loop at backend/scripts/run_parallel_simulation.py:1846-1855, drain until empty before the timed wait:
  
    while not _shutdown_event.is_set():
        # Drain every pending command this tick instead of one-per-0.5s
        drained_any = False
        while not _shutdown_event.is_set():
            if ipc_handler.poll_command() is None:
                break
            should_continue = await ipc_handler.process_commands()
            drained_any = True
            if not should_continue:
                break
        else:
            ...
        if not should_continue:
            break
        # Only idle-wait when the queue was empty this tick
        if not drained_any:
            try:
                await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                break
            except asyncio.TimeoutError:
                pass
  
  Simpler equivalent: have process_commands() loop internally (`while (command := self.poll_command()): handle(command)`) and return False if any command was CLOSE_ENV. Note process_commands re-reads via poll_command, so the extra poll_command() peek above causes a double file read per command; folding the drain into process_commands avoids that. Either way the win is bounded (~0.5s per queued command) and only matters when many ad-hoc single interviews are queued; given LLM-bound latency this is a low-priority polish, not a timeout fix.
  ```
- **Verified.**

  ```
  The mechanism is real and currently true. In run_parallel_simulation.py, ParallelIPCHandler.process_commands() (lines 574-576) calls poll_command() exactly once and returns after handling that single command; poll_command() (lines 263-284) returns only the oldest single command file, not a batch. The wait loop (lines 1846-1855) calls process_commands() once per iteration, then unconditionally executes `await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)`. There is no inner drain that keeps polling until the queue is empty. Therefore N queued single-interview commands are processed serially with a fixed ~0.5s idle gap inserted between each, on top of per-command latency. The root-cause claim and quoted evidence are accurate.
  
  However, the finding's impact is overstated, so I confirm P3 (minor efficiency nit), not higher:
  1. Interviews are LLM-bound. _interview_single_platform invokes the model, and dual-platform interviews already run both platforms concurrently via asyncio.gather (handle_interview line 406). The 0.5s tick is negligible versus multi-second LLM latency.
  2. The "may exceed their timeout" claim is implausible. send_interview defaults to timeout=60.0 and send_batch_interview to 120.0 (simulation_ipc.py lines 194/228). For 0.5s ticks alone to exhaust a 60s budget you would need ~120 queued commands. The actual throughput ceiling comes from inherent single-env serialization (each command must fully complete before the next is even read), which the proposed inner-drain fix does NOT remove — it only eliminates the 0.5s idle gaps. So the fix would not meaningfully prevent timeouts.
  3. The orchestrator's primary path is batch_interview (zep_tools.py / report_agent.py call /api/simulation/interview/batch), which packs all agents into one command = one tick, one response. Single-interview queuing is the exception, not the hot path, which the finding itself acknowledges.
  
  Net: a genuine but minor idle-latency inefficiency, correctly rated P3.
  ```

#### [F-6-8] Live DB read endpoints open SQLite without read-only/timeout while the sim writes the same file; transient locks silently return 0 rows

`P3` · `concurrency` · confidence **medium** · effort **M** · `backend/app/api/simulation.py` : 2018-2039, 2091-2116

- **Symptom.** get_simulation_posts/get_simulation_comments call sqlite3.connect(db_path) with defaults against {platform}_simulation.db while the OASIS subprocess writes it; on a write-lock the query raises OperationalError which is caught and turned into posts=[]/comments=[].
- **Root cause.** sqlite3.connect with defaults (no read-only URI, no explicit busy retry) can hit 'database is locked' against the live writer; the except sqlite3.OperationalError branch conflates 'locked, retry' with 'table missing' and returns empty.
- **Evidence.** ``conn = sqlite3.connect(db_path)` then `except sqlite3.OperationalError: posts = []` (and `comments = []`); runner._get_interview_history_from_db: `conn = sqlite3.connect(db_path)` with no timeout/ro.`
- **Impact.** During an active run the posts/comments panels intermittently show 0 rows instead of real data, misleading the live feed; the interview-history reader has only a broad except and can likewise return empty on a lock.
- **Fix.**

  ```
  Open the DB read-only and explicitly distinguish 'locked' (retryable) from 'no such table' (genuinely empty). Read-only mode prevents the reader from ever taking a write/reserved lock and avoids accidentally creating the file. Example for the posts endpoint (mirror for comments and the interview reader):
  
    import sqlite3, time
    def _read_ro(db_path):
        # mode=ro fails fast if file missing (already guarded by os.path.exists above)
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
  
    conn = _read_ro(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM post ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
        posts = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT COUNT(*) FROM post")
        total = cursor.fetchone()[0]
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no such table" in msg:
            posts, total = [], 0            # genuinely empty / not yet created
        else:
            # 'database is locked' / 'database is busy' -> surface, do not fake empty
            conn.close()
            return jsonify({"success": False, "error": "数据库繁忙，请重试", "retryable": True}), 503
    finally:
        conn.close()
  
  Additionally, set `PRAGMA query_only=ON` after connect if extra safety is wanted, and consider that the writer enabling WAL mode (journal_mode=WAL) on the OASIS side would let readers proceed without blocking writers at all (the most robust fix if the OASIS subprocess can be configured). At minimum, distinguish 'locked' from 'no table' and return a retryable signal instead of an empty-but-success payload, and add a logger.warning on the locked branch (currently the API endpoints log nothing).
  ```
- **Verified.**

  ```
  Verified against the actual code. The OASIS simulation runs as a subprocess.Popen (simulation_runner.py:452) that writes {platform}_simulation.db. Three readers open that same file concurrently with bare sqlite3.connect and no read-only mode:
  
  1) get_simulation_posts (api/simulation.py:2019): `conn = sqlite3.connect(db_path)` then `except sqlite3.OperationalError: posts = []; total = 0` (2035-2037).
  2) get_simulation_comments (api/simulation.py:2092): same pattern, `except sqlite3.OperationalError: comments = []` (2113-2114).
  3) _get_interview_history_from_db (simulation_runner.py:1783): `conn = sqlite3.connect(db_path)` inside `try/except Exception` (1819) that LOGS the error before returning [].
  
  The concurrency premise is genuine: these are explicitly live-feed endpoints (docstrings/messages reference reading while "simulation may not be running yet" / 实时), and the writer holds write locks on the same SQLite file. On a sustained write lock, the query raises OperationalError, which the handler turns into empty results that are indistinguishable from a genuinely empty/absent table — misleading the live posts/comments panels with 0 rows.
  
  Two corrections to the finding that reduce severity from P2 to P3:
  - Python's sqlite3.connect default timeout is 5.0s (not zero), so it already blocks/retries for up to 5s before raising 'database is locked'. OASIS write transactions are typically short, so most lock contention is absorbed; the empty-result symptom only manifests when a write transaction exceeds ~5s. This makes the defect transient/intermittent, not routine.
  - The "silently returns empty" claim is accurate only for the two API endpoints (no logging). The interview-history reader catches `except Exception` and DOES log via logger.error before returning [], so it is not silent.
  
  Impact is a read-only, intermittent UI display glitch (live feed momentarily shows 0 rows); no data loss or corruption, and the next poll typically recovers. Real but low severity, hence P3.
  ```

### x-concurrency — CROSS-CUTTING: lifecycle, concurrency, resource leaks

#### [F-12-7] IPC interview timeout leaves an orphaned command file; server may execute a stale interview after the client already gave up

`P3` · `concurrency` · confidence **medium** · effort **M** · `backend/app/services/simulation_ipc.py` : 157-187, 332-378

- **Symptom.** On client-side timeout, send_command removes the command file (best-effort) and raises TimeoutError, but the SimulationIPCServer poller may have already picked up that command file (or pick it up between the timeout and the remove) and will execute it and write a response that no one consumes — and if the remove loses the race, the command runs after the client abandoned it.
- **Root cause.** File-based IPC with no per-command ownership/lease: the client deletes the request to cancel, but the server reads-then-deletes independently, so there is a TOCTOU window. Orphaned response files accumulate (never garbage-collected) because the consumer already left.
- **Evidence.** `send_command on timeout: os.remove(command_file) (best-effort) then raise TimeoutError; server poll_commands reads first command and send_response writes response then removes command independently.`
- **Impact.** Wasted LLM interview calls after the user navigated away, plus slow accumulation of stale ipc_commands/ipc_responses JSON files in each simulation dir. Low blast radius (interview is an optional manual feature).
- **Fix.**

  ```
  Root cause: file-based IPC with no per-command ownership and no consumer-side cleanup on the cancel path. Fix in two layers:
  
  1) Server claims before executing (simulation_ipc.py SimulationIPCServer.poll_commands AND the duplicated poll_command in run_parallel_simulation.py / run_twitter_simulation.py / run_reddit_simulation.py — all four must change together): atomically os.rename the command file to <command_id>.json.processing before reading/executing, and skip files already marked .processing. This removes the read-then-execute-then-delete TOCTOU and prevents accidental re-pick/re-execution if a later delete loses a race. send_response then removes the .processing file.
  
  2) Client cancels via sentinel, not deletion (send_command timeout path, lines 178-187): instead of os.remove(command_file), write a <command_id>.cancel sentinel and still raise TimeoutError; the server checks for the sentinel before executing and skips/aborts. Keep the existing best-effort remove only as a fallback.
  
  3) Add a TTL sweep of both ipc_commands and ipc_responses (delete files older than, e.g., 2x max timeout) run on SimulationIPCServer.start()/stop() and at the top of each client send_command, so orphaned responses from cancelled commands and stale command files are garbage-collected. At minimum, sweep stale response/command files on simulation start/stop. Because all command_ids are fresh UUIDs, no live consumer is ever harmed by sweeping aged files.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. File-based IPC with no per-command ownership/lease, exactly as claimed.
  
  Client side (backend/app/services/simulation_ipc.py:157-187): send_command polls for response_file until timeout. On timeout it does os.remove(command_file) wrapped in try/except OSError (best-effort, swallowed) and raises TimeoutError. It deletes only the command file, never a response file.
  
  Server side: the real runner is backend/scripts/run_parallel_simulation.py (also run_twitter/reddit_simulation.py), and the module's SimulationIPCServer.poll_commands (lines 332-360) mirrors it. poll_command (lines 263-284) lists command files, sorts by mtime, and reads-then-RETURNS the first parseable command WITHOUT deleting or marking it. process_commands (lines 567-608) then runs the slow interview via `await env.step(...)`, and only afterward send_response (lines 286-305 / module 362-378) writes the response and deletes the command file. So the command file persists on disk throughout execution.
  
  TOCTOU window is real: if the client times out while the server is mid-execution (interview is LLM-backed and slow), the command has already been loaded into server memory and will run to completion; the server then writes a response no one consumes. The client's os.remove may also lose the race. There is no claimed/.processing rename, no command_id dedup set, and no lease.
  
  Orphaned-file accumulation is real: on the timeout path the client never deletes the response file (the read-success cleanup at lines 164-169 is the only place responses are removed). Command_ids are fresh UUIDs per call, so a response left by a timed-out command can never be matched again and lingers permanently in <sim_dir>/ipc_responses/. There is no GC/TTL/startup sweep anywhere.
  
  Severity P3 is correct: interviews are an optional manual feature (call sites simulation_runner.py:1575,1637,1751), low blast radius, impact limited to wasted LLM calls plus slow accumulation of stale JSON files — no corruption or crash. Not a misreading, not guarded by existing try/except, not dead code, not intended behavior.
  ```

### scripts — Simulation scripts (run_parallel/reddit/twitter) + export

#### [F-9-7] export_forum always reports ok=True; OASIS per-action failures are invisible in the demo feed

`P3` · `data-contract` · confidence **medium** · effort **M** · `backend/scripts/export_demo_site_data.py, backend/scripts/run_parallel_simulation.py` : export_demo_site_data.py:107; run_parallel_simulation.py:742-747, 1452-1459

- **Symptom.** Every forum entry's 'ok' flag is True. fetch_new_actions_from_db only reads actions that already succeeded (they are in the trace table) and log_action is always called with the default success=True, so a failed/declined action is never represented.
- **Root cause.** The pipeline derives logged actions from the trace table (success-only) and never threads through OASIS's per-action {success: False, error} return values; log_action's success param defaults True and is never overridden.
- **Evidence.** `export_demo_site_data.py:107 `"ok": bool(e.get("success", True)),`; action_logger.py:51 `success: bool = True`; fetch_new_actions_from_db only emits rows present in the trace table (run_parallel_simulation.py:701-747).`
- **Impact.** Low — the feed is success-only by construction. Mostly a transparency gap: the 'ok' field in forum.json conveys no real signal and could mislead anyone treating it as a success indicator.
- **Fix.** Lowest-effort correct fix: remove the meaningless `ok` field from export_forum's row dict (export_demo_site_data.py:102-108) so consumers aren't misled. If a real signal is desired instead, thread per-action success through the pipeline: capture per-action outcomes from `env.step(...)` results in run_parallel_simulation.py (the manual-action paths at ~1181, ~1368, and the round loops feeding fetch_new_actions_from_db) and pass `success=<actual>` into action_logger.log_action; note that DB-derived (trace-table) actions are inherently success-only, so for those the field would remain trivially True unless OASIS exposes declined-action records. Given that limitation, removing the field is the cleaner, honest fix.
- **Verified.** Confirmed by reading the actual code. (1) export_demo_site_data.py:107 sets `"ok": bool(e.get("success", True))`, defaulting to True. (2) action_logger.py PlatformActionLogger.log_action (line 51) defaults `success: bool = True` and writes it verbatim into actions.jsonl (line 62). (3) I read all 5 log_action call sites in run_parallel_simulation.py (lines 1169, 1354, 1453, 1584, 1683); NONE pass a `success=` argument, so every entry is written with success=True. (4) The DB-derived actions come from fetch_new_actions_from_db, which SELECTs from the OASIS `trace` table (lines 694-699) — only executed/recorded actions — and the appended dict (lines 742-747) carries only agent_id/agent_name/action_type/action_args, with no success/failure flag threaded through. (5) The manually-logged actions (scheduled events at 1169, initial posts at 1354/1584) are logged optimistically BEFORE env.step() runs, so even a failed env.step (caught by the surrounding try/except, e.g. line 1183-1185) leaves the entry recorded as success=True. The standalone run_reddit_simulation.py / run_twitter_simulation.py do not call log_action or write actions.jsonl at all, so the demo feed's `ok` field originates solely from run_parallel_simulation.py and is always True. The finding is a real, currently-true data-contract/transparency defect: the `ok` field conveys no signal. Severity P3 is correct — the feed is success-only by construction, so this misleads anyone treating `ok` as a success indicator but causes no functional break.

#### [F-9-2] Per-round SQLite connection churn: a fresh connect/close every round per platform (N round-trips, no reuse)

`P3` · `bottleneck` · confidence **medium** · effort **M** · `backend/scripts/run_parallel_simulation.py` : 688-749 (fetch_new_actions_from_db opens/closes each call), 1446 + 1676 (called once per round)

- **Symptom.** Each simulation round opens a brand-new sqlite3 connection, runs the trace query plus a cascade of per-action enrichment subqueries, then closes it. With two platforms running in asyncio.gather in one process, this is 2 fresh connections per round for the whole run.
- **Root cause.** fetch_new_actions_from_db creates `conn = sqlite3.connect(db_path)` and `conn.close()` on every invocation, and _enrich_action_context issues one or more additional SELECTs per action (post/comment/user lookups), so action-heavy rounds become N+1 query bursts on a cold connection.
- **Evidence.** `run_parallel_simulation.py:689 `conn = sqlite3.connect(db_path)`; 749 `conn.close()`; called every round at 1446 (twitter) and 1676 (reddit); enrichment subqueries at 786, 810, 822, 881, 899, 962, 980.`
- **Impact.** Unnecessary connection setup cost and repeated query planning every round; under large casts / many rounds this adds measurable wall time and contends with the OASIS writer connection on the same DB file. Not fatal but wasteful and scales poorly.
- **Fix.** Open one read connection per platform once (e.g., right after db_path is computed at line ~1310 / ~1532, guarded by os.path.exists) and pass it into fetch_new_actions_from_db so the same connection/cursor is reused across all rounds; close it after the round loop ends. Concretely, change fetch_new_actions_from_db to accept an existing `conn` (or a long-lived cursor) instead of calling sqlite3.connect/close internally, and drop the per-call open/close at lines 689/749. Since OASIS owns the writer on the same file, open the reader connection lazily (the .db may not exist until env.reset writes the first trace) and treat absence as \"no actions yet.\" Optionally memoize post/comment/user lookups within a single round to collapse the N+1 enrichment bursts, but that is secondary. Keep the surrounding try/except so a transient read failure still returns the accumulated actions and last_rowid rather than aborting the round. Given the P3 impact, this is a low-priority cleanup, not an urgent fix.
- **Verified.** Factually confirmed against the code. backend/scripts/run_parallel_simulation.py:689 opens `conn = sqlite3.connect(db_path)` and :749 closes it on every call to fetch_new_actions_from_db. That function is invoked once per round inside the per-round loops at :1446 (twitter, loop at :1399) and :1676 (reddit, loop at :1629), and both platforms run concurrently via asyncio.gather (:1807), so there are 2 fresh connect/close cycles per round. The N+1 enrichment claim is also accurate: _enrich_action_context plus _get_post_info/_get_comment_info/_get_user_name issue additional per-action SELECTs (:786, :810, :822, :881, :899, :962, :980), so action-heavy rounds become query bursts on a freshly-opened connection.\n\nHowever, the P2 severity is overstated. The default round count is modest: total_rounds = (total_simulation_hours*60)//minutes_per_round = (72*60)//60 = 72 rounds per platform (:1385-1387, :1615-1617), so ~72 connection opens per platform per run, not thousands. More importantly, the dominant per-round cost is `await result.env.step(actions)` (:1438, :1668), which fans out one LLM completion per active agent (seconds to minutes); a local-file SQLite connection open is sub-millisecond and the enrichment lookups are indexed primary-key/foreign-key reads. The connection setup and re-planning cost is therefore a rounding error against LLM inference time, and the \"contends with the OASIS writer connection\" claim is speculative — env.step completes before the read fires, and SQLite tolerates concurrent file access. This is genuine wasteful churn that scales poorly in principle but has negligible measured impact in this LLM-bound pipeline, so it is a P3 efficiency/cleanliness issue, not a P2 bottleneck.\n\nNot a misreading, not dead code, not guarded away — the defect exists exactly as described, only its impact is smaller than claimed.

#### [F-9-6] action_logger total_rounds metadata is hardcoded as hours*2, ignoring minutes_per_round

`P3` · `correctness` · confidence **high** · effort **S** · `backend/scripts/action_logger.py` : 98, 271

- **Symptom.** log_simulation_start writes `total_rounds = total_simulation_hours * 2` regardless of the configured minutes_per_round, so the reported total_rounds is wrong whenever minutes_per_round != 30.
- **Root cause.** Hardcoded `config.get("time_config", {}).get("total_simulation_hours", 72) * 2`. The real round count is (total_hours*60)//minutes_per_round (as computed in the runner scripts), and is further capped by max_rounds.
- **Evidence.** `action_logger.py:98 `"total_rounds": config.get("time_config", {}).get("total_simulation_hours", 72) * 2,` (duplicated at 271 in the legacy ActionLogger).`
- **Impact.** The simulation_start event advertises a misleading total_rounds (off by a factor whenever minutes_per_round differs from 30, and never reflects max-rounds truncation). Any consumer reading the start-event total_rounds gets a wrong denominator; the authoritative value is the simulation_end event, so impact is limited but the field is plainly incorrect.
- **Fix.**

  ```
  The start event is logged before total_rounds is computed in the runners (log_simulation_start at run_parallel_simulation.py:1327, but total_rounds is computed at line 1387), so the cleanest fix is to make the logger compute total_rounds with the same formula and default the runners use, deriving from minutes_per_round instead of hardcoding *2.
  
  In action_logger.py, replace the hardcoded line in BOTH PlatformActionLogger.log_simulation_start (line 98) and the legacy ActionLogger.log_simulation_start (line 271) with a computation that mirrors the runner formula and its default of 60:
  
      time_config = config.get("time_config", {})
      total_hours = time_config.get("total_simulation_hours", 72)
      minutes_per_round = time_config.get("minutes_per_round", 60)
      total_rounds = (total_hours * 60) // minutes_per_round if minutes_per_round else 0
  
  then set `"total_rounds": total_rounds` in the entry dict.
  
  Note this still cannot reflect max_rounds truncation, since max_rounds is not in config and the start event is emitted before truncation is applied. If the truncated value is needed at start time, prefer the stronger fix: add an optional `total_rounds` parameter to log_simulation_start and have the runners compute total_rounds (including the max_rounds min()) before calling it, then pass it in — i.e. move the log_simulation_start call to after the total_rounds computation block (after run_parallel_simulation.py:1387-1394). Either fix removes the incorrect *2 factor; the parameter-passing variant is most accurate because it also captures max_rounds truncation and reuses the single authoritative computation. Because no consumer currently reads this field, dropping it from the start event entirely is also acceptable and lowest-risk.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. In backend/scripts/action_logger.py, both log_simulation_start methods hardcode total_rounds as `config.get("time_config", {}).get("total_simulation_hours", 72) * 2` (line 98 in PlatformActionLogger, line 271 in legacy ActionLogger). The `* 2` is equivalent to assuming minutes_per_round=30, but the authoritative round count used everywhere else is `(total_hours * 60) // minutes_per_round` with a DEFAULT minutes_per_round of 60 (run_parallel_simulation.py:1386-1387, run_twitter_simulation.py:522-525, simulation_runner.py:362). So even under the default config the start-event value is 2x too large (e.g. 72h -> reports 144 vs. true 72), and it diverges further for any other minutes_per_round. It also ignores max_rounds truncation (run_parallel_simulation.py:1390-1394). PlatformActionLogger.log_simulation_start is actively called at run_parallel_simulation.py:1327 and :1549, so this code path is live (not dead).
  
  Severity is correctly P3 (cosmetic/diagnostic), confirmed by tracing consumers: (1) export_demo_site_data.py explicitly skips records with event_type, including simulation_start (lines 98-99), so it never reads this field; (2) the API and SimulationRunner read total_rounds from run_state.json, which is computed correctly via the proper formula in simulation_runner.py:362, not from the JSONL start event; (3) the simulation_end event (action_logger.py:105/278) is written with the correct total_rounds passed in by the runner (run_parallel_simulation.py:1473/1703). No current consumer reads the start-event total_rounds, so the bug is a plainly-wrong-but-unused metadata field — limited real-world impact, matching P3.
  ```

### memory — Graph memory readers/updaters/tools

#### [F-4-6] search_graph swallows all exceptions and silently downgrades to keyword search

`P3` · `robustness` · confidence **medium** · effort **S** · `backend/app/services/zep_tools.py` : 502-557

- **Symptom.** Any exception from the (already retried) graph.search call — including programming errors or schema/None bugs — is caught broadly and the function quietly falls back to local substring keyword matching, returning lower-quality results with no signal to the caller.
- **Root cause.** The `except Exception as e:` around the semantic search path logs a warning and unconditionally calls _local_search, so real defects are masked as 'degraded but working'.
- **Evidence.** `except Exception as e:\n logger.warning(f"Zep Search API失败，降级为本地搜索: {str(e)}")\n return self._local_search(graph_id, query, limit, scope)`
- **Impact.** Persistent search regressions are invisible: the report agent keeps receiving keyword-matched facts that look plausible but miss semantic hits, and no error is propagated for diagnosis.
- **Fix.** Add a `degraded: bool = False` field to the SearchResult dataclass (and surface it in to_dict/to_text, e.g. a "(降级搜索)" marker) so callers can detect persistent fallback. In search_graph, set degraded=True on the _local_search result. Critically, do not catch bare `Exception` in a way that masks defects: import the Zep client error hierarchy and the existing helpers (is_zep_rate_limit_error already imported) and only fall back for expected backend/connection/rate-limit/server errors — e.g. `except (zep_client_errors..., ConnectionError, TimeoutError) as e: ... fallback` while letting `except Exception` either re-raise or, at minimum, `logger.error(..., exc_info=True)` and increment a metric/counter before any fallback. Concretely, since _call_with_retry already exhausts retries for transient errors, the simplest robust change is: keep the fallback only when `is_zep_rate_limit_error(e)` or the exception is a known network/server type; otherwise log at ERROR with exc_info and re-raise so programming/schema bugs become visible. This preserves graceful degradation for genuine API outages while making real regressions diagnosable.
- **Verified.** Confirmed by reading the actual code. In backend/app/services/zep_tools.py, search_graph (lines 477-557) wraps the entire semantic-search path in `try ... except Exception as e:` (line 554) that logs only `logger.warning("Zep Search API失败，降级为本地搜索: ...")` and unconditionally returns `self._local_search(...)`, a substring/keyword matcher (lines 559-661, scoring at 590-603). The retry layer `_call_with_retry` (lines 444-475) already catches and retries every `Exception` and re-raises `last_exception`, so the outer broad `except` only fires after retries are exhausted OR on a programming/schema error in the result-parsing block (lines 514-552). Because the catch is bare `Exception`, genuine defects (AttributeError, TypeError, None bugs) in parsing or the API client are masked as a successful downgrade. The `SearchResult` dataclass (lines 27-43) has no `degraded`/`fallback` field, and `to_dict`/`to_text` carry no signal, so callers (api/report.py:969, internal callers at lines 866/956/1040/1055/1305) cannot distinguish high-quality semantic hits from keyword fallbacks. The symptom, root cause, and claimed impact are all accurate and currently true; not dead code, not intended-and-guarded, not a misreading. Severity P3 is correct: this is an observability/robustness concern, not a crash or correctness failure — the parsing is itself defensive (hasattr/getattr), the system keeps working, and the fallback produces plausible-but-degraded results rather than wrong data. The audit's proposed fix is sound and I tighten it below to be concrete for this codebase.

### graph-build — Graph builder + ontology

#### [F-3-5] build_graph_async / _build_graph_worker is divergent dead code that omits actor seeding, reference_time, and community detection

`P3` · `correctness` · confidence **high** · effort **M** · `backend/app/services/graph_builder.py` : 67-199  ·  ↺ overlaps EXECPLAN

- **Symptom.** The public build_graph_async path (and its _build_graph_worker) builds a graph WITHOUT seeding researched actors/relationships, WITHOUT passing reference_time to add_text_batches (so all chunks anchor to ingestion time, breaking the bi-temporal active/historical split the EXECPLAN golden thread relies on), and WITHOUT community detection. It has no live caller; the real pipeline path is in pipeline_orchestrator.py and api/graph.py's inline build_task.
- **Root cause.** An older end-to-end worker was left in place after the orchestrator took over building. It still wires _wait_for_episodes (now a no-op) and _get_graph_info, diverging from the maintained path.
- **Evidence.** `Lines 146 `chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)` then 155 `episode_uuids = self.add_text_batches(graph_id, chunks, batch_size, ...)` — no reference_time arg and no seed_actors call, unlike pipeline_orchestrator.py:1513/1525. grep shows no external caller of build_graph_async.`
- **Impact.** Maintenance/correctness trap: any future caller (or test) that uses build_graph_async gets a graph missing the golden-thread seeding and bi-temporal anchoring, silently producing a degraded graph. No current runtime impact since it is unreferenced externally.
- **Fix.** Delete build_graph_async and _build_graph_worker (graph_builder.py:67-199); they are unreferenced dead code and removing them eliminates the trap with zero behavioral change to live paths. If instead you want consistency across build paths, factor the orchestrator's GRAPH-stage body (seed_actors + add_text_batches with reference_time + build_communities, gated by Config.GRAPH_SEED_FROM_ACTORS / Config.GRAPH_BUILD_COMMUNITIES) into one reusable GraphBuilderService.build_graph(...) method and call it from BOTH the orchestrator AND api/graph.py's inline build_task (which currently has the identical omissions at lines 435-439) — not only from this dead method. The minimal, lowest-regret action for a P3 is deletion.
- **Verified.** Every factual claim verified against the code. backend/app/services/graph_builder.py:67-199 defines build_graph_async/_build_graph_worker, an end-to-end build path that calls create_graph -> set_ontology -> split_text -> add_text_batches -> _wait_for_episodes -> _get_graph_info. It does NOT call seed_actors and does NOT call build_communities, and at line 155 it calls add_text_batches WITHOUT reference_time (so it defaults to None). The add_text_batches docstring (line 365) confirms reference_time defaults to ingestion time ("缺省 = 落库时刻"), which is exactly the bi-temporal valid anchor the active/historical split relies on. The maintained path in pipeline_orchestrator.py confirms the divergence: seed_actors at 1513, reference_time=as_of passed at 1525, build_communities at 1538, with comments (1506-1509) stating as_of provides "a real bi-temporal axis for panorama_search's active/historical split." _wait_for_episodes is indeed now a no-op (lines 413-429: it only fires a progress callback and returns; the polling loop was deleted). A repo-wide grep across .py/.md/.json found ZERO external references to build_graph_async — it is genuinely dead code with no live caller. So the finding is factually accurate and currently true as a latent maintenance/correctness trap, but with NO current runtime impact (the finding itself correctly states this). P3 is the right severity: a real but dormant defect, not a phantom and not actively degrading any run. Note: api/graph.py's inline build_task (lines 374-457) is ALSO divergent in the same way (add_text_batches at 435 has no reference_time, and no seed_actors/build_communities) — but that is a separate live-path concern, not the dead method this finding targets.

### core-utils — Config, LLM clients, utils, settings API, app entry

#### [F-8-6] Rotating log filename frozen at import time; date never rolls

`P3` · `robustness` · confidence **high** · effort **S** · `backend/app/utils/logger.py` : 67-73

- **Symptom.** The per-day log file name is computed once with datetime.now() when setup_logger first runs, so a long-running server keeps writing to the day-0 file forever; days 1..N never get their own dated file.
- **Root cause.** log_filename = datetime.now().strftime('%Y-%m-%d') is evaluated a single time and bound to a RotatingFileHandler (which rotates by size, not date). The 'by date' naming implied by the code/comment never happens after the first day.
- **Evidence.**

  ```
  log_filename = datetime.now().strftime('%Y-%m-%d') + '.log'
  file_handler = RotatingFileHandler(os.path.join(LOG_DIR, log_filename), maxBytes=10*1024*1024, backupCount=5, ...)
  ```
- **Impact.** Multi-day pipeline runs (research can take tens of minutes to hours, sims multi-round) all land in one mislabeled day's file; size-based rotation also discards old segments, making post-hoc debugging across days confusing.
- **Fix.**

  ```
  Replace the size-based RotatingFileHandler plus once-computed dated filename with a TimedRotatingFileHandler that rotates daily, which both matches the "by date" intent and re-dates rolled files automatically:
  
      from logging.handlers import TimedRotatingFileHandler
      file_handler = TimedRotatingFileHandler(
          os.path.join(LOG_DIR, 'mirofish.log'),
          when='midnight',
          backupCount=30,
          encoding='utf-8',
      )
      file_handler.suffix = '%Y-%m-%d'   # rolled files become mirofish.log.YYYY-MM-DD
  
  This gives true daily files (active file is mirofish.log; previous days are dated) and bounds retention by days rather than size. If a per-day name AND a size cap are both desired, layer your own logic, but TimedRotatingFileHandler(when='midnight') is the minimal, intent-matching fix. Alternatively, if size-based rotation is genuinely preferred, use a single fixed name (e.g. 'mirofish.log') so the filename is honestly not date-based and remove the misleading 按日期命名 comment.
  ```
- **Verified.** Confirmed by reading backend/app/utils/logger.py:66-73. The code is exactly as quoted: `log_filename = datetime.now().strftime('%Y-%m-%d') + '.log'` is computed once and passed to a size-based `RotatingFileHandler(maxBytes=10MB, backupCount=5)`. A RotatingFileHandler rotates by size, never re-evaluating its filename, so the date in the name is frozen at handler-creation time. The handler is created exactly once per logger: setup_logger() short-circuits via `if logger.handlers: return logger` (line 52) and get_logger() reuses any logger that already has handlers (lines 101-104). The module-level `logger = setup_logger()` (line 108) runs at import, freezing the default logger's filename at process import time. The Chinese comment 按日期命名 (line 66, "named by date") documents an intent of daily files that the code does not deliver after day 0. So in a long-running FastAPI server, all output for days 1..N keeps landing in the day-0 dated file. This is a true, currently-present defect, not a misreading or guarded behavior. Severity P3 is correct: logging still functions and size-rotation still works; the only harm is a misleading filename and cross-day log conflation, which is a robustness/observability nuisance rather than a correctness or availability bug. Note one nuance to the claimed impact: the audit says "size-based rotation also discards old segments, making post-hoc debugging across days confusing" — backupCount=5 does cap retention at ~60MB, but that retention cap is the intended size-rotation behavior and is independent of the date-freezing bug; the core defect (date never rolls) stands on its own.

#### [F-8-8] events_to_schedule uses banker's rounding + degenerate horizon → distorted event timeline

`P3` · `correctness` · confidence **medium** · effort **S** · `backend/app/utils/actors.py` : 437-453

- **Symptom.** Round assignment uses Python's round() (round-half-to-even) on span/hz*total_rounds, and the horizon hz can collapse to 1 day when there is a single near-future event, mapping that event to the last round while a 0-day event maps to round 0.
- **Root cause.** hz = horizon_days or (max(spans) if spans else 1) or 1. When all key_events are at/just-after as_of, hz becomes 1, so any event >0 days out is multiplied to >= total_rounds and clamped to the final round; with multiple clustered events the half-to-even rounding produces non-monotone bucketing.
- **Evidence.**

  ```
  hz = horizon_days or (max(spans) if spans else 1) or 1
  ...
  "round": min(total_rounds - 1, round(span / hz * total_rounds)),
  ```
- **Impact.** Event timeline injected into the simulation can be temporally distorted (catalysts firing at the very end or all bunched), weakening the realism the EXECPLAN golden-thread relies on. Non-fatal (helper never throws) but degrades sim fidelity on tight horizons.
- **Fix.** Keep round() (it is already monotone; floor is unnecessary and the banker's-rounding concern is a non-issue). Fix only the degenerate-horizon collapse so a tightly clustered event window does not get artificially stretched across the whole round range. Enforce a sensible minimum absolute horizon and clamp explicitly, e.g. replace line 437 with `hz = horizon_days or (max(spans) if spans else 0); hz = max(hz, total_rounds)` (or another floor like a configured minimum-days), so when all events fall within a few days the early events stay in early rounds instead of one event jumping to the last round. Add a unit check for clustered near-term events (spans like [0,1] and [1,2,3]) asserting events are not all pushed to the final round and that ordering is preserved. Drop the non-monotone-bucketing rationale from the report.
- **Verified.** Partially confirmed at backend/app/utils/actors.py:437,450. The degenerate-horizon artifact is real: `hz = horizon_days or (max(spans) if spans else 1) or 1` with horizon_days never supplied at the only call site (simulation_config_generator.py:528 passes 3 positional args, so horizon_days stays None). When events cluster near as_of, e.g. spans=[0,1] with total_rounds=10, hz collapses to 1 and the round mapping yields [0, 9]: a 1-day-out event is clamped to the very last round while rounds 1-8 stay empty. I reproduced this and the milder spread distortion (spans=[1,2,3] -> rounds [3,7,9]). This degrades the realism of injected catalyst timing the EXECPLAN relies on, and the helper never throws, so impact is fidelity-only and bounded — P3 is correct.\n\nHowever two sub-claims in the finding are WRONG: (1) "banker's rounding produces non-monotone bucketing" is false — `s/hz*total_rounds` is monotone non-decreasing in span and `min()` preserves that, so round-half-to-even can only create ties between distinct spans, never order inversions (verified across 2000 random cases). Switching round() to floor would not fix anything monotonicity-related. (2) The root cause is not banker's rounding at all; it is that hz is normalized to the event cluster (max span) rather than an absolute calendar horizon. Note also that this cluster-relative normalization is the DOCUMENTED intended behavior (docstring lines 411-414: horizon defaults to "days from as_of to the farthest future event"), so "farthest event lands on the last round" is by design; only the hz=1 edge artifact is a genuine quirk. Net: a real but minor, partly-as-designed P3 fidelity issue, not a correctness defect.

### graph-shim — Graphiti shim (Zep-compatible) + FalkorDB driver

#### [F-2-7] Embedder truncates vectors AFTER normalization, breaking unit-norm on model/dim mismatch

`P3` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/graphiti_client/embedder.py` : 63-71

- **Symptom.** If GRAPHITI_EMBED_MODEL is changed to a model whose native dimension > GRAPHITI_EMBED_DIM (or the env defaults don't match the model), embeddings are sliced to embedding_dim, yielding non-unit vectors and mismatched dimensions vs the frozen EMBEDDING_DIM index.
- **Root cause.** _encode calls model.encode(..., normalize_embeddings=True) then does `vec = [float(x) for x in row][: self.embedding_dim]`. Truncating a normalized vector de-normalizes it, and silently producing the wrong dimension corrupts cosine search / index alignment instead of failing loudly.
- **Evidence.** `embeddings = model.encode(texts, normalize_embeddings=True, ...) ... vec = [float(x) for x in row][: self.embedding_dim]`
- **Impact.** Degraded or wrong similarity search if a non-default model is configured without matching GRAPHITI_EMBED_DIM; failure is silent.
- **Fix.**

  ```
  In `LocalSentenceTransformerEmbedder._ensure_model` (after loading the model), validate the model's real output dimension against the configured `embedding_dim` and the frozen index dim, raising a clear error on mismatch instead of silently truncating:
  
  ```python
  self._model = SentenceTransformer(self.model_name)
  native_dim = self._model.get_sentence_embedding_dimension()
  if native_dim != self.embedding_dim:
      raise RuntimeError(
          f"Embedding model '{self.model_name}' produces {native_dim}-dim "
          f"vectors but GRAPHITI_EMBED_DIM/EMBEDDING_DIM is {self.embedding_dim}. "
          f"Set GRAPHITI_EMBED_DIM to {native_dim} (and ensure it matches the "
          f"frozen EMBEDDING_DIM index) or choose a matching model."
      )
  ```
  
  Then drop the `[: self.embedding_dim]` slice in `_encode` (it is now provably a no-op):
  ```python
  vec = [float(x) for x in row]
  ```
  
  This fails loudly at first model load rather than silently corrupting cosine search with de-normalized/wrong-dim vectors. The alternative of re-normalizing after slicing is inferior — truncating a semantic embedding is meaningless regardless of norm and still mismatches the frozen index dim, so loud validation is the correct hardening. P3 severity is appropriate.
  ```
- **Verified.**

  ```
  Confirmed against the actual code at /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/embedder.py:63-71. `_encode` does `embeddings = model.encode(texts, normalize_embeddings=True, ...)` then `vec = [float(x) for x in row][: self.embedding_dim]`. The quoted evidence is accurate.
  
  The two technical claims hold:
  1. Truncating a unit-normalized vector de-normalizes it (dropped components reduce the L2 norm below 1) — mathematically true.
  2. There is NO dimension validation anywhere. `__init__.py:22` just sets `EMBEDDING_DIM = GRAPHITI_EMBED_DIM` (the frozen index dim) from env; nothing cross-checks it against the model's real `get_sentence_embedding_dimension()`. So on a model/dim mismatch the embedder silently emits a wrong-dimension and/or non-unit vector that diverges from the frozen index, instead of failing loudly. The opposite mismatch (native dim < embedding_dim) is also unguarded: the slice is a no-op and a too-short vector is returned silently.
  
  Reachability/severity: The default and only documented/supported config is model `paraphrase-multilingual-MiniLM-L12-v2` (384-native) with `GRAPHITI_EMBED_DIM=384` (config.py:265-266, README, .env.example). On that path the slice `[:384]` is a no-op — fully correct, no de-normalization. The defect only manifests when a user sets a larger model without matching `GRAPHITI_EMBED_DIM`, which README (EN+zh) and the config comment explicitly warn against ("must match GRAPHITI_EMBED_MODEL"). So this is a latent robustness gap under documented user-misconfiguration, not a normal-operation bug. That justifies the P3 rating — real but low impact, silent rather than data-loud, and avoidable via docs.
  
  This is not a misreading, not dead code, and not already guarded.
  ```

#### [F-2-8] FalkorDriver constructor schedules an unawaited concurrent index build (redundant + orphan-task)

`P3` · `robustness` · confidence **medium** · effort **S** · `backend/app/services/graphiti_client/runtime.py` : 181-205

- **Symptom.** Constructing the driver on the running loop fires a fire-and-forget build_indices_and_constraints task that races the explicit `await g.build_indices_and_constraints()` in _ensure_graph; its exceptions are never retrieved ('Task exception was never retrieved').
- **Root cause.** SanitizingFalkorDriver inherits FalkorDriver.__init__, which does `loop.create_task(self.build_indices_and_constraints())` when an event loop is running (falkordb_driver.py:177-184). _make_driver runs on the bg loop, so this always fires, then _ensure_graph also awaits a build. Two concurrent builds on a brand-new graph; conflicts are masked by the 'already indexed' handler but the orphan task's errors are dropped.
- **Evidence.** `_ensure_graph: await g.build_indices_and_constraints()  AND base __init__: loop.create_task(self.build_indices_and_constraints())`
- **Impact.** Redundant work and noisy/lost asyncio warnings; in the worst case a transient index error during build is silently discarded by the orphan task rather than surfaced.
- **Fix.**

  ```
  Fix lives entirely in this repo's subclass (no upstream patch needed). The base ctor only schedules the task and never stores a handle, and _ensure_graph already performs the build explicitly with error handling, so suppress the parent's auto-task and rely solely on the single explicit await.
  
  Add an __init__ override to SanitizingFalkorDriver (backend/app/services/graphiti_client/falkor_driver.py) that runs the parent initialization without the fire-and-forget build. The most surgical, low-risk form temporarily neutralizes the auto-call during super().__init__():
  
  class SanitizingFalkorDriver(FalkorDriver):
      def __init__(self, *args, **kwargs):
          # The base FalkorDriver.__init__ schedules build_indices_and_constraints()
          # as an unreferenced loop.create_task when a running loop is present
          # (falkordb_driver.py:177-184). GraphitiRuntime._ensure_graph already awaits
          # the build explicitly with error handling, so the auto-task is redundant and
          # drops its exceptions ("Task exception was never retrieved"). Suppress it so
          # indices are built exactly once with errors observed.
          import asyncio as _asyncio
          _orig_create_task = _asyncio.AbstractEventLoop.create_task
          # simplest robust approach: shadow the instance method during construction
          self.__dict__["build_indices_and_constraints"] = _noop_coro
          try:
              super().__init__(*args, **kwargs)
          finally:
              self.__dict__.pop("build_indices_and_constraints", None)
  
  (where _noop_coro is `async def _noop_coro(*a, **k): return None`). After construction the real bound method is restored, so the explicit await g.build_indices_and_constraints() in _ensure_graph runs normally and exactly once.
  
  Alternatively (equivalently safe): construct the driver off the running loop, or capture/await the scheduled task and log its result. Either way, index creation should happen exactly once with errors observed rather than silently dropped.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code at the pinned version (graphiti-core==0.29.2, matching both the installed venv copy and graphiti-0.29.2/ source).
  
  1) SanitizingFalkorDriver (backend/app/services/graphiti_client/falkor_driver.py:83-99) defines NO __init__, so it inherits FalkorDriver.__init__, which at .venv/.../graphiti_core/driver/falkordb_driver.py:177-184 does loop.create_task(self.build_indices_and_constraints()) whenever asyncio.get_running_loop() succeeds. The cited line range (177-184) matches exactly.
  
  2) The driver is always constructed inside _make_driver (runtime.py:121-152), awaited from _ensure_graph (runtime.py:181) on the persistent background event loop (run() -> run_coroutine_threadsafe to self._loop). So get_running_loop() always succeeds and the fire-and-forget task ALWAYS fires.
  
  3) _ensure_graph then explicitly does await g.build_indices_and_constraints() (runtime.py:205). Two builds therefore run on the same single-threaded loop and interleave at each `await execute_query`.
  
  4) The orphan Task is never stored or awaited anywhere (grep of the graphiti_client dir found no create_task/all_tasks handling), so its exception is never retrieved -> genuine "Task exception was never retrieved" asyncio warning.
  
  5) Critically, the FalkorDB index queries are NOT idempotent: graph_queries.py:30-49 emit bare `CREATE INDEX FOR ... ON ...` and :98-119 emit `CALL db.idx.fulltext.createNodeIndex(...)` with NO `IF NOT EXISTS` (unlike the Neo4j/Kuzu branches). So whichever build loses the race raises "index already exists". The explicit path catches it at runtime.py:206 and logs at debug ("indices may already exist; non-fatal"); the orphan path drops it.
  
  This precisely confirms both the redundant-double-build mechanism and the masked/dropped-exception behavior described. Severity P3 is correct: happy-path indices still get created (union of both builds), explicit errors are caught; impact is redundant work, noisy/lost asyncio warnings, and the worst case where a transient non-"already-exists" error in the orphan task is silently discarded rather than surfaced. Not dead code, not already-guarded (the guard only covers the explicit await, not the orphan task), not intended behavior.
  ```

#### [F-2-9] _shutdown stops the loop but never closes the embedded FalkorDB / leaves redislite subprocess

`P3` · `robustness` · confidence **low** · effort **M** · `backend/app/services/graphiti_client/runtime.py` : 506-510

- **Symptom.** On interpreter exit the atexit hook only calls loop.stop(); it does not close self._falkor_client or any cached Graphiti, so the embedded redislite-spawned Redis subprocess and the loop thread are torn down abruptly.
- **Root cause.** _shutdown does `self._loop.call_soon_threadsafe(self._loop.stop)` and nothing else — no await of g.close()/client.aclose() (and it couldn't easily, since it must not block, but there's no orderly drain either).
- **Evidence.** `def _shutdown(self): try: self._loop.call_soon_threadsafe(self._loop.stop) except Exception: pass`
- **Impact.** Possible orphaned redislite server process / stale lock or .db files between runs, and pending writes not flushed cleanly. Mostly a hygiene/resource-leak concern, not a correctness break.
- **Fix.**

  ```
  In `_shutdown`, before stopping the loop, schedule a best-effort, time-bounded orderly drain on the bg loop that (a) closes each cached Graphiti once, (b) closes the shared `_falkor_client` exactly once, then (c) stops the loop and joins the thread briefly. Guard with an idempotency flag so it runs only once, and never raise on teardown. Sketch:
  
  ```python
  def _shutdown(self):  # pragma: no cover - process teardown
      if getattr(self, "_shutdown_done", False):
          return
      self._shutdown_done = True
      try:
          fut = asyncio.run_coroutine_threadsafe(self._aclose_all(), self._loop)
          try:
              fut.result(timeout=5)
          except Exception:
              pass
      except Exception:
          pass
      finally:
          try:
              self._loop.call_soon_threadsafe(self._loop.stop)
          except Exception:
              pass
          try:
              self._thread.join(timeout=2)
          except Exception:
              pass
  
  async def _aclose_all(self):
      for g in list(self._graphs.values()):
          try:
              await g.close()
          except Exception:
              pass
      self._graphs.clear()
      client = self._falkor_client
      self._falkor_client = None
      if client is not None:
          try:
              await client.close()   # AsyncFalkorDB.close() -> AsyncRedis.close() -> _cleanup() (verified to kill the subprocess)
          except Exception:
              pass
  ```
  
  Notes: keep it best-effort and bounded (a hung loop must not block interpreter exit — the 5s/2s timeouts ensure that). Because the loop thread is already a daemon, the orphaned-subprocess hygiene is the real win here; the explicit `client.close()` is what actually terminates the redislite redis-server (verified empirically). Optionally also clear `_ontologies`.
  ```
- **Verified.**

  ```
  Confirmed by reading the code AND an empirical repro. runtime.py:506-510 `_shutdown` only does `self._loop.call_soon_threadsafe(self._loop.stop)` inside try/except — it never closes `self._falkor_client` nor any cached Graphiti, and registers no other teardown. The embedded redis-server subprocess (started lazily in `_get_falkor_client`, runtime.py:112-119 via `AsyncFalkorDB(dbfilename=...)`) is owned by redislite's underlying SyncRedis. Crucially, the async wrapper sets `self._sync_client._async_managed = True` (async_client.py:67), and redislite's `Client._cleanup` explicitly SKIPS shutdown when `_async_managed` is set (client.py:108-111), deferring teardown to an explicit `await db.close()`. The async wrapper registers NO atexit hook of its own. Since `_shutdown` never calls `close()`, redislite's own atexit/`__del__` cleanup is the only candidate, and it is intentionally neutered by the `_async_managed` flag.
  
  Empirical verification: I ran a script that creates AsyncFalkorDB, runs a query, then exits WITHOUT close() (mirroring `_shutdown`). The redis-server pid was STILL RUNNING after interpreter exit (orphaned). A second script that calls `await db.close()` terminated the subprocess cleanly. So the proposed fix (orderly close before stopping the loop) is effective.
  
  No mitigation exists elsewhere: there is no FastAPI lifespan / on_event("shutdown") that closes the runtime; `delete_graph` (the only path that calls `_falkor_client.select_graph(...).delete()` / `g.close()`) is invoked only on explicit user graph deletion, never on process exit. `_make_driver` for the falkordblite backend wraps this one shared client (runtime.py:135-139), so a single orphaned server backs all graphs.
  
  Severity P3 is correct: this is a resource-hygiene/leak concern (orphaned redis-server process, stale temp dir / unflushed writes), not a correctness break. Impact is bounded — the loop thread is a daemon, redislite uses a tempfile.mkdtemp dir, and a full shell exit usually reaps the orphan — but a long-lived/embedding parent process that re-imports or respawns can accumulate orphaned servers and leak the loop thread.
  
  The finding's mechanism, file:line, quoted evidence, and impact are all accurate. is_real=true, severity P3.
  ```

### report — Report agent + API

#### [F-7-8] plan_outline fallback returns 3 sections, violating the prompt's mandated minimum of 5 (and bypasses research grounding)

`P3` · `correctness` · confidence **medium** · effort **S** · `backend/app/services/report_agent.py` : 1436-1447 (plan_outline except branch)

- **Symptom.** If chat_json raises (after its own internal retry), the report silently degrades to a fixed 3-section, generic outline despite the system prompt requiring 5-8 sections.
- **Root cause.** The except branch hardcodes a 3-section ReportOutline as the fallback, with no research background and titles unrelated to the actual scenario/situation_brief.
- **Evidence.** `return ReportOutline(title="未来预测报告", summary=..., sections=[ReportSection(title="预测场景与核心发现"), ReportSection(title="人群行为预测分析"), ReportSection(title="趋势展望与风险提示")])  # only 3`
- **Impact.** Degraded, off-spec reports on planning failure; downstream section generation proceeds on a generic skeleton, masking the planning failure as a (poor) success rather than surfacing it.
- **Fix.**

  ```
  Build the fallback from self.simulation_requirement and produce >=5 sections so it satisfies the prompt's minimum and at least references the actual scenario. Replace lines 1438-1447 with something like:
  
      logger.error(f"大纲规划失败: {str(e)}")
      # 兜底大纲：至少5章以符合系统提示的章节下限，并据真实需求设题，避免泛化退化。
      req = (self.simulation_requirement or "目标场景").strip()
      return ReportOutline(
          title=f"未来预测报告：{req[:40]}",
          summary=f"针对「{req[:80]}」的模拟预测趋势与风险分析（注：大纲自动规划失败，已使用兜底结构）。",
          sections=[
              ReportSection(title="预测场景与核心发现", description=f"概述「{req[:60]}」的预测目标与最关键发现。"),
              ReportSection(title="关键群体与行为预测分析", description="分析核心人群/主体在该情景下的行为与博弈。"),
              ReportSection(title="关键变量与涌现信号", description="识别驱动趋势演化的关键变量与早期信号。"),
              ReportSection(title="趋势展望与情景演化", description="给出中短期趋势展望与可能的演化路径。"),
              ReportSection(title="风险提示与应对建议", description="列出主要风险与可行的应对/缓解建议。"),
          ],
      )
  
  Additionally, surface the failure rather than masking it: set a warning flag on the outline (or emit a clearly visible log/event via progress_callback) so the report is marked as having fallen back to a degraded skeleton. Optionally retry plan_outline once before falling back. This keeps the change minimal while satisfying the >=5-section spec and the scenario-grounding requirement.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. In backend/app/services/report_agent.py the plan_outline except branch (lines 1436-1447) returns a hardcoded ReportOutline with exactly 3 ReportSections ("预测场景与核心发现", "人群行为预测分析", "趋势展望与风险提示"), no descriptions, and titles unrelated to the actual scenario. The system prompt (lines 575, 592, 614: "最少5个章节，最多8个章节" / "sections数组最少5个") mandates a minimum of 5 sections, so the fallback violates the spec.
  
  The branch is genuinely reachable. llm_client.py chat_json (lines 122-136) already does its own 2-attempt retry with local JSON repair and then raises ValueError; self.chat can also raise on API/network errors. The broad `except Exception as e` catches all of these, so on repeated planner failure the report silently degrades to the generic 3-section skeleton, and downstream _generate_section proceeds on it — masking the planning failure as a poor success rather than surfacing it. All quoted evidence and the root-cause claim check out.
  
  Severity P3 is correct: this is a graceful-degradation fallback that only triggers after chat_json's own retry has already failed, so it is an edge case, not a primary-path defect. The available self.simulation_requirement (used at lines 1355/1372) lets the fallback be made scenario-aware and >=5 sections at no extra cost.
  ```

#### [F-7-9] Redundant save_report and double persistence between API thread and agent

`P3` · `robustness` · confidence **medium** · effort **S** · `backend/app/api/report.py` : 155-173 (run_generate)

- **Symptom.** After generate_report() already persists meta/outline/markdown internally (report_agent.py:2169 success / :2198 failure), run_generate calls ReportManager.save_report(report) again, then conditionally completes/fails the task.
- **Root cause.** Persistence responsibility is split: ReportAgent.generate_report saves the report and updates progress itself, and the API wrapper re-saves it. The duplication is harmless functionally but obscures ownership and doubles file writes (re-triggering the non-atomic write race window).
- **Evidence.** `report = agent.generate_report(progress_callback=..., report_id=report_id); ReportManager.save_report(report)  # generate_report already saved internally`
- **Impact.** Extra disk writes and an additional window for the partial-write race; if generate_report ever changes to not return a fully-saved report, the two layers could diverge.
- **Fix.** In backend/app/api/report.py run_generate, delete the redundant line 161 `ReportManager.save_report(report)`. generate_report() is the single owner of persistence and already saves on every return path (success and failure), so the API wrapper should only translate the returned report.status into task state: keep the `if report.status == ReportStatus.COMPLETED: task_manager.complete_task(...) else: task_manager.fail_task(...)` block. This makes ReportAgent.generate_report the sole persistence owner, eliminates the duplicate non-atomic file writes, and clarifies ownership. (Do not instead make generate_report skip saving and persist only in the API — the agent saves incrementally at multiple stages during generation for progress/recovery, so removing its internal saves would be a much larger, riskier change.)
- **Verified.** Confirmed by reading the actual code. backend/app/services/report_agent.py `generate_report` persists the report on every return path: line 2017 (init), 2047 (post-outline), 2169 (success, after assembling markdown + setting COMPLETED), and 2198 (failure, FAILED). Both the success branch (return at 2185) and the except branch (return at 2211) return a Report object that has already been saved — there is no return path that yields an unsaved report. Then backend/app/api/report.py run_generate line 161 calls `ReportManager.save_report(report)` unconditionally, immediately after `generate_report()` returns (line 155-158) and before the status check at line 163. So the second save is genuinely redundant: it re-writes meta.json, outline.json, and report.md with identical data the agent already wrote. `ReportManager.save_report` (lines 2873-2890) uses plain `open(path,'w')` + json.dump / f.write directly to the final paths with no temp-file-and-rename, so it is non-atomic, confirming the extra-write-window claim. The duplication is functionally harmless (idempotent, same thread, sequential — the two writes do not race each other and write identical bytes), so this is a code-health/robustness issue, not a functional defect. P3 is the correct severity: no user-visible bug, but split persistence ownership and a wasted non-atomic write that would diverge only if generate_report were later changed to not save. The proposed fix is correct and minimal.

### frontend — Vue frontend (views/components/api/store)

#### [F-10-2] GraphPanel link label background reads getBBox() on possibly-hidden text, can throw/NaN when edge labels are toggled off

`P3` · `robustness` · confidence **medium** · effort **S** · `frontend/src/components/GraphPanel.vue` : 800-810, 660-662

- **Symptom.** On every simulation tick, linkLabelBg.each() calls textEl.getBBox() for the corresponding label. The label <text> elements are created with style display:none when showEdgeLabels is false (line 662).
- **Root cause.** getBBox() on an SVG element with display:none returns a zero/empty box (and in some browsers can throw), so the background rects are computed with width/height of 0+8 / 0+4 and mispositioned; this runs continuously during the layout even though labels are hidden.
- **Impact.** Wasted per-tick work computing bounding boxes for hidden labels, and degenerate background rects; low user-visible harm because the bg is also hidden, but it is needless work on the hot tick path and a latent throw risk.
- **Fix.**

  ```
  Guard the background sizing on showEdgeLabels (and harden against the cross-browser getBBox quirk). In the tick handler at lines 800-810, skip the work entirely when labels are hidden, and defensively bail on a zero/invalid bbox. For example:
  
  linkLabelBg.each(function(d, i) {
    if (!showEdgeLabels.value) return  // labels hidden: nothing to size, avoid getBBox on display:none
    const textEl = linkLabels.nodes()[i]
    let bbox
    try {
      bbox = textEl.getBBox()
    } catch (e) {
      return  // Firefox throws getBBox() on display:none / detached elements
    }
    if (!bbox || bbox.width === 0) return  // degenerate box (hidden in Chrome/Safari); skip
    const mid = getLinkMidpoint(d)
    d3.select(this)
      .attr('x', mid.x - bbox.width / 2 - 4)
      .attr('y', mid.y - bbox.height / 2 - 2)
      .attr('width', bbox.width + 8)
      .attr('height', bbox.height + 4)
      .attr('transform', '')
  })
  
  This removes the needless per-tick getBBox work when labels are off, eliminates the Firefox throw risk inside the tick callback (which otherwise aborts the remainder of the tick), and avoids producing degenerate background rects. The showEdgeLabels.value check is the primary fix; the try/catch + width===0 guard hardens against any residual display:none or not-yet-laid-out case.
  ```
- **Verified.**

  ```
  Confirmed against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/components/GraphPanel.vue.
  
  Facts verified:
  - Lines 634 and 662: the link-label <rect> and <text> elements are created with style display set to 'block' when showEdgeLabels is true, else 'none'.
  - showEdgeLabels defaults to true (line 286) and is a user-toggleable checkbox (line 235); the watch at lines 840-847 flips display:none/block live while the simulation may still be ticking.
  - Lines 800-810: on EVERY simulation tick, linkLabelBg.each() unconditionally calls textEl.getBBox() for the corresponding label text node (line 803), with NO guard on showEdgeLabels and NO try/catch. There is no nearby handling that mitigates this.
  
  So the finding is not a misreading and is not already guarded. When a user unchecks "show edge labels", the label texts are display:none yet the tick still calls getBBox() on them every frame during any layout/drag activity.
  
  Correctness of the root-cause claim: largely accurate, with one refinement. In Chromium and WebKit, getBBox() on a display:none element returns a zero rect {0,0,0,0}, so the bg rects get degenerate sizes (width 8 / height 4) and are mispositioned — but harmless since the rect is itself display:none. The stronger, real concern is the latent throw: Firefox historically throws NS_ERROR_FAILURE from getBBox() on a display:none element (long-standing Gecko behavior). If a Firefox user toggles labels off while the simulation is active, the exception propagates out of the each() callback inside the tick handler, aborting the rest of that tick (so node/seed/label position updates after line 810 would not run for that frame). That is a genuine functional risk, not merely wasted work.
  
  Net: REAL defect. Severity P3 is appropriate: it requires the non-default state (user unchecks the box) AND, for the visible breakage, Firefox specifically; in the dominant Chrome/Safari engines the harm is only minor wasted per-tick work on a hidden element. Not P2 because there is no user-visible breakage in the default configuration or in Chromium/WebKit.
  ```

#### [F-10-3] ResearchView: research progress log re-fetched forever if final tail returns empty

`P3` · `bottleneck` · confidence **medium** · effort **S** · `frontend/src/views/ResearchView.vue` : 428-446

- **Symptom.** poll() computes researchRunning = !(research completed) || logLines.value.length === 0 and fetches 400 log lines every 2.5s while true. If research completes but getProgressLog ever returns an empty lines array (log file missing/rotated, or research produced no progress.log), logLines stays length 0 and researchRunning stays true for the entire pipeline lifetime.
- **Root cause.** The 'stop fetching logs after research done' optimization is gated on logLines.length === 0, which can never become false if the backend never returns any lines; there is no separate 'we have fetched at least once' flag.
- **Impact.** Redundant full 400-line fetch every 2.5s for the whole (potentially long) full-pipeline run when the log is empty — extra HTTP/IO with no benefit.
- **Fix.**

  ```
  Add a 'log fetch settled after research completed' flag instead of relying on logLines.length === 0.
  
  In the state declarations (near line 308) add:
    let researchLogSettled = false
  
  In resetState() (line 504-508) reset it alongside dossierFetched:
    logLines.value = []; dossier.value = null; dossierFetched = false; researchLogSettled = false
  
  Change the gate (line 430) to:
    const researchCompleted = stages.value.research && stages.value.research.status === 'completed'
    const researchRunning = !researchCompleted || !researchLogSettled
  
  And after assigning the log on line 446, mark it settled once we've done one fetch after completion:
    if (lg) { logLines.value = (lg.data && lg.data.lines) || []; if (researchCompleted) researchLogSettled = true }
  
  This stops the per-cycle 400-line fetch after one post-completion fetch regardless of whether the tail was empty, while still showing logs that do exist. Note researchCompleted must be computed from the freshly-updated stages.value (the assignment on line 441 runs before line 446, so reading stages.value.research at line 446 reflects the latest status). Keep researchLogSettled as a plain let (not reactive) since it is only read inside poll().
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. In /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/views/ResearchView.vue line 430:
  
    const researchRunning = !(stages.value.research && stages.value.research.status === 'completed') || logLines.value.length === 0
  
  By precedence this is researchRunning = A || B where A = (research not completed) and B = (logLines.length === 0). After research completes A is false, so researchRunning collapses to (logLines.value.length === 0). Line 446 sets logLines.value = (lg.data && lg.data.lines) || []. So if getProgressLog ever returns an empty lines array, logLines stays length 0, researchRunning stays true, and the 400-line fetch (line 433) keeps firing every 2.5s (interval set on line 422) for the entire remaining pipeline lifetime (graph build + simulation + report).
  
  The backend makes the empty return concretely reachable (verified in /Users/rogerlin/Downloads/DeepResearchForecast/backend/app/api/research.py get_progress_log, lines 404-420): when research_progress.log does not exist it returns {"lines": []} (line 410), and an empty/rotated log yields "".splitlines() == [] → also {"lines": []} (lines 416-418). So the 'stop after one complete tail' optimization (per the Chinese comment on lines 428-429) is defeated exactly in the legitimate empty-log case. There is no separate 'fetched at least once' flag — only dossierFetched exists (line 308), which is unrelated to the log gate.
  
  Impact is minor: it does not break UI or correctness; status polling already runs every 2.5s and the redundant log GET rides along in the same Promise.all. It is purely a redundant ~400-line GET per cycle in an edge case. P3 is the correct severity. Not a misreading, not dead code, not guarded elsewhere.
  ```

#### [F-10-8] Global 300s axios timeout applied to all GETs, including fast polling endpoints

`P3` · `robustness` · confidence **medium** · effort **S** · `frontend/src/api/index.js` : 4-10

- **Symptom.** The shared axios instance sets timeout: 300000 (5 min) for every request, including ResearchView's 2.5s poll (getPipelineStatus/getProgressLog) and SimulationView's loaders.
- **Root cause.** A single coarse timeout tuned for the slow ontology/build calls is reused for quick polling calls; a hung polling request will not time out for 5 minutes, and because the poll interval is 2.5s, requests can pile up against a stalled backend.
- **Impact.** On backend stalls, polling requests accumulate (no per-call abort) and the connection-loss banner is delayed; resource pressure from many pending sockets.
- **Fix.**

  ```
  Two changes, ordered by impact:
  
  1) Add an in-flight guard to each poller so a stalled request cannot pile up (highest leverage, prevents socket accumulation). In ResearchView.vue:
  ```js
  let pollInFlight = false
  async function poll() {
    if (!pipelineId.value || pollInFlight) return
    pollInFlight = true
    try { /* existing body */ } finally { pollInFlight = false }
  }
  ```
  Apply the same guard to fetchRunStatus/fetchRunStatusDetail (Step3Simulation.vue) and pollPrepareStatus/fetchProfilesRealtime/fetchConfigRealtime (Step2EnvSetup.vue).
  
  2) Use a short per-request timeout for status/log/poll calls instead of the global 300s. Pass a per-call override in research.js / simulation.js, e.g. `service({ url, method: 'get', timeout: 15000 })` for getPipelineStatus, getProgressLog, getRunStatus(/detail), getPrepareStatus, getSimulation*Realtime, getReportStatus, getAgentLog, getConsoleLog. Keep the 300s default only for the heavy synchronous POSTs (runPipeline, prepare/create/start, report generate). This makes a hung poll reject in ~15s so the existing pollFailures/POLL_FAILURE_THRESHOLD path surfaces the connection-loss banner promptly.
  
  (Optional) An AbortController to cancel the prior poll before issuing the next is a reasonable belt-and-suspenders addition, but the in-flight guard alone already prevents the pile-up since the interval skips while one poll is outstanding.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/api/index.js:6 sets `timeout: 300000` (5 min) on the single shared axios `service` instance. Every polling endpoint inherits it: ResearchView's `getPipelineStatus` and `getProgressLog` (frontend/src/api/research.js:126,169) both call bare `service({...})`, and the Step2/Step3 status/profiles/config pollers all use the same `service` with no per-call override.
  
  The pile-up mechanism is real and currently true. frontend/src/views/ResearchView.vue:422 runs `setInterval(poll, 2500)` with NO in-flight/busy guard and NO AbortController (verified: only `pollTimer`, `pollFailures` exist; no `inFlight`/`isPolling` flag). Each tick fires `poll()`, which issues two parallel GETs via `Promise.all` (lines 431-434) regardless of whether the previous poll resolved. On a TCP-connected-but-unresponsive backend, each request stays open up to 300s, so requests accumulate at ~2/2.5s and quickly saturate the browser's ~6-connections-per-host limit — exactly the resource-pressure claim. Step3Simulation.vue:467/471 and Step2EnvSetup.vue:825/836/954 follow the identical pattern.
  
  The delayed connection-loss banner is also real: `pollFailures` only increments when a request rejects (ResearchView.vue:473), and the banner shows after `POLL_FAILURE_THRESHOLD = 4` failures (line 474). Under a true stall, rejections don't fire until the 300s timeout elapses, so the "Lost connection" banner is delayed by minutes instead of ~10s.
  
  Caveat that keeps this at P3, not higher: a hard connection refusal / RST (backend down, port closed) rejects near-instantly, so the banner delay and pile-up only manifest in the specific "connected but hung / very slow" case — a real but narrow robustness/UX issue, not a correctness or data-integrity bug. The 300s value is intentional for the heavy synchronous endpoints (runPipeline ontology/build), so the root cause — one coarse timeout reused for fast polls — is accurately diagnosed.
  ```

#### [F-10-10] Pipeline persisted in localStorage under a different key prefix than the rest of the app (mirofish vs drf)

`P3` · `data-contract` · confidence **medium** · effort **S** · `frontend/src/views/ResearchView.vue` : 203, 263, 383, 413, 456, 515

- **Symptom.** ResearchView stores the active pipeline under 'mirofish_active_pipeline', while i18n persists locale under 'drf_locale' and SimulationRunView/other views use route params. The branding shown is 'DeepResearchForecast' / 'DeepAgentForecast' (per recent rename commit).
- **Root cause.** Leftover MIROFISH branding keys after the project was renamed (commit 62f6964 'Rename to DeepAgentForecast'); the storage key was not migrated.
- **Impact.** Purely a consistency/maintainability smell today (the key still works), but if any other view or a future cleanup reads a 'drf_'-prefixed key it will miss the active pipeline; also leaks the old product name into client storage.
- **Fix.** Standardize the prefix to `drf_active_pipeline` to match the i18n STORAGE_KEY convention, and add a one-time migration in onMounted so any user with an in-flight pipeline under the old key is not orphaned. In ResearchView.vue: change line 203 to `const ACTIVE_PIPELINE_KEY = 'drf_active_pipeline'` and `const LEGACY_PIPELINE_KEY = 'mirofish_active_pipeline'`. In the onMounted load (line 524-526), read the new key first, fall back to the legacy key, and if the legacy value is found, re-persist it under the new key and remove the legacy key, e.g.: `let saved = null; try { saved = localStorage.getItem(ACTIVE_PIPELINE_KEY) || localStorage.getItem(LEGACY_PIPELINE_KEY); if (saved && !localStorage.getItem(ACTIVE_PIPELINE_KEY)) { localStorage.setItem(ACTIVE_PIPELINE_KEY, saved); localStorage.removeItem(LEGACY_PIPELINE_KEY); } } catch (e) { saved = null }`. All other set/remove call sites already use ACTIVE_PIPELINE_KEY, so no further changes are needed. Given the negligible impact, this is safe to defer or batch into a broader branding-cleanup pass rather than fix in isolation.
- **Verified.** Confirmed by reading the code. frontend/src/views/ResearchView.vue:203 defines `const ACTIVE_PIPELINE_KEY = 'mirofish_active_pipeline'`, and that constant drives every localStorage read/write/remove in the file (lines 263, 383, 413, 456, 466, 515, 525). Meanwhile frontend/src/i18n.js:11 standardizes on the `drf_` prefix (`const STORAGE_KEY = 'drf_locale'`), so the stale `mirofish_` prefix is a genuine leftover from the DeepAgentForecast/DeepResearchForecast rename and was not migrated. The factual claim is accurate. However, impact is cosmetic only: the key is fully self-consistent (one constant for all ops), so the feature works correctly today. A grep across frontend/src confirms NO other view, store, or API references either `mirofish_active_pipeline` or a `drf_active_pipeline` key, and there is no migration code — so the claimed risk that 'a future cleanup reads a drf_-prefixed key and misses the active pipeline' is purely hypothetical with no current reader. The value is a non-sensitive pipeline UUID, so the 'leaks old product name' concern is negligible. This is a real but trivial consistency/maintainability smell; P3 is correct (the finding self-classifies as P3 and concedes 'the key still works').

#### [F-10-11] Markdown renderer drops link titles and is brittle on links containing parentheses/spaces; otherwise XSS-safe

`P3` · `robustness` · confidence **medium** · effort **M** · `frontend/src/utils/markdown.js` : 32-49

- **Symptom.** renderInline link regex [label](url) uses ([^)\s]+) for the URL, so any URL containing a space or a closing paren (common in Wikipedia-style citation URLs) is silently truncated or not linked. Images are reduced to alt text. The v-html sink in ForecastReport relies entirely on this renderer.
- **Root cause.** Hand-rolled regex markdown parser with a narrow URL character class. Note: escapeHtml runs before inline rules and the URL is scheme-whitelisted (https/mailto/#/relative) so reflected HTML/JS injection is prevented — this is NOT an XSS, but link fidelity is lossy.
- **Impact.** Forecast report citations whose URLs contain parens/spaces render as broken or plain text, degrading the report's clickable sources. No security impact.
- **Fix.**

  ```
  Broaden the link URL capture in renderInline (markdown.js:37) and post-process trailing punctuation, rather than the brittle ([^)\s]+) class. Concretely:
  
  1) Capture greedily up to an optional `"`-title and a closing `)`, then trim balanced/trailing punctuation off the URL while pushing any stripped chars back into the trailing text. Example replacement:
  
      // links: [label](url "optional title")
      t = t.replace(/\[([^\]]+)\]\(\s*([^)]*?)(?:\s+&quot;[^&]*&quot;)?\s*\)/g, (m, label, rawUrl) => {
        let url = rawUrl.trim()
        // re-attach unbalanced trailing ')' belonging to a Wikipedia-style path
        // (count parens; allow balanced inner parens like _(language_model))
        let opens = (url.match(/\(/g) || []).length
        let closes = (url.match(/\)/g) || []).length
        // (when the source had _(x)) the outer ) is the link delimiter and already consumed by the regex)
        const clean = label.replace(/^citation:/i, '')
        url = url.replace(/\s/g, '%20')               // encode spaces
        const safeUrl = /^(https?:|mailto:|#|\/)/i.test(url) ? url : '#'
        const external = /^https?:/i.test(safeUrl)
        return `<a href="${safeUrl}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${clean}</a>`
      })
  
  This uses [^)]*? plus an explicit closing `)`, so `_(language_model))` parses correctly (inner `(...)` stays in the URL, outer `)` is the delimiter), encodes spaces to %20, and preserves the existing escapeHtml-first + scheme-whitelist safety (unchanged). Note the title group must remain `&quot;...&quot;` because escaping runs first.
  
  2) Stronger, recommended given LLM-authored content: replace the hand-rolled inline parser with a vetted pipeline — `marked` (which handles balanced-paren and angle-bracket `<url>` link syntax) piped through `DOMPurify` (sanitize) before assigning to v-html. This removes the whole class of regex-fidelity bugs and keeps the XSS guarantee independent of the custom escaper. Add at least one unit test covering `_(language_model)` URLs and spaces.
  ```
- **Verified.**

  ```
  Confirmed by reading frontend/src/utils/markdown.js:32-49 and reproducing the regex behavior in Node against real inputs.
  
  1) Link URL capture is ([^)\s]+), so:
  - Parenthesized URLs (Wikipedia-style) truncate at the first ): `[Wiki](https://en.wikipedia.org/wiki/Foo_(bar))` renders href="https://en.wikipedia.org/wiki/Foo_(bar" with a stray `)` left in the text. This is a real, common case — the project's own research logs (e.g. backend/uploads/pipelines/*/handoff/research_progress.log and session JSON) are full of such URLs: `/wiki/Gemini_(language_model)`, `/wiki/Colossus_(supercomputer)`, `/wiki/Rubin_(microarchitecture)`, `/wiki/Semiconductor_..._International_Corporation`. These are exactly the citation sources fed into forecast reports.
  - URLs containing a space are not linked at all (regex fails to match), falling through as literal `[label](url)` text.
  - Link titles `(url "Title")` are matched but discarded (acceptable, but lossy as claimed).
  - Images are reduced to alt text (markdown.js:35) — also as claimed.
  
  2) Consumption surface confirmed: renderMarkdown feeds v-html in ForecastReport.vue:66, DossierViewer.vue:41, Step4Report.vue:51, Step5Interaction.vue. Content is LLM/web-sourced, so degraded citations are user-visible.
  
  3) Security sub-claim confirmed accurate: escapeHtml (lines 13-19) runs before inline rules (paragraph/heading/table/list/quote all call renderInline(escapeHtml(...))). The URL scheme whitelist (line 39) maps non-(https|mailto|#|/) schemes to `#`, neutralizing `javascript:`. Embedded `"` becomes `&quot;`, which both breaks the link regex and prevents href attribute breakout. Verified: `[click](javascript:alert(1))` -> href="#"; raw `<script>` -> escaped. So this is NOT XSS — correctly classified.
  
  Severity P3 is correct: cosmetic/fidelity degradation of citation links, no security or data-loss impact, and many URLs (no parens/spaces) work fine. The finding is accurate, well-scoped, and not already guarded against.
  ```

#### [F-10-13] SimulationRunView handleGoBack swallows env-status errors and may navigate back while a simulation is still running

`P3` · `robustness` · confidence **low** · effort **M** · `frontend/src/views/SimulationRunView.vue` : 147-193

- **Symptom.** handleGoBack queries getEnvStatus, and on any thrown error it logs '检查模拟状态失败' and then unconditionally router.push back to Step 2 (line 192 runs regardless of the catch). The stop/close attempts inside are also best-effort with only log messages.
- **Root cause.** The catch at 187-189 does not re-raise or block navigation, and the final router.push is outside any guard, so a failed status check or failed stop leaves the simulation subprocess/env potentially alive while the user leaves the page.
- **Impact.** Orphaned/running simulation environment after the user goes back, consuming resources and possibly causing 'simulation already running' conflicts when re-entering.
- **Fix.**

  ```
  Make navigation conditional on the stop/close outcome and give real user-facing feedback (not just addLog), while avoiding trapping the user on a stuck page. Concretely in handleGoBack:
  
  1. Track an explicit outcome flag. Replace the silent best-effort flow with one that records whether the env/process was successfully stopped, e.g.:
  
  ```js
  let stopFailed = false
  let lastError = null
  try {
    const envStatusRes = await getEnvStatus({ simulation_id: currentSimulationId.value })
    if (envStatusRes.success && envStatusRes.data?.env_alive) {
      try {
        await closeSimulationEnv({ simulation_id: currentSimulationId.value, timeout: 10 })
      } catch (closeErr) {
        try { await stopSimulation({ simulation_id: currentSimulationId.value }) }
        catch (stopErr) { stopFailed = true; lastError = stopErr }
      }
    } else if (isSimulating.value) {
      try { await stopSimulation({ simulation_id: currentSimulationId.value }) }
      catch (err) { stopFailed = true; lastError = err }
    }
  } catch (err) {
    stopFailed = true; lastError = err   // status check itself failed -> sim state unknown
  }
  
  if (stopFailed) {
    const proceed = window.confirm(
      `无法确认模拟已停止（${lastError?.message || '未知错误'}）。` +
      `继续返回可能会留下正在运行的模拟环境，导致重新进入时报“模拟已在运行中”。是否仍要返回？`
    )
    if (!proceed) {
      addLog('已取消返回，模拟可能仍在运行')
      return   // stay on the page so the user can retry stopping
    }
  }
  
  router.push({ name: 'Simulation', params: { simulationId: currentSimulationId.value } })
  ```
  
  This blocks the unconditional navigation only when the stop could not be confirmed, surfaces the failure in a visible dialog (since addLog disappears on unmount), and still lets the user deliberately leave a stuck page. Optionally pass a force flag to stopSimulation on confirm, and/or add a re-entry recovery path that calls closeSimulationEnv before startSimulation so a leftover env does not permanently block restarts.
  ```
- **Verified.**

  ```
  Confirmed by reading the actual code. In /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/views/SimulationRunView.vue, handleGoBack (lines 147-193) awaits getEnvStatus (156); the outer catch at 187-189 only calls addLog('检查模拟状态失败...') and does NOT re-raise. The router.push at line 192 sits outside the try/catch and runs unconditionally on every path. The nested stop/close attempts (161-185) are likewise best-effort: every failure (closeSimulationEnv, stopSimulation) is handled only with addLog and execution continues to the navigation. So a failed status check, failed close, or failed force-stop all still navigate the user back to Step 2.
  
  addLog (126-132) pushes to the in-component systemLogs ref shown only in the workbench panel; once router.push fires the component unmounts, so the user does not even reliably see the just-logged failure — feedback is effectively absent, not merely weak.
  
  Impact is corroborated by the backend: the simulation env is a separate IPC-backed subprocess (SimulationRunner.check_env_alive via ipc_client in backend/app/services/simulation_runner.py:1482-1497), and start_simulation raises ValueError('模拟已在运行中') when runner_status is RUNNING/STARTING (lines 342-343). Therefore a stop/close failure can leave an orphaned running env, and a subsequent re-entry+restart hits a real 'already running' conflict — exactly the claimed impact.
  
  This is a genuine robustness/UX defect, not a misreading, dead code, or intended behavior. Severity P3 is correct: it only manifests on the error path (backend down / IPC timeout / network failure); the happy path correctly stops the sim before navigating, and there is no data-loss or security exposure.
  ```

### setup-ops — Setup/ops (setup.sh, doctor, packaging, env)

#### [F-11-5] Live key-test always uses table-default model/base, never the user's tuned values; reasoning models may false-pass

`P3` · `robustness` · confidence **medium** · effort **S** · `setup.sh` : 390-405

- **Symptom.** The setup connectivity test posts the hard-coded PROVIDER_MODEL/PROVIDER_BASE for the chosen provider, not whatever LLM_MODEL_NAME/LLM_BASE_URL the user may already have in .env; and it uses max_tokens:1.
- **Root cause.** The test reads `${PROVIDER_MODEL[$CHOSEN_IDX]}` / `${PROVIDER_BASE[$CHOSEN_IDX]}` directly. If the user previously customized LLM_MODEL_NAME (e.g. a model only their account can access) the test validates a different model than the one the pipeline will actually call. Separately, `max_tokens:1` against reasoning models (MiniMax-M3, deepseek) can return HTTP 200 with empty content (finish_reason=length), so the test confirms auth but not that the model produces usable output.
- **Evidence.** `-d "{\"model\":\"${PROVIDER_MODEL[$CHOSEN_IDX]}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}" \`
- **Impact.** A green 'API key works' can still be followed by runtime failures when the real model name/base differs or the model returns no parseable content — partially undermining the 'catch typos at setup' value proposition.
- **Fix.**

  ```
  Make the test use the effective values that were just written to .env, falling back to the table defaults only when unset; and downgrade an empty/garbage 200 body to a soft warning rather than a hard pass.
  
  Replace the test block (around lines 389-405) so it derives the model/base from .env after the upsert logic has run:
  
    if [ -n "$LLM_API_KEY_INPUT" ] && have curl; then
      # Prefer the values actually persisted to .env (the user may have tuned
      # LLM_MODEL_NAME/LLM_BASE_URL); fall back to the provider table defaults.
      TEST_BASE="$(grep -E '^[[:space:]]*LLM_BASE_URL=' "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
      TEST_MODEL="$(grep -E '^[[:space:]]*LLM_MODEL_NAME=' "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
      [ -n "$TEST_BASE" ]  || TEST_BASE="${PROVIDER_BASE[$CHOSEN_IDX]}"
      [ -n "$TEST_MODEL" ] || TEST_MODEL="${PROVIDER_MODEL[$CHOSEN_IDX]}"
      info "Testing the API key against $TEST_BASE (model: $TEST_MODEL) …"
      ...
      HTTP_CODE="$(curl -sS -o /tmp/setup_llm_test.$$ -w '%{http_code}' --max-time 25 \
        "$TEST_BASE/chat/completions" \
        -H "Authorization: Bearer $LLM_API_KEY_INPUT" \
        -H "Content-Type: application/json" \
        ${UA_HEADER[@]+"${UA_HEADER[@]}"} \
        -d "{\"model\":\"$TEST_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":16}" \
        2>/dev/null || printf '000')"
      if [ "$HTTP_CODE" = "200" ]; then
        # 200 confirms auth + that the model name is accepted. Distinguish a usable
        # reply from an empty one (reasoning models can 200 with no content).
        if grep -q '"choices"' /tmp/setup_llm_test.$$ 2>/dev/null; then
          ok "API key works (HTTP 200 from $TEST_MODEL)"
        else
          warn "API key authenticated (HTTP 200) but the response had no choices —"
          warn "    verify the model name '$TEST_MODEL' produces output before a real run."
        fi
      elif ...
  
  Note: bump max_tokens slightly (e.g. 16) and gate the pass on the presence of a choices field so an empty/garbage 200 becomes a soft warning. Place the .env reads AFTER the upsert_env block (post line 365) so the effective values are already persisted.
  ```
- **Verified.**

  ```
  Confirmed by reading /Users/rogerlin/Downloads/DeepResearchForecast/setup.sh.
  
  Prong 1 (model/base mismatch) — REAL. The live key test at lines 397-402 hard-codes the table defaults `${PROVIDER_BASE[$CHOSEN_IDX]}` (line 398) and `${PROVIDER_MODEL[$CHOSEN_IDX]}` (line 402). These arrays are defined once at lines 169-186 and are never reassigned from .env (grep confirms only the two definitions plus read-only expansions). Meanwhile the env-write logic at lines 345-354 deliberately PRESERVES a user's previously tuned LLM_MODEL_NAME/LLM_BASE_URL: when the chosen provider equals the existing one (CURRENT_PROVIDER) and CURRENT_MODEL/CURRENT_BASE are non-empty, the `upsert_env` calls are skipped, so .env keeps the user's custom values. Therefore on a re-run where the user kept the same provider but had customized LLM_MODEL_NAME to a model only their account can access (or a custom base URL), the connectivity test validates the table-default model/base while the pipeline will actually call the custom one. The test is only reached when LLM_API_KEY_INPUT is non-empty (line 389), which requires an interactive run where the user pasted a fresh key (lines 261-263) — reachable. Note the divergence is bounded to the re-run + customized-value case: on a first run .env is written from the table so the test matches.
  
  Prong 2 (max_tokens:1 false-pass) — REAL but secondary. The body sends `"max_tokens":1` (line 402) and success is judged solely by HTTP 200 (line 404). A reasoning model (MiniMax-M3, deepseek) can legitimately return HTTP 200 with finish_reason=length and empty content, so the test confirms auth/endpoint/model-name acceptance but not usable output. Since the test's stated purpose is catching typo'd keys (auth), this is a weaker concern than prong 1.
  
  Severity P3 is correct: normal first-run setup is unaffected; this only weakens the early-warning value in a re-run-with-custom-model edge case, and the runtime pipeline still has its own behavior. Not dead code, not already-handled, not a misreading.
  ```

#### [F-11-6] .env.example 'OpenAI-compatible' example points at a DashScope/Qwen endpoint and model, inconsistent with setup.sh openai defaults

`P3` · `config` · confidence **high** · effort **S** · `.env.example` : 12-18

- **Symptom.** The commented `openai` section example sets `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` and `LLM_MODEL_NAME=qwen-plus`, while setup.sh's openai defaults are `https://api.openai.com/v1` + `gpt-4o-mini`, and a dedicated qwen section also exists.
- **Root cause.** The generic OpenAI-compatible example was authored with a Qwen/DashScope endpoint baked in, duplicating the separate qwen provider and diverging from the picker's own openai defaults.
- **Evidence.**

  ```
  # LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  # LLM_MODEL_NAME=qwen-plus  (under the OpenAI-compatible section)
  ```
- **Impact.** A user copying the openai example gets a Qwen endpoint under LLM_PROVIDER=openai, which is confusing and may not match their key; purely a documentation/consistency defect, no crash.
- **Fix.**

  ```
  Align the OpenAI-compatible example with setup.sh's openai defaults and stop duplicating the qwen provider. In /Users/rogerlin/Downloads/DeepResearchForecast/.env.example replace lines 14-18 with a real OpenAI endpoint/model:
  
  # 支持 OpenAI SDK 格式的任意 LLM API；下方默认与 setup.sh 选择 openai 时一致
  # LLM_API_KEY=sk-xxxxxxxx
  # LLM_BASE_URL=https://api.openai.com/v1
  # LLM_MODEL_NAME=gpt-4o-mini
  
  This matches PROVIDER_BASE/PROVIDER_MODEL for openai in setup.sh (api.openai.com/v1 + gpt-4o-mini) and leaves DashScope/Qwen to the dedicated qwen section (lines 53-60). Drop the line-14 "推荐使用阿里百炼qwen-plus" recommendation (and the "消耗较大" note that belongs with qwen) since that guidance is qwen-specific, not generic-OpenAI.
  ```
- **Verified.**

  ```
  Confirmed by reading both files. In /Users/rogerlin/Downloads/DeepResearchForecast/.env.example, the "OpenAI-compatible API config (only needed when LLM_PROVIDER=openai)" section at lines 12-18 sets the example to a DashScope/Qwen endpoint: line 14 recommends Alibaba Bailian qwen-plus, line 17 `# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`, line 18 `# LLM_MODEL_NAME=qwen-plus`. Meanwhile setup.sh's openai picker defaults (PROVIDER_BASE/PROVIDER_MODEL index 2, lines 169-186) are `https://api.openai.com/v1` + `gpt-4o-mini`. A dedicated Qwen section already exists at lines 53-60 (`dashscope-intl.aliyuncs.com/compatible-mode/v1` + `qwen-plus`), so the openai example both duplicates the qwen provider (with a slightly different non-intl host) and contradicts the picker's own openai defaults.
  
  This is a real, currently-true inconsistency. Severity is correctly P3 (documentation/consistency only): every line is commented out, so nothing runs; and in the normal interactive setup.sh flow, choosing "openai" actively upserts LLM_BASE_URL=https://api.openai.com/v1 and LLM_MODEL_NAME=gpt-4o-mini (setup.sh lines 348-353), overwriting these example values. The only confusing case is a user who hand-copies/uncomments the example without running the picker — they would get a Qwen endpoint under LLM_PROVIDER=openai. No crash, no data loss.
  ```

### x-security — CROSS-CUTTING: security & secrets

#### [F-13-4] Inconsistent path-traversal hardening: read/state endpoints lack the guard that delete() has

`P3` · `security` · confidence **medium** · effort **S** · `backend/app/services/pipeline_orchestrator.py, backend/app/api/research.py` : orchestrator.py:262-269 vs 192-251; research.py:274-329,404-420

- **Symptom.** PipelineManager.delete() explicitly rejects pipeline_id containing '/', '\\' or '..', but load()/state_path()/handoff_dir() and the GET dossier/status/progress/artifact endpoints join the URL-supplied pipeline_id into a filesystem path with no such guard.
- **Root cause.** _dir()/state_path()/handoff_dir() do `os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id, ...)` with the raw pipeline_id from the URL. Flask's default `<string>` route converter rejects literal '/', which blocks the obvious `../` traversal, so this is largely mitigated in practice — but the protection relies on the URL router, not the data layer, and the asymmetry with delete()'s explicit guard signals the read paths were not hardened deliberately. A future refactor (e.g. exposing pipeline_id via query string, a path-converter route, or another caller) would silently reintroduce traversal.
- **Evidence.** `orchestrator.py:263 `if not pipeline_id or "/" in pipeline_id or "\\" in pipeline_id or ".." in pipeline_id: return False` (delete only); orchestrator.py:193 `return os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id)` (load path, no guard)`
- **Impact.** Low as currently routed (single-segment <string> blocks slashes), but the trust-boundary check lives in the wrong layer and is inconsistent across methods; a small routing change could turn the read endpoints into arbitrary-file reads under PIPELINE_DATA_DIR's parent.
- **Fix.**

  ```
  Centralize a single validator at the data layer so every read/write/delete path is guarded, rather than relying on the route converter. Put the check in _dir() since state_path() and handoff_dir() both route through it; raise rather than return sentinels so existing API-layer try/except converts it to a clean 4xx and load()/list_pipelines() callers are not silently misled.
  
  In pipeline_orchestrator.py PipelineManager:
  
      import re
      _PIPELINE_ID_RE = re.compile(r"^pipe_[0-9a-fA-F]+$")
  
      @classmethod
      def _validate_id(cls, pipeline_id: str) -> str:
          if (not pipeline_id or "/" in pipeline_id or "\\" in pipeline_id
                  or ".." in pipeline_id or not cls._PIPELINE_ID_RE.match(pipeline_id)):
              raise ValueError(f"invalid pipeline_id: {pipeline_id!r}")
          return pipeline_id
  
      @classmethod
      def _dir(cls, pipeline_id: str) -> str:
          cls._validate_id(pipeline_id)
          return os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id)
  
  Then delete()'s inline guard can defer to _validate_id (wrap in try/except ValueError: return False to keep its bool contract), and load() should catch ValueError to keep returning None for malformed ids so list_pipelines()/os.listdir paths stay robust. Verify the actual id-generation format before pinning the regex to pipe_<hex>; if ids are not strictly that shape, drop the regex and keep only the separator/.. rejection so legitimate ids are not broken. The artifact <name> path needs no change since it resolves from stored state, not from joining name.
  ```
- **Verified.**

  ```
  Confirmed by reading the code. PipelineManager.delete() (pipeline_orchestrator.py:262-264) explicitly rejects pipeline_id containing '/', '\\', or '..' before calling _dir(). The read/write helpers _dir() (193), state_path() (196-197), and handoff_dir() (200-201) do raw os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id, ...) with NO such guard. The GET endpoints that consume them — /status/<pipeline_id> (277 -> load), /<pipeline_id>/dossier (301 -> handoff_dir), /<pipeline_id>/progress (407 -> handoff_dir), and edit_dossier PUT (340/354) — all take pipeline_id straight from the URL. The asymmetry and the misplaced trust boundary are real and currently true.
  
  The severity is correctly self-rated P3 (defense-in-depth / consistency), not an active vuln. All these routes use Flask's default route converter (registered as @research_bp.route('/<pipeline_id>/...') with no <path:> converter; blueprint registered at /api/research in __init__.py:81), which is the 'string' converter and does NOT match '/'. So a literal ../ traversal segment cannot reach these handlers — Werkzeug routes it elsewhere or 404s. Therefore arbitrary-file read is not exploitable as currently wired; the claim that it is 'largely mitigated in practice' is accurate, and the finding frames impact honestly as Low.
  
  The defect is genuine as a hardening/consistency issue: the trust check lives in the router, not the data layer, and is applied inconsistently (delete guarded, reads not). A future change — a <path:pipeline_id> converter, a query-string source, or a new caller — would silently reintroduce traversal. This is a legitimate P3 code-quality/security observation, not a misreading, dead code, or already-handled case. Note the artifact endpoint's <name> is NOT traversal-prone because it resolves path from stored state (artifacts.get(name), line 390), not from joining name into a path; the real vector is pipeline_id only.
  ```

### personas — Persona + simulation-config generation

#### [F-5-6] Realtime Twitter CSV uses first-profile keys as fieldnames → DictWriter ValueError on profiles with extra optional fields

`P3` · `robustness` · confidence **high** · effort **S** · `backend/app/services/oasis_profile_generator.py` : 949-959

- **Symptom.** Realtime Twitter CSV save silently fails (caught and swallowed) whenever a later profile has an optional key the first profile lacks.
- **Root cause.** save_profiles_realtime() for twitter derives `fieldnames = list(profiles_data[0].keys())` from only the first profile, but to_twitter_format() emits optional keys (age/gender/mbti/country/profession/interested_topics) conditionally, so rows have heterogeneous key sets. csv.DictWriter.writerow raises ValueError ('dict contains fields not in fieldnames') for any row with an extra key; the broad except at line 958 swallows it. Additionally this realtime CSV schema (user_id,username,name,bio,persona,...) differs entirely from the OASIS-required final schema (user_id,name,username,user_char,description) produced by _save_twitter_csv.
- **Evidence.** ``fieldnames = list(profiles_data[0].keys())` (953) with conditional keys in to_twitter_format (104-115); swallowed by `except Exception as e: logger.warning(...)` (958-959)`
- **Impact.** Realtime Twitter progress file is frequently empty/stale and, even when written, is not in the schema OASIS consumes; interrupted twitter runs have no usable artifact.
- **Fix.** Route the realtime twitter branch through the same serializer as the final file so the progress artifact is both crash-free and schema-correct. In save_profiles_realtime() (oasis_profile_generator.py ~948-957) replace the manual DictWriter block with `self._save_twitter_csv(existing_profiles, realtime_output_path)`. That produces the OASIS-contract schema (user_id,name,username,user_char,description) with fixed headers via csv.writer (no DictWriter, so no heterogeneous-keys ValueError) and makes the realtime file identical in shape to what OASIS and the realtime-profiles API expect. If for some reason to_twitter_format must be retained, the minimal alternative is a fixed superset of fieldnames plus extrasaction='ignore' (fieldnames=['user_id','username','name','bio','persona','friend_count','follower_count','statuses_count','created_at','age','gender','mbti','country','profession','interested_topics']), but reusing _save_twitter_csv is strictly better because it also eliminates the schema mismatch.
- **Verified.** Confirmed by reading the code. to_twitter_format() (oasis_profile_generator.py:104-115) emits 6 optional keys (age/gender/mbti/country/profession/interested_topics) conditionally on truthiness, so profiles have heterogeneous key sets. save_profiles_realtime() at line 953 derives `fieldnames = list(profiles_data[0].keys())` from ONLY the first profile in list order. csv.DictWriter uses default extrasaction='raise', so any later row carrying an optional key the first profile lacks raises ValueError('dict contains fields not in fieldnames') — I reproduced this exactly. The broad `except Exception as e: logger.warning(...)` at lines 958-959 swallows it. This is not rare: the fallback profile path (lines 986-994) sets NONE of the optional fields, and optional fields come from per-entity LLM output (lines 275-280), so subsets genuinely differ; if a sparse profile finishes first in list order, every richer profile triggers the crash. Crucially, writeheader() (956) runs before writerows() (957) raises, so the file is left with only a header (empty data). Impact is real and the symptom holds: the realtime twitter_profiles.csv is read back live by the realtime-profiles endpoint (simulation.py:1071-1090, csv.DictReader) for UI progress, so it shows empty/stale, and an interrupted twitter run leaves no usable data. The schema-mismatch claim is also correct: the realtime path writes the to_twitter_format schema (user_id,username,name,bio,persona,...), while OASIS's consumed final file uses _save_twitter_csv's schema (user_id,name,username,user_char,description, lines 1139). Severity is downgraded P2->P3: on a normally-completing run the final _save_twitter_csv overwrites the SAME path (simulation_manager.py:404-410) with the correct OASIS schema, so the artifact OASIS actually consumes is correct on the happy path; only the progress/interrupted artifact is degraded, and the failure is swallowed (no crash). It is a real robustness/UX defect, not a happy-path correctness bug.

---

## 7. Improvements — make the workflow more powerful, robust & comprehensive

67 concrete proposals across 10 areas. Each is optional/degrade-safe. `id` prefix `I-`.

> **Implementation status (2026-06-15).** ~58 improvements shipped in the first wave
> (branch `execplan2-implementation`, merged to `main` `e587a18`). The remaining
> L-effort items were then implemented on branch `execplan2-remaining`, all
> default-off / degrade-safe / offline-unit-tested:
> - **I-7-7** forecast-quality LLM-judge harness — done (`fa7556a`, opt-in `EVAL_ENABLED`)
> - **I-1-4** entity resolution / canonical-alias merge — done (`59c420d`, `GRAPH_RESOLVE_ENTITIES`)
> - **I-1-2** faction-aware GraphRAG — done (`e5899b8`, `GRAPH_COMMUNITY_RETRIEVAL`)
> - **I-2-1** dynamic per-agent affective state — done (`e2a13b6`, `SIM_AGENT_DYNAMICS`)
> - **I-0-4** per-KIQ/per-actor research fan-out — done (`6412119`, `RESEARCH_DEEP_FANOUT`)
> - **I-6-3** concurrent report section generation — done (`90b1e91`, `REPORT_SECTION_CONCURRENCY`).
>   A full re-audit of all 67 IDs found this was the only wave-1 item not actually
>   shipped (its sole "marker" was stale `.pyc` bytecode); now implemented with
>   thread-safe ReportLogger + ZepTools retrieval caches.
> - **I-4-2** mid-run OASIS resume — **DEFERRED (upstream-blocked).** Vendored
>   camel-oasis `create_db` runs bare `CREATE TABLE` (no `IF NOT EXISTS`) and
>   `env.reset()` re-signs-up every agent (PK conflict on an existing DB); the runner
>   `os.remove`s the DB before every start *because a fresh DB is mandatory*. True
>   resume needs forking camel-oasis (idempotent `create_db` + conditional signup +
>   rec-table restore) OR a deterministic ManualAction replay engine — neither fits
>   the degrade-safe/default-off/small-feature bar. Flag `SIM_RESUME_FROM_ROUND` is
>   reserved; no dead always-degrading plumbing was shipped.
>
> A parallel adversarial review verified all five shipped features; its three
> confirmed findings (fan-out log thread-safety, affective-state memory preservation,
> faction_brief type coercion) were fixed in `f559747`.

### 7.1 Deep-research quality & coverage

#### [I-0-5] Structured quantitative_facts table with units, as-of dates, and sanity flags

`comprehensiveness` · effort **S**

- **Today.** build_deep_phase_prompt forces a '## Quantitative facts and dates' notes heading (deerflow_research.py:286) and SKILL §6 mandates number sanity checks (units/magnitude, CAGR sanity, component-sum, definition drift, as-of vs publication date). But build_extraction_prompt has NO quantitative field at all — numbers live only as free text inside the report and inside actor memory strings. Downstream, simulation_config_generator and report_agent have to re-parse numbers out of prose (or re-search), and there is no machine-readable place for the as-of date, unit, or definition that the SKILL insists every number carry. The bi-temporal grounding work (T2.3, dates.py parse_as_of) has clean date handling for events but nothing analogous for quantities.
- **Proposed.** Add an optional quantitative_facts array to the extraction schema: [{metric, value, unit, as_of_date, definition, source, tier}]. Promote it (like timeline.json) to a first-class quantitative.json artifact. Render a compact table into the report background via actors.py, and optionally feed magnitudes to simulation_config_generator (e.g., audience sizes, market shares informing influence weights). Gate behind RESEARCH_FORECAST_INPUTS (shared) or its own flag; prompt-only, no extra tool cost.
- **Why.** Quantitative grounding is what separates a forecast from a vibe. Structuring numbers with units + as-of date + definition lets the report agent cite exact figures without re-searching, lets downstream sanity-check magnitudes, and prevents definition-drift errors the SKILL warns about. It also gives the influence-weighting and event-scheduling logic real magnitudes to work with instead of the current high/medium/low buckets.
- **Design.** Schema: "quantitative_facts": [{"metric": str, "value": str, "unit": str, "as_of_date": str, "definition": str, "source": str, "tier": str}]. In main(): after parsing obj, if quantitative_facts present and non-empty, write quantitative.json and set meta['quantitative_count'] (mirror the key_events/timeline block at lines 975-979). actors.py: quantitative_facts_block(actors, max=20) renders a markdown table; pipeline_orchestrator.add_if('quantitative', quantitative.json).
- **Impact.** Report cites exact, dated, defined figures; simulation can use real magnitudes; numbers stop being re-derived from prose. Tighter, more credible quantitative forecasts.
- **Depends on.** Mirrors the existing key_events -> timeline.json promotion pattern (deerflow_research.py:975-979). Touches deerflow_research.py (build_extraction_prompt schema + a quantitative.json writer + meta count), actors.py (quantitative_facts_block + extractor), report_agent background injection, optionally simulation_config_generator and pipeline_orchestrator file-add list (line ~1352).
- **Risk.** Low. Additive optional array + a new best-effort file write inside the existing try block; degrades to current behavior when omitted (matches timeline.json's already-proven pattern). Risk of unit-less or mis-defined numbers; mitigated by requiring unit+as_of_date per entry and keeping it optional.

#### [I-0-0] Carry source tiering (S1-S4), dates, and per-claim grading into sources.json and the report contract

`comprehensiveness` · effort **M**

- **Today.** build_extraction_prompt (deerflow_research.py:455-507) emits sources as a flat list of {title, url} only. SKILL.md §4 defines an explicit S1-S4 source-quality framework and §5 an Admiralty-style grading scheme (letter for reliability, digit for credibility), but NONE of that survives extraction: the model is told to write [S1]/[S2] flags in prose but the JSON sources have no tier, no date, no grade. Downstream, report_agent.py:_build_sources_index (lines 1035-1049) just re-numbers sources as [S1]..[S40] by position (a misleading reuse of the S-prefix that has nothing to do with the tier), and pipeline_orchestrator reads sources.json verbatim. The tradecraft the SKILL prescribes is invisible to every downstream consumer.
- **Proposed.** Extend the sources schema in build_extraction_prompt to {title, url, tier (S1|S2|S3|S4), date, supports (list of short claim refs), independent (bool)} and add a per-actor/per-relationship/per-event optional grade field (Admiralty B2-style). Gate with a new Config flag RESEARCH_EVIDENCE_GRADING (default true for the prompt-only change since it adds no tool cost; the report-side rendering reads it). Fix _build_sources_index to render the real tier instead of positional [S1]. Keep all fields optional so a model that omits them degrades to today's behavior exactly.
- **Why.** Source provenance and grading are the core of decision-grade forecasting (SKILL §1-§6) yet are currently discarded at the JSON boundary. Surfacing tier+grade lets the report agent weight evidence (prefer S1/B2 claims), lets the graph builder attach confidence to seeded edges, and lets a coverage gate (see other proposal) reject reports built on S4 slop. This directly raises forecast quality by making the pipeline reason over evidence strength, not just evidence presence.
- **Design.** In build_extraction_prompt: change sources schema to objects with tier/date/supports/independent; add optional "grade": "A1"|"B2"|... to actor and relationship objects. New actors.py helper sources_index_tiered(sources) grouping by tier and emitting [S1-a]/[S2-b] style refs with dates. report_agent._build_sources_index calls it when Config.RESEARCH_EVIDENCE_GRADING else falls back to current. meta.json gains tier histogram (s1_count..s4_count) for observability.
- **Impact.** Every downstream stage gains evidence-strength signal: report can show conflicts-not-averaged with weighting, graph edges carry confidence, sources index stops lying about tiers. Forecasts become defensible and auditable.
- **Depends on.** Extends the existing actors.json/sources.json contract and actors.py helpers; touches deerflow_research.py (build_extraction_prompt), backend/app/utils/actors.py (a tier-aware sources renderer), report_agent.py:_build_sources_index, config.py (RESEARCH_EVIDENCE_GRADING flag).
- **Risk.** Low-medium. Prompt-only schema additions cannot break parsing (extract_json_object tolerates extra/missing keys; actors.py degrades on missing fields). Main risk is models inventing tiers; mitigated by instructing tiering be evidence-based and keeping the field optional. No added tool/LLM cost in the extraction pass.

#### [I-0-1] Add a structured contested_claims / evidence-conflicts array to the extraction contract

`comprehensiveness` · effort **M**

- **Today.** The deep protocol has a dedicated contradictions-and-risks phase (DEEP_RESEARCH_PHASES[3], deerflow_research.py:101-111) and build_deep_phase_prompt forces a '## Contradictions or uncertainty' notes heading (lines 283-289). SKILL.md §6-§7 (triangulation, circular-sourcing, ACH-lite, conflicting numbers) is the methodological centerpiece. Yet build_extraction_prompt produces only situation_brief.fault_lines (a flat string list of issues actors argue over) and hot_topics. There is NO structured representation of where the EVIDENCE itself conflicts (two sources disagree on a number, a claim is single-origin, a thesis has a live bear case). All that adversarial work done in the deep passes is collapsed into prose and lost to the graph, simulation, and report.
- **Proposed.** Add an optional contested_claims array to the extraction schema: [{claim, positions: [{stance, sources, tier}], status: confirmed|contested|speculative|single-origin, why_they_differ}]. Populate it from the contradictions-and-risks pass for deep depth (and best-effort for standard). Render it via a new actors.py helper contested_claims_block() injected into the report background and optionally into persona memory so simulated actors hold genuinely disputed beliefs. Gate behind RESEARCH_EVIDENCE_GRADING (shared with the tiering proposal).
- **Why.** Forecast quality hinges on knowing what is actually contested vs. settled (SKILL §11 synthesis gate, §12.4 conflicts-shown-not-averaged). A simulation seeded with contested claims produces realistic disagreement among agents; a report that surfaces conflicts with their reasons is far more credible than one that silently averages. This turns the existing-but-discarded adversarial research into a first-class, machine-readable asset.
- **Design.** Schema: "contested_claims": [{"claim": str, "positions": [{"stance": str, "sources": [str], "tier": str}], "status": enum, "why_they_differ": str}]. actors.py: contested_claims_block(actors, max=8) renders a zh '## 争议证据' block; extract_contested_rows() validates shapes. Inject into report_agent._build_background_block and (optional) into the matched actor's persona memory in oasis_profile_generator when SIM_SEED_CONTESTED flag set.
- **Impact.** Simulation agents can be seeded with genuinely opposing beliefs (richer dynamics); report shows ranges + why-sources-differ instead of false precision; graph can mark contested edges. Materially higher forecast calibration.
- **Depends on.** Builds on the deep contradictions phase that already exists; touches deerflow_research.py (build_extraction_prompt, build_deep_phase_prompt nudge), actors.py (contested_claims_block + a safe extractor), oasis_profile_generator.py and report_agent background block to inject it. No new tool calls.
- **Risk.** Low. Optional array, parser-tolerant, degrades to current behavior when omitted. Slight risk of the model over-flagging trivial disagreements; mitigated by capping count and requiring each entry cite >=2 sources or a single-origin flag.

#### [I-0-2] Emit forecast-input objects: drivers, watchable indicators, base rates, and scenarios

`capability` · effort **M**

- **Today.** SKILL.md §8 (Forecast-Oriented Research) explicitly prescribes outside-view base rates / reference classes, drivers + watchable indicators (3-6 variables with dated signals), and trend-vs-break analysis, and the deep forecast-implications phase (DEEP_RESEARCH_PHASES[4], lines 112-122) instructs the model to gather 'timelines, catalysts, leading indicators, measurable variables, base/upside/downside scenarios'. The synthesis prompt for deep depth even asks the WRITTEN report to include 'base/upside/downside scenarios, leading indicators' (build_synthesis_prompt lines 307-314). But build_extraction_prompt has NO field for any of these: situation_brief.catalysts is the only forecast-ish field, and it is just a flat string list. So the single most forecast-relevant research output exists in prose but is never structured for the pipeline whose entire purpose is forecasting.
- **Proposed.** Add an optional forecast_inputs object to the extraction schema: {base_rates: [{reference_class, outcome_frequency, basis}], drivers: [{variable, direction, why_it_matters}], indicators: [{indicator, signals_what, date_or_trigger}], scenarios: [{name: base|upside|downside, probability_band, narrative, key_assumptions}]}. Render via actors.py forecast_inputs_block() pinned into the report background, and feed scenarios/indicators into simulation_config_generator so the simulation tracks the variables that actually move the outcome. Gate behind a new Config flag RESEARCH_FORECAST_INPUTS (default true; prompt-only, no extra tool cost).
- **Why.** This is the highest-leverage gap: the pipeline forecasts, the SKILL researches for forecasting, the deep passes gather forecast inputs, the synthesis writes them in prose — and then the contract throws them away. Structuring base rates (outside view), drivers, indicators, and probability-banded scenarios gives the report agent a calibrated scaffold and lets the simulation be steered by the real causal variables rather than re-deriving them. Directly raises forecast quality and makes predictions falsifiable via dated indicators.
- **Design.** Schema: "forecast_inputs": {"base_rates": [...], "drivers": [...], "indicators": [{"indicator": str, "signals_what": str, "date_or_trigger": str}], "scenarios": [{"name": enum, "probability_band": str, "narrative": str, "key_assumptions": [str]}]}. actors.py: forecast_inputs_block(actors) renders zh sections; indicators_to_schedule() can reuse parse_as_of to place dated indicators on simulation rounds. report_agent pins the block; simulation_config_generator reads drivers/indicators when RESEARCH_FORECAST_INPUTS set.
- **Impact.** Report gains a grounded scenario/probability scaffold and dated indicators to monitor; simulation can track driver variables; forecasts become calibrated and checkable instead of narrative. Largest single quality uplift in this focus area.
- **Depends on.** Leverages the existing deep forecast-implications phase. Touches deerflow_research.py (build_extraction_prompt, optionally a sharper forecast-implications nudge), actors.py (forecast_inputs_block + safe extractor), report_agent background injection, simulation_config_generator (optional indicator/driver wiring), config.py flag. Synergizes with events_to_schedule (indicators with dates can also map to rounds).
- **Risk.** Low-medium. Optional object, parser-tolerant, degrades cleanly. Risk that smaller models hallucinate base rates without real reference classes; mitigated by requiring a basis string per base_rate and keeping the whole object optional so a weak model simply omits it.

#### [I-0-3] Coverage-and-quality gate after research, before downstream consumption

`robustness` · effort **M**

- **Today.** pipeline_orchestrator validates only that research_report.md exists and is non-empty (pipeline_orchestrator.py:492-513), and deerflow_research.py guards length (SYNTHESIS_TRIGGER_CHARS=4000) and LLM-error fallbacks (looks_like_llm_error). There is NO check that the report actually COVERS the cast the question implies, that load-bearing claims are sourced, or that the SKILL §11 synthesis gate was met. A report can name 2 actors for a 12-actor situation, cite zero S1 sources, or have a situation_brief with empty fault_lines, and the pipeline will happily build an ontology, graph, and simulation on top of it and report success.
- **Proposed.** Add an optional post-extraction quality gate in deerflow_research.py main() that computes a coverage/quality scorecard from actors.json + sources.json (actor count vs. a target, fraction of actors with role+stance+memory filled, relationship density, presence of dated key_events, S1/S2 source fraction, situation_brief completeness) and writes it to meta.json as research_quality. If the score falls below a configurable floor AND depth allows, trigger ONE targeted gap-filling pass (reusing run_streamed_turn on the same thread with a gap-specific prompt) rather than restarting. Gate behind Config flag RESEARCH_QUALITY_GATE (default false to preserve current behavior/cost; when off, only the scorecard is written, which is free observability).
- **Why.** Garbage-in-garbage-out is the dominant failure mode: a thin or unsourced dossier silently degrades the graph, personas, and report while the run reports success. A scorecard turns silent quality variance into a measurable signal (observability), and the optional gap-pass turns it into self-correction (SKILL §11: 'any NO -> one targeted pass on that gap only'). This is the single biggest robustness win for forecast trustworthiness at scale.
- **Design.** compute_research_quality(actors_obj, sources_obj) -> {coverage: float, actor_fill: float, rel_density: float, sourced: float, s1s2_frac: float, brief_complete: bool, score: float}. Written to meta['research_quality'] always. If Config.RESEARCH_QUALITY_GATE and score < floor: build a gap prompt naming the weakest dimensions ('only 3 actors found, no dated events, 0 S1 sources') and run one extra research turn on thread_id, then re-extract. Cap via RESEARCH_GAP_PASS_RECURSION_LIMIT.
- **Impact.** Stops the pipeline from building forecasts on hollow research; gives operators a per-run quality number; optional auto-remediation raises floor quality without manual reruns. Major robustness + observability gain.
- **Depends on.** Reads the existing actors.json/sources.json contract via actors.py extractors; reuses run_streamed_turn for the gap pass. Touches deerflow_research.py (new compute_research_quality() + gate in main after extraction), config.py (RESEARCH_QUALITY_GATE, RESEARCH_QUALITY_FLOOR, RESEARCH_MIN_ACTORS), pipeline_orchestrator.py (optionally surface research_quality to the UI/meta).
- **Risk.** Medium. The gap pass adds tool/LLM cost and latency, so it is gated off by default and capped at one pass with its own recursion budget. Scoring heuristics could misjudge a legitimately small cast; mitigated by making the floor a soft warning unless the gate flag is on, and always allowing the run to proceed (never hard-fail on borderline coverage).

#### [I-0-4] Per-KIQ / per-actor subagent fan-out for the deep protocol

`performance` · effort **L**

- **Today.** The deep protocol (run_research_stage, deerflow_research.py:694-745) is a fixed LINEAR sequence: opening + 5 named phases + synthesis, all on ONE thread, each phase a single sequential agent turn. The DeerFlowClient already supports subagents (subagent_enabled, client.py:124/164/238-261; --subagents CLI flag and Config.DEERFLOW_SUBAGENTS exist) but the deep protocol does not exploit them for parallel breadth — it just optionally enables delegation inside each serial turn. SKILL §10 explicitly budgets '1/4 scoping, 1/2 targeted deep-dive, 1/4 disconfirmation' per dimension, which is naturally parallel across actors/KIQs, but the current shape forces one dimension at a time and re-reads the whole thread each turn (context bloat).
- **Proposed.** Add an optional fan-out mode for deep depth: after the opening scope pass produces the KIQ/actor list, dispatch parallel scoped sub-investigations (one per top KIQ or per high-influence actor) — either via the existing subagent delegation with explicit per-worker briefs, or via N short-lived threads run concurrently — then merge their working notes before the contradictions and synthesis passes. Gate behind a new Config flag RESEARCH_DEEP_FANOUT (default false) with RESEARCH_FANOUT_WIDTH cap. When off, the current linear protocol runs unchanged.
- **Why.** Breadth across the full cast is exactly the focus-area goal (coverage of the full cast) and is embarrassingly parallel. Fanning out lets each actor/KIQ get dedicated budget instead of competing in one serial pass, improving both coverage and depth-per-actor, and parallelism cuts wall-clock for deep runs (currently up to 10800s budget). It uses infrastructure that already exists but is underused.
- **Design.** After opening pass, parse a seed list (top N actors/KIQs). For each, build a scoped worker prompt (SKILL §10 budget split) and run concurrently via threads/asyncio over client.stream with separate thread_ids OR via subagent delegation. Collect worker notes, prepend to the main thread context, then run contradictions + synthesis as today. New helper run_deep_fanout(client, question, seeds, ...). Observability: per-worker tool-call counts logged to research_progress.log.
- **Impact.** Deeper per-actor evidence and broader cast coverage at lower wall-clock for deep runs; better use of the metered tool budget by allocating it per-dimension. Scales the research stage to large-cast situations.
- **Depends on.** Requires the opening pass to emit a machine-parseable KIQ/actor seed list (small extension to build_research_prompt deep / a lightweight mid-protocol extraction). Builds on DeerFlowClient subagent support and run_streamed_turn. Touches deerflow_research.py (run_research_stage fan-out branch, a merge step), config.py (RESEARCH_DEEP_FANOUT, RESEARCH_FANOUT_WIDTH), pipeline_orchestrator timeout budgeting.
- **Risk.** Medium-high. Parallel workers multiply tool/LLM cost and can duplicate searches; mitigated by width cap, per-worker scoped briefs, and a dedup/merge step. Concurrency adds failure modes (a worker crashing); mitigated by best-effort merge (a dead worker just contributes nothing, mirroring the existing 'salvage partial output' pattern in run_streamed_turn). Default-off keeps current behavior byte-identical.

### 7.2 Developer experience & ops

#### [I-8-4] Close DeerFlow provider parity: thinking-disable + reasoning params for qwen/glm/kimi research stanzas, and lift OASIS semaphore knobs into Config

`comprehensiveness` · effort **S** · deerflow_bridge/config.yaml (qwen/glm/kimi stanzas); deerflow_bridge/patches/models/ (optional wrapper reuse); backend/app/config.py (OASIS_CLI_SEMAPHORE/OASIS_SEMAPHORE attrs); backend/scripts/run_parallel_simulation.py + backend/app/utils/oasis_llm.py (read from Config).

- **Today.** Provider handling diverges between the backend and the research engine. In `deerflow_bridge/config.yaml`, `claude`/`minimax`/`deepseek`/`codex` use dedicated patched provider classes, but `qwen`, `glm`, and `kimi` use the raw `langchain_openai:ChatOpenAI` (config.yaml lines 134-137, 158-161, 201-204) -- so they get none of the thinking-disable / reasoning-budget handling the backend so carefully applies via `Config.reasoning_extra_body()` and `_DISABLE_THINKING_EXTRA_BODY` (config.py:99-125). The backend disables 'thinking' for these reasoning models precisely because leftover reasoning exhausts max_tokens and yields empty/truncated content (config.py:91-98) -- but the *research* stage on qwen/glm/kimi has no such guard. Separately, `OASIS_CLI_SEMAPHORE` and `OASIS_SEMAPHORE` are documented in `.env.example` (lines 104-107) and read inside `scripts/run_parallel_simulation.py`/`oasis_llm.py`, but are NOT defined on `Config` -- so they bypass the otherwise-centralized config surface and are invisible to doctor/manifest.
- **Proposed.** (1) For the OpenAI-compatible research stanzas (qwen/glm/kimi) in the bridge config.yaml, either route them through a thinking-disabling provider wrapper (mirror `patched_deepseek`/`patched_minimax`) or add the equivalent reasoning-off params, so research-stage output on these providers is as parse-stable as the backend's report/sim stage. (2) Promote `OASIS_CLI_SEMAPHORE`/`OASIS_SEMAPHORE` to first-class `Config` attributes (read by the runner via Config, not raw os.environ) so they appear in `Config.validate()`, the doctor report, and the run manifest. Both changes preserve current numeric defaults.
- **Why.** Provider parity is correctness, not polish: a deep-research run on qwen/glm/kimi today can silently degrade (empty sections from reasoning eating the token budget) while the same model behaves on the report stage -- a confusing, hard-to-diagnose quality cliff. Centralizing the OASIS concurrency knobs closes a config-surface gap so the single-source-of-truth invariant the codebase already strives for (PROVIDER_META, DEERFLOW_KEY_ENV) actually holds end-to-end, and makes concurrency tuning visible/reproducible.
- **Design.** In config.yaml, give qwen/glm/kimi a `when_thinking_enabled`/disable block or switch `use:` to a thin patched wrapper that injects `extra_body={thinking:{type:disabled}}` (qwen: `enable_thinking:false`), mirroring `_DISABLE_THINKING_EXTRA_BODY`. In config.py add `OASIS_CLI_SEMAPHORE=int(os.environ.get(...,'3'))`, `OASIS_SEMAPHORE=int(os.environ.get(...,'30'))`; have `get_oasis_semaphore` read Config. Include both in environment_report().
- **Impact.** More reliable, higher-quality research output on three providers; consistent behavior across stages; concurrency tuning becomes discoverable and recorded in the manifest.
- **Depends on.** Config.yaml stanza edits + optionally reuse existing `patched_*` provider pattern in `deerflow_bridge/patches/models/`. Reading semaphore values from Config in the OASIS runner script.
- **Risk.** Low. Stanza changes only affect runs that select qwen/glm/kimi as `DEERFLOW_MODEL`; defaults unchanged for claude/codex/minimax/deepseek. Semaphore promotion is a pure refactor with identical default values, so behavior is byte-stable when the env vars are unset.

#### [I-8-5] Add a checked-in `.env.example` <-> Config drift validator wired into doctor and CI

`testing` · effort **S** · new backend/scripts/check_env_example.py; scripts/doctor.sh (invoke as warning); .env.example (fix the drift it finds); optionally package.json (npm run check:env).

- **Today.** `backend/app/config.py` reads ~50 env knobs via `os.environ.get(...)`, and `.env.example` documents them in parallel prose, but nothing keeps the two in sync. Concrete drift already exists: `OASIS_CLI_SEMAPHORE`/`OASIS_SEMAPHORE` appear in `.env.example` (lines 104-107) but not in `config.py`; `LLM_BOOST_*` appear in `.env.example` (lines 98-102) with no corresponding Config read shown; conversely many Config knobs (e.g. `GRAPHITI_REMOTE`, `DEERFLOW_DEEP_OPENING_RECURSION_LIMIT`, `REPORT_AGENT_MAX_TOOL_CALLS_CHAT`) are easy to omit from the example. A user copying `.env.example` can set a typo'd or stale knob and get silent no-ops. There is no automated check; README's claim of documenting 'every knob' (README:26) is unverifiable.
- **Proposed.** Add `backend/scripts/check_env_example.py` that (a) statically extracts every `os.environ.get('NAME', ...)` / `os.environ.get('NAME')` key referenced in `config.py` (and the OASIS scripts), (b) parses the `NAME=`/`# NAME=` lines from `.env.example`, and (c) reports keys present in code but undocumented in the example, and keys documented in the example but never read by code. Wire it into `doctor` (as a warning) and as a standalone CI-friendly check with a nonzero exit on drift.
- **Why.** Docs accuracy is a forecast-quality lever in disguise: the knobs that change forecast behavior (agent count, research depth, concurrency, thinking-disable, communities) are exactly the ones most likely to drift out of the example and get mis-set. A mechanical drift check turns 'docs claim to be complete' into 'docs are provably complete', and surfaces dead/renamed knobs (the ZEP_* legacy names) for cleanup. It's the cheapest durable guard against the recurring 'I set X in .env and nothing happened' class of issues.
- **Design.** Use `ast.walk` over config.py to collect `Call` nodes where func is `os.environ.get` and first arg is a `Constant` str (also handle `os.environ[...]`). Parse `.env.example` for `^#?\s*([A-Z0-9_]+)=`. Diff the two sets minus ALLOWLIST. Print two sections (UNDOCUMENTED / UNUSED) and `sys.exit(1 if UNDOCUMENTED else 0)`. doctor.sh runs it with `|| warn 'env example drift -- see above'`.
- **Impact.** Guarantees .env.example documents exactly the knobs the code reads; catches stale/typo'd config keys mechanically; makes config onboarding trustworthy.
- **Depends on.** Pure stdlib (ast + regex). Optional allowlist for intentionally-undocumented internal vars (WERKZEUG_RUN_MAIN, etc.).
- **Risk.** Low. Static analysis only, no runtime impact. False positives handled by a small explicit allowlist in the script. Runs in doctor as a non-blocking warning so it never gates a real run.

#### [I-8-0] Unify doctor.sh + preflight_pipeline behind one Python preflight engine, exposed as `doctor --json` and `GET /api/research/preflight`

`devex` · effort **M** · scripts/doctor.sh; backend/app/services/pipeline_orchestrator.py (preflight_pipeline); backend/app/config.py (new environment_report()); new backend/scripts/preflight.py; backend/app/api/research.py (preflight route).

- **Today.** Two independent implementations of the same environment checks exist and are drifting. `scripts/doctor.sh` (bash) re-derives, in shell, the very logic `backend/app/services/pipeline_orchestrator.py::preflight_pipeline()` and `Config.validate()` already encode in Python: provider->key-env mapping (doctor.sh:133-156 hardcodes `df_key_check minimax MINIMAX_API_KEY` etc., duplicating `Config.DEERFLOW_KEY_ENV` at config.py:377-380), placeholder-key detection (doctor.sh:125-127 hardcodes `your_api_key|your_api_key_here` while `Config._PLACEHOLDER_VALUES` at config.py:391-394 has a richer set), and graph-backend importability (doctor.sh:108 vs preflight_pipeline lines 645-662). doctor.sh emits only human prose to a TTY (no `--json`), so CI / the frontend cannot consume it. The comment at config.py:684 ('single source of truth, shared with validate()/doctor.sh, avoiding three-place drift') states the intent but the bash copy violates it.
- **Proposed.** Make the Python `preflight_pipeline` the single source of truth and have `doctor.sh` become a thin wrapper that shells into the backend venv to run it, plus add structured output. Concretely: (1) add a `--json` flag to a new `backend/scripts/preflight.py` CLI that calls `preflight_pipeline(mode, model)` + a new `Config.environment_report()` and prints either colored text or a JSON document; (2) rewrite `doctor.sh` to call `backend/.venv/bin/python backend/scripts/preflight.py` for the credential/graph-backend/deerflow-model checks (keeping only the pure-tooling checks -- node/uv/git/venv existence -- in bash, since those must run before the venv exists); (3) add `GET /api/research/preflight?format=full` returning the same structured report so the UI Settings page can render a live readiness panel.
- **Why.** Eliminates a real correctness hazard: today a new provider added to `PROVIDER_META`/`DEERFLOW_KEY_ENV` is checked correctly by the server but silently mis-reported by doctor.sh until someone hand-edits the bash case statements. A single engine means doctor, the POST /run gate, and the UI can never disagree about whether a run will succeed. JSON output unlocks CI gating and a UI readiness widget.
- **Design.** New `Config.environment_report() -> dict` returning {provider, deerflow_model, graph_backend, venv_python, deerflow_dir, checks:[{id,severity,ok,message,fix}]}. `backend/scripts/preflight.py`: argparse `--json/--mode/--model`; severity exit code (0 ok, 1 blocking). doctor.sh keeps bash tooling block, then `PYJSON=$($BE_PY backend/scripts/preflight.py --json --mode full || true)` and pretty-prints. Reuse `Config.DEERFLOW_KEY_ENV` and `Config._PLACEHOLDER_VALUES` so bash never re-encodes them.
- **Impact.** Removes config drift across three check sites; gives CI a machine-readable gate; lets the frontend show exactly which credential is missing before the user spends 40 minutes of research budget.
- **Depends on.** Backend venv must exist for the Python portion (bash keeps a pre-venv fallback path that prints 'run uv sync first'). No new packages.
- **Risk.** Low. doctor's pure-tooling checks stay in bash so a broken venv still produces actionable output. The JSON schema is additive; default text output is byte-compatible with today's doctor for existing users.

#### [I-8-1] Write a per-run reproducibility manifest (run.json) capturing resolved provider/model/depth/seeds/versions/git SHA

`observability` · effort **M** · backend/app/services/pipeline_orchestrator.py (PipelineState + _run start, per-stage model resolution); backend/app/api/research.py (new manifest route); backend/app/config.py (new flags).

- **Today.** `PipelineState` (pipeline_orchestrator.py:123-180) persists prompt, mode, stage ids, options, artifacts and a research PID, but does NOT record the *resolved* execution environment: which `LLM_PROVIDER`/`LLM_MODEL_NAME` actually ran each stage, the effective `DEERFLOW_MODEL`+depth+timeout budget, `OASIS_MAX_AGENTS`/round count, `GRAPHITI_EMBED_MODEL`, the deer-flow commit (`DEERFLOW_REF`), or any package versions / git SHA of this repo. Provider can be switched at runtime mid-flight via `Config.apply_provider` (settings.py:38), so the .env at inspection time may not reflect what a given report was generated with. Today a finished forecast cannot be reliably reproduced or audited -- you cannot answer 'what model wrote section 3, with how many agents, off which research depth?'
- **Proposed.** At pipeline start (and updated as each stage resolves its model), write `uploads/pipelines/<id>/run.json` -- an immutable-ish manifest snapshotting the fully-resolved run configuration plus environment fingerprints, with secrets redacted. Surface it via a new `GET /api/research/<id>/manifest` and link it from the StageTimeline. Gate any extra cost (e.g. capturing `uv pip freeze`) behind a `Config.RECORD_RUN_MANIFEST` flag (default true for the cheap fields, the version-freeze behind `MANIFEST_CAPTURE_VERSIONS=false`).
- **Why.** Reproducibility and auditability are the backbone of forecast quality: a prediction is only as trustworthy as the ability to say exactly how it was produced and to re-run it. Because the provider is hot-swappable, the manifest is the *only* reliable record of what generated a given report. It also makes A/B comparison of forecasts (claude vs minimax research, 40 vs 80 agents) a first-class, queryable artifact instead of tribal knowledge.
- **Design.** `run.json` shape: {pipeline_id, created_at, repo_git_sha, deerflow_ref, resolved:{research:{model,depth,timeout_s}, ontology/graph/report:{provider,model_name}, simulation:{max_agents,total_rounds,recsys_wired}}, graph:{backend,embed_model,embed_dim,reranker}, env_fingerprint:{python, key_packages?}, secrets_redacted:true}. Populate `resolved` incrementally as `_run` enters each stage (the provider is already read from Config at each stage boundary). Add `Config.run_manifest_snapshot()` to centralize.
- **Impact.** Every forecast becomes replayable and auditable; enables side-by-side run comparison and post-hoc debugging ('this report used the placeholder embedding model / wrong depth'). Near-zero runtime cost for the default fields.
- **Depends on.** Reads `Config`, `git rev-parse`, deer-flow commit (already known to setup.sh via DEERFLOW_REF; read from `deer-flow/.git` if present or record 'vendored'). No new packages for the default path.
- **Risk.** Low. Manifest write is best-effort (wrapped like `_persist_env`'s try/except at config.py:222-248) so it never breaks a run. Must redact `LLM_API_KEY`/provider keys -- reuse the redaction helper from the secrets-hygiene item.

#### [I-8-2] Add a `doctor --deep` live-credential + first-run-asset probe (key reachability + embedding model presence + disk)

`robustness` · effort **M** · scripts/doctor.sh; new backend/scripts/preflight.py (--deep); refactor backend/app/api/settings.py test helpers into backend/app/services/connectivity.py; backend/app/config.py.

- **Today.** All current readiness checks are deliberately offline/cheap: `preflight_pipeline` does 'only cheap local checks (file existence / PATH / env vars), no network requests' (pipeline_orchestrator.py:631), and doctor.sh only checks importability/PATH/env presence. The one live test is in setup.sh (lines 389-416) and only fires when a key is *freshly typed during setup* -- a re-run, a key edited directly in .env, or a CLI OAuth token that has since expired is never live-verified until ~30+ minutes into a run. Separately, first graph build silently downloads a ~470MB sentence-transformers model (`GRAPHITI_EMBED_MODEL`, config.py:265; README:182); doctor never checks whether that model is cached or whether there is disk space, so an offline first run fails deep inside GraphBuilderService instead of at the doctor gate.
- **Proposed.** Add an opt-in `doctor --deep` (and `preflight.py --deep`) mode that performs the bounded, expensive checks the fast path skips: (1) a 1-token live completion against the configured `LLM_PROVIDER` (reusing settings.py `_test_openai_compat_provider` / `_test_cli_provider` logic) and the configured `DEERFLOW_MODEL`'s credential; (2) verify the `GRAPHITI_EMBED_MODEL` is present in the HF cache (and, if absent and online, offer to pre-pull it); (3) a free-disk check against `GRAPHITI_DATA_DIR`/uploads. Default doctor stays fast and offline; `--deep` is explicit.
- **Why.** Moves the most expensive class of failures (bad/expired key, un-cached embedding model, full disk) from 'discovered mid-run after burning budget' to 'discovered in a deliberate ~15s check'. This directly protects forecast throughput: the single biggest waste in this pipeline is a long research stage that succeeds, then a graph build that dies on a missing embedding model or a report stage that 401s. Reusing the already-written settings test logic keeps it DRY.
- **Design.** `connectivity.py`: `probe_llm(provider, key, base, model) -> {ok, latency_ms, status, hint}` (moved from settings.py), `probe_deerflow_model(model)`, `embedding_model_cached(name) -> bool` (check `huggingface_hub.try_to_load_from_cache`), `free_disk_bytes(path)`. doctor `--deep` calls `preflight.py --deep --json`, renders latency + cache status. Exit code 2 = deep warnings, 1 = blocking, 0 = ok.
- **Impact.** Catches the costliest first-run and stale-credential failures before any spend; makes onboarding on a fresh/offline machine deterministic.
- **Depends on.** Reuses `backend/app/api/settings.py` test helpers (factor them into a shared `backend/app/services/connectivity.py`). HF cache path via `huggingface_hub` (already a transitive dep of sentence-transformers).
- **Risk.** Low-medium. Live probes cost a few tokens and need network -- strictly opt-in via `--deep`, so the default contract (offline, free, instant) is preserved. Embedding pre-pull must be gated behind an explicit `--pull` to avoid surprise 470MB downloads.

#### [I-8-3] Centralize secret redaction + support external secret sources (file/env-indirection) and stop full-env subprocess leakage

`robustness` · effort **M** · backend/app/config.py (redact helpers, *_API_KEY_FILE resolution, key_env defaults); backend/app/services/pipeline_orchestrator.py (subprocess env build ~line 389); backend/app/api/settings.py (stop returning raw traceback / redact); .env.example (document *_API_KEY_FILE).

- **Today.** Secrets are handled ad-hoc. `LLM_API_KEY` is stored plaintext in `.env` and *mirrored* into up to five provider-specific vars (`apply_provider` config.py:208-215; setup.sh:355-363). The DeerFlow subprocess inherits the entire parent environment via `env = dict(os.environ)` (pipeline_orchestrator.py:389) -- every key for every provider, not just the one in use. config.py:446-449 deliberately `setdefault`s every `key_env` to '' so a missing key doesn't crash config.yaml parsing, which is correct but means empty/real keys all flow to the child. There is no single redaction helper: settings.py truncates error strings (lines 118-120) but nothing guarantees a key never lands in a log/manifest/traceback. settings.py:50 returns `traceback.format_exc()` to the client on error, which can include request context.
- **Proposed.** (1) Add a single `Config.redact(value)` / `redact_mapping(env)` helper and route every place that could surface a secret (the new run manifest, the settings error responses, any debug logging of env) through it. (2) Support secret indirection: allow `LLM_API_KEY_FILE` / `*_API_KEY_FILE` (read key from a file path) so keys need never be written into `.env` -- a standard ops pattern for Docker/secret-mounts. (3) Scope the DeerFlow subprocess env: pass only the keys the selected `DEERFLOW_MODEL` needs plus a curated allowlist, instead of the whole `os.environ`. All three are optional and default to current behavior when the new vars are unset.
- **Why.** Secrets hygiene is a baseline ops requirement the project currently only half-meets. `*_API_KEY_FILE` indirection lets the system run in containerized/CI/secret-manager environments without ever persisting plaintext keys (the current `.env`-mirroring model forces plaintext). Scoping the child env reduces blast radius if the deer-flow subprocess logs its environment. A central redactor makes the new reproducibility manifest safe to ship by design.
- **Design.** `Config.redact(v)` -> 'sk-...last4' or '***'. `_resolve_key_files()` at module load: for each KEY in {LLM_API_KEY, *provider key_envs}, if `{KEY}_FILE` set and readable, `os.environ.setdefault(KEY, open(path).read().strip())`. `build_deerflow_env(model)` -> dict(os.environ filtered to ALLOWLIST + DEERFLOW_KEY_ENV[model]) with every other provider key_env set to '' (preserve parse safety). Gate scoping behind `Config.DEERFLOW_SCOPED_ENV`.
- **Impact.** Enables secret-manager / Docker-secret deployments without plaintext keys; shrinks secret blast radius to the subprocess; guarantees keys cannot leak into manifests/logs/error payloads.
- **Depends on.** Touches config.py loading order and pipeline subprocess construction. The `_API_KEY_FILE` resolution must run at import time before `PROVIDER_META`/`LLM_API_KEY` are read.
- **Risk.** Medium. Subprocess env scoping is the riskiest part -- deer-flow's config.yaml greedily resolves `$VAR` for every stanza (config.py:441-449 comment), so the allowlist must still include empty defaults for all provider key_envs to avoid the documented parse failure. Keep current behavior behind a `DEERFLOW_SCOPED_ENV=false` default until validated.

### 7.3 Forecast quality & calibration

#### [I-3-2] Quantitative simulation-signal pack auto-injected into every section (deterministic grounding floor)

`comprehensiveness` · effort **S**

- **Today.** The deterministic structured tools simulation_outcomes/coalition_map/opinion_shift/scenario_diff (zep_tools.py:1803-1978) are powerful but only used if the LLM chooses to call them. In practice the ReAct loop nudges toward unused tools (REACT_UNUSED_TOOLS_HINT, report_agent.py:834) but a section can satisfy MIN_TOOL_CALLS (default 4) entirely with insight_forge/quick_search and never touch a single hard number. plan_outline pre-fetches one simulation_outcomes sweep (line 1382) but individual sections don't get a guaranteed quantitative floor.
- **Proposed.** Compute a compact, deterministic 'simulation signal pack' ONCE per report (top actors, per-round action volume + peak, action-type mix, coalition count/sizes, and if applicable scenario_diff totals) and inject it as an authoritative block into every section's system prompt — the same way situation_brief is pinned via _prepend_research_background. The model can still call tools for depth, but it can never write a chapter with zero quantitative grounding. Gate behind a flag; when off, no injection (current behavior).
- **Why.** Guarantees a quantitative grounding floor in every chapter and removes dependence on the model's tool-selection luck. It also reduces redundant tool calls (the headline numbers are already in context), saving latency/cost while raising the density of hard evidence — directly improving forecast quality and reducing the 'all narrative, no numbers' failure mode.
- **Design.** In __init__, add self._signal_pack = '' ; new ReportAgent._build_signal_pack() (lazy, called in generate_report before the section loop, guarded by Config.REPORT_SIGNAL_PACK): assemble = '\n'.join(filter present of [self.zep_tools.simulation_outcomes(self.simulation_id, top_n=8)[:1800], self.zep_tools.coalition_map(self.graph_id, self.simulation_id)[:800], scenario_diff(...)[:1200] if base_simulation_id]) wrapped in header '【模拟量化信号（确定性，权威，可直接引用）】'. Extend _prepend_research_background to also include self._signal_pack in prefix_parts (line 1053). Cache result so it's computed once. Flag: Config.REPORT_SIGNAL_PACK.
- **Impact.** Medium-High: every section gains verifiable numbers; fewer wasted tool calls on re-deriving the same stats; more consistent quantitative voice across chapters.
- **Depends on.** Reuses zep_tools.simulation_outcomes/coalition_map/scenario_diff. Mirrors the existing _build_background_block/_prepend_research_background pattern (report_agent.py:1006-1056). New Config flag REPORT_SIGNAL_PACK (default false).
- **Risk.** Low. Pure prompt augmentation, computed once and cached on self. If the structured data is missing the helpers already return friendly degraded strings, so the block self-suppresses. Adds tokens to every section prompt — bounded by truncating the pack (e.g., top 8 actors, 48 rounds).

#### [I-3-4] Structured base-vs-scenario comparison table generator for what-if reports

`comprehensiveness` · effort **S**

- **Today.** scenario_diff (zep_tools.py:1920) produces a solid deterministic prose+bullet diff (totals, peak rounds, per-round deltas, top-actor activity deltas), and plan_outline forces a comparison chapter when base_simulation_id is set (report_agent.py:1390-1401). But the comparison is left entirely to the LLM to narrate; there is no structured, side-by-side comparison artifact (table) and no per-dimension 'direction + magnitude + which scenario wins' summary. The LLM may bury or misstate the deltas in prose.
- **Proposed.** Add a deterministic structured-comparison renderer that turns scenario_diff (and, if ensemble mode is on, cross-scenario ensemble stats) into a normalized comparison table data structure: rows = comparison dimensions (total volume, peak round, escalation speed, dominant coalition, top mover), columns = baseline vs scenario(s), with a computed delta and a categorical verdict (higher/lower/earlier/later/unchanged). Render it as a Markdown table prepended to the comparison chapter and saved as comparison.json. Make the agent narrate *around* the authoritative table rather than re-deriving numbers.
- **Why.** Structured side-by-side comparison is exactly the 'structured comparison' quality lever in scope. A deterministic table removes the risk of the LLM misreporting deltas, makes the what-if contrast scannable, and gives a stable artifact for UI/diff tooling. It strengthens the existing scenario_diff investment by making its output load-bearing and verifiable.
- **Design.** Refactor zep_tools.scenario_diff to internally build a dict {dimensions:[{name, baseline, scenario, delta, verdict}], ...} and keep current to-text rendering derived from it; expose scenario_diff_structured(base,scen)->dict. New helper render_comparison_table(diff_dict)->str producing a GitHub-flavored Markdown table (| 维度 | 基线 | 情景 | Δ | 判定 |). In ReportAgent.generate_report, when self.base_simulation_id and Config.REPORT_COMPARISON_TABLE: compute diff_dict, write reports/{id}/comparison.json, and inject the rendered table into the comparison section by prepending to the section content (detect the comparison section by title containing '情景对比'/'反事实', already enforced by plan_outline). Flag: Config.REPORT_COMPARISON_TABLE.
- **Impact.** Medium-High for what-if reports: comparison becomes accurate-by-construction and visually scannable; reduces a common failure where the model inverts a delta sign or omits a dimension.
- **Depends on.** Pure post-processing of existing scenario_diff data (zep_tools.py:1920) — refactor scenario_diff to also expose a dict form (scenario_diff_data) feeding both the existing text and the new table. Only active when base_simulation_id is set; gated additionally by Config flag REPORT_COMPARISON_TABLE (default false). Composes with ensemble mode for multi-scenario columns.
- **Risk.** Low. Only runs for what-if reports and only when flagged; otherwise no-op. Deterministic arithmetic, no LLM. Minor risk of dimension selection being too rigid — keep the dimension list small and data-driven (skip rows whose data is missing).

#### [I-3-0] Structured forecast claims layer: explicit scenario probabilities, calibration bands, and a machine-checkable forecast block per report

`capability` · effort **M**

- **Today.** plan_outline (report_agent.py:1324) and _generate_section_* (report_agent.py:1489/1589) produce only free-prose Markdown chapters. There is no place anywhere in the pipeline where the agent commits to discrete outcome scenarios, assigns probabilities, or states confidence. The system prompts (PLAN_SYSTEM_PROMPT line 555, SECTION_SYSTEM_PROMPT_TEMPLATE line 618) frame the report as a 'future prediction' but never ask for quantified forecasts. assemble_full_report (report_agent.py:2717) only concatenates chapters; ReportOutline/Report dataclasses (lines 421/444) carry no forecast fields. Net effect: the 'forecast' is qualitative narrative with no probabilities, no confidence, no falsifiable claims.
- **Proposed.** Add an optional, gated post-synthesis 'Forecast Claims' stage that runs once after all chapters are written. It asks the LLM (via chat_json) to distill the finished report + structured sim signals into a typed ForecastClaimSet: a small set of mutually-exclusive-collectively-exhaustive scenarios each with a probability (summing to ~1.0), a 1-3 sentence resolution criterion (what observable would confirm it), a confidence tier (low/med/high), and the top 2-3 sim/dossier evidence pointers driving it. Validate the JSON (probabilities normalized, MECE check, criteria non-empty), render it as a leading 'Forecast Summary' section + a machine-readable forecast.json artifact, and prepend it to the assembled report.
- **Why.** Turns a prose essay into an actual forecast with falsifiable, comparable, resolvable claims. Probabilities + resolution criteria are the single biggest lever on forecast quality and the prerequisite for any future calibration scoring (Brier/log loss). It also forces the model to reconcile its narrative into a coherent probability mass rather than hand-waving across chapters.
- **Design.** New file section in report_agent.py: @dataclass ForecastClaim{scenario:str, probability:float, confidence:str, resolution_criterion:str, evidence:List[str]}; @dataclass ForecastClaimSet{horizon:str, claims:List[ForecastClaim], notes:str, to_markdown(), to_dict()}. New ReportAgent._generate_forecast_claims(full_markdown:str)->Optional[ForecastClaimSet] guarded by Config.REPORT_FORECAST_CLAIMS: builds prompt = situation_brief block + scenario_label + sim_outcomes(top_n=10) summary + truncated full report, calls self.llm.chat_json(temperature=0.2) expecting {claims:[{scenario,probability,confidence,resolution_criterion,evidence_refs}], horizon, notes}; _validate_and_normalize_claims() renormalizes probabilities to sum 1.0 (skip if any <0), drops claims with empty resolution_criterion. In generate_report (report_agent.py ~2144), after assemble_full_report, call _generate_forecast_claims; if non-None, ReportManager.save_forecast + prepend claim_set.to_markdown() under '## 预测摘要（量化情景）' and write reports/{id}/forecast.json. Flag: Config.REPORT_FORECAST_CLAIMS = env REPORT_FORECAST_CLAIMS=='true'.
- **Impact.** High: converts the headline output from 'a readable narrative' to 'a quantified, auditable forecast'. Enables downstream calibration tracking, scenario comparison UIs, and back-testing. Low marginal cost (one extra LLM call per report).
- **Depends on.** Reuses LLMClient.chat_json (llm_client.py:111) and existing structured tools (simulation_outcomes/scenario_diff). New Config flag REPORT_FORECAST_CLAIMS (default false). New dataclasses ForecastClaim/ForecastClaimSet in report_agent.py. New ReportManager.save_forecast(report_id, claim_set).
- **Risk.** Low. Fully gated; when off, behavior is byte-identical. Main risk is probabilities that don't sum to 1 or non-MECE scenarios — mitigated by a deterministic normalize+validate pass that renormalizes and, on hard failure, degrades to rendering scenarios without the numeric block (never blocks report completion).

#### [I-3-1] Citation-grounding verifier: enforce that every quantitative/quoted claim is traceable to a tool result or [S#] source, with an unsupported-claim audit

`robustness` · effort **M**

- **Today.** _build_sources_index (report_agent.py:1035) renders a [S1]/[S2] index and prompts the model to cite, but nothing verifies citations are actually used or valid. The contamination check (_looks_contaminated, line 879) only catches gross failures (leaked system prompts, tool-call residue, length<200). There is no check that an [S7] cited in text actually exists in self.sources, no check that quoted '> ...' blocks or numeric claims trace to any tool Observation. The tool results that grounded the chapter are logged to agent_log.jsonl but discarded for grounding purposes after generation.
- **Proposed.** Add a gated post-section grounding audit. While generating each section, accumulate the concatenated tool-result text (already available in the ReAct/native loops). After the section's Final Answer, run a deterministic check: (a) every [S#] reference in the text maps to a real index in self.sources; (b) optionally a lightweight LLM 'claim-support' pass that flags numeric claims and direct quotes not present in the accumulated tool results. Emit a per-section grounding score + list of unsupported claims into agent_log.jsonl and a report-level grounding_audit.json. Optionally (second flag) append a footnote or downgrade the section to a regeneration with an explicit 'these claims were unsupported, re-ground them' nudge.
- **Why.** Citations and quotes are the core evidence of a sim-grounded forecast; today they are unenforced decoration. Hallucinated numbers or dangling [S99] refs directly destroy forecast credibility. Catching them deterministically (dangling refs) plus optionally via LLM (unsupported quotes) raises factual grounding without changing the happy-path output.
- **Design.** Add ReportAgent._collect_section_evidence: both section generators already build tool Observations — accumulate them into a local `evidence_text` list and return it alongside content (small refactor returning a tuple, or stash on self._last_section_evidence). New ReportAgent._audit_section_grounding(content:str, evidence_text:str)->dict returning {dangling_refs:[int], cited_refs:[int], unsupported_quotes:[str], numeric_claims_unsupported:[str], score:float}. Dangling refs: regex r'\[S(\d+)\]' vs range(1,len(self.sources)+1). Quote support (gated): extract '> ' blocks + numbers via regex, ask self.llm.chat_json('which of these are NOT supported by the evidence text?'). Log via report_logger.log(action='grounding_audit', ...). Aggregate to ReportManager.save_grounding_audit(report_id, per_section_list). Flags: Config.REPORT_GROUNDING_AUDIT, Config.REPORT_GROUNDING_LLM_CHECK (both default false).
- **Impact.** High for trust/quality: produces an auditable grounding score per report, catches dangling citations and fabricated quotes that currently ship silently. Observability win even with the LLM pass disabled.
- **Depends on.** Hooks into both _generate_section_native (report_agent.py:1489, tool results at line 1571) and _generate_section_react (line 1589, results in REACT_OBSERVATION_TEMPLATE). New Config flags REPORT_GROUNDING_AUDIT (deterministic ref check, cheap) and REPORT_GROUNDING_LLM_CHECK (LLM quote/number support, costlier). Uses self.sources for [S#] validation.
- **Risk.** Low-Medium. Deterministic [S#] check is safe and cheap. The LLM claim-support pass adds cost and could produce false positives on paraphrased quotes — mitigated by making it advisory (logged, not blocking) unless a strict-mode flag is set. Regeneration-on-failure is the riskiest sub-feature, so keep it behind a separate flag defaulting off.

#### [I-3-5] Self-critique calibration pass: red-team the draft forecast for overconfidence, base-rate neglect, and unsupported leaps

`capability` · effort **M**

- **Today.** There is no critique/reflection stage. MAX_REFLECTION_ROUNDS=3 is declared (report_agent.py:943) and REPORT_AGENT_MAX_REFLECTION_ROUNDS exists in Config (config.py:311) but neither is used anywhere — reflection is dead config. Sections are written once and assembled; the only quality gates are contamination detection and the tool-count minimum. Nothing challenges the forecast's confidence level, checks for base-rate neglect, or flags claims that outrun the evidence.
- **Proposed.** Add a gated, single-pass calibration self-critique that runs after assembly (and after the Forecast Claims layer if enabled). It feeds the model the full report + forecast claims + the structured signal pack and asks specifically for: (1) overconfident claims (probability/confidence not justified by evidence spread), (2) base-rate / reference-class omissions, (3) claims unsupported by the sim or dossier, (4) missing disconfirming evidence. The critique either (a) appends a transparent 'Calibration & Caveats' section, or (b) in a stricter flag mode, feeds back as targeted edit instructions to adjust probabilities/confidence in forecast.json.
- **Why.** Activates the dormant reflection capability with a sharp, calibration-specific objective. Overconfidence and base-rate neglect are the dominant forecasting errors; a dedicated red-team pass measurably improves calibration and adds an explicit uncertainty/caveats artifact that the report currently lacks.
- **Design.** New ReportAgent._calibration_critique(full_markdown:str, claim_set:Optional[ForecastClaimSet])->dict guarded by Config.REPORT_CALIBRATION_CRITIQUE: prompt includes signal pack + ensemble variance (if any) + forecast.json + report; chat_json returns {overconfident:[{claim,reason,suggested_confidence}], missing_base_rates:[...], unsupported:[...], caveats:[str]}. In generate_report after assembly/forecast: if append-mode, render a '## 校准与不确定性' section from caveats+flags and append; if Config strict-mode also set, apply suggested_confidence/probability adjustments to claim_set then renormalize and re-save forecast.json with a changelog note. Cap iterations with min(Config.REPORT_AGENT_MAX_REFLECTION_ROUNDS, ReportAgent.MAX_REFLECTION_ROUNDS). Flags: Config.REPORT_CALIBRATION_CRITIQUE (+ optional REPORT_CALIBRATION_STRICT).
- **Impact.** Medium-High: directly attacks overconfidence, adds an auditable caveats section, and (in strict mode) can pull probabilities toward better-calibrated values. One extra LLM call per report.
- **Depends on.** Reuses LLMClient.chat / chat_json. Wires up the already-present MAX_REFLECTION_ROUNDS / REPORT_AGENT_MAX_REFLECTION_ROUNDS (currently unused). Strongest when combined with Forecast Claims (probabilities to critique) and the Signal Pack / Ensemble variance (evidence spread to judge overconfidence against). New Config flag REPORT_CALIBRATION_CRITIQUE (default false).
- **Risk.** Low-Medium. Append-only mode is safe. The strict feedback-edit mode could over-hedge (push everything to 50/50) — mitigate by constraining edits to claims the critique explicitly flags with a stated reason, and by capping to one pass (reuse MAX_REFLECTION_ROUNDS as the cap). Fully gated; off => no behavior change.

#### [I-3-3] Multi-replicate ensemble forecasting with frequency-derived scenario probabilities

`capability` · effort **L**

- **Today.** The pipeline runs a single simulation per report (pipeline_orchestrator.py:1700 instantiates one ReportAgent against one simulation_id). Scenario/what-if support exists (scenario_label, base_simulation_id, scenario_diff at zep_tools.py:1920) but compares exactly two single runs. OASIS uses weighted sampling for active agents (run_parallel_simulation.py:1007 _weighted_sample_without_replacement) and stochastic LLM agents, so a single run is one sample from a distribution — yet the report treats it as ground truth. There is no notion of run-to-run variance or empirical probability.
- **Proposed.** Add an optional ensemble mode: run the same scenario config N times (N small, e.g. 3-5, gated by a flag/count), then derive empirical scenario probabilities and stability metrics from the N runs' structured outcomes rather than from a single LLM judgment. The report agent ingests an EnsembleSummary (per-run timeline totals, peak rounds, top-actor activity, action-type mix; cross-run mean/variance/CV; and a clustering of runs into outcome buckets) as an authoritative tool/block. Probabilities in the Forecast Claims layer can then be anchored to observed run frequencies, with the spread reported as uncertainty.
- **Why.** This is the highest-leverage upgrade to forecast *calibration*: probabilities grounded in observed run frequency across replicates are far more defensible than a single-run LLM guess. Cross-run variance gives a principled uncertainty estimate ('peak round 3 in 4/5 runs; total volume CV 18%'), which is exactly the uncertainty quantification the current system lacks.
- **Design.** New zep_tools.ensemble_summary(simulation_ids:List[str])->str: for each sim call get_timeline+get_agent_stats; compute per-run total_actions, peak round, top-5 actors; cross-run mean/stdev/CV of totals and peak-round distribution; bucket runs by (peak_round, dominant_action_type) and report bucket frequencies as empirical scenario probabilities. New ReportAgent param ensemble_simulation_ids:Optional[List[str]]; if len>1, expose ensemble_summary as a tool (add to VALID_TOOL_NAMES + _define_tools + _execute_tool) and inject its summary into the signal pack. Orchestrator: when Config.REPORT_ENSEMBLE_RUNS>1, loop the simulation stage N times collecting sim_state.simulation_id into a list, pass to ReportAgent and to _generate_forecast_claims so probabilities = bucket frequencies (with LLM only narrating). Flag: Config.REPORT_ENSEMBLE_RUNS (default 1).
- **Impact.** Very High on calibration and robustness — turns a single stochastic draw into a sampled distribution. Also surfaces non-robust findings (high-variance signals) that a single run would present with false confidence.
- **Depends on.** Builds on existing parallel-run plumbing (run_parallel_simulation.py) and SimulationRunner.get_timeline/get_agent_stats. New aggregation EnsembleSummary computed from N simulation_ids. New Config flag REPORT_ENSEMBLE_RUNS (int, default 1 = current single-run behavior). Pipeline orchestrator must launch and collect N runs (extends the Stage 4 simulation block around pipeline_orchestrator.py:1586). Pairs naturally with the Forecast Claims layer for frequency-anchored probabilities.
- **Risk.** Medium-High. NxN cost/latency/compute is the main concern — strictly gated by REPORT_ENSEMBLE_RUNS (default 1 keeps everything unchanged). Run failures must degrade gracefully (use the runs that succeeded; if only 1 succeeds, fall back to single-run path). Needs care to reuse the same dossier/graph seed across runs so only stochasticity differs.

### 7.4 Knowledge-graph richness & retrieval

#### [I-1-0] Expose Graphiti's full search surface (filters, center-node, MMR, BFS) through the runtime/Zep facade

`capability` · effort **M** · backend/app/services/graphiti_client/runtime.py, backend/app/services/graphiti_client/client.py, backend/app/config.py

- **Today.** GraphitiRuntime._search (backend/app/services/graphiti_client/runtime.py:415-426) hardcodes EDGE_HYBRID_SEARCH_RRF / NODE_HYBRID_SEARCH_RRF and calls g.search_(query, config, group_ids=[graph_id]) with no center_node_uuid, no bfs_origin_node_uuids, and no search_filter. The Zep facade _GraphNamespace.search (client.py:212-222) accepts only graph_id/query/limit/scope/reranker and silently drops reranker. graphiti_core.search_ (graphiti-0.29.2/graphiti_core/graphiti.py:1527+) and SearchFilters (search_filters.py:55 with node_labels, edge_types, valid_at, invalid_at) and recipes (EDGE_HYBRID_SEARCH_NODE_DISTANCE, *_MMR, COMBINED_HYBRID_*) are all installed but unreachable. zep_tools.search_graph (zep_tools.py:477-557) therefore cannot do typed/temporal/diversity/graph-aware retrieval; all higher tools (insight_forge, panorama_search) inherit the limitation.
- **Proposed.** Thread an optional structured retrieval spec end-to-end: add params to runtime._search and the facade search() for center_node_uuid, bfs_origin_node_uuids, search_filter (dict -> SearchFilters), and a reranker/recipe selector ('rrf'|'mmr'|'node_distance'|'cross_encoder'|'combined'). Map a small dict (node_labels, edge_types, valid_at_after/before, invalid_at_is_null) into a real SearchFilters; pick the recipe from the selector (default rrf preserves today's behavior); pass center_node_uuid/bfs origins straight through. Keep the existing 5-arg call path working unchanged when the new params are omitted.
- **Why.** This is the single highest-leverage unlock: every downstream retrieval quality idea (typed retrieval, temporal as-of queries, ego-centric persona context, diverse evidence) depends on the facade being able to pass these arguments. Today the system pays for a bi-temporal, typed, community-aware graph but reads it back with a flat RRF keyword+vector blend, throwing away most of the structure it built.
- **Design.** runtime.py: def _search(self, graph_id, query, limit, scope, recipe='rrf', center_node_uuid=None, bfs_origin_node_uuids=None, search_filter: dict|None=None); build SearchFilters via a _to_search_filters(dict) helper using graphiti_core.search.search_filters.{SearchFilters,DateFilter,ComparisonOperator}; recipe map -> {EDGE,NODE}_HYBRID_SEARCH_{RRF,MMR,NODE_DISTANCE,CROSS_ENCODER} + COMBINED_HYBRID_SEARCH_RRF. client.py _GraphNamespace.search passes new kwargs through; default recipe inferred from existing `reranker` arg. config flag GRAPH_SEARCH_RECIPE default 'rrf'.
- **Impact.** Foundational. Enables 4-5 of the other improvements here with no new infra. Immediately allows graph-aware (node_distance) and diversity (MMR) reranking which materially raise the relevance and non-redundancy of facts fed to personas and the report.
- **Depends on.** None (graphiti_core already vendored at graphiti-0.29.2). Other proposals build on it.
- **Risk.** Low. Additive, defaulted-off params; the unchanged call path is byte-identical. Risk is malformed SearchFilters dicts -> wrap construction in try/except and fall back to SearchFilters() so a bad filter degrades to today's search rather than erroring.

#### [I-1-1] Bi-temporal as-of retrieval: query the graph as it was known at any simulation round / forecast date

`capability` · effort **M** · backend/app/services/zep_tools.py, backend/app/services/report_agent.py, backend/app/services/graphiti_client/runtime.py

- **Today.** Edges are stamped with valid_at at seed time (graph_builder.seed_actors valid_at=as_of, pipeline_orchestrator.py:1510-1513) and feedback edges carry round-derived valid_at (zep_graph_memory_updater._write_typed_edges:463-475). But retrieval never filters on time: zep_tools.panorama_search (zep_tools.py:1188-1278) loads ALL edges via fetch_all_edges and splits active/historical purely on Python-side edge.is_expired/is_invalid; there is no way to ask 'what was true as of date D' or 'as of round R'. EdgeInfo.to_text(include_temporal) exists but the bi-temporal axis is decorative, not queryable.
- **Proposed.** Add a temporal query tool as_of_search(graph_id, query, as_of, limit) that uses the new search_filter plumbing to push valid_at <= as_of AND (invalid_at IS NULL OR invalid_at > as_of) into the graph query, returning only facts that were true at the given instant. Expose 'as_of' as an optional param on panorama_search and insight_forge so the report agent can produce point-in-time and trajectory views (e.g. 'state at round 0 vs final round'). Default as_of=None preserves current all-time behavior.
- **Why.** Forecasting is inherently about how state evolves over a horizon. The pipeline already builds a real bi-temporal axis (as_of anchored seeds + round-stamped feedback edges) but the report can only see a time-flattened snapshot, so it cannot rigorously answer 'how did stance X shift between T0 and T_end' from the graph itself. Time-sliced retrieval turns the existing valid_at data into evidence for trajectory and turning-point claims.
- **Design.** zep_tools.as_of_search builds search_filter={'valid_at': [[{op:'<=', date:as_of}]], 'invalid_at_or_null_after': as_of} and calls self.client.graph.search(..., search_filter=...). Add as_of: Optional[datetime] to panorama_search/insight_forge signatures, thread into search calls and into active/historical classification. report_agent _define_tools (report_agent.py:1059+) gains optional as_of param doc; parse from tool args. Reuse app/utils/dates.parse_as_of.
- **Impact.** High for forecast quality: enables defensible temporal claims (emergence/decay of alliances, escalation timing) grounded in graph state rather than LLM narration. Pairs with the existing opinion_shift/scenario_diff structural tools to give a graph-native temporal view.
- **Depends on.** Requires the search-surface unlock (proposal 1). Relies on valid_at being populated, which seed_actors and typed feedback edges already do.
- **Risk.** Medium. Bi-temporal coverage is uneven: text-extracted edges from LLM ingestion may have null valid_at, so as-of filtering could under-return. Mitigate by treating null valid_at as 'always valid' in the filter (include them) and clearly labeling coverage; gate via Config and keep as_of optional so default path is unaffected.

#### [I-1-3] Domain-adaptive ontology generation (typed entities/edges that fit the actual forecast domain)

`comprehensiveness` · effort **M** · backend/app/services/ontology_generator.py, backend/app/config.py, backend/app/services/pipeline_orchestrator.py

- **Today.** OntologyGenerator (ontology_generator.py) hardcodes ONTOLOGY_SYSTEM_PROMPT for a Chinese social-media public-opinion scenario, forcing exactly 10 entity types where the last two MUST be Person/Organization fallbacks (lines 79-101, 287-340) and 6-10 generic edge types (WORKS_FOR, SUPPORTS, OPPOSES...). Crucially, generated entity/edge ATTRIBUTES are barely used: graph_builder.set_ontology turns every attribute into Optional[EntityText]/Optional[str] (graph_builder.py:243-272) but retrieval never reads node.attributes for ranking or filtering, and the rigid 10/Person+Organization cap drops domain-specific types when the forecast is, say, a market/geopolitics/product-launch question rather than a social-opinion event.
- **Proposed.** Generalize ontology generation to be domain-aware: derive the type budget and fallback policy from the central_question/simulation_requirement and actors.json type distribution rather than a fixed social-media template. Allow the prompt to emit edge attributes that matter for forecasting (e.g. sentiment, strength, since_date on relationships) and entity attributes (influence_tier, sector) that downstream retrieval can filter on via the new node_labels/edge SearchFilters. Keep a config-selectable template ('social_opinion' default = today's exact prompt) so current behavior is the default and new domains opt in.
- **Why.** Richer, domain-fit typing is the upstream lever for typed retrieval and typed personas. A market-shock or regulatory forecast modeled with the current student/professor/media template loses signal; a typed ontology that matches the domain produces more discriminative labels and attributes, which then make node_label-filtered retrieval (proposal 1) and faction reasoning sharper.
- **Design.** ontology_generator: add template registry {`social_opinion`: ONTOLOGY_SYSTEM_PROMPT, `general_forecast`: new prompt}; generate(..., template=Config.ONTOLOGY_TEMPLATE) picks prompt; pass actors type-histogram into _build_user_message so type budget tracks real cast composition. Loosen the hard 'exactly 10 + 2 fallbacks' to 'attempt fallbacks if Person/Org appear in cast, cap at MAX_ENTITY_TYPES'. Config.ONTOLOGY_TEMPLATE default 'social_opinion'.
- **Impact.** Medium-High. Broadens the pipeline beyond Chinese social-opinion events to general forecasting domains while improving label discriminativeness for retrieval. Low risk because it is gated and defaults to the existing template.
- **Depends on.** Synergistic with proposal 1 (typed/attribute retrieval gives the new types something to do). Independent to implement.
- **Risk.** Medium. Free-form ontologies can violate Zep/Falkor constraints (reserved attr names, >10 types). Keep the existing safe_attr_name reserved-name guard and MAX_ENTITY/EDGE_TYPES clamps in _validate_and_process; validate generated types and fall back to the social_opinion template on any schema violation.

#### [I-1-5] Ego-centric persona context via center-node + typed-neighborhood retrieval

`capability` · effort **M** · backend/app/services/oasis_profile_generator.py, backend/app/services/graphiti_client/runtime.py, backend/app/services/graphiti_client/client.py

- **Today.** OasisProfileGenerator._search_zep_for_entity (oasis_profile_generator.py:295-395) runs two flat client.graph.search calls (edges + nodes) on the entity NAME string, then _build_entity_context (around :450-472) lists whatever facts/relationships come back. Because the facade can't pass center_node_uuid or node_labels, the persona's graph context is a name-keyword search, not a true 1-2 hop ego network around the resolved node. relationship_briefing (actors.py:271-290) adds the researched relationships, but the LIVE graph neighborhood (including simulation-feedback edges) is retrieved only by fuzzy name match, so personas often miss their actual graph neighbors or get noisy unrelated facts.
- **Proposed.** Once the entity's node uuid is known, retrieve its ego network with center_node_uuid=that uuid (node_distance reranking) and an optional edge_types filter for relationship edges, returning the closest N neighbors and the typed edges connecting them. Feed this graph-grounded neighborhood into the persona prompt as 'your actual position in the network', complementing the researched relationship_briefing. Gate via Config.PERSONA_EGO_RETRIEVAL (default true once proposal 1 lands; falls back to current name search if uuid unknown).
- **Why.** Persona realism in OASIS depends on each agent knowing precisely who it interacts with, allies with, and opposes. Graph-distance-ranked neighbor retrieval gives sharper, less noisy social context than name-keyword search, and it picks up emergent simulation-feedback edges that the static actors.json relationships miss — producing agents whose behavior is consistent with the actual graph topology, which improves the downstream opinion-dynamics fidelity that the forecast rests on.
- **Design.** oasis_profile_generator: resolve entity -> uuid (search nodes, take top match), then self.zep_client.graph.search(graph_id, query=entity.name, scope='edges', center_node_uuid=uuid, search_filter={'edge_types': RELATIONSHIP_EDGE_TYPES}) ; format top-k neighbors into a '## 你在关系网中的实际位置' block appended in _build_entity_context. Config.PERSONA_EGO_RETRIEVAL.
- **Impact.** Medium-High on persona/simulation fidelity, which propagates into forecast quality. Reduces irrelevant-fact noise currently injected into persona prompts.
- **Depends on.** Proposal 1 (center_node_uuid plumbing). Stronger when proposal 5 (entity resolution) ensures the center node is the canonical, fully-connected node.
- **Risk.** Low-Medium. If the entity name doesn't resolve to a node uuid, must fall back to today's name search (no regression). Extra per-persona search call adds latency; cap neighbor count and reuse the existing ThreadPoolExecutor pattern already in _search_zep_for_entity.

#### [I-1-6] Diversity-aware (MMR) retrieval and graph-coverage observability for the report agent

`observability` · effort **M** · backend/app/services/zep_tools.py, backend/app/services/report_agent.py, backend/app/config.py

- **Today.** insight_forge (zep_tools.py:988-1133) generates sub-queries then unions facts with simple seen_facts string-dedup; ranking is whatever RRF returned, so semantically near-duplicate facts crowd out distinct evidence. panorama_search ranks by a naive Python keyword overlap (relevance_score, zep_tools.py:1258-1266). There is also no instrumentation of retrieval coverage: get_graph_statistics (zep_tools.py:918-932) reports counts but nothing tracks what fraction of entities/communities/edge-types the report actually touched, so silent under-retrieval (a report ignoring whole factions) is invisible. report_agent logs tool calls (log_tool_call:166) but not graph coverage.
- **Proposed.** Two coupled changes, both gated: (a) add an MMR recipe option to retrieval (via proposal 1) and use it in insight_forge so the fact set is relevant AND diverse, replacing exact-string dedup with embedding-redundancy suppression; (b) add a lightweight coverage tracker that records, per report run, which entity uuids / community ids / edge types were surfaced across all tool calls, and emit a handoff/retrieval_coverage.json plus a warning when coverage of high-influence actors or detected factions is below a threshold.
- **Why.** Diverse evidence prevents the report from over-weighting one repeated claim, and coverage observability catches the failure mode where the agent confidently writes a forecast having ignored a major faction or high-influence actor — the most dangerous silent quality bug in a forecasting system. Both directly raise trustworthiness without changing the user-facing flow.
- **Design.** zep_tools.insight_forge: recipe='mmr' when Config.GRAPH_SEARCH_RECIPE=='mmr'; replace seen_facts set with embedding-cosine dedup using the runtime embedder. New zep_tools method record_coverage() accumulating surfaced uuids/community_ids/edge names per (graph_id, report run); report_agent flushes to handoff/retrieval_coverage.json at end and compares surfaced high-influence actors (from actors.json influence=='high') vs total, logging a warning if < Config.REPORT_MIN_ACTOR_COVERAGE. Config: GRAPH_SEARCH_RECIPE ('rrf'), REPORT_MIN_ACTOR_COVERAGE (0.6).
- **Impact.** Medium. MMR improves evidence breadth per token budget; coverage telemetry makes retrieval quality measurable and regressions catchable across runs (currently impossible).
- **Depends on.** Proposal 1 for the MMR recipe. Coverage tracker is independent and can ship alone.
- **Risk.** Low. MMR is opt-in via recipe selector (default rrf unchanged). Coverage tracking is pure instrumentation with try/except so it never affects report output; threshold warnings are advisory only.

#### [I-1-2] Make detected communities first-class retrievable structure (faction-aware GraphRAG)

`capability` · effort **L** · backend/app/services/zep_tools.py, backend/app/services/report_agent.py, backend/app/services/graphiti_client/runtime.py, backend/app/services/oasis_profile_generator.py

- **Today.** build_communities runs Leiden + LLM summaries and persists handoff/communities.json (graph_builder.build_communities:431-440 -> runtime._build_communities:404-410; wired in pipeline_orchestrator.py:1535-1547, gated by GRAPH_BUILD_COMMUNITIES, default false). The community nodes/summaries are written to FalkorDB but retrieval NEVER searches them: runtime._search only uses EDGE/NODE recipes, never COMMUNITY_HYBRID_SEARCH_* or COMBINED_HYBRID_SEARCH_*. The only faction signal the report can get is zep_tools.coalition_map (zep_tools.py:1839-1895), which re-derives clusters from raw simulation action logs via union-find and ignores the graph's own community summaries entirely.
- **Proposed.** Add a faction_brief(graph_id, query='') tool that (a) hybrid-searches community nodes via COMMUNITY_HYBRID_SEARCH_RRF and (b) for top communities, lists member entities and their cross-community OPPOSES/ALLY_OF/COMPETES_WITH edges, returning a structured 'who is aligned with whom and why' digest with the LLM-written community summaries. Register it as a report-agent tool alongside coalition_map (graph-derived factions complement the action-log-derived ones). Also surface community membership as an entity attribute so persona generation can give agents an in/out-group identity.
- **Why.** Opinion dynamics and forecasts hinge on faction structure (in-group/out-group, bridge actors, polarization). The graph already computes communities but they are inert. Exposing them gives the report graph-native faction evidence and lets personas reason about coalition belonging, which is exactly what drives realistic social-simulation behavior and higher-fidelity opinion-shift forecasts.
- **Design.** runtime: add list_communities(graph_id) (Cypher MATCH (c:Community {group_id}) RETURN ...) and community search recipe. zep_tools.faction_brief: search communities -> for each, fetch member entities + inter-community typed edges -> render markdown. report_agent._define_tools adds 'faction_brief'; _execute_tool dispatches it. oasis_profile_generator: when building entity context (_build_entity_context near profile gen), attach community_id/summary to the actor briefing. Config: GRAPH_COMMUNITY_RETRIEVAL (default tied to GRAPH_BUILD_COMMUNITIES).
- **Impact.** High. Turns a currently dead artifact into retrievable structure; gives both personas (group identity) and the report (faction map with summaries + bridge actors) a richer, more trustworthy view of alignment than the heuristic action-log clustering alone.
- **Depends on.** Proposal 1 (to add COMMUNITY/COMBINED recipes to runtime._search). Benefits from GRAPH_BUILD_COMMUNITIES being enabled (currently default false) — would default-enable behind its existing flag.
- **Risk.** Medium. Community detection adds build-time cost (Leiden + per-community LLM summary) and quality varies with graph density; keep behind GRAPH_BUILD_COMMUNITIES and have faction_brief degrade to coalition_map's action-log clustering when no community nodes exist.

#### [I-1-4] LLM-based entity resolution / canonical-alias merge pass after graph build

`robustness` · effort **L** · backend/app/services/zep_entity_resolver.py (new), backend/app/services/graphiti_client/runtime.py, backend/app/services/pipeline_orchestrator.py, backend/app/config.py, backend/app/utils/actors.py

- **Today.** Dedup relies entirely on Graphiti's internal name+embedding resolution during add_triplet/add_episode (documented in client.py:196-201 add_triplet docstring and runtime._add_triplet). seed_actors writes researched relationships first so prose enrichment attaches to seeds (graph_builder.py:308-353), but there is no cross-name reconciliation: the same real entity appearing as 'OpenAI', 'OpenAI 公司', 'OpenAI, Inc.', '@OpenAI' across research prose, seeds, and simulation feedback (zep_graph_memory_updater writes 'agent_name: ...' episodes using display names) can produce duplicate nodes. actors.match_actor/normalize_name (actors.py:59-108) already encodes strong alias logic (NFKC, punctuation strip, bidirectional containment) but it is only used to map onto actors.json — never to merge graph nodes.
- **Proposed.** Add an optional post-build resolution pass resolve_entities(graph_id, actors) that (1) lists all entity nodes, (2) groups them with the existing normalize_name + containment logic AND an embedding-similarity threshold, (3) for each duplicate cluster, picks the actors.json canonical name as the survivor and rewires edges via a merge (re-point source/target uuids, union attributes/labels, delete the absorbed node). Run it once after community detection in the GRAPH stage, gated by Config.GRAPH_RESOLVE_ENTITIES (default false).
- **Why.** Duplicate/fragmented entities silently degrade every retrieval path: search recall splits across aliases, coalition_map and node-degree centrality undercount, and personas can be generated for phantom split nodes. Consolidation directly raises the density and correctness of the graph the whole forecast reads from, and the alias logic to do it well already exists in actors.py.
- **Design.** runtime: add merge_nodes(graph_id, survivor_uuid, victim_uuid) (Cypher: rewrite edges' source/target uuid, copy attributes, DETACH DELETE victim) and node_embeddings access. New service zep_entity_resolver.py: cluster -> choose canonical -> merge; emit handoff/entity_merges.json. pipeline_orchestrator GRAPH stage calls it after build_communities when Config.GRAPH_RESOLVE_ENTITIES. Config flags: GRAPH_RESOLVE_ENTITIES (false), GRAPH_RESOLVE_SIM_THRESHOLD (0.88).
- **Impact.** Medium-High for robustness and retrieval recall, especially on noisy multilingual reports where the same actor surfaces under many surface forms. Most beneficial when simulation feedback (display names) re-enters the graph alongside researched canonical names.
- **Depends on.** Reuses actors.normalize_name/match_actor. Benefits from but does not require proposal 1. Needs a node-merge primitive in the runtime.
- **Risk.** Medium-High. Over-merging distinct entities ('Apple' company vs a person named Apple) corrupts the graph irreversibly. Mitigate: only merge within the same primary label, require both normalized-name match AND embedding cosine > threshold, prefer merging non-canonical surface forms INTO an actors.json canonical (never merge two canonical actors), log every merge to handoff for audit, and keep default off.

### 7.5 New capabilities (make it more comprehensive)

#### [I-9-1] Structured, machine-readable forecast object (probabilities + resolution criteria)

`capability` · effort **M** · backend/app/services/report_agent.py (new synthesize_forecast(); call at end of generate_report; extend Report dataclass:445 + to_dict:460 with optional forecast); ReportManager.save_report (persist forecast.json); backend/app/api/report.py (new GET /<report_id>/forecast); backend/app/config.py (REPORT_STRUCTURED_FORECAST=false); frontend/src/components/research/ForecastReport.vue + frontend/src/api/report.js (fetch + render bars)

- **Today.** The report is prose-only. ReportAgent.generate_report (backend/app/services/report_agent.py:928+) plans a 2-5 section outline (plan_outline) and writes markdown sections via ReAct; the Report dataclass (report_agent.py:445-460) holds report_id, sections, status, full_report - no structured forecast. simulation_outcomes/scenario_diff (zep_tools.py:1803, 1920) produce descriptive counts and deltas but never a probability, confidence, or resolution date. Nothing commits to a falsifiable claim like 'P(event by date)=0.7'. frontend ForecastReport.vue renders only the markdown.
- **Proposed.** Add an optional final synthesis step that emits a structured forecast.json alongside the markdown: a list of forecast questions, each with a point probability (or numeric estimate + range), a 1-5 confidence, key drivers cited to graph facts/actors/sources, and explicit resolution_criteria + resolution_date. Implement as a new synthesize_forecast() in ReportAgent that runs after sections are written, fed the situation_brief, the (ensemble or single) simulation outcomes, and the section text, with a strict JSON schema and the existing _fix_truncated_json/contamination defenses. In ensemble mode, probabilities are grounded in empirical K-run frequency rather than a free-form LLM guess.
- **Why.** A forecasting system must produce falsifiable, comparable claims. Prose ('tensions may rise') cannot be scored, charted, compared across runs/models, or backtested. A structured forecast object is the keystone that makes calibration, model-comparison, and a programmatic SDK actually useful - each needs a numeric, resolvable claim. It also raises perceived rigor and lets the UI show probability bars and a 'resolves on' date.
- **Design.** Config REPORT_STRUCTURED_FORECAST (default false -> unchanged output). Schema: {questions:[{id, question, probability:0-1|null, estimate:str|null, range:[lo,hi]|null, confidence:1-5, drivers:[{claim, evidence_ref}], resolution_criteria, resolution_date}], generated_at, basis:'ensemble'|'single', n_runs}. synthesize_forecast(sections_text, outcomes_block, brief) -> llm.chat_json with schema-locked prompt + reuse _looks_contaminated; if ensemble_summary.json exists, override probability with empirical frequency where a question maps to a measurable outcome. Persist to uploads/reports/<id>/forecast.json; Report.forecast optional, absent -> UI hides panel. Degrades: flag off or LLM fails -> markdown-only report as today.
- **Impact.** Turns the deliverable from an essay into a scoreable forecast: enables probability visualizations, downstream scoring, cross-run/cross-model comparison, and a clean API surface. Foundational multiplier for calibration, comparison, and SDK.
- **Depends on.** Standalone, but probabilities are far stronger when ensemble mode supplies empirical frequencies. Itself a prerequisite for calibration, model-comparison, and the SDK.
- **Risk.** LLM may hallucinate over-confident probabilities in single-run mode (mitigate: flag single-run probabilities as 'model-estimated, uncalibrated'; prefer ensemble frequencies). JSON parse fragility on CLI providers (mitigate: reuse existing repair + contamination guards; on failure omit forecast.json rather than poison the report).

#### [I-9-3] Multi-question batch runs with shared research/graph reuse

`capability` · effort **M** · backend/app/api/research.py (new POST /batch); backend/app/services/pipeline_orchestrator.py (new start_batch() that runs anchor full pipeline then fork()s per extra prompt; reuse fork:1135 + apply_scenario_overlay pattern); lightweight batch record in PipelineManager (batch_state.json or batch_id in options); frontend/src/components/research/PipelineHistory.vue (group children under a batch)

- **Today.** POST /api/research/run (backend/app/api/research.py:35) and PipelineOrchestrator.start (pipeline_orchestrator.py:868) accept exactly one prompt and start one pipeline. The orchestrator already supports artifact reuse across stages: continue_to_full and fork (pipeline_orchestrator.py:1069, 1135) reuse a base pipeline's research/ontology/graph by marking those stages completed and hitting the reuse guards in _run (research reuse at :1392, graph reuse at :1476). But there is no way to ask several related questions about the same situation in one shot; each question re-runs the full (expensive) research+graph from scratch even when the underlying corpus is identical.
- **Proposed.** Add an optional batch endpoint taking a list of related prompts plus an optional shared base. It runs research+ontology+graph ONCE (for the anchor prompt or an explicit shared corpus), then forks one prepare/run/report per question reusing the same graph_id - exactly the mechanism fork() already proves works. Output is a batch record listing child pipeline ids and a combined view. Optionally the simulation itself is shared across questions (one OASIS run) with multiple report agents querying it under different simulation_requirements, the cheapest mode.
- **Why.** Real forecasting is rarely one isolated question - users want 'what happens to A, to B, and to the coalition between them' about the same event. Re-running 40-minute research + graph builds per question is wasteful and inconsistent (different graphs give incomparable answers). Sharing research/graph makes a basket of forecasts both cheap and internally consistent (all grounded in identical evidence), a meaningful scale and quality win that reuses code paths that already exist.
- **Design.** POST /batch {prompts:[...], shared_simulation:bool} -> validate, preflight once. start_batch(prompts): run prompt[0] as a normal full pipeline to graph completion; for prompt[1..] call new fork_question(base_id, prompt) mirroring fork() (research/ontology/graph completed -> reuse guards fire) but overriding state.prompt and (if shared_simulation) also marking PREPARE/RUN completed and pointing report at base simulation_id with a new simulation_requirement. Tag children options['batch_id']. New GET /batch/<id>/status aggregates child statuses. Existing single-prompt path untouched.
- **Impact.** N related forecasts at roughly 1x research/graph cost instead of Nx; internally consistent, comparable answers across a question basket. Major scale + cost-efficiency gain leveraging proven reuse machinery.
- **Depends on.** Builds directly on existing fork()/continue_to_full reuse guards. Pairs naturally with the structured forecast (each child emits a comparable forecast.json) for a batch dashboard.
- **Risk.** Question drift: a graph built for prompt[0] may under-cover entities relevant to prompt[2] (mitigate: build the anchor graph from a merged research corpus, or allow per-question incremental graph enrichment). Concurrency: many child sims can exhaust CLI LLM throughput (mitigate: serialize or cap batch fan-out via a config concurrency limit).

#### [I-9-5] Programmatic API + Python SDK with API-key auth

`capability` · effort **M** · backend/app/__init__.py (optional before_request API-key check; register v1 blueprint); backend/app/api/ (new v1.py thin aliases over existing service calls); backend/app/config.py (API_KEY=None default -> open; optional API_RATE_LIMIT); new sdk/deepagentforecast/client.py + sdk/README.md; docs/ API contract doc

- **Today.** The backend exposes Flask blueprints under /api/research, /api/graph, /api/simulation, /api/report (registered in backend/app/__init__.py), but CORS is permissive/open (create_app enables CORS on /api/* per ARCHITECTURE.md section 3) and there is NO authentication - the routes are designed solely for the local Vue SPA proxied via Vite. There is no API key, no rate limiting, no client library, and the response envelope ({success,data,error}, unwrapped by frontend/src/api/index.js) is implicit, not a documented stable contract. Headless use (CI backtests, scheduled runs, third-party integration) requires scraping SPA endpoints.
- **Proposed.** Promote the existing routes into a documented, optionally-authenticated public API and ship a thin Python SDK. Add an optional API-key gate (header X-API-Key) enforced by a before_request hook only when a key is configured (default: no key = open, exactly today's behavior). Add a stable /api/v1 surface aliasing the core lifecycle (run, status, dossier, forecast, list, resolve) and document the {success,data,error} contract. Provide sdk/deepagentforecast/client.py: Client(base_url, api_key).run(prompt, depth, ensemble=...), .wait(pipeline_id), .forecast(report_id), .resolve(report_id, outcomes).
- **Why.** Everything valuable here - scheduled re-runs, batch backtests, CI-driven model comparison - needs a clean programmatic entry point, and the current open, undocumented, SPA-only API is unsafe to expose and awkward to drive. A keyed API + SDK turns the system from a single-user GUI into an automatable forecasting service, the precondition for scale or embedding elsewhere. It is low-risk because it can be purely additive and default-off.
- **Design.** Config API_KEY (default None). before_request: if Config.API_KEY and request.path.startswith('/api/') and request.headers.get('X-API-Key')!=Config.API_KEY: abort(401) - when API_KEY unset, no-op (current behavior preserved; localhost SPA unaffected). SDK: requests-based wrapper over the existing envelope, polling /status/<id> until terminal, returning typed dicts mirroring the documented contract. Keep existing un-versioned routes for the SPA; /api/v1/* for external callers. Optional simple token-bucket rate limit gated by config.
- **Impact.** Unlocks headless/automated use (CI, schedulers, third-party apps) and safe remote exposure; the enabling substrate for scheduled re-runs and automated backtesting. Broadens reach from one GUI user to programmatic consumers.
- **Depends on.** Most useful once the structured forecast exists so .forecast() returns structured data. Prerequisite/enabler for scheduled re-runs and headless backtest/comparison automation.
- **Risk.** Security: exposing a previously-local, compute-heavy API publicly invites abuse (mitigate: default-off auth means no local behavior change; require API_KEY before binding to non-localhost; add rate limiting before documenting public exposure). Versioning drift between v1 aliases and underlying routes (mitigate: v1 delegates to the same service functions, not duplicated logic).

#### [I-9-6] Scheduled re-runs with drift detection on a saved prompt

`capability` · effort **M** · backend/app/services/scheduler.py (new: due-check daemon + schedule store, mirrors SimulationRunner monitor-thread + reconcile_orphans patterns); backend/app/services/forecast_diff.py (new: compare two forecast.json -> drift report); backend/app/api/research.py (new CRUD: POST /schedules, GET /schedules, DELETE /schedules/<id>); backend/app/__init__.py (start scheduler thread on app init, like register_cleanup); backend/app/config.py (SCHEDULER_ENABLED=false, SCHEDULER_TICK_SECONDS, DRIFT_PROB_THRESHOLD, optional DRIFT_WEBHOOK_URL); frontend: schedule management + evolution timeline

- **Today.** Pipelines are strictly one-shot and user-initiated: PipelineOrchestrator.start (pipeline_orchestrator.py:868) is only ever called from POST /run. There is robust lifecycle infrastructure already - file-backed PipelineManager records (pipeline_orchestrator.py:188), orphan reconciliation on startup (reconcile_orphans:763), cancellation, and fork() for re-running with a base. But nothing re-runs a forecast over time. A fast-moving situation (the system's core use case per ARCHITECTURE.md: news/policy/crisis) goes stale the moment the report is written, with no way to detect that the forecast has materially changed as new evidence emerges.
- **Proposed.** Add an optional scheduler that periodically re-runs a saved forecast definition (prompt + depth + options) on a cron-like interval, and diffs each new forecast against the previous to flag material drift (e.g. a question's probability moved >X, or a new dominant actor/coalition appeared). Store schedules in uploads/schedules/<id>/schedule.json; a background daemon thread (same pattern as the monitor threads) checks due schedules and calls PipelineOrchestrator.start. Each run links to the prior run so the UI can show a timeline of how the forecast evolved, with drift highlighted. Optionally fire a webhook when drift exceeds a threshold.
- **Why.** Forecasts about live events are perishable; a static one-time report under-serves the exact 'what if / what next' use case the engine is built for. Scheduled re-runs turn it into a continuous monitoring instrument and, with drift detection, surface the most valuable signal of all - when and why the prediction changed - which a human analyst would otherwise miss. It reuses the existing durable pipeline + reconcile machinery, so the runtime is well-trodden.
- **Design.** schedule.json: {schedule_id, prompt, depth, options, interval_minutes|cron, last_run_at, next_run_at, run_pipeline_ids:[...], enabled}. scheduler.py: daemon thread sleeps SCHEDULER_TICK_SECONDS, scans schedules, for any due+enabled calls PipelineOrchestrator.start(prompt, options...), appends pipeline_id, updates next_run_at. On app start only launch if SCHEDULER_ENABLED. forecast_diff(prev, curr): per matched question_id delta=|curr.p-prev.p|; flag if >DRIFT_PROB_THRESHOLD; also diff top actors/coalitions from run_summary. If DRIFT_WEBHOOK_URL set and drift flagged, POST a summary. Fully gated: SCHEDULER_ENABLED=false (default) -> no thread, no behavior change.
- **Impact.** Converts a one-shot report into continuous situation monitoring with automatic change-alerts; a qualitatively new capability for live/ongoing events and a strong automation story.
- **Depends on.** Needs the structured forecast (forecast.json) for meaningful drift detection on probabilities. Pairs with the API/SDK for external triggering and with reconcile_orphans (already present) for crash recovery of scheduled runs.
- **Risk.** Unattended runs burn LLM/compute budget silently (mitigate: default-off; per-schedule max-runs cap and a global concurrency guard so a schedule can't pile up runs if one is slow). Crash/restart could miss or double-fire ticks (mitigate: persist next_run_at and reconcile on startup; idempotent due-check that won't start a run if one for that schedule is already in-flight).

#### [I-9-0] Ensemble / multi-seed runs with aggregated, probabilistic forecasts

`capability` · effort **L** · backend/scripts/run_parallel_simulation.py (seed all RNGs from env at startup; ~near imports and in get_active_agents_for_round:1021); backend/app/services/simulation_runner.py (start_simulation:319 accept seed; inject env['SIM_SEED'] in the env block at :446); backend/app/services/pipeline_orchestrator.py (_run RUN stage :1611-1668 loop K seeds into K sim dirs; new aggregate step); new backend/app/services/ensemble.py (aggregate K run_summary.json -> ensemble_summary.json); backend/app/config.py (SIM_ENSEMBLE_SIZE=1, SIM_BASE_SEED); frontend/src/components/research/ForecastReport.vue (show probability/spread)

- **Today.** Every prompt produces exactly ONE simulation and the simulation is non-deterministic and un-seeded: get_active_agents_for_round uses bare random.random() and _weighted_sample_without_replacement (backend/scripts/run_parallel_simulation.py:1016, 1069) with no seed, and start_simulation (backend/app/services/simulation_runner.py:319, env setup at :446-461) never passes any RANDOM_SEED env to the OASIS subprocess. So a single run is one unrepeatable sample from a stochastic process, and the orchestrator (_run, pipeline_orchestrator.py:1611-1668) launches it once and reports off that one draw. There is no aggregation, no variance, no probability — the forecast rests on a single Monte-Carlo sample.
- **Proposed.** Add an optional ensemble mode that runs the OASIS simulation K times with different fixed seeds (sharing the same research, ontology, graph, personas, and config — all already produced once), then aggregates the K runs into distributional outcomes: per-outcome empirical frequency (= probability), mean/median action volumes with spread, and a consensus/divergence measure across runs. The report then forecasts off the aggregate (e.g. 'in 5/7 runs the coalition fractured -> P~=0.71') instead of one draw. Requires (a) a seed plumbed end-to-end into the subprocess for reproducibility, and (b) an aggregation step writing ensemble_summary.json.
- **Why.** A single run of a stochastic multi-agent system is statistically the weakest possible basis for a forecast - it cannot express probability or uncertainty and is not reproducible. Running K seeds turns the engine into a proper Monte-Carlo forecaster: emergent outcomes that recur across seeds are robust signals; outcomes that appear once are noise. This is the single highest-leverage way to raise forecast QUALITY and credibility, it directly produces the empirical probabilities every other capability (structured forecast, calibration, model-comparison) needs, and seeding also makes runs reproducible for debugging and backtesting.
- **Design.** Config SIM_ENSEMBLE_SIZE (default 1 -> exactly today's single non-seeded run; >1 enables ensemble) + SIM_BASE_SEED. run_parallel_simulation.py: read os.environ['SIM_SEED']; if set, random.seed(seed) and seed numpy/torch if present, so a given seed is reproducible. start_simulation gains seed param -> env['SIM_SEED']=str(seed). Orchestrator RUN stage: if ensemble_size>1, reuse the prepared sim dir to spawn K runs into <sim>/ensemble/seed_<n>/ (each its own actions.jsonl + db), seeds = base_seed+i; after all complete, ensemble.aggregate() reads each run_summary.json and emits ensemble_summary.json {n_runs, seeds, outcome_frequencies, per_round_mean_std, agent_engagement_mean_std, consensus_score}. ReportAgent gets the aggregate as an extra outcomes block. Default size=1 => current behavior byte-for-byte (no seed env, single run).
- **Impact.** Converts one unrepeatable sample into K-run empirical probability distributions with quantified uncertainty/consensus; reproducible runs. The biggest single lever on forecast quality and the data source for probabilistic, calibratable forecasts.
- **Depends on.** Self-contained on the simulation side. Is the empirical-probability source for the structured forecast, calibration, and model-comparison capabilities. Reuses existing prepare/run/run_summary machinery.
- **Risk.** Cost scales linearly with K (mitigate: default K=1; document the cost; allow capping rounds for ensemble members). True reproducibility may be imperfect if OASIS/LLM calls have hidden nondeterminism (LLM sampling) - seeding the agent-selection RNG still materially reduces variance and is honest about residual LLM stochasticity. Disk: K sim dirs (mitigate: store only run_summary per member, optionally prune raw dbs).

#### [I-9-2] Backtesting & calibration harness vs ground truth

`capability` · effort **L** · backend/app/services/calibration.py (new: scoring + aggregate store); backend/app/api/report.py (new POST /<report_id>/resolve, GET /<report_id>/resolution, GET /calibration); backend/app/config.py (CALIBRATION_ENABLED=false, CALIBRATION_DATA_DIR); backend/app/services/report_agent.py (forecast.json must carry resolution_criteria/date - depends on the structured-forecast item); frontend: new CalibrationView + resolution-entry form linked from PipelineHistory.vue

- **Today.** There is no notion of a forecast's resolution. Pipelines complete at STAGE_REPORT and are never revisited (PipelineManager.list_pipelines, pipeline_orchestrator.py:271). No file records what actually happened, and nothing computes accuracy. as_of_date exists for the research dossier (parse_as_of, used for graph valid_at at orchestrator:1510) but never defines a future resolution horizon or scores the prediction. The system can predict but can never learn whether it was right, so there is zero calibration signal and no empirical basis to tune depth/ensemble-size/model.
- **Proposed.** Add a backtesting/calibration layer that (a) lets a user record ground-truth outcomes against a completed forecast's resolution questions, and (b) computes scoring (Brier score for probabilities, hit-rate, calibration buckets) across many resolved forecasts. Two modes: retrospective backtest (run with an as_of cutoff in the past, then score against known outcomes you supply) and live tracking (forecast now, resolve later). Persist resolutions to uploads/reports/<id>/resolution.json and maintain an aggregate uploads/calibration/scores.json with per-model/per-depth/per-ensemble-size breakdowns.
- **Why.** Calibration is the most important quality signal a forecasting system can have, and the only objective way to justify expensive deep/ensemble settings. Without it every quality claim is unfalsifiable. A backtest harness converts the pipeline from 'plausible narrative generator' into a measurable forecaster whose accuracy can be tracked and improved - a categorical jump in credibility and a foundation for self-improvement.
- **Design.** resolution.json: {report_id, resolved_at, outcomes:[{question_id, actual:0|1|number, note}]}. calibration.py: score_report(forecast, resolution) -> per-question Brier (p-actual)^2, hit round(p)==actual; aggregate() scans reports with both forecast.json+resolution.json -> {n, mean_brier, calibration_buckets:[{p_range, predicted_mean, observed_freq, n}], by_model, by_depth, by_ensemble_size} -> scores.json. Retrospective backtest reuses the existing as_of support; user supplies resolution after run. All gated by CALIBRATION_ENABLED; absent -> feature invisible, pipeline unchanged.
- **Impact.** Provides objective accuracy/calibration metrics, enabling evidence-based tuning of depth, ensemble size, and model. Transforms credibility and enables a 'track record' view.
- **Depends on.** Hard dependency on the structured forecast (resolution_criteria + numeric probabilities). Strongly complemented by ensemble mode (well-calibrated probabilities need empirical frequencies).
- **Risk.** Ground truth is user-supplied and may be sparse/biased - keep scoring best-effort and clearly label small-n calibration buckets. Retrospective backtests can leak future info if research isn't truly cut off at as_of (mitigate: document that DeerFlow web search may surface post-cutoff facts; offer a 'research disabled / supply your own dossier' backtest path).

#### [I-9-4] Model-comparison harness (same forecast, multiple providers)

`capability` · effort **L** · backend/app/utils/llm_client.py (allow per-instance provider/base_url/model override instead of only reading Config); backend/app/services/report_agent.py (__init__ already accepts llm_client at :947, so mostly wiring a provider-specific client); backend/app/services/pipeline_orchestrator.py (new compare_models() rerunning REPORT per provider on a shared simulation); backend/app/api/research.py (new POST /<pipeline_id>/compare); backend/app/config.py (MODEL_COMPARISON_ENABLED=false); frontend: comparison view

- **Today.** Config.LLM_PROVIDER is a single global (config.py:39) used for the entire pipeline; Config.apply_provider (config.py:175) mutates it process-wide and persists to .env, so switching providers is a global, stateful operation, not a per-run choice. There are 8 providers in PROVIDER_META (config.py:142) and DeerFlow model overrides per-run already exist (research_model in start(), pipeline_orchestrator.py:909), but the report/simulation LLM is always the global provider. There is no way to run the same question through, say, claude-cli vs deepseek vs glm and compare resulting forecasts side by side.
- **Proposed.** Add an optional model-comparison mode that runs the same prompt (sharing research+graph+simulation where possible) but generates the forecast/report under multiple providers, then presents a side-by-side comparison of their structured forecasts. The cheapest, highest-signal variant fixes one simulation and reruns only the REPORT stage under each provider (where provider reasoning quality shows most); a fuller variant also varies the simulation/persona LLM. Requires plumbing a per-call provider override into LLMClient/ReportAgent instead of the global, without changing the global default.
- **Why.** Provider quality varies enormously for reasoning-heavy synthesis and users have no evidence for which to trust on their kind of question. A comparison harness surfaces disagreement (a genuine uncertainty signal - if 3 models agree on a probability that is far stronger than one model's guess) and, with calibration, lets users pick the empirically best provider for their domain. It also showcases the system's provider-agnostic design as a first-class capability.
- **Design.** Refactor LLMClient.__init__ to accept optional (provider, base_url, model, api_key) overriding Config defaults (default None -> exactly today's global behavior). compare_models(base_pipeline_id, providers:[...]): require base sim complete; for each provider build LLMClient(provider=p), construct ReportAgent(..., llm_client=that), generate into uploads/reports/<base>__<provider>/, collect each forecast.json. Aggregate comparison.json: per-question table of (provider -> probability/confidence) + agreement score (variance across providers). Gated by flag/endpoint; global provider and all existing single-provider runs untouched.
- **Impact.** Side-by-side multi-model forecasts expose model disagreement as an uncertainty signal and enable empirically-grounded provider selection; higher-quality, better-hedged forecasts.
- **Depends on.** Best with the structured forecast (makes the side-by-side meaningful) and calibration (to declare a winner). Requires the LLMClient per-instance-provider refactor as the only structural prerequisite.
- **Risk.** CLI providers (claude-cli/codex-cli) cannot run concurrently cheaply and need their own credentials; HTTP providers need API keys (mitigate: only offer providers whose preflight credentials pass; run CLI sequentially). Cost scales with provider count (default-off, user picks the set explicitly).

### 7.6 Observability & cost/latency telemetry

#### [I-5-5] Instrument SimulationIPCClient with per-command latency and timeout-rate telemetry

`observability` · effort **S** · backend/app/services/simulation_ipc.py, backend/app/config.py

- **Today.** SimulationIPCClient.send_command (simulation_ipc.py:117-187) polls the filesystem for responses and logs only 'sent' / 'received' / 'timed out' at INFO/ERROR. It measures start_time internally (L155) but never records the round-trip latency, never counts how often interviews time out, and never attributes slow/failed interviews to an agent_id or platform. The report agent's interview tools (used during section generation to probe agents) thus have no observability into IPC health, even though a slow or timing-out simulation env directly degrades report quality (missing agent perspectives) — and the failure is invisible until the report reads thin.
- **Proposed.** Add optional lightweight instrumentation in send_command: record per-command {command_type, agent_id (if present), platform, latency_ms, status (completed/failed/timeout), poll_count}. Aggregate into uploads/simulations/<id>/ipc_telemetry.jsonl (or feed the same LLMMeter-style sink) and expose summary stats (count, p50/p95 latency, timeout rate) so the report stage and operators can see whether agent interviews are healthy.
- **Why.** Interview timeouts silently starve the report of agent perspectives, lowering forecast richness; today that degradation is undiagnosable. Latency/timeout telemetry surfaces a slow or wedged simulation env early, lets operators tune the interview timeout/poll_interval, and distinguishes 'report is thin because the model chose not to interview' from 'report is thin because interviews kept timing out' — a real quality-vs-infrastructure ambiguity.
- **Design.** simulation_ipc.py: in send_command, capture t0; on response, compute latency_ms and poll_count; on TimeoutError, record status='timeout'. If Config.IPC_TELEMETRY_ENABLED, append a JSON line to os.path.join(self.simulation_dir, 'ipc_telemetry.jsonl'). Add classmethod SimulationIPCClient.summarize(simulation_dir) -> {count, timeout_rate, p50_ms, p95_ms, by_command}. Optionally fold that summary into write_run_summary / run_telemetry.json (proposal 2). Config: IPC_TELEMETRY_ENABLED=bool.
- **Impact.** Visibility into interview health (latency distribution + timeout rate) that directly explains report richness; early detection of a wedged sim env; tunable IPC timeouts grounded in measured p95.
- **Depends on.** Standalone (file-based, no new deps). Composes with proposals 2/3 if the run telemetry summary pulls in IPC stats and structured logging is enabled.
- **Risk.** Low. Pure measurement around an existing code path, gated by Config.IPC_TELEMETRY_ENABLED (default false). Writes are best-effort try/except so they never affect interview success/failure semantics or timing.

#### [I-5-7] Parse and structure the DeerFlow research stage's already-emitted token usage into pipeline telemetry

`observability` · effort **S** · backend/app/services/pipeline_orchestrator.py, backend/app/config.py (pricing for research models reused from proposal 1)

- **Today.** The DeerFlow bridge already computes real token usage per turn and writes it to research_progress.log as a free-text line: plog.write('usage', f'tokens in={...} out={...} total={...}') at deerflow_bridge/deerflow_research.py:678-679, and logs every [tool]/[result]/[stage] event. But on the MiroFish side, DeerFlowResearchRunner.run (pipeline_orchestrator.py:457-481) only pattern-matches those lines to nudge a heuristic progress int (tool_events count → percentage) and throws the usage numbers away. So the research stage — often the single most expensive stage (0-30% band, deep budget 10800s) — contributes zero structured cost/telemetry to the run, even though the data is right there in the stream.
- **Proposed.** In DeerFlowResearchRunner's stdout reader loop, additionally parse the '[usage] tokens in=.. out=.. total=..' lines and the tool-call/result counts, accumulate them, and return them in the run() result dict alongside report/actors/sources. Feed those into the run telemetry summary (proposal 2) and the meter (proposal 1) as the research stage's contribution, so research cost/tokens/tool-count appear in the same rollup as ontology/graph/report rather than being a stdout-only artifact.
- **Why.** Closes the biggest single observability gap with almost no new instrumentation — the expensive research stage's economics are already measured upstream and merely discarded at the boundary. Capturing them makes the whole-run cost rollup actually whole, makes depth selection (quick/standard/deep) a data-driven cost decision, and lets users see research tool-call volume (a proxy for evidence breadth → forecast grounding quality).
- **Design.** In DeerFlowResearchRunner.run's for-line loop, add: regex r'tokens in=(\d+) out=(\d+) total=(\d+)' on '[usage]' lines to accumulate research_tokens; count '[tool]'/'[result]' occurrences (tool_events already counted at L466). After the loop, build research_telemetry={tokens_in, tokens_out, tokens_total, tool_calls, results, wall_s}. Add to the returned dict (L535-542). Orchestrator stores it in state.options['research_telemetry'] and (if meter on) emits a synthetic LLMCallRecord(provider=research_model, stage='research', tokens..., est_cost). _write_run_telemetry includes it.
- **Impact.** Research stage gains structured tokens/cost/tool-count in the unified telemetry, making whole-run cost accurate and depth tradeoffs measurable. No change to research behavior.
- **Depends on.** Best surfaced via proposals 1/2 (meter + run_telemetry.json) but standalone-usable: even without them, run() can return a research_telemetry dict the orchestrator stashes in state.options. Bridge already emits the data; the change is parser-side in the runner.
- **Risk.** Low. Parsing is additive and tolerant (regex with graceful no-match → zero); the existing progress heuristic is untouched. Token lines may be absent for some research models, so all fields default to None/0. No new subprocess contract — it reads lines the bridge already prints.

#### [I-5-1] Per-run telemetry summary artifact (run_telemetry.json) with stage durations, token/cost rollup, and failure attribution

`observability` · effort **M** · backend/app/services/pipeline_orchestrator.py, backend/app/api/research.py, backend/app/config.py

- **Today.** StageState already records started_at/finished_at (pipeline_orchestrator.py:106-108) but durations are never computed or surfaced; the UI only sees status/progress/message. _record_stage_artifacts (L1343-1372) deep-links produced files but emits no metrics. There is no single artifact summarizing a completed/failed run: how long each stage took, retries, token/cost (once the meter from proposal 1 exists), graph entity counts (already in state.options['graph_entity_count']), seeded edges (state.options['graph_seeded_edges']), communities, agent count, rounds, or section count. To diagnose a run today you must hand-correlate pipeline_state.json + research_progress.log + agent_log.jsonl + run_summary.json across three directories. The cost_signals dict (L1273-1294) collects chunk/round/section counts purely for the progress bar and is then discarded.
- **Proposed.** On every terminal transition (completed/failed/cancelled) in PipelineOrchestrator._run, write uploads/pipelines/<id>/run_telemetry.json: per-stage {duration_s, status, retries, error}, total wall-clock, token/cost rollup by stage and model (from the meter), key scale signals (chunks, entities, seeded edges, communities, agents, rounds, sections), and a failure summary (which stage, exception class, last log line). Register it as an artifact ('telemetry') and expose it via the existing GET /research/<id>/artifact/<name> route. Add a get_stage_durations() helper that derives durations from existing started_at/finished_at so timing works even with telemetry otherwise off.
- **Why.** Turns post-hoc debugging from archaeology into one file read. Stage durations expose the real bottleneck (research vs OASIS vs report) so users can pick depth/max_rounds intelligently. Failure attribution (stage + exception + last line) makes the resume feature far more actionable. Scale signals + cost together let users reason about the quality/cost frontier of deeper research — directly supporting better-calibrated forecasts at a known budget.
- **Design.** Add PipelineOrchestrator._write_run_telemetry(state, outcome): compute {stage: (finished_at - started_at).total_seconds()} from StageState; pull token/cost from LLMMeter.snapshot(state.pipeline_id) if enabled; merge state.options scale signals; atomic tmp+os.replace to uploads/pipelines/<id>/run_telemetry.json; state.artifacts['telemetry']=path. Call it in the completed branch and in the except PipelineCancelled/Exception branches. Extend ALLOWED artifact names in research.py to include 'telemetry'. Gate detail level behind Config.RUN_TELEMETRY_ENABLED (default true for durations since cost is cheap to compute and data already exists; token/cost section only when LLM_TELEMETRY_ENABLED).
- **Impact.** Single auditable run summary; durations and failure cause visible in UI and API; foundation for cross-run comparison and the StageTimeline view to show real timings and spend.
- **Depends on.** Builds on proposal 1's meter for token/cost (degrades to durations-only without it). Reuses _record_stage_artifacts artifact registry and the existing artifact API route (research.py:383). No new endpoint needed.
- **Risk.** Low. Writing the summary is best-effort in a try/except in the finally/terminal blocks of _run, so it can never mask the real run status. Behavior-neutral; durations derive from data already persisted.

#### [I-5-2] Structured JSON logging mode with run/stage correlation IDs via contextvars

`observability` · effort **M** · backend/app/utils/logger.py, backend/app/utils/log_context.py (new), backend/app/config.py, backend/app/services/pipeline_orchestrator.py

- **Today.** logger.py emits only human-readable text ('[time] LEVEL [name.func:line] message') to a date-named rotating file and console. Pipeline log lines are manually prefixed with f'[{pipeline_id}]' at dozens of call sites in pipeline_orchestrator.py (e.g. L782, L814, L1195, L1428, L1489, L1517, L1547, L1607, L1738, L1756). There is no machine-parseable log stream, no stable correlation between a pipeline_id and the subordinate graph_id/simulation_id/report_id, and no stage field — so grepping a multi-run log to reconstruct one run's timeline across services (orchestrator, graph_builder, zep_tools, report_agent, simulation_runner) is brittle and manual.
- **Proposed.** Add an optional JSON log handler and a contextvar-based filter that auto-injects {pipeline_id, stage, graph_id, simulation_id, report_id} into every LogRecord when set. Provide a small context manager (run_log_context(pipeline_id=..., stage=...)) set by the orchestrator's stage updater so all downstream service logs (graph_builder, zep_tools, report_agent, simulation_runner) are automatically correlated without changing their call sites or message strings. Keep the existing text handler for humans; add the JSON file (one line per record) only when enabled.
- **Why.** Multi-stage, multi-service, concurrent runs (the orchestrator runs pipelines in daemon threads and supports scenario forks running in parallel) make unstructured logs nearly impossible to slice per-run. Structured correlated logs are the backbone of any real diagnosis or aggregation, let an operator reconstruct exactly one run's cross-service timeline, and feed downstream tooling (jq, log shippers) cheaply. It also removes the error-prone manual '[id]' prefixing convention.
- **Design.** logger.py: add class JsonFormatter(logging.Formatter) emitting json.dumps({ts, level, logger, func, line, msg, **_log_context.get()}); add a _log_context: ContextVar[dict] and a logging.Filter that merges it into record attrs; setup_logger adds a second RotatingFileHandler(<date>.jsonl) with JsonFormatter only when Config.LOG_FORMAT=='json'. New helper utils/log_context.py with @contextmanager run_log_context(**fields) that updates/resets the contextvar. Orchestrator._make_stage_updater wraps stage bodies (or sets context once per stage) with run_log_context(pipeline_id=..., stage=..., graph_id=..., simulation_id=...).
- **Impact.** Any single run's full cross-service log timeline becomes one jq filter; concurrent/forked runs no longer interleave unparseably; enables log-based alerting and aggregation. Text logs unchanged for humans.
- **Depends on.** Independent of proposals 1-2 but composes well (same contextvar scope can drive the meter and the telemetry summary). Pure stdlib (logging + contextvars), no new packages.
- **Risk.** Low. Gate behind Config.LOG_FORMAT='json' (default 'text' → no new handler, zero change). contextvars are thread-safe and default to empty, so unset context just omits the fields. The JSON handler is additive; the existing text handler/console behavior is untouched.

#### [I-5-3] Optional run-level budget guard: abort or downgrade when token/cost/time thresholds are exceeded

`robustness` · effort **M** · backend/app/services/pipeline_orchestrator.py, backend/app/config.py

- **Today.** The only spend guardrails today are per-stage time watchdogs: DeerFlowResearchRunner uses DEERFLOW_DEPTH_BUDGETS time budgets + a SIGKILL watchdog (pipeline_orchestrator.py:415-437); OASIS rounds can be capped via max_rounds (L1620-1623). There is no token or dollar budget anywhere, and no aggregate run budget. A misconfigured deep run, a report ReAct loop that keeps calling tools (MAX_TOOL_CALLS_PER_SECTION exists per section but not per run), or an 80-agent OASIS run can burn unbounded tokens/cost with no cap and no early warning — the user only finds out when the bill or wall-clock lands.
- **Proposed.** Once the meter (proposal 1) exists, add an optional run-level budget evaluated at the existing cancellation checkpoints (the stage updater already raises PipelineCancelled at every progress callback, L1302-1304). If cumulative est_cost_usd, total_tokens, or wall-clock for the run exceeds a configured soft cap, log a structured warning and record it in telemetry; exceeding a hard cap raises a new PipelineBudgetExceeded (subclass of PipelineCancelled so it cleanly tears down research/OASIS subprocesses via the existing cancel machinery) and marks the run 'budget_exceeded' rather than a generic failure.
- **Why.** Converts the system from 'hope the config is sane' to 'bounded, predictable spend' — essential for running many forecasts or letting non-experts trigger deep runs. Reusing the existing cancellation checkpoints means clean subprocess teardown (the cancel path already kills DeerFlow and OASIS process groups), so a budget abort doesn't orphan cost-burning children. Soft caps give early warning before money is gone.
- **Design.** Config: RUN_COST_SOFT_USD/RUN_COST_HARD_USD, RUN_TOKEN_HARD, RUN_WALLCLOCK_HARD_S (all default 0 = off). New PipelineBudgetExceeded(PipelineCancelled) in pipeline_orchestrator.py. In _make_stage_updater.update(), after the cancel check, call self._check_budget(state) which reads LLMMeter.snapshot(pipeline_id) + run start time; soft breach → log+telemetry flag once; hard breach → raise PipelineBudgetExceeded. Terminal handler maps it to status 'budget_exceeded' in PipelineManager. Resume already reuses artifacts, so a budget-aborted run can be resumed after raising the cap.
- **Impact.** Bounded, predictable per-run cost/time; early-warning telemetry; graceful, clean abort with a distinct status the UI/resume can treat sensibly. Fully opt-in.
- **Depends on.** Hard-depends on proposal 1 (the meter provides cumulative cost/tokens). Reuses the cancel-event checkpoint in _make_stage_updater and the PipelineCancelled teardown paths (L1740-1754).
- **Risk.** Medium. A too-low cap could abort legitimate deep runs, so all caps default to 0/disabled and are clearly documented as opt-in. Must ensure PipelineBudgetExceeded inherits BaseException-style propagation like PipelineCancelled (L86-93) so deep except Exception layers don't swallow it. Cost estimates can be imprecise in CLI subscription mode, so prefer token/time caps there.

#### [I-5-4] Enrich ReportLogger and run_summary with LLM telemetry; add a report-level cost/timing rollup

`observability` · effort **M** · backend/app/services/report_agent.py, backend/app/api/report.py

- **Today.** ReportAgent's ReportLogger (report_agent.py:43-303) writes a rich JSONL with elapsed_seconds per event (tool_call, tool_result, llm_response, section_complete) but records zero token/cost and no per-section latency rollup. log_report_complete (L280-290) reports only total_sections + total_time_seconds. The report ReAct loop makes many LLM + tool calls per section (MIN/MAX_TOOL_CALLS, L1662, L1680) with no visibility into which sections or tools dominate cost/time. simulation_runner.write_run_summary (L1131) aggregates engagement/actions but, being a separate subprocess, captures none of the report stage's LLM economics. So the most LLM-intensive stage (report) has the least cost observability.
- **Proposed.** Thread the proposal-1 meter scope through the report stage so each ReportLogger event optionally carries the token/cost/latency of its triggering LLM call, and have log_report_complete emit a rollup: per-section {llm_calls, tool_calls, tokens, est_cost_usd, duration_s} plus report totals. Surface this via the existing GET /report/<id>/agent-log and a new compact field in the report's saved metadata so the UI can show 'this report cost ~$X over Y s across Z sections'.
- **Why.** The report stage is where forecast quality is produced and where tool-call budgets are spent; making its economics visible lets users tune MIN/MAX tool calls and section count against cost, spot sections that thrash the ReAct loop, and compare report quality vs spend across runs. It also closes the loop with the per-stage telemetry summary (proposal 2) so the 92-100% band is no longer a cost blind spot.
- **Design.** report_agent.py: set the meter contextvar scope to (pipeline_id or report_id, 'report') at generate_report start (when present); after each self.llm.chat*/chat_with_tools call, read the most recent LLMMeter record and pass {tokens, est_cost_usd, latency_ms} into the matching ReportLogger.log_llm_response/log_tool_call details. Add ReportLogger.log_report_complete(..., section_rollup=[...], totals={...}). Persist a compact 'telemetry' dict on the saved Report object (ReportManager.save_report). Standalone reports (no pipeline) use report_id as the meter scope key so manual generations are also metered.
- **Impact.** Per-section and per-report cost/latency/tool-call visibility; ability to tune the most expensive stage for the quality/cost tradeoff; richer agent-log UI. No change to report content or flow when telemetry disabled.
- **Depends on.** Depends on proposal 1's meter + contextvar. Reuses ReportLogger.log infrastructure and the existing /report/<id>/agent-log endpoint (report.py:764). The orchestrator already sets the report stage scope if proposal 1/3 land.
- **Risk.** Low. Telemetry fields are additive to existing JSONL entries (extra keys, backward-compatible for any current log readers). All gated by LLM_TELEMETRY_ENABLED; when off, ReportLogger behaves exactly as today.

#### [I-5-6] Live progress heartbeat with ETA and current-spend, surfaced through the existing status/progress APIs

`observability` · effort **M** · backend/app/services/pipeline_orchestrator.py, backend/app/api/research.py, backend/app/config.py

- **Today.** Live progress is a single global_progress int + a message string, written to pipeline_state.json and TaskManager (pipeline_orchestrator._make_stage_updater, L1296-1326). The dynamic-bands ETA machinery (L1257-1294) reweights the progress bar by chunk/round/section counts but never produces a time estimate, and there is no notion of 'stuck' detection: research can sit silently for many minutes (the watchdog only fires at the full budget, L430-437) with the UI frozen at one percentage and stale message. The frontend must poll /research/status/<id> (research.py:274) and separately tail /research/<id>/progress; nothing reports elapsed time, ETA, or spend-so-far in the status payload.
- **Proposed.** Extend the persisted state (and thus the status API) with a lightweight live heartbeat: last_progress_at timestamp, elapsed_s, a coarse ETA derived from the existing dynamic-bands weights + per-stage durations of prior runs (or current-run elapsed vs band position), and (when the meter is on) spend_so_far_usd/tokens. Add a staleness flag when last_progress_at is older than a threshold so the UI can show 'still working (model thinking)' instead of a frozen bar. Compute it all from data already collected; no new polling channel.
- **Why.** Long opaque waits are the single worst UX of this pipeline (deep runs are tens of minutes). An honest ETA + spend-so-far + stuck-detection makes long runs trustworthy, lets users decide whether to cancel before more budget burns, and reuses the dynamic-bands cost model that already exists but is currently only feeding a bar position. Staleness detection distinguishes 'wedged' from 'thinking', reducing needless cancels/retries.
- **Design.** PipelineState: add last_progress_at (str) and computed-on-read elapsed_s/eta_s/stale (or compute in the status API). _make_stage_updater.update() sets state.last_progress_at = _utcnow() each call. Add PipelineOrchestrator.estimate_eta(state): using dynamic_bands position of global_progress vs elapsed since created_at, project remaining time; clamp and label approximate. /research/status augments the dict with elapsed_s, eta_s, stale (now - last_progress_at > Config.PIPELINE_STALE_S, default e.g. 300), spend_so_far (from LLMMeter.snapshot if enabled). Config: PIPELINE_STALE_S=int.
- **Impact.** Status payload gains elapsed, ETA, spend-so-far, and a staleness flag; users get a trustworthy long-run experience and an informed cancel decision. Purely additive fields; existing consumers unaffected.
- **Depends on.** ETA reuses _recompute_dynamic_bands weights (already present). spend_so_far needs proposal 1; without it the heartbeat still provides elapsed/ETA/staleness. Surfaced through existing /research/status/<id> serialization (PipelineState.to_dict).
- **Risk.** Low. All new fields are optional in PipelineState with from_dict defaults (the codebase already does this for artifacts at L179), so old state files load fine. ETA is explicitly labeled approximate. No change to control flow; the updater just stamps a timestamp it already implicitly has.

#### [I-5-0] Central LLM call meter: capture token/cost/latency from every provider into a per-run accounting sink

`observability` · effort **L** · backend/app/utils/llm_client.py, backend/app/utils/llm_meter.py (new), backend/app/config.py, backend/app/services/pipeline_orchestrator.py, backend/pyproject.toml

- **Today.** LLMClient.chat() returns a bare str and throws away all usage metadata. backend/app/utils/llm_client.py:333-344 (_chat_openai) reads response.choices[0] but never touches response.usage (which carries prompt_tokens/completion_tokens/total_tokens). _chat_claude_cli parses the CLI JSON envelope at L401-412 and discards its cost/usage fields (claude -p --output-format json returns total_cost_usd, usage.input_tokens/output_tokens). _chat_codex_cli at L462 explicitly detects the line 'tokens used' and *strips* it. There is no count of how many LLM calls a run makes, how many tokens it burned, how long each took, or how many retries fired (retries logged as warnings only, L105). The whole pipeline (ontology, persona gen, ~80 agents x N rounds in OASIS, report ReAct loops) is a cost black box. The only token data anywhere is a free-text string in the research stage's research_progress.log (deerflow_bridge/deerflow_research.py:678-679) that is never parsed back.
- **Proposed.** Introduce an optional LLMMeter sink threaded through LLMClient. On every chat()/chat_json()/chat_with_tools() call, record {provider, model, role/purpose tag, prompt_tokens, completion_tokens, total_tokens, est_cost_usd, latency_ms, retries, ok/error}. Extract usage from each provider path: OpenAI response.usage; Claude CLI envelope's usage + total_cost_usd; Codex's 'tokens used' line (parse instead of strip). When a provider gives no token counts (CLI subscription mode often omits them), fall back to a tiktoken/heuristic char-based estimate. Aggregate per (pipeline_id, stage) via a contextvar so the orchestrator's stage updater can attribute spend to research/ontology/graph/prepare/run/report without plumbing args through every call site.
- **Why.** Cost/latency are the dominant operational risks of this pipeline (a deep run spans DeerFlow research + graph build over 100+ chunks + 80-agent multi-round OASIS + multi-section ReAct report = potentially thousands of LLM calls and dollars). Today an operator cannot answer 'why did this run cost $40' or 'which stage is the token hog' or 'is the report ReAct loop thrashing'. A central meter turns the system from a black box into something tunable, makes the existing ETA/dynamic-bands heuristic (pipeline_orchestrator.py:1257-1294) far more honest, and is the prerequisite for budget caps and cost-aware depth selection.
- **Design.** New backend/app/utils/llm_meter.py: dataclass LLMCallRecord; class LLMMeter with record(rec) appending to an in-memory list + optional JSONL at uploads/pipelines/<id>/llm_calls.jsonl. ContextVar _current_meter_scope = (pipeline_id, stage). In llm_client._chat_openai return path, also pass response.usage to meter; add a private _emit_meter(provider, model, usage_obj, latency_ms, retries, ok). Config: LLM_TELEMETRY_ENABLED=bool, LLM_PRICING={model: {in_per_mtok, out_per_mtok}} (env-overridable JSON). Orchestrator: in _make_stage_updater, set the contextvar scope to (state.pipeline_id, stage).
- **Impact.** Every run gains exact (or estimated) token + cost + latency accounting attributable to stage and model; enables cost dashboards, regression alerts, and per-stage tuning. No behavior change when disabled.
- **Depends on.** Touches llm_client.py (all 3 provider paths + chat/chat_json/chat_with_tools), a new utils/llm_meter.py, and a contextvar set in pipeline_orchestrator._make_stage_updater. Optional tiktoken dependency in pyproject.toml for estimation fallback (graceful import-guard if absent).
- **Risk.** Low. Gate behind Config.LLM_TELEMETRY_ENABLED (default false → meter is a no-op, chat() signature unchanged). Pricing tables drift, so cost is labeled 'estimated' and pricing lives in a Config dict overridable by env. Must never let a metering exception break an LLM call (wrap sink writes in try/except).

### 7.7 Orchestration robustness & resumability

#### [I-4-4] Schema-versioned pipeline state with forward/backward migration on load

`robustness` · effort **S** · backend/app/services/pipeline_orchestrator.py (PipelineState 123-180 add schema_version; PipelineManager.load 242-251 + new _migrate; save 207-214), backend/app/config.py (PIPELINE_STRICT_SCHEMA), backend/app/api/research.py (surface incompatible-version as 409)

- **Today.** PipelineState.from_dict (pipeline_orchestrator.py:154-180) tolerates missing keys by defaulting (e.g. artifacts default to empty dict for old state files), and the schema has accreted fields across many tasks (research_pid, artifacts, options['dynamic_bands','cost_signals','scenario_overlay','resume_count',...]). There is no schema_version field on pipeline_state.json. mark_failed and edit_dossier mutate the raw JSON dict directly (217-240; research.py:332-378). Future shape changes can only be handled by ad-hoc per-field defaults scattered through from_dict, and there is no place to run a one-time migration or to detect an incompatible newer-version file written by a future build.
- **Proposed.** Add schema_version:int to PipelineState (current = an explicit constant), written on every save. Add PipelineManager._migrate(data) invoked at the top of load()/from_dict that applies ordered migration functions from the file's version up to current (e.g. backfill artifacts/manifest pointers, normalize options sub-dicts), and refuses (or read-only-degrades) files whose version is newer than the running code. Refuse-newer behavior relaxable via Config.PIPELINE_STRICT_SCHEMA (default True).
- **Why.** The state file is the single source of truth for resume/cancel/continue/fork across restarts and upgrades; without versioning, every future field addition risks subtly mis-parsing old records and a downgrade can silently corrupt a newer file. Explicit versioning + migration makes resumability durable across deploys — exactly the orchestration-robustness mandate.
- **Design.** PIPELINE_SCHEMA_VERSION=2 (module const). PipelineState.schema_version:int=PIPELINE_SCHEMA_VERSION. load: data=json; v=data.get('schema_version',1); if v>PIPELINE_SCHEMA_VERSION and Config.PIPELINE_STRICT_SCHEMA: return {'__incompatible__':v}; data=_migrate(data,v). _migrate: MIGRATIONS={1:_m1,...}; for ver in range(v,CURRENT): data=MIGRATIONS[ver](data); data['schema_version']=CURRENT. from_dict reads schema_version; save always writes current. API /status maps __incompatible__ to 409.
- **Impact.** Guarantees old pipeline records remain resumable after upgrades and prevents a newer-then-older binary from clobbering state. Centralizes shape evolution into one auditable migration ladder instead of scattered defaults.
- **Depends on.** PipelineState.to_dict/from_dict, PipelineManager.load/save. Independent of the other proposals but pairs naturally with the manifest (version the manifest too).
- **Risk.** Low: migrations run on read and must be pure and idempotent. The refuse-newer path needs the API to return a clear 409 rather than 500; the flag lets operators bypass in emergencies.

#### [I-4-0] Report stage resumability: reuse persisted per-section markdown instead of regenerating from scratch

`robustness` · effort **M** · backend/app/services/pipeline_orchestrator.py (REPORT stage ~1686-1724), backend/app/services/report_agent.py (generate_report ~1953-2200, ReportManager ~2330+), backend/app/config.py (add REPORT_RESUME_SECTIONS)

- **Today.** In PipelineOrchestrator._run the REPORT stage (backend/app/services/pipeline_orchestrator.py:1686-1724) always mints a fresh report_id = f"report_{uuid.uuid4().hex[:12]}" on every run AND every resume(), then calls ReportAgent.generate_report(report_id=report_id). ReportManager already persists outline.json and per-section section_NN.md incrementally (report_agent.py:2042 save_outline, 2111 save_section, 2145 assemble_full_report; each section's ReAct loop is the single most token-expensive step, doing multiple tool calls against the graph + simulation). But because resume() only resets the failed current_stage to pending and the REPORT stage has no '_reuse' guard like GRAPH/PREPARE/RUN do, a pipeline that fails on section 6 of 8 discards all 5 completed sections and the outline and regenerates everything. report.status == ReportStatus.FAILED is also raised as a hard RuntimeError (1722-1723) even when most sections succeeded and assemble_full_report produced a usable partial.
- **Proposed.** Make the REPORT stage resumable at section granularity, gated behind Config.REPORT_RESUME_SECTIONS (default True, degrades to current behavior when False). Persist report_id into PipelineState (field already exists) before calling generate_report; add a _reuse guard at the top of REPORT that, when state.report_id exists and reports/<report_id>/outline.json is present, passes resume=True to ReportAgent. generate_report's resume path loads outline.json and skips any section i whose section_{i:02d}.md already exists and is non-empty (validated against an outline signature), regenerating only missing/failed sections before re-running assemble_full_report.
- **Why.** Report generation is the longest tool-augmented LLM stage; losing it to a transient graph/LLM timeout on the last section forces a full expensive redo and is the most common late-stage failure. Section-level resume turns a 100% redo into a <1-section redo, dramatically improving robustness/cost on long forecasts and letting a near-complete report be salvaged instead of discarded.
- **Design.** Config.REPORT_RESUME_SECTIONS env bool default true. In _run REPORT: report_id = state.report_id if (Config.REPORT_RESUME_SECTIONS and state.report_id and os.path.exists(os.path.join(ReportManager.REPORTS_DIR, state.report_id,'outline.json'))) else f'report_{uuid4...}'; state.report_id=report_id; PipelineManager.save(state) BEFORE generate_report so a crash keeps the pointer. ReportAgent.generate_report(report_id, resume): if resume and outline exists -> outline=load_outline; sig=sha1(json(outline)+situation_brief); section loop: if resume and getsize(section_path)>0 and stored sig matches: generated_sections.append(read); continue; else regenerate + save_section + write sig.
- **Impact.** Cuts resume cost/latency of the report stage by completed_sections/total_sections, commonly 70-90% on late failures; eliminates the 'failed on final section -> regenerate entire report' worst case.
- **Depends on.** ReportManager section persistence helpers already exist (_get_section_path, _get_outline_path, save_section, assemble_full_report). Requires persisting state.report_id before generate_report and a resume branch in ReportAgent.generate_report.
- **Risk.** Low-medium: reusing sections written under a different outline could mix incompatible content; mitigate with an outline+situation_brief hash sidecar that invalidates cached sections on change. Flag-gated so default-on can be disabled instantly.

#### [I-4-1] Persisted run heartbeat + owner lease so reconcile_orphans distinguishes dead pipelines from slow ones

`robustness` · effort **M** · backend/app/services/pipeline_orchestrator.py (PipelineState ~123-180, reconcile_orphans ~763-793, _run ~1383-1772, add PipelineManager.touch_heartbeat), backend/app/config.py (PIPELINE_HEARTBEAT_ENABLED, PIPELINE_HEARTBEAT_INTERVAL_S, PIPELINE_HEARTBEAT_STALE_S)

- **Today.** reconcile_orphans (pipeline_orchestrator.py:763-793) treats any pipeline persisted as status=='running' that is not in cls._threads as a dead orphan and marks it failed. This is only correct under a single backend process whose _threads is authoritative. No heartbeat is written into pipeline_state.json; updated_at (210) only advances on progress callbacks. Long quiet stretches are common and expected: DeerFlow deep research runs silently for many minutes (the watchdog exists precisely because the subprocess emits no output while the model thinks), and persona generation in PREPARE runs between sparse callbacks. A second worker / gunicorn process / racing restart could mark a genuinely-live run failed, or two workers could both believe they own it.
- **Proposed.** Add heartbeat_at + owner_pid + owner_boot_id to PipelineState, with heartbeat_at refreshed on a fixed wall-clock cadence by a lightweight watcher thread in _run (independent of stage progress). reconcile_orphans reaps a running pipeline only when (a) owner_boot_id != current boot id OR owner_pid is not alive, AND (b) heartbeat_at older than Config.PIPELINE_HEARTBEAT_STALE_S. Gated by Config.PIPELINE_HEARTBEAT_ENABLED (default True); when disabled, falls back to today's _threads-based reap.
- **Why.** Makes orphan reconciliation correct across restarts and any multi-worker deployment instead of relying on in-process _threads, and prevents false-positive reaping of slow-but-alive deep-research/persona phases. Turns 'is this run dead?' from a guess into an evidence-based liveness decision — a core robustness win for a pipeline whose stages legitimately go quiet for minutes.
- **Design.** module _BOOT_ID=uuid4().hex. PipelineState += owner_pid:Optional[int], owner_boot_id:Optional[str], heartbeat_at:Optional[str]. PipelineManager.touch_heartbeat(pid): load json, set heartbeat_at=_utcnow(), atomic replace (no dataclass rebuild). _run start: state.owner_pid=os.getpid(); state.owner_boot_id=_BOOT_ID; spawn daemon Thread looping every INTERVAL_S calling touch_heartbeat until a threading.Event set in finally. reconcile_orphans: alive = owner_boot_id==_BOOT_ID and pid in _threads; if not alive: stale = owner_pid not alive or (now-parse(heartbeat_at))>STALE_S; reap only if stale (else leave running).
- **Impact.** Eliminates (1) reaping a live long-running research/prepare phase after a benign reload, and (2) double-execution/state clobber under multiple workers. Adds an auditable liveness signal the UI can surface (e.g. 'last heartbeat 8s ago').
- **Depends on.** PipelineState/from_dict/to_dict field additions, reconcile_orphans, _run lifecycle, Config. boot_id is a module-level uuid generated at import; pid-liveness via os.kill(pid,0) or psutil.
- **Risk.** Low: extra small JSON writes on a timer; mitigate with a targeted atomic heartbeat-only update (like mark_failed) rather than a full save. Flag-gated.

#### [I-4-3] Artifact manifest with content hashes for trustworthy stage reuse and corruption detection

`robustness` · effort **M** · backend/app/services/pipeline_orchestrator.py (_record_stage_artifacts 1343-1372; add _validate_reuse used by GRAPH/PREPARE/RUN reuse branches; PipelineManager manifest read/write), backend/app/config.py (PIPELINE_VALIDATE_ARTIFACTS), backend/app/api/research.py (expose manifest via /status or a new /manifest)

- **Today.** Stage reuse trusts status=='completed' plus, for some artifacts, existence-and-nonzero-size only. _record_stage_artifacts (pipeline_orchestrator.py:1343-1372) records artifact paths via os.path.exists + getsize>0. Only GRAPH has a semantic health check (entity count, 1480-1493). PREPARE self-heals if SimulationManager state is missing (1564-1569) but trusts simulation_config.json if present. There is no record of what content a stage produced — a truncated research_report.md >=400 chars (the only guard, 1392/583) or a half-written actors.json passes reuse and silently degrades every downstream stage. _load_research_handoff's length check is the lone content guard and covers only the markdown report.
- **Proposed.** Introduce a per-pipeline artifact manifest (handoff/manifest.json) recording per artifact {path, sha256, bytes, produced_by_stage, produced_at, schema_ok}. Compute it at stage completion (extend _record_stage_artifacts). On reuse, validate the artifact against its manifest entry (hash, size, lightweight schema probe) before honoring 'completed'; on mismatch, demote the stage to pending and rebuild, recording a resumed_stage_validation breadcrumb like the existing graph-rebuild path. Gate validation behind Config.PIPELINE_VALIDATE_ARTIFACTS (default True); reuse-without-validation when False.
- **Why.** Generalizes the one-off graph health check into a uniform evidence-based reuse contract for every stage, so resume never builds on a corrupted or half-written artifact and silently emits a degraded forecast. Hashes also give the UI/API a stable artifact identity and enable cheap change-detection for the report-section invalidation above.
- **Design.** PipelineManager.manifest_path(pid)=handoff/manifest.json. _complete_stage -> _record_stage_artifacts also writes manifest entries: streaming sha256, bytes, schema_ok via per-name probe (actors.json: dict with 'actors' list; ontology.json: has entity_types; communities.json: list). _validate_reuse(state,stage): recompute hash/size, compare to manifest, run probe; return ok. GRAPH/PREPARE/RUN reuse branches: if Config.PIPELINE_VALIDATE_ARTIFACTS and not _validate_reuse(...): _reuse=False; state.options['resumed_stage_validation']=f'{stage}_rebuilt_manifest_mismatch'.
- **Impact.** Prevents an entire class of silent-corruption forecasts on resume; converts undetectable garbage-in into a visible, auto-recovered rebuild with an audit breadcrumb. Adds artifact integrity/provenance metadata reusable for caching and dedup.
- **Depends on.** Builds on _record_stage_artifacts and the artifacts dict already on PipelineState; formalizes the existing graph health check. Schema probes can reuse situation_brief/extract_relationship_rows for actors.json.
- **Risk.** Low: hashing large research_report.md / graph dumps adds I/O at stage boundaries only (not per-tick). Schema probes must be best-effort and never fail a fresh run. Flag-gated.

#### [I-4-5] Prompt cancellation in PREPARE and tighter RUN poll cancel point to cap post-cancel spend

`robustness` · effort **M** · backend/app/services/pipeline_orchestrator.py (RUN poll loop 1632-1659; PREPARE call 1581-1588), backend/app/services/simulation_manager.py (prepare_simulation + persona worker pool accept cancel_check)

- **Today.** Cancellation is delivered via a threading.Event checked inside _make_stage_updater.update() (pipeline_orchestrator.py:1302-1304), so it only fires when a stage emits a progress callback. The RUN poll loop checks the event at loop top but sleeps time.sleep(5) (1659) and only emits progress on round change, so cancel can wait up to ~5s plus a quiet round. PREPARE (prepare_simulation, 1581-1588) runs persona generation with parallel_profile_count 3-8; between prepare_cb ticks the long per-persona LLM+Zep work cannot observe the cancel event at all, so cancelling during PREPARE keeps burning LLM credits until the next callback (potentially minutes for slow CLI providers). The research and OASIS subprocesses are killed promptly, but in-process LLM stages are not interruptible between callbacks.
- **Proposed.** Thread the cancel_event (as a cancel_check callable) into the in-process LLM stages so they can abort between units of work, not only at progress callbacks. PREPARE: hand SimulationManager.prepare_simulation a cancel_check consulted before dispatching each persona batch (and the parallel worker pool stops scheduling new personas once set). RUN: wait on cancel_event.wait(timeout=5) instead of bare time.sleep(5) so cancel returns sub-second. Reuses the existing _cancel_events mechanism — no new flag; degrades to current behavior if the callee ignores the optional cancel_check.
- **Why.** Cancellation is a first-class control-flow signal here (PipelineCancelled subclasses BaseException precisely so it pierces defensive except blocks), yet today cancelling during the two in-process LLM stages still spends real money/time until the next sparse callback. Making cancel promptly stop persona generation and tighten the run loop honors user intent and caps wasted spend.
- **Design.** RUN: replace time.sleep(5) with (cancel_ev.wait(5) if cancel_ev else time.sleep(5)); keep top-of-loop check. PREPARE: cancel_check = lambda: (ev:=PipelineOrchestrator._cancel_events.get(state.pipeline_id)) is not None and ev.is_set(); pass to prepare_simulation. In the persona pool: before submitting each task and at worker entry, if cancel_check and cancel_check(): stop scheduling and raise/propagate so _run sees PipelineCancelled. Keep _make_stage_updater check as backstop.
- **Impact.** Reduces worst-case post-cancel LLM spend during PREPARE from a full persona batch (up to 8 parallel personas x slow provider) to one in-flight unit, and cuts RUN cancel latency from up to ~5s+round to sub-second.
- **Depends on.** Existing per-pipeline _cancel_events Event; requires threading a cancel_check param through SimulationManager.prepare_simulation and its persona worker pool. RUN change is local to the poll loop.
- **Risk.** Low: cancel_check is optional and ignored by older callees (graceful degradation). Partially-generated persona artifacts must not later be mistaken for a complete PREPARE — cancel already marks the stage cancelled (no reuse); the manifest/validation proposal hardens this further.

#### [I-4-6] Live in-progress stage pointers and partial-artifact deep links (not only at stage completion)

`observability` · effort **M** · backend/app/services/pipeline_orchestrator.py (_make_stage_updater 1296-1326; helper _discover_partial_artifacts(stage)), backend/app/api/research.py (new GET /<id>/stage/<stage>; extend /status), backend/app/config.py (PIPELINE_LIVE_ARTIFACTS)

- **Today.** Artifacts are deep-linkable only after a stage fully completes: _record_stage_artifacts runs inside _complete_stage (pipeline_orchestrator.py:1338, 1343-1372) and GET /api/research/<id>/artifact/<name> (research.py:383-401) reads the artifacts map populated there. While a stage runs there is no pointer to in-flight partial output: report sections are written to disk section-by-section (section_NN.md) but unlinkable until REPORT completes; the research subprocess writes research_progress.log (exposed via /progress 404-420) but partial research_report.md / communities.json / run_summary.json are invisible until their stage finishes. Users watching a 40-minute deep run cannot inspect what has been produced so far, and a cancelled/failed run exposes nothing partial even when usable partials exist on disk.
- **Proposed.** Expose live, best-effort stage pointers: when a stage is running, surface a 'partial' view of whatever artifacts already exist on disk for that stage plus a per-stage descriptor (stage -> {status, artifact names available now, byte counts, last_modified}). Add GET /api/research/<id>/stage/<stage> returning current StageState + live-discovered partial artifacts, and have _make_stage_updater opportunistically register partial artifacts (existence+size) as they appear, not only at completion. Gate the extra disk scanning behind Config.PIPELINE_LIVE_ARTIFACTS (default True); when False, behavior is today's completion-only pointers.
- **Why.** Long, mostly-silent stages (deep research, multi-round simulation, multi-section report) are exactly where users need incremental evidence to trust the run and decide whether to cancel. Surfacing partials also means a cancelled/failed run yields salvageable, inspectable output instead of a black box — improving observability and the value recovered from partial failure.
- **Design.** _discover_partial_artifacts(state,stage): scan handoff_dir + report/sim dirs for known files of that stage, return [{name,path,bytes,mtime,provisional:True}]. New route returns {stage:asdict(StageState), partials:_discover_partial_artifacts(...)} with provisional set when stage.status=='running'. /artifact extended to allow reading provisional partials with a provisional flag. Updater: every Nth tick register newly-appeared partials into state.artifacts under provisional keys (e.g. 'report_partial') without overwriting completed entries.
- **Impact.** Turns the three long stages from opaque progress bars into inspectable, deep-linkable incremental output; makes early-cancel decisions evidence-based and partial results recoverable through the existing artifact endpoint.
- **Depends on.** Existing artifacts map, _record_stage_artifacts, /artifact endpoint, ReportManager section files, SimulationRunner run-state. Pairs with the manifest proposal (partials labeled unverified until completion).
- **Risk.** Low: live partials are explicitly best-effort and must be labeled provisional so the UI never treats a partial report as final. Directory stats are cheap and on-demand (endpoint) plus light registration in the updater.

#### [I-4-2] Mid-run simulation resume: continue OASIS from the last completed round instead of restarting at round 0

`robustness` · effort **L** · backend/app/services/pipeline_orchestrator.py (RUN 1611-1668), backend/app/services/simulation_runner.py (start_simulation 319+, _rotate_stale_action_logs 704-719, run-state load 249+, write_run_summary), backend/scripts/run_parallel_simulation.py (add --start-round), backend/app/config.py (SIM_RESUME_FROM_ROUND)

- **Today.** The RUN stage (pipeline_orchestrator.py:1611-1668) resumes only all-or-nothing: if STAGE_RUN is 'completed' it reuses, otherwise it calls SimulationRunner.start_simulation from scratch. SimulationRunner already persists fine-grained progress (run_state.json with current_round/total_rounds/twitter_current_round/reddit_current_round, simulation_runner.py:305-316) and _rotate_stale_action_logs (704-719) explicitly rotates actions.jsonl on re-run because resuming a failed run would otherwise mix old rounds into monitoring/reporting. So a deep simulation that fails at round 22/30 (after heavy per-round LLM spend) throws away all 22 rounds and reruns from round 1. There is no flag or code path to continue from the persisted round.
- **Proposed.** Add optional simulation continuation gated by Config.SIM_RESUME_FROM_ROUND (default False, opt-in since it depends on OASIS DB durability). When enabled and a prior run_state.json shows partial progress with an intact simulation DB, the RUN stage passes start_round=run_state.current_round to SimulationRunner.start_simulation, which skips already-simulated rounds and appends to (rather than rotates) actions.jsonl. When disabled or DB is gone, behavior is exactly today's full restart.
- **Why.** OASIS rounds are individually expensive (many agent LLM calls per round per platform); restarting a long social simulation from zero after a late transient failure is the biggest avoidable cost in the run stage. Continuation makes long-horizon, many-round forecasts (the high-fidelity ones) practical to recover under real-world flakiness, directly raising achievable simulation depth/scale.
- **Design.** Config.SIM_RESUME_FROM_ROUND bool default false. RUN: prior=SimulationRunner.get_run_state(sim_id); if flag and prior and 0<prior.current_round<prior.total_rounds and _sim_db_intact(sim_id): run_kwargs['start_round']=prior.current_round else full restart. start_simulation(start_round=0): if >0 skip rotate (append) and pass --start-round, set state.current_round=start_round. write_run_summary reads full appended log; dedup by (round,agent,action). Add _sim_db_intact() verifying the OASIS db exists and its last persisted round matches run_state.
- **Impact.** On a late-round failure, recovers completed_rounds/total_rounds of simulation compute — e.g. resume at 22/30 saves ~73% of agent LLM spend and wall time for the stage.
- **Depends on.** OASIS simulation DB must be durable across the failure. SimulationRunner.start_simulation must accept start_round and skip _rotate_stale_action_logs in append mode; backend/scripts/run_parallel_simulation.py must support seeking to --start-round.
- **Risk.** Medium: correctness depends on the OASIS DB being consistent at the failure boundary; a half-written round could corrupt continuation. Mitigate by validating run_state vs DB row counts before resuming, falling back to full restart on mismatch, deduping appended actions, and keeping the flag default-off.

### 7.8 Performance & cost optimization

#### [I-6-6] Pipeline-wide LLM observability: per-phase call counts, tokens, latency, cache hit-rate

`observability` · effort **S** · backend/app/utils/llm_metrics.py (new collector + contextvar phase tag + summary writer), backend/app/utils/llm_client.py (instrument chat/chat_json with timing + token estimate + cache outcome), backend/app/config.py (LLM_METRICS_ENABLED, LLM_METRICS_DIR), light phase-tag set-points in pipeline_orchestrator / report_agent / oasis_profile_generator

- **Today.** There is no aggregate LLM accounting. CLIModel builds a usage object per call (oasis_llm.py:88) but it is discarded. report_agent has rich structured logging of tool calls and responses (ReportLogger, report_agent.py:166-256) but nothing tallies LLM call count, estimated tokens, or wall-time by phase (research/graph/simulation/report). It is impossible to know which phase or which tool dominates cost/latency, which makes every optimization above un-measurable and tuning of the new Config knobs blind.
- **Proposed.** Add a lightweight, thread-safe metrics collector (utils/llm_metrics.py) that LLMClient.chat()/chat_json() increment on every call: count, estimated prompt+completion tokens (via the shared estimator), elapsed seconds, provider/model/tier, cache hit/miss, and a coarse phase tag set via a contextvar (e.g. 'report:section', 'graph:extract', 'profile', 'simulation', 'interview'). Emit a per-run summary to the pipeline data dir and log a one-line breakdown at phase boundaries. Gate emission behind a Config flag; collection is cheap and always-on-safe.
- **Why.** Every proposal here (cache, routing, parallelism, budgeting) needs evidence to tune and to prove it helps without hurting quality. Phase-tagged token/latency/cache metrics turn 'this feels faster' into measured deltas, let users right-size the Config knobs for their provider, and surface regressions (e.g. a model-routing change that triggers extra repair retries). It is the enabling instrumentation for the whole performance workstream.
- **Design.** metrics = LLMMetrics() singleton; phase = ContextVar('llm_phase', default='other'). In chat(): t0=time(); ...; metrics.record(phase.get(), provider, model, tier, est_prompt, est_completion, time()-t0, cache_hit). At phase boundaries call set_phase('report:section'). pipeline writes metrics.summary() to PIPELINE_DATA_DIR/<run>/llm_metrics.json: {by_phase:{tokens,calls,seconds,cache_hits}, total:{...}}.
- **Impact.** Makes cost/latency attributable per phase and per tool, enabling data-driven tuning of concurrency/cache/routing and immediate detection of pathological retry storms or runaway tool loops. Directly improves operability of long, expensive runs.
- **Depends on.** Uses the shared token estimator (token_budget proposal) and the cache layer's hit/miss signal (cache proposal). Contextvar phase tagging is stdlib (contextvars).
- **Risk.** Low. Token figures are estimates (char/4) — label them as such. Must be thread-safe (the parallel profile/section paths run under threads) — use a lock or per-thread accumulation merged at summary time. Negligible overhead; default emission off but collection harmless.

#### [I-6-0] Content-addressed LLM response cache (memoize identical chat()/chat_json() calls across the pipeline)

`performance` · effort **M** · backend/app/utils/llm_client.py (cache layer in chat/chat_json), backend/app/config.py (LLM_CACHE_ENABLED, LLM_CACHE_BACKEND=memory|disk, LLM_CACHE_DIR, LLM_CACHE_MAX_TEMP), backend/app/services/graphiti_client/llm_adapter.py (allow opt-in cache for extraction)

- **Today.** LLMClient.chat() (backend/app/utils/llm_client.py:79) and chat_json() (line 111) execute every request fresh with zero deduplication. The same effective prompt is re-issued repeatedly: plan_outline() and every section's ReAct loop call insight_forge() (zep_tools.py:988) which each fire _generate_sub_queries() (zep_tools.py:1135), and the graph is immutable during report generation, so identical sub-query-generation prompts recur. Graphiti extraction (graphiti_client/llm_adapter.py:103) explicitly constructs its own client with cache=False (line 62). There is no process-level or disk-level memoization anywhere.
- **Proposed.** Add an optional content-addressed cache inside LLMClient keyed by a stable hash of (provider, model, normalized messages, temperature, max_tokens, response_format). On hit, return the stored completion without spawning a CLI subprocess or making an HTTP call. Support two backends gated by Config: an in-process LRU (per-run, e.g. report generation) and an optional on-disk SQLite/JSON cache under uploads/ for cross-run reuse (e.g. re-running a report on the same graph). Only cache deterministic-intent calls (chat_json and temperature<=0.3 chat) by default to avoid freezing creative variety; expose a per-call use_cache override so the per-agent OASIS simulation path can opt out.
- **Why.** The pipeline is dominated by repeated near-identical retrieval/decomposition LLM calls on immutable inputs. Memoizing them removes pure-waste latency and token spend on CLI-subscription and metered providers alike, freeing budget for more sub-queries, more sections, or deeper interviews — i.e. higher forecast quality at equal cost. It also makes report re-generation (a common debugging/iteration loop) near-instant for the unchanged portions.
- **Design.** Key = sha256(json.dumps({provider, model, messages, temperature, max_tokens, rf}, sort_keys=True)). In chat(): if cache enabled and temperature<=max_temp and (use_cache!=False): k=_key(...); v=cache.get(k); if v: return v; ... compute ...; cache.set(k,result). Provide LLMCache protocol with MemoryLRU (OrderedDict, maxsize) and DiskCache (sqlite table key TEXT PRIMARY KEY, value TEXT, ts). Default LLM_CACHE_ENABLED=false to preserve current behavior exactly.
- **Impact.** Eliminates a large fraction of duplicate LLM calls during a single report (sub-query generation, planning sweeps, repeated tool-driven searches) and makes warm re-runs collapse to cache hits. On metered providers this is a direct token-cost cut; on CLI providers it removes subprocess-spawn latency (each claude/codex exec is seconds of fixed overhead).
- **Depends on.** None new; uses stdlib hashlib + functools/sqlite3. Must respect _clean_content normalization so cache keys are stable.
- **Risk.** Low-medium. Risk of stale hits if graph mutates mid-run (mitigate: include graph_id/version in key for retrieval-derived prompts, or keep cache memory-only and per-run by default). Caching creative/high-temp calls could reduce diversity — gated off by default via LLM_CACHE_MAX_TEMP. Disk cache needs size bounding (LRU eviction).

#### [I-6-1] Parallelize insight_forge sub-query searches and add a per-run retrieval cache in ZepToolsService

`performance` · effort **M** · backend/app/services/zep_tools.py (parallelize loop in insight_forge; add _search_cache/_forge_cache and lookups in search_graph/insight_forge; invalidation hook after interview writes), backend/app/config.py (REPORT_RETRIEVAL_PARALLEL, REPORT_RETRIEVAL_CACHE)

- **Today.** insight_forge() (zep_tools.py:1039) loops over sub_queries calling search_graph() strictly sequentially, then does one more main search (line 1055), then sequentially get_node_detail() for every entity uuid (line 1084). insight_forge is invoked once in plan_outline (report_agent.py:1370) AND inside every section's ReAct loop, against an immutable graph. ZepToolsService already caches whole-graph nodes/edges (_nodes_cache/_edges_cache, zep_tools.py:433-434) but does NOT cache search results or insight_forge outputs, so the same query re-runs the full fan-out every time. get_simulation_context is also re-fetched in planning.
- **Proposed.** (1) Run the sub-query + main searches concurrently with a ThreadPoolExecutor (the search path is I/O-bound against the local graph runtime and already thread-safe per the existing _search_zep_for_entity precedent). (2) Add a query-result cache (keyed by graph_id+normalized-query+limit+scope) and an insight_forge result cache (keyed by graph_id+query+report_context-hash) on the ZepToolsService instance, which persists across all sections since the agent holds one instance (report_agent.py:994). Gate both behind Config flags; invalidate the search caches when interview_agents writes new facts to the graph (T3.14 path).
- **Why.** Retrieval is the single most repeated unit of work in report generation and it currently runs serially and uncached on data that does not change between sections. Parallel fan-out shortens each insight_forge call; cross-section caching removes whole redundant fan-outs. Faster, cheaper retrieval lets the agent afford more tool calls per section (richer grounding) within the same time budget, directly improving forecast evidence density.
- **Design.** In insight_forge: with ThreadPoolExecutor(max_workers=cfg) as ex: futs={ex.submit(self.search_graph, graph_id, q, 15, 'edges'): q for q in sub_queries}; futs[ex.submit(...main...)]=query; gather. Caches: self._search_cache: Dict[tuple[str,str,int,str], SearchResult]; self._forge_cache: Dict[tuple[str,str], InsightForgeResult]. search_graph: key=(graph_id,query.strip().lower(),limit,scope); return copy on hit. Add self.invalidate_search_cache(graph_id) called post interview write.
- **Impact.** Sub-query fan-out wall-time drops toward the slowest single search instead of the sum; repeated insight_forge/search calls across the many sections become cache hits. Materially shortens the report phase, the longest tail of the pipeline.
- **Depends on.** Relies on graph search being concurrency-safe (already exercised by oasis_profile_generator parallel edge/node search). Cache invalidation must hook the interview_agents graph-write at zep_tools.py:1511.
- **Risk.** Medium. Concurrent searches increase peak load on the local graph runtime/embedder — bound the pool (e.g. 4) via config. Stale cache after interview writes is the main correctness risk; mitigated by explicit invalidation on write and per-run scope. Both default off to preserve current ordering/behavior.

#### [I-6-4] Token budgeting and adaptive context truncation with a shared token estimator

`robustness` · effort **M** · backend/app/utils/token_budget.py (new: estimate_tokens, budget_for(provider,model), fit_to_budget(parts, budget)), backend/app/config.py (PROVIDER context-window map, ADAPTIVE_CONTEXT flag, RESERVED_COMPLETION_TOKENS), call sites in report_agent.py and oasis_profile_generator.py replacing hardcoded slices

- **Today.** Context is truncated with hardcoded character slices scattered across the code: persona context[:3000] (oasis_profile_generator.py:727,776), previous sections [:8000] each with no cap on count (report_agent.py:1643), related_facts[:25] (report_agent.py:1361), forge_text[:3000] / outcomes[:2500] / diff[:2500] (report_agent.py:1378-1395), situation_brief seg[:600] (oasis_profile_generator.py:558). max_tokens is fixed per call (4096/8192). There is a token estimator but it lives only inside CLIModel._estimate_tokens (oasis_llm.py:57) and is not reused. Nothing adapts truncation to the model's actual context window, so large-window models (MiniMax 512K, DeepSeek 1M) are starved while small-window calls can still overflow.
- **Proposed.** Extract a shared token-estimation/budgeting utility (utils/token_budget.py) and use it to (a) compute a per-call token budget from the provider's context window (Config per-provider context sizes) minus a reserved completion allowance, and (b) replace ad-hoc char slices with budget-aware truncation that fills available context (more facts/longer prior-section context on big-window models, tighter on small ones). Gate behind a Config flag; when disabled, fall back to the existing hardcoded slices exactly.
- **Why.** Current fixed char limits simultaneously waste capacity on large-context models (lower grounding -> weaker forecasts) and risk silent truncation/JSON breakage on small ones. A single budgeting layer makes context usage adaptive and provider-aware, raising evidence density where the model can absorb it and preventing the truncation-induced JSON failures that already required extensive repair code (llm_client.py:227, oasis_profile_generator.py:622).
- **Design.** token_budget.estimate_tokens(str)->ceil(len/4) (shared with CLIModel). budget_for(provider): WINDOWS={'minimax':512000,'deepseek':1000000,'kimi':256000,'openai':128000,...}.get(provider, 32000); usable = window - RESERVED_COMPLETION_TOKENS - prompt_overhead. fit_to_budget(items, budget): greedily include items until budget exhausted, hard-cap each item. report_agent previous_content: instead of [:8000] per section unbounded, fit prior sections into usable budget; persona context uses fit_to_budget(facts+relations, persona_budget).
- **Impact.** Higher-quality grounding on large-window providers (more facts/relationships threaded into personas and sections) and fewer truncation-driven failures on small-window ones. Reduces wasted tokens from over-generous fixed slices on small models.
- **Depends on.** Reuse the existing estimator logic from oasis_llm.CLIModel._estimate_tokens (move it into the shared util and have CLIModel import it). Pairs with model routing (different tiers have different windows).
- **Risk.** Low-medium. Estimator is approximate (char/4) so keep a safety margin in RESERVED_COMPLETION_TOKENS. Behavior change is opt-in; defaults reproduce current char slices. Edge case: extremely long single facts must still be hard-capped to avoid one item consuming the whole budget.

#### [I-6-5] Reuse cached graph nodes/edges for local search instead of re-fetching per query

`performance` · effort **M** · backend/app/services/oasis_profile_generator.py (_search_zep_for_entity: optional cached-bulk path), backend/app/services/zep_tools.py (or a shared retrieval helper) for the cached node/edge match scoring, backend/app/config.py (PROFILE_ENRICH_FROM_CACHE flag)

- **Today.** ZepToolsService caches full node/edge lists (_nodes_cache get_all_nodes zep_tools.py:663; _edges_cache get_all_edges zep_tools.py:703). But the primary search path search_graph() (zep_tools.py:477) calls self.client.graph.search() which, for the local FalkorDB/Graphiti runtime, runs an embedding + rerank round-trip per query (runtime.py:412), and _local_search (zep_tools.py:559) is only a fallback. oasis_profile_generator._search_zep_for_entity (line 295) issues 2 fresh graph searches PER ENTITY across up to OASIS_MAX_AGENTS (80) profiles with no cross-entity cache — re-embedding and re-searching the same immutable graph dozens of times.
- **Proposed.** (1) For the profile-generation phase, add an optional shared retrieval cache keyed by (graph_id, query) so the many per-entity hybrid searches reuse results when queries collide, and/or precompute a single bulk pull of nodes+edges (already cached) and serve per-entity context from the in-memory cache via the existing match-scoring in _local_search when the query is the formulaic 'all info about <entity>' pattern. (2) Expose a flag to use the cached-bulk path for profile enrichment instead of N independent embedding searches. Default off -> current per-entity search behavior preserved.
- **Why.** Profile enrichment currently pays an embedding+search cost per entity against a graph that is fully cacheable in memory; for 80 agents that is ~160 redundant search round-trips. Serving enrichment from one cached node/edge pull (with local match scoring already implemented) collapses this to a single fetch, dramatically cutting the profile-generation phase cost while keeping the same context richness.
- **Design.** Add ProfileGenerator option enrich_from_cache: if set, fetch nodes/edges once (cached), and for each entity build context by filtering cached edges/nodes where entity.name appears (reusing _local_search-style match_score) instead of two client.graph.search calls. Otherwise keep current parallel edge/node search but consult a shared {(graph_id,query): result} dict first.
- **Impact.** Profile-generation retrieval drops from O(agents) graph searches to O(1) bulk fetch plus in-memory scoring, removing a large block of latency before simulation can start. Especially impactful as OASIS_MAX_AGENTS scales up.
- **Depends on.** Reuses existing _nodes_cache/_edges_cache and _local_search match_score (zep_tools.py:590). Profile generator currently uses its own zep_client directly (oasis_profile_generator.py:210) — would route enrichment through a shared cached service or replicate the cache there.
- **Risk.** Low-medium. In-memory match scoring is less semantically precise than embedding search, so keep the embedding path as the default and the cached path as an opt-in speed mode; or use the cache only to skip exact-duplicate queries. Memory footprint of caching all nodes/edges for very large graphs should be bounded.

#### [I-6-2] Two-tier model routing: cheap model for retrieval/decomposition/extraction, strong model for synthesis

`capability` · effort **L** · backend/app/config.py (LLM_FAST_MODEL, LLM_STRONG_MODEL, LLM_FAST_PROVIDER optional, routing flag LLM_TIERED_ROUTING), backend/app/utils/llm_client.py (tier param -> model selection; optionally lazily build a second OpenAI client for a different fast provider), backend/app/services/zep_tools.py (tier='fast' on _generate_sub_queries/_select_agents_for_interview/_generate_interview_questions), backend/app/services/graphiti_client/llm_adapter.py (map ModelSize.small/medium -> tier)

- **Today.** LLMClient binds exactly one model (self.model, llm_client.py:55) for every call regardless of task difficulty. The same model writes 2000-word personas, decomposes sub-queries (zep_tools.py:1135), selects interviewees (zep_tools.py:1612), extracts graph entities (llm_adapter.py), AND writes final report sections. config.py only has an unused-in-CLI 'boost' concept in oasis_llm._create_openai_model (oasis_llm.py:156) for the OpenAI path. There is no notion of a cheap/fast tier vs a strong/synthesis tier, and graphiti's ModelSize.small/medium signal (llm_adapter.py:71) is ignored.
- **Proposed.** Introduce an optional model-routing layer: define a 'fast' model alias and a 'strong' model alias per provider in Config (e.g. LLM_FAST_MODEL / LLM_STRONG_MODEL, plus the CLI analog such as a lighter claude/codex model or a cheaper OpenAI-compat model). Add LLMClient.chat(..., tier='fast'|'strong') (default 'strong' = current model, so behavior is unchanged). Route low-stakes structured calls to 'fast': sub-query generation, interviewee selection, interview-question generation, JSON repair retries, graph entity/edge extraction (honor graphiti ModelSize). Keep persona generation, report planning, and section synthesis on 'strong'. For CLI providers where only one subscription model exists, tier is a no-op (graceful degradation).
- **Why.** A large share of calls are mechanical (decompose, select, format) and do not need the flagship model, while a few calls (synthesis, personas) define output quality. Routing cheap work to a cheap model cuts cost and latency on the bulk of calls and lets the saved budget be spent on the flagship model for the calls that actually move forecast quality — a strictly better quality/cost frontier. It also unblocks using larger, more accurate extraction models only where they matter.
- **Design.** Config: FAST=os.environ.get('LLM_FAST_MODEL') or LLM_MODEL_NAME; STRONG=os.environ.get('LLM_STRONG_MODEL') or LLM_MODEL_NAME. LLMClient.chat(messages,...,tier='strong'): model = self._model_for_tier(tier); for OpenAI path pass model=model; for CLI path ignore (single subscription model). _model_for_tier returns self.model unless tiered routing enabled. llm_adapter: tier='fast' if model_size in (small,medium) else 'strong'.
- **Impact.** Shifts the majority of call volume (extraction during graph build, sub-query/selection/repair) to a cheaper, faster tier, cutting token cost and latency on metered providers while concentrating spend on synthesis. Enables a 'mixed-provider' setup (cheap local extraction, strong synthesis) without code changes per call site.
- **Depends on.** Builds naturally on top of the response-cache proposal. For OpenAI-compat fast tier with a different model only, just swap model per request; for a different fast provider entirely, instantiate a second client.
- **Risk.** Medium. A weaker model on extraction/sub-queries can lower retrieval recall or extraction accuracy — must keep strong tier as default and let users opt in; keep the rising-temperature/JSON-repair guards which already protect against weaker-model schema echoes (llm_adapter.py:98). Misconfiguration (fast model unset) must fall back to strong model, not error.

#### [I-6-3] Concurrent report section generation with dependency-aware ordering

`performance` · effort **L** · backend/app/services/report_agent.py (concurrent branch in generate_report around the section loop; build synthesis-brief; mark summary/conclusion sections last), backend/app/config.py (REPORT_SECTION_CONCURRENCY, REPORT_SECTION_CONTEXT_MODE=full|brief)

- **Today.** generate_report() generates sections strictly one at a time in a Python for-loop (report_agent.py:2058-2133). Each section's _generate_section_react runs up to 10 LLM iterations plus tool calls (report_agent.py:1675), and every section passes all previously generated sections as context (previous_content, line 1639) — creating a hard serial dependency that grows the prompt for every subsequent section. With N sections this is the dominant latency of the report phase and the prompt cost grows quadratically with section count.
- **Proposed.** Add an optional concurrent generation mode that fans out independent sections into a thread pool (bounded by a Config concurrency cap), while preserving narrative coherence by passing each section the report outline + a compact running 'synthesis brief' (titles + 1-2 line summaries of other sections) instead of the full text of every prior section. Sections that genuinely depend on a prior one (e.g. an executive-summary or conclusion section) can be marked sequential/last via a simple heuristic on title/description so they run after the body completes. Default off (concurrency=1) -> byte-identical to today.
- **Why.** Section generation is embarrassingly parallel once you replace the implicit 'all prior full text' dependency with a shared compact brief. This both removes the serial bottleneck AND fixes the quadratic context-growth problem (each section currently re-reads up to 8000 chars x number-of-prior-sections), which is itself a token-cost and quality regression (later sections drown in earlier text). A shared brief gives better cross-section consistency than truncated dumps.
- **Design.** If Config.REPORT_SECTION_CONCURRENCY>1: split outline.sections into body[] and tail[] (titles matching summary/conclusion/总结/结论). Build brief = [(s.title, s.description) for s in outline.sections]. with ThreadPoolExecutor(max_workers=cap): futures = {ex.submit(self._generate_section, sec, outline, brief_context, ...): i for i,sec in enumerate(body)}; collect into ordered slots; then run tail sequentially with full body text. report_logger/caches guarded by threading.Lock.
- **Impact.** Report phase wall-time drops from ~sum of sections toward ~max-section-time at the chosen concurrency; per-section input tokens stop growing with position, cutting total report tokens substantially on multi-section reports. Frees latency budget for more tool calls per section.
- **Depends on.** Strongly complements the LLM response cache and the ZepTools retrieval cache (shared across threads — needs the caches to be thread-safe, i.e. guarded with a lock). Per-section file saves already isolated by section index. CLI provider concurrency is bounded by the OASIS semaphore precedent.
- **Risk.** Medium-high. Coherence risk if the synthesis brief is too thin — mitigate by always passing the full outline + summaries and keeping summary/conclusion sequential. Thread-safety of shared ZepToolsService caches and report_logger must be ensured (add locks). Concurrency must be bounded to avoid overwhelming CLI subprocess limits. Default off preserves current behavior.

### 7.9 Simulation fidelity & scale

#### [I-2-2] Silent-majority audience population to scale the cast beyond the named research actors

`capability` · effort **M** · backend/app/services/oasis_profile_generator.py (new generate_audience_profiles using _generate_profile_rule_based, distinct user_id range, source_entity_type='Audience'); backend/app/services/simulation_config_generator.py (new _generate_audience_agent_configs appended to all_agent_configs); backend/app/config.py (SIM_AUDIENCE_SIZE, SIM_AUDIENCE_ACTIVE_CAP); backend/app/services/simulation_manager.py (invoke audience generation after named cast).

- **Today.** The cast is exactly the knowledge-graph entities, capped at OASIS_MAX_AGENTS=80 (config.py:358) via the T3.13 ranking in simulation_manager.py:293-320 (keeps matched research actors + top influence/edge-count). Every agent is a named, individually LLM-personated entity (one LLMClient call + up to two Zep searches each in oasis_profile_generator). So the simulated 'public' is ~80 elites/orgs/media with zero ordinary-public mass. Real opinion dynamics are driven by a large silent majority of low-influence lurkers who mostly like/repost/lurk and occasionally tip. Today no such population exists, so PlatformConfig.viral_threshold and cascade dynamics never have a crowd to cascade through, and 'public sentiment' forecasts rest on a tiny elite sample.
- **Proposed.** Add an optional procedurally-generated audience cohort: M synthetic low-influence persona agents (stance/demographically sampled to match the situation, not individually researched) appended after the named cast. Generated cheaply WITHOUT per-agent LLM calls — sample stance from the research stance distribution, demographics from existing COUNTRIES/MBTI pools, and a templated persona via the existing LLM-free OasisProfileGenerator._generate_profile_rule_based. They get low influence_weight, high lurk bias (mostly LIKE/REPOST/DO_NOTHING), and realistic activity. Gate behind `SIM_AUDIENCE_SIZE` (default 0 → exactly current behavior).
- **Why.** Scale + realism: lets the simulation model the crowd that actually carries virality and shifts aggregate sentiment, without the cost explosion of LLM-personating every one. With a silent majority present, viral_threshold and the recsys exposure knobs (SIM_WIRE_RECSYS) finally have a population to propagate through, and the stance-trajectory/polarization metrics become representative of 'the public' rather than 'the 80 loudest entities.' It also cheaply stress-tests scale (300-1000 agents) since audience profiles skip the expensive generation path.
- **Design.** Config SIM_AUDIENCE_SIZE=int(...0). In generate_config, after all_agent_configs built, if size>0: stance_dist = count actors per stance (or uniform); for k in range(size): build AgentActivityConfig(agent_id=offset+k, entity_name=f'公众_{k}', entity_type='Audience', activity_level~U(0.2,0.6), influence_weight~U(0.3,0.8), stance=sampled, interested_topics from hot_topics). generate_audience_profiles returns OasisAgentProfile list via rule-based path (no LLM/Zep). Append to profiles+configs; contiguous user_id keeps OASIS agent_graph consistent. Audience naturally joins _build_echo_chamber_follows clusters.
- **Impact.** Enables realistic crowd dynamics and population-level sentiment forecasts; raises believable scale ~5-10x at marginal generation cost. Makes virality and recsys knobs actually meaningful.
- **Depends on.** Builds on existing LLM-free _generate_profile_rule_based (oasis_profile_generator.py:813) and _generate_agent_config_by_rule (simulation_config_generator.py:1226). Needs stance distribution from actors.json (actors_digest exposes stances) or uniform fallback. Audience agents excluded from research-actor follow seeding but included in echo-chamber clustering (T3.4 _build_echo_chamber_follows already keys on stance+topic). Run-time LLM cost scales with active audience count — pair with activity throttling so only a fraction activate per round (get_active_agents_for_round already samples).
- **Risk.** Medium. Run-time LLM cost grows with active audience count — mitigated by low activity_level defaults, the weighted activation sampler, and an audience-specific per-round activation cap. Profile generation cost is negligible (rule-based). Risk of audience drowning out named actors — mitigated by influence-weighted activation already favoring high-influence named cast. Fully gated (size 0 default).

#### [I-2-3] Persona-conditioned action affordances (per-role available action sets)

`capability` · effort **M** · backend/scripts/run_parallel_simulation.py (ROLE_ACTION_POLICY table; build per-role action lists or persona constraint lines; thread into agent_graph construction at 1297/1519); backend/app/services/oasis_profile_generator.py (optional: append role behavioral-repertoire line to persona from the same policy); backend/app/config.py (SIM_ROLE_ACTION_PROFILES).

- **Today.** Every agent on a platform shares one global action list: TWITTER_ACTIONS / REDDIT_ACTIONS (run_parallel_simulation.py:182-209), passed once to generate_twitter_agent_graph / generate_reddit_agent_graph (lines 1297-1301, 1519-1523). A government-agency account, a state media outlet, a student, and an anonymous troll all have identical affordances (all can REPOST, QUOTE, FOLLOW, MUTE, etc.). In reality affordances and behavioral repertoire are role-dependent: official accounts rarely LIKE/REPOST individuals or MUTE; media heavily QUOTE/CREATE_POST/TREND; activists FOLLOW/REPOST aggressively; lurkers mostly LIKE/DO_NOTHING. The persona text hints at this but nothing constrains the action space, so the LLM frequently picks out-of-character actions, diluting fidelity.
- **Proposed.** Condition the action set on entity_type/role so each agent (or role bucket) gets a tailored available_actions list, plus optional per-role action-weight hints injected into the persona. Add a role→actions policy table (GovernmentAgency: CREATE_POST/DO_NOTHING/SEARCH; MediaOutlet: +QUOTE/REPOST/TREND; Student/Audience: full social set incl LIKE/REPOST/COMMENT; activist: +aggressive set). If camel-oasis supports per-agent available_actions, build distinct agent subgroups; otherwise keep the union as available_actions but inject a strong 'you typically only do X/Y' constraint line into each persona from this policy. Gate behind `SIM_ROLE_ACTION_PROFILES` (default false → single global list, current behavior).
- **Why.** Cheaply raises in-character fidelity and makes the per-role action mix realistic, which feeds directly into the emergent metrics (cross-stance interaction ratio, who originates vs amplifies). It curbs the common failure where official/institutional agents behave like chatty individuals, and reduces wasted LLM actions on implausible choices, improving signal density per round.
- **Design.** ROLE_ACTION_POLICY = {'governmentagency': [CREATE_POST, DO_NOTHING, SEARCH_POSTS], 'mediaoutlet': [CREATE_POST, QUOTE_POST, REPOST, TREND, CREATE_COMMENT, DO_NOTHING], 'student': TWITTER_ACTIONS, 'audience': [LIKE_POST, REPOST, DO_NOTHING, CREATE_COMMENT], ...}. If SIM_ROLE_ACTION_PROFILES and lib supports it: group agents by role, set agent.available_actions post-build. Else: for each profile append to persona '【行为习惯】你几乎只会：发帖/评论/沉默；很少点赞或转发个人。' from the policy so the LLM self-limits. Default flag off → existing single-list path untouched.
- **Impact.** More believable role behavior and a cleaner, more interpretable action mix; a modest but broad fidelity gain across every round of every run.
- **Depends on.** Needs confirmation of camel-oasis support for heterogeneous per-agent available_actions in one agent_graph; the comment at run_parallel_simulation.py:178-192 shows the team already reasons about which ActionTypes have platform-agnostic handlers, so the action-availability surface is understood. If per-agent action sets aren't supported without forking, fall back to persona-injected behavioral constraints (no library change). Reuses entity_type already on every AgentActivityConfig.
- **Risk.** Low-Medium. If the library lacks per-agent action sets, the prompt-constraint fallback is lossy (LLM may stray) but harmless. Over-restricting could make some roles inert — mitigate by always keeping CREATE_POST/COMMENT/DO_NOTHING for everyone and only varying the amplification/moderation verbs. Fully gated.

#### [I-2-4] Multi-seed ensemble runs with forecast confidence intervals

`robustness` · effort **M** · backend/scripts/run_parallel_simulation.py (accept --seed, call random.seed); backend/app/services/simulation_manager.py / pipeline_orchestrator.py (orchestrate K subprocess runs, collect K run_summary.json); backend/app/services/simulation_runner.py (new aggregate_ensemble → mean/CI/modal-outcome, write ensemble_summary.json); backend/app/config.py (SIM_ENSEMBLE_RUNS).

- **Today.** A simulation is a single stochastic trajectory. Activation (get_active_agents_for_round, run_parallel_simulation.py:1021) uses `random.random()` with no seeding; weighted sampling (_weighted_sample_without_replacement:1007) is random; LLM actions are sampled at temperature. The pipeline runs each platform exactly once and writes one run_summary.json. There is no notion of variance: a forecast like 'support collapses' could be a coin-flip artifact of one seed. The scenario-fork machinery (PipelineManager.fork:1135) compares overlays but each arm is still a single draw, so apparent overlay effects can be pure noise. No RNG seed is recorded anywhere, so runs aren't reproducible.
- **Proposed.** Add optional ensemble execution: run the same config K times with distinct, recorded RNG seeds (random.seed per replicate, threaded into activation/sampling), then aggregate the emergent metrics across replicates into mean ± spread (final stance % with CI, polarization-index distribution, modal outcome and its frequency, fracture rate). Persist seeds in run_summary for reproducibility. Expose `SIM_ENSEMBLE_RUNS` (default 1 → exactly current single run). The report agent then reports central tendency + uncertainty instead of a single trajectory.
- **Why.** Forecast credibility requires uncertainty quantification. One trajectory through a stochastic, LLM-driven multi-agent system is anecdote, not forecast. An ensemble turns the output into 'support 38% ± 9%, fractures in 7/10 runs' — far more defensible and the proper statistical basis for the scenario-fork comparisons the system already supports (you can test whether an overlay's effect exceeds the noise band). Recording seeds also makes runs reproducible for debugging, which they currently are not.
- **Design.** Config SIM_ENSEMBLE_RUNS=int(...1). Orchestrator: for k in range(K) launch run_parallel_simulation with --seed (base+k) into per-replicate subdir; collect run_summary['emergent']. aggregate_ensemble: stack stance_trajectory[-1].by_stance across replicates → {supportive:{mean,lo,hi}}; polarization_index → {mean,std}; fracture_rate = share of replicates with >1 dominant community; modal_outcome = most common final-majority stance + frequency. Write ensemble_summary.json next to run_summary; report stage add_if('ensemble_summary',...). K=1 → skip aggregation, behavior identical to today.
- **Impact.** Converts point forecasts into distributions with confidence intervals; makes scenario comparisons statistically meaningful and runs reproducible. Large credibility/robustness gain.
- **Depends on.** Builds on the emergent-metrics layer (improvement #1) since aggregation needs structured numeric metrics to average. Requires threading a seed through random calls in run_parallel_simulation (random.seed(seed) at run start covers activation + weighted sampling, both backed by stdlib `random`). Cost is K× LLM/runtime — pair with existing --max-rounds truncation and OASIS_MAX_AGENTS cap to keep ensembles affordable; default K=1.
- **Risk.** Medium. Linear K× cost in time and LLM spend — strictly opt-in via SIM_ENSEMBLE_RUNS; recommend small K (3-5) with reduced rounds. LLM nondeterminism means seeding `random` controls activation/sampling but not model sampling, so replicates still diverge (desirable for spread); document that seeds make activation reproducible, not the LLM. Aggregation must tolerate replicates that failed mid-run (reuse existing per-platform exception isolation).

#### [I-2-0] Emergent-structure & opinion-dynamics metrics layer (stance trajectory, polarization, follow-graph communities) wired into run_summary.json

`observability` · effort **L** · backend/app/services/simulation_runner.py (extend write_run_summary; new _compute_emergent_metrics + _detect_follow_communities + _score_stance_trajectory); backend/app/config.py (SIM_EMERGENT_METRICS); backend/app/services/pipeline_orchestrator.py (~1678 pass metrics through, already calls write_run_summary); backend/pyproject.toml (networkx).

- **Today.** Post-simulation analysis is shallow. `SimulationRunner.write_run_summary` (backend/app/services/simulation_runner.py:1131-1205) aggregates only per-agent engagement (`get_agent_stats`), per-round action volume (`get_timeline`), and a length-sorted `top_posts` list. `communities` is the only structural field and it is passed in from the BUILD-time Leiden run on the *knowledge graph* (GRAPH_BUILD_COMMUNITIES), NOT computed on the social follow graph the simulation actually grows. There is no opinion/stance distribution, no polarization index, no per-round sentiment trajectory, no measurement of the follow graph that `inject_initial_follows` + agents' FOLLOW actions build in the sqlite `follow` table. The report agent reads run_summary.json (pipeline_orchestrator.py:1372 `add_if('run_summary', ...)`), so whatever is missing here is invisible to the forecast.
- **Proposed.** Add an optional emergent-metrics computation pass at simulation end (alongside write_run_summary) that enriches run_summary.json with: (1) opinion/stance trajectory — per-round tally of agents by stance bucket (supportive/opposing/neutral/observer from agent_configs) weighted by their CREATE_POST/COMMENT volume, plus an LLM-or-lexicon sentiment score per post; (2) a polarization index (bimodality/variance of the per-agent net-sentiment distribution, and cross-stance vs within-stance interaction ratio computed from the typed action edges already mapped in zep_graph_memory_updater._INTERACTION_EDGES); (3) follow-graph community detection on the actual `follow` table from {platform}_simulation.db (networkx greedy-modularity or label-propagation) with per-community dominant stance and bridge agents; (4) cascade/virality stats per top post (reply+repost+quote depth). Gate behind `SIM_EMERGENT_METRICS` (default false to preserve exact current run_summary).
- **Why.** This is the single highest-leverage forecast-quality lever in the subsystem. The pipeline exists to forecast how opinion evolves, yet today the only signal handed to the report agent is 'who posted the most and what.' A quantified stance trajectory and polarization index turn the simulation from an anecdote generator into a measurement instrument: the report can say 'support eroded from 60% to 35% over 40 rounds and the network fractured into two non-communicating clusters' instead of paraphrasing a few posts. It also makes the what-if scenario forks (PipelineManager.fork, pipeline_orchestrator.py:1135) comparable by diffing polarization indices across overlays.
- **Design.** Config: `SIM_EMERGENT_METRICS = os.environ.get('SIM_EMERGENT_METRICS','false')=='true'`. In write_run_summary, if flag set: `emergent = cls._compute_emergent_metrics(simulation_id, agent_configs)`. _compute_emergent_metrics opens both *_simulation.db, builds a DiGraph from `follow(follower_id,followee_id)`, runs `networkx.algorithms.community.greedy_modularity_communities`, maps user_id→agent_id→stance via agent_configs, returns `{'communities':[{members,dominant_stance,size,bridge_agents}], 'stance_trajectory':[{round,by_stance:{supportive:n,...},net_sentiment}], 'polarization_index':float, 'cross_stance_interaction_ratio':float, 'cascades':[{post_id,depth,breadth}]}`. summary['emergent']=emergent. Stance trajectory groups actions by round_num (already in actions.jsonl), weights by agent stance, optional per-post sentiment via batched LLMClient.chat or CN/EN polarity lexicon.
- **Impact.** Transforms simulation output from qualitative post samples into quantitative, comparable forecast signals (stance %, polarization, fracture). Directly raises forecast quality and makes scenario A/B comparison meaningful.
- **Depends on.** networkx (add to backend/pyproject.toml; optional import with graceful skip). Reads {platform}_simulation.db follow/post/comment tables already populated. Reuses zep_graph_memory_updater._INTERACTION_EDGES for cross-stance interaction ratio. Sentiment: reuse LLMClient (batched) or a lightweight CN/EN lexicon fallback so it works offline.
- **Risk.** Medium. Community detection / sentiment scoring add end-of-run cost and a dependency; mitigated by SIM_EMERGENT_METRICS gate (default off), networkx optional-import skip, and lexicon fallback when LLM sentiment is too costly. All computation is read-only over the db + jsonl, so failure cannot corrupt a completed simulation (wrap in try/except like existing write_run_summary).

#### [I-2-1] Dynamic per-agent affective state (mood / fatigue / opinion-drift) threaded into each round's prompt

`capability` · effort **L** · backend/scripts/run_parallel_simulation.py (new AgentDynamicsTracker; integrate in both run_*_simulation round loops near LLMAction construction at lines 1434/1664; reuse _enrich_action_context); backend/app/config.py (SIM_AGENT_DYNAMICS + drift-rate constants); backend/app/services/simulation_config_generator.py (optional initial mood/opinion_strength fields on AgentActivityConfig).

- **Today.** Agents are statically prompted every round. OASIS re-uses the immutable `persona`/`user_char` string (built in oasis_profile_generator.py to_twitter_format / _save_reddit_json) plus the platform's recsys feed; there is no per-round mutable agent state. The only between-round dynamic is which agents get activated (`get_active_agents_for_round`, run_parallel_simulation.py:1021) plus the 1.5x recency multiplier. AgentActivityConfig (simulation_config_generator.py:58-90) has sentiment_bias and stance as fixed scalars that never change. So an agent that gets ratio'd, dunked on, or sees allies pile on never updates its emotional state or opinion — every round it answers as the same calm day-zero persona. This caps the realism of multi-round dynamics (no radicalization, no capitulation, no outrage fatigue).
- **Proposed.** Maintain a lightweight mutable affective-state vector per agent across rounds and inject a compact one-line 'current state' into that agent's prompt for the round. State = {mood: -1..1, energy: 0..1, opinion_strength: 0..1, fatigue: 0..1}. Update each round from observable signals: (a) interactions received last round mentioning/replying/liking/disliking this agent (pulled via the same db enrichment already in run_parallel_simulation._enrich_action_context), (b) whether same-stance vs opposite-stance agents dominated their feed, (c) cumulative activity → fatigue. Drift sentiment_bias toward the prevailing direction of received interactions (homophily reinforcement / backlash). Gate behind `SIM_AGENT_DYNAMICS` (default false → exactly today's static behavior).
- **Why.** Multi-round social dynamics are the product's reason to exist; without intra-agent state evolution the simulation is effectively N independent one-shot polls repeated T times. Affective drift produces the emergent phenomena forecasters care about — escalation spirals, bandwagon/cascade, outrage fatigue plateaus, opinion hardening in echo chambers — which then surface in the stance-trajectory metric above. It compounds with the existing echo-chamber follow seeding (T3.4) to produce realistic polarization instead of static factions.
- **Design.** class AgentDynamicsTracker: state: Dict[int, dict]; init from agent_configs (mood=sentiment_bias, opinion_strength from influence, fatigue=0). After each env.step, observe(actual_actions, db_cursor): per agent count received likes/dislikes/replies and authors' stances; mood += lr*net_received_valence (clamped); fatigue += k*acted - decay; opinion_strength += lr*(same_stance_share - 0.5). Before `actions = {agent: LLMAction()}`, render_state_line(agent_id) -> '【你当前状态】情绪:激动；立场已强化；略疲惫；刚因X被大量反对' passed as LLMAction(extra_context=...) or a ManualAction note. Gated; absent flag → unchanged LLMAction().
- **Impact.** Qualitatively richer multi-round behavior (escalation, capitulation, fatigue) and more realistic opinion-trajectory shapes, materially improving the fidelity and credibility of forecasts about how a situation evolves.
- **Depends on.** Requires per-round read of received interactions (computable from db via existing _get_post_info/_enrich_action_context helpers). Needs a small state store keyed by agent_id held across the round loop in run_twitter_simulation/run_reddit_simulation. Works best with LLMAction prompt-prefixing — verify camel-oasis LLMAction/agent supports an extra per-step context string; if not, fall back to ManualAction-driven state notes or skip cleanly.
- **Risk.** Medium-High. Main risk is whether camel-oasis exposes a clean hook to prepend per-round dynamic context to an agent's system prompt without forking the library; needs a spike. Update rules can over/under-drift — mitigate with bounded clamps and conservative learning-rate constants, and keep the whole feature behind SIM_AGENT_DYNAMICS so default runs are byte-identical.

### 7.10 Testing, CI & quality gates

#### [I-7-6] ruff + targeted mypy gate with project lint config (currently none at repo root)

`devex` · effort **S** · backend/pyproject.toml (add [tool.ruff], [tool.ruff.lint.per-file-ignores], [tool.mypy]), package.json (scripts.lint), .github/workflows/ci.yml (lint-type job)

- **Today.** There is no ruff/flake8/black config and no mypy config anywhere in the app (the only ruff.toml found belongs to the vendored deer-flow-2.0-m1-rc3 tree, not our backend). pyproject.toml has no [tool.ruff] or [tool.mypy]. Code uses inline `# noqa: E402` (test scripts) and `# noqa: BLE001` (report_agent.py:1463), implying linting was anticipated but never wired. ~14k lines of service code (report_agent.py alone is 3019 lines) ship with no static gate, so import errors, unused names, and obvious type mismatches only surface at runtime mid-pipeline.
- **Proposed.** Add [tool.ruff] to backend/pyproject.toml with a pragmatic ruleset (E,F,I,B,UP, plus per-file-ignores honoring the existing E402/BLE001 patterns) and `ruff format`. Add [tool.mypy] in non-strict mode scoped to the most contract-critical, dependency-light modules first — app/utils/actors.py, app/utils/dates.py, app/services/ontology_generator.py, app/config.py — with `ignore_missing_imports` for the heavy ML/graph deps. Wire both into the CI lint-type job and expose `npm run lint`.
- **Why.** Static gates are the cheapest defense for a large, multi-provider, JSON-heavy codebase: ruff catches unused imports, undefined names, and bug-prone patterns (B-series) before they break a forecast run; isort/format keeps diffs reviewable. Typing the pure contract modules (actors threading, ontology shapes, config) prevents the exact key/shape drift the golden tests assert at runtime, but earlier and across all call sites.
- **Design.** [tool.ruff] line-length=120, target-version='py311'; [tool.ruff.lint] select=['E','F','I','B','UP']; per-file-ignores={'scripts/*'=['E402'],'app/services/report_agent.py'=['BLE001']}. [tool.mypy] python_version='3.11', ignore_missing_imports=true, files=['app/utils/actors.py','app/utils/dates.py','app/services/ontology_generator.py','app/config.py']. CI: `uv run ruff check . && uv run ruff format --check . && uv run mypy`.
- **Impact.** Eliminates a class of runtime-only failures (NameError/ImportError mid-pipeline), enforces consistent formatting for cleaner PR review, and gives the CI lint-type job real teeth. Incremental mypy scope avoids a boil-the-ocean migration.
- **Depends on.** Feeds the CI proposal's lint-type job. Independent otherwise.
- **Risk.** Low. Initial ruff run may flag existing nits; start with a conservative select-set and `--add-noqa` baseline, expand rules over time. mypy scoped to a handful of files avoids overwhelming false positives from untyped third-party libs.

#### [I-7-0] pytest harness + conftest + single test entry point (npm/uv run test), unifying the 7 standalone scripts

`testing` · effort **M** · backend/pyproject.toml (add [tool.pytest.ini_options], markers), backend/tests/conftest.py (new), backend/tests/* (relocated/wrapped scripts), package.json (scripts.test, scripts.test:all)

- **Today.** All seven backend tests are standalone scripts gated on `if __name__ == "__main__"` with a hand-rolled `main()` and bare `assert`s: backend/scripts/test_graphiti_services.py, test_graphiti_migration.py, test_pipeline_resume.py (uses tmpdir + monkeypatched `PipelineOrchestrator._run`), test_deerflow_deep_research.py, test_zep_rate_limit.py, test_profile_format.py (prints, no assert pass/fail), test_reddit/twitter sim runners. pyproject already declares pytest>=8 and pytest-asyncio in [dependency-groups].dev, but there is NO conftest.py, NO pytest.ini/[tool.pytest.ini_options], NO `tests/` package, and NO `npm test`/`uv run pytest` entry. package.json scripts has no `test`; doctor.sh never runs tests. There is no way to run all tests with one command or get a pass/fail summary.
- **Proposed.** Add backend/tests/ as a pytest package and a [tool.pytest.ini_options] block in backend/pyproject.toml. Add markers `unit` (offline, no LLM/graph), `integration` (local Graphiti, needs LLM provider), `e2e` (full pipeline). Provide backend/tests/conftest.py with shared fixtures: `tmp_pipeline_dir` (sets Config.PIPELINE_DATA_DIR like test_pipeline_resume already does), `blocked_orchestrator` (the `_run` monkeypatch pattern lifted from test_pipeline_resume.py main()), and `requires_llm`/`requires_graph` skip-guards that auto-`pytest.skip` when no provider/backend is configured (so unit runs are always green on a bare checkout). Convert the existing scripts to `test_*` functions (keep their `__main__` blocks as thin shims so existing docs/CI commands still work). Add `npm run test` (-> `cd backend && uv run pytest -m unit`) and `npm run test:all`.
- **Why.** Today a contributor or CI cannot answer 'do all tests pass?' without running seven scripts by hand and eyeballing prints (test_profile_format.py never even fails on error). Unifying under pytest gives discovery, a pass/fail exit code, selective marker runs (fast unit set vs. costly integration), and shared fixtures that remove the duplicated tmpdir/monkeypatch boilerplate. This is the foundation every other testing improvement builds on and directly enables CI.
- **Design.** pyproject: [tool.pytest.ini_options] testpaths=["tests","scripts"], markers=["unit","integration","e2e","requires_llm","requires_graph"]. conftest: @pytest.fixture tmp_pipeline_dir(monkeypatch): d=tmp_path; monkeypatch.setattr(Config,'PIPELINE_DATA_DIR',str(d)). @pytest.fixture blocked_orchestrator: ev=Event(); orig=PipelineOrchestrator._run; PipelineOrchestrator._run=staticmethod(lambda s: ev.wait(30)); yield ev; ev.set(); restore. requires_graph(): skip unless importlib.util.find_spec('redislite.async_falkordb_client') or 'kuzu'. requires_llm(): skip unless a provider is resolvable.
- **Impact.** One command (`npm test`) yields a deterministic green/red gate; the offline unit subset runs in seconds on any checkout with zero credentials. Removes silent test rot (profile test currently can't fail).
- **Depends on.** None (pytest already in dev deps). Other proposals (CI, golden tests) layer on top.
- **Risk.** Low. Wrapping existing scripts is mechanical; skip-guards ensure no false failures when credentials/graph backend are absent.

#### [I-7-1] GitHub Actions CI: tiered jobs (lint+type, offline unit, overlay-sync gate) with caching

`robustness` · effort **M** · .github/workflows/ci.yml (new), backend/pyproject.toml (ruff config — see lint proposal)

- **Today.** There is no .github/ directory at the repo root (confirmed: `ls .github` empty). Nothing runs on push/PR. scripts/doctor.sh is a local env health check only. The DeerFlow bridge test (test_deerflow_deep_research.py: assert_bridge_synced/assert_skill_synced/assert_loop_detection_patch) already encodes byte-identical overlay-vs-deployed checks that would catch silent research-quality regressions — but nothing runs them automatically, so drift can land on main unnoticed.
- **Proposed.** Add .github/workflows/ci.yml with three fast jobs that need no API keys: (1) `lint-type` — ruff check + ruff format --check + a typed-subset check via `python -m compileall` and optionally mypy on a curated module list; (2) `unit` — `uv sync` then `uv run pytest -m unit` (the credential-free subset from the harness proposal); (3) `overlay-sync` — runs ONLY the assert_*_synced/patch checks from test_deerflow_deep_research.py against the committed deerflow_bridge/ overlay (no deer-flow clone needed because those asserts treat a missing deployed copy as pass and validate the overlay's own invariants/markers). Cache uv (~/.cache/uv) keyed on uv.lock.
- **Why.** CI converts the existing high-value but manual checks into an always-on gate. The overlay-sync job in particular protects forecast quality: the deep-research SKILL.md (S1–S4 source tiering, triangulation, synthesis gate) and the loop_detection_middleware per-run reset patch are load-bearing for research depth; a teammate editing the deployed copy instead of the overlay, or pulling upstream DeerFlow, silently degrades research. Catching that on PR is far cheaper than discovering shallow dossiers in production.
- **Design.** Matrix python 3.12. Steps: astral-sh/setup-uv; `uv sync --python 3.12 --group dev`; job unit: `uv run pytest -m unit -q`; job overlay-sync: `uv run pytest scripts/test_deerflow_deep_research.py -q` plus a standalone `uv run python -c` invoking only the assert_*_synced/patch functions (skip the bridge-import asserts that need langgraph). Concurrency group cancel-in-progress on PR ref.
- **Impact.** Every PR gets a sub-2-minute credential-free signal covering lint, types, offline logic, and research-overlay integrity. Prevents the most damaging silent regressions (prompt/skill/patch drift) from reaching main.
- **Depends on.** Builds on the pytest harness (markers) and the lint/type config proposal. uv is already the package manager.
- **Risk.** Low. All three jobs are offline/deterministic. Initial ruff run may surface existing style nits — gate with a baseline or `--select` a conservative ruleset first.

#### [I-7-2] Deterministic-seed harness for OASIS profile/persona generation and weighted sampling (Config.SIM_SEED)

`testing` · effort **M** · backend/app/config.py (SIM_SEED, LLM_TEMPERATURE_OVERRIDE), backend/app/services/oasis_profile_generator.py (self._rng, replace random.* calls), backend/scripts/run_parallel_simulation.py (module-level Random from SIM_SEED), optionally backend/app/utils/oasis_llm.py + simulation_config_generator.py (honor LLM_TEMPERATURE_OVERRIDE)

- **Today.** oasis_profile_generator.py calls the unseeded global `random` for persona demographics and counts: random.randint for karma/friend/follower/statuses (lines 271-274), suffix=random.randint(100,999) (292), and random.choice over MBTI/COUNTRIES + random.randint for age in the rule-based fallback (lines 829-881). run_parallel_simulation.py also uses unseeded randomness in its scheduling: weighted reservoir key `random.random()**(1.0/w)` (line 1016) and probabilistic posting `random.random() < p` (line 1069). simulation_config_generator and oasis_llm hardcode temperatures (0.7/0.3). Nothing in Config seeds RNG. Result: two runs of the same graph+prompt produce different personas, follow graphs, and event timings, so no golden/snapshot test of the prepare/run stages is possible and forecast reproducibility for A/B evaluation is impossible.
- **Proposed.** Add an OPTIONAL `Config.SIM_SEED` (env SIM_SEED, default None = today's nondeterministic behavior). When set, seed a per-run `random.Random(SIM_SEED)` instance and thread it through the stochastic call sites instead of the global module: OasisProfileGenerator gains `self._rng` (seeded from Config.SIM_SEED or a fresh Random()), run_parallel_simulation derives its sampling RNG from the same seed. Default-off preserves current behavior exactly; turned on it makes profiles, follow graphs, and event schedules reproducible. Also surface `Config.LLM_TEMPERATURE_OVERRIDE` (default None) so deterministic eval runs can force temperature=0 across generators.
- **Why.** Determinism is the prerequisite for every golden/snapshot test of stages 4–6 and for credible forecast evaluation: you cannot regression-test the persona/follow-graph builder, nor compare two pipeline variants, when the same inputs yield different agents. A seedable RNG is the smallest change that unlocks reproducible simulations without touching the LLM (whose nondeterminism is separately bounded by temperature override).
- **Design.** In OasisProfileGenerator.__init__: self._rng = random.Random(Config.SIM_SEED) if Config.SIM_SEED is not None else random.Random(). Replace random.randint->self._rng.randint, random.choice->self._rng.choice. run_parallel_simulation: _RNG = random.Random(int(os.environ.get('SIM_SEED'))) if SIM_SEED else random.Random(); key=_RNG.random()**(1/w). Generators: temperature = Config.LLM_TEMPERATURE_OVERRIDE if Config.LLM_TEMPERATURE_OVERRIDE is not None else <existing>.
- **Impact.** Enables byte-stable golden tests of the prepare stage and reproducible end-to-end forecasts for A/B/regression evaluation; aids debugging (replay a problematic run). Zero behavior change when SIM_SEED unset.
- **Depends on.** Pairs with the golden-fixture proposal (which consumes the determinism). Independent of CI.
- **Risk.** Low-Medium. Must catch every global-`random` call site in the two files; a missed one leaks nondeterminism but does not break runtime. Keep default None so production sampling diversity is untouched.

#### [I-7-3] Golden/fixture tests for the pure transform functions in each stage (offline, no LLM)

`testing` · effort **M** · backend/tests/golden/test_ontology_validate.py, test_actors_threading.py, test_simconfig_json_repair.py, test_report_tool_parse.py (new), backend/tests/golden/fixtures/*.json (new), backend/tests/golden/_helpers.py (assert_matches_golden)

- **Today.** Each stage has deterministic pure functions that today have zero coverage but are exactly the kind of logic that silently breaks the frontend contract or the golden thread: ontology_generator._validate_and_process (caps to 10 entity/edge types, truncates 100-char descriptions, injects Person/Organization fallbacks, lines 257-345); actors.py situation_brief/extract_relationship_rows/build_initial_follow_graph/events_to_schedule (the threaded research->sim handoff); simulation_config_generator._fix_truncated_json/_try_fix_config_json (714-766) and _build_scheduled_events (509); report_agent._parse_tool_calls (1254, three JSON-extraction fallbacks the ReAct loop depends on); graph_builder.get_graph_data node/edge shape that test_graphiti_services.py asserts inline but only when a live graph exists.
- **Proposed.** Add backend/tests/golden/ with table-driven unit tests over these pure functions using committed input/expected fixtures (no LLM, no graph). For ontology: feed a 12-entity LLM-shaped dict and assert exactly 10 returned with Person/Organization present and descriptions truncated. For actors: a fixture actors.json with situation_brief+relationships -> assert build_initial_follow_graph dedupes/orders edges and situation_brief renders stably. For sim-config JSON repair: feed real truncated LLM outputs (captured) -> assert recovered dict. For report _parse_tool_calls: feed the three malformed formats -> assert parsed calls. Store fixtures as JSON files diffable in PRs; use a small `assert_matches_golden(actual, path, update=os.environ.get('UPDATE_GOLDEN'))` helper.
- **Why.** These functions encode the system's hard contracts (Zep's 10-type cap, the frontend node/edge keys validated in test_graphiti_services, the situation_brief->follow-graph threading that IS the golden thread's downstream consumer). They are deterministic and run in milliseconds with no credentials, so they belong in the always-green CI unit set. Capturing real malformed-JSON samples as fixtures turns past production incidents into permanent regression guards.
- **Design.** _helpers.assert_matches_golden(actual,path,update): exp=json.load(open(path)); if update: json.dump(actual,...); assert actual==exp. Example: def test_caps_to_ten(): r=OntologyGenerator.__new__(OntologyGenerator)._validate_and_process(load('fixtures/ontology_12types.json')); assert len(r['entity_types'])==10 and {'Person','Organization'} <= {e['name'] for e in r['entity_types']}.
- **Impact.** Catches contract-breaking edits to the highest-leverage transforms instantly and offline; documents expected behavior as readable fixtures; extends coverage to the golden-thread's consumer side (follow-graph/schedule build) rather than just its producer.
- **Depends on.** pytest harness proposal. The actors/sim-config fixtures benefit from but do not require the seed harness.
- **Risk.** Low. Pure functions, no side effects. Main cost is curating representative fixtures; an UPDATE_GOLDEN env flag makes intentional changes one-command updates.

#### [I-7-4] Fake/record-replay LLMClient fixture to test generators end-to-end without burning API calls

`testing` · effort **M** · backend/tests/fakes/fake_llm.py (new), backend/tests/cassettes/*.json (new), backend/tests/test_ontology_generate_replay.py, test_simconfig_generate_replay.py (new)

- **Today.** Every generator takes an injectable LLMClient (OntologyGenerator.__init__(llm_client=None), SimulationConfigGenerator, OasisProfileGenerator all accept one; LLMClient.chat/chat_json in utils/llm_client.py is the single seam). Yet there is no FakeLLMClient: integration tests either need a real provider (test_graphiti_*.py require .env LLM) or skip the LLM entirely (test_pipeline_resume monkeypatches the whole _run). So the orchestration logic AROUND the LLM (chat_json repair-and-retry at llm_client.py:111-130, the temperature-decay retry loops in simulation_config_generator._call_llm_with_retry:673 and oasis_profile_generator:585) is untested, and there's no cheap way to test ontology->graph->config wiring.
- **Proposed.** Add a FakeLLMClient (tests/fakes/fake_llm.py) implementing chat()/chat_json() that returns scripted responses keyed by a matcher (substring of the system/user prompt) and records calls. Provide two modes: (a) scripted (responses from a fixtures dir) for unit tests, and (b) record-replay — on a `RECORD_LLM=1` run it wraps a real LLMClient and writes responses to a cassette JSON; subsequent runs replay offline. Inject it via the existing `llm_client=` params. Add tests that drive OntologyGenerator.generate and SimulationConfigGenerator.generate_config purely against cassettes, including malformed-then-valid sequences to exercise chat_json's repair path and the temperature-decay retry loops.
- **Why.** The retry/repair logic is where pipelines actually fail in the wild (truncated JSON, empty reasoning-model content), and it is currently invisible to tests because nothing can feed the LLM a controlled bad-then-good sequence. A fake/cassette client makes the full generate() paths deterministic, free, and fast — turning expensive flaky integration coverage into stable unit coverage, and letting CI exercise real generator wiring without any key.
- **Design.** class FakeLLMClient: def __init__(self, script): self.script=script; self.calls=[]. def chat(self,messages,**kw): self.calls.append(messages); key=_match(messages,self.script); return self.script[key]. chat_json delegates to chat then json.loads (so it also exercises real repair when the scripted string is malformed). RecordingLLMClient(real, cassette_path) writes {prompt_hash: response}; ReplayLLMClient reads it. Tests: gen=OntologyGenerator(llm_client=ReplayLLMClient('cassettes/ontology_geopolitics.json')); out=gen.generate(...); assert valid + 'INVOLVES' edge present.
- **Impact.** Tests the LLM-orchestration logic (retry, JSON repair, fallback) that directly determines forecast robustness; cassettes give realistic-but-offline integration coverage that CI can run; cuts test cost/flakiness.
- **Depends on.** pytest harness. Synergizes with golden tests (cassette outputs can feed _validate_and_process goldens).
- **Risk.** Low-Medium. Cassettes can drift from real provider behavior; mitigate by periodic RECORD_LLM refresh and keeping cassettes small/targeted. No production code changes beyond optionally exposing the injection point already present.

#### [I-7-5] Fast offline smoke pipeline (`make smoke` / scripts/smoke.sh) exercising stages 2-6 with stubbed research+LLM

`testing` · effort **L** · scripts/smoke.sh (new), package.json (scripts.smoke), backend/tests/fixtures/smoke_handoff/research_report.md + actors.json (new), backend/tests/test_smoke_pipeline.py (pytest wrapper, marker e2e)

- **Today.** doctor.sh validates the environment (venvs, deer-flow overlay, .env keys) but never executes any pipeline. The only end-to-end exercises are test_graphiti_services.py (needs a live LLM) and the heavyweight test_reddit/twitter_simulation.py runners. There is no quick, low/zero-cost run that confirms the stages actually wire together (ontology dict -> graph_builder -> entity_reader -> sim-config -> a 1-round micro-sim -> report assembly). A breaking change to a stage interface is only discovered on a full real run.
- **Proposed.** Add scripts/smoke.sh (and `npm run smoke`) that runs an OPTIONAL fast path: skip stage-1 research by feeding a committed tiny research handoff fixture (test_pipeline_resume already proves _load_research_handoff accepts a research_report.md without actors/sources), use the FakeLLMClient cassette for ontology+config+report, build a real local Graphiti graph from ~6 fixture chunks (the migration test already does this offline once a provider exists — gate behind requires_graph and fall back to a stub graph snapshot when absent), and run a minimal 1-round/2-agent simulation via run_parallel_simulation with SIM_SEED set and --max-rounds 1. Assert each stage emits its expected artifact shape (graph_data keys, profiles CSV/JSON headers from test_profile_format, a non-empty report outline). Total target < ~60s, $0 with cassettes.
- **Why.** A smoke pipeline is the single highest-signal integration check: it proves the inter-stage contracts hold after any refactor (the most common breakage class), which unit/golden tests alone cannot. Reusing the determinism seed, fake LLM, and handoff fixture makes it cheap and deterministic enough for CI's integration job and for a pre-push local gate.
- **Design.** smoke.sh: export SIM_SEED=1337 LLM_TEMPERATURE_OVERRIDE=0; uv run python -m tests.smoke --handoff tests/fixtures/smoke_handoff --cassette tests/cassettes/smoke.json --max-rounds 1 --agents 2. tests/smoke.py: load handoff via _load_research_handoff; gen ontology via ReplayLLMClient; if requires_graph: build real graph else load fixtures/graph_snapshot.json; ZepEntityReader.filter_defined_entities; SimulationConfigGenerator.generate_config (cassette); assert artifacts; optionally run sim if SMOKE_FULL.
- **Impact.** Catches stage-interface regressions (renamed keys, changed return shapes, broken artifact paths) in under a minute without real research/LLM spend; gives contributors a one-command 'is the pipeline still alive?' check.
- **Depends on.** Builds on seed harness (SIM_SEED, --max-rounds), FakeLLMClient cassettes, pytest markers (requires_graph), and the research-handoff fixture pattern. Should land after those.
- **Risk.** Medium. Most moving parts of all the proposals combined; OASIS micro-sim startup can be the slow/fragile link — gate the sim leg behind a `SMOKE_FULL=1` flag so the default smoke covers stages 2-4+6 deterministically and the sim leg is opt-in. Keep it OPTIONAL and never on the default `npm run dev` path.

#### [I-7-7] Forecast-quality regression scoring with a rubric LLM-judge over a fixed scenario set (gated, opt-in)

`testing` · effort **L** · backend/scripts/eval_forecast_quality.py (new), backend/tests/eval/scenarios/*.json (committed prompts + graph fixtures), backend/tests/eval/rubric.md (judge rubric), backend/tests/eval/baseline_scores.json (committed thresholds), backend/app/config.py (EVAL_ENABLED flag)

- **Today.** The system's whole purpose is forecast quality (report_agent.py system prompt: '未来预测报告', scenario/probability framing at lines 556-654), yet there is NO measurement of output quality — no test asserts a report is grounded, cites simulation agents, covers scenarios, or stays faithful to the graph. The only report-side test (test_zep_rate_limit assert_graph_snapshot_is_reused) checks caching, not content. So a prompt/model change that quietly produces vaguer, less-grounded, or hallucinated forecasts is undetectable until a human reads the output.
- **Proposed.** Add an OPTIONAL evaluation harness (gated behind `EVAL_ENABLED`/a CLI flag, never in default CI) that runs the pipeline on a small fixed scenario set (2-3 committed prompts, e.g. the semiconductor scenario already used across tests) with SIM_SEED + LLM_TEMPERATURE_OVERRIDE=0, then scores each report with an LLM-judge against a structured rubric: groundedness (claims traceable to graph facts / agent quotes), scenario coverage, probability/uncertainty calibration language, contradiction handling, and citation density. Persist scores as JSON; fail the eval if any dimension drops below a committed baseline threshold (regression mode) and emit a diff vs. the previous run.
- **Why.** This is the only proposal that measures the product's actual value — forecast quality — rather than plumbing. With determinism (seed+temp=0) the pipeline becomes repeatable enough that an LLM-judge rubric gives a stable, comparable score, turning 'did this prompt/model change make forecasts better or worse?' into a number. It also surfaces silent quality regressions from upstream model swaps (the system supports 8 providers) that no structural test can catch.
- **Design.** eval_forecast_quality.py: for scenario in scenarios: run pipeline (cassette upstream, real graph+sim with SIM_SEED, real report agent); judge = LLMClient(); scores = judge.chat_json([rubric_system, report+graph_facts+agent_samples]) -> {groundedness:0-5, coverage, calibration, contradiction, citation_density}; mean over k=3. assert all(score >= baseline[scenario][dim]-tolerance). Output eval_report.json with per-dimension deltas vs baseline_scores.json; `--update-baseline` to accept intentional improvements.
- **Impact.** Quantifies forecast quality and guards against silent degradation across prompt/model/provider changes; provides an objective signal for A/B'ing report-agent and research-skill edits — the highest-leverage quality lever in the repo.
- **Depends on.** Requires determinism seed harness + FakeLLM/cassette (for the upstream stages) + smoke pipeline scaffolding. Judge step needs a real LLM (so opt-in, not default CI).
- **Risk.** Medium. LLM-judge scores have variance; mitigate by judging at temperature 0, averaging k=3 judge runs, asserting on coarse thresholds/deltas rather than exact scores, and keeping the scenario set small and fixed. Must stay strictly opt-in to respect the cost/risk-behind-flags invariant.

---

## 8. Sequenced execution roadmap

From *stop-the-bleeding* to *more capable*. Each phase lists item ids (findings `F-*`, improvements `I-*`).

### Phase 0 -- Stop the bleeding (security + runaway cost)

*Goal:* Eliminate the two unbounded harms first: secrets written to disk/argv/logs, and OASIS subprocesses that outlive a restart and burn credits forever. Lock down the LAN-exposed API. No new features.

- `F-12-0` — OASIS simulation subprocesses are orphaned (not killed) across a backend restart — keep burning LLM credits forever
- `F-13-0` — No authentication + CORS '*' + 0.0.0.0 bind exposes all mutating pipeline/settings endpoints to the LAN
- `F-13-1` — API keys written to plaintext log file on every settings request (request-body DEBUG logging)
- `F-8-0` — API keys leak to disk via DEBUG request-body logging
- `F-8-1` — _persist_env writes API key to .env without escaping → newline/`=` injection & corruption
- `F-11-0` — API key leaked to process argv and a world-readable /tmp file during setup live-test
- `F-0-7` — Research prompt passed via argv (--prompt) exposes the full prediction question to process listing
- `F-13-3` — Research prompt passed via subprocess argv — visible in process list to all local users
- `F-13-2` — Unauthenticated SSRF via /api/settings/llm/test base_url (arbitrary outbound request with reflected response)
- `F-8-5` — _test_openai_compat_provider echoes full SDK error + 500 handlers return traceback to client
- `F-13-4` — Inconsistent path-traversal hardening: read/state endpoints lack the guard that delete() has
- `I-8-3` — Centralize secret redaction + support external secret sources (file/env-indirection) and stop full-env subprocess leakage

### Phase 1 -- Restart survivability + state integrity

*Goal:* Make lifecycle and on-disk state trustworthy: persisted pgid + heartbeat/owner-lease, a boot reaper that reaps or re-attaches, honest stop/start, and atomic writes so a SIGKILL or polling reader never corrupts the contract.

- `F-6-5` — Server-restart resume reports stale process_pid and runs without a live process handle/monitor; stop becomes a no-op against orphaned subprocesses
- `F-6-11` — stop_simulation sets status STOPPED even when no process handle exists and no kill executed, lying about a still-running subprocess
- `F-12-6` — start_simulation 'already running' guard and stop rely on in-memory state that is stale after restart, blocking re-runs of crashed simulations
- `I-4-1` — Persisted run heartbeat + owner lease so reconcile_orphans distinguishes dead pipelines from slow ones
- `F-1-8` — reconcile_orphans re-identifies the orphan research PID only by command substring 'deerflow_research.py' — can mis-signal a sibling pipeline's research on PID reuse
- `F-6-9` — SimulationManager and SimulationRunner both read-modify-write state.json concurrently with no shared lock, risking lost status updates
- `F-6-13` — _check_simulation_prepared rewrites state.json with non-atomic 'w' truncation, reintroducing the torn-read race the rest of the code avoids
- `F-7-6` — Non-atomic writes of progress.json / section_*.md / meta.json can be read mid-write by polling endpoints
- `F-0-4` — actors.json / sources.json / timeline.json written non-atomically — watchdog SIGKILL mid-write can corrupt the contract
- `F-6-12` — Requested graph-memory updater failure is swallowed; run proceeds with updates off while API still reports them enabled
- `F-6-10` — Platform-completion inferred from actions.jsonl existence; a slow/failed platform is treated as disabled and the run is marked COMPLETED prematurely
- `I-4-3` — Artifact manifest with content hashes for trustworthy stage reuse and corruption detection
- `I-4-4` — Schema-versioned pipeline state with forward/backward migration on load

### Phase 2 -- Golden-thread correctness (don't ship wrong-but-green forecasts)

*Goal:* Harden the data contracts the EXECPLAN just built so they degrade loudly, not silently: ontology/persona/config crash-safety, the dead FOLLOW/MUTE feedback edges, deterministic non-stale grounded reports, and standalone-script completion.

- `F-3-0` — OntologyGenerator._validate_and_process assumes entity_types/edge_types are lists; non-list LLM output crashes the whole ontology stage
- `F-5-0` — Realtime-saved reddit_profiles.json omits required keys (mbti/gender/age/country) → OASIS KeyError on interrupted runs
- `F-5-2` — LLM agent-config keyed by cfg['agent_id'] — KeyError or str/int mismatch silently discards an entire batch's LLM configs
- `F-4-0` — FOLLOW/MUTE typed feedback edges are never written (action_args key mismatch)
- `F-9-0` — Single-platform standalone scripts emit no actions.jsonl, breaking completion detection and all downstream consumers
- `F-7-0` — force_regenerate leaves stale report folder; get_report_by_simulation returns a non-deterministic (often stale) report
- `F-7-2` — Native tool-calling section path does NOT enforce minimum tool calls, so sections can be written with zero graph grounding
- `F-7-1` — ReportConsoleLogger attaches a FileHandler to MODULE-GLOBAL loggers, cross-contaminating concurrent reports' console logs and racing on handler lists
- `F-1-0` — REPORT stage has no reuse guard — resume regenerates the entire forecast (re-runs full LLM tool agent) even if the report already succeeded
- `I-4-0` — Report stage resumability: reuse persisted per-section markdown instead of regenerating from scratch
- `F-3-1` — set_ontology raises KeyError on entity/attr/edge entries missing "name", aborting the entire graph build
- `F-3-3` — seed_actors IS_A path writes a literal type-name node ("Person"/"Organization") as a graph entity
- `F-3-4` — Ontology fallback-insertion truncates from the tail and can silently drop legitimate specific entity types
- `F-5-1` — Realtime save filters out None profiles, shifting array positions and breaking positional agent_id contract
- `F-5-3` — interested_topics consumed by echo-chamber clustering but never produced → T3.4 clustering silently degrades to stance-only
- `F-6-0` — max_rounds truncation: persisted rounds_truncated_from/to dropped on state reload, falsifying the golden-thread truncation banner
- `F-6-1` — Monitor thread mutates a SimulationRunState shared with API request threads with no lock (torn reads / lost updates)
- `F-2-1` — delete_graph never deletes graph data on the FalkorDB *server* backend (silent no-op)
- `F-2-2` — Bi-temporal datetime fields leak through the Zep facade as datetime objects, not ISO strings

### Phase 3 -- Concurrency, leaks, and performance taxes

*Goal:* Serialize the shared mutable resources (graph runtime, state.json, run state), close the IPC/temp-file/disk leaks, and kill the N+1 / full-rescan hot paths that silently tax latency and cost.

- `F-2-5` — Concurrent add_episode on one cached Graphiti instance shares mutable driver/clients (dedup ordering + state hazard)
- `F-12-8` — Concurrent runtime calls share one event loop, one redislite FalkorDB client, and cached per-graph Graphiti instances with no per-graph write/read serialization
- `F-12-1` — Report stage reads the knowledge graph while the simulation→graph feedback writer is still flushing (concurrent read/write on one FalkorDB graph)
- `F-12-3` — SimulationRunner signal handler raises KeyboardInterrupt from inside the chained PipelineOrchestrator handler when the original handler is SIG_DFL
- `F-8-4` — apply_provider mutates shared Config + os.environ with no lock vs running pipelines
- `F-6-6` — IPC client orphans response files on timeout while the server is mid-interview; unread responses leak in ipc_responses/ and never get cleaned
- `F-12-7` — IPC interview timeout leaves an orphaned command file; server may execute a stale interview after the client already gave up
- `F-7-4` — download_report writes a NamedTemporaryFile with delete=False and never cleans it up — disk leak
- `F-4-3` — insight_forge does N+1 per-node round-trips instead of using the cached node map
- `F-6-2` — /run-status/detail re-parses both full actions.jsonl files up to 4x per request; analytics rebuild whole history per call (unbounded, repeated)
- `F-6-3` — get_timeline/get_agent_stats/run_summary silently cap at limit=10000 newest actions, truncating analytics on long simulations
- `F-7-3` — chat() re-scans every report folder (reading full markdown) on every user message — O(N) N+1 with large payloads
- `F-5-4` — Per-worker nested ThreadPool + serial Zep retries make persona generation latency-bound and amplify thread count
- `F-4-4` — fetch_all_edges has no max_items cap; full edge set loaded and cached unbounded
- `F-4-5` — Failed activity batches are silently dropped (no dead-letter / no re-buffer)
- `F-4-6` — search_graph swallows all exceptions and silently downgrades to keyword search
- `I-6-5` — Reuse cached graph nodes/edges for local search instead of re-fetching per query
- `I-6-1` — Parallelize insight_forge sub-query searches and add a per-run retrieval cache in ZepToolsService

### Phase 4 -- Observability, telemetry, and test/CI foundation

*Goal:* Make the pipeline measurable and regression-proof: central LLM cost/token/latency meter + per-run telemetry, structured logging with run/stage IDs, a budget guard, and the first real test harness + CI so later changes are safe.

- `I-5-0` — Central LLM call meter: capture token/cost/latency from every provider into a per-run accounting sink
- `I-5-1` — Per-run telemetry summary artifact (run_telemetry.json) with stage durations, token/cost rollup, and failure attribution
- `I-5-7` — Parse and structure the DeerFlow research stage's already-emitted token usage into pipeline telemetry
- `I-5-4` — Enrich ReportLogger and run_summary with LLM telemetry; add a report-level cost/timing rollup
- `I-5-6` — Live progress heartbeat with ETA and current-spend, surfaced through the existing status/progress APIs
- `I-5-2` — Structured JSON logging mode with run/stage correlation IDs via contextvars
- `I-5-3` — Optional run-level budget guard: abort or downgrade when token/cost/time thresholds are exceeded
- `I-6-6` — Pipeline-wide LLM observability: per-phase call counts, tokens, latency, cache hit-rate
- `I-7-0` — pytest harness + conftest + single test entry point (npm/uv run test), unifying the 7 standalone scripts
- `I-7-4` — Fake/record-replay LLMClient fixture to test generators end-to-end without burning API calls
- `I-7-3` — Golden/fixture tests for the pure transform functions in each stage (offline, no LLM)
- `I-7-6` — ruff + targeted mypy gate with project lint config (currently none at repo root)
- `I-7-1` — GitHub Actions CI: tiered jobs (lint+type, offline unit, overlay-sync gate) with caching
- `F-11-1` — pyproject requires-python >=3.11 silently drops the local graph backend (falkordblite/redis) on 3.11
- `F-11-4` — AGPL LICENSE file deleted from the tree while both manifests still declare AGPL-3.0
- `F-11-2` — doctor.sh envval does not strip whitespace/inline comments, causing false 'unknown provider' and false key-missing failures
- `F-8-6` — Rotating log filename frozen at import time; date never rolls
- `I-8-5` — Add a checked-in `.env.example` <-> Config drift validator wired into doctor and CI
- `I-8-0` — Unify doctor.sh + preflight_pipeline behind one Python preflight engine, exposed as `doctor --json` and `GET /api/research/preflight`

### Phase 5 -- Make it more powerful and comprehensive

*Goal:* Now that it is safe, honest, and measured, raise forecast quality and reach: structured machine-checkable forecasts with grounding + calibration, content-addressed LLM cache + two-tier routing, richer KG retrieval and simulation fidelity, and ensemble/multi-seed probabilistic forecasting.

- `I-3-0` — Structured forecast claims layer: explicit scenario probabilities, calibration bands, and a machine-checkable forecast block per report
- `I-9-1` — Structured, machine-readable forecast object (probabilities + resolution criteria)
- `I-3-1` — Citation-grounding verifier: enforce that every quantitative/quoted claim is traceable to a tool result or [S#] source, with an unsupported-claim audit
- `I-3-5` — Self-critique calibration pass: red-team the draft forecast for overconfidence, base-rate neglect, and unsupported leaps
- `I-6-0` — Content-addressed LLM response cache (memoize identical chat()/chat_json() calls across the pipeline)
- `I-6-2` — Two-tier model routing: cheap model for retrieval/decomposition/extraction, strong model for synthesis
- `I-6-3` — Concurrent report section generation with dependency-aware ordering
- `I-0-0` — Carry source tiering (S1-S4), dates, and per-claim grading into sources.json and the report contract
- `I-0-3` — Coverage-and-quality gate after research, before downstream consumption
- `I-1-0` — Expose Graphiti's full search surface (filters, center-node, MMR, BFS) through the runtime/Zep facade
- `I-1-2` — Make detected communities first-class retrievable structure (faction-aware GraphRAG)
- `I-1-6` — Diversity-aware (MMR) retrieval and graph-coverage observability for the report agent
- `I-2-0` — Emergent-structure & opinion-dynamics metrics layer (stance trajectory, polarization, follow-graph communities) wired into run_summary.json
- `I-2-4` — Multi-seed ensemble runs with forecast confidence intervals
- `I-3-3` — Multi-replicate ensemble forecasting with frequency-derived scenario probabilities
- `I-9-0` — Ensemble / multi-seed runs with aggregated, probabilistic forecasts
- `I-9-2` — Backtesting & calibration harness vs ground truth
- `I-9-5` — Programmatic API + Python SDK with API-key auth
- `F-10-7` — API base URL falls back to hardcoded http://localhost:5001, bypassing the Vite dev proxy and breaking non-localhost deploys
- `F-10-12` — requestWithRetry retries non-idempotent POSTs (create/prepare/start/interview), risking duplicate side effects
- `F-10-1` — GraphPanel deep-watch on graphData rebuilds the entire D3 simulation on every refresh, losing zoom/pan/positions

---

## Appendix A — Static-analysis results

**Tooling.** ruff 0.13.3 available; python 3.12.6 (system); pytest 8.4.2 (system). `ruff check backend/app deerflow_bridge --select F,E9 --output-format concise` ran successfully: 72 errors, all F401/F841/F541 — NO F821 (undefined names) and NO E9xx (syntax/runtime) errors. 65/72 are auto-fixable. uv 0.x available at /Users/rogerlin/.local/bin/uv with a venv at backend/.venv; project deps (flask-cors, pytest-asyncio) install via pyproject.toml and are present only inside that venv, not in system python. time.sleep AST scan: confirmed via ast parsing that NONE of the time.sleep() calls in pipeline_orchestrator.py, zep_graph_memory_updater.py, simulation_runner.py, zep_tools.py, graph_builder.py, llm_client.py, zep_paging.py occur inside `async def` — all are in synchronous retry/backoff code (correct usage). No eval(), pickle.load, yaml.load (unsafe), or shell=True found anywhere in backend/app, deerflow_bridge, or backend/scripts. No mutable default args (def ...=[] / ={}) found. No literal TODO/FIXME/XXX/HACK markers in code (the two grep hits were Chinese-language comments mentioning '\\uXXXX', not actual XXX markers).

**Syntax errors:** none (all backend modules byte-compile).

**Import errors:**
- When test modules are imported under an interpreter WITHOUT the project's deps (e.g. system python3 instead of `uv run`), backend/app/__init__.py:13 `from flask_cors import CORS` raises ModuleNotFoundError: No module named 'flask_cors', which cascades and breaks collection of all 5 importing test modules (scripts/test_graphiti_migration.py, test_graphiti_services.py, test_pipeline_resume.py, test_profile_format.py, test_zep_rate_limit.py). This is an environment/interpreter selection issue, not a code defect: under `cd backend && uv run python ...` the import resolves and collection succeeds. No genuine in-code import errors (no F401-as-error, no broken relative imports) were found by ruff or by byte-compilation.

**Test status.** Test collection FAILS under system python3 (5 collection errors: `ModuleNotFoundError: No module named 'flask_cors'` because importing the test modules triggers backend/app/__init__.py:13 `from flask_cors import CORS`; system env also lacks pytest_asyncio). However, the project uses a uv-managed venv (backend/.venv, uv at /Users/rogerlin/.local/bin/uv; flask-cors>=6.0.0 and pytest-asyncio>=0.23.0 are declared in backend/pyproject.toml). Re-running with `cd backend && uv run python -m pytest --collect-only -q` SUCCEEDS: exit code 0, no collection errors, 9 tests collected (all from scripts/test_pipeline_resume.py [8 tests] and scripts/test_profile_format.py [1 test]). The other four test_*.py files (test_deerflow_deep_research.py, test_graphiti_migration.py, test_graphiti_services.py, test_zep_rate_limit.py) are script-style/manual files containing no pytest `test_` functions, so they contribute 0 collected tests. CONCLUSION: tooling/test harness is healthy when run via uv; the only blocker is using the wrong interpreter (system python instead of the uv venv). Did not run the suite to completion (several tests require live ZEP/LLM/Graphiti services per the manual scripts), only collection was verified.

**Pattern smells:**

| pattern | file | note |
|---|---|---|
| bare except: | `backend/app/api/simulation.py:965` | Bare `except:` catches everything (including KeyboardInterrupt/SystemExit) when slicing created_at date; should be `except (TypeError, KeyError)` or at least `except Exception`. |
| bare except: | `backend/app/services/oasis_profile_generator.py:685` | Bare `except:` followed by `pass` while parsing/repairing LLM JSON; swallows all errors silently. Use `except Exception` and log. |
| bare except: | `backend/app/services/simulation_config_generator.py:755` | Bare `except:` around json.loads during JSON repair; catches non-Exception interrupts. Narrow to json.JSONDecodeError/Exception. |
| bare except: | `backend/app/services/simulation_config_generator.py:761` | Nested bare `except:` followed by `pass`; second json.loads failure is fully silenced, returns None implicitly with no diagnostic. |
| except Exception: pass (silent swallow) | `backend/app/services/pipeline_orchestrator.py:330,623,790,957,1339,1470,1666,1719,1753,1765` | Multiple `except Exception: pass` blocks silently swallow errors. Several are intentionally best-effort (e.g. line 623 has a comment 'best-effort enrichment must never break manual report generation'), but most have no logging, hiding real failures. |
| except Exception: pass (silent swallow) | `backend/app/services/simulation_runner.py:550,583,589,1377,1385` | `except Exception: pass` with no logging in cleanup/IPC paths; failures during teardown are silently ignored. |
| except Exception: pass (silent swallow) | `backend/app/utils/file_parser.py:42,51` | Two `except Exception: pass` blocks while parsing files; parse failures are silently dropped with no warning. |
| except Exception: pass (silent swallow) | `backend/app/config.py:247` | `except Exception: pass` in config loading; a malformed config value would be silently ignored. |
| print() used as logging in service | `backend/app/services/oasis_profile_generator.py:998-1056,1089` | Service code uses `print(...)` for progress/status output instead of the logging module (7 occurrences). In a long-running service this bypasses log levels/handlers. |
| F401 unused import | `backend/app/services/report_agent.py:14,17,26,27,28,29` | ruff F401: unused imports (`time`, `dataclasses.field`, and four names from `.zep_tools`: SearchResult/InsightForgeResult/PanoramaResult/InterviewResult). 65 of 72 ruff findings are auto-fixable with `ruff --fix`. |
| F841 unused local variable | `backend/app/services/oasis_profile_generator.py:344,369` | ruff F841: `last_exception` assigned in retry loops but never used/re-raised — the captured exception is discarded, so on final failure the original error is lost. Same pattern flagged at report_agent.py:2609,2723 and zep_tools.py:1238,1239 and simulation.py:314. |
| F541 f-string without placeholders | `backend/app/services/zep_tools.py (multiple, e.g. 174,177,185,191,197,207,253,255,264,270,276) and oasis_profile_generator.py/ontology_generator.py/report_agent.py/simulation.py` | ruff F541: many f-strings have no placeholders (the leading `f` is unnecessary). Harmless but indicates copy/paste; ~30 occurrences total. |

## Appendix B — End-to-end data-contract map & leaks

| stage | produces | consumes | drops / leaks |
|---|---|---|---|
| 1. DeerFlow research output (deerflow_bridge/deerflow_research.py) | Handoff dir files: research_report.md (REQUIRED, long Markdown dossier), prediction_requirement.txt (the question), actors.json (the FULL extracted JSON object: central_question, as_of_date, situation_brief{current_situation,context,dynamics,fault_lines,catalysts}, actors[]{name,type,role,stance,influence,memory}, relationships[]{source,target,type,sign,strength,basis}, key_events[]{date,event}, hot_topics[]), timeline.json (promoted copy of key_events), sources.json (the popped sources[]{title,url}), research_progress.log, meta.json (status + counts). | The prediction prompt/question (--prompt or --prompt-file), depth preset, model, target language. No upstream pipeline artifacts (this is stage 0). | extract_json_object pop('sources') REMOVES sources from actors.json (they live only in sources.json). key_events is DUPLICATED into both actors.json and timeline.json. The structured JSON is RE-DERIVED from scratch by a second tool-free LLM extraction pass (extract_structured_tool_free) reading the finished report.md, NOT carried structurally from the research thread -- so any actor/fact present in the prose but missed by the extractor is silently lost. sign/strength/basis fields on relationships and situation_brief.context/dynamics are produced here but several are never read downstream (see later stages). |
| 2. Ontology generation (ontology_generator.py + pipeline_orchestrator._actors_to_context) | ontology dict {entity_types[] (EXACTLY 10, last 2 forced to Person/Organization fallbacks), edge_types[] (max 10, each name/description/source_targets/attributes), analysis_summary}. Persisted to project.ontology and handoff/ontology.json. | report_md (document_texts=[report_md]), state.prompt (simulation_requirement), and additional_context built by _actors_to_context(actors): actor name/type/role/stance lines (first 25 actors), hot_topics (first 10), situation_brief_block, and relationship rows (first 30, rendered as 'src --[label/TYPE]--> tgt'). | report_md is TRUNCATED to MAX_TEXT_LENGTH_FOR_LLM=50000 chars before the LLM sees it (only for ontology analysis; full text still used for graph). _actors_to_context only passes actor name/type/role/stance -- it DROPS influence, memory, and relationship basis/sign/strength. _validate_and_process forcibly TRUNCATES entity_types to 10 and edge_types to 10: if the LLM produced richer edge types they are cut from the END, so research-confirmed relationship types pushed past index 10 are dropped. The ontology is RE-DERIVED by the LLM from prose+context rather than directly mapping the typed relationships in actors.json (the relationship types ALLY_OF/OPPOSES/etc are only hinted, not guaranteed to become edge_types). |
| 3. Graph build / seed (graph_builder.py, pipeline _run graph stage) | Zep/Graphiti graph_id; typed seed edges from relationships[] (add_triplet with type as edge name + basis as fact); IS_A type edges for isolated high-signal actors; episode nodes/edges extracted from report chunks; optional handoff/communities.json (best-effort, [{uuid,name,summary}]). | report_md (split via TextProcessor into chunks), project.ontology (set_ontology), actors (full object) for seed_actors, as_of = parse_as_of(actors.as_of_date) used as valid_at for seeds and reference_time for all chunks. | seed_actors maps actor.type via ACTOR_TYPE_TO_LABEL where Media/Government/Platform ALL COLLAPSE to 'Organization' -- the finer real-world type distinction is lost in the graph node label. relationships whose type is not in REL_EDGE_NAME are skipped. actor influence/stance/memory are NOT seeded as node properties (only role becomes the IS_A fact); they are re-derived later only via name-matching back to actors.json. relationship sign/strength are dropped (only basis is written as the edge fact). add_triplet dedup means text extraction must re-discover and 'enrich' seeds; if Zep extraction fails to merge by name+embedding, duplicate/disconnected nodes can result. set_ontology renames any attribute colliding with reserved names (uuid/name/etc) to entity_<name>. |
| 4. Persona + sim-config generation (oasis_profile_generator.py, simulation_config_generator.py) | reddit_profiles.json + twitter_profiles.csv (per-agent user_id/name/username/bio/persona/age/gender/mbti/country/profession/interested_topics; Twitter packs bio+persona into user_char). simulation_config.json: time_config, agent_configs[]{agent_id,entity_uuid,entity_name,entity_type,activity_level,posts_per_hour,comments_per_hour,active_hours,response_delay_min/max,sentiment_bias,stance,influence_weight,interested_topics}, event_config{initial_posts (with poster_agent_id),scheduled_events,hot_topics,narrative_direction,initial_follows}, twitter_config/reddit_config, as_of_date. | Graph entities via ZepEntityReader.filter_defined_entities (only nodes with a custom label beyond Entity/Node survive -- generic 'Entity'-only nodes are DROPPED), capped to Config.OASIS_MAX_AGENTS (top-N by matched-actor/influence/edge-count, all matched actors retained). Per persona: entity name/summary/attributes/related_edges/related_nodes + a live Zep hybrid search + match_actor(entity.name, actors) -> actor_briefing(role/stance/influence/memory) + relationship_briefing + situation_brief one-liner. Config generator: simulation_requirement, document_text (report_md), entities, actors (actors_digest, situation_brief_block, fault_lines, key_events->scheduled_events, relationships->initial_follows). | Actor->entity linkage is name-based fuzzy match (normalize_name + bidirectional substring): an actor that the graph named differently, or never extracted as a typed node, gets NO persona enrichment -- its researched stance/memory is silently lost. Entities with only the bare 'Entity' label are filtered out before persona/config, so research facts attached to un-typed nodes never reach an agent. document_text is truncated to remaining space within MAX_CONTEXT_LENGTH=50000 (situation_brief + actors_digest are prepended, so the raw report tail is cut). actor.memory truncated to 300-600 chars. persona context truncated to context[:3000]. Bio is re-truncated to 150 chars in reddit JSON. influence free-text is re-derived to a numeric weight via INFLUENCE_WEIGHTS; unparseable influence -> None -> default 1.0. as_of_date is stringified from actors but situation_brief.context/dynamics beyond a 600-char one-liner are not injected into personas. |
| 5. Simulation run + outputs (run_parallel_simulation.py / simulation_runner.py) | Per-platform OASIS DBs + actions.jsonl, run_state.json, and (post-run) run_summary.json {agent_count,total_actions,rounds_executed,peak_round,top_agents,action_volume_by_round,top_posts,optional communities}. With SIM_GRAPH_FEEDBACK, agent actions are written back into the Zep graph (graph memory update). | simulation_config.json + profile files. From agent_configs the run script ONLY reads: agent_id, entity_name (id->name map), influence_weight (weighted activation sampling), active_hours (activation gating). From event_config it reads initial_posts (poster_agent_id+content -> CREATE_POST), scheduled_events (round==loop_round -> CREATE_POST), initial_follows (round-0 follow edges). Platform recsys knobs (recsys_type/refresh_rec_post_count/max_rec_post_len, echo_chamber_strength) only when SIM_WIRE_RECSYS. as_of_date for the sim clock. The actual agent CHARACTER comes from the profile files' persona/user_char, not from agent_configs. | MAJOR LEAK: many per-agent config fields are produced but NEVER consumed by the run -- sentiment_bias, stance, interested_topics, response_delay_min/max, posts_per_hour, comments_per_hour, activity_level have no grep hit in the run script (only influence_weight + active_hours are used). So the researched stance baked into agent_configs.stance does not steer behavior; agent stance only survives via the persona TEXT. Event-level narrative_direction and hot_topics are produced but not read by the run loop. run_summary top_posts are ranked by a content-length APPROXIMATION (no real engagement column), re-deriving 'top' rather than using true interaction counts. interested_topics fed into echo_chamber clustering only affects initial_follows, not runtime. |
| 6. Report agent inputs (report_agent.py) | ReportOutline (5-8 sections) + per-section Markdown via ReAct / native tool calling; final Report.markdown_content; agent_log.jsonl + console_log.txt. | graph_id, simulation_id, simulation_requirement, and (pinned) situation_brief = situation_brief(actors) rendered text, actors (object), sources (list, for [S1]/[S2] index), research_report (md), scenario_label, base_simulation_id. At runtime it pulls live data via tools over the graph + sim: insight_forge, panorama_search, quick_search, interview_agents, simulation_outcomes (reads run_summary), coalition_map, opinion_shift, scenario_diff. | situation_brief() RE-RENDERS actors into a fresh text block (cast/relationships/timeline/hot_topics) truncated to max_chars=6000 -- relationship basis kept but actor memory is NOT included in the report background block. sources index capped to first 40. related_facts for planning sliced to 25. The research_report md is stored on the agent but the background block uses the situation_brief render, not the full dossier, so dossier detail beyond the brief is only reachable if the LLM re-searches the graph. Quantitative claims depend on run_summary, which itself approximated top_posts (stage 5 leak propagates). Section text can be replaced with SECTION_FAILURE_PLACEHOLDER on contamination. |

**Concrete leaks called out:**
- EXTRACTION RE-DERIVE (stage 1): actors.json/relationships/situation_brief are re-extracted by a second tool-free LLM pass from research_report.md, not carried structurally from the research thread. Anything in the prose the extractor misses is permanently lost before any downstream stage sees it.
- sources SPLIT from actors.json (stage 1): extract_json_object pops 'sources' so actors.json has no sources; downstream consumers that read only actors.json (e.g. _actors_to_context, situation_brief) never see source URLs. key_events is duplicated into both actors.json and timeline.json.
- _actors_to_context DROPS fields (stage 2): only actor name/type/role/stance + hot_topics + relationship type are forwarded to the ontology LLM; actor influence, memory, and relationship sign/strength/basis are not passed.
- ontology HARD TRUNCATION (stage 2): entity_types forced to exactly 10 (last 2 overwritten with Person/Organization fallbacks) and edge_types truncated to 10 from the end -- research-confirmed relationship/entity types beyond the cap are dropped. report_md truncated to 50000 chars for ontology analysis.
- actor.type COLLAPSE (stage 3): ACTOR_TYPE_TO_LABEL maps Media/Government/Platform all to 'Organization', losing the real-world type granularity in graph node labels. relationship sign/strength dropped at seed time (only basis becomes the edge fact); influence/stance/memory not stored as node properties.
- NAME-MATCH DEPENDENCY (stage 4): actor->entity enrichment is fuzzy name matching; actors that Zep named differently or never extracted as typed nodes get no stance/memory injection. Entities with only the bare 'Entity' label are filtered out entirely before personas/config, so facts on un-typed nodes never reach an agent.
- CONTEXT TRUNCATION (stage 4): document_text truncated within MAX_CONTEXT_LENGTH=50000 (after brief+digest prepend), persona context[:3000], actor.memory[:300-600], reddit bio[:150]. influence free-text re-derived to a numeric weight; unparseable -> default 1.0.
- RUN-STAGE DEAD FIELDS (stage 5 - the biggest contract leak): agent_configs.sentiment_bias, stance, interested_topics, response_delay_min/max, posts_per_hour, comments_per_hour, activity_level are generated but NEVER read by run_parallel_simulation.py (only influence_weight + active_hours + agent_id + entity_name are used). event_config.narrative_direction and hot_topics are also produced but not consumed by the run loop. Agent stance/behavior therefore only survives through the free-text persona, not the structured config.
- run_summary APPROXIMATION (stage 5): top_posts are ranked by content-length heuristic, not real engagement/interaction counts -- a re-derived 'top' that the report then cites as quantitative fact.
- REPORT BACKGROUND RE-RENDER (stage 6): situation_brief() re-renders actors to a 6000-char text block that OMITS actor.memory and caps relationships at 40/sources at 40; the full research_report dossier is held but the pinned background uses only the brief, so finer dossier detail is reachable only if the LLM re-searches the graph.

## Appendix C — Findings rejected by adversarial verification

29 candidate findings were dropped because an independent verifier could not confirm them against the code:

- **F-0-5 setup.sh unconditionally overwrites upstream provider/middleware modules with the bridge's vendored copies** (research) — The finding is mechanically accurate (setup.sh lines 583/591/601 are unconditional `cp` of bridge copies over the deer-flow checkout) but its characterization is a misreading, so it is not a real defect.

1) README claim is mischaracterized. The "never downgrades the upstream implementation" line in deerflow_bridge/README.md:26 is scoped ONLY to patches/models/patched_minimax.py — it is "DeerFlow 2.0's own upstreamed fix carried verbatim." The finding generalizes this single-file statement into a blanket promise about all overlaid files. The other overlaid files (claude_provider.py, credential_loader.py, loop_detection_middleware.py, SKILL.md) are intentional functional patches (OAuth-preference, macOS Keychain credential source, per-run loop-detection counter reset, overhauled source-tiering research skill) that exist specifically to REPLACE upstream behavior. They are not "verbatim/no-op" files being "silently reverted."

2) The "byte-identical no-op today" premise is false. I diffed all five against the live deer-flow checkout: patched_minimax.py, credential_loader.py, loop_detection_middleware.py, and SKILL.md are identical, but claude_provider.py DIFFERS (deer-flow/.../models/claude_provider.py:270 has a stale docstring "80% of max_tokens" while the bridge copy says "THINKING_BUDGET_RATIO of max_tokens, currently 50%"). The actual constant THINKING_BUDGET_RATIO=0.5 is identical in both, so the bridge copy is the CORRECTED version — the overlay updates, it does not downgrade.

3) The "future upstream fix silently reverted on next ./setup.sh" scenario is not the upgrade path. The deer-flow checkout is gitignored (.gitignore:76 `deer-flow/`), has no .git dir (verified: `deer-flow/.git` does not exist), and is regenerated by setup.sh from the pinned vendored build deer-flow-2.0-m1-rc3/ (or a pinned shallow clone). There is no in-place channel by which "upstream fixes patched_minimax under you" — the file is not independently tracked or auto-updated. setup.sh:509-510 and README:52-53 explicitly document the upgrade procedure: "To update the engine later, delete deer-flow/ (or drop a newer deer-flow-2.0-* vendor dir) and re-run ./setup.sh," at which point the maintainer refreshes the vendored pin and patches together. Re-applying a vendored-patch overlay on top of a pinned engine is the intended, documented behavior of this overlay system, not a latent hazard.

This is intended behavior of a standard vendored-patch overlay against a pinned, disposable, reproducible engine. The "stale overlay clobbers an upstream fix" concern is the normal, well-understood property of every overlay-on-pinned-vendor setup and is explicitly mitigated by the documented "delete + re-vendor + re-run" upgrade flow. Per the rejection criteria (intended behavior / documented guard) this should be is_real=false.
- **F-1-2 Shared per-pipeline `.tmp` filename makes concurrent state writes corruption-prone (save() vs mark_failed() orphan/cancel path)** (orchestrator) — The finding's literal code observations are accurate: PipelineManager.save() (pipeline_orchestrator.py:211-214) and PipelineManager.mark_failed() (236-239) both use a shared, non-unique tmp = state_path + ".tmp" with no per-write lock, and load() (250-251) swallows JSON errors returning None. However, the claimed concurrent-writer RACE is not reachable, so this is not a currently-true defect.

The design enforces a single-writer-per-pipeline invariant via two cooperating guards:

1) The _run thread is the only writer while a pipeline is live, and it removes itself from cls._threads only in its finally block (lines 1770-1771), AFTER every terminal save() (lines 1729/1749/1761). So whenever any save() is mid-write, the thread is still registered and thread.is_alive() is True.

2) cancel() (936-959) and delete_pipeline() (971-987) both hold _lifecycle_lock and call mark_failed() ONLY in the orphan branch, which is gated on cls._threads.get(pipeline_id) being absent/not-alive. With a live _run, cancel() merely sets the cancel event (no file write) and delete_pipeline() returns "still_running". Thus mark_failed() and a live _run's save() are mutually exclusive — the exact "save() vs mark_failed() interleave" the finding posits cannot occur.

3) resume()/continue_to_full()/rerun_from_stage all start a new _run only under _lifecycle_lock after checking live.is_alive() and status=="running" (e.g. 1016-1026), so two _run threads for the same id can never co-exist (the only way two save() calls could race).

4) reconcile_orphans() (764) is invoked exactly once at app init (backend/app/__init__.py:57), before any in-process _run thread exists, and it skips any pipeline_id already in cls._threads. The orphans it marks belong to a dead prior process with no live writer, so the "reconcile at boot vs draining _run" scenario also cannot fire.

The code comments at 756-759, 768-769, and 1017 explicitly document this invariant. The shared .tmp name is safe precisely because the orchestrator structurally guarantees at most one concurrent writer per pipeline state file. The proposed unique-suffix/per-pipeline-lock change is harmless defense-in-depth but fixes a race the current code already prevents, so the finding should be rejected (defect not confirmable as currently-true).
- **F-1-4 timeline.json is loaded, returned and registered as an artifact but never passed to ReportAgent (silently dropped before the report consumer)** (orchestrator) — The finding's mechanical claims are all factually true, but its claimed impact (data loss) is false, making this a non-defect / intended-design observation rather than a real bug.

Verified mechanics:
- pipeline_orchestrator.py:533,540 — timeline IS read from handoff_dir and returned in the research dict.
- :1353 — timeline.json IS registered as a deep-link artifact via add_if.
- :1700-1711 — ReportAgent is constructed with situation_brief(actors), actors, sources=research.get("sources"), research_report, scenario_label, base_simulation_id. There is no research.get("timeline") argument.
- report_agent.py:948-960 — ReportAgent.__init__ has no timeline parameter; grep finds zero "timeline" references in the entire file. So timeline could not be consumed even if passed.

Why it is NOT a real defect (the impact claim is wrong):
- deerflow_research.py:973-977 shows timeline.json is generated by literally copying obj.get("key_events"): the SAME list that line 968 already persists inside actors.json. The inline comment says so explicitly: "promote key_events to a first-class timeline.json (kept inside actors.json too for back-compat)". timeline.json is a verbatim duplicate, not an independent or richer artifact.
- actors.py:384-392 — situation_brief(actors), which IS passed to ReportAgent, already renders actors.key_events into the report background block under "关键时间线（调研实证）".
- Therefore the data in timeline.json is, by construction, a byte-for-byte subset/equal of data already flowing into the report via situation_brief(actors.key_events). The symptom's "any richer/extra entries in timeline.json are lost" is impossible given how the file is produced — there is no code path that can put extra entries into timeline.json that are not also in actors.key_events.
- timeline.json's real purpose is deliberate: a first-class deep-link artifact for the UI/API (research.py:319-326, "T5.2 一等公民时间线") and a clean valid_at source for downstream graph building (comment at line 974). Not passing a redundant copy to the report is intentional separation of concerns, not an accidental drop.

Net: zero actual information loss to report synthesis today; this is intended design, so is_real=false. If the severity were taken at face value it would be P3 cosmetic at most.
- **F-1-5 RUN progress is pinned at 5% for the whole run when total_rounds is 0/unset (no heartbeat fallback)** (orchestrator) — The finding rests on a false premise about the data flow. The quoted code at pipeline_orchestrator.py:1651-1654 is accurate (total>0 -> round-based %, else upd(5, "模拟进行中…")), but the root-cause claim — that total_rounds is "the round count published by the runner" and "never published / lags early," collapsing progress to 5% for the whole run — is wrong.

In simulation_runner.py:374-384, total_rounds is NOT streamed by the running subprocess. It is computed up-front from config (int(total_hours*60/minutes_per_round)) and persisted to run_state.json via _save_run_state(state) at status STARTING, BEFORE the subprocess is even launched (subprocess.Popen at line 452, second _save_run_state at 471). The orchestrator's polling loop only reads the run state after start_simulation() returns (line 1628), by which point total_rounds is already on disk. current_round is the field the runner increments over time; total_rounds is fixed at start.

Defaults guarantee total_rounds > 0: simulation_config_generator.py defaults total_simulation_hours=72 and minutes_per_round=60 (_parse_time_config lines 862-863, _get_default_time_config 830-831), yielding 72. The config-generator's own band computation already uses max(1, minutes_per_round) (line 525) so a zero divisor is impossible there, and runner to_dict guards progress_percent with max(total_rounds,1) (line 172), showing the only real concern was a divide-by-zero edge, not a sustained zero during a live run.

Therefore the symptom (bar pinned at 5% for the entire simulation) cannot occur under any config the system actually produces. The else branch is essentially unreachable in practice. The proposed heartbeat fix addresses a scenario that does not exist. Rejecting as a misreading of where total_rounds originates.
- **F-1-6 TaskManager state is process-local and unbounded; pipeline task progress is lost on restart and never auto-evicted** (orchestrator) — The finding's structural facts are correct (TaskManager is an in-memory singleton dict, cleanup_old_tasks is defined at task.py:172 but never called, tasks are not persisted), but its stated symptom and impact are a misreading of how the named subsystem actually works.

1) The central impact claim — "a frontend polling a pre-restart task_id gets None and cannot show progress" — is false for the research pipeline. ResearchView.vue and api/research.js never reference task_id; verified `grep task_id` over both returns empty. The frontend persists pipeline_id in localStorage (ACTIVE_PIPELINE_KEY) and polls progress exclusively via getPipelineStatus(pipelineId) -> /api/research/status/<pipeline_id> (research.py:274-280), which reads disk-backed pipeline_state.json through PipelineManager.load. That endpoint's docstring explicitly states it survives backend restart ("直接读 pipeline_state.json，可在后端重启后存活"). The finding's proposed fix ("let the frontend fall back to pipeline_state.json when the task lookup misses") is therefore already the implemented behavior, not a gap.

2) The "task_id resolves to None after restart" concern is already guarded everywhere it matters. reconcile_orphans (pipeline_orchestrator.py:786-791) reads tid from disk and only calls fail_task under `if tid:` wrapped in try/except. Every other consumer (lines 1319, 1422, 1750-1754, 1762-1766) guards with `if state.task_id:` plus try/except, and TaskManager.fail_task/complete_task/update_task are no-ops when the id is missing (update_task only acts `if task:`). After restart no background thread survives to dereference a stale id, and resume/continue mint a fresh task_id and reassign state.task_id on the same disk-backed pipeline_id (lines 1045-1049, 1111-1115), so polling continues seamlessly. No None-deref, no broken progress.

3) The only genuinely true residual — cleanup_old_tasks never auto-invoked, so _tasks grows for process lifetime — is benign for this workload. _tasks stores one small dataclass per pipeline/graph/simulation run; these are heavyweight, occasional operations, not high-QPS, so growth sits at the noise floor over realistic uptime. The cleanup method exists and can be wired to a timer if ever needed.

Net: the disk-backed pipeline_state.json + pipeline_id polling already provides exactly the persistence the finding proposes as its fix, and the None-resolution path is fully guarded. The remaining unbounded-map point is a trivial latent housekeeping nicety, not a currently-true defect causing incorrect behavior. Per the reject criteria (misreading + already handled by nearby guards), this is not a real, actionable defect.
- **F-1-9 reconcile_orphans is not synchronized against start()/resume(); a request served during boot could be marked a dead orphan** (orchestrator) — The finding's premise — that a request to start()/resume() could be served concurrently with reconcile_orphans() during boot — is false for this codebase, so the claimed race cannot occur.

Boot/serve ordering (verified):
- backend/app/__init__.py:57 calls PipelineOrchestrator.reconcile_orphans() synchronously inside create_app(). This runs to completion BEFORE the request-handling blueprints are registered (lines 78-82, which include research_bp that exposes start/resume).
- backend/run.py:37 calls create_app() (running reconcile to completion and returning a fully built app), and only THEN line 45 calls app.run(host, port, ..., threaded=True), which binds the listening socket and begins accepting connections.
- The only entry point is run.py via Werkzeug app.run(); grep found no gunicorn/uwsgi/waitress, no FLASK_APP lazy factory, and no separate WSGI module. So no worker can instantiate/serve before the factory returns.

Consequence: Werkzeug does not accept or dispatch any request until create_app() returns and the socket is bound. By that time reconcile_orphans() has already finished. There is no concurrency window in which start()/resume() could insert into cls._threads while reconcile is iterating. threaded=True only affects post-bind request handling; it does not pre-bind the socket.

Independent reinforcement: cls._threads is a fresh class-level dict (pipeline_orchestrator.py:753) in a new process and is populated only by start()/resume(), both of which are unreachable until routes are live. So at boot _threads is necessarily empty and every persisted 'running' pipeline is genuinely a prior-process orphan — exactly the invariant the Chinese comment ('进程刚启动时 _threads 必为空') states, and it is structurally guaranteed by the ordering, not merely assumed.

The proposed fix (run reconcile under _lifecycle_lock before accepting requests) describes a guarantee the code already provides via synchronous ordering; adding the lock here would be a no-op against a non-existent race. The audit itself rates probability 'very low' — in reality it is zero under the actual launch path. This is a misreading of the boot sequence, not a live defect. Per the reject-when-uncertain / already-prevented-by-ordering criteria, is_real=false.
- **F-2-0 delete_graph closes the SHARED embedded FalkorDB connection, killing the whole process's graph DB** (graph-shim) — The finding's architecture and call-chain claims are accurate, but its load-bearing IMPACT claim ("process-wide outage until restart; dead client never re-opened") is contradicted by the actual library behavior, so the described symptom would not occur.

Confirmed accurate parts:
- Shared client: for falkordblite, runtime.py:135-139 passes the single self._falkor_client to every SanitizingFalkorDriver, and graphiti_core FalkorDriver.__init__ sets self.client = falkor_db (falkordb_driver.py:157-159). So all cached Graphiti instances share one embedded client.
- Close chain: _delete_graph calls await g.close() (runtime.py:495) -> Graphiti.close() -> self.driver.close() (graphiti.py:344) -> FalkorDriver.close() (falkordb_driver.py:275-282) which calls aclose() on the shared client. self._falkor_client is indeed never reset to None (runtime.py:490-503).
- Reachability: ZEP_API_KEY defaults to sentinel 'local-graphiti' (config.py:277), the guard at graph.py:598 passes, and the route DELETE /graph/delete -> GraphBuilderService.delete_graph -> client.graph.delete (_GraphNamespace at client.py:135) -> runtime.delete_graph is real.

Why the impact claim is FALSE:
- AsyncFalkorDB defines close() not aclose(), so FalkorDriver.close() falls through to the branch await self.client.connection.aclose(). self.client.connection is the redislite AsyncRedis (async_falkordb_client.py:86), whose __getattr__ proxies aclose to the underlying redis.asyncio.Redis (_client).
- redis.asyncio.Redis.aclose() (client.py:691-709, redis-py 7.4.1) only releases the bound connection and calls connection_pool.disconnect(). ConnectionPool.disconnect() closes existing connections but does NOT make the pool unusable: the next command calls get_connection -> make_connection() to create fresh connections (connection.py:1467-1491). The pool reconnects lazily.
- The embedded redislite server is NOT terminated by this path. Server teardown only happens via AsyncRedis.close() -> _sync_client._cleanup() (async_client.py:97-105), which is never reached. The unix socket keeps listening.
- Net effect: after a single DELETE, the same shared AsyncFalkorDB object simply re-establishes pool connections to the still-running embedded server on the next operation. There is no "dead client handed back forever" and no restart is required. The reported symptom (all subsequent graph operations fail until restart) does not actually manifest.

Residual (genuine but minor) concern: calling g.close() on a shared client is architecturally wrong — it can abort in-flight connections that concurrent operations on OTHER graphs are using at that instant (a transient, recoverable error under concurrency) and causes needless connection churn. That is at most a P3 robustness/correctness nit, not a P1 process-wide outage. Because the finding's stated symptom and root-cause severity are not currently true, I mark it not real.
- **F-2-3 GraphitiRuntime.run() uses no timeout, so an LLM/DB hang blocks the calling thread forever** (graph-shim) — The finding's literal observation is correct (runtime.run() at backend/app/services/graphiti_client/runtime.py:72-74 uses future.result(timeout) with timeout=None, and all callers pass no timeout), but its ROOT-CAUSE claim — that LLM operations "can stall" with "no wall-clock cap either" and "no per-operation deadline anywhere on the sync->async bridge" — is a misreading. The LLM-bound path the finding targets is already bounded:

1. CLI providers (the finding explicitly says "claude-cli/codex-cli can stall"): _chat_claude_cli/_chat_codex_cli call subprocess.run(..., timeout=CLI_TIMEOUT) (backend/app/utils/llm_client.py:392, 447). CLI_TIMEOUT defaults to 180s and is configurable via LLM_CLI_TIMEOUT (llm_client.py:26). On expiry, subprocess.TimeoutExpired is caught and re-raised as RuntimeError (lines 421-422, 470-471).

2. That RuntimeError propagates up the bridge: chat() retries a bounded 3 times with exponential backoff then re-raises (llm_client.py:94-109) -> chat_json (lines 123-136) -> the adapter's loop.run_in_executor(...) await (llm_adapter.py:101-111, where it's normalized to EmptyResponseError/RateLimitError) -> the awaited coroutine -> future.result(None) returns by raising the exception, NOT by hanging. So a stuck CLI provider unblocks the calling thread within ~CLI_TIMEOUT*retries and surfaces a clear error to the orchestrator — the exact opposite of "blocks indefinitely with no recovery."

3. OpenAI-compatible providers use the OpenAI SDK (llm_client.py:74, 333) whose default request timeout is 600s — bounded, not infinite.

The only truly unbounded surface is a hung Cypher query in the embedded FalkorDB/redislite driver, but (a) the finding's evidence, symptom, and proposed fix are all framed around the LLM path ("LLM-bound operations", "CLI providers can stall"), which is already capped, and (b) a hang in an in-process embedded DB is theoretical and unsubstantiated by the code. Per the instruction to reject misreadings already handled by nearby guards/config, this finding does not hold as stated. Default-to-reject also applies given the core claim is demonstrably false.
- **F-2-4 scope='both' silently degrades to edges-only search** (graph-shim) — The finding misreads the call graph. The runtime `_search` (runtime.py:415-426) with `recipe = NODE_HYBRID_SEARCH_RRF if scope == "nodes" else EDGE_HYBRID_SEARCH_RRF` is reachable only through one chain: `_GraphNamespace.search` (client.py:221) -> `_rt.search`. The only caller of `_GraphNamespace.search`/`graph.search` is `ZepToolsService.search_graph` (zep_tools.py:504) and the two calls in oasis_profile_generator.py:336/361. I grepped every `scope=`/`"both"` occurrence in the relevant tree, and NO live caller ever passes `scope="both"` into this path: search_graph callers use the default `"edges"` or explicit `scope="edges"` (zep_tools.py:1044/1059/1309; report.py uses default); oasis (the exact persona/context-building path the finding says loses recall) deliberately issues TWO separate calls, `scope="edges"` AND `scope="nodes"` (lines 340 and 365), which already implements the proposed fix at the call site and retrieves node summaries.

The "both used elsewhere, e.g. zep_tools.py:606/629" cited as evidence is wrong: those two lines are inside `_local_search` (the pure-Python keyword fallback invoked only in search_graph's except branch), which CONSUMES `"both"` itself by running both edge and node branches correctly. They are not callers that forward `"both"` into the runtime `_search`. So `"both"` never reaches the branch in question.

Therefore the claimed symptom (a facade search called with scope='both' silently returns edges-only and drops node summaries) is not currently reachable from any code path. It is at most a latent robustness gap: if someone later passed an unrecognized scope, it would map to edges. Per the rejection criteria (not confirmable as currently-true; intended/unreachable behavior; default to is_real=false when uncertain), this is rejected.
- **F-2-6 set_ontology mutates shared dict from the caller thread while episodes read it on the loop thread** (graph-shim) — The finding describes a real code shape (set_ontology at runtime.py:235-254 mutates self._ontologies on the caller thread, while _add_episode at runtime.py:281 reads it on the background loop thread), but it is not a currently-true defect. Three things make it safe in practice:

1. Per-key isolation: graph_id is always freshly minted per build via uuid4 — `graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"` (graph_builder.py:203; pipeline_orchestrator.py:1503). Two concurrent builds therefore touch DIFFERENT keys in self._ontologies. For the claimed "graph A ontology update overlaps a graph A build from another thread" to occur, two threads would have to share a graph_id, which the design never produces.

2. Strict sequential ordering within a build, with a memory barrier between write and read. Every caller does set_ontology THEN add_batch on the same worker thread (graph_builder.py:138 then :155; pipeline_orchestrator.py:1504 then :1524). set_ontology (a plain sync method) fully returns before add_batch runs, and add_batch reaches the loop via run() -> asyncio.run_coroutine_threadsafe(...).result() (runtime.py:72-74). The dict write happens-before the coroutine is even submitted to the loop, and run_coroutine_threadsafe establishes cross-thread visibility. So _add_episode cannot read a stale value.

3. The "torn tuple" claim is factually wrong for CPython: dict __setitem__/__getitem__ are atomic under the GIL, and the value is a single tuple reference assigned in one bytecode store — there is no partially-constructed/torn read possible.

The author concedes "in the current single-build-per-graph usage it's effectively safe," which matches the actual code. This is a stylistic invariant observation ("the one method not routed through run()"), not a live race or correctness bug.
- **F-3-6 get_graph_data builds an O(N) node_map but never closes/bounds; edges referencing nodes beyond the 2000-node cap lose their names** (graph-build) — The code-level mechanics are accurately described: fetch_all_nodes caps at _MAX_NODES=2000 (zep_paging.py:83,114-117), fetch_all_edges is unbounded (zep_paging.py:129-171), and graph_builder.py:479-481/524-525 builds node_map only from capped nodes and resolves edge endpoint names via node_map.get(uuid, "") with a silent blank default. So for a >2000-node graph the backend payload WILL contain edges with blank source_node_name/target_node_name.

However, the finding's claimed symptom and impact are wrong, which is the deciding factor. The asserted harm is "edges that render with blank endpoints in the frontend graph view." That does not happen. Both D3 renderers filter edges to those whose BOTH endpoints exist in the returned node set before rendering: GraphPanel.vue:400-401 (`nodeIds.has(e.source_node_uuid) && nodeIds.has(e.target_node_uuid)`) and Process.vue:928-929 (identical filter). Out-of-cap edges are silently DROPPED, not drawn with blank endpoints. Moreover the frontend re-derives endpoint names client-side from its own nodeMap with non-blank fallbacks ('未知'/'Unknown'), not from the backend blank strings (GraphPanel.vue:412-413, Process.vue:936-937). The only consumer of the backend source_node_name/target_node_name fields is the edge detail panel (Process.vue:111-115), and it is reachable only for edges that survived the filter — i.e. whose endpoints ARE in the node set — so those names are never blank there.

Thus the user-visible "visually broken / misleading graph with no error" does not occur; the real behavior is graceful edge-dropping already guarded by the frontend filter, with the blank backend fields never displayed. The scenario also requires >2000 nodes, which the finding itself concedes is rare for these reports. This is at most a theoretical data-completeness gap in the API payload, not the rendering defect claimed. Because the asserted defect is not currently true and the real edge case is already handled by existing guards, I reject the finding.
- **F-5-7 _try_fix_json string-newline regex can merge structural tokens and corrupt valid JSON during repair** (personas) — Misreading of control flow. The "aggressive" control-char + whitespace-collapse pass (oasis_profile_generator.py:679-681) cannot corrupt valid JSON and cannot make outcomes worse, for three concrete reasons verified in code:

1) It is unreachable on valid JSON. The entire _try_fix_json repair block runs only after json.loads(content) has ALREADY failed (line 590 -> except -> 604). So the premise "corrupt valid JSON during repair" is false at entry.

2) It cannot worsen the result. The mutation is applied to a LOCAL variable json_str and is accepted only if json.loads(json_str) then succeeds (line 682). On failure, the bare except at 685-686 swallows it and discards json_str; step 6 (lines 688-693) re-extracts from the ORIGINAL `content`, never the mangled string. A failed aggressive pass therefore has zero effect on the returned object.

3) "Merge structural tokens" does not happen. I empirically tested the two regexes: re.sub(r'[\x00-\x1f\x7f-\x9f]',' ',s) then re.sub(r'\s+',' ',s) only collapses whitespace runs. Structural tokens { } [ ] : , and " are never whitespace, so they are never removed or merged. The only lossy effect is collapsing real spaces inside string text — cosmetic, and only when the result parses successfully (a strictly-better outcome than the alternative). Same analysis applies to _try_fix_config_json (simulation_config_generator.py:744-761), whose mangled json_str is also a discarded local on failure.

The single true sub-claim — the partial-extraction persona regex r'"persona"\s*:\s*"([^"]*)' stops at the first escaped quote (verified: a persona with \"...\" truncates) — is intended last-resort fallback behavior. It only runs after full parse + string-newline fix + aggressive fix all failed, and its alternative at that point is the generic default in step 7. Extracting a truncated persona is no worse than that default, so the claimed symptom ("dropping a persona that was only mildly malformed") does not occur: a mildly-malformed doc with embedded literal newlines is exactly what the string-newline fix at line 668 handles and would parse there before any aggressive pass or partial extraction runs.

The root-cause claim is therefore not currently true. Default to is_real=false.
- **F-5-8 as_of_date drives round mapping but is stringified inconsistently; non-string as_of in actors can mis-anchor scheduled events** (personas) — The finding is a misreading of the code on its central claims.

(1) Non-string as_of does NOT mis-anchor. backend/app/utils/dates.py parse_as_of (lines 44-50) explicitly handles datetime, date, and any value via str(value); it is fully type-tolerant. _build_scheduled_events (simulation_config_generator.py:527-528) passes the RAW actors.get("as_of_date") into events_to_schedule, which calls parse_as_of (actors.py:420). So the scheduling anchor is correct regardless of whether as_of is a str, date, or datetime. The str(...) coercion at line 446 is only for the SimulationParameters.as_of_date metadata field; it does not affect scheduling.

(2) The claimed "round->date in the report may not match the sim clock" divergence does not exist. report_agent.py reads actors["as_of_date"] directly (line 1023) — the SAME source the sim uses — and only renders it into a display header ("as-of {aod}"). grep confirms report_agent performs NO round->date mapping at all (only numeric round() calls at lines 86/287). The report never consumes SimulationParameters.as_of_date for any timeline reconstruction, so the two-path divergence premise is false. Both paths trace to the identical source dict key.

(3) Returning [] when as_of is missing/unparseable is documented, intended behavior, not a defect. Both _build_scheduled_events (docstring line 518: "无法解析日期 → []（模拟不变）") and events_to_schedule (actors.py:413-414) explicitly state unparseable date yields [] with the simulation unchanged. The proposed "anchor to earliest key_event date" is a feature enhancement, not a correction of a bug.

Net: the only true sub-claim (no scheduled events when as_of is absent) is explicitly intended and documented; the load-bearing claims (mis-anchoring from non-string types, and report/sim clock divergence) are not supported by the code. Default-to-false criteria apply: misreading + intended behavior + cannot confirm divergence from the code.
- **F-5-9 _build_initial_follows graph-edge fallback can pick the wrong neighbor uuid for an edge** (personas) — The finding rests on a misreading of the data structure. `_build_initial_follows` (backend/app/services/simulation_config_generator.py:488-505) consumes `EntityNode.related_edges`, which are produced exclusively by zep_entity_reader.py (lines 289-303 and 371-385). In those producers an OUTGOING edge dict contains ONLY `{direction, edge_name, fact, target_node_uuid}` with NO `source_node_uuid` key, and an INCOMING edge dict contains ONLY `source_node_uuid` with NO `target_node_uuid` key. The entity's own uuid is never stored in the edge dict at all.

Consequently `other_uuid = edge.get("target_node_uuid") or edge.get("source_node_uuid")` (line 491) is simply the standard idiom to read whichever endpoint key exists for that direction, and it always resolves to the NEIGHBOR's uuid, never the entity itself.

The claimed failure mode is impossible: for an outgoing edge with an empty `target_node_uuid`, `edge.get("source_node_uuid")` returns None (the key is absent), so `other_uuid` is falsy and `if not other_uuid: continue` (line 492) skips the edge. It cannot become "the entity itself," because there is no source_node_uuid key on an outgoing dict and the entity's uuid is never present. The premise "source_node_uuid (the entity itself)" only holds for a flat edge dict carrying both endpoints (e.g. zep_tools.ZepGraphEdge.to_dict at lines 102-103), but that shape does not feed this function.

The direction-based add at lines 501-504 re-derives orientation from the explicit `direction` field and is correct; other_uuid being the neighbor makes both branches correct. The proposed "fix" (select endpoint by direction explicitly) is functionally equivalent to the existing `or` idiom for this data shape and would change no observable behavior. Additionally the whole call is wrapped in try/except at line 375-379. Verdict: misreading of intended behavior; not a real, currently-true defect.
- **F-6-4 Actions sorted by raw ISO timestamp string with no tiebreaker; same-timestamp actions reorder, making pagination and recent_actions non-deterministic** (sim-runtime) — The finding's factual premises are correct but its claimed impact (non-determinism, pagination drop/duplicate, "real correctness wobble") is wrong, so the defect as described is not real.

Confirmed facts: backend/app/services/simulation_runner.py:978 sorts only by `x.timestamp` string descending with no secondary key. The producer backend/scripts/action_logger.py:56 (PlatformActionLogger.log_action) stamps `datetime.now().isoformat()` with no monotonic sequence field. Equal timestamps across the two merged platform files (twitter/actions.jsonl + reddit/actions.jsonl) are possible.

Why the impact claim fails:
1. Non-determinism is false. get_all_actions (lines 941-980) rebuilds the list every call in a FIXED order: it reads twitter/actions.jsonl line-by-line (append-only, fixed order), then extends with reddit/actions.jsonl line-by-line. _read_actions_from_file (853-919) preserves file line order. Python's list.sort() is STABLE, so among equal-timestamp keys the original input order is preserved exactly. I verified empirically: two reads of identical input with duplicate timestamps produce byte-identical output (['T1','T2','R1','R2'] both times). There is no set/dict iteration or hash randomization in the path. Given identical on-disk files, ordering is fully reproducible across reads.
2. "Pagination can drop/duplicate items between pages" is false for the same reason — get_actions slices actions[offset:offset+limit] of a deterministically rebuilt+sorted list (1006-1014). With unchanged files, page slices are consistent. If files grow between requests, that is inherent to any append-only feed and a (timestamp, round_num) tiebreaker would not fix it.
3. recent_actions (api/simulation.py:1830) is filtered to a single round and built deterministically.

The only genuine residual is cosmetic: among same-timestamp actions, the order is "all Twitter then all Reddit" (file read order) rather than true causal interleaving. This is deterministic and harmless. Notably the proposed fix key=(a.timestamp, a.round_num) would NOT help the cross-platform case at all, since same-round same-timestamp Twitter/Reddit actions share both keys — so even the remedy is mis-targeted.

This does not meet P2 (no current correctness/robustness failure; behavior is deterministic). Reject as a misreading of sort/stability behavior.
- **F-8-2 chat_with_tools bypasses retry loop and empty-content guard used everywhere else** (core-utils) — The structural observations are accurate: chat_with_tools (backend/app/utils/llm_client.py:184) calls self._openai_client.chat.completions.create() directly, outside the chat() MAX_RETRIES backoff (lines 94-109), and lacks the empty-content RuntimeError guard that _chat_openai has (lines 338-343). However, the claimed impact is not a currently-true defect once you trace the sole caller, _generate_section_native in backend/app/services/report_agent.py.

Both failure modes the finding describes are already handled by nearby guards:

1. "Transient SDK error / fail hard with no retry": Any exception from create() propagates up, but report_agent.py:1463 wraps _generate_section_native in `except Exception` and gracefully falls back to _generate_section_react, which uses self.llm.chat() (full retry + empty-content guard). A hard SDK failure produces no empty/degraded section and no report crash — it routes to the guarded ReAct path.

2. "content='' and tool_calls=[] -> silent empty section with no retry": The native loop does NOT accept the empty result. At report_agent.py:1577 `if content.strip():` is false, so it does not return the empty section; line 1580 appends a "directly output the chapter body" user message and re-iterates (up to max_iterations=10). If still empty, the line 1583 final fallback calls self.llm.chat(), which DOES have the empty-content RuntimeError guard (llm_client.py:338-343), exponential backoff (lines 101-108), and the exact "disable thinking / raise max_tokens" guidance the proposed fix wants.

So the precise symptom — an empty section silently accepted with no retry — does not occur. The caller's structure compensates for both the missing in-method retry (via fallback to chat()) and the missing empty-content guard (via the falsy-content loop continuation plus the guarded final chat() fallback). This is at most a defense-in-depth refinement (adding retry/guard inside chat_with_tools would be cleaner and avoid the wasted in-loop iterations), not a live robustness defect that degrades forecast output. Per the instruction to reject findings already handled by nearby guards, this is is_real=false.
- **F-8-3 oasis_llm mutates process-global OPENAI_API_KEY / OPENAI_API_BASE_URL** (core-utils) — The quoted code is accurate: _create_openai_model (backend/app/utils/oasis_llm.py:183-191) does write os.environ["OPENAI_API_KEY"] / ["OPENAI_API_BASE_URL"] as a construction side effect. But the claimed impact (concurrent cross-pipeline credential/endpoint bleed) is not currently true, verified three ways:

1) CAMEL captures env at construction, not per-request. backend/.venv/.../camel/models/openai_model.py:116-117 reads OPENAI_API_KEY/OPENAI_API_BASE_URL only inside __init__, then immediately bakes them into self._client/self._async_client (lines 149-160). After construction, requests use the captured clients, never the env vars. The env mutation only matters during the brief construction window.

2) The boost vs non-boost same-process case cannot interleave. In run_parallel_simulation.py, Twitter (use_boost=False, line 1289) and Reddit (use_boost=True, line 1512) build concurrently via asyncio.gather on a single-threaded event loop. But create_model -> create_oasis_model -> _create_openai_model -> ModelFactory.create is a fully synchronous chain with NO await between the env writes (lines 184/190) and the ModelFactory.create read (line 193). asyncio yields control only at await points, so the write-then-construct sequence is atomic per coroutine; the second build cannot corrupt the first's read. ModelFactory.create is a plain def (model_factory.py:113), not async.

3) No cross-process bleed and report_agent is not a victim. Pipeline simulations run in separate subprocess.Popen children (pipeline_orchestrator.py:398, start_new_session=True); env mutation cannot cross processes. create_oasis_model is only called from backend/scripts/run_*_simulation.py, never from the in-process orchestrator (its daemon threads only monitor subprocesses). report_agent builds clients via LLMClient, which passes Config.LLM_API_KEY/Config.LLM_BASE_URL EXPLICITLY as api_key/base_url kwargs to OpenAI() (llm_client.py:69,74) and never reads OPENAI_API_KEY/OPENAI_API_BASE_URL from env. The only other env touch (llm_client.py:371) deliberately strips OPENAI_API_KEY for codex-cli.

So the audit's specific defect — concurrent endpoint/key bleed to other OpenAI-SDK consumers in the process — does not occur under the actual execution model.
- **F-8-7 setup_logger silently ignores requested level on subsequent calls** (core-utils) — The finding's core claim is contradicted by the actual code. It claims "setup_logger(name, level=X) on an already-configured logger is a no-op for X." But in backend/app/utils/logger.py, line 46 (`logger.setLevel(level)`) executes UNCONDITIONALLY and BEFORE the `if logger.handlers: return logger` guard at lines 52-53. So setup_logger DOES re-apply the requested level on every call; the early return only skips re-adding handlers, which is correct (re-adding would duplicate console/file output). The finding's own quoted "evidence" conveniently omits line 46, which refutes the claim. Its proposed fix ("always apply logger.setLevel(level) even on the cached path") is therefore already implemented in setup_logger.

The only partially-true sub-point is that get_logger (lines 91-104) returns the cached logger without setting a level — but get_logger has NO level parameter, so there is nothing to "drop"; returning the existing logger is its documented contract. That is intended behavior, not a defect.

The claimed impact ("verbosity cannot be tuned per-component", "non-default level gives surprising behavior") is unsupported: across the entire Python source tree (backend/app, backend/scripts, deerflow_bridge, tests), no caller ever passes a non-default level — every setup_logger call uses the default logging.DEBUG. The level parameter is never exercised with a non-default value.

Finally, the "always-DEBUG file logging that persists secrets" is caused solely by the hardcoded `file_handler.setLevel(logging.DEBUG)` on line 74, which is entirely independent of the `level` parameter and the caching guard. The finding incorrectly links these.

This is a misreading (omitting line 46) plus intended behavior (get_logger has no level), with an impact path that is not reachable in the current code.
- **F-9-4 export_run dereferences required state keys (project_id, graph_id, simulation_id, report_id) without validation** (scripts) — The subscripts exist as quoted (export_demo_site_data.py:184,198,212,216,223-224 access state["project_id"]/["graph_id"]/["simulation_id"]/["report_id"]), but the finding is not a currently-true defect and misdiagnoses the failure mode.

1) Wrong symptom. PipelineState.to_dict() uses asdict() (pipeline_orchestrator.py:150-152) and the fields are declared Optional[str]=None (lines 133-136). So every saved pipeline_state.json ALWAYS contains all four id keys — present-but-null for stages that haven't run. A subscript on a present-but-null key returns None, not KeyError. The actual crash for a partial run would be TypeError ("os.path.join(..., None)") or passing None to a Zep call, not the KeyError the finding claims. KeyError only arises for a hand-truncated/genuinely pre-schema JSON, which does not exist in this repo.

2) Controlled, non-realistic input. This is a maintainer-only batch script (docstring: "cd backend && uv run python scripts/export_demo_site_data.py"). It iterates a HARDCODED allowlist of exactly 7 pipeline ids (RUNS dict, lines 41-49). I verified all 7 on disk: every one is status=completed with project_id, graph_id, simulation_id, and report_id populated (True/True/True/True). The script never runs against a partial pipeline. The only realistic failure (missing state file) is already guarded by "if not state: ... return" at line 166.

3) Not a runtime/user-facing path. It is a dev tool for publishing docs/demos; no API, no data loss. A loud traceback for a deliberately-misconfigured RUNS entry is acceptable for such a script.

Net: misreading of the failure mode (KeyError) plus a scenario that cannot occur given the curated, all-completed input set and the existing state guard. Defensive .get() hardening would be a minor nicety, not a fix for a present defect. Defaulting to is_real=false.
- **F-9-5 Parallel script's recsys-knob path silently ignores config when SIM_WIRE_RECSYS is unset, so configured recsys/echo_chamber settings never take effect** (scripts) — The gate is intended, documented behavior, and the central claim ("configured recsys/echo_chamber settings are silently dropped") is factually wrong.

1. Intended + documented. build_oasis_platform's docstring (run_parallel_simulation.py:1190-1198) explicitly states the SIM_WIRE_RECSYS gate is by design: "仅当 SIM_WIRE_RECSYS=true 时启用；否则返回 None，调用方退回 DefaultPlatformType（与旧行为逐字节一致）" (only enabled when flag=true; otherwise fall back to DefaultPlatformType, byte-for-byte identical to old behavior). SIM_WIRE_RECSYS is a first-class documented config flag (backend/app/config.py:365) defaulting to false. This is a deliberate opt-in experimental feature gate, not an accidental silent drop. The dataclass comment (simulation_config_generator.py:159) likewise states these knobs only take effect when SIM_WIRE_RECSYS=true.

2. echo_chamber_strength is NOT discarded when the flag is off. It is consumed independently at config-build time by _build_echo_chamber_follows (simulation_config_generator.py:399), which injects same-cluster follow edges into event_config.initial_follows — a graph the simulation ALWAYS consumes regardless of SIM_WIRE_RECSYS. So echo-chamber dynamics do take effect on default runs. The finding's claim "the simulation behaves identically regardless of those configured values" is false for echo chamber. (Side note: line 399 passes twitter_config_strength=None, so it uses a hardcoded 0.5, not the per-platform 0.5/0.6 written at lines 422/432 — but that is a separate minor wiring nuance, not the audited defect, and the audited fix would not address it.)

3. The three pure-recsys knobs (recsys_type, refresh_rec_post_count, max_rec_post_len) are NEVER assigned non-default values anywhere upstream — grep across backend/app and backend/scripts finds zero assignments outside the consumer. They sit at dataclass defaults ""/0/0, which the docstring/comments define as "use platform default." Even with SIM_WIRE_RECSYS=true, the `or` fallbacks (lines 1224-1238) yield identical behavior to DefaultPlatformType for these three. There are no tuned values being silently lost; the "data contract is lossy" premise is unfounded.

4. The proposed fix (warn when knobs are present but flag off) would fire on every single run for fields that always hold default/sentinel values, generating pure noise for a non-issue. The alternative fix (default the flag to true) would silently flip an experimental, opt-in feature contrary to its documented "byte-for-byte identical to old behavior" guarantee.

Verdict: misreading of intended, documented behavior plus a factually incorrect impact claim (echo chamber does apply; recsys knobs are never tuned). Not a real defect.
- **F-10-0 ForecastReport never polls — report stuck on empty/loading state for standalone or slow loads** (frontend) — The finding misreads the architecture. It claims ForecastReport.vue can be mounted with a reportId whose markdown is not yet ready via (a) the standalone /report/:reportId route and (b) a report row that exists before generation finished. Both entry points are false for this component.

1) ForecastReport.vue is instantiated in exactly ONE place: ResearchView.vue:176 (`<ForecastReport :report-id="reportId" />`). A grep over all of frontend/src finds no other usage. There is no "report row" list or any other mount point.

2) The standalone route /report/:reportId (router/index.js:40) maps to ReportView.vue, which renders Step4Report.vue — NOT ForecastReport.vue. The finding conflated ForecastReport with the actual standalone-route component (ReportView/Step4Report).

3) In the only real consumer (ResearchView.vue), reportId.value is set solely at poll line 445 (`if (d.report_id) reportId.value = d.report_id`) from the pipeline status payload. The status endpoint (research.py:274-280) returns the persisted pipeline_state.json verbatim. In pipeline_orchestrator.py, state.report_id is assigned exactly once at line 1721 — AFTER agent.generate_report() returns — and the persisting PipelineManager.save(state) is at line 1729. Every earlier save (e.g. line 1682 during the report stage) runs while state.report_id is still None. ReportManager.save_report (report_agent.py:2873) writes the markdown before line 1721. Therefore reportId is surfaced to the frontend only after the markdown file already exists; ForecastReport never receives a reportId pointing at not-yet-ready content.

The finding even concedes the ResearchView timing holds; since that is the ONLY entry point and the other two claimed entry points do not apply to this component, the described defect is not currently reachable. The component lacks polling, but the data flow guarantees content is present at first fetch, so this is at most a latent robustness gap, not a real current defect.
- **F-10-4 SimulationView: concurrent loadAll() invocations have no in-flight guard (button + auto-refresh + watch can overlap)** (frontend) — The finding's core root-cause claim ("two overlapping loadAll calls with IDENTICAL simId/platform both pass the stale check and both write") is not reachable in the actual code, and its claimed impact scenario is directly refuted by guards the audit overlooked.

File: /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/components/research/SimulationView.vue

Analysis of every trigger pathway:
1. Refresh button (lines 30-39): `:disabled="loading"` (line 33). It CANNOT be clicked while any load is in flight. This directly contradicts the audit's claimed impact ("a user clicks Refresh while the 10s timer fires") — the button is disabled during that load, so no duplicate load and no duplicate network work occurs.
2. Auto-refresh timer: `scheduleAutoRefresh()` (line 484-491) is invoked only at the END of loadAll (line 471), after `loading.value = false` (line 470). It clears any pre-existing timer first (line 485), so at most one timer exists, and the timer-fired load runs only after its scheduling load fully completed. No self-overlap.
3. switchPlatform (lines 474-478): runs only when `platform.value !== next` (line 475 guard), so it ALWAYS changes the platform. Any concurrent load it spawns therefore has DIFFERENT params from an in-flight one.
4. watch(simulationId) (lines 497-502): fires only when simulationId actually changes — again DIFFERENT params.

Therefore every pathway that can produce a genuinely concurrent load also changes a param (switch/watch), which is exactly the case the stale-response guard at lines 420-423 is built to catch and discard. The only same-param re-triggers (button, timer) are serialized by `:disabled="loading"` and end-of-load scheduling. The audit's "identical params, last-writer-wins, duplicate network/work" race cannot occur.

The only genuinely reachable artifact is a minor cosmetic one NOT matching the finding's description: if a user switches platform/sim mid-load, the new load sets loading=true (line 407) while the stale old response, on returning, hits line 420 and runs loading.value=false (line 421), briefly clearing the spinner. This is a transient, non-corrupting UI flicker confined to the param-change path — not the button+timer "identical params" overlap the finding describes. Per instructions, this finding is rejected as a misreading already handled by the existing button disable + stale guard.
- **F-10-5 ResearchView: user can be left viewing a tab that becomes disabled, or auto-switch is permanently suppressed after one manual pick** (frontend) — Verified against the actual code in /Users/rogerlin/Downloads/DeepResearchForecast/frontend/src/views/ResearchView.vue. The finding has two halves; neither is a real, currently-true defect.

HALF 1 — "activeTab can point at a disabled tab" (unreachable / misreading). The tab-enabling flags are monotonic within a single run. In poll() the IDs are only assigned from truthy backend values (lines 443-445: `if (d.graph_id) graphId.value = d.graph_id`, same for simulation_id/report_id) and are NEVER cleared during a run. `dossier.value` is only ever set (lines 451, 457), never unset within a run, and `researchDone` stays true via the `dossier.has_report` clause. The ONLY place the IDs are cleared is resetState() (line 507), which on the very same line (510) also sets `activeTab.value = 'log'` and `userPickedTab.value = false`. The `log` tab is hardcoded `enabled: true` (line 335). selectPipeline() (line 497) and reset() (line 513) both route through resetState(), so they always land on the always-enabled `log` tab. Therefore there is no reachable state in which `activeTab` points at a disabled tab, and the claimed "empty disabled tab body" impact cannot occur. The finding's premise that "a previously-active tab's enabling condition disappears" is false — no enabling condition transitions true→false within a run.

HALF 2 — "auto-switch permanently suppressed after one manual pick" (code-accurate but intended behavior). The watch at 354-358 does guard with `if (userPickedTab.value) return`, and pickTab (line 349) only sets it true (reset solely in resetState, line 510). So within a run, after the user clicks any tab, auto-switch to report/dossier stops. But this is the standard, deliberate "do not steal focus from a user who has made an explicit choice" UX pattern, not a defect. The user is never stuck: every artifact tab (graph/sim/report) becomes clickable the instant its ID arrives, with tab badges and the StageTimeline signaling readiness. Auto-yanking the view away from content the user is actively reading (e.g. the live log) would itself be a UX regression. The claimed impact ("report never auto-surfaces") is the intended consequence of respecting user intent, and is fully recoverable by one click.

Because half 1 is unreachable/a misreading and half 2 is intended behavior with no stuck/broken state, this is not a real defect. Default to is_real=false applies.
- **F-10-6 SimulationRunView reads route.query.maxRounds with parseInt and no NaN guard** (frontend) — The parse itself is real: SimulationRunView.vue:91 does `const maxRounds = ref(route.query.maxRounds ? parseInt(route.query.maxRounds) : null)`, so `?maxRounds=abc` yields NaN, which is passed to Step3Simulation via `:maxRounds` (line 54). However, the finding's claimed *impact* is not realizable in the current code. I traced every downstream consumer in Step3Simulation.vue:

1) Line 402: `if (props.maxRounds) { params.max_rounds = props.maxRounds }` — NaN is falsy, so it is treated exactly like null: the `max_rounds` param is simply omitted from the API call. No corruption, no NaN sent to backend.
2) Lines 22 and 63 (display): `runStatus.total_rounds || maxRounds || '-'` — since NaN is falsy, the chain falls through to `'-'`. It does NOT render the string "NaN".

There is no arithmetic use of maxRounds anywhere; it is only used in truthy-guarded contexts (`if (maxRounds.value)` at SimulationRunView.vue:301, and `||` fallback chains). In every one of those, NaN behaves identically to null. So the "corrupt round math or display 'NaN'" impact does not occur. This is at most a cosmetic/latent code-cleanliness nit (an unnormalized NaN value flowing through), with zero observable defect in current behavior. Per instructions, reject when the issue is effectively already handled by nearby guards (the `||`/truthy guards) and the claimed impact cannot be confirmed from the code. The proposed fix (Number.isFinite + radix 10 + n > 0 → null) is a reasonable defensive cleanup and harmless, but it fixes no actual bug.
- **F-10-9 HistoryDatabase uses non-unique :key (project.simulation_id) and index-based file keys; duplicate/missing ids break list rendering** (frontend) — The finding is speculative and not currently true given the actual data source.

simulation_id uniqueness/presence: In backend/app/services/simulation_manager.py the id is generated as `simulation_id = f"sim_{uuid.uuid4().hex[:12]}"` (line 217) and is used as the per-simulation directory name. list_simulations() (lines 500-516) builds the list by iterating directory names in SIMULATION_DATA_DIR, where each simulation_id IS the folder name. Filesystem directory names are inherently unique, so two rows with the same simulation_id is structurally impossible, and the id can never be null/empty for a listed simulation (a listed sim must have a directory name). The history endpoint (backend/app/api/simulation.py:871-982) just enriches these states and never strips or duplicates the id. So `:key="project.simulation_id"` (HistoryDatabase.vue:24) is keyed on a value that is unique and present by construction — the core premise of the finding (backend may return duplicate/null ids) does not hold for this code path.

Index-keyed file lists (lines 61-62 `:key="fileIndex"` and 137 `:key="index"`): index keys cause Vue keyed-diff problems only when the list is reordered, filtered, or spliced in place while rendered. These file arrays come from the backend as static, read-only data (simulation.py:948-953 builds `files` once as a fixed slice) and are never mutated, reordered, sorted, or filtered anywhere in the component. The card list renders `project.files.slice(0, 3)` and the modal renders the full static array — neither has any reorder/insert/delete path. Index keys for an immutable list are idiomatic and correct; the claimed "unstable across reorders" symptom has no triggering code path.

Additionally, the component already guards the display side: formatSimulationId (lines 346-350) returns 'SIM_UNKNOWN' for falsy ids, and hover state is keyed by array index (hoveringCard === index) which is independent of the :key value, so the described "hover state leaks" mechanism is not how the hover binding actually works.

This is a theoretical glitch (self-rated not-a-crash) that cannot be confirmed as currently true from the code; the assumed-unique-and-present id is in fact unique and present, and the file lists are immutable. Defaulting to is_real=false per the verification rules (misreading / not confirmable / already-safe by construction).
- **F-11-3 setup.sh trims deer-flow/scripts (and other dirs) without verifying the engine does not import from them** (setup-ops) — The finding's core premise — that the hard-coded trim at setup.sh:548-558 may remove something the headless engine imports/reads at runtime, surfacing only as an opaque subprocess failure — is contradicted by the actual code.

Verified facts:
1. The runtime invocation (backend/app/services/pipeline_orchestrator.py:368-407) runs deer-flow/deerflow_research.py with cwd=deer-flow/ using the deerflow venv. The bridge entry point (deerflow_bridge/deerflow_research.py) imports `deerflow` only, which lives entirely under backend/packages/harness/deerflow — a directory the trim never touches. backend/, skills/, config.yaml are all preserved.
2. The trim list (.git, frontend, docs, docker, contracts, scripts, .github, .agent, pr-build, logs, Makefile, *.md, CI configs) contains no Python packages and nothing the engine imports.
3. I grepped the engine package for runtime references to every trimmed top-level dir. The ONLY hit is a docstring in backend/packages/harness/deerflow/subagents/status_contract.py:19 that mentions `contracts/subagent_status_contract.json` as the "single source of truth" for TESTS. The runtime functions (extract_subagent_status, make_subagent_additional_kwargs) hard-code the status values as Python constants and never read the JSON. The JSON is loaded only by backend/tests/*.py and frontend/src — both of which are themselves trimmed/unused by the headless bridge.
4. No runtime open()/read_text/json.load/Path() in the engine references any trimmed dir.
5. Decisive empirical proof: the already-trimmed deer-flow/ checkout in the repo (scripts, contracts, Makefile, etc. all removed) imports cleanly — `python -c "import deerflow, langgraph"` returns "IMPORT OK from trimmed dir", which is exactly the import doctor.sh and the bridge rely on.

The "vendored RC vs upstream clone layout differs" worry is also weak: the vendored RC and the upstream pin are the same project; the vendor dir (deer-flow-2.0-m1-rc3/) has the identical top-level layout the trim targets.

On the "no post-trim sanity check / fails 40 min in" claim: the design already layers two setup-time gates that catch a broken engine. Step 3 (setup.sh:622-635) runs `uv sync` against backend/, and the Next Steps explicitly direct the user to `npm run doctor` (setup.sh:660-663) BEFORE `npm run dev`. doctor.sh runs `import deerflow, langgraph` in the deer-flow venv and exits non-zero on failure — i.e., the very import the proposed fix suggests already exists as the documented pre-run gate. So the "fail at setup, not 40 minutes in" goal is met by doctor, not contradicted.

The `( ... ) 2>/dev/null || true` swallowing of rm errors is cosmetic here: rm -rf of non-existent paths is a no-op success anyway, and even a partial trim leaves backend/ intact (the trim never targets backend/), so it cannot cause the claimed runtime import break.

This is at most a defensive-hardening nicety, not a currently-true defect. Nothing the bridge invokes references the trimmed entries; the trim is verified-safe both by code inspection and by the live trimmed checkout importing successfully.
- **F-12-2 run_summary.json can be aggregated from a partially-written actions.jsonl (no flush/exit barrier)** (x-concurrency) — The finding is a misreading of the write/flush model and the completion semantics; the proposed fix would also break the pipeline.

1) Durable per-entry flush, guaranteed ordering. PlatformActionLogger writes every entry with open('a')->write->close inside a `with` block (backend/scripts/action_logger.py:65-66 for log_action, 115-116 for log_simulation_end). The close flushes each line to the OS. In the simulation loop, ALL of a round's log_action calls happen, then log_round_end, and log_simulation_end is emitted only after the entire round loop finishes (run_parallel_simulation.py:1452-1473 for Twitter, 1681-1703 for Reddit). So for any platform, when a reader observes the simulation_end line, every action line for that platform is already durably written and visible. There is no partial-write window for the "final round's actions."

2) COMPLETED requires simulation_end from every enabled platform. simulation_runner.py:646-648 only sets RunnerStatus.COMPLETED when _check_all_platforms_completed (721-746) confirms both enabled platforms have their simulation_end (twitter_completed/reddit_completed). So COMPLETED implies both actions.jsonl files already contain all actions plus the end marker.

3) Aggregation re-reads the full files independently. write_run_summary -> get_all_actions -> _read_actions_from_file re-reads each actions.jsonl from offset 0 (simulation_runner.py:941-980), not relying on the monitor's tracked position. By the time the orchestrator poll loop sees COMPLETED and calls write_run_summary (pipeline_orchestrator.py:1655-1678), the files are complete. No under-count, no missing top posts.

4) The proposed fix is actively wrong. The runner launches the OASIS subprocess WITHOUT --no-wait (simulation_runner.py:430-438), and the script defaults to wait_for_commands = not args.no_wait (run_parallel_simulation.py:1755). The process therefore deliberately stays alive after simulation_end to serve on-demand Interview commands and never exits on its own. Gating aggregation on process.poll() is not None would hang the orchestrator indefinitely and run_summary would never be written. The simulation_end marker is the intended completion barrier precisely because process-exit is not a valid signal here.

5) No later actions are appended after simulation_end. Interview handling uses _get_interview_result and does not call action_logger.log_action; all log_action sites are inside the simulation loop / scheduled-event injection, all before simulation_end. So actions.jsonl is final once simulation_end is present.

The root-cause claim ("no flush/exit barrier; COMPLETED published while subprocess still writing") is false on both counts: there is a flush (close-per-line), and the writer orders all actions strictly before the simulation_end marker that gates COMPLETED.
- **F-12-4 stop_all / cleanup_all one-shot latches permanently disable later teardown (resume/second run leaks processes and updaters)** (x-concurrency) — The claimed defect depends on a control flow that does not exist in this codebase. The latches (_stop_all_done in zep_graph_memory_updater.py:598-606, _cleanup_done in simulation_runner.py:1289-1301) are set ONLY inside cleanup_all_simulations (which calls stop_all), and that method is invoked from EXACTLY two places (verified by grep across backend/app): (1) the signal cleanup_handler (simulation_runner.py:1432) and (2) atexit.register (simulation_runner.py:1451). Both are terminal shutdown paths.

The finding's premise is a "non-fatal signal that triggered cleanup" leaving the process alive so a later resume/continue/fork can leak. But every signal handler that runs cleanup immediately chains to the original/default OS handler, which terminates the process:
- The server is the Werkzeug dev server (run.py:45, app.run(..., threaded=True)). On SIGINT, SimulationRunner.cleanup_handler calls original_sigint, which is Python's default handler that raises KeyboardInterrupt; that propagates out of app.run() and main() returns -> process exits. The server does NOT resume serving requests after Ctrl+C.
- On SIGTERM/SIGHUP with the typical SIG_DFL original handler, cleanup_handler raises KeyboardInterrupt (simulation_runner.py:1448) or PipelineOrchestrator's handler re-raises via SIG_DFL + os.kill (pipeline_orchestrator.py:855-857) -> process exits.

There is no code path that catches the resulting KeyboardInterrupt/exit and keeps the same Python process alive to start brand-new simulations. The resume/continue_to_full/fork operations (pipeline_orchestrator.py:1009/1069/1135) are in-process thread spawns triggered by API calls during NORMAL operation; they never call cleanup_all_simulations/stop_all and cannot run after the process has begun terminating. The Werkzeug reloader, when used, re-execs a fresh OS process (WERKZEUG_RUN_MAIN), which resets all class-level latches anyway.

The latches are exactly the intended, correct de-dup guard for the legitimate double-invoke during a SINGLE terminal shutdown (e.g., SIGTERM handler runs cleanup, then atexit would otherwise re-run it; or chained SimulationRunner+PipelineOrchestrator handlers). They are never re-evaluated by a still-serving process that subsequently creates updaters/processes. The audit misreads a one-shot shutdown guard as a re-armable per-teardown guard. Default to is_real=false: the scenario (cleanup runs, process survives, new sims created, second teardown skipped) is not reachable here.
- **F-12-5 DeerFlow research subprocess can be orphaned if PID persistence fails in the spawn window** (x-concurrency) — The finding misreads the persistence timing. The PID write is already synchronous and completes before any subprocess output is read — which is exactly what the finding's own "Proposed fix" asks for.

Actual ordering in DeerFlowResearchRunner.run (pipeline_orchestrator.py):
- L398-407: subprocess.Popen returns (child running).
- L411: on_spawn(proc.pid) -> _persist_research_pid (L1399-1401) -> state.research_pid = pid; PipelineManager.save(state).
- PipelineManager.save (L208-214) does a BLOCKING, ATOMIC write: json.dump to ".tmp" then os.replace. It returns only after the file is in place.
- L459: the read loop (for raw in proc.stdout) starts ONLY AFTER on_spawn returns.

So persistence is synchronous and ordered-before reading output, matching the proposed fix verbatim. The "narrow spawn window" between child-start and PID-on-disk is the duration of a single blocking atomic write of a tiny JSON file (sub-millisecond). os.replace is atomic, so there is no torn read; reconcile_orphans (L783-785) reads whatever is on disk and uses research_pid if present.

The try/except at L410-413 only wraps on_spawn and swallows a save exception with a warning. That is intentional (comment L412): a one-off PID-write hiccup should not kill the paid research run. Treating it as fatal — as the finding suggests — would trade a vanishingly rare orphan for a guaranteed-abort failure mode, which is worse.

The only true residual is (a) a crash during the sub-ms atomic write, or (b) a logged-and-swallowed save exception — both extremely narrow, and the primary mitigation the finding proposes is already in place. The optional --out-dir scan is defense-in-depth, not a fix for a present defect. Not a currently-true P2; at most a theoretical P3 hardening idea, so is_real=false.

## Appendix D — Duplicate clusters & EXECPLAN overlaps

**Duplicate clusters** (same underlying defect, fix together):
- F-8-0, F-13-1
- F-0-7, F-13-3
- F-2-5, F-12-8
- F-6-6, F-12-7
- F-8-5, F-13-2

**Already covered by EXECPLAN.md (flagged ↺):** F-1-0, F-3-5, F-4-0

