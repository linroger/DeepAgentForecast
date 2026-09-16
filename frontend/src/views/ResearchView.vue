<template>
  <div class="research-container">
    <!-- 顶部导航 -->
    <nav class="navbar" :inert="showHistory" :aria-label="L('主导航', 'Main navigation')">
      <button ref="brandButton" type="button" class="nav-brand" @click="goHome" :disabled="starting || restoringLaunch" :aria-label="L('DeepResearchForecast · 新建研究', 'DeepResearchForecast · New research')">DeepResearch<span class="brand-accent">Forecast</span></button>
      <div class="nav-links">
        <span class="nav-tag">{{ L('研究工作台', 'Research workspace') }}</span>
        <button class="nav-icon-btn" :title="L('界面语言','Language')" :aria-label="L('切换为英文', 'Switch to Chinese')" @click="toggleLocale">{{ locale === 'en' ? '中' : 'EN' }}</button>
        <button class="nav-icon-btn" :title="L('设置','Settings')" :aria-label="L('设置','Settings')" @click="showSettings = true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
        <button class="nav-hist-btn" :title="L('历史推演','History')" :aria-label="L('历史推演','History')" @click="showHistory = !showHistory">
          <span class="nav-hist-label">{{ L('历史推演','History') }}</span>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
            <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
      </div>
    </nav>

    <!-- 历史抽屉 -->
    <transition name="drawer" @before-enter="element => { element.inert = false }">
      <div v-if="showHistory" ref="historyDialog" class="history-drawer" role="dialog" aria-modal="true" aria-labelledby="history-title" tabindex="-1" @keydown="onHistoryKeydown">
        <div class="drawer-head">
          <h2 id="history-title">{{ L('历史推演','Run history') }}</h2>
          <button ref="historyClose" type="button" class="drawer-close" :aria-label="L('关闭','Close')" @click="showHistory = false">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <PipelineHistory :active-id="pipelineId" @select="selectPipeline" @deleted="onRunDeleted" />
      </div>
    </transition>
    <transition name="fade">
      <div v-if="showHistory" class="drawer-scrim" @click="showHistory = false"></div>
    </transition>

    <!-- 设置 -->
    <SettingsMenu v-if="showSettings" @close="showSettings = false" @changed="onProviderChanged" />

    <!-- 危险操作确认弹窗（替代 window.confirm） -->
    <ConfirmDialog ref="confirmDlg" />

    <main class="main-content" :inert="showHistory">
      <!-- ====== 输入阶段 ====== -->
      <section v-if="!pipelineId" class="setup-section">
        <header class="setup-heading">
          <p class="eyebrow">{{ L('从证据出发', 'Start with evidence') }}</p>
          <h1 class="main-title">{{ L('研究当下，探索未来。', 'Research today. Explore what comes next.') }}</h1>
          <p class="lead">{{ L('提出一个问题，将来源、关键参与者和情景推演连接成一份可追溯的预测报告。', 'Connect sources, key actors and simulated scenarios in a forecast you can trace back to the research.') }}</p>
        </header>

        <div class="setup-grid">
        <div class="console-box">
          <div class="console-section">
            <div class="console-header"><label for="research-question">{{ L('您想研究什么？', 'What would you like to understand?') }}</label></div>
            <p id="question-hint" class="field-hint">{{ L('描述主题、时间范围，以及您希望判断的结果。', 'Include the topic, time horizon and the outcome you want to assess.') }}</p>
            <div class="input-wrapper">
              <textarea id="research-question" v-model="prompt" class="code-input" rows="5" aria-describedby="question-hint" :disabled="launchFormLocked"
                :placeholder="L('例如：到2035年，全球电动汽车市场将如何变化？哪些因素可能改变其发展轨迹？', 'How could the global EV market evolve by 2035, and what might change its trajectory?')"></textarea>
            </div>
            <div class="examples">
              <span class="ex-label">{{ L('示例：','Examples:') }}</span>
              <button v-for="ex in exampleList" :key="ex.question" type="button" class="ex-chip" :title="ex.question" @click="prompt = ex.question" :disabled="launchFormLocked">{{ ex.label }}</button>
            </div>
          </div>

          <div class="console-divider"><span>{{ L('研究设置','Research settings') }}</span></div>

          <div class="console-section params-row">
            <div class="param">
              <span id="mode-label" class="param-label">{{ L('模式','Mode') }}</span>
              <div class="seg" role="group" aria-labelledby="mode-label">
                <button :class="{active: mode==='full'}" :aria-pressed="mode==='full'" @click="mode='full'" :disabled="launchFormLocked">{{ L('完整管线','Full pipeline') }}</button>
                <button :class="{active: mode==='research_only'}" :aria-pressed="mode==='research_only'" @click="mode='research_only'" :disabled="launchFormLocked">{{ L('仅研究','Research only') }}</button>
              </div>
            </div>
            <div class="param">
              <span id="depth-label" class="param-label">{{ L('研究深度','Research depth') }}</span>
              <div class="seg" role="group" aria-labelledby="depth-label">
                <button v-for="d in depths" :key="d" :class="{active: depth===d}" :aria-pressed="depth===d" @click="depth=d" :disabled="launchFormLocked">{{ depthLabel(d) }}</button>
              </div>
            </div>
            <div class="param rounds-param" v-if="mode==='full'">
              <label for="max-rounds">{{ L('模拟回合上限','Simulation round cap') }}</label>
              <input id="max-rounds" aria-describedby="rounds-hint" v-model.number="maxRounds" type="number" min="1" :placeholder="L('留空=按时长自动','blank = auto')" class="num-input" :disabled="launchFormLocked"/>
              <p id="rounds-hint" class="field-hint">{{ L('留空则自动设置。日历模式调整时间粒度，保留完整预测期。', 'Automatic when blank. Calendar mode adjusts granularity while keeping the full horizon.') }}</p>
            </div>
          </div>

          <!-- T5.5: 高级（研究语言 + 研究模型，每次运行覆盖） -->
          <button type="button" class="adv-toggle" :aria-expanded="showAdvanced" aria-controls="advanced-settings" @click="showAdvanced = !showAdvanced">
            <span class="adv-caret">{{ showAdvanced ? '▾' : '▸' }}</span>
            {{ L('高级设置','Advanced settings') }}
          </button>
          <div id="advanced-settings" v-show="showAdvanced" class="console-section params-row adv-row">
            <div class="param">
              <label for="research-language">{{ L('研究语言','Research language') }}</label>
              <select id="research-language" v-model="researchLanguage" class="adv-select" :disabled="launchFormLocked">
                <option v-for="o in LANGUAGE_OPTIONS" :key="o.v" :value="o.v">{{ locale==='en' ? o.en : o.zh }}</option>
              </select>
            </div>
            <div class="param">
              <label for="research-model">{{ L('研究模型','Research model') }}</label>
              <select id="research-model" v-model="researchModel" class="adv-select" :disabled="launchFormLocked">
                <option value="">{{ L('默认','Default') }}</option>
                <option v-for="m in DEERFLOW_MODELS" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
          </div>

          <div class="preflight-banner" :class="`preflight-${preflight.status}`" role="status" aria-live="polite" :aria-busy="preflight.status === 'checking'">
            <span class="readiness-mark" aria-hidden="true">{{ preflight.status === 'ready' ? '✓' : preflight.status === 'checking' ? '◌' : '!' }}</span>
            <div class="preflight-copy">
              <p class="pf-title">{{ preflightTitle }}</p>
              <p v-if="preflight.status === 'unavailable'" class="pf-detail">{{ L('暂时无法确认配置。请检查连接后重试。', 'Configuration could not be verified. Check your connection and try again.') }}</p>
              <p v-else-if="preflight.status === 'blocked' && !preflight.errors.length" class="pf-detail">{{ L('请检查设置，然后重试。', 'Review your settings, then try again.') }}</p>
              <ul v-if="preflight.errors.length" class="pf-list">
                <li v-for="(e, i) in preflight.errors" :key="i">{{ e }}</li>
              </ul>
            </div>
            <button v-if="preflight.status === 'unavailable' || preflight.status === 'blocked'" type="button" class="preflight-retry" @click="checkPreflight">{{ L('重试', 'Retry') }}</button>
          </div>

          <div class="console-section btn-section">
            <div v-if="launchIntent" class="launch-recovery" role="status" aria-live="polite">
              <p v-if="launchIntent.admission?.launch_status === 'abandoned'">{{ L('此请求已在启动前撤回。您可以新建请求并修改输入。', 'This request was retired before launch. Choose New to edit the inputs.') }}</p>
              <p v-else-if="launchIntent.admission?.launch_status === 'unavailable'">{{ L('已保存的管线状态不可用，但启动标识仍然保留。请再次检查，或明确新建另一请求。', 'The saved pipeline state is unavailable, but its launch identity is retained. Check again or deliberately create another request.') }}</p>
              <p v-else-if="launchIntent.admission?.recovery_required">{{ L('无法确认最初的启动结果。请打开已保存的运行查看当前状态，或再次检查启动结果。', 'The initial launch confirmation is unavailable. Open the saved run to inspect its current status, or check this launch again.') }}</p>
              <p v-else>{{ L('此问题已有保存的启动请求。请检查启动结果，或使用同一请求重试。', 'This question has a saved launch request. Check its outcome or retry the same request.') }}</p>
              <div class="launch-recovery-actions">
                <button class="ghost-btn" @click="checkSavedLaunch" :disabled="starting || restoringLaunch">{{ L('检查启动结果', 'Check launch') }}</button>
                <button v-if="!launchIntent.admission" class="ghost-btn" @click="retrySavedLaunch" :disabled="starting || restoringLaunch">{{ L('重试此启动请求', 'Retry this launch') }}</button>
                <button v-else-if="launchIntent.admission.pipeline_id && launchIntent.admission.launch_status !== 'unavailable'" class="ghost-btn" @click="openSavedLaunch" :disabled="starting || restoringLaunch">{{ L('打开已保存的运行', 'Open saved run') }}</button>
                <button v-if="!launchIntent.admission" class="ghost-btn" @click="discardSavedLaunch" :disabled="starting || restoringLaunch">{{ L('撤回尚未启动的请求', 'Discard unsubmitted launch') }}</button>
                <button v-if="launchIntent.admission" class="ghost-btn" @click="reset" :disabled="starting || restoringLaunch">{{ L('新建', 'New') }}</button>
              </div>
            </div>
            <button v-else class="start-engine-btn" @click="start" :disabled="!canStart || starting || restoringLaunch">
              <span v-if="!starting">{{ mode==='full' ? L('开始研究与预测','Start research & forecast') : L('启动深度研究','Run deep research') }}</span>
              <span v-else>{{ L('初始化中…','Initializing…') }}</span>
              <span class="btn-arrow" aria-hidden="true">→</span>
            </button>
            <p v-if="error" class="err">{{ error }}</p>
          </div>
        </div>

        <aside class="journey-card" aria-labelledby="journey-title">
          <p class="eyebrow">{{ L('从问题到报告', 'From question to insight') }}</p>
          <h2 id="journey-title">{{ L('一条连贯的研究路径', 'A connected research workflow') }}</h2>
          <ol class="journey-list">
            <li v-for="(step, i) in journeySteps" :key="step.key" :class="{ 'journey-optional': mode === 'research_only' && i > 0 }">
              <span class="journey-number" aria-hidden="true">{{ i + 1 }}</span>
              <div><h3>{{ step.label }}</h3><p>{{ step.description }}</p></div>
            </li>
          </ol>
          <div class="journey-note">
            <strong>{{ mode === 'research_only' ? L('先完成研究', 'Start with research') : L('保留证据与推演的区别', 'Evidence and scenarios, clearly separated') }}</strong>
            <p>{{ mode === 'research_only' ? L('在研究档案完成后审阅结果，再决定是否继续模拟与报告。', 'Review the completed dossier before choosing whether to continue into simulation and reporting.') : L('模拟用于探索可能的情景。结合来源、假设和不确定性，审阅最终结论。', 'Simulation explores possible scenarios. Review the sources, assumptions and uncertainty alongside the conclusions.') }}</p>
          </div>
        </aside>
        </div>
      </section>

      <!-- ====== 运行 / 结果阶段 ====== -->
      <section v-else class="run-section">
        <div class="run-header">
          <div>
            <div class="console-label">
              {{ L('管线','Pipeline') }}
              <button type="button" class="pid-chip" :title="pipelineId"
                :aria-label="L('复制管线 ID','Copy pipeline id')" @click="copyPipelineId">
                {{ shortPipelineId }}
                <span v-if="pidCopied" class="pid-copied">✓ {{ L('已复制','Copied') }}</span>
              </button>
            </div>
            <h2 class="run-title">{{ statusTitle }}</h2>
            <RunVitals :live="liveVitals" :status="status"
              :created-at="runTiming.createdAt" :updated-at="runTiming.updatedAt"
              :resumed-at="runTiming.resumedAt" />
          </div>
          <div class="run-actions">
            <button v-if="status === 'running'" class="ghost-btn cancel-btn" :disabled="cancelling" @click="cancel">
              {{ cancelling ? L('取消中…','Cancelling…') : L('取消','Cancel') }}
            </button>
            <button v-if="canResume" class="ghost-btn resume-btn" :disabled="resuming" @click="resume">
              {{ resuming ? L('恢复中…','Resuming…') : L('继续','Resume') }}
            </button>
            <button v-if="canContinue" class="ghost-btn resume-btn" :disabled="continuing" @click="continueToFull">
              {{ continuing ? L('继续中…','Continuing…') : L('继续完整管线 →','Continue to full pipeline →') }}
            </button>
            <button class="ghost-btn" @click="showHistory = true">{{ L('历史','History') }}</button>
            <button class="ghost-btn" @click="reset" :disabled="starting || restoringLaunch">＋ {{ L('新建','New') }}</button>
          </div>
        </div>

        <div class="run-prompt-card">
          <span class="run-prompt-label">{{ L('初始研究 / 预测问题', 'Initial research / forecast prompt') }}</span>
          <p>{{ runPrompt || L('正在载入原始问题…', 'Loading the original prompt…') }}</p>
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
              <ResearchConsole v-show="activeTab==='log'" :log-lines="logLines"
                :history-meta="logHistoryMeta" @refresh="refreshFullResearchLog" />
              <DossierViewer v-show="activeTab==='dossier'" :dossier="dossier"
                :pipeline-id="pipelineId" :editable="canContinue" @saved-continue="continueToFull" />
              <div v-show="activeTab==='graph'" class="graph-wrap" :class="{ max: graphMax }">
                <GraphPanel v-if="graphData" :graph-data="graphData" :loading="graphLoading"
                  :seed-actors="seedActorNames"
                  @refresh="fetchGraph(true)" @toggle-maximize="graphMax = !graphMax" />
                <div v-else class="lazy-empty">
                  <template v-if="graphLoading">
                    <span class="lazy-spinner" aria-hidden="true"></span>
                    <span>{{ L('加载知识图谱…','Loading knowledge graph…') }}</span>
                  </template>
                  <template v-else-if="graphId">
                    <span class="lazy-icon" aria-hidden="true">◇</span>
                    <button class="primary-btn" @click="fetchGraph(true)">{{ L('加载知识图谱','Load knowledge graph') }} →</button>
                  </template>
                  <template v-else>
                    <span class="lazy-icon" aria-hidden="true">◇</span>
                    <span>{{ L('知识图谱构建完成后可在此查看…','The knowledge graph will appear here once built…') }}</span>
                  </template>
                </div>
              </div>
              <SimulationView v-show="activeTab==='sim'" :simulation-id="simulationId" />
              <ForecastReport v-show="activeTab==='report'" :report-id="reportId" />
            </div>
          </div>
        </div>

        <p v-if="error" class="err run-err">{{ error }}</p>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { runPipeline, getLaunchIntent, abandonLaunchIntent, cancelPipeline, resumePipeline, getPipelineStatus, getProgressLog, getDossier, continuePipeline, getPreflight } from '../api/research'
