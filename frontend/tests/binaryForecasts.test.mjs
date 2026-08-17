import assert from 'node:assert/strict'
import test from 'node:test'

import {
  binaryForecastRow,
  binaryForecastRows,
  confidenceLevel,
  formatDivergence,
  formatProbability,
  safeMarketUrl,
} from '../src/utils/binaryForecasts.js'

test('formatProbability renders one-decimal percentages and rejects non-numbers', () => {
  assert.equal(formatProbability(0.623), '62.3%')
  assert.equal(formatProbability(0), '0%')
  assert.equal(formatProbability(1), '100%')
  assert.equal(formatProbability('0.5'), '50%')
  assert.equal(formatProbability(''), '')
  assert.equal(formatProbability('nope'), '')
  assert.equal(formatProbability(null), '')
  assert.equal(formatProbability(true), '')
  assert.equal(formatProbability([]), '')
})

test('formatDivergence carries sign text and a tone that matches the rounded value', () => {
  assert.deepEqual(formatDivergence(0.123), { text: '+12.3pp', tone: 'pos' })
  assert.deepEqual(formatDivergence(-0.08), { text: '-8pp', tone: 'neg' })
  assert.deepEqual(formatDivergence(0), { text: '0pp', tone: 'zero' })
  // rounds to zero → text and tone agree (never '+0pp' colored positive)
  assert.deepEqual(formatDivergence(0.0004), { text: '0pp', tone: 'zero' })
  assert.equal(formatDivergence('n/a'), null)
  assert.equal(formatDivergence(undefined), null)
})

test('safeMarketUrl accepts only absolute http(s) URLs', () => {
  assert.equal(safeMarketUrl('https://polymarket.com/event/x'), 'https://polymarket.com/event/x')
  assert.equal(safeMarketUrl('  http://example.com/m  '), 'http://example.com/m')
  assert.equal(safeMarketUrl('javascript:alert(1)'), '')
  assert.equal(safeMarketUrl('ftp://example.com'), '')
  assert.equal(safeMarketUrl('/relative/path'), '')
  assert.equal(safeMarketUrl(42), '')
})

test('confidenceLevel normalizes known levels and flags unknown values', () => {
  assert.equal(confidenceLevel('High'), 'high')
  assert.equal(confidenceLevel(' medium '), 'medium')
  assert.equal(confidenceLevel('low'), 'low')
  assert.equal(confidenceLevel('speculative'), 'other')
  assert.equal(confidenceLevel(''), '')
  assert.equal(confidenceLevel(undefined), '')
})

test('builds a fully-populated row from a rich binary forecast', () => {
  const row = binaryForecastRow({
    id: 'b1',
    statement: 'BTC closes above $100k by 2026-12-31',
    probability: 0.62,
    confidence: 'High',
    adjustment_rationale: 'Base rate anchored to halving-cycle history.',
    p_low: 0.55,
    p_high: 0.7,
    interval_source: 'self_consistency_spread',
    market_anchor: {
      market_id: 'mkt-9',
      question: 'Will BTC close above $100k in 2026?',
      implied_yes_prob: 0.48,
      price_at_research: 0.48,
      divergence: 0.14,
      resolution_equivalence: 'Near',
      match_confidence: 0.85,
      url: 'https://polymarket.com/event/btc-100k',
      endDate: '2026-12-31',
    },
    market_influence: {
      market_id: 'mkt-9',
      market_question: 'Will BTC close above $100k in 2026?',
      prior_probability: 0.7,
      revised_probability: 0.62,
      anchor_removed: false,
      probability_restored: false,
    },
  }, 0)
  assert.equal(row.key, 'b1-0')
  assert.equal(row.statement, 'BTC closes above $100k by 2026-12-31')
  assert.equal(row.probabilityText, '62%')
  assert.deepEqual(row.interval, {
    lowText: '55%',
    highText: '70%',
    text: '55%–70%',
    source: 'self_consistency_spread',
  })
  assert.equal(row.confidenceLevel, 'high')
  assert.equal(row.rationale, 'Base rate anchored to halving-cycle history.')
  assert.equal(row.anchor.marketId, 'mkt-9')
  assert.equal(row.anchor.impliedText, '48%')
  assert.deepEqual(row.anchor.divergence, { text: '+14pp', tone: 'pos' })
  assert.equal(row.anchor.url, 'https://polymarket.com/event/btc-100k')
  assert.equal(row.anchor.equivalence, 'near')
  assert.equal(row.anchor.matchConfidenceText, '0.85')
  assert.equal(row.anchor.endDate, '2026-12-31')
  assert.equal(row.influence.priorText, '70%')
  assert.equal(row.influence.revisedText, '62%')
  assert.equal(row.influence.anchorRemoved, false)
  assert.equal(row.influence.probabilityRestored, false)
})

