# ASTRA-04b4: durable detached simulation usage

The isolated implementation now writes simulation API observations into the existing parent ledger as requests happen. The change covers the parallel, Twitter-only, and Reddit-only entrypoints. New child JSON snapshots are explicitly diagnostic, and a matching parent-owned launch receipt prevents their aggregate totals from being charged again. The full ASTRA-04 recommendation remains in progress.

## Why this slice exists

The refreshed [saved-run receipt](astra-simulation-run-evidence.json) rechecked 77 saved artifact hashes, 32 pipeline states, and 12 bounded logs without changes. All 43 direct simulation directories lacked `sim_llm_telemetry.json`. Four selected children had 221, 456, 648, and 212 saved model-boundary calls, while their parent RUN meter entries were absent. These counters overlap with the parent's embedded health counters and cannot establish historical token loss, physical retry counts, or measured zero use.

Source review found two paths: shared `LLMClient` attempts that disappeared with detached process memory, and direct CAMEL SDK requests that bypassed that meter. The old child wrapper saw final logical responses, including synthetic CLI/fallback responses, so summing a new physical meter with that wrapper would double count.

## Current behavior and invariants

- The parent persists a launch receipt before dispatch. An explicit environment context pins parent run/attempt, launch token, simulation identity, ledger location, configuration hash and budget settings. The runner removes any ambient context for unrelated launches.
- A child joins existing storage without invoking its initializer. Missing storage, foreign lineage, changed configuration and malformed context stop before provider work. Its API operation IDs contain a launch prefix while retaining the same parent attempt owner.
- The direct CAMEL hook records each actual synchronous or asynchronous HTTP send, preserving existing SDK retry policy. It settles usage before native-tool or structured-output parsing; empty content and parse failures therefore retain returned usage. Reported malformed usage remains an explicit accounting error. Transport failures remain unknown outcomes.
- Both shared LLMClient and direct CAMEL dispatches use the scoped identities. Child completion checks its own durable unfinished work and run-wide accounting errors/prior-owner uncertainty; live siblings can continue. Parent final completion still checks every unfinished operation.
- New-authority child snapshots require the exact persisted launch receipt, including when authority or token fields are removed or altered. Their totals remain child-scoped, success-only diagnostics. Simulations without shared launch history retain the prior legacy high-water importer.
- CLI successful logical text estimates remain recorded even when optional telemetry is disabled. Failed or retried CLI subprocess usage is still incomplete; subscription pricing and unknown coverage remain explicit.

## Offline evidence

The final affected backend gate passed **960 tests**, with **one skip** because the worktree has no deployed DeerFlow checkout for a deployment-parity check. Independent review passed **69 focused cases**: 38 native CAMEL/SDK tests and 31 detached-process/shared-ledger tests. All 15 changed Python files parse; Ruff found no new findings and matched 18 pre-existing findings in the legacy scripts and OASIS helper against baseline source.

The process scenario uses the actual SDK over `httpx.MockTransport`: 429 → empty response reporting usage → success preserves **3 calls and 180 tokens before any child JSON flush**. An abrupt `os._exit(23)` leaves an unfinished durable operation; attachment under a new parent attempt rejects admission. Concurrent pool workers retain RUN attribution. An already-admitted child's bootstrap observes parent spend committed after launch-context creation and makes zero provider calls when that recorded budget is exceeded. Replaying a matching diagnostic snapshot adds zero tokens.

The [comparison](astra-simulation-comparison.json) isolates native transport instrumentation using the baseline and current factory, the same fixture model, actual synchronous SDK calls, and current durable meter. Both sides make two requests in the retry case. Direct ledger capture changes from zero to two attempts, retaining the successful response's 15 tokens and one unknown outcome. Empty and parser-failed responses each retain 15 tokens. Baseline zero means no direct capture at that boundary before compatibility export, not zero historical consumption.

Across 30 successful mock requests per side, median local elapsed time was **0.1847 ms → 1.2600 ms**, or **1.0753 ms added overhead**. One-sample scenario timings are omitted because they are sensitive to warm-up/order. This is neither production latency nor a measured workflow speedup.

Exact commands, hashes, checks and limits are in [the machine-readable verification](astra-simulation-verification.json). Reproduce the comparison from the isolated worktree:

```bash
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python \
  backend/scripts/benchmark_simulation_usage.py \
  --baseline-ref b9a9f79 \
  --output /tmp/astra-simulation-comparison.json
```

## Remaining boundaries

Native coverage is for factory-installed SDK clients and nonstreaming chat, native tools and structured parse requests. Unsupported streaming stops before send. Externally copied/replaced SDK clients need separate validation. Existing platform completion events precede posthoc work; this change does not redesign that simulation lifecycle. Shared parent ownership does not prove child liveness or provide a child lease. Same-parent automatic replacement and concurrent budget reservations remain separate work.

These commits are local and undeployed. No provider requests, runtime synchronization, saved-run mutation or production performance proof occurred. The hourly improvement loop remains active; full workflow optimality has not been established.
