/**
 * binaryForecasts.js — pure row models for the first-class binary-forecast table.
 *
 * LOOP-017: the report UI used to show binary forecasts only as whatever
 * markdown the report happened to contain. forecast.json — served whole by
 * GET /api/report/<id>/forecast, which ForecastReport.vue already fetches —
 * carries a structured `binary_forecasts[]` payload ({id, statement,
 * probability, confidence, adjustment_rationale, p_low/p_high/interval_source,
 * market_anchor{...}, market_influence{...}}). This module folds each raw entry
 * into a display-ready, language-neutral row model so BinaryForecastTable.vue
 * stays a thin template; bilingual labels/tooltips are assembled in the
 * component via L().
 *
 * Contract: never throws on malformed payloads — invalid entries are skipped
 * and absent fields collapse to null/'' so the template degrades per cell.
 */

/** Strict numeric coercion: numbers and non-empty numeric strings only. */
function num(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  return null
}

function str(v) {
  return typeof v === 'string' ? v.trim() : ''
}

function isPlainObject(v) {
  return Boolean(v) && typeof v === 'object' && !Array.isArray(v)
}

/** 0..1 probability → '62.3%' (same rounding as the dashboard's pct()); non-numeric → ''. */
export function formatProbability(v) {
  const n = num(v)
  if (n == null) return ''
  return (Math.round(n * 1000) / 10) + '%'
}

/**
 * model − market divergence (0..1 scale) → { text: '+12.3pp', tone: 'pos'|'neg'|'zero' }.
 * Tone is derived from the ROUNDED value so text and color never disagree.
 * Non-numeric → null (cell omits the delta).
 */
export function formatDivergence(v) {
  const n = num(v)
  if (n == null) return null
  const pp = Math.round(n * 1000) / 10
  return {
    text: (pp > 0 ? '+' : '') + pp + 'pp',
    tone: pp > 0 ? 'pos' : (pp < 0 ? 'neg' : 'zero'),
  }
}

/** Market URLs render as live links — accept absolute http(s) only (no javascript:/data:). */
export function safeMarketUrl(value) {
  const url = str(value)
  return /^https?:\/\//i.test(url) ? url : ''
}

/** 'High' → 'high'; unknown non-empty → 'other'; absent/blank → ''. */
export function confidenceLevel(value) {
  const c = String(value == null ? '' : value).trim().toLowerCase()
  if (!c) return ''
  return (c === 'low' || c === 'medium' || c === 'high') ? c : 'other'
}

/**
 * i3 self-consistency interval: requires ordered bounds within [0,1]
 * (0 ≤ p_low ≤ p_high ≤ 1); anything else → null (point estimate only).
 * `source` carries interval_source provenance for the tooltip ('' when untagged).
 */
function intervalModel(raw) {
  const low = num(raw.p_low)
  const high = num(raw.p_high)
  if (low == null || high == null) return null
  if (low < 0 || high > 1 || low > high) return null
  return {
    lowText: formatProbability(low),
    highText: formatProbability(high),
    text: formatProbability(low) + '–' + formatProbability(high),
    source: str(raw.interval_source),
  }
}

/**
 * market_anchor → display model. Dropped (null) when nothing displayable
 * remains (neither an implied probability nor a market id).
 */
function anchorModel(raw) {
  const anchor = raw.market_anchor
  if (!isPlainObject(anchor)) return null
  const impliedText = formatProbability(anchor.implied_yes_prob)
  const marketId = str(anchor.market_id)
  if (!impliedText && !marketId) return null
  const matchConf = num(anchor.match_confidence)
  return {
    marketId,
    question: str(anchor.question),
    impliedText: impliedText || '—',
    priceText: formatProbability(anchor.price_at_research),
    divergence: formatDivergence(anchor.divergence),
    url: safeMarketUrl(anchor.url),
    equivalence: str(anchor.resolution_equivalence).toLowerCase(),
    matchConfidenceText: matchConf == null ? '' : String(Math.round(matchConf * 100) / 100),
    endDate: str(anchor.endDate),
  }
}

/**
 * market_influence stamp → badge model. The stamp survives anchor ejection
 * (extractor contract), so it is modeled independently of `anchor`:
 * anchorRemoved / probabilityRestored mirror the audit booleans strictly.
 */
function influenceModel(raw) {
  const inf = raw.market_influence
  if (!isPlainObject(inf)) return null
  return {
    marketId: str(inf.market_id),
    marketQuestion: str(inf.market_question),
    priorText: formatProbability(inf.prior_probability) || '—',
    revisedText: formatProbability(inf.revised_probability) || '—',
    anchorRemoved: inf.anchor_removed === true,
    probabilityRestored: inf.probability_restored === true,
  }
}

/**
 * One raw binary forecast → row model, or null when the entry has no usable
 * statement (a probability without a proposition is unreadable in a table).
 * `key` is always unique per list position even when ids collide.
 */
export function binaryForecastRow(raw, index = 0) {
  if (!isPlainObject(raw)) return null
  const statement = str(raw.statement)
  if (!statement) return null
  const id = str(raw.id)
  return {
    key: (id || 'bf') + '-' + index,
    id,
    statement,
    rationale: str(raw.adjustment_rationale),
    probabilityText: formatProbability(raw.probability) || '—',
    interval: intervalModel(raw),
    confidenceLevel: confidenceLevel(raw.confidence),
    confidenceRaw: String(raw.confidence == null ? '' : raw.confidence).trim(),
    anchor: anchorModel(raw),
    influence: influenceModel(raw),
  }
}

/** Forecast payload → row models. Missing/invalid `binary_forecasts` → []. */
export function binaryForecastRows(forecast) {
  const list = isPlainObject(forecast) && Array.isArray(forecast.binary_forecasts)
    ? forecast.binary_forecasts
    : []
  const rows = []
  list.forEach((raw, i) => {
    const row = binaryForecastRow(raw, i)
    if (row) rows.push(row)
  })
  return rows
}
