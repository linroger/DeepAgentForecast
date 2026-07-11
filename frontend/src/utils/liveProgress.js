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
