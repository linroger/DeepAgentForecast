import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreflightController } from '../src/utils/preflightState.js'

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function fixture() {
  const requests = []
  const states = []
  const controller = createPreflightController({
    request: mode => {
      const result = deferred()
      requests.push({ mode, ...result })
      return result.promise
    },
    onChange: state => states.push(state)
  })
  return { controller, requests, states, latest: () => states.at(-1) }
}
const ready = { success: true, data: { ready: true, errors: [] } }

test('a check starts pending, becomes ready only on evidence, and retry clears readiness', async () => {
  const f = fixture()
  const first = f.controller.check('full')
  assert.deepEqual(f.latest(), { status: 'checking', errors: [], mode: 'full' })
  f.requests[0].resolve(ready)
  await first
  assert.equal(f.latest().status, 'ready')
  const retry = f.controller.check('full')
  assert.equal(f.latest().status, 'checking')
  f.requests[1].reject(new Error('offline'))
  await retry
  assert.deepEqual(f.latest(), { status: 'unavailable', errors: [], mode: 'full' })
  const recovered = f.controller.check('full')
  f.requests[2].resolve(ready)
  await recovered
  assert.equal(f.latest().status, 'ready')
})

test('blocked configuration preserves actionable errors without trusting non-text payloads', async () => {
  const f = fixture()
  const check = f.controller.check('full')
  f.requests[0].resolve({ data: { ready: false, errors: ['Configure research', null, {}, ''] } })
  await check
  assert.deepEqual(f.latest(), { status: 'blocked', errors: ['Configure research'], mode: 'full' })
})

test('contradictory ready plus errors is blocked', async () => {
  const f = fixture()
  const check = f.controller.check('full')
  f.requests[0].resolve({ data: { ready: true, errors: ['Missing required provider'] } })
  await check
  assert.equal(f.latest().status, 'blocked')
})

for (const response of [undefined, {}, { data: {} }, { data: { ready: 'true' } }, { success: false, data: { ready: true } }]) {
  test(`malformed response cannot become ready: ${JSON.stringify(response)}`, async () => {
    const f = fixture()
    const check = f.controller.check('full')
    f.requests[0].resolve(response)
    await check
    assert.equal(f.latest().status, 'unavailable')
  })
}

for (const lateResult of ['ready', 'blocked', 'failure']) {
  test(`mode change ignores stale ${lateResult} after the newer check`, async () => {
    const f = fixture()
    const old = f.controller.check('full')
    const current = f.controller.check('research_only')
    f.requests[1].resolve(ready)
    await current
    if (lateResult === 'failure') f.requests[0].reject(new Error('late failure'))
    else f.requests[0].resolve({ data: { ready: lateResult === 'ready', errors: [] } })
    await old
    assert.deepEqual(f.latest(), { status: 'ready', errors: [], mode: 'research_only' })
    assert.equal(f.states.length, 3)
  })
}

test('provider recheck in the same mode ignores the prior request while pending', async () => {
  const f = fixture()
  const old = f.controller.check('full')
  const current = f.controller.check('full')
  f.requests[0].resolve(ready)
  await old
  assert.equal(f.latest().status, 'checking')
  f.requests[1].resolve({ data: { ready: false, errors: ['Select a provider'] } })
  await current
  assert.equal(f.latest().status, 'blocked')
})

test('unmount invalidates pending responses and prevents new requests', async () => {
  const f = fixture()
  const pending = f.controller.check('full')
  f.controller.invalidate()
  f.requests[0].resolve(ready)
  await pending
  await f.controller.check('full')
  assert.equal(f.states.length, 1)
  assert.equal(f.requests.length, 1)
})
