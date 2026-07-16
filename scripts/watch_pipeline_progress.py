#!/usr/bin/env python3
"""Stream concise pipeline-stage transitions from durable state files."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any


ACTIVE_PIPELINE_STATES = {"pending", "running", "processing", "resuming"}
TERMINAL_PIPELINE_STATES = {"completed", "failed", "cancelled"}
STAGE_ORDER = ("research", "ontology", "graph", "prepare", "run", "report")
STAGE_LABELS = {
    "research": "RESEARCH",
    "ontology": "ONTOLOGY",
    "graph": "GRAPH",
    "prepare": "PREPARE",
    "run": "SIMULATION",
    "report": "REPORT",
}
STATUS_MARKS = {
    "pending": "○",
    "running": "▶",
    "processing": "▶",
    "resuming": "↻",
    "completed": "✓",
    "failed": "✕",
    "cancelled": "■",
    "skipped": "↷",
}


def _clean(value: Any, limit: int = 220) -> str:
    return " ".join(str(value or "").split())[:limit]


def _progress_bucket(value: Any) -> int | None:
    try:
        progress = max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return None
    if progress in {0, 100}:
        return progress
    return (progress // 5) * 5


def _stage_signature(stage: Any) -> tuple[str, int | None, str]:
    row = stage if isinstance(stage, dict) else {}
    status = _clean(row.get("status") or "pending").lower()
    progress = _progress_bucket(row.get("progress"))
    error = _clean(row.get("error"))
    return status, progress, error


def _ordered_stage_names(stages: dict[str, Any]) -> list[str]:
    extras = sorted(name for name in stages if name not in STAGE_ORDER)
    return [name for name in STAGE_ORDER if name in stages] + extras


def _format_stage(pipeline_id: str, name: str, stage: dict[str, Any]) -> str:
    status, progress, error = _stage_signature(stage)
    mark = STATUS_MARKS.get(status, "•")
    label = STAGE_LABELS.get(name, name.upper())
    progress_text = f" {progress}%" if progress is not None else ""
    detail = error or _clean(stage.get("message"))
    detail_text = f" — {detail}" if detail else ""
    return f"[workflow {pipeline_id}] {mark} {label}{progress_text}{detail_text}"


def progress_events(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    pipeline_id: str,
) -> list[str]:
    """Return only meaningful stage/status changes for one durable state update."""
    events: list[str] = []
    current_status = _clean(current.get("status") or "pending").lower()
    previous_status = (
        _clean(previous.get("status") or "pending").lower()
        if isinstance(previous, dict)
        else None
    )
    current_stages = current.get("stages")
    current_stages = current_stages if isinstance(current_stages, dict) else {}
    previous_stages = previous.get("stages") if isinstance(previous, dict) else {}
    previous_stages = previous_stages if isinstance(previous_stages, dict) else {}

    if previous is None:
        events.append(f"[workflow {pipeline_id}] ◆ PIPELINE {current_status}")

    for name in _ordered_stage_names(current_stages):
        stage = current_stages.get(name)
        stage = stage if isinstance(stage, dict) else {}
        signature = _stage_signature(stage)
        old_signature = _stage_signature(previous_stages.get(name))
        status = signature[0]
        if previous is None:
            if status != "pending":
                events.append(_format_stage(pipeline_id, name, stage))
        elif signature != old_signature:
            events.append(_format_stage(pipeline_id, name, stage))

    if previous_status != current_status and current_status in TERMINAL_PIPELINE_STATES:
        mark = STATUS_MARKS.get(current_status, "•")
        error = _clean(current.get("error"))
        detail = f" — {error}" if error else ""
        events.append(
            f"[workflow {pipeline_id}] {mark} PIPELINE {current_status}{detail}"
        )
    return events


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class PipelineProgressWatcher:
    def __init__(self, pipelines_dir: Path) -> None:
        self.pipelines_dir = pipelines_dir
        self.states: dict[str, dict[str, Any]] = {}

    def poll(self, *, initial: bool = False) -> list[str]:
        events: list[str] = []
        paths = sorted(self.pipelines_dir.glob("*/pipeline_state.json"))
        live_ids: set[str] = set()
        for path in paths:
            payload = _read_state(path)
            if payload is None:
                continue
            pipeline_id = _clean(payload.get("pipeline_id") or path.parent.name, 100)
            live_ids.add(pipeline_id)
            previous = self.states.get(pipeline_id)
            status = _clean(payload.get("status") or "pending").lower()
            if previous is None and initial and status not in ACTIVE_PIPELINE_STATES:
                self.states[pipeline_id] = payload
                continue
            events.extend(progress_events(previous, payload, pipeline_id))
            self.states[pipeline_id] = payload
        for removed in set(self.states) - live_ids:
            self.states.pop(removed, None)
        return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pipelines-dir",
        type=Path,
        required=True,
        help="Directory containing <pipeline-id>/pipeline_state.json files",
    )
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    watcher = PipelineProgressWatcher(args.pipelines_dir)
    stop = False

    def _stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    for event in watcher.poll(initial=not args.once):
        print(event, flush=True)
    if args.once:
        return 0
    while not stop:
        time.sleep(max(0.2, args.interval))
        for event in watcher.poll():
            print(event, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
