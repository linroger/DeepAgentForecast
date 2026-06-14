# EXECPLAN — Tighten & Supercharge the DeerFlow × Graphiti × OASIS Pipeline

> **What this is.** A self-contained, dependency-ordered implementation runbook. The next
> session should be able to open this file and execute it top-to-bottom with no further
> discovery. Every task names exact files (with line anchors), the change, a code sketch,
> and observable acceptance checks. All anchors were verified against the live tree on
> 2026-06-13 by an 11-agent code study + 5-agent synthesis pass.
>
> **Read these first for context (authoritative, already accurate):**
> `ARCHITECTURE.md` (MiroFish engine) and `DEERFLOW_INTEGRATION.md` (the seam + Option C).
> This plan does **not** restate them; it extends them.
>
> **Prime directive of this plan — the "golden thread".** Today three excellent engines
> (DeerFlow deep-research, a local Graphiti knowledge graph, an OASIS multi-agent
> simulation) are wired end-to-end but the seams *leak structure*: the research stage
> learns the cast, the situation, and who-relates-to-whom, then writes a flat
> `actors.json` that drops the relationships and the situation framing; the graph is
> re-extracted from prose (discarding what research already knew); personas are
> relationship-blind; the simulation starts with an **empty** social graph and a silently
> **truncated** horizon; and the report re-mines everything from scratch. The single
> highest-leverage move is to thread **one enriched contract** — a *situation brief* + an
> *actor relationship graph* — from research all the way through graph-seeding, personas,
> the social graph, and the final report. That thread is Phases 1→4; everything else
> hardens or surfaces it.
>
> **Engineering invariant for every task below:** *optional-degrade.* Every new field is
> optional; every consumer falls back to today's behaviour when it is absent. A missing or
> partial handoff must never break the existing pure-LLM path. Gate anything with cost or
> risk behind a `Config` flag (defaults preserve current behaviour unless noted).

---

## 0. TL;DR — the fast path (one day to the headline win)

If you only do six tasks, do these in order — they deliver the user's core ask
(situation brief + relationship graph, threaded into the sim and report):

1. **T1.1** extend the DeerFlow extraction schema + SKILL → `situation_brief` + `relationships[]`.
2. **T1.3** add the `actors.py` helper layer (relationship/brief renderers + follow-graph builder).
3. **T2.1 + T2.2** surface `add_triplet` on the shim and seed researched actors+relations into the KG.
4. **T3.1** inject `relationship_briefing` into personas (and fix the `相关实体` name-destroyer).
5. **T3.2 + T3.3** build & inject the round-0 follow graph from relationships.
6. **T4.1** pin the brief + actors + relationships + dossier into `ReportAgent`.

Then **T3.7** (stop the 72→40 round truncation) and **T2.5+T2.6** (parallel ingest + kill dead
sleep) for cheap, high-value correctness/speed wins.

---

## 1. The entire flow, mapped (current state, with anchors)

```
 USER PROMPT  (one natural-language prediction question; reused verbatim twice)
   │
   ▼
┌─ STAGE 1: RESEARCH ─ DeerFlow 2.0 super-agent (separate venv, Option C subprocess) ──────┐
│  PipelineOrchestrator → DeerFlowResearchRunner.run()  [pipeline_orchestrator.py:338-525] │
│    subprocess: deer-flow/.venv → deerflow_research.py --prompt --out-dir --depth --model  │
│    loads SKILL deep-research [deerflow_bridge/skills/deep-research/SKILL.md]               │
│    depth quick/standard = 1 turn; deep = 6-pass loop (opening map → 5 phases → synth)      │
│      [deerflow_research.py DEPTH_PRESETS:55, DEEP_RESEARCH_PHASES:67, run_research_stage]  │
│    Stage-2 structured pass: build_extraction_prompt [:420] → extract_json_object [:459]    │
│  WRITES handoff/:  research_report.md (required)  ·  actors.json  ·  sources.json          │
│    [deerflow_research.py:855-882]   ── NO timeline.json, NO relationships, NO brief ──     │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │  handoff/{research_report.md, actors.json, sources.json}
   ▼
┌─ STAGE 2: ONTOLOGY ─ what kinds of actors exist? ────────────────────────────────────────┐
│  _actors_to_context(actors) [pipeline_orchestrator.py:657] → additional_context (LOSSY:   │
│     drops memory, influence, key_events, relationships)                                    │
│  OntologyGenerator.generate(text, requirement, additional_context) [ontology_generator.py]│
│     → exactly 10 entity types + 6-10 edge types (edge schema INVENTED from prose)          │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │  ontology dict
   ▼
┌─ STAGE 3: GRAPH ─ build the local knowledge graph (Graphiti + embedded FalkorDB) ─────────┐
│  GraphBuilderService [graph_builder.py] via Zep-compat shim [services/graphiti_client/]    │
│    create_graph → set_ontology → add_text_batches(chunks)  [orchestrator:1144-1166]        │
│    EVERY entity/edge RE-LLM-EXTRACTED from chunked report prose (researched cast discarded) │
│    per-batch time.sleep(1) [graph_builder.py:332] + no-op _wait_for_episodes (dead latency) │
│    shim runs Graphiti async on a bg event loop [runtime.py]; add_episode only (NO triplet) │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │  graph_id (≡ Graphiti group_id ≡ FalkorDB db name)
   ▼
┌─ STAGE 4: PREPARE (Env Setup) ─ turn graph entities into agents ──────────────────────────┐
│  ZepEntityReader.filter_defined_entities(graph_id) [zep_entity_reader.py] → typed cast      │
│     (UNBOUNDED — a deep dossier ⇒ hundreds of agents)                                       │
│  OasisProfileGenerator.generate_profiles_from_entities(..., actors=) [oasis_profile_gen.py] │
│     match_actor(name) → actor_briefing (role/stance/influence/memory) [:524,936]            │
│     ⚠ NO relationship briefing; _build_entity_context emits literal '相关实体' [:453-456]   │
│     → twitter_profiles.csv (user_char = system prompt) / reddit_profiles.json               │
│  SimulationConfigGenerator.generate_config(..., actors=) [simulation_config_generator.py]   │
│     time/event/per-agent activity/initial_posts/platform weights (stepwise LLM)             │
│     actors_digest [:411] grounds stance/influence_weight; poster_name targets posts [:793]  │
│     ⚠ seeds NO initial follow graph; recsys/echo-chamber weights generated then DROPPED     │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │  simulation_config.json + *_profiles.{csv,json}  (sim dir READY)
   ▼
┌─ STAGE 5: RUN ─ OASIS dual-platform simulation (detached subprocess) ─────────────────────┐
│  SimulationRunner.start_simulation [simulation_runner.py:316]  ⚠ default max_rounds=40      │
│     → subprocess.Popen scripts/run_parallel_simulation.py (Twitter ∥ Reddit, asyncio)       │
│     build agent graph from profiles (EMPTY social graph) → env.reset →                       │
│     round-0 inject ONLY initial_posts as ManualAction(CREATE_POST) [:1142-1172]             │
│     round loop: get_active_agents_for_round [:1001] (flat random, influence-BLIND) →         │
│        LLMAction → env.step → actions.jsonl     ⚠ only 6/23 Twitter actions, reply-less      │
│     monitor thread tails actions.jsonl → run_state.json                                      │
│     ⚠ ZepGraphMemoryUpdater feedback loop OFF in pipeline (and would crash key-free)         │
│     loop ends → wait-for-commands (IPC) for live interviews                                  │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │  *_simulation.db + actions.jsonl + run_state.json  (post-sim world, kept alive)
   ▼
┌─ STAGE 6: REPORT ─ ReAct prediction report ──────────────────────────────────────────────┐
│  ReportAgent(graph_id, simulation_id, requirement) [orchestrator:1260]                      │
│     ⚠ dossier/actors/sources in local scope but NOT passed — report re-mines from graph     │
│  plan_outline (1 search + 10 facts) → per-section ReAct loop (≥3 tool calls)                │
│     tools: insight_forge · panorama_search · quick_search · interview_agents [zep_tools.py] │
│     ⚠ NO tool reads structured sim outcomes (get_agent_stats/get_timeline exist, unused)     │
│     contamination defence + conflict_retries (because claude-cli has no native tools)         │
│  → outline.json + section_NN.md + full_report.md                                            │
└───────────────────────────────────────────────────────────────────────────────────────────┘
   │
   ▼
 STAGE 7: INTERACT — chat with ReportAgent · interview live OASIS agents (file IPC)
```

**Frontend mirror (Vue 3 SPA).** Step-0 `ResearchView.vue` (prompt + mode + depth) → live
`StageTimeline` + `ResearchConsole` + `DossierViewer` (report/actors/sources tabs) →
on completion routes into the existing `/report/:reportId` 5-step wizard. ⚠ No situation
brief, no relationship view, no edit-and-continue, no timeline surfacing.

**The leaks, in one list (what this plan fixes):**
- L1 `actors.json` is flat — no `relationships[]`, no `situation_brief` (`deerflow_research.py:420-451`).
- L2 `timeline.json` is contracted but never written.
- L3 ontology edge schema is invented from prose (`_actors_to_context` drops everything but name/type/role/stance).
- L4 KG is re-extracted from prose; the researched cast/relations are discarded (no `add_triplet` path).
- L5 personas are relationship-blind; the `相关实体` placeholder destroys real neighbour names.
- L6 OASIS starts with an **empty** follow graph; emergent structure is randomness, not research.
- L7 the "72-hour" sim is silently cut at round 40 (~44% truncation).
- L8 influence_weight/stance/recsys/echo-chamber are computed then ignored at runtime.
- L9 the graph-memory feedback loop is off (and crashes key-free).
- L10 the report never sees the dossier it paid for; no structured sim-outcome tools.
- L11 bi-temporal `valid_at` is fake (every fact stamped `now()`); communities never built.
- L12 build wastes serial latency (`sleep(1)` + no-op wait); ingest is fully sequential.

---

## 2. The keystone — one enriched contract (single source of truth)

This is the schema every Phase-1..4 task reads or writes. Define it once; treat every new
field as optional everywhere.

```jsonc
// handoff/actors.json  (extended — NEW fields are optional, fail-soft downstream)
{
  "central_question": "string — the refined prediction question",
  "as_of_date": "YYYY-MM-DD — research cutoff (anchors valid_at + the sim clock)",

  "situation_brief": {                       // NEW — the simulation-ready brief
    "current_situation": "2-4 factual sentences: state of play as of as_of_date",
    "context":           "how it got here (causal / historical)",
    "dynamics":          "forces in tension; what is escalating / de-escalating",
    "fault_lines":       ["3-6 issues the actors will argue over"],   // seed-post topics
    "catalysts":         ["events/decisions that would shift the situation"]
  },

  "actors": [
    {"name":"string", "type":"Person|Organization|Media|Government|Platform|Other",
     "role":"string", "stance":"string", "influence":"high|medium|low",
     "memory":"what this actor knows/believes about the event"}
  ],

  "relationships": [                          // NEW — directed typed edges between named actors
    {"source":"string",   // MUST equal an actors[].name
     "target":"string",   // MUST equal an actors[].name
     "type":"ALLY_OF|OPPOSES|COMPETES_WITH|REGULATES|DEPENDS_ON|PARTNERS_WITH|INFLUENCES",
     "sign":"ally|rival|neutral",
     "strength":"high|medium|low",
     "basis":"1-line researched evidence for the edge"}
  ],

  "key_events": [{"date":"YYYY-MM-DD","event":"string"}],   // also promoted → timeline.json
  "hot_topics": ["string"],
  "sources":    [{"title":"string","url":"string"}]          // popped → sources.json
}
```

