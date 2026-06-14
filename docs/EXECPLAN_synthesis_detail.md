

==========================================================================================
THEME: Enriched research→sim data contract: situation brief + actor relationship graph
==========================================================================================
SUMMARY: The pipeline's single highest-leverage gap is that DeerFlow's structured pass (deerflow_research.py:420-451 build_extraction_prompt) emits a FLAT actors.json — per-actor name/type/role/stance/influence/memory with NO relationships[] and NO situation_brief. Every downstream consumer then either re-derives the situation from the 6k-word report or guesses: the ontology LLM invents edge types from prose; Graphiti re-extracts all entities/edges from chunked text (pipeline_orchestrator.py:1147-1156) discarding what research already learned; personas get actor_briefing but are relationship-blind (and oasis_profile_generator.py:453-456 even substitutes the literal placeholder "相关实体" for real neighbour names); OASIS starts with an EMPTY follow graph and rediscovers structure by random posting (run_parallel_simulation.py:1142-1172 injects only CREATE_POST); and ReportAgent is built with only graph_id/sim_id/requirement (pipeline_orchestrator.py:1260-1264), never seeing the dossier.

This plan threads ONE enriched contract through the whole stack, building on mechanisms that already exist: the structured-extraction pass and extract_json_object salvage; the actors.py fail-soft helper layer (normalize_name/match_actor/actor_briefing/actors_digest); the per-graph Graphiti runtime cache and verified-present graphiti_core.add_triplet(EntityNode, EntityEdge, EntityNode) at graphiti.py:1645 (add_episode already accepts reference_time); the OasisProfileGenerator actor_briefing injection seam; and the round-0 ManualAction injection pattern (result.env.agent_graph.get_agent + env.step). The ordering is contract-first (S, unblocks everything), then the actors.py helper layer, then graph seeding via a new shim add_triplet, then persona relationship briefing, then OASIS follow-graph seeding, then threading the brief into ontology/sim-config/report. Every read is optional-degrade so a missing/partial handoff never breaks the existing pure-LLM path. timeline.json is promoted to a first-class artifact along the way (it is contracted in DEERFLOW_INTEGRATION.md §3 but never written), giving valid_at anchors for the seeded triplets.

----- [1-contract] contract-schema-brief-relationships  (effort=S impact=high) dep=[]
TITLE: Extend the DeerFlow structured pass + SKILL.md to emit situation_brief and relationships[]
PROBLEM: build_extraction_prompt (deerflow_bridge/deerflow_research.py:420-451) hardcodes a FLAT JSON schema: central_question, as_of_date, actors[{name,type,role,stance,influence,memory}], key_events[], hot_topics[], sources[]. There is NO relationships[] edge array and NO situation_brief object. The deep protocol ALREADY profiles actors-and-incentives (DEEP_RESEARCH_PHASES phase 3) and SKILL.md §8 gathers actor stance/drivers, but that relational knowledge is discarded because the schema has no place for it. SKILL.md §12 (deerflow_bridge/skills/deep-research/SKILL.md:179-188) only specifies a prose report. main() at deerflow_research.py:860-882 writes the parsed object verbatim to actors.json (popping sources), so adding fields to the prompt is sufficient to carry them through to the handoff with no writer change for fields that stay inside actors.json.
PROPOSAL: Add two blocks to the extraction JSON schema string in build_extraction_prompt: (1) a top-level situation_brief object {current_situation, context, dynamics, fault_lines[], catalysts[]} (2-4 factual sentences each, fault_lines/catalysts are the 3-6 things agents will argue over / what would shift the situation, all as-of as_of_date); (2) a top-level relationships[] array of typed directed edges between named actors. Constrain the type enum to a fixed vocabulary {ally, rival, competitor, regulator, partner, dependency, superior} plus {directed (bool, default true), intensity (high|medium|low), description (1-line researched basis)}. Instruct the model: emit edges ONLY between actors that appear in actors[].name, with an evidential basis, and only the relationships actually established by the research (no speculation). Add a §8 bullet to SKILL.md telling the analyst to populate situation_brief and relationships[] as first-class outputs of the actors-and-incentives pass. Bump the deep-run actor_range hint to also request a relationship pass. Keep BOTH fields strictly optional in spirit — the JSON salvage (extract_json_object) and all downstream readers must treat absence as normal. This is the keystone: it is the only producer change and it unblocks every other item.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py', '/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/skills/deep-research/SKILL.md']
ACCEPTANCE:
   - Running `deerflow_research.py --prompt <q> --out-dir /tmp/h --depth standard` produces actors.json containing a non-empty top-level situation_brief object with current_situation and fault_lines, and a relationships[] array whose every from/to matches some actors[].name
   - Each relationships[] row has a type in the 7-value enum and a non-empty description
   - extract_json_object still parses successfully when the model wraps the JSON in prose/fences (existing salvage path unaffected)
   - A prompt with no clear inter-actor relations yields relationships:[] (or omitted) without erroring, and situation_brief still populates
CODE_SKETCH:
# deerflow_research.py build_extraction_prompt(): inside the schema string, after hot_topics, before sources:
'  "situation_brief": {\n'
'    "current_situation": string,   // 2-4 sentence factual state-of-play as of as_of_date\n'
'    "context": string,            // how it got here (causal/historical)\n'
'    "dynamics": string,           // forces in tension; what is escalating/de-escalating\n'
'    "fault_lines": [ string ],    // 3-6 fault lines the actors will argue over\n'
'    "catalysts": [ string ]       // events/decisions that would shift the situation\n'
'  },\n'
'  "relationships": [ {\n'
'    "from": string,   // MUST equal an actors[].name\n'
'    "to": string,     // MUST equal an actors[].name\n'
'    "type": "ally"|"rival"|"competitor"|"regulator"|"partner"|"dependency"|"superior",\n'
'    "directed": true,\n'
'    "intensity": "high"|"medium"|"low",\n'
'    "description": string   // 1-line researched basis\n'
'  } ],\n'
# After the schema, append a hard rule line:
'Emit relationships ONLY between actors named in actors[]; every edge MUST cite a researched basis in description. Omit speculative edges.'
# SKILL.md §8: add a bullet: "Record the actor RELATIONSHIP GRAPH (who allies/opposes/regulates/depends-on whom, directed, with a one-line basis) and a SITUATION BRIEF (current situation, how it got here, dynamics, fault lines, catalysts) — these are first-class structured outputs, not just prose."

----- [1-contract] contract-timeline-artifact  (effort=S impact=medium) dep=['contract-schema-brief-relationships']
TITLE: Promote key_events to a first-class timeline.json artifact in the handoff
PROBLEM: DEERFLOW_INTEGRATION.md §3 contracts timeline.json and the task framing names it, but it is NEVER written — grep confirms no timeline.json writer in deerflow_research.py. key_events lives only nested inside actors.json (deerflow_research.py:876 dumps the whole obj). The orchestrator reads actors/sources in DeerFlowResearchRunner.run (pipeline_orchestrator.py:518-519) and _load_research_handoff (573-574) but there is no timeline read. Without a first-class dated timeline there is no clean source of valid_at timestamps to anchor the seeded relationship triplets (item graph-seed-triplets), so bi-temporal grounding stays guessed by the local LLM.
PROPOSAL: In deerflow_research.py main() (after the extraction object is parsed at ~line 871-878, alongside the existing `sources = obj.pop('sources', None)`), also pop key_events into its own file: write <out-dir>/timeline.json (new TIMELINE_FILENAME constant near REPORT_FILENAME/ACTORS_FILENAME) when key_events is a non-empty list. Keep key_events ALSO inside actors.json (do not remove it) so existing consumers (actors_digest, DossierViewer) are unaffected — timeline.json is an additive promotion, not a move. In pipeline_orchestrator.py DeerFlowResearchRunner.run and _load_research_handoff add `timeline = _read_json(os.path.join(handoff_dir, 'timeline.json'))` and include it in the returned research dict so later stages (graph valid_at anchoring, event-config seeding) can consume it. Best-effort: missing timeline.json yields None and changes nothing.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - A standard research run writes <out-dir>/timeline.json as a JSON list of {date,event} when key_events is non-empty
   - actors.json STILL contains key_events (no regression for actors_digest/DossierViewer)
   - research dict returned by DeerFlowResearchRunner.run includes a 'timeline' key (None when absent)
   - A run that yields no key_events writes no timeline.json and the pipeline proceeds unchanged
CODE_SKETCH:
# deerflow_research.py near other *_FILENAME constants:
TIMELINE_FILENAME = 'timeline.json'
# in main() after `sources = obj.pop('sources', None)` (keep key_events in obj too):
key_events = obj.get('key_events')
if isinstance(key_events, list) and key_events:
    (out_dir / TIMELINE_FILENAME).write_text(json.dumps(key_events, ensure_ascii=False, indent=2), encoding='utf-8')
    meta['timeline_count'] = len(key_events)
    plog.write('ok', f'wrote {TIMELINE_FILENAME} ({len(key_events)} events)')
# pipeline_orchestrator.py DeerFlowResearchRunner.run return dict and _load_research_handoff:
'timeline': _read_json(os.path.join(handoff_dir, 'timeline.json')),

----- [1-contract] helpers-relationship-brief-layer  (effort=S impact=high) dep=['contract-schema-brief-relationships']
TITLE: Add the actors.py helper layer: relationship rows, relationship_briefing, situation_brief block, follow-graph builder
PROBLEM: backend/app/utils/actors.py is the single fail-soft bridge for actors.json (normalize_name, match_actor, influence_weight, actor_briefing, actors_digest) and is reused by personas + sim-config + (proposed) report. It has NO concept of relationships or situation_brief because the schema never had them (actors.py:1-15 docstring). Every consumer that wants to thread the new fields would otherwise re-parse the raw dict independently and re-implement name-resolution. The persona seam additionally destroys neighbour names: oasis_profile_generator.py:453-456 emits the literal placeholder '相关实体' instead of the real ally/rival name.
PROPOSAL: Extend actors.py with four small, fail-soft helpers that all other items build on: (1) extract_relationship_rows(actors) -> List[{from,to,type,directed,intensity,description}] mirroring extract_actor_rows (drop rows whose from/to are empty); (2) relationship_briefing(actor_name, actors, max_edges=6) -> str: scan relationship rows touching actor_name (both directions), render a compact zh block listing allies/rivals/regulators/etc by REAL counterpart name + intensity, so personas know who to @mention; (3) situation_brief_block(actors) -> str: render the situation_brief object (current_situation/context/dynamics + fault_lines/catalysts bullet lists) as a compact zh prompt block, fail-soft to '' when absent; (4) build_initial_follow_graph(actors, agent_id_by_name) -> List[[follower_id, followee_id]]: resolve each relationship from/to to agent_ids via normalize_name lookup (the agents_by_name pattern already used in simulation_config_generator.py:785), then emit directed follow pairs by edge semantics (ally/partner/dependency/superior -> follow the counterpart; regulator -> regulator follows regulated for monitoring; rival/competitor -> a weak 'monitors' follow for visibility), de-duplicated. Update the actors.py module docstring (lines 3-15) to document the new relationships[] and situation_brief shape. These are pure functions with unit-test-friendly signatures; absence of fields returns [] / ''.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - Unit call relationship_briefing('教育部', actors) with a regulates edge returns a block naming the real counterparty (NOT '相关实体')
   - situation_brief_block(actors) returns a non-empty zh block when situation_brief present and '' when absent
   - build_initial_follow_graph(actors, {'教育部':3,'某大学':7}) returns [[3,7]] (or [[7,3]] per semantics) for a regulator/ally edge and [] when names do not resolve
   - All four helpers return [] / '' on a dict with no relationships/situation_brief (no exception)
CODE_SKETCH:
REL_FOLLOW_TYPES = {'ally','partner','dependency','superior'}
REL_MONITOR_TYPES = {'rival','competitor','regulator'}

def extract_relationship_rows(actors):
    if not isinstance(actors, dict): return []
    rows = actors.get('relationships')
    if not isinstance(rows, list): return []
    return [r for r in rows if isinstance(r, dict) and r.get('from') and r.get('to')]

def relationship_briefing(actor_name, actors, max_edges=6):
    rows = extract_relationship_rows(actors)
    if not rows or not actor_name: return ''
    me = normalize_name(actor_name); out=[]
    for r in rows:
        f,t = normalize_name(r['from']), normalize_name(r['to'])
        if me not in (f,t): continue
        other = r['to'] if me==f else r['from']
        out.append(f"- {r.get('type','关系')}({r.get('intensity','')}): {other}")
        if len(out)>=max_edges: break
    if not out: return ''
    return '## 你的社会关系网（调研实证，互动时据此 @ 相关方）\n' + '\n'.join(out)

def situation_brief_block(actors):
    sb = (actors or {}).get('situation_brief') if isinstance(actors,dict) else None
    if not isinstance(sb, dict): return ''
    parts=[]
    for label,key in (('当前态势','current_situation'),('来龙去脉','context'),('张力/动态','dynamics')):
        v=str(sb.get(key,'') or '').strip()
        if v: parts.append(f'### {label}\n{v}')
    for label,key in (('争议断层','fault_lines'),('潜在触发','catalysts')):
        lst=sb.get(key)
        if isinstance(lst,list) and lst: parts.append(f'### {label}\n'+'\n'.join(f'- {x}' for x in lst[:6]))
    return ('## 局势简报（深度研究实证，作为生成的权威背景）\n'+'\n'.join(parts)) if parts else ''

def build_initial_follow_graph(actors, agent_id_by_name):
    rows = extract_relationship_rows(actors); pairs=set()
    def aid(n): return agent_id_by_name.get(normalize_name(n))
    for r in rows:
        s,d = aid(r['from']), aid(r['to']); typ=str(r.get('type','')).lower()
        if s is None or d is None or s==d: continue
        if typ in REL_FOLLOW_TYPES: pairs.add((s,d))
        elif typ in REL_MONITOR_TYPES: pairs.add((s,d))  # monitor = directed follow for visibility
    return [list(p) for p in pairs]

----- [2-graph] graph-shim-add-triplet  (effort=M impact=high) dep=[]
TITLE: Surface add_triplet through the Graphiti shim (runtime + client) with valid_at
PROBLEM: graphiti_core exposes add_triplet(source_node: EntityNode, edge: EntityEdge, target_node: EntityNode) at graphiti.py:1645 (verified), which resolves/dedupes nodes against the existing graph and writes a typed embedded edge. But the Zep-compat shim _GraphNamespace (client.py:119-175) exposes only create/set_ontology/add_batch/add/search — NO triplet method — and the runtime (runtime.py:256-306) only has add_episode. So even once research emits relationships[] there is no API to write them as deterministic graph edges; everything is re-LLM-extracted from prose. add_episode already proves the sync->async bridge (self.run(coro) on the background loop) and already accepts reference_time, so the pattern to follow is established.
PROPOSAL: Add runtime.add_triplet(graph_id, source_name, edge_name, target_name, fact, valid_at=None, source_label='Entity', target_label='Entity') that, on the per-graph background loop, builds EntityNode(name=source_name, group_id=graph_id, labels=[source_label,'Entity']) and target likewise, an EntityEdge(source_node_uuid=src.uuid, target_node_uuid=tgt.uuid, name=edge_name, fact=fact, group_id=graph_id, valid_at=valid_at, created_at=now), ensures the graph (reuse _ensure_graph), and awaits g.add_triplet(src, edge, tgt). Expose it on the shim _GraphNamespace as graph.add_triplet(graph_id, source_name, edge_type, target_name, fact, valid_at=None) delegating to runtime. Reuse the existing graphiti instance from the per-graph cache (the same one add_episode uses) so seeded nodes dedupe against text-extracted ones. Keep it best-effort at the call site (log-and-continue) so a triplet failure never aborts the build.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/client.py']
ACCEPTANCE:
   - client.graph.add_triplet(gid,'A','ALLY_OF','B','researched basis') creates two EntityNodes (or dedups to existing) and one EntityEdge named ALLY_OF connecting them, verifiable via get_graph_data edges
   - Passing valid_at=datetime stamps the edge's valid_at (not now()) in the returned graph data
   - Calling add_triplet twice with the same triple does not create duplicate edges with mismatched endpoints (graphiti dedup/resolve path)
   - A triplet whose source/target names already exist as text-extracted nodes attaches to those nodes rather than creating duplicates
CODE_SKETCH:
# runtime.py (mirror add_episode bridge):
def add_triplet(self, graph_id, source_name, edge_name, target_name, fact, valid_at=None,
                source_label='Entity', target_label='Entity'):
    return self.run(self._add_triplet(graph_id, source_name, edge_name, target_name, fact,
                                      valid_at, source_label, target_label))
async def _add_triplet(self, graph_id, source_name, edge_name, target_name, fact, valid_at,
                       source_label, target_label):
    from graphiti_core.nodes import EntityNode
    from graphiti_core.edges import EntityEdge
    g = await self._ensure_graph(graph_id)
    now = datetime.now(timezone.utc)
    labels_s = list({source_label, 'Entity'}); labels_t = list({target_label, 'Entity'})
    src = EntityNode(name=source_name, group_id=graph_id, labels=labels_s, created_at=now)
    tgt = EntityNode(name=target_name, group_id=graph_id, labels=labels_t, created_at=now)
    edge = EntityEdge(source_node_uuid=src.uuid, target_node_uuid=tgt.uuid, name=edge_name,
                      fact=fact, group_id=graph_id, created_at=now, valid_at=valid_at)
    await g.add_triplet(src, edge, tgt)
    return edge.uuid
# client.py _GraphNamespace:
def add_triplet(self, graph_id, source_name, edge_type, target_name, fact, valid_at=None,
                source_label='Entity', target_label='Entity', **_):
    return self._rt.add_triplet(graph_id, source_name, edge_type, target_name, fact,
                                valid_at, source_label, target_label)

----- [2-graph] graph-seed-actor-relationships  (effort=M impact=high) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer', 'graph-shim-add-triplet', 'contract-timeline-artifact']
TITLE: Seed researched actors + relationships into the KG before chunk ingestion
PROBLEM: The GRAPH stage (pipeline_orchestrator.py:1144-1166) builds the graph PURELY from chunked research_report.md (TextProcessor.split_text -> add_text_batches batch_size=10) and lets Graphiti re-extract every entity/edge with the local LLM. The clean, high-confidence cast and relationships research already produced are thrown away and rediscovered lossily, adding extraction error and latency. There is no seed_actors path in graph_builder.py (it only does create_graph/set_ontology/add_text_batches/_wait_for_episodes). With add_triplet now surfaced (graph-shim-add-triplet) and a dated timeline available (contract-timeline-artifact), the cast and relations can be written deterministically and time-anchored.
PROPOSAL: Add GraphBuilderService.seed_actors(graph_id, actors, timeline=None) that, AFTER set_ontology and BEFORE add_text_batches: (1) for each actors[] row, ensure an entity node via client.graph.add_triplet with a self/identity fact OR write actor nodes by a light add_triplet to a stable hub — simplest: rely on relationship endpoints to create nodes, and for actors with no edges, write a single 'IS_A' triplet (actor -> type) so isolated high-influence actors still seed; (2) for each relationship row, map the 7-value type to an UPPER_SNAKE edge name (ally->ALLY_OF, rival->OPPOSES, competitor->COMPETES_WITH, regulator->REGULATES, partner->PARTNERS_WITH, dependency->DEPENDS_ON, superior->SUPERIOR_OF) and call add_triplet(graph_id, from, edge_name, to, description, valid_at=as_of_date), mapping the actor's DeerFlow type to the source/target label. Anchor valid_at to actors.as_of_date (parsed) or the matching key_events date when available. In the orchestrator GRAPH stage, after builder.set_ontology(graph_id, project.ontology) at line 1149, call builder.seed_actors(graph_id, actors, timeline) guarded by a try/except (log-and-continue) so seeding failure never aborts the build. Text extraction then runs and ENRICHES the seeded nodes (graphiti dedup attaches new facts to the seeded entities). This is the headline fidelity+speed win.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - After a full pipeline run, get_graph_data(graph_id) shows edges named ALLY_OF/OPPOSES/REGULATES/etc whose endpoints are the researched actor names and whose valid_at == as_of_date (not ingest time)
   - A high-influence actor with no relationships still appears as a node (IS_A seed) rather than depending on text extraction luck
   - Seeded actor names that also appear in report prose are NOT duplicated in the node set (dedup attaches text facts to the seeded node)
   - When actors.json is absent or has no relationships, the GRAPH stage logs a skip and builds exactly as today (no regression)
CODE_SKETCH:
# graph_builder.py
from app.utils.actors import extract_actor_rows, extract_relationship_rows
REL_EDGE = {'ally':'ALLY_OF','rival':'OPPOSES','competitor':'COMPETES_WITH','regulator':'REGULATES',
            'partner':'PARTNERS_WITH','dependency':'DEPENDS_ON','superior':'SUPERIOR_OF'}
