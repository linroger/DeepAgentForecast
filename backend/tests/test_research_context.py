"""Offline acceptance checks for durable, bounded research working views."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"
sys.path.insert(0, str(BRIDGE))

import research_context as rc  # noqa: E402


class DiskWorkspace:
    """Minimal durable artifact protocol; no process-local artifact cache."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)

    def put_artifact(self, text, kind):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        ref = {"id": digest, "sha256": digest, "bytes": len(text.encode("utf-8")), "path": f"blobs/{digest}.txt"}
        (self.root / digest).write_text(text, encoding="utf-8")
        return ref

    def read_artifact(self, ref):
        return (self.root / ref["id"]).read_bytes().decode("utf-8")


@pytest.fixture(autouse=True)
def clean_policy_env(monkeypatch):
    for name in rc.ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)


def block(text, identity="artifact-1", kind="fetched_source", **extra):
    return {"id": identity, "text": text, "kind": kind, **extra}


def test_default_envelope_and_estimation_are_explicit():
    policy = rc.ContextPolicy.from_env()
    assert policy.working_tokens == 64_000
    assert policy.context_window_tokens == 128_000
    assert policy.working_tokens + policy.reserved_output_tokens + policy.prompt_overhead_tokens + policy.safety_margin_tokens <= policy.context_window_tokens
    view = policy.select([block("产能 📊 42 in 2026")], "产能")
    assert view["estimated_tokens"] == len(view["text"].encode("utf-8"))
    assert "UTF-8" in view["estimation_basis"]
    assert "tokenizer" in view["estimation_basis"]


def test_stable_policy_identity_is_json_safe_and_changes_with_settings():
    policy = rc.ContextPolicy()
    assert json.loads(json.dumps(policy.to_dict(), sort_keys=True)) == policy.to_dict()
    assert policy.to_dict() == rc.ContextPolicy.from_env().to_dict()
    assert policy.to_dict() != rc.ContextPolicy(working_tokens=63_000).to_dict()
    copy_of_identity = policy.to_dict()
    copy_of_identity["working_tokens"] = 1
    assert policy.working_tokens == 64_000


@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.2", "", "true", "1_024"])
def test_invalid_environment_fails_closed(monkeypatch, value):
    monkeypatch.setenv("RESEARCH_AGENTIC_WORKING_TOKENS", value)
    with pytest.raises(ValueError, match="RESEARCH_AGENTIC_WORKING_TOKENS"):
        rc.ContextPolicy.from_env()


def test_environment_uses_only_agentic_settings_and_validates_margins(monkeypatch):
    monkeypatch.setenv("RESEARCH_LINEAR_CONVERSATION_CHAR_CAP", "12")
    assert rc.ContextPolicy.from_env().working_tokens == 64_000
    monkeypatch.setenv("RESEARCH_AGENTIC_CONTEXT_WINDOW_TOKENS", "64000")
    with pytest.raises(ValueError, match="window"):
        rc.ContextPolicy.from_env()
    monkeypatch.setenv("RESEARCH_AGENTIC_WORKING_TOKENS", "24000")
    assert rc.ContextPolicy.from_env().working_tokens == 24_000


@pytest.mark.parametrize("settings", [
    {"working_tokens": True}, {"reserved_output_tokens": 0},
    {"prompt_overhead_tokens": -1}, {"safety_margin_tokens": 1.5},
    {"retrieval_tokens": 65_000}, {"paragraph_tokens": 3},
])
def test_direct_constructor_also_validates_every_envelope(settings):
    with pytest.raises(ValueError):
        rc.ContextPolicy(**settings)


def test_all_environment_fields_are_read_and_identity_pins_them(monkeypatch):
    values = {"context_window_tokens": 32_000, "working_tokens": 16_000,
              "reserved_output_tokens": 4_000, "prompt_overhead_tokens": 4_000,
              "safety_margin_tokens": 2_000, "retrieval_tokens": 2_000,
              "paragraph_tokens": 512}
    for env, field in rc.ContextPolicy.ENV_FIELDS.items():
        monkeypatch.setenv(env, str(values[field]))
    policy = rc.ContextPolicy.from_env()
    assert all(policy.to_dict()[field] == value for field, value in values.items())
    view = policy.select([block("知识 2026\n\n" * 10000)], "知识")
    assert view["estimated_tokens"] <= view["budget_tokens"] == 16_000


