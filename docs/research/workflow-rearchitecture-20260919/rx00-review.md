# RX00 independent integration review

**Disposition: one important merge regression requires correction.** The explicit deployment and SDK conflict resolutions preserve both parents' intended changes. A deterministic offline probe found that the merged dual-track timeout path can conceal an ASTRA compaction stop from the parent.

Review date: 2026-09-19, Asia/Shanghai. Workspace: `/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919`.

## Scope and evidence

Reviewed working-tree source and diffs against **main `ec29ab1`** and **ASTRA `019a7e4`**, with common ancestor `4be3ce4`. Planning HEAD was `d45fcc1`; `MERGE_HEAD` was `019a7e4`. The two conflict files remained unmerged in the index but their working-tree contents contained no conflict markers. This review makes no index changes.

The behavioral review focused on `deerflow_research.py`, `pipeline_orchestrator.py`, and `llm_client.py`, tracing their linear-engine, compaction, actor-projection, provider-admission, and accounting connections. Related helper/test source was inspected where needed. This is not an exhaustive review of all imported ASTRA changes or all existing main debt.

No production source, tests, configuration, saved runs, or deployment files were changed. The only persistent review artifact is this document. Offline probes used temporary synthetic handoff directories, mocked research/provider work, and a Python audit hook rejecting network connection/name-resolution attempts. No services or providers were called. No broad suite was repeated.

## Critical issues

None found in the reviewed integration paths. This does not constitute an all-repository security or correctness clearance.

## Important issues

### RX00-R1 — P1: a timed-out actor worker can lose the stage-wide compaction stop

**Primary location:** [deerflow_research.py:16659](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:16659), including the non-waiting executor shutdown at line 16671. **Observed failing exit:** [deerflow_research.py:16769](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:16769), which writes ordinary actor-coverage failure metadata and returns 2 at line 16790.

Main's GLM reliability change catches Track B's wall timeout, clears `dossier`, and continues while its worker is still alive (`shutdown(wait=False, cancel_futures=True)` does not cancel running work). ASTRA's common `_raise_if_compaction_stopped()` check runs once after this block, at line 16707. If the actor worker encounters a compaction failure after that check, during foreground evidence/coverage finalization, the existing early failure return bypasses the exception handler that encodes the typed stop. `write_meta()` only checks the latch for `status == "completed"` at lines 16234–16237; it does not protect this `status == "failed"` exit.

**Reproduced consequence:** the process has an actual latched `ResearchCompactionError("archive_write_failed", "actor-thread")`, but exits **2**, with no `meta.compaction_stop`. The parent recognizes compaction stops only from current-attempt typed metadata or reserved exit **4** ([pipeline_orchestrator.py:1868](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:1868)). It consequently raises an ordinary subprocess failure at line 2568. In outer research fan-in, that takes the discardable-lane branch instead of setting the shared stop/cancelling queued lanes ([pipeline_orchestrator.py:11538](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:11538), lines 11563–11580). Additional queued research work is therefore not fenced by the compaction-failure list. The probe establishes lost stop authority, not successful publication of an invalid final report.

**Why this is an integration regression:** ASTRA waits for the actor future and exits the executor context before passing its post-track stop check. Main introduced bounded joins and abandonment but did not yet have ASTRA's typed-stop contract. Their combination introduces a new interval in which a real stop can arrive after the check and be discarded by an ordinary terminal return. This finding is separate from main's pre-existing linear-engine limitations.

**Deterministic reproduction performed:**

1. Load the actual `main()` function from each source version into a separate module; keep CLI control flow and metadata writing intact. Use `--model offline --evidence-only`, enable the actor track, and force two lead slots.
2. Stub Track A to return a synthetic evidence pack and stub fetched-source export to return one synthetic source. Stub client construction, skill telemetry, and provider work.
3. Run a real local `ThreadPoolExecutor`. Track B waits on a `threading.Event`; when released, it latches `archive_write_failed` and raises the typed compaction exception.
4. For the merged code's timed `Future.result`, compress the wall timeout to 1 ms without cancelling Track B. The foreground reaches evidence coverage after its line-16707 stop check.
5. In the coverage callback, release Track B and wait for its stop-latched event before returning `accountable=False`. The real foreground then follows the missing-dossier failure exit.
6. For ASTRA's untimed `Future.result`, release the same worker before joining; its typed failure reaches the pre-publication stop boundary. No real timeout wait, provider call, or network request is needed.

