# Agentic research implementation

Status: Complete for source implementation and offline acceptance; deployment/live evaluation and the broader rearchitecture remain outside this unit. The user explicitly requested building this now; the prior rearchitecture plan is already approved. This research feature takes priority over the previously queued ontology slices.

## Request and constraints
Preserve adaptive agent judgment, persist mid-flight discoveries, run up to five useful concurrent research workers per research phase under one shared provider envelope, improve context and memory management, and make LLM critique advisory while keeping deterministic publication checks. Continue in the isolated rearchitecture worktree; do not modify original checkout, deploy, resume a real run, call paid providers, or change credentials. The quoted performance numbers are unverified historical claims, not an accepted benchmark.

## Design
1. Retain the current native tool-using research and actor-intelligence production paths, compaction receipts, caching, source receipts and parent state authority. Add an adaptive phase coordinator behind a pinned engine choice for new pipelines. Keep legacy resumptions compatible.
2. Give each phase five distinct scoped investigators (up to five simultaneous useful tasks). Use isolated task threads, a shared model admission limit of five, bounded deadlines and cancellation. Avoid multiplying five workers by outer lanes and nested subagent fanout.
3. Store full task outputs, observed tool results, discovery proposals and immutable task-input identities in a durable run workspace before marking work complete. Detect mismatched/tampered inputs; resume only incomplete work. Retain raw evidence separately from bounded prompt views.
4. Route relevant archived evidence into each worker's prompt under an explicit context policy. Preserve source identities, contradictions, numbers and unresolved questions. Native tool-result offloading, compaction and prompt caching remain active; do not claim these were absent.
5. Persist discoveries as soon as observed, deduplicate them, and admit focused follow-up tasks within shared budgets. Research sufficiency may stop unproductive extra work; five is a concurrency envelope, not an obligation to invent tasks.
6. Separate an immutable mechanical quality receipt from advisory LLM scores. Keep exact report/source binding, citation resolution, structural checks and required actor contracts mandatory. A numeric score or model FAIL alone cannot force replay. Preserve accepted task/phase evidence when final synthesis/repair fails.

## Acceptance
- Barrier-controlled fixtures demonstrate five active workers and never six; resumes and cancellation remain bounded.
- A crash after one task/discovery persists reuses the completed task and resumes the remaining queue without duplicate retrieval. Question/model/policy changes and damaged records fail before provider work.
- Long-source tail evidence remains retrievable after context reduction and restart; prompts stay within configured working limits and archival failure never silently deletes evidence.
- New discoveries change the admitted work queue and survive process restart.
- Advisory FAIL with sound mechanical evidence can proceed; malformed citations, inconsistent numeric scenarios, missing required actor evidence or corrupt receipts cannot.
- Full bridge → orchestrator producer/consumer fixtures cover fresh, evidence-only, synthesis-only and resume behavior with zero external calls.
- Independent review, focused red/green tests and appropriate full regression checks precede completion claims.

## Current findings
The existing linear-v2 module already has mini-agent loops and increased character limits, but weak durability/identity, context eviction and mode/provenance parity remain. Native bridge configuration already externalizes large results, enables prompt caching and archives compaction receipts. Output hashes alone are not freshness proof.

## Progress and decisions
- Baseline: clean branch at 22238a2; RX00/RX01 previously verified.
- Current work: implementation interfaces and source-boundary integration.
- Publication remains blocked by previously rejected Git credentials; do not retry unchanged auth.

## Verification and remaining work
Component acceptance has run: scheduler/store/context combined 127 checks on the earlier store revision; archive slice 87; admission 50; quality and synthesis have separate receipts. These establish component behavior only. Parent producer/consumer integration, final current-source regression, and independent integration review remain required. No production speedup or model-quality claim will be inferred from offline tests.

## Updates
- 2026-09-18T22:26:36.527285+00:00: Created the current feature brief and acceptance plan before code changes.


## Archive slice handoff — 2026-09-19T01:49:37.706269+00:00

The archive slice is complete within its assigned scope. The overall agentic feature and parent integration retain the status recorded above.

