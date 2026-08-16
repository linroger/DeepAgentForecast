// Pure helpers for Step3Simulation's live mini charts:
// - world_state_trajectory.json rows → normalized stacked-area geometry
// - action feed → per-round bar geometry
// Kept free of Vue/DOM so `node --test` can exercise them directly.

/**
 * Tolerant reader for the decision-channel trajectory payload. Accepts either
 * `{trajectory: [...]}` (schema v2/v3) or a bare row list; keeps only dict rows
 * with a non-empty numeric `shares` map, and normalizes each row's shares to
 * sum to 1 (defensive — the writer already normalizes).
 *
 * @returns {Array<{round: number, label: string|null, shares: Object<string, number>}>}
 */
export function normalizeTrajectoryRows(payload) {
  const rows = Array.isArray(payload)
    ? payload
    : (payload && typeof payload === 'object' && Array.isArray(payload.trajectory))
      ? payload.trajectory
      : []
  const out = []
  rows.forEach((row, index) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) return
    const shares = row.shares
    if (!shares || typeof shares !== 'object' || Array.isArray(shares)) return
    const clean = {}
    let total = 0
    for (const [key, value] of Object.entries(shares)) {
      const num = Number(value)
      if (!Number.isFinite(num) || num < 0) continue
      clean[String(key)] = num
      total += num
    }
    if (!Object.keys(clean).length || total <= 0) return
    for (const key of Object.keys(clean)) clean[key] = clean[key] / total
    const round = Number(row.round)
    out.push({
      round: Number.isFinite(round) ? round : index,
      // CAL-TEMPORAL (schema v3): calendar rows carry period_end/as_of dates.
      label: typeof row.period_end === 'string' && row.period_end
        ? row.period_end
        : (typeof row.as_of === 'string' && row.as_of ? row.as_of : null),
      shares: clean,
    })
  })
  return out
}

/**
 * Build the stacked series: scenario names in first-appearance order (fixed
 * assignment — colors follow the entity, never the rank), folding overflow
 * series beyond `maxSeries` into "Other" so the categorical palette is never
 * cycled. Fewer than 2 usable rows → empty stack (area needs 2 time points).
 */
export function buildTrajectoryStack(payload, maxSeries = 8) {
  const points = normalizeTrajectoryRows(payload)
  if (points.length < 2) return { names: [], points: [] }
  const names = []
  for (const point of points) {
    for (const name of Object.keys(point.shares)) {
      if (!names.includes(name)) names.push(name)
    }
  }
  if (names.length <= maxSeries) return { names, points }
  const kept = names.slice(0, maxSeries - 1)
  const folded = new Set(names.slice(maxSeries - 1))
  for (const point of points) {
    let other = 0
    for (const name of folded) {
      other += point.shares[name] || 0
      delete point.shares[name]
    }
    point.shares.Other = (point.shares.Other || 0) + other
  }
  return { names: [...kept, 'Other'], points }
}

const fmt = value => String(Math.round(value * 100) / 100)

/**
 * Stacked-area band geometry for an inline SVG viewBox of `width`×`height`
 * (rendered with preserveAspectRatio="none"). Bands stack bottom-up in
 * `stack.names` order; each band returns its closed fill path `d` and its
 * upper-boundary `topLine` (polyline points) for the 2px surface-gap stroke.
 */
export function stackedAreaPaths(stack, width = 100, height = 30) {
  const points = (stack && stack.points) || []
  const names = (stack && stack.names) || []
  const n = points.length
  if (n < 2 || !names.length) return []
  const xs = points.map((_, i) => (i / (n - 1)) * width)
  const cumBefore = new Array(n).fill(0)
  const bands = []
  for (const name of names) {
    const upper = []
    const lower = []
    for (let i = 0; i < n; i++) {
      const value = points[i].shares[name] || 0
      lower.push([xs[i], height * (1 - cumBefore[i])])
      upper.push([xs[i], height * (1 - (cumBefore[i] + value))])
      cumBefore[i] += value
    }
    const d = 'M' + upper.map(p => `${fmt(p[0])},${fmt(p[1])}`).join(' L')
      + ' L' + lower.slice().reverse().map(p => `${fmt(p[0])},${fmt(p[1])}`).join(' L')
      + ' Z'
    bands.push({
      name,
      d,
      topLine: upper.map(p => `${fmt(p[0])},${fmt(p[1])}`).join(' '),
    })
  }
  return bands
}

// Beyond this span, filling every missing round with 0 would bloat the DOM;
// fall back to only the rounds that actually have actions.
const MAX_FILLED_ROUND_SPAN = 1000

/**
 * Per-round action counts from the accumulated action feed (both platforms
 * combined). Missing rounds inside the observed span are filled with 0 so the
 * x axis stays honest; actions without a numeric round_num are ignored.
 *
 * @returns {Array<{round: number, count: number}>} sorted by round
 */
export function actionsPerRound(actions) {
  const counts = new Map()
  for (const action of (actions || [])) {
    const round = Number(action && action.round_num)
    if (!Number.isFinite(round) || round < 0) continue
    counts.set(round, (counts.get(round) || 0) + 1)
  }
  if (!counts.size) return []
  const rounds = [...counts.keys()].sort((a, b) => a - b)
  const minRound = rounds[0]
  const maxRound = rounds[rounds.length - 1]
  if (maxRound - minRound > MAX_FILLED_ROUND_SPAN) {
    return rounds.map(round => ({ round, count: counts.get(round) }))
  }
  const out = []
  for (let round = minRound; round <= maxRound; round++) {
    out.push({ round, count: counts.get(round) || 0 })
  }
  return out
}

/**
 * Bar geometry for the per-round mini bar chart (same normalized viewBox as
 * the area chart). Bars are baseline-anchored with a small inter-bar gap.
 */
export function barGeometry(rows, width = 100, height = 30, gap = 0.6) {
  const list = rows || []
  const n = list.length
  if (!n) return []
  const maxCount = list.reduce((acc, row) => Math.max(acc, Number(row.count) || 0), 0)
  const slot = width / n
  const barGap = Math.min(gap, slot / 2)
  const barWidth = Math.max(slot - barGap, slot * 0.5)
  return list.map((row, i) => {
    const count = Number(row.count) || 0
    const h = maxCount > 0 ? (count / maxCount) * height : 0
    return {
      round: row.round,
      count,
      x: Math.round((i * slot + barGap / 2) * 100) / 100,
      w: Math.round(barWidth * 100) / 100,
      y: Math.round((height - h) * 100) / 100,
      h: Math.round(h * 100) / 100,
    }
  })
}
