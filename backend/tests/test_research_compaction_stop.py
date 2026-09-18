"""Parent admission rejects typed child compaction stops before artifact salvage."""

import json

import pytest

from app.services import pipeline_orchestrator as po


@pytest.mark.parametrize("exit_code,wait_timeout,meta_mode", [
    (2, False, "current"), (0, False, "current"), (0, True, "current"),
    (4, False, "missing"), (4, False, "stale"), (4, False, "running"),
    (4, True, "missing"),
])
def test_current_child_stop_cannot_be_salvaged_or_wrapped_as_generic_failure(
    tmp_path, monkeypatch, exit_code, wait_timeout, meta_mode,
):
    runtime = tmp_path / "deer-flow"
    runtime.mkdir()
    (runtime / "deerflow_research.py").write_text("# stub child\n")
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    monkeypatch.setattr(po.Config, "DEERFLOW_DIR", str(runtime))
    monkeypatch.setattr(po.Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _: None)
    monkeypatch.setattr(po, "_kill_process_group", lambda _: None)
    salvage_calls = []
    monkeypatch.setattr(po, "_run_extract_only_salvage", lambda *a, **k: salvage_calls.append(1))
    launches = []

    class Process:
        pid = 999991
        stdout = ["[error] research_compaction_failed\n"]
        waits = 0

        def poll(self):
            return exit_code

        def wait(self, timeout=None):
            self.waits += 1
            if wait_timeout and self.waits == 1:
                raise po.subprocess.TimeoutExpired("stub-child", timeout)
            return exit_code

    def spawn(*args, **kwargs):
        env = kwargs["env"]
        launches.append(env["RESEARCH_PROCESS_ATTEMPT_ID"])
        meta = {
            "status": "running" if meta_mode == "running" else "failed",
            "research_process_attempt_id": (
                "previous-launch" if meta_mode == "stale"
                else env["RESEARCH_PROCESS_ATTEMPT_ID"]
            ),
            "compaction_stop": {
                "code": "research_compaction_failed",
                "reason": "archive_write_failed", "thread_id": "thread-one",
            },
        }
        if meta_mode != "missing":
            (handoff / "meta.json").write_text(json.dumps(meta))
        # A fresh, apparently usable report must not override the producer stop.
        (handoff / "research_report.md").write_text("retained evidence " * 100)
        (handoff / "actors.json").write_text('{"actors": []}')
        return Process()

    monkeypatch.setattr(po.subprocess, "Popen", spawn)
    with pytest.raises(po._ResearchCompactionStopped) as caught:
        po.DeerFlowResearchRunner.run(
            "Question", str(handoff), on_progress=lambda *_: None, timeout=10,
        )
    assert caught.value.reason == (
        "archive_write_failed" if meta_mode == "current" else "compaction_failed")
    assert caught.value.thread_id == ("thread-one" if meta_mode == "current" else "")
    assert len(launches) == 1 and len(launches[0]) == 32
    assert salvage_calls == []
    assert (handoff / "research_report.md").exists()
    assert not list(handoff.glob(".prompt-*"))


@pytest.mark.parametrize("patch", [
    {"research_process_attempt_id": "previous-launch"},
    {"research_process_attempt_id": ""},
    {"status": "completed"},
    {"compaction_stop": {"code": "some-model-assertion"}},
])
def test_previous_or_untyped_meta_is_not_a_current_process_stop(tmp_path, patch):
    meta = {
        "status": "failed", "research_process_attempt_id": "current-launch",
        "compaction_stop": {"code": "research_compaction_failed", "reason": "invalid_summary"},
    }
    meta.update(patch)
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    po._raise_current_research_compaction_stop(str(tmp_path), "current-launch")


def test_parent_stop_omits_unstructured_error_details():
    stopped = po._ResearchCompactionStopped("provider error secret-value", "thread-safe")
    assert str(stopped) == "research_compaction_failed: compaction_failed"
