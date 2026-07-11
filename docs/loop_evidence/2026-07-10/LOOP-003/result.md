# LOOP-003 result — Actor-centered graph bounds

**Status:** Complete in code; live latency comparison pending.

- Reproduced problem: the latest representative graph reached 940 nodes and 10,000 edges, while 278 of 466 chunks were skipped and the browser overview remained expensive to navigate.
- Change: `graph_pruner.py` now applies a verified physical hard cap around the canonical actor cast and bounded related entities. The default retained set is 400 entities, with a 150-per-type cap and an explicit core-overflow rule so named key actors are never deleted merely to hit a cosmetic number.
- Delivery guard: the backend overview and frontend payload contract independently cap the default UI payload at 400 nodes, deduplicate IDs, prune orphan edges, and expose truncation metadata.
- Verification: focused graph-pruner, API payload, and frontend graph-payload tests pass, including 1,000-node, core-overflow, partial-delete, and failed-postcondition fixtures.
- Residual proof: a paid/live comparable run is still required to measure graph-stage wall time, final database size, and pan/zoom frame latency after pruning.
