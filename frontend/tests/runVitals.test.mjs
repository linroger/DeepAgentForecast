import test from 'node:test'
import assert from 'node:assert/strict'

import {
  HEARTBEAT_FRESH_S,
  formatDurationCompact,
  formatTokensCompact,
  formatCostUsd,
  deriveLiveness,
  buildRunVitalsModel
} from '../src/utils/runVitals.js'

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

test('duration formatting is compact across s / m / h / d tiers', () => {
  assert.equal(formatDurationCompact(0), '0s')
  assert.equal(formatDurationCompact(42), '42s')
  assert.equal(formatDurationCompact(754), '12m 34s')
  assert.equal(formatDurationCompact(3723), '1h 02m')
  assert.equal(formatDurationCompact(12240), '3h 24m')
  assert.equal(formatDurationCompact(2 * 86400 + 5 * 3600 + 120), '2d 5h')
})

test('duration formatting rolls rounded seconds into the next unit and rejects junk', () => {
  // 59.6 rounds to 60 and must not print "60s".
  assert.equal(formatDurationCompact(59.6), '1m 00s')
  assert.equal(formatDurationCompact(null), null)
  assert.equal(formatDurationCompact(undefined), null)
  assert.equal(formatDurationCompact(-1), null)
  assert.equal(formatDurationCompact('not a number'), null)
  assert.equal(formatDurationCompact(NaN), null)
})

test('token compaction covers raw / K / M / B tiers and trims integral decimals', () => {
  assert.equal(formatTokensCompact(0), '0')
  assert.equal(formatTokensCompact(999), '999')
  assert.equal(formatTokensCompact(1000), '1K')
  assert.equal(formatTokensCompact(1500), '1.5K')
  assert.equal(formatTokensCompact(12_400_000), '12.4M')
  assert.equal(formatTokensCompact(3_210_000_000), '3.2B')
  // Numeric strings (JSON round-trips) are tolerated.
  assert.equal(formatTokensCompact('2500'), '2.5K')
})

test('token compaction promotes tiers at the rounding boundary instead of printing 1000.0K', () => {
  assert.equal(formatTokensCompact(999_949), '999.9K')
  assert.equal(formatTokensCompact(999_950), '1M')
  assert.equal(formatTokensCompact(999_950_000), '1B')
})

test('token compaction rejects negatives and non-numbers', () => {
  assert.equal(formatTokensCompact(null), null)
  assert.equal(formatTokensCompact(undefined), null)
  assert.equal(formatTokensCompact(-5), null)
  assert.equal(formatTokensCompact('junk'), null)
})

test('cost formatting renders two decimals and rejects junk', () => {
  assert.equal(formatCostUsd(1.234), '$1.23')
  assert.equal(formatCostUsd(0), '$0.00')
  assert.equal(formatCostUsd('3.5'), '$3.50')
  assert.equal(formatCostUsd(null), null)
  assert.equal(formatCostUsd(-0.01), null)
  assert.equal(formatCostUsd('abc'), null)
})

// ---------------------------------------------------------------------------
// Liveness derivation
// ---------------------------------------------------------------------------

test('liveness: fresh heartbeat with owner not known-dead is ok', () => {
  assert.equal(deriveLiveness({ heartbeat_age_s: 12, owner_alive: null, stale: false }, false), 'ok')
  assert.equal(deriveLiveness({ heartbeat_age_s: HEARTBEAT_FRESH_S - 1, owner_alive: true }, false), 'ok')
  // A confirmed-alive owner without heartbeat data still reads as ok.
  assert.equal(deriveLiveness({ owner_alive: true, heartbeat_age_s: null }, false), 'ok')
})

test('liveness: stale flag or a lapsed heartbeat degrades to still-thinking', () => {
  assert.equal(deriveLiveness({ stale: true, owner_alive: true, heartbeat_age_s: 5 }, false), 'stale')
  assert.equal(deriveLiveness({ stale: false, heartbeat_age_s: HEARTBEAT_FRESH_S }, false), 'stale')
  assert.equal(deriveLiveness({ stale: false, heartbeat_age_s: 300 }, false), 'stale')
})

