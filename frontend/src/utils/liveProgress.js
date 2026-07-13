/** Small content revision for a rolling log tail.
 *
 * Once a tail reaches its fixed line limit, its length no longer changes.  Using
 * only `lines.length` therefore makes adaptive polling back off even while new
 * lines arrive.  Hash the newest line so live output keeps the fast cadence.
 */
export function liveLogRevision(lines) {
  const safe = Array.isArray(lines) ? lines : []
  // Include a small suffix rather than only the final row.  Some legacy tool
  // output contains a stable multiline continuation after the changing event.
  const newest = safe.slice(-4).map((line) => String(line ?? '')).join('\n')
  let hash = 2166136261
  for (let i = 0; i < newest.length; i += 1) {
    hash ^= newest.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return `${safe.length}:${(hash >>> 0).toString(16)}`
}

const ISO_TIMESTAMP_PREFIX = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))(?=\s|$)/

function annotateProgressLines(lines) {
  let timestamp = null
  return lines.map((line) => {
    const match = line.match(ISO_TIMESTAMP_PREFIX)
    if (match) timestamp = match[1]
    return { line, timestamp }
  })
}

/** Accumulate an overlapping rolling tail without discarding prior history.
 *
 * Concurrent research tracks are merged chronologically by the backend. A
 * newly persisted event can therefore appear *inside* the next tail rather
 * than after the prior suffix. Track exact-line occurrence counts so overlap
 * is removed without collapsing legitimate duplicate emissions, then merge
 * genuinely new rows into the observed chronology. Backend timestamps are UTC
 * ISO-8601 strings, whose lexical order is their event order. A final exact
 * snapshot still replaces this accumulated view at research completion.
 */
export function mergeProgressLines(history, incoming) {
  const existing = Array.isArray(history)
    ? history.map((line) => String(line ?? ''))
    : []
  const next = Array.isArray(incoming)
    ? incoming.map((line) => String(line ?? ''))
    : []

  if (existing.length === 0) return next
  if (next.length === 0) return existing

  const existingCounts = new Map()
  for (const line of existing) {
    existingCounts.set(line, (existingCounts.get(line) || 0) + 1)
  }

  const incomingCounts = new Map()
  const novel = annotateProgressLines(next).filter(({ line }) => {
    const occurrence = (incomingCounts.get(line) || 0) + 1
    incomingCounts.set(line, occurrence)
    return occurrence > (existingCounts.get(line) || 0)
  })
  if (novel.length === 0) return existing

  const observed = annotateProgressLines(existing)
  const merged = []
  let observedIndex = 0
  let novelIndex = 0
  while (observedIndex < observed.length && novelIndex < novel.length) {
    const observedRow = observed[observedIndex]
    const novelRow = novel[novelIndex]
    // Untimestamped rows are retained in their already-observed position. An
    // unanchored incoming continuation is safest at the end of known history.
    if (
      !observedRow.timestamp
      || !novelRow.timestamp
      || observedRow.timestamp <= novelRow.timestamp
    ) {
      merged.push(observedRow.line)
      observedIndex += 1
    } else {
      merged.push(novelRow.line)
      novelIndex += 1
    }
  }
  merged.push(...observed.slice(observedIndex).map(({ line }) => line))
  merged.push(...novel.slice(novelIndex).map(({ line }) => line))
  return merged
}

/** Whether status observation requires a distinct terminal exact snapshot. */
export function needsFinalProgressSnapshot(pipelineStatus, researchStatus, finalized) {
  if (finalized) return false
  const pipelineTerminal = ['completed', 'failed', 'cancelled'].includes(String(pipelineStatus || ''))
  return pipelineTerminal || String(researchStatus || '') === 'completed'
}
