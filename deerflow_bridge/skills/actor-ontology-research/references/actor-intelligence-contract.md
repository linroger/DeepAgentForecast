# Actor-intelligence contract

Load this reference for the per-actor deep dive, cast-wide completion pass,
structured extraction, or coverage audit. The resident skill contains the
research sequence and quality gate; this file owns the detailed
`actor-intelligence/v1` payload requirements.

## Required dimensions

Every Tier-1/2 actor must account for all 17 dimensions:

1. `identity_history`
2. `values_worldview`
3. `incentives`
4. `motivations`
5. `capabilities`
6. `constraints`
7. `operational_preferences`
8. `alliances`
9. `opponents_competitors`
10. `decision_rights_process_triggers`
11. `current_actions`
12. `future_plans`
13. `investments_capital_allocation`
14. `track_record`
15. `likely_actions`
16. `red_lines`
17. `knowledge_state`

For each dimension, provide substantive evidence or a precise gap. Do not use
generic filler to make a dimension look covered.

## Claim contract

Keep each material claim declarative and attach:

- `claim`;
- `evidence_type`: `verified_fact`, `actor_stated_claim`,
  `analyst_inference`, `contested`, or `unknown`;
- `claim_valid_at`, `horizon`, and `status` for every behavior-bearing claim;
- `confidence`;
- fetched `source_refs` plus `source_support`. Every support row MUST contain the
  source reference, an exact `supporting_quote`, its `supporting_span` (`start`
  and `end`), the producer-owned `receipt_id`, the source body's 64-character
  `content_sha256`, and `source_publication_date`;
- `dependencies`, `conditions`, and `contradictions` when applicable;
- bounded qualifiers such as project/program/product/asset, counterparty,
  geography, amount, unit, scale, action or allocation type, strategic purpose,
  basis, leverage, decision kind/authority/trigger, preference polarity, and
  capability limits. Preserve these qualifiers inside the canonical claim
  rather than flattening them into unsupported prose;
- `visibility` only from the controlled vocabulary `public`, `actor_known`,
  `known_to_actor`, `actor_internal`, `internal_to_actor`,
  `private_actor_knowledge`, `research_only`, `analyst_only`,
  `not_known_to_actor`, or `unknown`; and a strictly boolean `actor_knows` when
  the source actually establishes access.

Never turn public rhetoric into a private motive, an announced aspiration into
an approved or funded plan, analyst inference into actor knowledge, or a
modeler-visible report fact into omniscience. Truth status and information
access are separate: a contested claim may be visible to an actor only when
`actor_knows=true`, while an `analyst_inference` remains modeler context even if
it concerns the actor.

Source publication time and claim-valid time are separate clocks. Never use the
article date to fill an unknown `claim_valid_at`, and never admit a paraphrase as
an exact supporting quote. Deterministic admission resolves each reference
against a fetched source, checks the quote/span against its body or excerpt, and
checks the receipt and content hash. A model-authored receipt or hash cannot
substitute for that producer record.

## Forward-behavior evidence

Research these fields deliberately rather than treating them as a final-report
afterthought:

- current dated actions and implementation state;
- future plans separated as announced, proposed, approved, funded, underway,
  completed, or cancelled, with horizon, dependencies, conditions, and
  disconfirmers;
- capex, acquisitions, divestments, hiring, lobbying, contracts, financing, and
  other resource allocations, preserving amount/unit/scale and strategic
  purpose when sourced;
- decision authority, process, participants, information access, known
  unknowns, and conditional triggers;
- source-backed operational preferences and aversions, meaning repeated choices
  about methods, counterparties, policy/deal structures, or risk—not invented
  personality likes/dislikes;
- likely actions under the forecast's main uncertainty and actual red lines.

After the actor fan-out, run one bounded cast-wide completion pass even when
optional delegation did not run. For an unsupported dimension, make at most two
distinct source attempts and record the remaining evidence gap instead of
guessing or repeatedly searching.

## Gap-attempt contract

An unsupported dimension uses a structured gap object, not a free-form string:

```json
{
  "reason": "Specific evidence still missing",
  "attempted_queries": ["first distinct query", "second distinct query"],
  "receipt_ids": ["producer-owned fetched-source receipt IDs"],
  "result_ids": ["producer-owned search-result IDs"],
  "attempt_count": 2,
  "exhausted": true
}
```

`receipt_ids` MUST resolve to fetched-source receipts and `result_ids` MUST
resolve to actual search-result receipts produced in the current Track-B thread.
Each listed result receipt carries its normalized query and MUST hash-match one
of the exact strings in `attempted_queries`. Never invent either kind of
identifier. Critical behavior-family gaps require two distinct query/result
receipts bound to two distinct attempted queries; a fetch receipt plus one
search result, or two results from one query, is only one bounded search
attempt. Non-critical gaps require at least one attempt. `attempt_count` MUST
equal the number of distinct attempted queries.

## Relationship evidence

Every directed relationship edge uses the same epistemic and provenance fields
as an actor claim: declarative `basis`, `evidence_type`, `claim_valid_at`,
`horizon`, `status`, `confidence`, `source_refs`, exact quote/span-bound
`source_support`, dependencies, contradictions, and bounded qualifiers. Omit a
speculative or unquoted edge. Endpoint names must resolve to the retained
Tier-1/2 roster.

## Coverage ledger

End the dossier with exactly one `ACTOR_INTELLIGENCE_LEDGER_V1` HTML comment.
Its JSON object must use schema `actor-intelligence/v1`, list every Tier-1/2
actor exactly once, and include every required dimension. Each dimension cell
must be either:

- `covered` with at least one reference that resolves to a source fetched in
  the current actor-research lane, current thread, and actor-purpose turn, plus
  at least one exact quote/span/receipt/hash-bound claim; or
- `gap` with the structured bounded-attempt object above.

Every covered actor must also have a substantive dossier heading written
exactly as `### Actor: <canonical name>`. The ledger is accountability metadata,
not a substitute for profile prose.

## Runtime handoff

Structured extraction writes the canonical claim lists under
`actors[].intelligence.dimensions`, with explicit per-dimension gaps alongside
them. `actor-role/v2` receives the exact actor-specific context pack plus the
bounded relevant report slice. Research context calibrates behavior but is not
automatically actor knowledge. The runtime must preserve evidence type, dates,
status, horizon, qualifiers, gaps, and provenance without converting them into
instructions.

## Judge additions

In addition to the resident rubric, score:

- **Forward behavior coverage:** all Tier-1/2 actors have sourced actions,
  conditional plans, investments/resource allocation, preferences, decision
  process/triggers/knowledge limits, likely actions, and red lines—or precise
  gaps.
- **Cast-wide accountability:** exactly one valid source-bound ledger covers
  every Tier-1/2 actor and all 17 dimensions.

Both dimensions are critical and must score at least 4. An explicit terminal
judge FAIL makes the dossier unusable. Judge transport/parsing failure may
degrade only when the deterministic, source-bound coverage audit passes.
