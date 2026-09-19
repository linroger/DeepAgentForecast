# Judge-loop audit: retained September 18–19 lineage

Audit completed at 2026-09-19T11:54:59.979471+00:00. Scope: saved artifacts for `pipe_1ee2fae33f8c` in the original checkout, and the current isolated worktree. The original run was never resumed, changed, or probed against a provider. Only this document and [judge-loop-evidence.json](judge-loop-evidence.json) were authored by the forensic task. The evidence JSON holds selected numeric aggregates, source paths, line references, timestamps and SHA-256 hashes; it contains no prompt, report prose, judge-gap prose, credentials, fetched URLs, or raw provider payloads.

Documentation/source-anchor refresh: **2026-09-19T12:38:07.436551+00:00**. Canonical status and exact verification receipts are updated in the final section; historical saved facts retain their original evidence identities.

## Finding and accounting boundary

The retained evidence supports repeated judge rejection followed by more research activity. It does **not** support treating every resume as a complete rerun, treating the judge's criticisms as false, or treating the final zero research-token snapshot as zero historical cost.

The latest matching pipeline was created **2026-09-17 13:56 UTC** and completed **2026-09-18 16:03 UTC**, which is **September 19 00:03 in Asia/Shanghai**. It is the only retained pipeline-state lineage in the requested recent window. Its state records **29 resumes** and **15 research-budget epoch directories**. Eight of the fourteen distinct budget databases have nonzero activity; epoch 10 has an empty JSON snapshot and no database. The final handoff identifies `linear` with `extract_only=true`, while the pipeline state has no pinned research engine. No agentic deployment is implied by this historical completion.

The final parent telemetry contains **REPORT only: 4,791,240 tokens in 383 calls**. The report-local 4,791,167 tokens in 382 calls overlap it; the difference is **73 tokens and one call**. Do not add the two. The final research telemetry is zero tokens/tool calls and 213.7 seconds for its terminal recovery snapshot. It excludes the historical research work found below. The saved pipeline research-stage interval is 4,582.9 seconds, whereas creation-to-final-completion spans approximately **26.12 hours** including research, other stages, resumes and downtime. Neither interval is a sum of active model latency.

## Historical model identity and actual judge findings

| Question | What the saved artifacts establish |
|---|---|
| Research model | Alias `glm` in root/track metadata and client initialization observations. |
| Physical GLM 5.3 use | `glm:glm-5.3` in final REPORT telemetry; also separately in simulation metadata. These are different stages. |
| Physical report/actor critic model | **Unrecorded.** Neither retained judge JSON contains model/provider identity. Judge usage rows name phases, not served models. The final report-stage model is not proof of the critic's model. |
| Report verdict | Retained `FAIL`, seven scores with minimum 4 and mean 33/7 ≈ 4.714, and four substantive gap entries. A high numeric score does not erase the gap evidence. |
| Actor verdict | Retained `FAIL`, eight gap entries; low scores include cast-wide accountability 1, ontology readiness 2 and forward-behavior coverage 2. |

The report's first two gaps concern inconsistent scenario probabilities and conflicting baseline projections across sections. Another concerns the requested length. Coarse keyword counts are retained in JSON only as keyword matches, **not** as mutually exclusive semantic categories. The actor gaps include actor/behavior/relationship and source-grounding concerns. Their truth was not replaced with a score-threshold inference.

The current source permits an explicit `DEERFLOW_JUDGE_MODEL` and historically otherwise used the research alias. The saved files do not preserve which setting was active, so an exact historical critic ID cannot be reconstructed honestly.

## Numeric discrepancy: evidence and recognizer limits

A read-only probe verified that the retained judge's **80,730-character report-prefix hash still matches** the current saved 80,914-character report. Thus the numeric observations are in the judged prefix, not solely in an unrelated later report.

Two explicit scenario/probability tables have these numeric structures:

| Location | Heading level / horizon | Probability vector | Sum | Explicit baseline row |
|---|---|---|---|---|
| Heading line 40; table header 42; rows 44–47 | Level 3; forward 2030 projection | 40%, 30%, 20%, 10% | 100% | 40%, index range 190–210 |
| Heading line 587; table header 591; rows 593–596 | Level 3; forward 2030 projection | 55%, 15%, 20%, 10% | 100% | 55%, index range 190–210 |

