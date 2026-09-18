# ASTRA-04b5a: cheaper cumulative budget checks

Enabled budget guards now read only the cumulative counters they need. Token-only checks avoid constructing the complete usage report; cost-enabled checks preserve the existing grouping and rounding semantics. This completes the read-path slice. Transactional reservations remain ASTRA-04b5b, and full ASTRA-04 remains in progress.

## Finding and implementation

Previously, each pre/post request budget check built the full durable snapshot: ten summed metrics grouped by eight dimensions, stage/model/source/fallback breakdowns, classification flags and unresolved operation counts. The guard consumed only total tokens and cost.

`UsageLedger.budget_totals()` now reads existing storage inside one read transaction and verifies run identity before returning anything. Token-only checks use an integer scalar sum. Cost checks select the two needed metrics grouped by the same eight dimensions as the full snapshot, accumulate in the same order, and round cost to six decimals. This preserves an edge where flattening the cost sum would change 0.500001 to 0.5 and alter a threshold decision.

Review found another edge: valid token totals across groups can exceed SQLite's signed integer sum range while the existing full snapshot accumulates them as Python integers. The token-only path now falls back to grouped token sums only for that precise overflow error. The regression preserves a total of `2**63`, passes an equal cap, and raises `BudgetExceeded` for a cap one lower without recording a storage failure.

The meter adapter preserves cumulative legacy/replay/resume observations and sticky storage errors. Missing or unreadable accounting is never replaced by an empty memory total. Strict `>` comparisons, attribution fallback, and the early return when both caps are zero remain intact. Full snapshots, admission policy, schema, indexes and writers are unchanged.

## Measured local effect

The [comparison](astra-budget-comparison.json) loads the pinned `check_budget` function from `1c87a29` and compares it with the current guard against the same current meter and temporary ledger. Population and durable writes are outside timed intervals. Both paths are warmed and their order alternates across 20 samples per guard/mode/size. No provider is constructed.

| Synthetic delta rows | Token-only guard, before → after | Reduction | Cost-enabled guard, before → after | Reduction |
|---:|---:|---:|---:|---:|
| 100 | 0.4229 → 0.1388 ms | 67.18% | 0.4196 → 0.2449 ms | 41.63% |
| 1,000 | 2.0981 → 0.4474 ms | 78.68% | 2.1225 → 1.4672 ms | 30.87% |
| 10,000 | 21.6447 → 3.6771 ms | 83.01% | 21.6541 → 15.6073 ms | 27.92% |
| 100,000 | 279.2175 → 59.2455 ms | 78.78% | 283.4583 → 209.0073 ms | 26.27% |

The receipt also includes both caps enabled; that mode retains cost grouping and has similar gains to cost-only checks. These are local synthetic read-latency measurements, not production or end-to-end pipeline speedups. Limits default to zero, so no benefit is claimed for the unchanged disabled path. Both projections still scan the run's delta history; this is not constant-time accounting.

## Saved-run evidence and scale limits

The [historical refresh](astra-budget-run-evidence.json) confirms 77 saved artifact hashes, 32 pipeline-state hashes and 12 bounded log hashes remain unchanged. No new or changed logs needed parsing. Across 20 available meter files, current aggregate call counts range from 0 to 252. Only three have cumulative counts: 255, 335 and 629; missing fields remain unknown.

Those counters do not establish physical request or ledger sizes. The expensive July 9 reference represents 79,749,778 research tokens as one aggregate stage call. Cached observations and overlapping attempt snapshots further prevent converting call totals into delta counts. The 100-row fixture is adjacent to observed aggregate scale; 1,000 is sensitivity; 10,000 and 100,000 are explicitly scaling stress.

## Verification and reproduction

The affected backend gate passed **998 tests**, with **one skipped deployment-parity check** because the isolated worktree has no deployed DeerFlow checkout. Independent review passed **38 focused tests**: 26 ledger/guard cases and 12 actual SDK provider-boundary cases. All five changed Python files pass Ruff and parsing, and the diff whitespace check passes.

Shared plain/native-tool and direct CAMEL sync/async tests prove budget crossing still stops the request sequence after the first empty response reporting usage; repeated calls transmit nothing further. Tests separately preserve unresolved-work admission and missing-storage failure. Other scenarios cover legacy baseline import, stale/growing/replayed snapshots, resumed/child observations, read-only v1 compatibility, transaction-consistent reads, rounding boundaries and disabled guards with no reads.

Exact commands, source hashes and case facts are recorded in [the verification JSON](astra-budget-verification.json). Reproduce the comparison from the isolated worktree:

```bash
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python \
  backend/scripts/benchmark_budget_guard.py \
  --baseline-ref 1c87a29 \
  --rows 100 1000 10000 100000 \
  --samples 20 \
  --output /tmp/astra-budget-comparison.json
```

No runtime deployment, provider request or saved-artifact mutation occurred. The hourly loop remains active. The next slice designs bounded transactional reservations with explicit uncertainty; any materialized totals must separately address migration, mixed-version writers and floating cost compatibility.
