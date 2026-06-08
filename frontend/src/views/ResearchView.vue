<template>
  <div class="research-container">
    <!-- 顶部导航 -->
    <nav class="navbar">
      <div class="nav-brand" @click="goHome" style="cursor:pointer">DeepResearch<span class="brand-accent">Forecast</span></div>
      <div class="nav-links">
        <span class="nav-tag">{{ L('STEP 0 · 深度研究 → 模拟 → 预测', 'STEP 0 · Research → Simulate → Forecast') }}</span>
        <button class="nav-icon-btn" :title="L('界面语言','Language')" @click="toggleLocale">{{ locale === 'en' ? '中' : 'EN' }}</button>
        <button class="nav-icon-btn" :title="L('设置','Settings')" @click="showSettings = true">⚙</button>
        <button class="nav-hist-btn" @click="showHistory = !showHistory">{{ L('历史推演','History') }} ☰</button>
      </div>
    </nav>

    <!-- 历史抽屉 -->
    <transition name="drawer">
      <div v-if="showHistory" class="history-drawer">
        <div class="drawer-head">
          <span>{{ L('历史推演','Run history') }}</span>
          <button class="drawer-close" @click="showHistory = false">✕</button>
        </div>
        <PipelineHistory :active-id="pipelineId" @select="selectPipeline" />
      </div>
    </transition>
    <div v-if="showHistory" class="drawer-scrim" @click="showHistory = false"></div>

    <!-- 设置 -->
    <SettingsMenu v-if="showSettings" @close="showSettings = false" @changed="onProviderChanged" />

    <div class="main-content">
      <!-- ====== 输入阶段 ====== -->
      <section v-if="!pipelineId" class="setup-section">
        <div class="tag-row">
          <span class="orange-tag">{{ L('一句话 · 从调研到推演', 'One prompt · research to forecast') }}</span>
          <span class="version-text">/ DeerFlow × OASIS</span>
        </div>
        <h1 class="main-title">
          {{ L('输入一个问题', 'Ask one question') }}<br>
          <span class="gradient-text">{{ L('自动调研、构建世界、推演未来', 'auto-research, build a world, simulate the future') }}</span>
        </h1>
        <p class="lead">
          {{ L('深度研究 Agent（DeerFlow）联网搜集资料、提取关键参与者并生成研究档案；据此构建知识图谱、生成数字人设、运行多智能体群体模拟（OASIS 双平台），最终由报告 Agent 产出可交互的预测报告。',
               'A deep-research agent (DeerFlow) searches the web, extracts the key actors and builds a research dossier; the system then constructs a knowledge graph, generates digital personas, runs a multi-agent population simulation (OASIS, dual-platform), and a report agent synthesizes an interactive forecast.') }}
        </p>

        <div class="console-box">
          <div class="console-section">
            <div class="console-header"><span class="console-label">&gt;_ {{ L('研究 / 预测问题', 'Research / forecast question') }}</span></div>
            <div class="input-wrapper">
              <textarea v-model="prompt" class="code-input" rows="6" :disabled="starting"
                :placeholder="L('// 例：预判2035年前全球电动汽车市场的发展趋势', '// e.g. Forecast global EV market trends through 2035')"></textarea>
            </div>
            <div class="examples">
              <span class="ex-label">{{ L('示例：','Examples:') }}</span>
              <button v-for="(ex, i) in exampleList" :key="i" class="ex-chip" @click="prompt = ex" :disabled="starting">{{ exShort(ex) }}</button>
            </div>
          </div>

          <div class="console-divider"><span>{{ L('参数','Parameters') }}</span></div>

          <div class="console-section params-row">
            <div class="param">
              <label>{{ L('模式','Mode') }}</label>
              <div class="seg">
                <button :class="{active: mode==='full'}" @click="mode='full'" :disabled="starting">{{ L('完整管线','Full pipeline') }}</button>
                <button :class="{active: mode==='research_only'}" @click="mode='research_only'" :disabled="starting">{{ L('仅研究','Research only') }}</button>
              </div>
            </div>
            <div class="param">
              <label>{{ L('研究深度','Research depth') }}</label>
              <div class="seg">
                <button v-for="d in depths" :key="d" :class="{active: depth===d}" @click="depth=d" :disabled="starting">{{ depthLabel(d) }}</button>
              </div>
            </div>
            <div class="param" v-if="mode==='full'">
              <label>{{ L('模拟轮数（越多越丰富）','Simulation rounds (more = richer)') }}</label>
              <input v-model.number="maxRounds" type="number" min="1" :placeholder="L('留空=按时长自动','blank = auto')" class="num-input" :disabled="starting"/>
            </div>
          </div>

          <div class="console-section btn-section">
            <button class="start-engine-btn" @click="start" :disabled="!canStart || starting">
              <span v-if="!starting">{{ mode==='full' ? L('启动 研究 + 模拟 + 预测','Run research + simulate + forecast') : L('启动深度研究','Run deep research') }}</span>
              <span v-else>{{ L('初始化中…','Initializing…') }}</span>
              <span class="btn-arrow">→</span>
            </button>
            <p v-if="error" class="err">{{ error }}</p>
          </div>
        </div>
      </section>

      <!-- ====== 运行 / 结果阶段 ====== -->
      <section v-else class="run-section">
        <div class="run-header">
          <div>
            <div class="console-label">{{ L('管线','Pipeline') }} {{ pipelineId }}</div>
            <h2 class="run-title">{{ statusTitle }}</h2>
          </div>
          <div class="run-actions">
            <button class="ghost-btn" @click="showHistory = true">{{ L('历史','History') }}</button>
            <button class="ghost-btn" @click="reset">＋ {{ L('新建','New') }}</button>
          </div>
        </div>

        <div class="run-layout">
          <aside class="rail">
            <StageTimeline :stages="stages" :current-stage="currentStage" :mode="mode" :global-progress="globalProgress" />
          </aside>

          <div class="workspace">
            <div class="tabbar">
              <button v-for="t in tabs" :key="t.key"
                class="tab" :class="{ active: activeTab===t.key, disabled: !t.enabled }"
                :disabled="!t.enabled" @click="pickTab(t.key)">
                {{ t.label }}<span v-if="t.badge" class="tab-badge">{{ t.badge }}</span>
              </button>
            </div>

            <div class="tab-body">
              <ResearchConsole v-show="activeTab==='log'" :log-lines="logLines" />
              <DossierViewer v-show="activeTab==='dossier'" :dossier="dossier" />
              <div v-show="activeTab==='graph'" class="graph-wrap" :class="{ max: graphMax }">
                <GraphPanel v-if="graphData" :graph-data="graphData" :loading="graphLoading"
                  @refresh="fetchGraph(true)" @toggle-maximize="graphMax = !graphMax" />
                <div v-else class="lazy-empty">
                  <span v-if="graphLoading">{{ L('加载知识图谱…','Loading knowledge graph…') }}</span>
                  <span v-else-if="graphId">
                    <button class="primary-btn" @click="fetchGraph(true)">{{ L('加载知识图谱','Load knowledge graph') }} →</button>
                  </span>
                  <span v-else>{{ L('知识图谱构建完成后可在此查看…','The knowledge graph will appear here once built…') }}</span>
                </div>
              </div>
              <SimulationView v-show="activeTab==='sim'" :simulation-id="simulationId" />
              <ForecastReport v-show="activeTab==='report'" :report-id="reportId" />
            </div>
          </div>
        </div>

        <p v-if="error" class="err run-err">{{ error }}</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { runPipeline, getPipelineStatus, getProgressLog, getDossier } from '../api/research'
