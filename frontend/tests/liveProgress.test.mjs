import test from 'node:test'
import assert from 'node:assert/strict'

import { liveLogRevision } from '../src/utils/liveProgress.js'

test('rolling log revision changes when a full tail receives a new last line', () => {
  const before = Array.from({ length: 400 }, (_, i) => `line ${i}`)
  const after = before.slice(1).concat('line 400')

  assert.equal(before.length, after.length)
  assert.notEqual(liveLogRevision(before), liveLogRevision(after))
})

test('rolling log revision is stable and defensive', () => {
  assert.equal(liveLogRevision(null), liveLogRevision([]))
  assert.equal(liveLogRevision(['same']), liveLogRevision(['same']))
  assert.notEqual(liveLogRevision(['same']), liveLogRevision(['different']))
})

test('rolling log revision detects a changing event above a stable continuation', () => {
  assert.notEqual(
    liveLogRevision(['old event', 'stable continuation']),
    liveLogRevision(['new event', 'stable continuation'])
  )
})