def seed_actors(self, graph_id, actors, timeline=None):
    if not isinstance(actors, dict): return 0
    as_of = _parse_date(actors.get('as_of_date'))  # -> datetime|None
    rows = extract_actor_rows(actors); rels = extract_relationship_rows(actors); n=0
    by_name = {r.get('name'): r for r in rows}
    for r in rels:
        etype = REL_EDGE.get(str(r.get('type','')).lower())
        if not etype: continue
        sl = (by_name.get(r['from']) or {}).get('type','Entity'); tl = (by_name.get(r['to']) or {}).get('type','Entity')
        try:
            self.client.graph.add_triplet(graph_id, r['from'], etype, r['to'],
                str(r.get('description','') or f"{r['from']} {etype} {r['to']}"),
                valid_at=as_of, source_label=sl, target_label=tl); n+=1
        except Exception as e: logger.warning(f'seed edge skipped: {e}')
    # seed isolated actors so high-influence loners still exist as nodes
    seeded = {r['from'] for r in rels} | {r['to'] for r in rels}
    for a in rows:
        if a['name'] in seeded: continue
        try:
            self.client.graph.add_triplet(graph_id, a['name'], 'IS_A', a.get('type','Entity'),
                a.get('role','') or a['name'], valid_at=as_of, source_label=a.get('type','Entity')); n+=1
        except Exception: pass
    return n
# pipeline_orchestrator.py GRAPH stage after set_ontology (line 1149):
try:
    seeded = builder.seed_actors(graph_id, actors, research.get('timeline'))
    upd(8, f'已注入 {seeded} 条调研关系种子')
except Exception as e:
    logger.warning(f'[{state.pipeline_id}] actor seeding skipped: {e}')

----- [3-sim] personas-relationship-briefing  (effort=M impact=high) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer']
TITLE: Inject relationship_briefing into personas and fix the name-destroying placeholder
PROBLEM: Personas get actor_briefing (role/stance/influence/memory) injected at oasis_profile_generator.py:524,534-540 via match_actor (line 936), but actor_briefing carries NO relationships — agents know their own stance but are blind to who their allies/rivals are, so coalition formation and antagonism are emergent noise. Worse, _build_entity_context at oasis_profile_generator.py:453-456 substitutes the literal placeholder '相关实体' for a neighbour's real name on edges that lack a free-text fact, so even the graph's own relational neighbourhood reaches the LLM as 'X criticizes (some entity)'. The relationship data now exists (contract-schema-brief-relationships) and the rendering helper exists (helpers-relationship-brief-layer).
PROPOSAL: Two changes, both building on existing seams: (1) FIX oasis_profile_generator.py:453-456 to resolve the edge's source/target uuid against entity.related_nodes (already carried by filter_defined_entities) and emit the REAL neighbour name + its custom label, e.g. 'X --[CRITICIZES]--> 教育部(Government)'; (2) in generate_profiles_from_entities, right after the existing actor_briefing injection (~line 524/534-540), append relationship_briefing(entity.name, actors) so the persona prompt lists the actor's allies/rivals/regulators by real name with intensity. This gives the LLM concrete names to @mention and reinforces the seeded follow graph. Both reads are fail-soft (empty string when no relationships), so unmatched actors and missing actors.json degrade to exactly today's behaviour.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/oasis_profile_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - For an entity matched to a researched actor with relationships, the generated persona prompt contains a '社会关系网' block naming real allies/rivals (verify via a logged prompt or a unit harness)
   - _build_entity_context no longer emits the literal string '相关实体' when the neighbour name is resolvable from related_nodes
   - Entities with no matched actor or no relationships produce the SAME persona output as before (no relationship block, no errors)
   - A run with actors.json absent generates personas identically to the pre-change pipeline
CODE_SKETCH:
# oasis_profile_generator.py _build_entity_context (~453-456): replace placeholder
# old: emits '相关实体'
# new: look up neighbour by uuid in entity.related_nodes
nbr = related_by_uuid.get(edge.get('target_node_uuid')) or related_by_uuid.get(edge.get('source_node_uuid'))
name = (nbr or {}).get('name') or '相关实体'
label = next((l for l in (nbr or {}).get('labels',[]) if l not in ('Entity','Node')), '')
ctx_lines.append(f"{entity.name} --[{edge.get('edge_name')}]--> {name}{'('+label+')' if label else ''}")
# generate_profiles_from_entities, after actor_briefing append (~534-540):
from app.utils.actors import relationship_briefing
rb = relationship_briefing(entity.name, actors)
if rb:
    prompt_tail += '\n\n' + rb

----- [3-sim] sim-seed-follow-graph  (effort=M impact=high) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer']
TITLE: Seed the OASIS round-0 follow graph from researched relationships
PROBLEM: OASIS starts with an EMPTY follow graph: generate_twitter_agent_graph/generate_reddit_agent_graph build agents from profile files only and the run loop injects ONLY round-0 CREATE_POST ManualActions (run_parallel_simulation.py:1142-1172). FOLLOW is whitelisted (run_parallel_simulation.py:183,201) and agent_graph supports edges, but no follow edges are seeded, so agents discover each other randomly over 40-72 rounds and emergent network structure is an artifact of randomness, not researched alignment. The follow-graph builder now exists (helpers-relationship-brief-layer build_initial_follow_graph) and the round-0 ManualAction injection pattern (result.env.agent_graph.get_agent + env.step) is exactly reusable.
PROPOSAL: Producer: in SimulationConfigGenerator.generate_config, after agent batches assign agent_id<->entity_name, build agent_id_by_name = {normalize_name(cfg.entity_name): cfg.agent_id} (reuse the agents_by_name pattern at simulation_config_generator.py:785) and call build_initial_follow_graph(actors, agent_id_by_name); persist the result as event_config.initial_follows: List[[follower_id, followee_id]] on SimulationParameters (mirror initial_posts serialization). Consumer: in run_parallel_simulation.py, immediately after the existing initial_posts injection block (after line 1172, and the Reddit mirror ~1345-1383), read config.event_config.initial_follows and inject them as a batched round-0 step: for each [follower, followee] build {agent: ManualAction(ActionType.FOLLOW, {'followee_id': followee})} via result.env.agent_graph.get_agent(follower), then a single await result.env.step(follow_actions) BEFORE the round loop. Wrap in try/except (mirroring the bare-except around initial_posts) so a bad pair is skipped, not fatal. Falls back to today's empty graph when initial_follows is absent/empty.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py']
ACCEPTANCE:
   - simulation_config.json contains event_config.initial_follows as a list of [follower_id, followee_id] pairs when relationships are present
   - Simulation log shows '已注入 N 条初始关注边' with N>0 for a run with researched relationships
   - Querying the OASIS follow table after round 0 shows the seeded edges (non-empty) for such a run
   - A run with no relationships produces initial_follows:[] and the round loop proceeds exactly as today (empty follow graph)
   - An unresolvable follow pair is skipped without aborting round 0
CODE_SKETCH:
# simulation_config_generator.py generate_config (after agent configs built):
from app.utils.actors import build_initial_follow_graph, normalize_name
agent_id_by_name = {normalize_name(c.entity_name): c.agent_id for c in agent_configs if c.entity_name}
initial_follows = build_initial_follow_graph(self._actors, agent_id_by_name) if self._actors else []
# store onto EventConfig/SimulationParameters.to_dict():
event_config['initial_follows'] = initial_follows
# run_parallel_simulation.py after initial_posts injection (~1173):
initial_follows = event_config.get('initial_follows', [])
if initial_follows:
    follow_actions = {}
    for follower, followee in initial_follows:
        try:
            ag = result.env.agent_graph.get_agent(follower)
            follow_actions[ag] = ManualAction(action_type=ActionType.FOLLOW,
                                               action_args={'followee_id': followee})
        except Exception:
            pass
    if follow_actions:
        try:
            await result.env.step(follow_actions)
            log_info(f'已注入 {len(follow_actions)} 条初始关注边')
        except Exception as e:
            log_info(f'初始关注注入失败，跳过: {e}')

----- [3-sim] sim-ground-initial-posts-brief  (effort=M impact=medium) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer']
TITLE: Thread the situation brief into sim-config event context and ground initial posts
PROBLEM: SimulationConfigGenerator builds its event-config context from simulation_requirement + a truncated entity summary + actors_digest + raw document_text filling MAX_CONTEXT_LENGTH (simulation_config_generator.py:391-424, 681-690); without a distilled brief the event/agent LLM reasons over a possibly mid-sentence cut of the 6k-word dossier, and initial_posts are authored from that loose digest. A compact authoritative situation_brief now exists (contract-schema-brief-relationships) and a renderer exists (helpers-relationship-brief-layer situation_brief_block), but nothing feeds it into the config generator, so the event setup and grounded initial_posts re-derive the situation instead of opening from it.
PROPOSAL: In SimulationConfigGenerator._build_context (simulation_config_generator.py:391-424) prepend situation_brief_block(self._actors) ABOVE the truncated document_text, so the compact authoritative brief takes priority over the raw-report tail for both the time-config and event-config steps. In the event-config prompt (~681-690), add an explicit instruction that initial_posts should reflect the situation_brief.fault_lines (each fault line is a natural seed-post topic) authored as the relevant researched actor (poster_name targeting already exists). This reuses the existing actors_digest/poster_name machinery and only adds the brief as a higher-priority context block. Fail-soft: situation_brief_block returns '' when absent, so the existing document_text-only path is unchanged.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - When situation_brief is present, the assembled config context begins with the 局势简报 block before any raw document_text
   - Generated initial_posts span the fault_lines (each major fault line is represented by at least one seed post) for a brief-bearing run
   - A run with no situation_brief produces the same context assembly and initial_posts as today
   - No increase in context-truncation errors (brief is compact; verify total context stays under MAX_CONTEXT_LENGTH)
CODE_SKETCH:
# simulation_config_generator.py _build_context:
from app.utils.actors import situation_brief_block
brief = situation_brief_block(self._actors)
parts = [simulation_requirement]
if brief: parts.append(brief)        # authoritative, above raw report
if actors_digest_str: parts.append(actors_digest_str)
# then fill remaining MAX_CONTEXT_LENGTH with document_text as today
# event-config prompt (~681): add a rule line
'按 situation_brief.fault_lines 的每条断层，由最相关的调研角色（poster_name）撰写一条初始帖。'

----- [2-graph] ontology-brief-relationship-bias  (effort=S impact=medium) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer']
TITLE: Feed situation brief + researched relationships into the ontology prompt
PROBLEM: _actors_to_context (pipeline_orchestrator.py:657-677) is the ONLY actors->ontology bridge and is lossy: it flattens to name/type/role/stance for the first 25 actors + 10 hot_topics and DROPS influence, memory, key_events, and (now) the relationships entirely. The ontology LLM therefore invents edge_types and source_targets purely from prose (ontology_generator.py:12-155 system prompt), decoupling the graph's edge schema from the actually-researched inter-actor relations. The situation brief and relationship list now exist but never reach the ontology stage, so edge design stays guesswork.
PROPOSAL: Extend _actors_to_context (or add a sibling _research_to_context) to append two compact sections to the additional_context string the orchestrator already passes at pipeline_orchestrator.py:1125: (1) situation_brief_block(actors) so the ontology LLM knows the current situation/fault lines; (2) a one-line-per-edge listing of researched relationships (from --[type]--> to) plus the implied edge-type vocabulary, with an instruction line: 'These inter-actor relations were RESEARCHED — your edge_types SHOULD cover their types and your source_targets SHOULD connect the entity types these actors belong to.' This turns edge design from invention into grounding while staying within the existing single additional_context channel (no OntologyGenerator signature change required for the first cut). Keep it fail-soft and bounded (cap relationship lines, e.g. first 30).
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - additional_context passed to OntologyGenerator.generate contains the situation brief and a relationship list when actors.json carries them
   - For a relationship-bearing run, the produced ontology's edge_types include types semantically covering the researched relation types (e.g. an OPPOSES/REGULATES-equivalent edge when those relations were researched)
   - _actors_to_context returns the same name/type/role/stance lines as before when relationships/brief are absent (back-compat)
   - No exception when actors is None or relationships is empty
CODE_SKETCH:
# pipeline_orchestrator.py _actors_to_context, before return:
from app.utils.actors import situation_brief_block, extract_relationship_rows
sb = situation_brief_block(actors)
if sb: lines.append(sb)
rels = extract_relationship_rows(actors)
if rels:
    lines.append('已调研确认的角色间关系（你的 edge_types 应覆盖这些关系类型，source_targets 应连接对应实体类型）：')
    for r in rels[:30]:
        lines.append(f"- {r['from']} --[{r.get('type')}]--> {r['to']}（{r.get('description','')}）")
return '\n'.join(lines)

----- [4-report] report-thread-brief-relationships  (effort=M impact=high) dep=['contract-schema-brief-relationships', 'helpers-relationship-brief-layer']
TITLE: Pass the situation brief + actor/relationship digest into ReportAgent as pinned context
PROBLEM: ReportAgent is constructed with only graph_id/simulation_id/simulation_requirement (report_agent.py:947-980; pipeline_orchestrator.py:1260-1264) even though actors, the research_report and sources are all in local scope at the REPORT stage. plan_outline grounds sections in ONE get_simulation_context call (a single search + stats + 10 facts), and section writing context is just requirement + prior sections — the agent must rediscover the entire cast and all relationships via blind graph search. The brief, actor stances, and relationship graph that the pipeline already paid for are invisible to the final prediction, breaking auditability and forcing re-derivation.
PROPOSAL: Extend ReportAgent.__init__ with optional situation_brief: str=None and actors: dict=None (default None preserves the manual-mode 3-arg construction at api/report.py). In pipeline_orchestrator.py REPORT stage, build the context with the actors.py helpers — situation_brief_block(actors) + actors_digest(actors) + a relationship roster — and pass them in. Inject this as a pinned '【背景档案（深度研究，as-of <date>，权威）】' block into the plan_outline prompt and the per-section system prompt so the outline is designed around the real central_question and each section opens from known actors/relationships instead of cold graph search. Add a relationship_briefing-style roster so the report can reason about coalitions by name. All optional -> when actors is None the report behaves exactly as today.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/report_agent.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - ReportAgent constructed in the pipeline receives a non-empty situation_brief and the actors dict; the plan_outline prompt contains the 背景档案 block (verify via agent_log.jsonl or a logged prompt)
   - Generated report sections reference researched actor names/stances that were NOT independently surfaced by graph search (evidence the brief was used)
   - Manual-mode report construction (api/report.py 3-arg) still works unchanged (defaults None)
   - A pipeline run with actors.json absent produces a report via the existing cold-graph path with no error
CODE_SKETCH:
# report_agent.py __init__:
def __init__(self, graph_id, simulation_id, simulation_requirement, situation_brief=None, actors=None):
    ...
    self.situation_brief = situation_brief or ''
    self.actors = actors
# build a pinned context block once:
from app.utils.actors import situation_brief_block, actors_digest, extract_relationship_rows
def _research_context(self):
    blocks = [self.situation_brief]
    if self.actors:
        blocks.append(actors_digest(self.actors, max_chars=3000))
        rels = extract_relationship_rows(self.actors)
        if rels:
            blocks.append('角色关系：' + '；'.join(f"{r['from']}-{r.get('type')}-{r['to']}" for r in rels[:20]))
    txt = '\n\n'.join(b for b in blocks if b)
    return f'【背景档案（深度研究，权威，生成时以此为锚）】\n{txt}' if txt else ''
# inject self._research_context() into PLAN_USER_PROMPT_TEMPLATE and SECTION_SYSTEM_PROMPT_TEMPLATE
# pipeline_orchestrator.py REPORT stage (line 1260):
agent = ReportAgent(graph_id=graph_id, simulation_id=sim_state.simulation_id,
                    simulation_requirement=state.prompt,
                    situation_brief=situation_brief_block(actors), actors=actors)


==========================================================================================
THEME: Simulation fidelity: relationship-seeded social graph, agent memory, richer forum dynamics, OASIS action coverage
==========================================================================================
SUMMARY: I verified every load-bearing mechanism for the simulation-fidelity theme against the actual code: OASIS AgentGraph.add_edge(src,dst) exists (agent_graph.py:206) and the native generate_agents path writes a `follow` table (agents_generator.py:128,146-150), but MiroFish's run scripts build the agent graph from profiles only and start with an EMPTY social graph — initial_posts are injected as round-0 ManualAction(CREATE_POST) (run_parallel_simulation.py:1142-1172) while ActionType.FOLLOW is whitelisted (183/201) yet never used to seed follows. get_active_agents_for_round (1001-1051) activates agents by a flat stance/influence-blind random gate, capped at ~uniform(5,20) regardless of cast size, and influence_weight/stance are computed but never read in-sim. The feedback loop is off in the pipeline (start_simulation called without enable_graph_memory_update, pipeline_orchestrator.py:1212-1216) and would crash key-free (ZepGraphMemoryUpdater raises ValueError without ZEP_API_KEY, zep_graph_memory_updater.py:240-243). Only 6/23 Twitter actions are wired (reply-less), recsys/echo-chamber config is dead (oasis.make gets only agent_graph/platform/db/semaphore), scheduled_events is always [] and never replayed, and max_rounds defaults to 40 — silently truncating a 72-round sim by ~44%. The root contract gap: actors.json has no relationships[] and no situation_brief (deerflow_research.py:420-451; actors.py:1-15), and graphiti-core's add_triplet (graphiti.py:1645) is hidden by the shim.

The ranked plan threads one source of truth — a researched relationship graph + situation brief — through the social graph, agent memory, forum dynamics, action coverage, and the feedback loop, reusing existing seams (actor_briefing injection, the initial_posts ManualAction round-0 injection, the EntityNode.related_edges enrichment, the subprocess+monitor pattern, influence_weight) rather than replacing them. The headline chain is: emit relationships+brief (contract) -> build_follow_graph helper from relationships + graph edges -> inject as round-0 FOLLOW edges -> structured influence/recency-weighted activation -> typed key-free feedback edges -> scheduled-event timeline replay. Lowest-regret, highest-fidelity ordering: the contract item unblocks five sim items and the graph-seed item; the follow-graph trio (build -> inject -> echo-chamber) is the single biggest fidelity lever; the max-rounds and always-apply-influence fixes are cheap correctness wins that several other items depend on for honest behavior.

----- [1-contract] sim-contract-relationships-brief  (effort=M impact=high) dep=[]
TITLE: Extend actors.json with relationships[] + situation_brief at the research producer
PROBLEM: actors.json is a flat table (central_question/as_of_date/actors[name,type,role,stance,influence,memory]/key_events/hot_topics) with NO relationships array and NO situation_brief. The schema is hardcoded in build_extraction_prompt (deerflow_research.py:420-451) and documented in actors.py:1-15. Every downstream simulation-fidelity feature in this theme (follow-graph seeding, relationship-aware personas, KG seed edges, report grounding) is blocked because the researched who-relates-to-whom structure is never recorded as data — even though the deep-research protocol already profiles actors-and-incentives (DEEP_RESEARCH_PHASES phase 3) and SKILL.md §8 gathers stance/revealed-behavior.
PROPOSAL: Add two optional top-level blocks to the structured extraction JSON: situation_brief (current_situation, context, dynamics, key_tensions[]) and relationships[] ({source, target, type in ALLY_OF|OPPOSES|DEPENDS_ON|COMPETES_WITH|REGULATES|INFLUENCES, sign in ally|rival|neutral, strength in high|medium|low, basis}). Constrain source/target to names already present in actors[]. Write situation_brief.json + (relationships ride inside actors.json) in main(). Add parser helpers extract_relationship_rows(actors) and situation_brief(actors) to actors.py mirroring extract_actor_rows' fail-soft design. Keep everything optional so old handoffs still parse and the pure-LLM path is never blocked. Add a SKILL.md §8/§12 bullet instructing the analyst to emit edges only between named actors with an evidential basis.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py', '/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/skills/deep-research/SKILL.md', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - Running deerflow_research.py on a multi-actor prompt produces actors.json containing a non-empty relationships[] whose every source/target normalize-matches some actors[].name, plus a situation_brief.json with the four fields populated.
   - extract_relationship_rows(actors) returns the rows for a well-formed actors.json and [] for a missing/malformed one without raising.
   - situation_brief(actors) returns a non-empty zh string when situation_brief is present and '' when absent.
   - An old actors.json with no relationships/situation_brief still loads through actors.py helpers with no error (backward-compat).
CODE_SKETCH:
# deerflow_research.py build_extraction_prompt schema additions:
#   "situation_brief": {"current_situation": str, "context": str, "dynamics": str, "key_tensions": [str]},
#   "relationships": [{"source": str, "target": str, "type": "ALLY_OF|OPPOSES|DEPENDS_ON|COMPETES_WITH|REGULATES|INFLUENCES", "sign": "ally|rival|neutral", "strength": "high|medium|low", "basis": str}]
# main(): brief = obj.pop('situation_brief', None); _write_json(out_dir/'situation_brief.json', brief) if brief
# actors.py:
VALID_REL_TYPES = {"ALLY_OF","OPPOSES","DEPENDS_ON","COMPETES_WITH","REGULATES","INFLUENCES"}
def extract_relationship_rows(actors):
    if not isinstance(actors, dict): return []
    rels = actors.get('relationships')
    if not isinstance(rels, list): return []
    names = {normalize_name(r['name']) for r in extract_actor_rows(actors)}
    out = []
    for r in rels:
        if not isinstance(r, dict): continue
        s, t = r.get('source'), r.get('target')
        if s and t and normalize_name(s) in names and normalize_name(t) in names:
            out.append(r)
    return out
def situation_brief(actors):
    b = actors.get('situation_brief') if isinstance(actors, dict) else None
    if not isinstance(b, dict): return ''
    parts = [b.get('current_situation'), b.get('context'), b.get('dynamics')]
    kt = b.get('key_tensions') or []
    if kt: parts.append('核心张力: ' + '; '.join(str(x) for x in kt))
    return '\n'.join(p for p in parts if p)

