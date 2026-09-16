# Graph batch timeout: source review and next repair

Status: reproduced source defect; repair not yet implemented. The relevant runtime and graph-builder files remain identical between the original checkout and the ASTRA implementation baseline used by this audit.

The public path is `GraphBuilderService.add_text_batches` → `_GraphNamespace.add_batch` → `GraphitiRuntime.add_episodes_concurrent` → `run` → `_add_episodes_concurrent`. With concurrency enabled, one synchronous operation deadline covers the entire fan-out, queueing and bounded replay. The runtime's `run` cancels its cross-thread Future on timeout to release the graph write lock, then raises without a partial receipt.

Inside `_add_episodes_concurrent`, UUIDs are collected only after `asyncio.gather` returns. An episode can acknowledge a successful commit while its sibling is still running. If the whole operation times out, the acknowledged UUID does not reach the caller. GraphBuilder then counts every batch member failed. It can raise its all-text-failed guard even though a text episode was committed, or overstate the number of unprocessed chunks.

Source anchors:

- [Synchronous deadline and cancellation](../../backend/app/services/graphiti_client/runtime.py#L224)
- [Concurrent ingestion and gather](../../backend/app/services/graphiti_client/runtime.py#L1071)
- [Public compatibility namespace](../../backend/app/services/graphiti_client/client.py#L153)
- [Whole-batch failure accounting](../../backend/app/services/graph_builder.py#L2288)

The current GraphBuilder does **not** automatically retry a thrown batch; it records failure and continues. The earlier external reproducer explicitly repeated a batch to show possible duplicate extraction. Do not describe that repetition as an automatic retry in this caller. Likewise, historical failed/skipped-chunk counts are receipt/accounting observations, not independent proof that every corresponding database write is absent.

The smallest coherent next slice is preserving acknowledged input-index/UUID receipts across the actual synchronous timeout and through GraphBuilder accounting. Unknown writes must remain unacknowledged, and cancellation cleanup must complete before continuing graph publication; otherwise the stage should stop explicitly. Existing graph locking, bounded rate-limit replay, attempt budgets, source text/reference time and actor-seed verification must survive.

Durable process-restart reuse is a separate boundary: graph/input/ontology policy identity, persistent completion receipts, storage failure, ambiguous commit reconciliation and dependent-stage invalidation must be verified before claiming checkpointed resume. `chunk-0` repeats between batches and is not a durable identity. No performance percentage or historical savings is established by the current synthetic reproducer.

Acceptance should exercise the real public bridge/client/builder path with fake UUID-string acknowledgements and deterministic barriers; cover fast/hung/never-started inputs, cancellation during gather/cooldown/replay, empty UUIDs, exact ordering and ownership, unacknowledged committed writes, lock cleanup and the all-text-failed guard. No provider or real graph database is needed.
