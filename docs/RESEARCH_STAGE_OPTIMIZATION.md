# Research-Stage Token Optimization — Recommendations

**Status:** Proposal for owner sign-off. Written 2026-08-17 by the continuous-improvement loop
(evidence: 7-agent forensic audit of run telemetry + logs; reference run `pipe_f23527f7d903`,
`backend/uploads/pipelines/*/telemetry.json`, `research_progress.log`).
The levers below change *research product behavior* (breadth, depth, source coverage), so unlike
the already-landed containment work (loop-i1..i4), none of them is enabled unilaterally.

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
| 1 | **Provider prompt caching** | Enable/verify prefix caching for the re-sent thread on providers that support it (Anthropic prompt caching via CLI/OAuth path; MiniMax: verify support). Implementation lives in `deerflow_bridge/deerflow_research.py` + provider adapters. | Largest single lever — most of the 78M input is a stable prefix re-sent verbatim | None on output quality; billing-dependent (cached tokens are discounted, not free) | **Do first.** No behavior change. Needs a short spike to confirm each provider's caching semantics. |
| 2 | **Inter-phase thread compaction** | Tune the existing lead-agent `trim_tokens_to_summarize` down so each phase starts from a compact evidence brief instead of full history. | Breaks the quadratic growth curve | Mild: later phases see summaries, not raw transcripts. The final-dossier contract already tolerates this (the 80K summarization landed Jul-12). | **Do second**, A/B one run at a lower trim threshold before making it default. |
| 3 | **Track topology 3→2** | `RESEARCH_PARALLEL_TRACKS=2`, or keep 3 but seed tracks B/C with track A's evidence pack instead of independent web re-reads. | ~⅓ of research spend | Real: reduces independent-angle diversity that the merge step exploits. The shared-evidence variant preserves angle diversity but weakens independence. | Owner call. If cost matters more than marginal source diversity, shared-evidence seeding is the better variant. |
| 4 | **Phase budget multiplier** | Lower `RESEARCH_PHASE_BUDGET_MULT` (recursion limits 330–540 today). | Linear in the cut | Real: caps depth on hard questions; convergence gates may fire earlier with thinner evidence. | Only if 1+2 prove insufficient. |
| 5 | **Fanout width 8→5** | `RESEARCH_FANOUT_WIDTH=5`. | Moderate | Real: fewer parallel KIQ probes per phase. | Only alongside a quality check on KIQ coverage. |

## What already landed (no sign-off needed, commits `3306af8`..`fd3e077`)

- Failed/reset research attempts now meter their spend (the 31.9M-vs-5.35M gap) — you can finally
  *see* true research cost per run in `run_telemetry.json`.
- Run-level provider-outage halt stops doomed-call grinds (26h incident class).
- Graph stage no longer re-derives research output via LLM for cast-irrelevant chunks, and its
  spend is metered and budget-enforceable — so `LLM_RUN_BUDGET_TOKENS` is now a real safety net
  for any research-stage experiment above.

## Suggested experiment protocol

1. Pick one completed historical question; re-run research-only (no graph/sim/report) with lever 1,
   then 1+2, then 1+2+3-shared. Compare: total tokens, wall clock, source count/overlap,
   dossier-contract quality gates, and the final forecast's spine inputs.
2. Gate each step on the existing research quality gates (global-synthesis judge, dossier
   contract) rather than eyeballing.
3. `LLM_RUN_BUDGET_TOKENS` set to ~30M for these experiment runs so a regression can't silently
   burn a full-run budget.