Archive-owned implementation files are `deerflow_bridge/research_archive.py`, `deerflow_bridge/patches/middlewares/tool_output_budget_middleware.py`, `deerflow_bridge/cached_fetch.py`, and `backend/tests/test_agentic_evidence_archive.py`. This final correction changed only the archive adapter and its test file, plus this appended handoff record. The archive worker did not edit parent bridge/config/orchestrator files or alter the Git index, commit, or push. Existing concurrent changes are preserved.

Exact-body attachment uses URL plus content SHA-256 and verified managed artifacts. It preserves every caller provenance/status/provider/receipt/cache field, including canonical native fetched rows backed by archived cache bodies. Cited/snippet rows and URL/hash mismatches receive no attached body. No archive reference is added to caller rows; `source_rows()` retains original producer metadata.

Attachment calls `workspace.events('source:' + sha256(url))` once per distinct eligible URL and reuses bodies for duplicate rows. Empty, cited/snippet, and malformed-hash input rows cause no archive reads. Source replay uses `workspace.events_by_kind('native_source')`, retaining the existing native-source schema/producer/content/receipt checks. Neither export path calls `workspace.snapshot()`. The workspace owner supplied the indexed event-kind API and its compatible database-index migration in their separately owned files.

The actual current native exporter was exercised without editing it: repeated `export_fetched_sources_for_manifest(include_content=False)` calls perform no archive reads; the final full export attaches the complete CRLF-preserving body, including its late tail, while retaining the native receipt fields.

Verification used the existing interpreter and guarded offline runner with fresh phases. `evidence-archive-indexed-red-11` reproduced six full-scan failures; `evidence-archive-indexed-green-12` passed 59 archive checks. `evidence-archive-source-query-red-13` reproduced the source replay snapshot failure while its metadata-only native export control passed. Final `evidence-archive-final-indexed-green-14` passed 87 checks (61 archive and 26 existing cache/budget regressions), with 0 network attempts across 2 guarded processes. Evidence is `/Users/rogerlin/.codex/rearchitecture-evidence/20260919/evidence-archive-final-indexed-green-14/results.xml`, `/Users/rogerlin/.codex/rearchitecture-evidence/20260919/evidence-archive-final-indexed-green-14/command-completed.json`, and `/Users/rogerlin/.codex/rearchitecture-evidence/20260919/evidence-archive-final-indexed-green-14/archive-source-hashes.json`.

Runtime limits remain explicit: middleware tests load production overlay logic using framework-shell stubs because this interpreter lacks LangChain. No deployed native-client/provider/service validation or performance benchmark is claimed. The I/O improvement is supported by lookup-count and forbidden-snapshot acceptance checks. Parent source admission, retrieval provenance, configuration/overlay synchronization, and whole-pipeline acceptance remain parent-owned.

Independent workspace-owner recheck (2026-09-19T01:59:47.756333+00:00): `research_archive_indexed_readonly_review_15` passed with no findings in this boundary. Its probe forbade snapshots, task reads and workspace writes, verified one source-event query for duplicate native rows and one indexed kind query for source replay, and compared database state before/after to confirm no task/event/artifact/discovery/status mutations. It also confirmed the phase-14 87-pass, zero-network receipt. No review edits were made.

## Integration resumed — 2026-09-19
The approved feature remains in progress on codex/workflow-rearchitecture-2026-09-19. Native admission and quality consumer owners resumed their disjoint files; synthesis owner is implementing bounded section repairs; a test owner is exercising parent routing. Parent owns CLI/adaptor integration and acceptance. No new approval is required. Full source retention and prompt selection are separate; native offload/compaction/caching already existed and are being connected to durable phase recovery, not invented anew. Legacy resumptions retain their pinned engine.

## Parent integration ledger — 2026-09-19
The real CLI/coordinator/archive fixture completed 30 tasks (25 baseline plus five discovered follow-ups), observed peak concurrency five, retained full source bodies, and reused all 30 without new calls on resume. Publication red02 reproduced missing sources.json in no-actors mode; green03 passed five scenarios after persisting the verified ledger. Actual multipart tests passed successful-section reuse after a failed section, complete reuse, and timeout ownership retention. Parent/adjacent integrated01 passed 181 checks and one existing skip. These remain scoped offline results, not production measurements.

