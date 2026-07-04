---
name: actor-ontology-research
description: Use this skill for the DeepResearchForecast/DeerFlow forecasting pipeline whenever the research output must seed an ontology, a knowledge graph, and an actor-based simulation — i.e. any "forecast X" / "who wins / what happens to X" prediction run. It specializes the deep-research tradecraft toward an ACTOR-CENTRIC, ONTOLOGY-READY dossier: identify the real key actors (and demote mere reporters/outlets/sources), profile each in depth (role, values, beliefs, incentives, goals, constraints, resources, vulnerabilities, allies/opponents/customers/competitors/backers/investors), map their directed, typed, valenced relationships, and trace how the actors and their relationships have evolved over time. Runs a multipass, iteratively-refined workflow with an AI-judge quality gate that loops until the dossier is sufficiently detailed, rich, and excellent. Builds on (does not replace) the `deep-research` skill — use that skill's search craft, source tiering (S1–S4), evidence grading, and verification tradecraft as the foundation, and this skill for the mission, structure, and quality loop.
---

# Actor & Ontology Research Skill

> This skill produces the seed material for the rest of the pipeline. The downstream ontology generator reads your report and builds the entity/edge type ontology; a structured-extraction pass converts your report into `actors.json` (the cast), `relationships[]` (the network), `sources.json`, and the situation brief; those become the knowledge graph, the simulation personas, the social/follow graph, and the final forecast. **The quality of every later stage is capped by the quality of this dossier.** Your job is to make the cast and their network *legible* — richly profiled, correctly ranked, and explicitly connected — so the ontology step can build a rich knowledge map without re-mining from scratch.

> Foundation: follow the `deep-research` skill for all search craft, source-quality tiering (S1–S4), evidence grading (Admiralty B2 bar), triangulation, circular-source detection, temporal awareness, and the synthesis gate. This skill adds the *actor/ontology mission*, the *per-actor depth standard*, the *relationship/evolution requirements*, the *multipass workflow*, and the *AI-judge quality loop*. Where the two conflict, the underlying evidence discipline of `deep-research` always wins.

---

## 1. Mission & prime directive

Produce a **decision-grade, ontology-ready actor dossier**: the complete, correctly-ranked cast of the forecast question, each actor profiled deeply enough to drive a realistic persona and a grounded forecast, and the full directed/typed/valenced relationship network between them, with the **history and evolution** of both the actors and their relationships.

Five operating principles specialize the base tradecraft:

1. **Actors are the spine.** The forecast turns on *who decides, who is affected, and how they are connected*. Spend the majority of the budget profiling the key actors and mapping their relationships — not on generic topic description.
2. **Rank by causal role, not prominence.** The most-quoted name is often not the most important actor. Identify who can actually *move the outcome* (principals) and separate them from those who merely *talk about it* (amplifiers) or are *only cited* (sources). See §2 — this is the single most common failure.
3. **Depth per actor, not breadth of names.** A dossier of 12 deeply-profiled key actors beats a list of 40 thin ones. Each key actor gets a real profile (§3), not a label.
4. **Relationships are a first-class output, not a byproduct.** The directed, typed, *valenced* network (who allies with / opposes / competes with / supplies / funds / regulates / depends on whom) is researched and evidenced edge by edge (§4). A partner and a rival must not look the same.
5. **Time is a dimension.** Actors and relationships have histories. Research how the cast and its alliances/rivalries *formed and changed* — inflection points, realignments, entries/exits — not just a present-day snapshot (§5). Evolution is what makes the simulation and forecast dynamic rather than static.
6. **Evidence discipline (inherits the deep-research skill).** Source every profile and edge in documents you **actually fetched** — real URL, on-page date, S1/S2 tier. **Never fabricate** a source, URL, date, quote, or relationship from memory, and never list a future-dated/hypothetical document as fact. **No thrashing**: an elusive fact about an actor is worth **≤2 attempts**, then log it as a gap and move to the next actor/edge — never reissue a near-duplicate query (same intent, only re-quoted / new `site:` / reshuffled `OR`). Profile breadth across the **whole cast** beats hunting one missing detail about one actor.

Never write the dossier from general knowledge, and never let writing begin before the **judge gate** (§7) passes.

---