import { getGraphData } from '../api/graph'
import { locale, setLocale, L } from '../i18n'
import StageTimeline from '../components/research/StageTimeline.vue'
import ResearchConsole from '../components/research/ResearchConsole.vue'
import DossierViewer from '../components/research/DossierViewer.vue'
import SimulationView from '../components/research/SimulationView.vue'
import ForecastReport from '../components/research/ForecastReport.vue'
import PipelineHistory from '../components/research/PipelineHistory.vue'
import SettingsMenu from '../components/research/SettingsMenu.vue'
import GraphPanel from '../components/GraphPanel.vue'

const router = useRouter()
const ACTIVE_PIPELINE_KEY = 'mirofish_active_pipeline'

// —— 输入 ——
const prompt = ref('')
const mode = ref('full')
const depth = ref('deep')
const depths = ['quick', 'standard', 'deep']
function depthLabel(d) {
  return { quick: L('快速', 'Quick'), standard: L('标准', 'Standard'), deep: L('深度', 'Deep') }[d] || d
}
const maxRounds = ref(null)
const starting = ref(false)
const error = ref('')
const showSettings = ref(false)

const EXAMPLES = [
  ['预判2035年前全球电动汽车市场的发展趋势', 'Forecast global EV market trends through 2035'],
  ['俄乌战争最可能在何时、以何种方式收场？', 'How and when will the Russia–Ukraine war end?'],
  ['特朗普第二任期下美欧关系将如何演变？', 'How will US–EU relations evolve under Trump II?']
]
const exampleList = computed(() => EXAMPLES.map(e => (locale.value === 'en' ? e[1] : e[0])))
function exShort(ex) { return ex.length > 24 ? ex.slice(0, 24) + '…' : ex }

