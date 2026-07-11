# LOOP-007 result — Bounded research and compact handoff

**Status:** Complete in the control plane; live cost delta pending.

- A shared SQLite ledger coordinates all outer tracks and descendant search/fetch tools across processes.
- Defaults: 1,800 total attempts, 900 global searches / 360 per lane, 450 global fetches / 180 per lane, ten-minute negative-result TTL, and one retry after expiry.
- Cache hits still count as attempts but do not count as network calls; denials and degradation are persisted in `research_budget.json` and reduce forecast confidence rather than disappearing.
- The merged actor dossier is rendered from canonical `actors.json`, capped at 20 key actors and 80,000 characters, and restricts relationships to the selected cast plus bounded one-hop neighbors. Raw per-track dossiers remain available for audit; fallback selects one best dossier rather than concatenating all tracks.
- Focused budget tests passed (97), including shared multiprocess race checks; combined research merge/compaction/budget gates passed.
- Residual proof: a budget denial returns to the tool loop but does not forcibly terminate the entire research stream at that exact call; the stream should synthesize from evidence already gathered.
