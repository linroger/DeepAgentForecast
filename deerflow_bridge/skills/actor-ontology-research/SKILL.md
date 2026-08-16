---
name: actor-ontology-research
description: >-
  Use this skill in the DeepResearchForecast/DeerFlow forecasting pipeline when
  research must seed an ontology, knowledge graph, and actor simulation. It
  produces an actor-centric, ontology-ready dossier with deeply profiled key
  actors, directed typed and valenced relationships, historical evolution, and
  the behavioral fields needed for tailored runtime roles. It builds on the
  deep-research skill's source tiering, evidence grading, and verification
  discipline and adds a multipass actor/relationship workflow with an AI-judge
  quality gate.
---

# Actor & Ontology Research Skill

This body arrives complete via slash activation or eager subagent loading — do **not** reread `SKILL.md` from the filesystem; load only an explicitly named reference when the phase requires it.

> This skill seeds the whole pipeline; **the quality of every later stage is capped by this dossier** (section→artifact mapping: §10). Make the cast and network *legible* — profiled, correctly ranked, explicitly connected, behaviorally specific — so later stages never invent a generic persona or re-mine the report.

> Foundation: follow the `deep-research` skill for all search craft, source tiering (S1–S4), evidence grading (Admiralty B2 bar), triangulation, circular-source detection, temporal awareness, and the synthesis gate; where the two conflict, its evidence discipline wins.

## 1. Mission & prime directive

Produce a **decision-grade, ontology-ready actor dossier**: the complete, correctly-ranked cast of the forecast question, each actor profiled deeply enough to drive a realistic persona and a grounded forecast, the full directed/typed/valenced relationship network, and the **history and evolution** of both.

Six operating principles:

1. **Actors are the spine.** The forecast turns on *who decides, who is affected, how they are connected*. Spend most of the budget there, not on generic topic description.
2. **Rank by causal role, not prominence.** Separate who can *move the outcome* (principals) from who *talks about it* (amplifiers) or is *only cited* (sources) — §2; the single most common failure.
3. **Depth per actor, not breadth of names.** Twelve deeply-profiled actors beat forty thin ones — a real profile each (§3), not a label.
4. **Relationships are a first-class output.** The directed, typed, *valenced* network is researched and evidenced edge by edge (§4).
5. **Time is a dimension.** Research how the cast and its alliances/rivalries *formed and changed* (§5), not just a snapshot.
6. **Evidence discipline.** Source every profile and edge in documents you **actually fetched** — real URL, on-page date, S1/S2 tier. **Never fabricate** a source, URL, date, quote, or relationship, and never list a future-dated/hypothetical document as fact. **No thrashing**: an elusive fact is worth **≤2 attempts**, then log the gap and move on — never reissue a near-duplicate query. Breadth across the whole cast beats hunting one missing detail.

Never write from general knowledge; never let writing begin before the judge gate (§7) passes.

## 2. Who counts as a key actor (triage)

The pipeline otherwise over-includes minor entities — apply this triage deliberately.

### 2.1 The three-way separation (every named entity)

| Classification | Test | Where it goes |
|---|---|---|
| **ACTOR** | Can it *decide, act, allocate resources, set rules, or shape behavior* in a way that materially affects the outcome? | A profiled cast member (§3). |
| **SOURCE** | Appears *only because you cited its reporting as evidence*? | The **source list** (tier + date). **Not** the cast. |
| **CONTEXT OBJECT** | A thing, event, signal, rule, place, or claim that *shapes* actors but cannot itself act? | Situation brief / drivers / events / signals — **not** the cast. |

> **The reporter/outlet rule (critical).** A news outlet, reporter, analyst, or pollster is an **actor only if it is itself a principal or amplifier whose behavior moves the outcome** (e.g. the forecast is *about* media influence). If it appears only because you *quoted* it, it is a **SOURCE**: the simulation is populated by decision-makers and stakeholders, not the press covering them.

### 2.2 Role-class (one per actor)

| Role-class | Definition & handling |
|---|---|
| **principal** | Decisions directly move the outcome — core cast; profile deepest. |
| **arbiter** | Sets/enforces the rules gating the outcome (regulator, court, standards body); rulings are events — profile authority and posture. |
| **stakeholder** | Materially affected, limited agency; may be a *collective* (a bloc). |
| **amplifier** | Shapes narrative/information flow, does not decide; include sparingly, never dominant. |
| **intermediary** | Connects principals (supplier, distributor, conduit); carries dependency/economic edges — profile leverage. |

