import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isChartImageLine,
  isVizMarkerLine,
  stripLegacyVizMarkup,
  vizSkippedFootnote,
} from '../src/utils/legacyReportViz.js'

// ---- stripLegacyVizMarkup：注入块（report_agent._render_viz_block 的真实形态）----

test('strips a png viz block: marker line + chart image line removed, caption kept', () => {
  const md = [
    '## 情景分析',
    '',
    '<!-- viz:charts/scenario_probabilities.png -->',
    '![情景概率分布](charts/scenario_probabilities.png)',
    '',
    '*情景概率分布*',
    '',
    '正文继续。',
  ].join('\n')
  const out = stripLegacyVizMarkup(md)
  assert.ok(!out.includes('<!-- viz:'))
  assert.ok(!out.includes('!['))
  assert.ok(out.includes('*情景概率分布*'))
  assert.ok(out.includes('正文继续。'))
})

test('strips schema-v2 html-twin block (marker points at .html, image at png_path)', () => {
  const md = [
    '<!-- viz:charts/timeline.html -->',
    '![Timeline](charts/timeline.png)',
    '',
    '*Timeline*',
  ].join('\n')
  const out = stripLegacyVizMarkup(md)
  assert.ok(!out.includes('<!-- viz:'))
  assert.ok(!out.includes('!['))
})

test('strips ./charts/ prefixed and titled image lines', () => {
  const md = '![cap](./charts/x.png "Chart title")\ntext'
  assert.equal(stripLegacyVizMarkup(md), 'text')
})

test('inline chart references degrade to their alt text (no literal "![" remains)', () => {
  const md = '如图 ![情景对比](charts/comparison.png) 所示，差异显著。'
  assert.equal(stripLegacyVizMarkup(md), '如图 情景对比 所示，差异显著。')
})

test('non-chart images and plain text pass through untouched', () => {
  const md = '![logo](assets/logo.png)\n普通段落，含 charts/ 字样但非图片。'
  assert.equal(stripLegacyVizMarkup(md), md)
})

test('handles empty / nullish input', () => {
  assert.equal(stripLegacyVizMarkup(''), '')
  assert.equal(stripLegacyVizMarkup(null), '')
  assert.equal(stripLegacyVizMarkup(undefined), '')
})

// ---- 行级判定 ----

test('line predicates match whole lines only', () => {
  assert.ok(isVizMarkerLine('  <!-- viz:charts/a.png -->  '))
  assert.ok(!isVizMarkerLine('text <!-- viz:charts/a.png -->'))
  assert.ok(isChartImageLine('![cap](charts/a.png)'))
  assert.ok(!isChartImageLine('前缀 ![cap](charts/a.png)'))
  assert.ok(!isChartImageLine('![cap](assets/a.png)'))
})

// ---- vizSkippedFootnote ----

test('skipped footnote: empty / invalid input yields empty string', () => {
  assert.equal(vizSkippedFootnote([]), '')
  assert.equal(vizSkippedFootnote(null), '')
  assert.equal(vizSkippedFootnote(undefined), '')
  assert.equal(vizSkippedFootnote('nope'), '')
  assert.equal(vizSkippedFootnote(['bad', 42]), '')
})

test('skipped footnote: counts only object entries and embeds the count', () => {
  const note = vizSkippedFootnote([
    { builder: 'timeline', reason: 'no_input' },
    { builder: 'actors', reason: 'below_density_threshold' },
    'noise',
    { builder: 'comparison', reason: 'plotly_unavailable_or_disabled' },
  ])
  assert.ok(note.includes('3'))
  assert.ok(note.length > 0)
})
