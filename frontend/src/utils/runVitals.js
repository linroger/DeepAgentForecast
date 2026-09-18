/**
 * Run-vitals derivation for the computed `live` block on
 * GET /api/research/status/<pipeline_id> (backend I-5-6 / OBS-1).
 *
 * The block may be absent entirely (older servers, helper failure) and every
 * field inside it is individually nullable, so everything here is defensive:
 * a missing input yields `null` for that item only, and a `live` payload with
 * nothing renderable yields a `null` model (the strip renders nothing).
 *
 * All functions are pure so the RunVitals component template stays a thin
 * `v-if` mapping over the model returned by `buildRunVitalsModel`.
 */

/** Heartbeats younger than this (seconds) count as an actively-beating owner.
 *  Backend beats every PIPELINE_HEARTBEAT_INTERVAL_S (default 30s); 90s
 *  tolerates two missed beats before the chip degrades to "still thinking". */
export const HEARTBEAT_FRESH_S = 90

const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled']

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** Coerce to a finite non-negative number; anything else → null. */
function nonNegativeNumber(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return null
  return n
}

/** Coerce to a finite non-negative integer (rounded); anything else → null. */
function nonNegativeInt(value) {
  const n = nonNegativeNumber(value)
  return n === null ? null : Math.round(n)
}

/** Compact duration: "42s", "12m 05s", "3h 24m", "2d 5h". Null-safe. */
export function formatDurationCompact(seconds) {
  const total = nonNegativeInt(seconds)
  if (total === null) return null
  if (total < 60) return `${total}s`
  if (total < 3600) {
    const m = Math.floor(total / 60)
    const s = total % 60
    return `${m}m ${String(s).padStart(2, '0')}s`
  }
  if (total < 86400) {
    const h = Math.floor(total / 3600)
    const m = Math.floor((total % 3600) / 60)
    return `${h}h ${String(m).padStart(2, '0')}m`
  }
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  return `${d}d ${h}h`
}

/** Compact token count: "0", "999", "1.5K", "12.4M", "3.2B". Null-safe. */
export function formatTokensCompact(value) {
  const n = nonNegativeNumber(value)
  if (n === null) return null
  if (n < 1000) return String(Math.round(n))
  // Thresholds sit at the point where one-decimal rounding would print
  // "1000.0K" / "1000.0M": promote to the next tier instead.
  let base
  let suffix
  if (n >= 999.95e6) { base = 1e9; suffix = 'B' }
  else if (n >= 999.95e3) { base = 1e6; suffix = 'M' }
  else { base = 1e3; suffix = 'K' }
  const scaled = Math.round((n / base) * 10) / 10
  const text = Number.isInteger(scaled) ? String(scaled) : scaled.toFixed(1)
  return `${text}${suffix}`
}

/** Cost in USD to 2dp: "$1.23". Null-safe (negative / non-numeric → null). */
export function formatCostUsd(value) {
  const n = nonNegativeNumber(value)
  if (n === null) return null
  return `$${n.toFixed(2)}`
}

/**
 * Liveness of an in-flight pipeline: 'ok' | 'stale' | 'dead' | null.
 *
 * - terminal runs carry no liveness (the run is over, nothing to assert);
 * - owner_alive === false wins over everything: the owning process is gone;
 * - stale === true (no progress past the backend threshold) → 'stale'
 *   ("still thinking", not "dead" — the owner is alive or unknown);
 * - a fresh heartbeat (< HEARTBEAT_FRESH_S) → 'ok'; a lapsed one → 'stale';
 * - no heartbeat data at all: a confirmed-alive owner is still 'ok',
 *   otherwise nothing can be asserted → null (the chip is omitted).
 */
export function deriveLiveness(live, terminal) {
  if (terminal || !isPlainObject(live)) return null
  if (live.owner_alive === false) return 'dead'
  if (live.stale === true) return 'stale'
  const heartbeatAge = nonNegativeNumber(live.heartbeat_age_s)
  if (heartbeatAge !== null) return heartbeatAge < HEARTBEAT_FRESH_S ? 'ok' : 'stale'
  if (live.owner_alive === true) return 'ok'
  return null
}

/** Seconds between two ISO-8601 timestamps; null unless both parse and the
 *  span is non-negative. */
