---
name: prediction-markets
description: Use this skill whenever a DRF-2 forecast could be calibrated against real prediction markets. It teaches the prediction_market_search tool (Polymarket's public Gamma API — no API key required) — how to derive short high-recall queries, which markets to trust (open, priced, liquid), how to keep the snapshot diverse across events, and how to use market-implied probabilities as calibration anchors rather than ground truth, including the divergence-explanation rule.
allowed-tools:
  - prediction_market_search
  - tool_search
  - read_file
  - write_file
---

# Prediction Markets Skill

## 1. Why markets, and what they are not

Prediction-market prices are the **aggregated, money-weighted belief of people with skin in the game** — an excellent *calibration anchor* for a forecast. They are NOT ground truth: prices move continuously, thin markets are noisy, and market questions rarely match your forecast's resolution criteria word-for-word. The workflow is: fetch → filter → compare → explain divergence — never copy.

The `prediction_market_search` tool queries **Polymarket's public Gamma API** (`gamma-api.polymarket.com` — no key, no wallet; the `polymarket-cli` tool is just a thin wrapper over this same public API) for active markets and returns, per market: `market_id`, `exchange` (`"polymarket"`), `question`, `implied_yes_prob` (the "Yes" price), `volume`, `liquidity`, `event_title`. It is degrade-safe: network failure / nothing found → empty result. **An empty result is an answer** — note "no liquid markets overlap this question" and move on; never fabricate a market or a price.

## 2. Query craft (full-text search likes short phrases)

Market search is full-text; **short phrases (≤5 words) work best** — `tariff`, `semiconductor export`, `AI capex`, `Fed rate cut`, `US midterms`.

Derive up to ~6 queries deterministically from the run's materials:
1. The 2–3 most **salient noun phrases** of the central question (strip stopwords and generic forecast-speak like "forecast", "impact", "scenario"; keep short all-caps acronyms — AI, EU, GDP).
2. The run's **hot topics** (up to 4).
3. The **top 2–3 actor names** by salience.
4. Deduplicate case-insensitively; trim each query to ≤5 words.

Do not reissue near-duplicate queries; a thin result means change the phrase's *angle* (another actor, another driver), not its word order.

## 3. Filtering (which markets deserve to be anchors)

Keep a market only if ALL hold — an unfiltered market table is worse than none:

- **Open**: `closed` is not true. (Do NOT trust the `active` flag — Polymarket leaves it `true` on already-resolved markets; `closed` is the reliable gate.)
- **Priced**: `implied_yes_prob` — parsed from the market's `outcomes`/`outcomePrices` at the "Yes" index — is a number **strictly inside (0, 1)**. A price of exactly 0 or 1 means the market has effectively resolved and is worthless as an anchor.
- **Liquid enough**: `volume ≥ ~200` — ultra-low-volume prices are noise, not information.
- **Deduplicated** by `market_id` across queries (keep the first-matching query for traceability).

Rank the survivors by volume (descending), then **cap each event to ~3 markets** (`event_title`) before taking the top ~20. This matters: a single multi-outcome event — e.g. "How many Fed rate cuts in 2026?" with sub-markets for 0,1,2,…,12 — otherwise floods the whole snapshot with one topic's high-volume ladder and crowds out every *other* relevant event. Diversity across events beats depth within one.

## 4. Using the anchors

- **Render a "Prediction Market Signals" section** in the research report / brief: a table with market question (+ `market_id`), venue, implied P(yes), and volume, plus the standing caveat that this is a machine-fetched snapshot of moving prices.
- **Cite, don't copy.** Where a forecast overlaps a listed market, cite the market's implied probability in the forecast's `adjustment_rationale` and attach `market_anchor: {market_id, implied_yes_prob}`. Your probability remains YOUR probability, produced by your own base-rate + evidence + simulation reasoning.
- **The divergence rule**: when your probability differs from the cited market by **more than 10 percentage points**, explain the divergence explicitly — what the market is missing or mispricing (private research signal, simulation dynamics, definitional mismatch between the market question and your statement). Silent large divergence is a defect; so is silent convergence achieved by copying.
- **Question-match honesty**: check that the market's question, horizon, and resolution rules actually correspond to your statement before anchoring. A near-miss market (different threshold, different date) can still be cited — with the mismatch named — but must not be pasted into `market_anchor` as if equivalent.
- **Freshness**: note the fetch time context; a snapshot ages fast around catalysts. Re-fetch rather than reason from a stale table when the report stage runs long after research.

## 5. Failure modes

- ❌ Long natural-language queries ("what is the probability that the US will raise tariffs in 2026") — full-text search returns junk; use `tariff`, `US tariffs`.
- ❌ Anchoring on a closed, unpriced, or resolved (price 0/1) market.
- ❌ Trusting the `active` flag instead of `closed` — resolved Polymarket markets stay `active: true`.
- ❌ Letting one multi-outcome event's sub-market ladder dominate the whole snapshot (cap per event).
- ❌ Copying the market price as your forecast probability (that is abdication, not calibration).
- ❌ Diverging >10pp from a cited market without an explicit explanation.
- ❌ Treating a market with mismatched threshold/date as equivalent to your statement.
- ❌ Fabricating market IDs or prices when the tool returns nothing.
- ❌ Presenting the snapshot as live truth without the freshness caveat.
