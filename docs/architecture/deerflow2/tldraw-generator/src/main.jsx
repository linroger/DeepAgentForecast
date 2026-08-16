import React, { useCallback, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Tldraw,
  createBindingId,
  createShapeId,
  serializeTldrawJsonBlob,
  toRichText,
} from 'tldraw'
import 'tldraw/tldraw.css'

const COLORS = {
  native: 'blue',
  live: 'green',
  model: 'violet',
  tool: 'orange',
  state: 'light-blue',
  gate: 'red',
  future: 'grey',
  neutral: 'black',
}

const id = (name) => createShapeId(name)

function frame(name, x, y, w, h) {
  return {
    id: id(`frame-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`),
    type: 'frame',
    x,
    y,
    props: { w, h, name },
  }
}

function box(name, x, y, w, h, label, options = {}) {
  return {
    id: id(name),
    type: 'geo',
    x,
    y,
    props: {
      w,
      h,
      geo: options.geo || 'rectangle',
      color: options.color || COLORS.neutral,
      fill: options.fill || 'semi',
      dash: options.dash || 'solid',
      size: options.strokeSize || 'm',
      font: options.font || 'sans',
      align: options.align || 'middle',
      verticalAlign: options.verticalAlign || 'middle',
      richText: toRichText(label),
    },
  }
}

function textShape(name, x, y, w, label, options = {}) {
  return {
    id: id(name),
    type: 'text',
    x,
    y,
    props: {
      w,
      autoSize: false,
      color: options.color || COLORS.neutral,
      size: options.size || 'm',
      font: options.font || 'sans',
      textAlign: options.align || 'start',
      richText: toRichText(label),
    },
  }
}

function arrow(name, x1, y1, x2, y2, options = {}) {
  return {
    id: id(name),
    type: 'arrow',
    x: x1,
    y: y1,
    props: {
      start: { x: 0, y: 0 },
      end: { x: x2 - x1, y: y2 - y1 },
      color: options.color || COLORS.neutral,
      dash: options.dash || 'solid',
      size: options.size || 'm',
      bend: options.bend || 0,
      richText: toRichText(options.label || ''),
      arrowheadStart: options.start || 'none',
      arrowheadEnd: options.end || 'arrow',
    },
  }
}

