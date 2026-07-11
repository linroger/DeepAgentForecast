<template>
  <section class="research-console">
    <!-- 面板头部 -->
    <header class="rc-header">
      <div class="rc-title">
        <span class="rc-diamond">◇</span>
        <span class="rc-title-text">{{ L('研究控制台 · DeerFlow', 'Research Console · DeerFlow') }}</span>
      </div>

      <div class="rc-header-right">
        <span class="rc-tool-counter">{{ L('工具调用', 'Tool calls') }} {{ toolCallCount }}</span>

        <div class="rc-filter" role="tablist" :aria-label="L('日志过滤', 'Log filter')">
          <button
            v-for="opt in filterOptions"
            :key="opt.value"
            type="button"
            role="tab"
            class="rc-filter-btn"
            :class="{ active: activeFilter === opt.value }"
            :aria-selected="activeFilter === opt.value"
            @click="activeFilter = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
    </header>

    <!-- 终端主体 -->
    <div class="rc-terminal-wrap">
      <div
        ref="terminalRef"
        class="rc-terminal"
        @scroll="onScroll"
      >
        <template v-if="visibleLines.length">
          <div
            v-for="(line, idx) in visibleLines"
            :key="idx"
            class="rc-line"
            :style="{ color: colorFor(line) }"
          >{{ line }}</div>
        </template>

        <div v-else class="rc-empty">
          {{ emptyMessage }}
        </div>
      </div>

      <!-- 跳到底部 -->
      <button
        v-if="showJumpButton"
        type="button"
        class="rc-jump-btn"
        @click="jumpToBottom"
      >
        ↓ {{ L('跳到底部', 'Jump to bottom') }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { L } from '../../i18n'
import { liveLogRevision } from '../../utils/liveProgress'

const props = defineProps({
  logLines: {
    type: Array,
    default: () => []
  }
})

// 始终是一个安全的字符串数组，永不抛错
const safeLines = computed(() => {
  const src = Array.isArray(props.logLines) ? props.logLines : []
  return src.map((l) => (l == null ? '' : String(l)))
})

// 工具调用计数：包含 [tool] 的行数
const toolCallCount = computed(() =>
  safeLines.value.reduce((n, l) => (l.includes('[tool]') ? n + 1 : n), 0)
)

// 分段过滤器
const filterOptions = computed(() => [
  { value: 'all', label: L('全部', 'All') },
  { value: 'tool', label: L('工具', 'Tools') },
  { value: 'result', label: L('结果', 'Results') },
  { value: 'error', label: L('错误', 'Errors') }
])
const activeFilter = ref('all')

const visibleLines = computed(() => {
  const lines = safeLines.value
  switch (activeFilter.value) {
    case 'tool':
      return lines.filter((l) => l.includes('[tool]'))
    case 'result':
      return lines.filter((l) => l.includes('[result]'))
    case 'error':
      return lines.filter((l) => l.includes('[error]') || l.includes('[warn]'))
    default:
      return lines
  }
})

const emptyMessage = computed(() =>
  activeFilter.value === 'all'
    ? L('等待研究子进程输出…', 'Waiting for research subprocess output…')
    : L('当前过滤条件下暂无日志。', 'No logs under the current filter.')
)

// 按子串着色
function colorFor(line) {
  const l = typeof line === 'string' ? line : ''
  if (l.includes('[tool]')) return '#7dd3fc'
  if (l.includes('[ok]') || l.includes('[done]')) return '#86efac'
  if (l.includes('[error]')) return '#fca5a5'
  if (l.includes('[warn]')) return '#fbbf24'
  if (l.includes('[stage]') || l.includes('[init]')) return '#fdba74'
  if (l.includes('[result]')) return '#a3a3a3'
  return '#d6d6d6'
}

// ── 自动滚动到底部（仅当用户已在底部附近时）──────────────────────
const terminalRef = ref(null)
const atBottom = ref(true)
const SCROLL_THRESHOLD = 48 // px，距底部小于此值视为“在底部”

function computeAtBottom(el) {
  if (!el) return true
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  return distance <= SCROLL_THRESHOLD
}

function onScroll() {
  const el = terminalRef.value
  if (!el) return
  atBottom.value = computeAtBottom(el)
}

function scrollToBottom() {
  const el = terminalRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

function jumpToBottom() {
  scrollToBottom()
  atBottom.value = true
}

// 当没有更多内容可滚动时不显示按钮
const showJumpButton = computed(() => !atBottom.value && visibleLines.value.length > 0)

// 监听日志变化与过滤切换：若已在底部则自动跟随到底
watch(
  () => [liveLogRevision(safeLines.value), activeFilter.value],
  () => {
    if (!atBottom.value) return
    nextTick(() => scrollToBottom())
  }
)

onMounted(() => {
  nextTick(() => {
    scrollToBottom()
    onScroll()
  })
})
</script>

<style scoped>
.research-console {
  --orange: #FF4500;
  --border: #E5E5E5;
  --mono: 'JetBrains Mono', monospace;
  --muted: #666;
  --faint: #999;
  --term-bg: #0c0c0c;
  --term-fg: #d6d6d6;

  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--border);
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

/* ── 面板头部 ───────────────────────────────────────────── */
.rc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.rc-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: #000;
}

.rc-diamond {
  color: var(--orange);
  font-size: 0.8rem;
  line-height: 1;
}

.rc-title-text {
  white-space: nowrap;
}

.rc-header-right {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.rc-tool-counter {
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.5px;
  color: var(--muted);
  white-space: nowrap;
}

/* ── 分段过滤器 ─────────────────────────────────────────── */
.rc-filter {
  display: inline-flex;
  border: 1px solid var(--border);
}

.rc-filter-btn {
  appearance: none;
  border: none;
  border-right: 1px solid var(--border);
  background: #fff;
  color: var(--muted);
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.5px;
  padding: 5px 12px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.rc-filter-btn:last-child {
  border-right: none;
}

.rc-filter-btn:hover {
  color: #000;
  transform: translateY(-1px);
}

.rc-filter-btn.active {
  background: #000;
  color: #fff;
}

.rc-filter-btn.active:hover {
  color: #fff;
}

/* ── 终端主体 ───────────────────────────────────────────── */
.rc-terminal-wrap {
  position: relative;
}

.rc-terminal {
  background: var(--term-bg);
  color: var(--term-fg);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.6;
  padding: 14px 16px;
  max-height: 520px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.rc-line {
  white-space: pre-wrap;
  word-break: break-word;
}

.rc-empty {
  color: var(--faint);
  font-style: italic;
  font-size: 12px;
  padding: 8px 0;
  user-select: none;
}

/* 终端滚动条 */
.rc-terminal::-webkit-scrollbar {
  width: 8px;
}
.rc-terminal::-webkit-scrollbar-track {
  background: transparent;
}
.rc-terminal::-webkit-scrollbar-thumb {
  background: #2a2a2a;
  border-radius: 4px;
}
.rc-terminal::-webkit-scrollbar-thumb:hover {
  background: #3a3a3a;
}

/* ── 跳到底部按钮 ───────────────────────────────────────── */
.rc-jump-btn {
  position: absolute;
  right: 16px;
  bottom: 14px;
  appearance: none;
  border: 1px solid var(--orange);
  background: var(--orange);
  color: #fff;
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.5px;
  padding: 5px 11px;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  transition: transform 0.15s ease, opacity 0.15s ease;
}

.rc-jump-btn:hover {
  transform: translateY(-1px);
  opacity: 0.92;
}
</style>