No explicit dated revision/amendment marker was found in those table/nearest-heading contexts. The common baseline descriptor and matching horizon/range support a concrete inconsistency concern. However, **zero of the four full scenario labels match exactly across tables**, and the other rows describe different alternatives/ranges. It would be incorrect to invent a stable A/B/C/D mapping between all rows or to veto any two unrelated distributions solely because their probabilities differ.

The second gap also has numeric support: overview rows 57 and 67 contain 2027 values **185** and **1.9** for two anonymized metrics, while later row 603 carries explicit baseline columns **175** and **1.1**. Only the year-column header matches literally between these table layouts; the metric/series headers differ. These cross-section discrepancies need resolution, but this audit does not turn a critic's semantic alignment into a universal numeric rule.

The existing `scenario_probability_conflicts()` returned **zero conflicts**. Its implementation requires shallow English `Scenarios` headings, A–F keys in subheadings, and a later compatible probability table. This retained form has level-3 headings and **zero recognized A–F keys** in either table. The modern `evaluate_report()` also returned **no numeric error code** and `scenario_frame_not_supplied`; it checks authoritative supplied frames, which this invocation lacks. It separately failed on **131 citation/binding codes**, so this is not a passing modern publication fixture.

Source boundaries: [deerflow_research.py:10861](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:10861) and [research_quality.py:365](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_quality.py:365). A zero result from these limited recognizers is **not proof that the critic was wrong**.

A safe synthetic regression must make identity explicit: one frame `F`, one as-of date, one horizon, stable scenario IDs A–D, with A=40% in one rendering and A=55% in another; a separate explicit `(frame F, year 2027, metric X)` pair can test 185 versus 175. Negative controls must permit different frames, horizons, dates, conditional scenarios and explicit revisions. The historical document does not provide a literal four-way crosswalk, so source fixes must not silently guess that mapping. Source implementation remains parent-owned.

## Recorded usage: exact arithmetic, limited coverage

The four disjoint log files contain **145 usage rows**. Root observations begin after the track files' research intervals, avoiding a root/track copy double count. Every parsed row satisfies input + output = total.

| Saved log | Usage rows | Input tokens | Output tokens | Logged total |
|---|---:|---:|---:|---:|
| `research_progress.log` | 102 | 13,470,532 | 1,004,600 | 14,475,132 |
| `track_1/research_progress.log` | 33 | 28,880,204 | 1,305,436 | 30,185,640 |
| `track_2/research_progress.log` | 5 | 2,877,338 | 169,992 | 3,047,330 |
| `track_3/research_progress.log` | 5 | 1,552,217 | 127,864 | 1,680,081 |
| **Sum of logged aggregates** | **145** | **46,780,291** | **2,607,892** | **49,388,183** |

This is a reproducible **sum of recorded log aggregates**, not a certified actual-usage total, invoice, or lower bound. Native-turn aggregates account for **46,347,053** of those tokens and lack provider request IDs. Historical overlap within upstream cumulative measurements and missing failed-call usage cannot be eliminated. No complete research cache-read/cache-write partition is recorded; absence is not zero cache usage.

The separately identifiable tool-free critic phases comprise **13 usage records / 575,238 tokens**: six report judges (272,639) and seven actor judges (302,599). Two incremental report-patch calls add 97,322 logged tokens, already included in the 49,388,183 total. The logs show five actor tool-refinement starts, two report tool-refinement starts, three outline invocations, and repeated section-generation invocations. These distinguish direct judge overhead from the larger tool-bearing research and synthesis work surrounding rejection.

The final report-stage telemetry has 91 `cached` observations, which is a call-level field, not a research token-cache partition. No provider invoice or Firecrawl credit ledger was identified. **No actual dollar charge is claimed.**

## Search, fetch and Firecrawl reconciliation

Archived global counters reconcile against their corresponding SQLite databases opened with `mode=ro&immutable=1`. All observed WAL files were empty or absent. We sum each distinct database once; JSON and per-lane views are checks, not additive spending. The current root budget JSON repeats the last epoch and is not another epoch. Application-lifetime lease counters are excluded.

