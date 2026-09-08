# ASTRA-04b5b2: explicit native simulation output policy

Native simulations can now use an explicitly configured output limit with the existing token-reservation guard. The production factory previously supplied no output cap, so enabling token planning stopped those requests before dispatch. The new path carries a validated policy from parent launch through child imports and actual synchronous/asynchronous SDK requests. This slice is verified offline; full ASTRA-04 remains in progress.

## Configuration and behavior

The two settings in [.env.example](../../.env.example) default to:

```dotenv
OASIS_MAX_OUTPUT_TOKENS=0
OASIS_OUTPUT_TOKEN_PARAMETER=max_tokens
```

Zero preserves existing uncapped native requests. To use an explicit limit, an operator selects a positive integer and the parameter supported by the configured endpoint: `max_tokens` or `max_completion_tokens`. Both general and boost routes use the same policy. A positive cap can truncate generated output; this change does not choose a cap or change quality defaults. CLI models bypass this native policy. A positive durable token budget still rejects an uncapped request before transmission.

[The validator](../../backend/app/utils/oasis_output_policy.py) accepts an integer from zero through 2**53, rejects booleans and malformed policy objects, and returns independent metadata. Bound child policy takes precedence over later environment or Config changes. Unbound models read Config at creation; Config reads environment values at import. The cap is captured once, including zero.

[The factory](../../backend/app/utils/oasis_llm.py) injects a positive cap into copied SDK request options for chat and structured parse. It removes conflicting cap keys from copied arguments and `extra_body`, then inserts exactly the selected field. It preserves CAMEL's `model_config_dict`: installed CAMEL also treats its `max_tokens` setting as the agent memory limit. An initial direct model-config approach failed an actual ChatAgent reproduction, where an output limit of 8 rejected a 100-token system prompt. Request-boundary injection fixes that coupling. The permanent actual-ChatAgent test retains a system message longer than the output limit and confirms that the SDK still receives the cap.

Kimi's existing SDK replacement reinstalls the captured policy. Existing fallback receives the positive numerical limit through `LLMClient.chat(max_tokens=...)`; its legacy parameter API and routing are unchanged. Endpoint support is not inferred or validated by offline tests.

## Launch authority, resume and seals

[The parent](../../backend/app/services/pipeline_orchestrator.py) saves `native_output_policy` with launch ownership before child dispatch. New authorities also bind the token and dollar budget values. [Child setup](../../backend/app/utils/simulation_usage.py) validates exact receipt equality, pins both output settings in a private environment before Config imports, and exposes an independent bound policy after bootstrap. Altering, removing or injecting policy or changing a new receipt's budgets fails before model work.

Historical five-field authorities remain readable and resolve to a fixed disabled native policy. The first policy-aware launch of a simulation with only legacy history records the current configured policy. Every later launch of that simulation in the same parent pipeline must match all previously saved policy-aware receipts. A changed or malformed saved policy rejects the launch before another receipt is inserted. This rule also holds after `PipelineManager.load` and `PipelineState.from_dict` reload saved state. A new simulation can use a different policy; standalone models do not acquire a cross-process resume guarantee.

The policy is launch metadata. It does not change `simulation_config.json`, actor/context/role manifests or existing seals. Acceptance sends native requests from a child using the actual sealed fixture, verifies every artifact hash afterward, and revalidates both service and direct-child seals.

## Comparative evidence and acceptance

The [reproducible comparison](../../backend/scripts/benchmark_native_output_policy.py) substitutes only `_create_openai_model` from pinned baseline `b0532c91b6a90f2ce31a467aec82b43cb4d75688`. Both sides use current reservations, real CAMEL `ModelFactory`/`OpenAIModel`, installed SDKs and isolated temporary ledgers. SDK clients receive local `MockTransport` before any send; network attempts are guarded and remain zero. This isolates the missing factory configuration path, rather than replaying historical production behavior.

| Scenario, repeated synchronously and asynchronously | Baseline | Current |
|---|---|---|
| Explicit cap 10, run token budget 1,000; either cap parameter | Rejected; zero transmissions | One transmission with selected cap 10 |
| Output cap and run token budget disabled | One uncapped request | Identical serialized request, content and reported usage |
| Output cap disabled, token budget enabled | Rejected; zero transmissions | Rejected; zero transmissions |

Each admitted capped fixture holds 25 planned tokens before response: 15 estimated input plus 10 output allowance. It records 15 reported tokens and releases the remaining hold. CAMEL context capacity stays 128,000 on both sides. The 16 factory invocations produce eight mock transmissions across isolated cases. There is no timing measurement or production speedup claim.

The final backend gate passed **1,165 tests with one existing absent-deployment parity skip**. Independent review passed all **67 new cases**: 37 factory and 30 launch scenarios. Eight changed Python files parse; differential Ruff retains the same two existing `oasis_llm.py` findings and introduces none. Tests additionally cover tools, parse, boost/Kimi replacement, fallback, invalid configuration, private request copies, legacy authorities, persisted reload and real sealed artifacts. The final source hashes and full commands are in [the verification receipt](astra-native-cap-verification.json); actual scenario data are in [the comparison receipt](astra-native-cap-comparison.json).

Reproduce the targeted acceptance from the implementation worktree:

```sh
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python -m pytest -o addopts= -q backend/tests/test_oasis_output_policy.py backend/tests/test_native_output_launch.py backend/tests/test_simulation_config_seal.py
/Users/rogerlin/Downloads/DeepResearchForecast/backend/.venv/bin/python backend/scripts/benchmark_native_output_policy.py --output /tmp/astra-native-cap-comparison.json
```

The [saved-run refresh](astra-native-cap-run-evidence.json) revalidated 77 artifacts, 32 states and 12 bounded logs with no changes. The four inspected manifests/configurations contain no matching explicit cap or budget fields. Their historical settings remain unknown, and missing RUN/child usage snapshots remain gaps. Those logs were not reparsed merely to repeat unchanged counts.

## Limits and next work

Provider/model compatibility and output quality under a chosen positive cap remain unmeasured. Arbitrary custom SDK replacement after construction needs its own integration. Input planning remains an estimate; incomplete research/CLI provenance and old running processes are not retroactively covered. There is no hard invoice cap, latency gain, historical overshoot estimate or global Pareto-optimality claim.

ASTRA-04b5b2 closes the explicit native-cap configuration gap, `ASTRA-TOKEN-01`. The hourly loop remains active. Next is ASTRA-04b5b3: determine a defensible monetary reservation policy from existing rate and uncertainty contracts, with an offline reproducer before implementation. Broader graph/report recovery and other recommendations remain pending. No provider call, runtime deployment, saved-run mutation or retry of rejected GitHub authentication occurred.