function spanSeconds(fromIso, toIso) {
  if (!fromIso || !toIso) return null
  const from = Date.parse(fromIso)
  const to = Date.parse(toIso)
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null
  const seconds = (to - from) / 1000
  return seconds >= 0 ? Math.round(seconds) : null
}

/**
 * Build the render model for the run-vitals strip, or null to render nothing.
 *
 * `live` is the raw `data.live` block (possibly undefined). `context` carries
 * sibling fields of the same status response:
 *   { status, createdAt, updatedAt, resumedAt }
 *
 * Terminal runs need the context: the backend computes `elapsed_s` as
 * age-since-created *now*, which for a run finished days ago is its age, not
 * its duration. For terminal states the model instead derives the final
 * duration from (resumed_at || created_at) → updated_at (updated_at stops
 * moving at the terminal write), and omits elapsed entirely when those
 * timestamps are unusable — never a misleading number.
 */
export function buildRunVitalsModel(live, context = {}) {
  if (!isPlainObject(live)) return null
  const status = String(context.status || '')
  const terminal = TERMINAL_STATUSES.includes(status)

  let elapsedSeconds
  if (terminal) {
    elapsedSeconds = spanSeconds(context.resumedAt || context.createdAt, context.updatedAt)
  } else {
    elapsedSeconds = nonNegativeInt(live.elapsed_s)
  }

  // ETA only ever renders for in-flight runs; terminal eta_s === 0 must not
  // produce a "~0s remaining" row, and 0 while running is equally unhelpful.
  const etaRaw = nonNegativeInt(live.eta_s)
  const etaSeconds = !terminal && etaRaw !== null && etaRaw > 0 ? etaRaw : null

  const liveness = deriveLiveness(live, terminal)

  // Explicit availability preserves a registered zero; legacy zero-filled
  // payloads still carry no evidence that accounting was initialized.
  let spendTokens = null
  let spendCostUsd = null
  let spendUnknown = false
  let usageComplete = null
  let spendCoverage = null
  if (isPlainObject(live.spend_so_far)) {
    const spend = live.spend_so_far
    const tokens = nonNegativeInt(spend.tokens)
    const cost = nonNegativeNumber(spend.cost_usd)
    spendUnknown = spend.available === false
      || (spend.available === true && tokens === null && cost === null)
    usageComplete = typeof spend.usage_complete === 'boolean' ? spend.usage_complete : null
    spendCoverage = typeof spend.coverage === 'string' ? spend.coverage : null
    if (!spendUnknown && (spend.available === true || tokens > 0 || cost > 0)) {
      spendTokens = tokens
      spendCostUsd = cost
    }
  }

  // Unknown spend cannot imply zero usage or an untouched budget. Only an
  // omitted remaining field may be derived from a known recorded total.
  let budget = null
  if (isPlainObject(live.budget)) {
    const limitTokens = nonNegativeInt(live.budget.limit_tokens)
    if (limitTokens !== null && limitTokens > 0) {
      const spentTokens = live.budget.available === false
        ? null : nonNegativeInt(live.budget.spent_tokens)
      const remainingTokens = spentTokens === null ? null
        : live.budget.remaining_tokens === undefined ? Math.max(0, limitTokens - spentTokens)
          : nonNegativeInt(live.budget.remaining_tokens)
      const ratio = spentTokens === null ? null : spentTokens / limitTokens
      budget = {
        limitTokens,
        spentTokens,
        remainingTokens,
        pctUsed: ratio === null ? null : Math.max(0, Math.min(100, Math.round(ratio * 100))),
        critical: ratio !== null && ratio > 0.9
      }
    }
  }

  const model = {
    terminal,
    elapsedSeconds,
    etaSeconds,
    liveness,
    spendTokens,
    spendCostUsd,
    spendUnknown,
    usageComplete,
    spendCoverage,
    budget,
    // Raw ages for the liveness tooltip (native title with the raw seconds).
    heartbeatAgeS: nonNegativeInt(live.heartbeat_age_s),
    lastProgressAgeS: nonNegativeInt(live.last_progress_age_s),
    ownerPid: nonNegativeInt(live.owner_pid)
  }

  const hasSpend = model.spendTokens !== null || model.spendCostUsd !== null
  if (
    model.elapsedSeconds === null
    && model.etaSeconds === null
    && model.liveness === null
    && !hasSpend
    && !model.spendUnknown
    && !model.budget
  ) return null
  return model
}