## 2. Who counts as a key actor (salience & role triage)

This section exists because the pipeline otherwise over-includes minor entities. Apply it deliberately.

### 2.1 The three-way separation (do this for every named entity you encounter)

| Classification | Test | Where it goes |
|---|---|---|
| **ACTOR** | Can it *decide, act, allocate resources, set rules, or shape behavior* in a way that materially affects the outcome? | A profiled actor in the dossier cast (§3). |
| **SOURCE** | Does it appear *only because you cited it* — a news outlet, wire service, reporter, pollster, or analyst whose report you used as evidence, but which is not itself moving the outcome? | The **source list** (with tier + date). **Not** a cast member. |
| **CONTEXT OBJECT** | Is it a thing, event, signal, rule, place, or claim — something that *shapes* actors but cannot itself act (a product, a metric, an election date, an export rule, a capacity bottleneck)? | The situation brief / drivers / events / signals — **not** the cast. |

> **The reporter/outlet rule (critical).** A news outlet, reporter, analyst, or pollster is an **actor only if it is itself a principal or amplifier whose behavior moves the outcome** (e.g. the forecast is *about* media influence, or the outlet takes a stance that changes the result). If it shows up only because you *quoted* it, it is a **SOURCE**, not an actor. Do not put cited outlets, journalists, or "experts who commented" into the cast. This keeps the simulation populated by decision-makers and stakeholders, not by the press covering them.

### 2.2 Role-class (assign one to every actor)

| Role-class | Definition | Note |
|---|---|---|
| **principal** | Its decisions directly move the outcome; the forecast hinges on its choices. | The core cast. Profile deepest. |
| **arbiter** | Sets/enforces the rules that gate the outcome (regulator, court, standards body). | Its rulings are events; profile its authority and posture. |
| **stakeholder** | Materially affected but limited agency (customers, populations, supporters, passive investors). | May be a *collective* (a bloc), not one named entity. |
| **amplifier** | Shapes narrative/information flow but does not decide (media, commentators, influencers). | Include sparingly and label as amplifier; never let amplifiers dominate the cast. |
| **intermediary** | Connects principals (supplier, distributor, financier-as-conduit). | Carries dependency/economic edges; profile its leverage. |

### 2.3 Salience — rank the cast (a reasoned judgment, not prominence)

For each candidate actor, judge salience from five independent signals and record a one-line basis:

- **decision_power** — can its choices move the outcome? (dominant signal; principals high, amplifiers low)
- **stake** — how much is it affected / how strong is its incentive to act?
- **centrality** — how connected is it in the actor network?
- **evidence_grade** — how well-attested is its role? (source tier behind it)
- **recency** — how current is its relevance to the forecast horizon?

Output a salience **tier (high / medium / low)** per actor, with the basis, and **force-rank the cast by causal influence over the outcome** (most influential first). The downstream cap keeps the highest-salience actors; **do not let a well-covered amplifier outrank a pivotal principal.**

**Cast size is hard-capped.** Any real forecast simulation distills to **fewer than ~20 main actors** — only those whose *decisions and actions will causally affect the event being forecasted*. Aim for roughly **8–20 cast members** (fewer for narrow questions) and never exceed the deployment's `ACTOR_CAST_MAX` (default **20**); the extraction pass truncates anything beyond it, so an oversized cast just wastes profile depth on entries that will be cut. Do **not** spend cast slots on irrelevant entities or on media organizations/journalists/commentators/analysts/pollsters — per §2.1 they are SOURCES (context), never cast members, unless one of them is itself an outcome-mover. When over budget, cut the least causally-influential entries, not the profile depth of the principals.

---

## 3. The per-actor dossier standard (research depth)

For **every key actor**, research and write a profile that a downstream model could use to *role-play that actor convincingly and forecast its behavior*. Thin labels ("a major player", "an influential regulator") are failures. Each profile carries, evidence-backed wherever possible:

**Identity & classification**
- **Canonical name** + **aliases** (abbreviations, tickers, foreign-language forms, handles) — for entity resolution. **This is not decorative.** The `aliases` list you write here is the ONE authoritative signal the knowledge-graph build uses to recognize that "China" / "CCP" / "Beijing" / "MOFCOM" and "Government of the People's Republic of China" are the same real actor — graphiti's own per-chunk extraction sees only isolated prose and cannot infer this on its own. Be exhaustive: every abbreviation, nickname, foreign-language form, official-title variant, or handle you use anywhere in the dossier belongs in this list, or that surface form will show up as a phantom duplicate node and silently steal a cast slot from a genuinely distinct actor.
- **One-line disambiguator** ("TSMC, the Taiwanese contract chip foundry") — pins identity.
- **Archetype** (actor vs collective) and **role-class** (§2.2); **salience tier** + basis (§2.3); **jurisdiction / sector**.
- **Role in the question** — *why this actor matters to the outcome*, not just what it is.

**The "why" — what drives it (the depth that makes a profile real)**
- **Values** — what it holds important (e.g. "operational neutrality", "national security", "shareholder returns").
- **Beliefs / worldview** — how it sees the situation; its framing/ideology; its theory of the game.
- **Incentives** — its payoff structure: what it **gains** and **loses under each plausible outcome** (this is forecasting gold).
- **Goals** — ranked objectives, with time horizon.
- **Constraints** — hard limits on action (capital, capacity, legal, political, technical).
- **Resources / capabilities** — what it can deploy (authority, money, technology, distribution, audience, data).
- **Vulnerabilities** — exposures, dependencies, red lines, reputational risks, failure modes.
- **Decision rights** — what it actually controls, and the limits of that authority.
- **Stance**: its **stated** position (its own words, S1) vs. its **revealed** behavior (what it did). *The gap between the two is itself evidence* — surface it explicitly.

**History & evolution (§5)** — how this actor got here; how its position/strategy has changed; key past decisions and their outcomes; its track record on commitments.

**Relational roster** — its network rendered per-actor (derived from §4 but stated here so the profile is self-contained): its **allies, opponents, competitors, customers, suppliers, backers/investors, supporters, regulators, and dependents** — each a named counterparty with a one-line basis. This is exactly the relational context the ontology and simulation need.

**Evidence** — the load-bearing claims about this actor each tied to a source + tier; confidence; open questions.

> Depth heuristic: if you could not write three sentences of the actor's likely reasoning under the forecast's main uncertainty, you have not researched it enough.

---

## 4. The relationship network (directed, typed, valenced, evidenced)

The relationship graph between the named actors is a **first-class deliverable**. Research it edge by edge; do not infer a network from co-occurrence.

For each relationship between two cast members, establish:

- **Direction** — who → whom (never implicit). State it explicitly.
- **Type** — from the canonical vocabulary, chosen for *forecast mechanics*, not just social tone:
  - *Alignment (allied, +):* `ALLY_OF`, `SUPPORTS`, `PARTNERS_WITH`, `COALITION_WITH`
  - *Antagonism (adversarial, −):* `OPPOSES`, `COMPETES_WITH`, `LITIGATES_AGAINST`
  - *Governance:* `REGULATES`, `APPROVES`, `SANCTIONS`, `INVESTIGATES`
  - *Economic exchange:* `SUPPLIES`, `CUSTOMER_OF`, `FUNDS`, `INVESTS_IN`, `BACKS`, `OWNS`
  - *Dependency:* `DEPENDS_ON`, `BOTTLENECKED_BY`, `EXPOSED_TO`
  - *Information/influence:* `INFLUENCES`, `AMPLIFIES`, `ENDORSES`, `CRITICIZES`
  - (use a precise free-text label only when none of the above fits)
- **Valence** — allied / adversarial / neutral / transactional. **A rival and a partner must be distinguishable** — never flatten opposition into a generic "connected to". Carry the sign (ally / rival / neutral).
- **Strength** — high / medium / low (how load-bearing the relationship is for the outcome).
- **Basis** — a one-line researched evidence statement (and a source).
- **Mechanism & forecast implication** — *how* the relationship works and *why it matters* to the outcome (e.g. "sole-source supply → its capacity gates the customer's shipments").

Prioritize the relationships that are **load-bearing for the forecast** (the supply dependency, the regulatory chokepoint, the rivalry that drives the race) over incidental social ties. Ensure every key actor has its salient edges mapped — especially the economic and governance ones the press under-reports.

---

## 5. History & evolution over time

A static snapshot cannot drive a dynamic simulation or a credible forecast. Research the **trajectory**:

- **Formation** — how the current cast and its alliances/rivalries came to be: founding moments, mergers/splits, entries and exits, the origin of each major relationship.
- **Inflection points** — dated events where the situation, an actor's strategy, or a relationship materially changed (a policy shift, a leadership change, a deal, a betrayal, a market entry). Build the dated sequence; causation that violates chronology dies here.
- **Realignments** — relationships that flipped (former partners now competing; rivals now cooperating) — and *why*. These are the highest-signal evolution facts.
- **Trend vs. break** — establish the trajectory with data, then research what could structurally break it.
- **Track record** — for forecasting, each actor's history of keeping/breaking commitments and the outcomes of its past comparable decisions (base-rate input).

Tie evolution facts to dates and sources. The downstream pipeline maps dated events onto simulation rounds, so a dated, ordered timeline of how the situation evolved is directly consumed.

---

## 6. The multipass research workflow

Run the investigation in deliberate passes, checkpointing the evidence ledger after each so it survives summarization. This mirrors and uses the pipeline's deep fan-out: a landscape pass, parallel per-actor/per-KIQ deep dives, then synthesis.

**Pass 0 — Design (no tool calls).** Restate the forecast question, its object, horizon, and as-of date. Hypothesize the candidate cast and the 3–7 load-bearing KIQs (per `deep-research` §2). Name the S1/S2 sources likely to hold the answers. State priors and what would change them.

**Pass 1 — Landscape & cast identification.** Broad scoping to surface the candidate entity universe. Then run the **§2 triage on every named entity**: actor vs source vs context object; assign role-class; assign salience tier with basis. Produce the *ranked cast list* — this is the backbone everything else hangs on. Deliberately demote cited outlets/reporters to the source list.

**Pass 2 — Per-actor deep dives (fan-out).** For each key actor (highest salience first), run a focused investigation that fills the §3 profile: identity, values/beliefs/incentives, goals/constraints/resources/vulnerabilities, decision rights, stated-vs-revealed stance, and that actor's slice of the relationship network. Spend real budget here — this is the core of the dossier. Use entity-pivot search (actor → its filings, leadership statements, deals, regulatory record). One deep dive per key actor; parallelize where the workflow supports fan-out.

**Pass 3 — Relationship & network mapping.** With the cast profiled, research the edges (§4) systematically: for each load-bearing pair, establish direction, type, valence, strength, and basis. Fill the economic/governance/dependency edges the press under-covers. Cross-check that each actor's relational roster (§3) matches the network.

**Pass 4 — History/evolution & contested-claims sweep.** Build the dated trajectory (§5): formation, inflection points, realignments. Run the `deep-research` adversarial pass (§7 there): targeted disconfirmation, competing hypotheses on the contested relationships and on each actor's revealed intentions, and a premortem on the forecast. Capture genuine evidence conflicts as contested claims with their positions and *why they differ*.

After Pass 4, assemble the draft dossier (§9 format) and enter the judge loop.

**Budget discipline (per `deep-research` §10).** Roughly: ¼ landscape+cast, ½ per-actor deep dives + relationship mapping (protect this — it is the dossier), ¼ evolution + disconfirmation + judge-driven gap-filling. Never loop on a dead query; change the angle and note the gap. Checkpoint after each pass; never re-research a settled actor.

---

## 7. The AI-judge quality loop (iterate until excellent)

Do not ship the first draft. The dossier must pass an explicit **AI-judge gate** that scores it against the §8 rubric and drives targeted refinement. Run the judge as a distinct, skeptical evaluation step — either a self-critique pass in which you score your own dossier *honestly and adversarially* (assume it is not good enough and hunt for what is missing), or, when the workflow supports a separate judge model/sub-agent, hand the dossier to it. **Default to skepticism: a draft is inadequate until proven excellent.**

**The loop:**

