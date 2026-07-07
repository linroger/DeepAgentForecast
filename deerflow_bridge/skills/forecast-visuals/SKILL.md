---
name: forecast-visuals
description: Use this skill on any research/forecast run once the structured artifacts (actors.json, timeline.json, quantitative.json, and market anchors) have been written, to turn them into charts. It is a thin ROUTING layer over the chart-visualization skill: it maps each pipeline artifact to the right AntV chart type, tells you the exact invocation and where to write the image + a charts.json manifest the pipeline's artifact channel picks up, and — when host bash is unavailable — degrades every chart to an inline ```mermaid``` block so a run never loses its visuals. Triggers: "visualize the forecast", "chart the actors/timeline/metrics", "actor network graph", "model vs market chart", or any time a dossier's numbers/relationships should become a figure.
---

# Forecast Visuals Skill (artifact → chart routing)

## 0. What this skill is (and is not)
This skill does **not** render charts itself. It **routes** the forecasting
pipeline's already-written artifacts to the sibling **`chart-visualization`**
skill (`node scripts/generate.js`, 26 AntV chart types) and standardizes where
outputs land. 本技能是「工件→图表」的路由层，不重复造轮子：图由
chart-visualization 出，本技能只负责选型、给出精确调用、并把产物登记进
`charts.json`。它的运行前提是 **host bash 可用**（`sandbox.allow_host_bash: true`，
本部署已开）；若 bash 不可用，走 §6 的 NO-BASH mermaid 降级，绝不静默丢图。

**Inputs** are the run's working-dir artifacts (same dir as `actors.json`):
`actors.json` (with `relationships[]`), `timeline.json` (`[{date,event}]`),
`quantitative.json` (`[{metric,value,unit,as_of_date,source,tier}]`), and the
market anchors (the "Prediction Market Signals" rows: `question`,
`implied_yes_prob`, plus your own model probability where you produced one).
Only render an artifact that actually exists — a missing artifact is a skipped
chart, never a fabricated one.

**Locate the renderer once.** The chart-visualization skill is a sibling under
the same skills root (mounted at the sandbox `container_path`, default
`/mnt/skills`). Resolve it before the first call:
```bash
CHARTVIZ=$(dirname "$(find /mnt/skills . -maxdepth 4 -path '*chart-visualization/scripts/generate.js' 2>/dev/null | head -1)")
# CHARTVIZ now points at .../chart-visualization/scripts
```

## 1. The routing table (artifact → chart type)
| Artifact | Chart-visualization tool (`--type`) | Why |
|---|---|---|
| `actors.json` → `relationships[]` | `generate_network_graph` (`network-graph`) | directed/typed/valenced actor network |
| `timeline.json` | `generate_line_chart` (`line`) — or mermaid `timeline` in fallback | dated event trajectory |
| `quantitative.json` rows | `generate_column_chart` (`column`); `generate_dual_axes_chart` (`dual-axes`) when two units/scales | metric comparison with unit + as-of |
| market anchors (model vs market) | `generate_scatter_chart` (`scatter`) as a dumbbell | model-vs-market divergence |

Each invocation follows chart-visualization's contract exactly:
```bash
node "$CHARTVIZ/generate.js" '<payload_json>'   # prints an image URL
```
The renderer returns a **URL**. Localize it into the run's `charts/` dir so the
figure survives with the run, then register it in `charts.json` (§5):
```bash
mkdir -p charts
curl -sL "<returned_url>" -o "charts/<stem>.png"
```

## 2. actors.json → network graph (colored by role-class)
Build `data.nodes` from `actors[]` (unique `name`) and `data.edges` from
`relationships[]` (`source`→`target`, `name` = the edge `type`). Encode
**valence** and **role-class** so a partner and a rival never look the same:
- Node grouping: one class per `role_class`
  (`principal`/`arbiter`/`stakeholder`/`amplifier`/`intermediary`) — pass it as
  the node's group/category so nodes color by role-class.
- Edge label: `"<type> (<valence>)"`, e.g. `"COMPETES_WITH (adversarial)"`;
  keep 10–50 nodes (drop low-salience actors first if over budget).

```bash
node "$CHARTVIZ/generate.js" '{"tool":"generate_network_graph","args":{
  "data":{"nodes":[{"name":"TSMC","group":"principal"},{"name":"US BIS","group":"arbiter"}],
          "edges":[{"source":"US BIS","target":"TSMC","name":"REGULATES (governance)"}]},
  "theme":"academy","title":"Actor Relationship Network"}}'
```
Caption: name the two heaviest edges and what they gate for the forecast.

## 3. timeline.json → line (temporal trend)
Map `[{date,event}]` to an ordered line: X = `date`, Y = event index (1..N),
and carry each `event` string as the point label/tooltip. Sort by date; if
dates are non-numeric or sparse, prefer the mermaid `timeline` (§6) — it reads
better than a degenerate line. Caption: name the earliest inflection and the
latest catalyst.
```bash
node "$CHARTVIZ/generate.js" '{"tool":"generate_line_chart","args":{
  "data":[{"time":"2025-04","value":1},{"time":"2025-09","value":2}],
  "title":"Event Timeline","axisXTitle":"date","axisYTitle":"event #"}}'
```

