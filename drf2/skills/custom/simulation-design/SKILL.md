---
name: simulation-design
description: Use this skill in the DRF-2 pipeline to design the multi-agent OASIS simulation from the actor dossier and knowledge graph — dossier-grounded persona design (identity/views/incentives/relations/red-lines), the shared world brief, seed posts from researched stances, activity profiles and round mapping, scheduled events from the dated timeline, and the initial follow graph with valence-aware clustering. The output is the simulation_config.json contract consumed by the simulation engine's sim_start tool.
allowed-tools:
  - tool_search
  - read_file
  - write_file
  - ls
  - grep
  - glob
  - kg_search
  - kg_get_entities
  - kg_n_hop_subgraph
  - kg_centrality_priors
  - sim_start
  - sim_status
  - sim_results
  - sim_interview_agents
---

# Simulation Design Skill

## 1. Mission & prime directive

Turn the researched cast into a **grounded multi-agent social simulation** — not a generic role-play. Every design choice below exists to defeat the "hollow sim" failure mode, where agents with thin personas free-associate instead of behaving like the real actors.

**Prime directive: everything is dossier-grounded.** Personas, stances, seed posts, follows, and events are *distillations of researched evidence* (the actor dossier, actors.json, the knowledge graph) — never inventions. Cognitive diversity comes from the real differences between real actors, not from artificially assigned analytical schools. If the dossier does not support a stance, the persona does not have that stance.

Inputs: `actors.json` (cast + relationships + situation brief + key events + forecast inputs + hot topics), the KG (`kg_*` tools for ego networks and centrality), and the run's round budget. Output: the fields of `simulation_config.json` (§7).

## 2. Persona design (per selected agent)

Each cast-backed agent gets a structured `persona_design` object with **eight fields**, each distilled from the dossier's evidence for that specific actor:

| Field | Content | Grounding source |
|---|---|---|
| `identity` | Who it is; its role position in the system | actor role + worldview.identity |
| `views_beliefs` | Its worldview and reading of the current situation — what it believes is true | stance / beliefs / framing |
| `incentives` | Gains/losses under each plausible outcome; the objective function it optimizes | incentives (gains-if / loses-if) |
| `objectives` | Goals and its strategic path to them | goals + strategy |
| `relations` | Allies / opponents / dependencies and WHY — named counterparties | relational roster + relationships[] (valence preserved) |
| `constraints_red_lines` | Hard constraints and lines it will not cross | constraints + vulnerabilities |
| `decision_style` | How it weighs trade-offs; its action tempo | risk tolerance / track record |
| `rhetoric` | Its typical public voice and argumentative style | stated positions, quoted language |

