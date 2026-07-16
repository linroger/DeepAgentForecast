# ADR 0001 — Workflow Authority

- **Status:** Accepted
- **Date:** 2026-07-17
- **Work package:** WP0, slice 0C (`EXECPLAN_FOGLAMP.md` §8)
- **Ratifies:** `EXECPLAN_FOGLAMP.md` §4 decision 1; `CODEX_FOGLAMP.md` §6.2 (Control plane), §6.10 (Authoritative storage)
- **Invariants bound by this ADR:** I-05, I-06, I-07

## Context

The current system has no singular workflow authority. Run advancement is owned by a
process-local, thread-based controller: `PipelineOrchestrator` holds `_threads`,
`_cancel_events`, and `_lifecycle_lock`; `PipelineManager` holds per-run `_state_locks`;
`TaskManager` is an in-memory singleton; durability is implemented as recovery around
mutable files, with heartbeat/owner-fingerprint checks compensating for the absence of
leases and fenced attempts. A process kill loses in-flight state; resume reconstructs it
from file projections. `CODEX_FOGLAMP.md` §6.2 requires exactly one of two mutually
exclusive authority models — workflow-engine authority (Temporal or equivalent) or
database authority (transactional state machine plus outbox) — and requires that both
never be authoritative for the same run.

The deployment target is a single workstation today, with a plausible future move to a
team/production deployment. The required semantics (deterministic transitions, durable
timers, idempotent commands, leases and heartbeats, explicit retry/cancellation policy,
full attempt history) matter more than the product choice.

## Decision

1. **Authority model: transactional database state machine plus outbox.** One
   transactional metadata/event store owns workflow history: cases, immutable RunSpecs,
   commands, task/attempt states, leases, timers, events, budgets, review holds, and
   lineage pointers. Workers consume at-least-once outbox deliveries and commit effects
   idempotently. The AI planner may recommend transitions but is never the authority; a
   schema-validated policy layer admits or rejects the transition, and the state machine
   records that decision exactly once.
2. **Workstation implementation: SQLite in WAL mode.** Repository ports are designed to
   be PostgreSQL-compatible so a team deployment can substitute PostgreSQL without
   changing domain APIs or schemas. This portability is a **design goal, not a verified
   capability**: it is not claimed as delivered until an adapter conformance suite runs
   the same repository contract tests against both engines.
3. **No Temporal now.** No external workflow engine is introduced. If a future ADR
   selects engine authority, it supersedes this one and must migrate whole runs, never
   share advancement.
4. **Never dual advancement (I-07).** A workflow engine and the database state machine
   must never both advance the same run. Workflow authority is singular and pinned for
   the life of a run.
5. **Legacy controller remains authority until WP16D cutover.** The thread-based
   controller keeps advancing existing and new runs until the WP16A–16C shadow store,
   worker/activity extraction, and the WP16D cutover gate pass. From the moment RunSpec
   exists (WP4), every new run pins `workflowAuthority` in its immutable RunSpec
   (`legacy` until cutover, `database` after); resume honors the pin. No in-flight
   legacy run is ever converted; legacy never resumes a database-authority run.

### Decision-level semantics

These are ratified at the level of decision; schemas and code land in WP3/WP4/WP16.

- **Authority:** the database transaction that commits a state transition is the sole
  advancement event. File state, caches, and API projections are read models and never
  decide the next transition.
- **Transactions:** a transition, its emitted events, and its outbox rows commit
  atomically in one transaction. Business effects plus their outbox publication are
  never split.
- **Timers:** schedules, deadlines, and retry backoff are durable rows in the store,
  fired by workers polling/leasing them — never in-process `threading.Timer` state.
  Timers survive process death.
- **Attempts (I-05):** one logical task has at most one active leased attempt.
  Attempts carry monotonically increasing fencing tokens; a stale token cannot commit.
  Heartbeats renew leases; lease expiry makes the attempt eligible for supersession,
  and full attempt history is retained.
- **Commands (I-06):** start, resume, fork, cancel, publish, edit, and resolve are
  idempotent commands with client-supplied idempotency keys; duplicate admission
  returns the original result. Exactly-once business effect is not claimed until the
  WP16A command-uniqueness/outbox gates pass.
- **Cancellation:** cancel is a command that transitions the task graph to a
  cancelling state; workers observe it at heartbeat/checkpoint boundaries and confirm
  a terminal cancelled state. Cancellation is recorded, never inferred from a dead
  thread.
- **Rollback:** forward recovery by compensating commands and superseding attempts,
  not by mutating history. Workflow history is append-only; a wrong transition is
  corrected by a new recorded transition. Operational rollback of the cutover itself
  follows WP16D: drain the new queue, keep database-authority runs on the database,
  route only newly created runs back to legacy.

## Consequences

- Positive: kill/restart at any transition becomes testable (WP16D chaos gate); resume
  stops being reconstruction; attempt history and command idempotency become auditable;
  the same schema serves workstation and team deployments.
- Negative / accepted costs: we build and maintain state-machine, lease, timer, and
  outbox mechanics that Temporal would provide off the shelf; SQLite single-writer
  behavior constrains worker concurrency on a workstation; the legacy controller and the
  shadow store coexist for the duration of WP3–WP16, which the shadow-comparison
  discipline (§26 migration protocol) must police.
- Every test, review, and handoff references I-05/I-06/I-07 by ID; disagreement with
  this ADR is a blocking ADR revision, not an implicit exception.

## Alternatives considered

1. **Temporal (workflow-engine authority).** Rejected for now: it adds a third
   authority with its own history store, an operational dependency inappropriate for a
   single-workstation deployment, and its benefits (durable timers, retries, attempt
   history) are exactly the semantics we can satisfy transactionally. Revisit only via
   a superseding ADR at team scale.
2. **Hybrid engine + database advancement.** Rejected outright: two mechanisms deciding
   the next transition is the defining failure mode §6.2 prohibits and I-07 forbids.
3. **Hardening the current thread/file controller in place.** Rejected: recovery around
   mutable files cannot provide fenced attempts, durable timers, or atomic
   transition+outbox commits; it would re-derive a database state machine informally.
4. **Immediate cutover instead of legacy-until-WP16D.** Rejected: the WP16D gate (ten
   shadow replays, duplicate-command chaos, kill-at-every-transition, restore) is the
   evidence bar for authority transfer; converting in-flight runs is prohibited.
