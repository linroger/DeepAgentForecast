<template>
  <div class="forecast-report">
    <!-- Loading state -->
    <div v-if="loading" class="state-panel">
      <div class="state-spinner" aria-hidden="true"></div>
      <div class="state-text">{{ L('报告加载中…','Loading report…') }}</div>
    </div>

    <!-- Error state -->
    <div v-else-if="error" class="state-panel state-error">
      <div class="state-icon">⚠</div>
      <div class="state-text">{{ error }}</div>
      <button class="retry-btn" type="button" @click="load">{{ L('重试','Retry') }}</button>
    </div>

    <!-- Progressive state: 报告生成期，按章节增量展示已完成内容（PROGRESSIVE）。 -->
    <div v-else-if="isGenerating" class="progressive">
      <header class="report-head">
        <div class="report-head-left">
          <div class="report-eyebrow">
            <span class="diamond">◇</span>
            <span class="panel-label">{{ L('预测报告 · 生成中','Forecast report · generating') }}</span>
          </div>
          <h1 class="report-title">{{ reportTitle }}</h1>
        </div>
        <div class="report-head-right">
          <span class="status-pill pill-run">{{ L('生成中','Generating') }}</span>
        </div>
      </header>
      <div class="report-scroll">
        <div class="md-body">
          <template v-if="completedPartials.length">
            <section
              v-for="sec in completedPartials"
              :key="'partial-' + sec.index"
              class="partial-section"
            >
              <div class="md-body-inner" v-html="renderPartial(sec.content_md)"></div>
            </section>
          </template>
          <div v-else class="partial-empty">
            {{ L('报告正在生成，章节将在完成后陆续显示…','Report is being generated; sections will appear as they complete…') }}
          </div>
          <div class="writing-indicator">
            <span class="writing-dot" aria-hidden="true"></span>
            <span class="writing-text">{{ writingSectionLabel }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!currentMd" class="state-panel">
      <div class="state-icon">◇</div>
      <div class="state-text">{{ L('预测报告生成后在此显示…','Forecast report will appear here once generated…') }}</div>
    </div>

    <!-- Report -->
    <div v-else class="report-layout">
      <!-- Left: section nav -->
      <aside class="report-nav">
        <div class="panel-header">
          <span class="diamond">◇</span>
          <span class="panel-label">{{ L('目录','Contents') }}</span>
        </div>
        <nav v-if="headings.length" class="nav-list">
          <button
            v-for="(h, idx) in headings"
            :key="(h.id || 'h') + '-' + idx"
            class="nav-item"
            type="button"
            :style="{ paddingLeft: navIndent(h) }"
            @click="scrollToHeading(h)"
          >
            <span class="nav-item-text">{{ h.text || '—' }}</span>
          </button>
        </nav>
        <div v-else class="nav-empty">{{ L('暂无章节','No sections') }}</div>
      </aside>

      <!-- Right: report body -->
      <section class="report-main">
        <header class="report-head">
          <div class="report-head-left">
            <div class="report-eyebrow">
              <span class="diamond">◇</span>
              <span class="panel-label">{{ L('预测报告','Forecast report') }}</span>
            </div>
            <h1 class="report-title">{{ reportTitle }}</h1>
          </div>
          <div class="report-head-right">
            <span v-if="statusLabel" class="status-pill" :class="statusClass">
              {{ statusLabel }}
            </span>
            <!-- BILINGUAL：语种切换（仅在存在自动翻译版本时出现）。 -->
            <div v-if="langOptions.length > 1" class="lang-toggle" role="group" :aria-label="L('语言','Language')">
              <button
                v-for="opt in langOptions"
                :key="opt.key == null ? '__primary__' : opt.key"
                type="button"
                class="lang-btn"
                :class="{ active: activeLang === opt.key }"
                :disabled="langLoading"
                @click="switchLang(opt.key)"
              >{{ opt.label }}</button>
            </div>
            <!-- PDF-1：下载当前视图对应的 PDF（原文或所选语种）。 -->
            <a class="pdf-btn" :href="pdfHref" target="_blank" rel="noopener noreferrer">
              {{ L('下载 PDF','Download PDF') }}
            </a>
            <button class="copy-btn" type="button" @click="copyMarkdown">
              {{ copied ? L('已复制','Copied') : L('复制 Markdown','Copy Markdown') }}
            </button>
          </div>
        </header>

        <div v-if="isPartial" class="partial-warning">
          <span class="partial-icon" aria-hidden="true">⚠</span>
          <div class="partial-body">
            <div class="partial-text">{{ L('部分章节生成失败，以下章节为占位内容：','Some sections failed to generate and are shown as placeholders:') }}</div>
            <ul v-if="failedSections.length" class="partial-list">
              <li v-for="(title, idx) in failedSections" :key="(title || 'sec') + '-' + idx">{{ title }}</li>
            </ul>
          </div>
        </div>

        <div ref="scrollEl" class="report-scroll">
          <!-- FORECAST-DASH：结构化预测仪表盘（情景概率卡 + 置信徽章 + 市场分歧瓦片）。
               端点 404/409（旧报告 / 未启用结构化预测）→ 整体隐藏（degrade-safe）。 -->
          <section v-if="showDashboard" class="forecast-dash">
            <div class="dash-head">
              <span class="diamond">◇</span>
              <span class="panel-label">{{ L('预测仪表盘','Forecast Dashboard') }}</span>
              <span
                v-if="confidenceLevel"
                class="conf-badge"
                :class="'conf-' + confidenceLevel"
                :title="confidenceTitle"
              >{{ L('置信度','Confidence') }} · {{ confidenceLabel }}</span>
              <span v-if="ensembleInfo" class="ens-note">
                {{ L('集成一致度','Ensemble agreement') }} {{ ensembleInfo.agreement != null ? pct(ensembleInfo.agreement) : '—' }}<template v-if="ensembleInfo.nRuns"> · {{ ensembleInfo.nRuns }} {{ L('次运行','runs') }}</template><template v-if="ensembleInfo.spread != null"> · {{ L('离散度','spread') }} σ {{ pct(ensembleInfo.spread) }}</template>
              </span>
            </div>
            <p v-if="fcHeadline" class="dash-headline">{{ fcHeadline }}</p>
            <div class="dash-grid">
              <article v-for="(s, idx) in dashScenarios" :key="'sc-' + idx" class="dash-card">
                <div class="dash-prob">{{ pct(s.probability) }}</div>
                <div class="dash-bar" aria-hidden="true"><span :style="{ width: pct(s.probability) }"></span></div>
                <div class="dash-name" :title="s.name">{{ s.name }}</div>
                <div v-if="s.resolution_criteria" class="dash-criteria" :title="s.resolution_criteria">{{ s.resolution_criteria }}</div>
              </article>
            </div>
            <div v-if="marketTiles.length" class="dash-market">
              <div class="dash-sub">{{ L('市场分歧','Market Divergence') }}</div>
              <div class="market-grid">
                <div
                  v-for="(m, idx) in marketTiles"
                  :key="'mk-' + idx"
                  class="market-tile"
                  :class="{ 'tile-hot': m.exceeds_10pp }"
                >
                  <div class="mt-statement" :title="m.statement">{{ m.statement }}</div>
                  <div class="mt-nums">
                    <span class="mt-num">{{ L('模型','Model') }} {{ pct(m.model_probability) }}</span>
                    <span class="mt-num">{{ L('市场','Market') }} {{ pct(m.market_implied_yes_prob) }}</span>
                    <span class="mt-delta">Δ {{ divergencePP(m) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
          <!-- VIZ-1：图表画廊（确定性生成的 PNG/SVG 图表 + 图注）。无工件时整体不渲染。 -->
          <section v-if="galleryCharts.length" class="chart-gallery">
            <div class="gallery-head">
              <span class="diamond">◇</span>
              <span class="panel-label">{{ L('图表','Charts') }}</span>
            </div>
            <div class="gallery-grid">
              <figure v-for="(c, idx) in galleryCharts" :key="c.id || ('chart-' + idx)" class="chart-fig">
                <img v-if="c.src" class="chart-img" :src="c.src" :alt="c.caption || ('chart ' + (idx + 1))" loading="lazy" />
                <a
                  v-else-if="c.interactive"
                  class="chart-html-card"
                  :href="c.interactive"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <span class="chart-html-icon">◇</span>
                  <span>{{ L('打开交互式可视化','Open interactive visualization') }} ↗</span>
                </a>
                <figcaption v-if="c.caption" class="chart-cap">{{ c.caption }}</figcaption>
                <!-- VIZ-2：manifest 中存在同名 .html 孪生 → 新标签页打开交互版。 -->
                <a
                  v-if="c.interactive && c.src"
                  class="chart-interactive"
                  :href="c.interactive"
                  target="_blank"
                  rel="noopener noreferrer"
                >{{ L('交互版','Interactive') }} ↗</a>
              </figure>
            </div>
          </section>
          <div class="md-body" v-html="renderedHtml"></div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import {
  getReport, getVizManifest, getSectionsPartial,
  getReportTranslationMd, reportPdfUrl, reportAssetUrl, getForecast
} from '../../api/report'
import { renderMarkdown, extractHeadings } from '../../utils/markdown'
import {
  chartAssetKind,
  filterVizGalleryByMarkdown,
  normalizeVizGallery,
  safeChartPath,
} from '../../utils/vizManifest'
import { L } from '../../i18n'

const props = defineProps({
  reportId: { type: String, default: '' }
})

const md = ref('')
const meta = ref({})
const loading = ref(false)
const error = ref('')
const copied = ref(false)
const scrollEl = ref(null)

// VIZ-1：可视化清单（PNG 图表 + 图注）。空数组 → 不渲染图区（degrade-safe）。
const vizManifest = ref([])
// FORECAST-DASH：结构化预测对象（GET /api/report/<id>/forecast 的 data.forecast）。
// 404/409/网络错误（报告缺失或未通过发布门）→ null → 仪表盘隐藏。
const forecastPayload = ref(null)
// BILINGUAL：当前展示语种。null=原文（markdown_content）；否则为某翻译语种码（'en'|'zh'）。
const activeLang = ref(null)
const langMdCache = ref({})       // 翻译成稿缓存 { lang: markdown }
const langLoading = ref(false)    // 翻译版本拉取中
// PROGRESSIVE：生成期章节增量 [{index,title,status,content_md}]。
const partialSections = ref([])

let copyTimer = null
let pollTimer = null            // sections-partial 轮询句柄
let pollStopped = false         // 端点缺失(404)/完成后置真，避免无谓重试
let partialUnavailable = false  // sections-partial 端点缺失(404) → 仅靠 getReport 探测完成

// 生成中判定：报告已创建但尚未产出成稿（status ∈ pending/planning/generating 或成稿为空）。
function isGeneratingStatus(s) {
  const v = String(s || '').toLowerCase()
  return v === 'pending' || v === 'planning' || v === 'generating'
}
const isGenerating = computed(() => {
  const s = meta.value && meta.value.status
  return !md.value && (isGeneratingStatus(s) || (!s && partialSections.value.length > 0))
})

async function load() {
  stopPolling()
  if (!props.reportId) {
    md.value = ''
    meta.value = {}
    error.value = ''
    loading.value = false
    vizManifest.value = []
    forecastPayload.value = null
    activeLang.value = null
    langMdCache.value = {}
    partialSections.value = []
    return
  }
  loading.value = true
  error.value = ''
  forecastPayload.value = null
  try {
    const res = await getReport(props.reportId)
    const data = (res && res.data) || {}
    md.value = data.markdown_content || ''
    meta.value = data || {}
    activeLang.value = null
    // 成稿已就绪 → 拉可视化清单 + 结构化预测；否则进入生成期章节轮询（degrade-safe）。
    if (md.value) {
      loadVizManifest()
      loadForecast()
    } else {
      startPolling()
    }
  } catch (e) {
    error.value = (e && (e.message || e.msg)) || L('报告加载失败','Failed to load report')
    md.value = ''
    meta.value = {}
  } finally {
    loading.value = false
  }
}

// VIZ-1：拉取可视化清单。失败/为空 → 空数组（前端不渲染图区）。
async function loadVizManifest() {
  try {
    const res = await getVizManifest(props.reportId)
    const list = (res && res.data) || []
    vizManifest.value = Array.isArray(list) ? list : []
  } catch (e) {
    vizManifest.value = []
  }
}

// FORECAST-DASH：拉取结构化预测。旧报告会返回 forecast:null；其余失败（404/409/网络错误）
// → null → 仪表盘整体隐藏，报告正文不受影响（degrade-safe）。
async function loadForecast() {
  try {
    const res = await getForecast(props.reportId)
    const data = (res && res.data) || {}
    forecastPayload.value = (data.forecast && typeof data.forecast === 'object') ? data.forecast : null
  } catch (e) {
    forecastPayload.value = null
  }
}

// ---------- 当前展示成稿（原文 / 翻译）----------
const currentMd = computed(() => {
  if (activeLang.value && langMdCache.value[activeLang.value]) {
    return langMdCache.value[activeLang.value]
  }
  return md.value || ''
})

// resolveUrl：把报告内相对资源路径（charts/…）重写到 /charts 端点，使内嵌图片可显示。
const resolveAsset = (rel) => {
  if (!props.reportId) return ''
  return reportAssetUrl(props.reportId, rel)
}

// ---------- VIZ-2：manifest 驱动的交互图孪生 ----------
// 清单中所有 .html 工件路径集合（规范化去掉 './' 前缀）。
const htmlTwinSet = computed(() => {
  const s = new Set()
  ;(vizManifest.value || []).forEach(item => {
    const p = safeChartPath(item?.path)
    if (chartAssetKind(p, item?.type) === 'html') s.add(p)
  })
  return s
})

// 仅当 charts/<x>.png 在 viz_manifest 中存在同名 .html 孪生时返回其可访问 URL；
// 其余（含 markdown 里手写的任意路径）一律返回空串 → 不出现交互链接（No XSS surface）。
function interactiveHref(rel) {
  const p = safeChartPath(rel)
  if (chartAssetKind(p) !== 'image') return ''
  const twin = p.replace(/\.[^/.]+$/i, '.html')
  if (!htmlTwinSet.value.has(twin)) return ''
  return resolveAsset(twin)
}

// ---------- CITE-1：从成稿 References/参考来源 章节解析引文提示映射 ----------
// { 'S3': '来源标题…' }，供 renderMarkdown 给引文上标加 hover title。
// 成稿无 References 章节 → 空映射（上标仍渲染，只是无提示；Dossier 场景则完全不传）。
const REF_HEADING_RE = /references|sources|bibliography|参考|来源|文献/i
const citationsMap = computed(() => {
  const map = {}
  const src = String(currentMd.value || '')
  if (!src) return map
  let inRefs = false
  let inFence = false
  for (const line of src.split('\n')) {
    if (/^```/.test(line)) { inFence = !inFence; continue }
    if (inFence) continue
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) { inRefs = REF_HEADING_RE.test(h[2]); continue }
    if (!inRefs) continue
    const m = line.match(/^\s*(?:[-*+]|\d+\.)\s+\[S(\d+)(-[A-Za-z])?\]\s*[:：.、,，—–-]?\s*(.*)$/)
    if (!m) continue
    const key = 'S' + m[1] + (m[2] ? m[2].toLowerCase() : '')
    if (map[key]) continue
    // 去掉 markdown 链接/强调噪音，压成单行提示文本。
    const plain = m[3]
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/[*_`]/g, '')
      .trim()
    if (plain) map[key] = plain.length > 180 ? plain.slice(0, 177) + '…' : plain
  }
  return map
})

// renderMarkdown 统一渲染选项（资源解析 + 引文提示 + 交互图孪生）。
const renderOpts = computed(() => ({
  resolveUrl: resolveAsset,
  citations: citationsMap.value,
  interactiveHref
}))

const renderedHtml = computed(() => {
  try {
    return renderMarkdown(currentMd.value || '', renderOpts.value)
  } catch (e) {
    return ''
  }
})

const headings = computed(() => {
  try {
    const list = extractHeadings(currentMd.value || '')
    return Array.isArray(list) ? list : []
  } catch (e) {
    return []
  }
})

// ---------- BILINGUAL：可选语种与切换 ----------
// translations 每条形如 {lang, source_lang, path, chars, ...}。据此构造语种切换项：
// 首项为原文（primary，加载 markdown_content），其余为各翻译语种。
const translations = computed(() => {
  const list = meta.value && meta.value.translations
  return Array.isArray(list) ? list.filter(t => t && t.lang) : []
})

function langLabel(code) {
  const c = String(code || '').toLowerCase()
  if (c === 'en') return 'EN'
  if (c === 'zh') return '中文'
  return c ? c.toUpperCase() : L('原文', 'Original')
}

const langOptions = computed(() => {
  const t = translations.value
  if (!t.length) return []
  // 原文语种：取首条翻译的 source_lang（缺省则用中性 'primary'）。
  const primaryCode = (t[0] && t[0].source_lang) || ''
  const opts = [{ key: null, code: primaryCode, label: primaryCode ? langLabel(primaryCode) : L('原文', 'Original') }]
  t.forEach(entry => {
    opts.push({ key: entry.lang, code: entry.lang, label: langLabel(entry.lang) })
  })
  // 按语种码去重（原文语种若与某翻译重合极少见；保底避免重复项）。
  const seen = new Set()
  return opts.filter(o => {
    const k = o.key == null ? '__primary__' : o.key
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
})

async function switchLang(key) {
  if (key === activeLang.value) return
  if (key == null) { activeLang.value = null; return }
  if (langMdCache.value[key]) { activeLang.value = key; return }
  langLoading.value = true
  try {
    const res = await getReportTranslationMd(props.reportId, key)
    // 端点返回 text/markdown 原文；axios 响应拦截器对非 JSON 直接透传字符串。
    const text = typeof res === 'string' ? res : (res && res.data ? res.data : '')
    if (text) {
      langMdCache.value = { ...langMdCache.value, [key]: text }
      activeLang.value = key
    }
  } catch (e) {
    // 该语种版本不可用 → 保持当前展示（degrade-safe）。
  } finally {
    langLoading.value = false
  }
}

// ---------- PDF-1：当前视图对应的 PDF 直链 ----------
const pdfHref = computed(() => reportPdfUrl(props.reportId, activeLang.value || undefined))

// ---------- VIZ-1：图表画廊（静态预览优先；Plotly HTML-only 也可直接打开）----------
const galleryCharts = computed(() => {
  const fallbackOnly = filterVizGalleryByMarkdown(
    normalizeVizGallery(vizManifest.value),
    currentMd.value,
  )
  return fallbackOnly.map(item => ({
      id: item.id,
      src: item.imagePath ? resolveAsset(item.imagePath) : '',
      caption: item.caption,
      // schema-v2 的主 path 是 Plotly HTML、png_path 是静态孪生；legacy
      // PNG+HTML 双行同样被纯函数折叠为一个预览和一个交互链接。
      interactive: item.interactivePath ? resolveAsset(item.interactivePath) : ''
    }))
    .filter(c => c.src || c.interactive)
})

// ---------- FORECAST-DASH：结构化预测仪表盘 ----------
const fc = computed(() => forecastPayload.value || null)

// 0..1 概率 → 百分比字符串（保留 1 位小数，去掉尾零由 Math.round 保证）。
function pct(v) {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return (Math.round(n * 1000) / 10) + '%'
}

const dashScenarios = computed(() => {
  const list = fc.value && Array.isArray(fc.value.scenarios) ? fc.value.scenarios : []
  return list
    .filter(s => s && s.name && isFinite(Number(s.probability)))
    .slice()
    .sort((a, b) => Number(b.probability) - Number(a.probability))
    .slice(0, 6)
})

// 仪表盘可见性：至少有一个可展示情景才渲染（旧报告/无结构化预测 → 隐藏）。
const showDashboard = computed(() => dashScenarios.value.length > 0)

const fcHeadline = computed(() => {
  const h = fc.value && fc.value.headline
  return typeof h === 'string' ? h.trim() : ''
})

const confidenceLevel = computed(() => {
  const c = String((fc.value && fc.value.confidence) || '').toLowerCase().trim()
  if (!c) return ''
  return (c === 'low' || c === 'medium' || c === 'high') ? c : 'other'
})

const confidenceLabel = computed(() => {
  const c = confidenceLevel.value
  if (c === 'low') return L('低', 'Low')
  if (c === 'medium') return L('中', 'Medium')
  if (c === 'high') return L('高', 'High')
  return String((fc.value && fc.value.confidence) || '')
})

// hover 展示置信度理由（后端 confidence_rationale，可能为空）。
const confidenceTitle = computed(() => {
  const r = fc.value && fc.value.confidence_rationale
  return typeof r === 'string' ? r : ''
})

// 集成（多种子 ensemble）统计：一致度 / 运行次数 / 概率离散度（各情景 stdev 均值）。
const ensembleInfo = computed(() => {
  const e = fc.value && fc.value.ensemble
  if (!e || typeof e !== 'object') return null
  const n = Number(e.n_runs)
  const agreement = Number(e.agreement)
  const scen = Array.isArray(e.scenarios) ? e.scenarios : []
  const sds = scen.map(s => Number(s && s.stdev)).filter(v => isFinite(v))
  const spread = sds.length ? sds.reduce((a, b) => a + b, 0) / sds.length : null
  return {
    nRuns: isFinite(n) && n > 0 ? n : null,
    agreement: isFinite(agreement) ? agreement : null,
    spread
  }
})

// 市场分歧瓦片：market_comparison.comparisons（PM-2 负载）。缺失/为空 → 区块不渲染。
const marketTiles = computed(() => {
  const mc = fc.value && fc.value.market_comparison
  const comps = mc && Array.isArray(mc.comparisons) ? mc.comparisons : []
  return comps.filter(c => c && c.statement).slice(0, 6)
})

function divergencePP(m) {
  const d = Number(m && m.divergence)
  if (!isFinite(d)) return '—'
  const pp = Math.round(d * 1000) / 10
  return (pp > 0 ? '+' : '') + pp + 'pp'
}

// ---------- PROGRESSIVE：生成期章节轮询 ----------
function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

function startPolling() {
  stopPolling()
  pollStopped = false
  partialUnavailable = false
  pollOnce()
}

async function pollOnce() {
  if (pollStopped || !props.reportId) return

  // ① 章节增量（若端点可用）：驱动生成期的逐章展示。
  if (!partialUnavailable) {
    try {
      const res = await getSectionsPartial(props.reportId)
      // 契约 { sections:[...], done:bool }；容错兼容 { success, data:{...} } 包裹形态。
      const body = (res && Array.isArray(res.sections)) ? res
        : (res && res.data && Array.isArray(res.data.sections)) ? res.data
        : null
      if (body) {
        partialSections.value = Array.isArray(body.sections) ? body.sections : []
      }
    } catch (e) {
      // 端点尚未上线(404)：改为仅靠 getReport 探测完成（degrade-safe）；其它错误忽略后重试。
      if (e && e.response && e.response.status === 404) partialUnavailable = true
    }
  }

  // ② 完成探测（权威信号）：getReport 一旦产出成稿或进入终态，就重载渲染最终报告。
  try {
    const res = await getReport(props.reportId)
    const data = (res && res.data) || {}
    const finalMd = data.markdown_content || ''
    const s = String(data.status || '').toLowerCase()
    // 同步最新元信息（状态/失败章节等），即便尚未完成也让 UI 反映真实进度。
    meta.value = data || {}
    if (finalMd || s === 'completed' || s === 'failed') {
      pollStopped = true
      stopPolling()
      md.value = finalMd
      if (finalMd) { activeLang.value = null; loadVizManifest(); loadForecast() }
      return
    }
  } catch (e) {
    // getReport 暂时失败：忽略，下一轮重试。
  }

  if (!pollStopped) {
    pollTimer = setTimeout(pollOnce, 3000)
  }
}

// 生成期已完成章节（按 index 排序）与仍在写入的下一章序号。
const completedPartials = computed(() => {
  const secs = (partialSections.value || []).filter(s => s && String(s.status).toLowerCase() === 'completed')
  return secs.slice().sort((a, b) => (Number(a.index) || 0) - (Number(b.index) || 0))
})
const writingSectionLabel = computed(() => {
  const secs = partialSections.value || []
  const writing = secs.find(s => s && String(s.status).toLowerCase() !== 'completed')
  if (writing) {
    const idx = (Number(writing.index) || completedPartials.value.length) + 1
    const title = writing.title || ''
    return L(`正在撰写第 ${idx} 章${title ? '：' + title : ''}…`, `Writing section ${idx}${title ? ': ' + title : ''}…`)
  }
  const n = completedPartials.value.length + 1
  return L(`正在撰写第 ${n} 章…`, `Writing section ${n}…`)
})
function renderPartial(mdText) {
  try {
    // 生成期单章通常尚无 References 章节 → 引文自动降级为纯上标（markdown.js 预扫描决定）。
    return renderMarkdown(mdText || '', renderOpts.value)
  } catch (e) {
    return ''
  }
}

const reportTitle = computed(() => {
  const h1 = headings.value.find(h => h && Number(h.level) === 1)
  if (h1 && h1.text) return h1.text
  const first = headings.value[0]
  if (first && first.text) return first.text
  return L('预测报告','Forecast report')
})

const statusLabel = computed(() => {
  const s = meta.value && meta.value.status
  return s ? String(s) : ''
})

const statusClass = computed(() => {
  const s = String((meta.value && meta.value.status) || '').toLowerCase()
  if (s.includes('complete') || s.includes('done') || s.includes('success') || s.includes('finish')) return 'pill-ok'
  if (s.includes('fail') || s.includes('error')) return 'pill-err'
  if (s.includes('run') || s.includes('progress') || s.includes('pending') || s.includes('generat')) return 'pill-run'
  return ''
})

// 部分章节生成失败时的占位章节标题（老报告无此字段，视为空数组）
const failedSections = computed(() => {
  const list = meta.value && meta.value.failed_sections
  if (!Array.isArray(list)) return []
  return list.filter(t => typeof t === 'string' && t)
})

// partial 字段优先；缺失时回退到 failed_sections 是否非空
const isPartial = computed(() => {
  if (meta.value && typeof meta.value.partial === 'boolean') return meta.value.partial
  return failedSections.value.length > 0
})

function navIndent(h) {
  const level = h && Number(h.level) ? Number(h.level) : 1
  const px = Math.max(0, (level - 1)) * 12
  return (12 + px) + 'px'
}

function scrollToHeading(h) {
  if (!h || !h.id) return
  try {
    const el = document.getElementById(h.id)
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  } catch (e) {
    /* no-op: never throw on scroll */
  }
}

async function copyMarkdown() {
  const text = currentMd.value || ''
  if (!text) return
  try {
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copied.value = true
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 1500)
  } catch (e) {
    /* clipboard may be blocked; fail silently */
  }
}

onMounted(load)
watch(() => props.reportId, load)
onBeforeUnmount(() => {
  stopPolling()
  if (copyTimer) clearTimeout(copyTimer)
})
</script>

<style scoped>
.forecast-report {
  --orange: #FF4500;
  --border: #E5E5E5;
  --mono: 'JetBrains Mono', monospace;
  --display: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  --ink: #000;
  --paper: #fff;
  --muted: #666;
  --faint: #999;
  --ok: #16a34a;
  --err: #b91c1c;
  --soft: #FAFAFA;

  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--display);
}

/* ---------- States ---------- */
.state-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 48px 24px;
  text-align: center;
  border: 1px solid var(--border);
  margin: 16px;
  background: var(--soft);
}
.state-icon {
  font-size: 28px;
  color: var(--orange);
  line-height: 1;
}
.state-text {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--muted);
  letter-spacing: 0.02em;
}
.state-error .state-icon { color: var(--err); }
.state-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border);
  border-top-color: var(--orange);
  border-radius: 50%;
  animation: fr-spin 0.8s linear infinite;
}
@keyframes fr-spin { to { transform: rotate(360deg); } }
.retry-btn {
  font-family: var(--mono);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 7px 16px;
  border: 1px solid var(--ink);
  background: var(--ink);
  color: var(--paper);
  cursor: pointer;
  transition: transform 0.12s ease;
}
.retry-btn:hover { transform: translateY(-1px); }

