# ASTRA-04b1 research stream accounting

**Verified offline:** September 7, 2026. **Results:** 577 backend tests and 98 overlay tests passed, plus 23 complete-client checks against previously patched source. Two deployment-only checks were skipped. Independent review found no remaining blocking issue in this slice.

This iteration closes a prerequisite for cumulative budget enforcement: trustworthy observed usage across the research client, bridge, parent process, and durable ledger. The baseline is `0c37c31`. ASTRA-04 remains in progress; reservations and complete provider request coverage are separate work.

## Why this boundary was selected

The read-only saved-run inventory still contains 32 pipelines, with no creation newer than July 15, 2026. Research accounts for 96.103% of the reference run's main recorded tokens in [the retained historical analysis](astra-iteration-04-run-evidence.json). Those records prioritize the work; they do not establish current billing, current throughput, or a measured production improvement.

The complete client generator exposed defects that a helper-only test missed: values-mode display deduplication skipped later usage growth; replayed checkpoint history was charged again; independently increasing input/output high-water marks could disagree with total tokens; and a stream ending before its final aggregate lost all bridge accounting. Review also found that displaying the first message in an already-received frame could interrupt accounting for the remaining messages.

| Offline scenario | Previous recorded result | Corrected recorded result |
|---|---:|---:|
| Same message grows from 10 to 110 tokens | 10 | 110 |
| Checkpoint contains 100 tokens; new message uses 7 | 107 | 7 |
| Input/output snapshots move from (100, 10) to (90, 30) | 120 total | 130 total |
| Stream interrupted after observing 7 tokens, before end | 0 | 7 |
| Custom payload quotes fake usage; valid events contain 18 tokens | 12,006 | 18 |

These are synthetic accounting reproductions, not token savings or provider billing measurements.

## Implemented contracts

- The tracked overlay seeds message high-water marks from the effective graph's checkpoint before invocation. Unreadable, malformed, or unidentified checkpoint AI usage prevents graph execution. A stateless graph is labeled explicitly. Previous history is excluded from the new stream's observations.
- Full AI messages carry cumulative snapshots. LangChain message chunks carry additive usage; chunk accumulation precedes the message high-water comparison. Input/output totals remain consistent, and cache details remain part of inclusive input.
- Every stream has a fresh identity and a zero/start handshake. Accepted deltas have monotonically increasing sequences. All observed usage in a values frame is emitted before any display event can interrupt its delivery. The end aggregate identifies the same stream for reconciliation.
- The bridge logs each validated delta immediately. Exact transport replays add nothing; conflicting identities/sequences or mismatched end totals produce an explicit degraded-stream gap while preserving already observed consumption and partial research text. The final aggregate is not charged again. Legacy end-only clients retain their original contract.
- A corrective turn stops before advancing the generator into another model step. Its previously emitted usage survives even when there is no end event.
- Cache read/write observations survive running, failed, merged, repeated, and resumed process snapshots. They are never added to input twice. The parent supplies `uncached_tokens=None`; cache partition and complete usage remain unknown, including attempts with only late cache growth.
- Parent accounting recognizes an actual leading usage event and counters immediately following that event. Bridge progress logging escapes CR/LF to retain one physical line per event. Quoted source/custom text cannot become a second usage event through these paths.

## Acceptance

[The machine-readable receipt](astra-research-stream-verification.json) records exact commands, source hashes, numeric reproductions, test results, and review outcomes. The full-client harness executes this worktree's transforms against both fresh vendor source and the previously patched client, using real LangChain message objects with an offline graph/checkpointer. It does not install the overlay into the deployed runtime or invoke providers.

The backend scenarios cover actual bridge logging, parent subprocess consumption, durable reconciliation after failure/restart, malformed observations, late cache-only deltas, replay, corrective interruption, and source-text spoofing. Wider regressions cover research/source/compaction boundaries, launch identity, telemetry, simulation imports, and budget readers. A deployment-parity test is intentionally skipped because the implementation worktree has no deployed bridge.

## Limits and next work

This is observed stream coverage, not physical request identity or complete billing. Message IDs cannot prove that two records came from different provider requests. Current messages without stable IDs remain explicitly uncertain. A crash before an observation reaches the bridge/parent can still leave a gap. Missing provider usage, detached child calls, folded child cache details, and compaction/private adapter calls can remain incomplete. No historical missing usage is fabricated, and no saved run is rewritten.

Checkpoint seeding is conservative: an older checkpoint containing AI messages without trustworthy identity/counters fails before another graph invocation. Explicit reconciliation is needed before such a checkpoint can supply a trusted accounting baseline. The bridge preserves partial text and reports degradation through its existing behavior; this does not establish a workflow-wide accounting stop for all producer coverage gaps.

The tradeoff is one checkpoint read per stream plus small per-message accounting state, event validation, and durable parent writes. This iteration establishes accounting correctness and prevents one unnecessary generator advance; it does not claim a measured end-to-end speedup or global Pareto optimality.

Next, account for each physical backend API attempt before content validation, correct fast-provider attribution, and make SDK retry ownership explicit. Then add transactional reservations/settlement with unresolved holds. CLI output bounds and estimated input/pricing limitations must remain explicit. Graph episode durability/deadlines and report persistence remain the next ranked workflow bottlenecks after the accounting prerequisites.
