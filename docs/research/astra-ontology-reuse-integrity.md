# Cached ontology integrity before reuse

ASTRA-INTEGRATION-03a is a prerequisite within the still-open ontology reuse work. Its baseline is `5d7b147`. It verifies existing artifact/project consistency and preserves original registration evidence. It does not bind ontology to current research inputs or implement automatic downstream rebuilding.

## Source-backed defect

The real orchestrator accepted a truthy `project.ontology` without using the registered ontology's integrity proof. A test changed the registered file after registration: the existing generic guard returned false, but `_run` completed with the ontology-restored message and replaced the manifest hash with the altered file's hash. A separate case kept the registered file intact while changing the project ontology; that inconsistent object was also accepted.

The wider source trace also reproduces stale reuse after research regeneration and after ontology regeneration. Correct automatic regeneration requires durable dependent-state invalidation, preservation of canonical derived files, ensemble checkpoint lineage and safe ownership when a fork shares its base project/handoff. Those remain 03b/03c. Resetting stage flags alone would leave report lookup, ensemble checkpoints and old graph priors eligible for reuse.

## Implemented contract

The source boundary is [the cached-ontology guard](../../backend/app/services/pipeline_orchestrator.py#L9805), with [registration preservation at stage completion](../../backend/app/services/pipeline_orchestrator.py#L9012) and [the actual reuse branch](../../backend/app/services/pipeline_orchestrator.py#L12643).

When artifact validation is enabled, the reuse guard reads the available artifact manifests strictly and reads the ontology bytes once. It checks every available registered path, size and hash against that snapshot, parses and checks the JSON shape, and compares the complete object with the project ontology using canonical JSON. Different whitespace/key ordering is acceptable when the registered bytes themselves are unchanged; a project/file semantic disagreement is rejected.

A scenario fork can keep a local manifest while reading ontology from its base handoff. The guard checks both available registrations, deduplicating identical manifest paths. A child registration cannot override invalid owner proof. Both locations are read-only.

The guard runs before RESEARCH bookkeeping, because the general loader intentionally treats a malformed manifest as empty and a later write could erase the old evidence. It checks again at the actual reuse boundary. Rejected proof stops the attempt in ONTOLOGY before regeneration or downstream stage entry. Existing project, ontology, manifest and downstream artifacts remain available as history.

Successful ontology reuse preserves its original manifest entry and provenance. Stage completion does not re-read the file to create a replacement registration. A file changed after validation therefore cannot acquire a newly trusted fingerprint, while the already-validated project object remains unchanged. The next reuse check rejects that changed file.

## Compatibility and scope limits

- Explicit `PIPELINE_VALIDATE_ARTIFACTS=false` retains the existing opt-out. Status says validation was disabled.
- Missing legacy ontology files with no registration retain project-only reuse. A present unregistered file must agree with the project.
- Legacy registration without a hash can still use available size/schema/project checks. Status records that SHA-256 was not checked.
- Every accepted mode explicitly marks input freshness as unverified. Artifact consistency alone cannot prove which research generation produced the ontology.
- An invalid cached ontology is rejected, not automatically regenerated. Safe input-bound regeneration and dependent invalidation remain the next design/implementation boundary.
- The complete application and actor feature remain open. No production provider, pipeline run, restart, deployment or saved-run mutation was used to validate this change.

## Acceptance evidence

The permanent tests exercise the real `_run` state machine with external services faked and temporary storage. They cover tampered/missing/foreign/malformed ontology, project/file disagreement, malformed manifests before RESEARCH bookkeeping, valid reuse with original provenance, late file replacement, explicit opt-out, project-only legacy reuse and missing-hash legacy registration. Five additional shared-handoff cases cover owner proof, child proof, conflicts and read-only preservation.

The first 13 cases pass after the initial implementation; the owner-proof gap then reproduces four failures and one control before correction. The final focused gate passes all 18 permanent cases plus 55 existing orchestration cases. The three modified Python files pass Ruff and compilation. The first broader gate is retained separately because it predates the owner-proof correction.

Saved evidence remains historical: 77 selected artifacts, 32 states and 12 bounded logs are unchanged, with zero log reparses. Four inspected historical ontology files and their project objects match current saved manifest/data bytes; three saved completion messages use the reuse label. None of those four entries has the inspected input-binding metadata. This does not prove that historical reuse checked integrity or freshness, nor prove or rule out past stale reuse.


## Final gate and comparison

The final backend gate reports **3,923 passed, 1 failed, 17 skipped and 11 xfailed** in 148.45 seconds. The sole failure is the documented drf2 scaffold checkout-path mismatch; the suite exits with code 1 and is not fully green. All 18 permanent cases pass. The focused gate passes 73 and independent review passes 82, with no blocking finding. The final broad run recorded 69 guarded Python processes, zero network events and no source drift; all three changed Python files pass Ruff and compilation.

[Verification receipt](astra-ontology-reuse-verification.json), [paired comparison](astra-ontology-reuse-comparison.json), [independent review](astra-ontology-reuse-review.json) and [saved evidence](astra-ontology-reuse-run-evidence.json) contain hashes, commands and limits.

| Paired real-state-machine observation | Baseline | Current |
|---|---:|---:|
| Invalid cached cases incorrectly completed | 4 | 0 |
| Downstream stage entries across those invalid cases | 16 | 0 |
| Healthy reuse preserves original registration | No | Yes |
| Late file replacement receives new registration | Yes | No |

The baseline comparison executes the exact `5d7b147` `_run` and `_complete_stage` methods against the same offline service fixtures. Invalid scenarios are tampered ontology, project mismatch, missing registered ontology and malformed manifest. This measures correctness and bookkeeping, not avoided paid requests. A 21-check warm local guard benchmark on an 82-byte ontology has median 0.205 ms; healthy real runs perform the guard twice, and this tiny fixture does not establish production overhead.

One independent comparison harness mistake is retained in the evidence vault: copying module globals prevented service mocks from reaching the baseline function. The offline guard blocked 12 DNS attempts. That phase was interrupted and is invalid; no outbound request succeeded. Binding exact baseline code to live module globals corrected the harness, and the final comparison recorded zero network events.