----- [3-sim] sim-build-follow-edges-helper  (effort=M impact=high) dep=['sim-contract-relationships-brief']
TITLE: build_follow_graph(): derive a directed initial follow edge list from relationships + graph edges
PROBLEM: The OASIS social graph starts completely empty. generate_twitter_agent_graph (CSV) / generate_reddit_agent_graph (JSON) only call add_agent and set nodes=[]/edges=[]; run_parallel_simulation injects only round-0 CREATE_POST ManualActions (run_parallel_simulation.py:1142-1172). No follower edges exist, so round-1 information flow is whatever the cold recommender surfaces — emergent network structure is an artifact of randomness, not researched real-world alignment. The relationship data (once item sim-contract-relationships-brief exists) and the already-loaded Graphiti EntityNode.related_edges (zep_entity_reader.py:282-319, populated by filter_defined_entities(enrich_with_edges=True)) are discarded.
PROPOSAL: Add SimulationConfigGenerator._build_follow_edges(entities, actors, agent_configs) producing a deduped list of [follower_agent_id, followee_agent_id] pairs. Build two indices: uuid->agent_id and normalize_name(entity_name)->agent_id (reuse the agents_by_name pattern at simulation_config_generator.py:785). For each relationships[] row resolve source/target names to agent_ids; emit edges by type — ALLY_OF/INFLUENCES/DEPENDS_ON -> follower follows the higher-influence node (use influence_weight to pick direction); OPPOSES -> a weak 'monitor' follow for visibility; REGULATES/COMPETES_WITH -> mutual awareness follow. ALSO walk each EntityNode.related_edges (direction + target_node_uuid) to add follows where a graph edge implies attention, so seeding works even when relationships[] is sparse. Persist the result onto EventConfig/SimulationParameters as event_config.initial_follows. Fail-soft to [] when no relationships/edges resolve.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_entity_reader.py']
ACCEPTANCE:
   - For a sim with a researched relationships[] of N resolvable edges, simulation_config.json contains event_config.initial_follows with >= N directed [follower_id, followee_id] pairs, every id within [0, num_agents).
   - When actors.json has no relationships and graph edges exist, initial_follows is still populated from related_edges; when neither exists it is [] and generate_config completes without error.
   - No follower==followee self-loops and no duplicate pairs appear in initial_follows.
   - Direction check: for an ALLY_OF edge between a low-influence and a high-influence actor, the low-influence agent is the follower.
CODE_SKETCH:
# actors.py: resolve helper reused by config gen
# simulation_config_generator.py (new method, call after agent batches before platform config):
def _build_follow_edges(self, entities, actors, agent_configs):
    name2id = {}
    for cfg in agent_configs:
        en = cfg.get('entity_name')
        if en: name2id[normalize_name(en)] = cfg['agent_id']
    edges = set()
    for r in extract_relationship_rows(actors):
        sid = name2id.get(normalize_name(r['source'])); tid = name2id.get(normalize_name(r['target']))
        if sid is None or tid is None or sid == tid: continue
        s_inf = influence_weight(match_actor(r['source'], actors)) or 1.0
        t_inf = influence_weight(match_actor(r['target'], actors)) or 1.0
        typ = (r.get('type') or '').upper()
        if typ in ('ALLY_OF','INFLUENCES','DEPENDS_ON','REGULATES'):
            lo, hi = (sid, tid) if s_inf <= t_inf else (tid, sid)
            edges.add((lo, hi))
        elif typ == 'OPPOSES':
            edges.add((sid, tid))  # monitor for visibility
        elif typ == 'COMPETES_WITH':
            edges.add((sid, tid)); edges.add((tid, sid))
    # fallback / enrich from graph edges
    uuid2id = {getattr(e,'uuid',None): c['agent_id'] for e,c in zip(entities, agent_configs)}
    for e, cfg in zip(entities, agent_configs):
        for ed in getattr(e, 'related_edges', []) or []:
            tgt = uuid2id.get(ed.get('target_node_uuid'))
            if tgt is not None and tgt != cfg['agent_id']:
                edges.add((cfg['agent_id'], tgt))
    return [[a, b] for a, b in edges]

----- [3-sim] sim-inject-round0-follows  (effort=M impact=high) dep=['sim-build-follow-edges-helper']
TITLE: Inject the seeded follow graph as round-0 FOLLOW edges in the OASIS run scripts
PROBLEM: Even once initial_follows is computed, nothing applies it. run_parallel_simulation.py builds the agent graph from profiles only and goes straight into the round loop after the initial CREATE_POST injection (run_parallel_simulation.py:1100-1172 Twitter, 1303-1383 Reddit). AgentGraph.add_edge(src,dst) exists (oasis agent_graph.py:206), the OASIS `follow` table exists and is written by the native generate_agents path (agents_generator.py:128,146-150), and ActionType.FOLLOW is already whitelisted (run_parallel_simulation.py:183,201) — so the capability is present but unused.
PROPOSAL: In run_twitter_simulation / run_reddit_simulation, after generate_*_agent_graph and after env.reset(), read config['event_config']['initial_follows'] and apply each edge before the round loop, mirroring the exact initial_posts injection pattern. Two complementary writes: (1) result.env.agent_graph.add_edge(follower, followee) so the in-memory graph reflects the structure; (2) inject ManualAction(ActionType.FOLLOW, {'followee_id': followee}) for the follower agent in a single batched env.step (so the recsys/num_followers ranking and the follow table both see the edges from round 1). Wrap in try/except per edge and log a count, mirroring the swallow-and-continue style already used for initial_posts, but emit an explicit '已建立 N 条初始关注边' log line (do NOT silently swallow the whole batch).
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py']
ACCEPTANCE:
   - After a run with K initial_follows, the twitter_simulation.db / reddit_simulation.db `follow` table contains >= K rows immediately after round 0, verifiable by SELECT COUNT(*) FROM follow.
   - The run log prints a '已建立 N 条初始关注边' line with N matching the applied edge count.
   - A run with empty/absent initial_follows behaves exactly as today (empty follow table at round 0, no crash).
   - agent_graph.get_agent(follower).num_followings or equivalent reflects the seeded edges (OASIS num_followers ranking is non-flat at round 1).
CODE_SKETCH:
# run_parallel_simulation.py, after env.reset() and the initial_posts block:
initial_follows = event_config.get('initial_follows', [])
follow_actions = {}
for pair in initial_follows:
    try:
        follower_id, followee_id = pair[0], pair[1]
        agent = result.env.agent_graph.get_agent(follower_id)
        result.env.agent_graph.add_edge(follower_id, followee_id)
        follow_actions[agent] = ManualAction(ActionType.FOLLOW, {'followee_id': followee_id})
    except Exception as e:
        log_info(f'跳过关注边 {pair}: {e}')
if follow_actions:
    try:
        await result.env.step(follow_actions)
        log_info(f'已建立 {len(follow_actions)} 条初始关注边')
    except Exception as e:
        log_info(f'初始关注边建立失败: {e}')

----- [3-sim] sim-relationship-briefing-persona  (effort=M impact=high) dep=['sim-contract-relationships-brief']
TITLE: Inject a named relationship briefing into every persona and fix the name-destroying placeholder
PROBLEM: Two compounding fidelity losses in persona generation. (1) The relationship structure already loaded into EntityNode.related_edges/related_nodes is flattened to prose where, for any edge lacking a free-text fact, _build_entity_context emits the literal placeholder '相关实体' INSTEAD of the neighbour's real name (oasis_profile_generator.py:453-456) — so a typed CRITICIZES/ALLIES_WITH edge becomes 'X criticizes (some entity)' and the agent never learns WHO. (2) actor_briefing injects role/stance/influence/memory but no relationships (actors.py:109-132), so agents are grounded in their own stance yet blind to allies/rivals; coalition formation is emergent noise. The whole context is then clamped to context[:3000] (oasis_profile_generator.py:696,745) so hub actors lose the most.
PROPOSAL: (a) Fix the placeholder: resolve target_node_uuid/source_node_uuid against entity.related_nodes (already carried) and emit the real neighbour name + its custom label, e.g. 'X --[CRITICIZES]--> 教育部(Government)'. (b) Add actors.py:relationship_briefing(actor_name, actors, max=6) that scans relationships[] for rows touching actor_name and renders a compact zh block '盟友: A(high); 对立: B; 监管: C'. Derive polarity from edge type/sign. (c) Append the relationship briefing after actor_briefing in _generate_profile_with_llm (oasis_profile_generator.py:524) and add a dedicated '## 你的社会关系（调研实证）' field to both individual and group persona prompts. (d) Build the relationship block BEFORE the 3000-char clamp so it is never the part truncated (prepend it, or exempt it from the clamp).
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/oasis_profile_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - For an entity with a typed graph edge to a named neighbour, the persona prompt contains the neighbour's REAL name (not '相关实体') and its label.
   - An actor present in relationships[] gets a non-empty '你的社会关系' block listing allies/rivals by real name; an actor absent from relationships[] gets no block and generation still succeeds.
   - For a hub actor with many relationships, the relationship block survives the 3000-char context clamp (verify by inspecting the assembled prompt).
   - relationship_briefing returns '' for an unknown actor name without raising.
CODE_SKETCH:
# actors.py
REL_LABEL = {'ALLY_OF':'盟友','OPPOSES':'对立','DEPENDS_ON':'依赖','COMPETES_WITH':'竞争','REGULATES':'监管','INFLUENCES':'影响'}
def relationship_briefing(actor_name, actors, max=6):
    rows = extract_relationship_rows(actors)
    me = normalize_name(actor_name); out = []
    for r in rows:
        if normalize_name(r['source']) == me:
            out.append(f"{REL_LABEL.get(r['type'].upper(),'关联')}: {r['target']}({r.get('strength','')})")
        elif normalize_name(r['target']) == me:
            out.append(f"被{REL_LABEL.get(r['type'].upper(),'关联')}: {r['source']}")
        if len(out) >= max: break
    return ('## 你的社会关系（调研实证，必须据此互动/@提及）\n' + '\n'.join('- '+x for x in out)) if out else ''
# oasis_profile_generator.py _build_entity_context fix (~453-456):
#   nb = {n['uuid']: n for n in (entity.related_nodes or [])}
#   tgt = nb.get(edge['target_node_uuid']); name = tgt['name'] if tgt else '(未知)'
#   line = f"{entity.name} --[{edge['edge_name']}]--> {name}({tgt.get('labels',[''])[0] if tgt else ''})"

----- [3-sim] sim-structured-active-set  (effort=M impact=high) dep=[]
TITLE: Make per-round active-agent selection socially structured (influence + stance + recency)
PROBLEM: get_active_agents_for_round (run_parallel_simulation.py:1001-1051) picks a flat random target_count = uniform(base_min,base_max)*multiplier, then activates each agent by a stance/influence-blind Bernoulli random()<activity_level. influence_weight and stance ARE computed per agent (simulation_config_generator.py:887-975) but are NEVER read at activation — high-influence researched actors are not preferentially activated or made more visible. Worse, target_count caps at ~uniform(5,20) regardless of cast size, so for a large researched cast most agents never act in any round, and there is no recency boost so cascades don't form around recently-active actors.
PROPOSAL: Rewrite the candidate gate and selection: (1) keep the active_hours filter, but replace the flat Bernoulli with a weighted activation probability p = activity_level * (0.5 + 0.5 * normalized_influence) * multiplier, where normalized_influence comes from cfg['influence_weight']. (2) Add a recency boost: read the previous round's acting agent_ids (pass last_active set into the function, or query the DB for last-round authors) and multiply p by ~1.5 for agents whose entity_name was mentioned/replied-to last round so conversations sustain. (3) Scale target_count with cast size: target_count = min(hard_cap, max(base, ceil(0.2 * num_agents))) * multiplier so large casts aren't starved. (4) Select with random.choices(weights=influence) so influential actors are over-represented but not deterministic. Keep all behavior backward-safe when influence_weight is absent (defaults to 1.0).
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py']
ACCEPTANCE:
   - Over a multi-round run, agents with influence_weight>=2.5 are activated in strictly more rounds on average than agents with influence_weight<=1.0 (measurable from actions.jsonl by joining agent_id->influence).
   - For a 60-agent cast, the per-round active count scales above the old uniform(5,20) ceiling (mean active per round > 20 at peak hours).
   - An agent that acted/was-mentioned in round r has measurably higher activation rate in round r+1 than its baseline.
   - A config with no influence_weight fields runs unchanged (no crash; activation reduces to the prior random gate within tolerance).
CODE_SKETCH:
def get_active_agents_for_round(env, config, current_hour, round_num, last_active=None):
    last_active = last_active or set()
    ...
    num_agents = len(agent_configs)
    target_count = int(min(120, max(base_min, math.ceil(0.2*num_agents))) * multiplier)
    weighted = []
    for cfg in agent_configs:
        if current_hour not in cfg.get('active_hours', range(8,23)): continue
        infl = cfg.get('influence_weight', 1.0)
        n_infl = min(1.0, (infl-0.8)/(3.0-0.8))
        p = cfg.get('activity_level',0.5) * (0.5 + 0.5*n_infl) * multiplier
        if cfg['agent_id'] in last_active: p *= 1.5
        if random.random() < min(p, 0.98):
            weighted.append((cfg['agent_id'], infl))
    if not weighted: return []
    ids, w = zip(*weighted)
    k = min(target_count, len(ids))
    selected_ids = list(dict.fromkeys(random.choices(ids, weights=w, k=min(k*2,len(ids)*2))))[:k]
    return [(i, env.agent_graph.get_agent(i)) for i in selected_ids if _safe(i)]

----- [3-sim] sim-feedback-loop-on-keyfree-typed  (effort=M impact=high) dep=[]
TITLE: Turn the graph-memory feedback loop ON (key-free) and write typed identity-preserving edges
PROBLEM: The ZepGraphMemoryUpdater feedback loop never runs in the integrated pipeline: PipelineOrchestrator.start_simulation is called with only platform/max_rounds (pipeline_orchestrator.py:1212-1216), never enable_graph_memory_update=True/graph_id. So the post-sim graph the ReportAgent mines equals the pre-sim graph — the simulation's emergent dynamics never enrich the KG. Even if enabled, ZepGraphMemoryUpdater.__init__ raises ValueError('ZEP_API_KEY未配置') when no key (zep_graph_memory_updater.py:240-243), contradicting the local no-key deployment; and when on, each action is flattened to one Chinese sentence and graph.add(type='text') re-extracts entities (zep_graph_memory_updater.py:390-419), discarding the already-known agent identity, round number, and platform — risking duplicate/mis-resolved nodes.
PROPOSAL: (1) pipeline_orchestrator RUN stage: pass enable_graph_memory_update=True, graph_id=graph_id to start_simulation. (2) zep_graph_memory_updater: when GRAPH_BACKEND is local (no real key), construct against the local Graphiti shim instead of raising — mirror how GraphBuilderService/ZepToolsService already accept the 'local-graphiti' sentinel; drop the hard raise. (3) Keep the existing free-text episode batch as enrichment BUT, when an action already carries agent_name + target author_name (from _enrich_action_context, run_parallel_simulation.py:811-833), also write a typed edge via a new add_triplet shim path (<A> LIKED/REPLIED_TO/FOLLOWED <B>, valid_at=round timestamp) so identity and round-level bi-temporality survive. Gate the typed-edge path behind a flag so it degrades to today's free-text behavior if add_triplet is unavailable. This is the feedback half of the loop the theme calls for.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_graph_memory_updater.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_runner.py']
ACCEPTANCE:
   - With GRAPH_BACKEND=local and no ZEP_API_KEY, constructing ZepGraphMemoryUpdater(graph_id) succeeds (no ValueError).
   - After a full pipeline run, the graph node/edge count post-simulation is strictly greater than pre-simulation (feedback actually landed), verifiable via get_graph_data before/after.
   - For a sample FOLLOW/LIKE action whose enrichment supplied both author and target names, a typed edge with both endpoints and a round-derived valid_at appears in the graph (when the typed-edge flag is on).
   - Disabling the typed-edge flag reproduces today's free-text-only behavior without error.
CODE_SKETCH:
# pipeline_orchestrator RUN stage (~1212):
run_state = SimulationRunner.start_simulation(
    simulation_id, platform='parallel', max_rounds=max_rounds,
    enable_graph_memory_update=Config.SIM_GRAPH_FEEDBACK,  # new knob, default True for local
    graph_id=graph_id)
# zep_graph_memory_updater.__init__:
if not self.api_key:
    if Config.GRAPH_BACKEND in ('auto','local'):
        from app.services.graphiti_client.client import GraphClient  # local shim
        self.client = GraphClient(); return
    raise ValueError('ZEP_API_KEY未配置')
# typed-edge path (depends on graph-seeding add_triplet shim):
if self.typed_edges and act.target_name:
    self.client.graph.add_triplet(self.graph_id, act.actor_name, act.predicate(), act.target_name, fact=act.to_episode_text(), valid_at=act.round_ts)

----- [3-sim] sim-fix-max-rounds-truncation  (effort=S impact=high) dep=[]
TITLE: Stop silently truncating a 72h simulation to 40 rounds
PROBLEM: start_simulation computes total_rounds = total_hours*60/minutes_per_round (default 72) but the classmethod default max_rounds=40 (simulation_runner.py:316) and the script CLI default --max-rounds 40 (run_parallel_simulation.py) almost always win, and the orchestrator only passes max_rounds when options.max_rounds is set (pipeline_orchestrator.py:1213-1215). So a sim the product calls a '72-hour forecast' is structurally cut at round 40 (~44% truncation), curtailing exactly the long-horizon opinion evolution the report claims to forecast — and the truncation is logged once, not surfaced.
PROPOSAL: Decouple the safety cap from the intended horizon. Default max_rounds to None (no cap) in start_simulation when the caller does not pass it, and have the orchestrator pass options.max_rounds through but default it from a Config.OASIS_DEFAULT_MAX_ROUNDS knob that is honest about the horizon (e.g. equal to total_rounds for full runs). Where a cap is applied and total_rounds is reduced, write a first-class field on SimulationRunState (rounds_truncated_from/to) so the UI and report can show that the horizon was shortened, instead of only a log line. Keep an explicit small cap available for cheap smoke runs.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_runner.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/config.py']
ACCEPTANCE:
   - A default full-pipeline run with a 72h/60min config executes the full 72 rounds (not 40), confirmable by the final round_num in actions.jsonl / run_state.json.
   - When a caller explicitly sets max_rounds=N < total_rounds, run_state.json carries rounds_truncated_from=total_rounds, rounds_truncated_to=N.
   - A smoke run with an explicit small max_rounds still truncates as before.
   - No regression: passing max_rounds=None runs all computed rounds.
CODE_SKETCH:
# simulation_runner.start_simulation signature: max_rounds: Optional[int] = None
# config.py: OASIS_DEFAULT_MAX_ROUNDS = int(os.getenv('OASIS_DEFAULT_MAX_ROUNDS', '0')) or None
# orchestrator RUN stage: mr = self.state.options.get('max_rounds') or Config.OASIS_DEFAULT_MAX_ROUNDS
# run script: if max_rounds and total_rounds>max_rounds: state.rounds_truncated_from=total_rounds; state.rounds_truncated_to=max_rounds

