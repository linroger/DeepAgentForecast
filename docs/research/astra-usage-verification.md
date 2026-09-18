# ASTRA-04a: durable usage accounting and trustworthy status

This is the accounting foundation for ASTRA-04. The overall recommendation remains in progress. The implementation is isolated, verified offline, and undeployed; it does not establish complete provider coverage or pre-request budget reservations.

## Why this slice came first

The read-only [historical evidence receipt](astra-iteration-04-run-evidence.json) covers 32 saved pipelines created June 8–July 15, 2026. Twelve have no run telemetry and eight present totals are zero. Selected resumed runs retain cumulative usage materially above their current-attempt counters, while simulation health records 212 and 648 calls with zero RUN tokens in their parent records. Those discrepancies prevent reliable cost/performance comparisons.

The same receipt identifies the next time bottleneck: the reference graph stage took 31,050.61 seconds and skipped 278 of 466 chunks. Existing concurrency and quota cooldowns already exist, so simply adding more parallel work would not be an evidence-backed fix. Durable graph episode identity, deadline behavior, and report persistence remain later slices.

Historical values are recorded coverage, not billing or a proven lower bound. There are no saved end-to-end runs newer than the prior audit, so no production speedup is claimed.

## Final behavior and invariants

- A pipeline attaches one durable run/attempt binding before provider work. The ledger lives beside pipeline directories as `usage_ledger.sqlite3`. Each operation has a stable source and identity, and SQLite commits its high-water mark together with its positive contribution.
- Identical/stale snapshots add no usage. Growing snapshots add only positive increments. Distinct physical attempts remain distinct; a later parent attempt receives only growth it first observes. Per-attempt and cumulative views are separate.
- Research stdout observations carry physical child identity through lane/global summaries. Final publication reconciles those operations instead of adding the merged total again. These are process observations, not a physical API request count.
- Simulation imports use `meter_run_token` and simulation identity, accept growth, and cover extra ensemble seeds on success and failure. The producer's dominant model can change; a stable aggregate model label prevents identity churn, with detailed model diagnostics retained separately.
- Old totals are imported once as an opaque baseline. A saved legacy child marker is seeded before any new compatibility projection. The old marker-first crash ambiguity is explicit. Malformed totals, missing durable storage, and corrupt historical projections fail closed; failed initialization preserves original telemetry bytes through final cleanup.
- The actual inner `chat` calls own simulation JSON usage. One JSON call counts once; a repair counts both physical calls; parse-only errors cannot invent another provider failure. Persona executor jobs receive separate copies of their parent context.
- Ledger failures latch an attempt stop. Subsequent supported LLMClient, research-child, and simulation launches check it; both pipeline completion paths reject unresolved accounting failure even if a stage swallowed the original exception. Already in-flight operations may settle.
- Status reads cumulative durable usage from another process without attaching a meter. Missing/corrupt usage renders unknown. An explicitly registered zero remains visible. Saved legacy fallback requires a valid run identity and valid numeric totals.

## Acceptance and reproduction

[Machine-readable verification](astra-usage-verification.json) records exact commands, source hashes, gate results, review outcomes, and limitations. Tests use temporary storage and offline provider boundaries. They exercise separate spawned writers, thread contention, replay/growth/stale observations, process reset, migration crashes, projection failure, swallowed final write failure, real nested JSON behavior, concurrent persona contexts, and cumulative HTTP/status rendering.

The benchmark is reproducible from the implementation worktree:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python \
  backend/scripts/benchmark_usage_ledger.py \
  --operations 500 --reads 30 \
  --output docs/research/astra-usage-benchmark.json
```

[The retained sample](astra-usage-benchmark.json) measures synthetic accounting overhead: median durable write 0.479ms, aggregate read 0.941ms, and indexed availability guard 0.106ms. Five hundred operations reconcile to 550,000 tokens; 500 exact replays add zero usage. Storage uses synchronous durable transactions, a deliberate reliability cost. These are local measurements, not production throughput gains.

## Remaining boundaries

ASTRA-04b must cover provider-boundary reservations, in-flight allowance, live detached-child budgets, complete usage provenance, and report/graph adapter paths. Current budget checks now use durable cumulative observations but remain post-call and incomplete. CLI subscription and estimated API costs are labeled separately; unknown/cache partition coverage remains explicit. No historical artifact has been rewritten to infer missing requests.

The ledger must be retained with pipeline history. Restoring only JSON or independently deleting/rolling back the ledger loses accounting authority and is rejected where a durable registration is known. Cross-process resume ownership and backup/retention policy remain separate work.

The hourly same-task improvement loop remains active. Its next iteration should close ASTRA-04b before treating ASTRA-04 as complete, then work on graph ingestion and report persistence using the ranked saved-run evidence. Pareto claims require a stated workload, quality/recovery constraints, comparable measurements, and explicit tradeoffs; this slice establishes trustworthy measurements for that evaluation.