## 4. quantitative.json → column / dual-axes (unit + as-of labeled)
Each row is `{metric,value,unit,as_of_date,...}`. Parse the leading number of
`value`. **Group by `unit`**:
- **Single unit** → `generate_column_chart`: `category`=`metric`, `value`=number,
  `axisYTitle`=`unit`. Put the `as_of_date` in the title/caption ("as of …").
- **Exactly two units/scales** → `generate_dual_axes_chart`: `categories`=metrics,
  two `series` (`type:"column"` and `type:"line"`), each with its own
  `axisYTitle` = its unit. Never plot mixed units on one axis.
Always label unit + as-of; an unlabeled number is a defect (rows may carry
`is_stale`/`staleness_days` — flag stale figures in the caption).
```bash
node "$CHARTVIZ/generate.js" '{"tool":"generate_column_chart","args":{
  "data":[{"category":"TSMC 2026 capex","value":54}],
  "title":"Key metrics (as of 2026-01)","axisYTitle":"USD billion"}}'
```

## 5. market anchors → scatter/dumbbell (model vs market)
For each forecast statement with both a **model** probability and a **market**
`implied_yes_prob`, emit two scatter points per statement (same Y row) so the
gap reads as a dumbbell — this mirrors `report_visualizer.build_model_vs_market`
(model `#3b6fb0` vs market `#c0603a`). `group` distinguishes the two series;
Y = statement index, X = probability (0–1). Caption every row whose gap > 10pp
(the divergence rule) with the reason.
```bash
node "$CHARTVIZ/generate.js" '{"tool":"generate_scatter_chart","args":{
  "data":[{"x":0.62,"y":1,"group":"market"},{"x":0.48,"y":1,"group":"model"}],
  "title":"Model vs Market","axisXTitle":"P(yes)","axisYTitle":"statement"}}'
```

## 6. NO-BASH FALLBACK (degrade to inline mermaid — never skip)
When host bash is unavailable (`allow_host_bash: false`, no container sandbox,
or `node` missing) you **cannot** run `generate.js`. Do NOT drop the visual —
emit an inline ```` ```mermaid ```` block straight into the report instead, so
visuals degrade to text diagrams. Use the same mappings the deterministic
`report_visualizer` uses (its `render_mermaid_*` helpers produce exactly these):
- **network** → ```` ```mermaid graph TD ```` — one node per actor, edges
  `A -->|"TYPE (valence)"| B`; a `%%` role-class comment per node.
- **timeline** → ```` ```mermaid timeline ```` — `title …` then `date : event`.
- **quantitative / market** → a plain **markdown table** (metric | value | unit |
  as-of; or statement | model | market | gap) — mermaid has no numeric bar chart,
  and a labeled table beats a fake one.
No `charts/` files and no PNGs are written in fallback; the manifest (§7) is
skipped and the mermaid/table lives inline in the report body.

## 7. Output contract — charts/ + charts.json manifest
When bash IS available, after each successful render write the PNG under
`charts/` and append one entry to **`charts.json`** at the run working-dir root
(alongside `actors.json`). This is the manifest the pipeline's artifact channel
picks up — one object per chart:
```json
[
  {"title":"Actor Relationship Network",
   "caption":"BIS→TSMC export-control edge gates advanced-node shipments.",
   "source_data":"actors.json",
   "path":"charts/actor_network.png"}
]
```
- `title` — chart title; `caption` — 1 line of forecast-relevant reading
  (name the load-bearing edge / inflection / >10pp gap), NOT a restatement.
- `source_data` — the artifact filename it was built from (traceability).
- `path` — repo/run-relative, always under `charts/`.
Write `charts.json` atomically (whole array), append-only across the run; if it
already exists, read → append → rewrite. Degrade-safe: a render failure logs and
skips that one entry (falls back to §6 for that visual) — it never aborts the
rest of the manifest.

## 8. Failure modes (do not do these)
- ❌ Rendering an artifact that doesn't exist / fabricating nodes, numbers, or a
  market price the anchors don't contain.
- ❌ Plotting mixed units on one axis (use `dual-axes`), or dropping the
  unit/as-of label on a quantitative chart.
- ❌ Flattening valence — a rival edge and a partner edge must be distinguishable
  (carry `TYPE (valence)` on every edge, color nodes by role-class).
- ❌ Silently skipping a visual when bash is off — always emit the §6 mermaid/table.
- ❌ Writing PNGs outside `charts/`, or forgetting the `charts.json` entry (the
  artifact channel only sees what the manifest lists).
- ❌ A caption that just repeats the title instead of stating what the figure
  means for the forecast.
