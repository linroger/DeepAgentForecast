"""Offline model-policy, repair-control and large-report regression checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "deerflow_bridge")]

from deerflow_bridge import research_quality as quality  # noqa: E402
from deerflow_bridge import research_synthesis as synthesis  # noqa: E402
from deerflow_bridge.research_compaction import reset_compaction_stop  # noqa: E402
from deerflow_bridge.research_context import ContextPolicy, estimate_tokens  # noqa: E402
from deerflow_bridge.research_workspace import ResearchWorkspace, ResearchWorkspaceError  # noqa: E402


SOURCES = [{"url": "https://example.org/release", "text": "Verified capacity is 42."}]
REPORT = "# Report\r\n\r\n## Capacity\r\n\r\nOld capacity [S1].\r\n\r\n## Risks\r\n\r\nUnchanged [S1].\r\n"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    reset_compaction_stop()
    for name in ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    yield
    reset_compaction_stop()


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    for relative in (
        "deerflow_bridge/research_synthesis.py", "deerflow_bridge/research_quality.py",
        "deerflow_bridge/research_context.py", "deerflow_bridge/research_profiles.py",
        "deerflow_bridge/research_workspace.py",
        "backend/app/services/research_quality_gate.py", "backend/tests/test_agentic_synthesis_hardening.py",
    ):
        record_testsuite_property("source_sha256:" + relative, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest())


def advice(report=REPORT):
    return {"report_sha256": hashlib.sha256(report.encode()).hexdigest(), "reviews": [
        {"status": "FAIL", "weaknesses": [{"section_heading": "Capacity", "weakness": "Verify capacity"}]},
    ]}


def passing(report, sources):
    return {"passed": True, "errors": []}


def pinned_workspace(tmp_path, retrieval=128_000):
    policy = ContextPolicy(context_window_tokens=1_000_000, working_tokens=500_000,
                           retrieval_tokens=retrieval, reserved_output_tokens=64_000)
    workspace = ResearchWorkspace(tmp_path / "synthesis", {"model": "glm-5.3", "context_policy": policy.to_dict()})
    return workspace, policy


@pytest.mark.parametrize("retrieval", [2200, 128_000])
def test_advisory_uses_exact_pinned_policy_and_replays_after_env_change(tmp_path, monkeypatch, retrieval):
    workspace, policy = pinned_workspace(tmp_path, retrieval)
    report = REPORT + "\n\n" + ("Detailed source evidence and capacity 42. " * 500)
    calls = []

    def invoke(task):
        calls.append(task)
        assert estimate_tokens(task["system"] + task["evidence"]) <= retrieval
        return {"status": "FAIL", "weaknesses": []}

    result = synthesis.advisory_reviews(workspace, report, SOURCES, invoke)
    assert result["scope"]["policy"] == policy.to_dict()
    assert len(calls) == 5
    if retrieval > 8000:
        assert max(estimate_tokens(task["evidence"]) for task in calls) > 8000
    monkeypatch.setenv("RESEARCH_AGENTIC_RETRIEVAL_TOKENS", "invalid-new-environment")
    reopened = ResearchWorkspace(workspace.root, workspace.identity)
    replay = synthesis.advisory_reviews(reopened, report, SOURCES, lambda task: pytest.fail("pinned review replayed"))
    assert replay == result


def test_large_pinned_repair_keeps_complete_section_and_exact_cache(tmp_path, monkeypatch):
    workspace, policy = pinned_workspace(tmp_path)
    old = "Verified context 中文 [S1]. " * 650 + "TAIL must remain available."
    report = REPORT.replace("Old capacity [S1].", old)
    tasks = []

    def invoke(task):
        tasks.append(task)
        payload = json.loads(task["evidence"])
        assert old in payload["original_section"]
        assert 8000 < estimate_tokens(task["system"] + task["evidence"]) <= policy.retrieval_tokens
        return {"section_heading": "Capacity", "replacement": "Verified capacity is 42 [S1]."}

    result = synthesis.targeted_repairs(workspace, report, SOURCES, advice(report), invoke, passing)
    assert result["report"] == report.replace(old, "Verified capacity is 42 [S1].")
    assert result["repairs"][0]["status"] == "applied" and len(tasks) == 1
    monkeypatch.setenv("RESEARCH_AGENTIC_RETRIEVAL_TOKENS", "2200")
    replay = synthesis.targeted_repairs(workspace, report, SOURCES, advice(report),
        lambda task: pytest.fail("pinned repair replayed"), passing)
    assert replay == result


def test_default_glm_policy_reaches_advisory_and_targeted_repair(tmp_path):
    policy = ContextPolicy.from_env(model_name="glm-5.3")
    assert policy.context_window_tokens == 1_048_576
    assert policy.retrieval_tokens == 32_768
    workspace = ResearchWorkspace(tmp_path / "synthesis", {"model": "glm-5.3", "context_policy": policy.to_dict()})
    old = "Documented capacity 中文 [S1]. " * 400 + "TAIL evidence."
    report = REPORT.replace("Old capacity [S1].", old)
    seen = []

    def invoke(task):
        seen.append(task)
        assert estimate_tokens(task["system"] + task["evidence"]) <= policy.retrieval_tokens
        if task["label"].startswith("section-repair-"):
            assert old in json.loads(task["evidence"])["original_section"]
            return {"section_heading": "Capacity", "replacement": "Verified capacity 42 [S1]."}
        return {"status": "FAIL", "weaknesses": [{"section_heading": "Capacity", "weakness": "Verify capacity"}]}

    advisory = synthesis.advisory_reviews(workspace, report, SOURCES, invoke)
    result = synthesis.targeted_repairs(workspace, report, SOURCES, advisory, invoke, passing)
    assert result["report"] == report.replace(old, "Verified capacity 42 [S1].")
    assert len(seen) == 6 and result["repairs"][0]["status"] == "applied"
    assert all(row["inputs"]["inputs"]["policy"] == policy.to_dict()
               for row in workspace.snapshot()["tasks"] if row["task_id"].startswith("raw-completion-"))


@pytest.mark.parametrize("stage", ["baseline", "proposal"])
def test_repair_validator_workspace_corruption_is_a_control_failure(tmp_path, stage):
    workspace = ResearchWorkspace(tmp_path / "synthesis", {"model": "offline"})
    calls = []
    validations = []

    def invoke(task):
        calls.append(task)
        return {"section_heading": "Capacity", "replacement": "Verified capacity 42 [S1]."}

    def validate(report, sources):
        validations.append(report)
        if stage == "baseline" or "Verified capacity" in report:
            workspace.read_artifact({})  # Real storage error, including on proposal-cache replay.
        return passing(report, sources)

    with pytest.raises(ResearchWorkspaceError):
        synthesis.targeted_repairs(workspace, REPORT, SOURCES, advice(), invoke, validate)
    assert len(calls) == (stage == "proposal")
    if stage == "proposal":
        with pytest.raises(ResearchWorkspaceError):
            synthesis.targeted_repairs(workspace, REPORT, SOURCES, advice(),
                lambda task: pytest.fail("successful raw proposal must be reused"), validate)


class SliceCountedText(str):
    """Measure copied input characters deterministically, without wall-clock gates."""
    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.copied_chars = 0
        return result

    def __getitem__(self, key):
        result = super().__getitem__(key)
        if isinstance(key, slice):
            self.copied_chars += len(result)
        return result


@pytest.mark.parametrize("citation", ["[S1]", "[S1](https://example.org/release)", "[S1][release]"])
def test_large_report_citation_checks_do_not_copy_every_remaining_suffix(citation, record_testsuite_property):
    report = SliceCountedText(("An observed change remains uncertain. " * 20 + citation + ".\n\n") * 1200
                             + "\n[release]: https://example.org/release\n")
    errors, warnings = [], []
    quality._check_sources(SOURCES, report, errors, warnings)
    assert not errors
    record_testsuite_property("copy_volume:" + citation + ":report_chars", len(report))
    record_testsuite_property("copy_volume:" + citation + ":copied_input_chars", report.copied_chars)
    assert report.copied_chars <= len(report) * 2
    # The same input must pass the actual producer and receipt-replay boundary.
    receipt = quality.make_receipt(str(report), SOURCES, advisory={"verdict": "FAIL"})
    assert quality.validate_receipt(receipt, str(report), SOURCES) == []


def test_large_report_tail_link_and_exact_bytes_replayed_by_backend(tmp_path):
    spec = importlib.util.spec_from_file_location("synthesis_hardening_quality_gate",
        ROOT / "backend/app/services/research_quality_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    report = "# Findings\r\n\r\n" + ("Observed capacity 中文 remains uncertain [S1].\r\n\r\n" * 20_000)
    report += "Final evidence [S1](https://example.org/release).\r\n"

    def publish(text):
        receipt = quality.make_receipt(text, SOURCES, advisory={"verdict": "FAIL", "scores": {"all": 0}})
        payloads = {
            "meta.json": {"research_engine": "agentic-phases/v1", "quality_policy": quality.POLICY},
            "sources.json": SOURCES,
            "research_quality.json": receipt,
        }
        artifacts = {name: json.dumps(value, ensure_ascii=False).encode() for name, value in payloads.items()}
        artifacts["research_report.md"] = text.encode()
        for name, raw in artifacts.items():
            (tmp_path / name).write_bytes(raw)
        entries = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in artifacts.items()}
        (tmp_path / "research_contract_manifest.json").write_text(json.dumps({"version": 1, "files": entries}))
        return entries

    entries = publish(report)
    assert gate.quality_contract_errors(str(tmp_path), entries, report) == []
    assert gate.quality_errors(str(tmp_path)) == []
    assert "research_quality_report_input_mismatch" in gate.quality_contract_errors(str(tmp_path), entries, report.replace("\r\n", "\n"))
    altered = report.replace("[S1](https://example.org/release)", "[S1](https://example.org/wrong)")
    publish(altered)
    assert "citation_url_mismatch:S1" in gate.quality_errors(str(tmp_path))


@pytest.mark.parametrize("underline", ["-", "--", "---", "=", "==="])
def test_repair_rejects_setext_headings_with_any_valid_underline(tmp_path, underline):
    workspace = ResearchWorkspace(tmp_path / "synthesis", {"model": "offline"})
    result = synthesis.targeted_repairs(workspace, REPORT, SOURCES, advice(),
        lambda task: {"section_heading": "Capacity", "replacement": f"Injected section\n{underline}\nNew body [S1]."},
        passing)
    assert result["report"] == REPORT
    assert result["repairs"][0]["reason"] == "invalid_replacement"
