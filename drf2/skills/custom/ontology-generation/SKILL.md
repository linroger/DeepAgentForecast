---
name: ontology-generation
description: Use this skill in the DRF-2 pipeline to design the knowledge-graph ontology (entity types + edge types) from the research report and the actor dossier, before any graph construction. It encodes the proven methodology — domain-adaptive templates (social-opinion vs general-forecast), dossier-seeded type budgets, archetype and simulation-tier tagging, edge family/valence/direction semantics, causal edge design, and reserved-attribute rules — and produces the exact ontology.json contract the graph stage applies to the KG engine.
allowed-tools:
  - read_file
  - write_file
  - ls
  - grep
  - glob
  - tool_search
  - kg_search
  - kg_get_entities
---

# Ontology Generation Skill

## 1. Mission

Design the entity/edge type system that turns unstructured research into a **queryable, simulation-ready knowledge graph**. The ontology decides what the extraction stage can see: types you omit become untyped generic blobs; edges you leave unvalenced make rivals and partners indistinguishable. You are designing the *lens*, and the lens must fit the **forecast domain**, not a fixed template.

Inputs (read them all before designing):
1. The **actor dossier** (`actor_dossier.md` from the `actor-ontology-research` skill) — the single source of truth for the cast, archetypes, role-classes, and the relationship taxonomy already used.
2. The **research report** (`research_report.md`) — context, drivers, events, quantitative facts.
3. The **central question** — the forecast the graph must ultimately serve.
4. The structured **actors.json** if available (actor type histogram, relationships[]).

Output: one JSON object (schema in §5) written to **`ontology.json`** in the stage's outputs. The Pipeline Driver / graph stage applies it to the KG engine deterministically before any episode ingestion — you design the ontology; you do not apply it yourself.

## 2. Template selection (domain-adaptive, not one-size-fits-all)

Two proven design templates exist. Pick deliberately:

| Template | When | Signature |
|---|---|---|
| **social_opinion** | The question is about *public reaction / public opinion / public sentiment / social media dynamics* — backlash, controversy, virality, reputation, 舆情/民意/舆论/热搜. | Exactly 10 entity types; the last 2 are the fallback types `Person` and `Organization`; the first 8 are content-specific speakable subjects. |
| **general_forecast** | Everything else: market/asset moves, geopolitics/policy, product/tech launches, corporate events (M&A, earnings, litigation, supply chains). | 6–10 entity types sized to the real cast composition; fallback `Person`/`Organization` only if the cast actually contains unclassifiable people/orgs; type budget follows the actor-type histogram. |

**Auto-select rule:** default to `general_forecast`; switch to `social_opinion` only when the central question / simulation requirement contains genuine public-opinion semantics (two-word combinations like "public reaction", "public opinion", "social media", "舆情", "公众舆论" — NOT bare words like "sentiment" or "opinion", which appear in market questions as "investor sentiment" / "expert opinion" and must not trigger the social template).

## 3. Dossier seeding (single source of truth)

When an actor dossier / actors.json exists, **project the realized cast into ontology seeds instead of re-deriving types from prose** — this prevents schema/instance drift between the dossier and the graph:

- **Entity-type seeds**: the distinct `actor.type` values in the cast (e.g. `CentralBank`, `Regulator`, `ChipMaker`). **Keep every seed type; never rename them** — extraction resolves instances against these exact names.
- **Edge-type seeds**: the distinct `relationships[].type` values already researched (`ALLY_OF`, `OPPOSES`, `COMPETES_WITH`, `REGULATES`, `DEPENDS_ON`, `PARTNERS_WITH`, `INFLUENCES`, plus economic/governance/causal edges). Reuse them verbatim so typed retrieval matches the injected relationship graph.
- **Adaptive budget**: on top of the seeds you may add new domain-specific types, up to `max(2, 8 − seed_count)` new entity types, with a hard cap of **10 entity types total**. If the dossier's types are coarse (only `Government`/`Organization`), that budget lets you add fine domain types; if the seeds are already rich, add few.
- **Cast-histogram fit**: type count and composition should mirror the actor-type histogram — high-frequency types deserve their own crisp, non-overlapping entity type.

