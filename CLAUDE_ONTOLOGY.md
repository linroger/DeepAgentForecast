# CLAUDE_ONTOLOGY.md — A Generalized Forecast Ontology, Entity-Detail, and Relation Schema for DeepResearchForecast

> Scope: a domain-agnostic ontology + entity-detail + relation schema, with a structured output format, **tailored to this app's actual pipeline** (DeerFlow research → `actors.json` → Graphiti graph → OASIS personas → simulation → forecast report). Every recommendation is grounded in the current code and maps to a concrete consumer function, and every new field is **degrade-safe** (optional; absent → today's behavior), matching the existing `actors.py` invariant.

---

## 0. How to read this document

There are three artifacts the pipeline cares about, and this document gives a generalized, cross-domain schema for each:

1. **The Type Ontology** — `{entity_types, edge_types}` consumed by `graph_builder.set_ontology()` and `ontology.json`. Small, Graphiti-bounded (≤10 each). *What kinds of things exist.*
2. **The Entity-Detail (instance) layer** — the per-entity dossier carried in `actors.json` and rendered into personas/reports. *Who each thing is, what it wants, and who it is connected to.*
3. **The Relation (instance) layer** — `relationships[]`, the directed typed edges that seed the graph, the follow-graph, stances, and report coalition reasoning. *How things are connected, and with what polarity.*

The redesign rests on one reframing:

> **Move from a "social-actor ontology" to a "forecast-mechanics ontology."** The question the ontology must answer is not "who is in this story" but **"what can move the outcome, who decides it, what evidence supports that, and how does it propagate through the simulation."**

---

## 1. The problem, precisely (grounded diagnosis)

The user's two complaints — *(a) minor entities (reporters, sources, news outlets) are treated as key actors*, and *(b) the relevant actors are too thinly described* — are both real and have specific root causes in the current code.

### 1.1 Minor entities become first-class actors and even simulation agents

- **The default ontology is person/social-media-centric.** `ONTOLOGY_TEMPLATE` defaults to `social_opinion` (`config.py:134`), whose prompt forces **exactly 10 entity types** with `Journalist`, `Celebrity`, `MediaOutlet` as *first-class types* and `Person`/`Organization` as the forced last two (`ontology_generator.py:96, 136-161`). A reporter or a news outlet is, by construction, a top-level entity kind — not a citation.
- **The research stage has no source-vs-actor rule.** The extraction prompt says "include ONLY actors CENTRAL to the central_question … exclude entities mentioned only in passing" (`deerflow_research.py:697-699`), but `Media` is an allowed actor `type` (`:663`) and **nothing tells the model that a thing it only cites is a source, not an actor.** Cited outlets can land in both `actors[]` (as `type:Media`) and `sources[]`.
- **The entity→agent filter has no salience gate.** `zep_entity_reader.filter_defined_entities` keeps **any node carrying any non-default label** (`zep_entity_reader.py:252-260`); the simulation passes no type allowlist (`simulation_manager.py:238, 287-291`). So *every typed node* — including events, technologies, and outlets — is a candidate agent.
- **The agent cap ranks unmatched nodes by raw degree.** When candidates exceed `OASIS_MAX_AGENTS` (80), the keep-ranking is the 3-tuple `(matched_actor_flag, influence_weight, degree)` (`simulation_manager.py:299-314`). For any entity that did **not** match an `actors.json` row, the *only* signal is `len(related_edges)` — graph degree. A heavily-cited news outlet is highly connected, so **a peripheral-but-well-connected outlet can outrank a pivotal-but-sparsely-linked principal.** There is no centrality, recency, decision-power, or role signal.
- **Salience is a crude, decoupled afterthought.** The only importance signal is the per-actor free-text `influence ∈ {high, medium, low}` mapped to `{2.5, 1.5, 1.0}` (`actors.py:95-103, 184-194`). It is not part of the ontology, is absent for unmatched nodes, and is a 3-bucket label rather than a reasoned score.

**Net:** importance is inferred from *graph connectivity and a 3-way label*, not from *causal role in the forecast*. That is exactly why amplifiers crowd out principals.

### 1.2 The "relevant actors" are described too thinly

- **The research schema asks for thin identity fields.** `role`, `stance`, `influence` are single-clause comments; `description` is explicitly capped at "ONE sentence" (`deerflow_research.py:554, 663-666`). The richer motivational block (`goals/constraints/assets/vulnerabilities/stated_vs_revealed`) is **optional, flag-gated, and silently droppable** (`:553-562, 700-702`).
- **Values, beliefs, and incentives are not first-class anywhere.** Neither `actors.json` (`actors.py:12-21`) nor the persona prompt asks for an actor's *values*, *worldview/beliefs*, or *incentive structure* (gain/loss under each outcome) as structured fields. The persona prompt asks for `stance` and a free-text "memory"/"background" only (`oasis_profile_generator.py:919-966`).
- **The relational roster the user wants is implicit and lossy.** Allies/opponents/competitors/customers/suppliers/backers/investors exist *only* as edges in `relationships[]` (8-type vocabulary: `ALLY_OF|OPPOSES|COMPETES_WITH|REGULATES|DEPENDS_ON|PARTNERS_WITH|INFLUENCES|OTHER`, `actors.py:24`). They are never materialized per-entity as "X's backers: …, competitors: …". The report background surfaces only `name/type/role/stance/influence` per actor plus a flat edge list (`actors.py:453-481`) — it cannot enumerate an actor's alliance/opposition network without re-deriving it.
- **Rich fields reach only *matched* actors.** `actor_briefing()` (which renders goals/constraints/assets/vulnerabilities) only fires when an entity matches an `actors.json` row (`oasis_profile_generator.py:1216-1218`). The majority of graph nodes — and every node when `actors` is `None` — fall back to a generic template ("a recognized expert who shares insights…", `:1057-1128`).

### 1.3 Relations are typed but behaviorally collapsed

