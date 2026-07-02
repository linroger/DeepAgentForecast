"""Audit fixes — runner group (RUN-7/11/12/13/15/16/17, XRUN-2/4/9, RUN-18).

Offline: no LLM/network. run_parallel_simulation imports oasis/camel from the
venv but only pure / filesystem helpers are exercised here.
"""

import json
import os
import sqlite3
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
for _p in (_BACKEND, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_parallel_simulation as rps  # noqa: E402
from app.config import Config  # noqa: E402
from app.services.simulation_runner import (  # noqa: E402
    RunnerStatus,
    SimulationRunner,
    SimulationRunState,
)
from app.utils import oasis_llm  # noqa: E402


@pytest.fixture
def sim_env(tmp_path, monkeypatch):
    """Isolated RUN_STATE_DIR + clean in-memory runner registries."""
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_run_state_last_save", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {})
    return tmp_path


def _write_actions(sim_dir, platform, entries):
    plat_dir = os.path.join(str(sim_dir), platform)
    os.makedirs(plat_dir, exist_ok=True)
    with open(os.path.join(plat_dir, "actions.jsonl"), "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- RUN-11
def test_mark_env_stopped_overwrites_alive(sim_env):
    sim_id = "sim_run11"
    sim_dir = sim_env / sim_id
    sim_dir.mkdir()
    (sim_dir / "env_status.json").write_text(
        json.dumps({"status": "alive", "twitter_available": True}), encoding="utf-8")
    assert SimulationRunner.check_env_alive(sim_id) is True

    SimulationRunner._mark_env_stopped(sim_id)
    assert SimulationRunner.check_env_alive(sim_id) is False
    detail = SimulationRunner.get_env_status_detail(sim_id)
    assert detail["status"] == "stopped"
    assert detail["twitter_available"] is False


def test_mark_env_stopped_missing_dir_noop(sim_env):
    # 目录不存在时不得创建游离文件、不得抛出
    SimulationRunner._mark_env_stopped("sim_never_ran")
    assert not (sim_env / "sim_never_ran").exists()


def test_stop_simulation_marks_env_stopped(sim_env):
    sim_id = "sim_run11_stop"
    state = SimulationRunState(simulation_id=sim_id, runner_status=RunnerStatus.RUNNING)
    SimulationRunner._save_run_state(state)
    (sim_env / sim_id / "env_status.json").write_text(
        json.dumps({"status": "alive"}), encoding="utf-8")

    out = SimulationRunner.stop_simulation(sim_id)  # 无 pid/进程 → 视为已终止
    assert out.runner_status == RunnerStatus.STOPPED
    assert SimulationRunner.check_env_alive(sim_id) is False


# ---------------------------------------------------------------- RUN-16
def test_stop_simulation_allows_starting(sim_env):
    sim_id = "sim_run16"
    state = SimulationRunState(simulation_id=sim_id, runner_status=RunnerStatus.STARTING)
    SimulationRunner._save_run_state(state)
    out = SimulationRunner.stop_simulation(sim_id)
    assert out.runner_status == RunnerStatus.STOPPED


def test_stop_simulation_still_rejects_completed(sim_env):
    sim_id = "sim_run16_completed"
    state = SimulationRunState(simulation_id=sim_id, runner_status=RunnerStatus.COMPLETED)
    SimulationRunner._save_run_state(state)
    with pytest.raises(ValueError):
        SimulationRunner.stop_simulation(sim_id)


# ---------------------------------------------------------------- RUN-12
def test_interview_timeout_scales_with_batch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-cli")
    monkeypatch.delenv("OASIS_CLI_SEMAPHORE", raising=False)
    monkeypatch.setattr(Config, "INTERVIEW_TIMEOUT_PER_AGENT", 30.0, raising=False)
    # 80 采访 / 并发 4 → 60 + 30*20 = 660s（远大于旧的 180s 全场超时）
    assert SimulationRunner._scale_interview_timeout(180.0, 80) == 660.0


def test_interview_timeout_never_shrinks(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-cli")
    monkeypatch.delenv("OASIS_CLI_SEMAPHORE", raising=False)
    monkeypatch.setattr(Config, "INTERVIEW_TIMEOUT_PER_AGENT", 30.0, raising=False)
    # 单个采访：60+30=90 < 传入 120 → 保留调用方超时
    assert SimulationRunner._scale_interview_timeout(120.0, 1) == 120.0


def test_interview_timeout_scaling_disabled(monkeypatch):
    monkeypatch.setattr(Config, "INTERVIEW_TIMEOUT_PER_AGENT", 0, raising=False)
    assert SimulationRunner._scale_interview_timeout(180.0, 80) == 180.0


# ---------------------------------------------------------------- XRUN-9
def test_timeline_first_last_not_inverted(sim_env):
    sim_id = "sim_xrun9"
    _write_actions(sim_env / sim_id, "twitter", [
        {"round": 1, "timestamp": "2026-07-02T03:55:47", "agent_id": 1,
         "agent_name": "A", "action_type": "CREATE_POST", "action_args": {}},
        {"round": 1, "timestamp": "2026-07-02T03:53:05", "agent_id": 2,
         "agent_name": "B", "action_type": "LIKE_POST", "action_args": {}},
    ])
    timeline = SimulationRunner.get_timeline(sim_id)
    assert len(timeline) == 1
    r = timeline[0]
    assert r["first_action_time"] == "2026-07-02T03:53:05"
    assert r["last_action_time"] == "2026-07-02T03:55:47"
    assert r["first_action_time"] <= r["last_action_time"]


def test_agent_stats_first_last_not_inverted(sim_env):
    sim_id = "sim_xrun9_agent"
    _write_actions(sim_env / sim_id, "twitter", [
        {"round": 2, "timestamp": "2026-07-02T05:00:00", "agent_id": 7,
         "agent_name": "N", "action_type": "CREATE_POST", "action_args": {}},
        {"round": 1, "timestamp": "2026-07-02T04:00:00", "agent_id": 7,
         "agent_name": "N", "action_type": "LIKE_POST", "action_args": {}},
    ])
    stats = SimulationRunner.get_agent_stats(sim_id)
    assert stats[0]["first_action_time"] == "2026-07-02T04:00:00"
    assert stats[0]["last_action_time"] == "2026-07-02T05:00:00"


# ------------------------------------------------- RUN-13 / XRUN-2(2)
def test_run_summary_excludes_seeds_and_scheduled_from_organic(sim_env):
    sim_id = "sim_run13"
    _write_actions(sim_env / sim_id, "twitter", [
        # round-0 种子帖 + 种子 FOLLOW —— 不是 agent 决策
        {"round": 0, "timestamp": "2026-07-02T00:00:01", "agent_id": 0,
         "agent_name": "Seed", "action_type": "CREATE_POST",
         "action_args": {"content": "seed post"}},
        {"round": 0, "timestamp": "2026-07-02T00:00:02", "agent_id": 1,
         "agent_name": "F", "action_type": "FOLLOW",
         "action_args": {"is_seed_action": True}},
        # 时间线回放事件 —— 注入而非 agent 决策
        {"round": 3, "timestamp": "2026-07-02T00:03:00", "agent_id": 0,
         "agent_name": "Seed", "action_type": "CREATE_POST",
         "action_args": {"content": "scheduled", "is_scheduled_event": True}},
        # 真正的有机动作
        {"round": 2, "timestamp": "2026-07-02T00:02:00", "agent_id": 5,
         "agent_name": "Org", "action_type": "CREATE_POST",
         "action_args": {"content": "organic post"}},
        {"round": 4, "timestamp": "2026-07-02T00:04:00", "agent_id": 6,
         "agent_name": "L", "action_type": "LIKE_POST", "action_args": {}},
    ])
    summary = SimulationRunner.write_run_summary(sim_id)
    assert summary is not None
    assert summary["organic_action_count"] == 2
    assert summary["rounds_with_organic_actions"] == 2
    # top_posts: 只含有机帖，按轮次升序，且不再混入种子/回放
    contents = [p["content"] for p in summary["top_posts"]]
    assert contents == ["organic post"]
    assert summary["simulation_health"] != "hollow"


def test_run_summary_seed_only_run_is_hollow(sim_env):
    sim_id = "sim_run13_hollow"
    _write_actions(sim_env / sim_id, "twitter", [
        {"round": 0, "timestamp": "2026-07-02T00:00:01", "agent_id": 0,
         "agent_name": "Seed", "action_type": "CREATE_POST",
         "action_args": {"content": "seed"}},
        {"round": 1, "timestamp": "2026-07-02T00:01:00", "agent_id": 1,
         "agent_name": "F", "action_type": "FOLLOW", "action_args": {}},
    ])
    summary = SimulationRunner.write_run_summary(sim_id)
    assert summary["organic_action_count"] == 0
    assert summary["simulation_health"] == "hollow"
    assert summary["top_posts"] == []


# ---------------------------------------------------------------- RUN-15
def test_rotate_stale_logs_rotates_derived_artifacts(sim_env):
    sim_id = "sim_run15"
    sim_dir = sim_env / sim_id
    (sim_dir / "twitter").mkdir(parents=True)
    (sim_dir / "twitter" / "actions.jsonl").write_text("{}\n", encoding="utf-8")
    (sim_dir / "twitter" / "checkpoint.json").write_text("{}", encoding="utf-8")
    (sim_dir / "run_summary.json").write_text("{}", encoding="utf-8")
    (sim_dir / "world_state_trajectory.json").write_text("{}", encoding="utf-8")
    (sim_dir / "decisions.jsonl").write_text("{}\n", encoding="utf-8")

    SimulationRunner._rotate_stale_action_logs(str(sim_dir))

    assert not (sim_dir / "run_summary.json").exists()
    assert (sim_dir / "run_summary.json.prev").exists()
    assert not (sim_dir / "world_state_trajectory.json").exists()
    assert (sim_dir / "world_state_trajectory.json.prev").exists()
    assert not (sim_dir / "twitter" / "actions.jsonl").exists()
    assert (sim_dir / "twitter" / "actions.prev.jsonl").exists()
    assert (sim_dir / "twitter" / "checkpoint.prev.json").exists()


def test_cleanup_simulation_logs_removes_derived_artifacts(sim_env):
    sim_id = "sim_run15_cleanup"
    sim_dir = sim_env / sim_id
    (sim_dir / "reddit").mkdir(parents=True)
    (sim_dir / "run_summary.json").write_text("{}", encoding="utf-8")
    (sim_dir / "run_summary.json.prev").write_text("{}", encoding="utf-8")
    (sim_dir / "llm_health.json").write_text("{}", encoding="utf-8")
    (sim_dir / "reddit" / "checkpoint.json").write_text("{}", encoding="utf-8")

    result = SimulationRunner.cleanup_simulation_logs(sim_id)
    assert result["success"] is True
    assert not (sim_dir / "run_summary.json").exists()
    assert not (sim_dir / "run_summary.json.prev").exists()
    assert not (sim_dir / "llm_health.json").exists()
    assert not (sim_dir / "reddit" / "checkpoint.json").exists()


# ---------------------------------------------------------------- RUN-17
class _DummyProc:
    pid = 424242
    returncode = 0

    def poll(self):
        return 0


def test_monitor_flags_partial_completion(sim_env):
    sim_id = "sim_run17"
    state = SimulationRunState(
        simulation_id=sim_id,
        runner_status=RunnerStatus.RUNNING,
        twitter_enabled=True,
        reddit_enabled=True,
        twitter_completed=True,
        reddit_completed=False,  # Reddit 从未发出 simulation_end
    )
    SimulationRunner._save_run_state(state)
    SimulationRunner._processes[sim_id] = _DummyProc()

    SimulationRunner._monitor_simulation(sim_id)

    final = SimulationRunner.get_run_state(sim_id)
    assert final.runner_status == RunnerStatus.COMPLETED
    assert final.error and "simulation_end" in final.error
    # RUN-11: 进程退出后环境必须标记 stopped
    assert SimulationRunner.check_env_alive(sim_id) is False


def test_monitor_no_error_when_all_platforms_completed(sim_env):
    sim_id = "sim_run17_ok"
    state = SimulationRunState(
        simulation_id=sim_id,
        runner_status=RunnerStatus.RUNNING,
        twitter_enabled=True,
        reddit_enabled=False,
        twitter_completed=True,
    )
    SimulationRunner._save_run_state(state)
    SimulationRunner._processes[sim_id] = _DummyProc()

    SimulationRunner._monitor_simulation(sim_id)

    final = SimulationRunner.get_run_state(sim_id)
    assert final.runner_status == RunnerStatus.COMPLETED
    assert final.error is None


# ---------------------------------------------------------------- RUN-7 (runner side)
def test_resume_checkpoint_round_reads_max_platform_round(sim_env):
    sim_dir = sim_env / "sim_run7"
    (sim_dir / "twitter").mkdir(parents=True)
    (sim_dir / "reddit").mkdir(parents=True)
    (sim_dir / "twitter" / "checkpoint.json").write_text(
        json.dumps({"completed_round": 7}), encoding="utf-8")
    (sim_dir / "reddit" / "checkpoint.json").write_text(
        json.dumps({"completed_round": 5}), encoding="utf-8")
    assert SimulationRunner._resume_checkpoint_round(str(sim_dir)) == 7


def test_resume_checkpoint_round_rejects_invalid(sim_env):
    sim_dir = sim_env / "sim_run7_bad"
    (sim_dir / "twitter").mkdir(parents=True)
    (sim_dir / "twitter" / "checkpoint.json").write_text("not-json", encoding="utf-8")
    assert SimulationRunner._resume_checkpoint_round(str(sim_dir)) is None
    # completed_round=0（尚未完成任何轮）不可续跑
    (sim_dir / "twitter" / "checkpoint.json").write_text(
        json.dumps({"completed_round": 0}), encoding="utf-8")
    assert SimulationRunner._resume_checkpoint_round(str(sim_dir)) is None


def test_run_state_persists_resumed_from_round(sim_env):
    sim_id = "sim_run7_state"
    state = SimulationRunState(
        simulation_id=sim_id, runner_status=RunnerStatus.RUNNING, resumed_from_round=9)
    SimulationRunner._save_run_state(state)
    SimulationRunner._run_states.clear()
    loaded = SimulationRunner.get_run_state(sim_id)
    assert loaded.resumed_from_round == 9
    assert loaded.to_dict()["resumed_from_round"] == 9


# ---------------------------------------------------------------- RUN-7 (script side)
def test_round_checkpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_RESUME", "true")
    os.makedirs(tmp_path / "twitter")
    rps._write_round_checkpoint(str(tmp_path), "twitter",
                                completed_round=12, last_rowid=345,
                                total_rounds=36, total_actions=88)
    ckpt = rps._load_round_checkpoint(str(tmp_path), "twitter")
    assert ckpt["completed_round"] == 12
    assert ckpt["last_rowid"] == 345
    assert ckpt["total_actions"] == 88


def test_round_checkpoint_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_RESUME", raising=False)
    os.makedirs(tmp_path / "twitter")
    rps._write_round_checkpoint(str(tmp_path), "twitter", 1, 1, 10, 1)
    assert not os.path.exists(rps._checkpoint_file(str(tmp_path), "twitter"))


def test_load_round_checkpoint_rejects_zero_rounds(tmp_path):
    os.makedirs(tmp_path / "reddit")
    with open(rps._checkpoint_file(str(tmp_path), "reddit"), "w", encoding="utf-8") as f:
        json.dump({"completed_round": 0}, f)
    assert rps._load_round_checkpoint(str(tmp_path), "reddit") is None


def test_max_trace_rowid(tmp_path):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE trace (user_id INT, action TEXT, info TEXT)")
    conn.executemany("INSERT INTO trace VALUES (?, ?, ?)",
                     [(1, "sign_up", "{}"), (2, "create_post", "{}")])
    conn.commit()
    conn.close()
    assert rps._max_trace_rowid(db) == 2
    assert rps._max_trace_rowid(str(tmp_path / "missing.db")) == 0


def test_run_simulation_signatures_accept_resume():
    import inspect
    assert "resume" in inspect.signature(rps.run_twitter_simulation).parameters
    assert "resume" in inspect.signature(rps.run_reddit_simulation).parameters


# ---------------------------------------------------------------- XRUN-4
def test_free_disk_error_disabled_and_triggered(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_MIN_FREE_DISK_GB", "0")
    assert rps._free_disk_error(str(tmp_path)) is None
    monkeypatch.setenv("SIM_MIN_FREE_DISK_GB", "999999")
    err = rps._free_disk_error(str(tmp_path))
    assert err is not None and "SIM_MIN_FREE_DISK_GB" in err


def test_step_failure_limit_env(monkeypatch):
    monkeypatch.setenv("SIM_STEP_FAILURE_LIMIT", "5")
    assert rps._step_failure_limit() == 5
    monkeypatch.setenv("SIM_STEP_FAILURE_LIMIT", "0")
    assert rps._step_failure_limit() == 0
    monkeypatch.setenv("SIM_STEP_FAILURE_LIMIT", "abc")
    assert rps._step_failure_limit() == 3


# ---------------------------------------------------------------- XRUN-2
_TOOLS = [{
    "type": "function",
    "function": {
        "name": "create_post",
        "description": "Create a post",
        "parameters": {"type": "object", "properties": {"content": {"type": "string"}}},
    },
}]


def test_parse_tool_call_text_valid():
    content, calls = oasis_llm._parse_tool_call_text(
        '{"tool": "create_post", "arguments": {"content": "hi"}}', {"create_post"})
    assert calls == [{"name": "create_post", "arguments": {"content": "hi"}}]


def test_parse_tool_call_text_null_tool_and_garbage():
    content, calls = oasis_llm._parse_tool_call_text(
        '{"tool": null, "reply": "nothing to do"}', {"create_post"})
    assert calls == [] and content == "nothing to do"
    content, calls = oasis_llm._parse_tool_call_text("plain text", {"create_post"})
    assert calls == [] and content == "plain text"
    # 幻觉工具名不得放行
    _, calls = oasis_llm._parse_tool_call_text(
        '{"tool": "rm_rf", "arguments": {}}', {"create_post"})
    assert calls == []


def test_build_chat_completion_with_tool_calls():
    comp = oasis_llm._build_chat_completion(
        "claude-cli", [{"role": "user", "content": "x"}], "",
        [{"name": "create_post", "arguments": {"content": "hi"}}])
    choice = comp.choices[0]
    assert choice.finish_reason == "tool_calls"
    tc = choice.message.tool_calls[0]
    assert tc.function.name == "create_post"
    assert json.loads(tc.function.arguments) == {"content": "hi"}
    assert choice.message.content  # 绝不产生空 assistant 内容（S2-llm）


class _StubLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, temperature=0.7, max_tokens=4096, **kw):
        self.calls.append(messages)
        return self.reply


def test_cli_model_emulates_tool_calls(monkeypatch):
    monkeypatch.setenv("SIM_CLI_TOOL_EMULATION", "true")
    model = oasis_llm.CLIModel(
        model_type="claude-cli", provider="claude-cli",
        model_config_dict={}, api_key="cli-bridge")
    stub = _StubLLM('{"tool": "create_post", "arguments": {"content": "hello"}}')
    model._llm = stub
    comp = model._request_chat_completion(
        [{"role": "user", "content": "act"}], tools=_TOOLS)
    assert comp.choices[0].finish_reason == "tool_calls"
    assert comp.choices[0].message.tool_calls[0].function.name == "create_post"
    # 工具清单必须进入 prompt（追加的 user 指令块）
    assert any("create_post" in str(m.get("content", "")) for m in stub.calls[0])


def test_cli_model_emulation_off_ignores_tools(monkeypatch):
    monkeypatch.setenv("SIM_CLI_TOOL_EMULATION", "false")
    model = oasis_llm.CLIModel(
        model_type="claude-cli", provider="claude-cli",
        model_config_dict={}, api_key="cli-bridge")
    model._llm = _StubLLM("plain reply")
    comp = model._request_chat_completion(
        [{"role": "user", "content": "act"}], tools=_TOOLS)
    assert comp.choices[0].finish_reason == "stop"
    assert not comp.choices[0].message.tool_calls


def test_cli_model_no_second_tool_call_after_tool_result(monkeypatch):
    monkeypatch.setenv("SIM_CLI_TOOL_EMULATION", "true")
    model = oasis_llm.CLIModel(
        model_type="claude-cli", provider="claude-cli",
        model_config_dict={}, api_key="cli-bridge")
    model._llm = _StubLLM("done")
    comp = model._request_chat_completion(
        [{"role": "user", "content": "act"},
         {"role": "tool", "content": "post created"}], tools=_TOOLS)
    # 已有工具结果 → 纯文本收尾，避免文本协议下的无界工具循环
    assert comp.choices[0].finish_reason == "stop"


# ---------------------------------------------------------------- RUN-18
class _DummyOpenAIModel:
    model_type = "MiniMax-M3"
    model_config_dict = {}

    def __init__(self, exc):
        self._exc = exc

        def _raise(messages, tools=None):
            raise self._exc
        self._request_chat_completion = _raise


def test_fallback_guard_reroutes_content_filter(monkeypatch):
    monkeypatch.setenv("SIM_LLM_FALLBACK", "true")
    monkeypatch.setenv("SIM_CLI_TOOL_EMULATION", "true")

    class _FactoryStub:
        def __init__(self, provider=None, **kw):
            self.provider = provider

        def chat(self, messages, temperature=0.7, max_tokens=4096, **kw):
            return '{"tool": "create_post", "arguments": {"content": "rescued"}}'

    monkeypatch.setattr(oasis_llm, "LLMClient", _FactoryStub)
    model = _DummyOpenAIModel(Exception("Error code: 422 - new_sensitive"))
    oasis_llm._wrap_openai_fallback_guard(model, "minimax")

    before = oasis_llm.get_llm_fallback_stats()["llm_fallback_calls"]
    comp = model._request_chat_completion(
        [{"role": "user", "content": "geo"}], tools=_TOOLS)
    assert comp.choices[0].finish_reason == "tool_calls"
    assert comp.choices[0].message.tool_calls[0].function.name == "create_post"
    after = oasis_llm.get_llm_fallback_stats()
    assert after["llm_fallback_calls"] == before + 1
    assert after["llm_fallback_tool_calls"] >= 1


def test_fallback_guard_reraises_unrelated_errors(monkeypatch):
    monkeypatch.setenv("SIM_LLM_FALLBACK", "true")
    model = _DummyOpenAIModel(ValueError("totally unrelated boom"))
    oasis_llm._wrap_openai_fallback_guard(model, "minimax")
    with pytest.raises(ValueError):
        model._request_chat_completion([{"role": "user", "content": "x"}])


def test_fallback_guard_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("SIM_LLM_FALLBACK", "false")
    model = _DummyOpenAIModel(Exception("Error code: 422 - new_sensitive"))
    oasis_llm._wrap_openai_fallback_guard(model, "minimax")
    with pytest.raises(Exception, match="422"):
        model._request_chat_completion([{"role": "user", "content": "x"}])


def test_fallback_failure_raises_original_error(monkeypatch):
    monkeypatch.setenv("SIM_LLM_FALLBACK", "true")

    class _BrokenFactory:
        def __init__(self, provider=None, **kw):
            pass

        def chat(self, *a, **kw):
            raise RuntimeError("fallback also dead")

    monkeypatch.setattr(oasis_llm, "LLMClient", _BrokenFactory)
    original = Exception("Error code: 429 rate_limit")
    model = _DummyOpenAIModel(original)
    oasis_llm._wrap_openai_fallback_guard(model, "minimax")
    with pytest.raises(Exception, match="429"):
        model._request_chat_completion([{"role": "user", "content": "x"}])
