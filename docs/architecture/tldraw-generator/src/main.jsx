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

const C = {
  control: 'blue',
  stage: 'green',
  model: 'violet',
  tool: 'orange',
  state: 'light-blue',
  gate: 'red',
  conditional: 'grey',
  text: 'black',
}

const sid = (name) => createShapeId(name)

function frame(name, x, y, w, h) {
  return {
    id: sid(`frame-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`),
    type: 'frame',
    x,
    y,
    props: { w, h, name },
  }
}

function geo(name, x, y, w, h, label, options = {}) {
  return {
    id: sid(name),
    type: 'geo',
    x,
    y,
    props: {
      w,
      h,
      geo: options.geo || 'rectangle',
      color: options.color || C.text,
      fill: options.fill || 'semi',
      dash: options.dash || 'solid',
      size: options.stroke || 'm',
      font: options.font || 'sans',
      align: options.align || 'middle',
      verticalAlign: options.verticalAlign || 'middle',
      richText: toRichText(label),
    },
  }
}

function text(name, x, y, w, label, options = {}) {
  return {
    id: sid(name),
    type: 'text',
    x,
    y,
    props: {
      w,
      autoSize: false,
      color: options.color || C.text,
      size: options.size || 'm',
      font: options.font || 'sans',
      textAlign: options.align || 'start',
      richText: toRichText(label),
    },
  }
}

function arrow(name, x1, y1, x2, y2, options = {}) {
  return {
    id: sid(name),
    type: 'arrow',
    x: x1,
    y: y1,
    props: {
      start: { x: 0, y: 0 },
      end: { x: x2 - x1, y: y2 - y1 },
      color: options.color || C.text,
      dash: options.dash || 'solid',
      size: options.size || 'm',
      bend: options.bend || 0,
      richText: toRichText(options.label || ''),
      arrowheadStart: options.start || 'none',
      arrowheadEnd: options.end || 'arrow',
    },
  }
}