/* ---------- Layout ---------- */
.report-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 248px 1fr;
  gap: 0;
}

/* ---------- Nav ---------- */
.report-nav {
  border-right: 1px solid var(--border);
  background: var(--soft);
  padding: 18px 0 24px;
  overflow-y: auto;
  position: sticky;
  top: 0;
  align-self: start;
  max-height: 100%;
}
.panel-header {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 18px 12px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}
.diamond { color: var(--orange); font-size: 12px; line-height: 1; }
.panel-label {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink);
}
.nav-list { display: flex; flex-direction: column; }
.nav-item {
  display: block;
  width: 100%;
  text-align: left;
  background: transparent;
  border: none;
  border-left: 2px solid transparent;
  padding: 7px 16px 7px 12px;
  font-family: var(--display);
  font-size: 13px;
  line-height: 1.4;
  color: var(--muted);
  cursor: pointer;
  transition: color 0.12s ease, border-color 0.12s ease, background 0.12s ease, transform 0.12s ease;
}
.nav-item:hover {
  color: var(--ink);
  border-left-color: var(--orange);
  background: var(--paper);
  transform: translateY(-1px);
}
.nav-item-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-empty {
  padding: 12px 18px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--faint);
}

/* ---------- Main ---------- */
.report-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.report-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 28px 16px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.report-head-left { min-width: 0; }
.report-eyebrow {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 6px;
}
.report-title {
  font-family: var(--display);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.25;
  margin: 0;
  color: var(--ink);
  word-break: break-word;
}
.report-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.status-pill {
  font-family: var(--mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 4px 10px;
  border: 1px solid var(--border);
  background: var(--paper);
  color: var(--muted);
  white-space: nowrap;
}
.pill-ok { color: var(--ok); border-color: var(--ok); }
.pill-err { color: var(--err); border-color: var(--err); }
.pill-run { color: var(--orange); border-color: var(--orange); }
.copy-btn {
  font-family: var(--mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 6px 14px;
  border: 1px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  cursor: pointer;
  white-space: nowrap;
  transition: transform 0.12s ease, background 0.12s ease, color 0.12s ease;
}
.copy-btn:hover {
  transform: translateY(-1px);
  background: var(--ink);
  color: var(--paper);
}

/* ---------- Partial-report warning ---------- */
.partial-warning {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 16px 28px 0;
  padding: 12px 16px;
  border: 1px solid var(--err);
  border-left-width: 3px;
  background: var(--soft);
}
.partial-icon {
  font-size: 16px;
  line-height: 1.4;
  color: var(--err);
  flex-shrink: 0;
}
.partial-body { min-width: 0; }
.partial-text {
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.6;
  letter-spacing: 0.02em;
  color: var(--err);
}
.partial-list {
  margin: 6px 0 0;
  padding-left: 1.3em;
  font-family: var(--display);
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}
.partial-list li { margin: 2px 0; word-break: break-word; }

.report-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 28px 28px 64px;
}
.md-body {
  max-width: 760px;
  margin: 0 auto;
  color: var(--ink);
}

/* ---------- Markdown typography ---------- */
.md-body :deep(.md-h),
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4),
.md-body :deep(h5),
.md-body :deep(h6) {
  font-family: var(--display);
  font-weight: 700;
  line-height: 1.3;
  color: var(--ink);
  scroll-margin-top: 16px;
}
.md-body :deep(h1) { font-size: 1.7rem; margin: 0 0 0.6em; }
.md-body :deep(h2) {
  font-size: 1.35rem;
  margin: 1.8em 0 0.5em;
  padding-bottom: 0.3em;
  border-bottom: 1px solid var(--border);
}
.md-body :deep(h3) { font-size: 1.12rem; margin: 1.4em 0 0.4em; }
.md-body :deep(h4) { font-size: 1rem; margin: 1.2em 0 0.3em; color: var(--muted); }
.md-body :deep(h5),
.md-body :deep(h6) {
  font-size: 0.9rem;
  margin: 1em 0 0.3em;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.md-body :deep(p) {
  font-size: 1rem;
  line-height: 1.9;
  margin: 0 0 1.1em;
  color: #1a1a1a;
}
.md-body :deep(ul),
.md-body :deep(ol),
.md-body :deep(.md-list) {
  margin: 0 0 1.1em;
  padding-left: 1.5em;
  line-height: 1.85;
}
.md-body :deep(li) { margin: 0.25em 0; }
.md-body :deep(blockquote),
.md-body :deep(.md-quote) {
  margin: 1.2em 0;
  padding: 0.8em 1.1em;
  border-left: 3px solid var(--orange);
  background: var(--soft);
  font-style: italic;
  color: var(--muted);
  line-height: 1.8;
}
.md-body :deep(a) {
  color: var(--orange);
  text-decoration: none;
  border-bottom: 1px solid rgba(255, 69, 0, 0.3);
  transition: border-color 0.12s ease;
}
.md-body :deep(a:hover) { border-bottom-color: var(--orange); }
.md-body :deep(strong) { font-weight: 700; color: var(--ink); }
.md-body :deep(em) { font-style: italic; }
.md-body :deep(code) {
  font-family: var(--mono);
  font-size: 0.85em;
  background: var(--soft);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.1em 0.4em;
}
.md-body :deep(pre) {
  font-family: var(--mono);
  font-size: 0.82rem;
  line-height: 1.7;
  background: #0c0c0c;
  color: #d6d6d6;
  padding: 14px 16px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 1.2em 0;
}
.md-body :deep(pre code) {
  background: transparent;
  border: none;
  padding: 0;
  color: inherit;
  font-size: inherit;
}
.md-body :deep(hr),
.md-body :deep(.md-hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 2em 0;
}
.md-body :deep(table),
.md-body :deep(.md-table) {
  width: 100%;
  border-collapse: collapse;
  margin: 1.4em 0;
  font-size: 0.92rem;
}
.md-body :deep(.md-table th),
.md-body :deep(.md-table td),
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
  line-height: 1.6;
}
.md-body :deep(.md-table th),
.md-body :deep(th) {
  font-family: var(--mono);
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: var(--soft);
  color: var(--ink);
  font-weight: 600;
}
.md-body :deep(.md-table tbody tr:hover) { background: var(--soft); }
/* 宽表（如 Market Cross-Check）横向滚动容器：超出正文宽度时容器内滚动，绝不撑破整页。 */
.md-body :deep(.md-table-wrap) {
  overflow-x: auto;
  margin: 1.4em 0;
  -webkit-overflow-scrolling: touch;
}
.md-body :deep(.md-table-wrap) .md-table,
.md-body :deep(.md-table-wrap) table { margin: 0; }
/* 内嵌图片（报告 markdown 中的 charts/… 相对路径经 resolveUrl 重写后渲染）。 */
.md-body :deep(.md-img) {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.2em auto;
  border: 1px solid var(--border);
  background: var(--paper);
}
/* VIZ-2：内嵌图片的交互孪生链接（仅 manifest 中存在同名 .html 时出现）。 */
.md-body :deep(.md-img-wrap) {
  display: block;
  margin: 1.2em 0;
  text-align: center;
}
.md-body :deep(.md-img-wrap .md-img) { margin: 0 auto; }
.md-body :deep(.md-img-interactive) {
  display: inline-block;
  margin-top: 7px;
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  background: var(--soft);
  text-decoration: none;
  transition: color 0.12s ease, border-color 0.12s ease;
}
.md-body :deep(.md-img-interactive:hover) {
  color: var(--orange);
  border-color: var(--orange);
}

