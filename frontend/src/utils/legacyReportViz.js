// VIZ-GALLERY（legacy Step4/Step5 视图）：纯函数工具，供组件与 node --test 共用。
//
// Step4Report / Step5Interaction 的本地简易 markdown 渲染器不支持图片语法：
// report_agent._render_viz_block 注入成稿的图表块（"<!-- viz:charts/… -->" 标记行 +
// "![图注](charts/….png)" 图片行）会以 '![…](…)' 字面文本漏出。图表画廊上线后，
// 这些行由本模块剥离——图表统一在完成态画廊中呈现（surgical 缓解，渲染器其余行为不变）。

// 整行 viz 注记：<!-- viz:charts/xxx.png -->（注入时独占一行）。
const VIZ_MARKER_LINE_RE = /^\s*<!--\s*viz:[^>]*-->\s*$/
// 整行图表图片：![任意图注](charts/… 或 ./charts/…)，可带 markdown 标题（"…"）后缀。
const CHART_IMAGE_LINE_RE = /^\s*!\[[^\]]*\]\(\s*(?:\.\/)?charts\/[^)]+\)\s*$/
// 行内图表图片（少数手写场景）：剥引用后用 alt 文本原位替换，保持句子可读。
const CHART_IMAGE_INLINE_RE = /!\[([^\]]*)\]\(\s*(?:\.\/)?charts\/[^)\s]+(?:\s+"[^"]*")?\)/g
// 快速预检：源文本没有任何图表标记/引用时原样返回，避免整篇 split。
const CHART_REF_PROBE_RE = /!\[[^\]]*\]\(\s*(?:\.\/)?charts\//

/** 该行是否为 viz 注入标记行。 */
export function isVizMarkerLine(line) {
  return VIZ_MARKER_LINE_RE.test(String(line ?? ''))
}

/** 该行是否为整行 charts/ 图表图片引用。 */
export function isChartImageLine(line) {
  return CHART_IMAGE_LINE_RE.test(String(line ?? ''))
}

/**
 * 从 markdown 中剥离图表注入痕迹：
 *   1. 整行删除 viz 标记行与 charts/ 图片行（注入块的标准形态）；
 *   2. 行内残留的 charts/ 图片引用退化为其 alt 文本（不留 '![' 字面量）。
 * 非 charts/ 路径的图片引用不动——本渲染器对它们的历史行为保持不变。
 * @param {string} content - 原始 markdown
 * @returns {string}
 */
export function stripLegacyVizMarkup(content) {
  const src = String(content ?? '')
  if (!src) return ''
  if (!src.includes('<!-- viz:') && !CHART_REF_PROBE_RE.test(src)) return src
  return src
    .split('\n')
    .filter(line => !isVizMarkerLine(line) && !isChartImageLine(line))
    .join('\n')
    .replace(CHART_IMAGE_INLINE_RE, '$1')
}

/**
 * viz_manifest 的 skipped[] → 单行低调脚注文本。
 * 空 / 非数组 / 无有效条目 → ''（调用方据此不渲染脚注）。
 * @param {Array<{builder?: string, reason?: string}>} skipped
 * @returns {string}
 */
export function vizSkippedFootnote(skipped) {
  const count = Array.isArray(skipped)
    ? skipped.filter(entry => entry && typeof entry === 'object').length
    : 0
  if (!count) return ''
  return `另有 ${count} 项图表未达渲染条件，相关数据已在报告正文中以表格或文字形式呈现`
}