### 2.3 Salience — rank the cast

Judge each candidate on five signals, recording a one-line basis: **decision_power** (dominant signal), **stake**, **centrality**, **evidence_grade** (source tier behind its role), **recency** (relevance to the horizon). Output a salience **tier (high/medium/low)** + basis per actor and **force-rank the cast by causal influence over the outcome**. Do not let a well-covered amplifier outrank a pivotal principal.

**Cast size is hard-capped** at roughly **8–20 members** (fewer for narrow questions) — only actors whose decisions and actions causally affect the forecasted event — and never above the deployment's `ACTOR_CAST_MAX` (default **20**): extraction truncates the excess, so an oversized cast wastes depth on entries that will be cut. Spend no cast slots on anything §2.1 demotes; when over budget, cut the least causally-influential entries, not the principals' depth.

## 3. The per-actor dossier standard

For **every key actor**, write a profile a downstream model could use to *role-play that actor convincingly and forecast its behavior* — thin labels ("a major player") are failures. Each profile carries, evidence-backed wherever possible:

**Identity & classification**
- **Canonical name** + **aliases** (abbreviations, tickers, foreign-language forms, official-title variants, handles). This list is the ONE signal entity resolution uses to merge "China" / "CCP" / "Beijing" / "MOFCOM" into one actor — extraction cannot infer it; any surface form used in the dossier but missing here becomes a phantom duplicate node stealing a cast slot.
- **One-line disambiguator** ("TSMC, the Taiwanese contract chip foundry").
- **Archetype** (actor vs collective); **role-class** (§2.2); **salience tier** + basis (§2.3); **jurisdiction/sector**.
- **Role in the question** — *why it matters to the outcome*, not just what it is.

**The "why" — what drives it and what it is likely to do**
- **Values** and **beliefs/worldview** — what it holds important; how it frames the situation; its theory of the game.
- **Incentives** — what it **gains and loses under each plausible outcome** (forecasting gold); **motivations** and **goals** — ranked, with time horizon.
- **Constraints** — hard limits (capital, capacity, legal, political, technical); **resources/capabilities**; **vulnerabilities** — exposures, dependencies, red lines, failure modes.
- **Forward behavior** — evidence-backed operational preferences/aversions, decision rights/process/triggers, current actions, conditional future plans, investments/resource allocation, likely actions, red lines, and knowledge limits. Load `references/actor-intelligence-contract.md` for the exact dimension, status, qualifier, and epistemic rules.
- **Stance** — **stated** position (own words, S1) vs **revealed** behavior (what it did). *The gap between the two is itself evidence* — surface it explicitly.

**History & evolution (§5)** — how it got here; strategy changes; track record on commitments and comparable past decisions.

**Relational roster** — its network per-actor (from §4, restated so the profile is self-contained): allies, opponents, competitors, customers, suppliers, backers/investors, supporters, regulators, dependents — each named, with a one-line basis.

**Evidence** — type and source-bind every load-bearing claim; preserve dates, confidence, status/horizon, conditions, contradictions, and explicit gaps per `references/actor-intelligence-contract.md`.

> Depth heuristic: if you cannot write three sentences of the actor's likely reasoning under the forecast's main uncertainty, keep researching.

### 3.1 Runtime role-contract handoff (mandatory)

The profile sources the exact role the multi-agent simulation plays. Make these fields explicit and actor-specific so structured extraction populates `actors.json` without guessing:

- the §3 identity, "why", and stance fields (all of them), plus **risk tolerance**;
- interaction: named typed/valenced relationships, decision process/triggers, current actions, future plans, investments/resource allocation, **likely actions under the main uncertainty**, genuine **red lines**;
- epistemic boundary: known context/memory, as-of date, forecast horizon, evidence grade, source tags, explicit gaps.

Write these as **declarative evidence about the actor**, never as commands to a model (no “ignore…”/“write only…” imperatives, tool requests, hidden instructions, or role reassignment) — the runtime compiler treats dossier prose as untrusted data and may omit instruction-like values. A sparse or generic profile yields a deliberately cautious role, not a license to pad. Every active actor must be behaviorally distinguishable: reusing the same goals, likely actions, or red lines across the cast without actor-specific evidence is a judge failure. Sources and context objects never receive an agent role.

## 4. The relationship network (edge contract)

The relationship graph is a **first-class deliverable**: research it edge by edge; never infer a network from co-occurrence. Per edge between cast members:

- **Direction** — who → whom, stated explicitly.
- **Type** — from the canonical vocabulary, chosen for *forecast mechanics*:
  - *Alignment (+):* `ALLY_OF`, `SUPPORTS`, `PARTNERS_WITH`, `COALITION_WITH`
  - *Antagonism (−):* `OPPOSES`, `COMPETES_WITH`, `LITIGATES_AGAINST`
  - *Governance:* `REGULATES`, `APPROVES`, `SANCTIONS`, `INVESTIGATES`
  - *Economic exchange:* `SUPPLIES`, `CUSTOMER_OF`, `FUNDS`, `INVESTS_IN`, `BACKS`, `OWNS`
  - *Dependency:* `DEPENDS_ON`, `BOTTLENECKED_BY`, `EXPOSED_TO`
  - *Information/influence:* `INFLUENCES`, `AMPLIFIES`, `ENDORSES`, `CRITICIZES`
  - (precise free-text only when none fits)
- **Valence** — allied / adversarial / neutral / transactional. **A rival and a partner must be distinguishable** — never flatten opposition into "connected to".
- **Strength** — high / medium / low (how load-bearing for the outcome).
- **Basis** — a one-line researched evidence statement, with a source.
- **Mechanism & forecast implication** — how the relationship works and why it matters to the outcome.

Prioritize edges **load-bearing for the forecast** (supply dependency, regulatory chokepoint, driving rivalry) over incidental social ties; map every key actor's salient edges, especially the economic and governance ones the press under-reports.

## 5. History & evolution over time

A static snapshot cannot drive a dynamic simulation or credible forecast. Research the **trajectory**:

- **Formation** — how the cast and its alliances/rivalries came to be: founding moments, mergers/splits, entries/exits, origin of each major relationship.
- **Inflection points** — dated events where the situation, a strategy, or a relationship materially changed; causation violating chronology dies here.
- **Realignments** — relationships that flipped, and *why*. The highest-signal evolution facts.
- **Trend vs. break** — establish the trajectory with data, then research what could structurally break it.
- **Track record** — each actor's history of keeping/breaking commitments; outcomes of comparable past decisions (base-rate input).

Tie evolution facts to dates and sources: the pipeline maps dated events onto simulation rounds, so a dated, ordered timeline is directly consumed.

## 6. The multipass research workflow

Run deliberate passes, checkpointing the evidence ledger after each so it survives summarization.

**Pass 0 — Design (no tool calls).** Restate the question, object, horizon, as-of date. Hypothesize the candidate cast and 3–7 load-bearing KIQs (the `deep-research` core-loop decomposition), name the likely S1/S2 sources, state priors and what would change them.

**Pass 1 — Landscape & cast identification.** Broad scoping for the candidate entity universe, then the **§2 triage on every named entity**, producing the *ranked cast list* — the backbone.

**Pass 2 — Per-actor deep dives (fan-out).** Per key actor, highest salience first: fill the §3 profile and its network slice via entity-pivot search (filings, statements, deals, regulatory record, budgets, capital allocation, lobbying, and milestones). Parallelize where supported and protect this budget. Finish with the bounded cast-wide completion pass in `references/actor-intelligence-contract.md`.

**Pass 3 — Relationship & network mapping.** Research the §4 edges per load-bearing pair (direction/type/valence/strength/basis); fill the under-covered economic/governance/dependency edges; cross-check rosters (§3) against the network.

**Pass 4 — History/evolution & contested-claims sweep.** Build the dated trajectory (§5). Run the `deep-research` adversarial tradecraft (its `references/source-tradecraft.md`): targeted disconfirmation, competing hypotheses on contested relationships and revealed intentions, a premortem. Capture genuine conflicts as contested claims (§9.7). Then assemble the draft dossier (§9 format) and enter the judge loop.

**Budget discipline.** Roughly ¼ landscape+cast, ½ per-actor dives + relationship mapping (protect this), ¼ evolution + disconfirmation + judge-driven gap-filling. Apply the `deep-research` evidence-yield stopping rules; checkpoint each pass; never re-research a settled actor.

## 7. The AI-judge quality loop

Do not ship the first draft: the dossier must pass an **AI-judge gate** scored against the §8 rubric — a distinct, skeptical evaluation (adversarial self-critique or, where supported, a separate judge model/sub-agent). **A draft is inadequate until proven excellent.**