----- [3-sim] sim-scheduled-events-timeline-replay  (effort=M impact=medium) dep=['sim-contract-relationships-brief']
TITLE: Execute scheduled_events: replay the researched timeline as mid-simulation injections
PROBLEM: EventConfig.scheduled_events is defined but always [] and never executed; the round loop only injects round-0 initial_posts (run_parallel_simulation.py:1142-1172). DeerFlow key_events / timeline are only weakly used (hot_topics backfill + digest text). So the 72h sim reacts only to the opening seed and cannot trace the actual unfolding event chronology the research mapped — a major fidelity gap for a forecast that purports to model an evolving situation.
PROPOSAL: (1) In SimulationConfigGenerator, populate EventConfig.scheduled_events from actors.key_events (and timeline.json if present): map each event date onto a sim round index via (event_date - as_of_date) scaled into [0, total_rounds], and attach the most-relevant poster (highest-influence matched actor) plus the event text. Add actors.py:events_to_schedule(actors, total_rounds, as_of_date). (2) In the round loop, before active-agent selection, check scheduled_events for any whose round == round_num and inject them as ManualAction(CREATE_POST) by the matched poster_agent_id, reusing the exact initial_posts resolution/injection path. Fail-soft: events with unresolvable posters or out-of-window dates are skipped with a log line.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/actors.py']
ACCEPTANCE:
   - For a sim whose actors.key_events has dated events within the horizon, simulation_config.json contains scheduled_events with round indices in [0,total_rounds] and resolved poster_agent_id.
   - During the run, a scheduled event fires at its target round (a CREATE_POST with that event's content appears in actions.jsonl at the expected round).
   - Events dated before as_of_date or after the horizon are excluded; a config with no key_events yields scheduled_events=[] and the run is unchanged.
   - An event whose poster cannot be resolved is skipped with a log line, not a crash.
CODE_SKETCH:
# actors.py
def events_to_schedule(actors, total_rounds, as_of_date):
    from datetime import date
    out = []
    base = _parse_date(as_of_date)
    for ev in (actors.get('key_events') or []):
        d = _parse_date(ev.get('date'))
        if not d or not base: continue
        span = (d - base).days
        if span < 0: continue
        r = min(total_rounds-1, round(span/ max(1,(horizon_days)) * total_rounds))
        out.append({'round': r, 'event': ev.get('event'), 'date': ev.get('date')})
    return out
# run script loop, before get_active_agents_for_round:
for ev in scheduled_events:
    if ev['round'] == round_num:
        poster_id = _resolve_poster(ev, config)
        if poster_id is not None:
            await env.step({env.agent_graph.get_agent(poster_id): ManualAction(ActionType.CREATE_POST, {'content': ev['event']})})

----- [3-sim] sim-always-apply-influence-weight  (effort=S impact=medium) dep=[]
TITLE: Always apply the researched influence_weight (close the LLM-success bypass)
PROBLEM: The deterministic researched influence_weight override only fires on the rule-based fallback path (simulation_config_generator.py:958-961). On the LLM-success path (the common case) the model is merely 'hinted' to track the researched band (simulation_config_generator.py:896-902), so a high-influence researched actor can silently receive an arbitrary influence_weight whenever the per-batch LLM call succeeds. This is a silent fidelity regression that is invisible because the rule path looks correct — and it directly undermines sim-structured-active-set, which keys activation on influence_weight.
PROPOSAL: After building each AgentActivityConfig (on BOTH the LLM-success and rule paths, ~simulation_config_generator.py:976), apply the deterministic override: rw = influence_weight(matched_actors.get(agent_id)); if rw is not None: config.influence_weight = rw. Optionally clamp sentiment_bias sign to the researched stance polarity. This makes the researched influence band authoritative for every matched actor, regardless of whether the LLM batch succeeded.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py']
ACCEPTANCE:
   - For an actor matched to a 'high' research influence, the resulting agent_configs[i].influence_weight is in the researched high band (2.5-3.0) on BOTH the LLM-success and rule-fallback paths.
   - Agents with no matched actor keep the LLM/rule-derived weight unchanged.
   - A regression sim shows matched high-influence actors are consistently weighted high across runs (no run-to-run drift from LLM variance).
CODE_SKETCH:
# after config = AgentActivityConfig(...) is assembled for an agent_id:
matched = matched_actors.get(agent_id)
rw = influence_weight(matched)
if rw is not None:
    config.influence_weight = rw
    if matched and matched.get('stance'):
        config.sentiment_bias = _align_sign(config.sentiment_bias, matched['stance'])

----- [3-sim] sim-widen-twitter-actions  (effort=S impact=medium) dep=[]
TITLE: Widen the Twitter autonomous action set so conversations form threads
PROBLEM: Only 6/23 OASIS actions are wired for Twitter: CREATE_POST, LIKE_POST, REPOST, FOLLOW, DO_NOTHING, QUOTE_POST (run_parallel_simulation.py:179-186). Twitter has no CREATE_COMMENT/reply, no SEARCH, no TREND — so Twitter conversation is reply-less (only quote/repost), losing thread structure and the relational signal (who replies to whom) that the feedback loop and report mine. Reddit already wires 13 actions including CREATE_COMMENT.
PROPOSAL: Add the OASIS comment/discovery actions that the Twitter platform supports to TWITTER_ACTIONS — at minimum CREATE_COMMENT (replies, the biggest structural gain), and SEARCH_POSTS + TREND for discovery — keeping INTERVIEW manual-only. Before adding, confirm each ActionType is accepted by the Twitter DefaultPlatformType in the installed camel-oasis 0.2.5 (the action whitelist is validated at env build); guard with a capability check so an unsupported action is dropped rather than crashing the run. This enriches thread structure and the relational data downstream stages consume.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py']
ACCEPTANCE:
   - After adding CREATE_COMMENT to TWITTER_ACTIONS, a Twitter run produces comment/reply rows (actions.jsonl contains CREATE_COMMENT records and the comment table is non-empty).
   - The env builds without rejecting any action in the widened whitelist (no OASIS 'unsupported action' error at make/reset).
   - Reply threads are visible: at least some comments reference an existing post_id.
   - Removing the additions reverts to the prior reply-less behavior with no error.
CODE_SKETCH:
TWITTER_ACTIONS = [
    ActionType.CREATE_POST, ActionType.LIKE_POST, ActionType.REPOST,
    ActionType.QUOTE_POST, ActionType.FOLLOW, ActionType.DO_NOTHING,
    ActionType.CREATE_COMMENT,   # NEW: replies -> thread structure
    ActionType.SEARCH_POSTS,     # NEW: discovery (verify Twitter support)
    ActionType.TREND,            # NEW: discovery (verify Twitter support)
]
# guard: filter to actions the platform actually supports before passing to generate_twitter_agent_graph

----- [3-sim] sim-recsys-config-wired  (effort=M impact=medium) dep=[]
TITLE: Pass the generated recommender/echo-chamber weights into OASIS instead of dropping them
PROBLEM: PlatformConfig recency/popularity/relevance weights, viral_threshold, echo_chamber_strength are generated and serialized into simulation_config.json (simulation_config_generator.py:350-368) but oasis.make receives only agent_graph/platform/database_path/semaphore (run_parallel_simulation.py:1117-1122,1320-1325) — the recsys-tuning config is DEAD. OASIS's recsys runs at platform defaults (twhin-bert with refresh_rec_post_count=2/max_rec_post_len=2 for Twitter, per oasis/environment/env.py:81-96), so the echo-chamber/virality dynamics the report discusses are NOT parameterized by the config the LLM tuned.
PROPOSAL: Read config['twitter_config']/['reddit_config'] in run_twitter_simulation/run_reddit_simulation and map the supported knobs onto the OASIS Platform object's recsys parameters (refresh_rec_post_count, max_rec_post_len, recsys_type) that OasisEnv already accepts (env.py:50-96) — these are set on the platform/env, not on make() directly, so construct the Platform with the mapped values. For weights OASIS cannot consume (recency/popularity/relevance as continuous floats), either map them to the nearest supported recsys_type/post-count behavior or DELETE the unconsumable fields to stop implying fidelity that does not exist. Document which knobs are live.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py']
ACCEPTANCE:
   - Changing twitter_config.echo_chamber_strength / a mapped recsys knob between two runs produces a measurable difference in recommended-post exposure (e.g. refresh_rec_post_count reflected in per-round candidate counts).
   - The OASIS env builds with the mapped recsys params (no unexpected kwarg error), verified against the installed camel-oasis 0.2.5 OasisEnv signature.
   - Any PlatformConfig field that cannot be mapped is either consumed or removed from the serialized config (no permanently-dead knob remains undocumented).
   - A config relying on defaults still runs unchanged.
CODE_SKETCH:
# verify OasisEnv/platform recsys params first (env.py:50-96 -> recsys_type, refresh_rec_post_count, max_rec_post_len)
tw = config.get('twitter_config', {})
platform = oasis.DefaultPlatformType.TWITTER  # or a Platform(...) with mapped recsys params
# if Platform constructible with overrides:
#   refresh = max(1, round(2 * (1 + tw.get('echo_chamber_strength', 0))))
#   pass refresh_rec_post_count=refresh, max_rec_post_len=... into the platform/env
result.env = oasis.make(agent_graph=..., platform=platform, database_path=db_path, semaphore=...)

----- [3-sim] sim-persist-interviews-and-run-summary  (effort=M impact=medium) dep=['sim-feedback-loop-on-keyfree-typed']
TITLE: Persist interviews back into the graph and emit a structured run_summary.json
PROBLEM: interview_agents results — the richest end-of-run agent reflections (zep_tools.py:1315-1430) — are summarized into report prose and then lost; nothing retrievable persists, so a second report run or chat cannot retrieve them. There is also no aggregated outcome artifact: the report must re-mine emergent dynamics via fuzzy hybrid search over per-action sentence facts because no tool reads SimulationRunner.get_agent_stats/get_timeline/get_actions or runs community detection.
PROPOSAL: (1) After interview_agents_batch returns, write each agent's answer as a typed graph episode/edge (<agent> STATED_AT_END_OF_SIM <text>, valid_at=now) via the (key-free, typed) updater path so interviews become durable, queryable evidence. (2) Add SimulationRunner.write_run_summary(simulation_id) that aggregates actions.jsonl + the SQLite post/comment/follow tables into per-agent final stance/engagement, top cascades, and action volume by round, writing run_summary.json for the ReportAgent to consume directly. (3) Optionally call Graphiti build_communities once on the post-sim graph to label factions and include the community split in run_summary.json — giving the report ready-made coalition structure instead of re-deriving it.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_tools.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_runner.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_graph_memory_updater.py']
ACCEPTANCE:
   - After interviews, the graph contains per-agent STATED_AT_END_OF_SIM facts retrievable by a later search (a fresh chat/report query surfaces an interview answer).
   - write_run_summary produces run_summary.json with per-agent final engagement, action_volume_by_round, and top_posts for a completed run.
   - When build_communities is enabled, run_summary.json includes a faction/community split; when disabled the summary still writes without it.
   - A run where interviews are skipped still produces run_summary.json (degrades gracefully).
CODE_SKETCH:
# simulation_runner.write_run_summary(sim_id):
#   read actions.jsonl + sqlite -> {agent_id: {final_stance, posts, likes, followers}}
#   summary = {'per_agent': ..., 'action_volume_by_round': ..., 'top_posts': ...}
#   if Config.SIM_BUILD_COMMUNITIES: summary['factions'] = graph_client.graph.build_communities(graph_id)
#   json.dump(summary, open(sim_dir/'run_summary.json','w'))
# zep_tools: after interview_agents_batch, for each ans: updater.add_typed('STATED_AT_END_OF_SIM', agent, ans)

----- [3-sim] sim-cast-cap-influence-priority  (effort=S impact=medium) dep=[]
TITLE: Cap the agent cast with influence-based prioritization (always retain researched actors)
PROBLEM: filter_defined_entities returns the entire typed node set unbounded (zep_entity_reader.py:321-331) and prepare_simulation passes defined_entity_types=None, so a deep research dossier producing hundreds of typed nodes yields hundreds of agents — each costing 1 LLM persona call + 2 graph searches, throttled only by a 3-8 wide ThreadPool. This makes PREPARE the most expensive stage with no guardrail, and an oversized cast also dilutes the researched actors among generic extracted entities, hurting simulation fidelity (the social structure is dominated by noise nodes).
PROPOSAL: Add a max_agents param (Config.OASIS_MAX_AGENTS, e.g. 80) to filter_defined_entities. After filtering, rank entities by (matched-to-actors.json ? influence_weight : 0, len(related_edges)) and keep the top max_agents, ALWAYS retaining every actors.json-matched actor regardless of rank so researched signal is never dropped. Thread actors into the reader (or rank in prepare_simulation before generate_profiles_from_entities). Log matched/total and dropped counts so cast composition is observable. This bounds cost AND concentrates the social graph on the researched cast.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_entity_reader.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_manager.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/config.py']
ACCEPTANCE:
   - For a dossier with 300 typed nodes and OASIS_MAX_AGENTS=80, the cast is <=80 agents and every actors.json-matched actor is present.
   - The PREPARE stage LLM/search call count drops proportionally to the cap (observable in logs/timing).
   - A log line reports matched/total actors and how many entities were dropped by the cap.
   - With fewer typed nodes than the cap, behavior is unchanged (no entities dropped).
CODE_SKETCH:
# zep_entity_reader.filter_defined_entities(..., max_agents=None, actors=None):
if max_agents and len(filtered.entities) > max_agents:
    def rank(e):
        a = match_actor(e.name, actors)
        return (1 if a else 0, influence_weight(a) or 0.0, len(getattr(e,'related_edges',[]) or []))
    must_keep = [e for e in filtered.entities if match_actor(e.name, actors)]
    rest = sorted([e for e in filtered.entities if e not in must_keep], key=rank, reverse=True)
    filtered.entities = (must_keep + rest)[:max(max_agents, len(must_keep))]
    logger.info('cast cap: kept %d (%d researched) of %d', len(filtered.entities), len(must_keep), filtered.total)

----- [3-sim] sim-thread-brief-into-runner-personas  (effort=M impact=medium) dep=['sim-contract-relationships-brief', 'sim-scheduled-events-timeline-replay']
TITLE: Thread the situation brief into the runner clock, personas, and event timing
PROBLEM: There is no machine-readable situation brief handed to the runner or agents: the subprocess receives only --config; central_question/as_of_date/fault-lines are unavailable to the round loop. initial_posts content is LLM-authored from a digest and each persona's only grounding is its own prose; agents do not share a common ground-truth situation, and the sim clock is not anchored to as_of_date (so scheduled-event timing in sim-scheduled-events-timeline-replay has no real anchor).
PROPOSAL: (1) Persist a situation_brief block into simulation_config.json from actors.json/situation_brief.json (SimulationConfigGenerator). (2) In run_parallel_simulation, set the sim start day/hour from as_of_date so the clock is anchored (enabling correct round<->date mapping for scheduled events). (3) Prepend a compact brief summary (current_situation + key_tensions) to each agent's user_char/persona so all agents reason from the same situation. (4) Feed situation_brief verbatim into SimulationConfigGenerator._build_context ABOVE the truncated raw document_text so event/agent steps reason over a clean brief rather than a mid-sentence cut of the dossier.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/oasis_profile_generator.py']
ACCEPTANCE:
   - simulation_config.json contains a situation_brief block when actors.json/situation_brief.json is present.
   - The sim clock's round->date mapping starts at as_of_date (verifiable: round 0 timestamp == as_of_date).
   - Each generated persona's user_char contains the brief summary prefix; a run without a brief produces personas unchanged.
   - SimulationConfigGenerator._build_context includes the brief above the document_text (verify by inspecting the assembled context).
CODE_SKETCH:
# config gen _build_context: ctx = brief_text + '\n\n' + entity_summary + actors_digest + doc_text[:remaining]
# run script: base_date = _parse_date(config.get('situation_brief',{}).get('as_of_date'))
#   round_to_date(r) = base_date + timedelta(minutes=r*minutes_per_round)
# oasis_profile_generator: persona_header = brief_summary + '\n' ; user_char = persona_header + bio + persona

----- [3-sim] sim-seed-previous-posts  (effort=M impact=medium) dep=['sim-contract-relationships-brief']
TITLE: Seed stage-setting previous posts from actor memory so the world is not cold at round 0
PROBLEM: Agents wake with empty timelines: the Twitter CSV and Reddit JSON writers omit the previous_tweets column that OASIS's native generate_agents supports (agents_generator.py:130-133), so the researched actor.memory injected into the persona prose never manifests as actual visible content other agents can react to. The simulation therefore starts colder than the research warrants — round-0 has only the handful of initial_posts.
PROPOSAL: Low-risk option A (reuse the existing initial_posts injection path): for each high-influence matched agent, derive one 'stage-setting' CREATE_POST from actor.memory/role and add it to the round-0 ManualAction batch alongside initial_posts, so researched memory becomes observable in-sim with near-zero risk. Build these in SimulationConfigGenerator as additional initial_posts tagged is_memory_seed, authored by the matched actor (poster_name). This makes the opening world state reflect what the researched actors already believe/know, improving early-round realism without touching the OASIS profile loader.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/scripts/run_parallel_simulation.py']
ACCEPTANCE:
   - For a sim with high-influence matched actors carrying memory, round-0 contains stage-setting posts attributed to those actors (actions.jsonl round 0 shows CREATE_POSTs derived from memory, poster_agent_id matching the actor).
   - Other agents can react to these posts in round 1 (the seeded content appears in subsequent likes/replies/quotes).
   - Actors without memory produce no seed post; a config with no matched actors round-0 behaves as today.
   - The number of memory-seed posts is bounded (e.g. top-K by influence) to avoid flooding round 0.
CODE_SKETCH:
# config gen: build memory seed posts
for a in top_k_by_influence(matched_actors, k=8):
    mem = (a.get('memory') or '').strip()
    if mem:
        initial_posts.append({'content': _stage_setting_text(a, mem), 'poster_name': a['name'], 'is_memory_seed': True})
# run script: these flow through the existing initial_posts -> round-0 CREATE_POST injection unchanged

----- [3-sim] sim-echo-chamber-clustering  (effort=M impact=medium) dep=['sim-build-follow-edges-helper', 'sim-inject-round0-follows']
TITLE: Build echo-chamber structure by clustering agents on stance + topic and pre-following within clusters
PROBLEM: echo_chamber_strength is generated but dead; real echo chambers should be structural — agents sharing a stance and topic affinity should disproportionately follow each other at t=0. Today stance is computed per agent but only used as a persona string; it never shapes who-sees-whom. Without intra-cluster pre-following, the seeded relationship follows (sim-build-follow-edges-helper) capture explicit researched ties but not the broader homophily that produces polarized opinion dynamics.
PROPOSAL: After agent batches complete, group agent_configs by (stance bucket, dominant hot_topic/interested_topic overlap). For each cluster add intra-cluster follow edges with probability scaled by twitter_config.echo_chamber_strength, plus a smaller cross-cluster bridge probability for high-influence agents so narratives can still leak. Emit these into the SAME event_config.initial_follows list that sim-build-follow-edges-helper populates and sim-inject-round0-follows applies — so echo_chamber_strength finally drives behavior through the existing follow-seeding machinery, no new injection path needed. Store interested_topics on AgentActivityConfig if not already present so clustering has signal.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/simulation_config_generator.py']
ACCEPTANCE:
   - With echo_chamber_strength high, initial_follows contains denser intra-stance-cluster edges than cross-cluster edges (measurable: same-stance follow pairs >> opposite-stance pairs).
   - With echo_chamber_strength=0, only the explicit relationship-derived follows remain (no homophily edges added).
   - High-influence agents have at least some cross-cluster bridge follows so opinion can leak (no fully disconnected components dominated by one stance).
   - Clustering runs without error when stance/topic fields are sparse (degrades to relationship-only follows).
CODE_SKETCH:
clusters = defaultdict(list)
for cfg in agent_configs:
    key = (_stance_bucket(cfg.get('stance')), _dominant_topic(cfg))
    clusters[key].append(cfg['agent_id'])
ecs = twitter_config.echo_chamber_strength
for ids in clusters.values():
    for a in ids:
        for b in ids:
            if a != b and random.random() < 0.3*ecs:
                initial_follows.append([a, b])
# cross-cluster bridges for high-influence agents at lower prob

----- [2-graph] sim-graph-seed-add-triplet  (effort=M impact=high) dep=['sim-contract-relationships-brief']
TITLE: Surface add_triplet on the shim and seed researched actors+relationships as KG edges before ingestion
PROBLEM: The knowledge graph the simulation/persona layer reads is rebuilt purely from chunked research_report.md (graph_builder.py add_text_batches; pipeline_orchestrator.py:1147-1156) — the structured, high-confidence actor relationships research already found are re-extracted lossily by the local LLM. graphiti-core exposes add_triplet (graphiti.py:1645) but the Zep-compat shim only surfaces add/add_batch/search (client.py:127-184), so even with relationships[] in hand there is no API to write them as graph edges. This blocks deterministic, named relationship neighbourhoods reaching ZepEntityReader (the source for relationship-aware personas and follow seeding) and the typed feedback edges in sim-feedback-loop-on-keyfree-typed.
PROPOSAL: (1) Add runtime.add_triplet(graph_id, source_name, edge_type, target_name, fact, valid_at) wrapping graphiti_core.add_triplet on the shim's existing background loop (runtime.py:72-74 bridge), expose it on the shim graph namespace (client.py). (2) Add GraphBuilderService.seed_actors(graph_id, actors) that, after create_graph/set_ontology and BEFORE add_text_batches, writes each actors[] row as an entity and each relationships[] edge as a triplet with valid_at=as_of_date. (3) Call builder.seed_actors(graph_id, actors) in pipeline_orchestrator._run at the GRAPH stage (~1149), gated by a Config.GRAPH_SEED_FROM_ACTORS flag. Re-extraction from the report still runs and enriches; the seeded edges guarantee the cast+relations survive and give ZepEntityReader deterministic related_edges/related_nodes with REAL neighbour names for the persona/follow layers.
FILES: ['/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/client.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/pipeline_orchestrator.py', '/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/config.py']
ACCEPTANCE:
   - client.graph.add_triplet(...) writes an EntityNode pair + typed EntityEdge into the local FalkorDB graph (verifiable via get_graph_data showing the named edge).
   - After seed_actors, the post-build graph contains every actors[] name as a node and every relationships[] edge as a typed edge with valid_at=as_of_date, BEFORE any report chunk is ingested.
   - ZepEntityReader.filter_defined_entities then returns related_edges/related_nodes with the real seeded neighbour names (no '相关实体' placeholder needed for seeded edges).
   - With GRAPH_SEED_FROM_ACTORS=false the graph builds exactly as today (text-only).
CODE_SKETCH:
# runtime.py
def add_triplet(self, graph_id, source_name, edge_type, target_name, fact, valid_at=None):
    g = self._ensure_graph(graph_id)
    src = EntityNode(name=source_name, group_id=graph_id, labels=['Entity'])
    tgt = EntityNode(name=target_name, group_id=graph_id, labels=['Entity'])
    edge = EntityEdge(source_node_uuid=src.uuid, target_node_uuid=tgt.uuid, name=edge_type, fact=fact, group_id=graph_id, valid_at=valid_at)
    return self.run(g.add_triplet(src, edge, tgt))
# graph_builder.seed_actors(graph_id, actors): for r in extract_relationship_rows(actors): client.graph.add_triplet(graph_id, r['source'], r['type'], r['target'], r.get('basis',''), as_of_date)
# orchestrator GRAPH stage (~1149): if Config.GRAPH_SEED_FROM_ACTORS: builder.seed_actors(graph_id, actors)


