<template>
  <div class="settings-scrim" @click.self="$emit('close')">
    <div class="settings-modal">
      <div class="modal-head">
        <span class="modal-title">◇ {{ L('设置', 'Settings') }}</span>
        <button class="modal-close" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">
        <!-- 语言 -->
        <section class="block">
          <div class="block-label">{{ L('界面语言', 'Interface language') }}</div>
          <div class="seg">
            <button :class="{active: locale==='zh'}" @click="setLocale('zh')">中文</button>
            <button :class="{active: locale==='en'}" @click="setLocale('en')">English</button>
          </div>
        </section>

        <!-- 模型提供方 -->
        <section class="block">
          <div class="block-label">{{ L('模型提供方', 'Model provider') }}</div>
          <p class="hint">
            {{ L('切换对新发起的推演生效（运行中的不受影响）。Claude / Codex / MiniMax / DeepSeek / 通义千问 / GLM 都有各自的深度研究模型；OpenAI/Kimi 的研究阶段回退至 Claude。',
                 'Applies to new runs (in-flight runs are unaffected). Claude, Codex, MiniMax, DeepSeek, Qwen and GLM each drive their own deep-research model; OpenAI/Kimi fall back to Claude for the research stage.') }}
          </p>

          <div v-if="loadingInfo" class="loading">{{ L('加载中…', 'Loading…') }}</div>
          <div v-else class="provider-list">
            <label v-for="p in providers" :key="p.id" class="provider-card" :class="{ active: selected===p.id }">
              <input type="radio" :value="p.id" v-model="selected" />
              <div class="provider-main">
                <div class="provider-name">
                  {{ p.label }}
                  <span v-if="p.id===current" class="cur-pill">{{ L('当前', 'current') }}</span>
                </div>
                <div class="provider-sub">
                  {{ p.needs_key ? L('需要 API Key', 'Requires API key') : L('使用本机 CLI 订阅，无需 Key', 'Uses local CLI subscription, no key') }}
                  · {{ L('研究', 'research') }}: {{ deerflowModelFor(p.id) }}
                </div>
              </div>
            </label>
          </div>

          <!-- 需要 Key 的提供方：填写 Key（可选 base/model 高级项） -->
          <div v-if="selectedNeedsKey" class="key-area">
            <label class="field-label">API Key
              <span v-if="selected===current && hasApiKey" class="faint">（{{ L('已配置，可留空沿用', 'configured — leave blank to keep') }}）</span>
            </label>
            <input v-model="apiKey" type="password" class="text-input"
              :placeholder="keyPlaceholder" autocomplete="off" />
            <details class="advanced">
              <summary>{{ L('高级（可选）', 'Advanced (optional)') }}</summary>
              <label class="field-label">Base URL</label>
              <input v-model="baseUrl" type="text" class="text-input" :placeholder="defaultBase" />
              <label class="field-label">{{ L('模型名', 'Model name') }}</label>
              <input v-model="model" type="text" class="text-input" :placeholder="defaultModel" />
            </details>
          </div>

          <!-- 连通性测试：API 提供方验证 Key，CLI 提供方检查本机 CLI。不持久化任何配置。 -->
          <div class="test-row">
            <button class="test-btn" :disabled="testing || !canTest" @click="testConnection">
              {{ testing ? L('测试中…', 'Testing…') : L('测试连接', 'Test connection') }}
            </button>
            <span v-if="testResult" class="test-result" :class="testResult.ok ? 'ok' : 'err'">
              <template v-if="testResult.ok">
                ✓ {{ L('连接成功', 'Connected') }}<template v-if="testResult.latency_ms"> · {{ testResult.latency_ms }}ms</template><template v-if="testResult.model"> · {{ testResult.model }}</template><template v-if="testResult.detail"> · {{ testResult.detail }}</template>
              </template>
              <template v-else>✗ {{ testResult.error }}</template>
            </span>
          </div>
        </section>

        <p v-if="error" class="err">{{ error }}</p>
        <p v-if="okMsg" class="ok">{{ okMsg }}</p>
      </div>

      <div class="modal-foot">
        <button class="ghost-btn" @click="$emit('close')">{{ L('取消', 'Cancel') }}</button>
        <button class="primary-btn" :disabled="saving || !canSave" @click="save">
          {{ saving ? L('保存中…', 'Saving…') : L('应用', 'Apply') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getLlmSettings, setLlmSettings, testLlmSettings } from '../../api/settings'
import { locale, setLocale, L } from '../../i18n'

const emit = defineEmits(['close', 'changed'])

const loadingInfo = ref(true)
const providers = ref([])
const current = ref('')
const hasApiKey = ref(false)
const selected = ref('')
const apiKey = ref('')
const baseUrl = ref('')
const model = ref('')
const saving = ref(false)
const error = ref('')
const okMsg = ref('')
const testing = ref(false)
const testResult = ref(null)

const DEERFLOW_MAP = {
  'claude-cli': 'claude', 'codex-cli': 'codex', openai: 'claude', kimi: 'claude',
  minimax: 'minimax', deepseek: 'deepseek', qwen: 'qwen', glm: 'glm'
}
const DEFAULTS = {
  openai: { base: 'https://api.openai.com/v1', model: 'gpt-4o-mini', ph: 'sk-…' },
  kimi: { base: 'https://api.kimi.com/coding/v1', model: 'kimi-for-coding', ph: 'sk-…' },
  minimax: { base: 'https://api.minimaxi.com/v1', model: 'MiniMax-M3', ph: 'sk-cp-…' },
  deepseek: { base: 'https://api.deepseek.com/v1', model: 'deepseek-chat', ph: 'sk-…' },
  qwen: { base: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', ph: 'sk-…' },
  glm: { base: 'https://api.z.ai/api/paas/v4', model: 'glm-4.6', ph: '••••.••••' }
}
function deerflowModelFor(id) { return DEERFLOW_MAP[id] || 'claude' }

const selectedMeta = computed(() => providers.value.find(p => p.id === selected.value) || {})
const selectedNeedsKey = computed(() => !!selectedMeta.value.needs_key)
const defaultBase = computed(() => (DEFAULTS[selected.value] || {}).base || '')
const defaultModel = computed(() => (DEFAULTS[selected.value] || {}).model || '')
const keyPlaceholder = computed(() => (DEFAULTS[selected.value] || {}).ph || 'API key')
const canSave = computed(() => {
  if (!selected.value) return false
  if (selectedNeedsKey.value && !(selected.value === current.value && hasApiKey.value) && !apiKey.value.trim()) return false
  return true
})
// 可测试 = 可保存的同一约束（CLI 提供方永远可测；API 提供方需要 Key 或已配置的沿用 Key）
const canTest = computed(() => canSave.value)

async function load() {
  loadingInfo.value = true
  error.value = ''
  try {
    const res = await getLlmSettings()
    const d = res.data || {}
    providers.value = d.providers || []
    current.value = d.current || ''
    hasApiKey.value = !!d.has_api_key
    selected.value = d.current || (providers.value[0] && providers.value[0].id) || ''
  } catch (e) {
    error.value = (e && e.message) || 'Failed to load settings'
  } finally {
    loadingInfo.value = false
  }
}

async function testConnection() {
  if (!canTest.value || testing.value) return
  testing.value = true
  testResult.value = null
  try {
    const payload = { provider: selected.value }
    if (selectedNeedsKey.value) {
      if (apiKey.value.trim()) payload.api_key = apiKey.value.trim()
      if (baseUrl.value.trim()) payload.base_url = baseUrl.value.trim()
      if (model.value.trim()) payload.model = model.value.trim()
    }
    const res = await testLlmSettings(payload)
    testResult.value = res.data || { ok: false, error: 'Empty response' }
  } catch (e) {
    testResult.value = { ok: false, error: (e && e.message) || 'Test failed' }
  } finally {
    testing.value = false
  }
}

async function save() {
  if (!canSave.value || saving.value) return
  saving.value = true; error.value = ''; okMsg.value = ''
  try {
    const payload = { provider: selected.value }
    if (selectedNeedsKey.value) {
      if (apiKey.value.trim()) payload.api_key = apiKey.value.trim()
      if (baseUrl.value.trim()) payload.base_url = baseUrl.value.trim()
      if (model.value.trim()) payload.model = model.value.trim()
    }
    const res = await setLlmSettings(payload)
    const d = res.data || {}
    current.value = d.current || selected.value
    hasApiKey.value = !!d.has_api_key
    apiKey.value = ''
    okMsg.value = L('已切换到 ', 'Switched to ') + (selectedMeta.value.label || selected.value)
    emit('changed', d)
  } catch (e) {
    error.value = (e && e.message) || 'Failed to apply'
  } finally {
    saving.value = false
  }
}

// 切换提供方时清掉上一次的测试结果，避免误读为新选择的状态
watch(selected, () => { testResult.value = null })

onMounted(load)
</script>

<style scoped>
.settings-scrim { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 60; display: flex; align-items: center; justify-content: center; }
.settings-modal {
  width: 560px; max-width: 92vw; max-height: 88vh; background: #fff; border: 1px solid #000;
  display: flex; flex-direction: column; box-shadow: 0 24px 64px rgba(0,0,0,.3);
  --orange: #FF4500; --border: #E5E5E5; --mono: 'JetBrains Mono', monospace;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}
.modal-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--border); }
.modal-title { font-family: var(--mono); font-weight: 700; font-size: .95rem; }
.modal-close { background: none; border: none; font-size: 1.1rem; cursor: pointer; }
.modal-body { padding: 20px; overflow-y: auto; }
.block { margin-bottom: 24px; }
.block-label { font-family: var(--mono); font-size: .74rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
.hint { font-size: .78rem; color: #888; line-height: 1.6; margin-bottom: 14px; }
.seg { display: inline-flex; border: 1px solid #DDD; }
.seg button { background: #fff; border: none; border-right: 1px solid #eee; padding: 8px 18px; font-family: var(--mono); font-size: .82rem; cursor: pointer; color: #555; }
.seg button:last-child { border-right: none; }
.seg button.active { background: #000; color: #fff; }
.loading { color: #999; font-family: var(--mono); font-size: .82rem; }
.provider-list { display: flex; flex-direction: column; gap: 8px; }
.provider-card { display: flex; align-items: center; gap: 12px; border: 1px solid var(--border); padding: 12px 14px; cursor: pointer; transition: border-color .15s; }
.provider-card:hover { border-color: #bbb; }
.provider-card.active { border-color: var(--orange); background: #FFF8F5; }
.provider-card input { accent-color: var(--orange); }
.provider-main { flex: 1; }
.provider-name { font-weight: 600; font-size: .92rem; display: flex; align-items: center; gap: 8px; }
.cur-pill { background: #16a34a; color: #fff; font-family: var(--mono); font-size: .6rem; padding: 1px 6px; border-radius: 8px; }
.provider-sub { font-size: .74rem; color: #888; margin-top: 3px; font-family: var(--mono); }
.key-area { margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 14px; }
.field-label { display: block; font-family: var(--mono); font-size: .72rem; color: #888; margin: 10px 0 5px; }
.faint { color: #bbb; }
.text-input { width: 100%; border: 1px solid #DDD; padding: 9px 11px; font-family: var(--mono); font-size: .82rem; outline: none; }
.text-input:focus { border-color: var(--orange); }
.advanced { margin-top: 8px; }
.advanced summary { font-family: var(--mono); font-size: .74rem; color: #888; cursor: pointer; }
.modal-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 14px 20px; border-top: 1px solid var(--border); }
.primary-btn { background: var(--orange); color: #fff; border: none; padding: 10px 20px; font-family: var(--mono); font-weight: 700; cursor: pointer; }
.primary-btn:disabled { background: #E5E5E5; color: #999; cursor: not-allowed; }
.ghost-btn { background: #fff; border: 1px solid #DDD; padding: 10px 18px; font-family: var(--mono); cursor: pointer; }
.err { color: var(--orange); font-family: var(--mono); font-size: .8rem; margin-top: 12px; }
.ok { color: #16a34a; font-family: var(--mono); font-size: .8rem; margin-top: 12px; }
.test-row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.test-btn { background: #fff; border: 1px solid #000; padding: 8px 16px; font-family: var(--mono); font-size: .78rem; font-weight: 700; cursor: pointer; }
.test-btn:hover:not(:disabled) { background: #000; color: #fff; }
.test-btn:disabled { border-color: #DDD; color: #999; cursor: not-allowed; }
.test-result { font-family: var(--mono); font-size: .76rem; line-height: 1.5; word-break: break-word; }
.test-result.ok { color: #16a34a; margin-top: 0; }
.test-result.err { color: var(--orange); margin-top: 0; }
</style>
