/** Latest-request-only setup readiness. A failed check never means ready. */
export function createPreflightController({ request, onChange }) {
  let generation = 0
  let disposed = false

  async function check(mode) {
    if (disposed) return
    const requestedGeneration = ++generation
    onChange({ status: 'checking', errors: [], mode })
    try {
      const response = await request(mode)
      if (disposed || requestedGeneration !== generation) return
      const data = response?.data
      // Missing or malformed readiness is not evidence that launch is allowed.
      if (!data || typeof data.ready !== 'boolean' || response.success === false) {
        onChange({ status: 'unavailable', errors: [], mode })
        return
      }
      const errors = Array.isArray(data.errors)
        ? data.errors.filter(error => typeof error === 'string' && error.trim())
        : []
      onChange({ status: data.ready && !errors.length ? 'ready' : 'blocked', errors, mode })
    } catch {
      if (!disposed && requestedGeneration === generation) {
        onChange({ status: 'unavailable', errors: [], mode })
      }
    }
  }

  return {
    check,
    invalidate() { disposed = true; generation += 1 }
  }
}
