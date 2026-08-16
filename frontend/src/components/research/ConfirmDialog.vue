<template>
  <div v-if="visible" class="cd-scrim" @click.self="choose(false)" @keydown.esc="choose(false)">
    <div class="cd-modal" role="alertdialog" aria-modal="true" :aria-label="title">
      <div class="cd-head">
        <span class="cd-title">◇ {{ title }}</span>
        <button type="button" class="cd-close" :aria-label="L('关闭', 'Close')" @click="choose(false)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="cd-body">
        <p>{{ message }}</p>
      </div>
      <div class="cd-foot">
        <button type="button" class="cd-ghost" @click="choose(false)">
          {{ cancelLabel || L('取消', 'Cancel') }}
        </button>
        <button ref="confirmBtn" type="button" class="cd-confirm" :class="{ danger }" @click="choose(true)">
          {{ confirmLabel || L('确定', 'Confirm') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * In-app replacement for window.confirm(), styled after SettingsMenu's
 * scrim/modal pattern. Promise-based:
 *
 *   const ok = await confirmDialog.value.open({
 *     title: L('取消推演', 'Cancel run'),
 *     message: L('…', '…'),
 *     danger: true            // destructive action → red confirm button
 *   })
 */
import { ref, nextTick } from 'vue'
import { L } from '../../i18n'

const visible = ref(false)
const title = ref('')
const message = ref('')
const confirmLabel = ref('')
const cancelLabel = ref('')
const danger = ref(false)
const confirmBtn = ref(null)
let resolver = null

function open(opts = {}) {
  // A second open() while one is pending resolves the pending one as "no".
  if (resolver) { resolver(false); resolver = null }
  title.value = opts.title || L('请确认', 'Please confirm')
  message.value = opts.message || ''
  confirmLabel.value = opts.confirmLabel || ''
  cancelLabel.value = opts.cancelLabel || ''
  danger.value = !!opts.danger
  visible.value = true
  nextTick(() => { if (confirmBtn.value) confirmBtn.value.focus() })
  return new Promise(resolve => { resolver = resolve })
}

function choose(ok) {
  visible.value = false
  const r = resolver
  resolver = null
  if (r) r(!!ok)
}

defineExpose({ open })
</script>

<style scoped>
.cd-scrim {
  position: fixed; inset: 0; background: rgba(0, 0, 0, .4); z-index: 70;
  display: flex; align-items: center; justify-content: center;
}
.cd-modal {
  width: 440px; max-width: 92vw; max-height: 88vh; background: var(--color-paper, #fff);
  border: 1px solid var(--color-ink, #000); display: flex; flex-direction: column;
  box-shadow: 0 24px 64px rgba(0, 0, 0, .3);
  font-family: var(--font-sans, 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif);
  color: var(--color-ink, #000);
}
.cd-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px; border-bottom: 1px solid var(--color-border, #E5E5E5);
}
.cd-title { font-family: var(--font-mono, monospace); font-weight: 700; font-size: .9rem; }
.cd-close {
  background: none; border: none; cursor: pointer; color: inherit;
  display: flex; align-items: center; justify-content: center; padding: 4px;
}
.cd-body { padding: 18px; overflow-y: auto; }
.cd-body p { margin: 0; font-size: .88rem; line-height: 1.7; color: #333; }
.cd-foot {
  display: flex; justify-content: flex-end; gap: 10px;
  padding: 12px 18px; border-top: 1px solid var(--color-border, #E5E5E5);
}
.cd-ghost {
  background: var(--color-paper, #fff); border: 1px solid #DDD; padding: 9px 16px;
  font-family: var(--font-mono, monospace); font-size: .8rem; cursor: pointer; color: inherit;
}
.cd-ghost:hover { border-color: var(--color-ink, #000); }
.cd-confirm {
  background: var(--color-ink, #000); color: #fff; border: none; padding: 9px 18px;
  font-family: var(--font-mono, monospace); font-size: .8rem; font-weight: 700; cursor: pointer;
}
.cd-confirm.danger { background: var(--color-err, #B91C1C); }
.cd-confirm:hover { opacity: .88; }
</style>
