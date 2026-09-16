# Direct research workspace and localhost entry

Verified offline on September 16 against baseline b2a2e10. The root route remains the DeepResearchForecast product; the brand and `/legacy` now lead to that workspace. Report/interaction deep links are retained. The form uses concise bilingual copy, readable typography, consistent surfaces, a six-stage guide and responsive layout. Controls have accessible labels, selected state, keyboard disclosure and visible focus; history manages modal focus, Escape and background inertness.

Readiness has explicit checking/ready/blocked/unavailable states. Failed checks do not claim success or enable a new launch; newer mode/settings checks supersede older responses. Existing launch-intent payloads, defaults, response-loss recovery and cross-tab safeguards remain intact.

The launcher and Vite proxy share a selected validated backend port. Backend startup captures that choice before dotenv imports can override it. Vite does not independently open a browser; the launcher opens once after frontend HTML, exact resolved proxy-target metadata and proxied backend health pass. This fixes both split port configuration and reuse of a frontend routed to a different healthy DRF instance. Direct backend invocation retains its prior post-dotenv behavior.

## Verification

- Final backend: **3,957 passed, one known drf2 scaffold path failure, 17 skipped and 11 existing expected failures** in 163.97 seconds. No tests were silently deselected or newly marked expected failures.143 guarded Python processes recorded zero network attempts and no source drift.
- Frontend: **131 passed**, production build succeeded in 1.83 seconds. Main entry JS is 390.44 kB / 140.34 kB gzip versus baseline 387.47 kB / 139.44 kB. This adds interface/correctness behavior; it is not a bundle-size optimization or measured speedup.
- Independent review caught the same-product/wrong-target case before delivery. The corrected implementation passed 43 launcher/entry backend cases and 5 routing Node cases with no blocking source finding.
- Browser acceptance, completed before the user requested stopping browser automation, covered root/alias/brand, Advanced keyboard access, history focus trap/restoration, pending/failed/stale readiness, retry, English/Chinese mobile overflow and launch-response/cross-tab recovery. Navigation checks made zero pipeline POSTs; recovery checks retained exact expected fake POST counts. No JavaScript page exceptions occurred. Final 712 built asset files match the browser-tested build byte-for-byte.
- Three changed Python files pass Ruff; the launcher passes shell syntax. The environment-definition drift gate passes.

[Exact receipt and source hashes](astra-workspace-verification.json) retain commands, limits and screenshot hashes. Screenshots and raw logs are in the external experience-20260916 evidence vault. The current frontend-only preview is `http://localhost:3000/`; its backend is not started, so readiness is correctly unavailable. The production backend and original checkout remain unchanged.

## Audit and remaining work

The [successful-run audit](astra-successful-runs-audit-20260916.md) covers 26 completed pipelines, 156 stage rows and 254 runtime logs. Saved runs predate the ASTRA implementation. Research dominates recorded tokens; graph has the largest well-recorded interval, but historical graph/simulation metering and recovery timing are incomplete.

The user's latest direction is source review, with browser automation stopped. Next focus is [graph timeout receipt preservation](astra-graph-receipt-review.md), followed by the independently reproduced [market/visualization defects](astra-viz-market-audit-20260916.md) and the remaining integration/accounting work. Existing original-checkout cleanup is preserved, remote publication remains blocked by the recorded authentication issue, and the existing hourly loop is ACTIVE with the new source-review constraint.