def test_selects_relevant_tail_and_contradiction_without_mutating_inputs():
    text = "\n\n".join(["Unrelated background and introductory narrative."] * 240)
    text += "\n\nCapacity forecast was 12 GW in 2025."
    text += "\n\nHowever, capacity was NOT 12 GW: revised to 7.5 GW on 2026-09-18. TAIL_SENTINEL."
    blocks = [block(text, source_url="https://example.test/filing")]
    original = copy.deepcopy(blocks)
    view = rc.ContextPolicy().select(blocks, "capacity forecast revised", budget_tokens=1100)
    assert "TAIL_SENTINEL" in view["text"]
    assert "12 GW in 2025" in view["text"]
    assert "7.5 GW on 2026-09-18" in view["text"]
    assert "https://example.test/filing" in view["text"]
    assert view["omitted_refs"]
    assert "Omitted" in view["text"]
    assert blocks == original
    for selected in view["selected_refs"]:
        assert text[selected["start"]:selected["end"]] in view["text"]
        assert selected["ref"] == "artifact-1"


def test_single_huge_paragraph_tail_is_considered():
    text = "Background noise. " * 4000 + "Critical cobalt deficit 2027 is 39 tonnes LONGTAIL."
    view = rc.ContextPolicy().select([block(text)], "cobalt deficit", budget_tokens=2600)
    assert "LONGTAIL" in view["text"]
    assert view["estimated_tokens"] <= 2600


def test_cjk_query_finds_non_whitespace_tail():
    text = "无关信息。" * 3000 + "\n\n产能修订：2026年实际仅为七点五，存在矛盾。尾部证据。📊"
    view = rc.ContextPolicy().select([block(text)], "实际产能修订", budget_tokens=1100)
    assert "尾部证据" in view["text"]
    assert "📊" in view["text"]
    assert view["estimated_tokens"] <= 1100


def test_single_character_cjk_query_matches_inside_longer_word(tmp_path):
    text = "Unrelated introduction.\n\n" * 600 + "铜供应严重短缺。独有证据。"
    policy = rc.ContextPolicy()
    view = policy.select([block(text)], "铜", budget_tokens=1000)
    assert "铜供应严重短缺" in view["text"]
    workspace = DiskWorkspace(tmp_path)
    archived = policy.archive_result(workspace, text, kind="tool_result", query="铜")
    window = policy.read_evidence(workspace, archived["ref"], query="铜")
    assert window["excerpt"] == "铜供应严重短缺。独有证据。"


def test_fitting_complete_document_does_not_reserve_unneeded_omission_notice():
    text = "a" * 325
    view = rc.ContextPolicy().select([block(text)], "", budget_tokens=512)
    assert text in view["text"]
    assert view["omitted_refs"] == []
    assert view["estimated_tokens"] <= 512


@pytest.mark.parametrize("budget", [512, 777, 1024, 2048, 8192])
def test_budget_is_deterministic_including_labels_and_omission_notice(budget):
    blocks = [block(("证据📈 capacity 37.5% contradicts 2026.\n\n" * 130), f"ref-{i}") for i in range(4)]
    policy = rc.ContextPolicy()
    first = policy.select(blocks, "capacity", budget_tokens=budget)
    assert first == policy.select(blocks, "capacity", budget_tokens=budget)
    assert first["estimated_tokens"] == len(first["text"].encode("utf-8")) <= budget
    assert first["budget_tokens"] == budget
    for item in first["omitted_refs"]:
        assert item["ref"].startswith("ref-")
        assert 0 <= item["start"] < item["end"]


def test_derived_summary_is_not_upgraded_to_fetched_evidence():
    view = rc.ContextPolicy().select([block("Revenue was 42", kind="derived_summary")], "revenue")
    assert "derived_summary" in view["text"]
    assert "not fetched evidence" in view["text"]
    assert "fetched_source" not in view["text"]


@pytest.mark.parametrize("budget", [0, -1, 1, True, 4.5, 200_000])
def test_invalid_or_impossible_budget_is_rejected(budget):
    with pytest.raises(ValueError):
        rc.ContextPolicy().select([block("evidence")], "", budget_tokens=budget)