| Epoch ordinal | Database/snapshot created UTC | Search network units | Fetch network units | Payload epoch ID vs directory |
|---|---|---:|---:|---|
| 1 | 09-17T13:56 | 900 | 450 | matches |
| 2 | 09-17T16:48 | 182 | 53 | mismatch |
| 3 | 09-17T19:00 | 143 | 61 | mismatch |
| 4 | 09-17T23:11 | 124 | 42 | mismatch |
| 5 | 09-18T00:54 | 0 | 0 | mismatch |
| 6 | 09-18T01:02 | 342 | 180 | matches |
| 7 | 09-18T02:40 | 360 | 180 | mismatch |
| 8 | 09-18T04:26 | 349 | 180 | mismatch |
| 9 | 09-18T06:17 | 226 | 83 | mismatch |
| 10 | 09-18T07:43 | 0 | 0 | matches |
| 11 | 09-18T07:45 | 0 | 0 | mismatch |
| 12 | 09-18T08:18 | 0 | 0 | mismatch |
| 13 | 09-18T08:40 | 0 | 0 | mismatch |
| 14 | 09-18T09:01 | 0 | 0 | mismatch |
| 15 | 09-18T09:52 | 0 | 0 | mismatch |
| **Total** | | **2,626** | **1,229** | |

Twelve snapshot payloads carry an older epoch ID despite separate databases and creation times. Grouping by that stale string would lose distinct work; summing database, JSON, lanes and application-lifetime counters together would inflate it. Exact database hashes and equality checks are in the JSON evidence.

Other reconciled counters: **4,908 tool-attempt units**, including 2,714 search attempts and 2,194 fetch attempts; 75 negative-fetch suppressions; 61 Firecrawl transport-failure observations; nine Firecrawl circuit openings and 265 skips. Network counter units are not a provider billing ledger and may include reservation/fallback semantics.

The source registry contains **779 non-cache observations attributed to Firecrawl**, corresponding to **779 distinct exact URL hashes** across the retained databases, and **730 cache-hit observations** overall (620 cache-only plus 110 cache observations on Firecrawl-origin rows). Do not count those hits as additional network calls. The retained successful-source rows therefore do not establish duplicate successful Firecrawl fetches of the same exact URL across epochs; repeated research also explored new URLs and incurred failures/fallbacks. Semantically equivalent URLs cannot be inferred from hashes.

Firecrawl's current per-process counter/ceiling is not a durable billed-credit ledger and resets with the process. Its `maxAge` setting is not proof of zero provider charge. See [cached_fetch.py:332](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/cached_fetch.py:332) and [research_budget.py:602](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_budget.py:602).

## What the historical sequence proves

- Seven actor-judge FAIL observations appear in the baseline track; five explicit actor refinement starts occur. Other tracks show completed-pass skips on resume, so “all three tracks reran completely 29 times” would be false.
- Root final report publication fails at **September 18 04:20:54 UTC** (line 1832) and **06:15:03 UTC** (line 2800). New root client initializations follow at **04:26:43** and **06:17:08**.
- The following epochs 8 and 9 together record **575 search network units and 263 fetch network units**. This establishes further retrieval after rejection. It does not reveal whether a human, recovery loop or automation initiated each resume.
- Later recovery uses linear/extract-only behavior; epochs 11–15 have zero search/fetch counters. The final saved zero research aggregate is a terminal recovery observation, not an accounting reconciliation of prior attempts.
- The retained handoff has a report, a judge and a checkpoint, but **no `evidence_synthesis_manifest.json`, `research_contract_manifest.json`, or `evidence_pack.md`**. That missing recovery contract matters to the replay path below.

## Current gate/retry paths and verified changes

Historical evidence above is immutable evidence. The source below is the current worktree and must not be described as already deployed into that historical run.

