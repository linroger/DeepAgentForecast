# Ontology, graph, and actor evidence contracts

Review date: 2026-09-19. Source: `/Users/rogerlin/Downloads/DeepResearchForecast`, HEAD `ec29ab1d4b9a74ae9f0bea1b4644e4582a3a4d6f`. Delivery worktree has the same base. This is a read-only architecture review, not an implementation or a runtime acceptance result. Only this report was written. No providers, model downloads, graph databases, services, saved runs, or test suites were invoked.

The most urgent local contract defect is an incompatible roster hash between the research producer and actor-context consumer. Other concrete defects concern destructive merge recovery, splitter progress, ontology sampling, and temporal filtering. The existing deterministic actor seed, source receipts, context seals, permission separation, retry limits, and graph readback checks are useful foundations to preserve.

## 1. Scope, ownership, and evidence limits

This reviewer owns **only this report**. The source scope is ontology generation; graph construction, storage adapters, extraction, retrieval projections, resolution and pruning; text splitting; actor dossier, context and role contracts; their immediate utilities/models and relevant tests. Orchestration, simulation execution, and report generation belong to other reviewers. A small producer-boundary read in `deerflow_bridge/deerflow_research.py` was necessary to verify what the backend actually receives. Orchestrator symbol searches were used only to locate ownership and corroborate the roster-hash boundary, not to review its state machine.

There is **no `graphiti_backend.py` in the reviewed checkout**. Its relevant implementation is the `backend/app/services/graphiti_client/` package, especially `runtime.py`. Do not plan a patch against a nonexistent monolith. `drf2/driver/manifest.py` is outside this runtime scope and was not substituted for current validation.

The parent already owns these systemic risks: presence-based baseline ontology reuse versus the separate ASTRA03a integrity work; rebuilding GRAPH followed by PREPARE reuse based on completion and simulation existence without a direct graph-ID comparison; and optional graph derivatives written only when nonempty. The parent's proposed **RX01 read-only dependency planner, RX02 input binding, RX03 owned durable invalidation/fork safety** remain the integration plan. This report adds local contracts and defects rather than duplicating those findings. No complex implementation is authorized before PLANS.md approval.

Prior memory was used as a search lead, not as evidence that a repair is present in this checkout. In particular, the recorded ASTRA full-sanitation change is absent from the inspected ontology call site. Historical test totals and performance figures are not current verification. Current defaults below come from source configuration, not older comments or the ambient environment.

### Per-file production coverage

Depth means **full detailed read** of the whole file, **selected functions** with surrounding source, or **structural only** (AST/function inventory, imports, or targeted symbol searches). A selected-functions label does not imply every line was read. Paths in tables are relative to the source root above. Line counts were obtained by reading and AST-parsing files without importing application modules.

| File | Lines | Review depth | Covered responsibility / limits |
|---|---:|---|---|
| `backend/app/services/ontology_generator.py` | 1212 | Selected functions | `generate`, template selection, sampling/prompt assembly, normalization, endpoint reconciliation; prompt constants inspected in portions. |
| `backend/app/services/text_processor.py` | 86 | Full detailed read | File extraction facade, normalization, default resolution and splitter delegation. |
| `backend/app/utils/file_parser.py` | 192 | Full detailed read | Decoding, PDF/text extraction, multi-file error behavior, chunk advancement. |
| `backend/app/services/graph_builder.py` | 2873 | Selected functions | Seed detection/support/manifest, v1 preflight and writes, strict readback, ontology conversion, ingestion accounting, raw projection; layout/GC/centrality only structural. |
| `backend/app/services/graph_pruner.py` | 428 | Full detailed read | Pure keep/delete plan, core coverage gate, bounded retry and post-deletion verification. |
| `backend/app/services/zep_entity_resolver.py` | 542 | Full detailed read | Canonical/alias matching, both clustering algorithms, seed protection, execution audit. |
| `backend/app/services/zep_entity_reader.py` | 613 | Selected functions | Classification priority, node/edge reads, entity filtering/enrichment, direct detail entry; not every retry/detail branch. |
| `backend/app/services/graphiti_client/__init__.py` | 33 | Full detailed read | Compatibility surface, import-time embedding dimension. |
| `backend/app/services/graphiti_client/client.py` | 357 | Selected functions | Wrappers, batch ingestion, ontology/search forwarding; triplet signature and forwarding only partially read. |
| `backend/app/services/graphiti_client/compat.py` | 56 | Full detailed read | Episode, endpoint and exception value contracts. |
| `backend/app/services/graphiti_client/ontology.py` | 47 | Full detailed read | Optional string fields and ignored extras. |
| `backend/app/services/graphiti_client/runtime.py` | 2387 | Selected functions | Initialization/backend selection; ontology registry; timeout cancellation; extraction/retry/fallback/concurrent replay; deterministic writes; search filters; paging and seed readback; merge recovery. Causal traversal, deletion/GC and shutdown not fully read. |
| `backend/app/services/graphiti_client/llm_adapter.py` | 275 | Full detailed read | Provider adapter, executor context, response validation, schema recovery and tier routing. |
| `backend/app/services/graphiti_client/embedder.py` | 362 | Selected functions | Normalization/cache key, disk cache, model loading, encoding and async calls; pool tuning structural. |
| `backend/app/services/graphiti_client/falkor_driver.py` | 99 | Full detailed read | Nested-value coercion at driver and session boundaries. |
| `backend/app/services/graphiti_client/cross_encoder.py` | 26 | Full detailed read | No-op scoring; runtime compensates by choosing RRF recipes. |
| `backend/app/services/actor_dossier_compactor.py` | 320 | Full detailed read | Actor ranking, relationship selection/dedup, evidence rendering, clipping and counts. |
| `backend/app/services/actor_context.py` | 1601 | Selected functions | Contract validation, report selection, structured routing, temporal/access separation, bounded projection, pack/manifest write and validation. Gap schema helpers structural. |
| `backend/app/services/actor_role_prompt.py` | 2026 | Selected functions | Sanitizer/delimiters, context identity, role contract version handling, knowledge access, many dimension projections, provenance, bounded/emergency rendering. Not every projection/prompt literal. |
| `backend/app/utils/actors.py` | 2993 | Selected functions | Normalization/matching, version and claim helpers, cast reconciliation; other roster/coverage/forecast/event helpers structurally inventoried. |
| `backend/app/utils/atomic.py` | 77 | Full detailed read | File fsync and atomic replacement; JSON serialization. |
| `backend/app/utils/zep_paging.py` | 247 | Full detailed read | Transient page retry, duplicate/cursor guards, read caps. |
| `backend/app/utils/zep_rate_limit.py` | 52 | Full detailed read | Legacy rate-limit recognition and bounded delay calculation. |
| `backend/app/models/project.py` | 309 | Selected functions | Persisted fields/defaults, serialization, metadata/text writes, source files; list/delete details not fully read. |
| `backend/app/models/task.py` | 184 | Full detailed read | Process-local task status/progress store. |
| `backend/app/api/graph.py` | 725 | Selected functions | Ontology/build symbol flow and direct build configuration→split→ontology→ingest path; other endpoints structural. |
| `backend/app/config.py` | 1601 | Structural only | Relevant default-value declarations only; no environment/secret values read. |
| `backend/app/mcp/kg_server.py` | 358 | Structural only | Graph-tool entry points, optional as-of routing. |
| `deerflow_bridge/deerflow_research.py` | 17219 | Selected functions | Exact support binding, claim identity, normalization contract output, finalization fragments; research execution and full manifest/lineage implementation belong to research reviewer. |

