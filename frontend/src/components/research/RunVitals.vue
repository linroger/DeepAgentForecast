<template>
  <div v-if="model" class="run-vitals">
    <!-- Elapsed (ticks locally between polls; frozen final duration when terminal) -->
    <span v-if="elapsedText" class="rv-item" :title="L('运行时长', 'Run duration')">
      <span class="rv-k">{{ L('已运行', 'elapsed') }}</span>
      <span class="rv-v">{{ elapsedText }}</span>
    </span>

    <!-- ETA (approximate by construction; never rendered for terminal states) -->
    <span
      v-if="etaText"
      class="rv-item"
      :title="L('按当前进度线性外推，仅供量级参考', 'Linear extrapolation from current progress — rough guide only')"
    >
      <span class="rv-k">{{ L('预计剩余', 'eta') }}</span>
      <span class="rv-v">~{{ etaText }}</span>
      <span class="rv-approx">{{ L('约', 'approx') }}</span>
    </span>

    <!-- Liveness chip -->
    <span
      v-if="model.liveness"
      class="rv-chip"
      :class="'rv-chip--' + model.liveness"
      :title="livenessTitle"
    >
      <span class="rv-dot" aria-hidden="true"></span>
      {{ livenessLabel }}
    </span>

    <!-- Spend (in-process metered tokens / cost) -->
    <span
      v-if="spendTokensText || spendCostText"
      class="rv-item"
      :title="L('本进程实测的 LLM 消耗', 'LLM spend metered in this backend process')"
    >
      <span class="rv-k">{{ L('已消耗', 'spend') }}</span>
      <span v-if="spendTokensText" class="rv-v">{{ spendTokensText }} tok</span>
      <span v-if="spendCostText" class="rv-v">{{ spendCostText }}</span>
    </span>

    <!-- Budget (thin spent/limit bar + remaining count) -->
    <span v-if="model.budget" class="rv-item rv-budget" :title="budgetTitle">
      <span class="rv-k">{{ L('预算', 'budget') }}</span>
      <span class="rv-budget-track" :class="{ 'is-critical': model.budget.critical }">
        <span class="rv-budget-fill" :style="{ width: model.budget.pctUsed + '%' }"></span>
      </span>
      <span class="rv-v" :class="{ 'rv-v--err': model.budget.critical }">{{ budgetRemainingText }}</span>
      <span class="rv-k">{{ L('剩余', 'left') }}</span>
    </span>
  </div>
</template>

<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { L } from '../../i18n'
import {
  buildRunVitalsModel,
  formatDurationCompact,
  formatTokensCompact,
  formatCostUsd
} from '../../utils/runVitals'

/**
 * RunVitals — compact strip of live run vitals derived from the computed
 * `live` block of GET /api/research/status/<id>. Purely presentational: it
 * performs no API calls and renders nothing at all when the block is absent
 * (older servers, helper failure) or carries nothing renderable. Individual
 * null fields drop only their own item.
 */
const props = defineProps({
  /** Raw `data.live` block from the status response (null when absent). */
  live: { type: Object, default: null },
  /** Pipeline status (running / completed / failed / cancelled / …). */
  status: { type: String, default: '' },
  /** Sibling status-response timestamps; needed for terminal final duration. */
  createdAt: { type: String, default: '' },
  updatedAt: { type: String, default: '' },
  resumedAt: { type: String, default: '' }
})

const model = computed(() => buildRunVitalsModel(props.live, {
  status: props.status,
  createdAt: props.createdAt,
  updatedAt: props.updatedAt,
  resumedAt: props.resumedAt
}))

// —— Elapsed keeps counting between polls ——
// The poll cadence adapts up to 12s; a frozen elapsed readout looks stalled.
// Advance it locally from the moment the snapshot arrived (computed from
// wall-clock diffs, so background-tab timer throttling cannot skew it).
const liveReceivedAtMs = ref(0)
watch(() => props.live, () => { liveReceivedAtMs.value = Date.now() }, { immediate: true })

