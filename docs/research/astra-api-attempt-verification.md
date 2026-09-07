# ASTRA-04b2: physical API attempts and retry ownership

Status: verified offline. Full ASTRA-04 remains in progress. The implementation is on the isolated optimization branch and has not been deployed.

The backend previously enabled the OpenAI SDK's default retries whenever custom HTTP transport construction was unavailable. Combined with the application's three-attempt loop, one logical failure could cause nine transport requests. Usage was also recorded only after successful content validation, so an empty response could consume tokens without advancing the budget before a retry.

## Behavior and invariants

Ordinary chat and native-tool calls now use one SDK dispatch helper. It pins the run, stage, provider and model; checks recorded limits; writes a zero-counter in-flight operation before dispatch; and settles the same UUID with returned usage or an explicit unknown outcome. Usage is recorded before choices, content or tool arguments are validated. SDK retries are disabled at construction and with request-local options on shared clients. The shared client's defaults are not mutated.

Budget and accounting failures stop retry and fallback. Malformed counters, invalid usage objects, impossible cache observations and positive partial usage produce a typed accounting stop. Missing usage can still produce explicitly labeled text estimates. `completed` means response usage was settled; content validation can subsequently fail. Unknown operation counters must not be interpreted as proof of zero spend.

The actual fast or fallback provider controls request options, cost attribution, circuit state and cache ownership. The legacy last-response receipt is thread-local and cleared between calls; the durable attempt ledger is the backend API accounting authority.

## Reproduced effects

These measurements use the actual installed SDK with `httpx.MockTransport`, temporary SQLite ledgers and no network. Both revisions share current telemetry. Backoff sleeps are disabled. [Machine-readable comparison](astra-api-attempt-comparison.json).

| Scenario | Before | After | Meaning |
|---|---:|---:|---|
| Rate-limited chat | 9 transport requests | 3 | Application owns retries |
| Rate-limited native tools | 9 transport requests | 3 | Same shared dispatch boundary |
| Empty response using 120 tokens, then success using 60 | 60 recorded tokens / 1 call | 180 recorded tokens / 2 calls | Corrected coverage, not additional consumption |
| Empty response using 25 tokens with a 10-token limit | 3 requests / no tokens recorded | 1 request / 25 tokens recorded | Subsequent retries blocked; the first request can still exceed the cap |
| Successful request, median of 30 local samples | 0.844 ms | 1.318 ms | Approximately 0.474 ms added local accounting overhead |

Single failure-case timings in the JSON include initialization effects and are not latency benchmarks. The successful-request median describes this host and fixture, not production performance.

## Historical evidence

The [saved-run refresh](astra-api-run-evidence.json) revalidated 77 saved-artifact hashes and found the same 32 pipeline states, newest created July 15, 2026. Twelve bounded application log files were reviewed. Selected July 9, July 15 and July 16 rotated files contained 2,287, 14,039 and 413 retry-scheduling lines respectively. These overlap with rate-limit and fallback messages and cannot be added into a distinct-request count. No matching API empty-content marker was found in the inspected files; that failure is supported by source and offline reproduction, without a quantified historical loss claim.

## Validation and reproduction

The final affected backend gate passed **741 tests**, with one deployment-parity check skipped because this worktree has no deployed DeerFlow checkout. It covers the new attempt boundary plus durable ledger, pipeline completion, research, graph, report and prior contract regressions. All seven changed Python files pass Ruff and parsing. Independent review ran 85 affected tests and has no remaining blocking finding in this slice. Exact commands, skipped-test reason, source hashes and review resolutions are in [the verification receipt](astra-api-attempt-verification.json).

From the implementation worktree, use the existing dependency environment:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_api_attempts.py --baseline-ref a1180cc --repetitions 30 --output /tmp/astra-api-attempt-comparison.json
```

This comparison writes only its requested output and temporary fixture ledgers. It does not call a provider. The verification receipt contains the complete backend regression command for reproduction.

## Remaining accounting work

The in-flight marker is not a reservation. Concurrent calls can still pass recorded-budget checks together, and missing provider usage cannot establish a hard invoice bound. Detached simulation exports still carry only final logical-call usage rather than every child physical attempt. Unresolved zero-counter markers are retained in the operation table but are not yet counted in cumulative projections. CLI physical provenance and pre-delivery crash gaps remain incomplete. `usage_complete` must remain false.

The next bounded iteration should expose unresolved operations and define their recovery/admission policy, with restart and settlement tests, before adding transactional reservation enforcement. It should then close detached-child transport coverage. Graph episode recovery and report persistence remain subsequent measured priorities. The hourly same-task improvement loop is active; production speedup and Pareto optimality have not been established.