| Source | Real actor thread stopped compaction | Exit code | Typed stop in metadata |
|---|---|---|---|
| ASTRA `019a7e4` | Yes | **4** | **Present** |
| RX00 working tree | Yes | **2** | **Absent** |

An initial fake-executor probe produced the same result; a second probe using actual local executor threads confirmed it. Both probes executed the source's real CLI path and actual temporary `meta.json` writes. Assertions checked the stop event and the differing exit codes.

**Requested correction:** retain GLM's bounded shutdown behavior, but make terminal worker handling and typed-stop encoding consistent. An unresolved, still-running producer must not be silently treated as a disposable completed lane. Route early failure exits through a common finalizer that preserves any latched compaction stop, and define bounded cancellation/terminal coordination so a producer cannot invalidate the result after the final check. Merely adding another check at line 16707 does not close the demonstrated interval. Add an offline regression for timeout → late actor compaction failure → reserved exit/metadata → parent stage-wide stop, including no admission of queued lanes. This review does not implement that repair.

## Suggestions and prerequisite boundaries

### Linear mode negotiation remains a main-baseline prerequisite, not a new merge finding

[deerflow_research.py:16248](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:16248) dispatches every `RESEARCH_ENGINE=linear` request except extract-only directly to `linear_research.run`. The inspected linear implementation does not negotiate `args.evidence_only` or `args.synthesis_manifest`: it writes a report at [linear_research.py:1141](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/linear_research.py:1141) and invokes structured extraction at line 1166. The parent expects `evidence_pack.md` for evidence lanes ([pipeline_orchestrator.py:2529](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:2529)); global synthesis has a distinct manifest-bound actor/evidence contract. Keeping both deployment entries does not make those modes compatible.

This behavior is already present in `ec29ab1` and is covered by the assigned bridge prerequisite repairs. Keep it explicit until unsupported modes fail before provider admission or implement their actual artifact contracts. Do not count it as a second merge-introduced finding, and do not duplicate the other reviewer's implementation.

Likewise, linear `_Gateway.invoke` and `_compact_messages` are separate paths from ASTRA's durable API accounting and exact removed-message receipts. Their existing gaps are identified in `history-reuse.md` and `research-bridge.md`; this review does not reopen them as RX00 merge regressions. A synthetic typed-stop injection into an otherwise unsupported route would not by itself prove a reachable new production failure.

## Positive notes

- **Deployment resolution preserves both engines' dependencies.** [pipeline_orchestrator.py:1412](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:1412) includes both `linear_research.py` and `research_compaction.py`, including the latter's native-backend destination. The added sync test exercises both module entries.
- **SDK construction preserves main's timeout and ASTRA's attempt boundary.** [llm_client.py:275](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/utils/llm_client.py:275) retains `max_retries=0` and the explicit configurable `timeout`; request-local `with_options(max_retries=0)` remains in `_create_openai_completion`. No regression was identified in that resolution. The added test checks timeout and retry settings together without HTTP/2.
- **Actor repair uses the same validated evidence on both calls.** The actor stage collects receipt-validated thread messages at [deerflow_research.py:13975](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:13975), appends the Track-B receipt ledger, and passes the same `context` to original synthesis and main's bounded missing-ledger repair at lines 14025 and 14046. It does not invoke the global worker-note fallback. The adjusted test correctly distinguishes one call with a ledger marker from two without, checking retained summary/receipt content and exclusion of global notes on each invocation.
- **ASTRA's shared actor-access and accounting modules were retained exactly.** At the reviewed snapshot, `simulation_config_generator.py`, `telemetry.py`, and `usage_ledger.py` were byte-identical to `019a7e4`. This is preservation evidence, not a substitute for their existing behavioral suites.
- **The remaining orchestrator changes relative to ASTRA are narrow.** Besides the deployment union, main's preflight token cap remains 64. The existing receipt/parent-stop and durable accounting paths were not replaced by main's older implementations.

## Verification and concurrent-work limits

