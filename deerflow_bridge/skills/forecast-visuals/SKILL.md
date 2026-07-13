---
name: forecast-visuals
description: Use this skill during research and forecast runs when sourced structured artifacts should become decision-relevant figures. It renders local deterministic charts from quantitative.json, timeline.json, prediction_markets.json, actors.json, and sources.json; use it for comparable market/technology/policy benchmarks, published forecast revisions, dated inflection points, market-implied probabilities, industrial-chain relationship maps, and evidence diagnostics. It rejects fabricated data, incompatible denominators, proxy-score charts, and decorative visuals.
---

# Forecast Visuals

Turn structured forecast evidence into figures that answer a reader question. The
goal is not “three charts”; the goal is faster understanding of the forecast,
its evidence, and what changed.

## 1. Select the question before the chart

For every proposed figure, write one sentence internally:

> The reader needs this figure to understand **[comparison / trajectory /
> uncertainty / inflection / calibration]**, using **[artifact and fields]**.

Render it only when all five checks pass:

1. **Decision relevance** — the answer changes how a reader interprets the
   forecast, a regional pattern, a technical route, a policy path, or a risk.
2. **Actual rows** — every mark comes from a structured artifact or a dataset
   explicitly extracted from cited evidence; no invented filler values.
3. **Comparability** — rows on one axis share a denominator, unit, time basis,
   and definition. Plain `%`, `units`, and broad currency totals are not enough.
4. **Provenance** — source and as-of date survive into hover, label, caption, or
   an adjacent table. Forecasts and targets are visibly distinct from observations.
5. **Honest uncertainty** — intervals, ensembles, and sensitivity deltas appear
   only when the artifact actually contains them.

If a check fails, skip the chart and retain the evidence in a compact table.
Never substitute actor salience, keyword frequency, source counts, or another
internal proxy for missing domain data.

See [references/chart-patterns.md](references/chart-patterns.md) for the chart
decision matrix, forecast-domain examples, and anti-patterns.

## 2. Use the bundled renderer

Inputs live together in the run directory:

- `quantitative.json` — `metric`, `value`, `unit`, `as_of_date`, `source`,
  `tier`, and optional `definition`, `staleness_days`, `is_stale`. Automatic
  comparison/revision charts require `definition`, source, and a parseable
  `as_of_date`; rows may still exist without them but remain table-only.
- `timeline.json` — dated `event` rows.
- `prediction_markets.json` — dated markets with `implied_yes_prob`, volume,
  liquidity, and resolution metadata.
- `actors.json` — actors and sourced relationships; this is optional reader
  context, not a substitute for market or forecast data.
- `sources.json` — evidence tier/freshness diagnostics.
- `meta.json` — optional run metadata; when present, `finished_at` is the
  authoritative source-freshness anchor so all rows use one reproducible date.
- `charts_data.json` — fallback carrying the same row shapes, but only when
  those values were actually gathered in this run.

Run:

```bash
SKILL_DIR=$(dirname "$(find /mnt/skills . -maxdepth 5 -path '*forecast-visuals/scripts/render.py' 2>/dev/null | head -1)")/..
PYBIN="${RESEARCH_CHARTS_PYTHON:-python3}"
"$PYBIN" "$SKILL_DIR/scripts/render.py" --dir "$RUN_DIR"
```

The renderer is local, deterministic, idempotent, and network-free. It writes
self-contained Plotly HTML with a meaningful document title and embedded data
favicon, PNG when Kaleido or matplotlib is available, and a stable `charts.json`
manifest. Standalone chart pages must not make incidental network requests.
Missing or ineligible data is skipped, never fabricated.

## 3. Reader-facing outputs

Use these outputs when their data is eligible:

| Output | What it answers | Artifact |
|---|---|---|
| `quant_metrics` | Which comparable market, cost, rate, or technology benchmarks differ? | `quantitative.json` |
| `forecast_revisions` | How has the same published forecast changed across at least three vintages? | `quantitative.json` |
| `timeline` | Which dated events or policy milestones define the inflection path? | `timeline.json` |
| `market_probabilities` | What do liquid external markets imply as of a stated time? | `prediction_markets.json` |

`quant_metrics` uses up to three strict same-denominator panels. It deliberately
rejects “largest same-unit group” logic when the unit is ambiguous. Every row in
a panel must retain source and parseable as-of provenance and share a compatible
denominator, definition family, period/frequency, and exact as-of date. Circles
are observations; diamonds are published forecasts or targets. A negative
`staleness_days` value only says that the row date is after the research anchor;
it never turns an otherwise reported actual into a forecast. Such a future-dated
actual is withheld from automatic comparison panels unless forecast/target
semantics are independently present in the row.

`forecast_revisions` requires at least three rows for the same metric with
trailing vintage labels such as `(2024)`, `(2025)`, `(2026)`. Each suffix must
match the year in `as_of_date`; otherwise it may be a target year and is rejected.
The rows must also share a normalized publisher/outlook family, fixed target
horizon, unit, metric identity, and definition identity. Citation tags and years
inside `source` are ignored only while extracting the publisher/family, so a 2026
outlook that recaps a genuine 2025 vintage can support that point without being
misread as the vintage. Two points do not establish a reliable revision pattern.

## 4. Optional diagnostics

- `actor_network` is optional when relationship structure itself is central to
  industrial-chain competition, coalition behavior, or policy transmission.
  Explain that node size is an internal ranking proxy. Do not use it as evidence
  of market share, bargaining power, revenue, or forecast impact.
- `source_quality` belongs in methodology or audit context. It does not count as
  a forecast-domain data figure.

Do not create or request an “influence vs salience” chart. Do not label weighted
keyword frequency as a tornado or sensitivity analysis. A true tornado requires
measured output changes from controlled input perturbations.

## 5. Embed at the point of use

Embed each reader-facing figure beside the claim it clarifies, not as a generic
gallery. Prefer PNG in Markdown and link the interactive twin when useful:

```markdown
![Comparable adoption benchmarks](charts/quant_metrics.png)

*China and Europe were already on different adoption paths by the same 2025
measurement basis; the later outlook points are marked as forecasts, not actuals.*
```

Each caption must state the takeaway, denominator/time boundary, and forecast
implication. “Top metrics” and “see chart” are not captions.

If PNG is unavailable, link the HTML. If no chart backend is available, emit a
faithful table with `metric | value | unit | as-of | source` (or the equivalent
timeline/market schema). Do not emit Mermaid for report delivery.

## 6. Output contract and checks

The renderer owns these stable IDs and preserves custom producers:

`actor_network`, `timeline`, `quant_metrics`, `forecast_revisions`,
`market_probabilities`, `source_quality`.

For every manifest row verify:

- `path` exists and is the preferred PNG or HTML deliverable;
- `html_path` exists when an interactive twin is declared;
- `source_data` names the real producer artifact;
- the chart has at least two comparable rows (three vintages for revisions);
- labels, source/as-of hover, and observed-versus-forecast encoding are legible;
- standalone HTML has a meaningful browser title, embedded favicon, and no
  runtime dependency on the parent site;
- rerunning replaces the owned row rather than duplicating it.

Exit codes: `0` = at least one chart written, `2` = no eligible artifact, `3` =
no plotting library. On `2` or `3`, use the faithful-table fallback without
narrating renderer internals in the report.
