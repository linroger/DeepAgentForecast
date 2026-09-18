# RX01 independent review

**Disposition: two important boundary defects remain; request changes before RX01 acceptance.** The pure planner and its deliberately limited evidence semantics are sound within the reviewed scope. The remaining findings concern filesystem containment during reads and malformed persisted state at the API boundary.

Date: 2026-09-19, Asia/Shanghai. Base: `c57089e`, following verified RX00 merge `61636e5`. Reviewed the complete new `pipeline_contracts.py`, `pipeline_reuse.py`, and stabilized `pipeline_inspection.py`, the new API route, and the two new test files. Related existing helpers were read only to trace path construction and state parsing. No production source, tests, saved runs, index, or deployment was modified. No live providers, services, or broad acceptance suites were run.

## Critical issues

None found. In particular, the endpoint never turns checked output bytes into modern input receipts or execution authorization. The filesystem finding below demonstrates an outside-root read, not a demonstrated remote disclosure of real secrets.

## Important issues

### RX01-R1 — P2: containment and regular-file checks do not bind the file actually opened

**Locations:** [pipeline_inspection.py:41](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_inspection.py:41) for manifest reading, and [pipeline_inspection.py:90](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_inspection.py:90) for registered artifact reading. The path containment helper is at line 32.

The adapter resolves and validates a pathname, checks it with `stat()`, and subsequently reopens that pathname. A concurrent replacement can turn the allowed regular file into a symlink after `_contained()` returns. `Path.stat()` and `Path.open()` then follow the new target. The existing before/after inode/size/mtime checks do not protect this interval: if the replacement occurs before the first stat, both stats describe the outside-root target and agree. Checking an existing symlink or FIFO before the read, as the new permanent tests do, does not cover replacement after validation.

**Observed on the stabilized source:** a deterministic test hook replaced the synthetic known file immediately after successful containment validation. All files were inside a disposable temporary test directory; the replacement target was outside that fixture's allowed root. The actual `_artifact` reader opened the outside-root target and returned `("verified", "registered_bytes_match")` when its bytes matched the supplied registration. The same schedule at `_manifest` opened and parsed outside-root JSON with no error. This violates the explicit allowed-root read boundary even though the eventual plan remains advisory/legacy-unverified.

| Probe | Outside-root opens | Result |
|---|---:|---|
| Registered artifact replaced after containment | 1 | `verified / registered_bytes_match` |
| Manifest replaced after containment | 1 | Parsed external fixture object; no manifest error |

**Requested correction:** bind containment and regular-file validation to the object that will actually be read, before reading content. Prefer opening relative to trusted directory descriptors with an explicit symlink policy, then verifying the descriptor with `fstat`; protect ancestor replacement as well as the final component. If a no-symlink policy is selected, handle legitimate fork/shared-owner paths explicitly rather than weakening containment. A second pathname check or a post-read mismatch alone is insufficient because the forbidden read has already occurred. Apply the same discipline to manifest and artifact readers; audit the endpoint's state-file resolve-then-load boundary for the same pattern. Add deterministic file/ancestor replacement regressions that assert **no outside-root content read**, not merely a rejected result after reading.

### RX01-R2 — P2: a malformed `stages` container escapes as an unhandled API exception

**Location:** [research.py:414](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/api/research.py:414), with the exception list at line 422. Related parser: [pipeline_orchestrator.py:724](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_orchestrator.py:724).

The route invokes `PipelineState.from_dict(data)` before the adapter validates the shape of `state["stages"]`. A present nonempty list is valid JSON and survives `PipelineManager.load`, but `from_dict` immediately calls `.items()` on it. This raises `AttributeError`, which is absent from the route's handled exceptions. The stabilized malformed-version guard correctly returns 409 for a bad schema version; it does not cover this structural error.

