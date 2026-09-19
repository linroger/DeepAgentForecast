"""Offline replay of the agentic research publication contract.

The caller must select this gate using program-owned engine/policy metadata.
These checks establish artifact binding, not producer authenticity. Source
retrieval receipts, actor provenance and run authority remain parent checks.
Legacy judge handling belongs to the caller and is never a fallback here.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType


_BRIDGE = Path(__file__).resolve().parents[3] / "deerflow_bridge"
_ARTIFACTS = ("meta.json", "research_report.md", "sources.json", "research_quality.json")
_MANIFEST = "research_contract_manifest.json"


def is_agentic_quality(meta) -> bool:
    """Recognize the exact policy pair; this does not authenticate metadata."""
    return (
        isinstance(meta, dict)
        and meta.get("research_engine") == "agentic-phases/v1"
        and meta.get("quality_policy") == "mechanical-with-advisory/v1"
    )


@lru_cache(maxsize=2)
def _bridge_module(filename: str) -> ModuleType:
    # The only callers use these repository-owned files, never an artifact path.
    if filename not in {"research_quality.py", "deerflow_research.py"}:
        raise ValueError("Unsupported research quality helper")
    spec = importlib.util.spec_from_file_location(
        "_drf_quality_" + Path(filename).stem, _BRIDGE / filename,
    )
    if spec is None or spec.loader is None:
        raise ImportError("Repository research quality helper unavailable")
    module = importlib.util.module_from_spec(spec)
    if filename == "deerflow_research.py":
        # Its two top-level siblings (budget and compaction) are offline-safe.
        # Backend-only launch paths cannot otherwise resolve those imports.
        # Use a distinct string object so cleanup removes only our insertion.
        bridge_path = str(_BRIDGE) + "/"
        sys.path.insert(0, bridge_path)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = [path for path in sys.path if path is not bridge_path]
    else:
        spec.loader.exec_module(module)
    return module


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def _json(raw: bytes):
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _read_artifact(root: Path, name: str, errors: list[str]) -> bytes | None:
    """Read only a fixed artifact through pinned, symlink-free descriptors.

    Never reopen a validated pathname: concurrent model/tool writers can swap
    either a final component or an ancestor. O_NONBLOCK lets fstat reject FIFOs
    before a content reader is constructed. Fingerprints and parsing consume
    this same byte snapshot, which is discarded if the file changes mid-read.
    """
    if name not in (*_ARTIFACTS, _MANIFEST):
        errors.append("research_quality_artifact_name_invalid")
        return None
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        errors.append("research_quality_descriptor_reads_unavailable")
        return None
    directory_fd = file_fd = None
    snapshot = None
    try:
        # Lexical normalization only: resolving root would follow an ancestor
        # replaced with an outside symlink before descriptor traversal begins.
        absolute_root = Path(os.path.abspath(root))
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(absolute_root.anchor, directory_flags)
        for part in absolute_root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            previous_fd = directory_fd
            directory_fd = next_fd
            # close may release the FD before reporting an error. Transfer
            # ownership first so cleanup never retries a possibly reused FD.
            os.close(previous_fd)
        file_fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            errors.append(f"research_quality_artifact_not_regular:{name}")
            return None
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = None  # The handle now owns this descriptor.
            raw = handle.read()
            after = os.fstat(handle.fileno())
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if (
                any(getattr(before, field) != getattr(after, field) for field in fields)
                or (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(entry.st_mode)
            ):
                errors.append(f"research_quality_artifact_changed:{name}")
                return None
        snapshot = raw
    except FileNotFoundError:
        errors.append(f"research_quality_artifact_missing:{name}")
    except (OSError, RuntimeError, ValueError):
        errors.append(f"research_quality_artifact_unreadable:{name}")
    finally:
        for fd in (file_fd, directory_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    # Attempt each independent cleanup, without retrying a
                    # close that may already have released its descriptor.
                    errors.append(f"research_quality_artifact_unreadable:{name}")
                    snapshot = None
    return snapshot


def _registration_errors(name: str, raw: bytes, entry) -> list[str]:
    if (
        type(entry) is not dict
        or type(entry.get("bytes")) is not int
        or entry["bytes"] < 0
        or type(entry.get("sha256")) is not str
    ):
        return [f"research_quality_manifest_entry_invalid:{name}"]
    errors = []
    if entry["bytes"] != len(raw):
        errors.append(f"research_quality_artifact_size_mismatch:{name}")
    if entry["sha256"] != hashlib.sha256(raw).hexdigest():
        errors.append(f"research_quality_artifact_sha256_mismatch:{name}")
    return errors


def _actor_errors(meta: dict, receipt: dict, report: str, sources: list) -> list[str]:
    inputs = receipt.get("inputs")
    recorded = inputs.get("actor_audit") if isinstance(inputs, dict) else None
    if "actor_dossier_coverage" not in meta:
        if isinstance(recorded, dict) and recorded.get("required") is True:
            return ["research_quality_actor_coverage_missing"]
        return []
    try:
        # Only this offline-safe module's deterministic audit is called. Do not
        # invoke its native runtime, model, provider or CLI entry points.
        bridge = _bridge_module("deerflow_research.py")
        actual = bridge.audit_global_actor_report_coverage(
            report, meta["actor_dossier_coverage"], sources,
        )
        errors = []
        if _canonical(recorded) != _canonical(actual):
            errors.append("research_quality_actor_audit_mismatch")
        if actual.get("required") is not True or actual.get("complete") is not True:
            errors.append("research_quality_actor_audit_incomplete")
        errors.extend(f"research_quality_actor_audit:{error}" for error in actual.get("errors", []))
        return errors
    except Exception as exc:
        # Malformed persisted coverage must fail this boundary, not fall back to
        # a receipt's self-declared optional audit or the legacy judge.
        return [f"research_quality_actor_audit_unavailable:{type(exc).__name__}"]


def _scenario_errors(report: str) -> list[str]:
    """Retain the bridge's deterministic guard independently of model advice.

    The bridge owns recognition of canonical scenario sections and conflicting
    restatements. Do not derive the receipt's optional frame from report tables.
    """
    try:
        conflicts = _bridge_module("deerflow_research.py").scenario_probability_conflicts(report)
    except Exception as exc:
        return [f"research_quality_scenario_check_unavailable:{type(exc).__name__}"]
    return ["research_quality_scenario_probability_conflict"] if conflicts else []


def _contract_errors(root: str, entries: dict, report: str | None) -> list[str]:
    errors: list[str] = []
    root_path = Path(root)
    if type(entries) is not dict:
        errors.append("research_quality_manifest_files_invalid")
        entries = {}
    artifacts = {}
    for name in _ARTIFACTS:
        if name not in entries:
            errors.append(f"research_quality_artifact_unregistered:{name}")
        raw = _read_artifact(root_path, name, errors)
        if raw is None:
            continue
        if name in entries:
            errors.extend(_registration_errors(name, raw, entries[name]))
        try:
            artifacts[name] = raw.decode("utf-8") if name.endswith(".md") else _json(raw)
        except (UnicodeError, ValueError, RecursionError):
            errors.append(f"research_quality_artifact_malformed:{name}")

    meta = artifacts.get("meta.json")
    if not is_agentic_quality(meta):
        errors.append("research_quality_policy_identity_invalid")
    actual_report = artifacts.get("research_report.md")
    if report is not None and (type(report) is not str or report != actual_report):
        errors.append("research_quality_report_input_mismatch")
    if type(actual_report) is str:
        errors.extend(_scenario_errors(actual_report))
    sources = artifacts.get("sources.json")
    if type(sources) is not list:
        errors.append("research_quality_sources_not_list")
    receipt = artifacts.get("research_quality.json")
    if type(receipt) is not dict:
        errors.append("research_quality_receipt_invalid")

    if type(actual_report) is str and type(sources) is list and type(receipt) is dict:
        try:
            errors.extend(_bridge_module("research_quality.py").validate_receipt(
                receipt, actual_report, sources,
            ))
        except Exception as exc:
            errors.append(f"research_quality_replay_unavailable:{type(exc).__name__}")
        if isinstance(meta, dict):
            errors.extend(_actor_errors(meta, receipt, actual_report, sources))
    return list(dict.fromkeys(errors))


def quality_contract_errors(root: str, entries: dict, report: str) -> list[str]:
    """Validate registered exact artifacts and replay all mechanical checks.

    ``entries`` is the existing manifest's ``files`` mapping. Report text must
    equal the complete UTF-8 artifact, including annexes and original newlines.
    This modern-only entry point rejects missing/wrong policy identity.
    """
    errors = _contract_errors(root, entries, report)
    if type(report) is not str:
        errors.append("research_quality_report_input_mismatch")
    return list(dict.fromkeys(errors))


def quality_errors(root: str) -> list[str]:
    """Replay from disk, including registration of every quality input.

    This reads the version-1 research manifest directly, without importing the
    orchestrator or constructing application/provider state. It validates the
    four quality artifacts; the parent still validates the remaining contract.
    """
    errors: list[str] = []
    raw = _read_artifact(Path(root), _MANIFEST, errors)
    if raw is None:
        return errors
    try:
        manifest = _json(raw)
    except (UnicodeError, ValueError, RecursionError):
        return ["research_quality_manifest_malformed"]
    if type(manifest) is not dict or type(manifest.get("version")) is not int or manifest["version"] != 1:
        return ["research_quality_manifest_version_invalid"]
    return _contract_errors(root, manifest.get("files"), None)
