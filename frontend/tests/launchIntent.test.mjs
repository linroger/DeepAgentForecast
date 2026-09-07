import test from 'node:test'
import assert from 'node:assert/strict'
import { createLaunchIntentController, LAUNCH_INTENT_KEY } from '../src/utils/launchIntent.js'

function fixtures() {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key)
  }
  let queue = Promise.resolve()
  const locks = { request: (_key, callback) => {
    const result = queue.then(callback)
    queue = result.catch(() => {})
    return result
  } }
  let counter = 0
  const sent = []
  const lookups = []
  const admitted = new Map()
  const abandoned = new Map()
  const abandonments = []
  const response = id => ({ success: true, data: {
    pipeline_id: `pipe_${id.slice(-12)}`, task_id: 'task_1', mode: 'full',
    status: 'running', launch_status: 'dispatched', replayed: false, recovery_required: false
  } })
  const options = {
    getStorage: () => storage,
    locks,
    randomUUID: () => `00000000-0000-4000-8000-${String(++counter).padStart(12, '0')}`,
    now: () => '2026-09-07T00:00:00.000Z',
    submit: async (payload, id) => {
      const stored = JSON.parse(storage.getItem(LAUNCH_INTENT_KEY))
      assert.equal(stored.intent_id, id, 'persist before POST')
      assert.deepEqual(stored.payload, payload)
      sent.push({ payload, id })
      if (abandoned.has(id)) return abandoned.get(id)
      if (!admitted.has(id)) admitted.set(id, response(id))
      return admitted.get(id)
    },
    lookup: async id => {
      lookups.push(id)
      if (abandoned.has(id)) return abandoned.get(id)
      if (!admitted.has(id)) throw Object.assign(new Error('Unknown launch'), { response: { status: 404 } })
      return admitted.get(id)
    },
    abandon: async id => {
      abandonments.push(id)
      if (admitted.has(id)) return admitted.get(id)
      const result = { success: true, data: {
        pipeline_id: null, task_id: null, mode: null, status: 'cancelled',
        launch_status: 'abandoned', replayed: false, recovery_required: false
      } }
      abandoned.set(id, result)
      return result
    }
  }
  return { storage, values, sent, lookups, admitted, abandoned, abandonments, options, response, create: extra => createLaunchIntentController({ ...options, ...extra }) }
}

const payload = { prompt: 'A repeatable forecast', mode: 'full', depth: 'deep' }

test('persists exact immutable JSON payload before sending and keeps resolved identity', async () => {
  const f = fixtures()
  const controller = f.create()
  const input = { ...payload, language: undefined }
  await controller.start(input)
  input.prompt = 'Later edits'
  assert.deepEqual(controller.current().payload, payload)
  assert.equal(f.sent.length, 1)
  assert.equal(JSON.parse(f.storage.getItem(LAUNCH_INTENT_KEY)).admission.pipeline_id, 'pipe_000000000001')
  await controller.start(payload)
  assert.equal(f.sent.length, 1, 'known admission must not POST again')
})

test('lost response survives reconstruction; restore and check only issue GET', async () => {
  const f = fixtures()
  const original = f.create({ submit: async (...args) => { await f.options.submit(...args); throw new Error('Network Error') } })
  await assert.rejects(original.start(payload), /Network/)
  const id = original.current().intent_id
  const restored = f.create()
  await restored.restore()
  assert.equal(f.sent.length, 1)
  assert.equal(f.lookups.length, 0)
  const result = await restored.check()
  assert.equal(result.data.pipeline_id, 'pipe_000000000001')
  assert.deepEqual(f.lookups, [id])
  assert.equal(f.sent.length, 1)
})

test('explicit retry uses original key and snapshot; changed inputs cannot replace pending intent', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async (...args) => { await f.options.submit(...args); throw new Error('lost') } })
  await assert.rejects(controller.start(payload), /lost/)
  const id = controller.current().intent_id
  await assert.rejects(controller.start({ ...payload, depth: 'quick' }), { code: 'launch_payload_changed' })
  await assert.rejects(controller.retry(), /lost/)
  assert.equal(f.sent.length, 2)
  assert.ok(f.sent.every(item => item.id === id && item.payload.depth === 'deep'))
})