**Reproduction:** start with the temporary completed pipeline used by `test_pipeline_reuse_api.py`, replace its `stages` with `[{"name":"research","status":"completed"}]`, and call `GET /api/research/<id>/reuse-plan`. The real Flask test client propagates an uncaught `AttributeError`; with exception propagation disabled this is a server error rather than the route's structured 409 response. No execution or state write is needed to trigger it.

**Requested correction:** validate the persisted metadata shapes needed by this endpoint before calling the permissive shared state parser, then return the same non-sensitive 409 response for malformed state. Do not rely only on extending a generic exception catch, since malformed-but-falsy shapes can otherwise be coerced into absent/default state. Add a nonempty-list `stages` regression and table-driven malformed container cases. Keep omitted legacy fields compatible where that compatibility is intentional.

## Suggestions

No additional feature scope is requested. Keep byte integrity, input freshness, domain validity, and execution permission separate in the response. Neither finding requires adding graph access, provider calls, modern receipts, or automatic invalidation to RX01.

## Positive notes

- **Pure and frozen caller observations.** [pipeline_contracts.py:34](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_contracts.py:34) copies input mappings before wrapping them in `MappingProxyType`; frozen slotted observations prevent accidental field mutation. The core neither reads artifacts nor publishes receipts.
- **Incomplete modern proof is rejected; absent legacy proof stays uncertain.** [pipeline_reuse.py:29](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_reuse.py:29) distinguishes absent recorded inputs from incomplete current records, checks owners, rejects active/invalid observations, and propagates reject/rebuild/legacy decisions through the dependency graph. A report-only change leaves upstream work unaffected.
- **The adapter never invents input receipts.** [pipeline_inspection.py:169](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_inspection.py:169) supplies legacy observations even when registered bytes match. `observation_scope` explicitly excludes domain-schema and graph-content validation. The final response always remains advisory with `execution_authorized=False`.
- **The stabilized static boundaries are useful.** Duplicate manifest keys, broken manifest links, directories, and FIFOs are rejected. Reads skip active and unselected stages; artifact growth beyond the pre-read size is bounded. Registered missing files yield rebuild closure, while invalid registrations/bytes yield rejection. These protections remain valuable despite RX01-R1's replacement interval.
- **The added API tests exercise meaningful compatibility and non-mutation behavior.** They cover shared-owner fork bytes, report-only changes, absent manifests, malformed versions and identity, and snapshots of saved files before/after inspection. Spec construction was traced through the existing coordinator/report helpers; no graph/provider dispatch or directory-creation call was identified in the new endpoint path.

## Verification and limits

Fresh narrow test selection: **132 passed, 0 failed, 0 skipped** — core 110 plus API 22 — in 0.57 seconds, with **zero network attempts and no tracked source drift**. Evidence: [command receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-review-01/command-completed.json) and [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-review-01/results.xml). This is the RX01 test selection only, not the parent's wider launch/status gate.

Three fresh adversarial probes reproduced the two findings on unchanged source: artifact replacement, manifest replacement, and a persisted stages-list API request. The probes used synthetic temporary files, actual reader/route code, and a network-denying audit hook. The injected replacement schedule models a concurrent filesystem change; it does not claim a live exploit was observed. Evidence: [adversarial-probes.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-review-01/adversarial-probes.json).

The adapter and endpoint changed during the early portion of this review as the parent added the announced boundaries. All findings above were reproduced after those changes, and the hashes below identify the stabilized snapshot. Earlier concerns about pre-existing special files and malformed schema-version exceptions were not retained as open findings because the parent fixed them. `git diff --check` passed for the modified API file. No broad gate was duplicated.

## Reviewed fingerprints

