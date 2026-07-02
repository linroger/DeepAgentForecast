# OPTIMIZATIONS — DeepResearchForecast

A grounded, ranked catalog of latency/cost and quality/accuracy optimizations for the forecasting pipeline (research → ontology → Graphiti+FalkorDB KG → OASIS multi-agent sim → ReportAgent). Every entry cites `file:line` evidence and prefers tuning the existing config surface in `backend/app/config.py` over new machinery. Recommendations are calibrated to this run's measured regime: a **local OpenAI-compatible proxy** (vibeproxy → Antigravity/Google `gemini-3.5-flash-low`, a reasoning model even at "low") where **per-call latency, not rate limiting, is the cost** — there were **zero 429/quota/timeout/401 errors in ~5h of logs**, only 2 benign `chat_json parse failed` warnings.

## Executive summary

**Diagnosis.** Measured stage timings this run: research ~17 min, ontology ~1 min, **graph build ~5h and still running** (started 08:01Z, batch 29/40 by 12:46Z), with simulation and report still pending. The graph build is the overwhelming sink, and the cause is structural, not throttling:

1. **Too many episodes.** `DEFAULT_CHUNK_SIZE=500` *characters* (`config.py:496`) slices the dual-track corpus into ~400 micro-episodes (40 batches × 10). Episode count scales as `corpus_len / 500`.
2. **Strict serialization.** `GRAPH_BUILD_CONCURRENCY=1` (`client.py:166-183`) ingests those ~400 episodes one at a time, each a full entity+relationship extraction round-trip.
3. **Every extraction runs on the STRONG reasoning model.** `llm_adapter.py:_generate_response` ignores graphiti's `model_size` hint and always calls `chat_json` at the default `tier='strong'`, so `LLM_TIERED_ROUTING` is **completely inert** for the 5h stage — the per-call latency multiplier is never attacked.

It is **not quota-bound** — the absence of 429s over 5h is evidence the provider *tolerates the serial load*, not that it tolerates high concurrency (the latter is **untested** because the graph ran at concurrency=1; see `CORRECTION-2`).

**Highest-leverage levers (impact per effort), in order:**
1. **`GAP-1` — route mechanical extraction to a real fast tier.** Map graphiti `ModelSize.small → tier='fast'` in `llm_adapter.py` and enable `LLM_TIERED_ROUTING=true` with a genuine non-reasoning `LLM_FAST_MODEL`. This cuts per-call latency, the one multiplier the other graph fixes only *count*-reduce or *parallelize*.
2. **Chunk size `500 → 2500` chars** (`DEFAULT_CHUNK_SIZE`, overlap `→ 250`). One edit that cuts episode count ~5x **and** improves relationship recall. This single change subsumes the duplicate findings `GRAPH-2`, `RESEARCH-1`, `ONTO-1/2`, `CHUNK-1` (`CORRECTION-1`).
3. **`GRAPH_BUILD_CONCURRENCY 1 → 4`**, paired with **`GRAPH_RESOLVE_ENTITIES=true`** as the dedup safety net.
4. **`GRAPHITI_MAX_COROUTINES 8 → 16`** to widen intra-episode fan-out.
5. **Report bundle:** `REPORT_SECTION_CONCURRENCY>1` + `REPORT_NATIVE_TOOLS=true` + `REPORT_SIGNAL_PACK=true`.

**Caveat on all parallelism wins (`CORRECTION-2`, `CORRECTION-3`):** the concurrency speedups are hypotheses to **ramp into and measure**, not guarantees — they share a single provider throughput ceiling and a single-process SentenceTransformer CPU. Stacked multipliers are **sub-multiplicative**: realistic aggregate is **~5h → ~30-60 min**, not the naive product (>100x). Apply chunk-size + `GAP-1` first, then concurrency, then re-profile to find the next binding resource (likely embedding CPU per `GAP-2`, then the per-graph write lock at `runtime.py:350-358`).

**Simulation is the next sink.** Each agent action is its own gemini call across agents × rounds. Unlike the graph it has parallelism (`OASIS_SEMAPHORE`), but it inherits dead/under-tuned knobs: `SIM_CONVERGENCE_STOP` never fires (`SIM-1`), `OASIS_DEFAULT_MAX_ROUNDS=0` allows up to 336 rounds (`SIM-2`), and much forecasting machinery (ensemble, decision channel, audience, ledger) is dormant.

## Top 10 by impact × effort

| Rank | Optimization | Category | Impact | Effort | Knob/Change |
|---|---|---|---|---|---|
| 1 | `GAP-1` Route graph extraction to fast tier (`model_size→tier` map) | latency | Largest single lever; ~3-5x per-call | small | Code in `llm_adapter.py` + `LLM_TIERED_ROUTING=true` + `LLM_FAST_MODEL` |
| 2 | Chunk size 500→2500 chars (merged `GRAPH-2`/`RESEARCH-1`/`ONTO-1`/`CHUNK-1`) | latency+quality | ~5x fewer episodes + better recall | trivial | `DEFAULT_CHUNK_SIZE=2500`, `DEFAULT_CHUNK_OVERLAP=250` |
| 3 | `GRAPH-1` Parallel episode extraction | latency | ~4x graph stage | trivial | `GRAPH_BUILD_CONCURRENCY=4` |
| 4 | `GRAPH-4` Entity resolution (also dedup safety net) | quality | Truer centrality + safe parallelism; no LLM | trivial | `GRAPH_RESOLVE_ENTITIES=true` |
| 5 | `GRAPH-3` Widen intra-episode fan-out | latency | ~1.5-2x per episode | trivial | `GRAPHITI_MAX_COROUTINES=16` |
| 6 | `REPORT-1`+`REPORT-4` Concurrent + native-tool sections | latency | ~2.5-3.5x report stage | trivial | `REPORT_SECTION_CONCURRENCY=3`, `REPORT_NATIVE_TOOLS=true` |
| 7 | `SIM-2`+`SIM-1` Cap rounds + convergence early-stop | latency | ~2-9x sim tail risk bound; ~3-4x typical | trivial+medium | `OASIS_DEFAULT_MAX_ROUNDS=36`; wire `SIM_CONVERGENCE_STOP` |
| 8 | `REPORT-3`+`REPORT-2` Signal pack on + brief context | quality+cost | Grounded sections, ~half report input tokens | trivial | `REPORT_SIGNAL_PACK=true`, `REPORT_SECTION_CONTEXT_MODE=brief` |
| 9 | `GAP-2` Memoize identical embedding encodes | latency | Removes duplicate CPU encodes | small | LRU in `embedder.py` |
| 10 | `REPORT-5`/`SIM-14` Seed ensemble (graph reused) | accuracy | Point estimate → calibrated distribution | small | `N_FORECAST_SEEDS=3` |

## Quick wins (config-only, this regime)

Flip these existing knobs (then ramp-and-measure the concurrency ones per `CORRECTION-2`):

| Knob | From → To | Rationale | Risk |
|---|---|---|---|
| `DEFAULT_CHUNK_SIZE` / `DEFAULT_CHUNK_OVERLAP` | 500/50 → **2500/250** | ~5x fewer episodes + higher relationship recall; the single biggest in-scope speedup | Coarser per-episode attribution; verify node/edge counts hold |
| `GRAPH_BUILD_CONCURRENCY` | 1 → **4** | Take the concurrent fan-out path (`runtime.py:488-510`) | Duplicate-node race — pair with resolution below |
| `GRAPH_RESOLVE_ENTITIES` | false → **true** | Cheap embeddings+name-match merge; dedup safety net for parallelism; no LLM | Over-merge bounded by 0.88 gate + audit log |
| `GRAPHITI_MAX_COROUTINES` | 8 → **16** | Wider intra-episode per-node/edge fan-out | Combined `concurrency × coroutines × platforms` must stay under proxy/CPU ceiling |
| `LLM_TIERED_ROUTING` (+`LLM_FAST_MODEL`/`_PROVIDER`/`_BASE_URL`/`_KEY`) | false → **true** | Inert today (`GAP-1`); routes mechanical extraction to a fast non-reasoning model | Weaker model raises schema-echo retries — monitor via telemetry |
| `LLM_CACHE_ENABLED` | false → **true** | Content-addressed reuse of identical `chat()/chat_json()`; pays off across reruns/forks/ensembles | Process-local, exact-key only; safe |
| `REPORT_SECTION_CONCURRENCY` | 1 → **3** | Body sections are independent; near-linear section-phase speedup | Shared FalkorDB reads from threads; per-section telemetry skipped in concurrent mode |
| `REPORT_NATIVE_TOOLS` | false → **true** | Provider supports native tools; avoids brittle regex ReAct + conflict/contamination retries | Auto-falls back to ReAct per-section on exception |
| `REPORT_SECTION_CONTEXT_MODE` | full → **brief** | Kills O(N²) prior-section re-transmission | Slightly weaker verbatim anti-dup; tail sections still get full body |
| `REPORT_SIGNAL_PACK` | false → **true** | Pins deterministic sim aggregates into every section as a citable numeric floor | Bounded fixed prefix per section (offset by brief mode) |
| `OASIS_DEFAULT_MAX_ROUNDS` | 0 → **36** | Caps a pathological 336-round time_config (`SIM-2`); truncation already audited | Long-horizon scenarios must override |
| `OASIS_SEMAPHORE` | 16 → **24→32** (ramp) | More in-flight agent calls; no observed backpressure | Untested at high concurrency — step and watch p95 |
| `PROFILE_ZEP_SKIP_WHEN_CONTEXT` | false → **true** | Skip redundant Zep re-retrieval when entity already carries edges/nodes | Cold entities still retrieve |
| `SIM_AUDIENCE_SIZE` | 0 → **200-500** | Adds lurker/silent-majority mass for bandwagon dynamics; zero prep LLM cost | More per-round activations — bound with active cap + `SIM-1/2` |
| `N_FORECAST_SEEDS` | 1 → **3** | Calibrated distribution; graph (the 5h cost) is reused across seeds | Each seed adds a sim+report cycle |
| `ONTOLOGY_AUTO_SELECT` | false → **true** | Routes non-social forecasts to `general_forecast` instead of the 10-type social template | Empty-result fallback re-runs default — safe |
| `ADAPTIVE_CONTEXT` | false → **true** | Token-budgeted truncation instead of hardcoded char slices | Validate it doesn't drop required context across call sites |
| `SIM_CONVERGENCE_STOP` | false → **true** | Enables early-stop **after the missing wiring lands** (`SIM-1`) | No-op until code wired |
| `IPC_TELEMETRY_ENABLED` / `LLM_TELEMETRY_ENABLED` | false → **true** | Interview health + per-stage cost/latency visibility | Negligible disk |

## Latency & throughput

### Research

#### RESEARCH-2 — Dual-track feeds both full docs into the graph, doubling episodes
- **Evidence:** `deerflow_research.py:1676-1710` runs Track A (deep research) and Track B (actor dossier); `pipeline_orchestrator.py:2928-2929` splits `dossier_md` and prepends its chunks to the report chunks. Heavy content overlap; `DEERFLOW_DUAL_TRACK` defaults true.
- **Current:** The graph extracts both overlapping corpora, roughly doubling the dominant serial work and doubling research tokens.
- **Proposed:** Build the graph from the dossier alone when present (actors already seeded via `GRAPH_SEED_FROM_ACTORS`), keeping the report for ontology context only; or set `DEERFLOW_DUAL_TRACK=false`. See `ONTO-7` for the same redundancy from the chunking side.
- **Knob/Change:** `DEERFLOW_DUAL_TRACK=false`, or code at `orchestrator.py:2928-2929`.
- **Impact:** ~2x fewer episodes and ~2x lower research tokens; stacks with the chunk-size change.
- **Risk:** Dropping the report from the graph loses some broad evidence; mitigated because the larger chunk size makes the full corpus affordable anyway.
- **Effort:** small

#### RESEARCH-3 — Track B judge-refine loop sits on the research critical path
- **Evidence:** `deerflow_research.py:1486-1528` runs a judge plus up to `ACTOR_DOSSIER_JUDGE_MAX_ROUNDS` refine turns and a re-synthesis; main awaits Track B at `1703-1705`.
- **Current:** Up to ~5 sequential large-context calls on the slow model as quality insurance, extending research when Track B lags.
- **Proposed:** For latency runs set `ACTOR_DOSSIER_JUDGE_MAX_ROUNDS=0`; better, judge only when `dossier_coverage` flags weakness (`orchestrator.py:2782-2800`).
- **Knob/Change:** `ACTOR_DOSSIER_JUDGE_MAX_ROUNDS` → 0/1.
- **Impact:** Removes 1-3 large calls from the critical path.
- **Risk:** Thinner actor cast caps forecast quality — keep on for quality runs.
- **Effort:** trivial

#### RESEARCH-5 — Huge extraction schema + dossier+report input is slow and parse-fragile
- **Evidence:** `deerflow_research.py:515-779` defines a ~5KB schema all-on by default (`528-530`); `extract_structured_tool_free` feeds schema + dossier + report (`1785-1793`); parse failure costs a second agent extraction (`1796-1806`).
- **Current:** Very large schema + full dossier+report to the slow model, with occasional parse-failure fallback round-trips.
- **Proposed:** Bound input to dossier + a capped report excerpt; allow disabling heaviest schema blocks via `RESEARCH_EVIDENCE_GRADING`/`RESEARCH_FORECAST_INPUTS` for speed runs.
- **Knob/Change:** `RESEARCH_EVIDENCE_GRADING`/`RESEARCH_FORECAST_INPUTS` + input truncation at `1785-1793`.
- **Impact:** Faster, more reliably parsing extraction; fewer fallbacks.
- **Risk:** Trimming drops grading richness or a late-report actor; bounded by keeping the dossier intact.
- **Effort:** small

### Ontology

The chunk-size root cause findings `ONTO-1` and `ONTO-2` are the **same edit** as the chunk-size quick win (`CORRECTION-1`) and are counted once there. The ontology-specific items below are independent.

