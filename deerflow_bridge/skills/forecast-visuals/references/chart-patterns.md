# Decision-Relevant Forecast Chart Patterns

Use this reference after the core five-check gate in `SKILL.md`. Choose the
smallest chart that answers the reader's question. One strong chart is better
than a dashboard of weak proxies.

## Selection matrix

| Reader question | Required data contract | Preferred view | Non-negotiable check |
|---|---|---|---|
| How do regions differ now? | Same metric, denominator, period, and as-of rule by region | Sorted dot plot or grouped bar | Do not mix registrations, deliveries, and fleet share |
| How is a market changing? | At least 3 dated observations of the same metric | Line or slope chart | Show gaps; do not interpolate missing periods silently |
| How did a published outlook change? | Same publisher/outlook, target metric, definition, and unit across at least 3 dated publication vintages | Revision line | X-axis is publication vintage corroborated by as-of, not target year |
| Which technical route leads on measurable trade-offs? | Same test basis for cost, density, cycle life, charging, or yield | Small multiples or range dot plot | One axis per unit; include test basis |
| Where is the cost inflection? | Dated comparable cost observations or scenario values | Line, indexed line, or slope chart | Keep nominal/real currency and system boundary explicit |
| Is the supply chain concentrated? | Market shares with a declared market and period | Ranked bars; optional cumulative line | State denominator; shares should reconcile when exhaustive |
| What policy milestones change the path? | Effective/decision dates plus event type and region | Timeline lanes | Separate proposal, enactment, effective date, and court review |
| How is consumer demand structured? | Mutually exclusive segments that share one denominator | 100% stacked bars or grouped dots | Components must sum to approximately 100% |
| What outcomes does the forecast assign? | Mutually exclusive scenarios with probabilities and optional intervals | Sorted probability bars/dots | Probabilities reconcile to 100%; residual is explicit |
| Which binary calls matter most? | Proposition, P(yes), deadline, confidence/range | Probability dot plot | Resolution criteria and date remain accessible |
| How does the model differ from a market? | Exact proposition match, model probability, market probability, same as-of | Dumbbell or paired dots | Never compare semantically different questions |
| How did simulation state evolve? | Dated/round-indexed outcome shares from model output | Stacked area or lines | Label as simulation output, not observed history |
| Which inputs change the result? | Controlled perturbations and output deltas | Tornado/sensitivity bars | Keyword salience is not sensitivity |
| How large is uncertainty? | Quantiles, intervals, or multiple ensemble runs | Fan chart, interval dots, violin | Never invent bounds from point estimates |
| Where is supply versus demand tight? | Same-period supply, demand, capacity, and unit | Lines or diverging balance bars | Define capacity vs production vs shipments |

## Concrete data-first examples

The rows below illustrate shapes found in an EV forecast dossier. Use them only
when equivalent sourced rows exist in the current run.

### Regional adoption benchmark

Input rows:

| metric | value | unit | as-of |
|---|---:|---|---|
| Global EV market share | 25 | % new car sales | 2025-12-31 |
| China domestic EV penetration | 53 | % new car sales | 2025-12-31 |
| Europe EV market share | 28 | % new car sales | 2025-12-31 |
| Singapore EV share | 47 | % new car sales | 2025-12-31 |
| Turkey EV share | 22 | % new car sales | 2025-12-31 |

Use a sorted horizontal dot plot. The figure answers a regional-pattern
question because every point uses the same denominator and year. If the US row
uses `% new vehicle sales`, normalize only after confirming it is definitionally
equivalent. Do not add a fleet-stock percentage to this axis.

Good caption: “China and Singapore had moved far beyond the 2025 global adoption
rate on the same new-sales denominator, while Europe and Turkey remained closer
to the global baseline.”

### Technology cost comparison

Input rows:

| metric | value | unit | as-of |
|---|---:|---|---|
| Volume-weighted battery pack price | 108 | USD per kWh | 2025-12-31 |
| BEV-specific pack price | 99 | USD per kWh | 2025-12-31 |
| Stationary-storage pack price | 70 | USD per kWh | 2025-12-31 |

Use a dot plot or bars with a zero baseline. Keep pack/cell and chemistry scope
in the label. If historical rows exist, use a time series instead. A single
latest-year comparison must not imply a cost trajectory.

### Forecast revision

Input rows:

| metric vintage | forecast value | fixed target | unit / denominator | publication as-of | publisher / outlook | definition | source |
|---|---:|---:|---|---|---|---|---|
| BNEF US 2030 EV-share projection (2024) | 48 | 2030 | % of US new car sales | 2024-12-31 | BNEF / Electric Vehicle Outlook | BNEF 2024 forecast for US 2030 EV share | BNEF EVO 2024 |
| BNEF US 2030 EV-share projection (2025) | 27 | 2030 | % of US new car sales | 2025-12-31 | BNEF / Electric Vehicle Outlook | BNEF 2025 revision for US 2030 EV share | BNEF EVO 2026 recap |
| BNEF US 2030 EV-share projection (2026) | 17 | 2030 | % of US new car sales | 2026-06-30 | BNEF / Electric Vehicle Outlook | BNEF 2026 revision for US 2030 EV share | BNEF EVO 2026 |