**Design decisions (reconciled across the synthesis):**
- `type` is the **semantic UPPER_SNAKE** value, so it maps **1:1 to a graph edge name** (no remap
  table) and to a small follow-direction map. `sign`/`strength` are for persona tone + follow weight.
- `situation_brief` lives **inside `actors.json`** (so one dossier read carries it) *and* the UI
  reads it from the same object. `timeline.json` is an **additive promotion** of `key_events`
  (still kept inside `actors.json` for back-compat).
- All readers go through `backend/app/utils/actors.py` (the one fail-soft bridge). No consumer
  parses the raw dict itself.

**Follow-direction map (used by `build_initial_follow_graph` and graph seeding):**

| `type` | graph edge name | round-0 follow semantics |
|---|---|---|
| `ALLY_OF` | `ALLY_OF` | lower-influence → follows higher-influence |
| `PARTNERS_WITH` | `PARTNERS_WITH` | both directions (mutual) |
| `DEPENDS_ON` | `DEPENDS_ON` | source (dependent) → follows target |
| `INFLUENCES` | `INFLUENCES` | target → follows source (audience follows influencer) |
| `REGULATES` | `REGULATES` | source (regulator) → follows target (monitoring) |
| `OPPOSES` | `OPPOSES` | both directions (monitor the rival) |
| `COMPETES_WITH` | `COMPETES_WITH` | both directions (monitor the competitor) |

---

## 3. PHASE 1 — Enriched contract (the keystone). Unblocks everything.

### T1.1 — Emit `situation_brief` + `relationships[]` from DeerFlow  ·  S · **high** · deps: none
**Files:** `deerflow_bridge/deerflow_research.py`, `deerflow_bridge/skills/deep-research/SKILL.md`
**Problem (L1):** `build_extraction_prompt` (`:420-451`) hardcodes a flat schema; the deep
protocol already profiles *actors-and-incentives* (phase 3) and SKILL §8 gathers stance/drivers,
but that relational knowledge has nowhere to land. `main()` (`:855-882`) dumps the parsed object
verbatim to `actors.json`, so **adding fields to the prompt is sufficient** — no writer change for
fields that stay inside `actors.json`.
**Change:**
1. In `build_extraction_prompt`, insert the `situation_brief` object and the `relationships[]`
   array (exactly the §2 schema) into the JSON-schema string, after `hot_topics`, before `sources`.
2. Append a hard rule: *"Emit `relationships` ONLY between actors named in `actors[]`; every edge
   MUST cite a researched basis in `description`/`basis`. Omit speculative edges. Populate
   `situation_brief` from the actors-and-incentives analysis."*
3. In `build_research_prompt` (`:234`), `build_deep_phase_prompt` (`:267`, the actors/incentives
   pass), and `build_synthesis_prompt` (`:291`): add one instruction that the report must make the
   **inter-actor relationships explicit** (who allies/opposes/regulates/depends-on whom) so the
   structured pass has material to extract.
4. SKILL.md §8/§12: add a bullet — *"Record the actor RELATIONSHIP GRAPH (directed, typed, with a
   one-line basis) and a SITUATION BRIEF (current situation, how it got here, dynamics, fault
   lines, catalysts) as first-class structured outputs, not just prose."*
**Sketch:** see §2 for the exact schema block to paste.
**Accept:**
- `deerflow_research.py --prompt <multi-actor q> --out-dir /tmp/h --depth standard` writes
  `actors.json` with a non-empty `situation_brief` (has `current_situation` + `fault_lines`) and a
  `relationships[]` whose every `source`/`target` normalize-matches some `actors[].name`.
- Every `relationships[]` row has a `type` in the 7-value enum and a non-empty `basis`.
- `extract_json_object` still salvages JSON wrapped in prose/fences (unchanged path).
- A single-actor prompt yields `relationships:[]` (or omitted) and still populates `situation_brief`.

### T1.2 — Promote `key_events` to a first-class `timeline.json`  ·  S · medium · deps: T1.1
**Files:** `deerflow_bridge/deerflow_research.py`, `backend/app/services/pipeline_orchestrator.py`
**Problem (L2):** `timeline.json` is in the contract (`DEERFLOW_INTEGRATION.md §3`) but never written;
without dated events there is no clean source of `valid_at` for seeded edges (T2.3).
**Change:** In `main()`, after `sources = obj.pop('sources', None)`, also write
`<out-dir>/timeline.json` from `obj.get('key_events')` when non-empty (keep `key_events` inside
`actors.json` too). In `DeerFlowResearchRunner.run` (`:518`) and `_load_research_handoff` (`:556`)
add `timeline = _read_json(.../timeline.json)` to the returned dict.
**Sketch:**
```python
TIMELINE_FILENAME = 'timeline.json'
key_events = obj.get('key_events')
if isinstance(key_events, list) and key_events:
    (out_dir / TIMELINE_FILENAME).write_text(json.dumps(key_events, ensure_ascii=False, indent=2), 'utf-8')
    meta['timeline_count'] = len(key_events)
# orchestrator: research dict gains  'timeline': _read_json(os.path.join(handoff_dir, 'timeline.json'))
```
**Accept:** standard run writes `timeline.json` (list of `{date,event}`); `actors.json` still has
`key_events`; the research dict carries a `timeline` key (None when absent); no-events run writes
no file and proceeds unchanged.

### T1.3 — The `actors.py` helper layer (every downstream task builds on this)  ·  S · **high** · deps: T1.1
**File:** `backend/app/utils/actors.py`
**Problem:** `actors.py` is the single fail-soft bridge but has no concept of relationships or the
brief. Without these helpers every consumer re-parses the raw dict and re-implements name
resolution. (Also note the `相关实体` name-destroyer lives in the persona path — fixed in T3.1.)
**Change:** add five pure, fail-soft helpers + two small maps, and update the module docstring
(`:1-15`) to document the new `relationships[]`/`situation_brief` shape.
**Sketch (canonical signatures — implement exactly these so all callers line up):**
```python
REL_EDGE_NAME = {  # type → graph edge name (identity, but centralize it)
    'ALLY_OF':'ALLY_OF','OPPOSES':'OPPOSES','COMPETES_WITH':'COMPETES_WITH','REGULATES':'REGULATES',
    'DEPENDS_ON':'DEPENDS_ON','PARTNERS_WITH':'PARTNERS_WITH','INFLUENCES':'INFLUENCES'}
REL_LABEL = {'ALLY_OF':'盟友','OPPOSES':'对立','COMPETES_WITH':'竞争','REGULATES':'监管',
             'DEPENDS_ON':'依赖','PARTNERS_WITH':'伙伴','INFLUENCES':'影响'}

def extract_relationship_rows(actors):
    """Rows whose source AND target normalize-match some actors[].name. [] on bad data."""
    if not isinstance(actors, dict): return []
    rels = actors.get('relationships');  rows = extract_actor_rows(actors)
    if not isinstance(rels, list): return []
    names = {normalize_name(r['name']) for r in rows}
    out = []
    for r in rels:
        if not isinstance(r, dict): continue
        s, t = r.get('source'), r.get('target')
        if s and t and normalize_name(s) in names and normalize_name(t) in names:
            out.append(r)
    return out

def situation_brief_block(actors):
    """Render situation_brief as a compact zh prompt block; '' when absent."""
    sb = actors.get('situation_brief') if isinstance(actors, dict) else None
    if not isinstance(sb, dict): return ''
    parts = []
    for label, key in (('当前态势','current_situation'),('来龙去脉','context'),('张力/动态','dynamics')):
        v = str(sb.get(key,'') or '').strip()
        if v: parts.append(f'### {label}\n{v}')
    for label, key in (('争议断层','fault_lines'),('潜在触发','catalysts')):
        lst = sb.get(key)
        if isinstance(lst, list) and lst: parts.append(f'### {label}\n' + '\n'.join(f'- {x}' for x in lst[:6]))
    return ('## 局势简报（深度研究实证，作为权威背景）\n' + '\n'.join(parts)) if parts else ''

def relationship_briefing(actor_name, actors, max_edges=6):
    """Per-actor social-network block naming REAL counterparties; '' when none."""
    rows = extract_relationship_rows(actors)
    me = normalize_name(actor_name);  out = []
    for r in rows:
        typ = str(r.get('type','')).upper();  s, t = normalize_name(r['source']), normalize_name(r['target'])
        if me == s:   out.append(f"{REL_LABEL.get(typ,'关联')}（{r.get('strength','')}）: {r['target']}")
        elif me == t: out.append(f"被{REL_LABEL.get(typ,'关联')}: {r['source']}")
        if len(out) >= max_edges: break
    return ('## 你的社会关系网（调研实证，互动时据此 @ 相关方）\n' + '\n'.join('- '+x for x in out)) if out else ''

def build_initial_follow_graph(actors, agent_id_by_name):
    """relationships[] → deduped [[follower_id, followee_id]] per the §2 direction map."""
    rows = extract_relationship_rows(actors);  pairs = set()
    def aid(n): return agent_id_by_name.get(normalize_name(n))
    for r in rows:
        s, d = aid(r['source']), aid(r['target']);  typ = str(r.get('type','')).upper()
        if s is None or d is None or s == d: continue
        si = influence_weight(match_actor(r['source'], actors)) or 1.0
        ti = influence_weight(match_actor(r['target'], actors)) or 1.0
        if typ == 'ALLY_OF':                      pairs.add((s, d) if si <= ti else (d, s))
        elif typ in ('PARTNERS_WITH','OPPOSES','COMPETES_WITH'):  pairs.add((s, d)); pairs.add((d, s))
        elif typ == 'DEPENDS_ON':                 pairs.add((s, d))
        elif typ == 'INFLUENCES':                 pairs.add((d, s))
        elif typ == 'REGULATES':                  pairs.add((s, d))
    return [list(p) for p in pairs]

def events_to_schedule(actors, total_rounds, as_of_date, horizon_days=None):
    """key_events → [{round, event, date}] mapped onto [0, total_rounds). Skips pre-as_of events."""
    from app.utils.dates import parse_as_of    # T2.3 helper
    base = parse_as_of(as_of_date);  out = []
    evs = (actors or {}).get('key_events') or []
    spans = [ (parse_as_of(e.get('date')) - base).days for e in evs
              if base and parse_as_of(e.get('date')) and (parse_as_of(e.get('date')) - base).days >= 0 ]
    hz = horizon_days or (max(spans) if spans else 1) or 1
    for e in evs:
        d = parse_as_of(e.get('date'))
        if not d or not base: continue
        span = (d - base).days
        if span < 0: continue
        out.append({'round': min(total_rounds-1, round(span / hz * total_rounds)),
                    'event': e.get('event'), 'date': e.get('date')})
    return out
```
**Accept:** `relationship_briefing('教育部', actors)` for a `REGULATES` edge names the real
counterparty (not `相关实体`); `situation_brief_block` returns a block when present, `''` when
absent; `build_initial_follow_graph(actors, {'教育部':3,'某大学':7})` returns `[[3,7]]` for a
regulate/ally edge and `[]` when names don't resolve; all helpers return `[]`/`''` on a dict with
no new fields (no exception).