| Current path | Can it spend again? | Current boundary / interpretation |
|---|---|---|
| Legacy report judge → targeted top-up | Yes: a native tool-enabled turn can search/fetch. An unusable patch falls back to whole-report synthesis, not automatically to all evidence lanes. | [deerflow_research.py:11855](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:11855). Legacy behavior remains bounded by configured rounds; judge FAIL still prevents legacy publication. |
| Legacy actor judge → refinement → dossier synthesis → final rejudge | Yes: the refinement uses native tools. Before a root recovery manifest exists, a subsequent resume previously could re-enter this actor path. | [deerflow_research.py:14135](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:14135) and [deerflow_research.py:14027](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:14027). Deterministic actor coverage remains required. |
| Legacy global synthesis judge/patch and final publication gate | More tool-free synthesis/judging is possible; final FAIL returns failure and preserves the candidate. | [deerflow_research.py:17336](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:17336). An LLM score is still authoritative only on the legacy branch. |
| Parent reuse failure without sealed synthesis inputs | Previously could fall through to full research, especially old states without an engine pin. | **Fixed and independently verified in this worktree:** [pipeline_orchestrator.py:12389](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:12389). Invalid contract, saved report/gate FAIL, or legacy actor FAIL now stores `research_recovery_stop` and stops before lanes/provider children, including missing/short reports. |
| Parent valid evidence-manifest recovery | Retries global synthesis at most twice; it does not rerun source gathering. Invalid manifests fail closed. | [pipeline_orchestrator.py:11188](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:11188). Existing routing remains covered by tests. |
| Parallel baseline actor failure before manifest assembly | A failed baseline cannot seed global synthesis. | [pipeline_orchestrator.py:11666](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:11666). The new resume stop recognizes legacy actor FAIL markers at root and track directories. It does not turn actor failure into success. |
| Modern agentic actor/report advice | Advice alone cannot veto or demand whole-research replay. Targeted section proposals remain bounded and mechanically revalidated. | [agentic_bridge.py:465](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/agentic_bridge.py:465), [research_synthesis.py:478](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_synthesis.py:478), [research_quality.py:430](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_quality.py:430). Actual source/actor/citation/numeric checks must still pass. |
| Linear historical recovery | Its critique is advisory and completed phase caches may be reused. | [linear_research.py:964](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/linear_research.py:964). The historical completion does not prove a modern agentic receipt was produced. |
| Research-budget epochs and failed-attempt telemetry | Fresh epochs are explicit and bounded in current source; process snapshots retain failed usage without re-crediting the same operation. | [pipeline_orchestrator.py:2015](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:2015) and [pipeline_orchestrator.py:1738](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:1738). These source fixes cannot reconstruct absent historical request receipts or billing. |

Independent verification: **58 passed** in [glm-judge-audit-replay-guard02](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-judge-audit-replay-guard02/results.xml): 36 rejection cases across three engines, four failure types and three report states, each attempted twice (**72 blocked resume attempts**); one modern-advisory positive control; and 21 existing parent-routing cases. There were zero network attempts and no tracked-source changes during the run. These are temporary offline fixtures, not a resumed historical pipeline.

A separate current observability change records producer-owned `_judge_model` fields for legacy critics and per-callback provenance for modern advice. Modern review honors `DEERFLOW_JUDGE_MODEL`; observed physical model identity remains unknown when the provider/configuration does not establish it. The [focused provenance check](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-judge-audit-provenance03/results.xml) passed: five critic callbacks record requested/served/provider-response identities, and exact cache replay adds no callback. Legacy metadata additions were source-inspected at [deerflow_research.py:6398](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:6398); this audit does not claim an independent live-provider validation or retrospectively fill historical critic IDs.

## Remaining evidence and acceptance boundaries

1. Preserve the retained scenario and baseline-metric concerns. Both scalar probability sums are valid, while their cross-section consistency is unresolved by the current recognizers. Add only an explicitly bound frame/metric regression or a literal, unambiguous same-frame comparison; do not add a broad semantic veto.
2. Maintain historical unknowns: no exact critic physical ID, no research request-level token ledger/cache partition, no complete failed-send coverage, no Firecrawl billing credits, and no resume initiator trail were recoverable from this scope.
3. Keep successful cache behavior visible: 730 source cache observations and 779 distinct Firecrawl URL identities do not support a claim that all repeated work was duplicate fetching.
4. The current replay guard is a stop-and-preserve fix. It does not repair a rejected report, authorize publication, deploy the code, or resume the original run.

All **46 hashed saved artifacts remained unchanged** after the audit. Arithmetic was checked independently between JSON summaries and database counters; log row sums satisfy input + output = total; overlapping report totals were reconciled rather than added. The companion JSON preserves the exact tested/source identities and supports rechecking these conclusions without exporting private prose.

## Follow-up: canonical scenario-frame integration

Status refreshed at **2026-09-19T12:38:07.436551+00:00** against the production freeze beginning **2026-09-19T12:36:02.767477+00:00**: **bounded offline integration verified; all three independent review findings closed**. This supersedes the earlier in-progress status. Production source files were not edited during this documentation refresh.

The implemented flow makes one cached scenario-plan call, with at most one cached format-repair call, then persists the accepted SC1–SC4 frame before writers run. Every subsequent section/summary writer receives the shared instruction. The full `scenario_contract`—schema, horizon, IDs, names and probabilities—is covered by the quality receipt hash and compared with metadata and the probability projection during consumer replay. Each recognized Scenario/Probability table requires four unique canonical IDs and the bound weights. Legacy non-SC frames retain their existing semantics. Required-frame absence now stops before advisory calls and before publication.