test('liveness: a dead owner wins over everything else', () => {
  assert.equal(deriveLiveness({ owner_alive: false, heartbeat_age_s: 3, stale: false }, false), 'dead')
  assert.equal(deriveLiveness({ owner_alive: false, stale: true }, false), 'dead')
})

test('liveness: terminal runs and information-free payloads yield no chip', () => {
  assert.equal(deriveLiveness({ heartbeat_age_s: 3, owner_alive: true }, true), null)
  assert.equal(deriveLiveness({ heartbeat_age_s: null, owner_alive: null, stale: false }, false), null)
  assert.equal(deriveLiveness(null, false), null)
  assert.equal(deriveLiveness('junk', false), null)
})

// ---------------------------------------------------------------------------
// buildRunVitalsModel — the component's render contract:
// model === null ⇔ the strip renders nothing; a null item ⇔ that item only
// is omitted. Present / absent / partial payloads are pinned here.
// ---------------------------------------------------------------------------

const RUNNING = { status: 'running' }

test('render model: absent live block renders nothing', () => {
  assert.equal(buildRunVitalsModel(undefined, RUNNING), null)
  assert.equal(buildRunVitalsModel(null, RUNNING), null)
  assert.equal(buildRunVitalsModel([], RUNNING), null)
  assert.equal(buildRunVitalsModel('live', RUNNING), null)
  // A live block with nothing renderable also renders nothing.
  assert.equal(buildRunVitalsModel({}, RUNNING), null)
  assert.equal(
    buildRunVitalsModel({ elapsed_s: null, eta_s: null, stale: false, owner_alive: null }, RUNNING),
    null
  )
})

test('render model: active healthy run surfaces elapsed, eta, liveness, spend and budget', () => {
  const model = buildRunVitalsModel({
    elapsed_s: 754,
    eta_s: 1810,
    eta_approximate: true,
    stale: false,
    last_progress_age_s: 9,
    owner_pid: 4242,
    owner_alive: true,
    heartbeat_age_s: 11,
    spend_so_far: { tokens: 12_400_000, cost_usd: 1.234 },
    budget: { limit_tokens: 40_000_000, spent_tokens: 12_400_000, remaining_tokens: 27_600_000 }
  }, RUNNING)

  assert.ok(model)
  assert.equal(model.terminal, false)
  assert.equal(model.elapsedSeconds, 754)
  assert.equal(model.etaSeconds, 1810)
  assert.equal(model.liveness, 'ok')
  assert.equal(model.spendTokens, 12_400_000)
  assert.equal(model.spendCostUsd, 1.234)
  assert.equal(model.heartbeatAgeS, 11)
  assert.equal(model.lastProgressAgeS, 9)
  assert.equal(model.ownerPid, 4242)
  assert.deepEqual(model.budget, {
    limitTokens: 40_000_000,
    spentTokens: 12_400_000,
    remainingTokens: 27_600_000,
    pctUsed: 31,
    critical: false
  })
})

test('render model: partial payloads omit exactly the missing items', () => {
  const onlyElapsed = buildRunVitalsModel({ elapsed_s: 61 }, RUNNING)
  assert.ok(onlyElapsed)
  assert.equal(onlyElapsed.elapsedSeconds, 61)
  assert.equal(onlyElapsed.etaSeconds, null)
  assert.equal(onlyElapsed.liveness, null)
  assert.equal(onlyElapsed.spendTokens, null)
  assert.equal(onlyElapsed.budget, null)

  const onlyBudget = buildRunVitalsModel(
    { budget: { limit_tokens: 1_000_000, spent_tokens: 0 } }, RUNNING
  )
  assert.ok(onlyBudget)
  assert.equal(onlyBudget.elapsedSeconds, null)
  assert.deepEqual(onlyBudget.budget, {
    limitTokens: 1_000_000,
    spentTokens: 0,
    remainingTokens: 1_000_000,
    pctUsed: 0,
    critical: false
  })
})