`oasis_profile_generator.py`, `simulation_config_generator.py`, simulation manager/runner, `zep_graph_memory_updater.py`, `zep_tools.py`, report services, `pipeline_orchestrator.py`, frontend and external Graphiti internals are **not fully reviewed here**. Profile/role sealing and feedback dead-letter tests identify downstream obligations but do not expand this review into the other owners' implementations. LLM client internals, telemetry and general security utilities are also outside this file set; adapter-level observations do not prove total provider retry or billing behavior.

## 2. Current producer/consumer chain

```text
research report + actor dossier + fetched source receipts
  → canonical actor-intelligence/v1 + final source/actor artifacts
  ├→ ontology prompt sampling → normalized ontology → dynamic Graphiti models
  ├→ deterministic v1 actor/alias/type/relationship seeds → expected seed manifest
  ├→ compact dossier / research text → sanitized chunks → LLM episode extraction
  └→ selected actor context packs → sealed context manifest → actor-role/v2

graph rows → resolution → pruning → strict seed readback
          → facade / raw graph view / entity reader / search consumers
```

The diagram shows contracts, not a claim that every coordinator branch invokes every guard. Parent-owned sequencing determines which optional operations run and when artifacts are durable.

| Boundary | Current contract and concrete anchors | Implication |
|---|---|---|
| Fetched evidence → actor claims | Producer checks source lookup, required receipt purpose/lane/thread, receipt/content hash, exact supporting quote/span; [source support](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:2864). Claim digest binds actor, dimension, text, epistemics, timing, dependencies, contradictions, qualifiers and support; [identity](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:3014). | A URL or an LLM citation is not a fetched receipt. Preserve the support record, not just its display citation. |
| Final artifacts → actor contract | Producer emits report/dossier/sources, ordered/multiset roster, claim projection and relationship hashes; [contract](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:3823). Finalizer compares dossier/extracted ordered rosters and claim projections and writes final sources before actors; [finalization](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:4235). | The producer seal is non-circular. Its actual hash algorithms must agree with consumers; OG01 currently violates that. |
| Research/requirements → ontology | `generate` accepts document strings, requirement, optional context/template/question/actors; configured auto-selection defaults true (`config.py:358`), despite older default-off comments. One `chat_json` call uses temperature 0.3 and 8192 output tokens, with a second template call only if a nondefault result has no entities; [generation](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/ontology_generator.py:539). | General forecasting and social-opinion normalization differ. Underlying LLM client retries are separate. Output schema is not an input-freshness certificate. |
| Ontology → runtime | Validator coerces collections, deduplicates types, inserts fallbacks/caps and reconciles endpoints; [normalization](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/ontology_generator.py:948). Builder creates optional-string Pydantic fields and only registers edges with endpoint pairs; [conversion](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py:1233). | Rich type-level metadata is not automatically a node attribute. Invalid/malformed types can be skipped, and an edge with no surviving endpoint pairs does not reach runtime registration. Unknown Pydantic extras are ignored (`graphiti_client/ontology.py:33,42`). |
| Canonical v1 actors → graph seed | Whole-plan preflight validates identity uniqueness, aliases, source-support shape, claim seals, endpoint identity and relationship identity before writes; [preflight](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py:1764). Claims retain epistemics/qualifiers/support in canonical JSON attributes; relationships retain source/target actor IDs, quote/span/receipt and causal attributes (`graph_builder.py:2206`). | Preserve stable UUIDs and typed provenance. These local checks consume already-admitted evidence; they do not independently fetch or prove source truth. Unversioned dossiers intentionally use a weaker compatibility path (`graph_builder.py:1375`). |
| Seed plan → stored graph → readback | Deterministic triplets bypass LLM identity rewriting and embed sanitized, delimited records; [write](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:907). Manifest binds graph ID, UUIDs, names, required labels/attributes and summary/fact digests; [manifest](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py:326). Readback rejects missing/duplicate/changed seeds and allows audited alias collapse; [readback](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graph_builder.py:1548). | Strong structural integrity exists and must survive any rearchitecture. It is separate from global artifact freshness and from physical temporal-field coverage. |
| Text → episode | `List[str]` becomes `EpisodeData(data,type,reference_time)`; [value object](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/compat.py:17). Facade names each batch from `chunk-0` and sets `source_description="mirofish-text"`; [batch](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/client.py:153). Runtime defaults absent reference time to ingestion time (`runtime.py:775`). | The interface lacks a stable document/chunk digest, original span and source receipt reference. In-text citations are not a structured ingestion receipt. |
| Graph rows → consumers | `_ZepEdge` preserves attributes, four times and episode IDs (`client.py:49`); builder raw view preserves them (`graph_builder.py:2634`). Entity-reader adjacency drops most of them (`zep_entity_reader.py:445`), while generic paging returns capped lists with warnings (`zep_paging.py:163,235`). | Consumer projections can lose evidence even when storage is correct. A list return alone cannot prove full read coverage. |
| Actor artifacts → context → roles | Context checks schema, report/roster/count/dimension bindings and selected actor equality; [validation](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_context.py:1207). Packs retain full intelligence plus bounded text, relevant sections and omission audits; [pack](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_context.py:1384). Files/manifest use exact-byte hashes and ordered selected IDs; [seal](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_context.py:1426). Role contracts reject unsupported explicit versions and distinguish canonical v1 from legacy flat fields; [role](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_role_prompt.py:937). | Three different concepts must remain distinct: full producer roster hash, selected-pack roster hash, exact persisted pack hash. Hashes with different semantics must not share an implied algorithm. |