New boundary defects found and corrected: modern citation cleanup must preserve invalid markers; the citation index must use the same canonical URL identity as final sources; full source content must survive manifest seeding; raw streamed chunks must retain whitespace/CRLF; auxiliary actor passes need durable evidence and search-receipt replay. The targeted repair fixture verifies only its named section changes; successful repair passes fresh mechanics. The ongoing full regression is diagnostic because sibling source edits are still finishing. Final stable-source verification, independent review and commit remain open.

## Independent integration review — 2026-09-19
Sagan reproduced three P1 boundaries and one recall issue: persisted source rows were incorrectly treated as transient collector rows; a complete actor cache hit failed to restore its thread/search receipts; duplicate identical tool outputs made partial-resume block IDs ambiguous; derived context IDs could not be passed to read_evidence. Parent corrected all four. Persisted citation positions are now explicit, actor cache receipts are restored and validated, working views deduplicate by archived content identity, and every omitted-range reference points into an actual sanitized archived string. The reviewer is adding permanent reproductions and rereviewing.

Lorentz closed the native admission review after fixes for stop-during-reservation, blocking async accounting and cancellation during exceptional cleanup. Component final127 passed; the async accounting thread retains ticket context and drains before releasing capacity. The native actual-client configuration smoke passed seven checks under the network guard, with one pre-existing LangChain pending-deprecation warning. Full regression02 is running after the substantive integration fixes; minor exception-chaining/zip-strict lint changes are recorded separately.

## Callback ownership closure — 2026-09-19
Goodall reproduced unregistered time in native-agent preparation and bare-model construction after the waiting controller timed out. Parent wrapped the complete submitted callbacks. Singer then reproduced a pre-entry race: a delayed worker could register under the next invocation. Ownership is now reserved before executor dispatch and bound to that exact owner through Future completion or cancellation; the worker cannot join a newer invocation. All coordinator, advisory/repair, multipart and dual-track dispatches use the shared helper. Bare/package workspace imports now share canonical control-error classes so archive failures cannot degrade to ordinary advisory unavailability. Focused dispatch integration passed 287 checks.

Full regression02 passed 4,833 tests with 12 skips, 11 expected failures and zero network attempts across 208 guarded Python processes. It is superseded for final-source claims because the final callback-ownership fixes followed it. Source is frozen pending the three independent review regression fixtures and final full acceptance. New files pass the repository Ruff configuration; bash syntax and changed Python parsing checks pass.

## Final acceptance and next shift — 2026-09-19T10:51:16.874535+00:00
The frozen-source final run passed 4,844 tests, with 12 skips, 11 expected failures and 21 warnings in 266.37 seconds. There were zero network attempts and no tracked-source changes. All 32 changed Python files pass Ruff and parsing, setup.sh passes bash syntax, and the actual native client/config smoke passes seven checks. Independent review findings are closed with permanent regressions. The exact source hashes, scope and evidence paths are recorded in ../../research/agentic-research-20260919/verification.json. The new feature-list entry is passing; no earlier incomplete feature was changed.

The source feature is complete. It has not been deployed or exercised against paid providers. Remote publication remains blocked by the previously rejected credentials and was not retried. No live research pipeline was resumed. The broader project remains in progress; next approved source unit is RX-02 ontology input binding and strict publication, followed by RX-03 owned invalidation.

Next-session prompt: Read this handoff, docs/research/agentic-research-20260919/README.md and verification.json, then docs/handoff/workflow-rearchitecture-20260919/handoff.md and PLANS.md. Preserve the durable agentic research, source/actor identities and legacy engine pinning. Continue the approved RX-02 ontology slice with producer/consumer regression evidence. Do not infer provider performance or deploy/resume a paid pipeline from offline acceptance. Retry remote publication only after authentication changes.
