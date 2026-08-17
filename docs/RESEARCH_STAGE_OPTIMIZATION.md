# Research-Stage Token Optimization — Recommendations

**Status:** Levers 1–2 mechanism landed 2026-08-18 (loop iteration 7): lever 1 (provider prompt
caching) is **implemented and ON by default** with a kill-switch, and lever 2 (inter-phase
compaction) is **wired behind an env knob with today's default unchanged**. Levers 3–5 remain
proposals for owner sign-off. Originally written 2026-08-17 by the continuous-improvement loop
(evidence: 7-agent forensic audit of run telemetry + logs; reference run `pipe_f23527f7d903`,
`backend/uploads/pipelines/*/telemetry.json`, `research_progress.log`).
Levers 2–5 change *research product behavior* (breadth, depth, source coverage), so none of them
is enabled unilaterally; lever 1 does not change outputs, only billing/latency.

## The measured problem

The DeerFlow 2 research stage consumes **~80M tokens per run — 96% of all metered spend — at a
49:1 input:output ratio** (78.16M in / 1.59M out on the reference run). The mechanism is
architectural, not waste in any single prompt:

- Every model call re-sends the accumulated LangGraph thread. Input grows roughly quadratically
  with thread length; single agentic turns were measured at **22.16M input tokens** even after the
  Jul-12 80K-summarization fix.
- This multiplies across **3 parallel angle-specialized tracks** (`RESEARCH_PARALLEL_TRACKS=3`),
  each running **5 fixed phases** with recursion limits 330–540, plus per-KIQ fanout width 8
  (`RESEARCH_FANOUT_WIDTH`) and harness subagents (`DEERFLOW_SUBAGENTS=true`).
- Tool-level dedup exists (SQLite budget ledger, search/fetch caches) but there is **no dedup at
  the LLM-context level**: the three tracks independently accumulate and re-transmit overlapping
  evidence about the same question, merged only at the end (`merge_sources_union` /
  `merge_track_reports`).

## Ranked levers (decide per row)

| # | Lever | Change | Est. saving | Product impact / risk | Recommendation |
|---|-------|--------|-------------|----------------------|----------------|
| 1 | **Provider prompt caching** | **IMPLEMENTED (2026-08-18, default ON).** `ClaudeChatModel` (`deerflow_bridge/patches/models/claude_provider.py`) is a direct Anthropic Messages-API client (langchain-anthropic subclass, not a CLI shell-out); it now places ≤4 `cache_control: {type: ephemeral}` breakpoints — one anchor on the last system block (render order tools→system→messages, so that single marker caches tools + system) plus incremental markers on the newest turns — on **both** the API-key and the Code-plan OAuth path (the provider previously force-disabled caching for OAuth). Kill-switch: `DEERFLOW_CLAUDE_PROMPT_CACHE=0`. MiniMax/Quotio (OpenAI-compatible) rely on server-side prefix caching; both patched adapters were audited and mutate requests deterministically (no timestamps/UUIDs/reordering in the prefix), so no client-side machinery was added. Usage totals stay honest: langchain-anthropic folds `cache_read`/`cache_creation` back into `usage_metadata.input_tokens`, which every meter (bridge `_model_response_usage`, harness token-usage middleware) already consumes. | Largest single lever — most of the 78M input is a stable prefix re-sent verbatim; cache reads bill ~0.1× (writes 1.25×) | None on output quality. **Billing-semantics caveat:** dollar-level savings on the *subscription* (OAuth) path need a paid live spike to confirm — offline work cannot observe `cache_read_input_tokens`. | Landed. Verify with `usage.cache_read_input_tokens > 0` on the first live run; flip `DEERFLOW_CLAUDE_PROMPT_CACHE=0` if the API ever rejects a payload. |
| 2 | **Inter-phase thread compaction** | **WIRED, DEFAULT UNCHANGED (2026-08-18).** `RESEARCH_TRIM_TOKENS_TO_SUMMARIZE` now overrides `trim_tokens_to_summarize` at the single `DeerFlowSummarizationMiddleware` construction site (`deerflow_bridge/patches/middlewares/summarization_middleware.py`). Unset → today's behavior byte-for-byte (config `null` → summarize the complete discarded segment); positive integer → cap the summarizer's input; `none`/`null`/`full` → force the full segment. | Breaks the quadratic growth curve once a lower value is chosen | Mild: later phases see summaries, not raw transcripts. The final-dossier contract already tolerates this (the 80K summarization landed Jul-12). | Mechanism landed; **default not flipped**. A/B one run via the env knob before changing `config.yaml`. |
| 3 | **Track topology 3→2** | `RESEARCH_PARALLEL_TRACKS=2`, or keep 3 but seed tracks B/C with track A's evidence pack instead of independent web re-reads. | ~⅓ of research spend | Real: reduces independent-angle diversity that the merge step exploits. The shared-evidence variant preserves angle diversity but weakens independence. | Owner call. If cost matters more than marginal source diversity, shared-evidence seeding is the better variant. |
| 4 | **Phase budget multiplier** | Lower `RESEARCH_PHASE_BUDGET_MULT` (recursion limits 330–540 today). | Linear in the cut | Real: caps depth on hard questions; convergence gates may fire earlier with thinner evidence. | Only if 1+2 prove insufficient. |
| 5 | **Fanout width 8→5** | `RESEARCH_FANOUT_WIDTH=5`. | Moderate | Real: fewer parallel KIQ probes per phase. | Only alongside a quality check on KIQ coverage. |