### Actor permissions and uncertainty that must survive

At `actor_context.py:970`, literal `actor_knows=false` overrides a true flag or visibility label; only literal booleans and allowlisted visibility establish access. Analyst inference stays modeler-only. Contested/unknown evidence may be visible while retaining its uncertainty (`actor_context.py:1005`). Canonical v1 shared context excludes the legacy free-form situation brief/hot topics (`actor_context.py:837`). Role knowledge filtering repeats the explicit-access rule and suppresses legacy memory bypass (`actor_role_prompt.py:1080–1126`). Unsupported versions do not fall back to older flat behavior (`utils/actors.py:203`).

These are **local data classifications and prompt rules**, not proof that every downstream prompt preserves the distinction. The full context pack deliberately contains modeler-only audit evidence. Never feed the entire pack to an actor as a substitute for an actor-visible projection. This reviewer did not review shared-world selection or final simulation prompts; those owners must verify cross-actor duplicate/denial behavior. The role prompt's exact-byte attestation is also a downstream responsibility, not established by a locally recomputed contract hash.

## 3. Failure, retry, storage and recovery behavior

| Operation | Current behavior | Recovery/verification limit |
|---|---|---|
| File extraction | Single-file errors raise; multi-file extraction converts an error into a textual document marker (`file_parser.py:136–142`). | Failure text can enter the evidence stream unless the caller distinguishes it. PDF page/document identity is flattened to text. |
| Ontology normalization | Malformed collections/entries are normalized or dropped; named types may survive with fallback descriptions. Endpoint normalization logs drops. Builder catches many individual dynamic-model errors (`graph_builder.py:1286,1333`). | No structured emitted normalization-loss receipt. Empty endpoint pairs are dropped at registration. Presence of an ontology object is not semantic completeness. |
| Runtime ontology | `_ontologies` starts empty (`runtime.py:195`) and `set_ontology` assigns only to that dict (`runtime.py:461`). `_add_episode_once` uses `(None,None,None)` when absent (`runtime.py:764`). | Stored graph survives process lifetime independently of ontology registration. A later direct ingest must re-register or intentionally select generic extraction. |
| V1 seeding | Complete preflight precedes writes. A failed/empty required write raises `ActorGraphSeedError`, not partial success (`graph_builder.py:1529`). UUIDs are deterministic; runtime saves nodes then edge (`runtime.py:1062`). | Preflight avoids malformed-input partial writes, but DB/provider failure can still leave a partial graph. There is no multi-operation transaction here. Retry/readback must distinguish success from partial state. |
| Text extraction | Config defaults: concurrency 4 (`config.py:1185`), two complete episode attempts (`1287`), cast filtering on (`1280`). Schema re-roll and fallback share that budget; concurrent rate-limit replay uses only the remainder (`runtime.py:666,1128`). | The cap is complete extraction passes, **not two provider requests**. Adapter schema retries and external Graphiti/app-client retries remain nested. No durable per-chunk checkpoint is emitted by these methods. |
| Adapter/provider work | Dedicated LLM executor copies ContextVars; schema echoes/invalid responses receive bounded corrective retries; transient SDK errors become Graphiti rate-limit errors (`llm_adapter.py:163`). | Attribution propagation is present. A coroutine timeout cancels the async future (`runtime.py:234`), but does not by itself prove a blocking provider thread has stopped. No speed/cost claims were measured. |
| Partial ingestion | Per-episode failures are isolated; caller receives only successful UUIDs. Builder records failure/filter counts and throws only when all submitted chunks fail (`graph_builder.py:2401–2419`). | Aggregates show loss magnitude, not which original evidence failed. All-filtered input bypasses filtering; partly filtered input does not (`graph_builder.py:2312–2327`). |
| Resolution/pruning | Resolver protects canonical seeds and type nodes, executes per-merge best effort. Pruner skips low core coverage, verifies actual survivor set, retries only planned undeleted victims once (`graph_pruner.py:280–392`). | Good bounded deletion scope exists. Merge failure handling is weaker than pruning verification (OG02). Prune planning does not explicitly pin every required seed type node (OG11). |
| Context artifacts | Each pack and root manifest is written atomically, then hashed/redecoded; root manifest is last (`actor_context.py:1451–1498`). Validation checks fingerprints, identity/order, report/dossier binding and coverage. | Atomicity is per file, not a bundle transaction. Reuse must supply trusted expected hashes. Relative-path checks are lexical (`1545–1555`), not a realpath/symlink containment proof. |
| Project/task state | `Project` persists ontology, graph ID and split parameters; metadata uses atomic replacement (`project.py:168`). Extracted text uses a direct write (`279`). TaskManager stores state only in memory (`task.py:63–70`). | These models are not a restart journal or a graph-build transaction. Whole-project durable recovery belongs to RX01–RX03. |

