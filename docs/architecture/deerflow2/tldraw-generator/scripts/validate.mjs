import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createTLStore, parseTldrawJsonFile } from 'tldraw'

const here = path.dirname(fileURLToPath(import.meta.url))
const outputDir = path.resolve(here, '../..')
const scenePath = path.join(outputDir, 'deerflow2-architecture.tldr')
const svgPath = path.join(outputDir, 'deerflow2-architecture.svg')
const pngPath = path.join(outputDir, 'deerflow2-architecture.png')
const metadataPath = path.join(outputDir, 'render-metadata.json')

const sceneJson = fs.readFileSync(scenePath, 'utf8')
const schemaStore = createTLStore()
const parsed = parseTldrawJsonFile({ json: sceneJson, schema: schemaStore.schema })
assert.equal(parsed.ok, true, `invalid .tldr scene: ${JSON.stringify(parsed.error)}`)

const serialized = JSON.parse(sceneJson)
const shapeRecords = serialized.records.filter((record) => record.typeName === 'shape')
const shapeCount = shapeRecords.length
const shapeIds = shapeRecords.map((record) => record.id)
const uniqueShapeIds = new Set(shapeIds)
const arrowRecords = shapeRecords.filter((record) => record.type === 'arrow')
const arrowIds = new Set(arrowRecords.map((record) => record.id))
const bindingRecords = serialized.records.filter(
  (record) => record.typeName === 'binding' && record.type === 'arrow',
)
const pageCount = serialized.records.filter((record) => record.typeName === 'page').length
const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'))
assert.equal(shapeCount, metadata.shape_count, 'shape count differs from render metadata')
assert.equal(shapeCount, metadata.declared_shape_count, 'exported shape count differs from generator declaration')
assert.equal(uniqueShapeIds.size, metadata.declared_unique_shape_count, 'shape IDs are not unique')
assert.equal(uniqueShapeIds.size, shapeCount, 'duplicate shape IDs detected')
assert.equal(arrowRecords.length, metadata.arrow_count, 'arrow count differs from render metadata')
assert.equal(bindingRecords.length, metadata.binding_count, 'binding count differs from render metadata')
assert.equal(bindingRecords.length, arrowRecords.length * 2, 'each arrow must have two endpoint bindings')
assert.equal(pageCount, metadata.page_count, 'page count differs from render metadata')

const bindingsByArrow = new Map()
for (const binding of bindingRecords) {
  assert.ok(arrowIds.has(binding.fromId), `binding source is not an arrow: ${binding.fromId}`)
  assert.ok(uniqueShapeIds.has(binding.toId), `binding target does not exist: ${binding.toId}`)
  assert.ok(!arrowIds.has(binding.toId), `arrow endpoint is incorrectly bound to another arrow: ${binding.toId}`)
  assert.ok(
    binding.props.terminal === 'start' || binding.props.terminal === 'end',
    `unknown arrow terminal: ${binding.props.terminal}`,
  )
  const entries = bindingsByArrow.get(binding.fromId) || []
  entries.push(binding)
  bindingsByArrow.set(binding.fromId, entries)
}
for (const arrow of arrowRecords) {
  const bindings = bindingsByArrow.get(arrow.id) || []
  assert.equal(bindings.length, 2, `arrow ${arrow.id} must have exactly two bindings`)
  assert.deepEqual(
    new Set(bindings.map((binding) => binding.props.terminal)),
    new Set(['start', 'end']),
    `arrow ${arrow.id} must bind both start and end terminals`,
  )
}

const svg = fs.readFileSync(svgPath, 'utf8')
assert.match(svg, /<svg\b/, 'SVG export has no root element')

const png = fs.readFileSync(pngPath)
assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10], 'bad PNG signature')
const pngWidth = png.readUInt32BE(16)
const pngHeight = png.readUInt32BE(20)
assert.equal(pngWidth, metadata.png_width, 'PNG width differs from render metadata')
assert.equal(pngHeight, metadata.png_height, 'PNG height differs from render metadata')
assert.ok(shapeCount >= 100, 'architecture scene unexpectedly sparse')

schemaStore.dispose()
console.log(JSON.stringify({
  valid: true,
  pageCount,
  shapeCount,
  arrowCount: arrowRecords.length,
  bindingCount: bindingRecords.length,
  png: `${pngWidth}x${pngHeight}`,
  generator: metadata.generator,
}, null, 2))
process.exit(0)