- Fresh independent evidence: both-parent source comparisons; two deterministic timeout/compaction probes (one with real executor threads); AST parsing of the seven files below; absence of conflict markers in the three integration files; `git diff --check ec29ab1` for those three files completed cleanly.
- Parent-reported evidence, not rerun here: focused **159 passed**. The later full baseline gate reported **3,978 passed / 8 failed**, with existing main regressions and the known `drf2` path issue. It is not an all-green integration gate.
- Parent reported that another reviewer is repairing bridge extraction/mode failures and the native reviewer is repairing portable `drf2` configuration. Parent also changed the real OASIS prompt-propagation fixture to use installed CAMEL `StubModel`, avoiding the missing-tokenizer network download while retaining the actual OASIS constructor. Those repairs were not independently validated by this review.
- Source and index were left untouched. Do not stage, revert, or replace collaborators' work based on the snapshot here. Recheck this finding and line numbers after the assigned repairs and before RX00 acceptance.

### Reviewed source fingerprints

These SHA-256 values identify the content used for the finding and final source cross-check; they avoid treating later concurrent changes as reviewed.

| File | SHA-256 |
|---|---|
| `deerflow_bridge/deerflow_research.py` | `5cda62996be58d6fadfe7ed62e84fee1db230e1296b29da4ac8cb4c9ecd353cd` |
| `deerflow_bridge/linear_research.py` | `9bf997a7e8801b679ec10ea3e4a9e273b8eed5dd8ad0fe8f1ca6a01b1e23e4bd` |
| `backend/app/services/pipeline_orchestrator.py` | `a3ea402a688cfc8ec55640278190d49f2712626c6144a28a3e1ac0cdbad4015b` |
| `backend/app/utils/llm_client.py` | `a19f3dc24f83304b53d862d2dbd7d56418d13f1f140f013dc557d0ab4383abbe` |
| `backend/app/services/simulation_config_generator.py` | `504015fae1a8325c9dcb215fd9e8e2f1251ac6d4d31dc623cbe9fe48ac9c1efd` |
| `backend/app/utils/telemetry.py` | `15ee3ed7ef737e6ddca74d67bf79132fe6f6e5b305d252f905ce940074251b70` |
| `backend/app/utils/usage_ledger.py` | `c73be24c26145761e93a104566cb1d544021d776e9550bf2b7efe2450818d188` |

## Handoff

RX00-R1 is open. The next acceptance check is a narrow regression proving that a compaction stop from a timed-out actor worker retains its typed terminal identity and fences parent lane admission while shutdown remains bounded. The linear-mode prerequisite and baseline gate failures remain with their assigned owners. No new planner feature was started by this review.

## Addendum — independent review of roster compatibility, DRF2 skills portability, and OASIS fixture

**Date:** 2026-09-19, Asia/Shanghai. **Disposition:** no new blocking findings in these three completed repairs. This addendum leaves the original RX00-R1 evidence and disposition above intact. The active bridge-race repair has not been reviewed or accepted here; review of that repair awaits the parent's source-stability notification.

### Critical issues

None found in this addendum's scope.

### Important issues

None found in the changed roster validator, DRF2 skill-path configuration/documentation, or OASIS fixture. The evidence supports acceptance of these bounded repairs, not acceptance of the whole RX00 merge or all baseline failures.

### Review findings and positive notes

**Roster producer/consumer compatibility is restored without weakening current-format validation.** At [actor_context.py:1275](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/actor_context.py:1275), presence of either current roster-proof field selects the current format and requires all three digests to match: the compatibility alias, canonical ID-to-count multiset, and ordered newline-delimited IDs. The producer computes exactly these representations at [deerflow_research.py:3783](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:3783) and assigns the multiset alias at line 3870. `dict.fromkeys(actor_ids, 1)` is equivalent to the producer's counts here because missing and duplicate IDs are rejected before this branch. Canonical serialization agrees on UTF-8, unescaped Unicode, sorted keys, and compact separators. The existing legacy sorted-ID algorithm remains available only when both current fields are absent; an empty, malformed, or partially missing current proof cannot select that fallback. Existing report binding, selected-row equality, counts, dimension coverage, and epistemic filtering remain in force.