1. **Score** the draft on every §8 dimension (0–5), with a one-line justification per dimension citing concrete evidence in the dossier (or its absence).
2. **Decide.** PASS only if the bar in §8.2 is met. Otherwise FAIL.
3. **On FAIL, emit a targeted gap list** — specific, addressable items, e.g.: "Actor *X* has no incentives/loses-if; the *X↔Y* edge has no valence or basis; no evolution facts for the *X–Z* rivalry; outlet *W* is mis-listed as an actor — demote to source; salience of *V* looks inflated vs. its decision power." Vague critiques ("add more detail") are not allowed.
4. **Refine, surgically.** Run a refinement pass that addresses **only the listed gaps** (per the `deep-research` discipline: one targeted pass on the gap, not a restart). Add the missing profiles/edges/evolution; re-triage any mis-ranked entity; pull the missing evidence.
5. **Re-judge.** Repeat 1–4 until PASS, or until the **max-pass cap** (default **3 refinement rounds**) is reached.
6. **Ship.** On PASS, write the final dossier. If the cap is hit without full PASS, ship the best version with **residual gaps explicitly flagged** (which actors/edges/claims remain thin or single-origin) — never silently present an incomplete dossier as complete.

The loop converges on *excellence*, not mere completeness: each round should raise the weakest dimension, not polish the strongest.

---

## 8. The judge rubric

### 8.1 Dimensions (score each 0–5)

| # | Dimension | What 5/5 looks like | What ≤2 looks like |
|---|---|---|---|
| 1 | **Cast correctness** | The cast is the right set of *outcome-movers*; no cited outlet/reporter/source is mis-cast as an actor; collectives and arbiters are captured. | Press outlets and quoted commentators padding the cast; key decision-makers missing. |
| 2 | **Salience ranking** | Actors are ranked by decision power/stake with a basis each; no amplifier outranks a principal. | Ranking tracks prominence/coverage; inflated or flat salience. |
| 3 | **Per-actor depth** | Every key actor has values, beliefs, incentives (gains/loses-if), goals, constraints, resources, vulnerabilities, decision rights, and stated-vs-revealed stance — evidenced. | Actors are labels with a role and a one-line stance; no incentives/values. |
| 4 | **Relationship completeness** | The load-bearing edges are mapped: directed, typed, **valenced**, strength-rated, with a basis each; economic/governance/dependency edges present, not just social ties. | Few edges; undirected or unvalenced; rivals and partners indistinguishable. |
| 5 | **History & evolution** | Dated formation, inflection points, and realignments for actors and key relationships; track records for forecasting. | A present-day snapshot only; no trajectory or dates. |
| 6 | **Evidence grounding** | Load-bearing claims at B2+; sources tiered (S1–S4) and dated; circular sourcing avoided; numbers carry unit/as-of/definition. | Claims unattributed; aggregator/S4 reliance; echoes counted as independent. |
| 7 | **Contradiction handling** | Genuine conflicts surfaced as contested claims with positions, sources, and *why they differ*; single-origin items flagged. | Conflicts averaged away or omitted; false certainty. |
| 8 | **Ontology-readiness** | The report's structure (§9) lets the downstream step extract the cast, archetypes/role-classes, salience, the typed/valenced relationship network, and the timeline *without re-mining* — names are canonical and consistent; relations name real cast members. | Prose-only narrative; inconsistent names; the relationship network must be re-derived from scratch. |

### 8.2 Pass bar (all must hold)

- **No** dimension < **3**, AND
- Dimensions **1, 3, 4, 8** (cast correctness, per-actor depth, relationship completeness, ontology-readiness) are each ≥ **4** — these are non-negotiable for this pipeline, AND
- The **mean** across all eight dimensions is ≥ **4**.

(A stricter deployment may additionally require **every** dimension ≥ 4 — `ACTOR_DOSSIER_JUDGE_STRICT` — but the default bar tolerates a single non-critical dimension at 3 to absorb judge-score noise.)

If the bar is not met, the judge FAILS the draft and emits the targeted gap list (§7.3).

---

## 9. Output contract — the ontology-ready report

Write the report so the downstream ontology generator and the structured-extraction pass can read it directly. Keep the `deep-research` §12 output contract (layered claims, calibrated uncertainty, inline attribution, conflicts shown not averaged, no S4 citations, tiered source list), and add this **explicit, labeled structure**:

1. **Forecast frame** — the question, its forecast object, horizon, and as-of date; the situation brief (current situation, how it got here / context, the forces in tension / dynamics, the 3–6 fault lines the actors argue over, and the catalysts that would shift things).
2. **The cast (key actors)** — one clearly-delimited profile per actor, in salience order, each carrying the §3 fields. Use **canonical names consistently** (the same string everywhere) and list aliases once. Mark each actor's archetype, role-class, and salience tier explicitly, **with the one-line salience basis from §2.3 next to the tier** — the extraction pass emits it as `salience: {tier, basis}` and downstream coverage/agent-ranking read it.
3. **The relationship network** — an explicit, enumerated list of directed edges: `Source —[TYPE, valence, strength]→ Target — basis`, covering the load-bearing relationships from §4. Every endpoint must be a name from the cast.
4. **Per-actor relational roster** — within or beside each profile, the actor's allies / opponents / competitors / customers / suppliers / backers-investors / supporters / regulators / dependents, named.
5. **Evolution & timeline** — the dated sequence of formation, inflection points, and realignments (§5).
6. **Drivers, indicators & scenarios** — the 3–6 variables that move the outcome, each with a watchable dated indicator; base rates / reference class; and rough scenario likelihoods.
7. **Contested claims & source list** — genuine conflicts with positions and why they differ; the full source list with tier (S1–S4) and date.

> Consistency is what makes it ontology-ready: the same actor must be referable by one canonical name across the cast, the network, the roster, and the timeline, so extraction resolves entities cleanly and the ontology generator can map types, archetypes, salience, and a typed/valenced edge set without guessing.

---

## 10. What each section feeds downstream (why this structure)

| Dossier section | Feeds | Becomes |
|---|---|---|
| §9.1 situation brief | extraction → `situation_brief` | report background + simulation fault-line posts |
| §9.2 cast (archetype/role-class/salience) | ontology generation + `actors.json` + agent selection | entity types/archetypes; persona-eligible cast; salience-ranked agents (so amplifiers don't crowd out principals) |
| §9.2 per-actor values/beliefs/incentives/goals/etc. | `actors.json` actor detail | rich personas (not generic templates) and grounded report context |
| §9.3 relationship network (typed/valenced/directed) | `relationships[]` → graph seeding + follow graph + sentiment | the knowledge-graph edges, the simulation's initial follows, and ally/rival sentiment (valence preserved) |
| §9.4 relational roster | report + persona prompts | "who to @, ally with, or oppose"; report coalition reasoning |
| §9.5 evolution & timeline | `key_events` → scheduled events | dated simulation triggers; causal/temporal grounding |
| §9.6 drivers/indicators/scenarios | `forecast_inputs` | structured forecast: drivers, indicators, scenario probabilities |
| §9.7 contested claims + tiered sources | `contested_claims` / `sources.json` | calibrated confidence + provenance |

(For the precise target schema — archetypes, role-class, salience scoring, the relational roster, and valenced relations — see `CLAUDE_ONTOLOGY.md` in the repo root. This skill produces exactly what that schema consumes.)

---

## 11. Failure modes (do not do these)

- ❌ **Outlet-as-actor.** Putting a cited news outlet, reporter, pollster, or "expert who commented" into the cast. They are sources unless they themselves move the outcome (§2.1).
- ❌ **Ranking by prominence.** Letting the most-quoted or most-connected name outrank the actual decision-maker (§2.3).
- ❌ **Label-depth profiles.** "A key regulator with significant influence" — no values, incentives, constraints, or stated-vs-revealed (§3).
- ❌ **Undirected / unvalenced relationships.** "X is connected to Y" — or treating a rivalry the same as a partnership (§4). A partner and an opponent must be distinguishable.
- ❌ **Static snapshot.** A present-day picture with no formation, inflection points, or realignments, and no dates (§5).
- ❌ **Network by co-occurrence.** Inferring relationships from two names appearing together instead of researching the edge and its basis (§4).
- ❌ **Inconsistent naming.** Calling the same actor three different things, breaking entity resolution and the ontology map (§9).
- ❌ **Shipping the first draft.** Skipping the judge loop, or rubber-stamping a thin dossier instead of adversarially scoring it and refining the weakest dimension (§7–§8).
- ❌ **Breadth over depth.** Forty thin names instead of the right twelve profiled deeply (§1.3).
- ❌ Any `deep-research` §13 failure mode (S4 citations, circular sourcing, unverified quotes, un-sanity-checked numbers, writing before the gate).
