---
name: kg-construction
description: Use this skill in the DRF-2 pipeline whenever you build or query the temporal knowledge graph through the kg_* MCP tools (Graphiti + FalkorDB engine). It teaches WHEN and HOW to call each tool — episode chunking and batching discipline, bitemporal reference-time anchoring, entity-resolution hygiene against the canonical cast, causal-edge admission criteria, and the retrieval playbook (typed search, multi-hop causal traversal, cascade tracing, centrality).
allowed-tools:
  - tool_search
  - kg_add_episode
  - kg_search
  - kg_get_entities
  - kg_get_edges
  - kg_causal_paths
  - kg_n_hop_subgraph
  - kg_trace_cascade
  - kg_centrality_priors
  - read_file
  - write_file
  - ls
  - grep
  - glob
---

# Knowledge-Graph Construction Skill

## 1. Mission & sequencing

The KG engine (Graphiti on FalkorDB, exposed as `kg_*` MCP tools) turns the research corpus into a **typed, valenced, bitemporal graph** that later stages query for personas, simulation context, causal chains, and report evidence. The build order is fixed:

1. **Ontology first.** The ontology (from the `ontology-generation` skill, `ontology.json`) is applied to the graph by the Pipeline Driver / graph stage BEFORE any text is ingested. Confirm it is in place (e.g. `kg_get_entities` returns the expected type labels once data exists, or the driver reports the stage done) — episodes ingested before the ontology exists are extracted as untyped blobs and cannot be retroactively retyped.
2. **Ingest** the dossier + research report as chunked episodes (`kg_add_episode`, §2). Dossier first — its cast/relationship sections are the graph's spine; the research report follows as supplementary evidence.
3. **Wait for processing.** Episode extraction is asynchronous. Do not issue retrieval calls that depend on freshly-ingested content until the engine reports the episodes processed (poll the tool's status response; never assume immediacy).
4. **Verify** with `kg_get_entities` / `kg_get_edges` / `kg_search` spot checks (§4) before declaring the graph built.

## 2. Episode chunking discipline

`kg_add_episode` accepts text episodes. How you chunk decides extraction quality:

- **Chunk size ~500 characters with ~50-character overlap.** This is the proven operating point: large enough to keep a claim and its subject in one chunk, small enough that the extractor doesn't drop entities. Never dump a whole report as one episode.
- **Split on semantic boundaries** (paragraphs, list items, section breaks) — a chunk that cuts a sentence mid-claim produces phantom or orphaned entities. Respect the dossier's per-actor profile boundaries: one actor's profile should not be smeared across a chunk boundary with another actor's.
- **Batch your calls** (typically 3–10 chunks per batch) rather than one call per chunk or one call for everything; track failures per batch and re-send only the failed chunks (one retry, then log the gap).
- **Anchor reference_time.** Every episode carries a bitemporal `reference_time` — set it to the document's **as-of date** (the research run's as-of, or the dated event's date for timeline chunks), not the ingestion wall-clock. Downstream as-of queries and forward-projected edges depend on this anchor being the *content's* time.
- **Structured sections ingest better than prose.** The dossier's enumerated relationship edges (`Source —[TYPE, valence, strength]→ Target — basis`) extract nearly losslessly; keep such lines intact within a single chunk.

## 3. Entity-resolution hygiene

The graph is only as useful as its entity resolution. The canonical cast in `actors.json` is the single source of truth:

- **Use canonical names verbatim** in any text you author for ingestion (summaries, edge lists). Do not paraphrase names ("the Taiwanese foundry" for TSMC) in load-bearing sentences — extraction will mint a duplicate node.
- **Aliases**: mention each actor's aliases once, explicitly tied to the canonical name ("TSMC (Taiwan Semiconductor Manufacturing Company, 台积电, 2330.TW)"), so the resolver can merge surface forms.
- **Merge rules the engine applies** (know them so you don't fight them): a non-canonical surface form merges INTO a canonical when (a) names match exactly or by containment AND an embedding-cosine gate passes, OR (b) the surface form is listed in the canonical actor's `actors.json` aliases — this second path is an exact, authoritative signal and does NOT need to pass the cosine gate, because it comes from the dossier's own ground truth rather than a name-similarity guess. This is what lets lexically-unrelated aliases ("Beijing" / "CCP" / "MOFCOM" for one government) merge correctly even though they share no characters and would fail the containment/cosine test on their own — put every such alias in the dossier's `aliases` list (§entity-resolution above) or this path can't fire. **Two canonical actors are never merged into each other**; the canonical name always survives as the merged node's name; every merge is written to an audit log (`entity_merges.json`) for rollback.
- **After ingestion, verify resolution**: call `kg_get_entities` for the key types and check the cast appears once each. If you find duplicates (e.g. "US Federal Reserve" and "The Fed" as separate nodes), report them — do not hand-wave; duplicate principals corrupt centrality, ego-network retrieval, and persona grounding.

## 4. Causal-edge admission criteria

Causal edges (`CAUSES`, `ENABLES`, `CONSTRAINS`, `TRIGGERS`, `ACCELERATES`) are the highest-value edges in the graph — and the easiest to pollute. Admit one only when ALL hold:

1. **An evidenced mechanism exists** — the research states *how* A moves B (not mere correlation or co-mention).
2. **Direction is explicit** — you can write the one-sentence direction semantics without guessing.
3. **Chronology is respected** — cause precedes effect in the dated timeline; an edge whose direction violates the timeline is rejected.
4. **Attributes attached where known** — `sign` (does more A mean more or less B), `lag` (how long until it propagates — a causal spine needs propagation time, not just existence), `strength` (high/medium/low).

Co-occurrence, thematic similarity, or "analysts link X to Y" without a mechanism → NOT a causal edge; at most an `INFLUENCES`/`information` family edge, or nothing.

## 5. Retrieval playbook (when to use which tool)

| Tool | Use when | Craft |
|---|---|---|
| `kg_search` | Fact lookup, evidence for a claim, an actor's stance/edges | Keep queries **compact (≤350 chars)** and entity-anchored; use typed filters (edge types / entity types) when the question is about a relationship class — typed retrieval beats free text. Ask one question per call; never paste a paragraph as a query. |
| `kg_get_entities` | Enumerate the cast, check resolution, pull per-type entity lists with attributes | Filter by entity type; read `influence_tier`/`stance` attributes rather than re-deriving them. |
| `kg_get_edges` | Enumerate typed/valenced edges (with temporal validity) for verification or a relationship inventory | Spot-check that allied vs adversarial edges landed with the right type; check bitemporal validity windows on evolution facts. |
| `kg_causal_paths` | "How does a shock at A reach outcome B?" — multi-hop mechanism chains | Give source and target entities; inspect each hop's sign/lag/strength; a path with one unevidenced hop is a hypothesis, not a finding. |
| `kg_n_hop_subgraph` | Local neighborhood context for a persona or a driver (ego networks) | 1–2 hops around the focal entity; filter to relationship-class edges (ALLY_OF/OPPOSES/COMPETES_WITH/REGULATES/DEPENDS_ON/PARTNERS_WITH/INFLUENCES) when building social context, so you get the actor's *position*, not random fact edges. |
| `kg_trace_cascade` | "If X happens, what falls over downstream?" — forward propagation through causal/dependency edges | Start from the triggering event/actor; report the cascade with per-hop lag; stop at hops whose evidence grade collapses. |
| `kg_centrality_priors` | Rank actors by structural position (centrality priors for agent selection / report weighting) | Treat centrality as ONE salience signal — a well-covered amplifier can be central without decision power; always cross-check against the dossier's salience tiers. |

**Retrieval discipline:** never re-issue a near-identical query; change the entity, edge-type filter, or hop depth instead. Cache what you learned (the graph does not change under you mid-turn). When a search returns nothing, check entity naming first (§3) — the most common cause is a name mismatch, not missing data.

## 6. Stage artifact

When this skill runs as a pipeline stage, finish by writing **`kg_build_summary.json`** to the stage outputs: episodes submitted/processed/failed, entity counts per type, the cast-resolution verification result (duplicates found, if any), and the retrieval spot-check outcomes. The Pipeline Driver gates the stage on this artifact — an honest summary of a partial build beats a silent success claim.

## 7. Quality gate (before declaring the graph built)

- [ ] Ontology applied before ingestion; entity/edge types match the generated ontology?
- [ ] All chunks ingested (failed batches retried once and accounted for)?
- [ ] Episodes processed (engine-confirmed), not just submitted?
- [ ] Cast resolution verified via `kg_get_entities` — every high-salience actor present exactly once, typed correctly, cited outlets NOT instantiated as agentic actors?
- [ ] Relationship spot-check via `kg_search`: at least one allied edge and one adversarial edge retrievable with correct valence?
- [ ] Causal spine spot-check: `kg_causal_paths` returns at least one mechanism chain relevant to the central question (when the domain has one)?
- [ ] reference_time anchors reflect content dates, not ingestion time?

## 8. Failure modes

- ❌ Ingesting text before the ontology is applied — permanent untyped extraction.
- ❌ One giant episode, or chunks that cut claims mid-sentence.
- ❌ Ingestion-time `reference_time` on historical content — corrupts as-of queries and the timeline.
- ❌ Paraphrased actor names in authored text — duplicate nodes, broken ego networks.
- ❌ Causal edges from co-occurrence or vibes; causal direction contradicting the dated timeline.
- ❌ Querying freshly-added episodes before processing completes, then concluding "data missing".
- ❌ Paragraph-length search queries; near-duplicate retries instead of changing entity/filter/hop-depth.
- ❌ Treating centrality as ground-truth salience without the dossier cross-check.