The new [test_actor_roster_boundary.py:127](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_actor_roster_boundary.py:127) calls the real final producer seal, reads its written `actors.json`, and passes it through context artifact generation and manifest validation at line 167. The success path does not fabricate the current roster digest with the consumer algorithm. Its assertions also retain the sealed intelligence, exclude denied knowledge from actor beliefs, and check that producer bytes were not mutated. Negative cases cover each digest, mixed legacy/current proofs, partial current proofs, duplicate IDs, order changes, replacement IDs, and stripping current proof fields. All **19** cases passed independently.

**DRF2 skill resolution is portable under the documented project-root contract.** [config.yaml:382](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/drf2/config/config.yaml:382) now uses `drf2/skills`. The actual vendored `SkillsConfig.get_skills_path()` resolves this through `runtime_paths.resolve_path`, which uses `DEER_FLOW_PROJECT_ROOT` or the current working directory. The revised [README.md:74](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/drf2/README.md:74) sets the checkout root before changing into the vendor directory and explains the fallback accurately. A read-only check of the available original vendor launch scripts also confirmed that `make dev` invokes `serve.sh`, whose project-root default is applied only when the variable is empty; it preserves this explicit override. No launch command was executed.

The tests at [test_drf2_skills_config.py:385](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_drf2_skills_config.py:385) use real `AppConfig` loading, then verify an actual copied temporary skill tree from both a vendor-backend directory and an unrelated working directory. They assert resolution to the relocated checkout rather than the original tree, including all seven skill names. All **three selected real-config cases** passed independently, with no skips.

**The OASIS fixture preserves the intended producer-to-runtime assertion.** [test_actor_context_runtime.py:977](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_actor_context_runtime.py:977) supplies installed CAMEL `StubModel(ModelType.STUB)` to the real Reddit and Twitter graph constructors. Inspection of the installed OASIS implementation confirms those constructors still read the exported JSON/CSV, construct `UserInfo` and `SocialAgent`, and forward the model dependency. The test still reads the resulting agents' actual `system_message.content`, checks exact role text (with the established Twitter newline normalization), and rejects tampered context provenance at lines 999–1010. It does not replace the profile parser, agent constructor, role renderer, or assertions with mocks. Installed CAMEL's stub has a local token counter, so this avoids an unrelated tokenizer download. The selected real-OASIS test passed independently under the network guard.

### Suggestions and remaining limits

- Preserve the existing scope labels: this is **skills-path portability**, not complete DRF2 engine portability. `extensions_config.json` still contains machine-specific interpreter/PYTHONPATH entries, which the revised README explicitly discloses. They are outside this repair.
- The OASIS fixture proves profile/provenance/prompt propagation, not provider tokenization, model inference, or a live simulation. Its new explanatory comment makes that boundary clear; no additional provider validation is required for this fixture change.
- No additional changes are requested for these three repairs. RX00-R1 and the other owners' bridge/baseline work retain their separate acceptance gates.

### Independent verification

A fresh guarded phase, [rx00-independent-prereq-review-01/command-completed.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx00-independent-prereq-review-01/command-completed.json), recorded **23 passed, 0 failed, 0 skipped**, **0 network attempts**, and **no tracked source drift**. The selected cases were the complete new roster-boundary file (19), real DRF2 config loading (1), relocated skill resolution (2), and the real OASIS prompt propagation case (1). Test output reported 4.87 seconds. [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx00-independent-prereq-review-01/results.xml) are retained separately. No broad suite was repeated.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python -B \
  /Users/rogerlin/.codex/rearchitecture-evidence/20260919/offline_pytest.py \
  rx00-independent-prereq-review-01 \
  backend/tests/test_actor_roster_boundary.py \
  backend/tests/test_drf2_skills_config.py::TestRealSchemaValidation::test_config_yaml_loads_via_real_appconfig \
  backend/tests/test_drf2_skills_config.py::TestRealSchemaValidation::test_skills_resolve_in_relocated_project \
  backend/tests/test_actor_context_runtime.py::test_role_context_reaches_real_reddit_and_twitter_oasis_system_messages \
  -p no:cacheprovider