## 4. Ranked source-proven improvements and scenario acceptance

All changes below are **proposed**, not implemented. Priority describes impact of the demonstrated source path, not incident frequency. Acceptance scenarios are planned offline tests with fake provider/driver boundaries and isolated temporary files after approval. No production graph or saved run is needed.

### OG01 — P1: align actor roster hash semantics across the real producer/consumer boundary

**Evidence.** The producer builds `actor_id_counts`, hashes canonical JSON, and writes that digest into both `actor_ids_sha256` and `actor_ids_multiset_sha256` at [producer:3752](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:3752) and [producer:3836](/Users/rogerlin/Downloads/DeepResearchForecast/deerflow_bridge/deerflow_research.py:3836). The context consumer hashes `"\n".join(sorted(actor_ids))` and compares it to the same compatibility-named field at [actor_context:1275](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_context.py:1275). For IDs `actor_alpha`, `actor_beta`, these preimages are respectively `{"actor_alpha":1,"actor_beta":1}` and `actor_alpha\nactor_beta`; the contracts disagree even when identities and all evidence are unchanged. The context fixture explicitly manufactures the older hash ([test:175](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_actor_context_runtime.py:175)), so its isolated tests do not cover the producer's current output.

**Impact and scope.** A current producer artifact reaching this validator is rejected at the roster fingerprint check. This is independent of stale PREPARE reuse and does not justify relaxing integrity checks. This review did not run an end-to-end reproduction; the mismatch is directly visible in the two algorithms.

**Small change.** Share the canonical identity-hash projection or make the consumer explicitly understand the producer's current multiset/ordered fields. Retain an explicit, distinguishable compatibility path for old v1 artifacts; never accept whichever arbitrary hash happens to match without validating the accompanying contract shape, counts and duplicate rules. Keep context-manifest selected-ID JSON hashing separate.

**Acceptance.** Generate a valid roster through the actual producer normalizer with fabricated receipt fixtures, then pass its output unchanged into `build_actor_context_pack` and `build_actor_context_artifacts`. Verify success without rewriting producer evidence; order/multiplicity/claim tampering must still fail. Add old-v1 and unknown-version fixtures. This is a local contract prerequisite for RX02, not a replacement for it.

### OG02 — P1: a merge must not delete its victim after evidence-transfer failures

**Evidence.** [runtime:2102](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:2102) catches an edge-list failure and substitutes `edges=[]`. [runtime:2163](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:2163) aborts deletion for failed seeded-edge rewiring only, not extracted/legacy edges. [runtime:2190](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:2190) logs a survivor-save failure and then calls `victim.delete`. Thus an unavailable edge list, an unsuccessful non-seed rewire, or unsuccessful attribute union can be followed by removal of the only remaining evidence attachment. Existing runtime merge coverage tests the successful transfer path ([test:375](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_zep_entity_resolver.py:375)).

