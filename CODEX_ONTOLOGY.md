# CODEX_ONTOLOGY.md

## Purpose

This document proposes a generalized ontology schema for the DeepResearchForecast workflow. It is designed to improve ontology generation, entity detail capture, and relation modeling across arbitrary forecasting domains while remaining compatible with the app's current `entity_types` and `edge_types` contract.

The core recommendation is to treat ontology as two connected layers:

1. A compact Graphiti-compatible type layer, used by ontology generation and graph extraction.
2. A richer forecast instance layer, used by research dossiers, actor seeding, persona generation, simulation setup, retrieval, and final forecast reporting.

The current app already contains both ideas in partial form. `ontology.json` holds type definitions. `actors.json` holds named actors, relationships, situation briefs, quantitative facts, contested claims, indicators, and scenarios. The improvement is to make this separation explicit, standardize the fields, and ensure every entity and relation is useful for forecasting rather than just knowledge graph extraction.

## Current Workflow Fit

The workflow this schema must serve is:

1. User provides a forecasting question or simulation requirement.
2. Deep research produces `research_report.md`, `actors.json`, and `sources.json`.
3. `OntologyGenerator` generates a graph ontology from the research report, simulation requirement, central question, and actor context.
4. `GraphBuilderService.set_ontology()` converts `entity_types` and `edge_types` into dynamic Graphiti entity and edge models.
5. `GraphBuilderService.seed_actors()` writes researched actors and relationships into the graph before free-text extraction.
6. The graph ingests report chunks and enriches seeded entities with additional context.
7. `ZepEntityReader` filters custom-labeled graph nodes into simulation entities.
8. `OasisProfileGenerator` turns those entities plus actor context into agent personas.
9. `SimulationConfigGenerator` uses actors, relationships, events, indicators, and graph neighbors to configure time, events, agents, and initial follows.
10. Report generation and structured forecast extraction use graph retrieval, simulation output, actors, evidence grading, quantitative facts, contested claims, and forecast inputs.

Important current constraints:

- The current runtime expects `ontology["entity_types"]` and `ontology["edge_types"]`.
- Graphiti/Zep custom entity and edge type counts are capped at 10 each in the current generator validation.
- Entity attributes are currently text-like Graphiti fields; edge attributes are string fields.
- Reserved attribute names must be avoided or sanitized: `uuid`, `name`, `group_id`, `name_embedding`, `summary`, `created_at`.
- The current `actors.json` relationship vocabulary is mostly: `ALLY_OF`, `OPPOSES`, `COMPETES_WITH`, `REGULATES`, `DEPENDS_ON`, `PARTNERS_WITH`, `INFLUENCES`, plus `OTHER` with a free-text `relation_label`.
- Simulation follow graph semantics are relation-sensitive, so relation direction must be explicit and stable.

## Main Design Goals

The ontology should do more than label nodes. It should help the app answer: "What can change the forecast, who can change it, what evidence supports that belief, and how would this propagate through the simulation?"

The improved ontology should:

- Be domain-general: usable for markets, policy, geopolitics, companies, products, litigation, technology adoption, social opinion, elections, supply chains, climate risks, public health, and other forecast domains.
- Be workflow-specific: support deep research, evidence grading, graph extraction, actor seeding, persona generation, simulation dynamics, report retrieval, and structured forecast extraction.
- Separate actor-like entities from non-actor forecast objects: not every important entity should become a simulated social account.
- Make relations directional, typed, evidence-backed, time-aware, and forecast-relevant.
- Preserve a compact extraction schema for Graphiti while allowing richer downstream metadata.
- Standardize confidence, uncertainty, source grounding, temporal validity, and scenario conditions.
- Prefer reusable semantic relation families over ad hoc labels, while still allowing domain-specific relation names when the research requires them.
- Make entity details rich enough to seed credible simulation behavior: goals, constraints, incentives, stance, influence, vulnerabilities, likely actions, and information state.

## The Two-Layer Ontology Model

### Layer 1: Type Ontology

The type ontology defines what kinds of entities and relations the graph extractor should recognize.

It is the part the current app can consume immediately:

```json
{
  "entity_types": [],
  "edge_types": [],
  "analysis_summary": ""
}
```

This layer should stay small and high-signal because Graphiti type budgets are limited. It should contain the domain-specific labels that help extraction, such as `CentralBank`, `SemiconductorCompany`, `Regulator`, `CloudProvider`, `PoliticalParty`, `AssetClass`, or `ClinicalTrial`.

### Layer 2: Forecast Instance Model

The forecast instance model defines structured details about named entities, relation instances, events, signals, claims, scenarios, and evidence.

It should be used by the research dossier and downstream stages:

```json
{
  "entities": [],
  "relations": [],
  "events": [],
  "signals": [],
  "claims": [],
  "forecast_inputs": {},
  "sources": []
}
```

This layer can be richer than the Graphiti type schema because it is not constrained by the 10 type cap. It is where the workflow records "OpenAI depends on NVIDIA GPUs with high strength, evidence S1/S2, valid as of 2026-06-25, implication: GPU supply constraints affect AI revenue timing" rather than merely teaching the graph that `Company DEPENDS_ON Supplier`.

## Generalized Entity Archetypes

Every entity type should map to one archetype. Archetypes are stable across domains; entity type names are domain-specific.