---

## 4. PHASE 2 — Knowledge-graph power (seed researched truth; build faster)

### T2.1 — Surface `add_triplet` through the shim (the enabling primitive)  ·  M · **high** · deps: none
**Files:** `backend/app/services/graphiti_client/runtime.py`, `.../client.py`
**Problem (L4):** `graphiti_core.add_triplet(source, edge, target)` exists
(`graphiti-0.29.2/graphiti_core/graphiti.py:1645`) — it resolves/dedups nodes by name+embedding,
generates the fact embedding, and writes a typed `EntityEdge` with `valid_at`. But the shim
`_GraphNamespace` (`client.py:119-175`) exposes only create/set_ontology/add_batch/add/search, and
`runtime.py` only wraps `add_episode`. There is **no path to write a known (subject, predicate,
object) edge**.
**Change:** add `runtime.add_triplet(...)` mirroring the `add_episode` sync→async bridge
(`self.run(coro)` on the per-graph background loop, reusing `_ensure_graph`), and expose
`graph.add_triplet(...)` on `_GraphNamespace`. Accept `valid_at` (for T2.3) and optional
source/target labels (map to ontology entity types, else `Entity`).
**Sketch:**
```python
# runtime.py (near add_episode)
def add_triplet(self, graph_id, source_name, edge_name, target_name, fact,
                valid_at=None, source_label='Entity', target_label='Entity'):
    return self.run(self._add_triplet(graph_id, source_name, edge_name, target_name, fact,
                                      valid_at, source_label, target_label))
async def _add_triplet(self, graph_id, source_name, edge_name, target_name, fact,
                       valid_at, source_label, target_label):
    from graphiti_core.nodes import EntityNode
    from graphiti_core.edges import EntityEdge
    from datetime import datetime, timezone
    g = await self._ensure_graph(graph_id);  now = datetime.now(timezone.utc)
    def labels(x): return ['Entity'] + ([x] if x and x != 'Entity' else [])
    src = EntityNode(name=source_name, group_id=graph_id, labels=labels(source_label), summary='', attributes={})
    tgt = EntityNode(name=target_name, group_id=graph_id, labels=labels(target_label), summary='', attributes={})
    edge = EntityEdge(name=edge_name, fact=fact or f'{source_name} {edge_name} {target_name}',
                      group_id=graph_id, source_node_uuid=src.uuid, target_node_uuid=tgt.uuid,
                      created_at=now, valid_at=valid_at, episodes=[], attributes={})
    await g.add_triplet(src, edge, tgt)
    return edge.uuid
# client.py _GraphNamespace
def add_triplet(self, graph_id, source_name, edge_type, target_name, fact,
                valid_at=None, source_label='Entity', target_label='Entity', **_):
    return self._rt.add_triplet(graph_id, source_name, edge_type, target_name, fact,
                                valid_at, source_label, target_label)
```
**Accept:** `client.graph.add_triplet(gid,'教育部','REGULATES','某大学','基于X')` creates 2 nodes +
1 named edge (visible via `get_graph_data`); calling twice with the same triple does **not**
duplicate (dedup/resolve); a triplet whose endpoints already exist as text-extracted nodes attaches
to them; `valid_at=<date>` stamps the edge (not `now()`).

### T2.2 — Seed researched actors + relationships into the KG before ingest  ·  M · **high** · deps: T1.1, T1.3, T2.1
**Files:** `backend/app/services/graph_builder.py`, `backend/app/services/pipeline_orchestrator.py`, `backend/app/config.py`
**Problem (L4):** the GRAPH stage builds purely from chunked `research_report.md` (`orchestrator:1144-1166`)
and re-extracts everything; the clean cast + relations are rediscovered lossily.
**Change:** add `GraphBuilderService.seed_actors(graph_id, actors, valid_at=None)` that, **after**
`set_ontology` and **before** `add_text_batches`, writes each `relationships[]` row as a triplet
(`type` is the edge name) and each isolated actor as an `IS_A`-to-type node (so high-influence
loners still seed). Call it in the orchestrator GRAPH stage after `set_ontology` (`~:1149`), gated
by `Config.GRAPH_SEED_FROM_ACTORS` (default **true**), wrapped in try/except (log-and-continue).
Text extraction then **enriches** the seeded nodes (graphiti dedup attaches facts).
**Sketch:**
```python
# graph_builder.py
from app.utils.actors import extract_actor_rows, extract_relationship_rows, REL_EDGE_NAME
ACTOR_TYPE_TO_LABEL = {'Person':'Person','Organization':'Organization','Media':'Organization',
                       'Government':'Organization','Platform':'Organization'}
def seed_actors(self, graph_id, actors, valid_at=None):
    rows = extract_actor_rows(actors);  rels = extract_relationship_rows(actors)
    label = {a['name']: ACTOR_TYPE_TO_LABEL.get(a.get('type'),'Entity') for a in rows}
    n = 0; seeded_names = set()
    for r in rels:
        etype = REL_EDGE_NAME.get(str(r.get('type','')).upper())
        if not etype: continue
        try:
            self.client.graph.add_triplet(graph_id, r['source'], etype, r['target'],
                str(r.get('basis') or f"{r['source']} {etype} {r['target']}"), valid_at=valid_at,
                source_label=label.get(r['source'],'Entity'), target_label=label.get(r['target'],'Entity'))
            n += 1; seeded_names |= {r['source'], r['target']}
        except Exception as e: logger.warning('seed edge skipped: %s', e)
    for a in rows:                                   # isolated high-signal actors
        if a['name'] in seeded_names: continue
        try:
            self.client.graph.add_triplet(graph_id, a['name'], 'IS_A', a.get('type','Entity'),
                a.get('role') or a['name'], valid_at=valid_at, source_label=label.get(a['name'],'Entity'))
            n += 1
        except Exception: pass
    return n
# orchestrator GRAPH stage (~1149, after set_ontology):
if Config.GRAPH_SEED_FROM_ACTORS and actors:
    from app.utils.dates import parse_as_of
    try:
        seeded = builder.seed_actors(graph_id, actors, valid_at=parse_as_of((actors or {}).get('as_of_date')))
        upd(8, f'已注入 {seeded} 条调研关系种子…')
    except Exception as e: logger.warning('[%s] actor seeding skipped: %s', state.pipeline_id, e)
```
**Accept:** after a full run, `get_graph_data` shows `ALLY_OF`/`OPPOSES`/`REGULATES`/… edges whose
endpoints are researched actor names and whose `valid_at == as_of_date`; an isolated high-influence
actor appears as a node; seeded names that also appear in prose are not duplicated;
`GRAPH_SEED_FROM_ACTORS=false` (or `actors is None`) ⇒ builds exactly as today.

### T2.3 — Bi-temporal grounding from `as_of_date` / `timeline.json`  ·  S · medium · deps: T2.2
**Files:** `backend/app/services/graph_builder.py`, `.../graphiti_client/client.py`, `pipeline_orchestrator.py`, **new** `backend/app/utils/dates.py`
**Problem (L11):** `add_text_batches` builds `EpisodeData(data, type)` with no date, so every chunk is
stamped `now()` (`runtime.py:292-293`) — collapsing the bi-temporal axis Graphiti was chosen for and
making `panorama_search`'s active/historical split (`zep_tools.py:1188-1278`) spurious.
**Change:** add `utils/dates.py:parse_as_of(s)` (lenient ISO + common zh formats → `datetime|None`).
Thread an optional `reference_time` through `EpisodeData` → `add_text_batches` → `runtime.add_episode`
(param already exists). Default all research chunks to `as_of_date`. Pass per-event dates as
`valid_at` for seeded edges built from `key_events` (extend T2.2 to use the matching event date when
a relationship corresponds to an event).
**Sketch:**
```python
# graph_builder.add_text_batches(..., reference_time=None): EpisodeData(data=c, type='text', reference_time=reference_time)
# EpisodeData dataclass gains reference_time: datetime|None = None ; client.add_batch forwards it
# orchestrator: as_of = parse_as_of((actors or {}).get('as_of_date'))
#   builder.add_text_batches(graph_id, chunks, batch_size=10, progress_callback=cb, reference_time=as_of)
```
**Accept:** sampled research-derived edges carry `valid_at == as_of_date` (not ingest time);
`panorama_search` returns a non-trivial active/historical split when events span dates; missing/
unparseable `as_of_date` falls back to `now()` without crashing.

### T2.4 — `build_communities` → faction structure for sim + report  ·  M · medium · deps: T2.2
**Files:** `.../graphiti_client/runtime.py`, `.../client.py`, `graph_builder.py`, `pipeline_orchestrator.py`
**Problem:** `Graphiti.build_communities(group_ids)` (`graphiti.py:1490`, Leiden + LLM summaries) is
ideal for detecting camps/coalitions but is never called; `echo_chamber_strength` is a dead knob and
the report re-derives coalitions per question.
**Change:** add `build_communities` to runtime + shim (one `self.run` wrapper around
`g.build_communities(group_ids=[graph_id])` returning `[{uuid,name,summary}]`). Call it from
`GraphBuilderService` at the end of build (orchestrator progress band 95→98), best-effort
(catch+log; never fail the build). Persist `communities.json` (sim dir / project) so
`SimulationConfigGenerator` can seed echo-chambers (T3.4) and `ZepToolsService` can expose a
`faction_map` to the report (T4.2).
**Sketch:**
```python
# runtime
async def _build_communities(self, graph_id):
    g = await self._ensure_graph(graph_id);  nodes, _ = await g.build_communities(group_ids=[graph_id])
    return [{'uuid': n.uuid, 'name': n.name, 'summary': getattr(n,'summary','')} for n in nodes]
def build_communities(self, graph_id): return self.run(self._build_communities(graph_id))
# orchestrator GRAPH stage after _wait_for_episodes: communities = builder.build_communities(graph_id); persist
```
**Accept:** ≥1 community node with a non-empty summary for a multi-actor dossier; re-running clears
prior communities (no accumulation across resume); a community LLM error still completes the GRAPH
stage; `communities.json` lists which actors belong to which community.

