<template>
  <!-- LOOP-017：一等公民二元预测表。无二元预测（旧报告 / forecast 缺失）→ 整体不渲染。 -->
  <section v-if="rows.length" class="bft">
    <button
      class="bft-head"
      type="button"
      :aria-expanded="open ? 'true' : 'false'"
      @click="open = !open"
    >
      <span class="diamond" aria-hidden="true">◇</span>
      <span class="panel-label">{{ L('二元预测', 'Binary Forecasts') }}</span>
      <span class="bft-count">{{ rows.length }}</span>
      <span class="bft-caret" aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
    </button>
    <!-- 宽表在自身容器内横向滚动，绝不撑破报告正文。 -->
    <div v-show="open" class="bft-scroll">
      <table class="bft-table">
        <thead>
          <tr>
            <th class="col-statement">{{ L('命题', 'Statement') }}</th>
            <th>{{ L('概率', 'Probability') }}</th>
            <th>{{ L('置信度', 'Confidence') }}</th>
            <th>{{ L('市场锚点', 'Market anchor') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key">
            <td class="cell-statement">
              <span class="statement-text" :title="statementTitle(row)">{{ row.statement }}</span>
            </td>
            <td class="cell-prob">
              <span class="prob-main">{{ row.probabilityText }}</span>
              <span v-if="row.interval" class="prob-interval" :title="intervalTitle(row)">
                {{ row.interval.text }}
              </span>
            </td>
            <td class="cell-conf">
              <span
                v-if="row.confidenceLevel"
                class="conf-chip"
                :class="'chip-' + row.confidenceLevel"
              >{{ confidenceLabel(row) }}</span>
              <span v-else class="cell-empty">—</span>
            </td>
            <td class="cell-market" :title="row.anchor ? (anchorTitle(row) || null) : null">
              <span v-if="!row.anchor && !row.influence" class="cell-empty">—</span>
              <template v-else>
                <template v-if="row.anchor">
                  <a
                    v-if="row.anchor.url"
                    class="market-implied market-link"
                    :href="row.anchor.url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >{{ row.anchor.impliedText }} ↗</a>
                  <span v-else class="market-implied">{{ row.anchor.impliedText }}</span>
                  <span
                    v-if="row.anchor.divergence"
                    class="market-delta"
                    :class="'delta-' + row.anchor.divergence.tone"
                  >Δ {{ row.anchor.divergence.text }}</span>
                </template>
                <span
                  v-if="row.influence"
                  class="influence-badge"
                  :title="influenceTitle(row)"
                >{{ L('市场影响', 'market-influenced') }}</span>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { ref, computed } from 'vue'
import { binaryForecastRows } from '../../utils/binaryForecasts'
import { L } from '../../i18n'

// 薄模板：全部行模型/格式化在 utils/binaryForecasts.js（纯函数，可单测）；
// 组件只负责双语标签与 tooltip 的拼装。
const props = defineProps({
  forecast: { type: Object, default: null },
})

const open = ref(true)
const rows = computed(() => binaryForecastRows(props.forecast))

// 命题列：截断展示，原生 title 露出全文（附 anchor-and-adjust 理由，若有）。
function statementTitle(row) {
  return row.rationale ? row.statement + '\n\n' + row.rationale : row.statement
}

function intervalTitle(row) {
  const src = row.interval && row.interval.source
  return src
    ? L('区间来源', 'Interval source') + ': ' + src
    : L('概率区间', 'Probability interval')
}

function confidenceLabel(row) {
  const c = row.confidenceLevel
  if (c === 'low') return L('低', 'Low')
  if (c === 'medium') return L('中', 'Medium')
  if (c === 'high') return L('高', 'High')
  return row.confidenceRaw
}

function anchorTitle(row) {
  const a = row.anchor
  if (!a) return ''
  const lines = []
  if (a.question) lines.push(a.question)
  if (a.equivalence) lines.push(L('判定等价性', 'Resolution equivalence') + ': ' + a.equivalence)
  if (a.matchConfidenceText) lines.push(L('匹配置信度', 'Match confidence') + ': ' + a.matchConfidenceText)
  if (a.priceText) lines.push(L('研究时点价', 'Price at research') + ': ' + a.priceText)
  if (a.endDate) lines.push(L('截止', 'Ends') + ': ' + a.endDate)
  return lines.join('\n')
}

function influenceTitle(row) {
  const inf = row.influence
  if (!inf) return ''
  const lines = [
    L('先验', 'Prior') + ' ' + inf.priorText + ' → ' + L('修订', 'Revised') + ' ' + inf.revisedText,
  ]
  if (inf.anchorRemoved && inf.probabilityRestored) {
    lines.push(L('锚点已移除；概率已恢复为先验值', 'Anchor removed; probability restored to the prior'))
  } else if (inf.anchorRemoved) {
    lines.push(L('锚点已移除；修订概率保留', 'Anchor removed; revised probability retained'))
  } else {
    lines.push(L('市场锚点生效中', 'Market anchor active'))
  }
  if (inf.marketQuestion) lines.push(inf.marketQuestion)
  return lines.join('\n')
}
</script>

<style scoped>
.bft {
  max-width: 760px;
  margin: 0 auto 28px;
  border: 1px solid var(--color-border, #E5E5E5);
  background: var(--color-soft, #FAFAFA);
}

/* ---------- Collapsible header (monospace ◇ panel-label convention) ---------- */
.bft-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 12px 18px;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
}
.diamond {
  color: var(--color-accent, #FF4500);
  font-size: var(--fs-sm, 12px);
  line-height: 1;
}
.panel-label {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-xs, 11px);
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-ink, #0A0A0A);
  transition: color var(--dur-1, 120ms) var(--ease, ease);
}
.bft-head:hover .panel-label { color: var(--color-accent, #FF4500); }
.bft-count {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-2xs, 10px);
  color: var(--color-muted, #666);
  border: 1px solid var(--color-border, #E5E5E5);
  border-radius: var(--radius-pill, 999px);
  background: var(--color-paper, #FFFFFF);
  padding: 1px 8px;
  line-height: 1.5;
}
.bft-caret {
  margin-left: auto;
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-sm, 12px);
  color: var(--color-faint, #999);
}

/* ---------- Table (horizontal scroll stays inside this container) ---------- */
.bft-scroll {
  overflow-x: auto;
  border-top: 1px solid var(--color-border, #E5E5E5);
  -webkit-overflow-scrolling: touch;
}
.bft-table {
  width: 100%;
  min-width: 640px;
  border-collapse: collapse;
  background: var(--color-paper, #FFFFFF);
}
.bft-table th {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-2xs, 10px);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-muted, #666);
  background: var(--color-soft, #FAFAFA);
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border, #E5E5E5);
  white-space: nowrap;
}
.bft-table td {
  padding: 9px 12px;
  border-top: 1px solid var(--color-border, #E5E5E5);
  font-size: var(--fs-md, 13px);
  line-height: 1.5;
  color: var(--color-ink, #0A0A0A);
  vertical-align: top;
}
.bft-table tbody tr:first-child td { border-top: none; }
.bft-table tbody tr:hover td { background: var(--color-soft, #FAFAFA); }
.col-statement { min-width: 260px; }

.statement-text {
  display: block;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cell-prob { white-space: nowrap; }
.prob-main {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-base, 14px);
  font-weight: 700;
}
.prob-interval {
  display: block;
  margin-top: 2px;
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-2xs, 10px);
  color: var(--color-faint, #999);
  cursor: default;
}

/* 置信度 chip：low/medium/high → muted/accent/ok（badge 用 pill 圆角词汇）。 */
.conf-chip {
  display: inline-block;
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-2xs, 10px);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 9px;
  border: 1px solid var(--color-border, #E5E5E5);
  border-radius: var(--radius-pill, 999px);
  color: var(--color-muted, #666);
  background: var(--color-paper, #FFFFFF);
  white-space: nowrap;
}
.chip-medium {
  color: var(--color-accent, #FF4500);
  border-color: var(--color-accent, #FF4500);
  background: var(--color-accent-soft, #FFF6F2);
}
.chip-high {
  color: var(--color-ok, #16A34A);
  border-color: var(--color-ok, #16A34A);
  background: var(--color-ok-soft, #F0FDF4);
}

.cell-market { white-space: nowrap; }
.market-implied {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-md, 13px);
  color: var(--color-ink, #0A0A0A);
}
.market-link {
  color: var(--color-accent, #FF4500);
  text-decoration: none;
  border-bottom: 1px solid var(--color-accent-soft, #FFF6F2);
  transition: border-color var(--dur-1, 120ms) var(--ease, ease);
}
.market-link:hover { border-bottom-color: var(--color-accent, #FF4500); }
.market-delta {
  margin-left: 8px;
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-xs, 11px);
  font-weight: 700;
}
.delta-pos { color: var(--color-ok, #16A34A); }
.delta-neg { color: var(--color-err, #B91C1C); }
.delta-zero { color: var(--color-muted, #666); }

/* market_influence 印章徽标：锚点被弹出后徽标仍在（tooltip 讲明 prior→revised 与状态）。 */
.influence-badge {
  display: inline-block;
  margin-left: 8px;
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-2xs, 10px);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 1px 7px;
  border: 1px solid var(--color-warn, #D97706);
  border-radius: var(--radius-pill, 999px);
  color: var(--color-warn, #D97706);
  background: var(--color-paper, #FFFFFF);
  cursor: default;
  white-space: nowrap;
}

.cell-empty { color: var(--color-faint, #999); }
</style>
