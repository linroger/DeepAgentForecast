import test from 'node:test'
import assert from 'node:assert/strict'

import {
  liveLogRevision,
  mergeProgressLines,
  needsFinalProgressSnapshot
} from '../src/utils/liveProgress.js'

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

test('live progress accumulation preserves early history and removes overlapping tail rows', () => {
  const history = ['opening', 'search 1', 'search 2']
  const overlappingTail = ['search 2', 'search 3', 'synthesis']

  assert.deepEqual(
    mergeProgressLines(history, overlappingTail),
    ['opening', 'search 1', 'search 2', 'search 3', 'synthesis']
  )
})

test('live progress accumulation inserts a late concurrent-track event without duplicating its tail', () => {
  const history = [
    '2026-07-12T00:00:01.000000+00:00 [track:1] A',
    '2026-07-12T00:00:03.000000+00:00 [track:1] C'
  ]
  const reorderedTail = [
    '2026-07-12T00:00:01.000000+00:00 [track:1] A',
    '2026-07-12T00:00:02.000000+00:00 [track:2] B arrived late',
    '2026-07-12T00:00:03.000000+00:00 [track:1] C'
  ]

  assert.deepEqual(
    mergeProgressLines(history, reorderedTail),
    [history[0], reorderedTail[1], history[1]]
  )
})

test('live progress accumulation is defensive and preserves repeated rows already in history', () => {
  assert.deepEqual(mergeProgressLines(null, ['a', null]), ['a', ''])
  assert.deepEqual(mergeProgressLines(['same', 'same'], ['same', 'next']), ['same', 'same', 'next'])
  assert.deepEqual(mergeProgressLines(['a', 'same'], ['same', 'same']), ['a', 'same', 'same'])
})

test('terminal and research-complete states require a distinct exact snapshot', () => {
  assert.equal(needsFinalProgressSnapshot('running', 'running', false), false)
  assert.equal(needsFinalProgressSnapshot('running', 'completed', false), true)
  assert.equal(needsFinalProgressSnapshot('completed', 'completed', false), true)
  assert.equal(needsFinalProgressSnapshot('failed', 'failed', false), true)
  assert.equal(needsFinalProgressSnapshot('cancelled', 'running', false), true)
  assert.equal(needsFinalProgressSnapshot('completed', 'completed', true), false)
})