### T2.5 — Parallelize episode ingest (the dominant build-time win)  ·  M · **high** · deps: none
**Files:** `.../graphiti_client/runtime.py`, `.../client.py`, `config.py`, `pipeline_orchestrator.py`
**Problem (L12):** the shim adds episodes strictly serially (`client.py:139-152`, `runtime.run` blocks
per episode). On a 150-chunk deep report that is N serial LLM extractions; `batch_size=10` changes
only progress granularity, not speed.
**Change:** add `runtime.add_episodes_concurrent(graph_id, episodes, concurrency)` scheduling N
`add_episode` coroutines under an `asyncio.Semaphore` on the existing loop, returning uuids in order.
`client.add_batch` delegates when `Config.GRAPH_BUILD_CONCURRENCY > 1`. Auto-pick concurrency: **3**
for CLI providers (mirroring OASIS), **8** for HTTP. Default `GRAPH_BUILD_CONCURRENCY=1`
(byte-identical to today); document that >1 trades a small dedup-ordering risk for a large speedup.
**Sketch:**
```python
async def _add_episodes_concurrent(self, graph_id, episodes, concurrency):
    sem = asyncio.Semaphore(max(1, concurrency))
    async def one(i, ep):
        async with sem:
            return await self._add_episode(graph_id, name=ep.get('name', f'chunk-{i}'), body=ep['data'],
                       source_type=ep.get('type','text'), reference_time=ep.get('reference_time'))
    return await asyncio.gather(*[one(i, ep) for i, ep in enumerate(episodes)])
```
**Accept:** with `GRAPH_BUILD_CONCURRENCY=8` on an HTTP provider, GRAPH-stage wall-clock for a fixed
~100-chunk report is ≥2× lower than concurrency=1; final node/edge counts within a small tolerance;
concurrency=1 is byte-identical; uuids returned in episode order.

### T2.6 — Remove dead build latency  ·  S · medium · deps: none
**File:** `backend/app/services/graph_builder.py`
**Problem (L12):** `time.sleep(1)` after every batch (`:332`) is cloud rate-limit avoidance — pure
dead latency on a local FalkorDB; `_wait_for_episodes` (`:341-395`) polls `episode.processed` but the
shim always returns `processed=True` (synchronous-on-return), so the ≤600s loop is a no-op advertising
a 65→98% band.
**Change:** delete the `time.sleep(1)` (or gate behind a future `GRAPHITI_REMOTE` flag); collapse
`_wait_for_episodes` to an immediate `progress_callback(..., 1.0)` return (keep the signature). Reclaim
the 65→98 band for `build_communities` (T2.4).
**Accept:** GRAPH wall-clock drops ~`num_batches × 1s`; progress stays smooth (no 65% stall); no call
site breaks.

### T2.7 — Idempotent / resumable graph build  ·  M · medium · deps: T2.2
**Files:** `backend/app/services/pipeline_orchestrator.py`, `graph_builder.py`
**Problem:** the GRAPH resume guard (`:1138-1143`) reuses a graph on the stage flag + `graph_id`
presence only — a graph that built but yielded **0** filtered entities is reused, then PREPARE fails
(`simulation_manager.py:304-308`). Re-runs also mint a fresh `graph_id` and can leave orphan partial
graphs; seeded triplets could double-write.
**Change:** on resume, before reuse, run a cheap entity-count check (`ZepEntityReader` /
`fetch_all_nodes` len > 0); if 0, fall through to rebuild. Make `seed_actors` idempotent (rely on
`add_triplet` name dedup + skip an identical edge already present). Record
`graph_entity_count`/`graph_seeded_edges` in `pipeline_state` for observability + the health check.
**Accept:** resuming a 0-entity graph rebuilds (logged) instead of failing in PREPARE; a second
`seed_actors` pass adds no duplicate nodes/edges; `pipeline_state.json` carries the counts; normal
runs unaffected.

### T2.8 — Feed brief + relationships into the ontology prompt  ·  S · medium · deps: T1.1, T1.3
**Files:** `backend/app/services/pipeline_orchestrator.py`, `backend/app/utils/actors.py`
**Problem (L3):** `_actors_to_context` (`:657-677`) is the only actors→ontology bridge and flattens to
name/type/role/stance for 25 actors + 10 topics — dropping influence, memory, key_events, and
relationships; the ontology LLM invents edge types from prose.
**Change:** extend `_actors_to_context` to append (1) `situation_brief_block(actors)` and (2) a
one-line-per-edge relationship list + an instruction: *"These inter-actor relations were RESEARCHED —
your `edge_types` SHOULD cover their types and your `source_targets` SHOULD connect the entity types
these actors belong to."* Cap relationship lines (~30). Fail-soft and bounded; no `OntologyGenerator`
signature change.
**Accept:** `additional_context` contains the brief + relationship list when present; a
relationship-bearing run yields an ontology whose `edge_types` semantically cover the researched
relation types; back-compat lines unchanged when fields absent.

---

## 5. PHASE 3 — Simulation fidelity (a forum grounded in researched social structure)

### T3.1 — Relationship-aware personas + fix the name-destroyer  ·  M · **high** · deps: T1.1, T1.3
**Files:** `backend/app/services/oasis_profile_generator.py`, `backend/app/utils/actors.py`
**Problem (L5):** personas get `actor_briefing` (own stance) but no relationships — coalition
formation is emergent noise. Worse, `_build_entity_context` (`:453-456`) emits the literal
`相关实体` instead of a neighbour's real name on edges lacking a free-text fact, so even the graph's
own neighbourhood reaches the LLM as "X criticizes (some entity)". Context is clamped to `[:3000]`
(`:696,745`), so hub actors lose the most.
**Change:** (a) fix `_build_entity_context` to resolve `target_node_uuid`/`source_node_uuid` against
`entity.related_nodes` (already carried) and emit the **real** neighbour name + its custom label;
(b) append `relationship_briefing(entity.name, actors)` right after the existing `actor_briefing`
injection (`~:524/534-540`), **prepended** before the 3000-char clamp so it is never truncated.
**Sketch:**
```python
# _build_entity_context (~453): nb = {n['uuid']: n for n in (entity.related_nodes or [])}
#   tgt = nb.get(edge['target_node_uuid']) or nb.get(edge['source_node_uuid'])
#   name = (tgt or {}).get('name') or '(未知)'
#   label = next((l for l in (tgt or {}).get('labels',[]) if l not in ('Entity','Node')), '')
#   line = f"{entity.name} --[{edge['edge_name']}]--> {name}{'('+label+')' if label else ''}"
# generate_single_profile: rb = relationship_briefing(entity.name, actors);  if rb: prompt_head = rb + '\n\n' + prompt_head
```
**Accept:** a matched actor's persona prompt contains a `社会关系网` block naming real allies/rivals;
`_build_entity_context` no longer emits `相关实体` when resolvable; hub-actor relationship block
survives the clamp; unmatched/absent ⇒ identical to today.

### T3.2 — Build the round-0 follow graph in sim-config  ·  M · **high** · deps: T1.1, T1.3
**Files:** `backend/app/services/simulation_config_generator.py`, `backend/app/utils/actors.py`, `backend/app/services/zep_entity_reader.py`
**Problem (L6):** OASIS starts with an empty social graph. The follow-graph builder now exists
(`build_initial_follow_graph`, T1.3) and the `agents_by_name` pattern is already used
(`simulation_config_generator.py:785`).
**Change:** after agent batches assign `agent_id ↔ entity_name`, build
`agent_id_by_name = {normalize_name(cfg.entity_name): cfg.agent_id}` and call
`build_initial_follow_graph(actors, agent_id_by_name)`; **also** enrich from graph edges
(`EntityNode.related_edges` from `filter_defined_entities(enrich_with_edges=True)`) so seeding works
when `relationships[]` is sparse. Persist as `event_config.initial_follows: List[[follower,followee]]`
on `SimulationParameters.to_dict()` (mirror `initial_posts`).
**Accept:** `simulation_config.json` carries `event_config.initial_follows` (≥ N resolvable edges,
every id in `[0,num_agents)`); populated from `related_edges` when no `relationships[]`; no
self-loops/dupes; for an `ALLY_OF` low↔high edge the low-influence agent is the follower.

### T3.3 — Inject the seeded follows as round-0 FOLLOW edges  ·  M · **high** · deps: T3.2
**File:** `backend/scripts/run_parallel_simulation.py`
**Problem (L6):** nothing applies `initial_follows`. `AgentGraph.add_edge(src,dst)` exists (oasis
`agent_graph.py:206`), the `follow` table is real, and `ActionType.FOLLOW` is whitelisted (`:183,201`)
— capability present, unused.
**Change:** after `env.reset()` and the existing `initial_posts` injection (Twitter `~:1172`, Reddit
`~:1345-1383`), read `event_config['initial_follows']` and apply each edge **before** the round loop:
(1) `result.env.agent_graph.add_edge(follower, followee)`; (2) inject
`ManualAction(ActionType.FOLLOW, {'followee_id': followee})` in one batched `env.step`. try/except per
edge; emit an explicit `已建立 N 条初始关注边` log (do **not** silently swallow the batch).
**Sketch:** see digest `sim-inject-round0-follows` for the exact block.
**Accept:** after K follows, the `follow` table has ≥K rows immediately after round 0
(`SELECT COUNT(*) FROM follow`); the log prints the count; empty/absent ⇒ exactly today; an
unresolvable pair is skipped, not fatal.

### T3.4 — Echo-chamber homophily follows (make `echo_chamber_strength` live)  ·  M · medium · deps: T3.2, T3.3
**File:** `backend/app/services/simulation_config_generator.py`
**Problem (L8):** `echo_chamber_strength` is generated but dead; stance shapes the persona string but
never who-sees-whom.
**Change:** after agent batches, cluster `agent_configs` by `(stance bucket, dominant hot_topic)`; add
intra-cluster follow edges with probability `~0.3 × echo_chamber_strength`, plus a smaller
cross-cluster bridge probability for high-influence agents (so narratives can still leak). Append into
the **same** `event_config.initial_follows` list (no new injection path). Store `interested_topics` on
`AgentActivityConfig` if absent so clustering has signal.
**Accept:** high `echo_chamber_strength` ⇒ same-stance follow pairs ≫ opposite-stance; `=0` ⇒ only
relationship-derived follows; high-influence agents keep some cross-cluster bridges; sparse
stance/topic degrades to relationship-only without error.

### T3.5 — Socially-structured per-round activation  ·  M · **high** · deps: none
**File:** `backend/scripts/run_parallel_simulation.py`
**Problem (L8):** `get_active_agents_for_round` (`:1001-1051`) picks a flat random count
(`uniform(5,20)`) then activates by a stance/influence-blind Bernoulli; `influence_weight`/`stance`
are computed but never read; large casts are starved; no recency boost so cascades don't form.
**Change:** weight activation `p = activity_level × (0.5 + 0.5 × normalized_influence) × multiplier`;
add a recency boost (`×1.5` for agents acted/mentioned last round — pass a `last_active` set or query
last-round authors); scale `target_count = min(hard_cap, max(base, ceil(0.2×num_agents))) ×
multiplier`; select with `random.choices(weights=influence)`. Default `influence_weight=1.0` keeps it
safe when absent.
**Accept:** over a run, influence≥2.5 agents act in strictly more rounds than influence≤1.0 (joinable
from `actions.jsonl`); a 60-agent cast's mean active/round exceeds the old ceiling at peak; an
agent active/mentioned in round r has higher activation in r+1; no-influence config runs unchanged.

