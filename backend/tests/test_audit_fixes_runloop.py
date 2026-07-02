"""Audit fixes — runloop group (RUN-1..RUN-14, XRUN-14).

Offline: no LLM/network. The heavy module (run_parallel_simulation) imports
oasis/camel from the venv but the tested helpers are pure / filesystem-only.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
for _p in (_BACKEND, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_parallel_simulation as rps  # noqa: E402
from app.services import agent_dynamics  # noqa: E402


# ---------------------------------------------------------------- RUN-8
def test_target_user_name_in_target_keys():
    assert "target_user_name" in agent_dynamics.TARGET_NAME_KEYS


def test_follow_signal_resolves_via_target_user_name():
    actions = [{
        "agent_id": 1,
        "action_type": "FOLLOW",
        "action_args": {"target_user_name": "Alice"},
    }]
    received, activity = agent_dynamics.extract_round_signals(actions, {"Alice": 7})
    assert received[7]["pos"] == 1
    assert activity[1] == 1


def test_mute_signal_resolves_via_target_user_name():
    actions = [{
        "agent_id": 2,
        "action_type": "MUTE",
        "action_args": {"target_user_name": "Bob"},
    }]
    received, _ = agent_dynamics.extract_round_signals(actions, {"Bob": 3})
    assert received[3]["neg"] == 1


# ---------------------------------------------------------------- RUN-5
def test_hour_multiplier_full_ladder():
    tc = {
        "peak_hours": [20], "peak_activity_multiplier": 1.5,
        "off_peak_hours": [2], "off_peak_activity_multiplier": 0.05,
        "morning_hours": [6, 7, 8], "morning_activity_multiplier": 0.4,
        "work_hours": [9, 10], "work_activity_multiplier": 0.7,
    }
    assert rps._resolve_hour_multiplier(tc, 20) == 1.5
    assert rps._resolve_hour_multiplier(tc, 2) == 0.05
    assert rps._resolve_hour_multiplier(tc, 7) == 0.4
    assert rps._resolve_hour_multiplier(tc, 9) == 0.7
    assert rps._resolve_hour_multiplier(tc, 13) == 1.0


def test_hour_multiplier_backward_compatible_without_new_keys():
    # 配置缺 morning/work 键 → 与旧 peak/off_peak/1.0 行为一致
    tc = {"peak_hours": [9], "off_peak_hours": [1]}
    assert rps._resolve_hour_multiplier(tc, 9) == 1.5
    assert rps._resolve_hour_multiplier(tc, 1) == 0.3
    assert rps._resolve_hour_multiplier(tc, 12) == 1.0


# ---------------------------------------------------------------- RUN-4
def test_start_hour_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("SIM_START_HOUR", raising=False)
    assert rps._resolve_start_hour({}) == 0


def test_start_hour_from_env_and_config(monkeypatch):
    monkeypatch.setenv("SIM_START_HOUR", "9")
    assert rps._resolve_start_hour({}) == 9
    # time_config.start_hour 优先于环境变量
    assert rps._resolve_start_hour({"start_hour": 11}) == 11
    # 溢出取模、非法值回退 0
    assert rps._resolve_start_hour({"start_hour": 26}) == 2
    assert rps._resolve_start_hour({"start_hour": "bogus"}) == 0


# ---------------------------------------------------------------- RUN-3
@pytest.mark.parametrize("script", [
    "run_parallel_simulation.py",
    "run_twitter_simulation.py",
    "run_reddit_simulation.py",
])
def test_max_rounds_argparse_default_is_none(script):
    with open(os.path.join(_SCRIPTS, script), encoding="utf-8") as f:
        src = f.read()
    idx = src.index("'--max-rounds'")
    window = src[idx:idx + 400]
    assert "default=None" in window
    assert "default=40" not in window


# ---------------------------------------------------------------- RUN-2
class _FailingAsyncModel:
    def __init__(self):
        self.behavior = []

    async def _arequest_chat_completion(self, messages, tools=None):
        if self.behavior.pop(0) == "fail":
            raise ValueError("boom")
        return {"ok": True}


def test_llm_counter_counts_async_calls_and_errors():
    model = _FailingAsyncModel()
    model.behavior = ["ok", "fail", "fail"]
    counter = rps._wrap_model_llm_counter(model)

    async def _drive():
        await model._arequest_chat_completion([])
        for _ in range(2):
            with pytest.raises(ValueError):
                await model._arequest_chat_completion([])
    asyncio.run(_drive())
    assert counter == {"calls": 3, "errors": 2}


def test_llm_counter_sync_fallback_when_no_async():
    class SyncModel:
        def _request_chat_completion(self, messages, tools=None):
            raise RuntimeError("down")
    model = SyncModel()
    counter = rps._wrap_model_llm_counter(model)
    with pytest.raises(RuntimeError):
        model._request_chat_completion([])
    assert counter == {"calls": 1, "errors": 1}


def test_write_llm_health_merges_platforms_and_flags_degraded(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_LLM_ERROR_RATE_THRESHOLD", raising=False)
    logs = []
    rps._write_llm_health(str(tmp_path), "twitter", {"calls": 4, "errors": 3}, logs.append)
    rps._write_llm_health(str(tmp_path), "reddit", {"calls": 10, "errors": 0}, logs.append)
    with open(tmp_path / "llm_health.json", encoding="utf-8") as f:
        data = json.load(f)
    tw = data["platforms"]["twitter"]
    rd = data["platforms"]["reddit"]
    assert tw["llm_calls"] == 4 and tw["llm_errors"] == 3 and tw["degraded"] is True
    assert rd["degraded"] is False and rd["error_rate"] == 0.0
    assert data["threshold"] == 0.5


def test_write_llm_health_zero_calls_not_degraded(tmp_path):
    # 计数包装失败/无调用时（calls=0）不得误报 degraded
    rps._write_llm_health(str(tmp_path), "twitter", {"calls": 0, "errors": 0}, lambda m: None)
    with open(tmp_path / "llm_health.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["platforms"]["twitter"]["degraded"] is False


# ---------------------------------------------------------------- XRUN-14 (tool args)
def test_normalize_tool_kwargs_alias_and_drop():
    params = frozenset({"comment_id"})
    out = rps._normalize_tool_kwargs({"post_id": 7}, params, "like_comment")
    assert out == {"comment_id": 7}
    out = rps._normalize_tool_kwargs({"comment_id": 1, "hallucinated": "x"}, params, "like_comment")
    assert out == {"comment_id": 1}
    # 别名目标已被显式提供时不覆盖
    out = rps._normalize_tool_kwargs({"comment_id": 1, "post_id": 9}, params, "like_comment")
    assert out == {"comment_id": 1}


class _FakeTool:
    def __init__(self, func):
        self.func = func


class _FakeAgentGraph:
    def __init__(self, agents):
        self._agents = agents

    def get_agents(self):
        return [(i, a) for i, a in enumerate(self._agents)]


def _make_fake_agent():
    class FakeAgent:
        pass

    async def like_comment(comment_id: int):
        return ("liked", comment_id)

    agent = FakeAgent()
    agent._internal_tools = {"like_comment": _FakeTool(like_comment)}
    return agent


def test_tool_wrapper_recovers_hallucinated_arg(monkeypatch):
    monkeypatch.delenv("SIM_TOOL_ARG_NORMALIZE", raising=False)
    agent = _make_fake_agent()
    rps._wrap_agent_tool_arg_normalizer(_FakeAgentGraph([agent]), lambda m: None)
    tool = agent._internal_tools["like_comment"]
    assert getattr(tool.func, "_sim_arg_normalized", False)
    result = asyncio.run(tool.func(post_id=5))
    assert result == ("liked", 5)


def test_tool_wrapper_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("SIM_TOOL_ARG_NORMALIZE", "false")
    agent = _make_fake_agent()
    original = agent._internal_tools["like_comment"].func
    rps._wrap_agent_tool_arg_normalizer(_FakeAgentGraph([agent]), lambda m: None)
    assert agent._internal_tools["like_comment"].func is original


# ---------------------------------------------------------------- RUN-10 / XRUN-14 (IPC)
def _write_cmd(commands_dir, command_id, mtime=None, payload=None):
    path = os.path.join(commands_dir, f"{command_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload or {"command_id": command_id, "command_type": "interview", "args": {}}, f)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_poll_command_skips_and_cleans_cancelled(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    now = time.time()
    cancelled = _write_cmd(handler.commands_dir, "cmd1", mtime=now - 100)
    cancel_marker = os.path.join(handler.commands_dir, "cmd1.cancel")
    with open(cancel_marker, "w", encoding="utf-8") as f:
        json.dump({"command_id": "cmd1"}, f)
    _write_cmd(handler.commands_dir, "cmd2", mtime=now)

    cmd = handler.poll_command()
    assert cmd["command_id"] == "cmd2"
    # 被取消的命令与 .cancel 标记均被清理，不会累积
    assert not os.path.exists(cancelled)
    assert not os.path.exists(cancel_marker)


def test_send_response_skips_orphan_when_cancelled(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    _write_cmd(handler.commands_dir, "cmd3")
    with open(os.path.join(handler.commands_dir, "cmd3.cancel"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    handler.send_response("cmd3", "completed", result={"x": 1})
    assert not os.path.exists(os.path.join(handler.responses_dir, "cmd3.json"))
    assert not os.path.exists(os.path.join(handler.commands_dir, "cmd3.json"))
    assert not os.path.exists(os.path.join(handler.commands_dir, "cmd3.cancel"))


def test_send_response_skips_when_client_gone(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    # 客户端超时删除了命令文件（无 .cancel）：同样不写孤儿响应
    handler.send_response("ghost", "completed", result={})
    assert not os.path.exists(os.path.join(handler.responses_dir, "ghost.json"))


def test_send_response_writes_valid_json_and_cleans_command(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    _write_cmd(handler.commands_dir, "cmd4")
    handler.send_response("cmd4", "completed", result={"answer": 42})
    with open(os.path.join(handler.responses_dir, "cmd4.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "completed" and data["result"] == {"answer": 42}
    assert not os.path.exists(os.path.join(handler.commands_dir, "cmd4.json"))


def test_update_status_atomic_valid_json(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    handler.update_status("alive")
    with open(handler.status_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "alive"


def test_process_commands_refreshes_idle_timer_and_close_env(tmp_path):
    handler = rps.ParallelIPCHandler(simulation_dir=str(tmp_path))
    handler.last_command_at = 0.0
    _write_cmd(handler.commands_dir, "cmd5",
               payload={"command_id": "cmd5", "command_type": "close_env", "args": {}})
    should_continue = asyncio.run(handler.process_commands())
    assert should_continue is False
    assert handler.last_command_at > 0.0


# ---------------------------------------------------------------- RUN-1 (fetch/enrich path)
def _make_trace_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE trace (user_id INTEGER, action TEXT, info TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE follow (follow_id INTEGER, follower_id INTEGER, followee_id INTEGER)")
    cur.execute("CREATE TABLE user (user_id INTEGER, agent_id INTEGER, name TEXT, user_name TEXT)")
    cur.execute("CREATE TABLE post (post_id INTEGER, user_id INTEGER, content TEXT, "
                "original_post_id INTEGER, quote_content TEXT)")
    cur.execute("CREATE TABLE comment (comment_id INTEGER, user_id INTEGER, content TEXT)")
    conn.commit()
    return conn


def test_fetch_advances_rowid_and_enriches_follow_target(tmp_path):
    db_path = str(tmp_path / "twitter_simulation.db")
    conn = _make_trace_db(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO user VALUES (2, 2, 'Bob', 'bob')")
    cur.execute("INSERT INTO follow VALUES (1, 0, 2)")
    cur.execute("INSERT INTO trace VALUES (0, 'create_post', ?, 't0')",
                (json.dumps({"content": "seed"}),))
    cur.execute("INSERT INTO trace VALUES (0, 'follow', ?, 't1')",
                (json.dumps({"follow_id": 1}),))
    conn.commit()
    conn.close()

    names = {0: "Alice", 2: "Bob"}
    actions, last_rowid = rps.fetch_new_actions_from_db(db_path, 0, names)
    assert last_rowid == 2
    types = [a["action_type"] for a in actions]
    assert types == ["CREATE_POST", "FOLLOW"]
    # RUN-8 依赖的键：FOLLOW 上下文补充写的是 target_user_name
    assert actions[1]["action_args"]["target_user_name"] == "Bob"
    # 再次 fetch（RUN-1/RUN-14 的去重语义）：backlog 已消化，无新动作
    actions2, last_rowid2 = rps.fetch_new_actions_from_db(db_path, last_rowid, names)
    assert actions2 == [] and last_rowid2 == last_rowid


# ---------------------------------------------------------------- RUN-6
def test_build_oasis_platform_model_free_twitter_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_WIRE_RECSYS", raising=False)
    monkeypatch.delenv("SIM_TWITTER_MODEL_FREE_FEED", raising=False)
    monkeypatch.delenv("SIM_TWITTER_RECSYS", raising=False)
    platform = rps.build_oasis_platform(
        "twitter", str(tmp_path / "t.db"), {}, lambda m: None)
    assert platform is not None
    # 模型无关 feed：recsys_type 提升为 reddit（启发式排序），不再依赖 twhin-bert 下载
    assert str(getattr(platform.recsys_type, "value", platform.recsys_type)) == "reddit"


def test_build_oasis_platform_model_free_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_WIRE_RECSYS", raising=False)
    monkeypatch.setenv("SIM_TWITTER_MODEL_FREE_FEED", "false")
    assert rps.build_oasis_platform(
        "twitter", str(tmp_path / "t2.db"), {}, lambda m: None) is None


def test_build_oasis_platform_reddit_unchanged_without_wire(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_WIRE_RECSYS", raising=False)
    assert rps.build_oasis_platform(
        "reddit", str(tmp_path / "r.db"), {}, lambda m: None) is None


# ---------------------------------------------------------------- misc flags
def test_tokenizers_parallelism_silenced():
    # XRUN-14: 模块导入时已在任何 huggingface import 前设置
    assert os.environ.get("TOKENIZERS_PARALLELISM") == "false"


def test_cfg_flag_env_overrides_default(monkeypatch):
    monkeypatch.setenv("SIM_RECENCY_CARRY", "true")
    assert rps._flag_true("SIM_RECENCY_CARRY", "false") is True
    monkeypatch.delenv("SIM_RECENCY_CARRY", raising=False)
    assert rps._flag_true("SIM_RECENCY_CARRY", "false") is False