import { createLaunchIntentController } from '../utils/launchIntent'
import { createPreflightController } from '../utils/preflightState'
import { getGraphData } from '../api/graph'
import { locale, setLocale, L } from '../i18n'
import {
  liveLogRevision,
  mergeProgressLines,
  needsFinalProgressSnapshot
} from '../utils/liveProgress'
import StageTimeline from '../components/research/StageTimeline.vue'
import RunVitals from '../components/research/RunVitals.vue'
import ResearchConsole from '../components/research/ResearchConsole.vue'
import DossierViewer from '../components/research/DossierViewer.vue'
import SimulationView from '../components/research/SimulationView.vue'
import ForecastReport from '../components/research/ForecastReport.vue'
import PipelineHistory from '../components/research/PipelineHistory.vue'
import SettingsMenu from '../components/research/SettingsMenu.vue'
import ConfirmDialog from '../components/research/ConfirmDialog.vue'
import GraphPanel from '../components/GraphPanel.vue'

const router = useRouter()
const ACTIVE_PIPELINE_KEY = 'drf_active_pipeline'
// One-time migration: earlier builds persisted under the old MiroFish key.
const LEGACY_PIPELINE_KEY = 'mirofish_active_pipeline'

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
const restoringLaunch = ref(true)
const launchIntent = ref(null)
const launchFormLocked = computed(() => starting.value || restoringLaunch.value || !!launchIntent.value)
let launchViewActive = true
const error = ref('')
const showSettings = ref(false)