**Small change.** Treat complete edge enumeration and every required transfer/save as preconditions for deletion. Return a typed incomplete merge result and preserve the victim on any failure. Record old→new edge identity for legacy edges, which currently receive random UUIDs (`runtime.py:2155`); make interrupted retry behavior explicit so an already-rewired edge is not duplicated. Preserve seeded UUIDs and the current canonical-survivor rules.

**Acceptance.** Inject, separately, edge enumeration failure, a non-seed edge save failure, a seed save failure, and survivor save failure. Assert no victim deletion; repeat after recovery and verify every original non-self-loop fact, receipt, episode and temporal field is retained exactly once. Also retain successful alias collapse and no cross-actor merge tests. This is local mutation correctness underneath RX03's ownership rules.

### OG03 — P1: enforce splitter progress and reject invalid direct-build parameters

**Evidence.** [file_parser:172](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/utils/file_parser.py:172) advances with `start = end - overlap` without validating dimensions or requiring progress. With `text="abcdefgh"`, `chunk_size=4`, `overlap=4`, the first iteration returns to `start=0` indefinitely. Sentence-boundary shortening means even `overlap < chunk_size` does not always imply progress. The direct API accepts parameters from JSON without validation ([graph API:340](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/api/graph.py:340)) and forwards them to the splitter (`393`).

**Small change.** Validate integer/non-boolean positive chunk size and bounded nonnegative overlap before changing project/task state. At the splitter boundary enforce monotonic advancement after sentence-boundary adjustment, with an explicit policy for excessive overlap. Keep all valid historical defaults/results stable. Also honor an explicitly configured zero overlap: `TextProcessor.split_text` currently resolves a configured `0` to `50` through `or 50` (`text_processor.py:48`).

**Acceptance.** Empty/short/long inputs; zero/negative/bool/string parameters; overlap equal to/larger than window; sentence boundary before overlap; multilingual punctuation; configured zero overlap. Every accepted nonempty iteration must increase start and terminate. API invalid input must return a validation error before allocating work. Existing [splitter tests](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_utils_file_parser.py:8) cover valid defaults and sizes only.

### OG04 — P1: sanitize the whole document before ontology sampling

**Evidence.** [ontology_generator:707](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/ontology_generator.py:707) sanitizes each document with `max_chars=120000`; only afterward does it sample combined head/middle/tail. The sanitizer truncates at its cap ([actor_role_prompt:253](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_role_prompt.py:253)). For one 300k-character document, evidence beyond character 120k is already gone before sampling. The recorded `original_length` is also post-truncation. [Sampling tests:125](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_ontology_actors_optimizations.py:125) test the sampler alone, not this composed path.

**Small change.** Reconcile/backport the existing ASTRA full-sanitation approach after checking its exact commit against this base; do not invent a competing sanitizer. Separate complete sanitation from bounded presentation, then sample once. Preserve finite prompt limits, delimiters, unsafe-control handling and legacy finite-cap behavior. Parent owns graph preprocessing and reuse sequencing.

**Acceptance.** Stub `chat_json` and inspect the actual `generate` request for a single long document and multiple uneven documents containing unique late actor/relationship evidence. Cover NFKC expansion, CR-only line breaks, and split instruction-like text. Verify safe tail evidence survives while prompt size stays bounded and unsafe instructions are removed. Restoring evidence can increase subsequent extraction work; this is not a speedup claim.

### OG05 — P1 for constrained retrieval: malformed temporal filters must not become unfiltered searches

**Evidence.** [runtime:1507](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:1507) returns `None` for wrong shapes, unknown-only keys, or parsing/validation errors; [runtime:1600](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:1600) catches all filter exceptions. `_search` omits `search_filter` when that happens ([runtime:1691](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/runtime.py:1691)). An invalid `as_of` therefore permits all-time retrieval at this runtime boundary. A warning does not preserve the requested temporal constraint. Caller-specific validation may prevent some cases; it does not change this API behavior.

**Small change.** Distinguish absent filter (compatible unfiltered behavior) from an invalid supplied filter (typed failure or explicit empty/error result). Validate operators/date fields without silently dropping a required constraint. The field-level `claim_valid_at` in seed attributes and physical graph `valid_at` are distinct; do not substitute one implicitly.

**Acceptance.** Fake `g.search_` must not be called for malformed date, wrong-shaped labels, or unknown-only supplied filters. Valid as-of must produce `valid_at <= cutoff` plus `(invalid_at IS NULL OR invalid_at > cutoff)`. No-filter calls retain existing behavior; timezone/date-only handling is pinned by tests.

### OG06 — P2: add an evidence-addressed ingestion outcome contract

**Evidence.** `EpisodeData` carries only text/type/reference time; [facade:156](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/graphiti_client/client.py:156) restarts chunk numbering for each batch and supplies a generic source description. Concurrent runtime keeps failed indexes only during that call and drops failures from the returned UUID list (`runtime.py:1140–1207`). Builder stores only summary counters (`graph_builder.py:2401`). Name-based cast filtering discards every zero-hit chunk if at least one chunk matches; a pronoun-only condition or timeline continuation can therefore disappear independently of extraction success.

