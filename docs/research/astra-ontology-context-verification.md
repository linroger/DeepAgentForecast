# Full sanitation before ontology sampling and graph splitting

ASTRA-INTEGRATION-02 repairs research context transfer at the ontology and graph boundaries. The implementation baseline is `8cdd953`; the broader application integration audit remains in progress.

## Defect and correction

The ontology builder sanitized each document with the final presentation limit before combining documents for head/middle/tail sampling. A single long document therefore lost its ending before the sampler saw it. With multiple documents, the sampler saw only the already-truncated prefixes. This contradicted the intended cross-stage context contract.

The connected graph helper used a guessed sanitation limit derived from raw length and LF count. That estimate did not cover Unicode normalization or CR-only unsafe-line replacement. Independent exact-baseline reproductions show 1,046 raw characters expanding to 18,046 safe characters against a 1,238-character cap, and 7,046 raw characters expanding to 47,045 against a 7,238-character cap. Both examples lost safe tail evidence.

The [shared sanitizer](../../backend/app/services/actor_role_prompt.py#L133) now accepts explicit `max_chars=None` to retain its complete sanitized result. Its default and finite-cap behavior remain the existing policy. [Ontology prompt construction](../../backend/app/services/ontology_generator.py#L692) uses that mode before its existing sampler. [Graph preprocessing](../../backend/app/services/pipeline_orchestrator.py#L1269) uses it before its existing chunker. No raw unsanitized evidence enters either selector.

Every ontology document block remains bounded by `MAX_TEXT_LENGTH_FOR_LLM + 400`; the default base is 120,000 characters. Simulation requirement and additional-context limits are unchanged. Graph chunk size/overlap, image stripping and per-episode evidence delimiters remain unchanged. Both ontology primary and template-fallback requests retain the existing 8,192 output-token setting. No source artifacts or actor/config seals are rewritten by this repair.

## Scenario acceptance

The five previously strict-xfail ontology scenarios first reproduce as five failures and one passing short-document control with xfail suppression disabled. The repair removes all five markers. These cases cover a long report's head/middle/tail, combined dossier/report input, a split unsafe instruction near the tail, NFKC expansion and omission-marker expansion.

Two additional cases exercise actual `OntologyGenerator.generate()` with a recording fake client, including the real invalid-general-template → default-template fallback. Each captured user message contains the relevant sample markers, excludes unsafe split instructions, preserves the finite document envelope and retains request settings. Input documents remain unchanged.

Two connected graph regressions preserve safe head/tail evidence under normalization and CR-only omission expansion. Every resulting episode retains its explicit trust wrapper and stays within the configured test chunk envelope. This is local synthetic provider-boundary validation; no paid model generation or graph ingestion was executed.

## Tradeoffs and limits

This repair restores evidence rather than claiming a production speedup. Fully retained sanitized intermediate text can occupy more memory than truncated prefixes. For graph inputs that were previously truncated, correct coverage can create more episodes and therefore more downstream provider work. Existing per-request bounds remain finite; no wider total-work cap is introduced.

Head/middle/tail sampling is intentionally selective and does not make every detail in a long report visible to ontology generation. Graph chunking retains the complete sanitized corpus. Unsafe instruction removal continues to operate on full documents before selection; known sanitizer detection limits are not represented as a formal security guarantee.

The saved-run refresh finds the same 77 artifacts, 32 pipeline states and 12 selected logs. Historical pipeline creation dates are June 8–July 15, and the newest selected application event is August 18 without a recorded timezone. The long historical ontology endpoint interval overlaps restamped downstream stages, so it does not measure exclusive ontology processing or recoverable waste. No saved-run record measures the production benefit of this repair.

Ontology reuse/input freshness and dependent-stage invalidation remain ASTRA-INTEGRATION-03. Report entry-point and chat context gaps remain separate work. The complete actor feature and application audit remain open.


## Final verification and comparison

The final guarded backend suite reports **3,905 passed, 1 failed, 17 skipped and 11 xfailed** in 142.73 seconds. The sole failure is the existing drf2 scaffold checkout-path mismatch; the suite still exits with code 1. No tests were deselected and no new xfails were added. All 12 ontology/graph boundary cases pass, including the five formerly expected failures. Independent review passed 69 cases with no blocking findings. The broad run recorded 69 guarded Python processes, zero network events and no source drift. All five touched Python files pass Ruff and compilation. Frontend source was not changed in this slice; its prior September 11 unit/build evidence remains separately dated.

[Exact verification receipt](astra-ontology-context-verification.json), [paired comparison](astra-ontology-context-comparison.json), [independent review](astra-ontology-context-review.json), and [saved-run refresh](astra-ontology-run-evidence.json) retain hashes, commands and limitations.

| Synthetic scenario | Ontology markers retained before → after | Graph episodes before → after |
|---|---:|---:|
| Long report | 2/3 → 3/3 | 300 → 300 |
| Dossier plus report | 1/2 → 2/2 | 16 → 16 |
| Unicode normalization expansion | 1/2 → 2/2 | 3 → 34 |
| CR-only unsafe-line expansion | 1/2 → 2/2 | 14 → 87 |
| Split control near the tail | 1/3 → 3/3 | 9 → 9 |

Graph comparison uses a 600-character chunk size and 60-character overlap. Across seven synthetic scenarios, every selected ontology marker survives after the repair. All 56 default/finite-cap sanitizer comparisons are byte-identical, including zero and negative caps and None input. Full mode matches an ample-cap baseline in eight comparisons. Short full prompts and graph episodes are unchanged. All three captured primary/fallback request messages now retain the tail while request counts, roles, 8,192-token output cap, temperature and validated fixture outputs remain unchanged.

The three-sample local long-report prompt median is 1.55392 s before and 1.55618 s after. This is effectively unchanged at the precision justified by this small local sample; no statistical performance or production savings conclusion is drawn. The graph episode increases above are restored coverage and can cost more downstream work.
