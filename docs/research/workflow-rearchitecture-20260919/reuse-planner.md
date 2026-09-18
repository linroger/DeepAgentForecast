# Read-only stage reuse diagnostics

RX-01 adds a pure dependency planner and a read-only adapter for existing saved pipelines. It does not change the coordinator's execution, resume, fork, artifact registration, or regeneration behavior. RX-02/RX-03 will bind real producer input receipts and apply owned invalidation in separate verified changes.

## Use

When running this branch, the research API exposes:

```http
GET /api/research/<pipeline_id>/reuse-plan
GET /api/research/<pipeline_id>/reuse-plan?changed=graph
GET /api/research/<pipeline_id>/reuse-plan?changed=report
GET /api/research/<pipeline_id>/reuse-plan?changed=ontology,prepare
```

Use an existing pipeline ID. No endpoint starts or resumes work. `changed` describes a hypothetical replacement/change at the selected stages. Unknown or empty stage names return 400; a missing pipeline returns 404; unsupported state schema or inconsistent stored identity returns 409.

The response retains the existing `{success, data}` API envelope. `data` includes `schema: pipeline-reuse-plan/v1`, `advisory: true`, `execution_authorized: false`, canonical `changed_stages`, `affected_stages`, and one ordered diagnostic per selected stage. Each stage includes its decision, stable reason codes, upstream causes, and any registered-artifact byte checks. The question and file bodies are not included.

| Decision | Meaning |
|---|---|
| `reuse` | The pure planner received matching complete input observations, matching owner identity, completed status and verified outputs. This is conditional on those supplied observations; it is not authenticity proof or permission to execute. |
| `legacy_unverified` | Input freshness has not been established. Matching registered output bytes alone cannot upgrade this status. |
| `rebuild` | Work is missing/incomplete, inputs changed, or a proposed stage change reaches this stage through dependencies. |
| `reject` | An integrity/ownership error, active stage, incomplete modern proof, or rejected dependency prevents accepting the current generation. |

The initial saved-state adapter deliberately does **not** produce `reuse`. Existing state lacks complete stage input-generation receipts, so it supplies unbound observations and reports `legacy_unverified` for healthy completed legacy stages. It does not create a receipt from the current files or silently update hashes.

## Examples

For a healthy completed legacy run, a graph change selects `graph → prepare → run → report`. Research and ontology remain `legacy_unverified`. A report-only change selects only REPORT. A preparation change in a scenario fork selects preparation and descendants while reading shared base artifacts without rewriting the base.

A missing registered ontology file selects ontology and descendants for rebuilding. A mismatched digest, wrong-owner/attempt path, corrupt manifest or unsafe path rejects the affected chain. Rejection dominates a requested change; requesting a change does not bless invalid current evidence. The later execution adapter must apply its own validated repair/regeneration policy.

Research-only pipelines select RESEARCH alone. Active and unselected stages' artifacts are not opened. Running-stage diagnostics reject reuse and do not stop the active owner.

## Source and boundaries

- `backend/app/services/pipeline_contracts.py` defines the ordered stage graph and copies/freezes caller input mappings in `StageObservation`.
- `backend/app/services/pipeline_reuse.py` computes deterministic dependency decisions with no filesystem, clock, network, provider, thread or process operations.
- `backend/app/services/pipeline_inspection.py` reads known stage artifacts named by the existing registration manifest. It walks canonical paths through directory descriptors with no-follow flags, verifies regular-file descriptors before reading, and checks allowed roots, current candidate paths, sizes and digests; it rejects malformed/ambiguous manifests, nonregular files and observed changes during inspection.
- `backend/app/api/research.py` exposes the GET route and preserves normal missing/schema-conflict response semantics.

The observation scope is explicitly limited. It does not validate graph database contents, actor/domain schemas, producer authenticity, all unregistered outputs, or complete upstream freshness. It observes files at a point in time; execution must revalidate current inputs and ownership. The result never authorizes automatic reuse, deletion, invalidation or regeneration.

Legacy source roots and scenario handoffs remain owned by the current coordinator. The new reader validates registered paths against its expected stage candidates; it does not follow arbitrary paths from an artifact manifest. It returns reason codes instead of raw filesystem errors or file contents.

## Acceptance

The focused suite covers complete modern fixture observations, immutable inputs, every stage's dependency closure, legacy uncertainty, malformed modern observations, owner changes, active stages, missing/corrupt outputs, mode filtering, deterministic result order and zero execution. API scenarios use real temporary state and registration files, a Flask test client, forbidden lifecycle/write methods, and before/after byte snapshots.

Additional API checks cover fork/base preservation, report-only changes, existing launch/status compatibility, external symlinks, duplicate-key and broken-link manifests, nonregular files, malformed state versions and identity mismatch. Red/green evidence and the final independent review are retained beside the rearchitecture verification records.

Final evidence: [rx01-verification.json](rx01-verification.json), [independent review](rx01-review.md). All seven RX-01 Python files pass configured Ruff; 189 focused checks and 4,158 backend checks passed. The descriptor reader requires POSIX directory-descriptor/no-follow support and rejects unavailable secure-read support.