test('two tabs atomically acquire the same intent and preserve it after admission', async () => {
  const f = fixtures()
  const first = f.create()
  const second = f.create()
  const results = await Promise.all([first.start(payload), second.start(payload)])
  assert.equal(first.current().intent_id, second.current().intent_id)
  assert.equal(results[0].data.pipeline_id, results[1].data.pipeline_id)
  assert.equal(f.admitted.size, 1)
  const stale = f.create()
  await stale.start(payload)
  assert.equal(f.admitted.size, 1)
})

test('double-click cannot queue a second POST while one is in flight', async () => {
  const f = fixtures()
  let resolve
  const controller = f.create({ submit: (...args) => new Promise(r => { resolve = () => r(f.options.submit(...args)) }) })
  const pending = controller.start(payload)
  await assert.rejects(controller.start(payload), { code: 'launch_busy' })
  await new Promise(resolve => setImmediate(resolve))
  resolve()
  await pending
  assert.equal(f.sent.length, 1)
})

test('storage or coordination failure prevents all launch POSTs', async () => {
  const f = fixtures()
  const cases = [
    { locks: null },
    { getStorage: () => { throw new Error('SecurityError') } },
    { getStorage: () => ({ ...f.storage, setItem: () => { throw new Error('QuotaExceededError') } }) },
    { getStorage: () => ({ ...f.storage, setItem: () => {} }) }
  ]
  for (const options of cases) await assert.rejects(f.create(options).start(payload))
  assert.equal(f.sent.length, 0)
})

test('malformed stored identity is preserved and blocks fresh submission', async () => {
  const f = fixtures()
  f.storage.setItem(LAUNCH_INTENT_KEY, '{broken')
  await assert.rejects(f.create().start(payload), { code: 'launch_storage_invalid' })
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), '{broken')
  assert.equal(f.sent.length, 0)
})

test('404, 409, and 503 retain the recovery record and never generate another key', async () => {
  for (const status of [404, 409, 503]) {
    const f = fixtures()
    const controller = f.create({ submit: async () => { throw Object.assign(new Error('failure'), { response: { status } }) } })
    await assert.rejects(controller.start(payload))
    const before = f.storage.getItem(LAUNCH_INTENT_KEY)
    await assert.rejects(controller.retry())
    await assert.rejects(controller.newLaunch(), { code: 'launch_unresolved' })
    assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), before)
  }
})

test('incomplete admission is retained for read-only recovery without automatic retry', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async (body, id) => {
    const response = await f.options.submit(body, id)
    response.data.recovery_required = true
    response.data.launch_status = 'admitted'
    return response
  } })
  const result = await controller.start(payload)
  assert.equal(result.data.recovery_required, true)
  await controller.retry()
  assert.equal(f.sent.length, 1)
  await assert.rejects(controller.newLaunch(), { code: 'launch_confirmation_required' })
})

test('only explicit New after admission permits another key for the same forecast', async () => {
  const f = fixtures()
  const controller = f.create()
  await controller.start(payload)
  const oldId = controller.current().intent_id
  await controller.newLaunch()
  await controller.start(payload)
  assert.notEqual(controller.current().intent_id, oldId)
  assert.equal(f.admitted.size, 2)
})

test('unavailable admitted identity permits only a confirmed intentional New, not replay', async () => {
  const f = fixtures()
  const controller = f.create()
  await controller.start(payload)
  const original = controller.current().intent_id
  f.admitted.get(original).data.recovery_required = true
  f.admitted.get(original).data.launch_status = 'unavailable'
  await controller.check()
  await assert.rejects(controller.newLaunch(), { code: 'launch_confirmation_required' })
  await controller.retry()
  assert.equal(f.sent.length, 1)
  await controller.newLaunch({ confirmRecovery: true })
  await controller.start(payload)
  assert.notEqual(controller.current().intent_id, original)
  assert.equal(f.admitted.size, 2)
})