### T3.6 — Always apply the researched `influence_weight`  ·  S · medium · deps: none
**File:** `backend/app/services/simulation_config_generator.py`
**Problem (L8):** the deterministic override fires only on the rule-based fallback path (`:958-961`);
on the LLM-success path the model is merely *hinted* (`:896-902`), so a matched high-influence actor
can silently get an arbitrary weight — undermining T3.5.
**Change:** after building each `AgentActivityConfig` (BOTH paths, `~:976`), apply
`rw = influence_weight(matched_actors.get(agent_id)); if rw is not None: config.influence_weight = rw`
(optionally align `sentiment_bias` sign to the researched stance).
**Accept:** a matched 'high' actor lands in the 2.5–3.0 band on both paths; unmatched agents unchanged;
no run-to-run drift from LLM variance.

### T3.7 — Stop the silent 72→40 round truncation  ·  S · **high** · deps: none
**Files:** `backend/app/services/simulation_runner.py`, `backend/scripts/run_parallel_simulation.py`, `pipeline_orchestrator.py`, `config.py`
**Problem (L7):** `total_rounds = total_hours×60/minutes_per_round` (≈72) but the classmethod default
`max_rounds=40` (`simulation_runner.py:316`) and the script CLI default usually win, and the
orchestrator passes `max_rounds` only when set (`:1213-1215`). A "72-hour forecast" is structurally cut
~44%, and the truncation is a single log line.
**Change:** default `max_rounds=None` (no cap) when the caller omits it; orchestrator passes
`options.max_rounds or Config.OASIS_DEFAULT_MAX_ROUNDS` (knob honest about the horizon). When a cap is
applied, record `rounds_truncated_from/to` on `SimulationRunState` (first-class, surfaced to UI/report),
not just a log line. Keep an explicit small cap available for smoke runs.
**Accept:** a default full run with a 72h/60min config executes all 72 rounds (final `round_num` in
`run_state.json`); an explicit `max_rounds=N<total` records `rounds_truncated_from/to`; smoke runs
still truncate; `max_rounds=None` runs all computed rounds.

### T3.8 — Replay the researched timeline as mid-sim events  ·  M · medium · deps: T1.1, T1.3
**Files:** `backend/app/services/simulation_config_generator.py`, `backend/scripts/run_parallel_simulation.py`, `backend/app/utils/actors.py`
**Problem:** `EventConfig.scheduled_events` is defined but always `[]` and never executed; the round
loop only injects round-0 `initial_posts`. The sim can't trace the actual event chronology research
mapped.
**Change:** populate `EventConfig.scheduled_events` from `events_to_schedule(actors, total_rounds,
as_of_date)` (T1.3), attaching the highest-influence matched actor as poster. In the round loop, before
active-agent selection, fire any event whose `round == round_num` as `ManualAction(CREATE_POST)` via the
matched poster (reuse the `initial_posts` resolution/injection path). Skip unresolvable/out-of-window
events with a log line.
**Accept:** `scheduled_events` has round indices in `[0,total_rounds)` + resolved posters; a scheduled
event fires at its round (`CREATE_POST` in `actions.jsonl` at the expected round); pre-`as_of`/out-of-
horizon events excluded; no `key_events` ⇒ `[]` and run unchanged.

### T3.9 — Thread the brief into config, posts, personas & the clock  ·  M · medium · deps: T1.1, T1.3, T3.8
**Files:** `backend/app/services/simulation_config_generator.py`, `backend/scripts/run_parallel_simulation.py`, `backend/app/services/oasis_profile_generator.py`
**Problem:** the event/agent LLM reasons over a mid-sentence cut of the 6k-word dossier; `initial_posts`
are authored from a loose digest; agents don't share a common ground-truth situation; the sim clock
isn't anchored to `as_of_date` (so T3.8's round↔date mapping has no anchor).
**Change:** (1) in `_build_context` (`:391-424`) prepend `situation_brief_block(self._actors)` **above**
the truncated `document_text` for both time- and event-config steps; (2) add an event-config rule:
*"author one initial post per `situation_brief.fault_lines` entry, as the most relevant researched actor
(`poster_name`)"*; (3) optionally add bounded **memory-seed** posts (top-K by influence, from
`actor.memory`, tagged `is_memory_seed`) so the world isn't cold at round 0; (4) anchor the sim clock
start to `as_of_date`; (5) prefix a compact brief summary to each persona `user_char` so all agents
share the situation.
**Accept:** assembled context begins with `局势简报` before raw text; generated `initial_posts` span the
fault lines; round-0 has memory-seed posts attributed to high-influence actors; the clock's round→date
mapping starts at `as_of_date`; absent brief ⇒ unchanged; total context stays under `MAX_CONTEXT_LENGTH`.

### T3.10 — Turn the feedback loop ON (key-free) with typed edges  ·  M · **high** · deps: T2.1
**Files:** `backend/app/services/pipeline_orchestrator.py`, `backend/app/services/zep_graph_memory_updater.py`, `backend/app/services/simulation_runner.py`, `config.py`
**Problem (L9):** the pipeline calls `start_simulation` without `enable_graph_memory_update`/`graph_id`
(`:1212-1216`), so the post-sim graph the report mines == the pre-sim graph; and `ZepGraphMemoryUpdater`
raises `ValueError('ZEP_API_KEY未配置')` key-free (`:240-243`).
**Change:** (1) RUN stage: pass `enable_graph_memory_update=Config.SIM_GRAPH_FEEDBACK` (default **true**
for local), `graph_id=graph_id`. (2) `ZepGraphMemoryUpdater.__init__`: when `GRAPH_BACKEND` is local,
construct against the local shim instead of raising. (3) keep the free-text episode batch, but when an
action carries both author + target names (`_enrich_action_context`, `run_parallel_simulation.py:811-833`),
**also** write a typed edge via `add_triplet` (`<A> LIKED/REPLIED_TO/FOLLOWED <B>`, `valid_at = round
timestamp`) so identity + round-level bi-temporality survive. Gate the typed path behind a flag.
**Accept:** key-free construction succeeds; post-sim node/edge count > pre-sim (feedback landed,
verifiable before/after); a sample FOLLOW/LIKE with both names yields a typed edge with a round-derived
`valid_at`; disabling the typed flag reproduces today's free-text behaviour.

### T3.11 — Widen the Twitter action set so threads form  ·  S · medium · deps: none
**File:** `backend/scripts/run_parallel_simulation.py`
**Problem:** only 6/23 Twitter actions are wired (`:179-186`) — no `CREATE_COMMENT`/reply, so Twitter
conversation is reply-less (quote/repost only), losing thread structure and the who-replies-to-whom
signal the feedback loop + report mine. Reddit already wires 13 incl. `CREATE_COMMENT`.
**Change:** add `CREATE_COMMENT` (biggest structural gain) and, after verifying Twitter support in the
installed `camel-oasis 0.2.5`, `SEARCH_POSTS` + `TREND`. Guard with a capability check so an unsupported
action is dropped, not crashed.
**Accept:** a Twitter run produces `CREATE_COMMENT` rows + a non-empty comment table; env builds without
rejecting any whitelisted action; some comments reference an existing `post_id`; removing the additions
reverts cleanly.

### T3.12 — Wire the recsys / echo-chamber weights into OASIS  ·  M · medium · deps: none
**Files:** `backend/scripts/run_parallel_simulation.py`, `backend/app/services/simulation_config_generator.py`
**Problem (L8):** `PlatformConfig` recency/popularity/relevance/`viral_threshold`/`echo_chamber_strength`
are serialized but `oasis.make` gets only agent_graph/platform/db/semaphore (`:1117-1122,1320-1325`) — the
recsys-tuning config is **dead**; OASIS runs at platform defaults.
**Change:** read `config['twitter_config']/['reddit_config']` and map supported knobs onto the OASIS
Platform recsys params (`recsys_type`, `refresh_rec_post_count`, `max_rec_post_len`) the env accepts
(`oasis env.py:50-96`). For weights OASIS can't consume, map to the nearest supported behavior or
**delete** them so the config stops implying fidelity that doesn't exist. Document which knobs are live.
**Accept:** changing `echo_chamber_strength`/a mapped recsys knob between runs produces a measurable
exposure difference; the env builds with the mapped params (verify against the installed `OasisEnv`
signature); unmappable fields are consumed or removed; a defaults-only config runs unchanged.

### T3.13 — Cap the cast, always retain researched actors  ·  S · medium · deps: none
**Files:** `backend/app/services/zep_entity_reader.py`, `backend/app/services/simulation_manager.py`, `config.py`
**Problem:** `filter_defined_entities` returns the entire typed node set unbounded (`:321-331`); a deep
dossier ⇒ hundreds of agents, each costing 1 persona LLM call + 2 graph searches. An oversized cast also
dilutes researched actors among generic nodes.
**Change:** add `max_agents` (`Config.OASIS_MAX_AGENTS`, e.g. 80). Rank by
`(matched-to-actors? influence_weight : 0, len(related_edges))`, keep the top `max_agents`, **always**
retaining every `actors.json`-matched actor. Log matched/total + dropped counts.
**Accept:** 300 typed nodes + cap 80 ⇒ ≤80 agents and every matched actor present; PREPARE LLM/search
call count drops proportionally; a log line reports composition; fewer nodes than the cap ⇒ unchanged.

### T3.14 — Persist interviews + emit `run_summary.json`  ·  M · medium · deps: T3.10
**Files:** `backend/app/services/zep_tools.py`, `backend/app/services/simulation_runner.py`, `backend/app/services/zep_graph_memory_updater.py`
**Problem:** `interview_agents` answers (the richest end-of-run reflections, `zep_tools.py:1315-1430`) are
summarized into prose then lost; there is no aggregated outcome artifact, so the report re-mines emergent
dynamics via fuzzy search.
**Change:** (1) after `interview_agents_batch`, write each answer as a typed graph fact
(`<agent> STATED_AT_END_OF_SIM <text>`, `valid_at=now`) via the key-free typed updater (T3.10) so
interviews become durable/queryable; (2) add `SimulationRunner.write_run_summary(simulation_id)`
aggregating `actions.jsonl` + SQLite into per-agent final stance/engagement, action-volume-by-round, top
cascades → `run_summary.json`; (3) optionally fold in `build_communities` (T2.4) as a faction split.
**Accept:** post-interview graph contains retrievable `STATED_AT_END_OF_SIM` facts; `run_summary.json`
has per-agent engagement + `action_volume_by_round` + `top_posts`; communities included when enabled;
skipped interviews still produce a summary.

---

## 6. PHASE 4 — Report depth (ground the prediction in the dossier + sim outcomes)