- **Polarity is lost in the follow graph.** `OPPOSES`, `COMPETES_WITH`, and `PARTNERS_WITH` all produce the *same* symmetric mutual-follow (`actors.py:417-419`). A rival follows you exactly like a partner. Only `ALLY_OF/DEPENDS_ON/INFLUENCES/REGULATES` are asymmetric. The `sign ∈ {ally, rival, neutral}` field is **declared but read by nothing behavioral** (`actors.py:26`).
- **Relation type never seeds sentiment.** `sentiment_bias` is derived from `stance` buckets only (`simulation_config_generator.py:1615-1620`); a "competes_with"/"backer" edge never initializes directional sentiment.
- **Echo chambers ignore relations.** Clustering is by `(stance, topic, influence)` only (`:688-692`) — competitors are not pushed into opposing clusters, allies not pulled together.
- **`strength`, `since`, `until`, `grade` are inert for simulation** — only folded into the Graphiti edge fact string (`graph_builder.py:374-383`).

The schema below fixes all three classes of problem while preserving the existing contract.

---

## 2. Design principles

1. **Forecast-mechanics first.** Every entity and relation must earn its place by affecting the forecast: it moves the outcome, is affected by it, gates it, or provides evidence about it. "Appears in the story" is not sufficient.
2. **Separate *kind* from *role* from *salience*.** A thing's archetype (kind), its causal role-class (principal vs amplifier vs context), and its salience score are three independent axes. Today they are conflated into "has a label + degree."
3. **Not everything important becomes an agent.** Actors and collectives can be personas; assets, events, signals, claims, constraints, places, sources, and scenarios are *context that shapes agents* — never simulated accounts.
4. **Relations are directional, typed, *valenced*, evidence-backed, and time-aware.** Allied vs adversarial must survive into simulation dynamics.
5. **Materialize the relational roster per entity.** Allies/opponents/competitors/customers/backers/etc. should be a first-class projection on each entity so the report and personas can read them directly.
6. **Two layers, one source of truth.** A compact Graphiti-compatible **Type Ontology** for extraction, and a richer **Forecast Instance Layer** for everything downstream. The instance layer is the authority; the type layer is derived/compatible.
7. **Degrade-safe and additive.** Every new field is optional. Absent → exactly today's behavior. New behavior is flag-gated with current-behavior defaults. This mirrors the existing `actors.py` invariant (`actors.py:83-85`) and the project's house rule.

---

## 3. The two-layer ontology model

```
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 1 — TYPE ONTOLOGY  (ontology.json; ≤10 entity types, ≤10 edges)│
│   { entity_types[], edge_types[], analysis_summary }                 │
│   Consumed by graph_builder.set_ontology(); teaches the extractor    │
│   WHAT KINDS of nodes/edges to recognize. Domain-specific type NAMES, │
│   each mapped to a stable cross-domain ARCHETYPE + ROLE-CLASS.        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ every type name → archetype + role-class
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 2 — FORECAST INSTANCE LAYER  (actors.json, extended additively)│
│   { central_question, as_of_date, situation_brief,                   │
│     entities[]  ← supersedes/extends actors[]  (per-entity dossier),  │
│     relations[] ← supersedes/extends relationships[] (valenced edges),│
│     events[], signals[], claims[], sources[], forecast_inputs }       │
│   The authority. Carries salience, the relational roster, values/     │
│   beliefs/incentives, evidence, confidence, and simulation hints.     │
└─────────────────────────────────────────────────────────────────────┘
```

**Backward compatibility:** `entities[]` is a superset of today's `actors[]` (an actor is an entity whose `archetype == "actor"`); `relations[]` is a superset of `relationships[]`. The current keys remain valid and are emitted as a derived projection (see §11), so `actors.py` and every existing consumer keep working unchanged.

---

## 4. Entity archetypes (the cross-domain "kind" axis)

Every generated entity type maps to exactly one archetype. Archetypes are stable across all domains; the *type names* are domain-specific (e.g. `CentralBank`, `Foundry`, `Candidate`). This is what lets the same schema serve markets, geopolitics, products, litigation, elections, public health, supply chains, climate, and social opinion.

| Archetype | Can act? | Meaning | Cross-domain examples | Simulation eligibility |
|---|---|---|---|---|
| `actor` | yes | A decision-maker / agent that can choose, speak, allocate, or commit. | Company, Agency, Investor, Politician, Union, ActivistGroup | **Persona-eligible** (if salience passes). |
| `collective` | aggregate | A population/audience whose *aggregate* behavior matters. | Voters, Consumers, RetailInvestors, Developers, Patients | **Representative-persona-eligible** (sampled audience). |
| `institution_rule` | sets rules | A standing rule, body-as-rulebook, or governance regime. | Regulation, ExportControl, Treaty, Standard, Mandate | Context (its *enforcer* may be an `actor`). |
| `asset_object` | no | A tracked object the forecast is *about* but which cannot act. | Product, Model, Stock, Commodity, Bill, DrugCandidate | Context only. |
| `event` | no | A dated/triggered occurrence that changes state. | Election, EarningsCall, CourtRuling, Launch, Shock | Scheduled simulation trigger; timeline anchor. |
| `signal` | no | A measurable indicator / observable input. | InflationRate, PollResult, ShipmentVolume, ChurnRate | Forecast input / monitoring trigger; report evidence. |
| `claim_narrative` | no | A contested belief, frame, thesis, or rumor. | BullCase, SafetyConcern, FraudAllegation, PolicyFrame | Persona belief seed; contested-claim; report argument. |
| `constraint_resource` | no | A capacity, bottleneck, dependency, or finite resource. | ComputeCapacity, BudgetCap, FabCapacity, TalentPool | Forecast driver / causal limiter. |
| `place_jurisdiction` | no | A geographic/legal/market scope. | China, EU, California, AppStoreMarket | Scope qualifier for actors, rules, events. |
| `source` | no | An evidence-producing object/outlet **cited but not acting**. | SEC Filing, PeerReviewedStudy, NewsOutlet-as-citation, Pollster | **Never a persona.** Lives in `sources[]`. |
| `scenario` | no | A possible future state. | BaseCase, UpsideCase, TailRisk | Structured-forecast branch; report frame. |

