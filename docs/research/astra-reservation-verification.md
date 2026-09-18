# ASTRA-04b5b1: transactional token allowance

Token-enabled durable API calls now reserve their planned allowance before dispatch. Competing calls and retries cannot each consume the same recorded remainder. Responses commit actual usage and update the hold together; missing or uncertain usage retains an explicit allowance. This completes the API token-reservation core, with broader ASTRA-04b5b coverage still in progress.

## Evidence and behavior

The saved-run refresh revalidated 77 artifact hashes, 32 pipeline states and 12 bounded logs. None changed, and unchanged logs were not reparsed. Selected historical runs enabled both simulation platforms, but their saved artifacts do not establish peak physical overlap, enabled budget caps or actual budget overshoot. Source shows concurrency through platform `asyncio.gather`, semaphores and ensemble workers. Both API paths previously checked recorded totals before inserting a zero-counter dispatch marker, leaving a race for the same remainder.

The new [request planner](../../backend/app/utils/api_budget.py) includes messages, function tools, tool choices and structured-output schema in a versioned UTF-8 JSON byte estimate. It requires one explicit positive output limit and one text completion when durable token planning is enabled. Shared-client requests use a private copy so caller mutation cannot change the request after planning. Unsupported media, hosted search, prediction and SDK body overrides fail before dispatch. Request content is never stored in the reservation ledger.

[UsageLedger](../../backend/app/utils/usage_ledger.py) stores the allowance and dispatch marker in the same `BEGIN IMMEDIATE` transaction. Admission checks:

```text
recorded cumulative tokens + outstanding held tokens + new planned tokens <= pinned run cap
```

The first enabled reservation pins the run cap, including when that request has insufficient allowance. A later different or disabled cap cannot silently bypass it. Policy rejections are recoverable; restoring the pinned configuration permits eligible work without resetting accounting. The database trigger blocks older marker-aware writers from creating unreserved API dispatches after activation.

Settlement uses the existing high-water operation and delta transaction. Precisely reported terminal usage releases the hold, including an HTTP error with known usage. Unknown transport outcomes and successful responses with only estimated usage retain `max(planned tokens - recorded operation tokens, 0)`. Actual usage is never clipped to the plan. No timeout or restart automatically releases uncertainty. A prior accounting-error status retains its existing conservative recovery rule even if a later known error response releases the token hold.

[Shared LLMClient](../../backend/app/utils/llm_client.py) and [native OASIS SDK capture](../../backend/app/utils/oasis_usage.py) both apply this at the physical dispatch boundary. The response checks its captured token cap even if configuration changes while the request is in flight. Costs keep the existing recorded-usage check. [Status output](../../backend/app/api/research.py) distinguishes recorded remaining tokens from planned holds, subtracts holds from available allowance, and exposes a mismatch between configuration and the pinned run policy. Pipeline and meter telemetry files retain the same reservation state.

## Comparative offline results

The [comparison harness](../../backend/scripts/benchmark_api_reservations.py) loads only the baseline `LLMClient._create_openai_completion` method from `5924dd4`. Both sides use the same current retry loop, OpenAI SDK, meter and ledger schema, in separate temporary runs. Baseline runs have no reservation policy. All transmissions terminate at `httpx.MockTransport`; no provider or network is used.

| Scenario | Baseline | Current | Interpretation |
|---|---:|---:|---|
| Competing same-owner calls, cap 20 | 2 transmissions; 30 recorded tokens | 1 transmission; 15 recorded tokens | The competing request is rejected before dispatch; the admitted call completes. |
| Lost-response retries, cap 28 | 3 transmissions with unknown usage | 2 transmissions; 28 planned tokens held | Another retry cannot reuse the uncertain allowance. Recorded zero is not known zero consumption. |
| Successful calls, 30 timed samples | 2.1434 ms median | 2.2144 ms median | About 0.0710 ms additional local overhead in this fixture. |

Success timing includes three untimed warm-ups on each side, then alternating measurement order. Each side records 33 calls and 495 tokens with unchanged response content. Concurrency and timeout scenarios carry no latency claim. The empty-message input estimate is four tokens, while the fixture reports ten input tokens, deliberately preserving the distinction between an estimate and actual reported usage.

These results show avoided dispatch in the specified enabled-cap scenarios. They do not prove production speedups, historical overshoot, invoice savings or a global Pareto optimum. Reservation checks trade a little local overhead and conservative admission for reduced avoidable provider work under a configured allowance.

## Verification

The final combined gate passed **1,072 backend tests**, with one deployment-parity check skipped because the deployed DeerFlow checkout is absent. This includes the preceding 998 cases and **74 new cases**: 30 ledger/facade, 39 actual API/planner and five saved/status scenarios. All 15 changed Python files pass Ruff and parsing; `git diff --check` passes.

Independent review passed all 74 new cases. An additional probe loaded the actual baseline ledger implementation: old initialization preserved the extension, an old fresh marker was blocked, and an old terminal observation recorded ten tokens while retaining its sixty-token hold. Current precise reconciliation then released the hold. This supports the marker-aware compatibility claim; it does not cover code that never wrote physical dispatch markers.

Permanent scenarios cover simultaneous processes, crash-held allowance, write rollback, replay/growth, estimated and malformed usage, precise late settlement, partial schema loss, actual SDK retry and parser boundaries, caller mutation, configuration changes, and read-only restarted status. Exact commands, source hashes, measurements and limits are retained in [the verification receipt](astra-reservation-verification.json); [saved evidence](astra-reservation-run-evidence.json) and [comparison results](astra-reservation-comparison.json) are separately hashed there.

Reproduce the comparative fixture from the implementation worktree:

```bash
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_api_reservations.py --samples 30 --output /tmp/astra-reservation-comparison.json
```

## Operating limits and remaining work

Token budgets default to zero. This slice does not change those defaults, add monetary reservations, bound CLI processes, or complete research-provider accounting. Input tokenization and provider behavior can exceed the estimate; no hard provider-token or invoice ceiling is claimed.

Native production model creation currently omits an output cap. Enabling token reservations therefore stops those uncapped requests before dispatch. The next tracked slice must expose an explicit operator-configured native output cap and preserve the uncapped default, carrying it through normal factory and child configuration. Tests here supply an explicit cap to the actual native SDK; they do not claim that the production factory already has that configuration path.

Opaque legacy baselines and historical completed estimates remain recorded history. Activation rejects unreserved in-flight, unknown and accounting-error API observations but cannot reconstruct absent physical history. Partial loss of the reservation extension fails closed; all extension objects absent remains compatible with old v1 storage, and positive reservations require parent initialization. Neither this nor marker-aware compatibility is protection against arbitrary database tampering or pre-protocol code.

Outstanding-hold and cumulative-token reads still scale with stored history. No automatic expiry, policy replacement, live deployment or new paid run is part of this slice. Original checkout edits, saved artifacts, services and credentials were preserved. The hourly improvement loop remains active; publication still depends on resolving the existing GitHub authentication rejection.