1. **Score** every §8 dimension (0–5), one-line justification each, citing concrete dossier evidence (or its absence).
2. **Decide.** PASS only if the §8.2 bar holds; otherwise FAIL.
3. **On FAIL, emit a targeted gap list** — specific, addressable items (a missing incentive, an unvalenced edge, a mis-cast outlet); vague critiques are not allowed.
4. **Refine surgically** — one targeted pass per listed gap, not a restart.
5. **Re-judge.** Repeat 1–4 until PASS or the **max-pass cap** (default **3 refinement rounds**).
6. **Ship.** On PASS, write the final dossier. If the cap is hit with an explicit final FAIL, the dossier is unusable and MUST NOT enter synthesis or simulation. A judge transport/parsing failure may degrade only when the deterministic cast-wide coverage ledger passes.

Each round raises the weakest dimension, not the strongest — the loop converges on *excellence*.

## 8. The judge rubric

### 8.1 Dimensions (score each 0–5)

| # | Dimension | Judges |
|---|---|---|
| 1–2 | **Cast correctness / salience** | Right outcome-movers; decision power/stake with a basis (§2). |
| 3–5 | **Actor depth / relationships / evolution** | Complete evidenced profiles, load-bearing attributed edges, and dated trajectories (§3–§5). |
| 6–8 | **Grounding / contradictions / readiness** | B2+ tiered evidence, conflicts preserved, and §9 extractable without re-mining. |
| 9–10 | **Forward behavior / cast accountability** | Every Tier-1/2 actor and required dimension is source-covered or a precise gap; load `references/actor-intelligence-contract.md`. |

> Per-dimension anchors (5/5 vs ≤2) live in `references/scoring-rubric.md`; load it when running the judge.

### 8.2 Pass bar (all must hold)

- **No** dimension < **3**, AND
- dimensions **1, 3, 4, 6, 8, 9, 10** each ≥ **4** (non-negotiable for this pipeline), AND
- The **mean** across all ten dimensions is ≥ **4**.

(A stricter deployment may require every dimension ≥ 4 — `ACTOR_DOSSIER_JUDGE_STRICT`; the default tolerates one non-critical 3 to absorb judge noise.) Below the bar, FAIL with the targeted gap list (§7.3).

## 9. Output contract — ontology-ready

Write so the ontology generator and structured extraction read it directly. Keep the `deep-research` final-dossier contract (its `references/final-dossier-contract.md`), and add this **explicit, labeled structure**:

1. **Forecast frame** — question, forecast object, horizon, as-of date; situation brief (current situation, how it got here, forces in tension, the 3–6 fault lines actors argue over, catalysts).
2. **The cast (key actors)** — one `### Actor: <canonical name>` profile per Tier-1/2 actor, salience order, every §3 + §3.1 field; **canonical names used consistently**, aliases listed once; archetype, role-class, and salience tier marked explicitly **with the one-line §2.3 basis** (extraction emits `salience: {tier, basis}`).
3. **The relationship network** — enumerated directed edges: `Source —[TYPE, valence, strength]→ Target — basis` (§4). Every endpoint a cast name.
4. **Per-actor relational roster** — the §3 roster, named, within or beside each profile.
5. **Evolution & timeline** — the dated sequence of formation, inflection points, realignments (§5).
6. **Drivers, indicators & scenarios** — 3–6 outcome-moving variables, each with a watchable dated indicator; base rates / reference class; rough scenario likelihoods.
7. **Contested claims & source list** — conflicts with positions and why they differ; full source list with tier (S1–S4) and date.
8. **Actor-intelligence coverage ledger** — exactly one source-bound `ACTOR_INTELLIGENCE_LEDGER_V1`; use the complete schema and admission rules in `references/actor-intelligence-contract.md`.

> Consistency makes it ontology-ready: one canonical name per actor across cast, network, roster, and timeline lets extraction resolve entities cleanly and the ontology generator map types and edges without guessing.

## 10. What each section feeds downstream

The situation brief, cast, network, timeline, drivers, contested claims, and
source list feed their matching structured artifacts and graph/simulation
contracts. The §3.1 evidence becomes `actor-role/v2`; the §9.8 ledger gates
global synthesis and `actors[].intelligence.dimensions`. See
`references/actor-intelligence-contract.md` and root `CLAUDE_ONTOLOGY.md` for
the exact schemas.

## 11. Failure modes (checklist)

Reject outlet-as-actor errors, prominence-ranked casts, thin profiles,
imperative role text, inferred/unvalenced networks, static undated snapshots,
inconsistent names, rubber-stamp judging, and breadth that displaces actor
depth. Apply the additional actor-intelligence failures in
`references/actor-intelligence-contract.md` and the evidence failures in the
`deep-research` `references/final-dossier-contract.md` checklist.