### T4.1 — Pin the brief + actors + relationships + dossier into `ReportAgent`  ·  M · **high** · deps: T1.1, T1.3
**Files:** `backend/app/services/report_agent.py`, `pipeline_orchestrator.py`, `backend/app/api/report.py`, `backend/app/utils/actors.py`
**Problem (L10):** `ReportAgent` is constructed with only `(graph_id, simulation_id, requirement)`
(`orchestrator:1260`, `api/report.py:134`) though `report_md`, `actors`, `sources` are all in local
scope; the prediction re-derives the whole cast/relationships/timeline by blind graph search.
**Change:** add a `situation_brief(actors)` renderer in `actors.py` (central_question + as_of_date +
compact actor table + key_events + hot_topics + relationships). Extend `ReportAgent.__init__` with
optional `situation_brief`, `actors`, `sources`, `research_report` (all default None — preserves manual
3-arg mode). Inject a pinned `【背景档案（深度研究·权威，as-of <date>）】` block into
`PLAN_USER_PROMPT_TEMPLATE` and `SECTION_SYSTEM_PROMPT_TEMPLATE` + a compact sources index for `[S1]`/`[S2]`
citation. At `orchestrator:1260` pass `situation_brief(actors), actors, sources=research.get('sources'),
research_report=report_md`; in manual mode load `handoff/*` from the project dir if present.
**Accept:** the pipeline `ReportAgent` receives a non-empty brief + actors; the plan prompt contains the
背景档案 block (via `agent_log.jsonl`); sections reference researched actor names not independently
surfaced by graph search; manual 3-arg construction still works; absent `actors.json` ⇒ today's
cold-graph path, no error.

### T4.2 — Structured simulation-outcome tools  ·  M · **high** · deps: none
**Files:** `backend/app/services/zep_tools.py`, `backend/app/services/report_agent.py`, `backend/app/services/simulation_runner.py`
**Problem (L10):** the report's 4 tools all bottom out on fuzzy graph search; quantitative findings (who
was most active/influential, action volume per round, who clustered with whom) can only be guessed —
even though `SimulationRunner.get_agent_stats/get_timeline/get_actions/get_run_state` already return
structured dicts.
**Change:** add `ZepToolsService.simulation_outcomes(simulation_id, top_n=15)` (top agents by total
actions, `action_volume_by_round`, action-type breakdown), `coalition_map(graph_id, simulation_id)`
(cluster agents by shared follow/repost/like targets — deterministic, no LLM), and
`opinion_shift(simulation_id, actor_name)`. Register all three in `_define_tools` (`:982-1017`),
`_execute_tool` (`:1019-1124`), `VALID_TOOL_NAMES` (`:1130`), and `all_tools` (`:1363`). Update the
section tool-mix hint to require ≥1 `simulation_outcomes` call for any quantitative claim.
**Accept:** `simulation_outcomes` returns numbers matching `get_timeline`/`get_agent_stats`; a report's
`agent_log.jsonl` shows ≥1 call and the section cites concrete numbers; `coalition_map` returns ≥1 cluster
for a relational run and a valid empty structure otherwise; all three pass `_is_valid_tool_call` and are
in both lists.

