"""OASIS 模拟 job service —— MCP 工具背后的确定性核心。

职责（REDESIGN.md「Simulation engine」框）：
1. start：把 EXISTING backend/scripts/run_parallel_simulation.py 以独立进程拉起，
   与 SimulationRunner 完全同一条路径（直接 import 复用，不复制 spawn 逻辑），
   保留检查点/续跑（SIM_RESUME/--resume）与文件契约
   （simulation_config.json → actions.jsonl / run_state.json / run_summary.json / env_status.json）。
2. status：轮询面——轮次进度、动作计数、健康度（进程存活 + run_state 心跳
   + env_status），能诚实识别「run_state 说 RUNNING 但进程已死」的孤儿/崩溃 job。
3. results：读 run_summary.json（含有机/种子诚实记账、simulation_health）+
   {platform}_dynamics_summary.json；终态而缺摘要时按遗留契约现场聚合一次。
4. stop：复用 SimulationRunner.stop_simulation（进程组终止 + STOPPED 与
   「确实杀掉」绑定 + env_status 落 stopped）。
5. interview：桥接既有 interview IPC 契约（simulation_ipc 的文件命令/响应 +
   env_status.json 存活门），只加一层「env 说 alive 但进程已死」的防呆，
   避免对着尸体进程阻塞整个超时窗（RUN-11 的引擎侧镜像）。

本模块不做任何 LLM 调用；所有方法失败时抛 ValueError/RuntimeError（由
server.py 转成 {"success": False, "error": ...}），绝不让半截状态落盘。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# --- 让 `import app...` 可用：backend/ 加入 sys.path（与 backend/tests/conftest.py 同法） ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.services.simulation_runner import (  # noqa: E402  (路径注入后才可导入)
    RunnerStatus,
    SimulationRunner,
)

# run_state.json 心跳阈值：监控线程运行期每 ~2s 刷新 updated_at（受
# SIM_RUNSTATE_SAVE_INTERVAL 节流，典型 <30s）。超过该阈值且状态仍为
# RUNNING/STARTING 视为心跳陈旧（监控线程卡死/宿主被 SIGKILL）。
_DEFAULT_HEARTBEAT_STALE_SECONDS = 180.0

# 平台动态摘要文件（RUN-9：active=false 时报告端应抑制情绪演化叙事）。
_DYNAMICS_SUMMARY_FILES = {
    "twitter": "twitter_dynamics_summary.json",
    "reddit": "reddit_dynamics_summary.json",
}


def _heartbeat_stale_seconds() -> float:
    """心跳陈旧阈值，可用 DRF2_SIM_HEARTBEAT_STALE_S 覆盖；非法值回落默认。"""
    raw = os.environ.get("DRF2_SIM_HEARTBEAT_STALE_S")
    if not raw:
        return _DEFAULT_HEARTBEAT_STALE_SECONDS
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_HEARTBEAT_STALE_SECONDS
    except ValueError:
        return _DEFAULT_HEARTBEAT_STALE_SECONDS


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    """读 JSON 文件；缺失/损坏一律返回 None（degrade-safe，调用方自行降级）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _iso_age_seconds(iso_ts: Optional[str]) -> Optional[float]:
    """ISO 时间戳距现在的秒数；无法解析返回 None。"""
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(str(iso_ts))
    except ValueError:
        return None
    try:
        return max(0.0, (datetime.now(tz=then.tzinfo) - then).total_seconds())
    except (TypeError, OverflowError):
        return None