```

The evidence launcher rejects reuse of a phase directory; a future rerun must use a fresh phase name. `git diff --check` also passed for the five changed tracked files in this review.

Prior owner evidence was inspected without repeating those suites: roster red **10 failures / 9 passes**, roster related final **169 passes**, and DRF2 portability **41 passes / 2 skips**. The two DRF2 skips explicitly concern the absent deployed `deer-flow/skills/public` mirror, not schema loading or relocation. These receipts report no network attempts. They are supporting owner results, distinct from the fresh independent 23-test gate.

### Addendum source fingerprints and preservation

| File | SHA-256 |
|---|---|
| `backend/app/services/actor_context.py` | `d92c3ceb0bff741db73719646225d3d5eb39a31d6bde91319602b0cf1b562377` |
| `backend/tests/test_actor_roster_boundary.py` | `10650b97cf99679466381929640e056ce0c40b650e50f6a85fb1c5cdde96d241` |
| `backend/tests/test_actor_context_runtime.py` | `0c12afff5457163b660f946c7a692cd5774a0cc13bc80a9d73c307b41123056e` |
| `backend/tests/test_drf2_skills_config.py` | `137cd36787a0da5e0f26efa98ec54744330a9e89d87c50d40ea2d83153b30fe9` |
| `drf2/config/config.yaml` | `2c55cad409df37fff27939569f033285da19c52dae966f2e23b15602ca5f58a6` |
| `drf2/README.md` | `ba276e2ade980f9b85ff739022ac9f66cae9a6c7b2370ea1a9c299e48fc6d316` |

The original review occupies the first 13,245 bytes of this file, with SHA-256 `5ae381f418eb397031f49004944806b9d16031cfa73eea1dec05a4ae73a4de5c`. This addendum was appended after that content. Production source, tests, configuration, deployment, and index were not edited by this reviewer. Only this review append and isolated test evidence were written.

## Final bridge addendum — RX00-R1 resolved

**Date:** 2026-09-19, Asia/Shanghai. **Disposition: no blocker found in the current bridge repair; close RX00-R1 for the reviewed source snapshot.** This disposition supersedes the earlier open status without altering its original failed evidence. Scope is the working-tree bridge delta against its staged source, the associated permanent tests, and the parent stop/fan-in behavior. It is not an expansion into unrelated baseline work or live-provider acceptance.

### Critical and important issues

None remaining in the reviewed timeout/compaction repair. The demonstrated ordinary-exit-2 regression is corrected, and bounded abandonment cannot proceed into completed-success finalization.

### Why the repair closes RX00-R1

- [deerflow_research.py:16638](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:16638) retains `shutdown(wait=False, cancel_futures=True)`, then gives unfinished Track A/B futures **one shared one-second settlement grace**. The `finally` block also runs after Track-A timeout or other early exceptions, not only after the actor join.
- Completed futures are inspected for typed compaction exceptions before foreground finalization. If any producer remains unresolved, lines 16652–16665 record `research_worker_stop.reason=producer_terminal_state_unconfirmed`, latch `checkpoint_unavailable`, and raise. The ordinary missing-dossier return is unreachable from that unresolved state. A producer becoming terminal immediately after the grace snapshot can cause a conservative stop, but cannot be misclassified as successful abandonment.
- [deerflow_research.py:16189](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:16189) now checks the latch for ordinary **failed as well as completed** metadata writes. The shared handler attaches typed stop metadata before writing it and retains reserved exit **4**, including when diagnostic persistence fails ([deerflow_research.py:17406](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/deerflow_bridge/deerflow_research.py:17406)).
- The permanent regression at [test_audit_fixes_research.py:295](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_audit_fixes_research.py:295) uses real executor threads for both an error observed during settlement and an unresolved producer. It passes the child's actual terminal metadata/exit through the real parent decoder and `_run_parallel_research_tracks`, asserting that **only `track_1` launches**. The existing parent recognizes current-attempt metadata or exit 4 and fences queued lanes; its source remained unchanged during this repair.

The late-error distinction is explicit: an error observed during grace keeps `archive_write_failed`; a producer still pending at the deadline is already fenced as `checkpoint_unavailable`, with the separate worker-stop diagnostic. A later worker outcome cannot turn that terminal result into success. The stop latch also rejects subsequent provider admission.

### Fresh independent evidence

The selected permanent gate passed **24 tests, 0 failures, 0 skips**, with **0 network attempts and no tracked source drift**. It covered the two race variants and their real parent fan-in assertion, a successful dual-track control, current/stale metadata and reserved-exit decoding, failure-metadata write outage, swallowed-stop publication rejection, actor extraction resealing, and supported/unsupported linear routing. Evidence: [command receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx00-independent-r1-close-01/command-completed.json) and [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx00-independent-r1-close-01/results.xml). No broad suite was repeated.

Because the permanent race test shortens its grace for speed, a separate independent probe exercised the **actual production one-second wait** with real executor threads. Only the initial long track join was shortened; client/provider work was mocked. All test workers were released and joined during cleanup.

| Scenario | Actual grace wait | Observed wall time | Terminal outcome |
|---|---|---:|---|
| Track B remains pending, later returns an ordinary result | One wait, one future | 1.011 s | Exit 4; failed; `checkpoint_unavailable` |
| Track A remains pending | One wait, one future | 1.012 s | Exit 4; failed; `checkpoint_unavailable` |
| Both producers remain pending | One wait, two futures | 1.005 s | Exit 4; failed; `checkpoint_unavailable` |
| Track B raises a typed error during grace without pre-latching it | One wait, one future | 0.061 s | Exit 4; failed; `archive_write_failed` |

Every case made **zero foreground coverage/finalization calls and zero later provider calls**, used `shutdown(wait=False)`, and attempted no network access. These are local control-flow timings, not provider latency measurements. Source remained unchanged. Evidence: [independent bounded-settlement results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx00-independent-r1-bounds-01/results.json).

Worker receipts were also inspected: `rx00-bridge-race-red` has two failures, `rx00-bridge-race-green-v2` has two passes, and `rx00-bridge-final-v2` has **266 passes / 1 skip**, with zero network attempts. The one skip is the absent deployed checkout. The final-v2 bridge fingerprint matches the independently reviewed current source. `git diff --check` passed for the bridge and its three changed test files.

### Other changes in the reviewed staged-to-working-tree delta

No additional blocker was found. Actor-enabled extract-only again requires valid lineage and deterministic final sealing, while preserving proposed per-actor intelligence for validation instead of stripping it into a legacy cast. Model-generated top-level seal claims are discarded and replaced by the deterministic producer; the strengthened extraction test exercises this behavior. Linear dispatch now runs after credential/mode preflight inside the shared failure boundary. Unsupported evidence-only, synthesis-manifest, actor-enabled, resume, and explicit-config modes fail before engine dispatch; explicit fresh `--no-actors` routing remains available. This closes the earlier silent mode-contract acceptance concern without claiming that the linear engine now implements those modes or the full shared accounting contract.

### Merge-review handoff and fingerprints

**The independent bridge/race review is complete and has no remaining blocker.** Parent reported the final full backend gate as **4,016 passed / 12 skipped / 11 existing xfails**, frontend **131 passed/build passed**, and original 880 source hashes unchanged. Those broader results were not rerun here; the parent still owns checking their pending source-drift receipt before committing. This independent disposition is tied to the hashes below and does not substitute for that check.

| Reviewed file | SHA-256 |
|---|---|
| `deerflow_bridge/deerflow_research.py` | `673413bdd5b943f76e6aecc362f4af4d1dbf2567797bc4b9081e1bd14fbf3f70` |
| `backend/tests/test_audit_fixes_research.py` | `164b3ce20b2795e5b867b31e370b48cbe72351a6c5c3e2db16ed1d8636a64692` |
| `backend/tests/test_research_checkpoint.py` | `376b52db903aa3abe8649ff1f31946ac811b0a831ca1625ab8135ee82659a32e` |
| `backend/tests/test_actor_intelligence_producer.py` | `080cbda7150a5eb92369b9b2034d69b844252ca37f900a77c61b413ef5c80b53` |
| `backend/app/services/pipeline_orchestrator.py` | `a3ea402a688cfc8ec55640278190d49f2712626c6144a28a3e1ac0cdbad4015b` |

The prior review plus roster/portability/OASIS addendum occupy the first 22,179 bytes, SHA-256 `c405307803d174b4bcf0163420d299f06ff73932ffa748607772ead724e38886`, and remain unchanged. No production source, tests, configuration, deployment, or index was edited by this reviewer. This final review append and isolated verification evidence are the only new artifacts.
