<template>
  <div class="dossier-viewer">
    <!-- Segmented tab toggle -->
    <div class="tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        :aria-selected="activeTab === tab.key"
        @click="activeTab = tab.key"
      >
        <span class="tab-label">{{ tab.label }}</span>
        <span v-if="tab.count !== null" class="tab-count">{{ tab.count }}</span>
      </button>
    </div>

    <!-- 研究报告 -->
    <section v-show="activeTab === 'report'" class="panel" role="tabpanel">
      <div class="panel-head"><span class="diamond">◇</span>{{ L('研究报告', 'Research Report') }}</div>
      <div class="panel-body">
        <div v-if="hasReport" class="md-body" v-html="renderedReport"></div>
        <div v-else class="empty">
          <div class="empty-icon">◇</div>
          <div class="empty-title">{{ L('暂无研究报告', 'No research report yet') }}</div>
          <div class="empty-sub">{{ L('research_report.md 尚未生成或为空。', 'research_report.md has not been generated or is empty.') }}</div>
        </div>
      </div>
    </section>

    <!-- 关键参与者 -->
    <section v-show="activeTab === 'actors'" class="panel" role="tabpanel">
      <div class="panel-head"><span class="diamond">◇</span>{{ L('关键参与者', 'Key Actors') }}</div>
      <div class="panel-body">
        <template v-if="actorList.length">
          <!-- central question + as-of date -->
          <div v-if="centralQuestion || asOfDate" class="meta-row">
            <div v-if="centralQuestion" class="meta-question">
              <span class="meta-label">{{ L('核心问题', 'Central Question') }}</span>
              <span class="meta-text">{{ centralQuestion }}</span>
            </div>
            <div v-if="asOfDate" class="meta-date">{{ L('截至', 'As of') }} {{ asOfDate }}</div>
          </div>

          <div class="actor-grid">
            <article v-for="(actor, idx) in actorList" :key="actorKey(actor, idx)" class="actor-card">
              <header class="actor-head">
                <span class="actor-name">{{ actorName(actor) }}</span>
                <span class="influence-pill" :class="influenceClass(actor.influence)">
                  {{ influenceLabel(actor.influence) }}
                </span>
              </header>
              <div v-if="actor.type" class="type-badge">{{ actor.type }}</div>
              <div v-if="actor.role" class="actor-role">{{ actor.role }}</div>
              <div v-if="actor.stance" class="actor-stance" :title="actor.stance">{{ actor.stance }}</div>
              <div v-if="actor.memory" class="actor-memory" :title="actor.memory">{{ actor.memory }}</div>
            </article>
          </div>

          <!-- key events timeline -->
          <div v-if="keyEvents.length" class="sub-block">
            <div class="sub-label">{{ L('关键事件', 'Key Events') }}</div>
            <ul class="timeline">
              <li v-for="(ev, i) in keyEvents" :key="'ev' + i" class="timeline-item">
                <span class="timeline-date">{{ ev.date || '—' }}</span>
                <span class="timeline-event">{{ ev.event }}</span>
              </li>
            </ul>
          </div>

          <!-- hot topics chips -->
          <div v-if="hotTopics.length" class="sub-block">
            <div class="sub-label">{{ L('热点话题', 'Hot Topics') }}</div>
            <div class="chips">
              <span v-for="(topic, i) in hotTopics" :key="'topic' + i" class="chip">{{ topic }}</span>
            </div>
          </div>
        </template>

        <div v-else class="empty">
          <div class="empty-icon">◇</div>
          <div class="empty-title">{{ L('结构化参与者未生成（research_report.md 仍可用）', 'Structured actors not generated (research_report.md still available)') }}</div>
          <div class="empty-sub">{{ L('未在档案中找到结构化的参与者数据。', 'No structured actor data found in the dossier.') }}</div>
        </div>
      </div>
    </section>

    <!-- 信息来源 -->
    <section v-show="activeTab === 'sources'" class="panel" role="tabpanel">
      <div class="panel-head"><span class="diamond">◇</span>{{ L('信息来源', 'Sources') }}</div>
      <div class="panel-body">
        <template v-if="sourceList.length">
          <div class="sources-count">{{ L('共', 'Total') }} {{ sourceList.length }} {{ L('条来源', 'sources') }}</div>
          <ul class="source-list">
            <li v-for="(src, i) in sourceList" :key="'src' + i" class="source-item">
              <span class="source-idx">{{ i + 1 }}</span>
              <a
                :href="sourceHref(src)"
                target="_blank"
                rel="noopener"
                class="source-link"
              >{{ sourceTitle(src) }}</a>
            </li>
          </ul>
        </template>
        <div v-else class="empty">
          <div class="empty-icon">◇</div>
          <div class="empty-title">{{ L('暂无信息来源', 'No sources yet') }}</div>
          <div class="empty-sub">{{ L('该档案未附带可引用的来源链接。', 'This dossier has no citable source links attached.') }}</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import { L } from '../../i18n'

const props = defineProps({
  dossier: {
    type: Object,
    default: null
  }
})

const activeTab = ref('report')

