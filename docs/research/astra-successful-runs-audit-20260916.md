# All successful saved runs: cost, time and log audit

Audited at 2026-09-16T10:37:09.248018+00:00. Source checkout: `/Users/rogerlin/Downloads/DeepResearchForecast`. Implementation baseline: `b2a2e10`. Read-only; no pipeline/provider execution.

The local checkout contains **26 completed pipelines out of 32 saved states**, created June 8–July 15, 2026. No newer pipeline or changed artifact was found relative to the September 13 receipt. Every completed pipeline and all six stages are represented in the JSON (156 rows). **254 runtime log files / 137,010,574 bytes** were parsed once: 42 global, 151 directly owned by successful runs, and 61 from other saved runs. All 26 successful runs have at least some associated log coverage; stage-specific coverage is explicitly incomplete.

The user’s reported **over 150 million tokens per run** is not disproved by these artifacts. The largest saved parent meter contains 82,983,739 tokens; including two proven separate ensemble reports gives 89,340,365 recorded coverage. Graph, simulation and historical failed attempts are incompletely metered, while synthetic research aggregates cannot be checked for upstream snapshot double counting. Newer or different run storage is required to attribute the user’s exact figure.

## Where the recorded tokens and time go

| July 9 reference: pipe_f23527f7d903 | Saved stage interval | Parent recorded tokens | Interpretation |
|---|---:|---:|---|
| RESEARCH | 2:37:53 | 79,749,778 | 96.10% of parent recorded tokens; 78,156,386 input, 1,593,392 output. Its one meter call is a synthetic aggregate, not one physical request. |
| ONTOLOGY | 0:00:45 | 44,760 | Small recorded contribution. |
| GRAPH | 8:37:31 | unknown | 61.78% of created-to-updated elapsed; raw stage meter missing. 278 of 466 chunks skipped. |
| PREPARE | 0:04:51 | 20,858 | Recorded subset only. |
| RUN | 0:09:51 | unknown | Raw stage meter missing despite 221 platform LLM calls. |
| REPORT | 0:55:37 | 3,168,343 | Parent report entry overlaps report-local 3,168,162 tokens; difference 181. |

Reference created-to-updated time is **13:57:39**. Main stage intervals total **12:26:26**, leaving **1:31:13** outside them. The two extra report IDs are `report_c80ca48e67d1` (2,007,374 tokens) and `report_cf220c2cb963` (4,349,252), giving 6,356,626 separate report tokens. Do not add the parent research subtotal again: that produces 162,733,517 by double counting. This is a possible accounting pitfall, not a conclusion about how the user obtained their number.

Input tokens dominate, but repeated context is not automatically waste. Actual provider cache-read/write counters are absent; tool call/result totals (4,974/4,960) are not cache misses or unique fetched sources. Context loss or blind truncation can create more re-research and lower output quality.

## Every completed pipeline: recorded usage

Each stage cell is its **latest saved attempt** token aggregate. The parent column prefers cumulative_total when present and therefore can exceed the visible stage sum. `unknown` means missing; `0` means an explicitly stored zero, which does not prove zero physical usage. Parent totals are never summed across rows.

| Pipeline / created UTC date | Parent meter tokens | Scope | Research | Ontology | Graph | Prepare | Run | Report |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| pipe_8b47373016f1 / 2026-06-08 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_8bd4981639ac / 2026-06-08 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_66838c1c67de / 2026-06-09 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_2a5b07f9f8c1 / 2026-06-09 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_5985916eb3be / 2026-06-10 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_f01ed9fe06de / 2026-06-10 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_764249df9c38 / 2026-06-10 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_8118312f7b79 / 2026-06-11 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_a90b338fdfa0 / 2026-06-11 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_e2egold02 / 2026-06-13 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_cbbd2d4fe2fa / 2026-06-20 | 8,148,521 | total | 8,092,874 | 18,287 | unknown | 37,360 | unknown | unknown |
| pipe_41522d5d9790 / 2026-06-20 | 0 | total | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_3272477a8dc1 / 2026-06-27 | 72,913 | total | unknown | unknown | unknown | 41,567 | unknown | 31,346 |
| pipe_8bbc44016735 / 2026-06-27 | 0 | total | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_69f88f22ed30 / 2026-06-28 | 12,906,174 | total | 12,867,808 | 38,366 | unknown | unknown | unknown | 0 |
| pipe_a335177097fb / 2026-07-01 | 940,433 | total | unknown | unknown | unknown | unknown | unknown | 940,433 |
| pipe_bf2bb3095d11 / 2026-07-02 | 6,453,039 | total | 5,162,904 | 40,032 | unknown | 17,932 | unknown | 1,232,171 |
| pipe_aa0fb94abe92 / 2026-07-03 | 15,045,707 | total | 13,569,904 | 84,204 | unknown | 15,568 | unknown | 1,376,031 |
| pipe_7ef597666938 / 2026-07-05 | 4,816,286 | total | 3,570,144 | 32,739 | unknown | 24,379 | unknown | 1,189,024 |
| pipe_12251c96e78d / 2026-07-05 | 5,179,254 | total | 3,985,928 | 27,425 | unknown | 13,269 | unknown | 1,152,632 |
| pipe_a8986bffd918 / 2026-07-07 | unknown | missing | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_0f2bee7bd649 / 2026-07-08 | 0 | total | unknown | unknown | unknown | unknown | unknown | unknown |
| pipe_f23527f7d903 / 2026-07-09 | 82,983,739 | total | 79,749,778 | 44,760 | unknown | 20,858 | unknown | 3,168,343 |
| pipe_91aaf91f6392 / 2026-07-12 | 30,322,382 | cumulative_total | unknown | unknown | unknown | unknown | unknown | 2,386,860 |
| pipe_bef6879b2e94 / 2026-07-14 | 5,353,285 | cumulative_total | unknown | unknown | unknown | unknown | unknown | 2,558,618 |
| pipe_0e1b84d2682a / 2026-07-15 | 2,954,750 | cumulative_total | unknown | unknown | unknown | unknown | unknown | 2,940,662 |

