# DeepAgentForecast: Improved Generalized Ontology & Relationship Schema

## 1. Introduction & Core Philosophy

The current ontology generation approach in DeepAgentForecast extracts all entities mentioned in the research phase—including reporters, news outlets, and passive concepts—and often promotes them into the multi-agent simulation. This leads to "simulation dilution," where compute is wasted on minor actors, and the truly important players lack the deep, strategic context (incentives, beliefs, resources) required to drive a high-fidelity predictive simulation.

To solve this, we must shift from a **flat semantic extraction** to a **tiered, structurally rich behavioral ontology**. 

The improved generalized schema ensures:
1. **Entity Tiering:** Distinguishing active decision-makers from passive information sources.
2. **Behavioral DNA:** Equipping simulated actors with values, beliefs, and incentives rather than just factual summaries.
3. **Polarized & Strategic Relationships:** Defining edges that map power dynamics, dependencies, and conflicts.

---

## 2. Entity Tiering (Solving the "Minor Actor" Problem)

To ensure the simulation only instantiates relevant actors, the ontology must classify entities into functional tiers. The simulation engine should only spawn personas for Tier 1 and Tier 2.

*   **Tier 1: Core Decision Makers (Active)**
    *   *Examples:* Government Agencies, C-Suite Executives, Major Corporations, Central Banks.
    *   *Role:* They possess the power and resources to change the state of the world. They initiate actions.
*   **Tier 2: Key Stakeholders & Factions (Reactive/Aggregate)**
    *   *Examples:* Labor Unions, Consumer Demographics (e.g., "Gen Z EV Buyers"), Industry Coalitions.
    *   *Role:* They react to Tier 1 actions. Their collective behavior defines the market or political outcome.
*   **Tier 3: Information & Reporting Nodes (Passive/Contextual)**
    *   *Examples:* Reporters, News Outlets, Academic Journals.
    *   *Role:* They should **not** be simulated as active social media agents. They exist in the Knowledge Graph purely as provenance and context nodes that Tier 1/2 actors cite or react to.
*   **Tier 4: Abstract Concepts & Resources (Non-Agentic)**
    *   *Examples:* "Lithium", "Interest Rates", "AGI".
    *   *Role:* Structural nodes that actors possess, depend on, or regulate.

---

## 3. Generalized Entity Schema (Behavioral DNA)

For an LLM persona to behave realistically in an OASIS simulation, it needs more than a generic description. It needs a psychological and strategic profile. Regardless of the domain (geopolitics, tech markets, macroeconomics), every **Active Actor (Tier 1 & 2)** should conform to this generalized attribute schema:

### Actor Schema (Applies to Persons, Organizations, Factions)
*   **`identity`**: Name and formal role (e.g., "CEO of X", "Federal Reserve").
*   **`worldview`**: Core values, ideologies, and beliefs (e.g., "Believes AI regulation stifles innovation", "Prioritizes national security over free trade").
*   **`incentives`**: What are they trying to maximize/minimize? (e.g., "Maximizing shareholder value", "Securing re-election", "Market share acquisition").
*   **`resources_and_levers`**: What power do they wield? (e.g., "Legislative veto power", "$50B cash reserves", "Cult-like retail investor following").
*   **`risk_tolerance`**: High / Medium / Low (Dictates how aggressively they act in the simulation).
*   **`simulation_tier`**: 1 (Core), 2 (Stakeholder), 3 (Info/Passive), 4 (Concept).

---

## 4. Generalized Relationship Schema

Relationships (Edges) must define the **vectors of power and alignment**. Generic edges like `RELATES_TO` or `WORKS_WITH` are too weak for simulation. The generalized ontology should use a bounded set of strategic edge types that apply universally:

### Alignment & Support
*   `ALLIED_WITH`: Mutual defense or strategic alignment.
*   `FUNDS` / `INVESTS_IN`: Financial backing (directional).
*   `SUPPORTS`: Public or political backing.

### Conflict & Opposition
*   `COMPETES_WITH`: Direct market or political competition.
*   `OPPOSES`: Ideological or strategic resistance.
*   `SANCTIONS` / `SUES`: Active hostile action.

