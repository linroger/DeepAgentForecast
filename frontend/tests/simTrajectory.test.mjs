import assert from 'node:assert/strict'
import test from 'node:test'

import {
  actionsPerRound,
  barGeometry,
  buildTrajectoryStack,
  normalizeTrajectoryRows,
  stackedAreaPaths,
} from '../src/utils/simTrajectory.js'

test('normalizeTrajectoryRows accepts wrapped and bare payloads, drops junk rows', () => {
  const wrapped = normalizeTrajectoryRows({
    trajectory: [
      { round: 0, shares: { A: 0.6, B: 0.4 } },
      'garbage',
      { round: 1, shares: {} },
      { round: 2, shares: { A: 1, B: 1 } },
      { round: 3, shares: { A: 'x', B: -1 } },
    ],
  })
  assert.equal(wrapped.length, 2)
  assert.deepEqual(wrapped[0].shares, { A: 0.6, B: 0.4 })
  assert.deepEqual(wrapped[1].shares, { A: 0.5, B: 0.5 }) // normalized to sum 1

  const bare = normalizeTrajectoryRows([{ round: 0, shares: { A: 1 } }])
  assert.equal(bare.length, 1)
})

test('normalizeTrajectoryRows keeps calendar labels (schema v3)', () => {
  const rows = normalizeTrajectoryRows([
    { round: 0, shares: { A: 1 }, period_end: '2026-08-01' },
    { round: 1, shares: { A: 1 }, as_of: '2026-08-02' },
    { round: 2, shares: { A: 1 } },
  ])
  assert.equal(rows[0].label, '2026-08-01')
  assert.equal(rows[1].label, '2026-08-02')
  assert.equal(rows[2].label, null)
})

test('buildTrajectoryStack needs two points and keeps first-appearance order', () => {
  assert.deepEqual(buildTrajectoryStack({ trajectory: [{ round: 0, shares: { A: 1 } }] }),
    { names: [], points: [] })
  const stack = buildTrajectoryStack({
    trajectory: [
      { round: 0, shares: { B: 0.5, A: 0.5 } },
      { round: 1, shares: { A: 0.25, C: 0.75 } },
    ],
  })
  assert.deepEqual(stack.names, ['B', 'A', 'C'])
  assert.equal(stack.points.length, 2)
})

test('buildTrajectoryStack folds overflow series into Other', () => {
  const shares = {}
  for (let i = 0; i < 10; i++) shares[`S${i}`] = 0.1
  const stack = buildTrajectoryStack({ trajectory: [
    { round: 0, shares }, { round: 1, shares },
  ] }, 8)
  assert.equal(stack.names.length, 8)
  assert.equal(stack.names[7], 'Other')
  const last = stack.points[1].shares
  assert.ok(Math.abs(last.Other - 0.3) < 1e-9) // S7+S8+S9 folded
})

test('stackedAreaPaths stacks bottom-up and exposes top boundary lines', () => {
  const stack = buildTrajectoryStack({ trajectory: [
    { round: 0, shares: { A: 0.5, B: 0.5 } },
    { round: 1, shares: { A: 0.5, B: 0.5 } },
  ] })
  const bands = stackedAreaPaths(stack, 100, 30)
  assert.equal(bands.length, 2)
  // Band A (bottom): upper boundary at half height, lower at the baseline.
  assert.equal(bands[0].topLine, '0,15 100,15')
  assert.equal(bands[0].d, 'M0,15 L100,15 L100,30 L0,30 Z')
  // Band B (top): upper boundary at the chart top.
  assert.equal(bands[1].topLine, '0,0 100,0')
  assert.equal(bands[1].d, 'M0,0 L100,0 L100,15 L0,15 Z')
})

test('stackedAreaPaths returns nothing for degenerate stacks', () => {
  assert.deepEqual(stackedAreaPaths({ names: [], points: [] }), [])
  assert.deepEqual(stackedAreaPaths(null), [])
})

test('actionsPerRound counts across platforms and fills gaps with zero', () => {
  const rows = actionsPerRound([
    { round_num: 1, platform: 'twitter' },
    { round_num: 1, platform: 'reddit' },
    { round_num: 3, platform: 'twitter' },
    { round_num: 'x' },
    {},
  ])
  assert.deepEqual(rows, [
    { round: 1, count: 2 },
    { round: 2, count: 0 },
    { round: 3, count: 1 },
  ])
  assert.deepEqual(actionsPerRound([]), [])
})

test('barGeometry anchors bars to the baseline and scales to the max count', () => {
  const rects = barGeometry([
    { round: 1, count: 2 },
    { round: 2, count: 4 },
    { round: 3, count: 0 },
  ], 100, 30, 0.6)
  assert.equal(rects.length, 3)
  const tallest = rects[1]
  assert.equal(tallest.h, 30)
  assert.equal(tallest.y, 0)
  const half = rects[0]
  assert.equal(half.h, 15)
  assert.equal(half.y, 15)
  const empty = rects[2]
  assert.equal(empty.h, 0)
  assert.equal(empty.y, 30)
  // bars stay inside the viewBox with a gap between them
  for (const rect of rects) {
    assert.ok(rect.x >= 0 && rect.x + rect.w <= 100 + 1e-9)
  }
  assert.ok(rects[0].x + rects[0].w < rects[1].x)
})