// Every connector is bound to both endpoint shapes after scene creation. This
// keeps the .tldr canvas genuinely editable: moving a component carries its
// arrows with it instead of leaving coordinate-only lines behind.
const ARROW_BINDINGS = {
  'a-ui-gateway': ['native-human-ui', 'native-gateway'],
  'a-api-build': ['native-gateway', 'assembly-runtime-config'],
  'a-embedded-build': ['native-embedded', 'assembly-runtime-config'],
  'a-cli-embedded': ['native-cli', 'native-embedded'],
  'a-studio-build': ['native-studio', 'assembly-runtime-config'],
  'a-inputs-api': ['native-run-input', 'native-gateway'],
  'a-build-model': ['assembly-runtime-config', 'assembly-model-factory'],
  'a-build-skills': ['assembly-runtime-config', 'assembly-skills-prompt'],
  'a-build-tools': ['assembly-runtime-config', 'assembly-tool-registry'],
  'a-build-middleware': ['assembly-runtime-config', 'assembly-middleware'],
  'a-model-create': ['assembly-model-factory', 'assembly-create-agent'],
  'a-skills-create': ['assembly-skills-prompt', 'assembly-create-agent'],
  'a-tools-create': ['assembly-tool-registry', 'assembly-create-agent'],
  'a-middleware-create': ['assembly-middleware', 'assembly-create-agent'],
  'a-create-state': ['assembly-create-agent', 'assembly-state'],
  'a-create-loop': ['assembly-create-agent', 'loop-human-message'],
  'a-gateway-runs': ['native-gateway', 'gateway-run-manager'],
  'a-run-worker': ['gateway-run-manager', 'gateway-run-worker'],
  'a-worker-stream': ['gateway-run-worker', 'gateway-stream-bridge'],
  'a-worker-stores': ['gateway-run-worker', 'gateway-stores'],
  'a-stream-client': ['gateway-stream-bridge', 'native-output'],
  'a-loop-human-before': ['loop-human-message', 'loop-before-model'],
  'a-loop-before-model': ['loop-before-model', 'loop-lead-llm'],
  'a-loop-model-ai': ['loop-lead-llm', 'loop-ai-message'],
  'a-loop-ai-after': ['loop-ai-message', 'loop-after-model'],
  'a-loop-after-tool': ['loop-after-model', 'loop-tool-executor'],
  'a-loop-tool-result': ['loop-tool-executor', 'loop-tool-message'],
  'a-loop-result-repeat': ['loop-tool-message', 'loop-before-model'],
  'a-loop-after-final': ['loop-after-model', 'loop-final-output'],
  'a-loop-final-stream': ['loop-final-output', 'loop-stream-output'],
  'a-loop-summary-model': ['loop-before-model', 'loop-summarizer'],
  'a-loop-title-model': ['loop-after-model', 'loop-title'],
  'a-loop-memory-model': ['loop-after-model', 'loop-memory'],
  'a-loop-stop-worker': ['loop-stop', 'gateway-run-worker'],
  'a-tool-call-configured': ['loop-tool-executor', 'cap-configured-tools'],
  'a-tool-call-builtins': ['loop-tool-executor', 'cap-builtins'],
  'a-tool-call-mcp': ['loop-tool-executor', 'cap-mcp-acp'],
  'a-tool-call-sandbox': ['loop-tool-executor', 'cap-sandbox'],
  'a-task-to-executor': ['cap-task-tool', 'cap-subagent-executor'],
  'a-executor-subagent': ['cap-subagent-executor', 'cap-subagent-model'],
  'a-subagent-tools': ['cap-subagent-model', 'cap-subagent-tools'],
  'a-subagent-tool-return': ['cap-subagent-tools', 'cap-subagent-model'],
  'a-subagent-model-result': ['cap-subagent-model', 'cap-subagent-result'],
  'a-subagent-result-parent': ['cap-subagent-result', 'cap-task-tool'],
  'a-assembly-vendor': ['deploy-vendor', 'deploy-setup'],
  'a-assembly-overlay': ['deploy-overlay', 'deploy-setup'],
  'a-assembly-runtime': ['deploy-setup', 'deploy-generated'],
  'a-runtime-current-api': ['deploy-generated', 'integration-current-api'],
  'a-current-api-orchestrator': ['integration-current-api', 'integration-orchestrator'],
  'a-orchestrator-bridge': ['integration-orchestrator', 'integration-bridge'],
  'a-bridge-orchestrator-return': ['integration-bridge', 'integration-orchestrator'],
  'a-bridge-market': ['integration-bridge', 'integration-prepass-market'],
  'a-bridge-current-kg': ['integration-bridge', 'integration-existing-kg-mcp'],
  'a-current-kg-tracka': ['integration-existing-kg-mcp', 'integration-track-a'],
  'a-bridge-track-a': ['integration-bridge', 'integration-track-a'],
  'a-bridge-track-b': ['integration-bridge', 'integration-track-b'],
  'a-tracka-global': ['integration-track-a', 'integration-global'],
  'a-trackb-global': ['integration-track-b', 'integration-global'],
  'a-trackb-contract': ['integration-track-b', 'integration-contract'],
  'a-global-contract': ['integration-global', 'integration-contract'],
  'a-contract-downstream': ['integration-contract', 'receiver-knowledge'],
  'a-downstream-report': ['receiver-knowledge', 'receiver-report'],
  'a-drf2-chat-mcp': ['receiver-drf2-chat', 'receiver-drf2-engines'],
  'a-drf2-driver-api': ['receiver-drf2-driver', 'receiver-drf2-gateway'],
  'a-drf2-driver-skill-mcp': ['receiver-drf2-gateway', 'receiver-drf2-engines'],
  'a-drf2-driver-sim-http': ['receiver-drf2-driver', 'receiver-drf2-engines'],
}

