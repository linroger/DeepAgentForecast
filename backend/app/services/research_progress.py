"""Live deep-research progress estimation and multi-track log snapshots.

The DeerFlow bridge already emits a rich append-only event stream, but its events
do not carry numeric percentages.  This module keeps the numeric estimate tied to
explicit research milestones and only uses tool/result activity for small movement
*inside* the current milestone band.  It also exposes the active outer-track logs
without reading multi-megabyte files in full on every frontend poll.  A separate
explicit full-history snapshot is available for one-time hydration, completed-run
audits, and static publication; it never silently degrades into a tail.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Iterable


_TRACK_DIR_RE = re.compile(r"^track_(\d+)$")
_ISO_PREFIX_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\S+)\s+(.*)$")
_LEADING_EVENT_RE = re.compile(
    r"^(?:\d{4}-\d\d-\d\dT\S+\s+)?\[(?P<kind>[A-Za-z_-]+)\](?:\s|$)"
)
_ADAPTIVE_GAP_RE = re.compile(r"research:deep-adaptive-gap-(\d+)", re.I)
_COVERAGE_TOPUP_RE = re.compile(r"research:deep-coverage-topup-(\d+)", re.I)

MAX_PROGRESS_TRACKS = 16
MAX_PROGRESS_ATTEMPTS = 16
MAX_PROGRESS_LINES = 500
MAX_TAIL_BYTES_PER_FILE = 256 * 1024
MAX_PROGRESS_LINE_CHARS = 1200
MAX_FULL_PROGRESS_TOTAL_BYTES = 64 * 1024 * 1024


class ResearchProgressLimitError(ValueError):
    """Raised when an exact full-history snapshot exceeds its safety envelope."""


@dataclass(frozen=True)
class ProgressTail:
    """Bounded result returned to the progress endpoint."""

    lines: list[str]
    source_count: int
    truncated: bool


class ResearchProgressEstimator:
    """Monotonic, phase-bounded estimate for one DeerFlow research process.

    A raw tool-count heuristic reaches 90% during the opening search burst even
    though synthesis, extraction, citation repair, and visualization remain.  The
    estimator instead advances through observable bridge milestones.  Tool/result
    events provide smooth movement within the current band but asymptotically stop
    below its ceiling, so activity can never masquerade as phase completion.
    """

    _ACTIVITY_SCALE = 80.0

    def __init__(self) -> None:
        self._progress = 2
        self._phase_floor = 2
        self._phase_ceiling = 4
        self._phase_events = 0

    @property
    def progress(self) -> int:
        return self._progress

    def _advance_phase(self, floor: int, ceiling: int) -> None:
        floor = max(2, min(99, int(floor)))
        ceiling = max(floor, min(99, int(ceiling)))
        if floor > self._phase_floor:
            self._phase_floor = floor
            self._phase_ceiling = ceiling
            self._phase_events = 0
        elif floor == self._phase_floor:
            self._phase_ceiling = max(self._phase_ceiling, ceiling)
        self._progress = max(self._progress, floor)

    @staticmethod
    def _milestone(line: str) -> tuple[int, int] | None:
        text = line.lower()
        # Tool/result previews contain untrusted web and model text.  A page can
        # literally say "research complete" or mention an output filename; only
        # bridge-authored lifecycle events are allowed to cross phase boundaries.
        event = _LEADING_EVENT_RE.match(str(line or ""))
        if event is None or event.group("kind").lower() not in {
            "init", "stage", "ok", "done", "resume",
        }:
            return None

        # Terminal and post-processing milestones (most specific first).
        if "research complete" in text:
            return 99, 99
        if "research charts:" in text or "wrote charts.json" in text:
            return 98, 99
        # Track B runs concurrently and often finishes its dossier synthesis
        # while Track A is still in the opening search pass.  Its messages are
        # useful in the live console but must not impersonate the main report's
        # later synthesis/extraction milestones.
        if "actor-ontology" in text:
            return None
        if ("triangulation top-up:" in text
                and ("rewrote" in text or "adopted re-synthesized report" in text)):
            return 97, 98
        if "triangulation top-up: verifying" in text:
            return 95, 97
        if "research_quality=" in text or "wrote sources.json" in text:
            return 95, 96
        if "wrote timeline.json" in text or "wrote quantitative.json" in text:
            return 94, 95
        if "wrote actors.json" in text:
            return 93, 95
        if "extract (tool-free)" in text or "extract: starting agent turn" in text:
            return 92, 94
        if "wrote research_report.md" in text:
            return 91, 92
        if "research-report judge: pass" in text:
            return 90, 91
        if "research-report judge round" in text or "research-report refine round" in text:
            return 88, 90
        if "synthesize/multipart: produced" in text or "synthesize: produced" in text:
            return 87, 89
        if "synthesize/multipart: writing" in text:
            return 82, 87
        if "synthesize/multipart: outline parsed" in text:
            return 80, 84
        if ("synthesize/multipart: requesting" in text
                or "synthesize: writing report" in text):
            return 78, 84

        # Deep protocol milestones.
        if ("adaptive gap-closing: converged" in text
                or "adaptive gap-closing: gap set unchanged" in text
                or "adaptive gap-closing: hit pass ceiling" in text):
            return 77, 78
        gap = _ADAPTIVE_GAP_RE.search(text)
        if gap:
            number = max(1, min(6, int(gap.group(1))))
            floor = 69 + number
            return floor, min(77, floor + 2)
        coverage_topup = _COVERAGE_TOPUP_RE.search(text)
        if coverage_topup:
            # The coverage gate may run up to four long broadening turns before
            # synthesis.  Give each an explicit two-point slot in 69..76; larger
            # operator overrides safely share the final slot rather than crossing
            # the synthesis boundary.
            number = max(1, min(4, int(coverage_topup.group(1))))
            completed = "turn complete" in text
            floor = 67 + (2 * number) + (1 if completed else 0)
            return min(76, floor), min(77, floor + 1)
        if "coverage gate" in text and "/standard" not in text:
            return 69, 72
        if "research:deep-5-forecast-implications: turn complete" in text:
            return 68, 69
        if "research:deep-5-forecast-implications" in text:
            return 60, 68
        if "research:deep-parallel-phase-merge: turn complete" in text:
            return 58, 60
        if "research:deep-parallel-phase-merge" in text:
            return 53, 58
        if "deep: running" in text and "middle phases" in text:
            return 42, 53
        if "research:deep-1-scope: turn complete" in text:
            return 40, 42
        if "research:deep-1-scope" in text:
            return 34, 40
        if "research:deep-fanout-merge: turn complete" in text:
            return 32, 34
        if "research:deep-fanout-merge" in text:
            return 28, 32
        if "deep fan-out:" in text or "research:fanout:" in text:
            return 18, 32
        if "research:deep-opening: turn complete" in text:
            return 16, 18
        if "research:deep-opening" in text:
            return 8, 16
        if "deep: starting multi-pass" in text or "dual-track:" in text:
            return 6, 8

        # Quick/standard use one long research turn rather than the deep phases.
        if "coverage gate/standard" in text:
            return 68, 78
        if "research:standard-coverage-topup" in text:
            return 58, 68
        if "research: turn complete" in text:
            return 62, 68
        if "research: starting agent turn" in text:
            return 10, 62
        if "[init]" in text:
            return 4, 6
        return None

    def observe(self, line: str) -> int:
        """Consume one stdout/progress-log line and return a monotonic integer."""
        raw = str(line or "")
        milestone = self._milestone(raw)
        if milestone is not None:
            self._advance_phase(*milestone)

        if "[tool]" in raw or "[result]" in raw:
            self._phase_events += 1
            span = max(0, self._phase_ceiling - self._phase_floor)
            if span:
                # Keep one percentage point in reserve for the explicit next
                # milestone.  This is smooth under both sparse and fan-out-heavy
                # phases and cannot jump across phase boundaries.
                activity = int(span * (1.0 - math.exp(-self._phase_events / self._ACTIVITY_SCALE)))
                activity = min(max(0, span - 1), activity)
                self._progress = max(self._progress, self._phase_floor + activity)
        return self._progress


def aggregate_parallel_progress(
    track_progress: Iterable[int], *, previous: int = 2, ceiling: int = 95,
    excluded_indices: Iterable[int] = (),
) -> int:
    """Equal-weight, monotonic aggregate with room reserved for final merge.

    Each outer research angle is a real unit of work, so the arithmetic mean is
    more representative than the previous minimum. Terminal failed lanes may be
    excluded because they no longer contribute remaining wall-clock work. The
    ``previous`` guard prevents out-of-order callbacks from moving the persisted
    UI value backwards.
    """
    excluded = {int(index) for index in excluded_indices}
    values = [
        max(0, min(ceiling, int(value)))
        for index, value in enumerate(track_progress)
        if index not in excluded
    ]
    if not values:
        return max(0, min(ceiling, int(previous)))
    candidate = int(sum(values) / len(values))
    return max(int(previous), min(ceiling, candidate))


def _tail_lines(path: str, limit: int) -> tuple[list[str], bool]:
    """Read at most ``MAX_TAIL_BYTES_PER_FILE`` from the end of one regular file."""
    if os.path.islink(path) or not os.path.isfile(path):
        return [], False
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return [], False
        read_size = min(size, MAX_TAIL_BYTES_PER_FILE)
        with open(path, "rb") as handle:
            handle.seek(size - read_size)
            raw = handle.read(read_size)
        # When starting mid-file, discard the leading partial line.
        if read_size < size:
            split = raw.find(b"\n")
            raw = raw[split + 1:] if split >= 0 else b""
        decoded = raw.decode("utf-8", errors="replace").splitlines()
        trimmed = [line[:MAX_PROGRESS_LINE_CHARS] for line in decoded[-limit:]]
        return trimmed, read_size < size or len(decoded) > limit
    except OSError:
        return [], False


def _progress_sources(
    handoff_dir: str, *, require_all_tracks: bool = False,
) -> list[tuple[int, str, str]]:
    """Return trusted root/track/attempt logs in deterministic source order."""
    sources: list[tuple[int, str, str]] = []
    root = os.path.join(handoff_dir, "research_progress.log")
    if os.path.isfile(root) and not os.path.islink(root):
        sources.append((0, "", root))

    try:
        track_names = sorted(
            (name for name in os.listdir(handoff_dir) if _TRACK_DIR_RE.fullmatch(name)),
            key=lambda name: int(_TRACK_DIR_RE.fullmatch(name).group(1)),
        )
    except OSError:
        track_names = []
    if require_all_tracks and len(track_names) > MAX_PROGRESS_TRACKS:
        raise ResearchProgressLimitError(
            "research log source count exceeds the full-history safety limit "
            f"of {MAX_PROGRESS_TRACKS} tracks"
        )
    track_names = track_names[:MAX_PROGRESS_TRACKS]
    for source_order, name in enumerate(track_names, start=1):
        number = int(_TRACK_DIR_RE.fullmatch(name).group(1))
        track_dir = os.path.join(handoff_dir, name)
        path = os.path.join(track_dir, "research_progress.log")
        if (not os.path.islink(track_dir) and os.path.isdir(track_dir)
                and os.path.isfile(path) and not os.path.islink(path)):
            sources.append((source_order, f"track:{number}", path))

    attempts_dir = os.path.join(handoff_dir, "research_attempts")
    try:
        attempt_names = sorted(
            name for name in os.listdir(attempts_dir)
            if re.fullmatch(r"[A-Za-z0-9_-]+\.log", name)
        ) if os.path.isdir(attempts_dir) and not os.path.islink(attempts_dir) else []
    except OSError:
        attempt_names = []
    if require_all_tracks and len(attempt_names) > MAX_PROGRESS_ATTEMPTS:
        raise ResearchProgressLimitError(
            "research log source count exceeds the full-history safety limit "
            f"of {MAX_PROGRESS_ATTEMPTS} preserved attempts"
        )
    next_source_order = max((item[0] for item in sources), default=-1) + 1
    for name in attempt_names[:MAX_PROGRESS_ATTEMPTS]:
        path = os.path.join(attempts_dir, name)
        if os.path.isfile(path) and not os.path.islink(path):
            sources.append((next_source_order, f"attempt:{name[:-4]}", path))
            next_source_order += 1
    return sources


def _merge_progress_rows(
    batches: Iterable[tuple[int, str, list[str]]], *, deduplicate: bool,
) -> list[str]:
    """Merge root/track rows chronologically while keeping continuations adjacent."""
    rows: list[tuple[str, int, int, str]] = []
    seen: set[tuple[int, str]] = set()
    for source_order, label, lines in batches:
        last_timestamp = ""
        for ordinal, line in enumerate(lines):
            dedup_key = (source_order, line)
            if deduplicate and dedup_key in seen:
                continue
            seen.add(dedup_key)
            match = _ISO_PREFIX_RE.match(line)
            if match:
                timestamp, rest = match.groups()
                rendered = f"{timestamp} [{label}] {rest}" if label else line
                sort_key = timestamp
                last_timestamp = timestamp
            else:
                rendered = f"[{label}] {line}" if label else line
                # A multiline continuation inherits its producer's preceding
                # timestamp. If that event is outside a bounded tail, the empty
                # key puts the orphan first instead of masking newer ISO events.
                sort_key = last_timestamp
            rows.append((sort_key, source_order, ordinal, rendered))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows]


def merged_research_progress_tail(handoff_dir: str, limit: int = 200) -> ProgressTail:
    """Merge the live root + ``track_N`` logs into a bounded chronological tail."""
    limit = max(1, min(MAX_PROGRESS_LINES, int(limit or 200)))
    sources = _progress_sources(handoff_dir)
    batches: list[tuple[int, str, list[str]]] = []
    truncated = False
    for source_order, label, path in sources:
        lines, source_truncated = _tail_lines(path, limit)
        truncated = truncated or source_truncated
        batches.append((source_order, label, lines))
    merged = _merge_progress_rows(batches, deduplicate=True)
    if len(merged) > limit:
        truncated = True
    return ProgressTail(
        lines=merged[-limit:],
        source_count=len(sources),
        truncated=truncated,
    )


def merged_research_progress_full(
    handoff_dir: str, *, max_total_bytes: int = MAX_FULL_PROGRESS_TOTAL_BYTES,
) -> ProgressTail:
    """Return all recorded progress events from every trusted progress log.

    This path is intentionally separate from the recurring bounded tail. It
    preserves duplicate rows and complete persisted line contents. Progress
    events intentionally summarize large tool payloads; this is an exact snapshot
    of the recorded event stream, not a raw model transcript. If the aggregate
    input exceeds the explicit safety envelope, it raises instead of mislabeling
    a partial response as the complete recorded stream.
    """
    max_total_bytes = max(1, int(max_total_bytes))
    sources = _progress_sources(handoff_dir, require_all_tracks=True)
    batches: list[tuple[int, str, list[str]]] = []
    total_bytes = 0
    for source_order, label, path in sources:
        try:
            with open(path, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                if total_bytes + size > max_total_bytes:
                    raise ResearchProgressLimitError(
                        "research log exceeds the full-history safety limit "
                        f"of {max_total_bytes} bytes"
                    )
                raw = handle.read(size)
                if len(raw) != size:
                    raise RuntimeError(
                        f"research log changed during full-history snapshot: {path}"
                    )
        except ResearchProgressLimitError:
            raise
        except OSError as exc:
            raise RuntimeError(
                f"research log changed during full-history snapshot: {path}"
            ) from exc
        total_bytes += len(raw)
        lines = raw.decode("utf-8", errors="replace").splitlines()
        batches.append((source_order, label, lines))

    return ProgressTail(
        lines=_merge_progress_rows(batches, deduplicate=False),
        source_count=len(batches),
        truncated=False,
    )