Rules:
- **No invented stances or facts.** Every field must trace to dossier evidence; the stated-vs-revealed gap, when the dossier surfaces one, belongs in `views_beliefs`/`decision_style`.
- The persona prose (the agent's system-prompt biography) must *embody* the persona_design, and both must use the actor's **canonical name**.
- Enrich `relations` with the actor's KG ego network: retrieve 1–2 hop neighbors filtered to relationship edges (ALLY_OF, OPPOSES, COMPETES_WITH, REGULATES, DEPENDS_ON, PARTNERS_WITH, INFLUENCES) so the persona knows who to @, ally with, or oppose.
- **Agent selection is salience-ranked**: principals and arbiters before stakeholders; amplifiers sparingly; never let a well-covered amplifier crowd out a pivotal principal. KG centrality (`kg_centrality_priors`) is a supporting signal, not the ranking.
- **Pool size is the main cast, hard-capped at 20 (binding).** Every persona must be an entity whose decisions and actions carry forecast signal — no filler "audience" personas by default. A 15-agent simulation of real principals beats an 80-agent simulation padded with generic bystanders: filler agents burn one LLM call per active round each while contributing zero forecast information. Media organizations, journalists, commentators, and analysts are never personas (they are graph context/sources). If the dossier cast exceeds 20, take the top 20 by salience; if fewer, do not pad.
- Depth heuristic: if the persona cannot reason three sentences about the forecast's main uncertainty *in character*, it is not grounded enough.
- Persona language follows the simulated arena (English personas for US/global-market runs, Chinese for Chinese-audience runs).

Each agent also carries flat behavioral fields: `stance` (one of `supportive` / `opposing` / `neutral` / `observer` — relative to the central question), `interested_topics` (the fault lines it cares about, used for clustering), and an influence tier consistent with the dossier's salience.

## 3. The world brief (shared reality)

Compose ONE compact world brief (≤ ~1400 chars) injected into EVERY agent's system prompt, so all agents argue inside the same reality instead of hallucinating divergent worlds. Deterministic composition, three blocks in order:

1. **The central question** (first ~400 chars of the forecast question) — what this world is arguing about.
2. **The situation brief** from the dossier: current situation · how it got here (context) · the forces in tension (dynamics) · the 3–6 fault lines actors argue over · the catalysts that would shift things.
3. **Hot topics** — the ≤8 named discussion topics for the run.

Missing blocks shorten the brief; never pad with invented context. The brief is shared ground truth — per-actor beliefs and disagreements live in the personas, not here.

## 4. Seed posts (ignition from researched stances)

The simulation opens with initial posts that put the researched disagreement on the table:

- Authors: the **top-influence / top-tier actors** (those with a researched, non-empty stance).
- Content: each author states its **researched stance verbatim-faithful** on one hot topic — "On {topic} — our position: {stance}". Do not soften, merge, or invent stances.
- Engagement: end roughly half of the seed posts with a discussion hook ("Who gains and who loses if this holds?") so replies have somewhere to go.
- Coverage: seed posts should collectively touch the main fault lines, not five posts on one topic.
- Language matches the persona language of the run.

## 5. Time, activity & scheduled events

- **One round = one simulated hour.** Choose the activity profile matching the arena:
  - `china_default` — active 08:00–23:00; peak 19–22; off-peak 0–5; morning 6–8; work 9–18.
  - `us_business` — US business rhythm.
  - `global_market` — cross-timezone: some cohort is always awake, follow-the-sun peaks.
- Per-agent `active_hours` must fit both the profile and the actor (a regulator posts in work hours; a trader around market hours; a media account across peaks).
- **Scheduled events from the dated timeline**: map the dossier's `key_events` onto rounds in `[0, total_rounds)` preserving order and relative spacing, where `total_rounds = min(configured_rounds, execution_round_budget)` — never schedule a flashpoint beyond the rounds that will actually run. Attach to each event the **most relevant high-influence publisher** (the actor whose profile best matches the event) so the event enters the feed as a post from a plausible source.
- Event text is drawn from the researched event description — dated, factual, no invented outcomes.

## 6. The follow graph (valence-aware, clustered, bridged)

The initial follow structure encodes the researched network so influence flows realistically:

1. **Relationship-derived follows**: for each researched relationship between selected agents, add follow edges consistent with its type and valence — allies/partners follow each other; competitors and opponents follow each other too (adversaries monitor each other); regulators and regulated watch each other. Valence lives in the personas' relations (an opponent-follow is not an endorsement).
2. **Echo-chamber clustering**: cluster agents by `(stance bucket, dominant interested topic)` and densify follows within clusters — real discourse has homophily; a sim where everyone follows everyone converges to mush.
3. **Cross-cluster bridges**: high-influence agents keep follow edges ACROSS clusters, so information can jump chambers the way it does through elite accounts in reality.

Never fabricate a relationship edge that the dossier/KG does not support; sparse researched networks stay sparse (the clustering step adds homophily follows, clearly derived from stance/topic, not fake alliances).

## 7. Output contract (simulation_config.json)

The design lands in the config the simulation engine's `sim_start` consumes (pass its file path as `config_path`). Populate:

- `world_brief` — §3 (omit the field entirely if empty; the runner skips injection).
- `agent_configs[]` — per agent: canonical name, persona prose, `persona_design` (§2, under `other_info.persona_design`), `stance`, `interested_topics`, influence tier, `active_hours`.
- `event_config` — `initial_posts[]` (§4), `scheduled_events[]` (§5, with round index + publisher), `hot_topics[]`.
- `time_config` — profile hours (peak/off-peak/morning/work), rounds ↔ hour mapping.
- `initial_follows[]` — §6, as [follower_index, followee_index] pairs.

When run as a pipeline stage, `simulation_config.json` is the required stage artifact (the driver gates on it); optionally also emit `agent_profiles.json` (the per-agent profile list) for the manifest.

After `sim_start` (which returns a `sim_id` immediately — the run is a detached job), poll `sim_status` (the run takes 60–120 minutes; never busy-wait in tight loops), then read `sim_results`; use `sim_interview_agents` for post-hoc structured probes of specific agents. Do not attempt to run the simulation inside your own turn — it is an external job.

## 8. Quality gate

- [ ] Every persona field traces to dossier evidence; zero invented stances/facts; canonical names everywhere?
- [ ] Cast selection salience-ranked (principals first, amplifiers capped)?
- [ ] World brief ≤1400 chars, question + situation brief + hot topics, no invention?
- [ ] Seed posts cover the main fault lines with verbatim-faithful stances; ~half carry an engagement hook?
- [ ] Scheduled events within the actual round budget, ordered, each with a plausible high-influence publisher?
- [ ] Follow graph reflects researched valence; clusters formed; high-influence bridges present?
- [ ] Activity profile matches the arena; per-agent hours plausible for the actor type?

## 9. Failure modes

- ❌ Template personas ("a thoughtful analyst who weighs both sides") — the hollow-sim signature.
- ❌ Inventing a stance, alliance, or red line the dossier does not evidence.
- ❌ Assigning artificial "analytical schools" for diversity instead of using real actor differences.
- ❌ A world brief that editorializes or resolves the fault lines instead of stating them.
- ❌ Seed posts all on one topic, or paraphrased so far they no longer match the researched stance.
- ❌ Scheduling flashpoint events past the execution round budget (they never fire).
- ❌ Fully-connected or valence-blind follow graphs; rival and ally follows treated identically in personas.
- ❌ Amplifier-heavy agent rosters that crowd out the principals who actually move the outcome.
