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

    <!-- Empty state -->
    <div v-else-if="!md" class="state-panel">
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
          <div class="md-body" v-html="renderedHtml"></div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { getReport } from '../../api/report'
import { renderMarkdown, extractHeadings } from '../../utils/markdown'
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

let copyTimer = null

async function load() {
  if (!props.reportId) {
    md.value = ''
    meta.value = {}
    error.value = ''
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await getReport(props.reportId)
    const data = (res && res.data) || {}
    md.value = data.markdown_content || ''
    meta.value = data || {}
  } catch (e) {
    error.value = (e && (e.message || e.msg)) || L('报告加载失败','Failed to load report')
    md.value = ''
    meta.value = {}
  } finally {
    loading.value = false
  }
}

const renderedHtml = computed(() => {
  try {
    return renderMarkdown(md.value || '')
  } catch (e) {
    return ''
  }
})

const headings = computed(() => {
  try {
    const list = extractHeadings(md.value || '')
    return Array.isArray(list) ? list : []
  } catch (e) {
    return []
  }
})

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
  const text = md.value || ''
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
