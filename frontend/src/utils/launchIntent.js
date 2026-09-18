// A launch is an immutable user intent, not a comparison of prompt text alone.
// Web Locks coordinate tabs; the server remains the authority for admission.
export const LAUNCH_INTENT_KEY = 'drf_launch_intent_v1'
const LAUNCH_LOCK = 'drf_launch_intent_v1'
const INTENT_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i

function failure(code, message) {
  return Object.assign(new Error(message), { code })
}

function immutable(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(immutable)
    Object.freeze(value)
  }
  return value
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
  }
  return value
}

function samePayload(first, second) {
  return JSON.stringify(canonical(first)) === JSON.stringify(canonical(second))
}

function admissionFrom(response) {
  const data = response?.data
  const abandoned = data?.launch_status === 'abandoned' && data.pipeline_id === null &&
    data.task_id === null && data.mode === null && data.status === 'cancelled' && data.recovery_required === false
  if (response?.success === false || !data ||
      (data.launch_status === 'abandoned' && !abandoned) ||
      (!abandoned && (typeof data.pipeline_id !== 'string' || !/^pipe_[a-zA-Z0-9_-]+$/.test(data.pipeline_id))) ||
      typeof data.launch_status !== 'string' || typeof data.recovery_required !== 'boolean') {
    throw failure('launch_response_invalid', 'The launch response could not be verified. Check this saved launch again.')
  }
  return JSON.parse(JSON.stringify(data))
}

export function createLaunchIntentController({
  getStorage = () => globalThis.localStorage,
  locks = globalThis.navigator?.locks,
  randomUUID = () => globalThis.crypto.randomUUID(),
  now = () => new Date().toISOString(),
  submit,
  lookup,
  abandon,
  onChange = () => {}
} = {}) {
  let held = null
  let busy = false

  function setHeld(record) {
    held = immutable(record)
    onChange(held)
    return held
  }

  function read() {
    let raw
    try { raw = getStorage().getItem(LAUNCH_INTENT_KEY) } catch {
      throw failure('launch_storage_unavailable', 'Browser storage is unavailable. Enable storage before launching.')
    }
    if (raw === null) return null
    try {
      const record = JSON.parse(raw)
      if (record.version !== 1 || !INTENT_ID.test(record.intent_id) ||
          typeof record.created_at !== 'string' || !Number.isFinite(Date.parse(record.created_at)) ||
          !record.payload || Array.isArray(record.payload) || typeof record.payload !== 'object' ||
          typeof record.payload.prompt !== 'string' || !record.payload.prompt.trim() ||
          !Object.hasOwn(record, 'admission')) throw new Error('Invalid launch record')
      if (record.admission !== null) admissionFrom({ data: record.admission })
      return record
    } catch {
      throw failure('launch_storage_invalid', 'The saved launch record is unreadable. Preserve it and inspect run history before starting another run.')
    }
  }

  function write(record) {
    try {
      const raw = JSON.stringify(record)
      getStorage().setItem(LAUNCH_INTENT_KEY, raw)
      if (getStorage().getItem(LAUNCH_INTENT_KEY) !== raw) throw new Error('Storage did not retain the launch')
    } catch {
      throw failure('launch_storage_unavailable', 'The launch could not be saved in this browser. Enable storage before launching.')
    }
  }

  function remove() {
    try {
      getStorage().removeItem(LAUNCH_INTENT_KEY)
      if (getStorage().getItem(LAUNCH_INTENT_KEY) !== null) throw new Error('Storage did not clear')
    } catch {
      throw failure('launch_storage_unavailable', 'The new-launch choice could not be saved. Enable browser storage and try again.')
    }
  }

  function locked(callback) {
    if (!locks?.request) {
      throw failure('launch_coordination_unavailable', 'Safe launch coordination requires a browser with Web Locks on HTTPS or localhost.')
    }
    return locks.request(LAUNCH_LOCK, callback)
  }

  async function operation(callback) {
    if (busy) throw failure('launch_busy', 'A launch check or submission is already in progress.')
    busy = true
    try { return await callback() } finally { busy = false }
  }

  function requireHeld() {
    if (!held) throw failure('launch_missing', 'There is no saved launch to recover.')
    return held
  }

  async function accept(response, intent) {
    const admission = admissionFrom(response)
    const updated = { ...intent, admission }
    await locked(() => {
      const shared = read()
      // A late response from an old tab must never replace a newer intent.
      if (shared?.intent_id === intent.intent_id) {
        if (!samePayload(shared.payload, intent.payload)) {
          throw failure('launch_storage_invalid', 'The saved launch changed unexpectedly. Check run history before continuing.')
        }
        write(updated)
      }
      setHeld(updated)
    })
    return { ...response, data: held.admission }
  }

  async function sendHeld() {
    const intent = requireHeld()
    if (intent.admission) return { success: true, data: intent.admission }
    await locked(() => {
      const shared = read()
      if (shared?.intent_id !== intent.intent_id) {
        throw failure('launch_superseded', 'Another tab has changed the current launch. Check this saved launch or reload to view the current one.')
      }
      if (!samePayload(shared.payload, intent.payload)) {
        throw failure('launch_payload_changed', 'The saved request differs from these inputs. Recover the original launch first.')
      }
      // Verify persistence immediately before admission, including retries.
      write(shared)
    })
    return accept(await submit(intent.payload, intent.intent_id), intent)
  }

  return {
    current: () => held,
    restore: () => operation(() => locked(() => setHeld(read()))),
    start: payload => operation(async () => {
      const snapshot = JSON.parse(JSON.stringify(payload))
      await locked(() => {
        if (!held) setHeld(read())
        if (held) {
          if (!samePayload(held.payload, snapshot)) {
            throw failure('launch_payload_changed', 'A saved launch already exists with different inputs. Recover it before starting another run.')
          }
          return
        }
        const record = { version: 1, intent_id: randomUUID(), created_at: now(), payload: snapshot, admission: null }
        if (!INTENT_ID.test(record.intent_id)) throw failure('launch_id_unavailable', 'A secure launch identity could not be generated.')
        write(record)
        setHeld(record)
      })
      return sendHeld()
    }),
    retry: () => operation(sendHeld),
    check: () => operation(async () => {
      const intent = requireHeld()
      return accept(await lookup(intent.intent_id), intent)
    }),
    abandon: () => operation(async () => {
      const intent = requireHeld()
      // Only the server can close an unadmitted key atomically against a late
      // POST. A 400, 404, or an absent local record is not proof of retirement.
      const response = await accept(await abandon(intent.intent_id), intent)
      if (response.data.launch_status === 'abandoned') {
        await locked(() => {
          if (read()?.intent_id === intent.intent_id) remove()
          setHeld(null)
        })
      }
      return response
    }),
    newLaunch: ({ confirmRecovery = false } = {}) => operation(() => locked(() => {
      const shared = read()
      if (shared && shared.intent_id !== held?.intent_id) {
        throw failure('launch_superseded', 'Another tab has a different current launch. Reload before creating a new one.')
      }
      const record = shared || held
      if (record && !record.admission) {
        throw failure('launch_unresolved', 'The previous launch is not resolved. Check that launch before creating another one.')
      }
      if (record?.admission?.recovery_required && !confirmRecovery) {
        throw failure('launch_confirmation_required', 'This admitted pipeline may still be running. Confirm an intentional new run before releasing its saved launch.')
      }
      remove()
      setHeld(null)
    }))
  }
}