// ---- Safe accessors ------------------------------------------------------

// dossier.actors is the structured-actors container { actors:[...], key_events:[...], ... }
const actorsContainer = computed(() => {
  const a = props.dossier && props.dossier.actors
  return a && typeof a === 'object' ? a : null
})

const actorList = computed(() => {
  const c = actorsContainer.value
  return c && Array.isArray(c.actors) ? c.actors.filter(Boolean) : []
})

const keyEvents = computed(() => {
  const c = actorsContainer.value
  if (!c || !Array.isArray(c.key_events)) return []
  return c.key_events.filter(ev => ev && (ev.event || ev.date))
})

const hotTopics = computed(() => {
  const c = actorsContainer.value
  if (!c || !Array.isArray(c.hot_topics)) return []
  return c.hot_topics.filter(t => t != null && String(t).trim() !== '')
})

const centralQuestion = computed(() => {
  const c = actorsContainer.value
  return c && c.central_question ? String(c.central_question) : ''
})

const asOfDate = computed(() => {
  const c = actorsContainer.value
  return c && c.as_of_date ? String(c.as_of_date) : ''
})

// sources may be an array, or { sources:[...] }, or null
const sourceList = computed(() => {
  const s = props.dossier && props.dossier.sources
  if (!s) return []
  if (Array.isArray(s)) return s.filter(Boolean)
  if (typeof s === 'object' && Array.isArray(s.sources)) return s.sources.filter(Boolean)
  return []
})

const hasReport = computed(() => {
  const d = props.dossier
  return !!(d && d.has_report && d.report && String(d.report).trim() !== '')
})

const renderedReport = computed(() => {
  if (!hasReport.value) return ''
  try {
    return renderMarkdown(props.dossier.report)
  } catch (e) {
    return ''
  }
})

// ---- Tabs ----------------------------------------------------------------

const tabs = computed(() => [
  { key: 'report', label: L('研究报告', 'Research Report'), count: null },
  { key: 'actors', label: L('关键参与者', 'Key Actors'), count: actorList.value.length },
  { key: 'sources', label: L('信息来源', 'Sources'), count: sourceList.value.length }
])

// ---- Field helpers (defensive) ------------------------------------------

function actorName(actor) {
  if (!actor) return L('未知', 'Unknown')
  return actor.name || actor.user_name || actor.username || L('未知', 'Unknown')
}

function actorKey(actor, idx) {
  return (actor && (actor.name || actor.user_name)) ? `${actor.name || actor.user_name}-${idx}` : `actor-${idx}`
}

function influenceClass(influence) {
  const v = String(influence || '').toLowerCase()
  if (v === 'high' || v === '高') return 'inf-high'
  if (v === 'medium' || v === 'mid' || v === '中') return 'inf-medium'
  if (v === 'low' || v === '低') return 'inf-low'
  return 'inf-unknown'
}

function influenceLabel(influence) {
  const v = String(influence || '').toLowerCase()
  if (v === 'high' || v === '高') return L('高影响', 'High')
  if (v === 'medium' || v === 'mid' || v === '中') return L('中影响', 'Medium')
  if (v === 'low' || v === '低') return L('低影响', 'Low')
  return influence ? String(influence) : L('未知', 'Unknown')
}

function sourceTitle(src) {
  if (!src) return ''
  if (typeof src === 'string') return src
  return src.title || src.url || L('未命名来源', 'Untitled source')
}

function sourceHref(src) {
  if (!src) return '#'
  if (typeof src === 'string') return src
  const url = src.url || src.title
  return url ? String(url) : '#'
}
</script>

<style scoped>
.dossier-viewer {
  --orange: #FF4500;
  --border: #E5E5E5;
  --mono: 'JetBrains Mono', monospace;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  color: #000;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---- Segmented tabs ---- */
.tabs {
  display: inline-flex;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: 2px;
  overflow: hidden;
  align-self: flex-start;
  background: #fff;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.04em;
  background: #fff;
  color: #000;
  border: none;
  border-right: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, transform 0.15s;
}

.tab:last-child {
  border-right: none;
}

.tab:hover:not(.active) {
  background: #FAFAFA;
  transform: translateY(-1px);
}

.tab.active {
  background: #000;
  color: #fff;
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: 11px;
  background: var(--border);
  color: #000;
}

.tab.active .tab-count {
  background: var(--orange);
  color: #fff;
}

/* ---- Panels ---- */
.panel {
  border: 1px solid var(--border);
  border-radius: 2px;
  background: #fff;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #000;
  background: #FAFAFA;
}

.diamond {
  color: var(--orange);
  font-size: 12px;
}

.panel-body {
  padding: 20px 22px;
}

/* ---- Empty states ---- */
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 6px;
  padding: 48px 16px;
}

.empty-icon {
  color: var(--orange);
  font-size: 24px;
  margin-bottom: 4px;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: #000;
}

.empty-sub {
  font-size: 12px;
  color: #999;
  font-family: var(--mono);
}

/* ---- Actors meta row ---- */
.meta-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px dashed var(--border);
}

.meta-question {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 60%;
  min-width: 0;
}

