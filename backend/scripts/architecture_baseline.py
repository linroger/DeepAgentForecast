#!/usr/bin/env python3
"""Foglamp WP0A fixture baseline CLI.

Subcommands:
  capture --source-root <abs> --fixture-root <path> --manifest <path>
  verify  --manifest <path> [--fixture-root <path>]
  compare --manifest <path> --candidate-root <path>

Stdlib only. Deterministic JSON output (sorted keys, sorted file lists,
ensure_ascii=False, indent=2). Timestamps appear in the manifest only when
FOGLAMP_FAKE_NOW (ISO-8601) is set; otherwise they are omitted entirely.

Exit codes:
  0 success / identical
  1 verify or compare mismatch
  2 invalid invocation (relative or symlinked source root, bad paths)
  3 source tree changed between pre-read and post-read inventory
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "baseline-manifest/v1"

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"),
)


def _emit(document: object) -> None:
    json.dump(document, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.write("\n")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_regular_files(root: Path) -> list[str]:
    """Return sorted relative POSIX paths of regular (non-symlink) files under root."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            found.append(full.relative_to(root).as_posix())
    found.sort()
    return found


def _inventory_hash(root: Path, relative_paths: list[str]) -> str:
    """Hash sorted relative paths + sizes + mtimes so mid-capture mutation is detected."""
    digest = hashlib.sha256()
    for rel in relative_paths:
        stat = (root / rel).stat()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\x00")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _scan_for_secrets(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    return sorted({pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)})


def cmd_capture(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    if not source_root.is_absolute():
        _emit({"error": "source-root must be an absolute path", "exitCode": 2})
        return 2
    if source_root.is_symlink():
        _emit({"error": "source-root must not be a symlink", "exitCode": 2})
        return 2
    if not source_root.is_dir():
        _emit({"error": "source-root must be an existing directory", "exitCode": 2})
        return 2

    fixture_root = Path(args.fixture_root).resolve()
    manifest_path = Path(args.manifest).resolve()
    resolved_source = source_root.resolve()
    if fixture_root == resolved_source or resolved_source in fixture_root.parents:
        _emit({"error": "fixture-root must not be inside source-root", "exitCode": 2})
        return 2

    relative_paths = _walk_regular_files(source_root)
    pre_inventory = _inventory_hash(source_root, relative_paths)

    entries: list[dict[str, object]] = []
    quarantined: list[str] = []
    fixture_root.mkdir(parents=True, exist_ok=True)

    for rel in relative_paths:
        source_file = source_root / rel
        data = source_file.read_bytes()
        source_sha = _sha256_bytes(data)
        secret_hits = _scan_for_secrets(data)
        if secret_hits:
            quarantined.append(rel)
            entries.append(
                {
                    "fixtureSha256": None,
                    "lifecycleState": "quarantined_secret",
                    "relativeLocator": rel,
                    "secretScan": {"matchedPatterns": secret_hits, "status": "hit"},
                    "sizeBytes": len(data),
                    "sourceSha256": source_sha,
                    "transform": None,
                }
            )
            continue
        destination = fixture_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        entries.append(
            {
                "fixtureSha256": source_sha,
                "lifecycleState": "captured",
                "relativeLocator": rel,
                "secretScan": {"matchedPatterns": [], "status": "clean"},
                "sizeBytes": len(data),
                "sourceSha256": source_sha,
                "transform": None,
            }
        )

    post_paths = _walk_regular_files(source_root)
    post_inventory = _inventory_hash(source_root, post_paths)
    if post_inventory != pre_inventory:
        _emit(
            {
                "error": "source-root changed during capture (inventory hash mismatch)",
                "exitCode": 3,
                "postReadInventoryHash": post_inventory,
                "preReadInventoryHash": pre_inventory,
            }
        )
        return 3

    entries.sort(key=lambda entry: entry["relativeLocator"])
    manifest: dict[str, object] = {
        "files": entries,
        "fixtureRoot": os.path.relpath(fixture_root, manifest_path.parent),
        "inventoryHash": pre_inventory,
        "schemaVersion": SCHEMA_VERSION,
        "sourceRootId": _sha256_bytes(str(resolved_source).encode("utf-8")),
    }
    fake_now = os.environ.get("FOGLAMP_FAKE_NOW")
    if fake_now:
        manifest["capturedAt"] = fake_now

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    manifest_path.write_text(manifest_text, encoding="utf-8")

    _emit(
        {
            "capturedFiles": len(entries) - len(quarantined),
            "quarantinedFiles": sorted(quarantined),
            "status": "ok",
            "totalFiles": len(entries),
        }
    )
    return 0


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schemaVersion: {manifest.get('schemaVersion')!r}")
    return manifest


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = _load_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit({"error": f"cannot load manifest: {exc}", "exitCode": 2})
        return 2

    if args.fixture_root:
        fixture_root = Path(args.fixture_root).resolve()
    else:
        fixture_root = (manifest_path.parent / str(manifest["fixtureRoot"])).resolve()

    mismatches: list[dict[str, object]] = []
    for entry in manifest["files"]:
        if entry["lifecycleState"] != "captured":
            continue
        rel = str(entry["relativeLocator"])
        fixture_file = fixture_root / rel
        if not fixture_file.is_file():
            mismatches.append(
                {"actualSha256": None, "expectedSha256": entry["fixtureSha256"], "reason": "missing", "relativeLocator": rel}
            )
            continue
        actual = _sha256_file(fixture_file)
        if actual != entry["fixtureSha256"]:
            mismatches.append(
                {
                    "actualSha256": actual,
                    "expectedSha256": entry["fixtureSha256"],
                    "reason": "hash_mismatch",
                    "relativeLocator": rel,
                }
            )

    mismatches.sort(key=lambda item: item["relativeLocator"])
    _emit({"mismatches": mismatches, "status": "ok" if not mismatches else "mismatch"})
    return 0 if not mismatches else 1


def cmd_compare(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = _load_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit({"error": f"cannot load manifest: {exc}", "exitCode": 2})
        return 2

    candidate_root = Path(args.candidate_root)
    if not candidate_root.is_dir():
        _emit({"error": "candidate-root must be an existing directory", "exitCode": 2})
        return 2

    expected = {str(entry["relativeLocator"]): str(entry["sourceSha256"]) for entry in manifest["files"]}
    candidate_paths = _walk_regular_files(candidate_root)

    missing = sorted(set(expected) - set(candidate_paths))
    extra = sorted(set(candidate_paths) - set(expected))
    changed: list[dict[str, str]] = []
    for rel in sorted(set(candidate_paths) & set(expected)):
        actual = _sha256_file(candidate_root / rel)
        if actual != expected[rel]:
            changed.append({"actualSha256": actual, "expectedSha256": expected[rel], "relativeLocator": rel})

    identical = not (missing or extra or changed)
    _emit(
        {
            "changed": changed,
            "extra": extra,
            "missing": missing,
            "status": "identical" if identical else "different",
        }
    )
    return 0 if identical else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="architecture_baseline", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture a read-only fixture baseline")
    capture.add_argument("--source-root", required=True)
    capture.add_argument("--fixture-root", required=True)
    capture.add_argument("--manifest", required=True)
    capture.set_defaults(func=cmd_capture)

    verify = subparsers.add_parser("verify", help="verify fixture hashes against the manifest")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--fixture-root", default=None)
    verify.set_defaults(func=cmd_verify)

    compare = subparsers.add_parser("compare", help="compare a candidate tree against the manifest")
    compare.add_argument("--manifest", required=True)
    compare.add_argument("--candidate-root", required=True)
    compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