class SimulationJobService:
    """OASIS 模拟的作业层：解析 sim 目录、启动、健康监测、结果聚合、采访桥。

    无实例状态——全部状态在磁盘（run_state.json 等）与 SimulationRunner 的
    类级注册表里，因此 MCP server 崩溃重启后 status/results/stop 依然可用
    （stop 会走持久化 pid/pgid 的孤儿终止路径）。
    """

    # ------------------------------------------------------------------ 目录解析

    @staticmethod
    def run_root() -> str:
        """模拟目录根（与遗留 runner 完全一致，保证两套系统看到同一批 job）。"""
        return os.path.abspath(SimulationRunner.RUN_STATE_DIR)

    @classmethod
    def _sim_dir(cls, sim_id: str) -> str:
        """sim_id → 目录路径；拒绝路径穿越（sim_id 只允许普通目录名）。"""
        if not sim_id or os.path.basename(str(sim_id)) != str(sim_id) or str(sim_id) in (".", ".."):
            raise ValueError(f"非法 sim_id: {sim_id!r}")
        return os.path.join(cls.run_root(), str(sim_id))

    @classmethod
    def resolve(cls, sim_id: Optional[str] = None,
                config_path: Optional[str] = None) -> Tuple[str, str]:
        """把 (sim_id | config_path) 归一化为 (sim_id, sim_dir)。

        - sim_id：目录必须已存在且含 simulation_config.json（/prepare 的产物）。
        - config_path 指向 run_root 内某 sim 目录的 simulation_config.json：
          直接复用该目录（不复制，保住已有 profile/检查点）。
        - config_path 在 run_root 之外：新建 drf2sim_* 目录并把配置复制为
          simulation_config.json——SimulationRunner 只认这个文件契约。
        """
        if sim_id and config_path:
            raise ValueError("sim_id 与 config_path 只能二选一")
        if sim_id:
            sim_dir = cls._sim_dir(sim_id)
            if not os.path.isfile(os.path.join(sim_dir, "simulation_config.json")):
                raise ValueError(
                    f"模拟配置不存在: {sim_dir}/simulation_config.json（请先生成配置或改传 config_path）")
            return str(sim_id), sim_dir
        if not config_path:
            raise ValueError("必须提供 sim_id 或 config_path 之一")

        config_path = os.path.abspath(config_path)
        if not os.path.isfile(config_path):
            raise ValueError(f"配置文件不存在: {config_path}")
        # 先验证是合法 JSON，避免把坏配置复制进 run_root 后子进程才失败。
        if _read_json(config_path) is None:
            raise ValueError(f"配置文件不是合法 JSON 对象: {config_path}")

        parent = os.path.dirname(config_path)
        if (os.path.basename(config_path) == "simulation_config.json"
                and os.path.dirname(parent) == cls.run_root()):
            return os.path.basename(parent), parent

        new_id = f"drf2sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        sim_dir = cls._sim_dir(new_id)
        os.makedirs(sim_dir, exist_ok=True)
        shutil.copyfile(config_path, os.path.join(sim_dir, "simulation_config.json"))
        return new_id, sim_dir

    # ------------------------------------------------------------------ 生命周期

    @classmethod
    def start(
        cls,
        sim_id: Optional[str] = None,
        config_path: Optional[str] = None,
        platform: str = "parallel",
        max_rounds: Optional[int] = None,
        sim_seed: Optional[int] = None,
        resume: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """启动模拟并立即返回（子进程独立进程组运行，poll 用 status()）。

        完整复用 SimulationRunner.start_simulation：同一 spawn 契约
        （run_parallel_simulation.py --config … [--max-rounds N] [--resume]，
        cwd=sim_dir，start_new_session=True，SIM_SEED 仅注入子进程环境）、
        同一监控线程、同一 run_state.json 落盘、同一「已在运行则拒绝」交叉验证。
        """
        resolved_id, sim_dir = cls.resolve(sim_id=sim_id, config_path=config_path)
        state = SimulationRunner.start_simulation(
            simulation_id=resolved_id,
            platform=platform,
            max_rounds=max_rounds,
            sim_seed=sim_seed,
            resume=resume,
        )
        return {
            "success": True,
            "sim_id": resolved_id,
            "sim_dir": sim_dir,
            "run_state": state.to_dict(),
            "note": "模拟已在独立进程中启动；用 sim_status 轮询进度，完成后用 sim_results 取结果。",
        }

    @classmethod
    def stop(cls, sim_id: str) -> Dict[str, Any]:
        """停止模拟——复用遗留终止路径（STOPPED 与「确实杀掉进程」绑定，F-6-11）。"""
        sim_dir = cls._sim_dir(sim_id)
        if not os.path.isdir(sim_dir):
            raise ValueError(f"模拟不存在: {sim_id}")
        state = SimulationRunner.stop_simulation(sim_id)
        return {"success": True, "sim_id": sim_id, "run_state": state.to_dict()}

    # ------------------------------------------------------------------ 健康/状态

    @classmethod
    def _process_alive(cls, state) -> Optional[bool]:
        """进程是否确实存活（Popen 句柄或持久化 PID + 命令行身份校验）。

        复用 SimulationRunner._is_simulation_alive（防 PID 复用误判的实现只此一份，
        不重写）；探测本身失败时返回 None=未知，绝不抛出。
        """
        try:
            return bool(SimulationRunner._is_simulation_alive(state))
        except Exception:  # noqa: BLE001 — 健康探测必须 degrade-safe
            return None

    @classmethod
    def _assess_health(cls, state, env_status: Dict[str, Any]) -> Dict[str, Any]:
        """把 run_state + 进程存活 + 心跳年龄 归纳为单一 health 判定。

        - crashed：状态仍为 RUNNING/STARTING 但进程已死（重启遗留孤儿 / SIGKILL）。
        - stale_heartbeat：进程看似存活但 run_state 心跳超阈值（监控线程卡死）。
        - completed_with_errors：COMPLETED 但 error 非空（RUN-17 的「部分平台未产出」等）。
        """
        status = state.runner_status
        heartbeat_age = _iso_age_seconds(state.updated_at)
        process_alive = cls._process_alive(state)

        if status in (RunnerStatus.RUNNING, RunnerStatus.STARTING, RunnerStatus.STOPPING):
            if process_alive is False:
                health = "crashed"
                detail = ("run_state 为 " + status.value + " 但进程已不存在——孤儿/崩溃 job。"
                          "可用 sim_start(resume=True) 从检查点续跑，或 sim_stop 归档。")
            elif heartbeat_age is not None and heartbeat_age > _heartbeat_stale_seconds():
                health = "stale_heartbeat"
                detail = (f"进程存活但 run_state 心跳已 {heartbeat_age:.0f}s 未刷新"
                          f"（阈值 {_heartbeat_stale_seconds():.0f}s）——监控线程可能卡死。")
            else:
                health = "ok"
                detail = "运行中，心跳正常。"
        elif status == RunnerStatus.COMPLETED:
            if state.error:
                health = "completed_with_errors"
                detail = f"已完成但带错误标记: {state.error}"
            else:
                health = "completed"
                detail = "已正常完成。"
        elif status == RunnerStatus.FAILED:
            health = "failed"
            detail = state.error or "运行失败（无错误详情）。"
        elif status == RunnerStatus.STOPPED:
            health = "stopped"
            detail = "已被主动停止。"
        else:
            health = status.value
            detail = f"runner_status={status.value}"

        env_alive = env_status.get("status") == "alive"
        return {
            "health": health,
            "detail": detail,
            "process_alive": process_alive,
            "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            # 采访只有在 env 声称 alive 且进程确实存活时才可能成功。
            "interview_ready": bool(env_alive and process_alive),
        }

    @classmethod
    def status(cls, sim_id: str) -> Dict[str, Any]:
        """轮询面：轮次进度、动作计数、平台完成态、健康度、env(采访)可用性。"""
        sim_dir = cls._sim_dir(sim_id)
        state = SimulationRunner.get_run_state(sim_id)
        if state is None:
            return {
                "success": True,
                "sim_id": sim_id,
                "found": False,
                "health": {"health": "not_found",
                           "detail": "无 run_state.json——该模拟尚未启动或目录不存在。"},
                "sim_dir_exists": os.path.isdir(sim_dir),
            }

        env_status = SimulationRunner.get_env_status_detail(sim_id)
        health = cls._assess_health(state, env_status)
        run_state = state.to_dict()
        return {
            "success": True,
            "sim_id": sim_id,
            "found": True,
            "health": health,
            "env_status": env_status,
            # 便于 LLM 直接读的顶层进度摘要（run_state 里有全量字段）。
            "progress": {
                "runner_status": run_state["runner_status"],
                "current_round": run_state["current_round"],
                "total_rounds": run_state["total_rounds"],
                "progress_percent": run_state["progress_percent"],
                "twitter_current_round": run_state["twitter_current_round"],
                "reddit_current_round": run_state["reddit_current_round"],
                "twitter_completed": run_state["twitter_completed"],
                "reddit_completed": run_state["reddit_completed"],
                "resumed_from_round": run_state["resumed_from_round"],
            },
            "action_counts": {
                "twitter": run_state["twitter_actions_count"],
                "reddit": run_state["reddit_actions_count"],
                "total": run_state["total_actions_count"],
            },
            "run_state": run_state,
        }

    # ------------------------------------------------------------------ 结果

    @classmethod
    def results(cls, sim_id: str) -> Dict[str, Any]:
        """run_summary.json（含 organic/seed 诚实记账 + simulation_health）+ 动态摘要。

        摘要缺失时：终态 job 按遗留契约现场聚合一次（write_run_summary），
        运行中的 job 返回 partial=True 并附当前进度——绝不假装有终局结果。
        """
        sim_dir = cls._sim_dir(sim_id)
        if not os.path.isdir(sim_dir):
            raise ValueError(f"模拟不存在: {sim_id}")

        state = SimulationRunner.get_run_state(sim_id)
        terminal = state is not None and state.runner_status in (
            RunnerStatus.COMPLETED, RunnerStatus.FAILED, RunnerStatus.STOPPED)

        summary = _read_json(os.path.join(sim_dir, "run_summary.json"))
        regenerated = False
        if summary is None and terminal:
            # 遗留编排器也是运行结束后才聚合；这里保持同一契约与同一实现。
            summary = SimulationRunner.write_run_summary(sim_id)
            regenerated = summary is not None

        # 动态摘要独立读取（run_summary.agent_dynamics 只在生成时存在该文件才嵌入）。
        dynamics: Dict[str, Any] = {}
        for plat, fname in _DYNAMICS_SUMMARY_FILES.items():
            data = _read_json(os.path.join(sim_dir, fname))
            if data is not None:
                dynamics[plat] = data

        result: Dict[str, Any] = {
            "success": True,
            "sim_id": sim_id,
            "partial": not terminal,
            "run_summary": summary,
            "dynamics_summary": dynamics or None,
            "run_summary_regenerated": regenerated,
        }
        if state is not None:
            result["runner_status"] = state.runner_status.value
            if state.error:
                result["run_error"] = state.error
        if summary is None:
            result["note"] = ("run_summary.json 尚不存在"
                              + ("且聚合失败（详见服务端日志）。" if terminal
                                 else "——模拟仍在运行，用 sim_status 轮询，完成后再取结果。"))
        elif summary.get("simulation_health") not in (None, "ok"):
            result["note"] = (f"simulation_health={summary.get('simulation_health')}：报告阶段必须"
                              "如实呈现该退化（hollow=零有机内容，不得叙事化）。")
        return result

    # ------------------------------------------------------------------ 采访桥

    @classmethod
    def interview(
        cls,
        sim_id: str,
        prompt: Optional[str] = None,
        agent_id: Optional[int] = None,
        interviews: Optional[List[Dict[str, Any]]] = None,
        platform: Optional[str] = None,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """桥接既有 interview IPC 契约（文件命令/响应，见 simulation_ipc）。

        三种模式（与遗留 API 一一对应，复用其超时缩放与取消协议）：
        - interviews=[{agent_id, prompt, platform?}…] → 批量采访；
        - agent_id + prompt → 单个 agent；
        - 仅 prompt → 采访配置中的全部 agent。

        防呆：env_status 说 alive 但进程已死（SIGKILL/宿主崩溃后 env_status
        永远停在 alive）时，不发 IPC 干等超时——直接落 env_status=stopped 并报错。
        """
        sim_dir = cls._sim_dir(sim_id)
        if not os.path.isdir(sim_dir):
            raise ValueError(f"模拟不存在: {sim_id}")

        env_status = SimulationRunner.get_env_status_detail(sim_id)
        if env_status.get("status") != "alive":
            raise ValueError(
                f"模拟环境未运行（env_status={env_status.get('status')}），无法采访。"
                "采访窗口仅在模拟进程存活期间开放（含跑完后的等待命令模式）。")
        state = SimulationRunner.get_run_state(sim_id)
        if state is not None and cls._process_alive(state) is False:
            # RUN-11 的引擎侧镜像：确认进程已死就同步把 env_status 落 stopped，
            # 让后续调用立即失败而不是各自阻塞完整超时窗。
            try:
                SimulationRunner._mark_env_stopped(sim_id)
            except Exception:  # noqa: BLE001 — 标记失败不改变本次判定
                pass
            raise ValueError(
                "env_status 声称 alive 但模拟进程已不存在（陈旧心跳）——已将环境标记为 stopped。"
                "如需继续，请用 sim_start(resume=True) 从检查点重启。")

        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 120.0
        timeout = max(5.0, timeout)

        started = time.time()
        if interviews:
            payload = SimulationRunner.interview_agents_batch(
                simulation_id=sim_id, interviews=list(interviews),
                platform=platform, timeout=timeout)
        elif agent_id is not None and prompt:
            payload = SimulationRunner.interview_agent(
                simulation_id=sim_id, agent_id=int(agent_id), prompt=prompt,
                platform=platform, timeout=timeout)
        elif prompt:
            payload = SimulationRunner.interview_all_agents(
                simulation_id=sim_id, prompt=prompt,
                platform=platform, timeout=timeout)
        else:
            raise ValueError("必须提供 interviews 列表、或 agent_id+prompt、或仅 prompt（采访全员）")

        payload = dict(payload)
        payload["sim_id"] = sim_id
        payload["elapsed_seconds"] = round(time.time() - started, 1)
        return payload
