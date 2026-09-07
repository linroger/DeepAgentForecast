# ASTRA-04b3: unresolved API operations across recovery

Status: verified offline on the isolated optimization branch. Full ASTRA-04 and deployment remain unfinished.

The previous slice persisted an API dispatch marker before each request, but cumulative totals read only positive usage deltas. A process could exit after the marker and leave zero recorded tokens with no public indication of unfinished work. Malformed provider usage also shared the transport-error status and depended on a process-local stop, so restarting could lose that distinction.

## What changed

Snapshots now include `api_operation_state`, a compact run-wide projection of recorded API operations. It reports `in_flight`, `unknown` and `accounting_error`, plus current/other attempt in-flight counts when an explicit reference exists. Without a reference, those owner counts are null. Attempt token totals still describe deltas attributed to that attempt; they do not silently adopt another attempt's spending.

Indexed admission checks reject another attempt's in-flight operation and any accounting error. The same predicate runs inside the transaction that inserts a new dispatch marker, closing the gap between an earlier check and a competing owner. Concurrent work within the same attempt remains allowed. Neither ownership nor the word “other” proves a process is dead.

Malformed usage now persists `accounting_error`. A precisely known observation for the same physical identity can settle it and release the policy stop without resetting the binding. Actual storage failures retain the existing sticky stop because a successful later read does not prove a lost observation was recovered. Stale or estimated updates cannot clear an accounting error or turn a completed outcome back into a dispatch marker.

Pipeline startup checks uncertainty before entering a stage. Both full and research-only completion reject any unfinished/error API operation. Public status preserves recorded tokens while making the budget remainder unavailable when in-flight work or an accounting error has unquantified consumption. Both telemetry export paths take operation state from the same cumulative read as the cumulative total, avoiding contradictions when a response settles between reads.

## Verified behavior

| Recorded API state | New dispatch | Pipeline completion |
|---|---|---|
| No unresolved operation | Existing budget policy applies | Allowed by this accounting check |
| Same-attempt in-flight work | Allowed for concurrency | Blocked until settlement |
| Other-attempt in-flight work | Blocked pending reconciliation | Blocked |
| Accounting error | Blocked until precise settlement | Blocked |
| Unknown transport outcome | Existing retry policy applies | Does not independently block |

The [offline comparison](astra-unresolved-comparison.json) shows that the previously invisible zero-counter operation is now visible and other-owner admission is rejected. A separate-process test exits inside the shared LLMClient dispatch helper, using a fixture `create` method after marker persistence; the next owner starts **zero new SDK calls**, including when spending caps are zero. No network or provider is used.

With 10,000 completed fixture operations plus one unfinished marker, recorded totals remain **10,000 calls and 150,000 tokens**. `EXPLAIN QUERY PLAN` confirms a covering-index search. Median local admission time across 100 samples was **0.0940 ms before and 0.1128 ms after**. On an empty healthy ledger, it was **0.0944 ms before and 0.1061 ms after**. These measure the added SQLite checks; they are not end-to-end latency or savings estimates. The baseline and current implementation read the same fixture database, and the accounting-error comparison tests a status the baseline producer did not yet emit.

## Historical evidence and limits

The [compact evidence refresh](astra-unresolved-run-evidence.json) revalidated all 77 saved-artifact hashes, all 12 bounded log hashes and the unchanged 32-pipeline inventory. The latest saved pipeline was created July 15, 2026. No new production benchmark is available.

Three selected expensive or resumed runs have simulation health counters of 221, 648 and 212 calls, but their parent RUN meter entries are absent. Earlier audit projections represented that absence as zero; the new receipt explicitly records null and `present:false`. These health counters do not quantify tokens, distinct failed requests or invoices. Historical unresolved-operation counts remain unknown.

An in-flight marker may represent a crash before transport dispatch. Preserving it trades automatic recovery availability for retained uncertainty. No automatic expiry or reconciliation endpoint is added. Ordinary transport unknowns remain visible and retryable, and older unknown rows cannot safely be reclassified. Same-attempt concurrency still has no transactional token reservation. Detached simulation physical attempts, CLI provenance and full workflow budget coverage remain pending; `usage_complete` stays false.

## Validation and reproduction

The final affected backend gate passed **782 tests**, with one deployment-only parity check skipped because the isolated worktree has no deployed DeerFlow checkout. All nine changed Python files pass Ruff and parsing. Independent review passed 62 targeted tests and has no remaining blocking findings. Tests cover crashes, competing processes, same-owner concurrency, precise recovery without reset, failed writes, stale status replay, old-schema read-only access, startup, both completion modes and public status.

Exact commands, source hashes, the skipped-test reason and review resolutions are in [the verification receipt](astra-unresolved-verification.json). From the implementation worktree:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_unresolved_usage.py --baseline-ref d09ee4f --history-rows 10000 --samples 100 --output /tmp/astra-unresolved-comparison.json
```

The script writes only temporary fixture ledgers and the requested output. The hourly improvement loop remains active. The next slice closes detached-simulation physical-attempt transport before broader reservations. No production deployment, speedup or Pareto-optimality claim is made.
