import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createTLStore, parseTldrawJsonFile } from 'tldraw'

const here = path.dirname(fileURLToPath(import.meta.url))
const outputDir = path.resolve(here, '../..')
const base = 'deepresearchforecast-system-architecture'
const scenePath = path.join(outputDir, `${base}.tldr`)
const svgPath = path.join(outputDir, `${base}.svg`)
const pngPath = path.join(outputDir, `${base}.png`)
const metadataPath = path.join(outputDir, 'deepresearchforecast-system-render-metadata.json')

const sceneJson = fs.readFileSync(scenePath, 'utf8')
const schemaStore = createTLStore()
const parsed = parseTldrawJsonFile({ json: sceneJson, schema: schemaStore.schema })
assert.equal(parsed.ok, true, `invalid .tldr scene: ${JSON.stringify(parsed.error)}`)

const serialized = JSON.parse(sceneJson)
const shapeRecords = serialized.records.filter((record) => record.typeName === 'shape')
const shapeIds = shapeRecords.map((record) => record.id)
const uniqueShapeIds = new Set(shapeIds)
const arrows = shapeRecords.filter((record) => record.type === 'arrow')
const arrowIds = new Set(arrows.map((record) => record.id))
const bindings = serialized.records.filter(
  (record) => record.typeName === 'binding' && record.type === 'arrow',
)
const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'))

assert.equal(uniqueShapeIds.size, shapeRecords.length, 'duplicate shape IDs detected')
assert.equal(shapeRecords.length, metadata.shape_count, 'shape count differs from metadata')
assert.equal(shapeRecords.length, metadata.declared_shape_count, 'declared shape count differs')
assert.equal(arrows.length, metadata.arrow_count, 'arrow count differs from metadata')
assert.equal(bindings.length, metadata.binding_count, 'binding count differs from metadata')
assert.equal(bindings.length, arrows.length * 2, 'each arrow must bind two endpoints')

const byArrow = new Map()
for (const binding of bindings) {
  assert.ok(arrowIds.has(binding.fromId), `binding source is not an arrow: ${binding.fromId}`)
  assert.ok(uniqueShapeIds.has(binding.toId), `binding target does not exist: ${binding.toId}`)
  const entries = byArrow.get(binding.fromId) || []
  entries.push(binding.props.terminal)
  byArrow.set(binding.fromId, entries)
}
for (const arrow of arrows) {
  assert.deepEqual(
    new Set(byArrow.get(arrow.id) || []),
    new Set(['start', 'end']),
    `arrow ${arrow.id} must bind start and end`,
  )
}

assert.match(fs.readFileSync(svgPath, 'utf8'), /<svg\b/, 'SVG has no root element')
const png = fs.readFileSync(pngPath)
assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10], 'bad PNG')
const width = png.readUInt32BE(16)
const height = png.readUInt32BE(20)
assert.equal(width, metadata.png_width, 'PNG width differs from metadata')
assert.equal(height, metadata.png_height, 'PNG height differs from metadata')
assert.ok(shapeRecords.length >= 100, 'whole-system scene is unexpectedly sparse')
assert.ok(arrows.length >= 45, 'whole-system scene has too few material flows')

schemaStore.dispose()
console.log(JSON.stringify({
  valid: true,
  shapes: shapeRecords.length,
  arrows: arrows.length,
  bindings: bindings.length,
  png: `${width}x${height}`,
  generator: metadata.generator,
}, null, 2))
process.exit(0)