// —— T5.5: 高级（每次运行覆盖研究语言/模型）——
const showAdvanced = ref(false)
const researchLanguage = ref('')   // ''=默认；'auto'/'Chinese'/'English'
const researchModel = ref('')      // ''=默认；7 个 DeerFlow 模型之一
const DEERFLOW_MODELS = ['claude', 'codex', 'minimax', 'deepseek', 'qwen', 'glm', 'kimi']
const LANGUAGE_OPTIONS = [
  { v: '', zh: '默认', en: 'Default' },
  { v: 'Chinese', zh: '中文', en: 'Chinese' },
  { v: 'English', zh: '英文', en: 'English' },
  { v: 'auto', zh: '自动', en: 'Auto' }
]

function restoreLaunchInputs(record) {
  if (!record) return
  const data = record.payload
  prompt.value = data.prompt
  mode.value = data.mode || 'full'
  depth.value = data.depth || 'deep'
  maxRounds.value = data.max_rounds ?? null
  researchLanguage.value = data.language || ''
  researchModel.value = data.model || ''
}

const launchController = createLaunchIntentController({
  submit: runPipeline,
  lookup: getLaunchIntent,
  abandon: abandonLaunchIntent,
  onChange: record => {
    launchIntent.value = record
    restoreLaunchInputs(record)
  }
})

// —— T5.6: 启动前就绪检查 ——
const preflight = ref({ status: 'checking', errors: [], mode: mode.value })
const preflightController = createPreflightController({
  request: getPreflight,
  onChange: value => { preflight.value = value }
})
function checkPreflight() { return preflightController.check(mode.value) }
const preflightTitle = computed(() => ({
  checking: L('正在检查配置…', 'Checking configuration…'),
  ready: L('配置就绪', 'Configuration ready'),
  blocked: L('启动前需要完善配置', 'Configuration needs attention'),
  unavailable: L('就绪检查暂不可用', 'Readiness check unavailable')
})[preflight.value.status])

// —— T6.2: research_only → 继续完整管线 ——
const continuing = ref(false)
const canContinue = computed(
  () => !!pipelineId.value && status.value === 'completed' && mode.value === 'research_only'
)
async function continueToFull() {
  if (!pipelineId.value || continuing.value) return
  continuing.value = true
  error.value = ''
  try {
    const res = await continuePipeline(pipelineId.value)
    const id = (res && res.data && res.data.pipeline_id) || pipelineId.value
    pipelineId.value = id
    mode.value = 'full'
    status.value = 'running'
    // The previous terminal snapshot's `live` block would mislead under a
    // "running" header (its elapsed is age-since-created); wait for fresh data.
    liveVitals.value = null
    try { localStorage.setItem(ACTIVE_PIPELINE_KEY, id) } catch (e) { /* noop */ }
    startPolling()
  } catch (e) {
    error.value = e?.message || L('继续失败', 'Continue failed')
  } finally {
    continuing.value = false
  }
}

const EXAMPLES = [
  { label: ['电动汽车市场', 'EV market'], question: ['预判2035年前全球电动汽车市场的发展趋势', 'Forecast global EV market trends through 2035'] },
  { label: ['冲突与外交', 'Conflict & diplomacy'], question: ['俄乌战争最可能在何时、以何种方式收场？', 'How and when will the Russia–Ukraine war end?'] },
  { label: ['跨大西洋关系', 'Transatlantic relations'], question: ['特朗普第二任期下美欧关系将如何演变？', 'How will US–EU relations evolve under Trump II?'] }
]
const exampleList = computed(() => EXAMPLES.map(example => ({
  label: example.label[locale.value === 'en' ? 1 : 0],
  question: example.question[locale.value === 'en' ? 1 : 0]
})))
const journeySteps = computed(() => [
  { key: 'research', label: L('深度研究', 'Research'), description: L('搜集来源，建立研究档案。', 'Gather sources and build the dossier.') },
  { key: 'ontology', label: L('本体结构', 'Ontology'), description: L('定义参与者、概念与关系。', 'Define actors, concepts and relationships.') },
  { key: 'graph', label: L('知识图谱', 'Knowledge graph'), description: L('连接证据，映射关键联系。', 'Connect evidence and map key relationships.') },
  { key: 'prepare', label: L('模拟准备', 'Prepare'), description: L('建立参与者画像与情景。', 'Build actor profiles and the scenario.') },
  { key: 'run', label: L('多智能体模拟', 'Simulate'), description: L('探索互动与可能的发展。', 'Explore interactions and possible developments.') },
  { key: 'report', label: L('预测报告', 'Report'), description: L('整合发现、来源与不确定性。', 'Synthesize findings, sources and uncertainty.') }
])

// —— 运行态 ——
const pipelineId = ref('')
const status = ref('running')
const globalProgress = ref(0)
const currentStage = ref('')
const stages = ref({})
const graphId = ref('')
const simulationId = ref('')
const reportId = ref('')
const runPrompt = ref('')
const logLines = ref([])
const logSourceCount = ref(0)
const logHistoryHydrated = ref(false)
const logHistoryFinalized = ref(false)
const logInitialSnapshotAttempted = ref(false)
const logFinalSnapshotAttempts = ref(0)
const logHistoryLoading = ref(false)
const logHistoryError = ref('')
const dossier = ref(null)
const showHistory = ref(false)
const historyDialog = ref(null)
const historyClose = ref(null)
const brandButton = ref(null)
let historyOpener = null
let previousBodyOverflow = null

