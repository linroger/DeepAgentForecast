/**
 * Dependency-free Markdown → HTML renderer, tuned for the MiroFish research /
 * forecast reports (which use headings, bold/italic, blockquotes, lists, tables,
 * `code`, fenced blocks, horizontal rules, links and `[citation:Title](url)`).
 *
 * It is intentionally small and safe: all raw text is HTML-escaped first, then a
 * fixed set of block/inline rules add markup. No user HTML is ever passed through.
 *
 * Usage:  import { renderMarkdown, extractHeadings } from '../utils/markdown'
 *         el.innerHTML = renderMarkdown(md)
 */

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// Stable slug for heading anchors (used by the section nav).
export function slugify(text) {
  return String(text)
    .toLowerCase()
    .trim()
    .replace(/[^\w一-龥\s-]/g, '')
    .replace(/\s+/g, '-')
    .slice(0, 80) || 'h'
}

// Inline formatting on an already-escaped string.
function renderInline(text) {
  let t = text
  // images are not supported as raw HTML; strip to alt text
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  // links: [label](url)  — also handles [citation:Label](url)
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, label, url) => {
    const clean = label.replace(/^citation:/i, '')
    const safeUrl = /^(https?:|mailto:|#|\/)/i.test(url) ? url : '#'
    const external = /^https?:/i.test(safeUrl)
    return `<a href="${safeUrl}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${clean}</a>`
  })
  // bold then italic then inline code
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/__([^_]+)__/g, '<strong>$1</strong>')
  t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  return t
}

function renderTable(rows) {
  // rows: array of raw markdown table lines (without the separator row)
  const parseRow = (line) => line.replace(/^\||\|$/g, '').split('|').map(c => c.trim())
  const header = parseRow(rows[0])
  const body = rows.slice(2) // skip header + separator
  let html = '<table class="md-table"><thead><tr>'
  header.forEach(h => { html += `<th>${renderInline(escapeHtml(h))}</th>` })
  html += '</tr></thead><tbody>'
  body.forEach(line => {
    if (!line.trim()) return
    const cells = parseRow(line)
    html += '<tr>'
    cells.forEach(c => { html += `<td>${renderInline(escapeHtml(c))}</td>` })
    html += '</tr>'
  })
  html += '</tbody></table>'
  return html
}

export function renderMarkdown(md) {
  if (!md) return ''
  const src = String(md).replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const lines = src.split('\n')
  const out = []
  let i = 0

  const flushParagraph = (buf) => {
    if (buf.length) {
      out.push('<p>' + renderInline(escapeHtml(buf.join(' '))) + '</p>')
      buf.length = 0
    }
  }
  const paraBuf = []

  while (i < lines.length) {
    let line = lines[i]

    // fenced code block
    const fence = line.match(/^```(\w*)\s*$/)
    if (fence) {
      flushParagraph(paraBuf)
      const code = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { code.push(lines[i]); i++ }
      i++ // closing fence
      out.push('<pre class="md-pre"><code>' + escapeHtml(code.join('\n')) + '</code></pre>')
      continue
    }

    // table (header line + separator of dashes/pipes)
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      flushParagraph(paraBuf)
      const tbl = []
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) { tbl.push(lines[i]); i++ }
      out.push(renderTable(tbl))
      continue
    }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      flushParagraph(paraBuf)
      const level = h[1].length
      const text = h[2].replace(/\s+#*\s*$/, '')
      const id = slugify(text)
      out.push(`<h${level} id="${id}" class="md-h md-h${level}">${renderInline(escapeHtml(text))}</h${level}>`)
      i++
      continue
    }

    // horizontal rule
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      flushParagraph(paraBuf)
      out.push('<hr class="md-hr"/>')
      i++
      continue
    }

    // blockquote (possibly multi-line)
    if (/^\s*>/.test(line)) {
      flushParagraph(paraBuf)
      const quote = []
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      out.push('<blockquote class="md-quote">' + renderInline(escapeHtml(quote.join(' '))) + '</blockquote>')
      continue
    }

    // lists (unordered / ordered), supports simple nesting by indentation
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      flushParagraph(paraBuf)
      const ordered = /^\s*\d+\.\s+/.test(line)
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag} class="md-list">`)
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        const item = lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, '')
        out.push('<li>' + renderInline(escapeHtml(item)) + '</li>')
        i++
      }
      out.push(`</${tag}>`)
      continue
    }

    // blank line → paragraph break
    if (!line.trim()) {
      flushParagraph(paraBuf)
      i++
      continue
    }

    // accumulate paragraph text
    paraBuf.push(line.trim())
    i++
  }
  flushParagraph(paraBuf)
  return out.join('\n')
}

/**
 * Extract the heading outline for a section nav.
 * @returns {Array<{level:number, text:string, id:string}>}
 */
export function extractHeadings(md) {
  if (!md) return []
  const out = []
  String(md).split('\n').forEach(line => {
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      const text = h[2].replace(/\s+#*\s*$/, '').replace(/[*_`]/g, '').trim()
      if (text) out.push({ level: h[1].length, text, id: slugify(text) })
    }
  })
  return out
}