**The rule that fixes "reporters/outlets as actors":**

> A news outlet, reporter, analyst, or pollster is an `actor` **only if it is itself a principal or amplifier whose behavior moves the outcome** (e.g. the forecast turns on its coverage). If it appears only because you *cited* it, it is archetype `source` and belongs in `sources[]` — it must **not** appear in `entities[]` as an actor and must **not** become a simulation agent.

This is enforced by an explicit prompt rule (§10), a `source`-vs-`actor` disambiguation at extraction, and a simulation eligibility gate that admits only `actor`/`collective` archetypes above a salience threshold (§9).

---

## 5. The actor role-class and salience model (the core fix)

For `actor`/`collective` entities, two further axes decide *how much they matter* and *how they behave*, replacing the degree-only ranking.

### 5.1 Role-class (causal function in the forecast)

| Role-class | Definition | Persona behavior implication |
|---|---|---|
| `principal` | Its decisions directly move the outcome. The forecast hinges on what it chooses. | Originates positions; high activation; drives cascades. |
| `arbiter` | Sets or enforces the rules that gate the outcome (regulator, court, standards body). | Reactive, authoritative; monitored by principals; rulings are events. |
| `stakeholder` | Materially affected by the outcome but limited agency over it (customers, populations, supporters, passive investors). | Reacts to principals; aggregates into audience sentiment. |
| `amplifier` | Shapes information flow / narrative but does not decide the outcome (media, reporters, analysts, commentators, influencers). | **Amplifies/reframes others' positions rather than originating them**; never the cause, often the channel. |
| `intermediary` | Connects principals (supplier, distributor, broker, financier-as-conduit). | Bridges clusters; propagates shocks along dependency edges. |

Distinguishing `amplifier` from `principal` is what stops a newspaper from being modeled as a decision-maker even when it *is* legitimately an actor: an amplifier persona is told to *echo and frame*, and its salience is capped below principals unless the forecast is explicitly about media influence.

### 5.2 Salience (a reasoned 0–1 score, not a 3-way label)

Replace the `high/medium/low → {2.5,1.5,1.0}` heuristic with a small, explainable score computed from independent signals. Each sub-signal is `0..1`; the composite is a weighted blend, emitted with its basis.

| Signal | Question | Default weight |
|---|---|---|
| `decision_power` | Can this entity's choices move the outcome? (principals high, amplifiers low) | 0.35 |
| `stake` | How much is it affected / how strong is its incentive to act? | 0.20 |
| `centrality` | How structurally connected is it in the causal/relationship graph? (degree → *normalized* + later PageRank) | 0.15 |
| `evidence_grade` | How well-attested is its role? (maps to source tier S1–S4 / Admiralty grade) | 0.15 |
| `recency` | How current is its relevance to the as-of horizon? | 0.15 |

```
salience.score = Σ (weightᵢ · signalᵢ)        # 0..1, emitted with per-signal basis
salience.tier  = high (≥0.66) | medium (0.33–0.66) | low (<0.33)   # back-compat bucket
```

`salience.tier` preserves the legacy `influence` contract (so `influence_weight()` and the agent-cap keep working), while `salience.score` gives the agent-cap a continuous, multi-signal ranking that **does not let degree alone dominate** (§9). `decision_power` is the dominant term precisely so that amplifiers and stakeholders cannot outrank principals on connectivity.

---

## 6. The generalized Entity-Detail schema

This is the per-entity dossier the user asked for — *roles, values, beliefs, incentives, allies, opponents, customers, competitors, supporters, backers, investors* — generalized to any domain. It **supersedes and extends** today's actor object; every legacy field is retained (mapping shown in the right column), and every new field is optional.