function onHistoryKeydown(event) {
  const dialog = historyDialog.value
  if (!dialog) return
  // A history action may open its own confirmation. Keep that inner dialog's
  // keyboard scope and let it handle Escape without also closing history.
  const nestedDialog = dialog.querySelector('[role="alertdialog"]')
  if (event.key === 'Escape') {
    if (nestedDialog) return
    event.preventDefault()
    event.stopPropagation()
    showHistory.value = false
  } else if (event.key === 'Tab') {
    const scope = nestedDialog || dialog
    const focusable = [...scope.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
      .filter(element => element.getClientRects().length && !element.closest('[inert]'))
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (!first) {
      event.preventDefault()
      dialog.focus()
    } else if (event.shiftKey && (document.activeElement === first || !focusable.includes(document.activeElement))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (document.activeElement === last || !focusable.includes(document.activeElement))) {
      event.preventDefault()
      first.focus()
    }
  }
}
watch(showHistory, async open => {
  if (open) {
    historyOpener = document.activeElement
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    await nextTick()
    if (showHistory.value) historyClose.value?.focus()
  } else {
    // The leaving transition remains in the DOM briefly; remove it from focus.
    if (historyDialog.value) historyDialog.value.inert = true
    if (previousBodyOverflow !== null) document.body.style.overflow = previousBodyOverflow
    previousBodyOverflow = null
    await nextTick()
    if (!showHistory.value) {
      const target = historyOpener?.isConnected ? historyOpener : brandButton.value
      target?.focus()
    }
  }
})

// OBS-1/I-7: computed `live` block from the same status poll (may be absent —
// older servers, helper failure — and every field is individually nullable).
// runTiming carries the sibling timestamps RunVitals needs to derive a
// truthful final duration for terminal states.
const liveVitals = ref(null)
const runTiming = ref({ createdAt: '', updatedAt: '', resumedAt: '' })

// —— 标签 / 图谱 ——
const activeTab = ref('log')
const userPickedTab = ref(false)
const graphData = ref(null)
const graphLoading = ref(false)
const graphMax = ref(false)

// T5.3: 研究种子 actor 名（用于在图谱中高亮「研究确认 vs 模拟涌现」节点）
const seedActorNames = computed(() => {
  const a = dossier.value && dossier.value.actors
  const list = a && Array.isArray(a.actors) ? a.actors : []
  return list.map(x => x && x.name).filter(Boolean)
})

const logHistoryMeta = computed(() => ({
  sourceCount: logSourceCount.value,
  totalExact: logHistoryHydrated.value,
  finalized: logHistoryFinalized.value,
  loading: logHistoryLoading.value,
  error: logHistoryError.value
}))

let pollTimer = null
let dossierFetched = false
let pollFailures = 0
let connErrorShown = false
const POLL_FAILURE_THRESHOLD = 4 // 连续 4 次（约 10s）轮询失败才提示，容忍偶发抖动
const MAX_FINAL_SNAPSHOT_ATTEMPTS = 2

// FE-1: 自适应轮询 —— 状态未变化时把间隔从 2.5s 逐步拉到 12s，标签页隐藏时暂停，
// 重新可见时立即拉一次。降低长管线（数小时）空转时的后端/网络负载。
const POLL_MIN_MS = 2500
const POLL_MAX_MS = 12000
let pollActive = false          // 轮询是否处于活动状态（避免隐藏/终态后被定时器重新唤醒）
let pollDelay = POLL_MIN_MS     // 当前自适应间隔
let lastFingerprint = ''        // 上一次轮询观测到的状态指纹，用于判定"有无变化"
let visibilityHandler = null    // visibilitychange 监听器引用，便于卸载时移除
let pollInFlightGeneration = null // 同一 generation 不叠加；新管线不被旧慢请求阻塞
let pollGeneration = 0          // 丢弃上一条管线/上一轮轮询的迟到响应

// 计算一份轻量状态指纹。固定 400 行的 rolling tail 也必须看最新行内容；只看 length
// 会在控制台装满后误判为静止，把真正活跃的研究轮询退避到 12 秒。
function pollFingerprint() {
  const stageSig = Object.keys(stages.value || {})
    .map(k => `${k}:${stages.value[k] && stages.value[k].status}`)
    .join('|')
  return `${status.value}#${globalProgress.value}#${currentStage.value}#${stageSig}#${liveLogRevision(logLines.value)}`
}

function stageLabel(name) {
  return {
    research: L('深度研究', 'Research'), ontology: L('本体生成', 'Ontology'), graph: L('知识图谱', 'Graph'),
    prepare: L('环境搭建', 'Prepare'), run: L('群体模拟', 'Simulate'), report: L('预测报告', 'Forecast')
  }[name] || name
}

// Readiness is advisory; backend admission still validates every saved launch.
const canStart = computed(() =>
  prompt.value.trim().length > 0 && preflight.value.status === 'ready' && preflight.value.mode === mode.value
)
const statusTitle = computed(() => {
  if (status.value === 'completed') return mode.value === 'research_only' ? L('研究完成', 'Research complete') : L('推演完成 · 预测就绪', 'Done · forecast ready')
  if (status.value === 'failed') return L('运行失败', 'Run failed')
  if (status.value === 'cancelled') return L('已取消', 'Cancelled')
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
function onProviderChanged() { checkPreflight() }

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
  if (!canStart.value || starting.value || restoringLaunch.value) return
  await performLaunchAction(() => launchController.start({
    prompt: prompt.value.trim(),
    mode: mode.value,
    depth: depth.value,
    max_rounds: maxRounds.value || undefined,
    language: researchLanguage.value || undefined,
    model: researchModel.value || undefined
  }))
}

async function performLaunchAction(action, { openAdmitted = true } = {}) {
  if (starting.value) return
  starting.value = true
  error.value = ''
  try {
    const res = await action()
    // Admission without dispatch remains visible for deliberate recovery.
    if (launchViewActive && openAdmitted && res.data.pipeline_id && !res.data.recovery_required) {
      beginPipeline(res.data.pipeline_id, launchController.current().payload.prompt)
    }
  } catch (e) {
    error.value = e?.response?.status === 404
      ? L('尚未找到此启动请求。您可以使用保存的同一请求重试。', 'This launch has not been found. You can explicitly retry the same saved request.')
      : e?.message || L('启动结果未确认，请检查已保存的请求。', 'The launch outcome is unconfirmed. Check the saved request.')
  } finally {
    starting.value = false
  }
}

function checkSavedLaunch() { return performLaunchAction(() => launchController.check()) }
function retrySavedLaunch() { return performLaunchAction(() => launchController.retry()) }
async function discardSavedLaunch() {
  if (starting.value || restoringLaunch.value) return
  starting.value = true
  let confirmed = false
  try {
    confirmed = !!(confirmDlg.value && await confirmDlg.value.open({
      title: L('撤回尚未启动的请求', 'Discard unsubmitted launch'),
      message: L(
        '服务器将检查此请求。若已启动，会保留现有运行；否则会永久关闭该请求，您就可以修改输入。',
        'The server will check this request. If it has already started, the existing run will be retained. Otherwise the request will be permanently closed so you can edit the inputs.'
      ),
      confirmLabel: L('检查并撤回', 'Check and discard'),
      cancelLabel: L('保留请求', 'Keep request')
    }))
  } finally {
    starting.value = false
  }
  if (confirmed && launchViewActive) await performLaunchAction(() => launchController.abandon(), { openAdmitted: false })
}
function openSavedLaunch() {
  const saved = launchController.current()
  if (starting.value || restoringLaunch.value || !saved?.admission?.pipeline_id || saved.admission.launch_status === 'unavailable') return
  // Opening a known identity is read-only; any same-ID resume remains explicit.
  beginPipeline(saved.admission.pipeline_id, saved.payload.prompt)
}

function beginPipeline(id, initialPrompt = '') {
  pipelineId.value = id
  runPrompt.value = String(initialPrompt || '').trim()
  logLines.value = []
  logSourceCount.value = 0
  logHistoryHydrated.value = false
  logHistoryFinalized.value = false
  logInitialSnapshotAttempted.value = false
  logFinalSnapshotAttempts.value = 0
  logHistoryLoading.value = false
  logHistoryError.value = ''
  try { localStorage.setItem(ACTIVE_PIPELINE_KEY, id) } catch (e) { /* noop */ }
  startPolling()
}

function applyProgressLogResponse(response, { finalized = false } = {}) {
  const data = response && response.data
  if (!data || !Array.isArray(data.lines)) return false
  const exact = data.scope === 'full' && data.total_exact === true && data.truncated === false
  if (exact) {
    logLines.value = data.lines.map(line => String(line ?? ''))
    logHistoryHydrated.value = true
    logHistoryFinalized.value = !!finalized
    logHistoryError.value = ''
  } else {
    logLines.value = mergeProgressLines(logLines.value, data.lines)
  }
  logSourceCount.value = Math.max(logSourceCount.value, Number(data.source_count) || 0)
  return exact
}

async function requestProgressLog(id, scope) {
  if (!id) return null
  if (scope === 'full') logHistoryLoading.value = true
  try {
    return await getProgressLog(id, 500, scope)
  } catch (e) {
    if (scope === 'full') {
      logHistoryError.value = e?.message || L('全部记录事件载入失败', 'Failed to load recorded history')
    }
    return null
  } finally {
    if (scope === 'full') logHistoryLoading.value = false
  }
}

async function refreshFullResearchLog() {
  const id = pipelineId.value
  if (!id || logHistoryLoading.value) return
  // A snapshot may be labeled final only when the terminal boundary was
  // already observed before this request began. If status crosses while the
  // request is in flight, the polling path still owes a distinct final read.
  const researchStatusBefore = stages.value.research && stages.value.research.status
  const terminalBeforeRequest = needsFinalProgressSnapshot(
    status.value, researchStatusBefore, false
  )
  const response = await requestProgressLog(id, 'full')
  if (id !== pipelineId.value) return
  logInitialSnapshotAttempted.value = true
  if (terminalBeforeRequest) {
    logFinalSnapshotAttempts.value = Math.max(1, logFinalSnapshotAttempts.value)
  }
  applyProgressLogResponse(response, { finalized: terminalBeforeRequest })
}

async function finalizeResearchLog(id, generation) {
  while (logFinalSnapshotAttempts.value < MAX_FINAL_SNAPSHOT_ATTEMPTS) {
    logFinalSnapshotAttempts.value += 1
    const response = await requestProgressLog(id, 'full')
    if (!pollActive || generation !== pollGeneration || id !== pipelineId.value) return false
    if (applyProgressLogResponse(response, { finalized: true })) return true
  }
  return false
}

const cancelling = ref(false)
const resuming = ref(false)
const canResume = computed(() => !!pipelineId.value && (status.value === 'failed' || status.value === 'cancelled'))
const confirmDlg = ref(null)
async function cancel() {
  if (!pipelineId.value || cancelling.value) return
  const ok = confirmDlg.value && await confirmDlg.value.open({
    title: L('取消推演', 'Cancel run'),
    message: L('确定取消本次推演？正在运行的研究/模拟将被终止。', 'Cancel this run? The in-flight research/simulation will be stopped.'),
    confirmLabel: L('确定取消', 'Yes, cancel'),
    cancelLabel: L('继续运行', 'Keep running'),
    danger: true
  })
  if (!ok) return
  cancelling.value = true
  try {
    await cancelPipeline(pipelineId.value)
    // 状态翻转由轮询接管（cancelled 是终态）
  } catch (e) {
    error.value = e?.message || L('取消失败', 'Cancel failed')
  } finally {
    cancelling.value = false
  }
}

async function resume() {
  if (!pipelineId.value || resuming.value) return
  resuming.value = true
  error.value = ''
  try {
    const res = await resumePipeline(pipelineId.value)
    const id = (res && res.data && res.data.pipeline_id) || pipelineId.value
    pipelineId.value = id
    status.value = 'running'
    liveVitals.value = null  // same reasoning as continueToFull: await fresh live data
    // A failed/cancelled terminal snapshot is exact only for the pre-resume
    // stream. The resumed producer may append new research events.
    logHistoryFinalized.value = false
    logFinalSnapshotAttempts.value = 0
    try { localStorage.setItem(ACTIVE_PIPELINE_KEY, id) } catch (e) { /* noop */ }
    startPolling()
  } catch (e) {
    error.value = e?.message || L('恢复失败', 'Resume failed')
  } finally {
    resuming.value = false
  }
}

// FE-1: 用自重排的 setTimeout 取代固定 setInterval，使间隔可随"有无进展"自适应。
function startPolling() {
  stopPolling()
  pollActive = true
  pollGeneration += 1
  pollDelay = POLL_MIN_MS
  lastFingerprint = ''
  if (!visibilityHandler) {
    visibilityHandler = () => {
      if (!pollActive) return
      if (!document.hidden) {
        // 重新可见：复位为快节奏并立即拉一次，避免回到页面看到陈旧状态。
        pollDelay = POLL_MIN_MS
        scheduleNext(0)
      }
    }
    document.addEventListener('visibilitychange', visibilityHandler)
  }
  scheduleNext(0)
}

function stopPolling() {
  pollActive = false
  pollGeneration += 1
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  if (visibilityHandler) {
    document.removeEventListener('visibilitychange', visibilityHandler)
    visibilityHandler = null
  }
}

// 安排下一次轮询；隐藏标签页时不发起网络请求（暂停），仅保留一个慢心跳兜底，
// 防止极少数情况下 visibilitychange 事件丢失导致永不恢复。
function scheduleNext(delay) {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  if (!pollActive) return
  const generation = pollGeneration
  pollTimer = setTimeout(() => runPoll(generation), delay)
}

async function runPoll(generation) {
  if (!pollActive || generation !== pollGeneration) return
  if (typeof document !== 'undefined' && document.hidden) {
    // 暂停：标签页不可见时跳过实际请求，留一个 POLL_MAX_MS 的兜底心跳。
    scheduleNext(POLL_MAX_MS)
    return
  }
  if (pollInFlightGeneration === generation) {
    scheduleNext(POLL_MIN_MS)
    return
  }
  pollInFlightGeneration = generation
  try {
    await poll(generation)
  } finally {
    // 新 generation 可能已在旧请求返回前取得 ownership；旧请求不得清掉它。
    if (pollInFlightGeneration === generation) pollInFlightGeneration = null
  }
  if (!pollActive || generation !== pollGeneration) return
  scheduleNext(pollDelay)
}

async function poll(generation) {
  const requestedPipelineId = pipelineId.value
  if (!requestedPipelineId) return
  try {
    // 首次打开做一次精确全量 hydration；随后只拉有界 tail 并累积，避免每 2.5s
    // 对数小时日志做 O(file) 扫描。研究完成边界再做一次精确快照，弥合任何突发窗口。
    const researchRunning = !(stages.value.research && stages.value.research.status === 'completed') || logLines.value.length === 0
    const progressScope = !logHistoryHydrated.value && !logInitialSnapshotAttempted.value
      ? 'full'
      : (researchRunning ? 'tail' : null)
    if (progressScope === 'full') logInitialSnapshotAttempted.value = true
    const [st, lg] = await Promise.all([
      getPipelineStatus(requestedPipelineId),
      progressScope
        ? requestProgressLog(requestedPipelineId, progressScope)
        : Promise.resolve(null)
    ])
    if (!pollActive || generation !== pollGeneration || requestedPipelineId !== pipelineId.value) return
    pollFailures = 0
    if (connErrorShown) { error.value = ''; connErrorShown = false }
    const d = st.data
    status.value = d.status
    globalProgress.value = d.global_progress || 0
    currentStage.value = d.current_stage || ''
    stages.value = d.stages || {}
    if (d.mode) mode.value = d.mode
    if (typeof d.prompt === 'string') runPrompt.value = d.prompt
    // Per-response truth: an absent `live` block renders no vitals strip.
    liveVitals.value = (d.live && typeof d.live === 'object' && !Array.isArray(d.live)) ? d.live : null
    runTiming.value = {
      createdAt: d.created_at || '',
      updatedAt: d.updated_at || '',
      resumedAt: (d.options && d.options.resumed_at) || ''
    }
    if (d.graph_id) graphId.value = d.graph_id
    if (d.simulation_id) simulationId.value = d.simulation_id
    if (d.report_id) reportId.value = d.report_id
    const researchStatus = stages.value.research && stages.value.research.status
    const researchDone = researchStatus === 'completed'
    // An initial full hydration is exact only at the instant it was read. Never
    // promote it to "final" merely because the concurrently fetched status has
    // crossed a boundary; always take one distinct post-observation snapshot.
    applyProgressLogResponse(lg, { finalized: false })
    if (needsFinalProgressSnapshot(status.value, researchStatus, logHistoryFinalized.value)) {
      await finalizeResearchLog(requestedPipelineId, generation)
    }

    // FE-1: 有进展则复位为快节奏，连续无变化则把间隔向 POLL_MAX_MS 拉升（~1.5x 阶梯）。
    const fp = pollFingerprint()
    if (fp !== lastFingerprint) {
      pollDelay = POLL_MIN_MS
    } else {
      pollDelay = Math.min(POLL_MAX_MS, Math.round(pollDelay * 1.5))
    }
    lastFingerprint = fp

    if (researchDone && !dossierFetched) {
      dossierFetched = true
      try {
        const dossierResult = await getDossier(requestedPipelineId)
        if (pollActive && generation === pollGeneration && requestedPipelineId === pipelineId.value) {
          dossier.value = dossierResult.data
        }
      } catch (e) { /* noop */ }
    }

    if (status.value === 'completed' || status.value === 'failed' || status.value === 'cancelled') {
      stopPolling()
      try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (e) { /* noop */ }
      if (!dossier.value) {
        try {
          const dossierResult = await getDossier(requestedPipelineId)
          if (requestedPipelineId === pipelineId.value) dossier.value = dossierResult.data
        } catch (e) { /* noop */ }
      }
      if (status.value === 'failed') {
        const failed = Object.values(stages.value).find(s => s.status === 'failed')
        error.value = (failed && failed.error) || d.error || L('运行失败', 'Run failed')
      }
    }
  } catch (e) {
    if (!pollActive || generation !== pollGeneration || requestedPipelineId !== pipelineId.value) return
    if (e && e.response && e.response.status === 404) {
      stopPolling()
      try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (_) { /* noop */ }
      status.value = 'failed'
      error.value = L('该管线不存在或已被清理', 'This pipeline no longer exists')
      return
    }
    // 非 404 失败（后端崩溃/网络断开）不再静默吞掉：连续多次失败时提示用户，
    // 但保持轮询——后端恢复后下一次成功轮询会自动清掉提示。
    pollFailures += 1
    if (pollFailures >= POLL_FAILURE_THRESHOLD) {
      error.value = L('与后端失去连接（持续重试中）：', 'Lost connection to the backend (retrying): ') + (e?.message || '')
      connErrorShown = true
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

async function goHome() {
  await reset()
  if (!pipelineId.value) router.push({ name: 'Research' })
}

// —— 管线 ID：截断显示 + 点击复制完整 ID ——
const pidCopied = ref(false)
let pidCopiedTimer = null
const shortPipelineId = computed(() => (pipelineId.value ? pipelineId.value.slice(0, 8) : ''))
async function copyPipelineId() {
  const text = pipelineId.value
  if (!text) return
  let copied = false
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      copied = true
    }
  } catch (e) { copied = false }
  if (!copied) {
    // 非安全上下文（如 http://LAN）没有 navigator.clipboard，回退到 execCommand。
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      copied = document.execCommand('copy')
      document.body.removeChild(ta)
    } catch (e) { copied = false }
  }
  if (copied) {
    pidCopied.value = true
    if (pidCopiedTimer) clearTimeout(pidCopiedTimer)
    pidCopiedTimer = setTimeout(() => { pidCopied.value = false }, 1600)
  }
}

function resetState() {
  stopPolling()
  status.value = 'running'; globalProgress.value = 0; currentStage.value = ''
  stages.value = {}; graphId.value = ''; simulationId.value = ''; reportId.value = ''
  runPrompt.value = ''; logLines.value = []; logSourceCount.value = 0
  liveVitals.value = null; runTiming.value = { createdAt: '', updatedAt: '', resumedAt: '' }
  logHistoryHydrated.value = false; logHistoryFinalized.value = false
  logInitialSnapshotAttempted.value = false; logFinalSnapshotAttempts.value = 0
  logHistoryLoading.value = false; logHistoryError.value = ''
  dossier.value = null; dossierFetched = false
  graphData.value = null; graphLoading.value = false; graphMax.value = false
  activeTab.value = 'log'; userPickedTab.value = false; error.value = ''
}
async function reset() {
  if (starting.value || restoringLaunch.value) return
  starting.value = true
  try {
    // Only an explicit New action releases an already-admitted launch intent.
    // A stale tab is not allowed to clear another tab's replacement request.
    const admission = launchController.current()?.admission
    let confirmRecovery = false
    if (admission?.recovery_required) {
      confirmRecovery = !!(confirmDlg.value && await confirmDlg.value.open({
        title: L('有意创建新的推演', 'Create an intentional new run'),
        message: L(
          `已有管线 ${admission.pipeline_id} 可能仍在运行。新建会允许另一次独立推演，不会取消已有管线。确定继续？`,
          `Existing pipeline ${admission.pipeline_id} could still be running. New allows a separate forecast and does not cancel that pipeline. Continue?`
        ),
        confirmLabel: L('新建独立推演', 'Create separate run'),
        cancelLabel: L('保留已有请求', 'Keep saved launch')
      }))
      if (!confirmRecovery) return
    }
    await launchController.newLaunch({ confirmRecovery })
    resetState()
    pipelineId.value = ''
    try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (e) { /* noop */ }
    checkPreflight()
  } catch (e) {
    error.value = e?.message || L('无法新建，请先检查已有启动请求。', 'Check the existing launch before creating another.')
  } finally {
    starting.value = false
  }
}

/** 历史抽屉里删掉的运行若正是当前查看的运行，回到输入页（避免对已删 id 继续轮询/展示）。 */
function onRunDeleted(id) {
  if (id && id === pipelineId.value) {
    resetState()
    pipelineId.value = ''
    try { localStorage.removeItem(ACTIVE_PIPELINE_KEY) } catch (e) { /* noop */ }
    // Deletion changes the view, never the launch intent. Only explicit New
    // releases the retained identity; the server's admission tombstone remains.
  }
}

onMounted(async () => {
  let saved = null
  try {
    saved = localStorage.getItem(ACTIVE_PIPELINE_KEY)
    if (!saved) {
      // 一次性迁移旧 MiroFish 键：读旧写新，随后删除旧键。
      const legacy = localStorage.getItem(LEGACY_PIPELINE_KEY)
      if (legacy) {
        saved = legacy
        localStorage.setItem(ACTIVE_PIPELINE_KEY, legacy)
        localStorage.removeItem(LEGACY_PIPELINE_KEY)
      }
    }
  } catch (e) { saved = null }
  try {
    const intent = await launchController.restore()
    if (!launchViewActive) return
    // A historical run selected after admission remains the current view.
    // Unresolved requests still take precedence so their outcome is not lost.
    if (saved && intent?.admission?.pipeline_id && !intent.admission.recovery_required && saved !== intent.admission.pipeline_id) beginPipeline(saved)
    else if (intent) await checkSavedLaunch() // Reload only checks; it never submits.
    else if (saved) beginPipeline(saved)
  } catch (e) {
    error.value = e?.message || L('无法读取保存的启动请求。', 'Could not read the saved launch.')
    if (launchViewActive && saved) beginPipeline(saved)
  } finally {
    restoringLaunch.value = false
    if (launchViewActive && !pipelineId.value) checkPreflight()
  }
})
// T5.6: 切换模式时重新检查（research_only 跳过图谱/报告 LLM 检查）
watch(mode, () => { if (!pipelineId.value) checkPreflight() }, { flush: 'sync' })
onUnmounted(() => {
  launchViewActive = false
  preflightController.invalidate()
  if (previousBodyOverflow !== null) document.body.style.overflow = previousBodyOverflow
  stopPolling()
  if (pidCopiedTimer) { clearTimeout(pidCopiedTimer); pidCopiedTimer = null }
})
</script>

<style scoped>
.research-container {
  --orange: #a83f20; --border: #dce0e4; --mono: 'JetBrains Mono', ui-monospace, monospace;
  --font-sans: 'Inter', 'Noto Sans SC', system-ui, sans-serif;
  --color-ink: #18222f; --color-muted: #596575; --color-accent: #a83f20;
  --color-accent-soft: #fff3ec; --radius: 8px; --radius-md: 12px;
  min-height: 100vh; background: #f5f5f2; color: var(--color-ink); font-family: var(--font-sans);
}
button, input, select, textarea { font: inherit; }
button { touch-action: manipulation; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid #a83f20; outline-offset: 3px;
}
button:disabled { cursor: not-allowed; }
.navbar { min-height: 72px; background: #fff; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 12px 32px; position: sticky; top: 0; z-index: 20; }
.nav-brand { font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; font-weight: 700; font-size: 1.12rem; letter-spacing: -.04em; color: var(--color-ink); background: none; border: 0; padding: 8px 0; cursor: pointer; white-space: nowrap; }
.brand-accent { color: var(--orange); }
.nav-links { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; }
.nav-tag { font-size: .78rem; font-weight: 500; color: #647080; margin-right: 12px; }
.nav-icon-btn, .nav-hist-btn { background: #fff; border: 1px solid var(--border); border-radius: 8px; color: #344151; min-width: 38px; min-height: 38px; padding: 8px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 8px; font-size: .78rem; font-weight: 500; transition: background 150ms, border-color 150ms; }
.nav-hist-btn { padding-inline: 12px; }
.nav-icon-btn:hover, .nav-hist-btn:hover { background: #f7f7f4; border-color: #aeb6bf; }
.main-content { max-width: 1360px; margin: 0 auto; padding: 48px 40px 64px; }
.setup-heading { max-width: 850px; margin-bottom: 32px; }
.eyebrow { margin: 0 0 12px; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--orange); }
.main-title { font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; font-size: clamp(1.9rem, 3vw, 2.65rem); line-height: 1.18; letter-spacing: -.045em; font-weight: 500; margin: 0 0 16px; text-wrap: balance; }
.lead { max-width: 740px; color: #596575; font-size: .96rem; line-height: 1.75; margin: 0; }
.setup-grid { display: grid; grid-template-columns: minmax(0, 1fr) 324px; align-items: start; gap: 24px; }
.console-box, .journey-card { min-width: 0; border: 1px solid var(--border); border-radius: 12px; background: #fff; box-shadow: 0 3px 14px rgba(24,34,47,.035); }
.console-section { padding: 24px; }
.console-header { margin-bottom: 8px; }
.console-header label { font-weight: 600; font-size: 1rem; }
.field-hint { color: #647080; font-size: .76rem; line-height: 1.6; margin: 0 0 14px; }
.input-wrapper { border: 1px solid #cbd1d8; background: #fbfbf9; border-radius: 8px; transition: border-color 150ms, box-shadow 150ms; }
.input-wrapper:focus-within { border-color: var(--orange); box-shadow: 0 0 0 3px rgba(168,63,32,.09); background: #fff; }
.code-input { display: block; width: 100%; border: 0; border-radius: 8px; background: transparent; padding: 16px; font-size: .91rem; line-height: 1.75; color: var(--color-ink); resize: vertical; min-height: 150px; }
.code-input::placeholder { color: #6b7684; opacity: 1; }
.code-input:focus-visible { outline-offset: -3px; }
.examples { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; }
.ex-label { font-size: .72rem; color: #647080; }
.ex-chip { border: 1px solid var(--border); border-radius: 8px; background: #fff; font-size: .72rem; padding: 7px 10px; cursor: pointer; color: #425064; transition: background 150ms, border-color 150ms; }
.ex-chip:hover:not(:disabled) { border-color: #d0a491; color: var(--orange); background: #fff7f2; }
.ex-chip:disabled { color: #69717b; background: #f0f1f3; }
.console-divider { display: flex; align-items: center; gap: 12px; margin: 0 24px; color: #647080; font-size: .72rem; font-weight: 500; }
.console-divider::after { content: ''; flex: 1; height: 1px; background: #e6e8ec; }
.params-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 24px; }
.param { min-width: 0; }
.param label, .param-label { display: block; font-size: .78rem; font-weight: 500; color: #394657; margin-bottom: 9px; }
.seg { display: flex; gap: 3px; padding: 3px; background: #f0f2f4; border: 1px solid #e1e4e8; border-radius: 8px; }
.seg button { flex: 1; background: transparent; border: 0; border-radius: 5px; padding: 9px 8px; font-size: .76rem; cursor: pointer; color: #516074; white-space: nowrap; transition: background 150ms, color 150ms; }
.seg button:hover:not(.active):not(:disabled) { background: #e4e8ed; color: #18222f; }
.seg button.active { background: #fff; color: #18222f; font-weight: 600; box-shadow: 0 1px 4px rgba(24,34,47,.12); }
.seg button:disabled { opacity: .65; }
.rounds-param { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) 160px; column-gap: 18px; align-items: center; }
.rounds-param label { margin-bottom: 4px; }
.rounds-param .field-hint { grid-column: 1; grid-row: 2; margin: 0; font-size: .72rem; }
.num-input { grid-column: 2; grid-row: 1 / 3; }
.num-input, .adv-select { border: 1px solid #cbd1d8; border-radius: 8px; padding: 10px 12px; color: var(--color-ink); background: #fff; font-size: .82rem; width: 100%; min-height: 42px; }
.num-input::placeholder { color: #647080; opacity: 1; }
.adv-toggle { display: inline-flex; align-items: center; gap: 8px; background: none; border: 0; border-radius: 6px; padding: 6px 0; margin: 0 24px 16px; color: #49566a; font-size: .78rem; font-weight: 500; cursor: pointer; }
.adv-toggle:hover { color: var(--orange); }
.adv-caret { font-size: .85rem; }
.adv-row { border-top: 1px solid #edf0f2; background: #fbfbfa; padding-top: 18px; padding-bottom: 18px; }
.preflight-banner { display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px; margin: 0 24px 18px; border-radius: 8px; border: 1px solid #dde2e8; background: #f5f7f9; color: #48566a; font-size: .76rem; line-height: 1.55; }
.preflight-ready { border-color: #d5e4db; background: #f1f7f3; color: #2b6143; }
.preflight-blocked, .preflight-unavailable { border-color: #ecd8c9; background: #fff7ef; color: #824319; }
.readiness-mark { font-size: .87rem; font-weight: 700; flex-shrink: 0; }
.preflight-copy { flex: 1; min-width: 0; }
.pf-title { font-weight: 500; margin: 0; }
.pf-detail { margin: 3px 0 0; }
.pf-list { padding-left: 16px; margin: 6px 0 0; overflow-wrap: anywhere; }
.pf-list li + li { margin-top: 4px; }
.preflight-retry { border: 1px solid #d7bba8; border-radius: 6px; background: #fff; color: #824319; padding: 5px 9px; cursor: pointer; font-size: .74rem; }
.console-section.btn-section { padding-top: 0; }
.start-engine-btn { width: 100%; background: var(--orange); color: #fff; border: 1px solid var(--orange); border-radius: 8px; padding: 15px 18px; font-weight: 600; font-size: .9rem; display: flex; justify-content: space-between; gap: 12px; align-items: center; cursor: pointer; transition: background 150ms, box-shadow 150ms; text-align: left; }
.start-engine-btn:hover:not(:disabled) { background: #8f351b; border-color: #8f351b; box-shadow: 0 3px 8px rgba(168,63,32,.12); }
.start-engine-btn:disabled { background: #e8ebee; border-color: #e1e4e8; color: #5d6776; cursor: not-allowed; }
.btn-arrow { font-size: 1.15rem; }
.launch-recovery { border: 1px solid #d5dde6; border-radius: 8px; background: #f7f9fb; padding: 16px; font-size: .85rem; }
.launch-recovery p { margin: 0 0 12px; line-height: 1.7; }
.launch-recovery-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.err { color: #a03226; font-size: .8rem; line-height: 1.65; margin: 14px 0 0; overflow-wrap: anywhere; }
.journey-card { padding: 24px; }
.journey-card .eyebrow { font-size: .65rem; margin-bottom: 10px; }
.journey-card h2 { font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; font-size: 1.2rem; line-height: 1.4; letter-spacing: -.02em; font-weight: 500; margin: 0 0 24px; }
.journey-list { list-style: none; margin: 0; padding: 0; }
.journey-list li { display: flex; gap: 14px; position: relative; padding-bottom: 21px; }
.journey-list li:not(:last-child)::after { content: ''; position: absolute; left: 14px; top: 32px; bottom: 3px; border-left: 1px solid #dbe0e5; }
.journey-list li:last-child { padding-bottom: 0; }
.journey-number { display: flex; align-items: center; justify-content: center; flex: 0 0 29px; height: 29px; border: 1px solid #d9dfe5; background: #f6f7f8; color: #536074; border-radius: 9px; font-size: .73rem; font-weight: 600; }
.journey-list li:first-child .journey-number { background: #fff2e9; border-color: #efcdbd; color: var(--orange); }
.journey-optional .journey-number { background: #fff; border-style: dashed; }
.journey-list h3 { margin: 1px 0 5px; font-size: .8rem; font-weight: 600; }
.journey-list p { margin: 0; color: #647080; font-size: .73rem; line-height: 1.6; }
.journey-note { border-top: 1px solid #e4e7eb; margin-top: 24px; padding-top: 18px; }
.journey-note strong { display: block; font-size: .75rem; font-weight: 600; line-height: 1.55; }
.journey-note p { font-size: .73rem; color: #647080; line-height: 1.7; margin: 8px 0 0; }
.history-drawer { position: fixed; top: 0; right: 0; width: 420px; max-width: 94vw; height: 100vh; height: 100dvh; background: #fff; border-left: 1px solid var(--border); z-index: 40; box-shadow: -8px 0 40px rgba(24,34,47,.12); display: flex; flex-direction: column; }
.drawer-head { display: flex; justify-content: space-between; align-items: center; padding: 18px 22px; border-bottom: 1px solid var(--border); min-height: 72px; }
.drawer-head h2 { font-size: 1rem; margin: 0; font-weight: 600; }
.drawer-close { display: flex; align-items: center; justify-content: center; background: #f4f5f6; border: 1px solid var(--border); border-radius: 8px; width: 36px; height: 36px; cursor: pointer; color: #4b5869; }
.drawer-close:hover { color: #18222f; background: #e9edf0; }
.drawer-scrim { position: fixed; inset: 0; background: rgba(24,34,47,.32); z-index: 30; }
.drawer-enter-active, .drawer-leave-active { transition: transform 200ms ease; }
.drawer-enter-from, .drawer-leave-to { transform: translateX(100%); }
.fade-enter-active, .fade-leave-active { transition: opacity 200ms ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.run-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 24px; }
.console-label { font-size: .74rem; color: #647080; display: flex; align-items: center; gap: 10px; }
.pid-chip { background: #fff; border: 1px solid var(--border); border-radius: 6px; color: #526174; font-family: var(--mono); font-size: .7rem; padding: 5px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.pid-chip:hover { border-color: var(--orange); color: var(--orange); }
.pid-copied { color: #2b6143; font-weight: 500; }
.run-title { font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; font-size: 1.9rem; line-height: 1.3; font-weight: 500; margin: 12px 0 0; letter-spacing: -.035em; }
.run-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; padding-top: 5px; }
.primary-btn { background: var(--orange); color: #fff; border: 0; border-radius: 8px; padding: 12px 18px; font-size: .82rem; font-weight: 600; cursor: pointer; }
.primary-btn:hover { background: #8f351b; }
.ghost-btn { background: #fff; color: #394657; border: 1px solid #d5dbe2; border-radius: 8px; padding: 10px 14px; font-size: .78rem; cursor: pointer; }
.ghost-btn:hover:not(:disabled) { border-color: #aeb8c4; background: #f7f9fa; }
.cancel-btn { color: #9d3427; border-color: #e4c6c0; }
.resume-btn { color: #2b6143; border-color: #bdd7c7; }
.ghost-btn:disabled { color: #6b7380; background: #f0f2f3; }
.run-prompt-card { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 18px 22px; margin: 0 0 24px; }
.run-prompt-label { display: block; margin-bottom: 8px; color: #647080; font-size: .69rem; font-weight: 600; letter-spacing: .035em; text-transform: uppercase; }
.run-prompt-card p { margin: 0; color: #293649; font-size: .91rem; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.run-layout { display: grid; grid-template-columns: 292px minmax(0, 1fr); gap: 24px; align-items: start; }
.rail { position: sticky; top: 96px; }
.workspace { min-width: 0; border: 1px solid var(--border); border-radius: 12px; background: #fff; box-shadow: 0 3px 14px rgba(24,34,47,.025); }
.tabbar { display: flex; gap: 4px; padding: 9px; border-bottom: 1px solid var(--border); overflow-x: auto; border-radius: 12px 12px 0 0; background: #f9fafb; }
.tab { flex: 0 0 auto; display: flex; align-items: center; gap: 7px; background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 10px 13px; font-size: .76rem; cursor: pointer; color: #5b687b; white-space: nowrap; }
.tab:hover:not(.active):not(:disabled) { color: #18222f; background: #edf0f3; }
.tab.active { color: #8e371e; border-color: #ecd6c9; background: #fff4ed; font-weight: 600; }
.tab:disabled { color: #727b88; cursor: not-allowed; }
.tab-badge { background: #a83f20; color: #fff; font-size: .62rem; padding: 2px 6px; border-radius: 5px; }
.tab-body { min-height: min(560px, 70vh); background: #fff; border-radius: 0 0 12px 12px; }
.graph-wrap { height: 600px; }
.graph-wrap.max { height: calc(100vh - 220px); }
.lazy-empty { height: 100%; display: flex; flex-direction: column; gap: 14px; align-items: center; justify-content: center; color: #647080; font-size: .85rem; padding: 40px; text-align: center; }
.lazy-icon { font-size: 24px; color: var(--orange); line-height: 1; }
.lazy-spinner { width: 22px; height: 22px; border: 2px solid var(--border); border-top-color: var(--orange); border-radius: 50%; animation: rv-spin .8s linear infinite; }
@keyframes rv-spin { to { transform: rotate(360deg); } }
@media (max-width: 1080px) {
  .main-content { padding: 36px 24px 48px; }
  .setup-grid { grid-template-columns: minmax(0, 1fr) 280px; gap: 20px; }
  .journey-card { padding: 20px; }
  .run-layout { grid-template-columns: 1fr; }
  .rail { position: static; }
  .params-row { grid-template-columns: 1fr; }
  .rounds-param { grid-template-columns: minmax(0, 1fr) 140px; }
  .adv-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 800px) {
  .setup-grid { grid-template-columns: 1fr; }
  .nav-tag { display: none; }
  .params-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .journey-card { padding: 24px; }
  .journey-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
  .journey-list li { padding: 0; }
  .journey-list li::after { display: none; }
  .run-header { flex-direction: column; gap: 16px; }
  .run-actions { justify-content: flex-start; }
}
@media (max-width: 480px) {
  .navbar { min-height: 64px; padding: 10px 14px; gap: 8px; flex-wrap: wrap; }
  .nav-brand { font-size: .93rem; }
  .nav-links { gap: 6px; }
  .nav-icon-btn, .nav-hist-btn { min-width: 32px; min-height: 34px; padding: 7px; }
  .nav-hist-label { display: none; }
  .main-content { padding: 30px 16px 40px; }
  .setup-heading { margin-bottom: 24px; }
  .main-title { font-size: 2rem; }
  .lead { font-size: .88rem; }
  .console-section { padding: 20px; }
  .console-divider { margin-inline: 20px; }
  .preflight-banner { margin-inline: 20px; }
  .adv-toggle { margin-left: 20px; }
  .params-row { grid-template-columns: 1fr; gap: 20px; }
  .rounds-param { display: block; }
  .rounds-param .num-input { margin: 3px 0 9px; }
  .journey-list { grid-template-columns: 1fr; gap: 18px; }
  .run-title { font-size: 1.7rem; }
  .run-prompt-card { padding: 16px; }
  .tabbar { padding: 6px; }
  .tab { padding: 9px 10px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
@media print {
  .navbar, .history-drawer, .drawer-scrim, .rail, .tabbar,
  .run-actions, .pid-chip, .run-vitals, .run-err { display: none !important; }
  .research-container { background: #fff; }
  .main-content { max-width: none; padding: 0; }
  .run-layout { display: block; }
  .workspace, .tab-body { border: none; box-shadow: none; min-height: 0; }
  .run-prompt-card { border: 1px solid #ccc; background: #fff; }
}
</style>