### Dependency & Power
*   `REGULATES`: Legal or administrative control over an entity.
*   `DEPENDS_ON`: Supply chain, technological, or political reliance.
*   `SUPPLIES_TO`: Provides critical resources or components.
*   `OWNS` / `CONTROLS`: Subsidiary or direct hierarchical control.

### Passive (For Tier 3 / Tier 4)
*   `REPORTS_ON`: (Tier 3 -> Tier 1/2/4) Media coverage.
*   `CONSUMES`: (Tier 2 -> Tier 4) Market utilization.

---

## 5. Structured Output Format (JSON Schema)

When the deep-research phase completes, the system should prompt the LLM to extract the ontology using strict JSON Schema (e.g., via OpenAI Structured Outputs or Claude tool use). This guarantees the data maps perfectly into the Graphiti/OASIS pipeline.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DeepAgentForecast Ontology",
  "type": "object",
  "properties": {
    "domain_context": {
      "type": "string",
      "description": "A brief summary of the overarching domain/event."
    },
    "entities": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string", "description": "Unique identifier, e.g., 'org_openai'" },
          "name": { "type": "string" },
          "entity_type": { 
            "type": "string", 
            "enum": ["Person", "Organization", "GovernmentBody", "MarketFaction", "InformationSource", "AbstractConcept"] 
          },
          "simulation_tier": {
            "type": "integer",
            "enum": [1, 2, 3, 4],
            "description": "1: Core Actor, 2: Stakeholder, 3: Passive Source, 4: Concept"
          },
          "behavioral_dna": {
            "type": "object",
            "description": "Required for Tier 1 and 2. Omit for Tier 3 and 4.",
            "properties": {
              "role_description": { "type": "string" },
              "worldview_and_beliefs": { "type": "string" },
              "incentives_and_goals": { "type": "string" },
              "resources_and_levers": { "type": "string" },
              "risk_tolerance": { "type": "string", "enum": ["Low", "Medium", "High"] }
            }
          }
        },
        "required": ["id", "name", "entity_type", "simulation_tier"]
      }
    },
    "relationships": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "source_entity_id": { "type": "string" },
          "target_entity_id": { "type": "string" },
          "relation_type": {
            "type": "string",
            "enum": [
              "ALLIED_WITH", "FUNDS", "SUPPORTS", 
              "COMPETES_WITH", "OPPOSES", "SANCTIONS", 
              "REGULATES", "DEPENDS_ON", "SUPPLIES_TO", "OWNS", 
              "REPORTS_ON", "CONSUMES"
            ]
          },
          "context": {
            "type": "string",
            "description": "Specific qualitative context of this relation (e.g., 'Apple depends on TSMC for 3nm node fabrication')."
          }
        },
        "required": ["source_entity_id", "target_entity_id", "relation_type", "context"]
      }
    }
  },
  "required": ["domain_context", "entities", "relationships"]
}
```

---

## 6. Integration into the Workflow

1.  **Extraction Phase (DeerFlow):** Instead of standard entity extraction, use the JSON schema above as the required output format for the final synthesis pass. Instruct the LLM specifically to filter out journalists and news outlets into `Tier 3` and abstract concepts into `Tier 4`.
2.  **Graphiti Ingestion:** 
    *   Nodes are created for all entities. 
    *   The `behavioral_dna` object is stringified and stored as node attributes, ensuring they are retrievable by GraphRAG.
3.  **Simulation Filter (OASIS Profile Generation):**
    *   MiroFish scans the graph but **only selects nodes where `simulation_tier` is 1 or 2**.
    *   The `OasisProfileGenerator` takes the `behavioral_dna` fields (incentives, worldview) and the targeted `relationships` to craft the agent's system prompt. 
    *   For example: *"You are [Name]. Your core incentive is [incentives]. You deeply believe [worldview]. You have the power to [resources]. You strongly oppose [Opposing Entity] and depend on [Dependency Entity]. Act accordingly."*

### Conclusion
By enforcing a structured, tiered ontology with deep psychological attributes and polarized relationships, the simulation shifts from a noisy, unfocused social media chatterbox into a highly strategic, incentive-driven wargame. Minor actors act solely as graph context, preserving LLM context windows and compute for the actors who actually shape the forecast.
