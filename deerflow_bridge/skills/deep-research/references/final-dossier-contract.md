# Final Research Dossier Contract

Load this reference **only when the current prompt explicitly requests final dossier synthesis/finalization**. Opening notes, scoped workers, top-ups, verification, corrections, and absorption passes are exempt from this reference's length and section expectations.

## Length and completeness

There is no final word or character maximum. For a `deep` final dossier, use an evidence-dense 10,000-word floor and expand when KIQ count and independent evidence clusters require it. This is a completeness floor for the one final dossier—not a target for intermediate notes. Never pad; every section must own a priority KIQ, cross-cutting synthesis, or necessary structured handoff.

Quick/standard final outputs should match the evidence and brief without arbitrary padding. Internal context, transport, and convergence budgets are safety controls, not deliverable ceilings.

## Required final structure

1. Executive thesis: specific, falsifiable, horizon-bound, and calibrated.
2. Situation brief: current state, path to it, forces in tension, fault lines, catalysts.
3. KIQ-owned analysis sections with known/inferred/assumed/unknown clearly separated.
4. Outside view: reference classes, base rates, analogues, and case-specific adjustments.
5. Cast-wide actor intelligence: every Tier-1/2 outcome-mover; identity/history, values/worldview, incentives, motivations, capabilities, constraints, evidence-backed operational preferences/aversions, decision rights/process/triggers and knowledge limits, current actions, future plans with status/horizon/dependencies, investments/resource allocation, track record, likely actions, red lines, and explicit gaps. Keep verified fact, actor-stated claim, analyst inference, contested evidence, and unknowns distinct with citations/as-of/confidence/qualifiers.
6. Directed actor relationships: source, target, type/sign, basis, and material temporal change.
7. Drivers and causal pathways: countervailing mechanisms and second-order effects.
8. Scenarios/forecast implications: rough likelihoods, resolution conditions, assumptions, disconfirmation.
9. Watchable indicators: metric/event, threshold, date/window, source/update path.
10. Prediction Market Signals: only relevant liquid markets, with question, ID, P(yes), volume/liquidity, end date, URL, and fetch time; explain material divergence.
11. Contradictions and contested claims: positions, sources, why they differ, and weighting.
12. Evidence limitations and remaining gaps.
13. References: only real sources actually fetched/read, each with title, URL, date, and S-tier.

Choose the exact headings adaptively. Do not expose internal agent/simulation dynamics or research-process chatter in the user-facing dossier.

## Citation and claims contract

- Attribute every load-bearing factual/quantitative claim inline to a real indexed source.
- Preserve deterministic `[S<n>]` markers when a pinned source index is provided; never invent a marker or URL.
- Show conflicts rather than silently averaging them.
- Calibrated language: almost certain >90%, likely/probable 65–85%, roughly even 45–55%, unlikely 15–35%, remote <10%; name the uncertainty driver.
- Preserve flags for single-origin claims, actor self-claims, rumor-stage items, and fragile assumptions.
- Never cite S4.

## Chart-ready and structured handoff

The final dossier must carry data that downstream extraction and deterministic Plotly rendering can use:

- quantitative table: metric, value/range, unit, as-of date, definition, geography/population, source tag/URL;
- timeline table: date/window, event, actors, evidence, forecast relevance;
- actor relationship table: source actor, target actor, relationship type, valence, evidence basis;
- actor-intelligence table/section: canonical actor, dimension, claim, evidence type, as-of, horizon/status, confidence, source refs, dependencies/contradictions/qualifiers, or explicit gap;
- drivers/indicators table: driver, mechanism, leading indicator, threshold, horizon, source;
- scenario/probability table with assumptions and resolution criteria;
- prediction-market table with freshness fields;
- contested-claims table with differing positions and origins.

These feed `actors.json`, `sources.json`, `quantitative.json`, `timeline.json`, `contested.json`, market artifacts, charts, and the forecast report. Use stable names and explicit fields; prose alone is insufficient.

## Final self-check

- Every priority KIQ answered or gap named.
- Every load-bearing claim B2-or-better or visibly weak/single-origin.
- No circular sourcing, fabricated URL, future-dated source, unsupported quote, or S4 citation.
- Base rates and opposing evidence are present.
- Quantities have units, dates, definitions, and provenance.
- Actor graph is constrained to decision-relevant actors and material relations.
- Every Tier-1/2 actor is behaviorally substantive or has explicit per-dimension gaps; no announced aspiration is silently promoted into an approved/funded action, and no personality likes/dislikes are invented.
- Market hits are relevant to the same event/horizon/resolution rule.
- No internal simulation/agent dynamics language.
- No repeated filler, research logs, tool chatter, or process residue.
- Final length reflects evidence depth and is not an intermediate-pass floor multiplied across workers.
- One canonical scenario partition owns the scenario names and probabilities. Every executive-summary mention, binary-forecast dependency, and visualization/source table that repeats it uses the exact same names and weights; an alternate split that also totals 100% is a contradiction, not a second valid presentation.
