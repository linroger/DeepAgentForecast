# Captured API pricing — ASTRA-04b5b3a

The shared LLMClient and instrumented native CAMEL request paths now capture an estimated price before each physical API dispatch and preserve it through settlement, replay and detached-child recovery. This closes a prerequisite for monetary reservations. Full ASTRA-04 remains in progress.

## Problem and historical evidence

The earlier API boundary recomputed costs from ambient provider rates on every observation. An unchanged operation replayed after a rate increase could therefore add cost without adding calls or tokens. An unknown provider or a zero-rate placeholder could also record positive usage at zero dollars and pass an enabled dollar guard. The legacy override parser accepted malformed or invalid values too loosely for a monetary admission contract.

The [saved-run receipt](astra-monetary-run-evidence.json) revalidates 77 artifacts, the exact 32-state inventory and 12 bounded logs. They are unchanged, so prior bounded log aggregates were reused. Four selected current costs are $26.279737, $0.809589, $0.934069 and $1.090763; three cumulative costs are $9.883604, $1.912461 and $1.099359. These scopes overlap and must not be added together. They agree with the pinned rough MiniMax formula to six decimals. This establishes arithmetic consistency, not historical rate configuration or invoice accuracy. The saved `cost_estimated=false` labels are preserved as raw historical values. The selected artifacts contain no qualifying historical price, dollar-cap or invoice receipt fields.

## Price contract and request behavior

[api_cost.py](../../backend/app/utils/api_cost.py) resolves an exact `provider:model` override first, then a provider override, then an existing positive provider builtin. Provider keys normalize to lowercase; model case remains significant. `LLM_COST_PER_MTOK` values remain dollars per million tokens. The captured `api-cost-quote/v1` stores dollars per thousand tokens and the selected source, key, provider and model.

The complete configured JSON object must contain unique normalized keys and exactly two finite, nonnegative numeric rates per key. Boolean values, malformed JSON, duplicate keys, invalid lengths and excessive numeric values fail before dispatch with a recoverable `BudgetExceeded`. Correcting configuration permits the next request without resetting accounting. This validation also applies with the dollar cap disabled; an empty configuration retains valid default behavior.

Missing pricing uses an `unpriced` quote with null rates. Numeric cost remains zero for compatibility, but it does not mean free usage. A positive dollar cap rejects a new unpriced API request before a marker or transmission. Explicitly configured `[0,0]` is a known zero price and remains admissible. No new aggregate price-coverage projection is introduced here, so old unpriced usage remains an open monetary-policy concern.

[The shared boundary](../../backend/app/utils/llm_client.py) validates that the private request model matches attribution and rejects `extra_body` model overrides. [The native boundary](../../backend/app/utils/oasis_usage.py) reads the actual serialized model. Each captures one quote before its physical attempt. Rates changing during the response cannot reprice that operation. Different later operations may still capture different settings.

## Durable consistency and recovery

[Telemetry](../../backend/app/utils/telemetry.py) accepts the quote as an independent value, computes its estimate and preserves estimated-cost labeling in both in-memory and durable accounting. Inclusive input tokens use the captured input rate; cache observations remain intact without inferred billing discounts. Crossed cumulative token observations are priced from their merged token high-water totals.

[The ledger](../../backend/app/utils/usage_ledger.py) stores the quote in existing operation metadata. A quoted operation cannot lose or change its quote, and an unquoted legacy operation cannot acquire one on replay. Existing cost grouping and six-decimal rounding remain unchanged.

Independent review found a compatibility defect in the first implementation: an actual older writer could copy unchanged quote metadata but calculate a different cost. A later replay would then produce a negative correction. The final implementation installs quoted INSERT and UPDATE consistency guards transactionally during initialization. They reject inconsistent cost formulas, attribution and estimated flags as well as quote loss or replacement. Ordinary quoted writes require both guards and do not repair missing storage guards. Current replay also validates the previously stored quoted counter before calculating any delta. The permanent regression loads the actual writer from baseline `186af1a6bcd5ec8587365b398dfeb49d98d2ef63` and checks both rejection of the inconsistent write and acceptance of the correct settlement.

Child acceptance records three attempts, 180 reported tokens and $0.00021 through retries. A separate abrupt exit with code 23 leaves the quote durably attached to the unfinished marker. Parent recovery of 1,000 input plus 1,000 output tokens uses the stored quote to record $0.003 even after ambient rates change. Token reservations, native output policies and existing recovery gates remain covered by the final regression suite.

## Comparative acceptance

The [comparison harness](../../backend/scripts/benchmark_api_cost_quotes.py) substitutes only the pinned shared completion helper and native attempt class. Both sides use the current ledger, real installed SDKs and native factory. Local mock transports are installed before instrumentation; network connection attempts and provider requests remain zero. This is an isolated comparison of the dispatch/accounting boundary, not a replay of a historical production environment.

| Scenario across shared synchronous and native synchronous/asynchronous paths | Baseline | Current |
|---|---|---|
| Missing price, positive dollar cap | One mock transmission | Rejected before transmission |
| Configured rates change from `[1,2]` to `[2,4]` during response | $0.006 recorded | $0.003 from the captured quote |
| Rates then change to `[3,6]` before exact same-operation replay | Adds $0.003 | Adds $0 |
| Empty price configuration and disabled dollar cap | $0.02 | $0.02, identical serialized request and response content |

All 18 paired-case invocations passed, with 15 mock transmissions and six accounting replays. Each admitted current operation has the same quote before transport and after settlement. There is no timing or historical savings measurement. See the complete [comparison receipt](astra-api-cost-comparison.json).

The final affected backend gate passed **1,266 tests with one existing absent-deployment parity skip**. Independent review passed all **101 new cases**: 42 ledger, 57 API boundary and two detached-child cases. All 13 changed Python files parse and pass Ruff without findings. Three existing recovery/budget test modules were adjusted to supply explicit fixture prices, preserve saved quotes or provide the transmitted model; the original budget-crossing and crash-recovery assertions remain. Exact commands, source hashes, raw gate hashes and the two earlier gate runs with fixture failures are recorded in [the verification receipt](astra-api-cost-verification.json).

Reproduce the targeted checks from the implementation worktree:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python -m pytest -o addopts= -q backend/tests/test_api_cost_ledger.py backend/tests/test_api_cost_boundary.py backend/tests/test_api_cost_child.py
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_api_cost_quotes.py --output /tmp/astra-api-cost-comparison.json
```

## Remaining work

The hourly improvement loop remains ACTIVE. ASTRA-04b5b3b must define monetary admission, run-wide price/cap pinning and incomplete historical price coverage before adding dollar holds. Concurrent requests can still share the same dollar remainder. Recorded usage must remain distinct from held estimates, and unknown outcomes must survive restart without invented consumption or automatic release.

Quotes are estimates; existing fallback rates are rough. This slice does not establish invoice accuracy, conservative cost bounds, cache economics, output-quality gains, production latency improvements or Pareto optimality. Legacy/non-API estimates and old writers creating new operations are not retroactively covered. Broader CLI provenance, graph/report recovery and other recommendations remain pending. No provider calls, saved-run mutation or deployment occurred. GitHub publication remains blocked by the previously rejected authentication; it was not retried without changed evidence.