/* CITE-1：引文上标（[Sxx] → sup），保持阅读流不被打断。 */
.md-body :deep(.md-cite) {
  font-family: var(--mono);
  font-size: 0.68em;
  line-height: 0;
  vertical-align: super;
  margin: 0 1px;
  color: var(--muted);
}
.md-body :deep(.md-cite a) {
  color: var(--orange);
  border-bottom: none;
  padding: 0 2px;
  border-radius: 2px;
  transition: background 0.12s ease;
}
.md-body :deep(.md-cite a:hover) { background: rgba(255, 69, 0, 0.12); }
/* References 条目：锚点定位余量 + 标记标签样式。 */
.md-body :deep(.md-ref) { scroll-margin-top: 16px; }
.md-body :deep(.md-ref-tag) {
  font-family: var(--mono);
  font-size: 0.76em;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0 5px;
  margin-right: 5px;
  white-space: nowrap;
  background: var(--soft);
}
/* 被 #ref-Sxx 锚点命中的条目短暂高亮，帮助读者定位。 */
.md-body :deep(.md-ref:target) { background: rgba(255, 69, 0, 0.08); }

/* ---------- Header: PDF button + language toggle ---------- */
.pdf-btn {
  font-family: var(--mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 6px 14px;
  border: 1px solid var(--orange);
  background: var(--orange);
  color: var(--paper);
  cursor: pointer;
  white-space: nowrap;
  text-decoration: none;
  transition: transform 0.12s ease, opacity 0.12s ease;
}
.pdf-btn:hover { transform: translateY(-1px); opacity: 0.9; }
.lang-toggle {
  display: inline-flex;
  border: 1px solid var(--border);
  overflow: hidden;
}
.lang-btn {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.04em;
  padding: 6px 11px;
  border: none;
  border-left: 1px solid var(--border);
  background: var(--paper);
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s ease, color 0.12s ease;
}
.lang-btn:first-child { border-left: none; }
.lang-btn:hover:not(:disabled) { color: var(--ink); background: var(--soft); }
.lang-btn.active { background: var(--ink); color: var(--paper); }
.lang-btn:disabled { opacity: 0.55; cursor: default; }

/* ---------- Forecast dashboard (FORECAST-DASH) ---------- */
.forecast-dash {
  max-width: 760px;
  margin: 0 auto 28px;
  border: 1px solid var(--border);
  background: var(--soft);
  padding: 16px 18px 18px;
}
.dash-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-bottom: 10px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.conf-badge {
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 3px 9px;
  border: 1px solid var(--border);
  background: var(--paper);
  color: var(--muted);
  white-space: nowrap;
  cursor: default;
}
.conf-badge.conf-high { color: var(--ok); border-color: var(--ok); }
.conf-badge.conf-medium { color: var(--orange); border-color: var(--orange); }
.conf-badge.conf-low { color: var(--err); border-color: var(--err); }
.ens-note {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.03em;
  color: var(--faint);
  white-space: nowrap;
}
.dash-headline {
  font-family: var(--display);
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--ink);
  margin: 0 0 12px;
}
.dash-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}
.dash-card {
  border: 1px solid var(--border);
  background: var(--paper);
  padding: 12px 14px;
  min-width: 0;
}
.dash-prob {
  font-family: var(--mono);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--ink);
}
.dash-bar {
  height: 4px;
  background: var(--border);
  margin: 8px 0 10px;
  overflow: hidden;
}
.dash-bar span {
  display: block;
  height: 100%;
  background: var(--orange);
}
.dash-name {
  font-family: var(--display);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.dash-criteria {
  margin-top: 6px;
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--faint);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.dash-market { margin-top: 14px; }
.dash-sub {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--faint);
  margin-bottom: 8px;
}
.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}
.market-tile {
  border: 1px solid var(--border);
  background: var(--paper);
  padding: 10px 12px;
  min-width: 0;
}
.market-tile.tile-hot { border-left: 3px solid var(--orange); }
.mt-statement {
  font-family: var(--display);
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.mt-nums {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 7px;
}
.mt-num {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}
.mt-delta {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  color: var(--orange);
  white-space: nowrap;
}

/* ---------- Chart gallery (VIZ-1) ---------- */
.chart-gallery {
  max-width: 760px;
  margin: 0 auto 28px;
}
.gallery-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding-bottom: 10px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 18px;
}
.chart-fig {
  margin: 0;
  border: 1px solid var(--border);
  background: var(--soft);
  padding: 12px;
}
.chart-img {
  display: block;
  width: 100%;
  height: auto;
  background: var(--paper);
}
.chart-html-card {
  min-height: 180px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  border: 1px dashed var(--border);
  color: var(--ink);
  background: linear-gradient(145deg, var(--paper), var(--soft));
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.03em;
  text-align: center;
  text-decoration: none;
  transition: color 0.12s ease, border-color 0.12s ease, transform 0.12s ease;
}
.chart-html-card:hover {
  color: var(--orange);
  border-color: var(--orange);
  transform: translateY(-1px);
}
.chart-html-icon {
  font-size: 30px;
  line-height: 1;
  color: var(--orange);
}
.chart-cap {
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.6;
  color: var(--muted);
  margin-top: 8px;
  letter-spacing: 0.01em;
}
/* VIZ-2：画廊图的交互孪生链接。 */
.chart-interactive {
  display: inline-block;
  margin-top: 8px;
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 3px 10px;
  border: 1px solid var(--border);
  color: var(--muted);
  background: var(--paper);
  text-decoration: none;
  transition: color 0.12s ease, border-color 0.12s ease;
}
.chart-interactive:hover {
  color: var(--orange);
  border-color: var(--orange);
}

