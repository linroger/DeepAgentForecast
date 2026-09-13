# ASTRA application integration verification

Status: **in progress**. Verified slices are ASTRA-INTEGRATION-01 (shared actor access) and ASTRA-INTEGRATION-02 (complete sanitation before ontology sampling and graph splitting). The ontology artifact-integrity prerequisite03a is also verified; input binding and safe regeneration remain open. The wider application is not certified as fully connected or optimal. This document distinguishes offline evidence from production execution; no live provider or paid pipeline was used.

The authoritative workflow remains **RESEARCH → ONTOLOGY → GRAPH → PREPARE → RUN → REPORT**, coordinated by `PipelineOrchestrator` and durable `pipeline_state.json`. DeerFlow is inside research; `drf2` remains pre-cutover scaffolding.

## Context boundary map

| Boundary | Current contract and source | Verification and remaining work |
|---|---|---|
| Research lanes → unified evidence | Parent synthesis binds reports, dossiers, fetched-source receipts and actor lineage before downstream work. [Research orchestration](../../backend/app/services/pipeline_orchestrator.py#L11356). | Source trace and existing offline producer/contract regressions. Saved history cannot prove live provider health or current research quality. |
| Research → ontology | Dossier/report documents enter an explicitly untrusted, bounded prompt. [Prompt construction](../../backend/app/services/ontology_generator.py#L692). | **Issue 02 resolved offline:** sanitize the complete documents before sampling. Five former xfails and actual primary/fallback prompt cases now pass; [verification](astra-ontology-context-verification.md). |
| Ontology → graph and reuse | Ontology and research feed graph construction, source-bound actor seeds and graph readback. [Reuse guard](../../backend/app/services/pipeline_orchestrator.py#L9912), [ontology branch](../../backend/app/services/pipeline_orchestrator.py#L12643), [graph chunks](../../backend/app/services/pipeline_orchestrator.py#L1269). | **Issue 03 is partial:** [03a](astra-ontology-reuse-integrity.md) validates cached artifact/project consistency and preserves existing proof, including shared fork handoffs. Input freshness and safe dependent invalidation remain03b/03c. |
| Graph/research → PREPARE | Stable actors and report provenance feed sealed actor-context packs, roles, profiles and configuration. [Pack producer](../../backend/app/services/actor_context.py#L1426). | Existing focused cast, provenance, context and config-seal checks pass. Repeated batch preprocessing is a measured candidate, not a completed optimization. |
| PREPARE → agents and shared world | Current-v1 actor role bytes remain authoritative. Public canonical rows supply world/config/event/feed context, then the child injects the world brief. [Shared selector](../../backend/app/services/simulation_config_generator.py#L1649), [injection](../../backend/scripts/run_parallel_simulation.py#L739). | **Issue 01 repair:** deny a globally shared claim when any participating validated pack explicitly denies access to the same normalized claim. Preserve allowed actor-local knowledge and modeler audit. Six shared consumers are exercised with persisted seals. |
| Prepared child → multi-agent RUN | Parent/child seals, cast and provenance guard execution. Reddit verifies the effective composed prompt before reset. [Final Reddit attestation](../../backend/scripts/run_parallel_simulation.py#L4579). | **Issue 07 needs regression:** Twitter's preparation path lacks equivalent final composed-context attestation. This source gap is not a demonstrated saved-run failure. |
| RUN/research → REPORT | Simulation signals and structured research feed report generation; synthetic results retain the diagnostic forecast boundary. [Main report inputs](../../backend/app/services/pipeline_orchestrator.py#L13636), [alternate loader](../../backend/app/services/pipeline_orchestrator.py#L6513). | **Open issues 04–06:** alternate/ensemble constructors omit five structured artifacts, broad TypeError retries can discard them, chat omits constructed research background, and coalition analysis slices already-loaded history at 100,000 actions. |
| REPORT → API/UI | Publication and visualization gates serve completed report artifacts to the frontend. | Frontend unit/build and offline API checks provide bounded local evidence. Rendered current UI acceptance and live end-to-end execution are separate from those checks. |

## Shared access repair and its tradeoff

A literal `actor_knows=false` on a claim or its qualifiers already prevents that row from becoming actor-local knowledge. Previously, public visibility independently admitted it into a world brief distributed to every actor. The first patch removed the denied occurrence; independent review then reproduced the same leak through a public duplicate in another actor's pack.

The final selector resolves denied claim identities across every participating validated pack before emitting shared rows. It uses the existing sanitized, case-insensitive deduplication identity, so actor order and dimensions cannot change the decision. This is deterministic claim matching, not semantic paraphrase detection. Public evidence without a denial retains existing behavior. An actor's authorized private/local knowledge and modeler evidence remain in their sealed packs, which the selector does not rewrite.

The conservative tradeoff is that a public copy is omitted from the global broadcast if another participating actor is explicitly denied that same claim. An actor who is authorized to know it retains the local claim. The selector adds a small in-memory set and final filtering pass. No production latency, token savings or forecasting-quality improvement has been measured.

## September 11 verification history and limits

- Initial permanent actor regression: **8 failed, 4 passed**, with denied markers in six shared channels.
- First implementation: **168 focused cases passed**. Independent review still found the cross-actor duplicate route.
- Added duplicate regression: **4 failed, 12 passed**, covering both actor orders and two dimensions.
- Revised guarded focused gate: **172 passed**. All 16 new actor cases use real synthetic actor-context construction; provider generation is stubbed. Seals are validated and marker presence is observed in world brief, configuration context, event prompt, synthetic seed posts, and both agent system prompts.
- Pending ontology boundary: **5 failed, 1 passed** before marking the five known failures as strict xfails under issue 02. Xfails are outstanding defects, not completed acceptance criteria.
- September 9 broad backend output is **invalid** because the external launcher lacked a main guard. Spawned processes re-entered pytest and overwrote shared receipts. Preserve those files only for diagnosis.
- Corrected launcher recovery: **10 process tests passed**, with a separate drf2 scaffold path failure. The committed scaffold configuration pins the original checkout skills directory while the test expects this worktree. Its README documents the absolute-path setup; this is portability debt, not evidence of authoritative pipeline failure.
- Fresh saved-run refresh on September 11: **77/77 artifacts, 32/32 states, 12/12 bounded logs unchanged**. Pipeline creation dates remain June 8–July 15. The newest selected application-log event is August 18 with no recorded timezone. Historical GRAPH spans can include recovery downtime. No fresh production benchmark or historical occurrence of this actor leak can be inferred.

Exact current gate receipts, hashes and review verdict are retained in `astra-integration-verification.json` and the evidence vault `/Users/rogerlin/.codex/astra-evidence/integration-20260911`. Use [the integration handoff](../handoff/astra-integration/handoff.md) and [the issue ledger](../../astra-improvement-state.json) for the next dependency-ordered slice.

## Final September 11 local gate

The final corrected offline backend suite reports **3,896 passed, 1 failed, 17 skipped and 16 xfailed** in 133.81 seconds. The sole failure is ASTRA-INTEGRATION-11, the pre-cutover drf2 path expectation described above. Five xfails are the explicitly retained ontology regressions; eleven predate this slice. No tests were silently deselected. The final guarded run recorded **69 Python processes, zero blocked network events and no source changes during execution**.

The first broad gate's three DNS failures were repaired by deterministic resolver responses in the existing security tests. Production URL validation and the offline network guard were unchanged. All four touched Python files pass Ruff and compilation. The frontend reports **113 passed**, and its production build succeeds in **2.32 seconds** with output outside the shared checkout. Build and unit checks do not substitute for rendered UI acceptance.

## Paired selector comparison

The same 16 permanent scenarios ran in isolated offline processes, with only the selector replaced by the exact `ef8c419` method AST for the baseline. [Paired comparison](astra-integration-access-comparison.json) and [independent review](astra-integration-access-review.json) preserve method/source hashes and command receipts.

| Observation | Baseline | Current |
|---|---:|---:|
| Permanent actor cases passing | 4/16 | 16/16 |
| Denied claim presence across case/channel pairs | 71 | 0 |
| Positive marker presence across six channels | 24/24 | 24/24 |
| Unchanged context-pack sets | 16/16 | 16/16 |
| Other actor's authorized duplicate knowledge retained | 4/4 | 4/4 |

The baseline has 71 denied observations, not 72: the seed synthesizer's first-public-row-per-actor policy omits the marker in one ordering. The independent final review also passed all 16 permanent cases plus its two original cross-actor reproductions. This is a synthetic correctness comparison, not saved production behavior or measured savings. [Fresh saved-run evidence](astra-integration-run-evidence.json) records the historical limits separately.

## Latest gate and next acceptance boundary

On September 13, issue02 completed with 3,905 backend passes, one known drf2 scaffold path failure, 17 skips and 11 pre-existing xfails. All five ontology xfails have been removed after passing the original scenarios. Independent review passed 69 cases. The [ontology/graph verification](astra-ontology-context-verification.md) records exact comparison, current source hashes and restored-evidence costs.

Next repair issue03: ontology input binding, artifact integrity and dependent-stage reuse. Then close report entry-point/context gaps. Keep the full actor feature false and the application audit in progress until the corresponding acceptance boundaries are complete.


The subsequent September13 integrity slice03a passed3,923 backend cases with one known scaffold-path failure,17skips and11existingxfails. All18 new integrity cases and an82-case independent review pass. Its [verification report](astra-ontology-reuse-integrity.md) records four invalid cached cases incorrectly completing before the fix and zero afterward. Rejected cached ontology now stops safely; automatic regeneration remains unfinished and is not represented as verified.