// —— 运行态 ——
const pipelineId = ref('')
const status = ref('running')
const globalProgress = ref(0)
const currentStage = ref('')
const stages = ref({})
const graphId = ref('')
const simulationId = ref('')
const reportId = ref('')
const logLines = ref([])
const dossier = ref(null)
const showHistory = ref(false)

// —— 标签 / 图谱 ——
const activeTab = ref('log')
const userPickedTab = ref(false)
const graphData = ref(null)
const graphLoading = ref(false)
const graphMax = ref(false)

let pollTimer = null
let dossierFetched = false

function stageLabel(name) {
  return {
    research: L('深度研究', 'Research'), ontology: L('本体生成', 'Ontology'), graph: L('知识图谱', 'Graph'),
    prepare: L('环境搭建', 'Prepare'), run: L('群体模拟', 'Simulate'), report: L('预测报告', 'Forecast')
  }[name] || name
}

const canStart = computed(() => prompt.value.trim().length > 0)
const statusTitle = computed(() => {
  if (status.value === 'completed') return mode.value === 'research_only' ? L('研究完成', 'Research complete') : L('推演完成 · 预测就绪', 'Done · forecast ready')
  if (status.value === 'failed') return L('运行失败', 'Run failed')
  return currentStage.value ? `${L('运行中','Running')} · ${stageLabel(currentStage.value)}` : L('运行中…', 'Running…')
})

const tabs = computed(() => {
  const researchDone = (dossier.value && dossier.value.has_report) || (stages.value.research && stages.value.research.status === 'completed')
  const actorCount = dossier.value && dossier.value.actors && dossier.value.actors.actors ? dossier.value.actors.actors.length : 0
  return [
    { key: 'log', label: L('实时日志', 'Live log'), enabled: true },
    { key: 'dossier', label: L('研究档案', 'Dossier'), enabled: !!researchDone, badge: actorCount || '' },
    { key: 'graph', label: L('知识图谱', 'Graph'), enabled: !!graphId.value },
    { key: 'sim', label: L('群体模拟', 'Simulation'), enabled: !!simulationId.value },
    { key: 'report', label: L('预测报告', 'Forecast'), enabled: !!reportId.value }
  ]
})

function toggleLocale() { setLocale(locale.value === 'en' ? 'zh' : 'en') }
function onProviderChanged() { /* provider applies to new runs; nothing to refresh here */ }

function pickTab(key) {
  const t = tabs.value.find(x => x.key === key)
  if (!t || !t.enabled) return
  userPickedTab.value = true
  activeTab.value = key
  if (key === 'graph' && !graphData.value && graphId.value) fetchGraph()
}

watch([() => reportId.value, () => (dossier.value && dossier.value.has_report)], () => {
  if (userPickedTab.value) return
  if (reportId.value) activeTab.value = 'report'
  else if (dossier.value && dossier.value.has_report) activeTab.value = 'dossier'
})

async function start() {
  if (!canStart.value || starting.value) return
  starting.value = true
  error.value = ''
  try {
    const res = await runPipeline({
      prompt: prompt.value.trim(),
      mode: mode.value,
      depth: depth.value,
      max_rounds: maxRounds.value || undefined
    })
    beginPipeline(res.data.pipeline_id)
  } catch (e) {
    error.value = e?.message || L('启动失败', 'Failed to start')
  } finally {
    starting.value = false
  }
}

function beginPipeline(id) {
  pipelineId.value = id
  try { localStorage.setItem(ACTIVE_PIPELINE_KEY, id) } catch (e) { /* noop */ }
  startPolling()
}

function startPolling() { stopPolling(); pollTimer = setInterval(poll, 2500); poll() }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