#### ONTO-3 — Ontology corpus hard-truncated at 50K chars blinds the schema to the dossier+report tail
- **Evidence:** `ontology_generator.py:588` `MAX_TEXT_LENGTH_FOR_LLM=50000`; `627-629` slices `combined_text[:50000]`. `pipeline_orchestrator.py:2857` passes `[dossier_md, report_md]` (dossier first), so a large dossier truncates the report even harder.
- **Current:** Schema is derived from only the first 50K chars; any type/jurisdiction/relationship appearing later is absent, constraining all typed extraction and retrieval.
- **Proposed:** Raise the constant to ~120K (well within gemini's context), or token-budget it via `ADAPTIVE_CONTEXT`; alternatively sample head+middle+tail.
- **Knob/Change:** `ontology_generator.py:588` 50000 → 120000, or wire `ADAPTIVE_CONTEXT`.
- **Impact:** Fuller corpus coverage → fewer missed entity/edge types. Stage is ~1 min, so cost is immaterial.
- **Risk:** Slightly larger ontology prompt; negligible. Low.
- **Effort:** trivial

#### ONTO-7 — Dossier and report chunked independently then summed (doubles episodes with Track B)
- **Evidence:** `pipeline_orchestrator.py:2924` chunks `report_md`; `:2929` prepends chunks of `dossier_md` — both at 500 chars. With dual-track, total episodes = dossier_chunks + report_chunks over largely restated actor content.
- **Current:** Overlapping actor/relationship content is extracted twice plus a seam boundary chunk.
- **Proposed:** Primarily resolved by the chunk-size increase (shrinks both pools ~5x). Additionally evaluate skipping dossier re-chunking since `GRAPH_SEED_FROM_ACTORS=true` already seeds the cast as typed edges (`pipeline_orchestrator.py:2944-2950`); overlaps `RESEARCH-2`.
- **Knob/Change:** `DEFAULT_CHUNK_SIZE` (shared lever); dossier-skip is a code/policy experiment.
- **Impact:** Chunk-size change cuts both pools ~5x; optional dossier dedup removes further redundancy.
- **Risk:** Skipping dossier chunking loses narrative detail not in `actors.json` — treat as experiment.
- **Effort:** small

### Graph build (biggest bottleneck)

#### GAP-1 — `LLM_TIERED_ROUTING` is INERT for the graph build (the missing per-call multiplier)
- **Evidence:** `services/graphiti_client/llm_adapter.py:66-104` `_generate_response` receives `model_size: ModelSize` (small/medium) from graphiti_core but never uses it; the call at ~`:103` passes **no** `tier` kwarg, so `llm_client.py:237-242` defaults to `tier='strong'`. `tier='fast'` is wired only in `zep_tools.py:1674/2208/2270` (report-side), never in the extractor. `llm_client.py:131-135` makes `_model_for_tier` a no-op unless `LLM_TIERED_ROUTING=true` **and** a tier is passed.
- **Current:** Every one of the ~400 episodes' `extract_nodes`/`extract_edges`/dedup/attribute calls runs on the strong reasoning model even if an operator flips `LLM_TIERED_ROUTING=true`. On a proxy where "low" is still a reasoning model, this is the untouched per-call latency tax.
- **Proposed:** In `_generate_response`, map `model_size==ModelSize.small → tier='fast'` (optionally medium too for extraction), else `'strong'`. Then enable `LLM_TIERED_ROUTING=true` with `LLM_FAST_MODEL` pointed at a genuine non-reasoning fast model. Compounds multiplicatively with `GRAPH-1/2/3` because it cuts per-call latency, the quantity those only parallelize/count-reduce.
- **Knob/Change:** Code in `llm_adapter.py` (model_size→tier) + `LLM_TIERED_ROUTING=true` + `LLM_FAST_MODEL`/`LLM_FAST_PROVIDER`/`LLM_FAST_BASE_URL`/`LLM_FAST_KEY`.
- **Impact:** Largest single lever — if the fast model is ~3-5x faster per dense extraction call, the graph stage drops roughly proportionally *on top of* the concurrency/chunk wins.
- **Risk:** Weaker fast model raises schema-echo / missing-field retries (`llm_adapter.py:88-155`, `GRAPH-11`), which can re-inflate calls. Keep the rising-temp pre-validate retry, monitor via `LLM_TELEMETRY_ENABLED`, keep edge resolution on strong if recall drops, validate node/edge counts vs a strong-only baseline.
- **Effort:** small

#### Chunk size 500→2500 chars — root cause of the ~400-episode count (merged GRAPH-2/RESEARCH-1/ONTO-1/CHUNK-1)
- **Evidence:** `config.py:496` `DEFAULT_CHUNK_SIZE=500`; `file_parser.py:147-188` `split_text_into_chunks` slices by **character** count, not tokens; `pipeline_orchestrator.py:2924,2929` chunk both report and dossier at this size; `:2957-2958` `add_text_batches(chunks, batch_size=10)`. ~400 episodes (40×10), each a full `extract_nodes` + `extract_edges` + per-node dedup/attribute + per-edge resolve round-trip.
- **Current:** Tiny ~100-150 token episodes maximize the count of fixed-overhead extraction passes; overlap=50 re-extracts ~10% of text; related entities frequently split across chunk boundaries (hurting edge recall).
- **Proposed:** `DEFAULT_CHUNK_SIZE=2500`, `DEFAULT_CHUNK_OVERLAP=250` (the larger of the merged proposals; a reasoning model handles ~600 tokens comfortably). Episode count drops ~5x; relationship recall rises because more (subject, relation, object) triples sit inside one window. Note: `pipeline_orchestrator.py:2924/2929` reads `Config.DEFAULT_CHUNK_SIZE` directly (ignores `project.chunk_size`), so the Config default is the effective lever; also update the `file_parser.split_text_into_chunks` default to match.
- **Knob/Change:** `DEFAULT_CHUNK_SIZE` 500→2500; `DEFAULT_CHUNK_OVERLAP` 50→250.
- **Impact:** ~5x fewer episodes → roughly proportional graph-stage reduction (toward ~1h) even at concurrency=1; multiplies with `GRAPH-1`/`GAP-1`. Count this speedup **once** (`CORRECTION-1`).
- **Risk:** Very large chunks (>~5K chars) could depress recall; 2.5K is in the safe band. A single bad chunk loses more text on failure. Verify node/edge counts before/after.
- **Effort:** trivial

#### GRAPH-1 — Serial per-episode extraction (`GRAPH_BUILD_CONCURRENCY=1`) is the primary ~5h bottleneck
- **Evidence:** `client.py:166-183` reads `Config.GRAPH_BUILD_CONCURRENCY` (default 1) and falls into the serial `for ep in eps: self._rt.add_episode(...)` loop; `runtime.py:350-358` serializes each `add_episode` under the per-graph lock. `pipeline_orchestrator.py:2957` feeds ~400 chunks. Zero 429s in 5h ⇒ purely serial, not quota-bound.
- **Current:** All ~400 episodes ingest one at a time; wall time ≈ 400 × per-episode latency.
- **Proposed:** `GRAPH_BUILD_CONCURRENCY=4` (4-8) to take the concurrent fan-out path (`runtime.py:488-510`, semaphore-bounded under one per-graph lock). Pair with `GRAPH-4`. `seed_actors` pre-creates canonical high-value nodes (`graph_builder.py:363-506`), so most contended entities already exist, limiting the dedup race.
- **Knob/Change:** `GRAPH_BUILD_CONCURRENCY=4`; **requires** `GRAPH_RESOLVE_ENTITIES=true`.
- **Impact:** ~4x faster on the dominant stage (~5h → ~40-75 min), since there are no rate limits to absorb. Throughput per batch is capped by `batch_size=10`, so concurrency>10 yields no extra per-batch gain.
- **Risk:** Concurrent episodes can both miss read-before-commit dedup and create duplicate same-name nodes (`runtime.py:474-485`); mitigated by `GRAPH-4` + actor pre-seeding. `pipeline_orchestrator.py:3024-3030` already warns on dup groups but only auto-merges when resolution is on.
- **Effort:** trivial

#### GRAPH-3 — `GRAPHITI_MAX_COROUTINES=8` throttles intra-episode LLM parallelism below graphiti's default of 20
- **Evidence:** `runtime.py:252-262` passes `max_coroutines=int(GRAPHITI_MAX_COROUTINES default 8)`; graphiti `helpers.py:38` `SEMAPHORE_LIMIT` default is 20. Within one `add_episode`, `semaphore_gather` bounds per-node dedup (`node_operations.py:431/437`), per-node attributes (`:744`), per-edge resolution/invalidation (`edge_operations.py:365/392/407/490`).
- **Current:** Each episode's 15-30 per-entity calls run ≤8-wide, serializing into ~3-4 waves even at concurrency=1.
- **Proposed:** `GRAPHITI_MAX_COROUTINES=16` (toward 20). Size jointly with `GRAPH_BUILD_CONCURRENCY` — total in-flight ≈ `concurrency × max_coroutines × platforms`.
- **Knob/Change:** `GRAPHITI_MAX_COROUTINES=16` (read at `runtime.py:253`).
- **Impact:** ~1.5-2x faster per episode at concurrency=1; multiplies with `GRAPH-1`.
- **Risk:** Too-high combined fan-out could hit proxy limits or oversubscribe the local embedder thread pool (`GAP-2`); tune jointly and ramp (`CORRECTION-2`).
- **Effort:** trivial

#### GRAPH-7 — App ingests per-chunk instead of graphiti's native `add_episode_bulk`
- **Evidence:** `client.py:150-183` `add_batch` always calls per-episode `add_episode`. `graphiti_core` exposes `add_episode_bulk` using `extract_nodes_and_edges_bulk` + `dedupe_nodes_bulk` + `dedupe_edges_bulk` (`graphiti.py:783-902`), batching extraction and dedup across many episodes and resolving duplicates within the batch.
- **Current:** Every chunk pays its own extract→resolve→write cycle; cross-chunk dedup is only incremental.
- **Proposed:** Add an opt-in `GRAPH_BUILD_BULK=true` runtime path routing batches through `add_episode_bulk`, keeping per-episode as default. Parallelizes extraction and avoids the duplicate-node race entirely.
- **Knob/Change:** New `GRAPH_BUILD_BULK` flag (default false).
- **Impact:** Materially fewer LLM round-trips per N chunks plus race-free dedup; complementary to `GRAPH-1/2/3`.
- **Risk:** Bulk path has weaker per-episode bi-temporal/edge-invalidation semantics; verify `valid_at` anchoring before defaulting on. Larger change than a knob flip.
- **Effort:** medium

#### GRAPH-8 — `seed_actors` issues one synchronous `add_triplet` per relationship/actor/alias
- **Evidence:** `graph_builder.py:391-506` loops `self.client.graph.add_triplet` for every relationship, isolated actor (`IS_A`), and alias (`ALSO_KNOWN_AS`); each maps to `runtime.add_triplet → self.run(...)` (`runtime.py:419-465`), a separate round-trip acquiring the per-graph lock and embedding both endpoints.
- **Current:** Seeding is fully serialized one triplet at a time; embeddings computed per endpoint per call.
- **Proposed:** Batched seeding under the per-graph write lock, or precompute endpoint embeddings in one `embed_texts` call. Lower priority than `GRAPH-1/2`.
- **Knob/Change:** Pure code change (optional `GRAPH_SEED_CONCURRENCY`); keep `GRAPH_SEED_FROM_ACTORS=true`.
- **Impact:** Shaves the seeding sub-phase from O(triplets) serial round-trips to a few batched passes; small absolute time.
- **Risk:** Concurrent triplet writes reintroduce the endpoint dedup race; stay under the per-graph lock and process distinct endpoints.
- **Effort:** small

#### GAP-2 — Local embedder re-encodes identical name strings thousands of times with no memoization
- **Evidence:** `services/graphiti_client/embedder.py:73-91` `_encode` calls `model.encode` per call with no cache; `create()`/`create_batch()` always hit the SentenceTransformer (`paraphrase-multilingual-MiniLM-L12-v2`, `config.py:472`, 12-layer). During dedup, recurring canonical names ("OpenAI", seeded actors) are re-encoded on every episode mentioning them across all ~400 episodes.
- **Current:** Every occurrence of a recurring name pays a fresh 12-layer forward pass; under raised concurrency multiple threads oversubscribe CPU/GIL. (`GRAPH-10` only proposes batching, not dedup of identical strings.)
- **Proposed:** Bounded content-addressed LRU keyed on normalized text around `_encode` — vectors are deterministic, so this is lossless. Optionally pin torch/OMP thread count to avoid oversubscription when concurrency>1.
- **Knob/Change:** Pure code change (LRU in `embedder.py`); optional `GRAPHITI_EMBED_MODEL` swap + thread-count pin.
- **Impact:** Eliminates the bulk of duplicate encodes; becomes a growing share as `GAP-1`/`GRAPH-1/2/3` cut LLM latency.
- **Risk:** LRU memory bounded/trivial. A lighter embed model would change `EMBEDDING_DIM` (re-embed required) — keep the current model, just add the cache for zero quality risk.
- **Effort:** small

#### GRAPH-10 — Local embedder encodes per-call without cross-episode batching
- **Evidence:** `embedder.py:73-91` `create()` encodes a single text via `run_in_executor(None, self._encode, [text])`; graphiti calls `EmbedderClient.create` once per resolved node/edge name. Model loads lazily once (good), but per-name encodes are individual.
- **Current:** Embeddings computed one short string at a time; no batching within or across episodes.
- **Proposed:** Prefer the bulk path (`GRAPH-7`) so `create_batch` (`embedder.py:87-91`) coalesces encodes; optionally pin a dedicated executor / torch thread count.
- **Knob/Change:** Pure code change; benefits from `GRAPH-7`.
- **Impact:** Reduces embedding overhead once LLM latency is no longer dominant; secondary win, complements `GAP-2`.
- **Risk:** Minimal — batching changes throughput, not vectors.
- **Effort:** small

#### GAP-3 — Re-extracted overlap + fixed schema/instruction prefix paid per micro-episode
- **Evidence:** Each extraction prompt carries the full injected JSON schema + multilingual instruction + the schema-echo guard appended in `llm_adapter.py:78-87` — a fixed prefix paid ~400 times. `DEFAULT_CHUNK_OVERLAP=50` on 500-char chunks re-extracts ~10%. `ADAPTIVE_CONTEXT=false` (`config.py:85`) keeps hardcoded char slices elsewhere.
- **Current:** ~400 prompts each re-transmit the same boilerplate; overlap re-extracts ~10%.
- **Proposed:** Primarily rides the chunk-size increase (fewer, larger episodes amortize the prefix). Additionally keep overlap proportional (~5-8% not a fixed 50), trim the guard once on a known-conforming fast model, and enable `ADAPTIVE_CONTEXT`.
- **Knob/Change:** `DEFAULT_CHUNK_OVERLAP` scaled to chunk size; `ADAPTIVE_CONTEXT=true`; minor `llm_adapter` guard trim.
- **Impact:** Reduces redundant input tokens; secondary to chunk-size/tier wins but compounds.
- **Risk:** Lower overlap can split an entity across a boundary; keep proportional. `ADAPTIVE_CONTEXT` changes truncation across many sites — validate.
- **Effort:** small

### Simulation

#### SIM-2 — Unbounded `total_rounds` (up to 336) with no default cap
- **Evidence:** `run_parallel_simulation.py:2377` `total_rounds = (total_hours*60)//minutes_per_round`; `simulation_config_generator.py:1041-1044` allows 24-168h and 30-120 min/round → 168*60//30 = 336 rounds. `OASIS_DEFAULT_MAX_ROUNDS=0` (`config.py:502`) = no truncation; `pipeline_orchestrator.py:1148/3178` passes 0 through as None.
- **Current:** A worst-case config-gen output multiplies sim cost ~5x over the nominal 72-round case, discovered only after hours.
- **Proposed:** `OASIS_DEFAULT_MAX_ROUNDS=36` (or 48); additionally clamp `total_rounds = min(total_rounds, cap)` in the run script. Optionally clamp `minutes_per_round >= 60`.
- **Knob/Change:** `OASIS_DEFAULT_MAX_ROUNDS` 0 → 36 (already plumbed end-to-end at `pipeline_orchestrator.py:1148,1960,3178`).
- **Impact:** Caps a 336-round run at 36 (~9x) and a 72-round run at 36 (~2x) fewer sim calls.
- **Risk:** Truncation shortens the horizon; already surfaced as `rounds_truncated_from/to` (`simulation_runner.py:159-160,438-446`). Long-horizon scenarios must override.
- **Effort:** trivial

#### SIM-1 — `SIM_CONVERGENCE_STOP` is a dead knob: round loop never early-stops
- **Evidence:** `config.py:646-648` defines `SIM_CONVERGENCE_STOP/EPS/WINDOW`, but grep finds zero readers outside `config.py`. The twitter/reddit loops (`run_parallel_simulation.py:2392,:2638`) iterate the full `range(total_rounds)` with the only `break` being the shutdown check; stance trajectory is computed POST-sim from `actions.jsonl` (~`:1796`).
- **Current:** Every sim runs the full horizon even when stance shares and net sentiment have flatlined; each wasted late round is ~target_count independent gemini calls per platform.
- **Proposed:** Maintain a rolling per-round stance-share vector (the `by_stance` counts already aggregated for `run_summary`); after `SIM_CONVERGENCE_WINDOW` consecutive rounds with max per-stance delta `< SIM_CONVERGENCE_EPS`, break (only when `SIM_CONVERGENCE_STOP=true` and past a warmup ~5). Record `rounds_executed vs total_rounds`.
- **Knob/Change:** Flip `SIM_CONVERGENCE_STOP=true` **and** implement the wiring (EPS=0.02, WINDOW=3 are sane).
- **Impact:** Opinion dynamics typically settle in 10-25 rounds; cutting 72→~20 is ~3-4x fewer sim calls with negligible quality loss.
- **Risk:** Early stop on a transient plateau; mitigated by warmup floor + WINDOW consecutive stable rounds. Opt-in, no regression by default.
- **Effort:** medium

#### SIM-3 — OASIS concurrency under-provisioned (ramp, do not one-shot)
- **Evidence:** `oasis_llm.py:259-272` returns `cap//max(1,platforms)`; dual-platform → `OASIS_SEMAPHORE/2`. `config.py:513` default 30; `.env` sets 16 → per-platform 8. Zero 429s in 5h.
- **Current:** In-flight agent calls throttled to 8-15.
- **Proposed:** Raise `OASIS_SEMAPHORE`, keeping the `//platforms` split. **Ramp 16→24→32**, watching ipc/oasis p95 before considering 48-64 (`CORRECTION-2`: the no-429 evidence was at concurrency=1, so high concurrency is untested).
- **Knob/Change:** `OASIS_SEMAPHORE` 16 → 24→32 (staged).
- **Impact:** Sim wall-clock scales ~inversely with effective concurrency until the provider saturates; up to ~4x for rounds with many active agents.
- **Risk:** A hidden proxy ceiling could induce queueing/timeouts; ramp and watch.
- **Effort:** trivial

#### SIM-4 — Config generation runs agent batches strictly serially
- **Evidence:** `simulation_config_generator.py:454-471` loops `_generate_agent_configs_batch` over ~ceil(N/15) batches (`AGENTS_PER_BATCH=15`, `:353`), preceded by serial time- and event-config calls (`:442,448`). N=80 ⇒ ~6 sequential dense calls.
- **Current:** Prepare-stage config-gen is a serial chain on a slow model, though batches are independent (disjoint entity slices, `start_idx`).
- **Proposed:** Parallelize batches with a `ThreadPoolExecutor` (the pattern in `oasis_profile_generator.generate_profiles_from_entities:1285`), writing into a pre-sized list by `batch_idx` to preserve `agent_id` ordering.
- **Knob/Change:** New `CONFIG_GEN_BATCH_CONCURRENCY` (~4) or reuse the profile parallel count.
- **Impact:** Config-gen drops from ~6+ serial calls to ~2 waves.
- **Risk:** Higher prepare burst, bounded by worker cap; ordering must be index-preserved.
- **Effort:** small

#### SIM-6 — Decision channel elicits per-round commitments sequentially despite independence
- **Evidence:** `decision_channel.py:154-170` loops `decisions = _elicit_round_decisions(...); ws.step(...)`; each elicitation is a standalone `chat_json` (`:56-61`) whose **input** does not depend on prior rounds' LLM output — only `ws.step` ordering does.
- **Current:** With `SIM_DECISION_CHANNEL=true`, a 72-round sim makes 72 strictly sequential post-sim calls.
- **Proposed:** Two-phase: (1) parallelize all `_elicit_round_decisions` calls under a semaphore, (2) replay `ws.step(commitments)` in round order. WorldState stays deterministic.
- **Knob/Change:** Reuse `OASIS_SEMAPHORE` or new `DECISION_CHANNEL_CONCURRENCY`. Default-off feature, no regression.
- **Impact:** 72 calls at concurrency 16 ≈ 4-5x faster.
- **Risk:** Must collect all results before stepping to preserve trajectory/`converged_at` semantics.
- **Effort:** medium

#### SIM-7 — Per-persona Zep re-retrieval runs even when the entity already carries graph context
- **Evidence:** `oasis_profile_generator.py:674-684` gates `skip_zep` on `PROFILE_ZEP_SKIP_WHEN_CONTEXT` (default False), so by default every persona calls `_search_zep_for_entity` (`:313-455`) firing two limit=20/30 hybrid searches in a nested `ThreadPoolExecutor` with retry/backoff — even though entities arrive from `ZepEntityReader` already populated with `related_edges/related_nodes`.
- **Current:** ~80 personas each pay an extra round-trip + nested threadpool + jittered backoff, largely redundant.
- **Proposed:** `PROFILE_ZEP_SKIP_WHEN_CONTEXT=true` so personas with non-empty edges/nodes reuse bundled context; cold entities still retrieve.
- **Knob/Change:** `PROFILE_ZEP_SKIP_WHEN_CONTEXT` false → true.
- **Impact:** Removes one (often two) Zep searches per persona for most agents → faster, lower-jitter prepare.
- **Risk:** Slightly less enriched personas for entities Zep would have augmented; bundled edges usually suffice.
- **Effort:** trivial

#### SIM-10 — Agent-dynamics injects an unbounded, append-only system memory record every round
- **Evidence:** `run_parallel_simulation.py:1454-1476` `_inject_agent_dynamics` calls `agent.update_memory(note, SYSTEM)` each round for any agent with a non-empty `state_line`; the code deliberately chose append over re-seed. `SIM_AGENT_DYNAMICS` defaults true (`config.py:632`).
- **Current:** A diverging agent accumulates one `【你当前状态】...` record per active round; its prompt grows monotonically, raising cost/latency for the most forecast-relevant agents.
- **Proposed:** Replace the prior state note (track/overwrite the last injected record) or cap to the most recent line.
- **Knob/Change:** Code change in the injection helper; optional `SIM_DYNAMICS_REPLACE` (default replace).
- **Impact:** Bounds per-agent prompt growth under default-on dynamics.
- **Risk:** Touches camel memory internals — verify cross-round conversation memory is preserved (the original concern that motivated append).
- **Effort:** medium

#### SIM-11 — Persona-generation parallelism conservative for the HTTP proxy (ramp)
- **Evidence:** `pipeline_orchestrator.py:2036,:3114` pass `parallel_profile_count = 8` (HTTP) / 3 (CLI) into `prepare_simulation → generate_profiles_from_entities` (`oasis_profile_generator.py:1285` `max_workers=parallel_count`). `OASIS_SEMAPHORE=30`.
- **Current:** ~80 persona calls processed 8-wide, leaving provider concurrency idle.
- **Proposed:** Raise the HTTP branch toward observed-safe concurrency (16), paired with `SIM-7`. Per `CORRECTION-2`, ramp and measure.
- **Knob/Change:** HTTP `parallel_profile_count` 8 → 16 (`pipeline_orchestrator.py:2036,3114`), or read `OASIS_SEMAPHORE`.
- **Impact:** Roughly halves persona-generation wall-clock.
- **Risk:** More concurrent prepare calls; bounded, but untested at this width.
- **Effort:** trivial

#### SIM-13 — Monitor thread rewrites full run_state (incl. 50 actions) every 2s
- **Evidence:** `simulation_runner.py:604-619` polls `time.sleep(2)` and calls `_save_run_state(state)` each iteration; `_save_run_state` (`:374-383`) atomically writes `to_detail_dict()` including up to 50 serialized `recent_actions` (`:228-236`).
- **Current:** Thousands of full-JSON temp-write+rename cycles plus `_run_state_lock` contention over a multi-hour run, regardless of change.
- **Proposed:** Throttle to a dirty-flag (round advanced / counts / status changed) or a longer interval (5-10s); keep in-memory state fresh for polling endpoints.
- **Knob/Change:** New `SIM_RUNSTATE_SAVE_INTERVAL` (~5s) or dirty check.
- **Impact:** ~2-5x fewer disk writes and less lock contention.
- **Risk:** Slightly staler on-disk state; the in-memory object stays current so the API is unaffected.
- **Effort:** small

### Report

#### REPORT-1 — Section generation is strictly serial (`REPORT_SECTION_CONCURRENCY=1`)
- **Evidence:** `report_agent.py:3042` reads `REPORT_SECTION_CONCURRENCY` (default 1, `config.py:673`); `_generate_sections_concurrent` (`:2317-2364`) runs only when `_concurrency>1` (`:3045`), else serial loop at `:3051`. Each section is a ReAct loop ≤10 iterations (`:2597`) needing `MIN_TOOL_CALLS=4`/`MAX=8` (`config.py:667,527`) — ≥5 sequential calls × 5-8 sections.
- **Current:** Tens of serial multi-second calls back-to-back — many minutes of entirely parallelizable latency.
- **Proposed:** `REPORT_SECTION_CONCURRENCY=3`. The implementation already isolates body sections into a `ThreadPoolExecutor`, defers summary/conclusion tail sections to run last with full body (`:2300-2364`), and degrades per-section failures to placeholders.
- **Knob/Change:** `REPORT_SECTION_CONCURRENCY` 1 → 3.
- **Impact:** ~2.5-3.5x faster section phase.
- **Risk:** Shared `LLMClient`/`ZepToolsService` read from threads (low); per-section telemetry skipped in concurrent mode (`:3074`); higher peak proxy load.
- **Effort:** trivial

#### REPORT-4 — Native tool-calling OFF forces brittle regex ReAct with conflict/contamination retries
- **Evidence:** `config.py:670` `REPORT_NATIVE_TOOLS=false`; `supports_native_tools()` gates on it (`llm_client.py:272-280`). ReAct path spends calls on format-policing: conflict retries (`report_agent.py:2646-2680`), contamination retries (`:2714-2727, 2829-2842`), regex parsing (`:2082-2127`). The provider supports native tools.
- **Current:** Every section pays for hand-rolled parsing + up to 2 conflict + 2 contamination corrective round-trips, plus contamination-adoption risk. Native path (`:2412-2523`) avoids all of it.
- **Proposed:** `REPORT_NATIVE_TOOLS=true`. Native generator handles min-tool-call enforcement (`:2503-2513`) and auto-falls back to ReAct on exception (`:2380-2384`).
- **Knob/Change:** `REPORT_NATIVE_TOOLS` false → true.
- **Impact:** Fewer wasted corrective calls; eliminates the contamination-adoption failure mode.
- **Risk:** Relies on provider tool correctness; per-section ReAct fallback bounds the downside.
- **Effort:** trivial

#### REPORT-2 — `REPORT_SECTION_CONTEXT_MODE=full` re-sends every prior section each ReAct iteration (O(N²) tokens)
- **Evidence:** `config.py:676` default `'full'`. Serial+full passes the accumulating `generated_sections` (`report_agent.py:3090`) into `_generate_section_react`, truncating each prior section to 8000 chars and joining (`:2575-2581`) into the prompt re-sent on every ≤10 ReAct iteration (`:2621`). The compact `_build_synthesis_brief` (`:2306-2315`) is used only in `'brief'` or concurrent mode.
- **Current:** Section k carries up to (k-1)×8000 chars × ~10 iterations; the last section re-transmits ~50k+ chars dozens of times.
- **Proposed:** `REPORT_SECTION_CONTEXT_MODE='brief'` (or simply `REPORT_SECTION_CONCURRENCY>1`, which routes body sections through the brief). The brief preserves anti-duplication intent without quadratic blowup.
- **Knob/Change:** `REPORT_SECTION_CONTEXT_MODE` full → brief.
- **Impact:** Roughly halves report input tokens for an 8-section report; modest latency on top of `REPORT-1`.
- **Risk:** Slightly weaker verbatim anti-dup; tail/summary sections still get full body (`:2353-2363`), so the executive summary is unaffected.
- **Effort:** trivial

#### REPORT-8 — `interview_agents` (heaviest tool) is actively nudged via the "unused tools" hint
- **Evidence:** `interview_agents` does a dual-platform claude-cli interview of up to 6 agents (~14-40s each, `report_agent.py:1979-1981`). The ReAct loop appends a hint recommending any unused tool (`:2788-2791, 2697-2698`), and `interview_agents` is in `all_tools` (`:2603-2604`). With `MIN_TOOL_CALLS=4` the model is pushed toward the slowest tool on every section.
- **Current:** Sections can spend minutes inside interviews purely to satisfy tool-diversity nudges.
- **Proposed:** Exclude `interview_agents` from the unused-tools nudge (keep it available, not recommended), or gate behind a flag. Cheap deterministic tools (`simulation_outcomes`/`coalition_map`) already give the strongest grounding.
- **Knob/Change:** Drop `interview_agents` from the nudge set (`report_agent.py:2603`).
- **Impact:** Removes worst-case multi-minute interview detours.
- **Risk:** Slightly fewer first-person quotes unless the model chooses interviews itself.
- **Effort:** small

#### REPORT-9 — `REPORT_AGENT_SECTION_MAX_TOKENS=32768` applied to every ReAct/tool iteration
- **Evidence:** Every ReAct `chat()` passes `max_tokens=Config.REPORT_AGENT_SECTION_MAX_TOKENS` (`report_agent.py:2624,2862`) = 32768 (`config.py:533`), including intermediate tool-selection turns. Target length is only 1800-2800 chars (`:846-849`).
- **Current:** Tool-deciding turns get a 32k completion budget, inviting long reasoning excursions on a reasoning model.
- **Proposed:** Use ~8192 for tool-selection iterations; reserve 32768 only for the final-answer turn (naturally separable in the native path, `:2519`).
- **Knob/Change:** Keep 32768 for the final turn; add a smaller intermediate cap (code change).
- **Impact:** Faster, cheaper intermediate turns without shrinking final section length.
- **Risk:** A legitimately long mid-loop emission could truncate; low because final answers are the long ones.
- **Effort:** small

#### REPORT-10 — ReAct path hardcodes temperature 0.5, ignoring `REPORT_AGENT_TEMPERATURE`
- **Evidence:** `_generate_section_native` uses `Config.REPORT_AGENT_TEMPERATURE` (`report_agent.py:2465,2522`); `_generate_section_react` hardcodes `temperature=0.5` (`:2621,2861`). With `REPORT_NATIVE_TOOLS=false` (default), the knob (`config.py:529`) is silently inert.
- **Current:** Operators tuning `REPORT_AGENT_TEMPERATURE` see no effect on the default code path.
- **Proposed:** Replace the hardcoded 0.5 with `Config.REPORT_AGENT_TEMPERATURE`.
- **Knob/Change:** Code change to honor `REPORT_AGENT_TEMPERATURE` in the ReAct path.
- **Impact:** Makes the knob controllable; better tunability/observability.
- **Risk:** No behavior change unless the env value differs from 0.5.
- **Effort:** trivial

### LLM infra

#### SIM-9 / CACHE-1 — Content-addressed LLM cache disabled; identical prepare/persona/synthesis prompts re-billed
- **Evidence:** `telemetry.py:185-221` `LLMCache` (sha256 over provider/model/messages/temperature/max_tokens/response_format, bounded LRU 2048) is fully implemented; `llm_client.py:191-192` has the hit path. Gated by `LLM_CACHE_ENABLED=false`. Profile gen (`oasis_profile_generator.py:804`) and config gen (`simulation_config_generator.py:918`) issue calls identical across reruns and across `N_FORECAST_SEEDS` members (prep does not depend on seed); batch forks (`BATCH-1`) reuse anchor context.
- **Current:** Reruns/resumes/ensembles re-pay for byte-identical prepare-stage calls.
- **Proposed:** `LLM_CACHE_ENABLED=true` (process-local, bounded, exact-key match only). Pair with the `cached` counter (`telemetry.py:79-80`) to measure hit rate. Scope to a run/model version to avoid staleness.
- **Knob/Change:** `LLM_CACHE_ENABLED` false → true.
- **Impact:** Eliminates duplicate prepare-stage spend; magnitude grows with `N_FORECAST_SEEDS` and batch fork count.
- **Risk:** Only attempt-0 (deterministic-temp) calls hit; stochastic-temperature diversity is preserved because the key includes temperature/messages.
- **Effort:** trivial / small

#### GRAPH-11 — `llm_adapter` stacks a 3-step rising-temperature retry under graphiti's 4-attempt tenacity retry
- **Evidence:** `llm_adapter.py:98-155` inner `temps=(0.0,0.4,0.7)`, each re-issuing the full extraction prompt on schema-echo/validation failure (up to 3x), wrapped by graphiti's own retry (~4x) — worst case ~12 calls per failing extraction. Only 2 benign parse warnings in 5h, so rare today.
- **Current:** Failure modes can multiply LLM calls per episode; fine now but a tail risk if a weaker fast model is routed in (`GAP-1`).
- **Proposed:** Cap the combined retry budget (reduce inner temps to 2 or short-circuit when graphiti will retry anyway); if `LLM_TIERED_ROUTING` is on, keep extraction on a model known to emit conforming JSON. Log retry counts to telemetry.
- **Knob/Change:** Retry-budget code change; coordinate with `LLM_TIERED_ROUTING`/`LLM_TELEMETRY_ENABLED`.
- **Impact:** Bounds worst-case per-episode cost on failures; protects against a fast-model swap regressing.
- **Risk:** Fewer retries slightly raise hard-fail chance on a flaky model; low at current failure rate.
- **Effort:** small

#### RESEARCH-8 — Research-stage LLM calls bypass the backend cache
- **Evidence:** `deerflow_research.py` uses DeerFlow's `create_chat_model` in an isolated venv; `LLM_CACHE_ENABLED` applies only to the backend `llm_client`, which cannot cross the process boundary.
- **Current:** Near-identical large prompts (re-synthesis after refine, judge over the same dossier) pay full cost each time.
- **Proposed:** Pass provider prompt-cache hints if supported, or memoize synthesis/judge inputs within a run.
- **Knob/Change:** No in-subprocess cache today; per-run memoization keyed on exact content.
- **Impact:** Marginal token savings on judge/refine-heavy runs.
- **Risk:** Must key on exact content to avoid stale dossiers post-refine; low blast radius if per-run.
- **Effort:** medium

## Quality, thoroughness & accuracy

#### GRAPH-4 — `GRAPH_RESOLVE_ENTITIES=false` leaves duplicates un-merged (and uses NO LLM)
- **Evidence:** `config.py:604` default false. `zep_entity_resolver.py:190-237` `resolve_entities` calls only `runtime.embed_texts` (local), `plan_merges` (pure, `:89-165`), and `runtime.merge_nodes` (pure Cypher, `runtime.py:1018-1084`) — **no LLM**. Duplicate surface forms ("OpenAI" / "OpenAI 公司" / "@OpenAI") split search recall and under-count degree centrality feeding `GRAPH_CENTRALITY_PRIORS` and agent salience.
- **Current:** Same-entity nodes from seeding + prose + aliases stay split; centrality/components (`graph_builder.py:629-655`) computed on a fragmented graph; no post-build merge.
- **Proposed:** `GRAPH_RESOLVE_ENTITIES=true`. Cheap embeddings+name-match guarded by `GRAPH_RESOLVE_SIM_THRESHOLD=0.88` and same-label/canonical-protection rules (`:115-127, 168-173`). Also the documented cleanup for the `GRAPH-1` race.
- **Knob/Change:** `GRAPH_RESOLVE_ENTITIES` false → true; keep threshold 0.88.
- **Impact:** Higher recall, truer centrality, makes `GRAPH-1` parallelism safe. Adds well under a minute.
- **Risk:** Over-merge of distinct same-label entities; dual gate (name-match AND cosine ≥0.88, never two canonicals) bounds it; every merge audited to `entity_merges.json` for rollback.
- **Effort:** trivial

#### GRAPH-6 — `zep_paging` silently truncates node reads at `_MAX_NODES=2000`
- **Evidence:** `zep_paging.py:22` `_MAX_NODES=2000`; `fetch_all_nodes` (`:117-120`) hard-stops at 2000 with only a warning. Feeds `_get_graph_info` (`graph_builder.py:598`), `filter_defined_entities` (`zep_entity_reader.py:316`, builds the agent pool), and resolution scans. A ~400-episode corpus can exceed 2000 nodes.
- **Current:** Above 2000 nodes the graph is truncated for all downstream readers; centrality, components, dedup, and agent selection silently run on a partial, UUID-order-dependent graph.
- **Proposed:** Raise `_MAX_NODES` to ~8000 (matching the `_MAX_EDGES=10000` mindset) or make both Config-driven, and record truncation in `state.options`.
- **Knob/Change:** `_MAX_NODES` 2000 → Config-driven (e.g. `GRAPH_MAX_NODES=8000`).
- **Impact:** Prevents silent entity/edge loss → more complete graph, truer centrality and agent pool.
- **Risk:** Higher peak memory for in-RAM node/edge lists; bounded, acceptable for a single build.
- **Effort:** small

#### ONTO-4 — Default `social_opinion` template forces 10 types + Person/Org fallbacks even for non-social domains
- **Evidence:** `config.py:165` `ONTOLOGY_TEMPLATE='social_opinion'`; `:168` `ONTOLOGY_AUTO_SELECT=false`. `ontology_generator.py:653-662` injects "exactly 10 entity types" + "last 2 must be Person/Organization"; `_validate_and_process` (`:933-942`) force-adds those fallbacks and can truncate domain types (`:949-965`). The purpose-built `general_forecast` template (`:185-276`) only activates on explicit selection.
- **Current:** Market/geopolitical/product forecasts get shoehorned into a social-media schema; useful types (CentralBank, Regulator, Asset) can be evicted.
- **Proposed:** `ONTOLOGY_AUTO_SELECT=true` so `_auto_select_template` (`:473-487`) routes social questions to `social_opinion` and everything else to `general_forecast` (6-10 demand-driven types, causal edge attributes). Or set `ONTOLOGY_TEMPLATE='general_forecast'` directly.
- **Knob/Change:** `ONTOLOGY_AUTO_SELECT=true` (or `ONTOLOGY_TEMPLATE=general_forecast`).
- **Impact:** Domain-appropriate entity/edge types modeling transmission mechanisms instead of a generic social cast.
- **Risk:** Auto-select keys off bilingual keywords; a borderline question could mis-route, but the empty-result fallback re-runs the default (`:561-583`), so worst case equals today.
- **Effort:** trivial

#### ONTO-5 — Ontology call `max_tokens=4096` can truncate rich-schema JSON → parse retries
- **Evidence:** `ontology_generator.py:548-552` `chat_json(..., max_tokens=4096)`. With `ONTOLOGY_RICH_SCHEMA` on (default true, `:457`), the addendum (`:305-323`) asks for archetype/simulation_tier/role_class/anti_examples per entity AND family/valence/direction per edge, for up to 10+10 — exceeding 4096 on a reasoning model. The 2 logged `chat_json parse failed` warnings are consistent with truncation.
- **Current:** Verbose responses get cut mid-object, fail to parse, retry at lower temperature; partial dicts silently drop malformed entries (`:840-841`).
- **Proposed:** Raise to ~8192 (stage is ~1 min, off critical path). Optionally trim the rich-schema prose when budget is tight.
- **Knob/Change:** `ontology_generator.py:551` (and fallback `:579`) 4096 → 8192.
- **Impact:** Fewer parse-fail retries and fewer dropped types → more complete ontology first attempt.
- **Risk:** Marginally more output tokens on one call; negligible.
- **Effort:** trivial

#### ONTO-6 — No validation that edge `source_targets` reference defined entity types (dangling typed edges)
- **Evidence:** `ontology_generator.py:876-882` validates edges only for presence of `source_targets`/`attributes` and description length; never checks endpoints exist in `result['entity_types']`. Fallback truncation (`:957-958, 973-974`) can remove entity types while edges still point at them; `generate_python_code` (`:1083-1093`) then emits `EDGE_SOURCE_TARGETS` referencing undefined classes.
- **Current:** Typed edge filters in retrieval can silently match nothing.
- **Proposed:** After the entity set is finalized, drop/remap edge `source_targets` whose endpoints aren't in `entity_names` (fall back to a generic Entity or prune), logging remaps — an O(edges) pass alongside `_sanitize_reserved_attrs`.
- **Knob/Change:** Code change in `_validate_and_process` (`:876-882`).
- **Impact:** Self-consistent ontology → typed edge filters reliably resolve, fewer empty typed queries.
- **Risk:** Could prune a few intended pairs; logging makes it auditable.
- **Effort:** small

#### RESEARCH-4 — Synthesis context cap is blind to the actual Gemini provider
- **Evidence:** `deerflow_research.py:171-176` large-caps only minimax/qwen/deepseek; gemini gets the 400k default at `:167`; judging truncates at 60k at `:1380`.
- **Current:** A 1M-token Gemini is treated Claude-class and truncates gathered research during synthesis on large deep runs.
- **Proposed:** Add gemini/antigravity to the large-context branch at `:174`, or make the cap a config value.
- **Knob/Change:** Code at `deerflow_research.py:174` or new `SYNTHESIS_MAX_CONTEXT_CHARS`.
- **Impact:** Fuller evidence retention on deep runs.
- **Risk:** Larger context = slower synthesis; enable when dossiers justify it.
- **Effort:** trivial

#### RESEARCH-6 — Default standard depth single-turn; deep protocol and fanout off
- **Evidence:** `config.py:555` depth default `standard`; `deerflow_research.py:1151-1159` standard = one turn; deep (`:1161-1215`) = six-turn protocol; `RESEARCH_DEEP_FANOUT`/`DEERFLOW_SUBAGENTS` default false.
- **Current:** Standard skips contradiction/quantitative/incentive passes; deep multiplies turns 6x and enlarges the corpus (worsening the graph stage if chunking isn't fixed first).
- **Proposed:** Raise depth toward `deep` for quality **only after** the chunk-size fix; enable `RESEARCH_DEEP_FANOUT` width 4 for multi-actor breadth.
- **Knob/Change:** `DEERFLOW_RESEARCH_DEPTH` standard → deep, `RESEARCH_DEEP_FANOUT` → true, gated behind the chunk-size change.
- **Impact:** Richer contradiction-tested, quantitatively grounded forecast inputs.
- **Risk:** Deep + fanout raise wall-clock and corpus; without the chunk-size fix the graph bottleneck worsens.
- **Effort:** small

#### REPORT-3 — Deterministic simulation signal pack is OFF — sections narrate without a numeric floor
- **Evidence:** `config.py:191` `REPORT_SIGNAL_PACK=false`. `_build_signal_pack` (`report_agent.py:1347-1407`) assembles top actors, per-round volumes + peak, action-type distribution, coalition sizes, world-state P(outcome), scenario diff from deterministic tools; `_prepend_research_background` (`:1339-1342`) would inject it into every section. Off ⇒ `self._signal_pack` stays empty; the prompt's own warning about number-free sections (`:1403-1405`) is never injected. The spine builds its own grounded copy (`:1457-1461`), but section prose is not grounded.
- **Current:** Each section relies on the model to choose to call `simulation_outcomes`/`coalition_map`; sections can omit authoritative aggregates.
- **Proposed:** `REPORT_SIGNAL_PACK=true` to pin the bounded (each sub-block ~800-1800 chars) deterministic block into every section prompt — a citable numeric floor that also reduces wasted exploratory tool calls.
- **Knob/Change:** `REPORT_SIGNAL_PACK` false → true.
- **Impact:** Fewer ungrounded sections, higher citation coverage at the publish gate, fewer redundant retrievals.
- **Risk:** Fixed bounded prefix per section (offset by `REPORT-2`); sub-blocks self-suppress when data is missing.
- **Effort:** trivial

#### REPORT-5 / SIM-14 — `N_FORECAST_SEEDS=1`: single stochastic sample, no calibrated distribution despite cheap graph reuse
- **Evidence:** `config.py:154` default 1; `_maybe_run_seed_ensemble` (`pipeline_orchestrator.py:1932-1948`) returns immediately when `n_seeds<=1`. When >1 it re-runs only prepare→run→report on the **same** `graph_id` (`:1936,1972`) and aggregates via `ensemble.aggregate_forecasts` (`ensemble.py:34`) into mean probability + spread + agreement→confidence (`:1993`). The sim is stochastic (`_RNG` activation, temperature); `SIM_SEED` is injected per-run (`simulation_runner.py:533-536`) for reproducible ensembles.
- **Current:** A single OASIS run yields a point forecast; spread/agreement machinery and frequency-derived scenario probabilities never exercised.
- **Proposed:** `N_FORECAST_SEEDS=3` for full-mode runs. The 5h graph build is **reused**, so marginal cost is only sim+report per extra seed.
- **Knob/Change:** `N_FORECAST_SEEDS` 1 → 3.
- **Impact:** Point estimate → 3-sample distribution with earned (agreement-derived) confidence; materially better robustness/calibration.
- **Risk:** Extra seeds run serially (`:1963`); pair with `SIM-1/2` (shorter rounds), `SIM-3` (concurrency), and `REPORT-1` to bound total time.
- **Effort:** small

#### REPORT-7 — Ensemble matches scenarios by fuzzy name across independently-named seed runs
- **Evidence:** Each seed derives its own spine (`pipeline_orchestrator.py:1972`), so scenario names are model-generated per run; `aggregate_forecasts` buckets solely by `_norm_name` (`ensemble.py:52-55, 18-20`); agreement = `1 - 2*mean_spread` (`:93`). Name drift sends semantically identical scenarios to separate buckets, depressing matched probabilities and confidence.
- **Current:** Identical scenarios across seeds can fail to merge → artificially low agreement and demoted ensemble confidence.
- **Proposed:** Derive the spine once on the primary run and pass its scenario names/criteria into each extra seed's report so every run scores the same MECE list; aggregation then matches 1:1 by construction. Allow an "other/status-quo" residual bucket.
- **Knob/Change:** Code change in `_maybe_run_seed_ensemble`/`_run_one_seed` to share the primary spine (active only when `N_FORECAST_SEEDS>1`).
- **Impact:** Cleaner cross-seed matching, truer agreement — prerequisite for `REPORT-5` to be trustworthy.
- **Risk:** Reduces each seed's freedom to surface a novel scenario; mitigated by the residual bucket.
- **Effort:** medium

#### REPORT-6 — Publish gate penalizes confidence on citation coverage that sim-derived numbers can never satisfy
- **Evidence:** `audit_citation_grounding` (`forecast_extractor.py:334-352`) flags any line with a 2+ digit number/percentage/year (`_NUMBER_RE:331`) as a quantitative claim needing an `[S#]`/`【S#】` marker (`_CITATION_RE:330`). `_apply_publish_gate` (`report_agent.py:1610-1611,1626-1633`) demotes confidence when coverage `< REPORT_PUBLISH_GATE_MIN_COVERAGE` (0.5, `config.py:135`). But `[S#]` markers come only from research sources (`:1318-1327`); the signal pack legitimately cites simulation aggregates that are NOT `[S#]`-backed.
- **Current:** Reports rich in sim numbers (the desired behavior, amplified by `REPORT-3`) show low `[S#]` coverage and get silently demoted — penalizing well-grounded forecasts.
- **Proposed:** Either (a) count simulation-grounding markers (the `模拟量化信号/依据` edge-citation patterns + interview-quote markers) as valid grounding, or (b) restrict the quant audit to lines outside signal-pack/quote blocks. Minimal: exclude lines matching the edge-citation pattern (`依据：A --[REL]--> B`, `report_agent.py:917`) from the unsupported set. Keep source-only coverage as a separate reported metric.
- **Knob/Change:** Code change in `forecast_extractor.audit_citation_grounding`; optional `REPORT_PUBLISH_GATE_MIN_COVERAGE` lower as a stopgap.
- **Impact:** Stops spurious confidence demotion; makes the gate's coverage signal meaningful rather than structurally unsatisfiable.
- **Risk:** Broadening markers could mask genuinely ungrounded numbers — keep source-only coverage separately reported.
- **Effort:** small

#### SIM-5 — Decision channel silently truncates each round's roster to the first 60 agents
- **Evidence:** `decision_channel.py:32` (`active[:60]`), `:119` (`max_active_per_round=60`), `:155` (`list(by_round[rnd].values())[:max_active_per_round]`) — the slice is on insertion order (first-seen `agent_id`), not influence.
- **Current:** With `SIM_DECISION_CHANNEL` on and >60 active agents (plausible with `SIM_AUDIENCE_SIZE>0`), commitments from agents beyond the first 60 are dropped from the modeled WorldState outcome, biasing toward arbitrary low-id agents.
- **Proposed:** Sort active agents by influence (`_agent_weight_map`) and keep top-K before truncating; make K configurable; weight the WorldState step by influence (`commitments_from_decisions` already weights, but only for survivors of the slice).
- **Knob/Change:** Replace hard 60 with `SIM_DECISION_MAX_ACTIVE` (default 60) + influence-ordered selection.
- **Impact:** Removes a systematic outcome bias; ensures consequential actors are never dropped.
- **Risk:** Larger K increases per-round prompt size; influence-ordering changes which agents are modeled (intended).
- **Effort:** small

#### SIM-8 — Elites-only cast: silent-majority audience disabled, removing population dynamics
- **Evidence:** `simulation_config_generator.py:1707-1714` `_generate_audience_agent_configs` returns `[]` when `SIM_AUDIENCE_SIZE<=0` (default); named cast capped at `OASIS_MAX_AGENTS=80` (`simulation_manager.py:326-401`). Audience agents are generated programmatically with NO LLM/Zep calls (`:1736-1788`).
- **Current:** Only ~≤80 high-salience actors; no lurker mass to carry bandwagon/tipping/amplification, so cascades are elite-driven — a fidelity gap for public-reaction forecasting.
- **Proposed:** `SIM_AUDIENCE_SIZE=200-500` with `SIM_AUDIENCE_ACTIVE_CAP` to bound per-round activations, sampled by the researched stance distribution (already implemented).
- **Knob/Change:** `SIM_AUDIENCE_SIZE` 0 → 200-500; zero extra prep LLM cost.
- **Impact:** Adds bandwagon/lurker-tipping realism → better-calibrated public-reaction outcomes.
- **Risk:** More agents raises per-round volume (low `activity_level` keeps activations small); pair with `SIM_AUDIENCE_ACTIVE_CAP` + `SIM-1/2`; raise the `SIM-5` decision cap accordingly.
- **Effort:** trivial

## Robustness & observability

#### GRAPH-9 — `GRAPHITI_OP_TIMEOUT_S=1800` applied to the whole concurrent batch as one operation
- **Evidence:** `runtime.py:97-105` wraps every `self.run()` in `future.result(timeout)` defaulting to `GRAPHITI_OP_TIMEOUT_S=1800` (`config.py:613`). In the concurrent path the entire batch fan-out is a single `self.run(_add_episodes_concurrent)` (`:486`), so the 30-min cap covers all episodes in that batch together (the serial path gives each episode its own budget).
- **Current:** A slow batch (large chunks × high concurrency on a slow model) could approach 1800s and `TimeoutError`-abort the build, where equivalent serial work would not.
- **Proposed:** When raising concurrency/chunk size, pass a scaled explicit timeout proportional to `batch_size`, or keep `batch_size` modest (10). Document that `GRAPHITI_OP_TIMEOUT_S` must exceed expected per-batch wall time.
- **Knob/Change:** `GRAPHITI_OP_TIMEOUT_S` raise if batches grow, or per-call timeout in `add_episodes_concurrent`.
- **Impact:** Prevents spurious `TimeoutError` aborts of large parallel batches.
- **Risk:** Too-low aborts healthy batches; too-high removes the deadlock guard. Only relevant once concurrency/chunk size rise.
- **Effort:** trivial

#### RESEARCH-7 — Deep fanout seed extraction is brittle regex, silently yielding no breadth
- **Evidence:** `deerflow_research.py:1024-1061` `extract_kiqs_from_opening` parses seeds via regex; `run_deep_fanout` (`:1106-1139`) returns empty and warns when nothing matches (`:1118-1120`).
- **Current:** Breadth depends on the opening emitting bullets under actor/KIQ headings; prose or unmatched headings yield zero workers and silent breadth loss.
- **Proposed:** Fall back to `actors.json` cast top-N by influence as fanout seeds instead of returning empty at `:1117-1120`.
- **Knob/Change:** Code change at `deerflow_research.py:1117-1120`; bounded by `RESEARCH_FANOUT_WIDTH`.
- **Impact:** Reliable deep-run breadth when fanout is enabled.
- **Risk:** Depends on actor-extraction ordering; bounded.
- **Effort:** small

#### OBS-1 — Per-stage token/cost/latency telemetry is computed and persisted but never surfaced via API
- **Evidence:** `telemetry.py:98-153` builds `_RunMeter.by_stage/by_model` and `write_run_telemetry`, wired at `pipeline_orchestrator.py:2153-2154` (`set_stage`) and `:3409` (write to `telemetry.json`). Grep of `app/api/` for `telemetry|by_stage|snapshot` returns nothing. The frontend status payload (`ResearchView.vue:437-446`) carries only status/progress/stage; `StageTimeline.vue` renders a bare progress percent.
- **Current:** The rich by-stage rollup is written to disk only at run end; the UI shows a bare bar. During the 5h build an operator has no in-product view of which stage consumes time/tokens/cost.
- **Proposed:** Include `LLMMeter.snapshot(pipeline_id)` (or at least total + by_stage latency/tokens/cost) in `GET /api/research/status/{id}` and render a compact per-stage strip in `StageTimeline.vue`. `snapshot()` is lock-guarded and cheap.
- **Knob/Change:** Pure wiring; gate on `LLM_TELEMETRY_ENABLED` (recommend default true).
- **Impact:** Turns an opaque multi-hour run observable; catches runaway stages live; makes `LLM_RUN_BUDGET_TOKENS/USD` visible.
- **Risk:** Negligible. Note: `_COST_PER_1K` (`telemetry.py:48-63`) has no gemini/proxy entry, so cost shows 0 — add the proxy model or label cost as estimated.
- **Effort:** small

#### SIM-12 — IPC interview and profile-fallback telemetry off — silent report-thinness causes invisible
- **Evidence:** `simulation_ipc.py:36-46` gates round-trip telemetry on `IPC_TELEMETRY_ENABLED` (default false); `summarize` (`:474-544`) computes timeout/p50/p95 only if records exist. Separately, `oasis_profile_generator.py:842-846` silently falls back to generic rule-based personas after 3 failed LLM attempts with only a warning — no counter exposed.
- **Current:** When a report reads thin, there's no signal to distinguish "model chose not to interview" from "interviews timed out", and no metric for how many agents got generic personas (fidelity loss).
- **Proposed:** `IPC_TELEMETRY_ENABLED=true` for sim runs; add a profiles-degraded counter (LLM-success vs rule-fallback) into `run_summary.json` so the report stage can flag persona-fidelity loss.
- **Knob/Change:** `IPC_TELEMETRY_ENABLED` false → true + a counter in `generate_profiles_from_entities`.
- **Impact:** Makes interview health and persona degradation diagnosable.
- **Risk:** Negligible (best-effort JSONL append already guarded).
- **Effort:** small

#### FE-1 — Frontend status poll is fixed 2.5s with no backoff and no pause when the tab is hidden
- **Evidence:** `ResearchView.vue:422` `setInterval(poll, 2500)` with no backoff; `poll()` (`:425-479`) runs until terminal. Other views add fixed timers (`Step4Report.vue:2158-2159`, `Step3Simulation.vue:467-471`, `Step2EnvSetup.vue:825/836`, `MainView.vue:318`). No `visibilitychange` handling anywhere.
- **Current:** During the graph stage (advances ~once per 7-min batch) the client issues ~168 redundant reads per batch (~7000 over 5h), even when backgrounded. Each read hits `getPipelineStatus → PipelineManager.load` (disk JSON) on a serially-bound backend.
- **Proposed:** Adaptive interval (2.5s → 8-15s) when progress/stage are unchanged across N polls, or key the interval to stage; pause on `document.hidden`, resume + immediate poll on `visibilitychange`. The progress-log fetch is already correctly phase-gated (`:430-433`) — apply the same discipline to the status read.
- **Knob/Change:** Frontend code change in the poll loop (ideally a shared `usePolling` composable); optionally a backend-suggested per-stage interval in the payload.
- **Impact:** ~3-6x fewer status requests during multi-hour stages; zero polling while backgrounded.
- **Risk:** Slightly staler UI during slow stages; mitigated by immediate poll on focus and on cancel/resume.
- **Effort:** small

#### ATOMIC-1 — `atomic.py` fsyncs on every write — unnecessary cost on high-churn status/progress writes
- **Evidence:** `atomic.py:24-30` does mkstemp + write + flush + `os.fsync` + `os.replace` on every call; 63 call sites across status, manifest, telemetry, ledger, project, simulation.
- **Current:** Every state/status/progress write blocks on fsync. Right for durable artifacts; wasteful for transient per-batch progress (a lost last update is recovered on the next write).
- **Proposed:** Add `fsync=True` default param to `write_text_atomic`/`write_json_atomic`; pass `fsync=False` at high-frequency status/progress call sites. `os.replace` stays atomic, so readers never see a torn file.
- **Knob/Change:** New optional `fsync` param (default true preserves behavior); `fsync=False` at progress/status sites only.
- **Impact:** Removes fsync stalls from the hot status path; minor wall-time + SSD-wear win, especially if status write frequency rises.
- **Risk:** A crash could lose the most recent non-fsynced status — harmless since status is continuously overwritten. Keep fsync for ledgers/manifests/telemetry.
- **Effort:** small

#### EVAL-1 / GAP-4 — Eval/calibration harness is offline-only; no closed feedback loop validates the quality changes
- **Evidence:** `eval_forecast_quality.py:148-291` computes deterministic `objective_signals` (groundedness/citation density) and a K-pass LLM judge, gated by `EVAL_ENABLED` (`:333`), invoked only via CLI (`cmd_score/cmd_run:341/357`). Nothing runs it at pipeline completion; no link to `forecast_ledger`. `services/backtest.py` and `services/forecast_ledger.py` exist but are default-off/under-wired. The publish gate (`audit_citation_grounding`) is the only quality signal and is structurally biased (`REPORT-6`). All accuracy findings (`REPORT-3/5/6/7`, `SIM-5/8/14`) are argued from mechanism, not measured.
- **Current:** Forecast quality/calibration is measurable only on manual demand; calibration drift across prompt/model changes is invisible; the quality investments cannot be ranked by evidence.
- **Proposed:** On report completion, run the **free deterministic** `objective_signals` inline and persist alongside the forecast; trigger the K-pass judge when `EVAL_ENABLED`; feed scores into `forecast_ledger` so a lightweight backtest scores resolved questions over time. Turn on `LLM_TELEMETRY_ENABLED`/`IPC_TELEMETRY_ENABLED`.
- **Knob/Change:** Wire `objective_signals()` into the report stage; gate the live judge on `EVAL_ENABLED`; wire `forecast_ledger` persistence; telemetry flags → true.
- **Impact:** Converts the audit from one-shot guesswork into an eval-driven loop — prerequisite for trusting the ensemble/decision-channel investments rather than shipping them blind.
- **Risk:** Deterministic signals are free; the LLM judge adds K calls (keep behind `EVAL_ENABLED`); calibration needs resolved labels (start by logging, score later).
- **Effort:** medium

#### BATCH-1 / CACHE-1 — Shared-graph / shared-simulation fork machinery (largest cost amortization) is dormant
- **Evidence:** `batch_runs.py:237-366` `fork_question` reuses `base_state.graph_id`, marks research+graph completed (`:293-298`), and with `shared_simulation=True` marks prepare+run completed pointing at `base.simulation_id` (`:316-327`) so only REPORT runs; `start_batch` (`:374+`) runs the anchor, `_wait_for_graph` (`:191-229`), then forks the rest. Exposed only via CLI `--shared-simulation` (`:813`).
- **Current:** Every question pays its own ~5h graph build (and full OASIS run) unless someone manually drives `batch_runs --shared-simulation`. Multi-question/ensemble/scenario-sweep workloads re-do the most expensive stage per question.
- **Proposed:** Route multi-question and ensemble workloads through `fork_question`: one anchor builds research/ontology/graph (and optionally one simulation); siblings reuse `graph_id` and re-run only ontology→report (or report-only under `shared_simulation`). Surface beyond CLI (e.g. an `/api/research` batch endpoint) and use it for `N_FORECAST_SEEDS>1`. Pairs with `LLM_CACHE_ENABLED` for shared-anchor call reuse.
- **Knob/Change:** Existing `fork_question(..., shared_simulation=True)`; needs wiring + docs to become the default path for related-question batches.
- **Impact:** For an M-question batch, collapses M graph builds (and optionally M sims) into 1 — ~5h × (M-1) saved on this run.
- **Risk:** Shared graph is correct for same-event questions, wrong for unrelated prompts (`fork_question` re-generates ontology/persona per question, `:284-298`); `shared_simulation` trades per-question fidelity for ~Nx savings — keep opt-in for tightly related questions.
- **Effort:** small

## Phased roadmap

Sequence chosen so the cheap, high-leverage levers land first and each phase exposes the next binding resource before adding complexity (`CORRECTION-3`: re-profile after each phase).

**Phase 0 — config-only quick wins (minutes of effort, no code).** Flip defaults and ramp the concurrency knobs while watching p95 latency (`CORRECTION-2`):
- Chunk size: `DEFAULT_CHUNK_SIZE=2500`, `DEFAULT_CHUNK_OVERLAP=250` (merged `GRAPH-2`/`RESEARCH-1`/`ONTO-1`/`CHUNK-1`)
- Graph: `GRAPH_BUILD_CONCURRENCY=4` + `GRAPH_RESOLVE_ENTITIES=true` (`GRAPH-1`,`GRAPH-4`); `GRAPHITI_MAX_COROUTINES=16` (`GRAPH-3`)
- Report: `REPORT_SECTION_CONCURRENCY=3`, `REPORT_NATIVE_TOOLS=true`, `REPORT_SECTION_CONTEXT_MODE=brief`, `REPORT_SIGNAL_PACK=true` (`REPORT-1/2/3/4`)
- Sim: `OASIS_DEFAULT_MAX_ROUNDS=36` (`SIM-2`); `OASIS_SEMAPHORE` 16→24 (`SIM-3`); `PROFILE_ZEP_SKIP_WHEN_CONTEXT=true` (`SIM-7`)
- Infra: `LLM_CACHE_ENABLED=true` (`CACHE-1`/`SIM-9`); `LLM_TELEMETRY_ENABLED`/`IPC_TELEMETRY_ENABLED=true` (`OBS-1`,`SIM-12`); `ONTOLOGY_AUTO_SELECT=true` (`ONTO-4`); `ONTOLOGY` max_tokens→8192 and `MAX_TEXT_LENGTH_FOR_LLM→120000` (`ONTO-5`,`ONTO-3`, tiny code consts)

**Phase 1 — parallelism + the real multiplier (small code).** Attack per-call latency and remaining serial stages:
- `GAP-1` model_size→tier mapping + `LLM_TIERED_ROUTING=true` with a fast non-reasoning `LLM_FAST_MODEL` (the single biggest lever)
- `GAP-2` embedding LRU; `GRAPH-9` scaled batch timeout; `GRAPH-6` raise `_MAX_NODES`
- `SIM-4` parallel config-gen; `SIM-11` raise persona parallelism; `SIM-13` throttle run_state writes
- `REPORT-9` intermediate-turn token cap; `REPORT-10` honor `REPORT_AGENT_TEMPERATURE`; `REPORT-8` drop interview from the nudge
- `RESEARCH-2` graph-from-dossier; `FE-1` adaptive polling; `ATOMIC-1` optional fsync

**Phase 2 — quality machinery on (small/medium).** With latency tamed, turn on accuracy levers and fix structural biases:
- `N_FORECAST_SEEDS=3` (`REPORT-5`/`SIM-14`) + `REPORT-7` shared-spine scenario set
- `SIM-1` wire convergence early-stop; `SIM-8` audience agents + `SIM-5` influence-ordered decision roster
- `REPORT-6` fix the publish-gate citation bias; `ONTO-6` validate edge endpoints; `RESEARCH-6` deep depth/fanout (now affordable) + `RESEARCH-7` fanout fallback; `RESEARCH-4` Gemini context cap

**Phase 3 — architectural / eval-driven loop (medium).** Amortize cost and close the feedback loop:
- `BATCH-1` shared-graph/shared-simulation forking as the default multi-question/ensemble path
- `GRAPH-7` native `add_episode_bulk` path (+ `GRAPH-10` batched embeddings); `GRAPH-8` batched seeding; `GRAPH-11` bounded retry budget
- `EVAL-1`/`GAP-4` wire `objective_signals` + `forecast_ledger` + backtest so every quality change becomes measurable; `SIM-6` parallel decision channel; `SIM-10` bounded agent-dynamics memory; `RESEARCH-5` schema/input bounding; `RESEARCH-8` in-subprocess research cache


---

# Round 2 — Deeper Refinements (speed · quality · detail · realism · comprehensiveness)

## Round 2 executive summary

Round 1 was a latency-weighted catalog of **config flips** that count-reduce or parallelize the graph build. It deliberately stopped at the knob surface and under-explored five things that decide whether the run clears the Bridgewater bar even after every Phase-0 flip lands:

1. **Whether the parallelism knobs can physically take effect.** They cannot today. `GRAPHITI_MAX_COROUTINES` and `GRAPH_BUILD_CONCURRENCY` both dispatch blocking LLM POSTs to CPython's *shared default* `ThreadPoolExecutor(min(32, cpu+4))` ≈ 20 workers on this host (`R2-EXEC-1`), and embedding `encode` steals from the same 20 (`R2-EXEC-2`). Round 1's "4-16x" is hard-capped to ~2.5x until a dedicated I/O executor exists. **This is the single most important round-2 correction to round 1.**
2. **The forecast-calibration machinery.** The authoritative "spine" is **one** temperature-0.2 LLM draw (`R2-CAL-1`); the ensemble averages probabilities **arithmetically** (under-confident pooling, `R2-CAL-2`); there is **no probability floor** (0% is assignable → catastrophic log-loss, `R2-CAL-4`); measured calibration error feeds back **only as a printed sentence** (`R2-CAL-5`); and there is **no object at all** for the headline deliverable — *10+ independent binary forecasts* (`R2-CAL-6`/`R2-DETAIL-1`). Output is still low/medium/high, never a numeric interval (`R2-CAL-17`).
3. **Simulation causal fidelity.** The decision channel — *the only outcome model* — is fed a static actor roster and **nothing the agents actually simulated** (`R2-SIM-1`); stance is a frozen label so "support fell to Y%" measures voice-share not conversion (`R2-SIM-4`); the follow graph is frozen at round 0 so echo chambers are an *input* not an emergent outcome (`R2-SIM-7`).
4. **The KG causal-reasoning surface the 7h build paid for.** Multi-hop traversal returns bare edge **names** — no sign, strength, or lag (`R2-KG-1`/`R2-KG-3`) — so the report narrates cascades it cannot actually evaluate; traversal is also exact-name-matched (silently empty, `R2-KG-2`), time-flattened (phantom cascades, `R2-KG-4`), and never pinned into the signal pack (`R2-KG-7`). Centrality is degree-only, missing the chokepoints that "flip the outcome" (`R2-KG-5`).
5. **Research evidence-quality plumbing.** The research-quality scorecard and its gate are **fully dead code** (no producer, floor=0, `R2-RES-1`); Track B (the *primary* extraction seed) **never fans out per-actor** so profiles are label-depth (`R2-RES-2`); contradictions are narrated but never priced into confidence (`R2-RES-5`).

**Three hard preconditions (state, don't offer):**
- **`SIM_DECISION_CHANNEL` is OFF by default**, so the spine's "signal pack" carries only activity volumes (most-active agent, post counts, coalition sizes) and **zero modeled outcome**. The entire `R2-SIM-1`/`R2-CAL-3` thread is moot unless the channel is turned on — treat it as a required flag for any forecast run, not an option.
- **The base-rate anchor cannot fire as written.** Research scenarios store probability as `probability_band` (a string range, `actors.py:78`), but `world_state_seed_from_actors` only reads `probability`/`likelihood`/`base_rate`/`prob` (`actors.py:1559`) — so the outside-view prior is *always* discarded for the standard schema and the WorldState falls back to a **uniform** prior (`R2-CAL-13`/`R2-SIM-9`). Parsing `probability_band` is the cheapest, highest-leverage calibration lever in this list.
- **The sharpest researched numbers never anchor the forecast.** `quantitative_facts` (hard figures with `as_of_date` + S-tier) are injected into section *prose* (`report_agent.py:1168`) but **not** into the spine (only `forecast_inputs_block` is, `:1451/:1465`), so the load-bearing object is built from prose-y base rates + activity volumes, not the graded hard data (`R2-CAL-16`).

**The five highest-leverage quality/realism levers (do these for the Bridgewater bar):** (a) the binary-forecast machinery `R2-CAL-6` — the headline deliverable is *structurally impossible* today; (b) `R2-SIM-1` + `R2-CAL-3` + the `probability_band` fix `R2-CAL-13` — couple the decision channel to real sim state, anchor the spine on WorldState shares, make the anchor non-uniform; (c) `R2-CAL-2` extremized log-odds pooling + `R2-CAL-1` spine self-consistency — the cheap structural sharpening of the number; (d) `R2-CAL-4` probability floor + `R2-CAL-5` closed recalibration loop — never-say-never plus earned confidence; (e) `R2-RES-2` per-actor dossier fan-out + `R2-CAL-16` quant-facts-into-spine — set the differentiation ceiling with researched, graded depth.

**The three best advanced-latency levers (distinct from round-1 config flips):** (i) `R2-EXEC-1`/`R2-EXEC-2` dedicated I/O + compute executors — *unlocks* the parallelism round 1 only nominally promised; (ii) `R2-CAL-1` spine self-consistency (K cheap LLM calls, **no** graph/sim rerun) to get a calibrated distribution at a fraction of `N_FORECAST_SEEDS` wall-clock; (iii) `R2-EXEC-8` batch/parallelize the post-sim decision-channel elicitations (independent given the frozen action log) and `R2-EXEC-10` aggregate the audience tail into one weighted "public" block so 200-500 lurkers cost one elicitation, not 500.

## Round 2 — Top picks by dimension

| Dimension | Top refinement | ID | Effort |
|---|---|---|---|
| **Latency** | Dedicated LLM I/O executor — round-1 concurrency knobs are nullified by the shared `cpu+4` default pool | `R2-EXEC-1` | small |
| Latency | One continuous fan-out instead of 40 per-batch barriers (slowest-of-10 tax) | `R2-EXEC-3` | medium |
| **Quality** | Intra-run spine self-consistency ensemble (K cheap draws, no sim rerun) | `R2-CAL-1` | small |
| Quality | Extremized log-odds (geometric-odds) pooling, not arithmetic mean | `R2-CAL-2` | medium |
| **Realism** | Couple the decision channel to actual sim state (posts, WorldState shares, affect) | `R2-SIM-1` | medium |
| Realism | Seed WorldState from `probability_band` + allow abstention (kills the uniform prior) | `R2-CAL-13`/`R2-SIM-9` | small |
| **Detail** | A `forecast_register` of 10-15 independent calibrated binary forecasts | `R2-CAL-6`/`R2-DETAIL-1` | medium |
| **Comprehensiveness** | Per-actor dossier fan-out in Track B (the primary extraction seed) | `R2-RES-2` | medium |
| Comprehensiveness | Deterministic sign+lag causal spine pinned into every section | `R2-KG-7` | medium |
| **Accuracy** | Anchor spine probabilities directly on WorldState shares (anchor-and-adjust on the model) | `R2-CAL-3` | medium |
| Accuracy | Probability floor (`p_min≈0.03`) — never assign 0% | `R2-CAL-4` | trivial |

## Realism (simulation fidelity)

### R2-SIM-1 — Decision channel (the only outcome model) is causally decoupled from the simulation it summarizes
- **Builds on:** round-1 `SIM-6` (parallelizing the channel) — this is the *correctness* counterpart.
- **Evidence:** `decision_channel.py:25-43` `_build_round_decision_prompt` feeds the LLM only `{agent_id, name, stance, influence}`; `:46-61` the elicitation passes no post content, no affect, no WorldState shares; `run_parallel_simulation.py:2884-2897` calls `run_decision_channel` with `_acts=_read_actions_for_decision_channel` (only round/agent_id/agent_name). `worldstate.py:90-120` `step()` evolves shares purely from these re-imagined commitments.
- **Current:** Each round the LLM sees a *static cast list* and is asked, in the abstract, which scenario each actor "commits" to. Nothing the agents posted, liked, escalated, or how mood/opinion evolved enters the prompt — the modeled outcome trajectory is an LLM re-imagination of a roster, so the 7h graph + multi-round sim contribute **zero** causal signal to the number. **Precondition:** `SIM_DECISION_CHANNEL=true` (off by default), else this whole thread is inert and the spine sees only activity volumes.
- **Proposed:** Make elicitation a function of the round's real state: (1) inject each active agent's posts/comments for that round (`action_args.content` in `actions.jsonl`); (2) inject current `WorldState.shares` so commitments are path-dependent (momentum/bandwagon); (3) inject affect from `AgentDynamicsTracker.get_state` (mood/opinion_strength/fatigue). Pass `dynamics_tracker` + the per-round action map into `run_decision_channel` instead of a flattened action list.
- **Knob/Change:** Code in `decision_channel._build_round_decision_prompt` + `run_parallel_simulation` wiring; keep the flag but make it the default-on path for forecast runs.
- **Impact:** Converts the forecast from "LLM guesses the outcome from a cast list" to "outcome emerges from simulated stakeholder behavior" — the entire premise of the sim.
- **Risk:** Larger per-round prompt; bound injected post text and weight by content relevance so noise doesn't drive the outcome.
- **Effort:** medium

### R2-SIM-2 — Outcome commitments are weighted by social-media voice, not power over the outcome variable
- **Builds on:** NEW.
- **Evidence:** `decision_channel.py:82-93` `_agent_weight_map` reads `influence_weight`; `worldstate.py:33-62` sets weight = `influence_weight × confidence`. **CORRECTION (vs the raw finding):** for *matched/named* actors `influence_weight` is **overwritten by researched clout** (`simulation_config_generator.py:1490-1492`), so the entity-type 0.8-3.0 social scale (`:1395-1398/:1529-1612`) actually governs only **unmatched + audience** agents. The defect is real but narrower: voice-salience, not lever-control, still weights the outcome, and loud media/activists with high researched clout sit on the same axis as a state actor controlling export controls.
- **Current:** Whoever is salient on the feed moves the modeled outcome as much as whoever actually decides it; the audience tail is scored purely on the type scale.
- **Proposed:** Add a distinct `outcome_power`/`decision_leverage` field (control of policy/capital/supply/votes) sourced from `entity_simulation_tier`/`salience_score` (already computed at `simulation_manager.py:373-385`, used only for ranking). Use `outcome_power` for `commitments_from_decisions`; keep `influence_weight` for activation/visibility. Surface the weights in the appendix.
- **Knob/Change:** New `outcome_power` field (default = `influence_weight`) + decision-channel weight-map switch.
- **Impact:** Sharpens the outcome toward decisive actors; prevents loud media + silent-majority audience from swamping principals.
- **Risk:** Mis-scored power skews the outcome — falls back to `influence_weight` when absent; audit weights.
- **Effort:** medium

### R2-SIM-3 — Generated persona incentives/values/beliefs are requested then silently dropped
- **Builds on:** NEW.
- **Evidence:** `oasis_profile_generator.py:983-986` the persona prompt explicitly requests `values/beliefs/incentives {driver, gains_if, loses_if}`, but `OasisAgentProfile` (`:37-67`) has no fields for them and extraction (`:287-300`) pulls only bio/persona/karma/age; `AgentActivityConfig` (`simulation_config_generator.py:167-199`) likewise has none. The single most forecast-relevant signal — each actor's payoff under each outcome — survives only as prose inside the persona free-text.
- **Current:** Generated agents contribute no behavioral DNA; their "commitment" to a scenario can't be derived from payoff (only research-`actors.json` agents carry it via `behavioral_dna_block`).
- **Proposed:** Add `incentives/values/beliefs` to `OasisAgentProfile` + `AgentActivityConfig`, extract in `_generate_profile_with_llm`, persist into `reddit_profiles.json`/agent configs, and feed `gains_if/loses_if` into the decision-channel elicitation (`R2-SIM-1`) so an agent commits to the scenario that maximizes its stated payoff.
- **Knob/Change:** Schema + extraction change; reuses the existing `PERSONA_BEHAVIORAL_DNA` flag.
- **Impact:** Scenario commitments become economically grounded rather than vibe-based — truer faction behavior under stress.
- **Risk:** Slightly larger profiles; the prompt already says "don't invent, omit if unknown."
- **Effort:** medium

### R2-SIM-4 — Stance is static: `final_stance_share`/`stance_trajectory` measure voice-share of frozen labels, not opinion change
- **Builds on:** round-1 `SIM-1` (convergence-stop reads the same trajectory).
- **Evidence:** `_load_stance_by_agent` (`run_parallel_simulation.py:1701-1710`) reads each agent's configured stance **once**; `_score_stance_trajectory:1775-1781` buckets every speech action by that frozen stance; `final_stance_share:2088-2092` is just which static bucket spoke most last round. `AgentDynamicsTracker` tracks mood/opinion_strength but they never map back to an effective stance.
- **Current:** "Support fell from X% to Y%" is an artifact of which pre-labeled bucket spoke late, not of anyone changing their mind — a supportive agent dunked on for 20 rounds is still "supportive." Opinion *conversion* literally cannot be shown.
- **Proposed:** Derive a per-round **effective stance** from the dynamics tracker (`effective_stance = configured if opinion_strength high, else drift toward mood sign`) so collapsing mood/weak opinion can flip buckets; recompute `stance_trajectory` from it and persist per-round.
- **Knob/Change:** Code in `_score_stance_trajectory` + an `effective_stance()` helper in `agent_dynamics`; gate on `SIM_AGENT_DYNAMICS` (default-off keeps byte-identical behavior).
- **Impact:** Turns the headline "support share" into a real opinion-conversion curve — the difference between a chatter sim and a predictive one.
- **Risk:** Couples metrics to dynamics; needs the tracker available at scoring time.
- **Effort:** medium

### R2-SIM-5 — Affective dynamics are near-inert in practice (high thresholds, global learning rates, MBTI/archetype unused)
- **Builds on:** round-1 `SIM-10` (the same module's memory growth).
- **Evidence:** `agent_dynamics.py:170-188` `state_line` returns `''` unless mood≥0.4 OR opinion_strength≥0.7 OR fatigue≥0.6; with `mood_lr=0.25` through `_squash` (`:76-79`) and net valence ±1-2, mood gains ~0.08-0.15/round, so 0.4 needs ~4-6 consistent rounds. In a convergence-stopped ~20-round sim most agents never cross threshold → nothing injected. Learning rates are global (`from_config:133-139`); MBTI/archetype/risk preference never modulate volatility.
- **Current:** For most agents/rounds `_inject_agent_dynamics` injects nothing — the "dynamics" feature changes almost no behavior, the exact static-persona failure it was built to prevent.
- **Proposed:** (1) Emit a graded `state_line` from ~0.2 with intensity adverbs; (2) heterogeneous per-agent learning rates (scale `mood_lr` by trait volatility: high-N/activist → higher, institution/central-bank → lower; `opinion_lr` by conviction); (3) seed `fatigue_rate` from `activity_level`. Derive in `from_config` from fields already present.
- **Knob/Change:** Tune `SIM_DYNAMICS_*` defaults + per-agent LR derivation; gate on `SIM_AGENT_DYNAMICS`.
- **Impact:** Escalation, outrage-fatigue, and capitulation actually fire → differentiated faction trajectories instead of flat repetition.
- **Risk:** Too-low thresholds add prompt noise — bound line length; clamp LR range so volatile agents don't thrash.
- **Effort:** medium

### R2-SIM-6 — No directional position memory — `opinion_strength` is signless, so agents flip-flop at zero consistency cost
- **Builds on:** NEW.
- **Evidence:** `agent_dynamics.py:158-161` hardens a scalar `opinion_strength` on **any** attention (pos+neg+engage) with no record of *which* side; `state_line:182-183` renders "立场已明显强化" without direction; the per-round `LLMAction` (`run_parallel_simulation.py:2429`) carries no "you already committed to X" anchor.
- **Current:** An agent can post pro-A then pro-B with no penalty, and `opinion_strength` even rewards getting *attacked* (engagement) by hardening an undirected scalar. Real stakeholders pay to reverse public positions.
- **Proposed:** Track a per-agent position anchor (last stated scenario/stance + round); when `opinion_strength` is high and a reversal is considered, inject a consistency note ("你已公开表态X；反复立场会损害可信度/资源") and feed the anchor into the decision channel so reversals need strong contrary pressure.
- **Knob/Change:** New `SIM_POSITION_ANCHOR` flag (default on with dynamics).
- **Impact:** Realistic hysteresis and durable factions; kills the round-to-round oscillation that washes out a clean signal.
- **Risk:** Over-freezing — tie reversal cost to `opinion_strength` so weak positions still flip.
- **Effort:** medium

### R2-SIM-7 — Homophily/follow graph is frozen at round 0 — no rewiring as polarization develops
- **Builds on:** round-1 `SIM-1`.
- **Evidence:** Echo-chamber + relationship follows are built once (`simulation_config_generator._build_echo_chamber_follows:752-831`, `_build_initial_follows:590-637`) and injected at round 0 (`run_parallel_simulation.py:1493-1546`); the round loop (`2392-2438`) never adds/removes edges despite MUTE/FOLLOW being real OASIS actions.
- **Current:** Topology is static — polarization cannot structurally self-reinforce and bridges never break under conflict, so cross-stance exposure stays at its initial level regardless of how hostile discourse becomes. Community structure is pre-baked, not emergent.
- **Proposed:** Every K rounds, rewire from observed interactions (`agent_dynamics.extract_round_signals` + the follow DB): drop/mute after repeated negative cross-stance dunks, add follows after positive same-stance interactions, via `add_edge`/`remove` on `env.agent_graph`.
- **Knob/Change:** New `SIM_DYNAMIC_HOMOPHILY` + `SIM_REWIRE_EVERY_K` (default off).
- **Impact:** Polarization and faction crystallization emerge endogenously — the core opinion-dynamics realism needed for tail scenarios.
- **Risk:** Runaway fragmentation — cap edges added/removed per round, protect bridge hubs; mid-sim DB writes.
- **Effort:** large

### R2-SIM-8 — Lexicon sentiment is a 40-word bag with no negation handling, polluting polarization/net_sentiment on policy text
- **Builds on:** NEW.
- **Evidence:** `_lexicon_sentiment` (`run_parallel_simulation.py:1713-1722`) scores ±1 per substring over ~40 CN/EN words (`_POS/_NEG_LEXICON:1681-1698`); "AI is not a bubble" scores negative, "no growth" scores positive. Feeds `net_sentiment` and `polarization_index` (`:1811-1816`) — numbers the report cites.
- **Current:** Polarization/net-sentiment are computed by a brittle keyword counter on nuanced policy discourse; they invert sign on negated statements.
- **Proposed:** Add negation windowing (flip polarity if a negator precedes within N tokens) and use the agent's effective stance (`R2-SIM-4`) as the primary signal with lexicon as tiebreak; optionally a cheap embedding/LLM stance classifier on a sampled, cached subset. Stamp `sentiment_method`.
- **Knob/Change:** New `SIM_SENTIMENT_METHOD` (lexicon|negation|llm; default lexicon).
- **Impact:** Cleaner polarization/sentiment numbers — the report's quantitative claims stop being keyword artifacts.
- **Risk:** LLM scoring adds cost — sample, don't score every post.
- **Effort:** small

### R2-SIM-12 — WorldState inertia is a fixed 0.7 regardless of how much calendar time a round represents
- **Builds on:** NEW.
- **Evidence:** `worldstate.py:73-76` inertia defaults 0.7; `decision_channel.run_decision_channel:116` takes a constant inertia (`SIM_DECISION_INERTIA`). `round_to_date` is computed (`run_parallel_simulation.py:2896`, `_build_round_to_date`) and passed for stamping but never used to scale inertia, though `minutes_per_round` varies 30-120 and horizons 24-168h.
- **Current:** A 30-minute round and a week-long round both blend 70% prior / 30% new, so the outcome moves the same amount per step regardless of elapsed time — mismodeling the rate of change and the time-to-converge diagnostic.
- **Proposed:** Scale inertia by the calendar delta per round (more real time ⇒ lower inertia ⇒ more change), using `round_to_date`; scale `conv_eps` so `converged_at` is in calendar terms. Clamp to [0.3, 0.95].
- **Knob/Change:** Code in `decision_channel` to derive per-step inertia; keep `SIM_DECISION_INERTIA` as the base.
- **Impact:** Time-to-settle and rate of movement become physically meaningful → better horizon-dated forecasts.
- **Risk:** Needs a sane delta→inertia curve; low blast radius (default-off channel).
- **Effort:** small

### R2-SIM-13 — Named-agent heterogeneity collapses into 5 rigid type-templates with identical numeric bands
- **Builds on:** NEW.
- **Evidence:** `_generate_agent_config_by_rule` (`simulation_config_generator.py:1523-1612`) maps entity type → fixed activity/influence/delay tuples (all Students 0.8/0.8, all Universities 0.2/3.0); the LLM prompt (`:1395-1398`) gives tight per-type bands. **CORRECTION:** `influence_weight` is then overwritten by researched tier (`:1490-1492`), so the *behavioral* params (activity/posts/delay), not influence, are what's near-identical within a type; only audience agents get RNG jitter (`:1741-1763`).
- **Current:** Twenty "students" behave identically on activity/cadence; factions are monolithic blocks, not distributions with moderates, hardliners, and defectors — the heterogeneity that drives realistic tipping.
- **Proposed:** Add seeded per-agent jitter to `activity_level`/`posts_per_hour`/`response_delay` and a conviction draw around the type mean (deterministic via `SIM_SEED`); let MBTI/archetype shift the mean. Keep researched influence/tier authoritative — vary only behavioral params.
- **Knob/Change:** Code in `_generate_agent_config_by_rule`; reuse `SIM_SEED` RNG.
- **Impact:** Distributional factions enable minority-cascade and defection dynamics → credible tipping-point forecasts instead of block-vote artifacts.
- **Risk:** Too much jitter drowns the researched signal — keep variance modest and seeded.
- **Effort:** small

### R2-SIM-14 — Exogenous scheduled-event shocks never touch the outcome distribution
- **Builds on:** NEW (critic blind spot).
- **Evidence:** `events_to_schedule` is generated by config-gen and surfaced to agents, but grep shows no path from a scheduled event into `worldstate.step` or `decision_channel` — the WorldState evolves only from re-imagined agent commitments (`worldstate.py:90-120`). A scheduled "BIS rule on 2027-03" shock cannot perturb the modeled distribution.
- **Current:** Known forward catalysts (an election, an export-control deadline, an earnings print) influence agent *chatter* but apply no structured shock to the outcome trajectory, so the modeled distribution is endogenous-only.
- **Proposed:** Map each scheduled event to a dated, signed nudge on the relevant scenario share inside `worldstate.step` (e.g. at the round whose `round_to_date` brackets the event, add a bounded base-rate-pull toward the event's implied outcome), tagged so it's auditable. Reuse `round_to_date` (`R2-SIM-12`).
- **Knob/Change:** New `SIM_EVENT_SHOCKS` flag; code in `decision_channel`/`worldstate`.
- **Impact:** Lets the forecast price scheduled catalysts as discrete jumps rather than diffuse chatter — directly improves dated-binary timing.
- **Risk:** Over-large shocks could dominate — cap magnitude and require an event→scenario mapping with a rationale.
- **Effort:** medium

## Quality & accuracy (forecast calibration)

### R2-CAL-1 — The authoritative spine is a single stochastic draw — add an intra-run self-consistency ensemble (no sim rerun)
- **Builds on:** round-1 `REPORT-5`/`SIM-14` (`N_FORECAST_SEEDS`).
- **Evidence:** `forecast_extractor.py:180-184` `derive_forecast_spine` issues exactly **one** `chat_json` at temperature 0.2; `report_agent.py:1465-1477` pins that single draw into every section as 权威. `N_FORECAST_SEEDS` (`config.py:154`) reruns the **entire** sim+report to diversify — the spine within a run is one sample.
- **Current:** The number every section must defend (and that lands in `forecast.json`/ledger) is one temp-0.2 draw — systematically overconfident and order-sensitive; the cheap variance source is never sampled.
- **Proposed:** Call `derive_forecast_spine` K=5 times (temp 0.5-0.7) sharing **one** fixed scenario-name set (derive names once, re-elicit only probabilities + rationale), then `ensemble.aggregate_forecasts` over the K spines → `mean_probability` as the pinned spine and spread→confidence. Marginal cost = K cheap LLM calls, **no graph/sim rerun**.
- **Knob/Change:** New `REPORT_SPINE_SELFCONSISTENCY_K` (default 5; 1 reproduces today); code in `_derive_and_pin_forecast_spine`. Degrade to single draw on failure.
- **Impact:** Removes single-draw overconfidence on the load-bearing number for a few cheap calls; gives an earned spread/CI even when `N_FORECAST_SEEDS=1` — the cheapest distribution in the pipeline.
- **Risk:** K extra ~5s calls per report; bounded.
- **Effort:** small

### R2-CAL-2 — Ensemble averages probabilities arithmetically — switch to extremized log-odds pooling
- **Builds on:** NEW.
- **Evidence:** `ensemble.py:80-83` aggregates via arithmetic mean (`mean_probability/total`); `_mean` (`:23-24`) is plain arithmetic; agreement at `:89-93`. No logit/geometric/extremize anywhere in `app/services` (grep empty).
- **Current:** Arithmetic averaging is the known *under-confident* aggregator — it pulls pooled probability toward 0.5 and washes out shared signal, the exact error extremized log-odds pooling corrects. It is the difference between a sharp number and a mushy one.
- **Proposed:** Aggregate in logit space: `p_agg = sigmoid(a · mean(logit(clip(p_i, 0.02, 0.98))))` with extremizing `a≈2.0`. Apply in `aggregate_forecasts` and in any seed/self-consistency merge; keep arithmetic mean as a reported diagnostic.
- **Knob/Change:** New `ENSEMBLE_EXTREMIZE_A` (default 2.0) + log-odds code in `ensemble.py`.
- **Impact:** Sharper, better-calibrated pooled probabilities; corrects structural under-confidence across seeds and `R2-CAL-1` draws.
- **Risk:** Over-extremizing inflates confidence — gate `a` by historical ECE (`R2-CAL-5`) and clip extremes.
- **Effort:** medium

### R2-CAL-3 — WorldState P(outcome) is downgraded to prose and re-guessed — anchor spine probabilities directly on the shares
- **Builds on:** NEW.
- **Evidence:** `decision_channel.py:131-174` builds a resource-weighted, base-rate-seeded, convergence-tracked WorldState (`worldstate.py:90-138`) — the most rigorous quantitative object in the pipeline. `report_agent.py:1409-1434` renders its shares as **text** ("· name: 47%") into the signal pack; `forecast_extractor.py:129-189` then lets the spine LLM freely invent its own scenario names/numbers, merely told to treat them "为主锚." Nothing constrains the spine scenario set to the WorldState set or the spine prob to the modeled share.
- **Current:** The simulated, base-rate-seeded distribution is reduced to a string and reinterpreted by one LLM draw that can rename scenarios and pick arbitrary numbers — the model output is decorative, not load-bearing.
- **Proposed:** Pass `WorldState.outcome()['shares']` as a structured `base_distribution` into `derive_forecast_spine`; constrain the spine scenario set to the WorldState/`forecast_inputs` names (`actors.py:1542-1570`); require each spine probability stay within ±band (e.g. ±15pp) of its modeled share unless `adjustment_rationale` justifies the deviation (anchor-and-adjust on the **model**, not on the LLM's imagination).
- **Knob/Change:** Code in `_derive_and_pin_forecast_spine` + new `_SPINE_INSTRUCTIONS` `base_distribution` field; flag `REPORT_SPINE_ANCHOR_WORLDSTATE` (default true when `SIM_DECISION_CHANNEL` on).
- **Impact:** Makes the simulation's quantitative outcome the actual forecast anchor; eliminates the "who-talked-most → number" leap the WorldState was built to remove.
- **Risk:** If WorldState is degenerate/uniform (`R2-CAL-13`) the anchor is weak — fall back to free spine; keep the band wide.
- **Effort:** medium

### R2-CAL-4 — No probability floor — scenarios can be assigned 0%, risking catastrophic log-loss/Brier
- **Builds on:** NEW.
- **Evidence:** `forecast_extractor.py:74-77` renormalizes by dividing by total but never floors; a dropped/low scenario stays near 0. `backtest.py:53-54` clamps `log_loss eps=1e-9`, so if the *realized* scenario was ~0, log-loss explodes and multi-class Brier (`:45-52`) maxes out. No floor in `_normalize_scenarios`/`_assemble_forecast`.
- **Current:** A plausible-but-unforecasted outcome incurs the maximum penalty, and the published forecast can assert 0% — the canonical overconfidence failure a superforecaster never makes.
- **Proposed:** Floor every retained scenario at `p_min` (0.02-0.05) before the final renormalization; ensure the residual/status-quo bucket (required at `forecast_extractor.py:150`) never drops below the floor. Optionally scale the floor by horizon length.
- **Knob/Change:** New `FORECAST_PROB_FLOOR` (default 0.03) in `_normalize_scenarios`.
- **Impact:** Directly improves tail Brier/log-loss; protects the ledger from one black-swan resolution wrecking the track record.
- **Risk:** Slightly flattens sharp distributions — tiny at 3% and dominated by the tail-calibration gain.
- **Effort:** trivial

### R2-CAL-5 — Measured calibration error feeds back only as text — close the recalibration loop
- **Builds on:** round-1 `EVAL-1`/`GAP-4`.
- **Evidence:** `report_agent.py:1527-1539` reads `forecast_ledger.calibration_summary` (Brier/ECE) and merely **appends a sentence** to `confidence_rationale`; the probabilities are unchanged. `backtest.py:64-123` computes per-bin `mean_predicted` vs `observed_hit_rate` (the exact recalibration signal) — never consumed numerically.
- **Current:** The system is "calibration-capable, not calibrated" (its own docstring): historical overconfidence is printed but the next forecast repeats it; the reliability slope is discarded.
- **Proposed:** When `n_resolved ≥ threshold`, fit a 1-parameter recalibrator (temperature/Platt: `p' = sigmoid(logit(p)/T)`, T from the reliability slope) and apply it to spine probabilities before publish, recording pre/post. Minimal: shrink toward base rate by a factor of (mean_predicted − observed_hit_rate) in the top bins when historically overconfident.
- **Knob/Change:** New `REPORT_RECALIBRATE_FROM_LEDGER` (default false until enough labels) + fit/apply code; reuse backtest bins.
- **Impact:** Closes the loop — confidence becomes *earned*; overconfident histories auto-pull future probabilities toward base rates.
- **Risk:** Over-fitting to few samples — gate on `n_resolved≥N` (pairs with `R2-CAL-14`); always log the adjustment.
- **Effort:** medium

### R2-CAL-7 — Resolution-criteria sharpness is requested in prose but never validated
- **Builds on:** round-1 `REPORT-6` (publish-gate plumbing).
- **Evidence:** Prompts ask for metric+threshold+date (`forecast_extractor.py:143,151`); `render_resolution_block:238-278` just prints what the model returned; `_apply_publish_gate` (`report_agent.py:1582-1636`) checks coverage/sum/residual/entropy but **never** inspects criteria quality.
- **Current:** "利率可能上升" passes the gate identically to "若指标X于2027-12前超过Y则情景A确认." Un-sharp criteria can never be objectively scored, breaking the entire backtest/ledger loop.
- **Proposed:** A pure validator: each criterion must contain (a) a number/threshold, (b) a date or named trigger, (c) a metric/source noun. Flag violations into `forecast['quality']['vague_criteria']`; the gate demotes confidence and emits "needs sharper criteria." Reuse `_NUMBER_RE`/date regexes (`forecast_extractor.py:330-331`).
- **Knob/Change:** New `validate_resolution_criteria()` in the gate; flag `REPORT_REQUIRE_SHARP_CRITERIA` (default true).
- **Impact:** Forces falsifiable, trackable criteria — the precondition for any Brier/calibration signal to mean anything.
- **Risk:** Regex may miss valid prose — treat as a soft flag (demote, don't block).
- **Effort:** small

### R2-CAL-9 — Ensemble "agreement" conflates spread with disagreement and ignores `support_ratio`
- **Builds on:** round-1 `REPORT-7` (shared-name set).
- **Evidence:** `ensemble.py:89-93` `agreement = max(0, 1 − 2·mean_spread)` (the 2× is arbitrary); `support`/`support_ratio` (`:74-75`) never enter agreement, so a scenario appearing in 1 of N runs doesn't lower it.
- **Current:** Scenario-set instability (different runs naming different outcomes) is invisible, and the magnitude is on an uninterpretable scale.
- **Proposed:** `agreement = 1 − mean pairwise total-variation distance` between per-run distributions (bounded [0,1], interpretable), penalized by the fraction of scenarios with `support_ratio<1`.
- **Knob/Change:** Code in `aggregate_forecasts` (depends on `R2-CAL-2`'s shared-name set for clean pairing).
- **Impact:** Earned, interpretable agreement reflecting both spread and set-instability.
- **Risk:** Requires aligned scenario sets across runs — already the `REPORT-7` fix.
- **Effort:** small

### R2-CAL-10 — No Brier decomposition — "sharpness" is unmeasured, so calibrated-but-vague is indistinguishable from sharp
- **Builds on:** round-1 `EVAL-1`.
- **Evidence:** `backtest.py:64-123` `calibration_report` returns `mean_brier` + count-weighted `calibration_error` only — no resolution/discrimination term; `forecast_ledger.calibration_summary` (`:107-128`) surfaces only those two.
- **Current:** The loop can't tell a forecaster who always says "base rate" (calibrated, zero resolution) from one who sharply discriminates — yet the bar demands both. You optimize blind to half the objective.
- **Proposed:** Add Murphy decomposition (`Brier = reliability − resolution + uncertainty`) from the existing bins; surface `resolution` into `calibration_summary`/`confidence_rationale`.
- **Knob/Change:** Pure function addition in `backtest.py`.
- **Impact:** Makes sharpness measurable and rewardable, separating discrimination from calibration.
- **Risk:** None (additive, offline); needs enough resolved samples (`R2-CAL-14`).
- **Effort:** small

### R2-CAL-11 — Spine `max_tokens=2048` with the richest schema risks truncation → empty → silent fallback to the weaker prose extractor
- **Builds on:** round-1 `ONTO-5` (same truncation pattern).
- **Evidence:** `forecast_extractor.py:183` caps `max_tokens=2048` while `_SPINE_INSTRUCTIONS` (`:129-153`) asks for headline + horizon + up to 5 scenarios each with summary+key_drivers+base_rate_anchor+adjustment_rationale+resolution_criteria + key_uncertainties — easily >2048 on a reasoning model. On truncation `chat_json` returns non-dict → empty spine (`:185-186`) → `report_agent.py:1473-1475` silently falls back to post-hoc prose extraction (`:1507-1511`).
- **Current:** The most rigorous artifact can be silently lost to a token cap, degrading to numbers reverse-engineered from finished narrative — exactly what spine-first was built to prevent.
- **Proposed:** Raise to ~6144; log a **WARNING** (not info) on empty spine; retry once at higher budget before falling back.
- **Knob/Change:** `forecast_extractor.py:183` 2048→6144 + explicit warning.
- **Impact:** Prevents silent loss of the anchored MECE spine; the authoritative forecast reliably comes from the disciplined path.
- **Risk:** Marginally larger output on one off-critical-path call; negligible.
- **Effort:** trivial

### R2-CAL-12 — Anchor-and-adjust is optional and unverified — no base-rate-neglect guard
- **Builds on:** NEW.
- **Evidence:** `forecast_extractor.py:69-72` only copies `base_rate_anchor`/`adjustment_rationale` "if `s.get(...)`" — dropped silently when absent; the prose instruction (`:151-153`) demands them but `_normalize_scenarios`/`_apply_publish_gate` never check presence or anchor↔final coherence.
- **Current:** The model can skip the outside view and emit an inside-view number; nothing flags a large jump from base rate without a stated reason.
- **Proposed:** Schema-enforce `base_rate_anchor` + `adjustment_rationale` per scenario (retry once if missing); flag scenarios whose final probability deviates materially from a *numeric* anchor with empty/short rationale, demoting confidence.
- **Knob/Change:** Code in `_normalize_scenarios` + gate; flag `REPORT_REQUIRE_ANCHOR` (default true). Only enforce the numeric check when the anchor parses to a rate.
- **Impact:** Operationalizes outside-view discipline so every probability is traceably anchored.
- **Risk:** Some anchors are qualitative reference classes — fall back to a non-empty-rationale check.
- **Effort:** small

### R2-CAL-13 / R2-SIM-9 (merged) — WorldState discards the outside-view prior and forbids abstention; the `probability_band` schema mismatch is why
- **Builds on:** NEW (merges the two near-duplicate round-2 findings; incorporates the critic's precise root cause).
- **Evidence:** `world_state_seed_from_actors` (`actors.py:1559-1568`) only reads scenario keys `probability/likelihood/base_rate/prob`, **but the standard research scenario schema stores its probability as `probability_band`** (a string range, `actors.py:78`) — so `rates` stays empty and `worldstate.py:83-85` falls back to a **uniform** prior with no warning (inertia hardcoded 0.7 `:74`, eps 0.02 `:122`). `decision_channel` also forces every active agent to pick exactly one scenario each round (`_build_round_decision_prompt:34-43`, validation `:67-78` drops non-set picks) with no abstain option. `forecast_inputs.base_rates[]` is stored as `outcome_frequency` strings ("~30%", `actors.py:75,1626-1637`) — a different shape, also unread.
- **Current:** The carefully-researched base rates (the anchor a Bridgewater forecast starts from) are *always* thrown away for the standard schema and replaced with 50/50, and every agent is compelled to push some scenario each round even with no stake — inflating spurious commitment and biasing toward the LLM's default scenario. Convergence (= the confidence signal) then runs on arbitrary defaults.
- **Proposed:** (1) In `world_state_seed_from_actors`, **parse `probability_band`** (take the band midpoint) and, as a secondary source, `forecast_inputs.base_rates[].outcome_frequency` (strip %, map `reference_class`→scenario) — this single parse is the cheapest, highest-leverage calibration lever. (2) Add an explicit `abstain`/no-commitment option contributing zero weight so only staked agents move the distribution. (3) Keep a small base-rate pull each round so early sim noise can't erase the prior. (4) When base rates are genuinely absent, emit a `uniform-prior` flag into `outcome()` and **lower** forecast confidence instead of silently uniforming; expose inertia/eps as config.
- **Knob/Change:** Code in `actors.world_state_seed_from_actors` + `decision_channel` prompt/validation; new `SIM_BASE_RATE_ANCHOR` weight, `SIM_WORLDSTATE_INERTIA`/`SIM_WORLDSTATE_EPS`.
- **Impact:** Anchors the modeled outcome on the research base rate instead of 50/50 and makes convergence-derived confidence meaningful — the single biggest calibration lever for the binary forecasts.
- **Risk:** Parsing free-text bands is fragile — validate ranges, fall back to uniform-with-flag on failure; cap abstention so it doesn't starve the signal.
- **Effort:** small

### R2-CAL-14 — Calibration bins are equal-width with no smoothing or per-bin uncertainty — tiny-N ECE is noisy
- **Builds on:** round-1 `EVAL-1`.
- **Evidence:** `backtest.py:77-105` uses fixed equal-width bins via `int(p·bins)` and raw `sum/n` hit-rate — no per-bin CI, no minimum count, no smoothing. `calibration_summary` feeds this straight into `confidence_rationale` (`report_agent.py:1527-1539`) and would feed `R2-CAL-5`.
- **Current:** With a handful of resolved forecasts, one bin's hit-rate swings wildly → a misleading ECE that (once recalibration lands) over-corrects on noise.
- **Proposed:** Beta(α,β) smoothing (Laplace/Jeffreys) on per-bin hit-rates, report a credible interval per bin, require `n_resolved ≥ threshold` (and per-bin min count) before ECE is actionable; optionally quantile bins.
- **Knob/Change:** Code in `calibration_report`; new `CAL_MIN_RESOLVED` (default ~20).
- **Impact:** Stable, trustworthy metrics that won't over-fit recalibration to a few labels.
- **Risk:** None offline — only more conservative.
- **Effort:** small

### R2-CAL-16 — The graded `quantitative_facts` never anchor the forecast spine
- **Builds on:** NEW (critic blind spot 2).
- **Evidence:** `quantitative_facts` (hard figures with `as_of_date` + S-tier) are injected into section prose (`report_agent.py:1168`) but **not** into the spine — only `forecast_inputs_block` is (`:1451/:1465`). The spine is built from prose-y base rates + activity volumes, not the graded hard data.
- **Current:** The sharpest, best-sourced numbers in the pipeline (the "citable floor") inform section narrative but never the load-bearing probability object.
- **Proposed:** Pass a compact, S-tier-filtered `quantitative_facts` digest into `derive_forecast_spine` alongside `base_distribution` (`R2-CAL-3`), and require the spine's `base_rate_anchor`/`adjustment_rationale` to cite a specific fact (metric + as_of_date) where one exists. Fold into the binary register (`R2-CAL-6`) too.
- **Knob/Change:** Code in `_derive_and_pin_forecast_spine`; reuse `quantitative.json`.
- **Impact:** The forecast is anchored on graded, dated hard data instead of restated prose — the difference between defensible and generic numbers.
- **Risk:** Larger spine prompt — cap to top-K S-tier facts by relevance; pairs with `R2-CAL-11`'s raised budget.
- **Effort:** small

### R2-CAL-17 — Output confidence is still low/medium/high — emit numeric intervals
- **Builds on:** round-1 `REPORT-5` (the distribution exists but is collapsed).
- **Evidence:** Confidence is rendered as a categorical band in `confidence_rationale`/the publish gate; the spread/agreement machinery (`ensemble.py:89-93`, and `R2-CAL-1`'s K-draw spread) produces a numeric distribution that is then bucketed to low/medium/high.
- **Current:** A Bridgewater forecast quotes `p` with an interval; the pipeline discards the spread it already computes into three words.
- **Proposed:** Carry the self-consistency / seed spread through to a published `[p_low, p_high]` (e.g. 10th-90th percentile of the K draws or ±1 pooled stdev in logit space) per scenario and per binary forecast; keep the categorical band as a derived label.
- **Knob/Change:** Code in `_assemble_forecast`/render; reuse `R2-CAL-1`/`R2-CAL-2` spread.
- **Impact:** Numeric intervals make sharpness and uncertainty legible and backtestable — a core Bridgewater presentation requirement.
- **Risk:** None — additive; intervals degrade to a point when `K=1`.
- **Effort:** small

### R2-CAL-18 — Model-vs-simulation divergence is computed nowhere — use it as a meta-confidence signal
- **Builds on:** NEW (critic blind spot); complements `R2-CAL-3`.
- **Evidence:** Once `R2-CAL-3` anchors the spine on `WorldState.shares`, the gap between the *simulated* share and the *adjusted* spine probability is a free, informative quantity, but no code compares them; `adjustment_rationale` is prose only.
- **Current:** When the LLM spine departs sharply from the simulated distribution, that disagreement (a genuine uncertainty signal) is invisible.
- **Proposed:** Compute per-scenario `|p_spine − share_worldstate|`; large divergence with thin evidence (`R2-RES-3`) widens the interval (`R2-CAL-17`) / demotes confidence; large divergence with strong evidence is allowed but logged. Surface the divergence table in the appendix.
- **Knob/Change:** Code in `_apply_publish_gate`; reuses WorldState shares + spine.
- **Impact:** Turns the sim-vs-model gap into a calibrated humility input rather than an unexamined override.
- **Risk:** Needs the channel on (`R2-SIM-1` precondition); degrade gracefully when absent.
- **Effort:** small

## Detail & comprehensiveness (output + research + KG)

### R2-CAL-6 / R2-DETAIL-1 (merged) — No machinery for the 10+ independent binary forecasts the deliverable requires
- **Builds on:** NEW (merges the two duplicate round-2 findings).
- **Evidence:** The entire schema (`forecast_extractor.py:20-38` and spine `:129-153`) models **one** question as 2-5 MECE scenarios, and `_normalize_scenarios:74-77` forces the probabilities to sum to 1; `_SPINE_INSTRUCTIONS:150` demands "2-5 个互斥且尽量穷尽（MECE）的情景." grep for `binary/二元/p_yes` finds nothing. The stated deliverable is "10+ calibrated binary forecasts."
- **Current:** Ten independent yes/no questions ("export controls tighten by 2027?", "AI capex grows >X%?") cannot be represented; shoehorned as scenarios they get illegally renormalized to sum to 1. **The headline deliverable is structurally unreachable.**
- **Proposed:** Add a parallel `forecast_register: [{claim, p_yes, base_rate_anchor, adjustment_rationale, resolution_criteria, leading_indicators, drivers, confidence}]` via a new `derive_binary_forecasts(...)` that decomposes the central question into 12-15 *independent* binaries; probabilities are **NOT** renormalized; each is derived anchor-and-adjust (`R2-CAL-12`), scored independently in `backtest` with binary Brier/log-loss, and folded into the ledger as its own resolvable item. Render a numbered register + summary probability table and pin it into section prompts so prose defends specific numbered forecasts. Validate `0<p<1` and a verb+date+threshold per claim with a count floor of 10.
- **Knob/Change:** New `forecast_extractor.derive_binary_forecasts` + render + binary scoring path in `backtest.py`/`forecast_ledger.py`; flag `REPORT_BINARY_FORECASTS` (default true for full mode).
- **Impact:** Makes the primary requested artifact possible and individually backtestable — the single largest gap to the Bridgewater bar.
- **Risk:** More LLM output (raise `max_tokens`, `R2-CAL-11`) and a longer report; independent binaries ignore correlation — addressed by `R2-CAL-15`.
- **Effort:** medium

### R2-DETAIL-2 — The outline is planned before the spine is derived, so section taxonomy can't organize around the forecasts
- **Builds on:** NEW.
- **Evidence:** `plan_outline()` runs at `report_agent.py:2990`; the spine is only derived at `:3023-3027` (`_derive_and_pin_forecast_spine`). `plan_outline` (`:2152-2293`) sees graph stats + 25 facts + sweeps but never the scenarios/probabilities.
- **Current:** Sections are designed from raw sim statistics with generic titles, then the spine is bolted on as a prefix. The outline makes no structural commitment to cover each scenario/forecast, a framework section, or a calibration section — coherence is left to chance.
- **Proposed:** Reorder: derive the spine **and** the binary register (`R2-CAL-6`) first, then pass `spine.scenarios` + register claims into `plan_outline`'s prompt and require the outline to include (a) a Framework/transmission-mechanism section, (b) a per-scenario or thematic body covering each forecast, and (c) a calibration/appendix section.
- **Knob/Change:** Reorder calls in `generate_report`; extend `plan_outline` inputs.
- **Impact:** A report whose taxonomy is built around the forecasts — coherent framework + per-forecast defense instead of a topic summary with numbers appended.
- **Risk:** Spine derivation moves onto the report critical path before outlining — bounded (one cheap call); pairs with `R2-CAL-1`.
- **Effort:** small

### R2-DETAIL-3 — Spine input is truncated at 4000-char caps that starve a 10+ driver question
- **Builds on:** NEW (critic blind spot); complements `R2-CAL-16`.
- **Evidence:** The spine/forecast-input assembly applies ~4000-char caps to the injected blocks (`forecast_inputs_block`, signal pack sub-blocks) before `derive_forecast_spine`, so a question with many drivers/scenarios/base-rates loses the tail of its own evidence at the exact moment the load-bearing number is formed.
- **Current:** On a multi-driver Modern-Mercantilism×AI question, the spine sees a truncated slice of the drivers and base rates — directly capping how differentiated the binary register (`R2-CAL-6`) can be.
- **Proposed:** Raise the spine-input caps (the proxy is 1M-context, `RESEARCH-4` already notes Gemini is under-fed) or token-budget them via `ADAPTIVE_CONTEXT`; prioritize S-tier `quantitative_facts` and high-salience drivers when trimming is unavoidable.
- **Knob/Change:** Raise the per-block caps in the spine-input builder; reuse `ADAPTIVE_CONTEXT`.
- **Impact:** The spine reasons over the full driver/base-rate set → sharper, more differentiated forecasts.
- **Risk:** Larger spine prompt/latency — bounded; one off-critical-path call.
- **Effort:** small

### R2-RES-1 — The research-quality scorecard and `RESEARCH_QUALITY_GATE` are fully dead code
- **Builds on:** NEW.
- **Evidence:** `pipeline_orchestrator.py:2681` reads `rq=meta.get('research_quality')` and `:2687-2697` gates on `RESEARCH_QUALITY_GATE` + `RESEARCH_QUALITY_FLOOR`; the docstring (`:2668`) says the score is "written by the DeerFlow bridge `compute_research_quality()`." But grep of `deerflow_research.py` for `research_quality`/`compute_research_quality` returns **zero** — the bridge never writes the key. `config.py:158` defines `RESEARCH_QUALITY_GATE` but there is **no** `RESEARCH_QUALITY_FLOOR` attr, so `:2688` `getattr(...,0.0)` makes floor=0 → the gate can never fire. `source_tier_histogram` is computed (`deerflow_research.py:1846`) but dropped into `meta['source_tiers']` and never scored.
- **Current:** No aggregate evidence-quality number exists anywhere; the gate is doubly inert (no producer, floor=0). A dossier on S4 aggregators, zero edges, thin profiles flows to a published forecast with the same standing as a triangulated B2-grade one.
- **Proposed:** Implement `compute_research_quality()` (call before `write_meta` at `:1854`) fusing already-available signals into a 0-1 score: source-tier mix (S1/S2 up, S4/unknown down) from `source_tier_histogram`, dossier richness (`actors_count`, `relationships_count`, `has_situation_brief`, quant/contested counts), and the actor-dossier judge mean (`actor_dossier_judge.json`). Write `meta['research_quality']={'score':x,'components':{...}}`. Add `RESEARCH_QUALITY_FLOOR` (~0.45) and flip `RESEARCH_QUALITY_GATE=true`.
- **Knob/Change:** New `compute_research_quality` in the bridge + `RESEARCH_QUALITY_FLOOR`.
- **Impact:** Turns evidence quality from invisible to a first-class, gate-able signal — the prerequisite for trusting every downstream quality lever.
- **Risk:** A mis-calibrated floor could soft-warn on legitimately sparse single-actor questions — keep it a soft warning, tune on real runs.
- **Effort:** small

### R2-RES-2 — Track B (the primary extraction seed) never fans out per-actor — profile depth that caps the whole pipeline comes from one research turn
- **Builds on:** round-1 `RESEARCH-6` (deep protocol).
- **Evidence:** `run_actor_ontology_stage` (`deerflow_research.py:1394-1422`) is exactly one tool-using research turn (`:1415`) at standard depth + one synth + the judge loop; the comment (`:1401-1402`) deliberately states "刻意保持有界：不在 Track B 内跑完整 deep fan-out." `RESEARCH_DEEP_FANOUT` is read only in `run_research_stage:1178` (Track A), never Track B. The dossier is the **primary** input to actor extraction (orchestrator `:2785,:2857` puts it first); the actor-ontology SKILL §8.2 makes `per_actor_depth ≥4` non-negotiable.
- **Current:** The cast profiles — which cap persona realism, KG entities, and forecast sharpness — come from one undifferentiated turn that must simultaneously identify the cast, deeply profile 8-20 actors, and map the network. Reasoning-model attention spread this thin yields label-depth profiles (the SKILL's #1 failure), so personas become stance-label caricatures.
- **Proposed:** Add an opt-in `ACTOR_DOSSIER_FANOUT` path: after the first turn, parse the candidate cast (reuse `extract_kiqs_from_opening` with the actor regex at `:1004-1008`), dispatch `ACTOR_DOSSIER_FANOUT_WIDTH` scoped workers (reuse `run_scoped_worker`, the ThreadPoolExecutor at `:1123`) each tasked with one actor's §3 profile (identity, values, beliefs, incentives `gains_if/loses_if`, constraints, resources, vulnerabilities, that actor's edges), absorb their notes, then synthesize.
- **Knob/Change:** New `ACTOR_DOSSIER_FANOUT` (default false; recommend true for forecast runs) + `ACTOR_DOSSIER_FANOUT_WIDTH` (~6).
- **Impact:** Moves `per_actor_depth` from a single-pass guess to researched profiles — the most defensible place to spend time on a latency-bound proxy because it sets the ceiling for every persona, entity, and binary forecast downstream.
- **Risk:** N parallel research turns add Track-B wall-clock — bound width to ~6 top-salience actors, run concurrently.
- **Effort:** medium

### R2-RES-3 — Weak-evidence detection has no corrective action — coverage/tiers only log
- **Builds on:** NEW.
- **Evidence:** `dossier_coverage` is computed (`pipeline_orchestrator.py:2785` → `actors.py:1301`) producing `pct_actors_with_incentives`, `pct_edges_valenced`, etc., but the only consequence is `logger.warning` + an options string (`:2796-2798`). The bridge comment (`deerflow_research.py:1845`) promises "a downstream coverage gate can reject S4-heavy dossiers" — that gate does not exist; `n_relationships==0` just prints "无关系边."
- **Current:** A dossier with zero valenced edges or an S4-dominated source set produces the same pipeline as a rich one — measured weakness is observed then ignored; GIGO flows to a confidently-stated forecast.
- **Proposed:** Wire `dossier_coverage` + `meta['source_tiers']` into either (a) one targeted Track-B refine round seeded by the weak metric (when fanout is available), or minimally (b) a structured `forecast_confidence_penalty` into `state.options`, consumed by the publish gate so a thin evidence base provably **widens** the band.
- **Knob/Change:** New `RESEARCH_COVERAGE_GATE` (default off) reading the existing thresholds at `:2788-2795`.
- **Impact:** Closes the loop between measured evidence weakness and forecast output — a Bridgewater forecast must be less confident when its evidence is thin.
- **Risk:** Refine adds a turn; the confidence-penalty path is free and lower-risk. Keep thresholds conservative.
- **Effort:** small

### R2-RES-4 — No recency weighting — dates exist but staleness vs the forecast horizon is never flagged
- **Builds on:** NEW.
- **Evidence:** `sources` carry `date` (`deerflow_research.py:628`) and `quantitative_facts` carry a **required** `as_of_date` distinct from article date (`:646`); the orchestrator parses an `as_of_date` (`:2943`) — but nothing computes recency/staleness or weights newer evidence. For Modern Mercantilism × AI (export-control regimes, capex guidance, chip bans that change quarterly) a 2-year-old figure mis-grounds a forward forecast.
- **Current:** A stale S1 figure is treated identically to a current one; the graph anchors all chunks to one `as_of` with no per-fact freshness, so the sim/report can reason on superseded numbers as current.
- **Proposed:** Derive per-source/per-fact `staleness_days = run_as_of − fact.as_of_date` and an `is_stale` flag when > `min(180d, horizon/2)`; surface a freshness histogram into meta, prefer fresh facts in the signal pack, and mark stale load-bearing numbers "(as-of <date>, may be superseded)."
- **Knob/Change:** New `RESEARCH_RECENCY_WEIGHTING` (default true; derived field, near-zero cost).
- **Impact:** Makes the forecast current rather than a lagging average — the edge on a fast-moving question is pricing the latest move.
- **Risk:** Mislabeled dates mis-flag — only flag when `as_of_date` parses; never drop, only annotate.
- **Effort:** small

### R2-RES-5 — Contradictions are detected but never resolved or priced into calibration
- **Builds on:** NEW.
- **Evidence:** `build_extraction_prompt` emits `contested_claims` with positions+sources+status+why_they_differ (`deerflow_research.py:657-671`) → `contested.json` (`:1838`); `report_agent.py:1173` renders a **prose** block. Nothing adjudicates positions by tier/independence, and no path maps an unresolved load-bearing contested claim to a wider band. SKILL §7/§8.7 prescribe ACH-lite resolution and "shown not averaged."
- **Current:** Genuine evidence conflicts surface as a paragraph but leave the numeric forecast unchanged — a binary whose key driver is genuinely contested gets the same confidence as one with settled evidence.
- **Proposed:** An ACH-lite resolver over `contested.json`: score each position by max source tier + count of independent sources (`sources[].independent`, `:630`), label resolved/unresolved/leaning, emit a per-claim `confidence_delta`; bind unresolved contested claims that touch a forecast driver to a confidence demotion / interval widening in the gate; state the live bear/bull case with its evidence weight.
- **Knob/Change:** New code over `contested.json`; flag `CONTESTED_CALIBRATION` (default true).
- **Impact:** Confidence becomes a function of evidence agreement — sharper *and* better-calibrated; turns currently-cosmetic adversarial research into a real calibration input.
- **Risk:** Over-widening on trivial disagreements — extraction already pre-filters ("omit trivial disagreements," `:683`).
- **Effort:** medium

### R2-RES-6 — No completeness probe — nothing checks "did we miss a key actor or an unowned driver?"
- **Builds on:** NEW.
- **Evidence:** The pipeline relies on the model's self-judge (`judge_dossier`) to catch missing actors; there is no deterministic negative-space check. `situation_brief` emits `fault_lines`/`catalysts` (`deerflow_research.py:726`) and the question names entities, but no code verifies each named entity/fault_line/driver maps to a cast member or source. SKILL §11 lists "key decision-makers missing" as a top failure with no guard.
- **Current:** If research silently omits a pivotal actor (a fault line about "EU chip sovereignty" but no EU actor), the gap is invisible — the sim and forecast never model that force.
- **Proposed:** A deterministic pass after extraction: collect proper-noun candidates from `central_question` + `fault_lines` + `catalysts` + `hot_topics`; verify each appears in `actors[].name/aliases` or `sources[]`; emit `meta['coverage_gaps']={missing_named_entities, orphan_fault_lines}`. Optionally feed each gap as a fanout seed (`R2-RES-2`).
- **Knob/Change:** New util + meta field; flag `RESEARCH_COMPLETENESS_PROBE` (default true, pure/cheap).
- **Impact:** Catches the highest-cost failure (a missing mover) before it truncates the simulation — protects comprehensiveness on a multi-party question.
- **Risk:** Proper-noun heuristics over-flag — keep observational, dual-check against `sources[]`.
- **Effort:** small

### R2-RES-7 — `as_of_date` is a single unvalidated model-emitted field anchoring the entire bi-temporal graph
- **Builds on:** NEW.
- **Evidence:** Extraction emits `as_of_date` as one string (`deerflow_research.py:720`); the orchestrator does `parse_as_of(actors['as_of_date'])` (`pipeline_orchestrator.py:2943`) and uses it as `valid_at` for seeded actors (`:2946`) **and** `reference_time` for **all** chunks (`:2958`). No check that it is ≤ today, ≥ newest source date, or non-null (`parse_as_of` returns None silently).
- **Current:** A hallucinated/missing/stale `as_of_date` silently mis-anchors every edge's `valid_at` and every chunk's `reference_time`, corrupting recency ordering and any temporal/causal reasoning.
- **Proposed:** Validate before use: require it parses, is ≤ run date, ≥ `max(sources[].date)`; on failure fall back to `max(source dates)` or run date and log the substitution; cross-check against `quantitative_facts[].as_of_date`.
- **Knob/Change:** Small code change at `pipeline_orchestrator.py:2943`.
- **Impact:** Guarantees the KG temporal backbone is sound — the foundation of a calibrated forward forecast.
- **Risk:** Almost none — adds a guard with a safe fallback.
- **Effort:** trivial

### R2-RES-10 — Triangulation/independence is in the schema but never enforced — single-origin load-bearing claims pass as B2
- **Builds on:** round-1 `RESEARCH-5`.
- **Evidence:** `sources` carry `independent:boolean` (`deerflow_research.py:630`), actors/relationships carry an Admiralty grade (`:543-548`), and `actors.py:1113` references `s.get('independent')` — but extraction never requires ≥2 independent sources for a tier-1 actor's load-bearing claim or a high-strength edge, and `graph_builder` doesn't carry grade/independence onto seeded edges. SKILL §8.6 "B2+ bar"/circular-source detection are asked-for but unverified.
- **Current:** A pivotal claim backed by one source (or echoes of one origin) is indistinguishable from a triangulated one, so the forecast can rest load-bearing weight on un-corroborated evidence with no flag.
- **Proposed:** A post-extraction triangulation audit: for each tier-1 actor and `strength=high` edge, check for ≥2 `independent=true` sources OR grade B2+; otherwise set `single_origin=true` and add to `meta['single_origin_loadbearing']`. The report hedges single-origin load-bearing claims; assert S4 exclusion.
- **Knob/Change:** New util over `actors.json`/`sources.json`; flag `RESEARCH_TRIANGULATION_AUDIT` (default true).
- **Impact:** Surfaces the evidentiary weak points that should temper specific forecasts — prevents a single-sourced claim from driving an overconfident binary.
- **Risk:** Depends on honest `independent`/`grade` population — treat absence as unknown, flag conservatively.
- **Effort:** small

### R2-RES-11 — Quantitative facts are never cross-reconciled or sanity-checked — divergent values for one metric pass silently
- **Builds on:** NEW.
- **Evidence:** `quant_schema` captures metric/value/unit/as_of_date/definition/source/tier (`deerflow_research.py:640-651`) → `quantitative.json` (`:1831`) → a prose block (`report_agent.py:1168`). No dedup/reconciliation of rows sharing a normalized metric, no order-of-magnitude/unit check, no rule that two divergent figures become a `contested_claim`. SKILL §6 lists "un-sanity-checked numbers" as a failure mode.
- **Current:** Two sources giving different "TSMC 2026 capex" both land in `quantitative.json` and both can be cited as settled, and a USD-billion-vs-million typo propagates into the numeric floor.
- **Proposed:** A reconcile pass: group by normalized `(metric, unit)`; on disagreement beyond tolerance (a) auto-emit a `contested_claim` (feeding `R2-RES-5`), (b) keep the higher-tier/fresher row as primary, (c) flag values differing ~1000× (unit error). Persist a reconciliation audit into meta.
- **Knob/Change:** New util; flag `RESEARCH_QUANT_RECONCILE` (default true, pure/cheap).
- **Impact:** Hardens the numeric backbone the report cites as its citable floor (and that `R2-CAL-16` anchors on).
- **Risk:** Tolerance tuning — keep flags advisory, annotate don't delete.
- **Effort:** small

### R2-RES-12 — No source-diversity / jurisdictional-balance gate — a US/China/EU question can be evidenced from one region
- **Builds on:** NEW.
- **Evidence:** The contradictions phase asks for "regional disagreements" (`deerflow_research.py:130-134`) but `sources[]` has no jurisdiction/region/language field (`:622-635`) and nothing computes a regional distribution. For Modern Mercantilism × AI the live edge is precisely US (BIS) vs Chinese (MOFCOM) vs EU (Chips Act) divergence.
- **Current:** Evidence can skew to English-language Western sources, baking regional bias into a multilateral forecast with no signal.
- **Proposed:** Add optional `jurisdiction`/`lang` to the sources schema; compute a diversity histogram into `meta['source_diversity']`; warn (or trigger a targeted refine) when a multi-jurisdiction question draws >~70% of load-bearing sources from one region/language.
- **Knob/Change:** Prompt addition + meta histogram; flag `RESEARCH_SOURCE_DIVERSITY` (default true).
- **Impact:** Reduces home-bias on inherently geopolitical forecasts — the probability reflects all parties' positions, not one side's framing.
- **Risk:** Jurisdiction tagging is best-effort — degrade to "unknown," only warn.
- **Effort:** small

### R2-KG-1 — Causal multi-hop traversal is sign- and magnitude-blind — it returns only edge names, dropping the polarity that decides whether a cascade amplifies or dampens
- **Builds on:** NEW.
- **Evidence:** `runtime.py:590-596` (`_causal_paths`) RETURNs only `[e IN relationships(p) | e.name]`; `runtime.py:633` (`_n_hop_subgraph`) RETURNs only `rel.name AS edge`; `trace_cascade` renders `src --[edge]--> tgt` with no sign (`zep_tools.py:2505-2519`). Sign/strength live only in `edge.fact` text (`graph_builder.py:402-409`) or as `Optional[str]` attributes (`:322-330`), neither returned.
- **Current:** A traced path "export controls --[CONSTRAINS]--> China compute --[ACCELERATES]--> domestic fab" comes back as bare names; the report cannot compute net polarity (a CONSTRAINS(−) then ACCELERATES(+) net-flips direction), so it narrates chains it cannot evaluate.
- **Proposed:** Project sign/strength/polarity/fact in both traversals: `[e IN relationships(p) | {name:e.name, sign:e.sign, strength:e.strength, polarity:e.polarity, fact:e.fact}]`; render each hop as `--[CONSTRAINS, sign=-, strength=high]-->` and append a computed net-polarity (product of per-hop polarities). Parse `sign=`/`polarity=` from the fact text as fallback.
- **Knob/Change:** Code in `runtime._causal_paths`/`_n_hop_subgraph` + `zep_tools.trace_cascade`; always-on enrichment, degrades to today's behavior when attributes absent.
- **Impact:** Turns `trace_cascade` from a name-chain into an actual transmission calculation — every causal claim can state direction and net effect.
- **Risk:** Slightly larger payloads; net-polarity is "partial" when only some hops carry signs.
- **Effort:** medium

### R2-KG-2 — Traversal does exact `a.name=$src` matching with no alias/fuzzy resolution — silently returns empty on most model-supplied names
- **Builds on:** NEW; compounds round-1 `GRAPH-4`.
- **Evidence:** `runtime.py:591-592` matches `WHERE a.name=$src AND b.name=$tgt` verbatim; the report passes the LLM's raw strings (`report_agent.py:2009-2014`). Contrast `opinion_shift`, which normalizes (`zep_tools.py:2534-2535` via `normalize_name`); `actors.match_actor` (`actors.py:171-201`) and the alias bridge (`graph_builder.py:483-505`) exist but aren't consulted.
- **Current:** `trace_cascade(source='OpenAI', target='TSMC')` against canonical nodes "OpenAI 公司" / "TSMC（台积电）" yields zero paths → "(no path)" or a non-causal fallback. The most powerful structural tool fails silently exactly when entity resolution under-merged.
- **Proposed:** Before the Cypher, resolve source/target to canonical names (`normalize_name` + match against a cached node list / actors alias table); traverse from each candidate if several; log the resolution so empty results are attributable to true absence.
- **Knob/Change:** Code in `zep_tools.trace_cascade` (or a runtime helper); reuse `normalize_name`/`match_actor`.
- **Impact:** Recovers most currently-empty causal traversals — makes the graph's structural reasoning reachable.
- **Risk:** A wrong fuzzy match traverses the wrong node — require ≥2-char normalized overlap and log the chosen canonical.
- **Effort:** small

### R2-KG-3 — `lag` (time-to-impact) is dropped from seeded causal edges and never returned — cascades have no timing
- **Builds on:** NEW; pairs with `R2-KG-1`.
- **Evidence:** `graph_builder.py:404` reads only `sign, strength, grade` — `lag` is never read or folded into the fact (`402-409`), though the `actors.json` contract and ontology prompt call for `sign`/`lag`/`strength` (`actors.py:338`; `ontology_generator.py:262`). Traversal never returns it (`runtime.py:593-594`).
- **Current:** A causal edge "export controls CAUSES fab-localization, lag=18-24 months" loses its lag at seed time; the forecast can chain causes but cannot tell whether the cascade completes inside the horizon — the single most decision-relevant quantity for a dated binary.
- **Proposed:** (1) `graph_builder.py:404-409` read `r.get('lag')` and fold `lag=` into the fact (and an edge property when the ontology defines one); (2) surface lag in the traversal projection (`R2-KG-1`) and `trace_cascade`; (3) sum per-hop lags to estimate total time-to-impact and flag paths whose cumulative lag exceeds the horizon.
- **Knob/Change:** Code in `seed_actors` + traversal; gated by `ONTOLOGY_RICH_SCHEMA` for fact-folding parity.
- **Impact:** Every causal claim gets a timing dimension — distinguishes "fires within horizon" from "fires after," directly improving dated-binary calibration.
- **Risk:** Free-text lag formats — keep opaque, only sum when numerically parseable.
- **Effort:** small

### R2-KG-4 — Multi-hop traversal ignores the bi-temporal axis — paths can chain an invalidated edge with a future one into a phantom cascade
- **Builds on:** NEW.
- **Evidence:** `_causal_paths`/`_n_hop_subgraph` (`runtime.py:572-647`) take no `as_of`/`search_filter` and emit no `valid_at`/`invalid_at` predicate, though the pipeline builds a real bi-temporal axis and `insight_forge`/`panorama` already filter by `as_of` (`zep_tools.py:863-897, 1748-1761` via `_to_search_filters`). `trace_cascade` exposes no `as_of` (`zep_tools.py:2481-2482`).
- **Current:** A cascade can be assembled from edges valid at contradictory times — an alliance later invalidated plus a relationship valid only after the horizon — producing a structurally-real but temporally-impossible path narrated as a live mechanism.
- **Proposed:** Add optional `as_of` to the traversals, threaded as a per-edge predicate: `AND all(e IN relationships(p) WHERE (e.valid_at IS NULL OR e.valid_at <= $as_of) AND (e.invalid_at IS NULL OR e.invalid_at > $as_of))`; expose `as_of` on `trace_cascade` (mirroring `insight_forge`'s wiring at `report_agent.py:1937`). Default None = today's behavior.
- **Knob/Change:** Code in the traversals + `trace_cascade` signature; default-off preserves current behavior.
- **Impact:** Time-consistent cascade reasoning and as-of/T_end contrast ("which paths are live at the horizon vs at T0") — eliminates phantom-cascade false positives.
- **Risk:** Null-timestamp edges must be treated "always valid" to avoid over-pruning (already the convention in `_as_of_filter`).
- **Effort:** medium

### R2-KG-5 — Centrality prior is degree-only — the "which node flips the outcome" question needs betweenness/articulation (chokepoints)
- **Builds on:** NEW; feeds round-1 `GRAPH_CENTRALITY_PRIORS`.
- **Evidence:** `_get_graph_info` computes only normalized degree centrality (`graph_builder.py:648-655`) and top hubs by raw degree (`639-640`); it already builds the full node/edge lists + a union-find (`614-637`). `trace_cascade`'s stated purpose is "哪个节点一动就翻盘" (`zep_tools.py:2484`) — a betweenness/cut-vertex concept. No betweenness/articulation code exists (grep confirms).
- **Current:** Priors (`graph_priors.json`, `pipeline_orchestrator.py:3046-3052`) and salience fusion (`simulation_manager.py:360,384`, `+0.5*_centrality`) prioritize high-degree hubs, but a sole bridge between two clusters (an EUV/HBM bottleneck) can have low degree yet maximal leverage — never flagged.
- **Proposed:** In `_get_graph_info`, add a stdlib Brandes betweenness approximation (capped to top-K-by-degree sources for cost) and an articulation-point DFS (lowlink); emit `chokepoints: [node_name]` + a `betweenness` dict in `GraphInfo` (`graph_builder.py:64-88`). Pin top chokepoints into the report signal pack and query their causal neighborhoods first.
- **Knob/Change:** New `GRAPH_CHOKEPOINT_PRIORS=true`; pure in-memory (reuses fetched nodes/edges).
- **Impact:** Surfaces the structurally load-bearing actors a degree ranking misses — the "one node flips the forecast" insight that differentiates a sharp transmission call.
- **Risk:** Brandes is O(VE) — cap sources to top-K (~100); articulation DFS is O(V+E).
- **Effort:** medium

### R2-KG-6 — `trace_cascade`'s all-or-nothing causal filter makes pure-causal paths near-empty, then falls back to undifferentiated RELATES_TO chains
- **Builds on:** NEW.
- **Evidence:** `causal_paths` requires **every** edge to be causal-family: `all(x IN r WHERE x.name IN $types)` (`runtime.py:588`). With causal edges a minority and only 5 families (`actors.py:339-343`), a fully-causal ≤6-hop path is improbable; `trace_cascade` then re-queries with `edge_types=None` (`zep_tools.py:2496-2498`) and presents a mixed-semantics path as generic.
- **Current:** Most calls return nothing or a path where ALLY_OF/REGULATES/SUPPLIES hops are interleaved with causal hops and rendered identically — "transmission path" loses meaning.
- **Proposed:** Replace all-causal-or-fallback with a **tagged mixed** path: allow non-causal hops but return each edge's family (derivable from `actors._REL_TYPE_VALENCE` / the ontology family map `ontology_generator.py:413-418`), require ≥1 causal hop, render families inline, and compute a "causal coverage" fraction so the report prefers high-coverage paths. Keep pure-causal as a *preferred ranking*, not a hard gate.
- **Knob/Change:** Code in `runtime._causal_paths` (return family) + `trace_cascade` rendering/ranking.
- **Impact:** Far more non-empty, interpretable cascades; the report can reason about partially-causal transmission and cite the causal share.
- **Risk:** Mixed paths can mislead — mitigated by per-hop family tags + coverage fraction.
- **Effort:** medium

### R2-KG-7 — The signal pack pins coalition/outcome aggregates into every section but never a causal spine
- **Builds on:** round-1 `REPORT-3` (signal pack).
- **Evidence:** `_build_signal_pack` composes only `simulation_outcomes`, `coalition_map`, `scenario_diff` (`report_agent.py:1350, 1385-1397`); it never calls `trace_cascade`/`n_hop_subgraph`/`faction_brief`. The causal tools are reachable only if the ReAct model chooses them (`:2008-2014`) AND supplies an exact name (`R2-KG-2`).
- **Current:** Even with the signal pack on, sections are grounded in *behavioral* aggregates (who acted, who clustered) but nothing about how shocks transmit — the transmission-mechanism reasoning the causal-edge investment was built for is optional and fragile.
- **Proposed:** Add a deterministic **causal spine** block to `_build_signal_pack`: for the top-N chokepoint/centrality nodes (`R2-KG-5`), pre-compute `n_hop_subgraph(center, causal families)` and the strongest source→outcome `causal_paths`, render with signs/lags (`R2-KG-1/3`), pin a bounded (~1500-char) block into every section. Pure deterministic graph computation, no LLM.
- **Knob/Change:** Extend `_build_signal_pack` under `REPORT_SIGNAL_PACK` (or new `GRAPH_CAUSAL_SPINE=true`); bounded prefix.
- **Impact:** Every section inherits a citable, sign-and-lag-annotated transmission map keyed on chokepoints — the single biggest lever for making the forecast read like a mechanism analysis, not a topic summary.
- **Risk:** Fixed prefix length per section (offset by `REPORT_SECTION_CONTEXT_MODE=brief`); self-suppresses when no causal edges exist.
- **Effort:** medium

### R2-KG-8 — Entity resolver's zero-overlap-alias merge is structurally impossible, and it never merges cross-label duplicates
- **Builds on:** round-1 `GRAPH-4`.
- **Evidence:** `plan_merges` hard-gates on `_name_match` **before** cosine (`zep_entity_resolver.py:117-118`), and `_name_match` needs exact/containment overlap (`:56-65`) — so a zero-character-overlap alias can never reach the cosine test, despite the docstring bragging about "Buzz Aldrin"=="Edwin Aldrin" (`actors.py:144-146`). Separately, candidates must share `primary_label` (`:113`), so an entity seeded as Person and extracted as Organization never merges.
- **Current:** `GRAPH_RESOLVE_ENTITIES=true` merges only surface-form variants that already share characters and label — the easy cases graphiti mostly caught. True synonyms and cross-type duplicates stay split, under-counting centrality and the agent pool for the most important multi-named actors.
- **Proposed:** (1) An alias-aware branch: when both nodes match (via actors aliases / `ALSO_KNOWN_AS` edges) to the same canonical, allow merge without `_name_match`, still gated by cosine≥threshold. (2) Allow cross-label merge only when one side is the generic `Entity` label (the common IS_A-target pollution case). Log every cross-label/alias merge.
- **Knob/Change:** Code in `plan_merges`; keep `GRAPH_RESOLVE_SIM_THRESHOLD=0.88`; audit to `entity_merges.json`.
- **Impact:** Recovers the genuinely-split duplicates the resolver advertises but cannot handle — fixes centrality and phantom personas.
- **Risk:** Looser gates raise over-merge — bound by cosine + alias-table evidence + audit log.
- **Effort:** medium

### R2-KG-9 — `insight_forge` retrieves `scope='edges'` only — well-described but sparsely-connected entities are invisible
- **Builds on:** NEW.
- **Evidence:** Every `insight_forge` sub-query searches `scope='edges'` (`zep_tools.py:1510, 1524`); entity insights derive purely from edge endpoints (`:1543-1561`) — nodes are never independently retrieved. A node with a strong summary but low degree has no path in unless it's an edge endpoint.
- **Current:** The flagship retrieval tool is edge-biased: freshly-seeded, specialized, low-degree-but-important actors (the ones that matter most for a forward forecast) are systematically under-surfaced, feeding the high-influence-actor coverage gaps the coverage tracker (`:1042-1065`) is meant to catch.
- **Proposed:** Add one node-scope hybrid search per call (`search_graph(query, scope='nodes', limit=10)`) and fold node summaries into `entity_insights`/`semantic_facts`, deduped via the existing MMR.
- **Knob/Change:** Code in `insight_forge`; optional `GRAPH_NODE_SCOPE_RETRIEVAL=true` (default true).
- **Impact:** Closes the edge-only blind spot — raises actor coverage, pulls specialized entities into the forecast.
- **Risk:** Marginally more retrieval per forge — bounded by limit + existing parallel fan-out.
- **Effort:** small

### R2-KG-11 — Communities are detected but inter-community tension is never modeled
- **Builds on:** NEW.
- **Evidence:** `list_communities` returns name/summary/members (`runtime.py:559-567`) and `faction_brief` renders members + summaries (`zep_tools.py:2467-2476`), but neither computes edges crossing community boundaries; the adversarial/allied valence is available (`actors.relation_valence/polarity`, `:579-618`) but never aggregated at the community level.
- **Current:** The report sees "who is in which faction" but not "how hard these factions pull against each other" — yet inter-bloc tension (count + net polarity of cross-community OPPOSES/SANCTIONS/COMPETES edges) is a strong, directly-forecastable conflict signal.
- **Proposed:** Add a deterministic inter-community tension matrix to `faction_brief`: for each ordered community pair, sum cross-boundary edges weighted by `relation_polarity`; report the most adversarial and most allied pairs. Pure computation over fetched communities + edges.
- **Knob/Change:** Extend `faction_brief` under `GRAPH_COMMUNITY_RETRIEVAL` (default true via `GRAPH_BUILD_COMMUNITIES`).
- **Impact:** A quantified bloc-conflict signal (which alliances are hardening/fracturing) that sharpens coalition-structure forecasts beyond a static membership list.
- **Risk:** Membership noise propagates — show only pairs above an edge-count floor.
- **Effort:** small

## Advanced latency & throughput (architectural)

### R2-EXEC-1 — The shared default `ThreadPoolExecutor` silently caps real LLM concurrency at `cpu+4`, defeating round-1's concurrency knobs
- **Builds on:** round-1 `GRAPH-1`/`GRAPH-3` (`GRAPHITI_MAX_COROUTINES`, `GRAPH_BUILD_CONCURRENCY`) — this is the precondition that makes them effective.
- **Evidence:** `llm_adapter.py:101` `await loop.run_in_executor(None, lambda: self._app_llm.chat_json(...))` and `embedder.py:84,91` `run_in_executor(None, self._encode, ...)` both pass `executor=None`. No `set_default_executor` anywhere (grep empty). CPython's default executor is `ThreadPoolExecutor(min(32, cpu+4))` = **20** on this 16-core host.
- **Current:** Every graphiti extraction (the ~5s/call bottleneck) is a blocking POST on the single shared default executor. Round 1 advises `GRAPHITI_MAX_COROUTINES=16` × `GRAPH_BUILD_CONCURRENCY=4` (= 64 desired in-flight), but only **20** worker threads exist and embedding encodes consume some of those 20 — so actual LLM parallelism is hard-capped at ~20 regardless of either knob. The headline 4-16x is throttled to ~2.5x and the proxy ceiling is never approached.
- **Proposed:** A dedicated, generously-sized executor for the blocking LLM HTTP I/O (I/O-bound, threads can far exceed CPU): in `AppGraphitiLLMClient.__init__` create `self._io_pool = ThreadPoolExecutor(max_workers=Config.GRAPH_LLM_EXECUTOR_WORKERS, thread_name_prefix='graphiti-llm')` and use it at `llm_adapter.py:101`. Size it to `GRAPHITI_MAX_COROUTINES × GRAPH_BUILD_CONCURRENCY + headroom` (~96). Keep embedding on a **separate** pool (`R2-EXEC-2`).
- **Knob/Change:** New `GRAPH_LLM_EXECUTOR_WORKERS` (default 64) + code in `llm_adapter.py`.
- **Impact:** Unlocks the 4-16x round-1 knobs nominally promise but cannot deliver — on the 7h build, the difference between an effective ceiling of ~20 and the configured ~64 in-flight (up to ~3x beyond what round 1 actually achieves).
- **Risk:** More concurrent POSTs can finally hit the real proxy ceiling — ramp per `CORRECTION-2` and watch p95/timeouts. Threads are cheap.
- **Effort:** small

### R2-EXEC-2 — CPU-bound embedding shares the LLM I/O executor — mutual starvation + non-thread-safe concurrent encode on one SentenceTransformer
- **Builds on:** round-1 `GAP-2`/`GRAPH-10`.
- **Evidence:** `embedder.py:84` `create()` and `:91` `create_batch()` both `run_in_executor(None, self._encode)` — same default pool as `llm_adapter.py:101`. `_encode` (`:63-71`) calls `model.encode` on a single shared `self._model` (one SentenceTransformer, `:45-61`) with only a load-time lock, none around encode.
- **Current:** During a concurrent build, dozens of LLM POSTs and dozens of torch forward passes contend for the same ~20 threads; every encode steals a thread that could be awaiting the proxy, and concurrent `model.encode` calls serialize on the GIL/torch internals (risking nondeterminism).
- **Proposed:** Give the embedder its own small compute executor (`ThreadPoolExecutor(max_workers=Config.EMBED_EXECUTOR_WORKERS, thread_name_prefix='embed')`, default 4) and pin torch/OMP threads (`torch.set_num_threads`, `OMP_NUM_THREADS`). With `R2-EXEC-1` this cleanly separates the large I/O pool from the small compute pool.
- **Knob/Change:** New `EMBED_EXECUTOR_WORKERS` (default 4) + thread pin; code in `embedder.py`.
- **Impact:** Removes embedding-vs-LLM thread contention and GIL serialization — embedding stops eating the concurrency `R2-EXEC-1` unlocks.
- **Risk:** Too-few embed threads make embedding the tail once LLM is parallelized — size jointly and measure.
- **Effort:** small

### R2-EXEC-3 — Per-batch barrier causes head-of-line blocking: 40 serial 10-episode gathers each wait for their slowest episode under the graph lock
- **Builds on:** round-1 `GRAPH-1`.
- **Evidence:** `graph_builder.py:523` `for i in range(0, total_chunks, batch_size)` submits batches strictly serially; each `client.graph.add_batch` → `add_episodes_concurrent`, and `runtime.py:509` holds the per-graph lock across the **entire** `asyncio.gather` of that 10-episode batch; the semaphore (`:490`) is re-created per batch.
- **Current:** ~400 chunks at `batch_size=10` → ~40 sequential barriers. Batch N+1 cannot begin until batch N's slowest of 10 commits; wall time ≈ 40 × p100(10 episodes), not `total/effective_concurrency`. A single slow extraction (schema-echo retry, `GRAPH-11`) stalls the next batch; the fan-out is drained to zero at each barrier.
- **Proposed:** Replace the 40 batches with **one** continuous fan-out: `add_episodes_concurrent(graph_id, all_chunks, concurrency)` once (N persistent workers pulling from a shared queue) so fast episodes keep the pipeline full while a slow one is in flight; stream per-episode progress. The per-graph lock is still held once for the whole pass (already the semantics), so no new race.
- **Knob/Change:** Code in `add_text_batches` (pass all chunks); keep `batch_size` only for progress granularity.
- **Impact:** Eliminates ~40 barrier syncs and the slowest-of-10 tax — converts a stop-start pipeline into a saturated one, typically 1.3-1.8x on top of raw concurrency.
- **Risk:** One ~400-coroutine gather — bounded by the existing semaphore; move progress reporting to per-episode completion.
- **Effort:** medium

### R2-EXEC-4 — No embed coalescing: graphiti calls `embedder.create()` one name at a time, paying N single forward passes
- **Builds on:** round-1 `GRAPH-10`/`GAP-2`.
- **Evidence:** `embedder.py:73-85` `create()` wraps one string into `[input_data]` and encodes it alone; graphiti calls `EmbedderClient.create` once per resolved node/edge name during dedup. `create_batch` (`:87-91`) exists but is reached only on the bulk path (`GRAPH-7`, non-default).
- **Current:** Each of thousands of name embeddings is its own `model.encode([one_text])` — torch per-call overhead dominates (batched encode of 32 names ≪ 32 single encodes).
- **Proposed:** A transparent micro-batch coalescer in front of `create()`: buffer incoming calls for ~10-20ms or until a max batch (64), issue one `model.encode(batch)`, fan results back to each awaiting future. Reaps batched-inference efficiency on the default path without changing graphiti.
- **Knob/Change:** New `EMBED_COALESCE_WINDOW_MS` (default 15) / `EMBED_COALESCE_MAX` (default 64); code in `embedder.py`.
- **Impact:** Cuts embedding wall-time several-fold by amortizing torch per-call overhead — material once `R2-EXEC-1/2` make LLM no longer the sole bottleneck.
- **Risk:** Up to one window of latency per embed (trivial vs 5s LLM calls) — flush immediately when the pool is idle to avoid pathological waits.
- **Effort:** medium

### R2-EXEC-5 — Embedding cache should be persistent on-disk (cross-resume, cross-seed), not just in-process LRU
- **Builds on:** round-1 `GAP-2`.
- **Evidence:** `embedder.py` has no cache; vectors are deterministic (`normalize_embeddings=True`, fixed `paraphrase-multilingual-MiniLM-L12-v2`). Round-1 `GAP-2` proposes only a process-local LRU, discarded on the frequent 7h-build crashes/resumes (`_load_research_handoff` resume path exists) and across `N_FORECAST_SEEDS` reruns/forks.
- **Current:** A build that dies at batch 35/40 (or a resume, or seed #2/#3) re-encodes every name from scratch; canonical entities ("OpenAI", seeded actors) are re-embedded on every run.
- **Proposed:** Back the cache with a small disk store (sqlite or `np.memmap`+key index) keyed by `(model_name, normalized_text)`, loaded once at init, write-through on each encode (lossless). Layer the in-process LRU (`GAP-2`) on top.
- **Knob/Change:** New `EMBED_DISK_CACHE_PATH` (default `uploads/graphiti_db/embed_cache.sqlite`); code in `embedder.py`.
- **Impact:** Near-zero embedding cost on resumes and every seed beyond the first — turns `N_FORECAST_SEEDS` embedding from N× to ~1×.
- **Risk:** Key includes `model_name` so a model swap auto-misses; concurrent writers need a simple WAL/lock.
- **Effort:** small

### R2-EXEC-6 — Sync OpenAI client built with untuned httpx — no HTTP/2 multiplexing, 20-slot keepalive throttles the proxy under concurrency
- **Builds on:** NEW.
- **Evidence:** `llm_client.py:107-118` `_build_openai_client` constructs `OpenAI(api_key=..., base_url=...)` with no `http_client`, so httpx defaults apply: `Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5s)`, HTTP/1.1 only, and the SDK's own `max_retries=2`. The graph build drives this from `R2-EXEC-1`'s executor threads.
- **Current:** Above ~20 sustained in-flight calls the keepalive pool churns (TCP+TLS re-setup per call) with no multiplexing; the SDK's hidden 2 retries stack under the app-level `MAX_RETRIES=3` (`:199`) and graphiti tenacity, inflating worst-case fan-out.
- **Proposed:** Pass a shared tuned client: `http_client=httpx.Client(http2=True, limits=httpx.Limits(max_keepalive_connections=128, max_connections=256, keepalive_expiry=30), timeout=...)` and set `max_retries=0` (let `llm_client.chat` own retry). HTTP/2 multiplexes all concurrent calls over one connection, eliminating per-call socket/TLS setup.
- **Knob/Change:** New `LLM_HTTP2` (default true), `LLM_HTTP_KEEPALIVE` (default 128); code in `_build_openai_client`.
- **Impact:** Removes connection-setup latency and the 20-slot bottleneck so `R2-EXEC-1`'s concurrency actually reaches the proxy — larger as concurrency rises.
- **Risk:** Proxy must support HTTP/2 (vibeproxy generally does; fall back to http1 on negotiation failure).
- **Effort:** small

### R2-EXEC-7 — Pre-warm the SentenceTransformer and pre-embed known actor names during the idle research/ontology stages
- **Builds on:** NEW; pairs with `R2-EXEC-5`.
- **Evidence:** `embedder.py:45-61` lazy-loads the 12-layer model on the **first** encode (inside the first `add_episode`), blocking the graph critical path. Research runs ~17min and ontology ~1min with the embedder idle. Seeded actor names are known from `actors.json` (`orchestrator.py:2943-2946`) before any chunk is ingested.
- **Current:** The model cold-load (seconds) and the first-touch embedding of every seeded endpoint (`add_triplet` embeds both endpoints serially, `runtime.py:441-464`) all land on the graph critical path, after 17min of idleness.
- **Proposed:** A background thread at research/ontology start that calls `embedder._ensure_model()` and `embed_texts([all actor names + aliases])` (populating `R2-EXEC-5`'s cache). When graph build begins, the model is hot and every seeded endpoint is a cache hit. Pure overlap — zero added wall time.
- **Knob/Change:** Code in `pipeline_orchestrator` (spawn a warm thread at `STAGE_RESEARCH`/`ONTOLOGY`); reuses the embed cache.
- **Impact:** Removes model cold-start and seeded-endpoint embedding from the critical path by hiding them under the 17min research — modest but free.
- **Risk:** Wasted work only if cancelled before graph; best-effort thread must not block research on a slow download.
- **Effort:** small

### R2-EXEC-8 — Batch/parallelize the post-sim decision-channel elicitations across rounds (independent given the frozen action log)
- **Builds on:** round-1 `SIM-6` — deepened with the precise independence argument.
- **Evidence:** `decision_channel.py:154-172` is a serial K-round LLM loop; each `_elicit_round_decisions` (`:56-61`) is a standalone `chat_json` whose **input** does not depend on prior rounds' LLM output — only `ws.step` ordering does. The channel runs **post-simulation** over a frozen `actions.jsonl`, so all per-round elicitations are fully independent.
- **Current:** A 72-round sim makes 72 strictly sequential post-sim calls even though the action log is already complete.
- **Proposed:** Two-phase: (1) fan out all `_elicit_round_decisions` under a semaphore (reuse `R2-EXEC-1`'s pool); (2) replay `ws.step(commitments)` in round order. WorldState stays deterministic. Once `R2-SIM-1` injects per-round state, the per-round prompt is still a pure function of frozen inputs, so independence holds.
- **Knob/Change:** Reuse `OASIS_SEMAPHORE` or new `DECISION_CHANNEL_CONCURRENCY`; default-off feature.
- **Impact:** 72 calls at concurrency 16 ≈ 4-5x faster — the decision channel stops being a serial tail.
- **Risk:** Collect all results before stepping to preserve trajectory/`converged_at` semantics.
- **Effort:** medium

### R2-EXEC-9 — Speculatively start PREPARE/persona-gen on the seeded-actor subgraph before full text extraction finishes
- **Builds on:** NEW.
- **Evidence:** PREPARE (`orchestrator.py:3064+`) begins only after STAGE_GRAPH fully completes (communities + resolution + integrity, `:2968-3062`). Personas come from entities via `ZepEntityReader().filter_defined_entities` (`:2907`), and the highest-salience entities are exactly the seeded actors, which exist after `seed_actors` (`:2946`) — before the ~400 text episodes finish.
- **Current:** The ~12% PREPARE band (persona gen, ~80 LLM calls) waits for the entire multi-hour GRAPH stage though its primary inputs (the seeded cast) are available within minutes of graph start.
- **Proposed:** After seeding + the first K batches land, speculatively launch persona generation for seeded/high-salience actors in parallel with the remaining extraction, then reconcile centrality-driven salience once the full graph completes (re-rank/cap, not regenerate). Only pre-generate clearly-seeded actors.
- **Knob/Change:** New `PREPARE_OVERLAP_GRAPH` (default false); orchestrator code.
- **Impact:** Overlaps the ~12% PREPARE band with the GRAPH tail, hiding most persona-gen latency — larger as graph stays dominant.
- **Risk:** Centrality priors aren't final until the full graph is built — finalize the cap/ranking after graph completion; medium realism risk, opt-in.
- **Effort:** large

### R2-EXEC-10 — Aggregate the audience tail into one weighted "public" commitment block + cache per-round roster elicitations
- **Builds on:** round-1 `SIM-5` (the 60-cap) and `SIM-8` (audience agents) — this is the latency counterpart (subsumes the dropped duplicate R2-SIM-10).
- **Evidence:** `decision_channel.py:118` `max_active_per_round=60`; `:155` `active = list(by_round[rnd].values())[:60]` takes the first 60 by dict/insertion order, not stake. With `SIM_AUDIENCE_SIZE` 200-500 most of the cast is dropped and which 60 survive is a timing accident; each surviving agent costs an elicitation.
- **Current:** In any sim >60 active agents, the modeled outcome is computed from an arbitrary 60-agent slice, and a 500-lurker audience would otherwise cost 500 elicitations.
- **Proposed:** Rank active agents by `outcome_power` (`R2-SIM-2`)/salience before truncating, **and** collapse the audience tail into a single weighted "public" commitment block (count × power) so 200-500 lurkers cost **one** elicitation, not 500 — preserving silent-majority mass without the cost. Cache per-round roster elicitations by `(scenario-set, roster-signature)` since rosters repeat once the sim converges.
- **Knob/Change:** `DECISION_CHANNEL_MAX_ACTIVE` + power-sorted selection + audience-aggregation + roster cache.
- **Impact:** Stops dropping decisive actors and timing artifacts from the outcome, and makes a large-audience sim affordable in the decision channel — directly enables `SIM-8` at scale.
- **Risk:** Aggregation must weight by count × power correctly; cache key must include the scenario set.
- **Effort:** small

## Prompt engineering & model utilization

### R2-SIM-11 — Agents are never anchored to the forecast question/scenarios during the sim — they chat generically
- **Builds on:** NEW.
- **Evidence:** `forecast_inputs` (base rates/drivers/indicators/scenarios) are injected only into config-gen context (`simulation_config_generator._build_context:863-869`) and the shared situation-brief prefix (`:770-779`); the per-round action is a bare `LLMAction` (`run_parallel_simulation.py:2429`) and persona prompts (`oasis_profile_generator.py:942-1047`) never state the forecast question or the candidate scenarios the agents are implicitly resolving.
- **Current:** Agents post generic topical chatter; they are never told "the question is whether Modern Mercantilism × AI resolves toward A/B/C." The decision channel then reverse-engineers commitments from unfocused chatter, weakening the link to the forecast variable.
- **Proposed:** Inject a compact **forecast frame** (the question + candidate scenarios + key drivers/indicators) into the shared persona prefix and, optionally, as a per-round system nudge for high-power agents ("act on your interests w.r.t. outcome X"). Keep it as *background*, not advocacy instruction, to avoid parroting. Reuse `forecast_inputs_block` (already imported).
- **Knob/Change:** New `SIM_FORECAST_FRAME` flag.
- **Impact:** Agents debate the actual decision variable, so emergent content and commitments are forecast-relevant — sharper, more differentiated outputs.
- **Risk:** Over-anchoring could make agents parrot scenarios — keep the frame as background.
- **Effort:** small

### R2-CAL-8 — Self-critique is one generic adversarial pass that can RAISE confidence — replace with a humility-monotone pre-mortem
- **Builds on:** NEW.
- **Evidence:** `forecast_extractor.py:281-324`: a single `_CRITIQUE_INSTRUCTIONS` pass; `:315-318` overwrites confidence with whatever the model returns (can **increase** it); `:310` re-normalizes the revised scenarios with no guard. No pre-mortem/inversion framing.
- **Current:** The "red team" can inadvertently make the forecast more confident, and it is a generic recalibration rather than a structured pre-mortem that surfaces the missing scenario / fattens the residual.
- **Proposed:** (1) Make critique **humility-monotone**: confidence may only stay or drop; residual may only grow. (2) Add a pre-mortem stage: for the top scenario, prompt "assume by horizon this did NOT happen — give the single most likely reason," and feed that reason as a candidate new scenario or as mass into the residual before renormalizing. (3) Optionally run bull/bear/base analyst personas and pool via `R2-CAL-2`.
- **Knob/Change:** Code in `self_critique_forecast`; flag `REPORT_PREMORTEM` (default true).
- **Impact:** Turns the red-team into a genuine overconfidence/base-rate-neglect corrector that can only push toward humility and surfaces blind-spot scenarios.
- **Risk:** Extra 1-2 calls; pre-mortem can over-fatten the residual — cap residual growth per pass.
- **Effort:** medium

### R2-RES-8 — The dossier judge self-grades with the same weak model and never sees the source list
- **Builds on:** round-1 `RESEARCH-3`.
- **Evidence:** `judge_dossier` creates the judge via `create_chat_model(model_name)` (`deerflow_research.py:1377`) — the **same** model that wrote the dossier; `build_judge_prompt` (`:1313-1328`) contains the 8 dimensions and the dossier text (truncated to 60000 at `:1380`) but **no** source list, tiers, or coverage metrics, so dimension 6 "evidence_grounding" and 7 "contradiction_handling" are graded from narrative alone. SKILL §7 wants a separate judge model when available.
- **Current:** A model prone to overconfident prose grades its own evidence quality favorably; the §8.2 non-negotiable bar is policed by the same reasoning that may have produced the thin profiles.
- **Proposed:** (1) Route the judge to the strong tier / a distinct model via a new `DEERFLOW_JUDGE_MODEL` (default = strong-tier id). (2) Inject structured evidence signals into the judge prompt: the `sources[]` tier histogram, `dossier_coverage` metrics, contested/quant counts — so grounding/contradiction are scored against provenance, not prose. (3) Raise the 60000 truncation or sample head+tail.
- **Knob/Change:** New `DEERFLOW_JUDGE_MODEL` + prompt change in `build_judge_prompt`.
- **Impact:** Makes the gate genuinely discriminating — an independent, evidence-aware judge is what prevents shipping a confident-but-hollow dossier.
- **Risk:** A second model adds one call per judge round (bounded by `ACTOR_DOSSIER_JUDGE_MAX_ROUNDS`); judge runs ≤ a few times, not per-episode.
- **Effort:** small

### R2-RES-9 — Deep-research phases are static and linear — the opening's own gap list is discarded instead of steering later passes
- **Builds on:** round-1 `RESEARCH-6`.
- **Evidence:** `DEEP_RESEARCH_PHASES` is a fixed 5-phase list with static focus prompts (`deerflow_research.py:92-148`); `run_research_stage:1197-1208` loops them unconditionally. The opening pass is instructed to "End with a concise research plan and gap list" (`:285-286, :98-102`) and `build_deep_phase_prompt` asks each pass to end with "## Gaps to carry into the next pass" (`:349) — but neither the opening's gap list nor a phase's carried gaps are ever passed into the next phase's prompt (`build_deep_phase_prompt` takes no prior-gaps arg).
- **Current:** Each deep pass re-derives where to look from scratch against a generic focus, so passes redundantly re-cover settled ground while leaving opening-identified holes unfilled — wasting the slow per-call budget AND under-covering the real gaps.
- **Proposed:** Thread prior gaps forward: accumulate the trailing "## Gaps to carry into the next pass" from each turn and inject into `build_deep_phase_prompt(prior_gaps=...)`; allow a pass to short-circuit when its gap list is empty. Adaptive (sharper coverage) and can cut wasted passes (latency).
- **Knob/Change:** Code in `run_research_stage` + `build_deep_phase_prompt` signature.
- **Impact:** Concentrates the expensive deep passes on actual unknowns — more comprehensive (real holes filled) and faster (skip closed phases), a strict improvement on `RESEARCH-6`.
- **Risk:** Gap-section parsing is heuristic; if absent, fall back to today's static focus (degrade-safe).
- **Effort:** small

### R2-PROMPT-1 — Use the provider's native structured-output/JSON mode for graphiti extraction instead of prompt-appended schema + echo-guard
- **Builds on:** round-1 `GAP-1` (tier routing) and `GRAPH-11` (stacked retries).
- **Evidence:** `llm_adapter.py:78-87` appends a JSON schema + a "don't echo the schema" guard to every extraction prompt and `:98-155` runs a 3-step rising-temperature retry on schema-echo/validation failure under graphiti's own ~4-attempt tenacity — worst case ~12 calls per failing extraction. The native OpenAI-compatible proxy supports `response_format`/tool-calling (already used report-side, `R2`/round-1 `REPORT-4`).
- **Current:** Extraction relies on prompt-engineered JSON discipline that a reasoning model intermittently violates (echoing the schema), triggering the retry stack — the exact tail risk round-1 `GRAPH-11` flags and `GAP-1` would worsen with a weaker fast model.
- **Proposed:** Pass the extraction schema via the provider's structured-output mode (`response_format={type:'json_schema', ...}` or native tool definition) so conformance is enforced server-side, then drop the echo-guard and shrink the inner retry to 1. Makes a fast non-reasoning model (`GAP-1`) safe to route in.
- **Knob/Change:** Code in `llm_adapter._generate_response` (use structured output when `supports_native_tools()`); coordinate with `GAP-1`/`LLM_TELEMETRY_ENABLED`.
- **Impact:** Cuts schema-echo retries to near-zero, de-risking the fast-tier routing that is round-1's single biggest latency lever.
- **Risk:** Provider structured-output fidelity varies — keep one fallback retry; verify node/edge counts vs a strong-only baseline.
- **Effort:** small

## Novel capabilities & architecture

### R2-CAL-15 — Forecasts are treated as independent — add a Monte-Carlo over shared drivers for correlated/conditional probabilities and a scenario tree
- **Builds on:** NEW; depends on `R2-CAL-6` (binary register).
- **Evidence:** `ensemble.py` buckets scenarios independently by name; the MECE set models one question; once binary forecasts exist each carries its own marginal `p_yes` with no joint model. `forecast_inputs` exposes a `drivers` list (`actors.py:1283-1295`) that is never used to couple outcomes.
- **Current:** Compound/conditional probabilities — `P(A and B)`, `P(B|A)` — can only be formed by multiplying marginals, which is wrong when forecasts share drivers (AI-capex growth and export-control tightening are correlated). A Bridgewater framework lives on exactly these joint/conditional statements.
- **Proposed:** A lightweight Monte-Carlo module: sample the few latent drivers from `forecast_inputs.drivers` (each Bernoulli/ordinal with a researched probability), express each scenario/binary `p` as a function of driver states (LLM elicits sensitivities once), draw N=10k → joint distribution, correlation matrix, conditional probabilities. Render a scenario tree / driver-sensitivity table.
- **Knob/Change:** New pure `monte_carlo.py` + `REPORT_MONTECARLO_DRIVERS` (default off); reuse `forecast_inputs.drivers`.
- **Impact:** Realistic correlated/conditional probabilities and an explicit scenario tree instead of independent marginals — a major realism/comprehensiveness gain for the framework section.
- **Risk:** Driver-sensitivity elicitation is an extra LLM step and can be mis-specified — keep driver count ≤6 and surface the mapping for audit.
- **Effort:** large

### R2-KG-10 — No forward-projected edges to the horizon — the graph is an as-of snapshot, but the report reasons about T_end
- **Builds on:** NEW; the natural complement to `R2-KG-3`/`R2-KG-4`.
- **Evidence:** The only forward-projection reference is an aspirational comment ("前投未来边", `actors.py:338`); grep finds no implementation. Seed edges anchor at `valid_at=as_of` (`graph_builder.py:537`); the only forward-dated facts are per-round sim feedback. The bi-temporal machinery and `as_of` search exist (`R2-KG-4`) but nothing populates the horizon side from causal structure.
- **Current:** When the report runs `as_of=T_end` views, the future slice contains only sim-emitted edges, not the projection of known causal mechanisms forward in time — the graph carries no structural hypothesis about the horizon state.
- **Proposed:** An optional post-build pass that, for each causal edge with a parseable `lag` (`R2-KG-3`) and `valid_at` near `as_of`, writes a projected edge at `valid_at = as_of + lag` (with `projected=true` and decayed strength), capped to the horizon. The report can then query `as_of=T_end` and see the projected transmission structure (which mechanisms have fired by the horizon).
- **Knob/Change:** New `GRAPH_FORWARD_PROJECT=true` (default off); writes clearly-tagged projected edges excluded from present-time retrieval.
- **Impact:** Gives the forecast an explicit, queryable horizon-state structure derived from known mechanisms + lags — a major realism upgrade for dated forecasts.
- **Risk:** Projected edges are hypotheses — must be tagged and excluded from as-of=now views; cap count and decay strength to avoid inflating confidence.
- **Effort:** large

### R2-ARCH-1 — Unify the three quantitative objects (WorldState shares, spine scenarios, binary register) under one consistency reconciler
- **Builds on:** synthesizes `R2-CAL-3`, `R2-CAL-6`, `R2-CAL-16`, `R2-CAL-18`.
- **Evidence:** Today three quantitative objects are produced independently and never reconciled: `WorldState.shares` (`worldstate.py:90-138`), the spine `scenarios` (`forecast_extractor.py:129-189`), and (once it exists) the binary `forecast_register` (`R2-CAL-6`). The spine is built from prose, not from the shares or the graded `quantitative_facts` (`R2-CAL-16`), and nothing checks that a binary like "P(export controls tighten)" is consistent with the scenario that implies it.
- **Current:** The pipeline can publish a 47% modeled share, a 35% spine probability for the same scenario, and a binary `p_yes` that contradicts both — three numbers for one reality with no coherence check.
- **Proposed:** A deterministic reconciler stage after spine + register derivation: assert each binary's `p_yes` is consistent (within tolerance) with the marginal implied by the scenario probabilities; surface `|p_spine − share_worldstate|` (`R2-CAL-18`); on material incoherence, demote confidence / widen intervals (`R2-CAL-17`) and log the disagreement table into the appendix. This makes the WorldState → spine → register chain a single auditable object rather than three disconnected ones.
- **Knob/Change:** New reconciler in the report stage consuming the three objects; flag `REPORT_FORECAST_RECONCILE` (default true once the register lands).
- **Impact:** One coherent, internally-consistent forecast surface — the structural backbone a Bridgewater framework presents, and the precondition for trusting any single published number.
- **Risk:** Tolerance tuning; keep it advisory (demote/annotate, never silently rewrite) so genuine evidence-driven divergence survives with a logged rationale.
- **Effort:** medium

## Round 2 roadmap

Sequenced so the levers that *unlock* prior work land first. Re-profile after each tier (`CORRECTION-3`).

**Quick — config / prompt-only (land before anything else; several are preconditions):**
- **Turn the channel on:** `SIM_DECISION_CHANNEL=true` — hard precondition for the entire `R2-SIM-1`/`R2-CAL-3`/`R2-CAL-13` thread; without it the spine sees only activity volumes.
- `R2-CAL-4` probability floor (`FORECAST_PROB_FLOOR=0.03`), `R2-CAL-11` spine `max_tokens` 2048→6144 + WARNING log — trivial calibration safety.
- `R2-RES-7` `as_of_date` validation, `R2-RES-1` flip `RESEARCH_QUALITY_GATE=true` + add `RESEARCH_QUALITY_FLOOR` (after its producer lands in Medium).
- `R2-EXEC-6` `LLM_HTTP2=true`, `R2-EXEC-7` pre-warm thread — free latency overlap.
- `R2-SIM-11` `SIM_FORECAST_FRAME`, `R2-CAL-8` `REPORT_PREMORTEM`, `R2-RES-9` gap-threaded deep phases, `R2-RES-8` `DEERFLOW_JUDGE_MODEL` — prompt-only quality.
- **This regime (local reasoning-model proxy):** these matter *because* the proxy is latency-bound with 0 quota errors — config flips cost nothing and the prompt-only quality levers exploit the reasoning model's strength without adding rate-limit risk.

**Medium — new fields / algorithms (the calibration + executor core):**
- **The executor unlock (do first in this tier):** `R2-EXEC-1` dedicated LLM I/O pool + `R2-EXEC-2` separate embed pool — **without these, round-1's concurrency knobs and `R2-EXEC-3/8` are throttled to ~20 in-flight.** Then `R2-EXEC-3` continuous fan-out, `R2-EXEC-4` embed coalescing, `R2-EXEC-5` disk embed cache, `R2-EXEC-8` parallel decision channel, `R2-EXEC-10` audience aggregation.
- **Calibration core:** `R2-CAL-1` spine self-consistency (cheap distribution, no sim rerun — prefer over more `N_FORECAST_SEEDS`), `R2-CAL-2` log-odds pooling, `R2-CAL-13`/`R2-SIM-9` `probability_band` parse (kills the uniform prior — *the* cheapest high-leverage lever), `R2-CAL-3` anchor spine on WorldState, `R2-CAL-16` quant-facts into spine, `R2-CAL-6`/`R2-DETAIL-1` binary register, `R2-CAL-17` numeric intervals, `R2-CAL-7`/`R2-CAL-12` criteria + anchor validators, `R2-CAL-9`/`R2-CAL-10`/`R2-CAL-14` ensemble/Brier/bin smoothing, `R2-CAL-18` model-vs-sim divergence.
- **Sim fidelity:** `R2-SIM-1` couple the channel to sim state, `R2-SIM-2`/`R2-SIM-3` outcome-power + incentives, `R2-SIM-4` effective stance, `R2-SIM-5`/`R2-SIM-6` live dynamics + position memory, `R2-SIM-8`/`R2-SIM-12`/`R2-SIM-13`/`R2-SIM-14` sentiment/inertia/heterogeneity/event-shocks.
- **KG reasoning:** `R2-KG-1`/`R2-KG-3` sign+lag projection, `R2-KG-2` name resolution, `R2-KG-4` bi-temporal traversal, `R2-KG-5` chokepoints, `R2-KG-6` tagged-mixed paths, `R2-KG-7` causal spine in signal pack, `R2-KG-8` alias/cross-label merge, `R2-KG-9` node-scope retrieval, `R2-KG-11` inter-community tension.
- **Research depth:** `R2-RES-2` per-actor fan-out (the differentiation ceiling), `R2-RES-3`/`R2-RES-5`/`R2-RES-10`/`R2-RES-11` evidence→confidence wiring, `R2-RES-4`/`R2-RES-6`/`R2-RES-12` recency/completeness/diversity, `R2-DETAIL-2` outline-after-spine, `R2-DETAIL-3` raise spine-input caps.
- `R2-CAL-5` recalibration loop — gate on accumulated resolved labels.
- **This regime:** `R2-PROMPT-1` structured-output extraction is the enabler that makes round-1 `GAP-1` (fast-tier routing) safe on this proxy — sequence it alongside the executor unlock; `R2-CAL-1` over `N_FORECAST_SEEDS` is specifically chosen because re-running the 7h-graph-reusing sim+report per seed is the wrong place to spend wall-clock on a latency-bound proxy.

**Deep — new stages / architecture (highest blast radius, opt-in):**
- `R2-SIM-7` dynamic homophily rewiring, `R2-EXEC-9` speculative PREPARE overlap.
- `R2-CAL-15` Monte-Carlo over shared drivers + scenario tree, `R2-KG-10` forward-projected horizon edges, `R2-ARCH-1` the three-object reconciler — together these form the "framework engine" (correlated joint probabilities + queryable horizon structure + one coherent forecast surface) that distinguishes a Bridgewater framework from a scenario list.
- **This regime caveat:** the Deep items add LLM-driver elicitation and extra graph passes; on a ~5s/call latency-bound proxy they are only worth it once the Medium executor + calibration tier has re-profiled the binding resource — keep them flag-gated and validate they don't reintroduce a serial tail.