| Archetype | Meaning | Examples | Simulation Role |
|---|---|---|---|
| `actor` | Can decide, act, speak, regulate, allocate resources, or influence outcomes. | Person, Company, GovernmentAgency, Investor, PoliticalParty, University, ActivistGroup | Usually eligible for personas and initial follows. |
| `collective` | A population or audience segment whose aggregate behavior matters. | Voters, Consumers, RetailInvestors, Alumni, Developers | May become representative personas or audience distribution. |
| `asset_or_object` | A tracked forecast object that cannot itself act. | Stock, Product, Technology, Bill, Lawsuit, DrugCandidate, Commodity | Context object, not usually persona. |
| `event` | A dated or trigger-based occurrence that changes state. | Election, EarningsRelease, CourtDecision, ProductLaunch | Simulation event, timeline anchor, scenario trigger. |
| `signal` | A measurable indicator or observed input. | InflationRate, PollResult, ShipmentVolume, ChurnRate, SearchTrend | Forecast input, monitoring trigger, report evidence. |
| `claim_or_narrative` | A belief, contested assertion, story, frame, or thesis. | BullCase, SafetyConcern, FraudAllegation, PolicyNarrative | Persona belief seed, contested claim, report argument. |
| `constraint` | A limiting rule, capacity, dependency, bottleneck, or mandate. | Regulation, BudgetCap, ComputeCapacity, ExportControl | Forecast driver and causal limiter. |
| `location_or_jurisdiction` | A geographic, legal, market, or institutional scope. | China, EU, California, AppStoreMarket, SemiconductorSupplyChain | Context, source-target qualifier, policy scope. |
| `source` | Evidence-producing object or institution. | SEC Filing, PeerReviewedStudy, PressRelease, Pollster, NewsOutlet | Citation and reliability context. |
| `scenario` | A possible future state. | BaseCase, UpsideCase, DownsideCase, TailRiskCase | Structured forecast output and simulation branch. |

Type generation rule:

- Pick 6 to 10 entity types for Graphiti.
- Prefer domain-specific subtypes, but map each to an archetype.
- Include `Person` and `Organization` only when the domain actually contains important natural people or broad institutions that need fallback capture.
- Do not waste type budget on generic abstractions if the same concept can be represented as an attribute, signal, claim, event, or scenario.
- If a non-actor object is central to the forecast, include it even if it will not become a simulation persona.

## Generalized Entity Type Schema

Each generated entity type should follow this schema.

```json
{
  "name": "PascalCaseTypeName",
  "archetype": "actor|collective|asset_or_object|event|signal|claim_or_narrative|constraint|location_or_jurisdiction|source|scenario",
  "description": "Short description of the type and when to use it.",
  "selection_rule": "Boundary rule that tells extraction when this type applies and when it does not.",
  "simulation_role": "persona|representative_persona|context|event|signal|evidence|scenario|none",
  "persona_priority": "high|medium|low|none",
  "retrieval_priority": "high|medium|low",
  "attributes": [
    {
      "name": "snake_case_attribute",
      "type": "text",
      "description": "What this attribute captures.",
      "required": false,
      "forecast_use": "How this attribute changes simulation, retrieval, or forecast reasoning."
    }
  ],
  "examples": ["Example entity"],
  "anti_examples": ["Thing that should not be labeled with this type"]
}
```

The current Graphiti integration will ignore unknown fields such as `archetype`, `selection_rule`, `simulation_role`, `persona_priority`, `retrieval_priority`, and `anti_examples`. They should still be preserved in `ontology.json` after a migration because they are useful to validation, report prompts, and future downstream logic.

### Recommended Common Entity Attributes

These can appear on multiple actor-like entity types:

| Attribute | Type | Use |
|---|---|---|
| `role` | text | Role in the forecast question. |
| `stance` | text | Current position toward the key issue or outcome. |
| `influence_tier` | text | `high`, `medium`, or `low` influence. |
| `jurisdiction` | text | Legal, market, or geographic scope. |
| `sector` | text | Industry or domain segment. |
| `goals` | text | Compressed goal statement. |
| `constraints` | text | Known limits on action. |
| `resources` | text | Assets, authority, budget, data, capital, supply, or audience. |
| `vulnerabilities` | text | Failure modes, dependencies, exposures, reputation risks. |
| `information_state` | text | What the entity likely knows, believes, or does not know. |
| `time_horizon` | text | Short, medium, or long-term orientation. |
| `forecast_relevance` | text | Why this entity matters for the forecast. |

These can appear on signal-like or event-like entity types:

| Attribute | Type | Use |
|---|---|---|
| `metric_definition` | text | What exactly the signal measures. |
| `unit` | text | Unit or scale. |
| `as_of_date` | text | Observation date. |
| `release_date` | text | Publication or scheduled event date. |
| `thresholds` | text | Values that would change forecast interpretation. |
| `directionality` | text | Whether up/down/positive/negative matters. |
| `source_tier` | text | Evidence quality tier. |

## Generalized Entity Detail Schema

The entity detail schema is richer than the Graphiti type schema and should be used for `actors.json` evolution, graph seeding, persona generation, and report context. It can represent actors and non-actors.

