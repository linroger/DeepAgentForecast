# ADR 0002 — Forecast, Evidence, and Publication Authority

- **Status:** Accepted
- **Date:** 2026-07-17
- **Work package:** WP0, slice 0D (`EXECPLAN_FOGLAMP.md` §8)
- **Ratifies:** `EXECPLAN_FOGLAMP.md` §4 decisions 3–8; `CODEX_FOGLAMP.md` §6.3 (Evidence plane), §6.6 (Forecast plane), §6.7 (Publication plane), §6.8 (Evaluation plane), §7.2 (`ForecastBundle v2`), §7.3 (Identity and time semantics)
- **Invariants bound by this ADR:** I-08, I-09, I-10, I-11, I-20, I-21
- **Companion document:** `docs/foglamp/current-shape-map.md` (producer→consumer map and migration dispositions)

## Context

Today the system has multiple partial sources of truth. Probabilities are authored in
report prose and re-extracted (`forecast_extractor`); research markdown is the de facto
data bus; the shared Zep graph absorbs simulation-derived writes; publication artifacts
are mutable files; and evaluation is split across `ledger.jsonl`, `resolutions.jsonl`,
scenario ledgers, and manual resolution with disconnected lifecycles. `CODEX_FOGLAMP.md`
§4 diagnoses these as the core structural defects; §6–7 propose the plane and record
model this ADR ratifies. WP1 containment (in progress) has already pinned safety
defaults; this ADR fixes the authority targets the remaining packages build toward.

## Decision

1. **Canonical identity.** Random business IDs use typed UUIDv7/ULID-style namespaces
   (case, run, attempt, source, evidence, claim, bundle, publication, target,
   revision, resolution, score). Content identity is SHA-256 over one versioned
   canonical-JSON profile (fixed Unicode normalization, number/date encoding, map
   ordering, NaN/Infinity forbidden), with cross-process golden vectors. Paths are
   locations, never business identity (I-04 context). External JSON is
   `lowerCamelCase`; internal Python is `snake_case`; every schema declares aliases
   explicitly and round-trip tests prove an internal rename cannot alter the wire
   contract. Sensitive business identity is never derived from guessable content;
   collision handling is fail-closed and audited.
2. **Evidence authority: the source/evidence/claim ledger.** Immutable source
   snapshots, normalized claims, claim-to-source spans, freshness, credibility, and
   independence clusters are the epistemic system of record (WP9). The research
   dossier and the graph are projections of this ledger. The graph is **never
   authoritative** for evidence: graph/vector/search stores are rebuildable
   projections with snapshot/watermark checks (I-10), and observed, inferred, assumed,
   simulated, forecast, and resolved records never silently merge (I-11).
3. **Forecast authority: `ForecastBundle v2`, pinned per RunSpec.** Probabilities,
   distributions, scenarios, sensitivities, and their evidence-pack lineage live in
   the bundle; prose and renderers cannot author or mutate them (I-08). Each RunSpec
   pins its forecast-authority mode. After the WP7 cutover gate, legacy
   `forecast.json` is retained only as a deterministic projection of the bundle.
4. **Publication authority: external `AuditRecord` plus `PublicationSeal`.** Audit is
   performed outside the producing pipeline stage; the seal binds the immutable
   candidate hash and the audit hash without circular mutation (I-09). Post-seal
   renderers (HTML, PDF, localized variants) are deterministic and network-free: the
   same sealed inputs yield byte-stable output, and no post-seal step may fetch,
   re-model, or reword sealed content (WP8).
5. **Evaluation authority: one transactional target/revision/resolution/score
   lifecycle.** Forecast targets, revisions, resolutions, and scores are rows in one
   transactional lifecycle (WP13), replacing the disconnected
   `ledger.jsonl`/`resolutions.jsonl`/scenario/manual paths. Every eligible target
   stays in the evaluation denominator through resolution or a recorded terminal
   reason (I-20). No production policy is promoted solely from in-sample,
   answer-bearing, or famous-outcome retrospective evaluation (I-21).
6. **Migration: shadow first, authority immutable per run.** Every authority above is
   introduced as shadow writes plus recorded comparisons against the legacy path
   before any cutover gate (§26 migration protocol). Authority modes are pinned in the
   RunSpec at admission and are immutable for the life of the run; a run never changes
   evidence, forecast, publication, or evaluation authority mid-flight.

## Consequences

- Positive: one place to ask "what is the probability and why" (bundle + evidence
  pack); the graph becomes deletable/rebuildable, ending contamination-by-merge;
  published reports become verifiable against a seal; calibration gains a complete
  denominator; identity/time semantics (§7.3) make as-of backtests enforceable.
- Negative / accepted costs: dual-writing ledgers, bundles, and seals in shadow for
  the duration of WP6–WP13; canonical-JSON hashing demands golden vectors and injected
  clock/ID providers everywhere hashes are asserted; legacy `forecast.json` and
  existing API shapes must stay readable until the tested cutover and soak end (I-22
  context).
- Ordering consequences accepted from §6.1: a minimal `EvidenceSnapshot`/source
  identity (WP9A) exists before an authoritative `ForecastEvidencePack` (WP6); bundle
  authority (WP7) precedes any new publication (WP8); resolution/scoring authority
  (WP13B) waits for workflow cutover (WP16D).

## Alternatives considered

1. **Report prose as forecast authority (status quo) with better extraction.**
   Rejected: a prompt is not a forecast specification; extraction can only recover
   what prose happens to state, and renderers would still be able to mutate numbers
   (violates I-08).
2. **Graph as evidence authority.** Rejected: the graph is asked to play too many
   roles today and permits simulated facts to contaminate observed ones; making it
   authoritative would make I-10/I-11 unenforceable. It remains a projection.
3. **Path/filename-based identity with unversioned hashing.** Rejected: paths are
   locations (I-04); unversioned ad-hoc hashing makes stored hashes unverifiable after
   any serializer change and enables cross-context hash oracles.
4. **In-band audit (report stage audits and edits itself).** Rejected: circular
   mutation — the auditor rewriting the candidate it hashes — makes the seal
   meaningless (violates I-09).
5. **Big-bang migration without shadow comparison.** Rejected: §26 requires recorded
   parity evidence per authority; per-run immutable authority modes are the only way
   to keep mixed fleets analyzable during the transition.
6. **Multiple evaluation ledgers per record class.** Rejected: the disconnected
   lifecycle is the defect (see current-shape map row "Ledger/eval"); golden and
   characterization rows are excluded by record class inside one lifecycle instead
   (WP1 1E already redirects golden rows).