function buildScene() {
  const frames = [
    frame('1 · Native DeerFlow 2 entry surfaces', 80, 210, 1350, 1030),
    frame('2 · Agent assembly and policy composition', 1490, 210, 2700, 1030),
    frame('3 · Gateway persistence and service plane', 4250, 210, 2070, 1030),
    frame('4 · Lead-agent model/tool execution loop', 80, 1310, 3520, 1510),
    frame('5 · Tool, skill, sandbox, MCP and subagent plane', 3660, 1310, 2660, 1510),
    frame('6A · Deterministic runtime assembly', 80, 2890, 1570, 1450),
    frame('6B · Current Stage-1 integration: embedded DeerFlow 2', 1710, 2890, 3220, 1450),
    frame('6C · Downstream receivers and DRF2 target', 4990, 2890, 1330, 1450),
  ]

  const arrows = [
    arrow('a-ui-gateway', 340, 420, 570, 420, { color: COLORS.native }),
    arrow('a-api-build', 940, 420, 1580, 550, { color: COLORS.native }),
    arrow('a-embedded-build', 940, 690, 1580, 550, { color: COLORS.live }),
    arrow('a-cli-embedded', 340, 910, 570, 690, { color: COLORS.live }),
    arrow('a-studio-build', 940, 930, 1580, 650, { color: COLORS.native, dash: 'dashed' }),
    arrow('a-inputs-api', 340, 690, 570, 510, { color: COLORS.native, dash: 'dashed' }),
    arrow('a-build-model', 1830, 550, 2050, 410, { color: COLORS.model }),
    arrow('a-build-skills', 1830, 550, 2420, 410, { color: COLORS.tool }),
    arrow('a-build-tools', 1830, 550, 2790, 410, { color: COLORS.tool }),
    arrow('a-build-middleware', 1830, 550, 3320, 410, { color: COLORS.native }),
    arrow('a-model-create', 2290, 410, 3720, 650, { color: COLORS.model }),
    arrow('a-skills-create', 2660, 410, 3720, 650, { color: COLORS.tool }),
    arrow('a-tools-create', 3030, 410, 3720, 650, { color: COLORS.tool }),
    arrow('a-middleware-create', 3560, 620, 3720, 650, { color: COLORS.native }),
    arrow('a-create-state', 3830, 740, 3830, 900, { color: COLORS.state }),
    arrow('a-create-loop', 3830, 740, 420, 1540, { color: COLORS.live, dash: 'dashed' }),
    arrow('a-gateway-runs', 940, 420, 4400, 410, { color: COLORS.native, dash: 'dashed' }),
    arrow('a-run-worker', 4610, 410, 5020, 410, { color: COLORS.native }),
    arrow('a-worker-stream', 5230, 410, 5650, 410, { color: COLORS.native }),
    arrow('a-worker-stores', 5230, 500, 4610, 790, { color: COLORS.state, dash: 'dashed' }),
    arrow('a-stream-client', 5860, 500, 1170, 650, { color: COLORS.native, dash: 'dashed' }),
    arrow('a-loop-human-before', 420, 1540, 750, 1540, { color: COLORS.live }),
    arrow('a-loop-before-model', 1080, 1540, 1390, 1540, { color: COLORS.model }),
    arrow('a-loop-model-ai', 1720, 1540, 2030, 1540, { color: COLORS.model }),
    arrow('a-loop-ai-after', 2360, 1540, 2670, 1540, { color: COLORS.native }),
    arrow('a-loop-after-tool', 2840, 1650, 2840, 1910, { color: COLORS.tool }),
    arrow('a-loop-tool-result', 2670, 2050, 2360, 2050, { color: COLORS.tool }),
    arrow('a-loop-result-repeat', 2030, 2050, 1080, 1680, { color: COLORS.live, bend: 70 }),
    arrow('a-loop-after-final', 3000, 1540, 3330, 1540, { color: COLORS.live }),
    arrow('a-loop-final-stream', 3330, 1680, 3330, 2410, { color: COLORS.live }),
    arrow('a-loop-summary-model', 920, 1780, 920, 2360, { color: COLORS.model, dash: 'dashed' }),
    arrow('a-loop-title-model', 2840, 1680, 1510, 2360, { color: COLORS.model, dash: 'dashed' }),
    arrow('a-loop-memory-model', 2840, 1680, 2110, 2360, { color: COLORS.model, dash: 'dashed' }),
    arrow('a-loop-stop-worker', 2760, 2500, 5230, 500, { color: COLORS.gate, dash: 'dashed' }),
    arrow('a-tool-call-configured', 2840, 2050, 3890, 1530, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-tool-call-builtins', 2840, 2050, 3890, 1790, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-tool-call-mcp', 2840, 2050, 3890, 2050, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-tool-call-sandbox', 2840, 2050, 3890, 2310, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-task-to-executor', 4300, 1790, 5290, 1790, { color: COLORS.tool }),
    arrow('a-executor-subagent', 5460, 1870, 5620, 2050, { color: COLORS.model }),
    arrow('a-subagent-tools', 5290, 2135, 4960, 2135, { color: COLORS.tool }),
    arrow('a-subagent-tool-return', 4960, 2210, 5290, 2210, { color: COLORS.live, bend: -35 }),
    arrow('a-subagent-model-result', 5620, 2220, 4960, 2440, { color: COLORS.live }),
    arrow('a-subagent-result-parent', 4630, 2440, 4300, 1870, { color: COLORS.live }),
    arrow('a-assembly-vendor', 410, 3140, 650, 3140, { color: COLORS.native }),
    arrow('a-assembly-overlay', 410, 3440, 650, 3260, { color: COLORS.live }),
    arrow('a-assembly-runtime', 1010, 3200, 1180, 3200, { color: COLORS.live }),
    arrow('a-runtime-current-api', 1480, 3200, 1740, 3100, { color: COLORS.live }),
    arrow('a-current-api-orchestrator', 2020, 3100, 2110, 3100, { color: COLORS.live }),
    arrow('a-orchestrator-bridge', 2440, 3100, 2540, 3100, { color: COLORS.live, bend: 35, label: 'launch argv/env' }),
    arrow('a-bridge-orchestrator-return', 2540, 3170, 2440, 3170, { color: COLORS.state, bend: -45, label: 'stdout + files' }),
    arrow('a-bridge-market', 2705, 3210, 2705, 3630, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-bridge-current-kg', 2705, 3210, 2705, 3420, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-current-kg-tracka', 2870, 3495, 3100, 3105, { color: COLORS.tool, dash: 'dashed' }),
    arrow('a-bridge-track-a', 2870, 3100, 3100, 3105, { color: COLORS.model }),
    arrow('a-bridge-track-b', 2870, 3170, 3100, 3465, { color: COLORS.model }),
    arrow('a-tracka-global', 3560, 3105, 3740, 3150, { color: COLORS.model }),
    arrow('a-trackb-global', 3560, 3465, 3740, 3260, { color: COLORS.model, bend: -55, label: 'judged actor dossier' }),
    arrow('a-trackb-contract', 3560, 3465, 4400, 3340, { color: COLORS.state }),
    arrow('a-global-contract', 4110, 3150, 4400, 3210, { color: COLORS.state }),
    arrow('a-contract-downstream', 4730, 3210, 5090, 3210, { color: COLORS.live }),
    arrow('a-downstream-report', 5250, 3380, 5250, 3710, { color: COLORS.live }),
    arrow('a-drf2-chat-mcp', 5780, 3530, 5900, 3530, { color: COLORS.future, dash: 'dashed', label: 'stdio MCP' }),
    arrow('a-drf2-driver-api', 5700, 4010, 5900, 4010, { color: COLORS.future, dash: 'dashed', label: 'Runs API' }),
    arrow('a-drf2-driver-skill-mcp', 6045, 3910, 6045, 3650, { color: COLORS.future, dash: 'dashed', label: 'slash skills → KG MCP' }),
    arrow('a-drf2-driver-sim-http', 5570, 3920, 6040, 3660, { color: COLORS.future, dash: 'dashed', label: 'provisional HTTP mismatch' }),
  ]

  const boxes = [
    textShape('diagram-title', 100, 35, 4100, 'DeerFlow 2.0 architecture inside DeepResearchForecast', { size: 'xl', font: 'sans' }),
    textShape('diagram-subtitle', 105, 105, 4300, 'Current source snapshot · editable tldraw scene · solid = executed/default path · dashed = conditional, side-channel, or pre-cutover', { size: 'm', color: 'grey' }),
    box('legend-native', 4510, 55, 230, 80, 'Native DF2', { color: COLORS.native }),
    box('legend-live', 4755, 55, 230, 80, 'Live DRF path', { color: COLORS.live }),
    box('legend-model', 5000, 55, 230, 80, 'LLM boundary', { color: COLORS.model }),
    box('legend-tool', 5245, 55, 230, 80, 'Tool / external', { color: COLORS.tool }),
    box('legend-state', 5490, 55, 230, 80, 'State / output', { color: COLORS.state }),
    box('legend-gate', 5735, 55, 230, 80, 'Invariant / gate', { color: COLORS.gate, fill: 'none' }),
    box('legend-future', 5980, 55, 230, 80, 'Pre-cutover', { color: COLORS.future, dash: 'dashed' }),

    box('native-human-ui', 160, 340, 260, 160, 'Web / SDK / channel client\nmessages + config + uploads', { color: COLORS.native }),
    box('native-gateway', 570, 330, 370, 180, 'FastAPI gateway\nthreads · runs · stream/wait\nmodels · MCP · skills · memory', { color: COLORS.native }),
    box('native-run-input', 160, 610, 260, 190, 'Run input\nthread_id · messages\nmodel/thinking/plan/subagents\nmetadata · disconnect policy', { color: COLORS.state }),
    box('native-embedded', 570, 600, 370, 180, 'Embedded DeerFlowClient\ndirect in-process harness\nstream() / chat()', { color: COLORS.live }),
    box('native-cli', 160, 860, 260, 130, 'CLI / Python caller\nheadless automation', { color: COLORS.live }),
    box('native-studio', 570, 850, 370, 140, 'LangGraph Studio / direct graph\ndevelopment and graph inspection', { color: COLORS.native, dash: 'dashed' }),
    box('native-output', 1010, 535, 330, 250, 'Observable outputs\nmessages-tuple deltas\nvalues snapshots\ncustom/tool events\ninterrupt/end\nartifacts + token usage', { color: COLORS.state }),
    box('native-note', 1010, 850, 330, 240, 'Current Stage 1 uses the embedded client inside a subprocess. The native gateway is the DRF2 target transport. Uploaded inputs are copied into the thread workspace before a run.', { color: COLORS.gate, fill: 'none' }),

    box('assembly-runtime-config', 1580, 460, 250, 180, 'RunnableConfig\nrequest overrides\nagent_name / model\nthread + recursion', { color: COLORS.state }),
    box('assembly-model-factory', 2050, 330, 240, 160, 'Model factory\nreflection + credentials\nthinking/reasoning\nprovider adapters', { color: COLORS.model }),
    box('assembly-skills-prompt', 2420, 330, 240, 160, 'N6 · Prompt + skills\nmetadata catalog + security scan\n/skill activation\nallowed-tools policy', { color: COLORS.tool }),
    box('assembly-tool-registry', 2790, 330, 240, 160, 'Tool registry\nconfigured + built-ins\nMCP + ACP + task\ndeferred schemas', { color: COLORS.tool }),
    box('assembly-middleware', 3150, 320, 410, 320, 'Ordered middleware\nbase runtime guards\ndynamic context · skill activation\nsummarization · todo · usage\ntitle · memory · vision\ndeferred tools · subagent limit\nloop/safety · clarification', { color: COLORS.native }),
    box('assembly-create-agent', 3600, 570, 460, 170, 'LangChain create_agent\nmodel + tools + middleware\nsystem prompt + ThreadState', { color: COLORS.live, strokeSize: 'l' }),
    box('assembly-state', 3600, 850, 460, 190, 'ThreadState + checkpointer\nmessages · title · todos\nsandbox · uploads/thread data\ndeferred tools · memory context', { color: COLORS.state }),
    box('assembly-invariant', 1580, 850, 760, 190, 'Assembly invariant\nrequest model → agent config model → global default; tool schemas are filtered by group, skill policy, host-bash policy, vision support, MCP availability, and deferred-tool policy before binding.', { color: COLORS.gate, fill: 'none' }),

    box('gateway-run-manager', 4400, 330, 420, 160, 'RunManager · process-local\npath-thread admission\ncreate/reject/interrupt/rollback\nstatus + cancellation + configured store', { color: COLORS.native }),
    box('gateway-run-worker', 5020, 330, 420, 160, 'Run worker\nconstruct graph · astream events\njournal usage/messages\nterminal classification', { color: COLORS.native }),
    box('gateway-stream-bridge', 5650, 330, 420, 160, 'StreamBridge + SSE\nreplay/resume · disconnect policy\nvalues/messages/custom events', { color: COLORS.native }),
    box('gateway-stores', 4400, 790, 420, 220, 'Configured stores · durable or memory\ncheckpointer · LangGraph store\nrun rows · thread metadata\nevent journal · feedback', { color: COLORS.state }),
    box('gateway-workspace', 5020, 790, 420, 220, 'Thread workspace\n/mnt/user-data uploads/outputs\ntool-result externalization\nskill files · artifact API', { color: COLORS.state }),
    box('gateway-services', 5650, 790, 420, 220, 'Side services\nMCP sessions/OAuth\nlong-term memory queue\nchannels · N7 suggestions\nauth/CSRF/permissions', { color: COLORS.state }),

    box('loop-human-message', 180, 1460, 240, 160, 'HumanMessage\n+ dynamic reminder\n+ activated skill body', { color: COLORS.state }),
    box('loop-before-model', 750, 1450, 330, 180, 'before_model chain\ncontext compression\nstate/tool filtering\nguards and prompt mutation', { color: COLORS.native }),
    box('loop-lead-llm', 1390, 1450, 330, 180, 'N1 · Lead LLM call\nmessages + system prompt\ncurrently bound tool schemas\n→ AIMessage', { color: COLORS.model, strokeSize: 'l' }),
    box('loop-ai-message', 2030, 1450, 330, 180, 'AIMessage\ncontent · reasoning\ntool_calls · usage metadata\nfinish/stop reason', { color: COLORS.state }),
    box('loop-after-model', 2670, 1450, 330, 180, 'after_model chain\nsafety · limits · loop checks\nclarification / title hooks', { color: COLORS.native }),
    box('loop-tool-executor', 2670, 1910, 330, 180, 'Tool executor\nvalidated name + args\nerror/output-budget wrappers', { color: COLORS.tool }),
    box('loop-tool-message', 2030, 1960, 330, 180, 'ToolMessage\nresult / control failure\nartifact pointer / metadata', { color: COLORS.state }),
    box('loop-final-output', 3150, 1450, 330, 180, 'No tool calls → final\nmessage + values snapshot\ncheckpoint + end event', { color: COLORS.live }),
    box('loop-stream-output', 3150, 2410, 330, 190, 'Receiver\nSSE consumer, embedded caller,\nparent task tool, or channel\nreassembles deltas/state', { color: COLORS.live }),
    box('loop-summarizer', 600, 2360, 640, 190, 'N3 · Context summarizer (active, conditional)\n80K-token trigger · retain 16K recent tokens\nsummarize the full discarded span; model inherits the active run model', { color: COLORS.model }),
    box('loop-title', 1320, 2360, 380, 190, 'N4 · Title model\nnative first-exchange hook\ncurrent headless Stage 1 disables it\nbecause the title is unused', { color: COLORS.model, dash: 'dashed' }),
    box('loop-memory', 1780, 2360, 660, 190, 'N5 · Long-term-memory updater (native, background)\nconversation batch → per-user/per-agent memory.json; later turns read it. Current Stage 1 disables it to avoid cross-run state and extra calls.', { color: COLORS.model, dash: 'dashed' }),
    box('loop-stop', 2520, 2500, 480, 150, 'Stopping paths\nfinal answer · interrupt(Command)\nclient cancel/disconnect · timeout\nprovider/tool hard failure', { color: COLORS.gate, fill: 'none' }),

    box('cap-configured-tools', 3890, 1450, 410, 160, 'Configured tools via reflection\nweb/search/fetch · file ops · bash\nprovider adapters · N8 opaque tool-owned model calls', { color: COLORS.tool }),
    box('cap-builtins', 3890, 1710, 410, 160, 'Built-ins\npresent_files · ask_clarification\nview_image · setup/update agent\ntask · tool_search', { color: COLORS.tool }),
    box('cap-mcp-acp', 3890, 1970, 410, 160, 'MCP + ACP\nstartup-cached server tools\noptional deferred discovery\nOAuth/session pool boundaries', { color: COLORS.tool }),
    box('cap-sandbox', 3890, 2230, 410, 160, 'Sandbox / workspace\nlocal or AIO provider\nmounts · uploads · file locks\nhost bash fail-closed by default', { color: COLORS.tool }),
    box('cap-task-tool', 4630, 1710, 330, 160, 'task tool\ndescription + prompt + type\nparent policy/context inheritance', { color: COLORS.tool }),
    box('cap-subagent-executor', 5290, 1710, 330, 160, 'SubagentExecutor\nresolve type/model/tools/skills\nisolated state + timeout/cancel', { color: COLORS.model }),
    box('cap-subagent-tools', 4630, 2050, 330, 170, 'Child-scoped tools\nallowed web/file/prediction tools\nno task recursion · shared global model lease', { color: COLORS.tool }),
    box('cap-subagent-model', 5290, 2050, 660, 170, 'N2 · Subagent model loop\nown system/skill messages + non-recursive tool set\nmodel ↔ tools until final state; run-model inheritance unless overridden', { color: COLORS.model }),
    box('cap-subagent-result', 4630, 2360, 660, 170, 'Subagent result reception\nterminal status + final text + token records → task ToolMessage → parent lead loop', { color: COLORS.live }),
    box('cap-current-policy', 5480, 2490, 700, 210, 'Active Stage-1 policy\nscoped-researcher: web_search, web_fetch, prediction_market_search, read/write_file; deep-research + prediction-markets skills; no task/bash recursion. Context summarization is conditional; native title and persistent-memory LLM calls are disabled.', { color: COLORS.gate, fill: 'none' }),

    box('deploy-vendor', 120, 3060, 300, 180, 'deer-flow-2.0.0/\noptional local source drop\ncross-checked with public v2.0.0\nsix overlay-surface file diffs', { color: COLORS.native }),
    box('deploy-overlay', 120, 3360, 300, 180, 'deerflow_bridge/\ntracked driver + config\ntools · skills · focused overlays', { color: COLORS.live }),
    box('deploy-setup', 650, 3090, 360, 220, 'setup.sh\ncopy/pin + trim + overlay\nbuild isolated Python 3.12 venv\nrefresh tracked integration files', { color: COLORS.live }),
    box('deploy-generated', 1180, 3080, 300, 240, 'deer-flow/\ngitignored assembled runtime\nactual current Stage-1 imports\nexisting checkout is preserved', { color: COLORS.live }),
    box('deploy-drift', 650, 3500, 830, 350, 'Source and reproducibility boundary\nThe public v2.0.0 tag and optional local drop are the native-reference authority. An ordinary clone currently falls back to older setup pin 799bef6d…; the public v2.0.0 commit is 7e7f0410…. The preserved generated base does not encode its exact upstream provenance. Tracked overlays remain the current integration authority.', { color: COLORS.gate, fill: 'none' }),

    box('integration-current-api', 1740, 3030, 280, 160, 'Current browser/API entry\nPOST /api/research/run\nstatus + full progress\nreport/artifact reception', { color: COLORS.live }),
    box('integration-orchestrator', 2110, 3030, 330, 180, 'Flask PipelineOrchestrator\nquestion + depth/model/lang\n3 isolated evidence lanes; broad baseline alone owns shared actor plane\ncancel/resume + manifest policy', { color: COLORS.live }),
    box('integration-bridge', 2540, 3030, 330, 180, 'subprocess process group\ndeerflow_research.py\nembedded DeerFlowClient\nstdout progress + file handoff', { color: COLORS.live }),
    box('integration-existing-kg-mcp', 2540, 3370, 330, 170, 'Conditional-current existing-KG MCP\nfork / continue / resume-with-graph\nRESEARCH_MCP_KG + graph ID\nnormal first run does not attach it', { color: COLORS.tool, dash: 'dashed' }),
    box('integration-prepass-market', 2540, 3620, 330, 160, 'Optional market pre-pass\nmodel query derivation + relevance scoring\nthen Gamma/CLOB HTTP snapshot', { color: COLORS.tool, dash: 'dashed' }),
    box('integration-track-a', 3100, 3000, 460, 230, 'Default Track A · 3 evidence lanes\nopening → scope → primary / actor / contradiction passes → forecast implications → KIQ convergence\nlead + scoped-researcher model/tool loops\neach lane seals its own evidence pack', { color: COLORS.model }),
    box('integration-track-b', 3100, 3370, 460, 210, 'Default shared Track B · broad baseline only\nsemantic cast + 17-dimension deep actor research\nexact receipt/quote-bound claims · grounded relations\ntyped gaps + finite exact-input judge/refine\nother lanes must not emit it', { color: COLORS.model, strokeSize: 'l' }),
    box('integration-global', 3740, 3000, 370, 330, 'Default global synthesis\n3 sealed Track-A packs + 1 lineage/receipt/claim-sealed actor dossier\n→ manifest-v3 exact-byte binding\n→ outline + one call per section\n→ visible report audit: actor + exact claim + admitted citation per behavior family\n→ optional judge/patch/rejudge', { color: COLORS.model }),
    box('integration-contract', 4400, 3020, 330, 400, 'Validated Stage-1 handoff\nreport + actor dossier/coverage/judge + lineage\nstructured extraction + bounded recovery\nactor-intelligence/v1: semantic IDs, 17 dimensions, exact source support, grounded relations, typed gaps\nordered/multiset actor + claim/relation/family seals\nsources/timeline/quant/contested/markets/charts/metadata', { color: COLORS.state }),
    box('integration-count', 1810, 3950, 2960, 270, 'No truthful fixed provider-call count\nAn admitted model/tool loop is 1..N, but middleware can short-circuit at 0. Default breadth is 3 Track-A lead loops plus at most 9 harness-native child loops globally, and exactly one baseline Track-B research/coverage/synthesis loop. Track B may add bounded judge/refine turns. Global multipart synthesis, report judge/patch, extraction recovery, markets, summarization, retries/fallback, resume, and tool-owned models add conditional calls. The JSON inventories enumerate 42 stable logical call families and 68 subsystem interfaces.', { color: COLORS.gate, fill: 'none' }),

    box('receiver-knowledge', 5090, 3090, 320, 210, 'Current downstream\nontology gets structural ID/type + source-bound claims only\nGraphiti seed manifest + physical readback\nPREPARE actor-context/v1 → deterministic role/config → exact OASIS bytes\nouter PREPARE reseals; runner + direct child revalidate', { color: COLORS.live }),
    box('receiver-report', 5090, 3710, 320, 180, 'Report/forecast plane\nReportAgent + graph/sim tools\nforecast extraction/audits\ninteractive/PDF outputs', { color: COLORS.live }),
    box('receiver-status', 5480, 3000, 740, 300, 'Architectural approach comparison\nA · Native DeerFlow 2 gateway: complete thread/run/SSE platform; not the current Stage-1 transport.\nB · Current embedded bridge: live, specialized, subprocess-isolated, file-contract integration.\nC · DRF2 target: checked in but pre-cutover; it combines a chat-native agent topology with a separate deterministic workflow topology.', { color: COLORS.gate, fill: 'none' }),
    box('receiver-drf2-chat', 5480, 3420, 300, 210, 'DRF2 chat-native topology\nlead research agent + four custom agents\nskills + harness task delegation\nKG and simulation tools through MCP', { color: COLORS.future, dash: 'dashed' }),
    box('receiver-drf2-engines', 5900, 3420, 300, 230, 'DRF2 external engines\nKG stdio MCP server\nsimulation stdio MCP server\nheavy state remains outside the harness\npre-cutover / optional', { color: COLORS.future, dash: 'dashed' }),
    box('receiver-drf2-driver', 5390, 3910, 310, 200, 'DRF2 deterministic driver topology\nstate machine + manifests + gates\nensemble outside the model\nprovisional simulation HTTP client\n(no server adapter)', { color: COLORS.future, dash: 'dashed' }),
    box('receiver-drf2-gateway', 5890, 3910, 310, 240, 'Native Runs API skill-run transport\none persistent DF2 thread\nslash-skill lead runs\nseparate from chat/custom-agent topology', { color: COLORS.future, dash: 'dashed' }),
  ]

  return [...frames, ...arrows, ...boxes]
}

