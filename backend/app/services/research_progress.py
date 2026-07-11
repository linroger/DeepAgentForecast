"""Live deep-research progress estimation and bounded multi-track log tails.

The DeerFlow bridge already emits a rich append-only event stream, but its events
do not carry numeric percentages.  This module keeps the numeric estimate tied to
explicit research milestones and only uses tool/result activity for small movement
*inside* the current milestone band.  It also exposes the active outer-track logs
without reading multi-megabyte files in full on every frontend poll.
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
MAX_PROGRESS_LINES = 500
MAX_TAIL_BYTES_PER_FILE = 256 * 1024
MAX_PROGRESS_LINE_CHARS = 1200


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


def merged_research_progress_tail(handoff_dir: str, limit: int = 200) -> ProgressTail:
    """Merge the live root + ``track_N`` logs into a bounded chronological tail."""
    limit = max(1, min(MAX_PROGRESS_LINES, int(limit or 200)))
    sources: list[tuple[int, str, str]] = []
    root = os.path.join(handoff_dir, "research_progress.log")
    if os.path.isfile(root) and not os.path.islink(root):
        sources.append((0, "", root))

    try:
        track_names = sorted(
            (name for name in os.listdir(handoff_dir) if _TRACK_DIR_RE.fullmatch(name)),
            key=lambda name: int(_TRACK_DIR_RE.fullmatch(name).group(1)),
        )[:MAX_PROGRESS_TRACKS]
    except OSError:
        track_names = []
    for name in track_names:
        number = int(_TRACK_DIR_RE.fullmatch(name).group(1))
        track_dir = os.path.join(handoff_dir, name)
        path = os.path.join(track_dir, "research_progress.log")
        if (not os.path.islink(track_dir) and os.path.isdir(track_dir)
                and os.path.isfile(path) and not os.path.islink(path)):
            sources.append((number, f"track:{number}", path))

    rows: list[tuple[str, int, int, str]] = []
    truncated = False
    seen: set[tuple[int, str]] = set()
    for source_rank, label, path in sources:
        lines, source_truncated = _tail_lines(path, limit)
        truncated = truncated or source_truncated
        last_timestamp = ""
        for ordinal, line in enumerate(lines):
            dedup_key = (source_rank, line)
            if dedup_key in seen:
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
                # timestamp, so it stays immediately after that event.  If the
                # preceding line fell outside the bounded tail, place the orphan
                # first rather than last; otherwise a stable continuation could
                # permanently mask newer ISO events at the end of the response.
                sort_key = last_timestamp
            rows.append((sort_key, source_rank, ordinal, rendered))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    if len(rows) > limit:
        truncated = True
    return ProgressTail(
        lines=[row[3] for row in rows[-limit:]],
        source_count=len(sources),
        truncated=truncated,
    )