| File | SHA-256 |
|---|---|
| `backend/app/services/pipeline_contracts.py` | `bce250469a3f5151b333594d31470a3ca13256a125a8fe7d2bb2a426fc3756be` |
| `backend/app/services/pipeline_reuse.py` | `c9ee4159401f8f9c9262ad2d54ed650138c8af74bec14851415b82f0430aa047` |
| `backend/app/services/pipeline_inspection.py` | `a1e72b199202245ca77a64e08ef494e9d5e3b20d42899f6625c8bc45dec6f714` |
| `backend/app/api/research.py` | `9ee41d53ea6df31f5dd04f3a9949d411bb88aa82ea725845c93d3a18b7a5eeaf` |
| `backend/tests/test_pipeline_reuse.py` | `ba696405cb0d498fbaa8eb4678214e9813c21089c5f733438ce54a8d1c20787f` |
| `backend/tests/test_pipeline_reuse_api.py` | `839cb5bdbeb652045fb809632fef27cea4490d1d40658045bda3973672174578` |

## Handoff

RX01-R1 and RX01-R2 remain open. Next acceptance is bounded: prove replacement cannot cause an outside-root read and malformed persisted container shapes return a structured conflict, then rerun the relevant RX01 boundary cases. This review created only this document and isolated verification evidence; it made no source changes or commits.

## Follow-up disposition after the 179-test and saved-pipeline checks

**Date:** 2026-09-19, Asia/Shanghai. **Disposition: changes still required; RX01-R1 and RX01-R2 remain open.** The latest positive evidence does not close the two reproduced boundary failures.

The current API and inspection files were read again and their full SHA-256 values compared with the source captured by the original adversarial probe receipt. Both are **byte-identical to the files that reproduced the failures**:

| File | Current SHA-256 | Finding still applicable |
|---|---|---|
| `backend/app/services/pipeline_inspection.py` | `a1e72b199202245ca77a64e08ef494e9d5e3b20d42899f6625c8bc45dec6f714` | RX01-R1: resolve/stat followed by pathname open permits a replacement to redirect the read |
| `backend/app/api/research.py` | `9ee41d53ea6df31f5dd04f3a9949d411bb88aa82ea725845c93d3a18b7a5eeaf` | RX01-R2: `PipelineState.from_dict` still precedes structural validation, and `AttributeError` still escapes |

No new defect or unrelated audit scope is added here. The earlier deterministic reproductions remain applicable to the exact current bytes; repeating a broad suite would not resolve either missing boundary check.

The parent-provided evidence was inspected and its scope retained:

- [rx01-api-core-extended/command-completed.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-api-core-extended/command-completed.json) and its JUnit output confirm **179 passed, zero failures/skips, zero network attempts, and no recorded source drift**. This is valid positive regression evidence, but the current permanent API tests do not contain the replacement-race or malformed-stages-container scenarios from RX01-R1/R2.
- [rx01-saved-readonly/result.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-saved-readonly/result.json) contains **five HTTP-200 terminal saved-pipeline cases**, each with `state_unchanged=true`, `advisory=true`, and `execution_authorized=false`; the summary reports zero network events. Its stated procedure forbids write/lifecycle methods and retains no prompts/artifact contents. These observations support normal saved-state inspection and advisory behavior. They do not exercise concurrent file replacement or malformed metadata containers. This reviewer inspected the summary only and did not reread or mutate those saved pipelines.
- The parent reports Ruff passing for all six changed files. That lint result is distinct from the runtime boundary findings and was not rerun here.

**Required before approval:** close the two existing findings with descriptor-bound safe reads and structural state validation, add their narrow regressions, then re-review the changed source. The review does not authorize or perform a merge, deployment, provider call, service start, or state write.

The original 10,761-byte review prefix, SHA-256 `245382079ad0ee069075cdd6c8cb28fb12808fbaa73b0dfd6b8c3b72cbe70e65`, is preserved unchanged. Only this follow-up section was appended.

## Correction — saved-pipeline probe v1 superseded by v2

