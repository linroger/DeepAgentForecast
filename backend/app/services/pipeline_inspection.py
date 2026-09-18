"""Read-only artifact observations for the advisory reuse planner.

Legacy output registrations prove neither upstream freshness nor domain quality.
This adapter deliberately creates no input receipt and never authorizes execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence

from .pipeline_contracts import STAGES, StageObservation
from .pipeline_reuse import plan_reuse


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def _contained(path: Path, roots: tuple[Path, ...]) -> Path:
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError("artifact_outside_allowed_root")
    return resolved


@contextmanager
def _open_regular(path: Path, roots: tuple[Path, ...]):
    """Pin every directory and the file before reading, rejecting replacements.

    Resolve legitimate in-root aliases once, then walk the canonical path using
    directory descriptors. O_NOFOLLOW protects every component, including a
    directory or final file replaced after containment validation. O_NONBLOCK
    lets fstat reject a replacement FIFO without first blocking on its open.
    """
    resolved = _contained(path, roots)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("descriptor_reads_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(resolved.anchor, directory_flags)
    file_fd = None
    try:
        for part in resolved.parts[1:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            resolved.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("artifact_not_regular")
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = None  # handle owns the descriptor from here.
            yield handle
            after = os.fstat(handle.fileno())
            entry = os.stat(resolved.name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(entry.st_mode)
            ):
                raise ValueError("artifact_changed_during_inspection")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def read_observation_json(path: str, allowed_roots: Sequence[str]) -> dict | None:
    """Read one bounded JSON object without pathname-reopening validated data."""
    target = Path(path)
    roots = tuple(Path(root).resolve() for root in allowed_roots)
    if not roots:
        raise ValueError("allowed artifact roots are required")
    try:
        with _open_regular(target, roots) as handle:
            raw = handle.read(_MAX_MANIFEST_BYTES + 1)
    except FileNotFoundError:
        if target.is_symlink():
            raise ValueError("observation_link_target_missing") from None
        return None
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("observation_too_large")
    data = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(data, dict):
        raise ValueError("observation_must_be_object")
    return data


def _manifest(path: Path, roots: tuple[Path, ...]) -> tuple[dict, str | None]:
    try:
        data = read_observation_json(str(path), [str(root) for root in roots])
        return data if data is not None else {}, None
    except (OSError, RuntimeError, ValueError, TypeError):
        return {}, "artifact_manifest_invalid"


def _artifact(entry: object, candidates: Sequence[str], roots: tuple[Path, ...], stage: str) -> tuple[str, str]:
    if not isinstance(entry, dict) or entry.get("stage", stage) != stage:
        return "invalid", "registration_invalid"
    try:
        expected = {_contained(Path(path), roots) for path in candidates}
        declared = entry.get("path")
        if declared is not None:
            if not isinstance(declared, str) or not declared:
                return "invalid", "registration_path_invalid"
            actual = _contained(Path(declared), roots)
            if actual not in expected:
                return "invalid", "registration_owner_or_attempt_mismatch"
            paths = [actual]
        else:
            paths = sorted(expected)
    except (OSError, RuntimeError, ValueError, TypeError):
        return "invalid", "artifact_path_invalid"

    wanted_bytes = entry.get("bytes")
    wanted_sha = entry.get("sha256")
    if wanted_bytes is not None and (type(wanted_bytes) is not int or wanted_bytes < 0):
        return "invalid", "registration_size_invalid"
    if wanted_sha not in (None, "") and (
        not isinstance(wanted_sha, str) or not _SHA256.fullmatch(wanted_sha)
    ):
        return "invalid", "registration_digest_invalid"

    result = ("missing", "registered_artifact_missing")
    for path in paths:
        try:
            digest = hashlib.sha256()
            count = 0
            with _open_regular(path, roots) as handle:
                before = os.fstat(handle.fileno())
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    count += len(block)
                    if count > before.st_size:
                        return "invalid", "artifact_changed_during_inspection"
                    digest.update(block)
            if wanted_bytes is not None and wanted_bytes != count:
                result = ("invalid", "artifact_size_mismatch")
            elif wanted_sha and wanted_sha != digest.hexdigest():
                result = ("invalid", "artifact_digest_mismatch")
            elif wanted_sha:
                return "verified", "registered_bytes_match"
            else:
                return "unverified", "legacy_registration_without_digest"
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError, ValueError):
            result = ("invalid", "artifact_unreadable")
    return result


def inspect_pipeline_reuse(
    state: Mapping,
    *,
    manifest_path: str,
    artifact_specs: Mapping[str, Sequence[tuple[str, str]]],
    allowed_roots: Sequence[str],
    changed: Sequence[str] = (),
) -> dict:
    """Observe a snapshot without mutating state, artifacts, or registrations.

    The current legacy coordinator has no complete input-generation receipts.
    Even matching registered output bytes therefore remain legacy-unverified.
    The pure planner separately supports validated input snapshots for subsequent
    migration slices; this reader must never manufacture one from current bytes.
    """
    roots = tuple(Path(root).resolve() for root in allowed_roots)
    if not roots:
        raise ValueError("allowed artifact roots are required")
    mode = state.get("mode", "full")
    if mode not in ("full", "research_only"):
        raise ValueError("unsupported pipeline mode")
    selected = STAGES if mode == "full" else STAGES[:1]
    manifest, manifest_error = _manifest(Path(manifest_path), roots)
    stages = state.get("stages") or {}
    if not isinstance(stages, dict):
        raise ValueError("pipeline stages must be an object")
    observations = {}
    diagnostics = {}
    for stage in selected:
        info = stages.get(stage) or {}
        if not isinstance(info, dict):
            raise ValueError("pipeline stage must be an object")
        grouped: dict[str, list[str]] = {}
        for name, path in artifact_specs.get(stage, ()):
            grouped.setdefault(name, []).append(path)
        checks = []
        for name, candidates in grouped.items():
            if name not in manifest or info.get("status") == "running":
                continue
            status, reason = _artifact(manifest[name], candidates, roots, stage)
            checks.append({"artifact": name, "status": status, "reason": reason})
        statuses = {row["status"] for row in checks}
        errors = tuple(f"{row['artifact']}:{row['reason']}" for row in checks if row["status"] == "invalid")
        output_status = (
            "invalid" if manifest_error or "invalid" in statuses else
            "missing" if "missing" in statuses else
            "verified" if statuses == {"verified"} else "unverified"
        )
        observations[stage] = StageObservation(
            status=info.get("status", "pending"), output_status=output_status,
            owner_id=state.get("pipeline_id"),
            errors=(manifest_error,) if manifest_error else errors,
        )
        diagnostics[stage] = checks
    result = plan_reuse(observations, changed, mode=mode)
    result["pipeline_id"] = state.get("pipeline_id")
    result["pipeline_status"] = state.get("status")
    result["observation_scope"] = {
        "input_receipts": "not_available_in_legacy_state",
        "artifact_integrity": "known_stage_artifacts_registered_in_legacy_manifest",
        "domain_schema_validation": False,
        "graph_contents_validated": False,
        "note": "Advisory snapshot only; execution must independently validate current owned inputs.",
    }
    for row in result["stages"]:
        row["artifact_checks"] = diagnostics[row["stage"]]
    return result