async function poll() {
  if (!pipelineId.value) return
  try {
    const [st, lg] = await Promise.all([
      getPipelineStatus(pipelineId.value),
      getProgressLog(pipelineId.value, 400)
    ])
    const d = st.data
    status.value = d.status
    globalProgress.value = d.global_progress || 0
    currentStage.value = d.current_stage || ''
    stages.value = d.stages || {}
    if (d.mode) mode.value = d.mode
    if (d.graph_id) graphId.value = d.graph_id
    if (d.simulation_id) simulationId.value = d.simulation_id
    if (d.report_id) reportId.value = d.report_id
    logLines.value = (lg.data && lg.data.lines) || []

    const researchDone = stages.value.research && stages.value.research.status === 'completed'
    if (researchDone && !dossierFetched) {
      dossierFetched = true
      try { dossier.value = (await getDossier(pipelineId.value)).data } catch (e) { /* noop */ }
    }

    if (status.value === 'completed' || status.value === 'failed') {
      stopPolling()
      try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (e) { /* noop */ }
      if (!dossier.value) { try { dossier.value = (await getDossier(pipelineId.value)).data } catch (e) { /* noop */ } }
      if (status.value === 'failed') {
        const failed = Object.values(stages.value).find(s => s.status === 'failed')
        error.value = (failed && failed.error) || d.error || L('运行失败', 'Run failed')
      }
    }
  } catch (e) {
    if (e && e.response && e.response.status === 404) {
      stopPolling()
      try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (_) { /* noop */ }
      status.value = 'failed'
      error.value = L('该管线不存在或已被清理', 'This pipeline no longer exists')
    }
  }
}

async function fetchGraph(force = false) {
  if (!graphId.value) return
  if (graphData.value && !force) return
  graphLoading.value = true
  try {
    const res = await getGraphData(graphId.value)
    graphData.value = res.data || { nodes: [], edges: [] }
  } catch (e) {
    error.value = L('图谱加载失败：', 'Graph load failed: ') + (e?.message || '')
  } finally {
    graphLoading.value = false
  }
}

function selectPipeline(id) {
  if (!id || id === pipelineId.value) { showHistory.value = false; return }
  resetState()
  showHistory.value = false
  beginPipeline(id)
}

function goHome() { router.push({ name: 'Home' }) }

function resetState() {
  stopPolling()
  status.value = 'running'; globalProgress.value = 0; currentStage.value = ''
  stages.value = {}; graphId.value = ''; simulationId.value = ''; reportId.value = ''
  logLines.value = []; dossier.value = null; dossierFetched = false
  graphData.value = null; graphLoading.value = false; graphMax.value = false
  activeTab.value = 'log'; userPickedTab.value = false; error.value = ''
}
function reset() {
  resetState()
  pipelineId.value = ''
  try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (e) { /* noop */ }
}

onMounted(() => {
  let saved = null
  try { saved = localStorage.getItem(ACTIVE_PIPELINE_KEY) } catch (e) { saved = null }
  if (saved) beginPipeline(saved)
})
onUnmounted(stopPolling)
</script>

