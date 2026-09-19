"""Actual bridge receipts replayed at the provider-free backend boundary."""
from __future__ import annotations

import copy
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ("meta.json", "research_report.md", "sources.json", "research_quality.json")
META = {"research_engine": "agentic-phases/v1", "quality_policy": "mechanical-with-advisory/v1"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return load("backend_quality_handoff", ROOT / "backend/app/services/research_quality_gate.py")


@pytest.fixture(scope="module")
def quality():
    return load("producer_quality_handoff", ROOT / "deerflow_bridge/research_quality.py")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resign(receipt):
    receipt["receipt_sha256"] = digest(canonical({
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def seal(root):
    entries = {}
    for name in ARTIFACTS:
        path = root / name
        if path.exists():
            raw = path.read_bytes()
            entries[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    write_json(root / "research_contract_manifest.json", {"version": 1, "files": entries})
    return entries


@pytest.fixture
def handoff(tmp_path, quality):
    sources = [
        {"url": "https://example.gov/release", "title": "发布", "source_origin": "fetched"},
        {"url": "https://example.org/discovery", "source_origin": "search_snippet"},
    ]
    report = (
        "# Research report\n\n## Findings\n\n"
        + "The release describes an observed change; its persistence remains uncertain [S1]. " * 8
        + "\n\n## Limits\n\nThe discovery lead is search evidence only [S2].\n"
    )
    receipt = quality.make_receipt(report, sources, advisory={"verdict": "FAIL", "scores": {"all": 0}})
    (tmp_path / "research_report.md").write_bytes(report.encode("utf-8"))
    write_json(tmp_path / "meta.json", META)
    write_json(tmp_path / "sources.json", sources)
    write_json(tmp_path / "research_quality.json", receipt)
    return tmp_path, report, sources, receipt, seal(tmp_path)


def both(gate, handoff):
    root, report, _, _, entries = handoff
    return [gate.quality_contract_errors(str(root), entries, report), gate.quality_errors(str(root))]


@pytest.mark.parametrize("meta,expected", [
    (META, True), ({**META, "depth": "deep"}, True), (None, False), ([], False), ({}, False),
    ({"research_engine": META["research_engine"]}, False),
    ({"quality_policy": META["quality_policy"]}, False),
    ({**META, "quality_policy": "legacy"}, False),
    ({**META, "research_engine": "agentic-phases/v2"}, False),
    ({**META, "quality_policy": "mechanical-with-advisory/v1 "}, False),
])
def test_exact_program_policy_pair(gate, meta, expected):
    assert gate.is_agentic_quality(meta) is expected


def test_actual_receipt_accepts_advisory_fail_and_preserves_sources(gate, handoff):
    root, _, sources, receipt, _ = handoff
    before = {name: (root / name).read_bytes() for name in ARTIFACTS}
    assert receipt["passed"] is True
    assert both(gate, handoff) == [[], []]
    assert receipt["advisory"]["verdict"] == "FAIL"
    assert sources[1]["source_origin"] == "search_snippet"
    assert before == {name: (root / name).read_bytes() for name in ARTIFACTS}


@pytest.mark.parametrize("name", ARTIFACTS)
def test_all_quality_inputs_must_be_registered(gate, handoff, name):
    root, _, _, _, entries = handoff
    del entries[name]
    write_json(root / "research_contract_manifest.json", {"version": 1, "files": entries})
    for errors in both(gate, handoff):
        assert f"research_quality_artifact_unregistered:{name}" in errors


@pytest.mark.parametrize("name", ARTIFACTS)
def test_missing_artifact_cannot_succeed(gate, handoff, name):
    (handoff[0] / name).unlink()
    for errors in both(gate, handoff):
        assert f"research_quality_artifact_missing:{name}" in errors


@pytest.mark.parametrize("name", ARTIFACTS)
def test_unsealed_artifact_tampering_is_rejected(gate, handoff, name):
    with (handoff[0] / name).open("ab") as handle:
        handle.write(b" ")
    for errors in both(gate, handoff):
        assert f"research_quality_artifact_sha256_mismatch:{name}" in errors


@pytest.mark.parametrize("field", ["research_engine", "quality_policy"])
def test_resealed_wrong_policy_is_not_legacy_fallback(gate, handoff, field):
    root, report, sources, receipt, _ = handoff
    write_json(root / "meta.json", {**META, field: "legacy"})
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_policy_identity_invalid" in errors


@pytest.mark.parametrize("spoof", [False, True])
def test_failed_mechanics_and_resigned_model_pass_cannot_succeed(gate, quality, handoff, spoof):
    root, report, sources, _, _ = handoff
    report += "\nThis model-invented citation has no source [S999].\n"
    receipt = quality.make_receipt(report, sources, advisory={"verdict": "PASS"})
    assert receipt["passed"] is False
    if spoof:
        receipt.update(passed=True, errors=[])
        resign(receipt)
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "citation_unresolved:S999" in errors
        if spoof:
            assert "receipt_passed_mismatch" in errors


@pytest.mark.parametrize("field,value", [
    ("schema", "research-quality/v99"), ("policy", "model-score/v1"),
    ("gate_version", "future"), ("passed", 1), ("errors", ["invented"]),
    ("warnings", []), ("report_sha256", "0" * 64), ("report_chars", 1),
    ("sources_sha256", "0" * 64), ("inputs", {}),
])
def test_every_receipt_binding_field_is_replayed_even_if_resigned(gate, handoff, field, value):
    root, report, sources, receipt, _ = handoff
    receipt[field] = value
    resign(receipt)
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert any(error.startswith("receipt_") for error in errors)


def test_receipt_hash_covers_advice_without_using_it_as_veto(gate, handoff):
    root, report, sources, receipt, _ = handoff
    receipt["advisory"] = {"verdict": "PASS"}
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "receipt_hash_mismatch" in errors


def test_resealed_report_tamper_and_reordered_sources_fail_binding(gate, handoff):
    root, report, sources, receipt, _ = handoff
    report += "\nAdditional unjudged assertion.\n"
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "sources.json", sources[::-1])
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "receipt_report_sha256_mismatch" in errors
        assert "receipt_report_chars_mismatch" in errors
        assert "receipt_sources_sha256_mismatch" in errors


def test_exact_report_includes_original_newlines_and_annex(gate, quality, handoff):
    root, report, sources, _, _ = handoff
    report = report.replace("\n", "\r\n") + "\r\n## Annex\r\n\r\nAn exact appendix [S1].\r\n"
    receipt = quality.make_receipt(report, sources)
    (root / "research_report.md").write_bytes(report.encode("utf-8"))
    write_json(root / "research_quality.json", receipt)
    entries = seal(root)
    assert both(gate, (root, report, sources, receipt, entries)) == [[], []]
    assert "research_quality_report_input_mismatch" in gate.quality_contract_errors(
        str(root), entries, report.replace("\r\n", "\n"),
    )


@pytest.mark.parametrize("raw", [b"{", b"null", b"{}", b'[{"url":"https://example.org", "url":"https://evil.org"}]', b"[NaN]", b"\xff"])
def test_sources_malformed_or_not_list_never_succeed(gate, handoff, raw):
    root, report, sources, receipt, _ = handoff
    (root / "sources.json").write_bytes(raw)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert errors
        assert any("sources" in error for error in errors)


@pytest.mark.parametrize("raw", [None, b"{", b"null", b"[]", b'{"version":true,"files":{}}', b'{"version":2,"files":{}}', b'{"version":1}', b'{"version":1,"files":[]}'])
def test_missing_or_malformed_manifest_cannot_claim_success(gate, handoff, raw):
    path = handoff[0] / "research_contract_manifest.json"
    if raw is None:
        path.unlink()
    else:
        path.write_bytes(raw)
    assert gate.quality_errors(str(handoff[0]))


@pytest.mark.parametrize("entry", [None, {}, {"bytes": True, "sha256": "0" * 64}, {"bytes": -1, "sha256": 12}])
def test_malformed_registration_rejected(gate, handoff, entry):
    root, _, _, _, entries = handoff
    entries["sources.json"] = entry
    write_json(root / "research_contract_manifest.json", {"version": 1, "files": entries})
    for errors in both(gate, handoff):
        assert "research_quality_manifest_entry_invalid:sources.json" in errors


def actor_fixture(bridge, sources):
    """Minimal sealed projection; upstream actor provenance remains parent-owned."""
    actor = "Northstar"
    actor_id = bridge.stable_actor_id(actor)
    source_id = bridge.stable_source_id(sources[0]["url"])
    families = {}
    paragraphs = []
    for family, dimensions in bridge.ACTOR_BEHAVIOR_READY_FAMILIES.items():
        claim = f"Northstar maintains documented operational evidence about {family.replace('_', ' ')}."
        evidence = {
            "dimension": dimensions[0], "claim_id": "claim_" + digest(claim)[:20],
            "claim_sha256": digest(claim), "visible_claim_text": claim, "source_ids": [source_id],
        }
        families[family] = evidence
        paragraphs.append(claim + " [S1]\n" + bridge._actor_family_evidence_marker(actor_id, family, evidence))
    projection = [{"actor": actor, "actor_id": actor_id, "families": families}]
    coverage = {
        "tier_1_2_actor_ids_ordered": [actor_id], "admitted_source_ids": [source_id],
        "admitted_source_ids_sha256": digest(source_id), "behavior_family_projection": projection,
        "behavior_family_projection_sha256": digest(canonical(projection)),
    }
    return coverage, "# Actor findings\n\n" + "\n\n".join(paragraphs) + "\n"


@pytest.fixture
def actor_handoff(gate, quality, handoff):
    root, _, sources, _, _ = handoff
    bridge = gate._bridge_module("deerflow_research.py")
    coverage, report = actor_fixture(bridge, sources)
    audit = bridge.audit_global_actor_report_coverage(report, coverage, sources)
    assert audit["required"] is True and audit["complete"] is True
    receipt = quality.make_receipt(report, sources, actor_audit=audit, advisory={"verdict": "FAIL"})
    assert receipt["passed"] is True
    write_json(root / "meta.json", {**META, "actor_dossier_coverage": coverage})
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    return root, report, sources, receipt, seal(root)


def test_actual_complete_actor_audit_accepts_advisory_fail(gate, actor_handoff):
    assert both(gate, actor_handoff) == [[], []]


def test_modern_actor_audit_uses_persisted_source_projection(gate, quality, actor_handoff, monkeypatch):
    # Global actor replay consumes the admitted sources.json projection, not
    # collector rows. Native export/merge use source_origin/reachable and omit
    # the collector's transient ok flag. Engine selection must not erase them.
    root, report, sources, receipt, _ = actor_handoff
    # Carry the persisted fetched-source identity/admission fields explicitly.
    sources[0].update(
        source_id=gate._bridge_module("deerflow_research.py").stable_source_id(sources[0]["url"]),
        reachable=True,
    )
    receipt = quality.make_receipt(
        report, sources, actor_audit=receipt["inputs"]["actor_audit"], advisory={"verdict": "FAIL"},
    )
    assert receipt["passed"] is True
    write_json(root / "sources.json", sources)
    write_json(root / "research_quality.json", receipt)
    entries = seal(root)
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    assert all("ok" not in row for row in sources)
    for errors in both(gate, (root, report, sources, receipt, entries)):
        assert errors == []


@pytest.mark.parametrize("audit", [None, {"required": False}, {"required": True, "complete": True, "errors": []}])
def test_actor_audit_cannot_self_exempt_or_assert_success(gate, quality, actor_handoff, audit):
    root, report, sources, _, _ = actor_handoff
    receipt = quality.make_receipt(report, sources, actor_audit=audit)
    assert receipt["passed"] is True
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_actor_audit_mismatch" in errors


def test_required_actor_audit_without_metadata_coverage_fails(gate, actor_handoff):
    root, report, sources, receipt, _ = actor_handoff
    write_json(root / "meta.json", META)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_actor_coverage_missing" in errors


@pytest.mark.parametrize("coverage", [None, {}, [], {"behavior_family_projection": [42]}])
def test_present_invalid_coverage_cannot_be_skipped(gate, handoff, coverage):
    root, report, sources, receipt, _ = handoff
    write_json(root / "meta.json", {**META, "actor_dossier_coverage": coverage})
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_actor_audit_mismatch" in errors
        assert "research_quality_actor_audit_incomplete" in errors


def test_actor_replay_uses_actual_report_and_sources(gate, quality, actor_handoff):
    root, report, sources, receipt, _ = actor_handoff
    old_audit = copy.deepcopy(receipt["inputs"]["actor_audit"])
    sources[0]["url"] = "https://example.gov/different"
    report = report.replace("Northstar maintains", "Another actor maintains")
    # Recreate a perfectly bound quality receipt carrying a stale complete audit.
    receipt = quality.make_receipt(report, sources, actor_audit=old_audit)
    assert receipt["passed"] is True
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "sources.json", sources)
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_actor_audit_mismatch" in errors
        assert "research_quality_actor_audit_incomplete" in errors


def test_fixed_cached_helper_cannot_be_overridden_by_run_artifact(gate, handoff):
    (handoff[0] / "research_quality.py").write_text("raise AssertionError('untrusted helper')", encoding="utf-8")
    module = gate._bridge_module("research_quality.py")
    assert Path(module.__file__) == ROOT / "deerflow_bridge/research_quality.py"
    assert gate._bridge_module("research_quality.py") is module
    assert both(gate, handoff) == [[], []]


def test_standalone_import_and_actor_replay_need_no_app_or_provider(tmp_path):
    # Mirror the backend launched with only backend/ on sys.path. Keep the
    # inherited offline audit hook, but remove ambient bridge/repository paths.
    script = """
import builtins
import importlib.util
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() not in {root, root / 'deerflow_bridge'}]
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'app', 'deerflow', 'openai', 'anthropic', 'langchain', 'langgraph'}:
        raise AssertionError('Forbidden runtime import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
spec = importlib.util.spec_from_file_location('standalone_gate', root / 'backend/app/services/research_quality_gate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
before = list(sys.path)
policy = module._bridge_module('research_quality.py')
sources = [{'url': 'https://example.org/release'}]
report = 'An observed change remains uncertain [S1]. ' * 20
assert policy.validate_receipt(policy.make_receipt(report, sources), report, sources) == []
bridge = module._bridge_module('deerflow_research.py')
assert bridge.audit_global_actor_report_coverage(report, {}, sources)['complete'] is False
assert module._scenario_errors(report) == []
assert sys.path == before
print('isolated offline replay passed')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)], cwd=tmp_path,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated offline replay passed"


@pytest.mark.parametrize("boundary", ["contract", "disk"])
@pytest.mark.parametrize("kind", ["file", "root", "ancestor"])
def test_replacement_never_reads_outside_root(gate, handoff, monkeypatch, boundary, kind):
    base, report, _, _, entries = handoff
    root = base / "allowed" / "handoff"
    root.mkdir(parents=True)
    for name in (*ARTIFACTS, "research_contract_manifest.json"):
        (root / name).write_bytes((base / name).read_bytes())
    target = root / "sources.json"
    outside = base / "outside" / "handoff" / target.name
    outside.parent.mkdir(parents=True)
    # Matching bytes would defeat a post-read checksum-only defense. We must
    # reject the outside inode before it reaches either content reader.
    outside.write_bytes(target.read_bytes())
    outside_stat = outside.stat()
    outside_identity = (outside_stat.st_dev, outside_stat.st_ino)
    replaced, outside_reads = [], []

    def swap():
        if replaced:
            return
        replaced.append(True)
        if kind == "file":
            target.unlink()
            target.symlink_to(outside)
        else:
            directory = root if kind == "root" else root.parent
            directory.rename(directory.with_name(directory.name + "-retained"))
            destination = outside.parent if kind == "root" else outside.parent.parent
            directory.symlink_to(destination, target_is_directory=True)

    original_read = Path.read_bytes
    original_open = os.open
    original_path_open = Path.open
    original_fdopen = os.fdopen

    def before_path_read(path):
        # Reproduce the original resolve -> pathname-open race exactly.
        if path == target:
            swap()
        return original_read(path)

    def before_descriptor_open(path, flags, *args, **kwargs):
        component = {"file": target.name, "root": root.name, "ancestor": root.parent.name}[kind]
        if path == component and kwargs.get("dir_fd") is not None:
            swap()
        return original_open(path, flags, *args, **kwargs)

    def check_reader(handle):
        actual = os.fstat(handle.fileno())
        if (actual.st_dev, actual.st_ino) == outside_identity:
            outside_reads.append(True)
            handle.close()
            pytest.fail("outside-root inode reached a content reader")
        return handle

    def path_reader(path, *args, **kwargs):
        return check_reader(original_path_open(path, *args, **kwargs))

    def descriptor_reader(fd, *args, **kwargs):
        return check_reader(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(Path, "read_bytes", before_path_read)
    monkeypatch.setattr(Path, "open", path_reader)
    monkeypatch.setattr(os, "open", before_descriptor_open)
    monkeypatch.setattr(os, "fdopen", descriptor_reader)
    errors = (
        gate.quality_contract_errors(str(root), entries, report)
        if boundary == "contract" else gate.quality_errors(str(root))
    )
    assert replaced == [True]
    assert outside_reads == []
    assert errors


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_nonregular_sources_never_reach_reader_or_block(gate, handoff, monkeypatch, kind):
    root = handoff[0]
    target = root / "sources.json"
    target.unlink()
    if kind == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()
    original_path_open, original_fdopen, original_open = Path.open, os.fdopen, os.open
    readers = []

    def path_reader(path, *args, **kwargs):
        if path == target:
            readers.append(kind)
            pytest.fail("nonregular source must be rejected before pathname read")
        return original_path_open(path, *args, **kwargs)

    def descriptor_open(path, flags, *args, **kwargs):
        if path == target.name:
            assert flags & os.O_NONBLOCK, "FIFO open must not block before fstat"
        return original_open(path, flags, *args, **kwargs)

    def descriptor_reader(fd, *args, **kwargs):
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            readers.append(kind)
            pytest.fail("nonregular source reached descriptor content reader")
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(Path, "open", path_reader)
    monkeypatch.setattr(os, "open", descriptor_open)
    monkeypatch.setattr(os, "fdopen", descriptor_reader)
    for errors in both(gate, handoff):
        assert "research_quality_artifact_not_regular:sources.json" in errors
    assert readers == []


@pytest.mark.parametrize("kind", ["replace", "rewrite"])
def test_changes_after_open_invalidate_byte_snapshot(gate, handoff, monkeypatch, kind):
    root = handoff[0]
    target = root / "sources.json"
    before = target.stat()
    original_fdopen = os.fdopen
    changed = []

    def descriptor_reader(fd, *args, **kwargs):
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino) and not changed:
            changed.append(True)
            if kind == "replace":
                replacement = root / "replacement.json"
                replacement.write_bytes(b"[]")
                replacement.replace(target)
            else:
                target.write_bytes(b"[]")
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", descriptor_reader)
    errors = []
    assert gate._read_artifact(root, target.name, errors) is None
    assert changed == [True]
    assert "research_quality_artifact_changed:sources.json" in errors


def test_sources_replaced_by_fifo_at_open_are_rejected_before_read(gate, handoff, monkeypatch):
    root = handoff[0]
    target = root / "sources.json"
    original_open, original_fdopen = os.open, os.fdopen
    replaced = []

    def replace_at_open(path, flags, *args, **kwargs):
        if path == target.name and kwargs.get("dir_fd") is not None and not replaced:
            replaced.append(True)
            target.unlink()
            os.mkfifo(target)
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    def check_reader(fd, *args, **kwargs):
        assert stat.S_ISREG(os.fstat(fd).st_mode), "FIFO reached the content reader"
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_at_open)
    monkeypatch.setattr(os, "fdopen", check_reader)
    errors = gate.quality_contract_errors(str(root), handoff[4], handoff[1])
    assert replaced == [True]
    assert "research_quality_artifact_not_regular:sources.json" in errors


@pytest.mark.parametrize("name", ["unknown.json", "../sources.json", "/sources.json"])
def test_reader_rejects_noncontract_names_before_open(gate, tmp_path, monkeypatch, name):
    def forbidden_open(*args, **kwargs):
        pytest.fail("unexpected artifact name reached filesystem open")

    monkeypatch.setattr(os, "open", forbidden_open)
    errors = []
    assert gate._read_artifact(tmp_path, name, errors) is None
    assert errors == ["research_quality_artifact_name_invalid"]


@pytest.mark.parametrize("failure", ["directory_transfer", "file_cleanup", "directory_cleanup"])
def test_close_failure_does_not_leak_descriptors_or_escape(gate, handoff, monkeypatch, failure):
    root = handoff[0]
    target = root / "sources.json"
    if failure == "file_cleanup":
        target.unlink()
        os.mkfifo(target)
    real_open, real_close = os.open, os.close
    opened, failed = [], []
    source_fd = leaf_fd = None

    def tracking_open(path, flags, *args, **kwargs):
        nonlocal source_fd, leaf_fd
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        if path == target.name:
            source_fd, leaf_fd = fd, kwargs["dir_fd"]
        return fd

    def close_then_interrupt(fd):
        real_close(fd)
        chosen = (
            failure == "directory_transfer"
            or (failure == "file_cleanup" and fd == source_fd)
            or (failure == "directory_cleanup" and fd == leaf_fd)
        )
        if chosen and not failed:
            failed.append(fd)
            raise InterruptedError(errno.EINTR, "close completed before reporting interruption")

    def live_fds():
        result = []
        for fd in set(opened):
            try:
                os.fstat(fd)
            except OSError as exc:
                assert exc.errno == errno.EBADF
            else:
                result.append(fd)
        return result

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", close_then_interrupt)
    try:
        errors = []
        assert gate._read_artifact(root, target.name, errors) is None
        assert len(failed) == 1
        assert "research_quality_artifact_unreadable:sources.json" in errors
        assert live_fds() == []
    finally:
        # Keep even the pre-fix red run from leaking into later pytest cases.
        for fd in live_fds():
            real_close(fd)


def scenario_sections(probabilities=(50, 30, 20)):
    base, upside, downside = probabilities
    return (
        "\n## Scenarios\n\n"
        "### SCN-A — Base: 50%\n\nThe observed trend persists [S1].\n\n"
        "### SCN-B — Upside: 30%\n\nAdoption could broaden [S1].\n\n"
        "### SCN-C — Downside: 20%\n\nSupply could become constrained [S1].\n\n"
        "## Probability summary\n\n"
        "| Scenario | Probability |\n|---|---:|\n"
        f"| A. Base | {base}% |\n| B. Upside | {upside}% |\n| C. Downside | {downside}% |\n"
    )


@pytest.mark.parametrize("advisory", ["FAIL", "PASS"])
@pytest.mark.parametrize("probabilities,conflicting", [((50, 30, 20), False), ((60, 25, 15), True)])
def test_source_scenario_guard_is_independent_of_advisory_and_optional_frame(
    gate, quality, handoff, advisory, probabilities, conflicting,
):
    root, report, sources, _, _ = handoff
    report += scenario_sections(probabilities)
    receipt = quality.make_receipt(report, sources, advisory={"verdict": advisory})
    # Both tables sum to 100, but incompatible restatements require the extra
    # source-level guard even when the optional explicit frame was not supplied.
    assert receipt["passed"] is True
    assert receipt["inputs"]["scenario_frame"] is None
    assert quality.validate_receipt(receipt, report, sources) == []
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    entries = seal(root)
    snapshot = (root / "research_quality.json").read_bytes()
    for errors in both(gate, (root, report, sources, receipt, entries)):
        if conflicting:
            assert "research_quality_scenario_probability_conflict" in errors
        else:
            assert errors == []
    assert (root / "research_quality.json").read_bytes() == snapshot


def test_arbitrary_first_table_does_not_become_canonical_scenario_frame(gate, quality, handoff):
    root, report, sources, _, _ = handoff
    report += (
        "\n## Local market survey\n\n"
        "| Scenario | Probability |\n|---|---:|\n"
        "| A. Local growth | 10% |\n| B. Local contraction | 40% |\n| C. Local stability | 50% |\n"
    ) + scenario_sections()
    receipt = quality.make_receipt(report, sources, advisory={"verdict": "FAIL"})
    assert receipt["inputs"]["scenario_frame"] is None
    assert receipt["passed"] is True
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    assert both(gate, (root, report, sources, receipt, seal(root))) == [[], []]


def test_explicit_scenario_frame_total_remains_a_separate_required_check(gate, quality, handoff):
    root, report, sources, _, _ = handoff
    report += scenario_sections()
    frame = [{"name": name, "weight": weight} for name, weight in (
        ("Base", 50), ("Upside", 30), ("Downside", 30),
    )]
    receipt = quality.make_receipt(report, sources, advisory={"verdict": "FAIL"}, scenario_frame=frame)
    assert receipt["passed"] is False
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "scenario_weights_total_not_100" in errors
        assert "research_quality_scenario_probability_conflict" not in errors


def test_unavailable_source_scenario_guard_cannot_claim_success(gate, handoff, monkeypatch):
    def unavailable(report):
        raise RuntimeError("offline helper unavailable")

    monkeypatch.setattr(gate._bridge_module("deerflow_research.py"), "scenario_probability_conflicts", unavailable)
    for errors in both(gate, handoff):
        assert "research_quality_scenario_check_unavailable:RuntimeError" in errors


def test_source_scenario_guard_and_actor_audit_are_a_conjunction(gate, quality, actor_handoff):
    root, report, sources, _, _ = actor_handoff
    report += scenario_sections((60, 25, 15))
    receipt = quality.make_receipt(report, sources, actor_audit={"required": False}, advisory={"verdict": "FAIL"})
    assert receipt["passed"] is True
    (root / "research_report.md").write_text(report, encoding="utf-8")
    write_json(root / "research_quality.json", receipt)
    for errors in both(gate, (root, report, sources, receipt, seal(root))):
        assert "research_quality_scenario_probability_conflict" in errors
        assert "research_quality_actor_audit_mismatch" in errors


@pytest.fixture
def promoted_agentic_contract(gate, handoff, monkeypatch):
    """Real offline producer -> orchestrator promotion, with no quality stubs.

    This exercises the publication functions used by PipelineOrchestrator;
    provider research, extraction and source/actor authenticity are separate
    parent boundaries. All files here belong to pytest's temporary directory.
    """
    monkeypatch.syspath_prepend(str(ROOT / "deerflow_bridge"))
    from app.services import pipeline_orchestrator as po

    producer = load("agentic_quality_test_producer", ROOT / "deerflow_bridge/agentic_bridge.py")
    dr = gate._bridge_module("deerflow_research.py")
    source, report, sources, _, _ = handoff
    report += scenario_sections()
    meta = {**META, "depth": "deep", "status": "completed"}
    advisory = {
        "verdict": "FAIL", "scores": dict.fromkeys(po._RESEARCH_JUDGE_DIMS, 0),
        "gaps": ["Model critique remains advisory for this modern producer."],
    }
    (source / "research_report.md").write_bytes(report.encode("utf-8"))
    receipt = producer.persist_quality(dr, source, report, sources, meta, advisory=advisory)
    producer_receipt_bytes = (source / "research_quality.json").read_bytes()
    write_json(source / "meta.json", meta)
    write_json(source / "research_report_judge.json", advisory)
    # The producer writes the receipt; the actual promotion must create and
    # register the complete consumer manifest, without the test's seal helper.
    (source / "research_contract_manifest.json").unlink()
    published = source / "published"
    first = po._promote_research_contract(str(source), str(published))
    return SimpleNamespace(
        po=po, producer=producer, dr=dr, source=source, published=published,
        report=report, sources=sources, meta=meta, advisory=advisory,
        receipt=receipt, first=first,
        producer_receipt_bytes=producer_receipt_bytes,
    )


def published_bytes(root):
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def assert_published_valid(case):
    assert case.po._research_contract_validation_errors(str(case.published)) == []
    assert case.po._research_contract_quality_errors(str(case.published), require_judge=True) == []
    assert case.po._research_report_is_judge_bound(str(case.published)) is True
    assert not any(path.name.startswith(".research-") for path in case.published.iterdir())


def test_native_producer_promotes_and_finalizes_despite_advisory_fail(gate, promoted_agentic_contract):
    case = promoted_agentic_contract
    assert case.receipt["passed"] is True
    assert case.receipt["advisory"]["verdict"] == "FAIL"
    assert set(ARTIFACTS) <= set(case.first["files"])
    assert_published_valid(case)
    receipt_bytes = (case.published / "research_quality.json").read_bytes()
    assert receipt_bytes == case.producer_receipt_bytes
    updated_meta = {**case.meta, "research_budget": {"denials": 2}}
    payload = {"report": case.report, "sources": case.sources, "meta": updated_meta}

    final = case.po._finalize_research_contract(str(case.published), payload)

    assert final["generation"] != case.first["generation"]
    assert set(ARTIFACTS) <= set(final["files"])
    assert (case.published / "research_report.md").read_bytes() == case.report.encode("utf-8")
    assert (case.published / "research_quality.json").read_bytes() == receipt_bytes
    assert json.loads((case.published / "sources.json").read_bytes()) == case.sources
    assert json.loads((case.published / "meta.json").read_bytes()) == updated_meta
    assert json.loads((case.published / "research_report_judge.json").read_bytes())["verdict"] == "FAIL"
    assert_published_valid(case)
    assert gate.quality_errors(str(case.published)) == []
    snapshot = published_bytes(case.published)
    assert case.po._finalize_research_contract(str(case.published), payload) == final
    assert published_bytes(case.published) == snapshot


@pytest.mark.parametrize("defect,expected", [
    ("missing_receipt", "research_quality_artifact_unregistered:research_quality.json"),
    ("report_changed", "receipt_report_sha256_mismatch"),
    ("sources_reordered", "receipt_sources_sha256_mismatch"),
    ("forged_pass", "citation_unresolved:S999"),
    ("scenario_conflict", "research_quality_scenario_probability_conflict"),
])
def test_orchestrator_rejects_bad_modern_producer_and_rolls_back(
    promoted_agentic_contract, defect, expected,
):
    case = promoted_agentic_contract
    snapshot = published_bytes(case.published)
    if defect == "missing_receipt":
        (case.source / "research_quality.json").unlink()
    elif defect == "report_changed":
        (case.source / "research_report.md").write_text(case.report + "\nChanged prose.\n", encoding="utf-8")
    elif defect == "sources_reordered":
        write_json(case.source / "sources.json", case.sources[::-1])
    else:
        report = (
            case.report + "\nUnsupported citation [S999].\n" if defect == "forged_pass"
            else case.report.replace("| A. Base | 50% |", "| A. Base | 60% |")
            .replace("| B. Upside | 30% |", "| B. Upside | 25% |")
            .replace("| C. Downside | 20% |", "| C. Downside | 15% |")
        )
        (case.source / "research_report.md").write_text(report, encoding="utf-8")
        with pytest.raises(RuntimeError, match="mechanical research publication checks failed"):
            case.producer.persist_quality(
                case.dr, case.source, report, case.sources, case.meta, advisory=case.advisory,
            )
        assert case.meta["research_report_quality_gate"]["passed"] is False
        write_json(case.source / "meta.json", case.meta)
        receipt = json.loads((case.source / "research_quality.json").read_bytes())
        if defect == "forged_pass":
            assert receipt["passed"] is False
            receipt.update(passed=True, errors=[])
            resign(receipt)
            write_json(case.source / "research_quality.json", receipt)
        else:
            # The unchanged receipt schema's mechanics pass; both real
            # producer and consumer must add the source-level scenario guard.
            assert receipt["passed"] is True

    with pytest.raises(RuntimeError, match=expected):
        case.po._promote_research_contract(str(case.source), str(case.published))

    assert published_bytes(case.published) == snapshot
    assert_published_valid(case)


@pytest.mark.parametrize("mutation,expected", [
    ("report", "post-judge research report mutation rejected"),
    ("sources", "receipt_sources_sha256_mismatch"),
])
def test_orchestrator_finalization_cannot_rebind_modern_receipt(
    promoted_agentic_contract, mutation, expected,
):
    case = promoted_agentic_contract
    snapshot = published_bytes(case.published)
    payload = {"report": case.report, "sources": case.sources, "meta": case.meta}
    payload[mutation] = case.report + "\nNew assertion.\n" if mutation == "report" else case.sources[::-1]

    with pytest.raises(RuntimeError, match=expected):
        case.po._finalize_research_contract(str(case.published), payload)

    assert published_bytes(case.published) == snapshot
    assert_published_valid(case)


def test_canonical_pinned_sources_keep_full_body_through_publication(
    gate, promoted_agentic_contract, monkeypatch,
):
    case = promoted_agentic_contract
    import research_archive

    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_INLINE_CITATIONS", "true")
    monkeypatch.delenv("RESEARCH_BUDGET_DB", raising=False)
    monkeypatch.setattr(research_archive, "_WORKSPACE", None)
    monkeypatch.setattr(case.dr, "_FETCHED_SOURCES", [])
    monkeypatch.setattr(case.dr, "_PINNED_CITATION_INDEX", [])
    urls = ["https://EXAMPLE.test/release/#overview", "https://EXAMPLE.test/release?edition=2#evidence"]
    bodies = [f"Source {i} 中文\r\n" + "Retained evidence.\r\n" * 900 + f"TAIL_SENTINEL_{i}\r\n" for i in (1, 2)]
    supplied = [{
        "url": url, "source_origin": "fetched", "reachable": True,
        "content": body, "content_sha256": digest(body), "content_chars": len(body),
        "receipt_id": f"fixture-fetch-{i}",
    } for i, (url, body) in enumerate(zip(urls, bodies, strict=True), 1)]
    # A spelling/fragment alias must not create a second citation identity.
    supplied.insert(1, {**supplied[0], "url": "https://example.test/release"})
    assert case.dr.seed_manifest_sources(supplied) == 2
    exported = case.dr.export_fetched_sources_for_manifest()
    sources, dropped = case.dr.merge_fetched_into_sources([])
    assert dropped == 0
    canonical_urls = ["https://example.test/release", "https://example.test/release?edition=2"]
    pinned = case.dr.build_citation_index(case.dr._FETCHED_SOURCES)
    assert [entry["url"] for entry in pinned] == canonical_urls
    assert [entry["n"] for entry in pinned] == [1, 2]
    assert [row["url"] for row in sources] == canonical_urls
    for i, (row, export, body, url) in enumerate(zip(sources, exported, bodies, urls, strict=True), 1):
        assert row["source_id"] == export["source_id"] == case.dr.stable_source_id(url)
        assert row["content"] == export["content"] == body
        assert row["content_sha256"] == export["content_sha256"] == digest(body)
        assert row["receipt_id"] == export["receipt_id"] == f"fixture-fetch-{i}"

    case.dr._set_pinned_citation_index(pinned)
    report = case.dr.finalize_report_citations(case.report, SimpleNamespace(write=lambda *_: None))
    assert "## References" in report
    assert all(url in report for url in canonical_urls)
    (case.source / "research_report.md").write_bytes(report.encode("utf-8"))
    write_json(case.source / "sources.json", sources)
    receipt = case.producer.persist_quality(case.dr, case.source, report, sources, case.meta, advisory=case.advisory)
    assert receipt["passed"] is True
    write_json(case.source / "meta.json", case.meta)
    producer_receipt = (case.source / "research_quality.json").read_bytes()
    case.po._promote_research_contract(str(case.source), str(case.published))
    case.po._finalize_research_contract(str(case.published), {
        "report": report, "sources": sources, "meta": {**case.meta, "body_handoff_verified": True},
    })
    assert (case.published / "research_quality.json").read_bytes() == producer_receipt
    assert json.loads((case.published / "sources.json").read_bytes()) == sources
    assert gate.quality_errors(str(case.published)) == []
    assert_published_valid(case)

    snapshot = published_bytes(case.published)
    changed = copy.deepcopy(sources)
    changed[0]["content"] += "tampered tail"
    with pytest.raises(RuntimeError, match="receipt_sources_sha256_mismatch"):
        case.po._finalize_research_contract(str(case.published), {"report": report, "sources": changed})
    assert published_bytes(case.published) == snapshot


@pytest.mark.parametrize("marker,expected", [("[S999]", "citation_unresolved:S999"), ("[S01]", "citation_marker_malformed")])
def test_modern_citation_finalizer_preserves_invalid_markers_for_consumer(
    promoted_agentic_contract, monkeypatch, marker, expected,
):
    case = promoted_agentic_contract
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_INLINE_CITATIONS", "true")
    monkeypatch.setattr(case.dr, "_PINNED_CITATION_INDEX", [])
    # The native builder takes confirmed collector records, rather than the
    # persisted source projection (which intentionally has no collector ok bit).
    confirmed = [{"url": row["url"], "title": row.get("title", ""), "ok": True} for row in case.sources]
    case.dr._set_pinned_citation_index(case.dr.build_citation_index(confirmed))
    snapshot = published_bytes(case.published)
    report = case.dr.finalize_report_citations(
        case.report + f"\nUnsupported marker {marker}.\n", SimpleNamespace(write=lambda *_: None),
    )
    assert marker in report
    assert "## References" in report
    (case.source / "research_report.md").write_bytes(report.encode("utf-8"))
    with pytest.raises(RuntimeError, match="mechanical research publication checks failed"):
        case.producer.persist_quality(case.dr, case.source, report, case.sources, case.meta, advisory=case.advisory)
    receipt = json.loads((case.source / "research_quality.json").read_bytes())
    assert receipt["passed"] is False and expected in receipt["errors"]
    write_json(case.source / "meta.json", case.meta)
    with pytest.raises(RuntimeError, match=expected):
        case.po._promote_research_contract(str(case.source), str(case.published))
    assert published_bytes(case.published) == snapshot
    assert_published_valid(case)


def test_fresh_fetch_pinned_index_matches_final_canonical_source_namespace(gate, quality, monkeypatch):
    dr = gate._bridge_module("deerflow_research.py")
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_INLINE_CITATIONS", "true")
    monkeypatch.delenv("RESEARCH_BUDGET_DB", raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "deerflow_bridge"))
    import research_archive

    monkeypatch.setattr(research_archive, "_WORKSPACE", None)
    monkeypatch.setattr(dr, "_FETCHED_SOURCES", [])
    monkeypatch.setattr(dr, "_PINNED_CITATION_INDEX", [])
    # Exercise the real collector path as well as the manifest-seeded path.
    dr._merge_pending_fetches([
        {"url": "https://EXAMPLE.test/release/#overview", "ok": True},
        {"url": "https://example.test/release", "ok": True},
        {"url": "https://example.test/second", "ok": True},
    ])
    pinned = dr.build_citation_index(dr._FETCHED_SOURCES)
    sources, dropped = dr.merge_fetched_into_sources([])
    assert dropped == 0
    dr._set_pinned_citation_index(pinned)
    report = "# Findings\n\n" + "Observed evidence remains uncertain. " * 20
    report += "\n" + "\n".join(f"A supported observation [S{row['n']}]." for row in pinned)
    report = dr.finalize_report_citations(report, SimpleNamespace(write=lambda *_: None))
    receipt = quality.make_receipt(report, sources, advisory={"verdict": "FAIL"})
    assert receipt["passed"] is True, receipt["errors"]
    assert [row["url"] for row in pinned] == [row["url"] for row in sources]
    assert [row["n"] for row in pinned] == list(range(1, len(sources) + 1))
