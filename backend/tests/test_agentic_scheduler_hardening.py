"""Offline regressions for cancellation fences and large scheduler contexts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow_bridge import agentic_research as scheduler
from deerflow_bridge.research_context import ContextPolicy
from deerflow_bridge.research_workspace import ResearchWorkspace, ResearchWorkspaceError


QUESTION = "Which water permits constrain chip capacity by 2030?"


@pytest.fixture(scope="module", autouse=True)
def source_receipts(record_testsuite_property):
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "deerflow_bridge/agentic_research.py", "deerflow_bridge/research_workspace.py",
        "deerflow_bridge/research_context.py", "deerflow_bridge/research_invocation.py",
        "backend/tests/test_agentic_scheduler_hardening.py",
    ):
        record_testsuite_property("scheduler_hardening_sha256:" + relative,
                                 hashlib.sha256((root / relative).read_bytes()).hexdigest())


@pytest.fixture
def workspace(tmp_path):
    return ResearchWorkspace(tmp_path / "workspace", {"question": QUESTION, "model": "offline"})


def result(task):
    return {"text": "Notes for " + task["id"], "evidence": ["Original evidence"],
            "sources": [{"url": "https://example.test/permit", "content": "Permit expires 2029"}],
            "discoveries": ["Which permit expires first?"]}


def run(workspace, worker, **kwargs):
    return scheduler.run_research(workspace, question=QUESTION, depth="deep", language="en",
                                  context_policy=kwargs.pop("context_policy", ContextPolicy()),
                                  worker=worker, **kwargs)


def wait_for_lease(workspace):
    deadline = time.monotonic() + 5
    while True:
        try:
            with workspace.execution_lock():
                return
        except ResearchWorkspaceError:
            assert time.monotonic() < deadline, "abandoned callback did not release execution lease"
            time.sleep(0.005)


@pytest.mark.parametrize("blocked_kind", ["worker_result", "evidence", "source"])
def test_timeout_stops_persistence_after_inflight_write(workspace, monkeypatch, blocked_kind):
    entered, release = threading.Event(), threading.Event()
    writes = []
    put = workspace.put_artifact

    def blocking_put(text, kind):
        writes.append(kind)
        if kind == blocked_kind:
            entered.set()
            assert release.wait(5)
        return put(text, kind)

    monkeypatch.setattr(workspace, "put_artifact", blocking_put)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, workspace, lambda task, context, emit: result(task),
                               workers=1, phase_deadline_s=0.3)
        try:
            assert entered.wait(5)
            with pytest.raises(scheduler.AgenticResearchHalt, match="phase_deadline"):
                future.result(timeout=3)
            at_halt = writes[:]
        finally:
            release.set()
    wait_for_lease(workspace)
    assert writes == at_halt, "a halted worker began another artifact write"
    assert workspace.discoveries() == [], "a halted worker admitted a new discovery"
    assert workspace.events_by_kind("task_done") == []
    assert workspace.events_by_kind("research_complete") == []


def test_timeout_stops_discovery_delivery_after_inflight_archive(workspace, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    observed = []
    add = workspace.add_discovery

    def blocking_add(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return add(*args, **kwargs)

    monkeypatch.setattr(workspace, "add_discovery", blocking_add)

    def worker(task, context, emit):
        emit("discovery", {"question": "A gap observed before timeout"})
        return result(task)

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, workspace, worker, workers=1, phase_deadline_s=0.3,
                               emit=lambda kind, payload: observed.append(kind))
        try:
            assert entered.wait(5)
            with pytest.raises(scheduler.AgenticResearchHalt, match="phase_deadline"):
                future.result(timeout=3)
        finally:
            release.set()
    wait_for_lease(workspace)
    # An in-flight storage operation may finish. It must not start a subsequent
    # delivery or event write after cancellation was already observed.
    assert len(workspace.discoveries()) == 1
    assert "discovery" not in observed
    assert workspace.events_by_kind("discovery") == []
    assert workspace.events_by_kind("task_done") == []


def test_timed_out_callback_does_not_copy_its_late_result(workspace, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    validated = []
    validate = scheduler._validate_result

    def track_validation(value):
        validated.append(value)
        return validate(value)

    def worker(task, context, emit):
        entered.set()
        assert release.wait(5)
        return result(task)

    monkeypatch.setattr(scheduler, "_validate_result", track_validation)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, workspace, worker, workers=1, phase_deadline_s=0.3)
        try:
            assert entered.wait(5)
            with pytest.raises(scheduler.AgenticResearchHalt, match="phase_deadline"):
                future.result(timeout=3)
        finally:
            release.set()
    wait_for_lease(workspace)
    assert validated == [], "cancelled result was needlessly validated and serialized"
    assert workspace.discoveries() == []


def test_five_workers_close_writes_after_sibling_failure(workspace, monkeypatch):
    gate = threading.Barrier(5, timeout=5)
    entered, release = threading.Event(), threading.Event()
    put = workspace.put_artifact
    writes, calls = [], []

    def blocking_put(text, kind):
        writes.append(kind)
        if kind == "worker_result":
            entered.set()
            assert release.wait(5)
        return put(text, kind)

    def worker(task, context, emit):
        calls.append(task["id"])
        gate.wait()
        if task["focus"] == "primary-evidence":
            assert entered.wait(5)
            raise RuntimeError("failed sibling")
        if task["focus"] != "scope":
            assert release.wait(5)
        return result(task)

    monkeypatch.setattr(workspace, "put_artifact", blocking_put)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(run, workspace, worker, workers=5)
        try:
            assert entered.wait(5)
            with pytest.raises(scheduler.AgenticResearchHalt, match="worker_failed"):
                future.result(timeout=3)
            assert len(calls) == 5
            at_halt = writes[:]
            with pytest.raises(ResearchWorkspaceError):
                with workspace.execution_lock():
                    pytest.fail("still-running callbacks must retain the lease")
        finally:
            release.set()
    wait_for_lease(workspace)
    assert writes == at_halt
    assert workspace.discoveries() == []
    assert workspace.events_by_kind("task_done") == []
    assert workspace.events_by_kind("research_complete") == []


def test_expired_pending_lookup_stops_further_admissions(workspace, monkeypatch):
    original = workspace.load_task
    calls = []

    def slow_load(task_id, inputs):
        if task_id.startswith("ar-"):
            calls.append(task_id)
            if len(calls) == 1:
                time.sleep(0.2)
        return original(task_id, inputs)

    monkeypatch.setattr(workspace, "load_task", slow_load)
    with pytest.raises(scheduler.AgenticResearchHalt, match="phase_deadline"):
        run(workspace, lambda *args: pytest.fail("expired task must not run"), phase_deadline_s=0.1)
    assert len(calls) == 1
    admissions = [task for task in workspace.snapshot()["tasks"] if task["task_id"].startswith("ar-")]
    assert len(admissions) == 1


def test_followup_receives_frozen_discovery_evidence_without_source_upgrade(workspace):
    body = "原始许可证材料\r\nDeadline: 2029-12-31; limit 42 units."
    reference = workspace.put_artifact(body, "tool_result")
    gap = "Which permit expires first?"
    followups = []

    def worker(task, context, emit):
        if task["phase"] == "scope" and task["focus"] == "scope":
            emit("discovery", {"question": gap, "evidence_refs": [reference]})
        if task["question"] == gap:
            followups.append((task, context))
        value = result(task)
        value.update(discoveries=[], sources=[], evidence=[])
        return value

    output = run(workspace, worker, workers=1)
    assert len(followups) == 5
    for task, context in followups:
        assert reference in task["context_refs"]
        assert body in context
        assert '"kind":"discovery_evidence"' in context
        assert "derived or unverified; not fetched evidence" in context
    assert reference in output["evidence_refs"]
    assert output["sources"] == []
    discovery = output["discoveries"][0]
    assert discovery["question"] == gap
    assert discovery["evidence_refs"] == [reference]
    resumed = run(workspace, lambda *args: pytest.fail("completed discovery must be reused"), workers=1)
    assert resumed["evidence_refs"] == output["evidence_refs"]
    (workspace.root / reference["path"]).write_text(body + "tampered", encoding="utf-8")
    with pytest.raises(scheduler.AgenticResearchHalt, match="identity_or_workspace"):
        run(workspace, lambda *args: pytest.fail("tampered discovery must not enter native code"))


def test_new_round_deduplicates_case_and_whitespace_but_preserves_observations(workspace):
    variants = ["Which water permit expires?", "which   water permit expires?"]
    # Select a separate gap that sorts after both variants under the existing
    # deterministic hash order, so duplicates previously exhausted the cap.
    maximum = max(hashlib.sha256(" ".join(q.split()).encode()).hexdigest() for q in variants)
    distinct = next(q for i in range(1000) if
                    hashlib.sha256((q := f"What is the independent capacity constraint {i}?").encode()).hexdigest() > maximum)
    investigations = []

    def worker(task, context, emit):
        value = result(task)
        value["discoveries"] = variants + [distinct] if task["phase"] == "scope" else []
        if task["phase"].startswith("followup-"):
            investigations.append(task["question"])
        return value

    output = run(workspace, worker, workers=1, max_followups=2)
    normalized = {" ".join(question.split()).casefold() for question in investigations}
    assert normalized == {variants[0].casefold(), distinct.casefold()}
    assert len(investigations) == 10
    assert {d["question"] for d in output["discoveries"]} == set(variants + [distinct])
    assert all(d["evidence_refs"] for d in output["discoveries"])
    assert len(workspace.events_by_kind("discovery_observed")) == 15
    resumed = run(workspace, lambda *args: pytest.fail("completed rounds must not be readmitted"),
                  workers=1, max_followups=2)
    assert resumed["discoveries"] == output["discoveries"]
    assert resumed["stats"]["reused_tasks"] == 35


def test_phase_selection_shares_immutable_text_but_detaches_containers(workspace, monkeypatch):
    original_blocks = scheduler._Run._context_blocks
    originals = {}
    seen = []

    def capture_blocks(state):
        blocks = original_blocks(state)
        originals.clear()
        originals.update((block["id"], block["text"]) for block in blocks)
        return blocks

    class InspectPolicy:
        def to_dict(self):
            return ContextPolicy().to_dict()

        def select(self, blocks, query):
            assert len(blocks) == len(originals)
            for block in blocks:
                assert block["text"] is originals[block["id"]], "full evidence string was duplicated"
                assert block["kind"] != "mutated"
                seen.append(block["id"])
            selection = ContextPolicy().select(blocks, query)
            for block in blocks:
                block["kind"] = "mutated"
                block["text"] = "mutated"
            blocks.clear()
            return selection

    monkeypatch.setattr(scheduler._Run, "_context_blocks", capture_blocks)

    def worker(task, context, emit):
        value = result(task)
        value["discoveries"] = []
        value["evidence"] = ["许可证原始材料\r\n" * 1000 + "2030年尾部数据 42"]
        return value

    output = run(workspace, worker, context_policy=InspectPolicy())
    assert output["stats"]["completed_tasks"] == 25
    assert seen


def test_large_pinned_policy_five_workers_and_verified_full_reuse(workspace, monkeypatch):
    policy = ContextPolicy(context_window_tokens=1_000_000, working_tokens=262_144,
                           retrieval_tokens=32_768)
    barrier = threading.Barrier(5, timeout=5)
    long_evidence = "水资源许可证约束2030年。\r\n" * 6000 + "\r\nTail evidence: 42 units in 2030."
    contexts, calls = [], []

    def worker(task, context, emit):
        calls.append(task["id"])
        contexts.append(context)
        barrier.wait()
        value = result(task)
        value["evidence"] = [long_evidence]
        value["discoveries"] = []
        return value

    first = run(workspace, worker, context_policy=policy)
    assert len(calls) == 25
    assert max(len(context.encode("utf-8")) for context in contexts) > 64_000
    assert all(len(context.encode("utf-8")) <= 262_144 for context in contexts)
    assert first["evidence"] == [long_evidence] * 25
    snapshot = workspace.snapshot()
    expected_refs = {ref["id"] for ref in snapshot["artifacts"]}
    context_refs = {item["task"]["context_ref"]["id"] for task in snapshot["tasks"]
                    if task["task_id"].startswith("agentic-plan-") for item in task["result"]["tasks"]}
    verified, context_reads = set(), []
    verify, read = workspace._artifact, workspace.read_artifact

    def track_verification(conn, ref):
        verified.add(ref["id"])
        return verify(conn, ref)

    def track_read(ref):
        if ref["id"] in context_refs:
            context_reads.append(ref["id"])
        return read(ref)

    def forbidden(*args, **kwargs):
        pytest.fail("full reuse must not select context or call native workers")

    monkeypatch.setattr(workspace, "_artifact", track_verification)
    monkeypatch.setattr(workspace, "read_artifact", track_read)
    monkeypatch.setattr(ContextPolicy, "select", forbidden)
    resumed = run(workspace, forbidden, context_policy=policy)
    assert resumed["stats"]["reused_tasks"] == 25
    assert resumed["stats"]["executed_tasks"] == 0
    assert expected_refs <= verified, "full reuse omitted artifact integrity verification"
    assert context_reads == [], "verified cached contexts were needlessly materialized again"
    for key in ("text", "evidence", "sources", "evidence_refs", "discoveries"):
        assert resumed[key] == first[key]
    # A corrupt tail remains fatal even when no prompt/native work is needed.
    tail_ref = next(ref for ref in first["evidence_refs"] if read(ref) == long_evidence)
    (workspace.root / tail_ref["path"]).write_text(long_evidence + "corruption", encoding="utf-8")
    with pytest.raises(scheduler.AgenticResearchHalt, match="identity_or_workspace"):
        run(workspace, forbidden, context_policy=policy)