const nowMs = ref(Date.now())
let tickTimer = null
function syncTicker() {
  const active = !!(model.value && !model.value.terminal && model.value.elapsedSeconds !== null)
  if (active && !tickTimer) {
    tickTimer = setInterval(() => { nowMs.value = Date.now() }, 1000)
  } else if (!active && tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
}
watch(model, syncTicker, { immediate: true })
onUnmounted(() => { if (tickTimer) { clearInterval(tickTimer); tickTimer = null } })

const elapsedText = computed(() => {
  const m = model.value
  if (!m || m.elapsedSeconds === null) return null
  if (m.terminal) return formatDurationCompact(m.elapsedSeconds)
  const drift = liveReceivedAtMs.value
    ? Math.max(0, Math.floor((nowMs.value - liveReceivedAtMs.value) / 1000))
    : 0
  return formatDurationCompact(m.elapsedSeconds + drift)
})

const etaText = computed(() => {
  const m = model.value
  return m && m.etaSeconds !== null ? formatDurationCompact(m.etaSeconds) : null
})

// —— Liveness ——
const livenessLabel = computed(() => {
  switch (model.value && model.value.liveness) {
    case 'ok': return L('活跃', 'active')
    case 'stale': return L('仍在思考', 'still thinking')
    case 'dead': return L('进程失联', 'process down')
    default: return ''
  }
})

const livenessTitle = computed(() => {
  const m = model.value
  if (!m) return ''
  const parts = []
  if (m.heartbeatAgeS !== null) parts.push(`${L('心跳', 'heartbeat')} ${m.heartbeatAgeS}s`)
  if (m.lastProgressAgeS !== null) parts.push(`${L('距上次进度', 'last progress')} ${m.lastProgressAgeS}s`)
  if (m.ownerPid !== null) parts.push(`PID ${m.ownerPid}`)
  return parts.join(' · ')
})

// —— Spend / budget ——
const spendTokensText = computed(() => {
  const m = model.value
  return m ? formatTokensCompact(m.spendTokens) : null
})
const spendCostText = computed(() => {
  const m = model.value
  return m ? formatCostUsd(m.spendCostUsd) : null
})
const budgetRemainingText = computed(() => {
  const m = model.value
  return m && m.budget ? (formatTokensCompact(m.budget.remainingTokens) || '0') : ''
})
const budgetTitle = computed(() => {
  const m = model.value
  if (!m || !m.budget) return ''
  const spent = formatTokensCompact(m.budget.spentTokens) || '0'
  const limit = formatTokensCompact(m.budget.limitTokens) || '0'
  return `${spent} / ${limit} tokens · ${m.budget.pctUsed}%`
})
</script>

<style scoped>
.run-vitals {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2, 8px) var(--sp-4, 16px);
  margin-top: var(--sp-2, 8px);
  font-family: var(--font-mono, 'JetBrains Mono', ui-monospace, monospace);
  font-size: var(--fs-xs, 11px);
  line-height: var(--lh-tight, 1.3);
  color: var(--color-muted, #666);
}

.rv-item {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1, 4px);
  white-space: nowrap;
}

.rv-k {
  font-size: var(--fs-2xs, 10px);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-faint, #999);
}

.rv-v {
  font-weight: 600;
  color: var(--color-ink, #0A0A0A);
  font-variant-numeric: tabular-nums;
}

.rv-v--err {
  color: var(--color-err, #B91C1C);
}

.rv-approx {
  font-size: var(--fs-2xs, 10px);
  color: var(--color-faint, #999);
  border: 1px solid var(--color-border, #E5E5E5);
  border-radius: var(--radius-pill, 999px);
  padding: 0 var(--sp-2, 8px);
}

/* —— Liveness chip (pill vocabulary) —— */
.rv-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1, 4px);
  padding: calc(var(--sp-1, 4px) / 2) var(--sp-2, 8px);
  border: 1px solid currentColor;
  border-radius: var(--radius-pill, 999px);
  font-size: var(--fs-2xs, 10px);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  white-space: nowrap;
}

.rv-dot {
  /* Em-based so the dot scales with the chip's type size (≈6px at 10px). */
  width: 0.6em;
  height: 0.6em;
  border-radius: var(--radius-pill, 999px);
  background: currentColor;
  flex-shrink: 0;
}

.rv-chip--ok {
  color: var(--color-ok, #16A34A);
  background: var(--color-ok-soft, #F0FDF4);
}

.rv-chip--ok .rv-dot {
  /* Stylesheet animation: the global prefers-reduced-motion collapse applies. */
  animation: rv-pulse calc(var(--dur-3, 250ms) * 8) var(--ease, ease) infinite;
}

.rv-chip--stale {
  color: var(--color-warn, #D97706);
  background: var(--color-soft, #FAFAFA);
}

.rv-chip--dead {
  color: var(--color-err, #B91C1C);
  background: var(--color-err-soft, #FEF2F2);
}

@keyframes rv-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

/* —— Budget bar —— */
.rv-budget-track {
  display: inline-block;
  width: 9ch;
  height: var(--sp-1, 4px);
  background: var(--color-border, #E5E5E5);
  border-radius: var(--radius-pill, 999px);
  overflow: hidden;
}

.rv-budget-fill {
  display: block;
  height: 100%;
  background: var(--color-accent, #FF4500);
  border-radius: var(--radius-pill, 999px);
  transition: width var(--dur-3, 250ms) var(--ease, ease);
}

.rv-budget-track.is-critical .rv-budget-fill {
  background: var(--color-err, #B91C1C);
}
</style>