If the dossier is missing or degraded (an error string, or suspiciously short), fall back to designing from the research report alone with the safe default of including both `Person` and `Organization` fallbacks — absence of dossier data is NOT evidence that the cast has no people or organizations.

## 4. Design rules

### 4.0 Main-actor discipline (binding, applies to everything below)
The ontology exists to serve a forecast whose cast is **at most 20 main actors** — only entities whose **decisions and actions will causally affect the event being forecasted**. Design the schema so the extraction naturally enforces this: entity types for the principal/stakeholder cast get `simulation_tier` 1–2; **media organizations, journalists, commentators, analysts, pollsters, and think tanks are tier-3 sources** (archetype=`source`) unless the forecast question is literally about media behavior — they must never be instantiable as agentic tier-1/2 actors. Put this exclusion in each relevant type's `anti_examples`. A schema that lets 50 entities qualify as agents is a design failure: bias every selection_rule toward decision agency over the outcome.

### 4.1 Entity types
- Entities must be **real subjects relevant to the forecast** — things that can decide, speak, or exert influence (people, companies, institutions, regulators, states/economies, media platforms) or **trackable core objects** of the question (an asset, a product) when they must be followed.
- **Never** create types for abstractions ("Risk", "Sentiment", "Trend"), bare topics ("Inflation", "AI regulation"), or opinions/stances ("Supporters", "Bears").
- 1–3 **retrieval-serving attributes** per key type: `influence_tier` (high/medium/low), `sector`, `jurisdiction`, `role`, `stance`. For agentic subjects, prefer attributes that can drive agent behavior: `role`, `stance`, `influence_tier`, `motivation`/`goals`, `interests`.
- Naming: type names in English **PascalCase**; descriptions in English, ≤100 characters; attributes in English **snake_case**.

### 4.2 Edge types
- 6–10 edge types covering the domain's real interactions. **Prefer the research relationship taxonomy** (`ALLY_OF`, `OPPOSES`, `COMPETES_WITH`, `REGULATES`, `DEPENDS_ON`, `PARTNERS_WITH`, `INFLUENCES`) when semantics match; define domain-specific names (UPPER_SNAKE_CASE) only when nothing fits.
- **Causal/mechanism edges are high-value for forecasting — strongly recommended** whenever the domain has transmission mechanisms: `CAUSES`, `ENABLES`, `CONSTRAINS`, `TRIGGERS`, `ACCELERATES` (family=causal), each carrying `sign` / `lag` / `strength` attributes where possible. These upgrade the graph from an index into a transmission model — "how a shock propagates to the outcome" beats "who knows whom".
- Every edge type's `source_targets` must connect entity types you actually defined.
- Attach **forecast-discriminating attributes** to key edge types: `sentiment` (positive/negative/neutral), `strength` (high/medium/low), `since_date`.

### 4.3 Reserved attribute names (hard rule)
Attribute names must NEVER be `name`, `uuid`, `group_id`, `created_at`, `summary`, or `name_embedding` (system-reserved). Use `full_name`, `org_name`, etc. instead.

## 5. Output JSON contract

```json
{
    "entity_types": [
        {
            "name": "PascalCaseTypeName",
            "description": "short English description, max 100 chars",
            "attributes": [
                {"name": "snake_case_attr", "type": "text", "description": "attribute description"}
            ],
            "examples": ["Example Entity 1", "Example Entity 2"],
            "archetype": "actor",
            "simulation_tier": 1,
            "role_class": "principal",
            "selection_rule": "one-sentence rule deciding which entities belong to this type",
            "anti_examples": ["things that must NOT be instantiated as this type"]
        }
    ],
    "edge_types": [
        {
            "name": "UPPER_SNAKE_CASE_REL",
            "description": "short English description, max 100 chars",
            "source_targets": [{"source": "SourceType", "target": "TargetType"}],
            "attributes": [
                {"name": "sentiment", "type": "text", "description": "positive/negative/neutral"}
            ],
            "family": "alignment",
            "valence": "allied",
            "direction_semantics": "one sentence: what source does to target, so the direction is never read backwards"
        }
    ],
    "analysis_summary": "brief analysis of the forecast question and domain (may be Chinese)"
}
```

