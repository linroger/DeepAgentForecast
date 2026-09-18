# Graph batch acknowledgements survive operation timeouts

ASTRA-06a is verified offline against baseline `45a8da8`. It fixes receipt loss and inaccurate accounting within the current process. Durable episode checkpoints and restart recovery remain open under ASTRA-06b.

## Defect and correction

A successful episode could return its UUID while another episode in the same concurrent batch remained pending. The synchronous bridge timed out, cancelled its Future, and discarded all batch results. GraphBuilder then counted every input as failed and could raise all-text-failed despite the acknowledged successful episode.

A thread-safe per-call collector now records started indexes, acknowledged UUIDs and final failure reasons. A typed timeout carries an immutable validated snapshot through the public client to GraphBuilder. Only acknowledged UUID strings count as success; started inputs without receipts and inputs that never started remain distinct. Input indexes are local to the call, not a durable identifier; repeated `chunk-0` names cannot overwrite each other.

Cancellation requests are separated from cleanup acknowledgement. The submitted coroutine's outermost finally signals that owned async work and graph locks have unwound. The bridge waits for a finite configured grace (default 2 seconds, finite values clamped 0–30; unparsable/nonfinite values use 2). If cleanup remains unconfirmed, the builder records provisional diagnostics and stops before another batch, runtime counter query or graph publication. This does not terminate already-running executor/provider work or prove database rollback.

The bridge also preserves an already-completed Future's own TimeoutError rather than misclassifying it as its operation deadline. Normal/serial requests, source bodies/reference time, cast-filter denominators, attempt budgets, bounded rate-limit replay and actor-seed checks retain their existing contracts. No automatic replay or durable cache was added.

## Review caught a second accounting race

If the coroutine completed its failure counters but its result had not reached the synchronous Future, the first patch merged the same failure reason twice. Indexed runtime-accounted reasons now distinguish counters already represented in the runtime. No unscoped aggregate counter is subtracted. Cancelled child results retain their final classification once, and unavailable counters or unconfirmed cleanup retain provisional indexed diagnostics.

The permanent tests reproduced both defects before repair: acknowledged-first/second cases failed to deliver their UUID, then two final-accounting cases double-counted reasons. The original red evidence is retained. The expanded suite includes 21 public-path scenarios covering queued inputs, gather/cooldown/replay interruption, cleanup refusal, inner timeouts, ordering, duplicate names, empty UUIDs, unknown writes and exact source propagation.

## Verification and measured effect

| Public-path observation | Baseline | Current |
|---|---:|---:|
| UUIDs retained after sibling timeout, either input order | 0 | 1 |
| Recorded failures for one acknowledged + one hung input | 2 | 1 |
| Successful receipts invented for an unacknowledged write | 0 | 0 |
| Failure-reason count in the delivery-race case | 2 | 1 |

Fake-core call counts, normal-path UUID order and input/reference-time values remain identical. The caller previously recorded a thrown batch failed and advanced; it did not automatically replay that batch. Earlier explicit-replay reproductions must not be represented as automatic retries in GraphBuilder.

The final backend gate reports **3,978 passed, one known drf2 scaffold-path failure, 17 skipped and 11 existing expected failures** in 148.49 seconds. 143 guarded Python processes recorded zero network events and no source drift. All 21 new permanent cases pass; the broader focused graph gate passes 144, and independent review passes 109 with no blocking finding. Six Python files pass Ruff/compilation, and environment documentation validation passes. The runner uses xunit1 only to preserve test metadata without xunit2 warnings.

[Verification receipt](astra-graph-receipt-verification.json), [paired public-path comparison](astra-graph-receipt-comparison.json), [all scenarios](astra-graph-receipt-scenarios.json), [independent review](astra-graph-receipt-review.json), and [corrected race observation](astra-graph-receipt-accounting-race.json) retain exact source/evidence hashes.

These are source and synthetic integration results, not a production speedup or token-saving measurement. The historical audit still lacks complete graph/simulation metering and has recovery-overlapped intervals. Safe durable recovery needs persisted receipts, graph/input/ontology policy identity, ambiguous-write reconciliation and dependency ownership; those remain explicit next work.