function buildScene() {
  const frames = [
    frame('A · Input, API and workflow authority', 80, 220, 950, 1510),
    frame('B · Stage 1 — DeerFlow 2 research (0–30%)', 1070, 220, 2880, 1510),
    frame('C · Stage 2 — Ontology (30–40%)', 3990, 220, 900, 720),
    frame('D · Stage 3 — Graph (40–60%)', 4930, 220, 1050, 720),
    frame('E · Stage 4 — Prepare (60–72%)', 6020, 220, 1000, 720),
    frame('F · Stage 5 — OASIS simulation (72–92%)', 3990, 980, 3030, 1530),
    frame('G · Stage 6 — Report, forecast and publication (92–100%)', 80, 1770, 3870, 1750),
    frame('H · Outputs, feedback and post-run lifecycle', 3990, 2550, 3030, 970),
    frame('I · Cross-cutting stores, providers, operations and pre-cutover surfaces', 80, 3560, 6940, 1100),
  ]

  const boxes = [
    text('title', 100, 34, 4200, 'DeepResearchForecast · complete system architecture', { size: 'xl' }),
    text('subtitle', 105, 104, 4300, 'Current source snapshot · DeerFlow 2 is the live Stage-1 subsystem · every material handoff is indexed in JSON', { color: 'grey' }),
    geo('legend-control', 4430, 45, 220, 75, 'Control / API', { color: C.control }),
    geo('legend-stage', 4665, 45, 220, 75, 'Live stage', { color: C.stage }),
    geo('legend-model', 4900, 45, 220, 75, 'Model call', { color: C.model }),
    geo('legend-tool', 5135, 45, 220, 75, 'Tool / external', { color: C.tool }),
    geo('legend-state', 5370, 45, 220, 75, 'State / artifact', { color: C.state }),
    geo('legend-gate', 5605, 45, 220, 75, 'Gate / invariant', { color: C.gate, fill: 'none' }),
    geo('legend-conditional', 5840, 45, 260, 75, 'Dashed = conditional', { color: C.conditional, dash: 'dashed' }),
    geo('legend-index', 6120, 45, 840, 75, '95 material flows · 101 Flask routes · 100-family normalized model census\n(including the 42-family DeerFlow 2 subsystem inventory)', { color: C.state }),

    geo('human-input', 130, 330, 300, 180, 'User / operator input\nUI: question · mode · depth · rounds\nlanguage · model\nAPI/compat: project + scenario overlay', { color: C.control }),
    geo('vue-cockpit', 560, 330, 390, 180, 'Vue 3 unified /research cockpit\nform · 6-stage timeline · logs\ndossier · graph · simulation · embedded report\nlocalStorage active ID + drf_locale', { color: C.control }),
    geo('flask-api', 130, 620, 390, 210, 'Flask API/security plane\nauth token/loopback · CORS · redaction\nvalidation · provider preflight\n{success,data,error} envelopes', { color: C.control }),
    geo('route-families', 560, 620, 390, 290, '101 exact HTTP interfaces\nresearch/control/dossier · projects/files\ngraph/tasks · simulations/IPC\nreports/forecast/viz/export/Q&A\nsettings · telemetry · optional /api/v1\n→ dataflow-inventory.json', { color: C.state }),
    geo('orchestrator', 130, 1010, 390, 240, 'PipelineOrchestrator\nRESEARCH → ONTOLOGY → GRAPH\n→ PREPARE → RUN → REPORT\nadmission · daemon owner · heartbeat\nstage reuse · cancel/resume/continue/fork', { color: C.control, stroke: 'l' }),
    geo('pipeline-state', 560, 1010, 390, 240, 'Four distinct run artifacts\npipeline_state.json = lifecycle authority\nrun.json = best-effort launch snapshot\nhandoff/manifest = stage artifact index\nresearch_contract_manifest = Stage-1 seal', { color: C.state }),
    geo('control-branches', 130, 1370, 390, 230, 'Control receptions\nstatus/progress polling · visibility pause\ncancel process groups · startup recovery\nresearch_only seal → continue\nscenario fork starts at PREPARE', { color: C.control }),
    geo('ephemeral-control', 560, 1370, 390, 230, 'Ephemeral convenience state\nTaskManager · in-process events/locks\nbrowser poll generation/localStorage\nnever authoritative after restart', { color: C.conditional, dash: 'dashed' }),

    geo('runtime-sync', 1120, 320, 360, 170, 'Tracked runtime assembly\nsetup/stale guard overlays deerflow_bridge/\nonto gitignored deer-flow/ runtime\noptional local v2 source drop is not shipped', { color: C.stage }),
    geo('research-process', 1540, 320, 360, 170, 'Isolated Stage-1 process group\nmode-0600 prompt file · argv/env\nembedded DeerFlowClient\nprogress stdout + cancellation watcher', { color: C.stage }),
    geo('df2-agent-assembly', 1960, 320, 430, 170, 'DeerFlow 2 agent assembly\nmodel + tools + 4 allowlisted skills\nsubagents + ThreadState/checkpoint\nordered middleware policy stack', { color: C.stage }),
    geo('outer-lanes', 2450, 310, 620, 190, 'Default outer breadth: 3 isolated Track-A evidence lanes\n1 broad baseline · 2 base rates/analogs\n3 incentives/contrarian/markets\neach owns a thread/output/evidence pack; baseline also owns one shared actor plane', { color: C.stage, stroke: 'l' }),
    geo('shared-track-b', 3130, 300, 360, 210, 'Default shared Track B · baseline lane only\nsemantic cast + 17-dimension deep actor research\nexact receipt/quote-bound claims · grounded relations\ntyped gaps + finite exact-input judge/refine', { color: C.stage, stroke: 'l' }),
    geo('existing-kg-mcp', 3550, 320, 350, 170, 'Conditional existing-KG stdio MCP\nonly fork/continue/resume-with-graph\nrequires graph_id + RESEARCH_MCP_KG\nnormal first run has no graph', { color: C.tool, dash: 'dashed' }),

    geo('lead-loop', 1120, 620, 430, 250, 'Per-lane lead-agent loop\nHumanMessage/prompt → lead LLM\n→ AIMessage(tool calls) → executor\n→ ToolMessage observation → next LLM\n→ final message/stream/checkpoint\nvariable calls bounded by recursion/budget', { color: C.model, stroke: 'l' }),
    geo('research-tools', 1620, 620, 370, 250, 'Tool plane\nweb_search · web_fetch\nprediction_market_search · file I/O\noptional KG MCP\nnormalized errors/results return to model', { color: C.tool }),
    geo('harness-subagents', 2060, 620, 420, 250, 'Harness-native scoped subagents\nparent task tool → isolated child state\nchild model ↔ scoped tools → final result\nresult becomes parent ToolMessage\ndefault global cap 9 ≈ max 3/lane', { color: C.model }),
    geo('legacy-fanout', 2550, 620, 400, 250, 'Alternative bridge fan-out\nper-KIQ/per-actor workers (width ≤ 8)\nused only when harness delegation is not owner\nnever stacked with native breadth by default', { color: C.conditional, dash: 'dashed' }),
    geo('research-control', 3020, 620, 400, 250, 'Cross-process research control\nSQLite budgets · model leases\ncache/single-flight · source registry\nprovider health/circuits · retries/fallback\nadmit/deny/observe every operation', { color: C.state }),
    geo('context-summary', 3490, 620, 410, 250, 'Conditional context summarization LLM\ntrigger 80K tokens · retain recent 16K\nsummarize complete discarded span\ninherits active model\ntitle + persistent-memory calls disabled here', { color: C.model, dash: 'dashed' }),

    geo('passes-evidence', 1120, 1030, 520, 260, 'Each Track-A pass sequence\nopening → scope/KIQs → planned primary passes\nactor/incentive + contradiction lanes\nforecast implications → adaptive coverage top-ups\ngap closure / correction / verification\n→ evidence_pack + sources + usage/progress', { color: C.stage }),
    geo('evidence-manifest', 1710, 1030, 420, 260, 'Evidence reception and seal\n3 accepted packs + source identities\nTrack-B question/run/lane/thread lineage\nactor/source/claim/relation/family hashes\n→ immutable global-child inputs', { color: C.state }),
    geo('global-synthesis', 2200, 1030, 430, 260, 'One default global child\n3 sealed Track-A packs + one judged actor dossier\ntool-free outline → section calls\ncast-wide owner must expose exact sealed claims\nactor + admitted citation in visible report text', { color: C.model }),
    geo('research-judge', 2700, 1030, 390, 260, 'Report quality loop\n7-dimension judge bound to exact bytes\nFAIL → streamed targeted top-up\n→ incremental patch or resynthesis\n→ rejudge; bounded rounds', { color: C.gate, fill: 'none' }),
    geo('research-extract', 3160, 1030, 390, 260, 'Contract extraction + enrichment\nactors/sources/timeline/quantitative/contested\ninvalid JSON → one recovery call\nsemantic actor IDs + exact source-support receipts\nclaim/relation/causal seals · typed gaps · coverage', { color: C.model }),
    geo('research-contract', 3620, 1030, 280, 410, 'Manifest-last sealed Stage-1 contract\nreport + actor dossier/coverage/judge\nactor-intelligence/v1 + lineage\nsources/timeline/quantitative/contested\nmarkets · charts · budgets · metadata\nreport/dossier/source/actor + ordered/multiset\nclaim/relation/family seals\n→ exact rollback candidate', { color: C.state, stroke: 'l' }),
    geo('stage1-call-index', 1120, 1410, 940, 190, 'Stage-1 call accounting\nlead/subagent streams are 1..N model turns, not one call; shared Track B adds research + deterministic coverage + synthesis + judge/refine; global synthesis adds outline + S sections + expansions/stitch + report judge/extraction. Inventories record every stable family and multiplicity.', { color: C.gate, fill: 'none' }),
    geo('stage1-research-only', 2130, 1410, 470, 190, 'research_only terminal branch\nseal contract → completed\nlater POST /continue reuses exact contract\nand starts Stage 2', { color: C.conditional, dash: 'dashed' }),
    geo('stage1-default-invariant', 2670, 1410, 880, 190, 'Default invariant\nRESEARCH_GLOBAL_SYNTHESIS=true launches 3 Track-A evidence lanes. Exactly the broad baseline lane also runs the one shared Track B; other lanes must not emit actor dossiers. The judged actor dossier is a required global-writer input, not an optional post-report sidecar.', { color: C.gate, fill: 'none' }),

    geo('project-materialize', 4040, 320, 360, 170, 'Project create/reuse\nnew: report → extracted_text + project.files metadata\nactors retain nested actor-intelligence/v1\nMarkdown is not copied into files/; reused project is not refreshed', { color: C.stage }),
    geo('ontology-llm', 4450, 320, 380, 170, 'OntologyGenerator chat_json\nentity/edge types + attributes\nreport plus bounded actor-intelligence briefing\nprimary selected template; full-context default-template second pass only if non-default yields empty types', { color: C.model }),
    geo('ontology-output', 4140, 620, 590, 190, 'Normalized ontology reception\nproject ontology + handoff/ontology.json\ncurrent v1 exposes structural ID/type/tier/aliases + bounded source-bound claims only\nflat role/stance/memory/topics and unsealed relations are excluded', { color: C.state }),

    geo('graph-builder', 4980, 310, 300, 190, 'GraphBuilder\nseed deterministic actor/claim/relation UUIDs first\npersist full support + causal attributes\nthen sanitize/delimit prose episodes\nseeded canonical identity always wins resolution', { color: C.stage }),
    geo('graph-models', 5330, 310, 300, 190, 'Graphiti model boundaries\nstructured extraction/dedup via chat_json\nlocal sentence-transformer embeddings\noptional BGE reranker (default NoOp)', { color: C.model }),
    geo('graph-store', 5680, 310, 250, 190, 'Temporal graph store\nFalkorDB server/Lite or Kuzu\nnodes · edges · episodes\npositions kept separately', { color: C.state }),
    geo('graph-output', 5050, 620, 800, 190, 'Graph completion/reception\nactor-graph-seed-manifest/v1 + physical readback after seed, mutation, reuse\nsealed aliases collapse only into their actor; type nodes excluded; UUIDs/attrs/endpoints preserved\nmissing/stale/corrupt current-v1 graph rebuilds before PREPARE', { color: C.state }),

    geo('cast-merge', 6070, 300, 270, 210, 'Cast + context seal\ncurrent v1 admits matched eligible actors only\none exact actor-context/v1 pack per actor\npublic / actor-known / modeler-only / contested split\ncomplete typed gap audit', { color: C.stage }),
    geo('profile-llm', 6380, 300, 270, 210, 'Current-v1 role-only compiler · zero LLM calls\nactor-role/v2 is the complete executable persona\nrelevant sourced behavior claims + redacted gap summary\nno graph, report, flat-field or generic fallback\nlegacy profiles retain compatibility LLM path', { color: C.model }),
    geo('config-llm', 6690, 300, 280, 210, 'Current-v1 per-agent config · zero LLM calls\ndeterministic neutral/source-bound cadence, topics and leverage\nseparate complete 64 KiB evidence-gap audit\nshared events use explicit-public evidence only\nlegacy batches retain compatibility LLM path', { color: C.model }),
    geo('ready-store', 6120, 620, 790, 190, 'Fail-closed READY gate + durable prepare outputs\ncast/context/role/profile/config manifests and exact hashes must agree\nscenario/WorldState mutations trigger an idempotent authorized reseal + immediate validation\ncompleted reuse revalidates without rewriting; child hashes its exact loaded bytes', { color: C.state }),

    geo('runner', 4040, 1080, 390, 220, 'SimulationRunner\nrevalidate READY + cast/context/role/profile/config/checkpoint hash chain\nreject missing, stale, mutated or downgraded actor seams\nrotate stale outputs unless exact resume\nlaunch/monitor process group + IPC', { color: C.stage, stroke: 'l' }),
    geo('oasis-env', 4650, 1080, 380, 180, 'OASIS / CAMEL environments\nTwitter + Reddit, or selected platform\nReddit loads exact persona; Twitter loads exact newline-flattened user_char\nmanual follows/posts/events + world clock', { color: C.stage }),
    geo('actor-round-llm', 5060, 1080, 400, 220, 'Per active actor × platform × round\nenv.step → OASIS/CAMEL LLMAction\nexact sealed role + explicit-public world/calendar only\nowner-local sourced facts stay local; modeler-only/contested never become knowledge\nprovider may use internal action/tool loops', { color: C.model }),
    geo('platform-actions', 5510, 1080, 400, 220, 'Action/event reception\nposts · replies · likes · follows · beliefs\nplatform SQLite DBs + action JSONL\nround buffers + telemetry/checkpoints', { color: C.state }),
    geo('round-pair', 5960, 1080, 400, 220, 'Paired-round coordinator\nTwitter + Reddit buffers by calendar round\nunique actor roster + both contexts\nmissing/invalid side is explicit', { color: C.stage }),
    geo('decision-llm', 6410, 1080, 540, 220, 'In-band decision-channel chat_json\none batched call per eligible paired round\nmodel: agent/scenario/magnitude/confidence only\ncode constructs commitments/status\nnon-committed outcomes freeze WorldState', { color: C.model }),
    geo('world-state', 4160, 1460, 430, 230, 'Serial shared WorldState\nvalidated decisions → scenario shares/state\ncalendar-scaled inertia + entropy floor\nonly qualitative delta feeds SocialAgents\nnumeric shares stay hidden; diagnostic-only default', { color: C.state }),
    geo('simulation-ipc', 4660, 1460, 430, 230, 'Conditional file IPC\ninterview subqueries → actor selection\nquestion generation → target-agent interview\nanswer synthesis; env control commands', { color: C.conditional, dash: 'dashed' }),
    geo('simulation-summary', 5160, 1460, 430, 230, 'Terminal simulation store/reception\nsimulation_id + run_state/status\nplatform logs/DB/actions/checkpoints\ndecision/WorldState sidecars + separate run_summary\nREPORT later reads selected detail by ID', { color: C.state, stroke: 'l' }),
    geo('simulation-branches', 5660, 1460, 430, 230, 'Failure/recovery branches\nprocess death/timeout/cancel → explicit state\npost-hoc decision fallback conditional\nexplicit resume requires hash-compatible checkpoint\nautomatic resume is off', { color: C.gate, fill: 'none' }),
    geo('simulation-count', 6160, 1460, 790, 230, 'Simulation call multiplicity\nΣ platforms × rounds × active actors × dependency-owned completions\n+ eligible paired-round decision calls + optional interviews/fallbacks. No fixed total exists; semaphores bound API/CLI concurrency.', { color: C.gate, fill: 'none' }),

    geo('report-inputs', 130, 1850, 430, 250, 'Stage-6 evidence reception\norchestrator passes graph/simulation IDs + selected research fields\nno copied run_summary/result/action payload\nReportAgent loads graph/sim detail by ID + markets/policy\nhistorical calibration later annotates confidence only', { color: C.state }),
    geo('report-preflight', 600, 1850, 410, 250, 'Report readiness + pre-prose probability spine\nprovider/contract/hollow-run checks\nresearch + situation + markets + horizon\nK scenario draws (default K=1) + default red-team\nsimulation pack empty under diagnostic_only', { color: C.model }),
    geo('report-outline', 1070, 1850, 330, 250, 'Outline + Part plan\nsection titles/objectives\nrouted research/graph/simulation evidence\nprogress store initialized', { color: C.model }),
    geo('report-sections', 1440, 1850, 430, 250, 'Concurrent section workers (≤6)\nnative tool-call loop when provider supports it\notherwise ReAct text loop\nmodel → tool → observation → model\n→ final section', { color: C.model, stroke: 'l' }),
    geo('section-repair', 1910, 1850, 380, 250, 'Per-section quality loops\ncritique → optional revision\ncontinuation · language enforcement\nsimulation-leak / missing-slot repair\npersist each accepted section', { color: C.model }),
    geo('part2-assemble', 2330, 1850, 330, 250, 'Initial prose assembly\nordered accepted sections\ncompleted/partial marker\nfull_report candidate\nno Part I/II/III wrapping yet', { color: C.stage }),
    geo('forecast-extract', 2700, 1850, 430, 250, 'Post-assembly forecast finalization\nreuse pinned scenarios; extract binary contracts\nmarket match/divergence + objective criteria\nfallback prose scenario extraction/critique only if spine absent\noptional premortem default off', { color: C.model }),
    geo('presentation-assemble', 3170, 1850, 430, 250, 'Presentation assembly after forecast\nprepend deterministic Part I binaries + inject viz\nPart II LLM synthesis + Part III wrap\nresolution section + language-purity repair\nthen editorial lint/citations', { color: C.stage }),
    geo('report-tools', 1440, 2160, 430, 180, 'Section tool/reception loop\ngraph search · statistics · optional interview\nmarket snapshot → ToolMessage/observation\nreturns only to the requesting section worker', { color: C.tool }),

    geo('report-call-index', 130, 2420, 380, 240, 'Stage-6 model-call accounting\npreflight + spine/red-team + outline + section turns/repairs + binaries/markets + Part II + optional impurity repair, bilingual units and Q&A\n→ llm-call-inventory.json', { color: C.gate, fill: 'none' }),
    geo('report-client', 570, 2420, 430, 240, 'Vue ForecastReport\nloads report/forecast/viz manifest\npolls partial progress while generating\ninteractive charts/TOC/downloads\nterminal pipeline links report_id', { color: C.control }),
    geo('report-api', 1040, 2420, 430, 240, 'Report/API reception\npublication-gated: answer-bearing report/forecast\ndownload · PDF · visualization artifacts\nendpoint-specific: progress/sections/logs\nlist/delete/existence/Q&A and optional /api/v1', { color: C.control }),
    geo('publish-gate', 1510, 2420, 430, 240, 'Publication gate\ncompleted + non-partial\ncurrent hard-policy success\nexact report/forecast hashes + valid audit\notherwise suppress answer-bearing body', { color: C.gate, fill: 'none', geo: 'diamond' }),
    geo('report-store', 1980, 2420, 500, 240, 'Sealed report store\nmeta/outline/progress/sections\nfull_report.md · forecast.json · citations/audit\nagent/console logs · telemetry\ncharts + viz_manifest', { color: C.state }),
    geo('final-audit', 2520, 2420, 430, 240, 'Read-only final audit\nexact full_report.md + forecast.json bytes\npolicy/contracts/artifact identities\nrecords exact hashes; does not rewrite sealed bytes', { color: C.gate, fill: 'none' }),
    geo('report-lint', 2990, 2420, 440, 240, 'Deterministic completion gates\neditorial lint · citation stabilization\nforecast schema/normalization · safety assertions\nno partial body can become publishable', { color: C.gate, fill: 'none' }),

    geo('multiseed', 1250, 2800, 620, 220, 'Multi-seed sidecar (default N=1 = inactive)\nN−1 prepare/run/report lanes; concurrency 1–3 (default 2)\nraw seed forecast accepted; checkpoint/reuse → ensemble\nafter primary report, before health gate; primary bytes unchanged', { color: C.conditional, dash: 'dashed' }),
    geo('pipeline-health', 1980, 2800, 560, 220, 'Final primary pipeline-health gate\nvalidate real report + forecast deliverables\noptional ensemble failure does not erase primary success\nfailure → pipeline failed; pass → terminal state', { color: C.gate, fill: 'none' }),
    geo('pipeline-completed', 2600, 2800, 600, 220, 'Terminal completion reception\nwrite authoritative pipeline_state.json\nstatus = completed · global_progress = 100\natomic save + TaskManager terminal result\npipeline/project/graph/simulation/report IDs', { color: C.state, stroke: 'l' }),

    geo('language-exports', 4040, 2640, 430, 220, 'Language and lazy sidecars\nautomatic finalized EN/ZH variant attempt after audit\nvariant exposure needs primary + variant gates\nmanual request and lazy PDF/brief/digest are publication-bound\nsource sealed bytes remain unchanged', { color: C.state }),
    geo('forecast-ledger', 4580, 2640, 430, 220, 'Production forecast ledger\nappended before final audit/publication\nreport/horizon/scenarios/confidence\nno objective_signals in current writer\nmanual/market resolutions do not mutate row', { color: C.state }),
    geo('resolution', 5120, 2640, 430, 220, 'Post-run resolution\nmonitor reads raw forecast.json (no publication gate)\nprice_track + separate market resolutions/Brier\nneeds_manual exists only in result/monitor Markdown\noptional v1 writes separate resolved.json', { color: C.stage }),
    geo('calibration', 5660, 2640, 430, 220, 'Calibration/evaluation\nonly production-ledger rows already carrying outcomes qualify\nresolved.json and resolutions.jsonl stay disconnected\nno automatic production join; later-run use only if joined', { color: C.stage }),
    geo('scheduled-rerun', 6200, 2640, 760, 220, 'Scheduled rerun + drift (default off; tick 300s)\ncalls PipelineOrchestrator.start for a fresh pipeline; suppresses a duplicate in-flight tick\ncompares raw forecasts/actors/graph without a publication gate\nrecords drift + optional webhook', { color: C.conditional, dash: 'dashed' }),
    geo('postrun-lineage', 4040, 2980, 2920, 330, 'Reverse lineage invariant\nPublished scenario probability → seal/audit → pinned pre-prose spine → research actors/situation/markets/horizon → sealed research/source operations; simulation and final prose are excluded under diagnostic_only.\nPublished binary probability → post-section binary draw from the research dossier (or final-report fallback only if the dossier is absent) + situation + pinned scenarios + markets + horizon; simulation is excluded under diagnostic_only.\nPublished paragraph → sealed section/model-tool trace → research + graph + optionally labeled simulation/WorldState diagnostics. Files are handoff contracts; generated trees are not independent products.', { color: C.gate, fill: 'none' }),

    geo('external-llms', 130, 3670, 700, 220, 'External model transports\napplication LLMClient: OpenAI-compatible API or Claude/Codex CLI\nDeerFlow 2: SDK/direct HTTP provider adapters (CLI-origin auth is credential provenance)\nOASIS/CAMEL: API or CLIModel adapter\ncache/retry/fallback/circuit behavior is transport-specific', { color: C.model }),
    geo('external-web', 900, 3670, 600, 220, 'Research external systems\nFirecrawl / Exa / Jina / direct HTTP\npublic Gamma + CLOB prediction-market APIs\nURL/source policy · fetch cache · hashes\nno market wallet/trading path', { color: C.tool }),
    geo('cross-stores', 1570, 3670, 780, 220, 'Durable stores and artifacts\npipeline authority + launch snapshot + stage manifest + Stage-1 seal\nprojects/files · Graphiti DB/cache · simulations/platform DBs\nreports/sections/audits/viz · ledgers/resolutions/schedules · .env\natomic files + content hashes define reuse boundaries', { color: C.state }),
    geo('process-map', 2420, 3670, 740, 220, 'Process/thread map\nFlask process + orchestrator daemon threads\nStage-1 child process groups (+ outer executor)\nGraphiti dedicated async loop · simulation process group\nReport section executors · standalone monitor/scheduler\ncancel targets owned process groups; locks remain process-local', { color: C.control }),
    geo('ops-observability', 3230, 3670, 650, 220, 'Operations/observability\nprogress JSONL/tails · heartbeat · watcher\nstage/run/model telemetry · token/cost estimates\nprovider health · logs/redaction · artifact manifests\nstartup recovery reconciles durable state with live ownership', { color: C.state }),
    geo('inventory-box', 3950, 3670, 650, 220, 'Mechanical completeness indexes\ndataflow-inventory.json: every material pass + all Flask routes\nllm-call-inventory.json: every current static/logical model site\ndeerflow2/* inventories: native DF2, current Stage 1, DRF2 target detail', { color: C.state }),
    geo('drf2-chat', 4670, 3670, 670, 220, 'DRF2 pre-cutover target A · chat-native\none lead + four custom agents\ncustom skills + KG/simulation stdio MCP\nseparate topology from deterministic driver\nnot current workflow authority', { color: C.conditional, dash: 'dashed' }),
    geo('drf2-driver', 5410, 3670, 740, 220, 'DRF2 pre-cutover target B · deterministic driver\nsix manifest/gate stages → persistent Runs API slash-skill lead runs\nKG MCP through skills; provisional simulation HTTP client has no matching HTTP server adapter\nnot current workflow authority', { color: C.conditional, dash: 'dashed' }),
    geo('architecture-boundary', 6220, 3670, 740, 220, 'Current authority boundary\nfrontend/backend/deerflow_bridge are product source\ndeer-flow/ is generated deployment state\noptional deer-flow-2.0.0/ source drop is ignored/local only\ndrf2/ is implemented scaffold, not a cutover', { color: C.gate, fill: 'none' }),
    geo('failure-taxonomy', 130, 4050, 1200, 300, 'Failure and reception taxonomy\nAdmission failure: no pipeline. Stage failure: persisted error + reusable prior artifacts. Actor failure: identity/alias collision; stale lineage; missing receipt/quote support; invalid tier; typed-gap under-attempt; nonfinite/truncated judge; report-family omission; graph readback drift; cast/context/role/config mismatch; post-seal mutation or downgrade. Every current-v1 seam fails closed.', { color: C.gate, fill: 'none' }),
    geo('default-envelope', 1410, 4050, 1190, 300, 'Default execution envelope\n3 outer Track-A evidence lanes + at most 9 harness children globally; exactly 1 shared baseline Track-B actor plane; one global synthesis child consumes both evidence and actor dossier; one graph, prepare, run and report lane; all eligible matched current-v1 actors survive cast selection regardless legacy cap; Twitter+Reddit; forecast K=1; optional branches remain labeled.', { color: C.gate, fill: 'none' }),
    geo('model-formula', 2680, 4050, 1270, 300, 'Provider-call formula (structural, not a promised total)\nresearch = Σ Track-A lead/child turns + shared Track-B research/coverage/synthesis/judge/refine + global outline/sections/stitch + report judge/extraction/markets\ngraph = Graphiti operations × adapter/json/provider retries + embeddings\nprepare current v1 = zero per-actor persona/config calls; deterministic compile + conditional shared event/horizon calls\nsimulation = platforms × rounds × active actors × dependency turns + decisions/interviews\nreport = spine + outline + section loops/repairs + forecasts + optional translation/Q&A', { color: C.model }),
    geo('authority-invariants', 4030, 4050, 1420, 300, 'Authority and handoff invariants\nPipelineState/orchestrator advances six stages. Stage-1 binds 3 evidence packs + one lineage/receipt/claim/relation-sealed dossier. Current-v1 behavior chain is actor-intelligence → actor-context → actor-role/config → exact OASIS bytes → final config seal. Evidence becomes actor knowledge only when visibility permits. Graph and runner physically revalidate. WorldState advances only from valid decisions; final audit is read-only.', { color: C.gate, fill: 'none' }),
    geo('scope-note', 5530, 4050, 1430, 300, 'Scope of this map\nCurrent DeepResearchForecast workflow from input through post-run feedback, including DeerFlow 2 internals as Stage 1. The original DeerFlow architecture is excluded. Pre-cutover DRF2 surfaces are shown only to prevent them from being mistaken for live authority. Exact source anchors and per-record inputs/outputs live in the linked atlas and JSON inventories.', { color: C.gate, fill: 'none' }),
  ]

  const nodeById = new Map(boxes.filter((shape) => shape.type === 'geo').map((shape) => [shape.id, shape]))
  const links = []
  const specs = []
  function connect(name, from, to, options = {}) {
    const a = nodeById.get(sid(from))
    const b = nodeById.get(sid(to))
    if (!a || !b) throw new Error(`missing endpoint for ${name}: ${from} -> ${to}`)
    links.push(arrow(name, a.x + a.props.w / 2, a.y + a.props.h / 2, b.x + b.props.w / 2, b.y + b.props.h / 2, options))
    specs.push([name, from, to])
  }

  connect('a-human-ui', 'human-input', 'vue-cockpit', { color: C.control })
  connect('a-ui-api', 'vue-cockpit', 'flask-api', { color: C.control })
  connect('a-api-routes', 'flask-api', 'route-families', { color: C.control })
  connect('a-api-orchestrator', 'flask-api', 'orchestrator', { color: C.control })
  connect('a-orchestrator-state', 'orchestrator', 'pipeline-state', { color: C.state })
  connect('a-state-control', 'pipeline-state', 'control-branches', { color: C.control })
  connect('a-orchestrator-ephemeral', 'orchestrator', 'ephemeral-control', { color: C.conditional, dash: 'dashed' })
  connect('a-orchestrator-runtime', 'orchestrator', 'runtime-sync', { color: C.stage, label: 'Stage 1' })
  connect('a-runtime-process', 'runtime-sync', 'research-process', { color: C.stage })
  connect('a-process-assembly', 'research-process', 'df2-agent-assembly', { color: C.stage })
  connect('a-assembly-lanes', 'df2-agent-assembly', 'outer-lanes', { color: C.stage })
  connect('a-lanes-loop', 'outer-lanes', 'lead-loop', { color: C.model })
  connect('a-loop-tools', 'lead-loop', 'research-tools', { color: C.tool })
  connect('a-tools-loop', 'research-tools', 'lead-loop', { color: C.stage, bend: 85 })
  connect('a-loop-subagents', 'lead-loop', 'harness-subagents', { color: C.model, bend: -135 })
  connect('a-subagents-loop', 'harness-subagents', 'lead-loop', { color: C.stage, bend: 135 })
  connect('a-loop-legacy-fanout', 'lead-loop', 'legacy-fanout', { color: C.conditional, dash: 'dashed', bend: -210 })
  connect('a-control-loop', 'research-control', 'lead-loop', { color: C.state, dash: 'dashed', bend: 235 })
  connect('a-loop-summary', 'lead-loop', 'context-summary', { color: C.model, dash: 'dashed', bend: -300 })
  connect('a-kg-tools', 'existing-kg-mcp', 'research-tools', { color: C.tool, dash: 'dashed' })
  connect('a-lanes-trackb', 'outer-lanes', 'shared-track-b', { color: C.stage })
  connect('a-loop-passes', 'lead-loop', 'passes-evidence', { color: C.stage })
  connect('a-passes-manifest', 'passes-evidence', 'evidence-manifest', { color: C.state })
  connect('a-manifest-global', 'evidence-manifest', 'global-synthesis', { color: C.model })
  connect('a-global-judge', 'global-synthesis', 'research-judge', { color: C.model })
  connect('a-judge-global', 'research-judge', 'global-synthesis', { color: C.gate, bend: 60, label: 'FAIL → refine' })
  connect('a-judge-extract', 'research-judge', 'research-extract', { color: C.model, label: 'PASS' })
  connect('a-extract-contract', 'research-extract', 'research-contract', { color: C.state })
  connect('a-trackb-global', 'shared-track-b', 'global-synthesis', { color: C.model, bend: -70, label: 'judged actor dossier' })
  connect('a-trackb-contract', 'shared-track-b', 'research-contract', { color: C.state })
  connect('a-contract-researchonly', 'research-contract', 'stage1-research-only', { color: C.conditional, dash: 'dashed' })
  connect('a-contract-project', 'research-contract', 'project-materialize', { color: C.stage, label: 'Stage 2' })
  connect('a-project-ontology', 'project-materialize', 'ontology-llm', { color: C.model })
  connect('a-ontology-output', 'ontology-llm', 'ontology-output', { color: C.state })
  connect('a-ontology-graph', 'ontology-output', 'graph-builder', { color: C.stage, label: 'Stage 3' })
  connect('a-contract-graph', 'research-contract', 'graph-builder', { color: C.state, dash: 'dashed', bend: 65, label: 'actors + report' })
  connect('a-builder-models', 'graph-builder', 'graph-models', { color: C.model })
  connect('a-models-store', 'graph-models', 'graph-store', { color: C.state })
  connect('a-builder-store', 'graph-builder', 'graph-store', { color: C.state, bend: -40 })
  connect('a-store-graphoutput', 'graph-store', 'graph-output', { color: C.state })
  connect('a-graph-cast', 'graph-output', 'cast-merge', { color: C.stage, label: 'Stage 4' })
  connect('a-contract-cast', 'research-contract', 'cast-merge', { color: C.state, dash: 'dashed', bend: 85 })
  connect('a-cast-profiles', 'cast-merge', 'profile-llm', { color: C.model })
  connect('a-cast-config', 'cast-merge', 'config-llm', { color: C.model, bend: 45 })
  connect('a-profile-ready', 'profile-llm', 'ready-store', { color: C.state })
  connect('a-config-ready', 'config-llm', 'ready-store', { color: C.state })
  connect('a-ready-runner', 'ready-store', 'runner', { color: C.stage, label: 'Stage 5' })
  connect('a-runner-oasis', 'runner', 'oasis-env', { color: C.stage })
  connect('a-oasis-actorllm', 'oasis-env', 'actor-round-llm', { color: C.model })
  connect('a-actor-actions', 'actor-round-llm', 'platform-actions', { color: C.state })
  connect('a-actions-pair', 'platform-actions', 'round-pair', { color: C.stage })
  connect('a-pair-decision', 'round-pair', 'decision-llm', { color: C.model })
  connect('a-decision-world', 'decision-llm', 'world-state', { color: C.state, bend: 85 })
  connect('a-world-oasis', 'world-state', 'oasis-env', { color: C.stage, bend: -85, label: 'next-round digest' })
  connect('a-runner-ipc', 'runner', 'simulation-ipc', { color: C.conditional, dash: 'dashed' })
  connect('a-actions-summary', 'platform-actions', 'simulation-summary', { color: C.state })
  connect('a-world-summary', 'world-state', 'simulation-summary', { color: C.state })
  connect('a-runner-branches', 'runner', 'simulation-branches', { color: C.gate, dash: 'dashed', bend: -90 })
  connect('a-summary-reportinputs', 'simulation-summary', 'report-inputs', { color: C.stage, label: 'simulation_id · Stage 6' })
  connect('a-contract-reportinputs', 'research-contract', 'report-inputs', { color: C.state, bend: 105 })
  connect('a-graph-reportinputs', 'graph-output', 'report-inputs', { color: C.state, bend: -115 })
  connect('a-inputs-preflight', 'report-inputs', 'report-preflight', { color: C.model })
  connect('a-preflight-outline', 'report-preflight', 'report-outline', { color: C.model })
  connect('a-outline-sections', 'report-outline', 'report-sections', { color: C.model })
  connect('a-sections-tools', 'report-sections', 'report-tools', { color: C.tool })
  connect('a-tools-sections', 'report-tools', 'report-sections', { color: C.stage, bend: 70 })
  connect('a-sections-repair', 'report-sections', 'section-repair', { color: C.model })
  connect('a-repair-assemble', 'section-repair', 'part2-assemble', { color: C.stage })
  connect('a-assemble-forecast', 'part2-assemble', 'forecast-extract', { color: C.model })
  connect('a-forecast-presentation', 'forecast-extract', 'presentation-assemble', { color: C.stage })
  connect('a-presentation-lint', 'presentation-assemble', 'report-lint', { color: C.gate, bend: 55 })
  connect('a-lint-audit', 'report-lint', 'final-audit', { color: C.gate })
  connect('a-audit-store', 'final-audit', 'report-store', { color: C.state })
  connect('a-store-gate', 'report-store', 'publish-gate', { color: C.gate })
  connect('a-gate-api', 'publish-gate', 'report-api', { color: C.control })
  connect('a-store-api-admin', 'report-store', 'report-api', { color: C.control, dash: 'dashed', bend: -190 })
  connect('a-api-client', 'report-api', 'report-client', { color: C.control })
  connect('a-audit-auto-translation', 'final-audit', 'language-exports', { color: C.state, dash: 'dashed', bend: -85 })
  connect('a-gate-published-exports', 'publish-gate', 'language-exports', { color: C.state, dash: 'dashed', bend: 125 })
  connect('a-forecast-ledger', 'forecast-extract', 'forecast-ledger', { color: C.state, bend: -105 })
  connect('a-store-resolution', 'report-store', 'resolution', { color: C.stage, bend: -110 })
  connect('a-ledger-calibration', 'forecast-ledger', 'calibration', { color: C.conditional, dash: 'dashed', bend: -100 })
  connect('a-resolution-calibration-gap', 'resolution', 'calibration', { color: C.gate, dash: 'dashed' })
  connect('a-report-multiseed', 'report-store', 'multiseed', { color: C.conditional, dash: 'dashed' })
  connect('a-report-health', 'report-store', 'pipeline-health', { color: C.gate })
  connect('a-multiseed-health', 'multiseed', 'pipeline-health', { color: C.conditional, dash: 'dashed' })
  connect('a-health-completed', 'pipeline-health', 'pipeline-completed', { color: C.state })
  connect('a-drf2chat-inventory', 'drf2-chat', 'inventory-box', { color: C.conditional, dash: 'dashed' })
  connect('a-drf2driver-inventory', 'drf2-driver', 'inventory-box', { color: C.conditional, dash: 'dashed' })

  return { scene: [...frames, ...links, ...boxes], links, specs }
}