## What already landed (no sign-off needed)

Commits `3306af8`..`fd3e077` (loop-i1..i6):

- Failed/reset research attempts now meter their spend (the 31.9M-vs-5.35M gap) — you can finally
  *see* true research cost per run in `run_telemetry.json`.
- Run-level provider-outage halt stops doomed-call grinds (26h incident class).
- Graph stage no longer re-derives research output via LLM for cast-irrelevant chunks, and its
  spend is metered and budget-enforceable — so `LLM_RUN_BUDGET_TOKENS` is now a real safety net
  for any research-stage experiment above.

Loop iteration 7 (2026-08-18, this change):

- **Lever 1 implemented:** Anthropic prompt caching ON by default for the research provider,
  OAuth/subscription path included (`DEERFLOW_CLAUDE_PROMPT_CACHE=0` to kill). Breakpoint
  placement is strip-first and capped at the API's 4-marker limit; `thinking` blocks are never
  marked (they reject `cache_control`). Offline payload tests:
  `backend/tests/test_claude_prompt_cache.py` (includes a tracked-vs-deployed byte-identity guard
  for `patches/models` + `patches/middlewares`).
- **Lever 2 wired:** `RESEARCH_TRIM_TOKENS_TO_SUMMARIZE` env knob, default preserved exactly when
  unset. Pinning tests: `deerflow_bridge/patches/tests/test_summarization_trim_env.py`
  (`test_unset_env_preserves_explicit_null` is the default-preservation proof).
- **Cache-hostility audit of the OpenAI-compatible paths:** `PatchedChatOpenAI` (Quotio/gemini)
  only re-injects stored per-message `thought_signature`s (deterministic replay);
  `PatchedChatMiniMax` adds a constant `extra_body.reasoning_split` flag and deterministically
  strips per-message user `name`s. Neither injects timestamps/UUIDs nor reorders the prefix, so
  provider-side automatic prefix caching is unimpeded and no client change was needed.

## Suggested experiment protocol

1. Pick one completed historical question; re-run research-only (no graph/sim/report) with lever 1,
   then 1+2, then 1+2+3-shared. Compare: total tokens, wall clock, source count/overlap,
   dossier-contract quality gates, and the final forecast's spine inputs.
2. Gate each step on the existing research quality gates (global-synthesis judge, dossier
   contract) rather than eyeballing.
3. `LLM_RUN_BUDGET_TOKENS` set to ~30M for these experiment runs so a regression can't silently
   burn a full-run budget.