.meta-label {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #999;
}

.meta-text {
  font-size: 14px;
  line-height: 1.6;
  color: #000;
}

.meta-date {
  font-family: var(--mono);
  font-size: 11px;
  color: #666;
  white-space: nowrap;
}

/* ---- Actor grid ---- */
.actor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
}

.actor-card {
  border: 1px solid var(--border);
  border-radius: 2px;
  padding: 14px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: transform 0.15s, box-shadow 0.15s;
}

.actor-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.actor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.actor-name {
  font-weight: 700;
  font-size: 15px;
  color: #000;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.influence-pill {
  flex-shrink: 0;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

.inf-high {
  background: var(--orange);
  color: #fff;
}

.inf-medium {
  background: #fff;
  color: #000;
  border: 1px solid #000;
}

.inf-low {
  background: #F0F0F0;
  color: #666;
}

.inf-unknown {
  background: #F0F0F0;
  color: #999;
}

.type-badge {
  align-self: flex-start;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 2px 7px;
  background: #000;
  color: #fff;
  border-radius: 2px;
}

.actor-role {
  font-size: 13px;
  color: #000;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actor-stance {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.actor-memory {
  font-size: 11px;
  color: #999;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ---- Sub-blocks (timeline / chips) ---- */
.sub-block {
  margin-top: 22px;
}

.sub-label {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #999;
  margin-bottom: 10px;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  border-left: 1px solid var(--border);
}

.timeline-item {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 6px 0 6px 16px;
}

.timeline-item::before {
  content: '';
  position: absolute;
  left: -4px;
  top: 11px;
  width: 7px;
  height: 7px;
  background: var(--orange);
  border-radius: 50%;
}

.timeline-date {
  flex-shrink: 0;
  font-family: var(--mono);
  font-size: 11px;
  color: #666;
  min-width: 84px;
}

.timeline-event {
  font-size: 13px;
  color: #000;
  line-height: 1.5;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  font-size: 12px;
  padding: 4px 12px;
  border: 1px solid var(--orange);
  border-radius: 14px;
  color: var(--orange);
  background: #fff;
  white-space: nowrap;
}

/* ---- Sources ---- */
.sources-count {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.04em;
  color: #999;
  margin-bottom: 12px;
}

.source-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.source-item {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid var(--border);
}

.source-item:last-child {
  border-bottom: none;
}

.source-idx {
  flex-shrink: 0;
  font-family: var(--mono);
  font-size: 11px;
  color: #999;
  min-width: 24px;
}

.source-link {
  color: var(--orange);
  text-decoration: none;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
  transition: text-decoration 0.15s;
}

.source-link:hover {
  text-decoration: underline;
}

/* ---- Markdown body typography ---- */
.md-body {
  font-size: 15px;
  color: #000;
  line-height: 1.85;
  word-wrap: break-word;
}

.md-body :deep(h1) {
  font-family: 'Space Grotesk', system-ui, sans-serif;
  font-size: 28px;
  font-weight: 700;
  line-height: 1.25;
  margin: 28px 0 16px;
}

.md-body :deep(h2) {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
  margin: 26px 0 14px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}

.md-body :deep(h3) {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
  margin: 22px 0 10px;
}

.md-body :deep(h4) {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  margin: 18px 0 8px;
  color: #333;
}

.md-body :deep(p) {
  margin: 0 0 14px;
  line-height: 1.85;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0 0 14px;
  padding-left: 26px;
}

.md-body :deep(li) {
  margin: 4px 0;
  line-height: 1.7;
}

.md-body :deep(blockquote) {
  margin: 16px 0;
  padding: 10px 16px;
  border-left: 3px solid var(--orange);
  background: #FAFAFA;
  color: #444;
}

.md-body :deep(.md-table),
.md-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 13px;
}

.md-body :deep(.md-table th),
.md-body :deep(.md-table td),
.md-body :deep(table th),
.md-body :deep(table td) {
  border: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.md-body :deep(.md-table th),
.md-body :deep(table th) {
  background: #FAFAFA;
  font-weight: 600;
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}

.md-body :deep(code) {
  font-family: var(--mono);
  font-size: 0.88em;
  background: #FAFAFA;
  border: 1px solid var(--border);
  border-radius: 2px;
  padding: 1px 5px;
}

.md-body :deep(pre),
.md-body :deep(.md-pre) {
  margin: 16px 0;
  padding: 14px 16px;
  background: #FAFAFA;
  border: 1px solid var(--border);
  border-radius: 2px;
  overflow-x: auto;
}

.md-body :deep(pre code),
.md-body :deep(.md-pre code) {
  background: none;
  border: none;
  padding: 0;
  font-size: 13px;
  line-height: 1.6;
}

.md-body :deep(a) {
  color: var(--orange);
  text-decoration: none;
}

.md-body :deep(a:hover) {
  text-decoration: underline;
}

.md-body :deep(hr),
.md-body :deep(.md-hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}

.md-body :deep(strong) {
  font-weight: 700;
}

.md-body :deep(img) {
  max-width: 100%;
  height: auto;
}
</style>
