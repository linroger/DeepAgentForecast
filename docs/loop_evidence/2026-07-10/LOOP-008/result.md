# LOOP-008 result — Live deep-research logs and honest progress

**Status:** Complete and replayed against the last three runs.

- Root cause 1: the progress API tailed only `handoff/research_progress.log`, while parallel research writes active logs under `handoff/track_N/research_progress.log`.
- Root cause 2: `10 + tool_calls * 4` reached 90% during the opening search burst, leaving synthesis/extraction hours visually frozen.
- Root cause 3: the frontend's fixed 400-line tail fingerprint and console auto-scroll watched only array length, so a rolling full tail looked unchanged.
- Root cause 4: status and progress were fetched as one `Promise.all`, so an optional log error discarded healthy status; slow responses from an old pipeline could also block or overwrite a newly selected run.
- Change: the API reads bounded per-file tails, chronologically merges/deduplicates active track logs, and reports honest source/truncation metadata (`total: null`, `total_exact: false`). A phase-bounded estimator advances only inside explicit research milestones; the parallel aggregate is monotonic, excludes terminal failed lanes, and is capped at 95 until merge. Frontend polling and auto-scroll use a newest-line content revision, keep status authoritative when logs fail, serialize same-generation polls, and discard stale pipeline responses. Status/progress responses are explicitly non-cacheable.
- Last-three replay: fresh opening phases never exceeded 16%; `pipe_f23527f7d903` produced 88 monotonic aggregate changes; all three aggregates stayed monotonic and at or below 95 before merge. Legacy coverage-top-up rounds map progressively through 69–76 rather than freezing.
- Performance replay: the largest recent three-track tail returned 400 ordered lines (about 80 KB) in approximately 3.0 ms average and 3.7 ms maximum across 25 local reads.
- Verification: 209 focused backend tests passed; compileall and diff-check passed; 14/14 frontend Node tests and the Vite production build (699 modules) passed. Independent final review found no critical or important issue.
- Residual proof: no paid live run was launched, so browser observation during a newly running pipeline remains the final operational confirmation.