```json
{
  "id": "stable_slug_or_uuid",
  "canonical_name": "Canonical entity name",
  "entity_type": "GeneratedEntityTypeName",
  "archetype": "actor",
  "aliases": ["Known alias", "Ticker", "Abbreviation"],
  "description": "One-sentence disambiguating description.",
  "role_in_question": "Why this entity matters to the central forecast question.",
  "jurisdiction": "Relevant legal/geographic/market scope",
  "sector": "Relevant sector or domain",
  "status": "active|inactive|proposed|emerging|legacy|unknown",
  "stance": {
    "label": "supportive|opposing|neutral|mixed|unknown",
    "toward": "Forecast object, policy, claim, person, or outcome",
    "rationale": "Evidence-backed explanation"
  },
  "influence": {
    "tier": "high|medium|low|unknown",
    "score": 0.0,
    "basis": "Why this influence level is assigned",
    "channels": ["regulatory", "capital", "media", "technical", "distribution"]
  },
  "goals": [
    {
      "goal": "Goal statement",
      "priority": "high|medium|low",
      "time_horizon": "near|medium|long",
      "evidence_refs": ["S1"]
    }
  ],
  "constraints": [
    {
      "constraint": "Constraint statement",
      "severity": "high|medium|low",
      "binding_condition": "When it matters",
      "evidence_refs": ["S2"]
    }
  ],
  "assets": [
    {
      "asset": "Resource, authority, capability, audience, or capital",
      "forecast_use": "How it can affect outcomes"
    }
  ],
  "vulnerabilities": [
    {
      "vulnerability": "Exposure, weakness, dependency, or reputational risk",
      "trigger": "What activates it",
      "forecast_use": "How it affects scenarios"
    }
  ],
  "dependencies": [
    {
      "depends_on": "Other entity canonical name",
      "relation_type": "DEPENDS_ON|SUPPLIES|FUNDS|REGULATES|OTHER",
      "strength": "high|medium|low",
      "basis": "Evidence-backed summary"
    }
  ],
  "likely_actions": [
    {
      "action": "Likely action",
      "probability_band": "low|medium|high",
      "preconditions": ["Condition that makes the action likely"],
      "expected_effect": "Forecast or simulation effect"
    }
  ],
  "decision_rights": [
    {
      "decision": "What this entity can decide",
      "scope": "Scope of authority",
      "limits": "Legal, financial, technical, or political limits"
    }
  ],
  "information_state": {
    "known_facts": ["What this entity likely knows"],
    "beliefs": ["Beliefs or assumptions it seems to hold"],
    "unknowns": ["Information gaps"],
    "asymmetries": ["What it knows that others may not"]
  },
  "stated_vs_revealed": {
    "stated_position": "Public statement",
    "revealed_behavior": "Observed behavior",
    "gap": "Difference and interpretation"
  },
  "simulation_profile_hints": {
    "persona_seed": "Behavioral/persona paragraph for agent generation",
    "activity_level": "high|medium|low|unknown",
    "sentiment_bias": -0.2,
    "response_speed": "fast|normal|slow",
    "interested_topics": ["Topic A"],
    "posting_style": "How this entity would speak in the simulation"
  },
  "retrieval_tags": ["tag1", "tag2"],
  "forecast_relevance": {
    "drivers": ["Driver this entity affects"],
    "scenarios": ["Scenario this entity makes more or less likely"],
    "indicators_to_monitor": ["Observable sign"]
  },
  "evidence": [
    {
      "claim": "Entity-specific claim",
      "source_id": "S1",
      "quote_or_summary": "Short evidence summary",
      "tier": "S1|S2|S3|S4",
      "confidence": "high|medium|low"
    }
  ],
  "confidence": {
    "overall": "high|medium|low",
    "rationale": "Data quality, agreement, recency, ambiguity"
  },
  "as_of_date": "YYYY-MM-DD",
  "open_questions": ["Question future research should resolve"]
}
```

### Entity Detail Rules

- `canonical_name` must be stable enough to use for graph seeding and actor matching.
- `aliases` should include non-overlapping names, abbreviations, tickers, translated names, handles, and common spellings.
- `description` should disambiguate the entity; it should not merely repeat the name.
- `role_in_question` should explain why the entity matters to the forecast, not just what the entity is.
- `simulation_profile_hints` should exist only for actor or collective entities that may become personas.
- Non-actor entities should still include `forecast_relevance`, evidence, indicators, and scenario links.
- Confidence must be attached to the entity details, not just to the final forecast.

## Generalized Relation Families

Relations should be grouped into stable semantic families. The concrete edge type can be domain-specific, but it should map to one of these families.

| Family | Core Meaning | Recommended Edge Names |
|---|---|---|
| `alignment` | Shared goals, support, opposition, rivalry, coalitions. | `ALLY_OF`, `SUPPORTS`, `OPPOSES`, `COMPETES_WITH`, `COALITION_WITH` |
| `governance` | Authority, rules, enforcement, approval, legal control. | `REGULATES`, `APPROVES`, `SANCTIONS`, `INVESTIGATES`, `LITIGATES_AGAINST`, `VOTES_ON` |
| `ownership_control` | Equity, control rights, management, custody, voting power. | `OWNS`, `CONTROLS`, `MANAGES`, `HOLDS`, `ACQUIRES` |
| `economic_exchange` | Money, supply, purchase, contracts, revenue, funding. | `SUPPLIES`, `BUYS_FROM`, `FUNDS`, `INVESTS_IN`, `CUSTOMER_OF`, `REVENUE_DEPENDS_ON` |
| `dependency` | Reliance, bottlenecks, prerequisites, exposure. | `DEPENDS_ON`, `REQUIRES`, `BOTTLENECKED_BY`, `EXPOSED_TO`, `VULNERABLE_TO` |
| `causal_mechanism` | Cause, enablement, constraint, acceleration, delay. | `CAUSES`, `ENABLES`, `CONSTRAINS`, `TRIGGERS`, `ACCELERATES`, `DELAYS` |
| `information_influence` | Persuasion, narrative, media, signaling, disclosure. | `INFLUENCES`, `AMPLIFIES`, `CRITICIZES`, `ENDORSES`, `SIGNALS`, `REPORTS` |
| `measurement_evidence` | Evidence, measurement, citation, source support. | `MEASURES`, `CITES`, `SUPPORTS_CLAIM`, `CONTRADICTS_CLAIM`, `PUBLISHED_BY` |
| `temporal_sequence` | Before/after, schedule, lead-lag. | `PRECEDES`, `FOLLOWS`, `SCHEDULED_FOR`, `VALID_UNTIL` |
| `substitution_complementarity` | Replacements, complements, alternatives. | `SUBSTITUTES_FOR`, `COMPLEMENTS`, `DISPLACES` |
| `scenario_link` | Relation to possible futures. | `INCREASES_PROBABILITY_OF`, `DECREASES_PROBABILITY_OF`, `RESOLVES_TO`, `INDICATES_SCENARIO` |

