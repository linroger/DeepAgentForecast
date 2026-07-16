"""Offline tests for the Foglamp WP0A baseline CLI (scripts/architecture_baseline.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parents[1] / "scripts" / "architecture_baseline.py")


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "FOGLAMP_FAKE_NOW"}
    return subprocess.run(
        [sys.executable, SCRIPT, *argv],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _make_source_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "run.json").write_text('{"status": "completed"}\n', encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "report.md").write_text("# Baseline report\n", encoding="utf-8")
    (nested / "data.bin").write_bytes(b"\x00\x01\x02deterministic")


def _capture(source: Path, fixture: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        "capture",
        "--source-root",
        str(source),
        "--fixture-root",
        str(fixture),
        "--manifest",
        str(manifest),
    )


def test_capture_verify_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_source_tree(source)
    fixture = tmp_path / "fixtures"
    manifest = fixture / "baseline_manifest.json"

    captured = _capture(source, fixture, manifest)
    assert captured.returncode == 0, captured.stdout + captured.stderr
    assert manifest.is_file()

    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == "baseline-manifest/v1"
    assert str(source) not in manifest.read_text(encoding="utf-8")
    locators = [entry["relativeLocator"] for entry in document["files"]]
    assert locators == sorted(locators)
    assert set(locators) == {"run.json", "nested/report.md", "nested/data.bin"}
    for entry in document["files"]:
        assert entry["lifecycleState"] == "captured"
        assert entry["fixtureSha256"] == entry["sourceSha256"]
        assert entry["transform"] is None

    verified = _run("verify", "--manifest", str(manifest), "--fixture-root", str(fixture))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["mismatches"] == []

    # verify also works without --fixture-root (defaults from the manifest)
    defaulted = _run("verify", "--manifest", str(manifest))
    assert defaulted.returncode == 0, defaulted.stdout + defaulted.stderr


def test_verify_detects_mutated_fixture_byte(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_source_tree(source)
    fixture = tmp_path / "fixtures"
    manifest = fixture / "baseline_manifest.json"
    assert _capture(source, fixture, manifest).returncode == 0

    target = fixture / "nested" / "data.bin"
    original = bytearray(target.read_bytes())
    original[0] ^= 0xFF
    target.write_bytes(bytes(original))

    verified = _run("verify", "--manifest", str(manifest))
    assert verified.returncode == 1
    mismatches = json.loads(verified.stdout)["mismatches"]
    assert [item["relativeLocator"] for item in mismatches] == ["nested/data.bin"]
    assert mismatches[0]["reason"] == "hash_mismatch"


def test_compare_detects_changed_missing_extra(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_source_tree(source)
    fixture = tmp_path / "fixtures"
    manifest = fixture / "baseline_manifest.json"
    assert _capture(source, fixture, manifest).returncode == 0

    candidate = tmp_path / "candidate"
    _make_source_tree(candidate)
    (candidate / "run.json").write_text('{"status": "mutated"}\n', encoding="utf-8")
    (candidate / "nested" / "report.md").unlink()
    (candidate / "extra.txt").write_text("surplus\n", encoding="utf-8")

    compared = _run("compare", "--manifest", str(manifest), "--candidate-root", str(candidate))
    assert compared.returncode == 1
    diff = json.loads(compared.stdout)
    assert diff["missing"] == ["nested/report.md"]
    assert diff["extra"] == ["extra.txt"]
    assert [item["relativeLocator"] for item in diff["changed"]] == ["run.json"]

    identical = _run("compare", "--manifest", str(manifest), "--candidate-root", str(source))
    assert identical.returncode == 0
    assert json.loads(identical.stdout)["status"] == "identical"


def test_relative_source_root_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "fixtures"
    manifest = fixture / "baseline_manifest.json"
    rejected = _run(
        "capture",
        "--source-root",
        "relative/source/path",
        "--fixture-root",
        str(fixture),
        "--manifest",
        str(manifest),
    )
    assert rejected.returncode == 2
    assert "absolute" in json.loads(rejected.stdout)["error"]
    assert not manifest.exists()


def test_seeded_secret_canary_is_quarantined_and_never_copied(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_source_tree(source)
    canary = source / "credentials.env"
    canary.write_text("AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")
    fixture = tmp_path / "fixtures"
    manifest = fixture / "baseline_manifest.json"

    captured = _capture(source, fixture, manifest)
    assert captured.returncode == 0, captured.stdout + captured.stderr
    assert json.loads(captured.stdout)["quarantinedFiles"] == ["credentials.env"]

    assert not (fixture / "credentials.env").exists()
    document = json.loads(manifest.read_text(encoding="utf-8"))
    entry = next(item for item in document["files"] if item["relativeLocator"] == "credentials.env")
    assert entry["lifecycleState"] == "quarantined_secret"
    assert entry["fixtureSha256"] is None
    assert entry["secretScan"]["status"] == "hit"

    # quarantined entries do not fail verify (nothing was copied to verify)
    assert _run("verify", "--manifest", str(manifest)).returncode == 0


def test_manifest_is_byte_identical_across_two_runs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_source_tree(source)

    fixture_a = tmp_path / "fix_a"
    fixture_b = tmp_path / "fix_b"
    manifest_a = fixture_a / "baseline_manifest.json"
    manifest_b = fixture_b / "baseline_manifest.json"

    assert _capture(source, fixture_a, manifest_a).returncode == 0
    assert _capture(source, fixture_b, manifest_b).returncode == 0

    assert manifest_a.read_bytes() == manifest_b.read_bytes()