Among the 26 successful pipelines, 12 parent meters are positive, three explicitly zero, and 11 absent. Raw GRAPH and RUN token attribution is absent throughout this historical inventory. Application-level `cached` hit counts, retry markers, health calls, checkpoints, report-local records and exact source-key/hash receipts are included per row in the JSON. None is converted to an invented token estimate.

## Every completed pipeline: saved timing and coverage

Times are hours:minutes:seconds. **Stage spans are endpoints, not exclusive runtime**: resume/reuse can preserve old starts and overwrite finishes. The overlap column exposes this instead of adding overlapped spans. Created-to-updated time can include stops and later metadata edits.

| Pipeline | Created→updated | Research | Ontology | Graph | Prepare | Run | Report | Stage overlap | Associated logs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pipe_8b47373016f1 | 0:49:24 | 0:09:26 | 0:01:05 | 0:11:47 | 0:09:26 | 0:00:20 | 0:17:20 | 0:00:00 | 5 |
| pipe_8bd4981639ac | 2:10:31 | 0:08:06 | 0:00:28 | 0:08:48 | 0:09:58 | 1:42:56 | 0:00:14 | 0:00:00 | 5 |
| pipe_66838c1c67de | 1:15:15 | 0:11:59 | 0:00:47 | 0:03:19 | 0:04:26 | 0:29:32 | 0:25:12 | 0:00:00 | 5 |
| pipe_2a5b07f9f8c1 | 2:27:08 | 0:07:25 | 0:00:58 | 0:09:25 | 0:10:39 | 0:37:33 | 1:21:08 | 0:00:00 | 7 |
| pipe_5985916eb3be | 0:52:59 | 0:08:20 | 0:00:44 | 0:04:26 | 0:04:20 | 0:12:01 | 0:23:10 | 0:00:00 | 6 |
| pipe_f01ed9fe06de | 2:51:19 | 1:06:08 | 0:00:49 | 0:10:26 | 0:11:54 | 0:24:02 | 0:57:59 | 0:00:00 | 4 |
| pipe_764249df9c38 | 2:12:43 | 0:58:49 | 0:02:18 | 0:10:20 | 0:06:59 | 0:15:57 | 0:38:21 | 0:00:00 | 3 |
| pipe_8118312f7b79 | 3:15:06 | 0:26:20 | 0:00:33 | 0:10:12 | 0:09:04 | 1:21:57 | 1:07:01 | 0:00:00 | 6 |
| pipe_a90b338fdfa0 | 12:07:08 | 1:04:41 | 0:00:41 | 0:10:26 | 0:18:36 | 0:38:20 | 9:54:24 | 0:00:00 | 5 |
| pipe_e2egold02 | 0:59:50 | 0:00:00 | 0:00:47 | 0:21:29 | 0:06:30 | 0:03:20 | 0:27:44 | 0:00:00 | 5 |
| pipe_cbbd2d4fe2fa | 2:49:07 | 1:27:17 | 0:00:25 | 0:39:36 | 0:04:29 | 0:36:56 | 0:00:24 | 0:00:00 | 4 |
| pipe_41522d5d9790 | 32:40:49 | 31:10:21 | 30:55:53 | 30:55:33 | 30:13:32 | 31:36:24 | 0:01:38 | 122:12:33 | 7 |
| pipe_3272477a8dc1 | 4:26:07 | 2:57:36 | 1:28:55 | 2:22:52 | 0:03:30 | 0:14:21 | 0:16:25 | 2:57:33 | 7 |
| pipe_8bbc44016735 | 2:46:40 | 1:05:55 | 0:39:21 | 1:09:57 | 0:37:57 | 0:25:52 | 0:02:52 | 1:15:16 | 7 |
| pipe_69f88f22ed30 | 2:28:55 | 0:32:22 | 0:02:04 | 0:46:36 | 0:39:00 | 0:26:17 | 0:02:36 | 0:00:00 | 5 |
| pipe_a335177097fb | 26:34:39 | 25:29:08 | 25:07:19 | 25:05:00 | 24:04:09 | 23:25:02 | 10:40:22 | 107:16:21 | 9 |
| pipe_bf2bb3095d11 | 2:07:56 | 0:23:34 | 0:00:40 | 1:06:26 | 0:02:32 | 0:08:55 | 0:25:47 | 0:00:00 | 6 |
| pipe_aa0fb94abe92 | 3:52:19 | 2:12:44 | 0:03:07 | 0:55:02 | 0:02:24 | 0:09:21 | 0:29:39 | 0:00:00 | 7 |
| pipe_7ef597666938 | 1:30:49 | 0:15:27 | 0:00:28 | 0:51:58 | 0:02:46 | 0:04:05 | 0:16:03 | 0:00:00 | 6 |
| pipe_12251c96e78d | 1:20:47 | 0:15:08 | 0:00:39 | 0:27:27 | 0:02:58 | 0:09:11 | 0:24:51 | 0:00:00 | 7 |
| pipe_a8986bffd918 | 71:38:05 | 4:04:05 | 0:00:38 | 7:13:37 | 0:03:59 | 0:00:10 | 4:38:24 | 0:00:00 | 8 |
| pipe_0f2bee7bd649 | 55:18:46 | 5:34:51 | 0:02:15 | 1:37:59 | 0:01:36 | 0:07:35 | 0:12:14 | 0:00:00 | 9 |
| pipe_f23527f7d903 | 13:57:39 | 2:37:53 | 0:00:45 | 8:37:31 | 0:04:51 | 0:09:51 | 0:55:37 | 0:00:00 | 20 |
| pipe_91aaf91f6392 | 5:28:29 | 5:18:21 | 3:46:01 | 3:45:36 | 3:17:22 | 3:15:16 | 3:13:26 | 17:07:33 | 14 |
| pipe_bef6879b2e94 | 8:10:02 | 0:06:03 | 0:00:42 | 1:39:52 | 0:04:32 | 0:48:00 | 0:24:37 | 0:00:00 | 15 |
| pipe_0e1b84d2682a | 7:17:16 | 0:58:29 | 0:01:00 | 0:53:58 | 0:48:41 | 0:11:11 | 0:20:40 | 0:01:00 | 18 |