## Recommended Cross-Domain Relation Set

For the current 10-edge cap, start with a small reusable set. Add domain-specific edges only when the central question requires them.

| Edge | Direction | Forecast Use | Simulation Use |
|---|---|---|---|
| `INFLUENCES` | Source affects target's beliefs, behavior, exposure, or outcome. | Captures persuasion, market-moving influence, policy pressure, or narrative diffusion. | Target may follow or react to source. |
| `DEPENDS_ON` | Source relies on target. | Reveals bottlenecks and fragility. | Source attends to target; target shocks propagate to source. |
| `REGULATES` | Source sets or enforces rules on target. | Captures authority and policy risk. | Regulator monitors target; target reacts to regulator. |
| `OPPOSES` | Source conflicts with target. | Captures adversarial dynamics and fault lines. | Bidirectional attention and reply likelihood. |
| `PARTNERS_WITH` | Source collaborates with target. | Captures coalitions, alliances, business relationships. | Bidirectional attention and amplification. |
| `COMPETES_WITH` | Source competes with target. | Captures market share, political rivalry, resource competition. | Bidirectional monitoring and contrast. |
| `SUPPLIES` | Source supplies target with critical input. | Captures material dependencies. | Target follows supplier if supplier is an actor. |
| `FUNDS` | Source funds target. | Captures capital and incentive flow. | Recipient attends to funder. |
| `CONSTRAINS` | Source limits target or outcome. | Captures binding risks and blockers. | Target reacts to constraint. |
| `SIGNALS` | Source provides evidence about target/outcome. | Captures indicators and weak signals. | Used more for forecast/report than persona graph. |

If the domain is social opinion heavy, the legacy relation set remains useful. If the domain is market, policy, or technical, `SUPPLIES`, `FUNDS`, `CONSTRAINS`, and `SIGNALS` are often more forecast-useful than another generic social relation.

## Generalized Relation Type Schema

Each relation type should follow this schema.

```json
{
  "name": "UPPER_SNAKE_CASE",
  "family": "alignment|governance|ownership_control|economic_exchange|dependency|causal_mechanism|information_influence|measurement_evidence|temporal_sequence|substitution_complementarity|scenario_link",
  "description": "Short description of the relation.",
  "direction_semantics": "Plain-language rule: source does X to/for/against/through target.",
  "inverse_semantics": "Plain-language inverse, if useful.",
  "source_targets": [
    {
      "source": "SourceEntityType",
      "target": "TargetEntityType"
    }
  ],
  "attributes": [
    {
      "name": "strength",
      "type": "text",
      "description": "high|medium|low; magnitude or importance of this relation."
    },
    {
      "name": "polarity",
      "type": "text",
      "description": "positive|negative|mixed|neutral; relation direction for outcome or sentiment."
    },
    {
      "name": "confidence",
      "type": "text",
      "description": "high|medium|low; confidence in the relation."
    },
    {
      "name": "valid_from",
      "type": "text",
      "description": "Start date or as-of date if known."
    },
    {
      "name": "basis",
      "type": "text",
      "description": "Short evidence-backed basis for the relation."
    },
    {
      "name": "forecast_relevance",
      "type": "text",
      "description": "Why this relation matters for forecast outcomes."
    }
  ],
  "simulation_effect": {
    "attention_direction": "source_to_target|target_to_source|bidirectional|none",
    "follow_bias": "increase|decrease|none",
    "stance_effect": "align|oppose|monitor|none",
    "shock_propagation": "source_to_target|target_to_source|bidirectional|none"
  },
  "forecast_use": "How analysts and report generation should use this edge.",
  "examples": [
    {
      "source": "Example A",
      "target": "Example B",
      "basis": "Why this is a valid example."
    }
  ],
  "anti_examples": ["Example misuse"]
}
```

## Generalized Relation Instance Schema

Every researched relation instance should use this shape. This can evolve the current `actors.json.relationships[]` format without breaking the old fields.

```json
{
  "id": "stable_relation_id",
  "source": "Canonical source entity name",
  "target": "Canonical target entity name",
  "type": "DEPENDS_ON",
  "relation_label": "Optional domain-specific label if type is OTHER or if more precision is needed",
  "family": "dependency",
  "summary": "One-sentence relationship summary.",
  "direction_semantics": "source depends on target",
  "mechanism": "Why/how the relation works.",
  "sign": "ally|rival|neutral|mixed|unknown",
  "polarity": "positive|negative|mixed|neutral|unknown",
  "strength": "high|medium|low|unknown",
  "confidence": "high|medium|low",
  "grade": "A1",
  "since": "YYYY-MM-DD",
  "until": "YYYY-MM-DD",
  "valid_at": "YYYY-MM-DD",
  "lag": "Immediate, days, quarters, years, or unknown",
  "basis": "Evidence-backed basis for the edge.",
  "evidence_refs": ["S1", "S2"],
  "source_tiers": ["S1", "S2"],
  "scenario_conditions": [
    {
      "condition": "When this relation becomes stronger/weaker/irrelevant",
      "effect": "Forecast impact under that condition"
    }
  ],
  "forecast_implication": "How this relation changes scenario likelihood, timing, magnitude, or uncertainty.",
  "simulation_mapping": {
    "initial_follow": true,
    "follow_direction": "source_to_target|target_to_source|bidirectional|none",
    "interaction_bias": "increase|decrease|none",
    "response_trigger": "What kind of event causes source/target to react"
  },
  "open_questions": ["What would change confidence in this relation?"]
}
```

### Relation Direction Rules

Relation direction must never be implicit. For each relation type:

- `REGULATES`: source is the regulator, target is regulated.
- `DEPENDS_ON`: source is dependent, target is depended on.
- `SUPPLIES`: source supplies, target receives.
- `FUNDS`: source provides capital, target receives.
- `INFLUENCES`: source influences, target is influenced.
- `OPPOSES`: source opposes target; often mirrored if both sides actively oppose.
- `COMPETES_WITH`: usually symmetric, but keep extracted direction and allow simulation to mirror.
- `PARTNERS_WITH`: usually symmetric, but keep extracted direction and allow simulation to mirror.
- `CONSTRAINS`: source is the constraint or constraining actor, target is constrained.
- `SIGNALS`: source is the signal/evidence, target is the object or outcome it informs.

## Event Schema

Events should not be buried in prose because they drive scheduled simulation triggers and scenario timing.

```json
{
  "id": "event_id",
  "name": "Event name",
  "event_type": "announcement|deadline|release|vote|hearing|earnings|launch|crisis|shock|milestone|other",
  "date": "YYYY-MM-DD",
  "date_precision": "day|month|quarter|year|unknown",
  "status": "scheduled|occurred|rumored|conditional|cancelled",
  "participants": ["Canonical entity name"],
  "summary": "What happens or happened.",
  "trigger_conditions": ["Condition if event is conditional"],
  "expected_effects": [
    {
      "target": "Entity, signal, claim, or scenario",
      "effect": "What changes",
      "direction": "increase|decrease|clarify|delay|accelerate|unknown"
    }
  ],
  "evidence_refs": ["S1"],
  "simulation_use": {
    "schedule_as_event": true,
    "initial_post_hint": "How this should enter the simulation as a prompt/event"
  }
}
```

## Signal Schema

Signals are measurable forecast inputs. They should be explicit enough for monitoring, scenario resolution, and backtesting.

```json
{
  "id": "signal_id",
  "name": "Signal name",
  "metric": "Metric being measured",
  "definition": "Exact definition",
  "unit": "Unit",
  "current_value": "Value as text or number",
  "as_of_date": "YYYY-MM-DD",
  "frequency": "real_time|daily|weekly|monthly|quarterly|annual|event_based|unknown",
  "source_id": "S1",
  "source_tier": "S1|S2|S3|S4",
  "directionality": {
    "higher_means": "What a higher value implies",
    "lower_means": "What a lower value implies"
  },
  "thresholds": [
    {
      "threshold": "Value or condition",
      "interpretation": "Why this threshold matters"
    }
  ],
  "related_entities": ["Canonical entity name"],
  "related_scenarios": ["Scenario name"],
  "forecast_use": "How this signal updates probabilities or timing"
}
```

## Claim And Narrative Schema

Claims and narratives are not the same as facts. They should be represented so the report can reason about disagreement.

```json
{
  "id": "claim_id",
  "claim": "Claim text",
  "claim_type": "factual|causal|forecast|normative|narrative|rumor",
  "status": "confirmed|contested|speculative|single_origin|false|unknown",
  "supporting_sources": ["S1"],
  "opposing_sources": ["S2"],
  "positions": [
    {
      "entity": "Canonical entity name or source",
      "stance": "supports|opposes|qualifies|unclear",
      "basis": "Evidence summary"
    }
  ],
  "why_it_matters": "Forecast relevance",
  "resolution_method": "How the claim could be verified or falsified",
  "confidence": "high|medium|low"
}
```

## Structured Ontology Output Format

The ontology generator should produce a full object like this. The first three fields are the current-compatible core. The additional fields make the ontology self-explanatory and allow future stages to consume richer semantics.

```json
{
  "schema_version": "forecast_ontology.v1",
  "ontology_id": "stable-id-or-slug",
  "central_question": "The forecast question being modeled",
  "domain": {
    "label": "Domain label",
    "subdomains": ["Subdomain A"],
    "forecast_object": "The object, outcome, or decision being forecast",
    "horizon": "Forecast horizon",
    "as_of_date": "YYYY-MM-DD"
  },
  "entity_types": [
    {
      "name": "Regulator",
      "archetype": "actor",
      "description": "Government or quasi-government body that sets or enforces rules.",
      "selection_rule": "Use for named agencies or formal bodies with rulemaking or enforcement power.",
      "simulation_role": "persona",
      "persona_priority": "high",
      "retrieval_priority": "high",
      "attributes": [
        {
          "name": "jurisdiction",
          "type": "text",
          "description": "Legal or market jurisdiction.",
          "required": false,
          "forecast_use": "Determines where regulatory action applies."
        },
        {
          "name": "stance",
          "type": "text",
          "description": "Current policy stance toward the forecast object.",
          "required": false,
          "forecast_use": "Seeds persona and scenario pressure."
        }
      ],
      "examples": ["SEC", "European Commission"],
      "anti_examples": ["Regulation as a legal text; use Policy or Constraint instead."]
    }
  ],
  "edge_types": [
    {
      "name": "REGULATES",
      "family": "governance",
      "description": "Source sets or enforces rules on target.",
      "direction_semantics": "source regulates target",
      "inverse_semantics": "target is regulated by source",
      "source_targets": [
        {
          "source": "Regulator",
          "target": "Company"
        }
      ],
      "attributes": [
        {
          "name": "strength",
          "type": "text",
          "description": "high|medium|low regulatory force."
        },
        {
          "name": "confidence",
          "type": "text",
          "description": "high|medium|low confidence in the relation."
        },
        {
          "name": "valid_from",
          "type": "text",
          "description": "Known start or as-of date."
        },
        {
          "name": "basis",
          "type": "text",
          "description": "Short evidence-backed basis."
        },
        {
          "name": "forecast_relevance",
          "type": "text",
          "description": "How this relation affects the forecast."
        }
      ],
      "simulation_effect": {
        "attention_direction": "source_to_target",
        "follow_bias": "increase",
        "stance_effect": "monitor",
        "shock_propagation": "source_to_target"
      },
      "forecast_use": "Regulatory changes can constrain, delay, approve, or redirect target behavior.",
      "examples": [
        {
          "source": "SEC",
          "target": "PublicCompany",
          "basis": "SEC has formal enforcement and disclosure authority."
        }
      ]
    }
  ],
  "relation_types": "Optional alias of edge_types for tools that prefer relation wording.",
  "entity_detail_schema": {
    "required_fields": ["canonical_name", "entity_type", "archetype", "description", "role_in_question", "forecast_relevance", "confidence", "as_of_date"],
    "recommended_fields": ["aliases", "stance", "influence", "goals", "constraints", "assets", "vulnerabilities", "likely_actions", "simulation_profile_hints", "evidence"]
  },
  "relation_instance_schema": {
    "required_fields": ["source", "target", "type", "summary", "direction_semantics", "strength", "confidence", "basis", "forecast_implication"],
    "recommended_fields": ["family", "mechanism", "sign", "polarity", "valid_at", "since", "until", "evidence_refs", "scenario_conditions", "simulation_mapping"]
  },
  "quality_gates": [
    "6-10 entity_types unless the app cap changes.",
    "6-10 edge_types unless the app cap changes.",
    "Every edge_type has explicit direction_semantics.",
    "Every edge_type source_targets entry references a generated entity type or Entity fallback.",
    "Every actor-like entity type has role, stance, influence_tier, goals or constraints where relevant.",
    "Every relation type includes strength, confidence, basis, and forecast_relevance attributes unless not supported by current runtime.",
    "No attribute uses reserved names: uuid, name, group_id, name_embedding, summary, created_at."
  ],
  "analysis_summary": "Short explanation of why these types and relations fit the central forecast question."
}
```