test('confirmation cannot release a request whose admission is still unknown', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async () => { throw new Error('lost') } })
  await assert.rejects(controller.start(payload))
  await assert.rejects(controller.newLaunch({ confirmRecovery: true }), { code: 'launch_unresolved' })
})

test('a stale tab cannot erase a replacement intent or overwrite it with a late result', async () => {
  const f = fixtures()
  const stale = f.create({ submit: async (...args) => { await f.options.submit(...args); throw new Error('lost') } })
  await assert.rejects(stale.start(payload), /lost/)
  const other = f.create()
  await other.restore()
  await other.check()
  await other.newLaunch()
  await other.start({ ...payload, prompt: 'Intentional new forecast' })
  const replacement = f.storage.getItem(LAUNCH_INTENT_KEY)
  await assert.rejects(stale.retry(), { code: 'launch_superseded' })
  await stale.check()
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), replacement)
  await assert.rejects(stale.newLaunch(), { code: 'launch_superseded' })
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), replacement)
})

test('invalid success response leaves the original recovery identity intact', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async () => ({ success: true, data: {} }) })
  await assert.rejects(controller.start(payload), { code: 'launch_response_invalid' })
  assert.equal(controller.current().admission, null)
  assert.ok(f.storage.getItem(LAUNCH_INTENT_KEY))
})

test('explicit server retirement releases rejected inputs only after an abandoned response', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async () => { throw Object.assign(new Error('invalid inputs'), { response: { status: 400 } }) } })
  await assert.rejects(controller.start(payload))
  const original = controller.current().intent_id
  assert.ok(f.storage.getItem(LAUNCH_INTENT_KEY))
  const result = await controller.abandon()
  assert.equal(result.data.launch_status, 'abandoned')
  assert.deepEqual(f.abandonments, [original])
  assert.equal(controller.current(), null)
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), null)
  await assert.rejects(controller.start({ ...payload, prompt: 'Edited inputs' }))
  assert.notEqual(controller.current().intent_id, original)
  assert.ok(f.abandoned.has(original))
})

test('retirement preserves an already admitted run whose response was lost', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async (...args) => { await f.options.submit(...args); throw new Error('lost') } })
  await assert.rejects(controller.start(payload))
  const original = controller.current().intent_id
  const result = await controller.abandon()
  assert.equal(result.data.pipeline_id, 'pipe_000000000001')
  assert.equal(controller.current().intent_id, original)
  assert.ok(f.storage.getItem(LAUNCH_INTENT_KEY))
  assert.equal(f.abandoned.size, 0)
  assert.equal(f.sent.length, 1)
})

test('failed retirement keeps the original request immutable and retriable', async () => {
  const f = fixtures()
  const controller = f.create({
    submit: async () => { throw new Error('lost') },
    abandon: async () => { throw new Error('lookup unavailable') }
  })
  await assert.rejects(controller.start(payload))
  const before = f.storage.getItem(LAUNCH_INTENT_KEY)
  await assert.rejects(controller.abandon(), /unavailable/)
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), before)
})

test('contradictory retirement response cannot clear a launch with a pipeline identity', async () => {
  const f = fixtures()
  const controller = f.create({
    submit: async () => { throw new Error('lost') },
    abandon: async id => ({ ...f.response(id), data: { ...f.response(id).data, launch_status: 'abandoned' } })
  })
  await assert.rejects(controller.start(payload))
  const before = f.storage.getItem(LAUNCH_INTENT_KEY)
  await assert.rejects(controller.abandon(), { code: 'launch_response_invalid' })
  assert.equal(f.storage.getItem(LAUNCH_INTENT_KEY), before)
})

test('abandoned nullable identity survives reload without posting another launch', async () => {
  const f = fixtures()
  const controller = f.create({ submit: async () => { throw new Error('lost') } })
  await assert.rejects(controller.start(payload))
  await f.options.abandon(controller.current().intent_id)
  await controller.check()
  assert.equal(controller.current().admission.pipeline_id, null)
  const restored = f.create()
  await restored.restore()
  await restored.check()
  assert.equal(restored.current().admission.launch_status, 'abandoned')
  assert.equal(f.sent.length, 0)
  await restored.newLaunch()
  assert.equal(restored.current(), null)
})