The latest completed pipeline (`pipe_0e1b84d2682a`, July 15) has **7:17:16** created-to-updated elapsed, but only a 2,940,662-token REPORT latest attempt and 2,954,750 cumulative meter. Its research snapshot is 763,197 tokens, which cannot safely be added to a cumulative meter without complete attempt lineage. RUN has 212 health calls with no token snapshot. The RESEARCH/ONTOLOGY endpoint intervals overlap by 60.2 seconds. A newer full run cannot be reconstructed from these counters.

## Logs, recovery and wasted-work evidence

The complete global-log scan found **16,576 quota/429 marker lines**, **41,718 retry marker lines**, **616 timeout marker lines** and **878 graph-skip marker lines**. These overlap, can repeat the same exception, and mostly lack a literal run ID on the same line. They are neither distinct requests nor additive waste estimates. Each file has a hash and selected counts; only explicit matching IDs are attributed to a pipeline. Hook chats and private action/provider transcript payloads are excluded from retry interpretation.

9 report artifacts have mismatched tool/section attribution: their concurrent section telemetry is empty while logs contain calls. For example, the high-token main report records 0 tool calls despite 57 logged calls, and the latest report records 0 despite 37. Current `report_agent.py` deliberately skips per-section metering after concurrent generation; its shared counter does not provide accurate concurrent section attribution. Repeated parameter fingerprints indicate potential reuse opportunities, not proved cache misses.

The July 12 pipeline retains 30,322,382 cumulative tokens versus a 2,386,860 latest attempt. Its stage endpoints overlap heavily and cannot be summed into elapsed time. The July 14 completed pipeline retains a failed prior report with 2,794,667 tokens plus the final 2,558,618: 5,353,285 cumulative. These records justify durable completed-work reuse, but do not prove every earlier attempt was unnecessary.