```jsonc
{
  // ── Identity & classification ───────────────────────────────────────
  "id": "stable-slug",                       // stable key for seeding/resolution
  "canonical_name": "TSMC",                  // ← actors[].name  (REQUIRED)
  "aliases": ["台积电", "Taiwan Semiconductor", "2330.TW"],  // ← actors[].aliases
  "entity_type": "Foundry",                  // Layer-1 type name
  "archetype": "actor",                      // §4 — gates persona eligibility
  "role_class": "principal",                 // §5.1 — principal|arbiter|stakeholder|amplifier|intermediary
  "description": "The Taiwanese contract chip foundry.",  // ← actors[].description (1-line disambiguator)
  "jurisdiction": "Taiwan",                  // place scope (also a graph attr)
  "sector": "Semiconductors",
  "role_in_question": "Sole-source leading-edge capacity is the binding constraint on AI compute supply.",  // WHY it matters (≠ what it is)

  // ── Salience (§5.2) ────────────────────────────────────────────────
  "salience": {
    "score": 0.92, "tier": "high",           // tier ← actors[].influence (back-compat)
    "signals": { "decision_power": 0.95, "stake": 0.9, "centrality": 0.85,
                 "evidence_grade": 0.9, "recency": 0.95 },
    "basis": "Controls >90% of <5nm capacity; every AI-compute scenario routes through it."
  },

  // ── Profile: who they are and what drives them ─────────────────────
  "worldview": {                             // NEW — authenticity + stance propagation
    "values": ["operational excellence", "customer trust", "political neutrality"],
    "beliefs": ["leading-edge is won on yield, not price", "geopolitical hedging is existential"],
    "identity": "engineering-first national champion",
    "frame": "We are a neutral foundry, not a competitor to our customers."
  },
  "goals": [                                 // ← actors[].goals (now ranked/structured)
    { "goal": "Hold leading-edge process leadership", "priority": "high", "horizon": "long" }
  ],
  "incentives": [                            // NEW — payoff structure (gain/loss under outcomes)
    { "driver": "Revenue concentration in AI accelerators",
      "gains_if": "AI capex supercycle continues",
      "loses_if": "Export controls fragment its customer base",
      "intensity": "high" }
  ],
  "constraints": [                           // ← actors[].constraints
    { "constraint": "Fab build lead time ~2-3 yrs", "severity": "high", "binding_when": "demand spikes" }
  ],
  "resources": [                             // ← actors[].assets (renamed for generality)
    { "resource": "EUV-equipped leading-edge fabs", "forecast_use": "Capacity = supply ceiling" }
  ],
  "vulnerabilities": [                       // ← actors[].vulnerabilities
    { "vulnerability": "Geographic concentration in Taiwan", "trigger": "cross-strait escalation" }
  ],
  "decision_rights": [                       // NEW (esp. arbiters/principals)
    { "decision": "Allocation of scarce leading-edge wafers", "scope": "its own fabs", "limits": "long-term supply agreements" }
  ],
  "stance": {                                // ← actors[].stance (now structured)
    "toward": "US-China chip decoupling", "label": "cautious-neutral",
    "stated": "compliant with all export rules",
    "revealed": "diversifying fabs to US/Japan",     // ← actors[].stated_vs_revealed (the gap is evidence)
    "intensity": 0.6
  },
  "information_state": {                      // ← actors[].memory (now structured)
    "knows": ["true yield curves", "customer order books"],
    "believes": ["demand durability"],
    "blind_spots": ["second-order policy moves"]
  },

  // ── Relational roster (the user's explicit ask) ────────────────────
  // A per-entity PROJECTION of relations[] (auto-derivable; see §11.4), so the
  // report and personas can read an actor's network directly without re-deriving it.
  "relational_roster": {
    "allies":       [ { "name": "Apple", "basis": "largest customer + co-development", "strength": "high" } ],
    "partners":     [ { "name": "ASML",  "basis": "EUV tool supplier",                 "strength": "high" } ],
    "opponents":    [ ],
    "competitors":  [ { "name": "Samsung Foundry", "basis": "leading-edge rivalry",    "strength": "high" },
                      { "name": "Intel Foundry",   "basis": "emerging rival",          "strength": "medium" } ],
    "customers":    [ { "name": "NVIDIA", "basis": "primary AI-accelerator client",    "strength": "high" } ],
    "suppliers":    [ { "name": "ASML",   "basis": "EUV lithography",                  "strength": "high" } ],
    "backers_investors": [ ],
    "supporters":   [ { "name": "Taiwan Government", "basis": "strategic protection",   "strength": "high" } ],
    "regulators":   [ { "name": "US BIS", "basis": "export-control authority",         "strength": "high" } ],
    "dependents":   [ { "name": "NVIDIA", "basis": "supply-constrained on TSMC",       "strength": "high" } ]
  },

  // ── Simulation hints (only for actor/collective) ───────────────────
  "simulation": {
    "persona_seed": "Measured, technical, apolitical foundry voice; defers on geopolitics, precise on capacity.",
    "activity_level": "medium", "response_speed": "slow",
    "interested_topics": ["leading-edge capacity", "export controls", "AI demand"],
    "sentiment_priors": { "Samsung Foundry": -0.3, "Apple": 0.4, "US BIS": -0.1 }   // seeded from valenced relations (§7)
  },

  // ── Evidence, confidence, time ─────────────────────────────────────
  "grade": "A1",                             // ← actors[].grade (Admiralty [A-F][1-6])
  "evidence": [ { "claim": "controls >90% of <5nm", "source_id": "S1", "tier": "S1", "confidence": "high" } ],
  "confidence": { "overall": "high", "rationale": "multiple S1 filings + analyst consensus" },
  "as_of_date": "2026-06-25",
  "open_questions": ["How fast can US/Japan fabs reach leading-edge parity?"]
}
```

### Entity-detail rules

- **`canonical_name` + `archetype` + `salience.score`** are the spine: name for resolution, archetype for persona eligibility, salience for ranking.
- **`worldview.values` / `worldview.beliefs` / `incentives`** are the new "why" fields the report and personas have been missing. They should be evidence-backed, not invented.
- **`relational_roster` is a derived projection** of `relations[]` (so it is not duplicate data entry) — but materializing it lets the report enumerate "X's backers / competitors / regulators" and lets personas know *who to @, ally with, or attack*.
- **`simulation.*` exists only for `actor`/`collective`.** A `signal`, `event`, `constraint`, or `source` entity carries `forecast_use`/`evidence` but no persona block — so `InflationRate` or a cited outlet never becomes an awkward simulated account.
- **Confidence and evidence attach to the entity**, not only to the final forecast, so the report can calibrate.

---

## 7. The generalized Relation schema (fixing polarity collapse)

### 7.1 Relation families (cross-domain) and the canonical edge vocabulary

Every concrete edge maps to a stable family with an explicit **valence** and **direction**. Valence is what the current pipeline throws away.

