// GraphPanel visual-encoding helpers (pure, unit-tested):
// - deterministic entity-type colors (name-hash → palette slot, so colors do not
//   shift between graphs the way Object-insertion-order + modulo cycling did)
// - degree-scaled node radii (sqrt scale so hub area ~ degree)
//
// Palette: validated 8-slot categorical instance from the dataviz reference
// (`references/palette.md`). The previous 10-color array failed the palette
// validator (CVD adjacent ΔE 3.9, lightness band); this order passes every hard
// gate on the panel's light surfaces (#FAFAFA/#FFFFFF): worst adjacent CVD
// ΔE 9.1, normal-vision ΔE 19.6. Three slots sit below 3:1 contrast (relief
// rule) — GraphPanel satisfies relief via always-on node labels + the legend.
export const GRAPH_TYPE_PALETTE = [
  '#2a78d6', // blue
  '#eb6834', // orange
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#e87ba4', // magenta
  '#008300', // green
  '#4a3aa7', // violet
  '#e34948', // red
]

// FNV-1a 32-bit — stable, fast, dependency-free string hash.
export function hashTypeName(name) {
  let hash = 0x811c9dc5
  const text = String(name || '')
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

/**
 * Deterministic type→color assignment. Each type's primary slot is
 * hash(name) % palette.length, so a type keeps its color across graphs and
 * across refreshes regardless of node ordering. Collisions are resolved by
 * linear probing in sorted-name order (deterministic for a given type set);
 * once every slot is taken (more types than slots) the pure hash slot is
 * reused — the legend remains the disambiguator there.
 *
 * @param {string[]} typeNames
 * @param {string[]} [palette]
 * @returns {Map<string, string>} name → hex color
 */
export function assignTypeColors(typeNames, palette = GRAPH_TYPE_PALETTE) {
  const result = new Map()
  if (!Array.isArray(palette) || palette.length === 0) return result
  const names = [...new Set((typeNames || []).filter(n => n != null).map(String))]
  const taken = new Array(palette.length).fill(false)
  const sorted = [...names].sort((a, b) => a.localeCompare(b))
  for (const name of sorted) {
    const primary = hashTypeName(name) % palette.length
    let slot = primary
    let found = false
    for (let probe = 0; probe < palette.length; probe++) {
      const idx = (primary + probe) % palette.length
      if (!taken[idx]) {
        slot = idx
        taken[idx] = true
        found = true
        break
      }
    }
    if (!found) slot = primary // more types than slots: fall back to pure hash
    result.set(name, palette[slot])
  }
  return result
}

/**
 * Node radius scaled by sqrt(degree) so marker AREA tracks degree (hubs stand
 * out without dwarfing the layout). Degree 0 → min; the max-degree node → max.
 * Graphs with no edges keep the legacy uniform radius (fallback).
 */
export function nodeRadius(degree, maxDegree, { min = 6, max = 18, fallback = 10 } = {}) {
  const m = Number(maxDegree)
  if (!Number.isFinite(m) || m <= 0) return fallback
  const d = Number(degree)
  const ratio = Number.isFinite(d) && d > 0 ? Math.min(d / m, 1) : 0
  return min + (max - min) * Math.sqrt(ratio)
}