**Small change.** Add a backward-compatible envelope/result carrying source artifact digest, stable chunk ID, preprocessing version, source span/derivation, reference time, ontology digest, seed/roster binding, attempt class and final status/episode ID. Return or expose the envelope locally; let the existing coordinator own durable registration/restart decisions. Keep cast-filter exclusions distinct from provider failures and include their IDs and reasons. Do not treat structured seed provenance as proof of later LLM extraction provenance.

**Acceptance.** Three distinguishable chunks with middle failure must preserve A/B/C→outcome mapping in serial/concurrent modes and across batch sizes. A partial retry targets only the unchanged failed content; changed text/ontology cannot inherit old success. A named paragraph followed by a pronoun-only constraint must either be admitted together or produce an explicit excluded-evidence record. This extends RX02 bindings and RX03 recovery without creating a second coordinator.

### OG07 — P2: make cold-process ontology registration explicit

**Evidence.** The runtime ontology registry is memory-only (`runtime.py:195,442–461`), and a missing entry causes generic extraction (`764–787`). A durable graph and a durable project ontology are not automatically associated when runtime reconnects. This is a concrete restart semantic, separate from whether the project ontology bytes are fresh.

**Small change.** Require an explicit verified ontology binding on typed ingestion entry, or rehydrate it through a caller-supplied loader using the RX02 identity. Preserve deliberate legacy generic extraction as an explicit compatibility mode. Avoid serializing dynamic Python classes or silently loading whichever project happens to reference the graph.

**Acceptance.** Fake cold runtime plus existing graph: a typed ingest must rehydrate the registered ontology from approved bytes or stop clearly before extraction. Wrong graph/ontology digest must stop. Legacy no-ontology ingestion remains available only through its documented path. Search-only reconnect must not regenerate ontology.

### OG08 — P2: preserve provenance and completeness through graph read projections

**Evidence.** The facade exposes temporal/episode fields, but [entity reader:274](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_entity_reader.py:274) omits them, and [adjacency enrichment:450](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/zep_entity_reader.py:450) reduces edges to direction/name/fact/neighbor UUID, losing even edge UUID and attributes. The builder raw view retains the richer form. Generic paging can stop at caps or ineffective cursors while returning an ordinary list (`zep_paging.py:156,225`).

**Small change.** Add a reusable additive edge projection with identity, support/qualifier attributes, temporal fields and episode IDs. Do not expose modeler-only evidence as actor-visible merely by preserving it. Add an explicit read-completeness receipt for capped/error-truncated reads, preserving the legacy list facade for existing callers. Strict seed readback should continue to use its uncapped, duplicate-preserving path.

**Acceptance.** Pass a contested, `actor_knows=false`, temporally expired, receipt-bound edge through facade→reader→related edge; assert all metadata survives and actor-access projection still denies it. Read more than configured caps and an ineffective-cursor fake; assert partial coverage is explicit and never confused with proof of absent evidence.

### OG09 — P2: label compact dossier output as a lossy view and make its coverage truthful

**Evidence.** Relationship compaction deduplicates on `(source,target,type)` only ([compactor:181](/Users/rogerlin/Downloads/DeepResearchForecast/backend/app/services/actor_dossier_compactor.py:181)), while strict seeding correctly distinguishes causal/claim identity (`graph_builder.py:2149`; `test_graph_actor_v1_seed.py:360`). Compaction prints legacy flat role/stance/goals even for v1 rows (`actor_dossier_compactor.py:205`); its claim renderer appends qualifiers then truncates the whole string (`utils/actors.py:334–342`). Typed evidence gaps use `reason`, but compactor dict gaps read `claim` (`actor_dossier_compactor.py:262`). Finally, `actors_rendered`/`relationships_rendered` count selected rows before the output clipping loop (`304–318`), even if their sections were never emitted.

**Small change.** Preserve compact text as a derived view of the canonical seal. Use claim/relationship IDs for v1 selection; never allow a flattened view to replace the canonical seed plane. Separate selected/emitted/omitted counts and IDs, render complete evidence-plus-qualifier units, and support the current typed-gap `reason`. Keep legacy formatting behind the legacy branch.

**Acceptance.** Two same-type relationships with opposite sign/distinct claims remain distinguishable; a long claim cannot lose its uncertainty/access marker while keeping assertive text; typed gaps render; a tiny character budget reports actually emitted actors and relationships. Existing tests cover deterministic size limits and older wrapped/string-gap shapes ([test:40](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_actor_dossier_compactor.py:40), [test:100](/Users/rogerlin/Downloads/DeepResearchForecast/backend/tests/test_actor_dossier_compactor.py:100)), not these current-contract interactions.

### OG10 — P2: extend seed readback to the physical temporal semantics it protects

**Evidence.** Seed writer sets the physical edge `valid_at` from the caller (`runtime.py:1009`; `graph_builder.py:2254`). Expected manifest rows bind names/endpoints/fact/attributes (`graph_builder.py:378`), and physical readback omits times (`runtime.py:1967`). Thus changing only physical `valid_at`/`invalid_at` is invisible to the current exact-seed comparison, even though temporal search uses physical times. The canonical claim time in attributes can remain unchanged.

**Small change.** Define which physical temporal fields are immutable seed semantics and include them in a versioned, additive manifest/readback contract. Distinguish record creation time, knowledge/reference cutoff, and claim validity. Maintain legacy manifest validation without falsely upgrading its guarantee.