### Current-Compatible Minimal Output

Until the pipeline preserves extra ontology fields, the generator should still produce the richer object internally but can safely persist this minimal subset:

```json
{
  "entity_types": [
    {
      "name": "Regulator",
      "description": "Government or quasi-government body that sets or enforces rules.",
      "attributes": [
        {
          "name": "jurisdiction",
          "type": "text",
          "description": "Legal or market jurisdiction."
        },
        {
          "name": "stance",
          "type": "text",
          "description": "Current policy stance toward the forecast object."
        }
      ],
      "examples": ["SEC", "European Commission"]
    }
  ],
  "edge_types": [
    {
      "name": "REGULATES",
      "description": "Source sets or enforces rules on target.",
      "source_targets": [
        {
          "source": "Regulator",
          "target": "Company"
        }
      ],
      "attributes": [
        {
          "name": "strength",
          "type": "text",
          "description": "high|medium|low regulatory force."
        },
        {
          "name": "confidence",
          "type": "text",
          "description": "high|medium|low confidence."
        },
        {
          "name": "basis",
          "type": "text",
          "description": "Short evidence-backed basis."
        },
        {
          "name": "forecast_relevance",
          "type": "text",
          "description": "How this relation affects the forecast."
        }
      ]
    }
  ],
  "analysis_summary": "Short rationale."
}
```

## Ontology Generation Procedure

The ontology generator should follow this sequence:

1. Identify the central forecast object.
   - What outcome, decision, market, event, adoption curve, or social reaction is being forecast?
   - What horizon and as-of date matter?

2. Identify the entity universe.
   - Which named actors can act?
   - Which non-actor objects are central enough to track?
   - Which signals, claims, constraints, and events are forecast-critical?

3. Allocate the type budget.
   - Reserve types for high-frequency or high-impact entity categories.
   - Avoid generic `Person` and `Organization` unless needed.
   - Avoid using entity types for one-off entities unless they represent a reusable category.

4. Choose relation families.
   - Include at least one relation type for influence or information flow if simulation behavior matters.
   - Include dependency or constraint relations when the forecast turns on bottlenecks, regulations, capital, supply, approval, or timing.
   - Include evidence/signal relations when indicators and measurable criteria matter.

5. Define direction semantics.
   - Every relation must state exactly what source and target mean.
   - Symmetric relations can still be stored directionally; simulation may mirror them.

6. Attach forecast attributes.
   - Entity types need attributes that improve personas or forecast reasoning.
   - Relation types need attributes that support strength, confidence, timing, evidence, and forecast implication.

7. Validate against workflow gates.
   - Counts, names, source-target validity, reserved attributes, direction semantics, actor/non-actor simulation role, and evidence compatibility.

## Prompting Improvements For Ontology Generation

The ontology prompt should explicitly ask for:

- The central forecast object, horizon, and as-of date.
- A distinction between actor, object, event, signal, claim, constraint, source, and scenario.
- Domain-specific entity type names mapped to stable archetypes.
- Relation family and direction semantics for every relation.
- Relation attributes that support strength, confidence, basis, temporal validity, and forecast relevance.
- A clear explanation of which entity types should become simulation personas and which are context only.
- A compact schema that respects the 10 entity and 10 edge cap.
- Coverage of researched relationships from `actors.json`.
- Anti-examples to reduce over-extraction and concept drift.

A stronger ontology prompt should include these explicit prohibitions:

- Do not create entity types for vague concepts like "sentiment", "risk", "trend", "uncertainty", or "debate" unless the workflow will track them as a claim, signal, scenario, or measurable object.
- Do not create relation names that are vague synonyms of `RELATES_TO`.
- Do not use relation direction ambiguously.
- Do not force social-media actor types into non-social domains.
- Do not put source-specific facts in the type ontology; put them in entity details, relation instances, signals, claims, or evidence.

## Mapping To The App Stages

### Research Dossier

The research stage should produce richer `actors.json` objects that are compatible with the entity detail and relation instance schemas. Existing fields can remain:

- `central_question`
- `as_of_date`
- `situation_brief`
- `actors`
- `relationships`
- `key_events`
- `hot_topics`
- `sources`
- `quantitative_facts`
- `contested_claims`
- `forecast_inputs`

Recommended additions:

- `entities`: include non-actor forecast objects, signals, constraints, claims, and events.
- `relations`: richer relation instances that can be mapped down to existing `relationships`.
- `ontology_hints`: candidate entity types, relation types, and relation families found during research.

Backward-compatible rule:

- Keep `actors[]` for persona-eligible actors.
- Keep `relationships[]` for actor-to-actor or actor-to-key-entity graph seeding.
- Add richer structures without requiring downstream consumers to parse them immediately.

### Ontology Stage

The ontology stage should generate the full structured object, then persist all fields rather than trimming to only `entity_types` and `edge_types`.

Near-term compatibility:

- `set_ontology()` consumes only `entity_types` and `edge_types`.
- Extra fields are available to validation, report prompts, and future migration.

Important improvement:

- `_actors_to_context()` currently tells the generator to cover researched relationships. It should also pass relation labels, relation families, non-actor entities, signals, constraints, and forecast inputs so the ontology covers forecast mechanics, not only named actors.

### Graph Stage

Graph construction should use:

- `entity_types` and `edge_types` for extraction.
- `relations[]` or `relationships[]` for deterministic graph seeds.
- Entity `aliases` and `description` for deduplication.
- `basis`, `strength`, `confidence`, `grade`, and source references in edge facts or edge attributes.

Recommended improvement:

- When `seed_actors()` writes `add_triplet()`, carry structured edge attributes if the runtime supports them later. Until then, preserve them in the fact string.

### Entity Resolution

The entity detail schema improves resolution because it gives:

- Canonical names.
- Aliases that may share no characters with canonical names.
- Disambiguating descriptions.
- Jurisdictions and sectors.
- Source-backed evidence.

Validation should flag:

- Same canonical name used for multiple entities.
- Distinct entities sharing aliases without disambiguation.
- Generic names like "AI", "government", or "market" without scope.

### Persona Generation

Only entities with archetype `actor` or `collective` and `simulation_role` of `persona` or `representative_persona` should be eligible by default.

Persona prompts should consume:

- `role_in_question`
- `stance`
- `influence`
- `goals`
- `constraints`
- `assets`
- `vulnerabilities`
- `information_state`
- `stated_vs_revealed`
- `relationship_briefing`
- `simulation_profile_hints`

This prevents non-actors such as `InflationRate`, `ExportControlRule`, or `Scenario` from becoming awkward simulated accounts while still letting them shape agent behavior.

### Simulation Configuration

Simulation configuration should use relation `simulation_effect`:

- `attention_direction` maps to initial follow direction.
- `follow_bias` influences whether an initial follow should be added.
- `stance_effect` guides agent stance and response style.
- `shock_propagation` identifies which agents react when events affect dependencies or constraints.

Relation instances should drive:

- Initial follows.
- Event reactions.
- Agent interested topics.
- Scenario-specific behavior.
- Expected post/comment tone and timing.

### Report And Forecast Extraction

Report generation should use ontology semantics to avoid generic retrieval:

- Retrieve by relation family when writing sections about drivers, dependencies, constraints, or contested claims.
- Use `forecast_relevance` to decide which graph facts deserve report space.
- Use signals and thresholds for measurable forecast updates.
- Use scenario links for structured forecast extraction.
- Use evidence tiers and contested claims to calibrate confidence.

The final structured forecast should be able to cite:

- Which entity goals/constraints mattered.
- Which relations were decisive.
- Which signals would update the probabilities.
- Which claims were contested.
- Which events were scheduled or conditional.

## Validation Checklist

### Entity Type Validation

- [ ] Entity type count is 6 to 10 unless the app cap changes.
- [ ] Every entity type has `name`, `description`, `attributes`, and `examples`.
- [ ] Every entity type has an archetype in the full schema.
- [ ] Every entity type has a clear selection rule.
- [ ] Actor-like types include attributes for role, stance, influence, goals, or constraints when relevant.
- [ ] Non-actor types are marked context, event, signal, evidence, scenario, or none for simulation role.
- [ ] No attributes use reserved names.
- [ ] No entity type is merely a vague topic unless it is modeled as claim, signal, event, scenario, or constraint.

### Relation Type Validation

- [ ] Relation type count is 6 to 10 unless the app cap changes.
- [ ] Every relation has `name`, `description`, `source_targets`, and `attributes`.
- [ ] Every relation has explicit direction semantics.
- [ ] Every source and target references an existing entity type or a deliberate fallback.
- [ ] Every relation includes strength, confidence, basis, and forecast relevance where supported.
- [ ] Relation names are not vague `RELATED_TO` style edges unless used as last-resort fallback.
- [ ] The relation set covers the key researched relationship types.
- [ ] The relation set contains enough causal/dependency/governance/signal semantics for the forecast, not only social alignment.

### Entity Detail Validation

- [ ] Every entity has canonical name, entity type, archetype, description, and forecast relevance.
- [ ] Persona-eligible entities include stance, influence, goals, constraints, and simulation hints.
- [ ] Non-persona entities are still tied to signals, scenarios, claims, events, or evidence.
- [ ] Entity aliases include abbreviations, translations, handles, tickers, and common variations.
- [ ] Evidence is source-linked and confidence-rated.
- [ ] Open questions are captured when confidence is low or evidence conflicts.

### Relation Instance Validation

- [ ] Every relation endpoint resolves to a canonical entity name or an explicitly scoped non-actor object.
- [ ] Relation direction is unambiguous.
- [ ] Strength, confidence, basis, evidence refs, and forecast implication are present.
- [ ] Time validity is recorded when known.
- [ ] Scenario conditions are recorded when the relation is conditional.
- [ ] Simulation mapping is present for actor-relevant relations.

