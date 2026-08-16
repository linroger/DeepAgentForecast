import assert from 'node:assert/strict'
import test from 'node:test'

import {
  GRAPH_TYPE_PALETTE,
  assignTypeColors,
  hashTypeName,
  nodeRadius,
} from '../src/utils/graphStyle.js'

test('type colors are deterministic and independent of input order', () => {
  const names = ['Person', 'Organization', 'Event', 'Location']
  const forward = assignTypeColors(names)
  const reversed = assignTypeColors([...names].reverse())
  for (const name of names) {
    assert.equal(forward.get(name), reversed.get(name))
    assert.ok(GRAPH_TYPE_PALETTE.includes(forward.get(name)))
  }
})

test('a type keeps its hash slot when its primary slot is uncontested', () => {
  const name = 'Person'
  const primary = hashTypeName(name) % GRAPH_TYPE_PALETTE.length
  const solo = assignTypeColors([name])
  assert.equal(solo.get(name), GRAPH_TYPE_PALETTE[primary])

  // Same color in a different graph whose other type occupies a different slot
  // (precondition asserted, so the test cannot silently rely on a collision).
  const other = 'Event'
  assert.notEqual(hashTypeName(other) % GRAPH_TYPE_PALETTE.length, primary)
  const pair = assignTypeColors([other, name])
  assert.equal(pair.get(name), GRAPH_TYPE_PALETTE[primary])
})

test('colors stay distinct while palette slots remain', () => {
  const names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
  const colors = [...assignTypeColors(names).values()]
  assert.equal(new Set(colors).size, names.length)
})

test('more types than slots falls back to hash reuse without throwing', () => {
  const names = Array.from({ length: 12 }, (_, i) => `Type${i}`)
  const map = assignTypeColors(names)
  assert.equal(map.size, 12)
  for (const color of map.values()) assert.ok(GRAPH_TYPE_PALETTE.includes(color))
})

test('node radius follows sqrt(degree) in the 6-18px range', () => {
  assert.equal(nodeRadius(0, 16), 6)
  assert.equal(nodeRadius(16, 16), 18)
  assert.equal(nodeRadius(4, 16), 12) // sqrt(0.25) = 0.5 → midpoint
  assert.equal(nodeRadius(3, 0), 10) // no edges → legacy uniform radius
  assert.equal(nodeRadius(NaN, 16), 6)
  assert.ok(nodeRadius(32, 16) <= 18) // degree above max clamps
})