**Acceptance.** Seed/readback roundtrip with explicit UTC time; mutate only `valid_at` and ensure the current contract rejects it. Pin allowed invalidation behavior rather than rejecting legitimate future updates indiscriminately. Keep absent legacy timestamps explicitly weaker.

### OG11 — P2: make prune's keep-set aware of required structural seed nodes

**Evidence.** Strict seed readback requires entity-type nodes and identity edges (`graph_builder.py:398–405,1660`). Resolver deliberately excludes/protects type nodes (`zep_entity_resolver.py:262`; `runtime.py:2074`). Pruner core protection instead matches actor names/aliases, then admits all other nodes under hop/type/cap budgets (`graph_pruner.py:125–205`). For a valid v1 graph with cap equal to actor-node count and no alias nodes remaining, its type node is a noncore candidate and can be deleted, taking required `IS_A` structure with it. This is a local helper contract incompatibility; parent must determine where its stage sequence exposes it.

**Small change.** Pass the verified required-seed set into the pure prune planner, or derive protection from validated seed metadata. Raise effective capacity for mandatory structure and report that exception; do not loosen readback to bless its destruction.

**Acceptance.** Valid v1 seed→alias resolution→prune with a tight cap/per-type cap or zero-hop setting→strict readback must retain all mandatory actor/type/relationship identities. Unexpected extra nodes remain removable. Existing low-core-coverage and immutable deletion-plan safeguards stay intact.

### Smaller, bounded follow-ups

1. **Resolver parity:** curated dossier aliases bypass a low cosine in full planning (`zep_entity_resolver.py:285`) but not the large-graph fast path (`425`). Add a below-threshold authoritative-alias fixture on both sides of `GRAPH_RESOLVE_MAX_NODES`; choose one documented policy while preserving protection of distinct canonical actors. This is separate from intentional omission of expensive containment matching in the fast path.
2. **Embedding identity:** disk key binds model name and normalized text (`embedder.py:253`), while raw encoding receives the first original whitespace variant (`327`) and model loading does not pin a revision (`241`). Dimension mismatch is checked on disk hits (`314`), so do not claim it is unchecked. Consider an explicit embedding-policy/revision fingerprint and consistent normalization, with fake-model cache tests; do not download a model for audit.
3. **Atomic source text:** `ProjectManager.save_extracted_text` still uses direct overwrite (`project.py:279`). Use existing atomic helpers and source digest binding if this direct API remains supported; metadata atomic replacement alone does not make multi-writer updates transactional.
4. **Pack path containment:** context validation rejects absolute/traversal paths but follows symlinks (`actor_context.py:1545–1555`). If the sealed-directory threat/ownership model requires physical containment, add realpath checks and a symlink fixture without changing hash semantics. This is not presented as an observed exploit.

## 5. Test inventory and review depth

No tests were run. Counts below are AST-discovered `test_*` functions, **not collected parametrized cases or passing totals**. “Structural only” means the file was inventoried, and may include test-name searches; its assertions are not claimed as verified. Referenced tests from excluded simulation/report modules remain the other reviewers' responsibility.

| File under `backend/tests/` | Lines | Test functions | Review depth | What it establishes in source / limitation |
|---|---:|---:|---|---|
| `test_actor_cast_discipline.py` | 530 | 36 | Structural only | Cast selection/eligibility suite located; runtime behavior not verified. |
| `test_actor_context_runtime.py` | 1374 | 27 | Selected functions | Old-form roster fixture `172–227`; tamper/access/coverage/role propagation scenarios located. Real producer handoff missing from inspected fixture path. |
| `test_actor_dossier_compactor.py` | 142 | 5 | Full detailed read | Cast/one-hop exclusion, deterministic truncation, structured brief and older intelligence shapes. |
| `test_actor_dossier_judge.py` | 52 | 6 | Full detailed read | Pure judge thresholds and missing-scorecard rejection; not a live judge validation. |
| `test_actor_intelligence_producer.py` | 1628 | 42 | Structural only | IDs/receipts/quote binding/finalization/ordered roster and claim-projection scenarios inventoried; implementation verified in producer source rather than all test bodies. |
| `test_actor_role_prompt.py` | 1359 | 30 | Selected functions | Literal access, denied knowledge and uncertainty tests `439–525`; role/manifest/runner test names located. |
| `test_audit_fixes_graph.py` | 379 | 19 | Structural only | Ingest counters, alias priors, projection-order/tier tests; includes feedback dead-letter tests outside this review. |
| `test_audit_fixes_ontology.py` | 213 | 22 | Structural only | Null descriptions, missing roster fallback, causal family, template routing, seed budget. |
| `test_dossier_coverage.py` | 46 | 3 | Full detailed read | Empty/rich/hollow dossier metrics; metrics are not identity/source admission. |
| `test_graph_actor_v1_seed.py` | 767 | 18 | Selected functions | Real builder capture fixtures, support attributes, distinct causal relations, fail-closed writes, exact manifests and tamper readback `318–612`; no real DB run. |
| `test_graph_cost_containment.py` | 433 | 22 | Structural only | Filtering, denominator, attempt-cap/fallback/replay, temporal slim-key cases. |
| `test_graph_optimizations.py` | 365 | 16 | Structural only | Cache, tier routing, ContextVar propagation, retry/echo, serial isolation, timeout lock release cases. |
| `test_graph_research_boundary.py` | 50 | 2 | Full detailed read | Safe evidence/delimiters/control removal through parent helper; no large Unicode-expansion evidence case here. |
| `test_ontology_actors_optimizations.py` | 250 | 20 | Selected functions | Sampler and endpoint tests `125–171`; composed long-document generation remains a gap. |
| `test_ontology_refinements.py` | 591 | 29 | Structural only | Tier/archetype, relationship polarity/follows, ontology seeds, reconciliation cases. |
| `test_pipeline_actor_contract.py` | 1382 | 37 | Structural only | Parent admission suite located; no orchestration conclusions imported from its names. |
| `test_prompt_injection_boundaries.py` | 253 | 5 | Structural only | Boundary suite inventoried; no all-prompt safety claim. |
| `test_utils_file_parser.py` | 43 | 5 | Full detailed read | Defaults, empty/short/long and valid explicit sizes; lacks bad-parameter/progress cases. |
| `test_wave9_kg.py` | 907 | 38 | Structural only | Prune postconditions/coverage/readback failure, paging/UI and bounded replay test names inspected. |
| `test_zep_entity_resolver.py` | 505 | 28 | Selected functions | Fast/full boundary scenarios and successful seeded runtime merge `345–505`; destructive failure cases not covered by that success test. |