test('renders nothing when no binary forecasts exist', () => {
  assert.deepEqual(binaryForecastRows(null), [])
  assert.deepEqual(binaryForecastRows(undefined), [])
  assert.deepEqual(binaryForecastRows({}), [])
  assert.deepEqual(binaryForecastRows({ binary_forecasts: [] }), [])
  assert.deepEqual(binaryForecastRows({ binary_forecasts: 'not-a-list' }), [])
  assert.deepEqual(binaryForecastRows([]), [])
})

test('skips entries without a usable statement and non-object entries', () => {
  const rows = binaryForecastRows({
    binary_forecasts: [
      { probability: 0.5 },
      { statement: '   ' },
      'just a string',
      null,
      ['nested'],
      { statement: 'Kept', probability: 0.3 },
    ],
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].statement, 'Kept')
})

test('minimal row degrades per field: no anchor, influence, interval or confidence', () => {
  const row = binaryForecastRow({ statement: 'Sparse claim' }, 3)
  assert.equal(row.key, 'bf-3')
  assert.equal(row.id, '')
  assert.equal(row.probabilityText, '—')
  assert.equal(row.interval, null)
  assert.equal(row.confidenceLevel, '')
  assert.equal(row.anchor, null)
  assert.equal(row.influence, null)
})

test('interval requires ordered bounds inside [0,1]', () => {
  const base = { statement: 'S', probability: 0.4 }
  assert.equal(binaryForecastRow({ ...base, p_low: 0.5, p_high: 0.3 }, 0).interval, null)
  assert.equal(binaryForecastRow({ ...base, p_low: -0.1, p_high: 0.5 }, 0).interval, null)
  assert.equal(binaryForecastRow({ ...base, p_low: 0.2, p_high: 1.2 }, 0).interval, null)
  assert.equal(binaryForecastRow({ ...base, p_low: 0.2 }, 0).interval, null)
  const untagged = binaryForecastRow({ ...base, p_low: 0.3, p_high: 0.5 }, 0).interval
  assert.deepEqual(untagged, { lowText: '30%', highText: '50%', text: '30%–50%', source: '' })
})

test('anchor sanitizes unsafe URLs and drops undisplayable anchors', () => {
  const unsafe = binaryForecastRow({
    statement: 'S',
    market_anchor: { market_id: 'm1', implied_yes_prob: 0.4, url: 'javascript:alert(1)' },
  }, 0)
  assert.equal(unsafe.anchor.url, '')
  assert.equal(unsafe.anchor.impliedText, '40%')
  // market_id only (implied price missing) is still displayable — em-dash implied
  const idOnly = binaryForecastRow({
    statement: 'S',
    market_anchor: { market_id: 'm2' },
  }, 0)
  assert.equal(idOnly.anchor.impliedText, '—')
  assert.equal(idOnly.anchor.divergence, null)
  // an empty anchor object carries nothing displayable → dropped
  assert.equal(binaryForecastRow({ statement: 'S', market_anchor: {} }, 0).anchor, null)
  assert.equal(binaryForecastRow({ statement: 'S', market_anchor: 'mkt' }, 0).anchor, null)
})

test('influence badge survives anchor ejection and reports its audit state', () => {
  const row = binaryForecastRow({
    statement: 'S',
    probability: 0.55,
    market_influence: {
      market_id: 'm3',
      prior_probability: 0.55,
      revised_probability: 0.42,
      anchor_removed: true,
      probability_restored: true,
    },
  }, 0)
  assert.equal(row.anchor, null)
  assert.equal(row.influence.priorText, '55%')
  assert.equal(row.influence.revisedText, '42%')
  assert.equal(row.influence.anchorRemoved, true)
  assert.equal(row.influence.probabilityRestored, true)
})

test('influence with missing numeric fields falls back to em-dashes', () => {
  const row = binaryForecastRow({
    statement: 'S',
    market_influence: { market_id: 'm4', market_question: 'Q?' },
  }, 0)
  assert.equal(row.influence.priorText, '—')
  assert.equal(row.influence.revisedText, '—')
  assert.equal(row.influence.anchorRemoved, false)
  assert.equal(row.influence.probabilityRestored, false)
  assert.equal(row.influence.marketQuestion, 'Q?')
})

test('row keys stay unique even when payload ids collide', () => {
  const rows = binaryForecastRows({
    binary_forecasts: [
      { id: 'dup', statement: 'A' },
      { id: 'dup', statement: 'B' },
    ],
  })
  assert.deepEqual(rows.map(r => r.key), ['dup-0', 'dup-1'])
})
