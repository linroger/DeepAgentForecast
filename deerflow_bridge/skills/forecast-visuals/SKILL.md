---
name: forecast-visuals
description: Use this skill on any research/forecast run to turn the structured artifacts (actors.json, timeline.json, quantitative.json — or data you assemble yourself pre-extraction) into charts. It is a SELF-CONTAINED plotly renderer: a bundled scripts/render.py reads the run's artifacts and writes charts/*.html + charts/*.png plus a charts.json manifest the pipeline's artifact channel picks up — no network, no external chart service, no URL localization. The dossier MUST embed a minimum of 3 charts (actor network, event timeline, top quantitative metrics) as ![](charts/x.png) images. When python/plotly is unavailable, degrade every chart to an inline ```mermaid``` block or a markdown table so a run never loses its visuals. Triggers: "visualize the forecast", "chart the actors/timeline/metrics", "actor network graph", "required charts", or any time a dossier's numbers/relationships should become a figure.
---

# Forecast Visuals Skill (plotly-local, bundled renderer)

## 0. What this skill is (and is not)
This skill ships its **own renderer**: `scripts/render.py` (in this skill's
directory) reads the run's structured artifacts and writes self-contained
plotly charts. 本技能**不再路由**到任何外部图表服务（旧 AntV/chart-visualization
远程 URL 流程已废弃）——渲染完全本地、零网络、零 curl。它的运行前提是
**host bash 可用**（`sandbox.allow_host_bash: true`，本部署已开）且有一个能
import plotly（或至少 matplotlib）的 python；两者都没有时走 §5 的 NO-PYTHON
mermaid/表格降级，绝不静默丢图。

**Inputs** are the run's working-dir artifacts (same dir as `actors.json`):
- `actors.json` — `actors[]` (`name`, `influence`/`salience`, `role_class`) +
  `relationships[]` (`source`, `target`, `type`, `sign`/`valence`/`polarity`)
- `timeline.json` — `[{date, event}]`
- `quantitative.json` — `[{metric, value, unit, as_of_date, tier, ...}]`
- `charts_data.json` — **optional fallback** for the write step: when the
  structured extraction has not run yet, write this file yourself from data you
  actually gathered — `{"actors": [...], "relationships": [...],
  "timeline": [...], "quantitative": [...]}` (same row shapes as above) — and
  the renderer consumes it in place of the missing first-class files.

Only render an artifact that actually exists — a missing artifact is a skipped
chart, never a fabricated one.

## 1. The one invocation
```bash
SKILL_DIR=$(dirname "$(find /mnt/skills . -maxdepth 5 -path '*forecast-visuals/scripts/render.py' 2>/dev/null | head -1)")/..
# Pick a python that can import plotly (the deployment's backend venv has
# plotly + kaleido); plain python3 works if plotly is installed there.
PYBIN="${RESEARCH_CHARTS_PYTHON:-python3}"
"$PYBIN" "$SKILL_DIR/scripts/render.py" --dir "$RUN_DIR"   # RUN_DIR = where actors.json lives
```
The renderer is idempotent and degrade-safe end-to-end:
- writes `charts/actor_network.{html,png}`, `charts/timeline.{html,png}`,
  `charts/quant_metrics.{html,png}` — HTML is fully self-contained (plotly.js
  inlined); PNG uses kaleido when importable, falls back to a matplotlib
  redraw, and if neither works the manifest entry points at the HTML;
- merges entries into `charts.json` at the run root (read → merge by `path` →
  atomic rewrite);
- exit 0 = charts written; exit 2 = no renderable artifacts; exit 3 = no chart
  library at all (→ use §5 fallback).

## 2. REQUIRED minimum — 3 charts embedded in the dossier
Every dossier MUST carry at least these three figures, embedded as markdown
images at the section where each belongs (not dumped at the end):
| # | Chart | Source artifact | Embed as |
|---|---|---|---|
| 1 | Actor relationship network | `actors.json` | `![Actor network](charts/actor_network.png)` |
| 2 | Event timeline | `timeline.json` | `![Event timeline](charts/timeline.png)` |
| 3 | Top quantitative metrics | `quantitative.json` | `![Key metrics](charts/quant_metrics.png)` |

Add a one-line caption under each image stating what the figure means for the
forecast (name the load-bearing edge / inflection / dominant metric — NOT a
restatement of the title). If a PNG could not be produced but the HTML exists,
link it instead: `[Actor network (interactive)](charts/actor_network.html)`.

Note: the pipeline ALSO runs this renderer deterministically after structured
extraction and appends any missing figures as a `## Visual Annex` — but you
embedding them at the right sections is strictly better than the annex.

## 3. What the renderer encodes (so your captions match the figure)
- **Network**: node size = influence, node color = `role_class`
  (principal/arbiter/stakeholder/amplifier/intermediary), edge color =
  valence (adversarial red / cooperative green / governance purple), duplicate
  edges deduped, top ~30 actors by influence.
- **Timeline**: events sorted by date, deduplicated, capped at the ~40 most
  recent, staggered lanes, full event text on hover (HTML).
- **Quantitative**: groups rows by `unit` and plots the LARGEST same-unit
  group (top ~12 by magnitude) — units are never mixed on one axis; `as_of`
  dates are carried into the bar labels.

## 4. Output contract — charts/ + charts.json manifest
All files land under `charts/`; the manifest `charts.json` sits at the run
root (alongside `actors.json`) — one object per chart:
```json
[
  {"title": "Actor Relationship Network",
   "caption": "Node size = influence, color = role class; red edges adversarial, green cooperative.",
   "source_data": "actors.json",
   "path": "charts/actor_network.png",
   "html_path": "charts/actor_network.html"}
]
```
- `path` — what to embed (PNG preferred; HTML when no PNG could be exported).
- `html_path` — the interactive twin when both exist.
- The pipeline's artifact channel only sees what the manifest lists — the
  renderer maintains it; do not hand-edit entries you did not render.

## 5. NO-PYTHON FALLBACK (degrade to inline mermaid/table — never skip)
When no usable python/plotly/matplotlib exists (render.py exits 3), do NOT
drop the visuals — emit inline blocks straight into the report instead:
- **network** → ```` ```mermaid graph TD ```` — one node per actor, edges
  `A -->|"TYPE (valence)"| B`; escape `>` inside labels as `＞`.
- **timeline** → ```` ```mermaid timeline ```` — `title …` then `date : event`,
  capped at ~30 rows (bucket by quarter beyond that).
- **quantitative / market** → a plain **markdown table** (metric | value |
  unit | as-of) — mermaid has no numeric bar chart and a labeled table beats a
  fake one.
No `charts/` files and no manifest are written in fallback; the mermaid/table
lives inline in the report body.

## 6. Failure modes (do not do these)
- ❌ Rendering an artifact that doesn't exist / fabricating nodes, numbers, or
  events into `charts_data.json` that your research did not gather.
- ❌ Mixing units on one axis, or dropping the unit/as-of label on a
  quantitative chart.
- ❌ Flattening valence — a rival edge and a partner edge must be
  distinguishable (the renderer colors them; keep that in your caption).
- ❌ Skipping the embed: a chart that exists on disk but is never referenced
  from the report is a defect — embed `![](charts/x.png)` where it belongs.
- ❌ Narrating render failures in the dossier prose — log them, fall back
  (§5), keep writing.
- ❌ Writing files outside `charts/`, or hand-editing `charts.json` out of
  sync with what is actually on disk.