async function blobToBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  const stride = 0x8000
  for (let i = 0; i < bytes.length; i += stride) {
    binary += String.fromCharCode(...bytes.subarray(i, i + stride))
  }
  return btoa(binary)
}

function App() {
  const [status, setStatus] = useState('waiting for tldraw')

  const onMount = useCallback(async (editor) => {
    try {
      setStatus('building editable scene')
      const scene = buildScene()
      const sceneIds = scene.map((shape) => shape.id)
      const uniqueSceneIds = new Set(sceneIds)
      if (uniqueSceneIds.size !== scene.length) {
        throw new Error(`duplicate shape IDs: declared ${scene.length}, unique ${uniqueSceneIds.size}`)
      }

      const arrowShapes = scene.filter((shape) => shape.type === 'arrow')
      const arrowNames = new Set(arrowShapes.map((shape) => shape.id.replace('shape:', '')))
      const bindingNames = new Set(Object.keys(ARROW_BINDINGS))
      const missingBindingSpecs = [...arrowNames].filter((name) => !bindingNames.has(name))
      const orphanBindingSpecs = [...bindingNames].filter((name) => !arrowNames.has(name))
      if (missingBindingSpecs.length || orphanBindingSpecs.length) {
        throw new Error(
          `arrow binding map mismatch; missing=${missingBindingSpecs.join(',') || 'none'}; orphan=${orphanBindingSpecs.join(',') || 'none'}`,
        )
      }

      for (const [arrowName, endpointNames] of Object.entries(ARROW_BINDINGS)) {
        for (const endpointName of endpointNames) {
          if (!uniqueSceneIds.has(id(endpointName))) {
            throw new Error(`arrow ${arrowName} references missing endpoint ${endpointName}`)
          }
        }
      }

      editor.createShapes(scene)
      const bindings = arrowShapes.flatMap((shape) => {
        const arrowName = shape.id.replace('shape:', '')
        const [fromName, toName] = ARROW_BINDINGS[arrowName]
        return [
          {
            id: createBindingId(),
            type: 'arrow',
            fromId: shape.id,
            toId: id(fromName),
            props: {
              terminal: 'start',
              normalizedAnchor: { x: 0.5, y: 0.5 },
              isPrecise: false,
              isExact: false,
              snap: 'none',
            },
          },
          {
            id: createBindingId(),
            type: 'arrow',
            fromId: shape.id,
            toId: id(toName),
            props: {
              terminal: 'end',
              normalizedAnchor: { x: 0.5, y: 0.5 },
              isPrecise: false,
              isExact: false,
              snap: 'none',
            },
          },
        ]
      })
      editor.createBindings(bindings)
      editor.selectNone()
      editor.zoomToFit({ animation: { duration: 0 }, inset: 24 })

      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const shapeIds = [...editor.getCurrentPageShapeIds()]
      const tldrBlob = await serializeTldrawJsonBlob(editor)
      const svgResult = await editor.getSvgString(shapeIds, {
        background: true,
        padding: 70,
        scale: 1,
      })
      if (!svgResult) throw new Error('tldraw SVG export returned no result')
      const pngResult = await editor.toImage(shapeIds, {
        format: 'png',
        background: true,
        padding: 70,
        pixelRatio: 1,
        scale: 1,
      })

      setStatus('writing .tldr / SVG / PNG')
      const response = await fetch('/__export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tldrBase64: await blobToBase64(tldrBlob),
          svg: svgResult.svg,
          pngBase64: await blobToBase64(pngResult.blob),
          metadata: {
            generated_at_utc: new Date().toISOString(),
            generator: 'tldraw 5.2.5 via official browser SDK export APIs',
            page_count: 1,
            shape_count: shapeIds.length,
            declared_shape_count: scene.length,
            declared_unique_shape_count: uniqueSceneIds.size,
            arrow_count: arrowShapes.length,
            binding_count: bindings.length,
            svg_width: svgResult.width,
            svg_height: svgResult.height,
            png_width: pngResult.width,
            png_height: pngResult.height,
            source: 'tldraw-generator/src/main.jsx',
          },
        }),
      })
      if (!response.ok) throw new Error(await response.text())
      const result = await response.json()
      setStatus(`ready · ${shapeIds.length} shapes · ${result.outputDir}`)
      window.__DEERFLOW2_DIAGRAM_READY__ = true
      window.__DEERFLOW2_DIAGRAM_RESULT__ = result
    } catch (error) {
      console.error(error)
      setStatus(`export failed: ${error?.message || error}`)
      window.__DEERFLOW2_DIAGRAM_ERROR__ = String(error?.stack || error)
    }
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <Tldraw onMount={onMount} autoFocus hideUi />
      <div
        id="export-status"
        style={{
          position: 'fixed',
          right: 16,
          bottom: 16,
          zIndex: 9999,
          maxWidth: 560,
          padding: '10px 14px',
          borderRadius: 8,
          background: 'rgba(16, 24, 40, 0.92)',
          color: '#fff',
          font: '13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace',
          boxShadow: '0 4px 18px rgba(0,0,0,.22)',
        }}
      >
        {status}
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