Frozen source anchors: planning/writer dispatch at [deerflow_research.py:6570](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:6570); full-frame parsing at [research_scenarios.py:41](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_scenarios.py:41); workspace loading at [agentic_bridge.py:136](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/agentic_bridge.py:136); receipt construction/replay at [research_quality.py:493](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_quality.py:493) and [research_quality.py:524](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/research_quality.py:524); consumer comparison at [research_quality_gate.py:254](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/research_quality_gate.py:254).

| Exact evidence | Outcome | Established scope |
|---|---|---|
| [glm-scenario-crossreview-red01](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-scenario-crossreview-red01/results.xml) | 3 reproduced failures before the fixes | Horizon/name/deletion binding gaps, missing-plan publication and duplicate canonical rows. |
| [glm-scenario-crossreview-latest04](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-scenario-crossreview-latest04/results.xml) | 3 passed; zero network attempts; no tracked-source drift | Resealed metadata changes are rejected; missing plan permits zero advisory callbacks and no receipt write; duplicate rows fail. Exact source hashes and returned error codes are recorded. |
| [glm-scenario-crossreview-recovery02](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-scenario-crossreview-recovery02/results.xml) | 1 passed; zero network attempts | After one section fails, the accepted frame stays unchanged; outline/planner/successful sections are reused and only the failed section is called again. |
| [glm-standalone-scenario01](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-standalone-scenario01/results.xml) and [completion receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-standalone-scenario01/command-completed.json) | 19 passed; zero failures/errors/skips and network attempts; no tracked-source drift | Real CLI/coordinator/archive control flow with synthetic native/model callbacks: 30 research tasks, peak five, actual multipart synthesis and owned frame, advisory FAIL, mechanical publication, then unchanged report and zero additional research/synthesis/critic callbacks on resume. |
| [glm-native-config-integration02 output](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-native-config-integration02/output.txt), [probe](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-native-config-integration02/probe.py) and installed guard record | Probe reports passed | Actual native client/config/tool-schema integration and late-evidence search/read in a temporary workspace. No model construction or provider calls. One existing LangChain pending-deprecation warning is present. |

The standalone fixture exercises `--no-actors` with synthetic model responses; it does not establish live provider quality or the standalone actor-enabled path. Its exact acceptance entry is [test_agentic_bridge_integration.py:322](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_agentic_bridge_integration.py:322). Permanent canonical regressions are in [test_agentic_scenario_integration.py:21](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_agentic_scenario_integration.py:21). The native smoke uses its JSON success line and installed-guard file as available evidence; it is not represented as a pytest command-completion receipt.

The completed [glm-final-backend02 receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-final-backend02/command-completed.json) and [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-final-backend02/results.xml) establish **5,085 passed, 12 skipped and 11 expected failures**, with **zero unexpected failures/errors, zero network attempts, 216 guarded processes and zero tracked-source drift**. The phase completed at **2026-09-19T12:28:10.651606+00:00**. This result applies to the source snapshot **before** the parent's two lint-only line replacements in `research_budget.py` (lambda variable capture and explicit exception chaining).

The new production freeze is bound to [glm-final-backend03's start receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/glm-final-backend03/command-started.json), beginning **2026-09-19T12:36:02.767477+00:00**. The evidence inventory refreshes only the included `research_budget.py` source hash; its source line anchors are unchanged. **Final backend03 and SDK08 acceptance remains pending the parent's final record.** This document does not extend the successful backend02 result to that later snapshot.

This is a **bounded consistency contract**, not a truth guarantee. A well-formed shared frame may still have mistaken definitions, poorly calibrated probabilities, unsupported assumptions or an unsuitable horizon. Historical labels are not retroactively assigned SC IDs. The retained 185-versus-175 and 1.9-versus-1.1 metric differences still require domain analysis and explicit metric/frame identity. Local conditional or differently dated scenarios must not be equated through an invented label mapping. The historical numeric observations, accounting limitations and unchanged saved artifacts above remain intact.

## Parent final acceptance — 2026-09-19T12:55:32.861546+00:00

The final implementation passed 5,085 backend tests, with 12 skips and 11 expected failures, and 34 native SDK serialization tests. Both runs recorded zero network attempts and no source drift. Their receipts are in [glm-verification.json](glm-verification.json). The parent rehashed all 46 retained artifacts again without finding a change. This completes offline implementation acceptance; it does not certify historical billing, deploy the code, or resume the saved pipeline.