/* ---------- Progressive generation view ---------- */
.progressive {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.md-body-inner { color: var(--ink); }
.partial-section { margin-bottom: 0.4em; }
.partial-empty {
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.8;
  color: var(--muted);
  padding: 8px 0 4px;
}
.writing-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 20px;
  padding: 12px 0 4px;
  border-top: 1px dashed var(--border);
}
.writing-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--orange);
  animation: fr-pulse 1.1s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes fr-pulse { 0%,100% { opacity: 0.35; transform: scale(0.85); } 50% { opacity: 1; transform: scale(1); } }
.writing-text {
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.03em;
  color: var(--orange);
}
/* 生成期正文继承 md-body 排版（复用 :deep 规则）。 */
.progressive .md-body-inner :deep(h1),
.progressive .md-body-inner :deep(h2),
.progressive .md-body-inner :deep(h3),
.progressive .md-body-inner :deep(p),
.progressive .md-body-inner :deep(li) { color: var(--ink); }

/* ---------- Responsive ---------- */
@media (max-width: 980px) {
  .report-layout {
    grid-template-columns: 1fr;
  }
  .report-nav {
    position: static;
    border-right: none;
    border-bottom: 1px solid var(--border);
    max-height: 220px;
    padding-bottom: 12px;
  }
  .report-scroll { padding: 20px 18px 48px; }
  .report-head { padding: 16px 18px 14px; }
  .partial-warning { margin: 14px 18px 0; }
  .report-title { font-size: 19px; }
}
</style>