**Date:** 2026-09-19, Asia/Shanghai. **Use v2 as the saved-pipeline acceptance evidence.** The earlier `rx01-saved-readonly` probe is retained as a setup-error record, not valid evidence of the intended saved-pipeline decision behavior. Its setup changed `Config.OASIS_SIMULATION_DATA_DIR` but left the static `SimulationRunner.RUN_STATE_DIR` bound elsewhere. Because the existing run-stage artifact spec uses the latter, the test created a fixture-only owner/root mismatch and incorrectly produced downstream `reject` decisions. This was not an observed defect in the saved pipelines. The prior section's characterization of that first probe as acceptance evidence is superseded by this correction.

The fresh [rx01-saved-readonly-v2/result.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-saved-readonly-v2/result.json) binds the source roots consistently. This reviewer inspected the summary and checked all five cases programmatically:

| Check | V2 result |
|---|---|
| HTTP response | Five HTTP 200 responses |
| Research and ontology | `legacy_unverified` in every case |
| Graph, prepare, run, report | `rebuild` in every case |
| Saved state | Every recorded state hash unchanged |
| Authorization | `advisory=true`, `execution_authorized=false` throughout |
| Network | Zero recorded network events |
| Write/lifecycle boundary | Forbidden by the probe procedure; parent reports zero writes |

V2 is read-only Flask test-client acceptance against existing terminal saved states. It is not a live-service/provider execution result. This reviewer did not rerun the saved-data probe or read the underlying saved state/artifact contents.

The corresponding current source adjustment is confirmed at [research.py:420](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/api/research.py:420): `allowed_roots` explicitly includes `SimulationRunner.RUN_STATE_DIR`, matching the existing run-stage spec. The API fixture binds both the dynamic Config directory and static runner directory at [test_pipeline_reuse_api.py:21](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_pipeline_reuse_api.py:21). Current fingerprints after this root-only correction are API `89b4532a26997f7ed8e30a15266374c1304d273ef6d9401a78718b903ef2e9a5` and API tests `b4b496ec06d0b0a11e6332472daf456be5407d47b1508117f3604e33d5e3243f`. Earlier byte-identity statements describe the earlier review snapshot, not these newly adjusted files.

This evidence correction does not resolve RX01-R1 or RX01-R2: the inspection reader remains byte-identical (`a1e72b199202245ca77a64e08ef494e9d5e3b20d42899f6625c8bc45dec6f714`), and the API still calls `PipelineState.from_dict` before validating the `stages` container. No new finding is added. For future probe setup, bind the roots used by the actual artifact-spec producer, including static class attributes captured at import time, before interpreting an owner/path mismatch as application behavior.

The preceding 13,774 bytes, SHA-256 `430ad61bf447e5f47b5111e23a7afb889a92514212a0dbeca1a1bc3f7bd77ecd`, remain unchanged. Only this correction was appended; no source or saved data was edited by this reviewer.

## Final metadata-precheck review — RX01-R2 resolved; RX01-R1 remains open

**Date:** 2026-09-19, Asia/Shanghai. The frozen API now rejects a present, non-null, non-dictionary `stages` root at [research.py:414](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/api/research.py:414), before `PipelineState.from_dict`. **Close RX01-R2.** This direct source check and fresh execution supersede the prior correction section's statement that the API still lacked this validation. The saved-pipeline acceptance correction remains valid: use v2, not the misconfigured first probe.

The permanent test at [test_pipeline_reuse_api.py:223](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_pipeline_reuse_api.py:223) restores the correct pipeline identity and schema version before submitting malformed `stages`, so its 409 assertion now exercises the intended precheck rather than the earlier identity rejection. It passed independently: [rx01-independent-r2-close-01 receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-r2-close-01/command-completed.json).

Seven additional temporary Flask-client cases verified the root-shape behavior directly. Nonempty list, empty list, integer zero, boolean false, empty string, and nonempty string each returned **409**. Legacy null returned **200** and remains explicitly compatible. All seven saved fixture files remained byte-identical; zero network attempts occurred. Evidence: [stage-shapes.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-r2-close-01/stage-shapes.json). The API bytes were unchanged across the probe; no original saved pipelines were accessed.

