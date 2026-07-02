"""drf2/engines/simulation 的离线测试：job 生命周期、文件契约、健康检测、采访桥。

不 spawn 真实模拟、不打 LLM/网络：subprocess.Popen 被 FakePopen 替换，
interview IPC 用一个本地 responder 线程模拟模拟进程侧的应答（真实文件契约）。
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

# 仓库根加入 sys.path，使 `import drf2...`（命名空间包）可用。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.services import simulation_runner as sr_mod  # noqa: E402
from app.services.simulation_runner import RunnerStatus, SimulationRunner  # noqa: E402

from drf2.engines.simulation.jobs import SimulationJobService  # noqa: E402


class FakePopen:
    """替代 subprocess.Popen 的假子进程：可控 returncode，记录 spawn 参数。"""

    instances = []

    def __init__(self, cmd, **kwargs):
        self.cmd = list(cmd)
        self.kwargs = kwargs
        self.pid = 424242
        self._returncode = None
        FakePopen.instances.append(self)

    def poll(self):
        return self._returncode

    @property
    def returncode(self):
        return self._returncode

    def wait(self, timeout=None):
        if self._returncode is None:
            raise RuntimeError("FakePopen.wait called while still 'running'")
        return self._returncode

    def terminate(self):
        self._returncode = -15

    def kill(self):
        self._returncode = -9

    def finish(self, code=0):
        self._returncode = code


@pytest.fixture
def sim_env(tmp_path, monkeypatch):
    """隔离的 run_root + FakePopen + 清空 SimulationRunner 类级注册表。"""
    run_root = tmp_path / "simulations"
    run_root.mkdir()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(run_root))
    monkeypatch.setattr(sr_mod.subprocess, "Popen", FakePopen)
    FakePopen.instances = []
    # 类级字典是跨测试共享的可变状态，必须清空避免串味。
    SimulationRunner._run_states.clear()
    SimulationRunner._run_state_last_save.clear()
    SimulationRunner._processes.clear()
    SimulationRunner._monitor_threads.clear()
    SimulationRunner._action_queues.clear()
    SimulationRunner._stdout_files.clear()
    SimulationRunner._stderr_files.clear()
    yield str(run_root)
    # 结束所有仍在"运行"的假进程并汇流监控线程，防止守护线程在
    # monkeypatch 还原后向真实 uploads/ 目录写 run_state。
    for p in FakePopen.instances:
        if p._returncode is None:
            p.finish(0)
    for sim_id in list(SimulationRunner._monitor_threads):
        SimulationRunner.join_monitor_thread(sim_id, timeout=10)
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()


def _make_config(path, agents=2, hours=2, minutes_per_round=60):
    cfg = {
        "time_config": {
            "total_simulation_hours": hours,
            "minutes_per_round": minutes_per_round,
        },
        "agent_configs": [
            {"agent_id": i, "agent_name": f"agent_{i}"} for i in range(agents)
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg


def _make_sim_dir(run_root, sim_id, **cfg_kwargs):
    sim_dir = os.path.join(run_root, sim_id)
    _make_config(os.path.join(sim_dir, "simulation_config.json"), **cfg_kwargs)
    return sim_dir


def _write_run_state(sim_dir, **overrides):
    state = {
        "simulation_id": os.path.basename(sim_dir),
        "runner_status": "running",
        "current_round": 3,
        "total_rounds": 10,
        "twitter_current_round": 3,
        "reddit_current_round": 2,
        "twitter_actions_count": 12,
        "reddit_actions_count": 7,
        "twitter_enabled": True,
        "reddit_enabled": True,
        "updated_at": datetime.now().isoformat(),
        "process_pid": 999999,
        "recent_actions": [],
    }
    state.update(overrides)
    os.makedirs(sim_dir, exist_ok=True)
    with open(os.path.join(sim_dir, "run_state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return state


# ---------------------------------------------------------------- resolve/start


def test_start_from_external_config_spawns_parallel_script(sim_env, tmp_path):
    """config_path 在 run_root 外 → 新建 sim 目录复制配置，并按遗留契约 spawn。"""
    ext_cfg = tmp_path / "elsewhere" / "myconfig.json"
    _make_config(str(ext_cfg))

    out = SimulationJobService.start(config_path=str(ext_cfg), sim_seed=7, max_rounds=5)

    assert out["success"] is True
    sim_id = out["sim_id"]
    sim_dir = os.path.join(sim_env, sim_id)
    assert os.path.isfile(os.path.join(sim_dir, "simulation_config.json"))
    # 立即返回：状态为 running，并带 pid。
    assert out["run_state"]["runner_status"] == "running"
    assert out["run_state"]["process_pid"] == 424242

    proc = FakePopen.instances[-1]
    # spawn 契约：run_parallel_simulation.py --config <simdir>/simulation_config.json --max-rounds 5
    assert any(c.endswith("run_parallel_simulation.py") for c in proc.cmd)
    cfg_idx = proc.cmd.index("--config")
    assert proc.cmd[cfg_idx + 1] == os.path.join(sim_dir, "simulation_config.json")
    assert "--max-rounds" in proc.cmd and "5" in proc.cmd
    assert proc.kwargs.get("cwd") == sim_dir
    assert proc.kwargs.get("start_new_session") is True
    # 种子仅注入子进程环境（NEXTSTEPS P0-3），不污染本进程。
    assert proc.kwargs["env"]["SIM_SEED"] == "7"
    assert os.environ.get("SIM_SEED") != "7"


def test_start_rejects_missing_and_ambiguous_inputs(sim_env, tmp_path):
    with pytest.raises(ValueError):
        SimulationJobService.start()  # 二者皆缺
    with pytest.raises(ValueError):
        SimulationJobService.start(sim_id="nope")  # 目录/配置不存在
    ext_cfg = tmp_path / "c.json"
    _make_config(str(ext_cfg))
    with pytest.raises(ValueError):
        SimulationJobService.resolve(sim_id="a", config_path=str(ext_cfg))  # 二选一
    with pytest.raises(ValueError):
        SimulationJobService.status("../evil")  # 路径穿越


def test_start_with_prepared_sim_id_reuses_dir(sim_env):
    _make_sim_dir(sim_env, "prepared01")
    out = SimulationJobService.start(sim_id="prepared01")
    assert out["sim_id"] == "prepared01"
    assert out["run_state"]["runner_status"] == "running"
    # total_rounds 按 time_config 推导：2h × 60min/轮 = 2 轮。
    assert out["run_state"]["total_rounds"] == 2


# ---------------------------------------------------------------- status/health


def test_status_not_found(sim_env):
    out = SimulationJobService.status("ghost")
    assert out["found"] is False
    assert out["health"]["health"] == "not_found"


def test_status_detects_crashed_orphan(sim_env):
    """run_state=RUNNING 但进程已死（pid 不存在）→ health=crashed。"""
    sim_dir = _make_sim_dir(sim_env, "crashy")
    _write_run_state(sim_dir, runner_status="running", process_pid=999999)

    out = SimulationJobService.status("crashy")
    assert out["found"] is True
    assert out["health"]["health"] == "crashed"
    assert out["health"]["process_alive"] is False
    assert out["health"]["interview_ready"] is False
    # 进度/计数如实透传。
    assert out["progress"]["current_round"] == 3
    assert out["action_counts"]["total"] == 19


def test_status_running_ok_and_stale_heartbeat(sim_env, monkeypatch):
    sim_dir = _make_sim_dir(sim_env, "livey")
    _write_run_state(sim_dir, runner_status="running")
    monkeypatch.setattr(SimulationRunner, "_is_simulation_alive", classmethod(lambda cls, s: True))
    # env_status alive → 采访可用。
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "alive", "twitter_available": True,
                   "reddit_available": True, "timestamp": datetime.now().isoformat()}, f)

    out = SimulationJobService.status("livey")
    assert out["health"]["health"] == "ok"
    assert out["health"]["interview_ready"] is True

    # 心跳陈旧：updated_at 半小时前 → stale_heartbeat（进程仍存活）。
    SimulationRunner._run_states.clear()
    _write_run_state(sim_dir, runner_status="running",
                     updated_at=(datetime.now() - timedelta(minutes=30)).isoformat())
    out = SimulationJobService.status("livey")
    assert out["health"]["health"] == "stale_heartbeat"
    assert out["health"]["heartbeat_age_seconds"] > 1000


def test_status_completed_with_errors(sim_env):
    sim_dir = _make_sim_dir(sim_env, "doneish")
    _write_run_state(sim_dir, runner_status="completed",
                     error="部分平台未产出 simulation_end")
    out = SimulationJobService.status("doneish")
    assert out["health"]["health"] == "completed_with_errors"


# ---------------------------------------------------------------- results


def test_results_reads_summary_and_dynamics(sim_env):
    sim_dir = _make_sim_dir(sim_env, "resulty")
    _write_run_state(sim_dir, runner_status="completed")
    summary = {"simulation_id": "resulty", "organic_action_count": 42,
               "seed_action_count": 100, "simulation_health": "ok",
               "rounds_executed": 10}
    with open(os.path.join(sim_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f)
    with open(os.path.join(sim_dir, "twitter_dynamics_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"active": True, "signals": 5}, f)

    out = SimulationJobService.results("resulty")
    assert out["partial"] is False
    assert out["run_summary"]["organic_action_count"] == 42
    assert out["dynamics_summary"]["twitter"]["active"] is True
    assert out["run_summary_regenerated"] is False


def test_results_partial_while_running(sim_env):
    sim_dir = _make_sim_dir(sim_env, "runningy")
    _write_run_state(sim_dir, runner_status="running")
    out = SimulationJobService.results("runningy")
    assert out["partial"] is True
    assert out["run_summary"] is None
    assert "sim_status" in out["note"]


def test_results_regenerates_summary_with_honest_accounting(sim_env):
    """终态且缺 run_summary.json → 现场聚合；有机/种子记账须诚实。"""
    sim_dir = _make_sim_dir(sim_env, "regen01")
    _write_run_state(sim_dir, runner_status="completed", current_round=2,
                     total_rounds=2)
    os.makedirs(os.path.join(sim_dir, "twitter"), exist_ok=True)
    lines = [
        # round-0 关注 = 种子图动作，不是有机互动。
        {"round": 0, "timestamp": "2026-07-03T10:00:00", "agent_id": 1,
         "agent_name": "a1", "action_type": "FOLLOW", "action_args": {}},
        # round-1 自发发帖 = 有机。
        {"round": 1, "timestamp": "2026-07-03T10:05:00", "agent_id": 1,
         "agent_name": "a1", "action_type": "CREATE_POST",
         "action_args": {"content": "hello world"}},
        {"event_type": "round_end", "round": 2, "simulated_hours": 2},
    ]
    with open(os.path.join(sim_dir, "twitter", "actions.jsonl"), "w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec) + "\n")

    out = SimulationJobService.results("regen01")
    assert out["run_summary_regenerated"] is True
    rs = out["run_summary"]
    assert rs["organic_action_count"] == 1
    assert rs["seed_action_count"] == 1
    assert rs["simulation_health"] == "ok"
    # 落盘契约：run_summary.json 已写出。
    assert os.path.isfile(os.path.join(sim_dir, "run_summary.json"))


def test_results_flags_hollow_run(sim_env):
    sim_dir = _make_sim_dir(sim_env, "hollow01")
    _write_run_state(sim_dir, runner_status="completed")
    with open(os.path.join(sim_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"simulation_health": "hollow", "organic_action_count": 0}, f)
    out = SimulationJobService.results("hollow01")
    assert "hollow" in out["note"]


# ---------------------------------------------------------------- stop


def test_stop_lifecycle_marks_stopped_and_env(sim_env):
    _make_sim_dir(sim_env, "stoppy")
    SimulationJobService.start(sim_id="stoppy")
    proc = FakePopen.instances[-1]
    assert proc.poll() is None  # 仍在"运行"

    # FakePopen.terminate 会置 returncode，走真实终止路径的 Unix 分支会
    # os.getpgid(424242) 抛 ProcessLookupError → stop_simulation 视为已终止。
    out = SimulationJobService.stop("stoppy")
    assert out["run_state"]["runner_status"] == "stopped"
    # RUN-11：进程确认终止后 env_status 同步落 stopped。
    env = json.load(open(os.path.join(sim_env, "stoppy", "env_status.json"), encoding="utf-8"))
    assert env["status"] == "stopped"


def test_stop_missing_sim_raises(sim_env):
    with pytest.raises(ValueError):
        SimulationJobService.stop("ghost")


# ---------------------------------------------------------------- interview 桥


def _fake_responder(sim_dir, stop_evt, reply):
    """模拟进程侧的 IPC 应答者：轮询 ipc_commands，写响应到 ipc_responses。"""
    commands_dir = os.path.join(sim_dir, "ipc_commands")
    responses_dir = os.path.join(sim_dir, "ipc_responses")
    os.makedirs(responses_dir, exist_ok=True)
    while not stop_evt.is_set():
        if os.path.isdir(commands_dir):
            for fname in os.listdir(commands_dir):
                if not fname.endswith(".json"):
                    continue
                cmd_path = os.path.join(commands_dir, fname)
                try:
                    with open(cmd_path, encoding="utf-8") as f:
                        cmd = json.load(f)
                except (OSError, ValueError):
                    continue
                resp = {
                    "command_id": cmd["command_id"],
                    "status": "completed",
                    "result": dict(reply, echo=cmd.get("args", {})),
                    "error": None,
                    "timestamp": datetime.now().isoformat(),
                }
                tmp = os.path.join(responses_dir, cmd["command_id"] + ".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(resp, f)
                os.replace(tmp, os.path.join(responses_dir, cmd["command_id"] + ".json"))
        time.sleep(0.05)


def test_interview_single_agent_roundtrip(sim_env, monkeypatch):
    """真实文件 IPC 契约往返：命令写入 ipc_commands → 响应从 ipc_responses 读回。"""
    sim_dir = _make_sim_dir(sim_env, "talky")
    _write_run_state(sim_dir, runner_status="running")
    monkeypatch.setattr(SimulationRunner, "_is_simulation_alive", classmethod(lambda cls, s: True))
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "alive", "timestamp": datetime.now().isoformat()}, f)

    stop_evt = threading.Event()
    t = threading.Thread(target=_fake_responder,
                         args=(sim_dir, stop_evt, {"answer": "I am agent 1"}), daemon=True)
    t.start()
    try:
        out = SimulationJobService.interview("talky", agent_id=1, prompt="why?", timeout=10)
    finally:
        stop_evt.set()
        t.join(timeout=5)

    assert out["success"] is True
    assert out["sim_id"] == "talky"
    assert out["result"]["answer"] == "I am agent 1"
    # 请求参数确实通过 IPC 契约送达（echo 回传验证）。
    assert out["result"]["echo"]["agent_id"] == 1
    assert out["result"]["echo"]["prompt"] == "why?"


def test_interview_all_agents_builds_batch_from_config(sim_env, monkeypatch):
    sim_dir = _make_sim_dir(sim_env, "allhands", agents=3)
    _write_run_state(sim_dir, runner_status="running")
    monkeypatch.setattr(SimulationRunner, "_is_simulation_alive", classmethod(lambda cls, s: True))
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "alive", "timestamp": datetime.now().isoformat()}, f)

    stop_evt = threading.Event()
    t = threading.Thread(target=_fake_responder,
                         args=(sim_dir, stop_evt, {"answers": "all"}), daemon=True)
    t.start()
    try:
        out = SimulationJobService.interview("allhands", prompt="stance?", timeout=15)
    finally:
        stop_evt.set()
        t.join(timeout=5)

    assert out["success"] is True
    assert out["interviews_count"] == 3
    assert len(out["result"]["echo"]["interviews"]) == 3


def test_interview_rejects_dead_env_immediately(sim_env):
    """env_status=stopped → 立即拒绝，不发 IPC。"""
    sim_dir = _make_sim_dir(sim_env, "deady")
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "stopped", "timestamp": datetime.now().isoformat()}, f)
    with pytest.raises(ValueError, match="未运行"):
        SimulationJobService.interview("deady", agent_id=1, prompt="hi", timeout=5)


def test_interview_detects_stale_alive_env(sim_env):
    """env 说 alive 但进程已死 → 立即报错且 env_status 被纠正为 stopped（RUN-11 镜像）。"""
    sim_dir = _make_sim_dir(sim_env, "staley")
    _write_run_state(sim_dir, runner_status="running", process_pid=999999)
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "alive", "timestamp": datetime.now().isoformat()}, f)

    started = time.time()
    with pytest.raises(ValueError, match="stopped|已死|陈旧"):
        SimulationJobService.interview("staley", agent_id=1, prompt="hi", timeout=60)
    assert time.time() - started < 5  # 没有阻塞完整超时窗

    env = json.load(open(os.path.join(sim_dir, "env_status.json"), encoding="utf-8"))
    assert env["status"] == "stopped"


def test_interview_requires_some_mode(sim_env, monkeypatch):
    sim_dir = _make_sim_dir(sim_env, "modes")
    _write_run_state(sim_dir, runner_status="running")
    monkeypatch.setattr(SimulationRunner, "_is_simulation_alive", classmethod(lambda cls, s: True))
    with open(os.path.join(sim_dir, "env_status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "alive", "timestamp": datetime.now().isoformat()}, f)
    with pytest.raises(ValueError, match="必须提供"):
        SimulationJobService.interview("modes")


# ---------------------------------------------------------------- MCP server 面


def test_mcp_server_registers_expected_tools():
    """server.py 可导入，5 个工具齐全，长任务语义写进描述（tool_search 可发现）。"""
    from drf2.engines.simulation import server as srv

    import asyncio

    tools = asyncio.run(srv.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"sim_start", "sim_status", "sim_results", "sim_stop",
                     "sim_interview_agents"}
    by_name = {t.name: t for t in tools}
    assert "RETURN IMMEDIATELY" in by_name["sim_start"].description
    assert "poll" in by_name["sim_start"].description.lower()
    assert "polling surface" in by_name["sim_status"].description


def test_mcp_tools_surface_errors_as_payload(sim_env):
    """工具面绝不向协议层抛异常：失败 → {success: False, error}。"""
    from drf2.engines.simulation import server as srv

    import asyncio

    out = asyncio.run(srv.sim_stop("no_such_sim"))
    assert out["success"] is False
    assert "error" in out

    out = asyncio.run(srv.sim_start())  # 缺参数
    assert out["success"] is False
    assert "sim_id" in out["error"] or "config_path" in out["error"]


def test_mcp_sim_status_and_results_via_server(sim_env):
    """从 MCP 工具层走通 status/results（异步 → 线程池 → JobService）。"""
    from drf2.engines.simulation import server as srv

    import asyncio

    sim_dir = _make_sim_dir(sim_env, "viaserver")
    _write_run_state(sim_dir, runner_status="completed")
    with open(os.path.join(sim_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"simulation_health": "ok", "organic_action_count": 3}, f)

    st = asyncio.run(srv.sim_status("viaserver"))
    assert st["success"] is True and st["found"] is True
    assert st["health"]["health"] == "completed"

    res = asyncio.run(srv.sim_results("viaserver"))
    assert res["success"] is True
    assert res["run_summary"]["organic_action_count"] == 3
