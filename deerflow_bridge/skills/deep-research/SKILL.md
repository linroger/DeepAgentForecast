---
name: deep-research
description: Use this skill instead of WebSearch for ANY question requiring web research. Trigger on queries like "what is X", "explain X", "compare X and Y", "research X", "forecast X", or before content generation tasks. Provides a systematic multi-angle research methodology with strict source-quality tiering — prioritize high-signal primary and reputable sources, reject SEO farms and aggregator slop. Use this proactively when the user's question needs online information.
---

# Deep Research Skill

## Mission

Produce **evidence-grounded, source-tiered research** efficiently. Every claim you carry forward must be traceable to a vetted source. You have a finite tool budget per run — spend it on high-signal sources, never on low-value ones. One authoritative source outranks five aggregators repeating it.

**Never generate content from general knowledge alone.** But also: never confuse *volume* of searching with *quality* of evidence. The goal is the smallest set of tool calls that yields a defensible, triangulated picture.

## Source Quality Framework

Judge every search result BEFORE fetching it, and every fetched page BEFORE citing it.

### Source tiers

| Tier | What | Examples | How to use |
|---|---|---|---|
| **S1 — Primary / authoritative** | Original data & documents from the actor or an official body | Government statistics (BLS, Eurostat, NBS, IMF, World Bank), regulator filings (SEC/EDGAR, FDA, central banks), peer-reviewed journals, court documents, earnings reports & call transcripts, official press releases, standards bodies, primary datasets | **Preferred for every load-bearing claim.** Quote numbers, dates and wording directly. |
| **S2 — High-quality secondary** | Original reporting & analysis with editorial standards and named accountability | Reuters, AP, Bloomberg, FT, WSJ, The Economist, Nikkei, Caixin; top-tier domain trade press; major research houses (Gartner, McKinsey — note their commercial angle); established think tanks (CSIS, Brookings, Carnegie — note ideological lean); industry associations (SEMI, SIA, IEA) | Good for synthesis, context, expert quotes, and as a **pointer back to the S1 origin**. |
| **S3 — Conditional** | Useful but biased, unvetted, or derivative — corroborate before relying on | Company blogs/marketing, vendor whitepapers, named-expert blogs/Substacks with credentials, conference talks, Wikipedia (use as a **map to its primary citations**, never as the citation itself), preprints (arXiv — not yet peer-reviewed) | Cite only when corroborated by S1/S2, or clearly labeled as the actor's own claim ("Company X claims…"). |
| **S4 — Reject** | Low-signal noise that wastes budget and contaminates conclusions | SEO content farms ("Top 10 X in 2026"), affiliate listicles, AI-generated aggregator slop, wire-reprint sites adding no reporting, undated/anonymous pages, forum threads (Reddit/Quora) presented as fact, stock/crypto-pump sites, sites that only rewrite other articles | **Do not fetch. Do not cite.** Skip these in search results without spending a tool call. (Exception: forums may be cited *as sentiment evidence only*, explicitly labeled as such.) |

### Signal heuristics — 8 quick checks

Scan these from the search snippet/URL before spending a fetch:

1. **Named author or institution** with relevant standing? Anonymous → suspect.
2. **Publication date visible** and recent enough for the claim? Undated → suspect.
3. **Original work** (reporting, data, analysis) or a rewrite of someone else's?
4. **Specificity**: concrete numbers, names, dates — or vague superlatives ("huge growth", "experts say")?
5. **Methodology / data sourcing disclosed** for any statistic?
6. **Incentive check**: who benefits if you believe this? (Vendor selling the solution, fund talking its book, advocacy group.)
7. **Domain reputation**: established outlet / .gov / .edu / known institution vs. keyword-stuffed domain (`best-ai-tools-2026.xyz`).
8. **Headline–content match**: clickbait framing usually signals thin content.

≥2 red flags → treat as S4 and move on without fetching.

### The circular-sourcing trap

When many outlets repeat the same striking number or quote, they are usually echoing **one** origin. Find that origin (the S1 document or the first S2 report), cite *it*, and count the claim as **one** source — not ten. A claim repeated 50 times from a single origin is exactly as strong as that origin. Also date-check statistics: a 2019 figure repackaged in a 2026 listicle is still a 2019 figure — say so.

## Research Protocol

### Phase 1 — Scope (no tools yet)

Before the first search, spend one thinking step: restate the question, list the 3–6 dimensions that must be covered (actors, mechanisms, data, counterarguments, trajectory), and draft the initial query set. A planned query set prevents redundant searching later.

### Phase 2 — Broad survey (cheap, wide)

2–4 searches across the main dimensions to map the territory. From the results: identify the key subtopics, the recurring primary sources (which S1 documents does everyone cite?), and the named experts/institutions worth targeting. **Do not fetch anything yet** unless a clear S1 source already surfaced.

### Phase 3 — Targeted deep dive (spend budget here)

For each dimension, in priority order:

1. **Search with precision**: entity names, specific metrics, document types — `"[company] 10-K 2025"`, `"[agency] [statistic] site:gov"`, `"[topic] peer-reviewed study"`, `"[expert name] [topic]"`.
2. **Hunt the primary**: prefer queries that surface S1 documents over commentary about them.
3. **Fetch selectively**: at most the **1–3 most load-bearing sources per dimension** — the ones your conclusions will actually rest on. Read those in full; for the rest, snippets suffice.
4. **Mine fetched pages**: an S2 article's own citations are a free map to S1 sources — follow the best one instead of issuing a fresh blind search.

### Phase 4 — Triangulate & stress-test

- Every **load-bearing claim** (a number, date, quote, or causal assertion your output depends on) needs **two independent S1/S2 sources** — independent meaning different origins, not two echoes of one report.
- Deliberately search for **disconfirmation**: `"[claim] criticism"`, `"[claim] debunked"`, `"[topic] risks"`, `"[forecast] skeptics"`. A picture with no contradictions found is a picture you haven't tested.
- When sources **conflict**, do not average silently: report the range, identify why they differ (definition, time window, methodology, incentive), and state which you weight higher and why.

### Phase 5 — Synthesis gate

Proceed to writing only when you can answer YES to all:

- [ ] Each major dimension is covered by at least one S1/S2 source?
- [ ] Every load-bearing number/quote is triangulated or explicitly flagged as single-source?
- [ ] I searched for the opposing case, not just confirmation?
- [ ] I know the *origin* of each key statistic (no circular sourcing)?
- [ ] Information is current, and anything dated is labeled with its true date?

Any NO → one more targeted pass on that gap only. Do not restart broad searching.

## Efficiency & Budget Discipline

Tool calls are metered (per-run limits on `web_search` and `web_fetch`). Waste = worse research, because budget spent on noise is unavailable for verification.

- **Plan, then search.** Never fire near-duplicate queries (`"AI chips market"` → `"market for AI chips"`). If a query returns weak results, change the *angle* (different entity, document type, or language), not the word order.
- **Triage from snippets.** Apply the 8 signal checks to search results and discard S4 hits without fetching.
- **Fetch with intent.** Each `web_fetch` should answer a specific question you can name. If you can't name it, don't fetch.
- **Stop on diminishing returns.** When two consecutive searches add nothing new on a dimension, that dimension is done — move on.
- **Checkpoint as you go.** After each dimension, mentally fix what's established, with which sources, and what's still open. Never re-research something already settled, including in later passes of a multi-pass run.
- **Never loop.** If a tool errors or a page won't load, try ONE alternative (different URL or query), then route around it. Repeating a failing call burns budget for nothing.

## Temporal Awareness

**Always check `<current_date>` before forming ANY time-sensitive query.** Match precision to intent:

| User intent | Precision | Example |
|---|---|---|
| "today / just released" | month + day + year | `"chip export rules February 28 2026"` |
| "this week" | week range | `"semiconductor news week of Feb 24 2026"` |
| "recently / latest" | month + year | `"AI regulation February 2026"` |
| "this year / trends" | year | `"foundry capex 2026"` |

- Never hardcode a past year from memory — use the actual current year from `<current_date>`.
- Year-only queries will NOT surface today's news; day-level intent needs day-level queries.
- Recency cuts both ways: for stable facts (history, established science), an authoritative older source beats a fresh low-tier rewrite.

## Robustness Playbook

| Obstacle | Move |
|---|---|
| **Paywall** on an S2 article | Don't fight it. Find the same story via wire coverage (Reuters/AP), the underlying S1 document, or the outlet's free summary. |
| **Conflicting numbers** | Report the range + why (definitions, time windows, methodology); weight the more independent / more primary source. |
| **Thin results** in English on a regional topic | Search in the relevant language (Chinese, Japanese, German…) and in local S1/S2 outlets (e.g. Caixin, Nikkei, national statistics offices). |
| **Only S3/S4 sources exist** for a claim | Either drop the claim or carry it explicitly flagged: "single low-tier source — unverified". Never launder a weak source by citing it without its tier. |
| **Breaking/rumor-stage story** | Label rumor vs. confirmation; cite who reported first and who confirmed independently. |
| **Tool failure / empty page** | One retry with a changed approach, then route around. Note the gap rather than stalling. |

## Output Requirements

Carry your evidence forward so downstream consumers can audit it:

1. **Inline attribution** for every load-bearing claim: source name + date (`Reuters, 2026-02-14`; `TSMC Q4'25 earnings call`).
2. **Source list** with URL, date, and tier (S1/S2/S3) for each source actually used.
3. **Flags preserved**: single-source claims, actor-self-claims, conflicts and their ranges, rumor-stage items.
4. **No S4 citations, ever.** If something is only known via S4, it is not known.

## Failure Modes to Avoid

- ❌ Fetching the first pretty result instead of triaging by tier
- ❌ Counting ten echoes of one report as ten sources
- ❌ Burning budget on duplicate queries or retry loops
- ❌ Only searching the confirming side of a question
- ❌ Citing Wikipedia/aggregators instead of the primaries they point to
- ❌ Presenting a vendor's claim about itself as independent fact
- ❌ Using an old statistic without dating it
- ❌ Starting to write before the synthesis gate passes