==========================================================================================
THEME: Knowledge-graph power: direct relationship seeding, communities, bi-temporal, build performance
==========================================================================================
SUMMARY: The Graphiti knowledge graph is the pipeline's most under-exploited asset. graphiti-core (graphiti.py:1645 add_triplet, :1490 build_communities) ships exactly the capabilities this theme needs, but the Zep-compat shim (client.py, runtime.py) surfaces only episode-based add/add_batch/search — so today the KG is rebuilt entirely by re-LLM-extracting entities from chunked research_report.md prose (pipeline_orchestrator.py:1147-1161). The researched actor cast and relationships, the timeline dates, and any notion of factions are all thrown away and rediscovered lossily. Three structural facts drive these proposals: (1) add_triplet resolves/dedups against existing nodes and accepts an EntityEdge with valid_at, so researched relationships can be seeded as authoritative, bi-temporally-stamped edges BEFORE prose ingestion enriches them; (2) the shim adds episodes strictly serially on one background loop (client.py:139-152, runtime.run) with a dead time.sleep(1) per batch (graph_builder.py:332) and a no-op _wait_for_episodes — so build time is N serial LLM extraction calls and batch_size is a pure UI knob, making concurrency the dominant performance win; (3) build_communities(group_ids=[graph_id]) is one call away and gives the sim echo-chamber seeds and the report ready-made faction structure. The ranked plan: first surface add_triplet + valid_at + build_communities through the shim/runtime (the enabling infra), then seed actors+relationships as triplets anchored to as_of_date/timeline dates, run community detection, parallelize ingest, kill the dead sleep/wait latency, ground episode reference_time, and make rebuilds idempotent/resumable. Everything builds on the existing per-graph Graphiti cache (runtime._ensure_graph), the run() sync→async bridge, the local sentence-transformers embedder's create_batch, and the orchestrator's existing GRAPH-stage reuse guard. The relationship-seeding chain depends on the contract producing a relationships[] array (kg-shim-add-triplet is the consumer-side enabler; the producer change is owned by the contract theme but referenced here).

----- [2-graph] kg-shim-add-triplet  (effort=M impact=high) dep=[]
TITLE: Surface Graphiti add_triplet through the runtime + Zep-compat shim (the enabling primitive)
PROBLEM: graphiti-core exposes Graphiti.add_triplet(source_node, edge, target_node) (graphiti-0.29.2/graphiti_core/graphiti.py:1645) which resolves/dedups nodes against the existing graph, generates embeddings, and writes a typed EntityEdge with valid_at — exactly the deterministic edge-seeding primitive this theme needs. But the shim (backend/app/services/graphiti_client/client.py:119-175 _GraphNamespace) only exposes create/set_ontology/add_batch/add/search, and runtime.py (backend/app/services/graphiti_client/runtime.py:256-306) only wraps add_episode. There is no code path anywhere to write a known (subject, predicate, object) edge as a graph edge; every edge is re-LLM-extracted from prose.
PROPOSAL: Add an add_triplet method to GraphitiRuntime that constructs EntityNode/EntityEdge from primitives and runs graphiti.add_triplet on the existing background loop via self.run(...), and expose it on _GraphNamespace. Reuse the per-graph cache (_ensure_graph) and the run() sync→async bridge already in place. Map source/target labels to the ontology entity types when known, else 'Entity'. Accept valid_at so callers can anchor bi-temporality (depended on by kg-bitemporal-seed).
FILES: ['backend/app/services/graphiti_client/runtime.py', 'backend/app/services/graphiti_client/client.py']
ACCEPTANCE:
   - A unit/integration script calls client.graph.add_triplet(gid, 'Ministry of Education', 'REGULATES', 'University X', 'MOE regulates University X', valid_at=<date>) twice with the same names and the graph contains exactly one source node, one target node, one edge (dedup verified via fetch_all_nodes/fetch_all_edges).
   - The created edge appears in EDGE_HYBRID_SEARCH_RRF results for query 'regulates University X' (i.e. fact_embedding was generated).
   - Passing valid_at sets the edge's valid_at in get_graph_data output (zep_paging unwrap) rather than null/now().
   - Calling add_triplet on a graph_id whose ontology was set still works (labels beyond Entity do not break SanitizingFalkorDriver writes).
CODE_SKETCH:
# runtime.py (add near add_episode ~256)
from datetime import datetime, timezone

def add_triplet(self, graph_id, source_name, edge_name, target_name, fact,
                valid_at=None, source_label='Entity', target_label='Entity',
                source_attrs=None, target_attrs=None, edge_attrs=None):
    return self.run(self._add_triplet(
        graph_id, source_name, edge_name, target_name, fact, valid_at,
        source_label, target_label, source_attrs, target_attrs, edge_attrs))

async def _add_triplet(self, graph_id, source_name, edge_name, target_name, fact,
                       valid_at, source_label, target_label,
                       source_attrs, target_attrs, edge_attrs):
    g = await self._ensure_graph(graph_id)
    from graphiti_core.nodes import EntityNode
    from graphiti_core.edges import EntityEdge
    from uuid import uuid4
    now = datetime.now(timezone.utc)
    src = EntityNode(name=source_name, group_id=graph_id,
                     labels=['Entity'] + ([source_label] if source_label and source_label != 'Entity' else []),
                     summary='', attributes=source_attrs or {})
    tgt = EntityNode(name=target_name, group_id=graph_id,
                     labels=['Entity'] + ([target_label] if target_label and target_label != 'Entity' else []),
                     summary='', attributes=target_attrs or {})
    edge = EntityEdge(name=edge_name, fact=fact or f'{source_name} {edge_name} {target_name}',
                      group_id=graph_id, source_node_uuid=src.uuid, target_node_uuid=tgt.uuid,
                      created_at=now, valid_at=valid_at, episodes=[], attributes=edge_attrs or {})
    # add_triplet resolves src/tgt against existing nodes by name+embedding (dedup)
    await g.add_triplet(src, edge, tgt)
    return edge.uuid

# client.py _GraphNamespace (add method)
def add_triplet(self, graph_id, source_name, edge_name, target_name, fact,
                valid_at=None, source_label='Entity', target_label='Entity', **kw):
    return self._rt.add_triplet(graph_id, source_name, edge_name, target_name, fact,
                                valid_at=valid_at, source_label=source_label,
                                target_label=target_label, **kw)

----- [2-graph] kg-seed-actors-relationships  (effort=M impact=high) dep=['kg-shim-add-triplet']
TITLE: Seed researched actors + relationships as Graphiti triplets before prose ingestion
PROBLEM: The GRAPH stage (pipeline_orchestrator.py:1144-1166) builds the KG ONLY by chunking research_report.md and re-extracting entities/edges with the local LLM. The structured cast (actors.json, already typed Person/Organization/Media/Government/Platform) and — once the contract emits it — the relationships[] array are discarded and rediscovered noisily. ZepEntityReader's downstream cast then depends on extraction luck; actors whose names the LLM mis-extracts contribute zero persona/stance signal. There is no path to inject known facts as ground truth.
PROPOSAL: Add GraphBuilderService.seed_actors(graph_id, actors) that, after create_graph+set_ontology and BEFORE add_text_batches, writes each actors[] row as an Entity node (via a one-line triplet IS_A or an attribute-only node) and each relationships[] edge as a triplet with valid_at=as_of_date, using the new shim add_triplet. Call it in the orchestrator GRAPH stage right after set_ontology (pipeline_orchestrator.py:1149). Prose extraction still runs and enriches; because add_triplet resolves by name, the seeded canonical nodes anchor extraction dedup instead of competing with it. Gate behind Config.GRAPH_SEED_FROM_ACTORS=true so it is a no-op when actors is None (timeout-salvage path).
FILES: ['backend/app/services/graph_builder.py', 'backend/app/services/pipeline_orchestrator.py', 'backend/app/utils/actors.py', 'backend/app/config.py']
ACCEPTANCE:
   - After a full pipeline run on a prompt whose actors.json has relationships[], get_graph_data shows the seeded edges with edge.name matching the relationship type (e.g. OPPOSES) and source/target names matching actor names — verifiable before any sim feedback.
   - An actor named in relationships[] but spelled slightly differently in the report prose resolves to ONE node (seeded + extracted merged), confirmed by node count not double-counting that actor.
   - When actors is None (timeout salvage), the GRAPH stage runs unchanged and seed_actors is skipped (log line confirms skip).
   - Seeded edges carry valid_at == actors.as_of_date (not now()) in get_graph_data.
CODE_SKETCH:
# actors.py — new helpers
def extract_relationship_rows(actors):
    rels = (actors or {}).get('relationships') or []
    out = []
    for r in rels:
        s, t = (r.get('source') or '').strip(), (r.get('target') or '').strip()
        if s and t:
            out.append({'source': s, 'target': t,
                        'type': (r.get('type') or 'RELATED_TO').upper(),
                        'fact': r.get('basis') or r.get('note') or f'{s} {r.get("type","related to")} {t}'})
    return out

ACTOR_TYPE_TO_LABEL = {'Person':'Person','Organization':'Organization','Media':'Organization',
                      'Government':'Organization','Platform':'Organization'}

# graph_builder.py
def seed_actors(self, graph_id, actors, valid_at=None):
    from app.utils.actors import extract_actor_rows, extract_relationship_rows, ACTOR_TYPE_TO_LABEL
    rows = extract_actor_rows(actors)
    label_by_name = { (a.get('name') or '').strip(): ACTOR_TYPE_TO_LABEL.get(a.get('type'),'Entity') for a in rows }
    seeded = 0
    for rel in extract_relationship_rows(actors):
        self.client.graph.add_triplet(
            graph_id, rel['source'], rel['type'], rel['target'], rel['fact'],
            valid_at=valid_at,
            source_label=label_by_name.get(rel['source'],'Entity'),
            target_label=label_by_name.get(rel['target'],'Entity'))
        seeded += 1
    return seeded  # surface count for telemetry

# pipeline_orchestrator.py GRAPH stage (~1149, after set_ontology)
if Config.GRAPH_SEED_FROM_ACTORS and actors:
    from app.utils.dates import parse_as_of  # parse actors['as_of_date']
    n = builder.seed_actors(graph_id, actors, valid_at=parse_as_of(actors.get('as_of_date')))
    upd(8, f'已注入 {n} 条调研关系边…')