def test_ambiguous_duplicate_refs_and_invalid_blocks_are_rejected():
    policy = rc.ContextPolicy()
    with pytest.raises(ValueError, match="duplicate"):
        policy.select([block("one"), block("two")], "")
    with pytest.raises(ValueError):
        policy.select([{"id": "a", "text": "missing kind"}], "")


def test_archive_failure_propagates_before_select(monkeypatch):
    class FailingWorkspace:
        def put_artifact(self, *args, **kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(rc.ContextPolicy, "select", lambda *args, **kwargs: pytest.fail("selection before durable archive"))
    with pytest.raises(OSError, match="disk full"):
        rc.ContextPolicy().archive_result(FailingWorkspace(), "full evidence", kind="tool_result", query="")


def test_original_survives_view_failure_after_archive(monkeypatch, tmp_path):
    workspace = DiskWorkspace(tmp_path)
    original = "Exact\r\n原始📚"

    def fail_select(*args, **kwargs):
        raise ValueError("view failed")

    monkeypatch.setattr(rc.ContextPolicy, "select", fail_select)
    with pytest.raises(ValueError, match="view failed"):
        rc.ContextPolicy().archive_result(workspace, original, kind="tool_result", query="")
    assert (tmp_path / hashlib.sha256(original.encode()).hexdigest()).read_bytes() == original.encode()


def test_archive_preserves_exact_fulltext_and_returns_usable_refs(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    text = "原始证据\r\n\n" + "background " * 14000 + "\n\nTAIL deficit 29 in 2027."
    archived = rc.ContextPolicy().archive_result(workspace, text, kind="fetched_source", query="deficit")
    assert (tmp_path / archived["ref"]["id"]).read_bytes() == text.encode("utf-8")
    assert archived["view"]["omitted_refs"]
    assert "TAIL" in archived["view"]["text"]
    assert archived["view"]["estimated_tokens"] <= 64_000
    assert archived["view"]["omitted_refs"][0]["ref"] == archived["ref"]


def test_read_windows_round_trip_unicode_after_restart(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    text = "开头📚\r\n\n" + "证据🙂 e\u0301 2026\n" * 400 + "END"
    archived = rc.archive_result(workspace, text, kind="tool_result", query="")
    # A new process has no access to the original workspace object or view.
    script = '''
import json, sys
from pathlib import Path
from deerflow_bridge.research_context import read_evidence
class Workspace:
    def read_artifact(self, ref):
        return (Path(sys.argv[1]) / ref["id"]).read_bytes().decode("utf-8")
ref = json.loads(sys.stdin.read())
offset, pieces = 0, []
while True:
    result = read_evidence(Workspace(), ref, offset=offset, limit=333)
    assert result["estimated_tokens"] <= result["budget_tokens"]
    pieces.append(result["excerpt"])
    if result["next_offset"] is None:
        break
    assert result["next_offset"] > offset
    offset = result["next_offset"]
print(json.dumps("".join(pieces), ensure_ascii=False))
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], input=json.dumps(archived["ref"]), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == text


def test_query_retrieval_finds_tail_and_labels_skipped_ranges(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    text = "Unrelated introduction.\n\n" * 5000 + "产能下降至7.5。2026-09-18 TAIL_RETRIEVAL."
    result = rc.archive_result(workspace, text, kind="fetched_source", query="产能")
    view = rc.read_evidence(DiskWorkspace(tmp_path), result["ref"], query="产能下降")
    assert "TAIL_RETRIEVAL" in view["text"]
    assert view["offset"] == text.index("产能")
    assert view["excerpt"] == text[view["offset"]:view["end_offset"]]
    assert view["omitted_refs"][0]["start"] == 0
    assert view["omitted_refs"][0]["end"] == view["offset"]
    assert view["estimated_tokens"] <= view["budget_tokens"]


def test_query_retrieval_respects_explicit_range_and_unmatched_falls_back(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    text = "irrelevant beginning\n\n" * 100 + "tail needle"
    result = rc.archive_result(workspace, text, kind="tool_result", query="needle")
    window = rc.read_evidence(workspace, result["ref"], query="needle", offset=5, limit=80)
    assert window["offset"] == 5
    assert window["excerpt"] == text[5:85]
    assert window["next_offset"] == 85


def test_read_verifies_reference_and_rejects_tampered_artifact(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    archived = rc.archive_result(workspace, "original", kind="tool_result", query="")
    ref = archived["ref"]
    (tmp_path / ref["id"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        rc.read_evidence(workspace, ref)
    with pytest.raises(ValueError, match="ref"):
        rc.read_evidence(workspace, {"id": "../../outside"})


def test_real_workspace_archive_and_retrieval_survive_new_instance(tmp_path):
    from research_workspace import ResearchWorkspace
    identity = {"run_id": "context-check", "lane": "evidence", "question": "capacity", "model": "offline", "language": "en", "policy": rc.ContextPolicy().to_dict()}
    root = tmp_path / "real-workspace"
    workspace = ResearchWorkspace(root, identity)
    text = "来源📊\r\n" + "background\n\n" * 900 + "\n\nCapacity revised to 7.5 GW on 2026-09-18. TAIL"
    result = rc.archive_result(workspace, text, kind="fetched_source", query="capacity")
    assert workspace.read_artifact(result["ref"]) == text
    restarted = ResearchWorkspace(root, identity)
    start = text.index("Capacity")
    view = rc.read_evidence(restarted, result["ref"], offset=start)
    assert view["excerpt"] == text[start:]
    assert view["next_offset"] is None
    assert view["selected_refs"][0]["ref"] == result["ref"]
    assert "not fetched evidence" in view["text"]


def test_real_workspace_rejects_reference_from_other_run(tmp_path):
    from research_workspace import ResearchWorkspace, ResearchWorkspaceError
    first = ResearchWorkspace(tmp_path / "first", {"question": "one", "model": "offline"})
    second = ResearchWorkspace(tmp_path / "second", {"question": "two", "model": "offline"})
    result = rc.archive_result(first, "run-bound evidence", kind="tool_result", query="")
    with pytest.raises(ResearchWorkspaceError):
        rc.read_evidence(second, result["ref"])


def test_empty_artifact_and_exact_end_window_terminate(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    archived = rc.archive_result(workspace, "", kind="tool_result", query="")
    view = rc.read_evidence(workspace, archived["ref"])
    assert view["excerpt"] == "" and view["next_offset"] is None
    assert view["selected_refs"] == [] and view["omitted_refs"] == []
    archived = rc.archive_result(workspace, "🧪", kind="tool_result", query="")
    view = rc.read_evidence(workspace, archived["ref"], offset=1)
    assert view["excerpt"] == "" and view["next_offset"] is None


def test_each_returned_and_omitted_range_partitions_original():
    text = "产能下降 2026年。\n\n" * 160 + "尾部 final revised 42"
    view = rc.ContextPolicy().select([block(text)], "revised", budget_tokens=1500)
    ranges = sorted((r["start"], r["end"]) for r in view["selected_refs"] + view["omitted_refs"])
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False))
    assert "".join(text[start:end] for start, end in ranges) == text


def test_prompt_only_view_includes_actionable_omitted_reference():
    # Workers inject text, not the unbounded control-plane reference arrays.
    view = rc.ContextPolicy().select([block("background " * 4000, identity="omitted-document")], "missing", budget_tokens=512)
    omitted = view["omitted_refs"][0]
    assert f'"{omitted["ref"]}" chars={omitted["start"]}:{omitted["end"]}' in view["text"]
    assert view["estimated_tokens"] <= 512


def test_retrieval_does_not_trust_unbound_kind_in_reference(tmp_path):
    workspace = DiskWorkspace(tmp_path)
    result = rc.archive_result(workspace, "a model claim", kind="derived_summary", query="")
    view = rc.read_evidence(workspace, {**result["ref"], "kind": "fetched_source"})
    assert "fetched_source" not in view["text"]
    assert "not fetched evidence" in view["text"]


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": -1}, {"limit": 2.5}, {"offset": 100}])
def test_invalid_retrieval_window_is_rejected(tmp_path, kwargs):
    workspace = DiskWorkspace(tmp_path)
    archived = rc.archive_result(workspace, "evidence", kind="tool_result", query="")
    with pytest.raises(ValueError):
        rc.read_evidence(workspace, archived["ref"], **kwargs)