| Family | Valence | Canonical edges (direction: source → target) | Replaces / extends today |
|---|---|---|---|
| `alignment` | allied (+) | `ALLY_OF`, `SUPPORTS`, `PARTNERS_WITH`, `COALITION_WITH` | `ALLY_OF`, `PARTNERS_WITH` |
| `antagonism` | adversarial (−) | `OPPOSES`, `COMPETES_WITH`, `LITIGATES_AGAINST` | `OPPOSES`, `COMPETES_WITH` |
| `governance` | directional/neutral | `REGULATES`, `APPROVES`, `SANCTIONS`, `INVESTIGATES` | `REGULATES` |
| `economic_exchange` | transactional | `SUPPLIES`, `CUSTOMER_OF`, `FUNDS`, `INVESTS_IN`, `BACKS`, `OWNS` | `OTHER`+`relation_label` (SUPPLIES/FUNDS/OWNS) |
| `dependency` | directional | `DEPENDS_ON`, `BOTTLENECKED_BY`, `EXPOSED_TO` | `DEPENDS_ON` |
| `information_influence` | directional | `INFLUENCES`, `AMPLIFIES`, `ENDORSES`, `CRITICIZES`, `REPORTS_ON` | `INFLUENCES` |
| `causal` | signed | `CAUSES`, `ENABLES`, `CONSTRAINS`, `TRIGGERS`, `ACCELERATES`, `DELAYS` | (new — forecast mechanics) |
| `evidential` | neutral | `MEASURES`, `SIGNALS`, `SUPPORTS_CLAIM`, `CONTRADICTS_CLAIM` | (new — signals/claims) |
| `scenario_link` | signed | `INCREASES_PROBABILITY_OF`, `DECREASES_PROBABILITY_OF`, `RESOLVES_TO` | (new — forecast output) |

The first six families cover everything the user listed — **customers** (`CUSTOMER_OF`), **suppliers** (`SUPPLIES`), **competitors** (`COMPETES_WITH`), **backers/investors** (`BACKS`/`INVESTS_IN`/`FUNDS`), **supporters** (`SUPPORTS`), **allies** (`ALLY_OF`/`PARTNERS_WITH`), **opponents** (`OPPOSES`) — promoting them from today's `OTHER`+free-text escape hatch to first-class typed edges.

### 7.2 Relation instance schema

```jsonc
{
  "id": "rel-tsmc-nvidia-supplies",
  "source": "TSMC", "target": "NVIDIA",          // ← must resolve to entities[].canonical_name (unchanged rule)
  "type": "SUPPLIES",                            // ← relationships[].type (vocabulary expanded, §7.1)
  "family": "economic_exchange",                 // NEW — the stable family
  "valence": "transactional",                    // NEW — allied | adversarial | neutral | transactional | directional
  "direction": "source_to_target",               // NEW — explicit; never implicit
  "polarity": 0.2,                               // NEW — signed −1..+1 sentiment seed (supersedes the inert `sign`)
  "strength": "high",                            // ← relationships[].strength
  "summary": "TSMC fabs NVIDIA's leading-edge accelerators.",
  "mechanism": "Sole-source leading-edge supply; NVIDIA volume gated by TSMC allocation.",
  "since": "2020-01-01", "until": null,          // ← relationships[].since/until (now consumed, §11)
  "basis": "TSMC/NVIDIA disclosures, analyst supply-chain mapping.",  // ← relationships[].basis
  "grade": "A1",                                 // ← relationships[].grade
  "evidence_refs": ["S1", "S2"],
  "confidence": "high",
  "scenario_conditions": [
    { "condition": "Export controls bar advanced GPU sales to China",
      "effect": "shifts NVIDIA volume mix; relation strength persists, demand mix changes" }
  ],
  "forecast_implication": "TSMC allocation timing is a leading indicator of NVIDIA shipment ramps.",
  "simulation_effect": {                          // NEW — the bridge that fixes polarity collapse (§7.3)
    "follow": "target_to_source",                // NVIDIA (dependent/customer) follows TSMC
    "sentiment_seed": 0.2,                        // mild positive (transactional partner)
    "interaction": "monitor",                     // amplify | contest | monitor | none
    "shock_propagation": "source_to_target"       // TSMC capacity shock → NVIDIA
  }
}
```

### 7.3 The `simulation_effect` mapping (eliminates rival == partner)