### T4.3 — Ground `plan_outline` in the dossier + a real sweep  ·  S · medium · deps: T4.1, T4.2
**File:** `backend/app/services/report_agent.py`
**Problem:** `plan_outline` (`:1202-1239`) designs all sections from one `get_simulation_context` call +
only the first 10 facts (`:1238`), before any deep retrieval — the report's whole structure is decided
blind.
**Change:** prepend the brief to `PLAN_USER_PROMPT_TEMPLATE`; run one `insight_forge(central_question)`
sweep + one `simulation_outcomes(simulation_id)` sweep and feed both digests into the planning prompt;
raise the fact slice 10→25.
**Accept:** `plan_outline` logs the two sweeps; outline sections vary with the sim result (e.g. a
high-cascade run yields a cascade section a quiet run doesn't); the prompt receives >10 facts + the brief;
absent artifacts ⇒ valid ≥2-section fallback.

### T4.4 — Wire dead `REPORT_AGENT_*` knobs + strip dead Zep surface  ·  S · medium · deps: none
**Files:** `backend/app/services/report_agent.py`, `config.py`, `backend/app/services/zep_tools.py`
**Problem:** `config.py:307-309` advertises `REPORT_AGENT_MAX_TOOL_CALLS/…` but `ReportAgent` hardcodes
8/3/2 (`:939-945`) and inline temps; operators can't tune cost/depth. Separately `ZepToolsService.__init__`
(`:425-428`) raises `ValueError` unless `ZEP_API_KEY='local-graphiti'`, `_call_with_retry` carries dead
Zep-429 backoff, and `search_graph` passes `reranker='cross_encoder'` the local shim ignores.
**Change:** read `MAX_TOOL_CALLS`/`MIN_TOOL_CALLS`/`MAX_TOOL_CALLS_CHAT`/`section_temperature` from
`Config.REPORT_AGENT_*` (raise defaults to the current 8/4/2 so behaviour is unchanged); drop the
`ValueError` key guard; replace the Zep-429 branch with a bounded local-error retry; make
`reranker` honor `Config.GRAPHITI_RERANKER` (`'cross_encoder'` only when `=='bge'`).
**Accept:** `REPORT_AGENT_MAX_TOOL_CALLS=3` caps per-section calls at 3 (`agent_log.jsonl`);
`ZepToolsService()` constructs with `ZEP_API_KEY` unset **and** `''`; `search_graph` no longer forces
`cross_encoder`; default config unchanged.

### T4.5 — Native tool calling via DeerFlow `ClaudeChatModel` (retire ReAct hacks)  ·  L · high · deps: T4.1, T4.2
**Files:** `backend/app/utils/llm_client.py`, `backend/app/services/report_agent.py`, `deerflow_bridge/patches/models/claude_provider.py`
**Problem:** the report is a hand-rolled prompt-ReAct loop (regex tool parsing `:1132-1177`,
`conflict_retries` `:1403-1436`, `_looks_contaminated` `:879-888`, `SECTION_FAILURE_PLACEHOLDER`) only
because `claude-cli` has no native tool calling — brittle (an English denylist a model swap bypasses;
bare-JSON misparse). DeerFlow ships `ClaudeChatModel` (`claude_provider.py:49`, a `ChatAnthropic`
subclass on the same Claude Code plan) with real `bind_tools()`.
**Change:** add `LLMClient.supports_native_tools()` + `chat_with_tools(messages, tools_schema, temp)`
backed by `ClaudeChatModel`. Refactor `_generate_section_react` to a structured tool-calling loop
(pass tool schemas, receive `tool_calls`, execute via existing `_execute_tool`, feed back tool-role
messages). **Keep the prompt-ReAct path verbatim as a fallback** for pure-CLI providers (capability gate).
Once native is default for `claude`, delete `conflict_retries` and shrink `CONTAMINATION_MARKERS` to the
interview-timeout strings. **Do this last** — it's the deepest change; gate behind the fidelity wins above.
**Accept:** a section that previously triggered conflict/contamination completes via a structured
`tool_call` with zero regex (`agent_log.jsonl` shows structured args); `_looks_contaminated` no longer
fires on the native path; pure-CLI providers still produce reports via the unchanged fallback; the final
text carries no `<tool_call>` residue.

### T4.6 — What-if scenario re-runs (fork at PREPARE)  ·  L · high · deps: T4.1
**Files:** `pipeline_orchestrator.py`, `backend/app/api/research.py`, `simulation_config_generator.py`, `report_agent.py`
**Problem:** a run is one prompt → one report; no way to ask "what if the regulator intervenes early" or
"2× media influence" without paying for a full new pipeline (research + graph). The resume reuse-guards
already reuse `graph_id`/ontology — the machinery to fork at PREPARE exists but isn't exposed.
**Change:** add `POST /api/research/<id>/scenario` with a `scenario_overlay`
`{label, max_rounds?, influence_overrides{name:weight}, stance_overrides{name:stance},
injected_events[{round,poster_name,content}], as_of_shift?}`. `orchestrator.fork(base, overlay)` clones
`PipelineState` (new id, copies `project_id`/`graph_id`, resets prepare/run/report to pending, stashes
the overlay). `_run` honors it: `SimulationConfigGenerator` applies influence/stance overrides
(deterministically, on both paths — closes the T3.6 bypass) and seeds `injected_events` as
`scheduled_events`; `ReportAgent` opens with the scenario framing. Reuse the graph build (skipped).
**Accept:** `POST /<base>/scenario {label, max_rounds:60}` reuses `base.graph_id` (logged "reused") and
re-runs prepare/run/report only (wall-clock excludes research+graph); an `influence_overrides` value
lands deterministically in `simulation_config.json`; `injected_events` appear mid-sim; the scenario
report names the label; omitted overlay fields leave base behaviour.

### T4.7 — Counterfactual A/B diff report  ·  L · medium · deps: T4.2, T4.6
**Files:** `backend/app/services/zep_tools.py`, `report_agent.py`, `pipeline_orchestrator.py`
**Problem:** each what-if produces an isolated report; the most decision-useful output is the **delta**
(e.g. "under intervention the negative cascade peaks 30% lower and resolves 12 rounds earlier").
**Change:** add `ZepToolsService.scenario_diff(base_sim_id, scenario_sim_id)` computing per-round volume
delta, top-actor activity delta, coalition churn, final-stance shift (deterministic from structured
logs). When a pipeline carries `options.base_pipeline_id`, give `ReportAgent` both `simulation_id`s + the
`scenario_diff` tool and force a `情景对比 / 反事实` outline section. Register like `simulation_outcomes`.
**Accept:** `scenario_diff` numbers reconcile to each sim's `get_timeline`/`get_agent_stats`; a scenario
pipeline produces a `情景对比` section citing concrete base-vs-scenario numbers; the tool passes
validation and is in both lists; absent `base_pipeline_id` ⇒ section omitted, no error.

---

## 7. PHASE 5 — Unified UI (surface the enriched contract; close the loop)

### T5.1 — Situation Brief + Relationship tabs in the dossier  ·  M · **high** · deps: T1.1 (consumes), none (UI)
**Files:** `frontend/src/components/research/DossierViewer.vue`, `frontend/src/views/ResearchView.vue`
**Problem:** `DossierViewer` (`:34-114`) has only report/actors/sources tabs — no brief, no relationships.
**Change:** conditionally push a **Brief** tab (renders `actors.situation_brief`) and a **Relationships**
tab (renders `actors.relationships[]` as `source --[type]--> target` with a sentiment/strength chip + an
optional small D3 force view reusing the `GraphPanel` pattern) into the existing `tabs` computed
(`:192-196`) **only when present**. Additive; the dossier endpoint already returns the full `actors`
object — no backend change.
**Accept:** Brief/Relationships tabs appear only when their fields exist; each edge shows
source→type→target + a chip; tab counts reflect `relationships.length`; old dossiers render as today.

### T5.2 — Surface `timeline.json` in the dossier  ·  S · low · deps: T1.2
**Files:** `backend/app/api/research.py`, `frontend/src/components/research/DossierViewer.vue`
**Problem:** `get_dossier` (`:199-221`) reads report/actors/sources but never `timeline.json`; the
DossierViewer timeline widget feeds only from `actors.key_events`.
**Change:** in `get_dossier` add `timeline = _read('timeline.json')` → `data.timeline`; in DossierViewer
feed the widget from `dossier.timeline` when present, else fall back to `actors.key_events`.
**Accept:** the endpoint returns a `timeline` key when the file exists (null otherwise); the widget renders
from it with fallback; no-timeline runs render as today.

### T5.3 — Highlight researched-seed vs simulation-grown nodes  ·  M · medium · deps: T5.1
**Files:** `frontend/src/components/GraphPanel.vue`, `frontend/src/views/ResearchView.vue`
**Problem:** `GraphPanel` gets a flat `graphData` with no concept of seed vs extracted vs grown — the user
can't see whether the KG absorbed the researched cast or how it evolved.
**Change:** add an optional `seedActors:Array` prop (names from `dossier.actors.actors[].name`); a node
whose NFKC-lowercase name matches gets a `researched-seed` ring + legend entry; use the bi-temporal
`valid_at` (already wrapped by the shim) to tag post-sim-start nodes/edges as `simulation-grown` (third
legend color). Purely client-side.
**Accept:** seed-matched nodes render with a ring + legend; a tri-state legend shows when both inputs
present; empty `seedActors` renders fine.

### T5.4 — Edit-and-continue the dossier (human in the loop)  ·  L · high · deps: T6.2
**Files:** `backend/app/api/research.py`, `frontend/src/components/research/DossierViewer.vue`, `ResearchView.vue`, `frontend/src/api/research.js`
**Problem:** the dossier is read-only; `extracted_text` is seeded from `research_report.md`
(`orchestrator:1112-1116`), so a flawed dossier silently propagates into an expensive run with no edit
gate.
**Change:** add `PUT /api/research/<id>/dossier {report?, actors?}` that atomically overwrites
`handoff/research_report.md`/`actors.json` (tmp + `os.replace`), permitted only when `status==completed`
&& `mode==research_only` (or failed-before-graph). DossierViewer gets an Edit toggle (textarea + editable
actor rows) and a **Save & Continue** that PUTs then calls `continuePipeline(id)` (T6.2). Because `_run`
re-seeds `extracted_text` from the edited report on continue, edits genuinely change downstream fidelity.
**Accept:** PUT overwrites atomically (200); an edited unique phrase appears in a graph node summary after
Save & Continue; edit rejected (409) while running; the toggle shows only for completed research_only.

### T5.5 — Per-run language + research-model override in Step 0  ·  S · low · deps: T6.4
**Files:** `frontend/src/views/ResearchView.vue`, `frontend/src/api/research.js`, `backend/app/api/research.py`, `pipeline_orchestrator.py`
**Problem:** Step 0 collects only prompt/mode/depth/max_rounds; DeerFlow already accepts
`--target-language`/`--model` but there's no per-run override (power users must edit `.env`). The
zh-vs-en mismatch risk is flagged in `DEERFLOW_INTEGRATION.md §8`.
**Change:** add a collapsible **Advanced** row (language select Chinese/English/auto + research-model
select of the 7 `DEERFLOW_MODEL` values). Pass `language`/`model` in the run body; validate `model`
against `Config.SUPPORTED_DEERFLOW_MODELS` (T6.4) + language allowlist; thread into the subprocess argv,
overriding Config defaults only when provided.
**Accept:** `language=English` ⇒ English report (argv shows `--target-language English`); `model=deepseek`
runs on deepseek (preflight enforces `DEEPSEEK_API_KEY`); omitting both falls back to defaults; an invalid
model ⇒ 400 before any subprocess launch.

### T5.6 — `GET /api/research/preflight` readiness endpoint  ·  S · medium · deps: none
**Files:** `backend/app/api/research.py`, `frontend/src/views/ResearchView.vue`, `frontend/src/api/research.js`
**Problem:** `preflight_pipeline` (`pipeline_orchestrator.py:580-654`) runs cheap local checks but is only
callable inline from `POST /run` and `/resume` — the first misconfig signal is a failed submission.
**Change:** add `GET /api/research/preflight?mode=full|research_only` returning `{ready, errors}`. In
ResearchView call it on mount + mode change; show a readiness banner (green ready / red blockers) above
Run and disable Run when not ready. Reuses the exact same check the POST path runs (no drift).
**Accept:** `?mode=full` returns `{ready:true,errors:[]}` on a good host; a missing research-model key ⇒
`ready:false` with the specific blocker; the banner disables Run; errors match `POST /run`'s rejection.

---

## 8. PHASE 6 — Orchestration robustness, observability, provider parity, DX

### T6.1 — Validate artifact health on resume  ·  M · high · deps: none
**File:** `backend/app/services/pipeline_orchestrator.py`
**Problem:** `resume()` (`:937-995`) re-enters `_run` whose reuse guards trust coarse stage flags — a
0-entity graph or a contaminated report is reused, then fails downstream.
**Change:** add cheap health checks to each reuse guard: GRAPH reuses only if `ZepEntityReader` entity
count > 0; PREPARE only if a profiles file exists; REPORT only if the existing report isn't FAILED and
`full_report.md` is non-empty. On failure, record `state.options['resumed_stage_validation']` and fall
through to regenerate. Fail-open toward regeneration; never crash resume.
**Accept:** resuming a 0-entity graph rebuilds (logged) instead of failing in PREPARE; `resumed_stage_
validation` records forced regenerations; a healthy graph still reuses (no needless rebuild); checks never
raise.

### T6.2 — Continue a completed `research_only` run into the full pipeline  ·  M · high · deps: T6.1
**Files:** `pipeline_orchestrator.py`, `backend/app/api/research.py`, `frontend/src/api/research.js`
**Problem:** `research_only` returns after the dossier (`:1087-1098`) and `resume()` refuses
`status=='completed'` (`:956-957`) — the dossier the user paid for is a dead-end (contradicting
`DEERFLOW_INTEGRATION.md §6.1`).
**Change:** add `PipelineOrchestrator.continue_to_full(pipeline_id)` (requires `mode==research_only` +
`status==completed` + a present `research_report.md`): flip `mode='full'`, re-seed the full
`STAGE_BANDS` keys as pending (keep research completed), reuse the `resume()` launch tail. Expose
`POST /api/research/<id>/continue` (preflight first). `_run` reuses the research stage via the report-md
guard and skips the research_only early-return.
**Accept:** `POST /<id>/continue` on a completed research_only run runs ontology→report without
re-running research (no new `research_pid`); reuses `research_report.md`/`actors.json`; calling on a
full/running pipeline ⇒ 409; `api/research.js` gains `continuePipeline(id)`.

### T6.3 — Artifact pointers through `PipelineState` (deep-link each stage)  ·  M · medium · deps: none
**Files:** `pipeline_orchestrator.py`, `backend/app/api/research.py`, `frontend/src/components/research/StageTimeline.vue`, `ResearchView.vue`
**Problem:** `PipelineState` (`:116-169`) carries only graph/sim/report ids — the status poll can't let
the UI navigate to the ontology/personas/initial_posts/sources/timeline the pipeline already produced.
**Change:** add `artifacts: dict[str,str]` (default empty) populated as each stage completes
(`ontology`, `personas`, `initial_posts`, `dossier`, `timeline`); serialize in to/from_dict (tolerate
old files). Add `GET /api/research/<id>/artifact/<name>`. StageTimeline shows a `view →` link per
completed stage with an artifact.
**Accept:** `pipeline_state.json` gains a populated `artifacts` map; `GET .../artifact/ontology` returns
the JSON; the `view →` affordance shows only on completed stages with an artifact; `from_dict` defaults
`{}` for old files.

### T6.4 — Validate `DEERFLOW_MODEL` + its key at boot  ·  S · medium · deps: none
**Files:** `backend/app/config.py`, `pipeline_orchestrator.py`, `scripts/doctor.sh`
**Problem:** `Config.validate()` (`:352-374`) never validates `DEERFLOW_MODEL` or its key; a typo or a
missing `DEEPSEEK_API_KEY` surfaces only 40 minutes into a run. The model→key map is duplicated in three
places and can drift.
**Change:** add a single source of truth `SUPPORTED_DEERFLOW_MODELS` + `DEERFLOW_KEY_ENV` to `config.py`;
in `validate()` error on an unknown model and warn on a missing mapped key (claude/codex need none).
Have `preflight_pipeline._df_key_env` + `doctor.sh` derive from this map.
**Accept:** a typo'd `DEERFLOW_MODEL` fails validation at startup with a clear message; `deepseek` with no
key warns at boot; preflight + doctor reference `Config.DEERFLOW_KEY_ENV`; claude/codex pass keyless.

### T6.5 — codex-cli env hygiene (force plan auth)  ·  S · medium · deps: none
**Files:** `backend/app/utils/llm_client.py`, `backend/app/utils/oasis_llm.py`
**Problem:** `_chat_claude_cli` strips `ANTHROPIC_API_KEY` (override via `LLM_CLI_USE_API_KEY`) but
`_chat_codex_cli` (`:360-403`) passes `os.environ` unmodified — a stray `OPENAI_API_KEY` can silently flip
codex to API billing. Parity is incomplete.
**Change:** add `_codex_cli_env()` symmetric to `_claude_cli_env()` (pop `OPENAI_API_KEY` unless
`LLM_CLI_USE_API_KEY==true`); pass it in the codex `subprocess.run`. Mirror in `oasis_llm.py` if the OASIS
codex path inherits `os.environ`.
**Accept:** with `LLM_PROVIDER=codex-cli` + a stray `OPENAI_API_KEY`, the codex env lacks it (debug log);
`LLM_CLI_USE_API_KEY=true` preserves it; a codex completion still succeeds via plan OAuth.

### T6.6 — Promote DeerFlow knobs into `Config`  ·  S · low · deps: none
**Files:** `backend/app/config.py`, `pipeline_orchestrator.py`, `.env.example`
**Problem:** `DEERFLOW_DEEP_OPENING_RECURSION_LIMIT` is read directly from `os.environ` inside the bridge
(`deerflow_research.py:634,716`), and the depth→timeout map is hardcoded in `DeerFlowResearchRunner.run`
(`:405`) — so `config.py:335`'s `DEERFLOW_RESEARCH_TIMEOUT=10800` default is dead for the common path.
**Change:** add `Config.DEERFLOW_DEEP_OPENING_RECURSION_LIMIT` + `Config.DEERFLOW_DEPTH_BUDGETS =
{'quick':900,'standard':2400,'deep':10800}`; reference them in the orchestrator (pass the recursion limit
through the subprocess env); make `DEERFLOW_RESEARCH_TIMEOUT` an explicit override only (default unset) and
document precedence (explicit > env > depth budget) in `.env.example`.
**Accept:** depth timeouts come from `Config.DEERFLOW_DEPTH_BUDGETS` (no local dict); the recursion limit
is a Config attr visible in `provider_info()`; `.env.example` documents precedence; unset timeout still
yields 900/2400/10800.

### T6.7 — Cost-aware stage progress + per-stage timeline  ·  S · low · deps: T6.3
**Files:** `pipeline_orchestrator.py`, `frontend/src/components/research/StageTimeline.vue`
**Problem:** `STAGE_BANDS` are static (research 30%, run 20%) regardless of real cost; `started_at`/
`finished_at` exist but aren't rendered, so the global bar/ETA mislead.
**Change:** keep static bands as default but optionally re-weight once `chunk_count`/`total_rounds`/
`section_count` are known (store `state.options['dynamic_bands']`; `_global_from_stage` reads it when
present). Surface per-stage elapsed to StageTimeline.
**Accept:** a deep run shows the graph stage occupying a larger share than a shallow run;
`_global_from_stage` uses `dynamic_bands` when present; StageTimeline shows per-stage elapsed; global
progress stays monotonic.

### T6.8 — Reconcile the stale integration doc  ·  S · medium · deps: none
**File:** `DEERFLOW_INTEGRATION.md`
**Problem:** `:14-15,385-388` claim "stage-aware resume/continue … still open", but `resume()`
(`:937-995`) + per-stage reuse guards + `POST /<id>/resume` are implemented. The doc misleads about what
exists.
**Change:** mark resume/cancel/preflight as IMPLEMENTED with anchors; add a "Resume semantics" subsection
(same `pipeline_id`, fresh `task_id`, resets only the failed stage, reuses completed-stage artifacts via
the five `_run` guards); re-scope the genuinely-open items to this plan's ids (T6.1/T6.2/T5.4/T6.3).
**Accept:** the doc no longer claims resume is open and cites `:937-995`; a Resume-semantics subsection
lists the five guards; the open-items list matches this plan.

---

## 9. Dependency graph, recommended order, milestones

```
T1.1 ─┬─ T1.2 ─ T2.3
      ├─ T1.3 ─┬─ T2.2(+T2.1) ─┬─ T2.4 ─┐
      │        │               ├─ T2.7  │
      │        ├─ T2.8         └────────┼─ (richer KG)
      │        ├─ T3.1                  │
      │        ├─ T3.2 ─ T3.3 ─ T3.4    │
      │        ├─ T3.8 ─ T3.9           │
      │        └─ T4.1 ─┬─ T4.3 ─┐      │
      │                 ├─ T4.5  │      │
      │                 └─ T4.6 ─ T4.7  │
      └─ T5.1, T5.2(needs T1.2), T5.3(needs T5.1)

independent (do anytime): T2.1, T2.5, T2.6, T3.5, T3.6, T3.7, T3.11, T3.12, T3.13,
                          T4.2, T4.4, T5.6, T6.1, T6.3, T6.4, T6.5, T6.6, T6.8
T3.10 needs T2.1 ; T3.14 needs T3.10 ; T4.2 feeds T4.3/T4.7 ; T6.2 needs T6.1 ; T5.4 needs T6.2 ;
T5.5 needs T6.4 ; T6.7 needs T6.3.
```

**Milestone A — Golden thread (the user's ask), ~1–2 days:**
T1.1 → T1.3 → T2.1 → T2.2 → T3.1 → T3.2 → T3.3 → T4.1. End state: the researched situation brief +
relationship graph flows into the KG (as real edges), into every persona, into the round-0 social graph,
and into the final report. Verify with the §11 end-to-end check.

**Milestone B — Honest, faster, richer sim, ~1 day:**
T3.7 (un-truncate) + T2.5/T2.6 (parallel ingest, kill dead latency) + T3.5/T3.6 (structured activation +
influence) + T3.10 (feedback loop on) + T3.11 (replies). End state: a full-horizon, socially-structured,
self-enriching simulation that builds materially faster.

**Milestone C — Decision-grade report + KG depth, ~1–2 days:**
T2.3/T2.4 (bi-temporal + communities) + T4.2/T4.3 (sim-outcome tools + grounded outline) +
T4.4 (knobs). Optional deep cut: T4.5 (native tools). End state: an auditable report citing real cast,
relationships, factions, and quantitative sim outcomes.

**Milestone D — Loop-closing UX + robustness, ~1–2 days:**
T5.1/T5.2/T5.3 (surface brief/relationships/timeline/seed-overlay) + T6.1/T6.2/T5.4 (validate-on-resume +
research_only→full + edit-and-continue) + T5.6/T6.4 (preflight + boot validation) + T6.8 (doc).

**Milestone E — Power features (optional, high-value):**
T4.6/T4.7 (what-if scenarios + counterfactual diff), T3.4 (echo chambers), T3.8/T3.9 (timeline replay +
brief-into-clock), T3.12/T3.13/T3.14 (recsys wiring, cast cap, run_summary), T5.5/T6.3/T6.5/T6.6/T6.7.

---

## 10. Cross-cutting engineering rules (apply to every task)

1. **Optional-degrade everywhere.** New fields are optional; every consumer falls back to today's path
   when absent. Mirror the existing `actors.py` fail-soft style (return `[]`/`''`/`None`, never raise).
2. **All actors.json access goes through `backend/app/utils/actors.py`.** No service parses the raw dict.
3. **Gate cost/risk behind a `Config` flag.** New flags + their safe defaults:
   - `GRAPH_SEED_FROM_ACTORS=true` (T2.2) · `GRAPH_BUILD_CONCURRENCY=1` (T2.5) ·
     `GRAPH_BUILD_COMMUNITIES=false` (T2.4)
   - `SIM_GRAPH_FEEDBACK=true` for local (T3.10) · `SIM_TYPED_FEEDBACK_EDGES=true` (T3.10) ·
     `OASIS_MAX_AGENTS=80` (T3.13) · `OASIS_DEFAULT_MAX_ROUNDS=0`→None (T3.7)
   - `REPORT_AGENT_MAX_TOOL_CALLS=8` / `MIN_TOOL_CALLS=4` / `MAX_TOOL_CALLS_CHAT=2` /
     `TEMPERATURE=0.4` (T4.4) · `REPORT_NATIVE_TOOLS=false` (T4.5)
   - `SUPPORTED_DEERFLOW_MODELS` + `DEERFLOW_KEY_ENV` + `DEERFLOW_DEPTH_BUDGETS` +
     `DEERFLOW_DEEP_OPENING_RECURSION_LIMIT` (T6.4/T6.6)
   Add every new knob to `.env.example` with a one-line comment and its default.
4. **Reuse proven patterns, don't replace them:** the subprocess+monitor template
   (`SimulationRunner`/`DeerFlowResearchRunner`), the per-graph Graphiti cache + `run()` sync→async
   bridge, the `actor_briefing` injection seam, the round-0 `ManualAction` injection, the resume
   reuse-guards, the RRF search recipes, the `agents_by_name` index.
5. **Two venvs stay isolated.** Never `pip install deerflow` into MiroFish's env. The only cross-venv
   touch is T4.5's optional `ClaudeChatModel` import (guarded, capability-gated).
6. **Keep the HTTP/JSON contract stable.** New endpoints are additive; existing shapes are preserved
   (the frontend depends on them).
7. **Compile + smoke after each task.** `python -m py_compile` the touched backend modules;
   `npm run build` after any `frontend/` change. Run the targeted acceptance check before moving on.

---

## 11. Verification & acceptance harness

**Per-task:** each task lists its own acceptance checks — satisfy them before moving on.

**Golden-thread end-to-end (after Milestone A):**
```bash
# 1) research only, fast — verify the enriched contract is produced
cd deer-flow && backend/.venv/bin/python deerflow_research.py \
  --prompt "若某市全面放开网约车牌照，三个月内本地出租车司机群体舆情如何演变？" \
  --out-dir /tmp/ht --depth quick
python -c "import json;d=json.load(open('/tmp/ht/actors.json'));\
print('brief:', bool(d.get('situation_brief')));\
print('rels:', len(d.get('relationships',[])));\
print('timeline:', __import__('os').path.exists('/tmp/ht/timeline.json'))"
# expect: brief True, rels >0, timeline True

# 2) full pipeline (UI or API), then assert the thread landed:
#    - get_graph_data(graph_id) shows ALLY_OF/OPPOSES/REGULATES edges between actor names (T2.2)
#    - a persona prompt log contains a 社会关系网 block naming a real counterpart (T3.1)
#    - simulation_config.json has event_config.initial_follows (T3.2) and the follow table is non-empty after round 0 (T3.3)
#    - the report's agent_log.jsonl shows the 背景档案 block and a section cites a researched actor (T4.1)
```

**Existing regression tests (must stay green):**
`backend/scripts/test_graphiti_services.py`, `test_graphiti_migration.py`, `test_pipeline_resume.py`,
`test_zep_rate_limit.py`, `test_profile_format.py`, `test_deerflow_deep_research.py`.
Add focused unit tests for the new `actors.py` helpers (T1.3) and `seed_actors` idempotency (T2.7).

**Before/after evidence to capture in `handoff.md`:** graph node/edge counts pre/post seeding and
pre/post simulation; rounds executed (expect full horizon after T3.7); GRAPH-stage wall-clock at
concurrency 1 vs 8 (T2.5); a persona prompt with vs without the relationship block.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Non-premium models emit malformed enriched JSON | `extract_json_object` salvage already tolerant; every field optional-degrade; keep `relationships`/`situation_brief` strictly optional. |
| `add_triplet` dedup mis-merges two distinct same-named actors | rare in a researched cast; rely on name+embedding resolve; if observed, disambiguate seed node names with the actor `type`. |
| Parallel ingest (T2.5) perturbs dedup ordering | default `GRAPH_BUILD_CONCURRENCY=1`; opt-in; acceptance asserts node/edge counts within tolerance vs serial. |
| Full-horizon sims (T3.7) raise cost/latency | `OASIS_DEFAULT_MAX_ROUNDS` knob + the depth/`max_rounds` UI control + stage gating (research_only); surface `rounds_truncated_*` so any cap is visible. |
| Native tool calling (T4.5) destabilizes the report | gated behind `REPORT_NATIVE_TOOLS`; the prompt-ReAct path is kept verbatim as fallback; do it last. |
| Community detection (T2.4) adds LLM cost | best-effort, `GRAPH_BUILD_COMMUNITIES` default off; never fails the build. |
| zh/en mismatch when research language differs from sim prompts | T5.5 per-run language; keep `DEERFLOW_RESEARCH_LANGUAGE` default Chinese to match the zh-targeted sim. |

---

## 13. Source of this plan

Generated 2026-06-13 by an 11-agent parallel code study (`deerflow_research.py`, the deep-research SKILL,
`pipeline_orchestrator.py`, `actors.py`, `ontology_generator.py`, the `graphiti_client` shim,
`zep_entity_reader.py`, `oasis_profile_generator.py`, `simulation_config_generator.py`,
`simulation_runner.py`, `run_parallel_simulation.py`, `report_agent.py`, `zep_tools.py`, the research UI,
config/providers/setup) + a 5-theme synthesis, every anchor verified against the live tree. The full
55-finding synthesis (with the longer per-task code sketches, including duplicate framings that this
plan merged) is archived in the repo at **`docs/EXECPLAN_synthesis_detail.md`** — consult it when a
task's sketch here needs more depth. The canonical, deduplicated, dependency-ordered set is the
46 tasks above (Phase 1×3, Phase 2×8, Phase 3×14, Phase 4×7, Phase 5×6, Phase 6×8).