<style scoped>
.research-container {
  min-height: 100vh; background: #fff;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  color: #000; --orange: #FF4500; --mono: 'JetBrains Mono', monospace; --border: #E5E5E5;
}
.navbar { height: 60px; background:#000; color:#fff; display:flex; justify-content:space-between; align-items:center; padding:0 40px; position:sticky; top:0; z-index:20; }
.nav-brand { font-family: var(--mono); font-weight:800; letter-spacing:.5px; font-size:1.05rem; }
.brand-accent { color: var(--orange); }
.nav-links { display:flex; align-items:center; gap:14px; }
.nav-tag { font-family: var(--mono); font-size:.74rem; color:#bbb; }
.nav-icon-btn { background:transparent; border:1px solid #444; color:#ddd; font-family:var(--mono); font-size:.74rem; width:30px; height:30px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.nav-icon-btn:hover { border-color:var(--orange); color:#fff; }
.nav-hist-btn { background:transparent; border:1px solid #444; color:#ddd; font-family:var(--mono); font-size:.74rem; padding:6px 12px; cursor:pointer; }
.nav-hist-btn:hover { border-color:var(--orange); color:#fff; }

.main-content { max-width:1480px; margin:0 auto; padding:40px; }

.history-drawer { position:fixed; top:0; right:0; width:380px; max-width:90vw; height:100vh; background:#fff; border-left:1px solid var(--border); z-index:40; box-shadow:-8px 0 32px rgba(0,0,0,.12); display:flex; flex-direction:column; }
.drawer-head { display:flex; justify-content:space-between; align-items:center; padding:18px 20px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:.85rem; height:60px; }
.drawer-close { background:none; border:none; font-size:1.1rem; cursor:pointer; }
.drawer-scrim { position:fixed; inset:0; background:rgba(0,0,0,.25); z-index:30; }
.drawer-enter-active,.drawer-leave-active { transition: transform .25s ease; }
.drawer-enter-from,.drawer-leave-to { transform: translateX(100%); }

.tag-row { display:flex; gap:15px; align-items:center; margin-bottom:20px; font-family:var(--mono); font-size:.8rem; }
.orange-tag { background:var(--orange); color:#fff; padding:4px 10px; font-weight:700; letter-spacing:1px; font-size:.75rem; }
.version-text { color:#999; }
.main-title { font-size:3rem; line-height:1.2; font-weight:500; letter-spacing:-1.5px; margin:0 0 22px; }
.gradient-text { background:linear-gradient(90deg,#000,#FF4500); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.lead { color:#666; line-height:1.85; max-width:820px; margin-bottom:34px; }
.console-box { border:1px solid #CCC; padding:8px; }
.console-section { padding:20px; }
.console-section.btn-section { padding-top:0; }
.console-header { display:flex; justify-content:space-between; margin-bottom:12px; font-family:var(--mono); font-size:.75rem; color:#666; }
.input-wrapper { border:1px solid #DDD; background:#FAFAFA; }
.code-input { width:100%; border:none; background:transparent; padding:18px; font-family:var(--mono); font-size:.9rem; line-height:1.6; resize:vertical; outline:none; min-height:130px; }
.examples { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:12px; }
.ex-label { font-family:var(--mono); font-size:.72rem; color:#999; }
.ex-chip { border:1px solid var(--border); background:#fff; font-size:.74rem; padding:5px 10px; cursor:pointer; color:#444; font-family:'Noto Sans SC',sans-serif; }
.ex-chip:hover { border-color:var(--orange); color:#000; }
.console-divider { display:flex; align-items:center; margin:6px 0; }
.console-divider::before,.console-divider::after { content:''; flex:1; height:1px; background:#EEE; }
.console-divider span { padding:0 15px; font-family:var(--mono); font-size:.7rem; color:#BBB; letter-spacing:1px; }
.params-row { display:flex; gap:32px; flex-wrap:wrap; }
.param label { display:block; font-family:var(--mono); font-size:.72rem; color:#888; margin-bottom:8px; }
.seg { display:flex; border:1px solid #DDD; }
.seg button { background:#fff; border:none; border-right:1px solid #eee; padding:8px 14px; font-family:var(--mono); font-size:.8rem; cursor:pointer; color:#555; }
.seg button:last-child { border-right:none; }
.seg button.active { background:#000; color:#fff; }
.num-input { border:1px solid #DDD; padding:8px 10px; font-family:var(--mono); font-size:.85rem; width:200px; outline:none; }
.start-engine-btn { width:100%; background:#000; color:#fff; border:none; padding:18px; font-family:var(--mono); font-weight:700; font-size:1.05rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer; transition:all .25s; letter-spacing:1px; }
.start-engine-btn:hover:not(:disabled) { background:var(--orange); transform:translateY(-2px); }
.start-engine-btn:disabled { background:#E5E5E5; color:#999; cursor:not-allowed; }
.err { color:var(--orange); font-family:var(--mono); font-size:.8rem; margin-top:12px; }
.run-err { margin-top:18px; }

.run-header { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:20px; }
.console-label { font-family:var(--mono); font-size:.72rem; color:#999; }
.run-title { font-size:1.7rem; font-weight:520; margin:6px 0 0; }
.run-actions { display:flex; gap:12px; }
.primary-btn { background:var(--orange); color:#fff; border:none; padding:12px 18px; font-family:var(--mono); font-weight:700; cursor:pointer; }
.ghost-btn { background:#fff; color:#000; border:1px solid #DDD; padding:10px 16px; font-family:var(--mono); font-size:.82rem; cursor:pointer; }
.ghost-btn:hover { border-color:var(--orange); }

.run-layout { display:grid; grid-template-columns:330px 1fr; gap:24px; align-items:start; }
.rail { position:sticky; top:84px; }
.workspace { min-width:0; }
.tabbar { display:flex; gap:4px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.tab { background:#fff; border:1px solid var(--border); border-bottom:none; padding:11px 18px; font-family:var(--mono); font-size:.8rem; cursor:pointer; color:#777; position:relative; top:1px; display:flex; align-items:center; gap:7px; }
.tab.active { color:#000; border-color:var(--border); border-bottom:2px solid var(--orange); background:#fff; font-weight:700; }
.tab.disabled { color:#ccc; cursor:not-allowed; background:#FAFAFA; }
.tab-badge { background:var(--orange); color:#fff; font-size:.62rem; padding:1px 6px; border-radius:8px; }
.tab-body { border:1px solid var(--border); border-top:none; min-height:560px; background:#fff; }
.graph-wrap { height:600px; }
.graph-wrap.max { height:calc(100vh - 220px); }
.lazy-empty { height:100%; display:flex; align-items:center; justify-content:center; color:#999; font-family:var(--mono); font-size:.85rem; padding:40px; }

@media (max-width: 1080px) {
  .run-layout { grid-template-columns:1fr; }
  .rail { position:static; }
  .main-title { font-size:2.2rem; }
}
</style>