## Recommended Migration Plan

### Step 1: Preserve Rich Ontology Fields

Current pipeline code persists only:

```python
project.ontology = {
    "entity_types": ontology.get("entity_types", []),
    "edge_types": ontology.get("edge_types", []),
}
```

Change this to preserve the full ontology object while ensuring `entity_types` and `edge_types` remain present. This is low risk because current consumers can still read the existing keys.

### Step 2: Extend The General Forecast Prompt

Update the `general_forecast` ontology prompt to request:

- Archetypes.
- Simulation roles.
- Direction semantics.
- Relation families.
- Forecast relevance.
- Entity and relation quality gates.

Keep the minimal JSON core valid for the current parser.

### Step 3: Add A Normalization Pass

After LLM generation, normalize:

- `relation_types` -> `edge_types` if only one is present.
- Missing archetypes to inferred values.
- Missing relation families to inferred values.
- Missing relation attributes by adding standard text attributes.
- Invalid simulation roles to `context` or `none`.

### Step 4: Evolve `actors.json`

Keep existing `actors[]` and `relationships[]`, but add:

- `entities[]` for non-actor objects.
- `relations[]` for richer relation instances.
- `signals[]` for measurable indicators.
- `claims[]` for contested narratives.

Downstream code can keep using the old helpers while newer helpers consume richer structures.

### Step 5: Use Ontology Semantics In Simulation

Use `simulation_role` to filter persona candidates and `simulation_effect` to map relations to initial follows, shock propagation, and response behavior.

### Step 6: Use Ontology Semantics In Report Retrieval

Allow report tools to retrieve facts by relation family and forecast relevance, not only by text search or raw edge names.

### Step 7: Add Tests

Add tests for:

- Ontology validation accepts a full schema and emits a valid current-compatible subset.
- Reserved attribute names are sanitized.
- Relation source/target references are valid.
- Persona filtering excludes non-actors.
- Relation direction maps correctly to initial follows.
- Rich `relations[]` degrade into legacy `relationships[]` where needed.

## Example Type Allocation By Domain

### AI Infrastructure Forecast

Potential entity types:

- `AICompany` (`actor`)
- `ChipSupplier` (`actor`)
- `CloudProvider` (`actor`)
- `Regulator` (`actor`)
- `Investor` (`actor`)
- `Model` (`asset_or_object`)
- `ComputeCapacity` (`constraint`)
- `Benchmark` (`signal`)

Potential edge types:

- `DEPENDS_ON`
- `SUPPLIES`
- `FUNDS`
- `REGULATES`
- `COMPETES_WITH`
- `PARTNERS_WITH`
- `CONSTRAINS`
- `SIGNALS`

### Election Forecast

Potential entity types:

- `Candidate` (`actor`)
- `PoliticalParty` (`actor`)
- `VoterBloc` (`collective`)
- `MediaOutlet` (`actor`)
- `Pollster` (`source`)
- `Jurisdiction` (`location_or_jurisdiction`)
- `CampaignEvent` (`event`)
- `PollingSignal` (`signal`)

Potential edge types:

- `SUPPORTS`
- `OPPOSES`
- `INFLUENCES`
- `FUNDS`
- `ENDORSES`
- `MEASURES`
- `SIGNALS`
- `PRECEDES`

### Product Launch Forecast

Potential entity types:

- `Company` (`actor`)
- `Product` (`asset_or_object`)
- `Competitor` (`actor`)
- `CustomerSegment` (`collective`)
- `DistributionPartner` (`actor`)
- `Regulator` (`actor`)
- `LaunchEvent` (`event`)
- `AdoptionMetric` (`signal`)

Potential edge types:

- `COMPETES_WITH`
- `PARTNERS_WITH`
- `SUPPLIES`
- `REGULATES`
- `INFLUENCES`
- `SUBSTITUTES_FOR`
- `COMPLEMENTS`
- `SIGNALS`

## Practical Defaults

If the ontology generator is uncertain, use these defaults:

- Prefer `actor`, `asset_or_object`, `event`, `signal`, and `constraint` archetypes before adding exotic categories.
- Include `INFLUENCES`, `DEPENDS_ON`, `CONSTRAINS`, and `SIGNALS` unless the domain clearly does not need them.
- Include `REGULATES` for any domain with law, compliance, approval, licensing, platform rules, or government action.
- Include `COMPETES_WITH` for any market, political, social, hiring, product, or resource-allocation domain.
- Include `PARTNERS_WITH` when alliances, coalitions, supply agreements, distribution, or joint action matter.
- Use `OTHER` only in the instance layer, with a concrete `relation_label`; do not make `OTHER` a first-class edge type unless absolutely necessary.
- If a relation affects forecast probability but not simulation interaction, set `simulation_effect.attention_direction` to `none`.
- If an entity is important but should not become an agent, set `simulation_role` to `context`, `event`, `signal`, `evidence`, or `scenario`.

## Summary Recommendation

The app should move from a "social actor ontology" to a "forecast mechanics ontology."

The best generalized schema is:

- A compact, Graphiti-compatible type ontology: `entity_types`, `edge_types`, and `analysis_summary`.
- A richer forecast instance schema: `entities`, `relations`, `events`, `signals`, `claims`, `forecast_inputs`, and `sources`.
- Entity archetypes that separate actors from objects, events, signals, constraints, claims, sources, and scenarios.
- Relation families that preserve stable meaning across domains.
- Relation instances that are directional, evidence-backed, time-aware, confidence-rated, and mapped to simulation effects.
- Entity details that capture goals, constraints, influence, stance, vulnerabilities, likely actions, information state, and forecast relevance.

This design keeps the existing pipeline working while giving ontology generation enough structure to improve graph quality, persona realism, simulation dynamics, retrieval precision, forecast calibration, and downstream report quality.