## Current implementation versus historical behavior

| Mechanism | Current verification | What remains |
|---|---|---|
| Accounting, growing snapshots, retry ownership, compaction provenance, actor denials, ontology sanitation/integrity | ASTRA worktree already contains the corresponding repairs; SDK request-local retries are disabled and durable accounting is present. Historical logs predate these changes. | Production parity is unverified. Original checkout lacks usage_ledger.py and differs in orchestrator/LLM/meter code. Do not call the existing repairs new omissions or claim they improved old runs. |
| Graph batch partial success | Current runtime still gathers the complete batch before returning; GraphBuilder treats thrown batch as all failed. Reproduced offline. | Durable episode receipts, bounded episode deadlines and correct resume identity. |
| Report recovery and section attribution | Concurrent sections are generated before the later saving loop; concurrent per-section metering is intentionally disabled. | Save each validated section as it finishes, bind it to inputs, restore only valid work, and count per-worker operations. |
| Graph full-edge reads | Every _list_edges page calls a complete edge fetch/parse then slices it. | One bounded immutable snapshot per full read/cursor, retaining the no-WHERE database workaround. |
| Ontology downstream freshness | Integrity fence exists; research-input binding and durable dependent invalidation remain open. | Treat this as a prerequisite to safe graph/report reuse across changed research and scenario forks. |

## One recommended next workflow change

**Implement ASTRA-06 durable per-episode graph completion, after defining its ontology/input identity.** It targets the largest well-attributed wall-time stage and preserves expensive evidence. The new offline reproducer ran the actual `_add_episodes_concurrent` method with one fast fake commit and one hung episode. Timeout released the lock correctly but returned no receipt for the fast commit. Repeating the batch sent the fast extraction twice and produced three fake commits for two logical episodes. No provider or database was used; this is a current mechanism proof, not a historical savings estimate.

A passing regression must persist each accepted episode receipt before a sibling timeout, resume only incomplete work, reject changed ontology/content/reference-time/policy, reconcile ambiguous writes without duplicates, and preserve actor-seed validation. Measure distinct physical calls per accepted chunk and wall time on the same synthetic interruption schedule. Increasing concurrency is not a safe substitute: the current runtime explicitly documents concurrent entity-dedup races.

## Evidence and reproduction

- [Full 26-run / 156-stage evidence, aggregate logs, keys and hashes](/Users/rogerlin/.codex/astra-evidence/experience-20260916/cost-time-audit.json)
- [Reproducible read-only audit script](/Users/rogerlin/.codex/astra-evidence/experience-20260916/audit_cost_time.py)
- [Audit finalization and arithmetic checks](/Users/rogerlin/.codex/astra-evidence/experience-20260916/finalize_cost_time_audit.py)
- [Graph partial-timeout reproducer](/Users/rogerlin/.codex/astra-evidence/experience-20260916/graph_batch_timeout_repro.py)
- [Graph reproduction receipt](/Users/rogerlin/.codex/astra-evidence/experience-20260916/graph-batch-timeout-reproduction.json)
- [September 13 baseline hash refresh](/Users/rogerlin/.codex/astra-evidence/experience-20260916/saved-refresh.json)

Current source anchors:

- [backend/app/services/graphiti_client/runtime.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/services/graphiti_client/runtime.py:224), relevant lines 224, 1072, 1092, 1130, 1829.
- [backend/app/services/graph_builder.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/services/graph_builder.py:2332), relevant lines 2332, 2351, 2372.
- [backend/app/services/pipeline_orchestrator.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/services/pipeline_orchestrator.py:7328), relevant lines 7328, 7416, 10594, 12723, 12789.
- [backend/app/services/report_agent.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/services/report_agent.py:10427), relevant lines 10427, 10479, 10555.
- [backend/app/utils/llm_client.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/utils/llm_client.py:479), relevant lines 479, 582, 626.
- [backend/app/utils/usage_ledger.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/utils/usage_ledger.py:1), relevant lines 1.
- [backend/app/utils/telemetry.py](/Users/rogerlin/.codex/worktrees/drf-astra-improvements/backend/app/utils/telemetry.py:1), relevant lines 1.

Validation: all 26 completed IDs are covered exactly once; every row has six stages; all current and stage input+output token sums match saved totals; the reference 89,340,365 arithmetic matches; source hashes and line anchors are present. No saved artifact, provider state, service, credential or pipeline was changed. The audit is not an end-to-end quality benchmark or a claim of global/Pareto optimality.