----- [2-graph] kg-build-communities  (effort=M impact=medium) dep=['kg-seed-actors-relationships']
TITLE: Run build_communities after graph build and expose faction structure to sim + report
PROBLEM: Graphiti.build_communities(group_ids) (graphiti.py:1490) does Leiden-style clustering + LLM community summaries — ideal for detecting camps/coalitions in an opinion event — but is never called by the shim. The sim's echo_chamber_strength is a dead config knob and the report agent re-derives coalitions per-question via fuzzy search. The seeded+enriched actor graph (after kg-seed-actors-relationships) is the perfect substrate for one community-detection pass.
PROPOSAL: Add build_communities to runtime + shim (one self.run wrapper around g.build_communities(group_ids=[graph_id])). Call it from GraphBuilderService at the end of the build (after _wait_for_episodes, in the orchestrator GRAPH stage progress band 95→98). Add a read helper that lists community nodes + member entities so ZepToolsService can expose a faction_map to the report agent and SimulationConfigGenerator can derive echo-chamber/clustering seeds. Make it best-effort (catch+log; never fail the build) since community LLM summaries cost extra calls.
FILES: ['backend/app/services/graphiti_client/runtime.py', 'backend/app/services/graphiti_client/client.py', 'backend/app/services/graph_builder.py', 'backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - After build, get_graph_data (or a new list-communities call) returns >=1 community node for a multi-actor dossier; each community has a non-empty summary.
   - Re-running build_communities on the same graph clears prior communities first (remove_communities is called internally) so counts don't accumulate across resume.
   - If the community LLM call errors, the GRAPH stage still completes successfully (build_communities returns [] and logs a warning).
   - A report-side faction_map tool (or at minimum a persisted communities.json) lists which actor entities belong to which community.
CODE_SKETCH:
# runtime.py
def build_communities(self, graph_id):
    return self.run(self._build_communities(graph_id))
async def _build_communities(self, graph_id):
    g = await self._ensure_graph(graph_id)
    nodes, edges = await g.build_communities(group_ids=[graph_id])
    return [{'uuid': n.uuid, 'name': n.name, 'summary': getattr(n,'summary','')} for n in nodes]

# client.py _GraphNamespace
def build_communities(self, graph_id):
    return self._rt.build_communities(graph_id)

# graph_builder.py
def build_communities(self, graph_id):
    try:
        return self.client.graph.build_communities(graph_id) or []
    except Exception as e:
        logger.warning('build_communities failed (non-fatal): %s', e)
        return []

# pipeline_orchestrator.py GRAPH stage, after _wait_for_episodes (~1161)
upd(95, '检测社区/阵营结构…')
communities = builder.build_communities(graph_id)
upd(98, f'识别到 {len(communities)} 个阵营')
# persist community labels onto project for report/sim consumption

----- [2-graph] kg-parallel-ingest  (effort=M impact=high) dep=[]
TITLE: Parallelize episode ingest (bounded concurrency) — the dominant build-time win
PROBLEM: The shim's add_batch loops episodes ONE AT A TIME calling runtime.add_episode sequentially (client.py:139-152), and runtime.run blocks on asyncio.run_coroutine_threadsafe(...).result() per episode (runtime.py:72-74), so each chunk's LLM extraction + embedding finishes before the next is submitted. On a 150-chunk deep report this is N serial LLM calls = minutes of wall-clock. The orchestrator's batch_size=10 comment claims near-linear throughput (pipeline_orchestrator.py:1154-1156) but batch_size changes NOTHING about ingest speed — it only changes progress granularity. This is the single biggest latency lever in the whole KG build.
PROPOSAL: Add runtime.add_episodes_concurrent(graph_id, episodes, concurrency) that schedules N add_episode coroutines under an asyncio.Semaphore on the existing background loop and awaits them together (return uuids in order). Have client.add_batch delegate to it. Pick concurrency from the provider: CLI providers (claude-cli/codex-cli subprocess) → 3 (mirroring OASIS's CLI semaphore), OpenAI-compatible HTTP → 8-16. Gate behind Config.GRAPH_BUILD_CONCURRENCY (default 1 to preserve current dedup-correct behavior; document that >1 trades a small dedup-ordering risk for large speedup). Graphiti's per-graph extraction is independent per episode; cross-episode dedup runs at save time, so bounded concurrency is safe in practice for the research corpus.
FILES: ['backend/app/services/graphiti_client/runtime.py', 'backend/app/services/graphiti_client/client.py', 'backend/app/config.py', 'backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - With GRAPH_BUILD_CONCURRENCY=8 on an HTTP provider, total GRAPH-stage wall-clock for a fixed ~100-chunk report is measurably lower (>=2x) than with concurrency=1, captured as before/after timings in the GRAPH stage StageState started_at/finished_at.
   - Final node_count/edge_count for the same input is within a small tolerance between concurrency=1 and concurrency=8 (no catastrophic dedup loss).
   - With concurrency=1 (default), behavior is byte-identical to today (regression-safe).
   - uuids are returned in episode order so downstream uuid handling is unaffected.
CODE_SKETCH:
# runtime.py
def add_episodes_concurrent(self, graph_id, episodes, concurrency=4):
    return self.run(self._add_episodes_concurrent(graph_id, episodes, concurrency))
async def _add_episodes_concurrent(self, graph_id, episodes, concurrency):
    sem = asyncio.Semaphore(max(1, concurrency))
    async def one(i, ep):
        async with sem:
            return await self._add_episode(graph_id, name=ep.get('name', f'chunk-{i}'),
                body=ep['data'], source_type=ep.get('type','text'),
                source_description=ep.get('source_description','mirofish-text'),
                reference_time=ep.get('reference_time'))
    return await asyncio.gather(*[one(i, ep) for i, ep in enumerate(episodes)])

# client.py add_batch — when Config concurrency>1, delegate:
if concurrency and concurrency > 1:
    eps = [{'data': getattr(ep,'data','') or '', 'type': getattr(ep,'type','text') or 'text',
            'reference_time': getattr(ep,'reference_time',None)} for ep in episodes]
    uuids = self._rt.add_episodes_concurrent(graph_id, eps, concurrency)
    return [_ZepEpisode(u) for u in uuids]

# config.py: GRAPH_BUILD_CONCURRENCY = int(os.environ.get('GRAPH_BUILD_CONCURRENCY','1'))
# auto-pick in orchestrator: 3 for CLI providers, 8 for HTTP, overridable by env

----- [2-graph] kg-kill-dead-latency  (effort=S impact=medium) dep=[]
TITLE: Remove the per-batch time.sleep(1) and short-circuit the no-op _wait_for_episodes
PROBLEM: graph_builder.add_text_batches sleeps time.sleep(1) after EVERY batch (graph_builder.py:332) as cloud rate-limit avoidance — pure dead latency against a local FalkorDB with no rate limits (e.g. 150 chunks / batch_size 10 = 15s of pure sleep). Separately, _wait_for_episodes (graph_builder.py:341-395) polls episode.processed, but the shim's _EpisodeNamespace.get always returns processed=True (client.py:114-116) because Graphiti ingestion is synchronous-on-return — so the entire ≤600s wait loop collapses to a single no-op pass, yet it advertises a 65→98% progress band and adds complexity.
PROPOSAL: Delete the time.sleep(1) in add_text_batches (or gate it behind a GRAPHITI_REMOTE flag that is false for local). Replace the body of _wait_for_episodes with an immediate progress_callback(..., 1.0) return (keep the signature so the orchestrator call site is untouched) and add a one-line comment that local Graphiti ingestion is synchronous. Reclaim the 65→98 progress band for build_communities (kg-build-communities) instead of a fake wait.
FILES: ['backend/app/services/graph_builder.py']
ACCEPTANCE:
   - GRAPH-stage wall-clock drops by ~(num_batches * 1s) versus before on the same input (timing captured in StageState).
   - The pipeline still reports smooth progress through the graph band (no stall at 65%).
   - No call sites of _wait_for_episodes break (signature unchanged); orchestrator GRAPH stage completes.
   - When GRAPHITI_REMOTE is set (future cloud path), the sleep behavior is restored.
CODE_SKETCH:
# graph_builder.py add_text_batches: remove line 332 `time.sleep(1)`
# (or: if os.environ.get('GRAPHITI_REMOTE'): time.sleep(1))

# graph_builder.py _wait_for_episodes -> collapse to no-op
def _wait_for_episodes(self, episode_uuids, progress_callback=None, timeout=600):
    # Local Graphiti ingestion is synchronous on add_episode return; nothing to await.
    if progress_callback:
        progress_callback(f'文本处理完成 ({len(episode_uuids)} 块)', 1.0)
    return

----- [2-graph] kg-bitemporal-seed  (effort=S impact=medium) dep=['kg-seed-actors-relationships']
TITLE: Ground episode reference_time and seeded-edge valid_at from timeline.json / key_events / as_of_date
PROBLEM: runtime._add_episode supports reference_time but add_text_batches builds EpisodeData(data, type) with no date, so every research chunk is stamped datetime.now() (runtime.py:292-293) — collapsing the bi-temporal axis Graphiti was chosen for. timeline.json/key_events dates and actors.as_of_date never anchor any fact's valid_at, so panorama_search's active-vs-historical split (zep_tools.py:1188-1278) and any report evolution claim are spurious (all facts share ingest time). The sim feedback path (future) would compound this.
PROPOSAL: Thread an optional reference_time through EpisodeData → add_text_batches → runtime.add_episode (the param already exists, just defaulted). Default all research chunks to actors.as_of_date when present (a correct lower bound: facts known as-of the research cutoff). For seeded relationship edges (kg-seed-actors-relationships) and key_events, pass the specific event date as valid_at. Add a tiny dates helper to parse as_of_date / key_event dates leniently (ISO and common zh formats), falling back to now() only when unparseable.
FILES: ['backend/app/services/graph_builder.py', 'backend/app/services/graphiti_client/client.py', 'backend/app/services/pipeline_orchestrator.py', 'backend/app/utils/dates.py']
ACCEPTANCE:
   - After a run whose actors.as_of_date is set, sampled research-derived edges in get_graph_data have valid_at == as_of_date (not the ingest timestamp).
   - panorama_search returns a non-trivial active/historical split when timeline key_events span different dates and those dates are applied as valid_at on seeded edges.
   - When as_of_date is missing/unparseable, ingest still succeeds and falls back to now() (no crash).
   - EpisodeData without reference_time behaves exactly as today (back-compat).
CODE_SKETCH:
# graph_builder.add_text_batches gains reference_time param
def add_text_batches(self, graph_id, chunks, batch_size=3, progress_callback=None, reference_time=None):
    episodes = [EpisodeData(data=c, type='text', reference_time=reference_time) for c in batch_chunks]
# EpisodeData dataclass gains optional reference_time: datetime|None=None
# client.add_batch forwards getattr(ep,'reference_time',None) -> runtime.add_episode(reference_time=...)

# pipeline_orchestrator.py GRAPH stage:
from app.utils.dates import parse_as_of
as_of = parse_as_of((actors or {}).get('as_of_date'))
uuids = builder.add_text_batches(graph_id, chunks, batch_size=10,
                                 progress_callback=add_cb, reference_time=as_of)
# seed_actors(..., valid_at=as_of); per key_event edges use their own date

----- [2-graph] kg-idempotent-rebuild-resume  (effort=M impact=medium) dep=['kg-seed-actors-relationships']
TITLE: Make graph build idempotent/resumable: validate non-empty graph on resume and rebuild deterministically
PROBLEM: The GRAPH-stage resume guard (pipeline_orchestrator.py:1140-1143) reuses an existing graph if the stage flag is set and graph_id is present — it does NOT check the graph has usable entities. A graph that built but yielded 0 filtered entities is reused and then fails in PREPARE (per findings, simulation_manager.py:304-308). Conversely a re-run mints a fresh mirofish_<hex> graph_id every time (graph_builder.create_graph), so a failed-then-resumed build can leave orphan partial graphs in FalkorDB with no cleanup, and seeded triplets (kg-seed-actors-relationships) could be double-written on a retry that reuses the same graph_id.
PROPOSAL: On resume, before reusing the graph, run a cheap entity-count check (ZepEntityReader / fetch_all_nodes count > 0); if zero, fall through to rebuild instead of resuming into a broken state. Make seed_actors idempotent by relying on add_triplet's name-based dedup (already resolves existing nodes) plus an edge guard that skips a relationship already present between the two resolved nodes with the same edge name. Record graph entity_count + community_count + seeded_edge_count in pipeline_state for observability and to drive the resume health check. This builds on the existing reuse-guard pattern rather than replacing resume.
FILES: ['backend/app/services/pipeline_orchestrator.py', 'backend/app/services/graph_builder.py']
ACCEPTANCE:
   - Resuming a pipeline whose graph stage completed but whose FalkorDB graph has 0 nodes triggers a rebuild (log confirms) instead of failing in PREPARE.
   - Re-running seed_actors on an already-seeded graph_id does not create duplicate nodes or duplicate identical edges (node/edge counts stable across a second seed pass).
   - pipeline_state.json after a successful build contains graph_entity_count and graph_seeded_edges fields.
   - A normal (non-resume) run is unaffected and the entity-count check adds negligible latency.
CODE_SKETCH:
# pipeline_orchestrator.py GRAPH reuse guard (~1140)
def _graph_has_entities(gid):
    try:
        info = GraphBuilderService(api_key=Config.ZEP_API_KEY)._get_graph_info(gid)
        return (info.node_count or 0) > 0
    except Exception:
        return False
if graph_stage_done and graph_id and _graph_has_entities(graph_id):
    upd(100, '复用已有知识图谱…'); ...
else:
    if graph_stage_done and graph_id:
        logger.warning('graph stage marked done but graph %s empty; rebuilding', graph_id)
    ... # full build, then record:
    state.options['graph_entity_count'] = info.node_count
    state.options['graph_seeded_edges'] = seeded

# graph_builder.seed_actors: before add_triplet, optionally check existing edge
# (add_triplet already dedups nodes; rely on it + skip exact-duplicate fact edges)


==========================================================================================
THEME: Report depth + native tool calling + multi-scenario / what-if
==========================================================================================
SUMMARY: The final ReportAgent stage is the pipeline's weakest seam: it is constructed with only (graph_id, simulation_id, simulation_requirement) at pipeline_orchestrator.py:1260-1264 and throws away the report_md, actors and sources that are already in local scope, so the predictive report must blindly re-mine the cast/relationships/timeline from the post-sim graph. It also has no tool that reads the simulation OUTCOMES (coalitions, opinion shifts, influential agents) even though SimulationRunner already exposes get_actions/get_timeline/get_agent_stats/get_run_state (simulation_runner.py:971,1005,1076,230) as structured class methods. The whole stage rides a hand-rolled prompt-ReAct loop (regex tool-call parsing at 1132-1190, conflict_retries at 1403-1436, contamination markers at 844-888, SECTION_FAILURE_PLACEHOLDER) purely because the default claude-cli provider has no native tool calling — yet DeerFlow ships ClaudeChatModel (deerflow_bridge/patches/models/claude_provider.py:49, a ChatAnthropic subclass with native bind_tools) on the same Claude Code plan. And there is no what-if / scenario / counterfactual capability at all: a run is one prompt → one simulation → one report. This plan ranks the lowest-regret, highest-fidelity path: (1) thread the dossier+brief+actors into ReportAgent as pinned context (small, immediate fidelity win, depends only on a situation_brief helper); (2) add structured simulation-outcome tools reading the existing SimulationRunner readers; (3) ground plan_outline in the dossier+a real retrieval sweep; (4) wire the dead REPORT_AGENT_* config knobs and strip dead Zep-cloud surface; (5) route ReportAgent LLM calls through ClaudeChatModel to retire the ReAct/contamination hacks; (6) add what-if scenario re-runs and (7) counterfactual A/B scenario diffing in the report. Every item builds on existing mechanisms (actor_briefing/actors_digest helpers, the tools dict + _execute_tool dispatch, the per-graph search caches, the resume/reuse machinery, the subprocess+monitor pattern) rather than replacing them, and all new context is optional-degrade so absent artifacts never fail a run.

----- [4-report] report-thread-dossier-brief-actors  (effort=M impact=high) dep=[]
TITLE: Thread the situation brief + actor roster + relationships + original dossier into ReportAgent as pinned context
PROBLEM: ReportAgent is constructed with only (graph_id, simulation_id, simulation_requirement) at backend/app/services/pipeline_orchestrator.py:1260-1264 and the same 3-arg form in backend/app/api/report.py:134-138. The orchestrator already holds report_md, actors and sources in local scope at the REPORT stage but passes none of them. ReportAgent.__init__ (report_agent.py:947-980) has no slot for them; plan_outline (report_agent.py:1202-1239) sees only get_simulation_context output and section writing (report_agent.py:1326-1348) sees only simulation_requirement + previous sections. Result: the prediction report re-derives the entire cast, stances, relationships and timeline by blind graph search instead of starting from the authoritative dossier the deep-research stage already produced. backend/app/utils/actors.py already has actor_briefing/actors_digest but NO situation_brief helper.
PROPOSAL: Add a situation_brief(actors) helper in actors.py that renders central_question + as_of_date + a compact actor table (name/type/role/stance/influence) + key_events + hot_topics (+ relationships when present). Extend ReportAgent.__init__ with optional situation_brief, actors, sources, research_report params (all default None). Inject the brief as a pinned 【背景档案（深度研究·权威）】 block into PLAN_USER_PROMPT_TEMPLATE and SECTION_SYSTEM_PROMPT_TEMPLATE, and append a compact sources index so claims can cite [S1]/[S2]. At pipeline_orchestrator.py:1260 pass situation_brief=situation_brief(actors), actors=actors, sources=sources, research_report=report_md. For manual mode (api/report.py:134) load handoff/actors.json+sources.json+research_report.md from the project dir if present. Keep every field optional-degrade (mirror the existing actors.py fail-soft pattern) so a missing dossier never fails the report.
FILES: ['backend/app/services/report_agent.py', 'backend/app/services/pipeline_orchestrator.py', 'backend/app/api/report.py', 'backend/app/utils/actors.py']
ACCEPTANCE:
   - situation_brief(actors) returns a non-empty zh block containing central_question and at least the top actors when actors.json present; returns '' on None/empty without raising
   - A full-pipeline run logs that the brief/actors were threaded into ReportAgent (e.g. an INFO line 'report grounded in 研究档案: N actors, M sources') and the generated section_NN.md references researched actor names that appear in actors.json
   - With actors.json absent (timeout-salvage path) the report still generates with no exception and a log line noting degraded grounding
   - outline.json section titles reflect the researched central_question rather than only the raw prompt
CODE_SKETCH:
# actors.py
def situation_brief(actors: Optional[dict]) -> str:
    if not actors: return ''
    cq = actors.get('central_question',''); asof = actors.get('as_of_date','')
    rows = extract_actor_rows(actors)[:20]
    lines = [f"## 研究态势简报（权威，as-of {asof}）", f"核心问题: {cq}", "### 主要行为方"]
    for a in rows:
        lines.append(f"- {a.get('name')}（{a.get('type')}）角色:{a.get('role')} 立场:{a.get('stance')} 影响力:{a.get('influence')}")
    rels = actors.get('relationships') or []
    if rels:
        lines.append('### 关系'); lines += [f"- {r.get('source')} --[{r.get('type')}]--> {r.get('target')}" for r in rels[:20]]
    ke = actors.get('key_events') or []
    if ke: lines.append('### 关键事件'); lines += [f"- {e.get('date')}: {e.get('event')}" for e in ke[:15]]
    ht = actors.get('hot_topics') or []
    if ht: lines.append('热点: ' + '、'.join(ht[:12]))
    return '\n'.join(lines)

# report_agent.py __init__
def __init__(self, graph_id, simulation_id, simulation_requirement, llm_client=None, zep_tools=None,
             situation_brief: str = '', actors: Optional[dict] = None, sources: Optional[list] = None,
             research_report: str = ''):
    ...
    self.situation_brief = situation_brief or ''
    self.actors = actors; self.sources = sources or []
    self.research_report = research_report or ''
# in plan_outline/_generate_section_react prepend self.situation_brief (+ sources index) to the system prompt

# pipeline_orchestrator.py:1260
from app.utils.actors import situation_brief
agent = ReportAgent(graph_id=graph_id, simulation_id=sim_state.simulation_id,
                    simulation_requirement=state.prompt,
                    situation_brief=situation_brief(actors), actors=actors,
                    sources=research.get('sources'), research_report=report_md)

----- [4-report] report-simulation-outcome-tools  (effort=M impact=high) dep=[]
TITLE: Add structured simulation-outcome tools that read OASIS results directly (activity ranking, opinion-shift timeline, coalitions)
PROBLEM: Simulation outcomes are reachable to the report only as undifferentiated per-action sentence facts (ZepGraphMemoryUpdater turns each action into one Chinese sentence at zep_graph_memory_updater.py:34-196 — and that feedback loop is OFF in the pipeline anyway). The report's 4 tools (insight_forge/panorama_search/quick_search/interview_agents, report_agent.py:982-1017) all bottom out on fuzzy graph search, so quantitative findings (who was most active/influential, what action volume happened per round, who clustered with whom) can only be guessed at. Yet SimulationRunner ALREADY exposes structured class-method readers returning dicts: get_agent_stats (simulation_runner.py:1076), get_timeline (1005), get_actions (971), get_run_state (230). None of these is surfaced as a report tool.
PROPOSAL: Add ZepToolsService.simulation_outcomes(simulation_id) that wraps SimulationRunner.get_agent_stats + get_timeline + get_actions into a structured digest: top-N agents by total_actions (influence ranking), action_volume_by_round (cascade shape), top action_types, and most-active/most-followed actors. Add a coalition_map(graph_id, simulation_id) that derives camps from follow/repost/like edges in get_actions (group agents by shared targets) — a deterministic clustering fallback that does not require Graphiti build_communities. Add opinion_shift(simulation_id, actor_name) reading get_timeline + per-agent action stance trajectory. Register all three as new tools in report_agent._define_tools (982-1017) and dispatch in _execute_tool (1019-1124), and add them to BOTH VALID_TOOL_NAMES (1130) and all_tools (1363). Update the tool-mix hints in the section prompt so the agent is told to call simulation_outcomes at least once for any quantitative claim.
FILES: ['backend/app/services/zep_tools.py', 'backend/app/services/report_agent.py', 'backend/app/services/simulation_runner.py']
ACCEPTANCE:
   - zep_tools.simulation_outcomes(sim_id) returns a dict/text with top_agents (ranked by total_actions), action_volume_by_round (list len == rounds executed), and action_type_breakdown, matching the same numbers as SimulationRunner.get_timeline for that sim
   - A report run's agent_log.jsonl shows at least one simulation_outcomes tool call and the resulting section cites concrete numbers (e.g. 'Agent X posted N times across R rounds') that match get_agent_stats
   - coalition_map returns >=1 cluster for a sim with follow/repost actions and an empty-but-valid structure when no relational actions occurred (no exception)
   - All three tool names pass _is_valid_tool_call (report_agent.py:1183) and are listed in both VALID_TOOL_NAMES and all_tools
CODE_SKETCH:
# zep_tools.py
def simulation_outcomes(self, simulation_id: str, top_n: int = 15) -> 'ToolResult':
    from app.services.simulation_runner import SimulationRunner
    stats = SimulationRunner.get_agent_stats(simulation_id)
    timeline = SimulationRunner.get_timeline(simulation_id)
    stats_sorted = sorted(stats, key=lambda s: s['total_actions'], reverse=True)[:top_n]
    digest = {
      'top_agents': [{'name': s['agent_name'], 'actions': s['total_actions'], 'types': s['action_types']} for s in stats_sorted],
      'action_volume_by_round': [{'round': r['round_num'], 'total': r['total_actions'], 'active': r['active_agents_count']} for r in timeline],
      'rounds_executed': len(timeline),
    }
    return ToolResult.from_dict(digest)  # to_text() renders a ranked table

def coalition_map(self, graph_id, simulation_id, max_clusters=6):
    actions = SimulationRunner.get_actions(simulation_id, limit=10000)
    # group agents that follow/repost/like the same targets -> union-find or shared-target Jaccard
    ...  # returns [{cluster_id, members:[names], shared_focus}]

# report_agent.py _define_tools add:
'simulation_outcomes': {'name':'simulation_outcomes','description':TOOL_DESC_SIM_OUTCOMES,'parameters':{'top_n':'返回前N个最活跃Agent（默认15）'}},
'coalition_map': {'name':'coalition_map','description':TOOL_DESC_COALITIONS,'parameters':{}}
# _execute_tool add branches calling self.zep_tools.simulation_outcomes(self.simulation_id) / coalition_map(self.graph_id, self.simulation_id)
# add 'simulation_outcomes','coalition_map' to VALID_TOOL_NAMES (1130) and all_tools (1363)

----- [4-report] report-ground-outline-in-dossier  (effort=S impact=medium) dep=['report-thread-dossier-brief-actors', 'report-simulation-outcome-tools']
TITLE: Ground plan_outline in the situation brief + a real retrieval/outcome sweep instead of 10 facts
PROBLEM: plan_outline (report_agent.py:1202-1239) designs ALL sections from a single get_simulation_context call (zep_tools.py:934-984) plus graph stats and only the first 10 related_facts (sliced at report_agent.py:1238). It runs BEFORE any deep retrieval, so the outline cannot reflect the actual emergent dynamics, the researched central question, or simulation outcomes — the report's whole structure is decided blind.
PROPOSAL: Build on report-thread-dossier-brief-actors and report-simulation-outcome-tools: in plan_outline, prepend the situation_brief to PLAN_USER_PROMPT_TEMPLATE, run one insight_forge(query=central_question or simulation_requirement) and one simulation_outcomes(simulation_id) sweep, and feed a digest of both into the planning prompt. Raise the related_facts slice above 10 (e.g. 25). This makes section design evidence-grounded (real factions, real cascades, real central question) rather than a generic 3-section fallback shape.
FILES: ['backend/app/services/report_agent.py']
ACCEPTANCE:
   - plan_outline logs that it ran an insight_forge sweep and a simulation_outcomes sweep before emitting the outline
   - The produced outline.json sections vary with the simulation result (e.g. a high-cascade run yields a 'cascade/虚假信息扩散' section that a quiet run does not) — verifiable by running two different prompts and diffing outline titles
   - PLAN_USER_PROMPT_TEMPLATE receives related_facts_json with >10 entries and the situation_brief text
   - No regression: when actors.json and sim outcomes are absent, plan_outline still produces a valid >=2-section outline via the existing fallback
CODE_SKETCH:
# report_agent.py plan_outline, after get_simulation_context
brief = self.situation_brief
outcomes = self.zep_tools.simulation_outcomes(self.simulation_id).to_text() if self.simulation_id else ''
seed = self.zep_tools.insight_forge(self.graph_id, query=(self.actors or {}).get('central_question') or self.simulation_requirement, simulation_requirement=self.simulation_requirement).to_text()
user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
    simulation_requirement=self.simulation_requirement,
    situation_brief=brief, outcome_digest=outcomes, retrieval_digest=seed,
    total_nodes=..., total_edges=..., entity_types=...,
    related_facts_json=json.dumps(context.get('related_facts', [])[:25], ensure_ascii=False, indent=2))
# extend PLAN_USER_PROMPT_TEMPLATE with {situation_brief}/{outcome_digest}/{retrieval_digest} slots

----- [4-report] report-wire-config-knobs-strip-zep  (effort=S impact=medium) dep=[]
TITLE: Wire the dead REPORT_AGENT_* config knobs and strip the dead Zep-cloud surface from ZepToolsService
PROBLEM: config.py:307-309 advertises REPORT_AGENT_MAX_TOOL_CALLS=5, REPORT_AGENT_MAX_REFLECTION_ROUNDS=2, REPORT_AGENT_TEMPERATURE=0.5 but ReportAgent NEVER reads them — it hardcodes 8/3/2 (report_agent.py:939-945), max_iterations=10/min_tool_calls=4 (1357-1358) and inline temperatures 0.3-0.5. Operators cannot tune report cost/depth without code edits (a silent misconfiguration trap). Separately ZepToolsService.__init__ (zep_tools.py:425-428) raises ValueError('ZEP_API_KEY 未配置') unless the sentinel 'local-graphiti' is set (config.py:277), _call_with_retry carries dead Zep-429 backoff (444-475), and search_graph passes reranker='cross_encoder' (509) which the local shim ignores (defaults to RRF) — dead/misleading complexity that obscures real local failure modes and is a footgun if ZEP_API_KEY='' is set explicitly.
PROPOSAL: In ReportAgent.__init__ read self.MAX_TOOL_CALLS_PER_SECTION/MAX_TOOL_CALLS_PER_CHAT/section temperature from Config.REPORT_AGENT_*; raise the config defaults to match the current 8 so behavior is unchanged by default; add Config.REPORT_AGENT_MIN_TOOL_CALLS for line 1358. Replace the literals at 939-945,1248,1382. In zep_tools.py: drop the ValueError key guard (425-428), replace _call_with_retry's is_zep_rate_limit branch with a simple bounded retry on transient local errors, and remove reranker='cross_encoder' from search_graph (509) or make it honor Config.GRAPHITI_RERANKER. This is a low-effort robustness/clarity win and removes the explicit-empty-key footgun.
FILES: ['backend/app/services/report_agent.py', 'backend/app/config.py', 'backend/app/services/zep_tools.py']
ACCEPTANCE:
   - Setting REPORT_AGENT_MAX_TOOL_CALLS=3 in .env and running a report observably caps tool calls per section at 3 (visible in agent_log.jsonl)
   - ZepToolsService() constructs successfully with ZEP_API_KEY unset AND with ZEP_API_KEY='' (no ValueError)
   - search_graph no longer passes reranker='cross_encoder' unless Config.GRAPHITI_RERANKER=='bge'; a search still returns results against the local graph
   - No behavior change with default config (defaults raised to current hardcoded 8/4)
CODE_SKETCH:
# config.py
REPORT_AGENT_MAX_TOOL_CALLS = int(os.getenv('REPORT_AGENT_MAX_TOOL_CALLS', '8'))
REPORT_AGENT_MIN_TOOL_CALLS = int(os.getenv('REPORT_AGENT_MIN_TOOL_CALLS', '4'))
REPORT_AGENT_MAX_TOOL_CALLS_CHAT = int(os.getenv('REPORT_AGENT_MAX_TOOL_CALLS_CHAT', '2'))
REPORT_AGENT_TEMPERATURE = float(os.getenv('REPORT_AGENT_TEMPERATURE', '0.4'))
# report_agent.__init__
self.MAX_TOOL_CALLS_PER_SECTION = Config.REPORT_AGENT_MAX_TOOL_CALLS
self.MIN_TOOL_CALLS = Config.REPORT_AGENT_MIN_TOOL_CALLS
self.MAX_TOOL_CALLS_PER_CHAT = Config.REPORT_AGENT_MAX_TOOL_CALLS_CHAT
self.section_temperature = Config.REPORT_AGENT_TEMPERATURE
# zep_tools.__init__: delete the ValueError guard; keep self.api_key = Config.ZEP_API_KEY for compat
# search_graph: reranker = 'cross_encoder' if Config.GRAPHITI_RERANKER=='bge' else 'rrf'

----- [4-report] report-native-tool-calling-claudechatmodel  (effort=L impact=high) dep=['report-thread-dossier-brief-actors', 'report-simulation-outcome-tools']
TITLE: Route ReportAgent LLM calls through DeerFlow ClaudeChatModel native tool calling to retire the ReAct/contamination hacks
PROBLEM: ReportAgent is a hand-rolled prompt-ReAct loop ONLY because the default claude-cli provider has no native tool calling: tool calls are parsed from free text by regex/JSON heuristics in _parse_tool_calls (report_agent.py:1132-1177), tool+FinalAnswer conflicts are retried (conflict_retries, 1403-1436), and _looks_contaminated (879-888) scans for leaked Claude-Code system-prompt fragments because claude-cli is being run as the model (CONTAMINATION_MARKERS at 844-861, SECTION_FAILURE_PLACEHOLDER fallback). All of this is brittle (an English denylist that a model swap bypasses; bare-JSON fallback can misparse a section body starting with '{'). DeerFlow already ships ClaudeChatModel at deerflow_bridge/patches/models/claude_provider.py:49 — a ChatAnthropic subclass on the same Claude Code OAuth plan that has REAL native tool calling via inherited bind_tools().
PROPOSAL: Add an LLMClient capability (or a thin adapter) that, when a tools-capable backend is available, exposes a bind_tools()-style structured tool-calling path backed by ClaudeChatModel. Refactor _generate_section_react into a tool-calling loop: pass self.tools as JSON tool schemas, receive structured tool_calls, execute via the existing _execute_tool dispatch, feed results back as tool-role messages. Keep the existing prompt-ReAct path verbatim as a fallback for pure-CLI providers (gate on capability so claude-cli/codex-cli without the ChatAnthropic path are unaffected). Once the native path is the default for the claude backend, delete conflict_retries and shrink CONTAMINATION_MARKERS to just the interview-timeout strings. This is the deepest change so it is gated behind the fidelity wins above; do it last to avoid destabilizing the report stage prematurely.
FILES: ['backend/app/utils/llm_client.py', 'backend/app/services/report_agent.py', 'deerflow_bridge/patches/models/claude_provider.py']
ACCEPTANCE:
   - With the native path enabled, a section that previously triggered conflict_retries or contamination now completes via a structured tool_call with zero regex parsing (verifiable: agent_log.jsonl tool calls carry structured args, not regex-extracted JSON)
   - _looks_contaminated no longer fires on the native path for normal sections (no SECTION_FAILURE_PLACEHOLDER unless a tool genuinely times out)
   - Pure-CLI providers (codex-cli, or claude-cli with the native path disabled) still produce reports via the unchanged prompt-ReAct fallback
   - A full report run on the claude backend produces section_NN.md with the same or better word counts and no <tool_call> residue in the final text
CODE_SKETCH:
# llm_client.py: add
def supports_native_tools(self) -> bool:
    return self.provider == 'claude-cli'  # via ClaudeChatModel on the Claude Code plan
def chat_with_tools(self, messages, tools_schema, temperature) -> ToolCallResponse:
    from deerflow.models.claude_provider import ClaudeChatModel  # overlay import path
    model = ClaudeChatModel(...).bind_tools(tools_schema)
    return model.invoke(messages)  # returns AIMessage with .tool_calls
# report_agent._generate_section_react: branch at top
if self.llm.supports_native_tools():
    return self._generate_section_native(section, outline, previous_sections, ...)
# _generate_section_native: loop up to MAX_TOOL_CALLS_PER_SECTION calling chat_with_tools,
#   for each ai.tool_calls -> self._execute_tool(tc['name'], tc['args']) -> append tool-role msg,
#   stop when the model returns content with no tool_calls. No regex, no conflict/contamination handling.

----- [4-report] scenario-whatif-reruns  (effort=L impact=high) dep=['report-thread-dossier-brief-actors']
TITLE: What-if scenario re-runs: re-execute the simulation+report with scenario variables without re-running research/graph
PROBLEM: A pipeline run is strictly one prompt → one research dossier → one graph → one simulation → one report. There is no way to ask 'what if the regulator intervenes early' or 're-run with 2x influence on the media actors' or 'longer horizon' without paying for a full new pipeline (including the expensive research + graph build). The orchestrator already has stage-aware RESUME with per-stage reuse guards (pipeline_orchestrator.py:937-995, 1138-1206) that reuse the existing graph_id/ontology, and prepare_simulation already threads actors into config generation — the machinery to fork at the PREPARE stage exists but is not exposed as a scenario fork.
PROPOSAL: Add a scenario fork that reuses the completed RESEARCH+ONTOLOGY+GRAPH stages of a base pipeline and re-runs only PREPARE→RUN→REPORT with a scenario overlay. Define a scenario_overlay JSON {label, max_rounds, influence_overrides:{actor_name:weight}, stance_overrides:{actor_name:stance}, injected_events:[{round,poster_name,content}], as_of_shift}. Add POST /api/research/<id>/scenario that clones the base PipelineState (new pipeline_id, copies project_id/graph_id, resets prepare/run/report to pending, stores options.scenario_overlay). _run honors the overlay: SimulationConfigGenerator applies influence/stance overrides (it already name-matches actors at simulation_config_generator.py:882) and seeds injected_events as scheduled mid-round ManualActions; ReportAgent receives the overlay label so the report opens with the scenario framing. Reuse the existing reuse-guard pattern so graph build is skipped. Falls back to a clean full run if no base pipeline.
FILES: ['backend/app/services/pipeline_orchestrator.py', 'backend/app/api/research.py', 'backend/app/services/simulation_config_generator.py', 'backend/app/services/report_agent.py']
ACCEPTANCE:
   - POST /api/research/<base_id>/scenario with {label:'regulator-intervenes', max_rounds:60} creates a new pipeline that reuses base graph_id (graph stage logged as 'reused') and re-runs prepare/run/report only — wall-clock excludes research+graph build
   - An influence_overrides:{'教育部':3.0} overlay produces a simulation_config.json where that actor's influence_weight==3.0 regardless of LLM path (deterministic override applied)
   - injected_events appear as mid-simulation posts at the specified rounds in actions.jsonl
   - The scenario report's opening section names the scenario label and contrasts with the base assumption
   - Omitting an overlay field leaves base behavior unchanged
CODE_SKETCH:
# PipelineState.options gains scenario_overlay; base_pipeline_id
# api/research.py
@bp.post('/<pid>/scenario')
def fork_scenario(pid):
    base = PipelineManager.load(pid); overlay = request.json.get('overlay', {})
    new = orchestrator.fork(base, overlay)  # clones state, reuses project_id/graph_id, resets prepare/run/report
    return {'pipeline_id': new.pipeline_id, 'base': pid}
# orchestrator.fork: copy state, new pipeline_id/task_id, graph_stage marked done (reuse guard at 1138 honors it),
#   stash overlay in options; _run reads options.scenario_overlay and threads it to prepare_simulation
# simulation_config_generator: after match_actor, if overlay influence/stance override present -> force it on BOTH paths
#   (closes the existing LLM-success bypass at 958); push injected_events into EventConfig.scheduled_events
# report_agent: prepend f'【情景假设】{overlay.label}' to the section system prompt

----- [4-report] scenario-counterfactual-diff-report  (effort=L impact=medium) dep=['report-simulation-outcome-tools', 'scenario-whatif-reruns']
TITLE: Counterfactual A/B scenario diffing in the report (compare base vs what-if outcomes)
PROBLEM: Even once what-if re-runs exist (scenario-whatif-reruns), each produces an isolated report with no comparison to the base. A forecasting product's most decision-useful output is the DELTA: 'under the regulator-intervenes scenario, the negative-sentiment cascade peaks 30% lower and resolves 12 rounds earlier'. There is no tool or report path that reads two simulations' outcomes and contrasts them; ReportAgent only ever sees one simulation_id.
PROPOSAL: Build on report-simulation-outcome-tools and scenario-whatif-reruns. Add ZepToolsService.scenario_diff(base_sim_id, scenario_sim_id) that computes deltas from SimulationRunner.get_agent_stats/get_timeline for both sims: action-volume-by-round delta, top-actor activity delta, coalition-membership churn, and final-stance shift per matched actor. Add a counterfactual report mode: when a pipeline carries options.base_pipeline_id, ReportAgent gets both simulation_ids and a scenario_diff tool, and plan_outline adds a dedicated '反事实对比 / 情景差异' section. Register scenario_diff in _define_tools/_execute_tool/VALID_TOOL_NAMES/all_tools exactly like simulation_outcomes. The diff is deterministic (reads structured action logs), so the report's comparative claims are auditable rather than hallucinated.
FILES: ['backend/app/services/zep_tools.py', 'backend/app/services/report_agent.py', 'backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - scenario_diff(base_sim, scen_sim) returns a dict with per-round action-volume delta and per-actor activity delta whose numbers reconcile to each sim's get_timeline/get_agent_stats
   - A scenario pipeline (with base_pipeline_id set) produces a report containing a '情景对比' section that cites concrete base-vs-scenario numbers (e.g. peak round, total actions) matching scenario_diff output
   - scenario_diff is callable as a report tool (passes _is_valid_tool_call) and listed in VALID_TOOL_NAMES + all_tools
   - When base_pipeline_id is absent the report omits the comparison section with no error
CODE_SKETCH:
# zep_tools.py
def scenario_diff(self, base_sim_id, scenario_sim_id):
    from app.services.simulation_runner import SimulationRunner
    b = SimulationRunner.get_timeline(base_sim_id); s = SimulationRunner.get_timeline(scenario_sim_id)
    bs = {a['agent_name']: a['total_actions'] for a in SimulationRunner.get_agent_stats(base_sim_id)}
    ss = {a['agent_name']: a['total_actions'] for a in SimulationRunner.get_agent_stats(scenario_sim_id)}
    names = set(bs)|set(ss)
    return ToolResult.from_dict({
      'volume_delta_by_round': _align_rounds(b, s),
      'actor_activity_delta': sorted(({'name':n,'base':bs.get(n,0),'scenario':ss.get(n,0),'delta':ss.get(n,0)-bs.get(n,0)} for n in names), key=lambda x: abs(x['delta']), reverse=True)[:20],
      'peak_round_base': max(b, key=lambda r:r['total_actions'])['round_num'] if b else None,
      'peak_round_scenario': max(s, key=lambda r:r['total_actions'])['round_num'] if s else None})
# report_agent: if self.base_simulation_id: add scenario_diff tool + force a '情景对比' outline section
# orchestrator: when options.base_pipeline_id set, pass base_simulation_id into ReportAgent


==========================================================================================
THEME: Orchestration robustness, stage-aware resume, unified UI, observability, provider parity
==========================================================================================
SUMMARY: Stage-aware resume already exists (PipelineOrchestrator.resume, pipeline_orchestrator.py:937-995) but is shallow: it trusts coarse stage flags, has no health validation, cannot continue a completed research_only run into the full pipeline, and offers no human-in-the-loop edit-and-continue. The integration doc (DEERFLOW_INTEGRATION.md:14-15,385-388) is stale and contradicts the code. Observability is thin: PipelineState (lines 116-169) carries no artifact pointers, the dossier endpoint (research.py:199-221) drops timeline.json, and the only relational/brief data the rest of the build wants does not exist in the contract. Provider parity is incomplete (codex-cli lacks the ANTHROPIC_API_KEY-strip hygiene claude-cli has; DEERFLOW_MODEL is unvalidated at boot; depth budgets/recursion-limit knobs live outside Config). This plan ranks 14 optimizations that harden the existing subprocess+monitor+resume machinery rather than replacing it: validate-on-resume, research_only→full continue, edit-and-continue, artifact-pointer observability, per-run language/model, boot-time DeerFlow validation, codex env hygiene, cost-aware progress, situation-brief + relationship-graph surfacing in the UI, and doctor/setup coverage for the new knobs. Every item names exact files/functions verified against source and reuses proven patterns (resume reuse-guards, the dossier reader, _persist_env, preflight_pipeline, the StageTimeline component).

----- [6-infra] doc-resume-truth-reconcile  (effort=S impact=medium) dep=[]
TITLE: Reconcile the stale integration doc with the implemented resume/cancel/preflight reality
PROBLEM: DEERFLOW_INTEGRATION.md:14-15 and :385-388 state that 'stage-aware resume/continue of failed or research-only pipelines' is 'still open', but PipelineOrchestrator.resume (pipeline_orchestrator.py:937-995) plus per-stage reuse guards in _run (report-md reuse ~1063, ontology reuse ~1104, graph reuse ~1138, prepare reuse ~1173, run reuse ~1206) are fully implemented, and research.py exposes POST /<id>/resume (research.py:110-141) gated by preflight_pipeline. The doc will mislead the next engineer about what exists and where the real gaps are (validation, research_only→full continue, edit-and-continue).
PROPOSAL: Rewrite the 'Still open' lines and the §10 'Known follow-ups' to: (1) mark resume/cancel/preflight as IMPLEMENTED with file anchors; (2) re-scope the genuinely-open items to the precise gaps this plan addresses (artifact-health validation on resume, research_only→full continuation, human-in-the-loop edit-and-continue, artifact-pointer observability, timeline.json surfacing, per-run language/model, codex env hygiene, boot-time DEERFLOW_MODEL validation). Add a short 'Resume semantics' subsection documenting that resume reuses the same pipeline_id, resets only the failed stage to pending, and reuses completed-stage artifacts via the _run guards.
FILES: ['DEERFLOW_INTEGRATION.md']
ACCEPTANCE:
   - DEERFLOW_INTEGRATION.md no longer claims stage-aware resume is open; it cites pipeline_orchestrator.py:937-995 as implemented
   - A new 'Resume semantics' subsection lists the five reuse guards by stage
   - The open-items list matches the ids in this plan (resume-validate, research-only-continue, edit-and-continue)
CODE_SKETCH:
## Still open (deliberate)
- Artifact-health validation on resume (see resume-artifact-validation)
- research_only → full continuation without re-running research (see research-only-continue)
- Human-in-the-loop edit-and-continue of the dossier (see dossier-edit-and-continue)

### Resume semantics (IMPLEMENTED — pipeline_orchestrator.py:937-995)
resume() keeps the same pipeline_id, assigns a fresh task_id, resets only state.current_stage (if failed/cancelled) to pending, then re-enters _run. _run skips completed stages via reuse guards: report-md (~1063), ontology if project.ontology set (~1104), graph if completed+graph_id (~1138), prepare if sim_state exists (~1173), run if completed (~1206).

----- [6-infra] resume-artifact-validation  (effort=M impact=high) dep=[]
TITLE: Validate artifact health on resume so reuse never re-enters a broken/empty stage
PROBLEM: resume() (pipeline_orchestrator.py:937-995) resets only the failed stage and re-enters _run, whose reuse guards trust coarse stage flags. The graph reuse guard (~1138) only checks the stage flag + graph_id presence — a graph that built but yielded 0 filtered entities is reused, then PREPARE fails downstream (simulation_manager.py:304-308 per findings). Similarly a contaminated report (report_agent._looks_contaminated territory) can be reused as-is. Resume can therefore loop back into a broken state with no signal.
PROPOSAL: Add lightweight health checks to the existing reuse guards in _run. For the GRAPH guard: only reuse if ZepEntityReader reports entity_count > 0 for graph_id (reuse the reader already imported for PREPARE; wrap in try/except → treat failure as 'needs rebuild'). For PREPARE: only reuse if the simulation profiles file (twitter_profiles.csv or reddit_profiles.json) exists under the sim dir. For REPORT: only reuse if the existing report is not FAILED and full_report.md is non-empty. On any failed health check, log a resumed_stage_validation note onto state.options and fall through to regenerate that stage. Keep all checks cheap and fail-open toward regeneration (never crash resume).
FILES: ['backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - Resuming a pipeline whose graph stage completed with 0 entities rebuilds the graph instead of failing in PREPARE
   - state.options['resumed_stage_validation'] records which stages were force-regenerated
   - A pipeline whose graph has >0 entities still reuses it (no needless rebuild) — verified by log line 'graph stage reused (N entities)'
   - Resume never raises from the health checks themselves (try/except around each)
CODE_SKETCH:
# in _run, graph reuse guard (~line 1138)
graph_ok = bool(graph_stage_done and state.graph_id)
if graph_ok:
    try:
        reader = ZepEntityReader()
        n = reader.count_entities(state.graph_id)  # thin wrapper over fetch_all_nodes len
        if n <= 0:
            graph_ok = False
            state.options.setdefault('resumed_stage_validation', []).append(f'graph:rebuilt(0 entities)')
    except Exception as e:
        graph_ok = False
        state.options.setdefault('resumed_stage_validation', []).append(f'graph:rebuilt(check-failed:{e})')
if graph_ok:
    logger.info(f'[{pid}] graph stage reused ({n} entities)')
else:
    ... # run graph build

----- [6-infra] research-only-continue  (effort=M impact=high) dep=['resume-artifact-validation']
TITLE: Allow a completed research_only pipeline to continue into the full pipeline
PROBLEM: research_only mode returns right after the dossier (pipeline_orchestrator.py:1087-1098) and resume() explicitly refuses status=='completed' (lines 956-957 raise 'already completed'). DEERFLOW_INTEGRATION.md:336-345 and §6.1 call for stopping after research, reviewing, then continuing into the expensive sim — but there is no code path to promote a completed research_only run to a full run. The dossier the user paid for is a dead-end.
PROPOSAL: Add PipelineOrchestrator.continue_to_full(pipeline_id) that: (1) loads state, requires mode=='research_only' and status=='completed' and a present research_report.md; (2) flips state.mode='full', re-seeds state.stages with the full STAGE_BANDS keys (ontology/graph/prepare/run/report) as pending while keeping the completed research stage; (3) reuses the existing resume() thread-launch tail (assign fresh task_id, status='running', start _run). _run already reuses the research stage via the report-md guard (~1063) and the research_only early-return (1087-1098) is skipped because mode is now full. Expose POST /api/research/<id>/continue in research.py mirroring the resume route (preflight first). Reuse RESEARCH_ONLY_BANDS→STAGE_BANDS switch already present in resume() (lines 961-963).
FILES: ['backend/app/services/pipeline_orchestrator.py', 'backend/app/api/research.py', 'frontend/src/api/research.js']
ACCEPTANCE:
   - POST /api/research/<id>/continue on a completed research_only pipeline transitions it to full and runs ontology→report without re-running research
   - research_report.md / actors.json from the research stage are reused (no second DeerFlow subprocess launched — verified by absence of a new research_pid)
   - Calling continue on a full-mode or still-running pipeline returns 409 with a clear message
   - Frontend api/research.js gains continuePipeline(id)
CODE_SKETCH:
@classmethod
def continue_to_full(cls, pipeline_id):
    with cls._lifecycle_lock:
        data = PipelineManager.load(pipeline_id)
        if data is None: raise FileNotFoundError('管线不存在')
        if data.get('mode') != 'research_only': raise RuntimeError('仅 research_only 可继续')
        if data.get('status') != 'completed': raise RuntimeError('研究尚未完成')
        state = PipelineState.from_dict(data)
        state.mode = 'full'
        for name in STAGE_BANDS.keys():
            state.stages.setdefault(name, StageState(name=name))
        # research stage stays completed; reuse the resume() launch tail
        ... (assign task_id, status='running', start _run thread)

----- [5-ui] dossier-edit-and-continue  (effort=L impact=high) dep=['research-only-continue']
TITLE: Human-in-the-loop: edit the dossier (report + actors), persist, then continue to simulation
PROBLEM: The dossier is strictly read-only (DossierViewer.vue:21-31 renders report via v-html; no save path) and there is no PUT endpoint to overwrite handoff/research_report.md or actors.json. DEERFLOW_INTEGRATION.md:336-345 explicitly wants an Edit & Continue gate before paying for a sim, because extracted_text (seeded from research_report.md at pipeline_orchestrator.py:1112-1116) directly determines graph fidelity. Today edits are impossible, so a flawed dossier silently propagates into an expensive run.
PROPOSAL: Add PUT /api/research/<id>/dossier that accepts {report?: str, actors?: dict} and atomically overwrites handoff/research_report.md and handoff/actors.json (tmp+os.replace, mirroring PipelineManager.save). Guard: only permit edits when status is completed and mode is research_only (or failed before graph) to avoid racing a live run. Frontend: in DossierViewer add an 'Edit' toggle swapping v-html for a textarea v-model=draftReport plus editable actor rows; a 'Save & Continue' button POSTs the edits then calls continuePipeline(id) (research-only-continue). Since _run re-seeds extracted_text from the edited research_report.md on the continue path, edits genuinely change downstream fidelity.
FILES: ['backend/app/api/research.py', 'frontend/src/components/research/DossierViewer.vue', 'frontend/src/views/ResearchView.vue', 'frontend/src/api/research.js']
ACCEPTANCE:
   - PUT /api/research/<id>/dossier overwrites research_report.md and actors.json atomically and returns 200
   - Editing the report then 'Save & Continue' produces a graph whose chunks reflect the edited text (spot-check a unique edited phrase appears in a graph node summary)
   - Edit is rejected (409) while the pipeline is running
   - DossierViewer shows an Edit toggle only when status==='completed' && mode==='research_only'
CODE_SKETCH:
@research_bp.route('/<pid>/dossier', methods=['PUT'])
def put_dossier(pid):
    data = request.get_json() or {}
    handoff = PipelineManager.handoff_dir(pid)
    if 'report' in data:
        _atomic_write(os.path.join(handoff,'research_report.md'), data['report'])
    if 'actors' in data:
        _atomic_write(os.path.join(handoff,'actors.json'), json.dumps(data['actors'], ensure_ascii=False, indent=2))
    return jsonify({'success': True})
# DossierViewer.vue: <textarea v-if=editing v-model=draftReport/> ... @click=saveAndContinue

----- [6-infra] artifact-pointer-observability  (effort=M impact=medium) dep=[]
TITLE: Thread artifact pointers through PipelineState so each stage deep-links to its output
PROBLEM: PipelineState (pipeline_orchestrator.py:116-169) carries only graph_id/simulation_id/report_id — nothing for the ontology JSON, personas, initial_posts, sources, or timeline. The status poll (research.py:179-184 returns pipeline_state.json verbatim) therefore can't let the UI navigate to intermediate artifacts the pipeline already produces. StageTimeline.vue:142-211 renders passive status only. Rich intermediate richness is invisible.
PROPOSAL: Add an artifacts: dict[str,str] field to PipelineState (default empty) populated in _run as each stage completes: artifacts['ontology']=project ontology path, artifacts['personas']=sim profiles path, artifacts['initial_posts']=simulation_config.json path, artifacts['dossier']='research_report.md', artifacts['timeline']='timeline.json' (if present). Serialize it in to_dict/from_dict. Add GET /api/research/<id>/artifact/<name> in research.py returning the file (json or markdown). In StageTimeline.vue render a small 'view →' link per completed stage when state.artifacts[stageKey] exists, emitting an open-artifact event ResearchView handles by switching tabs or opening a modal.
FILES: ['backend/app/services/pipeline_orchestrator.py', 'backend/app/api/research.py', 'frontend/src/components/research/StageTimeline.vue', 'frontend/src/views/ResearchView.vue']
ACCEPTANCE:
   - pipeline_state.json gains an 'artifacts' map with paths populated as stages complete
   - GET /api/research/<id>/artifact/ontology returns the ontology JSON for a completed run
   - StageTimeline shows a 'view →' affordance only on completed stages that have an artifact
   - from_dict tolerates old pipeline_state.json without an artifacts key (defaults to {})
CODE_SKETCH:
@dataclass
class PipelineState:
    ...
    artifacts: dict[str, str] = field(default_factory=dict)
# in _run after ontology completes:
state.artifacts['ontology'] = project_ontology_path
PipelineManager.save(state)
# research.py
@research_bp.route('/<pid>/artifact/<name>')
def get_artifact(pid, name):
    rel = PipelineManager.load(pid).get('artifacts',{}).get(name)
    return send_file(...) if rel and os.path.exists(rel) else (jsonify({'success':False}),404)

----- [5-ui] dossier-timeline-surface  (effort=S impact=low) dep=[]
TITLE: Read and return timeline.json from the dossier endpoint and render it in the UI
PROBLEM: timeline.json is part of the documented handoff contract (DEERFLOW_INTEGRATION.md §3) and the task framing, but get_dossier (research.py:199-221) reads only research_report.md / actors.json / sources.json — it never reads timeline.json. DossierViewer.vue already has a timeline widget (per findings, DossierViewer.vue:63-71) but feeds it only from actors.key_events. So even when a first-class timeline artifact exists it is silently dropped at the API seam.
PROPOSAL: In get_dossier add timeline = _read('timeline.json') and return data.timeline = json.loads(timeline_raw) when present. In DossierViewer feed the existing timeline widget from dossier.timeline when non-null, falling back to actors.key_events (keep the current fallback so old dossiers still render). This is purely additive and unblocks the timeline artifact for any producer that writes it (contract-side timeline.json emission is out of scope for this theme but this makes the UI ready to consume it).
FILES: ['backend/app/api/research.py', 'frontend/src/components/research/DossierViewer.vue']
ACCEPTANCE:
   - GET /api/research/<id>/dossier returns a 'timeline' key when handoff/timeline.json exists, null otherwise
   - DossierViewer renders timeline entries from dossier.timeline when present, else from actors.key_events
   - A pipeline with no timeline.json renders exactly as today (no regression)
CODE_SKETCH:
# research.py get_dossier
timeline_raw = _read('timeline.json')
return jsonify({'success':True,'data':{... ,'timeline': _json.loads(timeline_raw) if timeline_raw else None}})
# DossierViewer.vue computed:
const timelineRows = computed(() => props.dossier?.timeline || props.dossier?.actors?.key_events || [])

----- [5-ui] situation-brief-panel-ui  (effort=M impact=high) dep=[]
TITLE: Surface a Situation Brief panel + actor Relationship view in the dossier (consume new contract fields when present)
PROBLEM: The user's headline ask is to surface a simulation-ready SITUATION BRIEF and an actor RELATIONSHIP graph. DossierViewer (DossierViewer.vue:34-114) has only report/actors/sources tabs and renders no relationships and no brief. The status contract carries neither. Even once the contract gains these fields, the frontend has nowhere to show them, so the optimization is un-surfaceable.
PROPOSAL: Make the dossier UI forward-compatible: in DossierViewer add two conditionally-rendered tabs — a 'Brief' tab rendering actors.situation_brief (object with summary/dynamics/key_tensions or a string) and a 'Relationships' tab rendering actors.relationships[] as a compact adjacency list (source --[type]--> target with a sentiment/strength chip) plus an optional small force/D3 view reusing the GraphPanel pattern. Push {key:'brief'} and {key:'relations', count: relationships.length} into the existing tabs computed (DossierViewer.vue:192-196) only when the fields are present. No router change — additive panels inside the existing Dossier tab. The dossier endpoint already returns the full actors object, so no backend change is needed beyond the contract producer (separate theme).
FILES: ['frontend/src/components/research/DossierViewer.vue', 'frontend/src/views/ResearchView.vue']
ACCEPTANCE:
   - When actors.situation_brief is present, a 'Brief' tab appears and renders it; absent → no tab (no empty state regression)
   - When actors.relationships[] is present, a 'Relationships' tab appears showing each edge as source→type→target with a sentiment/strength chip
   - Old dossiers without these fields render exactly as today
   - Tab counts reflect relationships.length
CODE_SKETCH:
// DossierViewer.vue tabs computed
if (actors.value?.situation_brief) tabs.push({key:'brief', label:'情势简报'})
if (actors.value?.relationships?.length) tabs.push({key:'relations', label:'关系', count: actors.value.relationships.length})
// relations tab
<div v-for="r in actors.relationships">{{r.source}} <span class=chip>{{r.type}}</span> {{r.target}} <span class=pill>{{r.sentiment||r.strength}}</span></div>

----- [5-ui] graph-seed-overlay-ui  (effort=M impact=medium) dep=['situation-brief-panel-ui']
TITLE: Highlight researched-seed nodes/edges vs simulation-grown nodes in the knowledge-graph view
PROBLEM: GraphPanel receives a flat {graphData} (GraphPanel.vue:242-249) with no concept of researched-seed vs extracted vs simulation-grown nodes/edges. ResearchView wires getGraphData(graphId)→GraphPanel (ResearchView.vue:127-137,378-390). The user cannot see whether the KG actually absorbed the researched cast or how it evolved during simulation — breaking auditability of the integration.
PROPOSAL: Add optional props seedActors:Array (names) to GraphPanel. In ResearchView, derive seedActors from dossier.actors.actors[].name and pass them in. In GraphPanel, when a node's normalized name (reuse the same NFKC-lowercase normalize the backend uses in actors.py) matches a seed actor, draw it with a distinct 'researched' ring and add a legend entry. Use the bi-temporal valid_at already wrapped by the shim (get_graph_data returns created_at/valid_at) to tag nodes/edges created after the simulation start as 'simulation-grown' with a third legend color. Purely client-side; no backend change.
FILES: ['frontend/src/components/GraphPanel.vue', 'frontend/src/views/ResearchView.vue']
ACCEPTANCE:
   - Nodes whose name matches a dossier actor render with a 'researched-seed' ring and appear in a legend
   - A tri-state legend (researched-seed / extracted / simulation-grown) is shown when graphData + seedActors are both present
   - GraphPanel still renders correctly when seedActors is empty (default [])
CODE_SKETCH:
// GraphPanel.vue
const props = defineProps({ graphData: Object, seedActors: { type: Array, default: () => [] } })
function norm(s){ return s.normalize('NFKC').toLowerCase().replace(/\s+/g,'') }
const seedSet = computed(()=> new Set(props.seedActors.map(norm)))
// node class: seedSet.value.has(norm(node.name)) ? 'seed' : (isGrown(node) ? 'grown' : 'extracted')
// ResearchView.vue: <GraphPanel :graphData=graphData :seedActors="dossier?.actors?.actors?.map(a=>a.name)||[]"/>

----- [5-ui] per-run-language-model-ui  (effort=S impact=low) dep=['deerflow-model-boot-validate']
TITLE: Expose per-run research language + model override in Step-0 and thread to the subprocess
PROBLEM: The Step-0 form collects only prompt/mode/depth/max_rounds (ResearchView.vue:259-276; research.js:12-18; run body parsed at research.py:45-63). DeerFlow already accepts --target-language and --model (deerflow_research.py main) and the orchestrator threads DEERFLOW_* into argv (pipeline_orchestrator.py:365-377), but there is no per-run override — power users running English topics or a specific code-plan provider must edit .env. DEERFLOW_INTEGRATION.md flags the zh-vs-en mismatch risk (§8) and ships 7 model options (§5.2).
PROPOSAL: Add a collapsible 'Advanced' section to the params row (ResearchView.vue:60-78) with a target-language select (Chinese/English/auto) and an optional research-model select (the 7 DEERFLOW_MODEL values). Pass language/model in the runPipeline body (research.js:12-18). In research.py run_pipeline, validate model against Config.SUPPORTED_DEERFLOW_MODELS (see deerflow-model-boot-validate) and language against a small allowlist, then pass them into PipelineOrchestrator.start(...). Thread them into DeerFlowResearchRunner argv as --target-language/--model overriding the Config defaults only when provided. Defaults stay env-driven so casual users see no change.
FILES: ['frontend/src/views/ResearchView.vue', 'frontend/src/api/research.js', 'backend/app/api/research.py', 'backend/app/services/pipeline_orchestrator.py']
ACCEPTANCE:
   - Submitting with language=English produces a research_report.md in English (DeerFlow argv shows --target-language English)
   - Submitting with model=deepseek runs the research stage on deepseek (preflight enforces DEEPSEEK_API_KEY present, else 400)
   - Omitting both falls back to Config.DEERFLOW_RESEARCH_LANGUAGE / DEERFLOW_MODEL (no behavior change)
   - An invalid model is rejected with 400 before any subprocess launch
CODE_SKETCH:
// research.js
export function runPipeline({prompt, mode, depth, max_rounds, language, model}) {
  return api.post('/research/run', {prompt, mode, depth, max_rounds, language, model})
}
# research.py run_pipeline
language = (data.get('language') or '').strip() or None
model = (data.get('model') or '').strip() or None
if model and model not in Config.SUPPORTED_DEERFLOW_MODELS: return 400
PipelineOrchestrator.start(..., research_language=language, research_model=model)

----- [6-infra] deerflow-model-boot-validate  (effort=S impact=medium) dep=[]
TITLE: Validate DEERFLOW_MODEL and its required key at boot in Config.validate(), not 40 minutes into a run
PROBLEM: Config.validate() (config.py:352-374) checks LLM_PROVIDER and GRAPH_BACKEND but never validates DEERFLOW_MODEL against an allowed set, nor that the selected model's key_env is present. A typo'd DEERFLOW_MODEL or a missing DEEPSEEK_API_KEY only surfaces in preflight_pipeline (pipeline_orchestrator.py:634-652) or doctor.sh. The DEERFLOW_MODEL→key_env map is duplicated in three places (config.py PROVIDER_META key_env, preflight _df_key_env, doctor.sh) and can drift.
PROPOSAL: Add to config.py a single source of truth: SUPPORTED_DEERFLOW_MODELS = ('claude','codex','minimax','deepseek','qwen','glm','kimi') and a DEERFLOW_KEY_ENV map (model→env, claude/codex→None). In Config.validate(): append an error if DEERFLOW_MODEL not in SUPPORTED_DEERFLOW_MODELS; append a warning-level note if its mapped key_env is unset (and not claude/codex). Have preflight_pipeline._df_key_env and doctor.sh derive from this map instead of re-listing it. run.py already calls Config.validate() at startup, so this fails fast at boot.
FILES: ['backend/app/config.py', 'backend/app/services/pipeline_orchestrator.py', 'scripts/doctor.sh']
ACCEPTANCE:
   - Setting DEERFLOW_MODEL=typo makes the app fail validation at startup with a clear message
   - Setting DEERFLOW_MODEL=deepseek with DEEPSEEK_API_KEY unset surfaces a warning at boot (not only at run time)
   - preflight_pipeline and doctor.sh reference Config.DEERFLOW_KEY_ENV rather than re-declaring the model→key map
   - claude/codex selection requires no key and passes validation
CODE_SKETCH:
# config.py
SUPPORTED_DEERFLOW_MODELS = ('claude','codex','minimax','deepseek','qwen','glm','kimi')
DEERFLOW_KEY_ENV = {'minimax':'MINIMAX_API_KEY','deepseek':'DEEPSEEK_API_KEY','qwen':'DASHSCOPE_API_KEY','glm':'ZHIPUAI_API_KEY','kimi':'KIMI_API_KEY'}
# in validate():
if cls.DEERFLOW_MODEL not in cls.SUPPORTED_DEERFLOW_MODELS: errors.append(f'DEERFLOW_MODEL 非法: {cls.DEERFLOW_MODEL}')
ke = cls.DEERFLOW_KEY_ENV.get(cls.DEERFLOW_MODEL)
if ke and not os.environ.get(ke): warnings_list.append(f'{ke} 未设置（DEERFLOW_MODEL={cls.DEERFLOW_MODEL} 运行时会失败）')

----- [6-infra] codex-cli-env-parity  (effort=S impact=medium) dep=[]
TITLE: Give codex-cli the same env hygiene as claude-cli (strip OPENAI_API_KEY to force plan auth)
PROBLEM: _chat_claude_cli strips ANTHROPIC_API_KEY so the CLI uses subscription OAuth instead of API billing, overridable via LLM_CLI_USE_API_KEY (llm_client.py:288-299). _chat_codex_cli (llm_client.py:360-403) passes os.environ unmodified, so a stray OPENAI_API_KEY can silently flip codex to API billing or cause auth confusion — the exact failure class the claude path was hardened against. Provider parity is incomplete and the asymmetry is invisible until billing is wrong.
PROPOSAL: Add a _codex_cli_env() symmetric to _claude_cli_env(): env=dict(os.environ); if os.environ.get('LLM_CLI_USE_API_KEY','').lower()!='true': env.pop('OPENAI_API_KEY', None). Pass env=self._codex_cli_env() in the subprocess.run at llm_client.py:371-376. Mirror the same hygiene in oasis_llm.py if the OASIS codex path inherits os.environ (per findings the OASIS CLI path wraps create_oasis_model — verify the codex branch and apply the same pop).
FILES: ['backend/app/utils/llm_client.py', 'backend/app/utils/oasis_llm.py']
ACCEPTANCE:
   - With LLM_PROVIDER=codex-cli and a stray OPENAI_API_KEY set, the codex subprocess env does not contain OPENAI_API_KEY (verified by logging env keys under debug)
   - Setting LLM_CLI_USE_API_KEY=true preserves OPENAI_API_KEY (opt-out works, matching claude behavior)
   - A codex-cli completion still succeeds via plan OAuth after the change
CODE_SKETCH:
def _codex_cli_env(self):
    env = dict(os.environ)
    if os.environ.get('LLM_CLI_USE_API_KEY','').lower() != 'true':
        env.pop('OPENAI_API_KEY', None)
    return env
# in _chat_codex_cli subprocess.run(..., env=self._codex_cli_env())

----- [6-infra] deerflow-knobs-into-config  (effort=S impact=low) dep=[]
TITLE: Promote DEERFLOW_DEEP_OPENING_RECURSION_LIMIT and the depth→timeout budgets into Config
PROBLEM: DEERFLOW_DEEP_OPENING_RECURSION_LIMIT is documented in .env.example:122 and read directly from os.environ inside the bridge (deerflow_research.py:634,716), not via Config — inconsistent with the seven other DEERFLOW_* knobs and invisible to provider_info()/doctor. The depth→timeout map {quick:900,standard:2400,deep:10800} is hardcoded inside DeerFlowResearchRunner.run (pipeline_orchestrator.py:405), so the config.py:335 DEERFLOW_RESEARCH_TIMEOUT=10800 default is dead for the common unset path (a documentation/behavior mismatch noted in findings).
PROPOSAL: Add Config.DEERFLOW_DEEP_OPENING_RECURSION_LIMIT (int, default matching .env.example) and Config.DEERFLOW_DEPTH_BUDGETS = {'quick':900,'standard':2400,'deep':10800}. Pass the recursion limit through the orchestrator subprocess env (it already builds argv/env at pipeline_orchestrator.py:365-377). Reference Config.DEERFLOW_DEPTH_BUDGETS in DeerFlowResearchRunner.run instead of the local _DEPTH_BUDGETS dict. Make DEERFLOW_RESEARCH_TIMEOUT an explicit override only (default None/unset) and document the precedence (explicit timeout > env DEERFLOW_RESEARCH_TIMEOUT > depth budget) in .env.example, removing the misleading 10800 literal default.
FILES: ['backend/app/config.py', 'backend/app/services/pipeline_orchestrator.py', '.env.example']
ACCEPTANCE:
   - Config.DEERFLOW_DEPTH_BUDGETS is the single source for depth timeouts; DeerFlowResearchRunner.run reads it (no local dict)
   - DEERFLOW_DEEP_OPENING_RECURSION_LIMIT is a Config attribute passed into the subprocess env and visible in provider_info()
   - .env.example documents the timeout precedence and no longer implies a dead 10800 default
   - Unset DEERFLOW_RESEARCH_TIMEOUT still yields quick=900/standard=2400/deep=10800
CODE_SKETCH:
# config.py
DEERFLOW_DEPTH_BUDGETS = {'quick':900,'standard':2400,'deep':10800}
DEERFLOW_DEEP_OPENING_RECURSION_LIMIT = int(os.environ.get('DEERFLOW_DEEP_OPENING_RECURSION_LIMIT','220'))
# pipeline_orchestrator DeerFlowResearchRunner.run
budget = Config.DEERFLOW_RESEARCH_TIMEOUT or Config.DEERFLOW_DEPTH_BUDGETS.get(depth, 10800)
env['DEERFLOW_DEEP_OPENING_RECURSION_LIMIT'] = str(Config.DEERFLOW_DEEP_OPENING_RECURSION_LIMIT)

----- [6-infra] cost-aware-stage-progress  (effort=S impact=low) dep=['artifact-pointer-observability']
TITLE: Make stage progress weighting cost-aware and persist a stage-completion timeline
PROBLEM: STAGE_BANDS are static (pipeline_orchestrator.py:66-73: research 30%, run 20%) regardless of real cost; a 200-chunk deep research with a 2-round sim mis-weights the global bar wildly. _global_from_stage (lines 999-1004) linearly maps within fixed bands. StageState already has started_at/finished_at (lines 99-100) but they are not used to render a real timeline, so the global progress bar and any ETA are misleading.
PROPOSAL: Keep the static STAGE_BANDS as the default but add an optional cost-aware re-weighting: once chunk_count (from the graph stage), total_rounds (from sim config), and section_count (from the report outline) are known, recompute band widths proportionally and store them on state.options['dynamic_bands']. _global_from_stage reads dynamic_bands when present, else STAGE_BANDS. Surface started_at/finished_at per stage (already persisted) to the UI so StageTimeline can render real per-stage elapsed and a rough ETA. Low-risk: dynamic re-weighting only adjusts the mapping, never the stage order or completion semantics.
FILES: ['backend/app/services/pipeline_orchestrator.py', 'frontend/src/components/research/StageTimeline.vue']
ACCEPTANCE:
   - A deep run with many chunks shows the graph stage occupying a larger share of the global bar than a shallow run
   - _global_from_stage uses state.options['dynamic_bands'] when present and falls back to STAGE_BANDS otherwise
   - StageTimeline displays per-stage elapsed from started_at/finished_at
   - Global progress remains monotonic non-decreasing across the run
CODE_SKETCH:
@staticmethod
def _global_from_stage(state, stage, local_pct):
    bands = state.options.get('dynamic_bands') or (RESEARCH_ONLY_BANDS if state.mode=='research_only' else STAGE_BANDS)
    lo, hi = bands.get(stage, (0,100)); local_pct = max(0,min(100,local_pct)); return int(lo+(hi-lo)*local_pct/100)
# after graph: recompute bands proportional to {research:1, ontology:0.3, graph:max(1,chunks/50), prepare:0.5, run:max(1,rounds/10), report:max(1,sections/4)} normalized into 0-100

----- [5-ui] preflight-endpoint  (effort=S impact=medium) dep=[]
TITLE: Add a GET /api/research/preflight endpoint so the UI can show readiness before committing a run
PROBLEM: preflight_pipeline (pipeline_orchestrator.py:580-654) runs cheap local checks (graph backend importable, LLM key/CLI present, deerflow_research.py exists, research-model key present) but is only callable inline from POST /run and POST /resume (research.py:66,117). There is no way for the UI to display readiness before a user types a prompt and commits — so the first signal of a misconfig is a failed run submission, and resume/continue knobs (research-only-continue) aren't reachable as first-class pre-checks.
PROPOSAL: Add GET /api/research/preflight?mode=full|research_only in research.py that calls preflight_pipeline(mode) and returns {ready: bool, errors: [...]}. In ResearchView, call it on mount and on mode change, and show a readiness banner (green 'ready' / red list of blockers) above the Run button, disabling Run when not ready. This reuses the exact same check the POST path runs, so there is no drift between the displayed readiness and the actual gate.
FILES: ['backend/app/api/research.py', 'frontend/src/views/ResearchView.vue', 'frontend/src/api/research.js']
ACCEPTANCE:
   - GET /api/research/preflight?mode=full returns {ready:true,errors:[]} on a correctly configured host
   - With a missing research-model key it returns ready:false with the specific blocker
   - ResearchView shows a readiness banner and disables Run when not ready
   - The errors returned match exactly what POST /run would reject with
CODE_SKETCH:
@research_bp.route('/preflight', methods=['GET'])
def preflight():
    mode = (request.args.get('mode') or 'full').lower()
    errs = preflight_pipeline(mode=mode)
    return jsonify({'success': True, 'data': {'ready': not errs, 'errors': errs}})
// research.js: export const getPreflight = (mode) => api.get('/research/preflight', {params:{mode}})
