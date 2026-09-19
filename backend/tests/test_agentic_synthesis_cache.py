"""Offline raw-completion durability and bounded advisory review acceptance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import threading
import time

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"
sys.path.insert(0, str(BRIDGE))
import research_synthesis as synthesis
from research_compaction import ResearchCompactionError, get_compaction_stop, reset_compaction_stop
from research_context import ContextPolicy
from research_workspace import ResearchWorkspace, ResearchWorkspaceError


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    reset_compaction_stop()
    for name in ContextPolicy.ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    yield
    reset_compaction_stop()


@pytest.fixture
def workspace(tmp_path):
    return ResearchWorkspace(tmp_path / "synthesis", {"run": "fixture", "model": "offline-v1"})


def inputs(**changes):
    return {"model": "offline-v1", "label": "section-1", "system": "Write a report section.",
            "evidence": "Complete source 中文 42", "max_output": 4096,
            "policy": ContextPolicy().to_dict(), **changes}


def completed(workspace):
    return [row for row in workspace.snapshot()["tasks"] if row["status"] == "done"]


def test_exact_raw_replay_after_restart_without_calls_or_input_changes(workspace):
    request = inputs()
    original = copy.deepcopy(request)
    raw = "  Raw answer\r\n未验证 [S42]  "
    with workspace.execution_lock():
        assert synthesis.cached_invoke(workspace, request, lambda: raw) == raw
    reopened = ResearchWorkspace(workspace.root, workspace.identity)
    with reopened.execution_lock():
        assert synthesis.cached_invoke(reopened, request, lambda: pytest.fail("cached invocation")) == raw
    assert request == original
    assert len(completed(reopened)) == 1
    receipt = reopened.load_task(completed(reopened)[0]["task_id"], completed(reopened)[0]["inputs"])
    assert receipt["raw_completion"] == raw
    assert receipt["publication_validated"] is False


def test_raw_completion_replays_in_fresh_process(workspace):
    raw = "Raw full response\r\n尚未发布"
    with workspace.execution_lock():
        synthesis.cached_invoke(workspace, inputs(), lambda: raw)
    script = '''
import json,sys
from deerflow_bridge.research_workspace import ResearchWorkspace
from deerflow_bridge.research_synthesis import cached_invoke
payload=json.load(sys.stdin)
w=ResearchWorkspace(payload['root'],payload['identity'])
def forbidden():
    raise AssertionError('provider replay')
with w.execution_lock():
    print(json.dumps(cached_invoke(w,payload['inputs'],forbidden),ensure_ascii=False))
'''
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps({
        "root": str(workspace.root), "identity": workspace.identity, "inputs": inputs(),
    }), text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout) == raw


@pytest.mark.parametrize("field,value", [("model", "offline-v2"), ("label", "section-2"),
    ("system", "Different full prompt"), ("evidence", "Changed evidence tail"),
    ("max_output", 2048), ("policy", {"version": "different"})])
def test_complete_input_identity_change_produces_new_call(workspace, field, value):
    calls = []
    def invoke():
        calls.append(1)
        return str(len(calls))
    with workspace.execution_lock():
        assert synthesis.cached_invoke(workspace, inputs(), invoke) == "1"
        assert synthesis.cached_invoke(workspace, inputs(**{field: value}), invoke) == "2"
    assert len(completed(workspace)) == 2


@pytest.mark.parametrize("value", ["", " \n", None, {"text": "not raw"}])
def test_empty_or_non_string_completion_is_not_marked_done(workspace, value):
    with workspace.execution_lock(), pytest.raises(ValueError):
        synthesis.cached_invoke(workspace, inputs(), lambda: value)
    assert not completed(workspace)


def test_transport_failure_remains_retryable_and_prior_raw_survives(workspace):
    def fail():
        raise TimeoutError("sensitive transport detail")
    with workspace.execution_lock():
        assert synthesis.cached_invoke(workspace, inputs(), lambda: "unvalidated draft") == "unvalidated draft"
        with pytest.raises(TimeoutError):
            synthesis.cached_invoke(workspace, inputs(label="next-section"), fail)
    reopened = ResearchWorkspace(workspace.root, workspace.identity)
    with reopened.execution_lock():
        assert synthesis.cached_invoke(reopened, inputs(), lambda: pytest.fail("replayed successful section")) == "unvalidated draft"
        assert synthesis.cached_invoke(reopened, inputs(label="next-section"), lambda: "retry") == "retry"


def test_same_process_concurrent_identical_calls_coalesce(workspace):
    barrier = threading.Barrier(5)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def invoke():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return "single raw completion"
    def caller():
        barrier.wait(2)
        return synthesis.cached_invoke(workspace, inputs(), invoke)
    with workspace.execution_lock(), ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(caller) for _ in range(5)]
        assert entered.wait(2)
        release.set()
        assert [f.result(2) for f in futures] == ["single raw completion"] * 5
    assert len(calls) == 1 and len(completed(workspace)) == 1


def test_save_failure_never_returns_success_or_marks_complete(workspace, monkeypatch):
    def fail(*args):
        raise ResearchWorkspaceError("save failed")
    monkeypatch.setattr(workspace, "save_task", fail)
    with workspace.execution_lock(), pytest.raises(ResearchWorkspaceError):
        synthesis.cached_invoke(workspace, inputs(), lambda: "raw")
    assert not completed(workspace)


@pytest.mark.parametrize("changes", [{"model": ""}, {"label": ""}, {"system": ""},
    {"max_output": True}, {"max_output": 0}, {"policy": []}, {"evidence": ("lossy",)}])
def test_invalid_identity_never_invokes(workspace, changes):
    with pytest.raises(ValueError):
        synthesis.cached_invoke(workspace, inputs(**changes), lambda: pytest.fail("invalid admission"))


def test_control_failure_is_never_cached(workspace):
    def stop():
        raise ResearchCompactionError("archive_write_failed")
    with workspace.execution_lock(), pytest.raises(ResearchCompactionError):
        synthesis.cached_invoke(workspace, inputs(), stop)
    assert not completed(workspace)


REPORT = "# Capacity\nCapacity is 42 in 2026 [S1].\n\n# Uncertainty\nHowever, the revised capacity may be 31 in 2027 [S1]."
SOURCES = [{"id": "S1", "url": "https://example.test/source", "content": "Capacity was 42 in 2026."}]


def test_five_distinct_advisory_roles_fail_is_data_and_exact_replay(workspace):
    tasks = []
    def invoke(task):
        tasks.append(copy.deepcopy(task))
        return {"status": "FAIL", "weaknesses": ["Needs stronger source support"], "score": 1}
    result = synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke)
    assert len(tasks) == len(result["reviews"]) == 5
    assert len({task["label"] for task in tasks}) == 5
    assert all(set(task) == {"label", "system", "evidence"} for task in tasks)
    assert all(row["status"] == "FAIL" for row in result["reviews"])
    assert result["aggregate_weaknesses"] == ["Needs stronger source support"]
    assert result["report_sha256"] == hashlib.sha256(REPORT.encode()).hexdigest()
    assert result["scope"]["advisory_only"] is True
    assert "score" not in json.dumps(result)
    replay = synthesis.advisory_reviews(workspace, REPORT, SOURCES, lambda task: pytest.fail("replayed review"))
    assert replay == result


def test_reviews_really_allow_five_and_never_six(workspace):
    barrier = threading.Barrier(5)
    guard = threading.Lock()
    active = peak = 0
    def invoke(task):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(2)
            return "Review observation"
        finally:
            with guard:
                active -= 1
    result = synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, timeout_s=5)
    assert peak == 5 and active == 0
    assert len(result["reviews"]) == 5


def test_transport_errors_are_type_only_unavailable_and_not_cached(workspace):
    def fail(task):
        raise ConnectionError("secret transport details")
    result = synthesis.advisory_reviews(workspace, REPORT, SOURCES, fail)
    assert all(r["status"] == "unavailable" and r["error_type"] == "ConnectionError" for r in result["reviews"])
    assert "secret" not in json.dumps(result)
    assert not completed(workspace)
    retried = synthesis.advisory_reviews(workspace, REPORT, SOURCES, lambda t: {"weaknesses": ["new"]})
    assert all(r["status"] == "reported" for r in retried["reviews"])


def test_review_model_output_limit_and_sources_are_bound_exactly(workspace):
    calls = []
    def invoke(task):
        calls.append(task["label"])
        return {"weaknesses": []}
    synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, model="review-v1", max_output=1800)
    assert len(calls) == 5
    synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, model="review-v1", max_output=1800)
    assert len(calls) == 5
    synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, model="review-v2", max_output=1800)
    assert len(calls) == 10
    synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, model="review-v2", max_output=900)
    assert len(calls) == 15
    synthesis.advisory_reviews(workspace, REPORT, [*SOURCES, {"id": "S2", "text": "changed"}], invoke, model="review-v2", max_output=900)
    assert len(calls) == 20


def test_review_timeout_returns_boundedly_holds_lease_and_fences_late_persistence(workspace):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    def invoke(task):
        entered.set()
        release.wait(5)
        returned.set()
        return {"status": "FAIL", "weaknesses": ["late"]}
    start = time.monotonic()
    try:
        with pytest.raises(synthesis.ResearchSynthesisTimeout):
            synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, workers=1, timeout_s=0.15)
        assert entered.is_set() and time.monotonic() - start < 1.5
        assert get_compaction_stop() is not None
        before = workspace.snapshot()
        with pytest.raises(ResearchWorkspaceError, match="lock"):
            with workspace.execution_lock():
                pytest.fail("timed-out callback released ownership early")
    finally:
        release.set()
    assert returned.wait(2)
    deadline = time.monotonic() + 2
    while True:
        try:
            with workspace.execution_lock():
                break
        except ResearchWorkspaceError:
            assert time.monotonic() < deadline
            time.sleep(0.005)
    assert workspace.snapshot() == before
    assert not completed(workspace)


def test_control_error_propagates_and_stops_queued_callbacks(workspace):
    called = []
    def invoke(task):
        called.append(task["label"])
        raise ResearchCompactionError("archive_write_failed")
    with pytest.raises(ResearchCompactionError):
        synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, workers=1)
    assert len(called) == 1
    assert not completed(workspace)


def test_control_stop_is_visible_while_sibling_persistence_is_blocked(workspace, monkeypatch):
    saving, release, second_entered = threading.Event(), threading.Event(), threading.Event()
    original_save, original_call = workspace.save_task, synthesis._call_review
    calls = []
    first, second = "evidence-and-citations", "numbers-and-dates"
    def save(*args):
        saving.set()
        assert release.wait(3)
        return original_save(*args)
    def call(invoke, task, fence):
        if task["label"] not in {first, second}:
            assert saving.wait(2)
        return original_call(invoke, task, fence)
    def invoke(task):
        calls.append(task["label"])
        if task["label"] == first:
            assert second_entered.wait(2)
            return "successful raw sibling"
        if task["label"] == second:
            second_entered.set()
            assert saving.wait(2)
            raise ResearchCompactionError("archive_write_failed")
        return "queued callback must not run"
    monkeypatch.setattr(workspace, "save_task", save)
    monkeypatch.setattr(synthesis, "_call_review", call)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(synthesis.advisory_reviews, workspace, REPORT, SOURCES, invoke, 2, 5)
        try:
            assert saving.wait(2)
            deadline = time.monotonic() + 0.3
            while get_compaction_stop() is None and time.monotonic() < deadline:
                time.sleep(0.005)
            assert get_compaction_stop() is not None
        finally:
            release.set()
        with pytest.raises(ResearchCompactionError):
            future.result(2)
    assert set(calls) == {first, second}


def test_deep_json_raw_review_is_safe_on_initial_call_and_cache_replay(workspace, monkeypatch):
    raw = "[" * 3000 + "0" + "]" * 3000
    parse = json.loads
    # JSON implementations have different depth limits. Inject the documented
    # parser failure for this one raw response; real persistence still runs.
    def depth_limited(value, *args, **kwargs):
        if value == raw:
            raise RecursionError("fixture parser depth exceeded")
        return parse(value, *args, **kwargs)
    monkeypatch.setattr(synthesis.json, "loads", depth_limited)
    first = synthesis.advisory_reviews(workspace, REPORT, SOURCES, lambda task: raw)
    replay = synthesis.advisory_reviews(workspace, REPORT, SOURCES, lambda task: pytest.fail("raw was cached"))
    assert first == replay
    assert first["aggregate_weaknesses"] == [raw]
    assert all(r["status"] == "reported" for r in first["reviews"])


def test_simultaneous_cold_imports_share_coalescing_registry(workspace):
    script = '''
import builtins,importlib,json,sys,threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,sys.argv[1])
original=builtins.__import__
barrier=threading.Barrier(2)
seen=set()
guard=threading.Lock()
names=['research_synthesis','deerflow_bridge.research_synthesis']
def hooked(name,globals=None,locals=None,fromlist=(),level=0):
    module=(globals or {}).get('__name__')
    pause=False
    if name.endswith('research_compaction') and module in names:
        with guard:
            if module not in seen:
                seen.add(module)
                pause=True
    if pause:
        barrier.wait(3)
    return original(name,globals,locals,fromlist,level)
builtins.__import__=hooked
try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        modules=list(pool.map(importlib.import_module,names))
finally:
    builtins.__import__=original
assert modules[0]._IN_FLIGHT is modules[1]._IN_FLIGHT
from research_workspace import ResearchWorkspace
payload=json.load(sys.stdin)
w=ResearchWorkspace(payload['root'],payload['identity'])
entered,release=threading.Event(),threading.Event()
calls=[]
def invoke():
    calls.append(1)
    entered.set()
    assert release.wait(2)
    return 'one shared raw completion'
with w.execution_lock(), ThreadPoolExecutor(max_workers=2) as pool:
    futures=[pool.submit(m.cached_invoke,w,payload['inputs'],invoke) for m in modules]
    assert entered.wait(2)
    release.set()
    assert [f.result(2) for f in futures]==['one shared raw completion']*2
assert len(calls)==1
print('shared')
'''
    result = subprocess.run([sys.executable, "-c", script, str(BRIDGE)], input=json.dumps({
        "root": str(workspace.root), "identity": workspace.identity, "inputs": inputs(),
    }), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "shared"


def test_deadline_before_planning_has_complete_unavailable_scope(workspace):
    result = synthesis.advisory_reviews(workspace, REPORT, SOURCES,
        lambda task: pytest.fail("expired admission"), timeout_s=1e-12)
    assert result["status"] == "partial"
    assert result["scope"]["advisory_only"] is True
    assert len(result["reviews"]) == 5
    with workspace.execution_lock():
        pass


def test_successful_sibling_cached_before_timeout_and_resume_reuses_it(workspace):
    release = threading.Event()
    first_label = "evidence-and-citations"
    calls = []
    def invoke(task):
        calls.append(task["label"])
        if task["label"] != first_label:
            release.wait(3)
        return {"status": "FAIL", "weaknesses": [task["label"]]}
    try:
        with pytest.raises(synthesis.ResearchSynthesisTimeout):
            synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, workers=2, timeout_s=0.25)
        assert len(completed(workspace)) == 1
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while True:
        try:
            with workspace.execution_lock():
                break
        except ResearchWorkspaceError:
            assert time.monotonic() < deadline
            time.sleep(0.005)
    replayed = []
    reset_compaction_stop()  # A fresh, explicit run attempt after callbacks drain.
    resumed = synthesis.advisory_reviews(workspace, REPORT, SOURCES,
        lambda task: (replayed.append(task["label"]) or {"weaknesses": []}))
    assert len(replayed) == 4 and first_label not in replayed
    assert resumed["reviews"][0]["status"] == "FAIL"


@pytest.mark.parametrize("settings", [{"workers": 0}, {"workers": 6}, {"workers": True},
    {"timeout_s": 0}, {"timeout_s": float("nan")}, {"timeout_s": True}])
def test_invalid_review_envelope_never_invokes(workspace, settings):
    with pytest.raises(ValueError):
        synthesis.advisory_reviews(workspace, REPORT, SOURCES, lambda task: pytest.fail("invalid envelope"), **settings)


def test_long_report_review_prompts_are_bounded_and_full_input_identity_retained(workspace, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENTIC_RETRIEVAL_TOKENS", "2200")
    report = "\n\n".join(["Background narrative." * 100] * 100) + "\n\nHowever revised capacity is 19 in 2029. TAIL."
    tasks = []
    def invoke(task):
        tasks.append(task)
        assert len((task["system"] + task["evidence"]).encode()) <= 2200
        return {"weaknesses": []}
    result = synthesis.advisory_reviews(workspace, report, SOURCES, invoke)
    assert len(tasks) == 5
    assert all(task["evidence"] != report for task in tasks)
    assert workspace.read_artifact(result["scope"]["report_ref"]) == report
    changed = synthesis.advisory_reviews(workspace, report + "\nChanged tail", SOURCES, invoke)
    assert len(tasks) == 10 and changed["report_sha256"] != result["report_sha256"]


REPAIR_REPORT = "# Report\r\n\r\n## Capacity\r\n\r\nBad capacity claim [S1].\r\n\r\n## Risks\r\n\r\nUnchanged risk discussion [S1].\r\n"


def advice(report=REPAIR_REPORT, heading="Capacity", weakness="Correct the unsupported claim"):
    return {"report_sha256": hashlib.sha256(report.encode()).hexdigest(), "reviews": [
        {"status": "FAIL", "weaknesses": [{"section_heading": heading, "weakness": weakness}]},
    ]}


def validate_repair(report, sources):
    errors = []
    if "Bad capacity" in report:
        errors.append("capacity_unsupported")
    if "[S99]" in report:
        errors.append("citation_invalid")
    return {"passed": not errors, "errors": errors, "warnings": [],
            "report_sha256": hashlib.sha256(report.encode()).hexdigest()}


def test_review_requests_and_retains_structured_section_weaknesses(workspace):
    def invoke(task):
        assert "section_heading" in task["system"] and "weakness" in task["system"]
        return {"status": "FAIL", "weaknesses": [{"section_heading": "Capacity", "weakness": "Verify number"}]}
    result = synthesis.advisory_reviews(workspace, REPAIR_REPORT, SOURCES, invoke)
    assert result["aggregate_weaknesses"] == [{"section_heading": "Capacity", "weakness": "Verify number"}]


def test_targeted_repair_changes_only_one_body_and_caches_exact_replacement(workspace):
    calls = []
    original_sources = copy.deepcopy(SOURCES)
    original_advice = advice()
    def invoke(task):
        calls.append(task)
        assert set(task) == {"label", "system", "evidence"}
        assert "Unchanged risk discussion" not in task["evidence"]
        assert "never rewrite the whole report" in task["system"].lower()
        return {"section_heading": "Capacity", "replacement": "Verified capacity is 42 [S1]."}
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, original_advice, invoke, validate_repair, model="repair-v1")
    expected = REPAIR_REPORT.replace("Bad capacity claim [S1].", "Verified capacity is 42 [S1].")
    assert result["report"] == expected
    assert result["repairs"][0]["status"] == "applied"
    assert result["repairs"][0]["replacement_sha256"]
    replay = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, original_advice,
        lambda task: pytest.fail("cached replacement"), validate_repair, model="repair-v1")
    assert replay == result and len(calls) == 1
    assert SOURCES == original_sources and original_advice == advice()
    assert any(row["inputs"].get("replacement_sha256") == result["repairs"][0]["replacement_sha256"]
               for row in completed(workspace))


@pytest.mark.parametrize("response", [
    {"section_heading": "Capacity", "replacement": "# Entire report\nNew report"},
    {"section_heading": "Risks", "replacement": "Wrong section"},
    {"section_heading": "Capacity", "replacement": ""},
    {"section_heading": "Capacity", "replacement": "Invalid citation [S99]."},
    {"section_heading": "Capacity", "replacement": "##\nInjected heading"},
    {"section_heading": "Capacity", "replacement": "```\nAn unclosed fence"},
    "unstructured whole report text",
])
def test_invalid_or_mechanically_worse_repair_keeps_original(workspace, response):
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(),
        lambda task: response, validate_repair, model="repair-v1")
    assert result["report"] == REPAIR_REPORT
    assert all(row["status"] != "applied" for row in result["repairs"])


def test_unavailable_repair_and_validator_preserve_original(workspace):
    def unavailable(task):
        raise ConnectionError("private transport detail")
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(), unavailable, validate_repair)
    assert result["report"] == REPAIR_REPORT
    assert result["repairs"][0]["error_type"] == "ConnectionError"
    assert "private" not in json.dumps(result)
    def bad_validation(report, sources):
        raise RuntimeError("private validator detail")
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(),
        lambda task: pytest.fail("invalid baseline"), bad_validation)
    assert result["report"] == REPAIR_REPORT and "private" not in json.dumps(result)


def test_targeted_repair_revalidates_cached_raw_against_current_mechanics(workspace):
    callback = lambda task: {"section_heading": "Capacity", "replacement": "Fixed claim [S1]."}
    good = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(), callback, validate_repair)
    assert good["report"] != REPAIR_REPORT
    def stricter(report, sources):
        return {"passed": False, "errors": ["new_bad_claim"] if "Fixed" in report else ["capacity_unsupported"]}
    rejected = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(),
        lambda task: pytest.fail("raw replay"), stricter)
    assert rejected["report"] == REPAIR_REPORT


def test_repair_cache_binds_model_full_report_and_sources(workspace):
    calls = []
    def invoke(task):
        calls.append(task)
        return {"section_heading": "Capacity", "replacement": "Fixed [S1]."}
    for report, sources, model in [
        (REPAIR_REPORT, SOURCES, "v1"), (REPAIR_REPORT, SOURCES, "v2"),
        (REPAIR_REPORT + "\nNew untouched tail", SOURCES, "v2"),
        (REPAIR_REPORT + "\nNew untouched tail", SOURCES + [{"id": "S2", "text": "new source"}], "v2"),
    ]:
        synthesis.targeted_repairs(workspace, report, sources, advice(report), invoke, validate_repair, model=model)
    assert len(calls) == 4


def test_stale_unstructured_ambiguous_or_root_targets_do_not_invoke(workspace):
    cases = [(REPAIR_REPORT, {**advice(), "report_sha256": "0" * 64}),
             (REPAIR_REPORT, {"report_sha256": hashlib.sha256(REPAIR_REPORT.encode()).hexdigest(), "reviews": [{"weaknesses": ["generic advice"]}]}),
             (REPAIR_REPORT, advice(heading="Report")),
             (REPAIR_REPORT + "\n## Capacity\nDuplicate\n", advice(REPAIR_REPORT + "\n## Capacity\nDuplicate\n"))]
    for report, advisory in cases:
        result = synthesis.targeted_repairs(workspace, report, SOURCES, advisory,
            lambda task: pytest.fail("unscoped repair"), validate_repair)
        assert result["report"] == report


@pytest.mark.parametrize("reviews", [None, {"weaknesses": []}, [{"weaknesses": None}]])
def test_malformed_advisory_preserves_original(workspace, reviews):
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, {**advice(), "reviews": reviews},
        lambda task: pytest.fail("malformed review"), validate_repair)
    assert result["report"] == REPAIR_REPORT


def test_heading_inside_code_does_not_split_target_or_mutate_other_sections(workspace):
    report = REPAIR_REPORT.replace("Bad capacity claim [S1].", "Bad capacity claim [S1].\r\n```text\r\n## Risks\r\n```")
    result = synthesis.targeted_repairs(workspace, report, SOURCES, advice(report),
        lambda task: {"section_heading": "Capacity", "replacement": "Fixed [S1]."}, validate_repair)
    assert result["report"] == REPAIR_REPORT.replace("Bad capacity claim [S1].", "Fixed [S1].")


def test_oversized_section_is_skipped_without_truncating_report(workspace):
    report = REPAIR_REPORT.replace("Bad capacity claim [S1].", "Bad capacity " * 10000)
    result = synthesis.targeted_repairs(workspace, report, SOURCES, advice(report),
        lambda task: pytest.fail("oversized section"), validate_repair)
    assert result["report"] == report
    assert result["repairs"][0]["reason"] == "section_too_large_or_empty"


def test_bad_validation_binding_and_source_mutation_cannot_apply(workspace):
    original = copy.deepcopy(SOURCES)
    def validate(report, sources):
        sources.clear()
        return {"passed": True, "errors": [], "report_sha256": "0" * 64}
    result = synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(),
        lambda task: pytest.fail("bad baseline receipt"), validate)
    assert result["report"] == REPAIR_REPORT and SOURCES == original


def test_exactly_five_max_repairs_one_round_and_bounded_prompts(workspace):
    report = "# Report\n\n" + "".join(f"## Section {i}\n\nOld body {i}.\n\n" for i in range(7))
    advisory = {"report_sha256": hashlib.sha256(report.encode()).hexdigest(), "reviews": [{"weaknesses": [
        {"section_heading": f"Section {i}", "weakness": "Clarify this section"} for i in range(7)]}]}
    barrier = threading.Barrier(5)
    calls = []
    def invoke(task):
        calls.append(task)
        assert len((task["system"] + task["evidence"]).encode()) <= ContextPolicy().retrieval_tokens
        data = json.loads(task["evidence"])
        barrier.wait(2)
        return {"section_heading": data["section_heading"], "replacement": "Improved body."}
    result = synthesis.targeted_repairs(workspace, report, SOURCES, advisory, invoke,
        lambda report, sources: {"passed": True, "errors": []}, timeout_s=5)
    assert len(calls) == 5
    assert sum(row["status"] == "applied" for row in result["repairs"]) == 5
    assert "Old body 5" in result["report"] and "Old body 6" in result["report"]


def test_targeted_timeout_retains_lease_and_never_commits_late_result(workspace):
    release, entered = threading.Event(), threading.Event()
    def invoke(task):
        entered.set()
        release.wait(3)
        return {"section_heading": "Capacity", "replacement": "Late fix [S1]."}
    try:
        with pytest.raises(synthesis.ResearchSynthesisTimeout):
            synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(), invoke,
                validate_repair, workers=1, timeout_s=0.15)
        assert entered.is_set()
        before = workspace.snapshot()
        with pytest.raises(ResearchWorkspaceError):
            with workspace.execution_lock():
                pass
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while True:
        try:
            with workspace.execution_lock():
                break
        except ResearchWorkspaceError:
            assert time.monotonic() < deadline
            time.sleep(0.005)
    assert workspace.snapshot() == before


@pytest.mark.parametrize("phase", ["advisory", "repair"])
def test_timed_out_callbacks_retain_whole_invocation_ownership(workspace, phase):
    from research_invocation import invocation_scope
    release, entered = threading.Event(), threading.Event()
    def invoke(task):
        entered.set()
        release.wait(3)
        return {"section_heading": "Capacity", "replacement": "Late fix [S1]."}
    try:
        with invocation_scope(workspace.root):
            with pytest.raises(synthesis.ResearchSynthesisTimeout):
                if phase == "advisory":
                    synthesis.advisory_reviews(workspace, REPORT, SOURCES, invoke, workers=1, timeout_s=0.15)
                else:
                    synthesis.targeted_repairs(workspace, REPAIR_REPORT, SOURCES, advice(), invoke,
                        validate_repair, workers=1, timeout_s=0.15)
            assert entered.is_set()
        with pytest.raises(ResearchCompactionError):
            with invocation_scope(workspace.root):
                pytest.fail("invocation ownership released before callback exit")
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while True:
        try:
            with invocation_scope(workspace.root):
                break
        except ResearchCompactionError:
            assert time.monotonic() < deadline
            time.sleep(0.005)


def test_cached_invoke_enters_producer_scope_only_on_miss(workspace, monkeypatch):
    from contextlib import contextmanager
    inside = []
    entered = []
    @contextmanager
    def producer():
        entered.append(1)
        inside.append(True)
        try:
            yield
        finally:
            inside.pop()
    def invoke():
        assert inside
        return "raw"
    monkeypatch.setattr(synthesis, "producer_scope", producer)
    with workspace.execution_lock():
        assert synthesis.cached_invoke(workspace, inputs(), invoke) == "raw"
        assert synthesis.cached_invoke(workspace, inputs(), invoke) == "raw"
    assert entered == [1] and not inside