This is the single most behaviorally-impactful change. The current `build_initial_follow_graph` (`actors.py:392-426`) and echo-chamber/sentiment logic should read `simulation_effect` (with safe fallbacks to today's type→direction table when absent). Canonical defaults by family:

| Family / edge | `follow` | `sentiment_seed` | `interaction` | Effect vs today |
|---|---|---|---|---|
| `ALLY_OF` / `SUPPORTS` / `PARTNERS_WITH` | weaker→stronger or bidirectional | **+** | amplify | today: symmetric follow, **no sentiment** |
| `OPPOSES` / `COMPETES_WITH` / `LITIGATES_AGAINST` | bidirectional (monitor) | **−** | contest | today: **identical** to partner; **−sentiment is new** |
| `REGULATES` / `INVESTIGATES` / `SANCTIONS` | regulated→regulator + regulator→regulated | slightly − | monitor | today: one-way only |
| `DEPENDS_ON` / `SUPPLIES` / `CUSTOMER_OF` | dependent/customer → supplier | mild + | monitor | today: `SUPPLIES`/`CUSTOMER_OF` were `OTHER` → **no follow at all** |
| `FUNDS` / `INVESTS_IN` / `BACKS` | recipient → backer | + | amplify | today: `OTHER` → **no follow** |
| `INFLUENCES` / `AMPLIFIES` / `ENDORSES` | audience → influencer | + | amplify | unchanged direction; sentiment new |
| `CRITICIZES` / `CONTRADICTS_CLAIM` | bidirectional | − | contest | new |

Two concrete wins:
1. **Adversarial edges now seed negative directional sentiment** and `contest`-style interaction, so the simulation can model rivalry/opposition instead of treating it as friendly mutual-following.
2. **Economic edges (`SUPPLIES`/`CUSTOMER_OF`/`FUNDS`/`BACKS`) now create follows**, where today they fall through `OTHER` and create *no* edge — so supply chains and capital flows finally shape the social graph.

---

## 8. Non-actor object schemas (events, signals, claims)

These keep important non-actors *out of the agent pool* while letting them shape it. They extend today's `key_events`, `forecast_inputs.indicators`, and `contested_claims`.

```jsonc
// event  (archetype: event)  — extends key_events[]
{ "id":"evt-bis-rule", "name":"New BIS export rule", "event_type":"regulatory_action",
  "date":"2026-Q3", "date_precision":"quarter", "status":"conditional",
  "participants":["US BIS","NVIDIA"], "summary":"Tighter advanced-GPU export controls.",
  "trigger_conditions":["China stockpiling exceeds threshold"],
  "expected_effects":[{ "target":"NVIDIA", "effect":"China revenue mix down", "direction":"decrease" }],
  "evidence_refs":["S2"],
  "simulation":{ "schedule_as_event":true, "poster_hint":"US BIS announces; NVIDIA reacts" } }

// signal (archetype: signal)  — extends forecast_inputs.indicators[]
{ "id":"sig-cowos", "name":"CoWoS packaging capacity", "metric":"monthly advanced-packaging wafers",
  "unit":"k wafers/mo", "current_value":"~35", "as_of_date":"2026-06-01", "frequency":"monthly",
  "source_id":"S1", "source_tier":"S1",
  "directionality":{ "higher_means":"supply easing", "lower_means":"supply bottleneck" },
  "thresholds":[{ "threshold":"<25", "interpretation":"binding HBM/accelerator constraint" }],
  "related_entities":["TSMC","NVIDIA"], "related_scenarios":["downside"],
  "forecast_use":"Leading indicator for accelerator shipment ceiling." }

// claim  (archetype: claim_narrative)  — extends contested_claims[]
{ "id":"clm-demand-durable", "claim":"AI accelerator demand is durable through 2028",
  "claim_type":"forecast", "status":"contested",
  "positions":[ { "entity":"NVIDIA","stance":"supports","tier":"S2" },
                { "entity":"Skeptic analysts","stance":"opposes","tier":"S2" } ],
  "why_it_matters":"Determines whether capacity expansion is over- or under-built.",
  "resolution_method":"Track hyperscaler capex guidance vs. utilization.", "confidence":"medium" }
```

---

## 9. Salience-driven agent selection (replacing degree-only ranking)

Concrete replacement for the `(matched_flag, influence_weight, degree)` tuple in `simulation_manager.py:299-314`:

1. **Eligibility gate (new, before ranking).** Admit a candidate only if `archetype ∈ {actor, collective}`. Drop `event/signal/claim/constraint/place/source/scenario` nodes from the agent pool entirely (they remain in the graph and shape behavior, but are never personas). This alone removes the bulk of "minor entity becomes agent."
2. **Rank by `salience.score`** (§5.2), not degree. Ties broken by `decision_power`, then degree. Because `decision_power` weights 0.35 and `centrality` only 0.15, a highly-connected amplifier cannot outrank a principal.
3. **Role-class quota (optional, flag-gated).** Cap `amplifier` personas at a fraction of the population (e.g. ≤20%) so media voices inform but do not dominate; reserve the majority for `principal`/`stakeholder`. Default off = today's behavior.
4. **Always-keep principals.** Any `role_class == principal` with `salience.tier == high` is retained unconditionally (generalizes today's "matched actors always kept").

`collective` entities become *representative* personas (sampled audience) rather than one account, feeding the existing audience-distribution logic (`simulation_config_generator.py:1522-1540`).

---

## 10. The structured output format

### 10.1 Full ontology object (Type Ontology + handoff to the instance layer)

The generator should produce this object. The **first three keys are byte-compatible with today** (`graph_builder.set_ontology` + `ontology.json` read only `entity_types`/`edge_types`); the rest is additive metadata that validation, report prompts, and future stages consume.

```jsonc
{
  "schema_version": "forecast_ontology.v1",
  "central_question": "Who leads leading-edge AI compute supply by 2027?",
  "domain": { "label": "Semiconductors / AI compute", "forecast_object": "leading-edge capacity leadership",
              "horizon": "2027", "as_of_date": "2026-06-25" },

  // ── Layer-1 type ontology (≤10 each; Graphiti-bounded) ──────────────
  "entity_types": [
    {
      "name": "Foundry",                       // domain-specific PascalCase (unchanged shape)
      "archetype": "actor",                    // NEW — gates persona eligibility
      "default_role_class": "principal",       // NEW — §5.1
      "description": "Contract chip manufacturer.",
      "selection_rule": "Use for firms that fabricate chips for others; NOT for fabless designers (use ChipDesigner) or tool vendors (use Equipment).",
      "anti_examples": ["A news outlet covering chips → it is a `source`, not a Foundry"],
      "attributes": [                          // ← unchanged shape; 1-3 attrs; reserved names sanitized
        { "name": "jurisdiction", "type": "text", "description": "Primary fab jurisdiction." },
        { "name": "process_node", "type": "text", "description": "Leading process node." }
      ],
      "examples": ["TSMC", "Samsung Foundry"]
    }
    // … 6–10 total, each mapped to an archetype
  ],
  "edge_types": [
    {
      "name": "SUPPLIES",                      // ← unchanged shape
      "family": "economic_exchange",           // NEW
      "valence": "transactional",              // NEW
      "description": "Source supplies a critical input to target.",
      "direction_semantics": "source supplies target",   // NEW — explicit
      "source_targets": [ { "source": "Foundry", "target": "ChipDesigner" } ],
      "attributes": [ { "name": "strength", "type": "text", "description": "high|medium|low" },
                      { "name": "polarity", "type": "text", "description": "-1..1 sentiment seed" } ]
    }
    // … 6–10 total
  ],

  // ── Handoff: schemas + gates the instance layer must satisfy ───────
  "entity_detail_schema": {
    "required": ["canonical_name","entity_type","archetype","role_class","description","role_in_question","salience","confidence","as_of_date"],
    "recommended_for_actors": ["worldview","goals","incentives","constraints","resources","vulnerabilities","stance","relational_roster","simulation"]
  },
  "relation_instance_schema": {
    "required": ["source","target","type","family","valence","direction","strength","basis"],
    "recommended": ["polarity","mechanism","since","until","grade","evidence_refs","forecast_implication","simulation_effect"]
  },
  "quality_gates": [
    "6–10 entity_types, 6–10 edge_types (current runtime cap).",
    "Every entity_type maps to an archetype; persona-eligible only if archetype ∈ {actor, collective}.",
    "Every edge_type has a family, valence, and explicit direction_semantics.",
    "Cited-but-non-acting outlets/reporters are `source`, never `actor`.",
    "Adversarial edges (OPPOSES/COMPETES_WITH) carry negative polarity; allied edges positive.",
    "No attribute uses reserved names: uuid, name, group_id, name_embedding, summary, created_at."
  ],
  "analysis_summary": "Why these types/relations fit the central question."
}
```

### 10.2 Current-compatible minimal subset (what `ontology.json` can persist today, unchanged)

```jsonc
{
  "entity_types": [ { "name": "Foundry", "description": "Contract chip manufacturer.",
                      "attributes": [ { "name": "jurisdiction", "type": "text", "description": "Primary fab jurisdiction." } ],
                      "examples": ["TSMC", "Samsung Foundry"] } ],
  "edge_types":   [ { "name": "SUPPLIES", "description": "Source supplies a critical input to target.",
                      "source_targets": [ { "source": "Foundry", "target": "ChipDesigner" } ],
                      "attributes": [ { "name": "strength", "type": "text", "description": "high|medium|low" } ] } ],
  "analysis_summary": "…"
}
```

The generator builds the **rich** object internally and **persists the rich object** (recommended) or downgrades to this subset until the persistence step is widened (§12, Step 1). Either way, `set_ontology` keeps working because `entity_types`/`edge_types` retain their exact current shape.

---

## 11. Workflow-stage mapping (every field → its real consumer)

| Stage / function | Today | With this schema |
|---|---|---|
| **Research extraction** (`deerflow_research.py:build_extraction_prompt`) | thin actors + 8-type edges; no source-vs-actor rule | emit `entities[]` with `archetype/role_class/salience/worldview/incentives/relational_roster`; `relations[]` with `family/valence/direction/polarity`; explicit "cited outlet → `source`, not actor" rule |
| **Ontology gen** (`ontology_generator.generate`) | `social_opinion` default; `Journalist`/`MediaOutlet` as types | domain-adaptive types each tagged `archetype`/`default_role_class`; `general_forecast`-style by default for non-social domains (already wired behind `ONTOLOGY_AUTO_SELECT`); persist rich ontology |
| **Graph seeding** (`graph_builder.seed_actors`, `set_ontology`) | seeds `relationships[]`; `sign/strength/grade` only as fact text | seed `relations[]` carrying `valence/polarity`; entity attrs carry `salience.tier`, `role_class`, `jurisdiction` |
| **Entity read / filter** (`zep_entity_reader.filter_defined_entities`) | keep any custom-labeled node | + eligibility gate: only `archetype ∈ {actor,collective}` become agent candidates |
| **Agent cap** (`simulation_manager.py:299-314`) | `(matched, influence, degree)`; degree-only for unmatched | rank by `salience.score`; role-class quota for amplifiers (§9) |
| **Persona gen** (`oasis_profile_generator` prompts; `actors.actor_briefing`) | rich block only for matched actors; prompt asks stance+memory | prompt asks for values/beliefs/incentives; `relational_roster` tells persona who to @/ally/attack; works for every `entity` with detail, not just matched |
| **Follow graph** (`actors.build_initial_follow_graph`) | OPPOSES==COMPETES==PARTNERS (symmetric); `OTHER` → no edge | read `relation.simulation_effect.follow`; economic edges now create follows; valence preserved |
| **Sentiment / echo chambers** (`simulation_config_generator.py:671-719, 1615-1620`) | sentiment from stance only; clusters ignore relations | seed `sentiment_priors` from `relation.polarity`; cluster allies together / rivals apart using `valence` |
| **Report background** (`report_agent._build_background_block`; `actors.situation_brief`) | 5 fields/actor + flat edge list | enumerate per-actor `relational_roster` ("backers / competitors / regulators: …") + `worldview`/`incentives`; coalition reasoning grounded, not re-derived |
| **Forecast extraction** (`forecast_extractor`) | scenarios from report text | link `signals[]`/`claims[]`/`scenario_link` edges → resolution criteria + indicators |

---

## 12. Migration plan (degrade-safe, phased, tied to real functions)

Each step is independently shippable and preserves current behavior when its new fields are absent.

1. **Preserve rich ontology fields.** In `pipeline_orchestrator.py:2540-2553`, persist the full ontology object (keep `entity_types`/`edge_types` keys intact). Low risk: existing readers ignore unknown keys.
2. **Tag types with archetype/role-class.** Extend both ontology prompts (`ontology_generator.py`) to emit `archetype`/`default_role_class`/`selection_rule`/`anti_examples`; add a normalization pass that infers a missing `archetype` from the type name. Keep the minimal core valid for `set_ontology`.
3. **Add the source-vs-actor rule + archetype to extraction.** In `deerflow_research.build_extraction_prompt`, add the explicit "cited-only → `source`" instruction and request `archetype`/`role_class`/`salience` per entity. Emit `entities[]` alongside legacy `actors[]` (the latter as a filtered projection: `entities` where `archetype=='actor'`).
4. **Materialize `relational_roster` + `relations[].family/valence/polarity`.** Add a pure helper in `actors.py` that (a) projects `relations[]` into each entity's `relational_roster`, and (b) backfills `valence`/`polarity` from `type` when missing (allied families → +, antagonism → −). Pure, testable, no behavior change until consumed.
5. **Eligibility gate + salience ranking.** In `zep_entity_reader`/`simulation_manager`, gate candidates to `actor`/`collective` archetypes and rank by `salience.score` (fall back to today's tuple when scores absent). Flag-gate (`SIM_SALIENCE_RANKING`, default off → identical to today).
6. **Valenced follow graph + sentiment seeding.** In `actors.build_initial_follow_graph` and `simulation_config_generator`, read `relation.simulation_effect`/`polarity` when present (fall back to the current type table). Flag-gate (`SIM_VALENCED_RELATIONS`, default off).
7. **Richer persona + report prompts.** Add `worldview`/`incentives`/`relational_roster` to the persona prompt fields and to the report background renderer (`actors.situation_brief`, `report_agent._build_background_block`).
8. **Tests.** Ontology validation accepts the rich object and emits a valid minimal subset; reserved-name sanitization; archetype eligibility excludes non-actors; `relations[]` degrade into legacy `relationships[]`; valenced follow direction; roster projection round-trips.

---

## 13. Validation / quality gates (what "good" looks like)

**Type ontology**
- [ ] 6–10 entity types, 6–10 edge types; each entity type → an archetype; persona-eligible only if `actor`/`collective`.
- [ ] No entity type is a vague concept ("sentiment", "risk", "trend") — model those as `signal`/`claim`/`scenario`.
- [ ] Every edge type has `family`, `valence`, and explicit `direction_semantics`; adversarial edges carry negative polarity.
- [ ] No reserved attribute names.

**Entity detail**
- [ ] Every entity has `canonical_name`, `archetype`, `role_class`, `role_in_question`, `salience`, `confidence`, `as_of_date`.
- [ ] `source`/citation-only outlets are archetype `source`, **not** `actor` — they do not appear in `entities[]` as agents.
- [ ] Actor entities carry `worldview`(values+beliefs), `incentives`, and a `relational_roster`; non-actors carry `forecast_use`+evidence instead of a persona block.
- [ ] `salience.score` has per-signal basis; principals are not outranked by connectivity alone.

**Relations**
- [ ] Every endpoint resolves to an `entities[].canonical_name`; direction is explicit; valence set.
- [ ] Customers/suppliers/backers/investors/competitors are first-class typed edges, not `OTHER` free-text.
- [ ] Adversarial relations seed negative `polarity`/`sentiment_seed`; economic relations create follows.

---

## 14. Cross-domain worked examples (type allocation)

**AI compute (markets/tech).** Entities: `Foundry`(actor/principal), `ChipDesigner`(actor/principal), `CloudProvider`(actor/principal), `Equipment`(actor/intermediary), `Regulator`(actor/arbiter), `Investor`(actor/stakeholder), `ComputeCapacity`(constraint_resource), `PackagingThroughput`(signal). Edges: `SUPPLIES`, `CUSTOMER_OF`, `DEPENDS_ON`, `COMPETES_WITH`, `PARTNERS_WITH`, `REGULATES`, `FUNDS`, `CONSTRAINS`.

**Election (geopolitics/opinion).** Entities: `Candidate`(actor/principal), `Party`(actor/principal), `VoterBloc`(collective/stakeholder), `MediaOutlet`(actor/**amplifier**), `Pollster`(**source**), `Jurisdiction`(place), `CampaignEvent`(event), `PollResult`(signal). Edges: `SUPPORTS`, `OPPOSES`, `INFLUENCES`, `ENDORSES`, `FUNDS`, `MEASURES`, `COMPETES_WITH`. Note `Pollster` is a `source` (never a persona) while `MediaOutlet` is an `amplifier` actor capped by quota — the exact distinction missing today.

**Product launch (business).** Entities: `Company`(actor/principal), `Competitor`(actor/principal), `CustomerSegment`(collective/stakeholder), `DistributionPartner`(actor/intermediary), `Regulator`(actor/arbiter), `Product`(asset_object), `LaunchEvent`(event), `AdoptionRate`(signal). Edges: `COMPETES_WITH`, `PARTNERS_WITH`, `SUPPLIES`, `CUSTOMER_OF`, `SUBSTITUTES_FOR`, `REGULATES`, `INFLUENCES`, `SIGNALS`.

**Litigation.** Entities: `Plaintiff`(actor/principal), `Defendant`(actor/principal), `Court`(actor/arbiter), `Regulator`(actor/arbiter), `Investor`(actor/stakeholder), `LegalClaim`(claim_narrative), `Ruling`(event), `SettlementProbability`(signal). Edges: `LITIGATES_AGAINST`, `REGULATES`, `INVESTIGATES`, `FUNDS`, `INFLUENCES`, `SUPPORTS_CLAIM`, `CONTRADICTS_CLAIM`, `INCREASES_PROBABILITY_OF`.

---

## 15. Summary recommendation

Move from a **social-actor ontology** to a **forecast-mechanics ontology** with three explicit, independent axes — *archetype* (kind), *role-class* (causal function), and *salience* (a reasoned score) — and a relation layer that is *typed, valenced, directional, and simulation-mapped*.

The highest-leverage changes, in order:

1. **Separate actors from sources and from non-actor objects** (archetype + the explicit "cited outlet → `source`" rule + the `actor/collective`-only agent eligibility gate). This is the direct fix for reporters/outlets crowding the cast.
2. **Rank agents by a multi-signal salience score, not graph degree** — with `decision_power` dominant so amplifiers can't outrank principals.
3. **Make per-entity detail first-class**: `worldview`(values/beliefs), `incentives`, and a materialized `relational_roster` (allies/opponents/competitors/customers/suppliers/backers/investors/supporters/regulators/dependents) — reaching *every* entity's persona and the report, not just matched actors.
4. **Restore relation polarity**: valenced, directional relations with a `simulation_effect` map, so rivals ≠ partners, and so supply/capital edges finally shape the social graph and sentiment.

Every step is additive and degrade-safe: with the new fields absent, the pipeline behaves exactly as it does today; with them present, ontology quality, persona realism, simulation dynamics, retrieval precision, and forecast calibration all improve — while the `{entity_types, edge_types}` contract that `graph_builder.set_ontology()` and `ontology.json` depend on stays byte-compatible.
