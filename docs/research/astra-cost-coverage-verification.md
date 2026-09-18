# Recorded price coverage — ASTRA-04b5b3b1

Dollar-enabled requests on the shared LLMClient and instrumented native CAMEL paths now check whether prior recorded usage has price evidence before dispatch. The same run-wide coverage state reaches saved telemetry and public spend status. This prerequisite is verified offline; full monetary admission remains in progress.

## Problem and saved-run evidence

An API request made while the dollar limit was disabled could record known tokens with an unpriced quote and numeric zero cost. Enabling a dollar limit and configuring rates for the next request previously admitted more work against that incomplete historical amount. A new price cannot reconstruct or replace the old receipt.

The [saved-run receipt](astra-cost-coverage-run-evidence.json) was independently rechecked on September 9: all 77 artifacts, 16 selected documents, 12 bounded logs and four selected child-health files retain their recorded hashes. The inventory remains 32 pipeline states, with the latest created July 15, 2026. Unchanged bounded logs reuse the previous aggregates rather than reparsing them. Selected artifacts contain no qualifying historical price-coverage, rate-policy, dollar-cap or invoice fields. Historical unknown-price request counts and enabled caps remain unknown, rather than zero.

The same receipt preserves source-backed next opportunities: graph episode completion is coarser than individual extraction work, and concurrent report sections reach the outer save loop only after the generation helper returns. Those recovery improvements remain in the queue; missing telemetry alone is not evidence of duplicated provider work or measurable savings.

## Contract and behavior

[api_cost.py](../../backend/app/utils/api_cost.py) captures the quote and the current dollar-enabled decision together. [The shared boundary](../../backend/app/utils/llm_client.py) and [native boundary](../../backend/app/utils/oasis_usage.py) carry that decision into the initial durable API marker. A captured token or price requirement survives the tested SDK-options binding reset even when ordinary telemetry is disabled: losing the required durable binding stops dispatch.

[The ledger](../../backend/app/utils/usage_ledger.py) checks historical price coverage inside the same `BEGIN IMMEDIATE` transaction as new-marker admission, before token policy, token hold, operation or usage-delta writes. It rejects:

- API history with an unpriced quote or a missing/invalid quote.
- Opaque non-API observations, except strictly identified completed local cache hits with known usage and zero tokens/cost.
- Any imported legacy baseline, including an opaque zero-valued baseline.

The stop is a recoverable `BudgetExceeded`, so a policy failure does not latch a storage outage. Rejected requests leave the recorded history unchanged. Existing unpriced quotes remain immutable; configuring later rates cannot repair them by reinterpretation. The regression explicitly disabling the dollar limit verifies compatibility, not an automatic downgrade or a recommended recovery action. Disabled limits and unbound legacy paths retain their prior behavior.

The additive `cost-coverage-state/v1` projection contains these run-wide fields, even in an attempt-filtered snapshot:

| Field | Meaning |
|---|---|
| `priced_api_operations` | Recorded API operations with a valid non-null price, including explicit configured zero rates |
| `unpriced_api_operations` | Recorded API operations with valid unpriced/null-rate receipts |
| `unquoted_api_operations` | Recorded API operations with missing or invalid quote evidence |
| `inexact_api_operations` | API operations still in flight, with accounting errors, or without known usage; overlaps the price classes |
| `non_api_operations` | Recorded observations outside API pricing and the narrow local-cache exception |
| `cache_only_operations` | Strictly recognized local cache observations |
| `legacy_baseline_present`, `legacy_baseline_ambiguous` | Explicit opaque import state |
| `price_coverage_complete` | No price gap within the recorded scope |
| `usage_complete` | Always false; recorded price evidence cannot establish complete physical consumption |

An empty recorded scope has complete price coverage within that empty scope. Same-owner priced requests can still overlap, and priced operations with inexact usage remain separately visible. A terminal HTTP failure carrying known usage is not considered inexact solely because its transport status is unknown. This is a price-evidence check, not a remaining-dollar guarantee.

[Telemetry](../../backend/app/utils/telemetry.py) and [the orchestrator](../../backend/app/services/pipeline_orchestrator.py) propagate the projection without changing monetary totals. Saved flat and nested accounting views use the same cumulative read, so a settlement between attempt and cumulative reads does not create contradictory coverage states.

## Query cost and compatibility

An additive built-in SQL expression index, `usage_operations_cost_coverage`, stores the classification used by both grouping and price-gap lookup. Existing writers maintain the index automatically; it does not depend on a Python SQLite function. Unicode/control/NUL provider-label cases that need Python's exact normalization semantics use an indexed rare-row lookup and the existing quote validator. Strict JSON numeric types prevent boolean zero values from masquerading as cache counters.

