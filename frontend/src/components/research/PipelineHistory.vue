<template>
  <div class="pipeline-history">
    <!-- 头部：标题 + 计数 + 刷新 -->
    <header class="ph-header">
      <span class="ph-title">
        <span class="diamond">◇</span> {{ L('历史推演','Research history') }}
        <span class="ph-count" v-if="items.length">{{ items.length }}</span>
      </span>
      <span class="ph-header-actions">
        <button
          v-if="failedCount > 0"
          class="ph-refresh ph-clean"
          type="button"
          :disabled="cleaning"
          @click="cleanFailed"
          :title="L('删除所有失败/已取消的运行记录','Delete all failed/cancelled run records')"
        >
          {{ cleaning ? L('清理中…','Cleaning…') : L('清理失败','Clear failed') + ` (${failedCount})` }}
        </button>
        <button
          class="ph-refresh"
          type="button"
          :disabled="loading"
          @click="load"
          :title="L('刷新历史列表','Refresh history list')"
        >
          <span class="ph-refresh-icon" :class="{ spinning: loading }">↻</span>
          {{ L('刷新','Refresh') }}
        </button>
      </span>
    </header>

    <!-- 错误态 -->
    <div v-if="error" class="ph-error">
      <span class="ph-error-dot"></span>{{ error }}
    </div>

    <!-- 列表 -->
    <div class="ph-list" role="list">
      <!-- 加载态（仅首次空列表时显示骨架） -->
      <div v-if="loading && !items.length" class="ph-loading">
        <span class="ph-loading-dot"></span>{{ L('加载中…','Loading…') }}
      </div>

      <!-- 空态 -->
      <div v-else-if="!items.length" class="ph-empty">
        {{ L('暂无历史','No history yet') }}
      </div>

      <!-- 行（div 而非 button：行内还有取消/删除等嵌套按钮，按钮不能嵌按钮） -->
      <div
        v-for="item in items"
        :key="rowKey(item)"
        class="ph-row"
        :class="{ active: isActive(item) }"
        role="listitem"
        tabindex="0"
        @click="select(item)"
        @keydown.enter.self="select(item)"
        @keydown.space.self.prevent="select(item)"
      >
        <!-- 顶行：状态徽标 + 相对时间 -->
        <div class="ph-row-top">
          <span class="ph-status" :class="statusOf(item).cls">
            <span class="ph-status-dot"></span>{{ statusOf(item).label }}
          </span>
          <span class="ph-time">{{ timeAgo(item && item.created_at) }}</span>
        </div>

        <!-- 提示词（钳制两行） -->
        <p class="ph-prompt">{{ promptOf(item) }}</p>

        <!-- 进度条 -->
        <div class="ph-progress">
          <div class="ph-progress-track">
            <div class="ph-progress-fill" :style="{ width: progressOf(item) + '%' }"></div>
          </div>
          <span class="ph-progress-pct">{{ progressOf(item) }}%</span>
        </div>

        <!-- 行内操作：运行中可取消；已结束可删除 -->
        <div class="ph-row-actions">
          <button
            v-if="isRunning(item)"
            type="button"
            class="ph-action ph-action-cancel"
            :disabled="busyId === item.pipeline_id"
            @click.stop="cancelRun(item)"
          >{{ busyId === item.pipeline_id ? L('取消中…','Cancelling…') : L('取消','Cancel') }}</button>
          <button
            v-else
            type="button"
            class="ph-action ph-action-delete"
            :disabled="busyId === item.pipeline_id"
            @click.stop="deleteRun(item)"
          >{{ busyId === item.pipeline_id ? L('删除中…','Deleting…') : L('删除','Delete') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { listPipelines, cancelPipeline, deletePipeline, cleanPipelines } from '../../api/research'
import { L } from '../../i18n'

const props = defineProps({
  activeId: { type: String, default: '' }
})

const emit = defineEmits(['select', 'deleted', 'cancelled'])

const items = ref([])
const loading = ref(false)
const error = ref('')
const busyId = ref('')
const cleaning = ref(false)

const failedCount = computed(() =>
  items.value.filter(it => {
    const s = it && String(it.status || '').toLowerCase()
    return s === 'failed' || s === 'cancelled' || s === 'canceled' || s === 'error'
  }).length
)

function isRunning(item) {
  const s = item && String(item.status || '').toLowerCase()
  return s === 'running' || s === 'in_progress' || s === 'pending'
}

/** 取消一条运行中的管线，随后刷新列表。 */
async function cancelRun(item) {
  const id = item && item.pipeline_id
  if (!id || busyId.value) return
  if (!window.confirm(L('确定取消该推演？正在运行的研究/模拟将被终止。', 'Cancel this run? The in-flight research/simulation will be stopped.'))) return
  busyId.value = id
  try {
    await cancelPipeline(id)
    emit('cancelled', id)
    await load()
  } catch (e) {
    error.value = (e && e.message) ? e.message : L('取消失败', 'Cancel failed')
  } finally {
    busyId.value = ''
  }
}

/** 删除一条已结束的管线记录，随后刷新列表。 */
async function deleteRun(item) {
  const id = item && item.pipeline_id
  if (!id || busyId.value) return
  if (!window.confirm(L('确定删除该运行记录？研究档案等产物将一并删除，不可恢复。', 'Delete this run? Its research dossier and artifacts will be removed permanently.'))) return
  busyId.value = id
  try {
    await deletePipeline(id)
    emit('deleted', id)
    await load()
  } catch (e) {
    error.value = (e && e.message) ? e.message : L('删除失败', 'Delete failed')
  } finally {
    busyId.value = ''
  }
}

/** 批量清理失败/已取消的运行记录。 */
async function cleanFailed() {
  if (cleaning.value) return
  if (!window.confirm(L(`确定清理全部 ${failedCount.value} 条失败/已取消的记录？不可恢复。`, `Delete all ${failedCount.value} failed/cancelled runs? This cannot be undone.`))) return
  cleaning.value = true
  try {
    const res = await cleanPipelines()
    const deleted = (res && res.data && res.data.deleted) || []
    deleted.forEach(id => emit('deleted', id))
    await load()
  } catch (e) {
    error.value = (e && e.message) ? e.message : L('清理失败', 'Clean failed')
  } finally {
    cleaning.value = false
  }
}

/**
 * 拉取管线历史列表。永不抛出：任何异常都落到 error 文案，列表保持上一次的安全值。
 */
async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listPipelines()
    const list = res && res.data && res.data.pipelines
    items.value = Array.isArray(list) ? list : []
  } catch (e) {
    error.value = (e && e.message) ? e.message : L('加载历史失败','Failed to load history')
    if (!Array.isArray(items.value)) items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ——— 防御性取值 helpers ———

function rowKey(item) {
  return (item && (item.pipeline_id || item.report_id)) || JSON.stringify(item || {})
}

function isActive(item) {
  return !!(item && item.pipeline_id && props.activeId && item.pipeline_id === props.activeId)
}

function promptOf(item) {
  const p = item && item.prompt
  return (typeof p === 'string' && p.trim()) ? p : L('（无提示词）','(No prompt)')
}

/** 归一化 global_progress 为 0–100 的整数。 */
function progressOf(item) {
  let v = item && item.global_progress
  if (v == null) return 0
  v = Number(v)
  if (!Number.isFinite(v)) return 0
  // 兼容 0–1 比例与 0–100 百分比两种后端口径
  if (v > 0 && v <= 1) v = v * 100
  if (v < 0) v = 0
  if (v > 100) v = 100
  return Math.round(v)
}

/** 状态 → 文案 + 样式类。未知状态回退灰色。 */
function statusOf(item) {
  const raw = (item && item.status != null) ? String(item.status).toLowerCase() : ''
  if (raw === 'running' || raw === 'in_progress' || raw === 'pending') {
    return { cls: 'running', label: L('运行中','Running') }
  }
  if (raw === 'completed' || raw === 'complete' || raw === 'done' || raw === 'success') {
    return { cls: 'completed', label: L('已完成','Completed') }
  }
  if (raw === 'failed' || raw === 'error' || raw === 'cancelled' || raw === 'canceled') {
    return { cls: 'failed', label: L('失败','Failed') }
  }
  return { cls: 'unknown', label: raw ? raw : L('未知','Unknown') }
}

function select(item) {
  const id = item && item.pipeline_id
  if (id) emit('select', id)
}

/**
 * 相对时间。容错解析多种时间表示（ISO 字符串、时间戳秒/毫秒），无法解析时返回占位符。
 */
function timeAgo(value) {
  if (value == null || value === '') return '—'
  let ms
  if (typeof value === 'number') {
    // 10 位 → 秒，13 位 → 毫秒
    ms = value < 1e12 ? value * 1000 : value
  } else {
    let s = String(value).trim()
    // 后端常返回不带时区的 ISO（视为 UTC，补 Z）
    if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(s)) {
      s = s.replace(' ', 'T') + 'Z'
    }
    ms = Date.parse(s)
  }
  if (!Number.isFinite(ms)) return '—'

  const diff = Date.now() - ms
  if (!Number.isFinite(diff)) return '—'
  if (diff < 0) return L('刚刚','Just now') // 时钟偏差导致的未来时间
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return L('刚刚','Just now')
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} ${L('分钟前','min ago')}`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} ${L('小时前','hr ago')}`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} ${L('天前','days ago')}`
  const mon = Math.floor(day / 30)
  if (mon < 12) return `${mon} ${L('个月前','months ago')}`
  const yr = Math.floor(day / 365)
  return `${yr} ${L('年前','years ago')}`
}

defineExpose({ load })
</script>

<style scoped>
.pipeline-history {
  --orange: #FF4500;
  --border: #E5E5E5;
  --mono: 'JetBrains Mono', monospace;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border: 1px solid var(--border);
  background: #fff;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  color: #000;
}

/* ——— 头部 ——— */
.ph-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  flex: 0 0 auto;
}

.ph-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: #222;
}

.diamond { color: var(--orange); }

.ph-count {
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  color: #fff;
  background: var(--orange);
  padding: 1px 7px;
  border-radius: 999px;
  letter-spacing: 0;
}

.ph-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #fff;
  color: #000;
  border: 1px solid var(--border);
  padding: 6px 12px;
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.ph-refresh:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: var(--orange);
  background: #FAFAFA;
}

.ph-refresh:disabled {
  cursor: default;
  color: #999;
}

.ph-refresh-icon {
  display: inline-block;
  font-size: 0.9rem;
  line-height: 1;
}

.ph-refresh-icon.spinning {
  animation: ph-spin 0.8s linear infinite;
}

@keyframes ph-spin {
  to { transform: rotate(360deg); }
}

/* ——— 错误态 ——— */
.ph-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  font-family: var(--mono);
  font-size: 0.72rem;
  color: #b91c1c;
  border-bottom: 1px solid var(--border);
  background: #FAFAFA;
}

.ph-error-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #b91c1c;
  flex: 0 0 auto;
}

/* ——— 列表 ——— */
.ph-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 6px;
}

.ph-loading,
.ph-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 16px;
  font-family: var(--mono);
  font-size: 0.78rem;
  color: #999;
  letter-spacing: 0.5px;
}

.ph-loading-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--orange);
  animation: ph-blink 1s step-end infinite;
}

@keyframes ph-blink {
  50% { opacity: 0.25; }
}

/* ——— 行 ——— */
.ph-row {
  display: block;
  width: 100%;
  text-align: left;
  background: #fff;
  border: 1px solid transparent;
  border-bottom: 1px solid var(--border);
  border-left: 3px solid transparent;
  padding: 12px 14px;
  cursor: pointer;
  font-family: inherit;
  color: inherit;
  transition: background 0.16s ease, transform 0.16s ease, border-color 0.16s ease;
}

.ph-row:last-child {
  border-bottom: 1px solid transparent;
}

.ph-row:hover {
  background: #FAFAFA;
  transform: translateY(-1px);
}

.ph-row.active {
  border-left: 3px solid var(--orange);
  background: #FFF6F2;
}

.ph-row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

/* 状态徽标 */
.ph-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: #666;
}

.ph-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #999;
  flex: 0 0 auto;
}

.ph-status.running { color: var(--orange); }
.ph-status.running .ph-status-dot {
  background: var(--orange);
  animation: ph-blink 1s step-end infinite;
}

.ph-status.completed { color: #16a34a; }
.ph-status.completed .ph-status-dot { background: #16a34a; }

.ph-status.failed { color: #b91c1c; }
.ph-status.failed .ph-status-dot { background: #b91c1c; }

.ph-status.unknown { color: #999; }
.ph-status.unknown .ph-status-dot { background: #999; }

.ph-time {
  font-family: var(--mono);
  font-size: 0.66rem;
  color: #999;
  flex: 0 0 auto;
  white-space: nowrap;
}

/* 提示词（钳制两行） */
.ph-prompt {
  margin: 0 0 10px;
  font-size: 0.82rem;
  line-height: 1.45;
  color: #222;
  word-break: break-word;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* 进度条 */
.ph-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ph-progress-track {
  flex: 1 1 auto;
  height: 4px;
  background: #F0F0F0;
  border: 1px solid var(--border);
  overflow: hidden;
}

.ph-progress-fill {
  height: 100%;
  background: var(--orange);
  transition: width 0.4s ease;
}

.ph-progress-pct {
  flex: 0 0 auto;
  font-family: var(--mono);
  font-size: 0.66rem;
  color: #666;
  min-width: 34px;
  text-align: right;
}

/* ——— 头部操作区 ——— */
.ph-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.ph-clean {
  color: #b91c1c;
}

.ph-clean:hover:not(:disabled) {
  border-color: #b91c1c;
}

/* ——— 行内操作 ——— */
.ph-row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.ph-row:hover .ph-row-actions,
.ph-row:focus-within .ph-row-actions,
.ph-row.active .ph-row-actions {
  opacity: 1;
}

.ph-action {
  background: #fff;
  border: 1px solid var(--border);
  padding: 4px 10px;
  font-family: var(--mono);
  font-size: 0.66rem;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.16s ease, color 0.16s ease;
}

.ph-action:disabled { color: #bbb; cursor: default; }

.ph-action-cancel { color: var(--orange); }
.ph-action-cancel:hover:not(:disabled) { border-color: var(--orange); }

.ph-action-delete { color: #b91c1c; }
.ph-action-delete:hover:not(:disabled) { border-color: #b91c1c; }
</style>