The reviewed API SHA-256 is `89b4532a26997f7ed8e30a15266374c1304d273ef6d9401a78718b903ef2e9a5`; permanent API tests are `bd8a374d0a5ee513bf7a791ca1a47ea690a23c4a69e430700e653cb0a369dd95`. The parent's 179-check final focused run was still in progress when requested; this review neither repeats that gate nor claims its result.

**Overall disposition remains changes required solely for RX01-R1.** The inspection module still has SHA-256 `a1e72b199202245ca77a64e08ef494e9d5e3b20d42899f6625c8bc45dec6f714`, identical to the source that reproduced outside-root manifest/artifact reads after containment validation. The metadata precheck and corrected run-state root binding do not alter that read path. No new issue is introduced in this addendum, and no source/index changes were made by the reviewer.

## Descriptor-bound reader review — RX01-R1 resolved; no remaining review blocker

**Date:** 2026-09-19, Asia/Shanghai. **Final scoped disposition: close RX01-R1. Together with the validated metadata precheck, both RX01-R1 and RX01-R2 are resolved. No remaining blocker was found in the reviewed RX01 implementation.** This supersedes the earlier open dispositions while preserving their failed evidence and the saved-probe correction.

### Source review

[pipeline_inspection.py:42](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_inspection.py:42) now resolves an admissible canonical path and walks every directory component with `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`. The final component is opened relative to the pinned directory with `O_NOFOLLOW | O_NONBLOCK`; `fstat` confirms a regular file before `fdopen` exposes a content reader. A substituted symlink is rejected by the open itself, and a substituted FIFO cannot block waiting for a writer before the regular-file check. Descriptor ownership/cleanup is explicit on success and failure.

The same reader serves bounded JSON state and manifest observations at [pipeline_inspection.py:86](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/services/pipeline_inspection.py:86) and registered artifacts at line 147. The API now consumes state through `read_observation_json` at [research.py:406](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/app/api/research.py:406), rather than reopening the validated pathname through `PipelineManager.load`. The R2 stages-root precheck remains before `PipelineState.from_dict`. Existing schema/identity rejection and advisory-only output semantics remain in place.

Post-read descriptor and pinned-directory-entry checks detect final-file replacement or content change. These checks are supplementary: they do not provide the protection against outside-root reads. That protection comes from the descriptor walk and no-follow opens before content access. Legitimate in-root aliases resolve to their pinned targets; shared-owner fork paths continue to work within configured roots. This assessment concerns the demonstrated pathname/symlink replacement boundary, not a general filesystem authenticity or input-freshness proof.

### Fresh independent validation — no outside-root content read

The permanent race/API selection passed **32 tests, zero failures/skips**, with zero network attempts and no tracked source drift. It includes all ten new replacement/alias cases plus the 22 API cases. The race tests at [test_pipeline_inspection_races.py:49](/Users/rogerlin/.codex/worktrees/drf-rearchitecture-20260919/backend/tests/test_pipeline_inspection_races.py:49) inspect the external fixture's device/inode before allowing a descriptor to reach `fdopen`; six API variants cover file/ancestor replacement for state, manifest, and artifact reads. Positive alias and post-open replacement cases are included. Evidence: [independent command receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-r1-close-01/command-completed.json) and [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-r1-close-01/results.xml).

The reviewer also replayed the original schedules independently and injected replacements at the final OS-open boundary. Both `os.open` and `os.fdopen` were guarded against the outside fixture's device/inode. An unsafe descriptor would fail the probe immediately, before any content read; merely returning an eventual rejection would not pass.

| Independent schedule | Result | Outside descriptors opened / content readers |
|---|---|---|
| Original artifact replacement after containment | Invalid artifact | **0 / 0** |
| Original manifest replacement after containment | Invalid manifest | **0 / 0** |
| Final component replaced immediately before `os.open` | Invalid manifest | **0 / 0** |
| Ancestor replaced immediately before its `os.open` | Invalid manifest | **0 / 0** |
| Final file replaced after a safe descriptor was opened | Read only original in-root bytes; detect replacement at exit | **0 / 0** |