Read-only access to an older ledger without the index remains correct and does not migrate the file. Quote shape, legacy ambiguity, old-writer updates, corrupted quote classification, admission races and absence of orphan token holds are covered by permanent tests. No operation/delta schema or existing cost grouping and six-decimal rounding semantics change.

The [comparison harness](../../backend/scripts/benchmark_cost_coverage.py) measures complete current read methods with symmetric `INDEXED BY` and `NOT INDEXED` adapters. Each sample includes both production SQL reads and Python aggregation. There are two warmups and 20 alternating samples per mode at each size; connection/transaction setup, writes and provider latency are excluded.

| Synthetic operation rows | Coverage projection without / with index | Price-gap admission without / with index |
|---:|---:|---:|
| 100 | 3.232500 / 0.072042 ms | 3.202313 / 0.019354 ms |
| 1,000 | 31.649605 / 0.286083 ms | 31.806438 / 0.027438 ms |
| 10,000 | 338.115958 / 2.310521 ms | 324.998812 / 0.048959 ms |

All unforced query plans also choose the expression index, and the full recorded snapshot remains unchanged. The fixtures contain valid priced, known, completed operations with normal ASCII attribution and no rare-validator rows. Full coverage grouping still scales with recorded operations. Index write, storage and initial-build costs remain unmeasured. These are local read-cost comparisons for the new feature, not a historical implementation comparison or production workflow speedup.

## Acceptance and independent review

The [paired SDK receipt](astra-cost-coverage-comparison.json) contains 30 scenario-mode invocations and 39 mock transmissions across shared synchronous and native synchronous/asynchronous paths. Only the pinned baseline dispatch helper and native attempt class are substituted; both modes retain the current ledger, meter, prices, SDKs and factory.

| Scenario | Baseline dispatch boundary | Current dispatch boundary |
|---|---|---|
| Prior unpriced known usage, then configured rates and USD 1 limit | One additional mock transmission | Zero additional transmissions; exact operation/delta/token history preserved |
| Disabled limits and empty configured prices | Admitted | Same request bytes, content and accounting |
| Fresh priced history or explicit configured zero price | Admitted | Admitted |
| Same-owner priced work in flight | Peak concurrency 2 | Peak concurrency 2 |

The final affected backend gate was rerun on September 9 because the interrupted session's temporary logs were no longer available. The new durable evidence records **1,363 passing tests, one existing absent-deployment parity skip, and zero failures/errors**. Independent review also passed all **97 new cases**: 63 ledger, 29 API boundary and five status/child cases. All ten changed Python files parse and pass Ruff without findings; shell smoke syntax passes. The isolated worktree has no private virtual environment, so `init.sh` was inspected and syntax-checked, not claimed as executed.

Review corrections cover SQL/Python normalization parity, boolean cache counters, a facade binding reset during validation, and a shared-producer binding reset while telemetry is disabled. Final source hashes match the files tested. Exact commands, source hashes, raw evidence hashes and independent audits are in [the verification receipt](astra-cost-coverage-verification.json).

Reproduce focused acceptance from the implementation worktree:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python -m pytest -o addopts= -q backend/tests/test_cost_coverage_ledger.py backend/tests/test_cost_coverage_boundary.py backend/tests/test_cost_coverage_status.py
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_cost_coverage.py --output /tmp/astra-cost-coverage-comparison.json
```

## Remaining work

The ACTIVE hourly loop next selects ASTRA-04b5b3b2: define run-wide price/cap and uncertainty policy, then atomically admit and settle joint token/dollar holds. Two priced requests can still share a dollar remainder. The retained offline reproducer admits two mock calls reporting $0.50 each against a $0.75 limit, leaving $1.00 recorded. This slice deliberately records that unresolved behavior. Future joint admission must check both capacities before any hold is written; the current token-denial path commits its policy, so independently adding a dollar hold first could commit an orphan hold.

Quotes remain estimates, and unknown outcomes must remain distinct from recorded consumption. Older writers creating new operations are not fully fenced by this per-request requirement. CLI physical provenance, run-wide rate/cap pinning, monetary holds, graph/report recovery and other recommendations remain open. No provider calls, saved-run mutation or deployment occurred. Local verification does not establish invoice accuracy, production savings, quality improvements or Pareto optimality. Publication remains blocked by the previously rejected GitHub authentication and was not retried without changed evidence.