Core keys (`name`/`description`/`attributes`/`examples` for entities; `name`/`description`/`source_targets`/`attributes` for edges) are consumed by the engine's set-ontology step — never restructure them. The classification keys below are **additive metadata** the downstream stages read.

### 5.1 Rich classification metadata (always emit)

For every **entity type**:
- `archetype` — one of: `actor` (a real subject that decides/speaks), `collective` (faction/group/coalition), `institution_rule` (institution/rule/law), `asset_object` (asset/product/tracked object), `event`, `signal` (indicator/metric), `claim_narrative`, `constraint_resource`, `place_jurisdiction`, `source` (a cited outlet/report — an information source), `scenario`.
- `simulation_tier` — integer default tier: **1** = core decision-maker, **2** = stakeholder/faction, **3** = passive information source (cited media/reports/analyses), **4** = abstract concept/resource/object.
- `role_class` (optional) — `principal` / `arbiter` / `stakeholder` / `amplifier` / `intermediary`.
- `selection_rule` — one-sentence disambiguation rule for the extraction stage.
- `anti_examples` — what must NOT be instantiated as this type. **Critical rule: cited media/publications/rankings/databases are `source` (archetype=source, tier=3), NOT agentic actors** — unless the outlet itself is a party driving the outcome, never give it an agentic entity type.

For every **edge type**:
- `family` — one of: `alignment` (allied/supportive, +), `antagonism` (opposition/sanction/criticism, −), `economic` (supply/funding/ownership/consumption, transactional), `governance` (regulation/adjudication), `dependency`, `influence`, `information` (reporting/disclosure), `causal` (mechanism transmission: CAUSES/ENABLES/CONSTRAINS/TRIGGERS/ACCELERATES, directed).
- `valence` — one of: `allied`, `adversarial`, `transactional`, `directional`, `neutral`.
- `direction_semantics` — one sentence pinning who does what to whom.

The KG engine normalizes missing archetype/family/valence deterministically from names (causal edge names are hard-locked to `family=causal, valence=directional`), but that fallback is coarse — **label explicitly; do not rely on inference**.

## 6. Quality checklist (before writing ontology.json)

- [ ] Template matches the domain (no social-media schema forced onto a market/geopolitics question, and vice versa)?
- [ ] All dossier seed types preserved verbatim; new types within the adaptive budget; total entity types ≤ 10?
- [ ] Every entity type is a concrete, mutually-exclusive subject class — no abstractions, topics, or stances?
- [ ] Cited-outlet trap avoided: information sources typed as `source`/tier-3, not as agentic actors?
- [ ] Edge set covers alignment + antagonism + the under-reported economic/governance/dependency edges, and at least one causal family edge when a transmission mechanism exists?
- [ ] Every edge has family, valence, and direction_semantics; rivals and partners are distinguishable?
- [ ] No reserved attribute names; naming conventions respected (PascalCase / UPPER_SNAKE_CASE / snake_case)?
- [ ] `source_targets` reference only defined entity types?
- [ ] Output is valid JSON and nothing else?

## 7. Failure modes

- ❌ Forcing the fixed 10-type social-media template onto a non-social forecast (or triggering it on bare words like "sentiment").
- ❌ Renaming or dropping dossier seed types — breaks entity resolution against actors.json.
- ❌ Typing a cited newspaper/report/database as an agentic actor instead of `source`/tier-3.
- ❌ Abstract-concept entity types ("Risk", "Uncertainty") that extraction can never ground.
- ❌ Unvalenced edges — `RELATED_TO`-style types where opposition and partnership look identical.
- ❌ Omitting causal edges in a domain that plainly has transmission mechanics (tariffs → costs → prices).
- ❌ Using reserved attribute names (`name`, `uuid`, `group_id`, `created_at`, `summary`, `name_embedding`).
- ❌ More than 10 entity types, or budget-padding with types the cast histogram does not support.