Use a line with publication vintage on the x-axis and the fixed 2030 target
metric on the y-axis. This visual communicates model/outlook instability that a
single latest point hides. The 2025 point remains a 2025 vintage because its
metric suffix and as-of year agree; the later BNEF EVO source is provenance for
the recap, not a replacement vintage. It is not a historical adoption trend.
The reader payoff is forecast calibration: the 31-point downgrade is itself a
decision-relevant result, revealing how quickly policy and demand assumptions
changed. A revision line is invalid if the publisher, outlook family, fixed
target, denominator, or definition changes between points.

### Policy path timeline

Use separate lanes for `proposal`, `enactment`, `effective date`, `judicial
review`, and `expiry` when available. A proposed 2035 target and an effective
rule are different states. The caption should identify which milestone changes
the forecast branch, not merely restate the dates.

### Scenario and binary probability views

- Sort mutually exclusive scenarios by probability and display interval whiskers
  only when ensemble min/max or quantified bounds exist.
- Show binary forecasts on a 0–100% axis with deadline and confidence in hover.
- Use model-versus-market dumbbells only for exact proposition/deadline matches.
- Use market price history only when there are multiple dated market points;
  a latest quote belongs in a dot plot or table.

### Industry-chain concentration

Use ranked shares for cell makers, mining/refining capacity, or OEM sales only
when geography, product scope, and period match. A company installation share,
a country's refining-capacity share, and a material production share are three
different panels, even though all are percentages.

### Consumer structure

Use 100% stacked bars for mutually exclusive price bands, powertrain routes,
buyer types, or purchase channels. If segments overlap, use grouped dots or a
table. Never force overlapping survey responses to sum to 100%.

### Technical-route trade-offs

Prefer small multiples:

- energy density — Wh/kg;
- pack/cell cost — USD/kWh;
- charging rate — C-rate or minutes under a shared protocol;
- cycle life — cycles under a shared depth-of-discharge/test temperature;
- yield or defect rate — percent under a declared production stage.

Do not use a radar chart when axes have unrelated scales or when one route lacks
half the measurements. Missing values are missing, not zeros.

### Supply-demand and bottleneck risk

When supply, demand, and capacity share a unit and period, show lines or a
balance bar (`supply - demand`). Annotate policy/plant/geopolitical events at the
date they affect availability. Do not compare announced capacity directly with
realized production unless both are labeled and visually separated.

### Real sensitivity and uncertainty

A tornado chart requires rows like:

| perturbed input | low-case output delta | high-case output delta |
|---|---:|---:|
| battery cost ±10% | -4 pp | +3 pp |
| charging rollout ±20% | -2 pp | +5 pp |

If the only data is `key_drivers` text or an actor salience score, use prose or a
ranked evidence table, not a tornado. A fan chart similarly requires actual
quantiles or ensemble paths, not an arbitrary band around a point forecast.

## Anti-patterns and replacements

| Avoid | Why it fails readers | Replace with |
|---|---|---|
| Influence vs salience bubbles | Internal ordinal proxies, overlapping labels, no forecast quantity | Actual market shares, cost metrics, dated policy events, or omit |
| “Top metrics” by absolute magnitude | Magnitude across unrelated definitions is meaningless | Strict same-denominator panels chosen by reader question |
| Actor network as the lead chart | Relationship structure is not market size or impact | Optional industrial-chain map after data views |
| Keyword-weighted “tornado” | Frequency is not an output perturbation | Real sensitivity deltas or a qualitative driver table |
| Source-tier chart in the executive section | Methodology volume does not explain the forecast | Put in methodology; show domain data first |
| Dual-axis line/bar | Visual correlation can be manufactured by scale choices | Indexed series or aligned small multiples |
| Pie/donut with many categories | Hard to compare and label | Sorted bars or 100% stacked bars |
| Network hairball | Edge density hides the takeaway | Filter to a named transmission path or use a table |
| Waterfall without additive identity | Implies components reconcile when they do not | Grouped bars or explicit bridge with checked total |
| Fan chart without quantiles | Fabricates uncertainty | Point estimate plus stated qualitative uncertainty |

## Final visual QA

Before embedding, inspect the actual PNG/HTML and answer:

1. Can a reader state the takeaway in ten seconds?
2. Are the unit, denominator, geography, period, and actual/forecast status clear?
3. Can every point be traced to a source row and as-of date?
4. Are labels readable without hovering, with hover adding—not rescuing—meaning?
5. Does the caption explain why this matters to the forecast?
6. Would removing the chart lose information? If not, remove it.