async function blobToBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binary)
}

function App() {
  const [status, setStatus] = useState('waiting for tldraw')
  const onMount = useCallback(async (editor) => {
    try {
      setStatus('building editable whole-system scene')
      const { scene, links, specs } = buildScene()
      const ids = scene.map((shape) => shape.id)
      if (new Set(ids).size !== ids.length) throw new Error('duplicate shape IDs')
      editor.createShapes(scene)
      const bindings = specs.flatMap(([name, from, to]) => {
        const fromId = sid(name)
        return [
          {
            id: createBindingId(),
            type: 'arrow',
            fromId,
            toId: sid(from),
            props: { terminal: 'start', normalizedAnchor: { x: 0.5, y: 0.5 }, isPrecise: false, isExact: false, snap: 'none' },
          },
          {
            id: createBindingId(),
            type: 'arrow',
            fromId,
            toId: sid(to),
            props: { terminal: 'end', normalizedAnchor: { x: 0.5, y: 0.5 }, isPrecise: false, isExact: false, snap: 'none' },
          },
        ]
      })
      editor.createBindings(bindings)
      editor.selectNone()
      editor.zoomToFit({ animation: { duration: 0 }, inset: 20 })
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const pageShapeIds = [...editor.getCurrentPageShapeIds()]
      const tldrBlob = await serializeTldrawJsonBlob(editor)
      const svg = await editor.getSvgString(pageShapeIds, { background: true, padding: 70, scale: 1 })
      if (!svg) throw new Error('tldraw SVG export returned no result')
      const png = await editor.toImage(pageShapeIds, { format: 'png', background: true, padding: 70, pixelRatio: 1, scale: 1 })
      const response = await fetch('/__export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tldrBase64: await blobToBase64(tldrBlob),
          svg: svg.svg,
          pngBase64: await blobToBase64(png.blob),
          metadata: {
            generated_at_utc: new Date().toISOString(),
            generator: 'tldraw 5.2.5 via official browser SDK export APIs',
            source_revision: 'fcf7378b2e2fabcfd836fb6e2c512fe153c6727c',
            page_count: 1,
            shape_count: pageShapeIds.length,
            declared_shape_count: scene.length,
            declared_unique_shape_count: new Set(ids).size,
            arrow_count: links.length,
            binding_count: bindings.length,
            svg_width: svg.width,
            svg_height: svg.height,
            png_width: png.width,
            png_height: png.height,
            source: 'tldraw-generator/src/main.jsx',
          },
        }),
      })
      if (!response.ok) throw new Error(await response.text())
      const result = await response.json()
      setStatus(`ready · ${pageShapeIds.length} shapes · ${result.outputDir}`)
      window.__DRF_SYSTEM_DIAGRAM_READY__ = true
      window.__DRF_SYSTEM_DIAGRAM_RESULT__ = result
    } catch (error) {
      console.error(error)
      setStatus(`export failed: ${error?.message || error}`)
      window.__DRF_SYSTEM_DIAGRAM_ERROR__ = String(error?.stack || error)
    }
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <Tldraw onMount={onMount} autoFocus hideUi />
      <div id="export-status" style={{ position: 'fixed', right: 16, bottom: 16, zIndex: 9999, maxWidth: 700, padding: '10px 14px', borderRadius: 8, background: 'rgba(16,24,40,.92)', color: '#fff', font: '13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace' }}>{status}</div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
