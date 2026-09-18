"""Attempt-local graph acknowledgements, never a durable replay/rollback proof."""

from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class BatchReceiptSnapshot:
    """Input-indexed observations frozen at the synchronous timeout boundary.

    An acknowledgement means the ingestion coroutine returned a nonempty UUID.
    A started input without one may already have written data. Even confirmed
    asyncio cleanup says nothing about detached executor/provider work.
    """

    graph_id: str
    input_count: int
    acknowledged: tuple[tuple[int, str], ...]
    started_unacknowledged: tuple[int, ...]
    never_started: tuple[int, ...]
    failure_reasons: tuple[tuple[int, str], ...] = ()
    runtime_accounted_reasons: tuple[tuple[int, str], ...] = ()

    def validate(self, graph_id: str, input_count: int) -> None:
        """Reject wrong-batch, malformed or overlapping receipts before counting."""
        if self.graph_id != graph_id or self.input_count != input_count:
            raise ValueError("Graph batch receipt identity/count mismatch")
        ack_indexes = []
        for index, uuid in self.acknowledged:
            if not isinstance(uuid, str) or not uuid.strip():
                raise ValueError("Graph batch acknowledgement requires a nonempty UUID string")
            ack_indexes.append(index)
        groups = (ack_indexes, self.started_unacknowledged, self.never_started)
        indexes = [index for group in groups for index in group]
        if (
            any(type(index) is not int for index in indexes)
            or len(indexes) != input_count
            or set(indexes) != set(range(input_count))
            or any(list(group) != sorted(group) for group in groups)
        ):
            raise ValueError("Graph batch receipts must partition ordered input indexes")
        reasons = dict(self.failure_reasons)
        if (
            len(reasons) != len(self.failure_reasons)
            or any(type(index) is not int for index in reasons)
            or not set(reasons).issubset(self.started_unacknowledged)
            or any(not isinstance(reason, str) or not reason for reason in reasons.values())
        ):
            raise ValueError("Graph batch failure reasons must describe unacknowledged inputs")
        accounted = dict(self.runtime_accounted_reasons)
        if (
            len(accounted) != len(self.runtime_accounted_reasons)
            or any(type(index) is not int for index in accounted)
            or any(index not in reasons or reasons[index] != reason
                   for index, reason in accounted.items())
        ):
            raise ValueError("Runtime-accounted reasons must match indexed batch failures")


class GraphBatchTimeout(TimeoutError):
    """A sync batch deadline with validated receipts and explicit cleanup state."""

    def __init__(self, snapshot: BatchReceiptSnapshot, *, cleanup_confirmed: bool):
        snapshot.validate(snapshot.graph_id, snapshot.input_count)
        self.snapshot = snapshot
        self.cleanup_confirmed = cleanup_confirmed
        super().__init__(
            "Graph batch deadline exceeded; "
            f"{len(snapshot.acknowledged)}/{snapshot.input_count} acknowledged; "
            f"async cleanup {'confirmed' if cleanup_confirmed else 'unconfirmed'}"
        )


class BatchReceiptCollector:
    """One collector per public batch call; writers and sync snapshots share a lock."""

    def __init__(self, graph_id: str, input_count: int):
        self.graph_id = graph_id
        self.input_count = input_count
        self.cleanup_complete = threading.Event()
        self._lock = threading.Lock()
        self._started: set[int] = set()
        self._acknowledged: dict[int, str] = {}
        self._failure_reasons: dict[int, str] = {}
        self._runtime_accounted_reasons: dict[int, str] = {}

    def start(self, index: int) -> None:
        with self._lock:
            self._started.add(index)
            # A replay that starts but never acknowledges has an uncertain
            # outcome; an earlier 429 is no longer its final skip reason.
            self._failure_reasons.pop(index, None)

    def acknowledge(self, index: int, uuid: object) -> None:
        # Invalid/empty return values cannot become successful receipt evidence.
        if isinstance(uuid, str) and uuid.strip():
            with self._lock:
                self._acknowledged[index] = uuid
                self._failure_reasons.pop(index, None)

    def failed(self, index: int, reason: str) -> None:
        with self._lock:
            self._failure_reasons[index] = reason

    def runtime_accounted(self, index: int, reason: str) -> None:
        """Mark a final skip only after the runtime has incremented its counter.

        This also captures returned BaseExceptions such as child CancelledError,
        which deliberately bypass the ingest wrapper's Exception handler.
        """
        with self._lock:
            self._failure_reasons[index] = reason
            self._runtime_accounted_reasons[index] = reason

    def snapshot(self) -> BatchReceiptSnapshot:
        with self._lock:
            return BatchReceiptSnapshot(
                graph_id=self.graph_id,
                input_count=self.input_count,
                acknowledged=tuple(sorted(self._acknowledged.items())),
                started_unacknowledged=tuple(sorted(self._started - self._acknowledged.keys())),
                never_started=tuple(sorted(set(range(self.input_count)) - self._started)),
                failure_reasons=tuple(sorted(self._failure_reasons.items())),
                runtime_accounted_reasons=tuple(sorted(self._runtime_accounted_reasons.items())),
            )