The source inventory/AST parsing covered all **29 production/boundary files and 20 test files** listed above. It is a syntax/inventory check only. It neither imports their dependencies nor demonstrates application execution.

## 6. RX01–RX03 integration constraints and acceptance order

For **RX01**, the pure dependency planner should report a graph binding as unknown when it cannot establish ontology registration, extraction coverage, required seed identity or applicable temporal semantics. It must not equate a process-local ontology entry, a graph ID, or ordinary capped node/edge lists with a durable verified artifact. No dependency planning may start Graphiti merely to inspect it: constructing the runtime starts a background loop, and ensuring a graph can initialize storage/models (`runtime.py:187,377`).

For **RX02**, bind ontology not only to its output bytes but to the actual ordered documents/actors/requirement/question/template/prompt and normalization policy that determine it. Carry source-byte identity separately from sanitized/sampled input identity. For graph output bind ontology, selected source/chunk identities, reference cutoff, strict actor seed manifest and extraction policy. Preserve an explicit compatibility status for old artifacts with missing binding fields. Include the corrected producer/consumer roster algorithm from OG01. Do not forge stronger receipts around legacy text or derived compact dossiers.

For **RX03**, retain parent ownership/fork constraints and avoid deleting shared physical graphs. Local operation recovery must not erase evidence: OG02 is a prerequisite to treating resolution as safely repeatable; OG06 supplies fine-grained outcomes rather than a second restart authority. State invalidation cannot repair a mismatched hash formula, missing ontology registry entry, or fail-open date filter by itself.

Recommended approved slices are: (1) producer→context compatibility test and OG01; (2) non-destructive merge failure tests and OG02; (3) splitter progress/validation and OG03; (4) reconcile the existing ASTRA sanitation implementation for OG04; (5) strict supplied-filter behavior for OG05; then the additive graph evidence/readback work under RX02/RX03. Each slice needs relevant regression checks plus a composed producer→consumer scenario. Keep tests offline with fake DB/provider objects and temporary directories; do not instantiate real graph/model clients as a shortcut.

## 7. Handoff and remaining evidence

Completed: source inventory; concrete ontology/graph/actor contracts; local failure/recovery analysis; version/legacy distinctions; source-ranked improvements; test-source inventory and offline acceptance proposals. No runtime/code edits, schema migrations, git commits, pushes, source-harness changes, or saved-run mutations were made.

Remaining before implementation: parent synthesis and PLANS.md approval; confirm the roster-hash mismatch through the composed offline fixture; select compatibility policy; inspect other reviewers' conclusions for actor-visible prompts and parent freshness/reuse; decide whether direct graph API compatibility remains a supported path. Real driver transactional/rewire behavior, cancellation of already-running provider I/O, provider throughput, and model-dependent extraction quality were not tested. Do not promote historical memory results into acceptance evidence for this base.

Resume prompt: “Read ontology-graph.md at ec29ab1 and the parent's approved PLANS.md. Implement only the selected OG/RX slice. Preserve current semantic IDs, source receipts, actor permission/uncertainty fields, compatibility modes and fork ownership. Establish a failing composed offline scenario first, avoid live providers/services/saved runs, and record exact validation evidence without modifying unrelated files.”

Review record: 2026-09-19 — source review and report created; parent RX01–RX03 steering incorporated. Report verification checks and any final corrections are recorded below.

Final static verification, 2026-09-19: all 40 clickable source anchors resolve to existing files and in-range lines; all 49 inventoried Python files AST-parse and their recorded line counts match; the 20 test files contain 410 test-function definitions (not collected/passing cases). All 29 production/boundary files match `git show ec29ab1:<path>` byte for byte. The report is the only file written by this reviewer and remains untracked for parent integration. No runtime/test pass claim is made. An additional specialist delegation was attempted but the parallel study had reached its agent limit; this report therefore received local self-review, not an independent secondary-agent review.
