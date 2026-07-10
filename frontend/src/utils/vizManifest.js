const IMAGE_RE = /\.(png|svg|jpe?g|gif|webp)$/i
const HTML_RE = /\.html$/i

export function safeChartPath(value) {
  const raw = String(value || '').trim().replace(/^\.\//, '')
  // Manifest entries are filesystem-relative paths, not URL-encoded paths.
  // Reject '%' outright so browser URL normalization cannot turn %2e%2e into '..'.
  if (
    !raw || raw.startsWith('/') || raw.includes('\\') || raw.includes('%') ||
    /[\u0000-\u001F\u007F]/.test(raw)
  ) return ''
  const parts = raw.split('/').filter(part => part && part !== '.')
  if (parts[0]?.toLowerCase() !== 'charts' || parts.length < 2 || parts.includes('..')) return ''
  return parts.join('/')
}

export function chartAssetKind(path, declaredType = '') {
  if (!path) return ''
  if (IMAGE_RE.test(path)) return 'image'
  if (HTML_RE.test(path)) return 'html'
  // A real extension is authoritative. Only extensionless legacy rows may use type.
  if (/\.[^/]+$/.test(path)) return ''
  const type = String(declaredType || '').toLowerCase()
  if (['png', 'svg', 'jpg', 'jpeg', 'gif', 'webp', 'image'].includes(type)) return 'image'
  if (['html', 'plotly', 'interactive'].includes(type)) return 'html'
  return ''
}

function assetStem(path) {
  return String(path || '').replace(/\.[^/.]+$/, '')
}

function preferImage(current, candidate) {
  if (!current) return candidate
  if (/\.png$/i.test(candidate) && !/\.png$/i.test(current)) return candidate
  return current
}

/**
 * Fold legacy PNG+HTML twin rows and schema-v2 HTML/png_path rows into cards.
 * HTML-only Plotly artifacts remain first-class cards when static export fails.
 */
export function normalizeVizGallery(manifest) {
  const rows = Array.isArray(manifest) ? manifest.filter(x => x && typeof x === 'object') : []
  const groups = new Map()

  for (const item of rows) {
    const path = safeChartPath(item.path)
    const pngPath = safeChartPath(item.png_path)
    const primaryKind = chartAssetKind(path, item.type)
    const pngKind = chartAssetKind(pngPath, 'image')

    let imagePath = pngKind === 'image' ? pngPath : ''
    let interactivePath = ''
    if (primaryKind === 'image') imagePath = preferImage(imagePath, path)
    if (primaryKind === 'html') interactivePath = path
    if (!imagePath && !interactivePath) continue

    const stem = assetStem(interactivePath || imagePath)
    if (!stem) continue
    const existing = groups.get(stem) || {
      imagePath: '',
      interactivePath: '',
      caption: '',
      id: '',
    }
    existing.imagePath = preferImage(existing.imagePath, imagePath)
    if (!existing.interactivePath && interactivePath) existing.interactivePath = interactivePath
    if (!existing.caption && item.caption) existing.caption = String(item.caption)
    if (!existing.id) existing.id = String(item.id || item.name || stem)
    groups.set(stem, existing)
  }

  return [...groups.values()].filter(item => item.imagePath || item.interactivePath)
}

export function filterVizGalleryByMarkdown(gallery, markdown) {
  const embedded = new Set()
  const markerRe = /<!--\s*viz:([^\s>]+)\s*-->/g
  const source = String(markdown || '')
  let match
  while ((match = markerRe.exec(source)) !== null) embedded.add(match[1])
  return (Array.isArray(gallery) ? gallery : []).filter(item => (
    !embedded.has(item?.imagePath) && !embedded.has(item?.interactivePath)
  ))
}