All five probes used synthetic temporary files, attempted no network access, and left the reviewed source unchanged. Evidence: [race-replay.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-r1-close-01/race-replay.json). This directly reverses the earlier reproduced outside-root reads; closure is not based only on post-read error status.

The parent's [rx01-descriptor-boundary receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-descriptor-boundary/command-completed.json) was inspected separately and records **189 passed**, zero network attempts, and no tracked source drift across core, API, race, launch-intent, and status tests. That is supporting parent evidence; no broad final gate or saved-pipeline probe was repeated by this reviewer. The earlier saved-pipeline v2 remains the valid normal-path probe, with v1 retained solely as a setup-error record.

### Reviewed snapshot and handoff

| File | SHA-256 |
|---|---|
| `backend/app/services/pipeline_inspection.py` | `5094f60acc563a8c9e3abae65834ca8c1223688d833d16bac802a37c79dc3478` |
| `backend/app/api/research.py` | `af5eb3c642536e0a6b0d37cdfe0f7f8116ae251a756a9711fcf931149d227057` |
| `backend/tests/test_pipeline_inspection_races.py` | `47e27922203ab6a49b7cb38c57cd624ab30fde2d49aa04cd597be0ff379eee35` |
| `backend/tests/test_pipeline_reuse_api.py` | `bd8a374d0a5ee513bf7a791ca1a47ea690a23c4a69e430700e653cb0a369dd95` |

**Independent RX01 review is complete with no remaining blocker on these bytes.** Parent retains ownership of the broad final gate and commit. The prior 19,548-byte review prefix, SHA-256 `b971c63cac8b12a16ea2afb386dc07af197b5a9844932c7ebee4d71b872d81ba`, is preserved unchanged. This reviewer changed no production source, tests, configuration, saved data, deployment, or index; only this review append and isolated verification evidence were written.

## Final frozen-source confirmation before the broad gate

**Date:** 2026-09-19, Asia/Shanghai. **Disposition confirmed: RX01-R1 and RX01-R2 remain closed; no independent-review blocker remains on the frozen source.** API, inspection reader, race tests, and API tests still match the four reviewed fingerprints in the preceding section.

At the parent's explicit request, the original failures were replayed again on this exact snapshot:

- Artifact and manifest replacement immediately after containment validation were rejected with **zero outside-root descriptors opened**. Both `os.open` and `os.fdopen` were checked against the external synthetic fixture identity; an unsafe read followed by rejection would fail the probe.
- The original nonempty persisted `stages` list returned **409**, with fixture state bytes unchanged.
- The ten permanent race/alias tests plus the malformed-state regression passed again: **11 passed**, zero failures/skips, zero network attempts, and no tracked source drift.

Fresh evidence: [original-failures-replayed.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-final-confirm-01/original-failures-replayed.json), [command receipt](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-final-confirm-01/command-completed.json), and [JUnit results](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-independent-final-confirm-01/results.xml). These reruns were limited to the requested finding boundaries; no broader audit or full suite was repeated.

The latest descriptor-version [rx01-saved-readonly-final/result.json](/Users/rogerlin/.codex/rearchitecture-evidence/20260919/rx01-saved-readonly-final/result.json) was inspected and checked: five HTTP 200 cases, all recorded state hashes unchanged, research/ontology `legacy_unverified`, graph through report `rebuild`, advisory-only responses, and zero network events. It is the latest saved-state acceptance receipt; the initial misconfigured v1 probe remains invalid for that purpose. Only the summary was read by this reviewer, not the underlying original saved files.

The parent reports 189 passing descriptor-boundary checks and Ruff clean on all seven changed Python files. The separate `rx01-backend-final` gate and its source-drift verification remain parent-owned and are not asserted complete by this review. All previous review evidence is retained; no source/index edits, provider calls, or live-service operations were performed.