test('render model: eta of zero or null never renders a misleading countdown', () => {
  assert.equal(buildRunVitalsModel({ elapsed_s: 10, eta_s: 0 }, RUNNING).etaSeconds, null)
  assert.equal(buildRunVitalsModel({ elapsed_s: 10, eta_s: null }, RUNNING).etaSeconds, null)
  assert.equal(buildRunVitalsModel({ elapsed_s: 10, eta_s: 90 }, RUNNING).etaSeconds, 90)
})

test('render model: terminal runs show final duration from timestamps, no eta, no liveness chip', () => {
  // Backend elapsed_s for a finished run is its age (here ~3 days) — the
  // model must ignore it and use created_at → updated_at instead.
  const model = buildRunVitalsModel(
    { elapsed_s: 259_200, eta_s: 0, stale: false, heartbeat_age_s: 259_100, owner_alive: null },
    {
      status: 'completed',
      createdAt: '2026-08-15T10:00:00+00:00',
      updatedAt: '2026-08-15T11:00:00+00:00'
    }
  )
  assert.ok(model)
  assert.equal(model.terminal, true)
  assert.equal(model.elapsedSeconds, 3600)
  assert.equal(model.etaSeconds, null)
  assert.equal(model.liveness, null)
})

test('render model: terminal resumed runs anchor the final duration at resumed_at', () => {
  const model = buildRunVitalsModel(
    { elapsed_s: 999_999, eta_s: 0 },
    {
      status: 'failed',
      createdAt: '2026-08-10T00:00:00+00:00',
      resumedAt: '2026-08-15T11:00:00+00:00',
      updatedAt: '2026-08-15T11:10:00+00:00'
    }
  )
  assert.equal(model.elapsedSeconds, 600)
})

test('render model: terminal runs without usable timestamps omit elapsed rather than mislead', () => {
  const withSpend = buildRunVitalsModel(
    { elapsed_s: 259_200, spend_so_far: { tokens: 5000, cost_usd: 0.4 } },
    { status: 'cancelled' }
  )
  assert.ok(withSpend)
  assert.equal(withSpend.elapsedSeconds, null)
  assert.equal(withSpend.spendTokens, 5000)

  // Nothing else renderable either → the whole strip disappears.
  assert.equal(
    buildRunVitalsModel({ elapsed_s: 259_200, eta_s: 0 }, { status: 'cancelled' }),
    null
  )
})

test('render model: zero-filled spend is treated as unmetered and omitted', () => {
  assert.equal(
    buildRunVitalsModel({ spend_so_far: { tokens: 0, cost_usd: 0 } }, RUNNING),
    null
  )
  const tokensOnly = buildRunVitalsModel({ spend_so_far: { tokens: 1500 } }, RUNNING)
  assert.equal(tokensOnly.spendTokens, 1500)
  assert.equal(tokensOnly.spendCostUsd, null)
})

test('render model: budget crosses critical strictly beyond 90% and clamps the fill', () => {
  const at90 = buildRunVitalsModel(
    { budget: { limit_tokens: 1_000_000, spent_tokens: 900_000, remaining_tokens: 100_000 } },
    RUNNING
  )
  assert.equal(at90.budget.pctUsed, 90)
  assert.equal(at90.budget.critical, false)

  const past90 = buildRunVitalsModel(
    { budget: { limit_tokens: 1_000_000, spent_tokens: 950_000, remaining_tokens: 50_000 } },
    RUNNING
  )
  assert.equal(past90.budget.pctUsed, 95)
  assert.equal(past90.budget.critical, true)

  const overrun = buildRunVitalsModel(
    { budget: { limit_tokens: 1_000_000, spent_tokens: 2_000_000, remaining_tokens: 0 } },
    RUNNING
  )
  assert.equal(overrun.budget.pctUsed, 100)
  assert.equal(overrun.budget.critical, true)
})

test('render model: a non-positive budget limit is not a budget', () => {
  assert.equal(
    buildRunVitalsModel({ budget: { limit_tokens: 0, spent_tokens: 10 } }, RUNNING),
    null
  )
  const missingRemaining = buildRunVitalsModel(
    { budget: { limit_tokens: 100, spent_tokens: 30 } }, RUNNING
  )
  assert.equal(missingRemaining.budget.remainingTokens, 70)
})
